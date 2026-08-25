"""Backtest a margin-policy change against quotes that were actually priced.

Answers one question: **if the approval floor had been different, which quote
lines would have needed approval, and what was at stake on them?**

    # what a 14% approval floor would have done to the last year
    python -m app.commercial.backtest --org org_pie --min-margin 0.14

    # a review-floor move as well, over one period
    python -m app.commercial.backtest --org org_pie --min-margin 0.14 \
        --margin-floor 0.18 --since 2025-04-01 --until 2026-03-31

**Why this needs no recomputation and no as-of reconstruction.** ``QuoteDecision``
is append-only and freezes the values a line was judged against — the quantity,
the price quoted, and the unit cost resolved on the day. The approval verdict is
a function of exactly those three plus the floor, so replaying it against a
different floor reads the stored row and computes nothing from today's data.
There is no lookahead here to get wrong, because the row *is* the snapshot.

**It reuses the real evaluator rather than restating its two rules.** The verdict
comes from ``quote_exceptions.evaluate`` over ``references.build_references`` —
the same pair ``quote_intelligence.assess_line`` calls on the live quote screen.
A second implementation of "is this below the floor" would drift from the screen,
and a backtest that disagrees with the product is worse than no backtest.

The stored ``references`` JSON on the row is deliberately *not* replayed: it
serialises money as a rounded float, and rebuilding the ladder from the frozen
``Decimal`` cost is both exact and what the variant floor requires anyway.

**What it does not reproduce.** The relationship and peer rules — last price
paid, band price, peer median, erosion — need trading history this row does not
carry, so they are absent from the replayed exception list. None of them can set
``requires_approval``, which is why the approval verdict is still exact; but for
that reason the report states the approval outcome only, and never claims to be
the full exception set a quoter saw.

**Everything this prints is RESTRICTED.** Costs, margins and shortfalls, for a
manager or owner. It is a CLI with no role scoping of its own — do not pipe its
output anywhere a salesperson reads.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from .config import CommercialThresholds
from .policy import load_for_org
from .quantity import band_for
from .quote_exceptions import evaluate
from .references import MIN_MARGIN_PRICE, build_references, by_code

log = logging.getLogger("pie_portal.commercial.backtest")

_ZERO = Decimal("0")
_PAISE = Decimal("0.01")


@dataclass(frozen=True)
class LineVerdict:
    """One quote line under one policy."""

    requires_approval: bool
    #: Rupees between the quoted price and the approval floor, across the line.
    #: ``None`` where no floor could be computed, which is not the same as zero.
    shortfall: Optional[Decimal]
    codes: tuple[str, ...]


@dataclass
class ChangedLine:
    """A line whose approval outcome moves under the variant."""

    quote_id: str
    quote_line_id: str
    as_of: date
    customer_ref: str
    product_ref: str
    quantity: Decimal
    quoted_unit_price: Optional[Decimal]
    margin: Optional[float]
    #: What the line is worth — quantity x price. The size of the thing that
    #: would have been stopped, which is the number an owner ranks on.
    line_revenue: Optional[Decimal]
    shortfall_to_new_floor: Optional[Decimal]
    #: Whether a person overrode the rule that fired at the time. A line already
    #: overridden once is weak evidence that a stricter floor would have held.
    overridden: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "quote_line_id": self.quote_line_id,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "customer_ref": self.customer_ref,
            "product_ref": self.product_ref,
            "quantity": str(self.quantity),
            "quoted_unit_price": _money(self.quoted_unit_price),
            "margin": self.margin,
            "line_revenue": _money(self.line_revenue),
            "shortfall_to_new_floor": _money(self.shortfall_to_new_floor),
            "overridden": self.overridden,
        }


@dataclass
class BacktestReport:
    """What the variant policy would have done, and what could not be judged."""

    organization_id: str
    baseline_version: str = ""
    variant_version: str = ""
    baseline_min_margin: float = 0.0
    variant_min_margin: float = 0.0
    lines_examined: int = 0

    #: Lines that would newly have needed approval, and lines that would no
    #: longer have. Both directions, because a floor can be lowered.
    newly_requires_approval: list[ChangedLine] = field(default_factory=list)
    no_longer_requires_approval: list[ChangedLine] = field(default_factory=list)

    #: Lines carrying no usable cost. Reported, never counted as passing: a line
    #: whose economics are unknown has an UNKNOWN verdict, not a clean one.
    unjudgeable: int = 0

    #: Lines where replaying the row's own baseline does not reproduce the
    #: verdict it was actually stamped with. Where the stamp resolved this is a
    #: genuine disagreement worth looking at; where it did not, the harness fell
    #: back to today's policy and the row was judged by something else.
    #: Counted and surfaced rather than quietly folded into the totals.
    baseline_disagreements: int = 0
    versions_seen: dict[str, int] = field(default_factory=dict)

    #: Lines — not distinct versions — whose ``thresholds_version`` could not
    #: be dereferenced, split by
    #: the reason ``threshold_registry`` gave. The split is the point: PRE_EPOCH
    #: is expected, finite and shrinking — history from before the registry
    #: existed — while POST_EPOCH_GAP is a defect in the recording path and
    #: UNRECORDED_ORG means the backfill never ran for this tenant. Rolling the
    #: three into one number would turn a bug report into a footnote.
    unresolvable_stamps: dict[str, int] = field(default_factory=dict)

    #: Lines replayed against the policy actually recorded for their stamp,
    #: rather than against today's. The part of the report that is now exact.
    lines_on_recorded_baseline: int = 0

    @property
    def revenue_newly_gated(self) -> Decimal:
        return sum((c.line_revenue or _ZERO for c in self.newly_requires_approval),
                   _ZERO)

    @property
    def shortfall_newly_gated(self) -> Decimal:
        return sum((c.shortfall_to_new_floor or _ZERO
                    for c in self.newly_requires_approval), _ZERO)

    def by_customer(self) -> list[dict[str, Any]]:
        """Newly-gated lines grouped by customer, largest exposure first."""
        return _grouped(self.newly_requires_approval, lambda c: c.customer_ref)

    def by_product(self) -> list[dict[str, Any]]:
        """Newly-gated lines grouped by item.

        By item rather than by tool family: ``QuoteDecision`` does not record the
        family, and inferring one here from today's master would be a second
        answer to a question ``commercial.categories`` already owns.
        """
        return _grouped(self.newly_requires_approval, lambda c: c.product_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "policy": {
                "baseline_version": self.baseline_version,
                "variant_version": self.variant_version,
                "baseline_min_margin": self.baseline_min_margin,
                "variant_min_margin": self.variant_min_margin,
            },
            "lines_examined": self.lines_examined,
            "newly_requires_approval": len(self.newly_requires_approval),
            "no_longer_requires_approval": len(self.no_longer_requires_approval),
            "revenue_newly_gated": _money(self.revenue_newly_gated),
            "shortfall_newly_gated": _money(self.shortfall_newly_gated),
            "unjudgeable_no_cost_on_record": self.unjudgeable,
            "baseline_disagreements": self.baseline_disagreements,
            "lines_on_recorded_baseline": self.lines_on_recorded_baseline,
            "unresolvable_stamps": dict(self.unresolvable_stamps),
            "thresholds_versions_seen": dict(self.versions_seen),
            "by_customer": self.by_customer(),
            "by_product": self.by_product(),
            "lines": {
                "newly_requires_approval":
                    [c.to_dict() for c in self.newly_requires_approval],
                "no_longer_requires_approval":
                    [c.to_dict() for c in self.no_longer_requires_approval],
            },
        }


def _money(value: Optional[Decimal]) -> Optional[str]:
    """Money out as a string, quantized to paise at the boundary.

    Full precision is kept through the arithmetic and rounded only here — a
    floor price is ``cost / (1 - margin)`` and divides into a long tail, and
    printing thirty digits of it invites a conversation about the last twenty.
    The same ``quantize`` the floor itself is published through in ``floor.py``.
    """
    return None if value is None else str(value.quantize(_PAISE))


def _grouped(lines: list[ChangedLine], key) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for line in lines:
        name = key(line) or "(unresolved)"
        bucket = buckets.setdefault(
            name, {"name": name, "lines": 0, "revenue": _ZERO, "shortfall": _ZERO})
        bucket["lines"] += 1
        bucket["revenue"] += line.line_revenue or _ZERO
        bucket["shortfall"] += line.shortfall_to_new_floor or _ZERO
    out = sorted(buckets.values(), key=lambda b: -b["revenue"])
    for bucket in out:
        bucket["revenue"] = _money(bucket["revenue"])
        bucket["shortfall"] = _money(bucket["shortfall"])
    return out


def _decimal(value: Any) -> Optional[Decimal]:
    """A stored Numeric back to ``Decimal``. ``None`` stays unknown."""
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def verdict_for(*, quantity: Decimal, quoted_unit_price: Optional[Decimal],
                unit_cost: Optional[Decimal], as_of: date,
                th: CommercialThresholds) -> LineVerdict:
    """Would this line have needed approval under ``th``?

    The same two calls ``quote_intelligence.assess_line`` makes, over the values
    frozen on the decision row. ``family`` is ``None`` because the row does not
    record one and the only reference it would change — the target-margin price
    — cannot set ``requires_approval``.

    ``as_of`` is the line's own quote date and is required rather than defaulted
    to today. With no trading history in scope it selects nothing, so it cannot
    move the verdict — but a function that reads the clock is not reproducible,
    and this one exists to be reproducible.
    """
    band = band_for(quantity, th)
    references = build_references(
        lines=[], band=band, unit_cost=unit_cost, benchmark=None,
        historical_margin=None, family=None, as_of=as_of, th=th)
    exceptions = evaluate(
        proposed_price=quoted_unit_price, qty=quantity, unit_cost=unit_cost,
        references=references, metrics=None, benchmark=None, th=th)

    blocking = [e for e in exceptions if e.requires_approval]
    floor_ref = by_code(references).get(MIN_MARGIN_PRICE)
    shortfall = None
    if floor_ref is not None and quoted_unit_price is not None:
        shortfall = max(_ZERO, (floor_ref.value - quoted_unit_price) * quantity)
    return LineVerdict(
        requires_approval=bool(blocking),
        shortfall=shortfall,
        codes=tuple(e.code for e in blocking))


def _baseline_for(session: Session, org: str, row: Any,
                  today: CommercialThresholds,
                  cache: dict[str, tuple[Optional[CommercialThresholds], str]],
                  report: "BacktestReport") -> CommercialThresholds:
    """The policy that judged this row, or today's with the shortfall counted.

    ``rebuild`` rather than ``resolve`` alone, because a replay needs the whole
    dataclass and not one value out of it — and ``rebuild`` refuses when today's
    field set has moved, which is exactly the case where constructing the object
    anyway would fill a new field from today's default and hand back an impostor
    of the historical policy. A refusal here costs one approximate line and is
    counted; accepting the impostor would cost the report's credibility with no
    trace of having done so.

    Falling back to today's policy is a *degradation that is reported*, not a
    silent substitution: the line is still replayed (a backtest that dropped
    every pre-registry row would have nothing to say about the year that
    matters), and ``unresolvable_stamps`` carries the reason it was approximate.

    **The reason is counted per line, and the cache stores it for exactly that
    purpose.** Resolving once per distinct version is right — ``resolve`` reads
    a row and a backtest runs over a year of quote lines — but counting once per
    distinct version was not: five hundred lines sharing one unrecorded stamp
    reported ``{"POST_EPOCH_GAP": 1}`` beside ``lines_examined: 500``, and an
    owner reading that concludes 499 lines were replayed exactly when none of
    them were. Worse, the two counters either side of it are per line
    (``UNSTAMPED`` above, ``lines_on_recorded_baseline`` below), so one report
    mixed two units and the parts no longer summed to the whole. Every count
    here is per line.
    """
    from .. import threshold_registry

    version = row.thresholds_version or ""
    if not version:
        report.unresolvable_stamps["UNSTAMPED"] = (
            report.unresolvable_stamps.get("UNSTAMPED", 0) + 1)
        return today
    if version in cache:
        found, reason = cache[version]
    else:
        found, reason = None, ""
        try:
            stamped_at = getattr(row, "created_at", None)
            resolution = threshold_registry.resolve(
                session, org, version, stamped_at=stamped_at)
            rebuilt = threshold_registry.rebuild(resolution)
            if isinstance(rebuilt, CommercialThresholds):
                found = rebuilt
            else:
                # A ``th_`` signal stamp on a quote line. Vanishingly rare and
                # named anyway: it resolved, so calling it UNRECORDED_ORG or
                # PRE_EPOCH would point the reader at the wrong fault.
                reason = "NOT_A_COMMERCIAL_POLICY"
        except threshold_registry.UnresolvedStamp as exc:
            reason = exc.reason
        except (threshold_registry.CorruptThresholdRecord,
                threshold_registry.UnrebuildableThresholds) as exc:
            reason = type(exc).__name__
            log.warning("backtest: %s", exc)
        cache[version] = (found, reason)
    if found is None:
        report.unresolvable_stamps[reason] = (
            report.unresolvable_stamps.get(reason, 0) + 1)
        return today
    report.lines_on_recorded_baseline += 1
    return found


def run(session: Session, org: str, *, min_margin: float,
        margin_floor: Optional[float] = None, since: Optional[date] = None,
        until: Optional[date] = None) -> BacktestReport:
    """Replay every recorded quote line under a variant approval floor.

    Read-only. Nothing here writes, and the caller need not roll anything back.
    """
    baseline = load_for_org(session, org)
    changes: dict[str, Any] = {"min_margin": min_margin}
    if margin_floor is not None:
        changes["margin_floor"] = margin_floor
    variant = replace(baseline, **changes)

    #: Per-stamp baselines, resolved once each: the rebuilt policy, or ``None``
    #: and the reason it could not be had. Recorded here rather than recomputed
    #: per line, because ``resolve`` reads a row and a backtest runs over every
    #: quote line in a year — and the reason is cached alongside so that a
    #: fallback to today's policy is still counted on every line it happens to,
    #: not only on the first line of each version.
    resolved: dict[str, tuple[Optional[CommercialThresholds], str]] = {}
    #: The variant derived from each distinct baseline, memoised beside it. The
    #: baseline was already cached per stamp and the variant was rebuilt on
    #: every row — one dataclass construction per quote line over a year of
    #: them, and because ``serialized`` and ``version`` are ``cached_property``
    #: *per instance*, any path that reads ``.version`` off the variant re-ran
    #: json.dumps + sha256 per line. That per-row cost is exactly what
    #: ``commercial/config.py`` added ``cached_property`` to remove.
    variants: dict[int, CommercialThresholds] = {}

    report = BacktestReport(
        organization_id=org,
        baseline_version=baseline.version, variant_version=variant.version,
        baseline_min_margin=baseline.min_margin,
        variant_min_margin=variant.min_margin)

    stmt = select(models.QuoteDecision).where(
        models.QuoteDecision.organization_id == org)
    if since is not None:
        stmt = stmt.where(models.QuoteDecision.as_of >= since)
    if until is not None:
        stmt = stmt.where(models.QuoteDecision.as_of <= until)
    stmt = stmt.order_by(models.QuoteDecision.as_of,
                         models.QuoteDecision.created_at)

    for row in session.scalars(stmt):
        report.lines_examined += 1
        report.versions_seen[row.thresholds_version or "(unstamped)"] = (
            report.versions_seen.get(row.thresholds_version or "(unstamped)", 0) + 1)

        quantity = _decimal(row.quantity) or _ZERO
        price = _decimal(row.quoted_unit_price)
        cost = _decimal(row.unit_cost)
        # A zero or negative cost is a placeholder, not a purchase price — the
        # same rule ``line_economics`` and ``assess_line`` both apply.
        if cost is not None and cost <= _ZERO:
            cost = None
        if cost is None or price is None:
            report.unjudgeable += 1
            continue

        # The floor this line was *actually* judged against, where the registry
        # can say. This is what the threshold registry bought: before it, every
        # row was replayed under today's policy and a row priced under an older
        # floor produced a baseline verdict that simply disagreed with what had
        # been recorded. Where the stamp does not resolve, today's policy stands
        # in — and the count of those is reported by reason rather than folded
        # away, because a report that silently mixes exact and approximate rows
        # is one nobody can act on.
        line_baseline = _baseline_for(session, org, row, baseline, resolved, report)
        # Keyed on the baseline object's identity rather than its stamp: two
        # rows resolving to the same policy get the same instance out of
        # ``resolved``, and a row whose stamp did not resolve falls back to the
        # single ``baseline`` object, so identity covers both without a second
        # notion of which policy this is.
        line_variant = variants.get(id(line_baseline))
        if line_variant is None:
            line_variant = replace(line_baseline, **changes)
            variants[id(line_baseline)] = line_variant

        before = verdict_for(quantity=quantity, quoted_unit_price=price,
                             unit_cost=cost, as_of=row.as_of, th=line_baseline)
        after = verdict_for(quantity=quantity, quoted_unit_price=price,
                            unit_cost=cost, as_of=row.as_of, th=line_variant)

        # The row records what actually happened. Where replaying its own
        # baseline does not reproduce it, something other than the floor moved,
        # and saying so is the honest answer.
        if before.requires_approval != bool(row.requires_approval):
            report.baseline_disagreements += 1

        if before.requires_approval == after.requires_approval:
            continue
        changed = ChangedLine(
            quote_id=row.quote_id, quote_line_id=row.quote_line_id,
            as_of=row.as_of, customer_ref=row.customer_ref or "",
            product_ref=row.product_ref or "", quantity=quantity,
            quoted_unit_price=price, margin=row.margin,
            line_revenue=price * quantity,
            shortfall_to_new_floor=after.shortfall,
            overridden=bool(row.overridden))
        if after.requires_approval:
            report.newly_requires_approval.append(changed)
        else:
            report.no_longer_requires_approval.append(changed)

    return report


def _day(raw: Optional[str]) -> Optional[date]:
    return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", required=True, help="organization_id to replay")
    parser.add_argument("--min-margin", type=float, required=True,
                        help="variant approval floor, as a ratio (0.14, not 14)")
    parser.add_argument("--margin-floor", type=float,
                        help="variant review floor, as a ratio. Optional.")
    parser.add_argument("--since", help="earliest quote date, YYYY-MM-DD")
    parser.add_argument("--until", help="latest quote date, YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    from ..db import SessionLocal

    session = SessionLocal()
    try:
        report = run(session, args.org, min_margin=args.min_margin,
                     margin_floor=args.margin_floor,
                     since=_day(args.since), until=_day(args.until))
    finally:
        session.close()

    print(json.dumps(report.to_dict(), indent=2))
    if report.unjudgeable:
        print(f"\n{report.unjudgeable} line(s) had no cost on record and were "
              "not judged either way.", file=sys.stderr)
    if report.baseline_disagreements:
        print(f"{report.baseline_disagreements} line(s) replayed against their "
              "own recorded baseline still do not reproduce the verdict on the "
              "row. Something other than the approval floor moved; read the "
              "counts above as covering the rest.", file=sys.stderr)
    # Never a value and never a blank for a stamp that did not resolve. A blank
    # invites the reader to supply the number themselves, and a value would be
    # today's policy wearing an old stamp — the substitution §1 forbids.
    for reason, count in sorted(report.unresolvable_stamps.items()):
        print(f"{count} line(s): UNKNOWN — {_STAMP_REASONS.get(reason, reason)}",
              file=sys.stderr)


#: What each unresolvable reason means, in one line, for the CLI. Kept next to
#: the printing rather than on the exception because the exception's own message
#: names the tenant and the epoch and is what belongs in a log.
_STAMP_REASONS: dict[str, str] = {
    "PRE_EPOCH": ("stamped before the registry recorded this tenant's policy. "
                  "The values behind that stamp were never captured; these "
                  "lines were replayed under today's policy instead"),
    "POST_EPOCH_GAP": ("stamped after the registry was recording and never "
                       "recorded. This is a defect in the recording path, not "
                       "missing history — report it"),
    "UNRECORDED_ORG": ("this organization has no recorded threshold versions "
                       "at all; the boot backfill has not run against this "
                       "database"),
    "UNSTAMPED": "the row carries no threshold version at all",
    "NOT_A_COMMERCIAL_POLICY": ("the stamp resolved to a signal policy rather "
                                "than a commercial one, so it cannot be a "
                                "quote line's approval floor"),
    "CorruptThresholdRecord": ("the recorded values do not hash to the stamp "
                               "they are filed under and were not used"),
    "UnrebuildableThresholds": ("the recorded policy has a different field set "
                                "from today's dataclass, so it cannot be "
                                "rebuilt without inventing values"),
}


if __name__ == "__main__":
    main()
