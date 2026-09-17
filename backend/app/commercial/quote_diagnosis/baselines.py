"""What the price and the cost should have been, with the outliers named.

Two baselines, kept apart on purpose, because separating a pricing problem from
a cost problem is the reason this engine is worth building. A price below the
band with cost at its normal level is money left on the table; the same price
with cost above its normal level is supply-side margin compression and is **not**
pricing leakage — wording it as though it were sends a salesperson to renegotiate
something they did not do.

They are also kept apart because only one of them may ever be rendered to a
salesperson. ``PriceBaseline`` holds prices this customer and its peers have
actually seen. ``CostBaseline`` holds purchase economics and is restricted
absolutely. Two types, so the operations view has no field to leak through.

Both are trimmed by the same symmetric MAD rule (``commercial.dispersion``), in
both directions, with every removed row named and counted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from ..config import CommercialThresholds
from ..dispersion import HIGH, LOW, trim
from .comparables import BandStats, EMPTY_STATS, stats
from .evidence import (BASELINE_CLASSES, CostObservation, EvidenceRow,
                       Exclusion, QUOTED_LOST)

_ZERO = Decimal("0")

#: Why a row left a baseline. Distinct from the visibility reasons in
#: ``evidence``: those say the row could not have been known, these say it was
#: known and is not representative.
OUTLIER_LOW = "OUTLIER_LOW"
OUTLIER_HIGH = "OUTLIER_HIGH"

_REASON = {LOW: OUTLIER_LOW, HIGH: OUTLIER_HIGH}


@dataclass(frozen=True)
class Resistance:
    """What this customer has refused, and the ceiling they have accepted (§8).

    Lost quotes never raise a band — they are evidence of resistance, not of
    achievability, and a band that took its top from a price the customer
    declined would recommend re-quoting at a number already refused.

    They do one thing: truncate opportunity at the highest price this customer
    has actually *accepted*. So the qualifier is carried through to the renderer
    in plain words rather than silently shrinking a number on the card.
    """

    observed: bool
    #: The highest price this customer has actually paid or accepted. The
    #: ceiling opportunity is truncated at. ``None`` when nothing was accepted.
    highest_accepted: Optional[Decimal]
    #: Lost quotes priced at or below the top of the computed band — the ones
    #: that say the band's top is not reachable for this account.
    lost_at_or_below_band: int
    citations: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {"observed": self.observed,
                "highest_accepted": (float(round(self.highest_accepted, 2))
                                     if self.highest_accepted is not None else None),
                "lost_at_or_below_band": self.lost_at_or_below_band}


NO_RESISTANCE = Resistance(observed=False, highest_accepted=None,
                           lost_at_or_below_band=0)


@dataclass(frozen=True)
class PriceBaseline:
    """The supportable price range, after trimming, with its full audit trail.

    ``OPERATIONAL``: every figure here is a price this customer or a peer has
    already seen. Nothing on this type is derived from purchase cost.
    """

    band: BandStats
    #: Before trimming. Kept because "₹400–₹1,600 became ₹950–₹1,020 after
    #: removing two rows" is the sentence that makes a trim reviewable, and a
    #: baseline that only showed its own conclusion would not be.
    band_before_trim: BandStats
    cited: tuple[str, ...]
    excluded: tuple[Exclusion, ...]
    excluded_low: int
    excluded_high: int
    exclusion_rate: float
    #: The MAD was zero — more than half the observations identical, which is
    #: what a stable price history looks like — so the trim judged against a
    #: relative band around the median instead. Reported because it changes what
    #: an exclusion here means.
    zero_spread_fallback: bool
    #: Past ``diagnosis_max_exclusion_rate``. The band is not trustworthy and the
    #: rules layer caps confidence at WEAK because of it.
    over_exclusion_limit: bool
    resistance: Resistance = NO_RESISTANCE

    @property
    def supportable(self) -> bool:
        return self.band.median is not None

    def to_dict(self) -> dict:
        return {"band": self.band.to_dict(),
                "band_before_trim": self.band_before_trim.to_dict(),
                "excluded_low": self.excluded_low,
                "excluded_high": self.excluded_high,
                "exclusion_rate": round(self.exclusion_rate, 4),
                "zero_spread_fallback": self.zero_spread_fallback,
                "over_exclusion_limit": self.over_exclusion_limit,
                "resistance": self.resistance.to_dict(),
                "cited": list(self.cited),
                "excluded": [e.to_dict() for e in self.excluded]}


EMPTY_PRICE_BASELINE = PriceBaseline(
    band=EMPTY_STATS, band_before_trim=EMPTY_STATS, cited=(), excluded=(),
    excluded_low=0, excluded_high=0, exclusion_rate=0.0,
    zero_spread_fallback=False, over_exclusion_limit=False)


def price_baseline(rows: Sequence[EvidenceRow], *, as_of: date,
                   th: CommercialThresholds,
                   lost: Sequence[EvidenceRow] = (),
                   ) -> PriceBaseline:
    """Trim a set of comparables into a band, symmetrically, and show the work.

    ``rows`` must already be point-in-time filtered, normalised and restricted to
    ``BASELINE_CLASSES``; this function does not re-check visibility. ``lost`` is
    the customer's declined quotes, which never enter the band and are read only
    for resistance.
    """
    usable = [r for r in rows if r.evidence_class in BASELINE_CLASSES]
    if not usable:
        return EMPTY_PRICE_BASELINE

    before = stats(usable, as_of=as_of, recent_days=th.diagnosis_recent_days)
    result = trim([r.unit_price for r in usable],
                  k=Decimal(str(th.diagnosis_mad_k)),
                  relative_band=Decimal(str(th.diagnosis_degenerate_band_pct)))
    kept = [usable[i] for i in result.kept]
    after = stats(kept, as_of=as_of, recent_days=th.diagnosis_recent_days)

    excluded = tuple(
        Exclusion(usable[d.index].evidence_id, usable[d.index].source_table,
                  _REASON[d.direction],
                  f"{d.deviations:.1f} MADs {d.direction.lower()} of the median")
        for d in result.excluded)

    return PriceBaseline(
        band=after,
        band_before_trim=before,
        cited=tuple(r.evidence_id for r in kept),
        excluded=excluded,
        excluded_low=result.excluded_low,
        excluded_high=result.excluded_high,
        exclusion_rate=result.exclusion_rate,
        zero_spread_fallback=result.zero_spread_fallback,
        over_exclusion_limit=(result.exclusion_rate
                              > th.diagnosis_max_exclusion_rate),
        resistance=resistance(kept, lost),
    )


def resistance(accepted: Sequence[EvidenceRow],
               lost: Sequence[EvidenceRow]) -> Resistance:
    """What the customer's declined quotes say about the top of the band.

    Deliberately narrow. It reports the ceiling and the count; it does not adjust
    the band, and it never pushes a number up. The opportunity layer truncates
    against ``highest_accepted``, and the renderer says so in words — a silently
    shortened range would look like the engine simply thought less was available.
    """
    declined = [r for r in lost if r.evidence_class == QUOTED_LOST]
    if not declined:
        return NO_RESISTANCE
    # ``accepted`` is the *trimmed* set, so this is the highest price the
    # customer accepted that the band still believes. Truncating opportunity at
    # an untrimmed maximum would put an outlier the band just removed back in
    # through the one number a reviewer acts on.
    #
    # It is also the top of the computed band, because the band is built from
    # exactly these rows — one value, named once, rather than two names for it
    # that a later edit could let drift apart.
    band_top = max((r.unit_price for r in accepted), default=None)
    at_or_below = sum(1 for r in declined
                      if band_top is not None and r.unit_price <= band_top)
    return Resistance(
        observed=at_or_below > 0,
        highest_accepted=band_top,
        lost_at_or_below_band=at_or_below,
        citations=tuple(r.citation() for r in
                        sorted(declined, key=lambda r: r.evidence_id)),
    )


# ── the cost side (§9) ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class CostBaseline:
    """What this item should have cost, from the ledger and nothing else.

    **RESTRICTED in its entirety.** Every field is purchase economics. Nothing
    on this type may be rendered to a salesperson, and the operations view is
    built from a record that has no place to put it.

    Scope is the ERP: bill lines, their dates, vendors, quantities and unit
    costs. Special prices and manufacturer schemes are negotiated outside the
    system and are not recorded, so nothing here assumes one existed or asks for
    one — and, crucially, nothing here *explains* a low purchase. It reports the
    observation and attaches the rows; the reason is not in the data.
    """

    #: The cost the quote should have been priced against. ``None`` when the
    #: evidence is too thin to say — never a manufactured figure.
    expected_cost: Optional[Decimal]
    #: The trimmed median of the window, *before* any known-change override.
    #: Kept separate because the two answer different questions and collapsing
    #: them breaks the rule that reads them: "cost is materially above its
    #: historical level" is meaningless if the historical level has already been
    #: moved to the new one, and the comparison silently becomes zero.
    historical_cost: Optional[Decimal]
    low: Optional[Decimal]
    high: Optional[Decimal]
    #: The most recent purchase that was knowable when the quote was written.
    #: Separate from ``expected_cost`` because a cost that has genuinely moved
    #: and a cost that looks odd for one row are different findings.
    latest_cost: Optional[Decimal]
    latest_on: Optional[date]
    observations: int
    cited: tuple[str, ...]
    excluded: tuple[Exclusion, ...]
    excluded_low: int
    excluded_high: int
    exclusion_rate: float
    #: The latest knowable purchase sits materially above the trimmed historical
    #: level, and it was recorded before the quote. That is a cost change the
    #: desk could have known about, so ``expected_cost`` moves to the new level.
    known_cost_change: bool
    #: An unexplained purchase well below the normal band was removed. Reported
    #: as context with its rows attached, never as an assertion about why.
    unexplained_low_purchase: bool

    @property
    def known(self) -> bool:
        return self.expected_cost is not None

    def to_dict(self) -> dict:
        """RESTRICTED. Only ever serialised into an owner-facing payload."""
        def m(v: Optional[Decimal]) -> Optional[float]:
            return float(round(v, 2)) if v is not None else None
        return {"expected_cost": m(self.expected_cost),
                "historical_cost": m(self.historical_cost), "low": m(self.low),
                "high": m(self.high), "latest_cost": m(self.latest_cost),
                "latest_on": self.latest_on.isoformat() if self.latest_on else None,
                "observations": self.observations,
                "excluded_low": self.excluded_low,
                "excluded_high": self.excluded_high,
                "exclusion_rate": round(self.exclusion_rate, 4),
                "known_cost_change": self.known_cost_change,
                "unexplained_low_purchase": self.unexplained_low_purchase,
                "cited": list(self.cited),
                "excluded": [e.to_dict() for e in self.excluded]}


UNKNOWN_COST = CostBaseline(
    expected_cost=None, historical_cost=None, low=None, high=None,
    latest_cost=None, latest_on=None,
    observations=0, cited=(), excluded=(), excluded_low=0, excluded_high=0,
    exclusion_rate=0.0, known_cost_change=False, unexplained_low_purchase=False)


def cost_baseline(costs: Sequence[CostObservation], *, as_of: date,
                  th: CommercialThresholds) -> CostBaseline:
    """The expected acquisition cost, trimmed symmetrically like the price band.

    **Why the trim matters more here than anywhere else.** Some historical
    purchases sit well below the normal acquisition band for reasons the ledger
    does not record — a scheme, a clearance, a correction. Let one of those
    become ``expected_cost`` and every subsequent normally-priced purchase is
    diagnosed as cost-driven erosion: the engine cries wolf on its own history,
    at volume, and everything it says afterwards is discounted.

    ``expected_cost`` is the trimmed **median**, not the latest purchase. The
    latest is reported beside it, and it becomes the baseline only when it has
    moved to a materially different level *and* was recorded in time to have
    been known — which is the ERP-only reading of §10's known cost change. There
    are no price lists behind it: Zoho price lists carry no effective date at
    all, so what a price list said on a past date is unknowable and no rule here
    pretends otherwise.
    """
    window = [c for c in costs
              if c.unit_cost > _ZERO and c.qty > _ZERO
              and c.event_date > as_of - timedelta(days=th.historical_lookback_days)]
    if not window:
        return UNKNOWN_COST

    ordered = sorted(window, key=lambda c: (c.event_date, c.evidence_id))
    result = trim([c.unit_cost for c in ordered],
                  k=Decimal(str(th.diagnosis_mad_k)),
                  relative_band=Decimal(str(th.diagnosis_degenerate_band_pct)))
    kept = [ordered[i] for i in result.kept]
    prices = sorted(c.unit_cost for c in kept)

    excluded = tuple(
        Exclusion(ordered[d.index].evidence_id, ordered[d.index].source_table,
                  _REASON[d.direction],
                  f"{d.deviations:.1f} MADs {d.direction.lower()} of the median")
        for d in result.excluded)

    latest = ordered[-1]
    historical = result.median if kept else None
    baseline = historical

    # ── the one asymmetry in this module, and it is not in the trim ──────────
    #
    # The trim above is symmetric and stays symmetric: both tails are removed,
    # both are counted, both are reported. What follows is a different decision —
    # not "which observations are representative" but "which level does this
    # quote actually face" — and there the two directions are genuinely not
    # mirror images.
    #
    # A single observation well above the rest is indistinguishable, by spread
    # alone, from a genuine step up in what the supplier charges; with one
    # purchase at the new level there is no statistic that can tell them apart.
    # What breaks the tie is which error is affordable. Reading a real increase
    # as an outlier means quoting at a margin that will not arrive, and nothing
    # downstream ever objects. Reading a one-off spike as a new level means
    # saying "margin here is compressed by supply cost, no price change needed",
    # which costs nothing and tells nobody to drop a price.
    #
    # **Downward, the rule is the opposite and absolute.** A latest purchase
    # *below* the historical level never moves the baseline, because that is
    # precisely the unexplained cheap purchase §9 is about: let it become
    # ``expected_cost`` and every subsequent normally-priced purchase is
    # diagnosed as cost-driven erosion, at volume, on the engine's own history.
    moved = False
    if (baseline is not None and baseline > _ZERO
            and latest.unit_cost > baseline
            and (latest.unit_cost - baseline) / baseline
            >= Decimal(str(th.meaningful_cost_increase_pct))):
        moved = True
        baseline = latest.unit_cost
        # The trusted range has to contain the level the quote is judged
        # against, or a reader sees an expected cost sitting outside the range
        # it was supposedly drawn from.
        prices = sorted(prices + [latest.unit_cost])

    return CostBaseline(
        expected_cost=baseline,
        historical_cost=historical,
        low=(prices[0] if prices else None),
        high=(prices[-1] if prices else None),
        latest_cost=latest.unit_cost,
        latest_on=latest.event_date,
        observations=len(kept),
        cited=tuple(c.evidence_id for c in kept),
        excluded=excluded,
        excluded_low=result.excluded_low,
        excluded_high=result.excluded_high,
        exclusion_rate=result.exclusion_rate,
        known_cost_change=moved,
        unexplained_low_purchase=result.excluded_low > 0,
    )
