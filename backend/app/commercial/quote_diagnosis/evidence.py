"""What the business could have known at a moment, normalised and classified.

Two jobs, both of which have to happen before anything is comparable:

**Point-in-time.** A diagnosis for a quote written at T may cite only evidence
that had become visible by T. Visibility is ``recorded_at`` — the *source
system's* creation stamp — never ``event_date``, and never the platform's own
sync time. The three are different clocks and only one of them answers the
question; ``models.SalesTxn.source_recorded_at`` has the argument at length.

Filtering on ``event_date`` instead is not a small error here. Measured on the
live books before this module existed: a bill's median entry lag is three days
and its p90 is seven, so a cost baseline built on event dates routinely cites
purchases the desk had not yet seen. On the migrated cohort the median lag is
165 days.

**Normalisation.** Two rows are not comparable until they are expressed in the
same units. This is a gate, not a weight — a row that cannot be normalised is
excluded and counted, never included with a penalty.

Every exclusion is recorded with its row id and a reason. A diagnosis that
silently dropped evidence would be unauditable, and the count is frequently the
most informative thing on the card: "14 of 60 excluded" is a data-quality
finding, not a footnote.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional

from ...clock import aware

# ── evidence classes (§8) ────────────────────────────────────────────────────
#
# Realized prices are survivorship-biased: they are, by definition, the prices a
# customer accepted. A band built only from them understates what was achievable
# and overstates opportunity exactly where the customer had been pushing back.
# So an evidence row says which kind of fact it is, and the baseline layer reads
# the distinction rather than averaging over it.

#: Invoiced. What this customer has actually paid — the strongest evidence there
#: is about what they will pay, and the most biased about what they would have.
REALIZED = "REALIZED"

#: Quoted, and the ERP recorded the customer accepting it.
QUOTED_WON = "QUOTED_WON"

#: Quoted, and the ERP recorded the customer declining it. **Resistance
#: evidence.** Never raises a supportable range — it can only damp opportunity.
QUOTED_LOST = "QUOTED_LOST"

#: Quoted with no outcome recorded. Excluded from every baseline: a price
#: nobody has answered is not evidence of anything yet, and the pile is large
#: (54% of the quotes on one live book are `expired`, which is not a loss).
QUOTED_OPEN = "QUOTED_OPEN"

EVIDENCE_CLASSES = (REALIZED, QUOTED_WON, QUOTED_LOST, QUOTED_OPEN)

#: The classes a price baseline may be built from. `QUOTED_LOST` is deliberately
#: absent — §8 — and `QUOTED_OPEN` carries no information at all.
BASELINE_CLASSES = frozenset({REALIZED, QUOTED_WON})


# ── exclusion reasons ────────────────────────────────────────────────────────
#
# Named rather than boolean because they mean different things to a reader and
# call for different fixes. "Recorded after the quote" is the engine working;
# "no recorded time" is a connector gap; "backfilled" is a migration artefact.

#: The source recorded it after the quote was written. The engine working.
RECORDED_AFTER = "RECORDED_AFTER"

#: The source system gave no creation stamp, so when it became visible is
#: unknown. Excluded rather than imputed from ``event_date``: an imputed
#: visibility is indistinguishable from a measured one once it is in the band.
NO_RECORDED_AT = "NO_RECORDED_AT"

#: Recorded before this book's ``history_loaded_before`` — bulk-loaded from a
#: prior system, so its creation stamp is the load time and says nothing about
#: when the business knew. Two thirds of one live book is this cohort.
BACKFILLED = "BACKFILLED"

#: No outcome recorded yet, so it supports nothing (§8).
NO_OUTCOME = "NO_OUTCOME"

#: The item's unit of measure is unknown, so the quantity has no meaning and the
#: row cannot join a quantity band. Never guessed — §5.
NO_UNIT = "NO_UNIT"

#: The row's unit disagrees with the subject's. Nothing in these books carries a
#: conversion factor, so there is no honest way to reconcile them.
UNIT_MISMATCH = "UNIT_MISMATCH"

#: Quantity or price is absent, zero or negative — a placeholder, not a trade.
NOT_A_TRADE = "NOT_A_TRADE"


@dataclass(frozen=True)
class EvidenceRow:
    """One historical transaction, normalised, with its full provenance.

    Frozen because a diagnosis cites these by id and must be reproducible: a row
    that could be edited between the citation and the replay would make the
    evidence hash a lie.

    **Carries cost nowhere.** This is price evidence. The cost baseline reads
    ``CostObservation`` below, and keeping them as two types is what lets the
    operations view be built from a record that structurally cannot leak — see
    ``rules.OperationsDiagnosis``.
    """

    #: The primary key of the row this came from, and which table it is in.
    #: Together they are the citation §I4 requires: a diagnosis names every row
    #: that produced it and every row it excluded.
    evidence_id: str
    source_table: str

    customer_id: Optional[str]
    product_id: str

    #: When the commercial event happened. Used for recency weighting and for
    #: narrative text — "they bought this in March" — and **never** as the
    #: visibility cut-off.
    event_date: date
    #: When the source system recorded it. The visibility cut-off, and ``None``
    #: when the source gave none, which makes the row unusable as evidence.
    recorded_at: Optional[datetime]

    evidence_class: str
    qty: Decimal
    #: Net of discount, in the book's base currency. See ``normalize`` for why
    #: there is no currency conversion here and why that is a guarantee rather
    #: than an omission.
    unit_price: Decimal
    #: The item's unit, canonicalised. ``None`` when the master does not say.
    unit: Optional[str]
    source_ref: dict
    #: ``recorded_at`` was estimated, not read. See ``impute_recorded_at``.
    #: Defaults to ``False`` and the engine's own builders never set it: the
    #: default path *excludes* a row whose visibility is unknown rather than
    #: estimating it. A caller that chooses to estimate gets the downgrade that
    #: comes with it — ``rules.strength`` caps at WEAK on any specific-tier row
    #: carrying this, so the cost of the assumption is paid where it is made.
    recorded_at_imputed: bool = False

    def citation(self) -> dict:
        """The traceable reference, for the diagnosis's evidence list."""
        return {"id": self.evidence_id, "table": self.source_table,
                "event_date": self.event_date.isoformat(),
                "recorded_at": (self.recorded_at.isoformat()
                                if self.recorded_at else None),
                "recorded_at_imputed": self.recorded_at_imputed,
                "evidence_class": self.evidence_class}


@dataclass(frozen=True)
class CostObservation:
    """One purchase, normalised. Restricted — never reaches an operations view.

    A separate type from ``EvidenceRow`` rather than a flag on it. The two are
    read by different layers, one of them may be rendered to a salesperson and
    the other may never be, and a shared type with a ``kind`` field is one
    ``if`` away from the wrong one being serialised.
    """

    evidence_id: str
    source_table: str
    product_id: str
    vendor_id: Optional[str]
    event_date: date
    recorded_at: Optional[datetime]
    qty: Decimal
    unit_cost: Decimal
    unit: Optional[str]
    source_ref: dict
    recorded_at_imputed: bool = False

    def citation(self) -> dict:
        return {"id": self.evidence_id, "table": self.source_table,
                "event_date": self.event_date.isoformat(),
                "recorded_at": (self.recorded_at.isoformat()
                                if self.recorded_at else None)}


@dataclass(frozen=True)
class Exclusion:
    """One row that was not used, and why. Ordered by id for determinism."""

    evidence_id: str
    source_table: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"id": self.evidence_id, "table": self.source_table,
                "reason": self.reason, "detail": self.detail}


@dataclass
class EvidenceSet:
    """What survived, what did not, and the counts a reader needs to judge it."""

    rows: list[EvidenceRow] = field(default_factory=list)
    costs: list[CostObservation] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)

    #: True when this organization's connection has never stated a migration
    #: cut-over. Reported rather than assumed in either direction: with no date
    #: no row is treated as backfilled, and a reader has to be told that, because
    #: the silent alternative is a band built from a bulk load.
    backfill_cutover_unknown: bool = False

    def by_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.excluded:
            out[e.reason] = out.get(e.reason, 0) + 1
        return dict(sorted(out.items()))

    def by_class(self) -> dict[str, int]:
        out = {c: 0 for c in EVIDENCE_CLASSES}
        for r in self.rows:
            out[r.evidence_class] = out.get(r.evidence_class, 0) + 1
        return out

    def summary(self) -> dict:
        """The evidence summary §5 and §8 require, including the honest gaps."""
        classes = self.by_class()
        return {
            "usable": len(self.rows),
            "cost_observations": len(self.costs),
            "excluded": len(self.excluded),
            "excluded_by_reason": self.by_reason(),
            "by_class": classes,
            # Named on its own because §8 asks for it explicitly: if outcome
            # data is absent, say so rather than letting a band of realized
            # prices imply that all history is acceptance.
            "outcome_recorded": classes[QUOTED_WON] + classes[QUOTED_LOST],
            "unnormalizable": (self.by_reason().get(NO_UNIT, 0)
                               + self.by_reason().get(UNIT_MISMATCH, 0)),
            "backfill_cutover_unknown": self.backfill_cutover_unknown,
        }


# ── unit canonicalisation (§5) ───────────────────────────────────────────────
#
# §5 asks for a conversion factor and an exclusion when none exists. Applied
# literally to these books that excludes every row, because Zoho Books has no
# unit-conversion table at all — that is a Zoho *Inventory* feature these books
# do not use. The rule is still right; the hazard it guards is just a different
# one from the one it was written for.
#
# Within one item the unit is fixed by the master, so two transactions for the
# same item are already in the same unit and need no factor. The live hazard is
# *synonym units across items*: one book spells the same unit `pcs` on 456 items
# and `nos` on 57. A cross-item tier that treated those as different would split
# an item from its own sibling; one that ignored units entirely would one day
# compare a metre of bar stock against a piece of it.
#
# So: canonicalise the label, never invent a magnitude. Equal canonical labels
# are comparable; unequal ones are excluded and counted. A blank label is
# excluded and counted. No factor is ever guessed.

#: Spellings that denote a discrete count. Both live books use exactly this
#: concept and disagree only on how to spell it.
_EACH = frozenset({"nos", "no", "no.", "nos.", "pcs", "pc", "pcs.", "pc.",
                   "piece", "pieces", "each", "ea", "unit", "units", "qty"})


def canonical_unit(uom: Optional[str]) -> Optional[str]:
    """One token for a unit label, or ``None`` when the master does not say.

    Deliberately shallow: it folds the spellings of "a discrete thing" and
    lower-cases everything else. It does **not** know that a kilogram is a
    thousand grams, because knowing that would invite converting between them,
    and there is no factor in this data that says how many grams of a given item
    make one of anything.
    """
    if uom is None:
        return None
    token = " ".join(str(uom).split()).strip().lower()
    if not token:
        return None
    return "each" if token in _EACH else token


# ── the point-in-time filter ─────────────────────────────────────────────────

def is_knowable(recorded_at: Optional[datetime], *, knowable_by: datetime,
                backfill_before: Optional[date]) -> Optional[str]:
    """``None`` when this row was visible at ``knowable_by``, else the reason.

    Returns the reason rather than a bool so the caller cannot lose it. Every
    exclusion this engine makes is reported, and a predicate that answered
    ``False`` would put the reason's construction in the caller — where the
    three cases would eventually collapse into one.

    Strictly ``<``: a row recorded in the same instant as the quote was written
    is not evidence the quoter had. The boundary has to fall somewhere and it
    falls on the conservative side.

    Both sides go through ``clock.aware`` first. SQLite hands back a naive
    datetime from a ``DateTime(timezone=True)`` column, so a stored stamp and a
    caller's ``knowable_by`` are not comparable as they arrive — and the failure
    is a ``TypeError`` at the exact comparison the engine's integrity rests on.
    ``clock.aware`` is the platform's one answer to that; a second local
    normalisation here would be a second answer to it.
    """
    stamp = aware(recorded_at)
    cutoff = aware(knowable_by)
    if stamp is None or cutoff is None:
        return NO_RECORDED_AT
    if backfill_before is not None and stamp.date() < backfill_before:
        return BACKFILLED
    if stamp >= cutoff:
        return RECORDED_AFTER
    return None


def knowable_at(rows: Iterable[EvidenceRow], *, knowable_by: datetime,
                backfill_before: Optional[date] = None,
                ) -> tuple[list[EvidenceRow], list[Exclusion]]:
    """Split price evidence into what was visible at a moment and what was not.

    Sorted by ``(event_date, evidence_id)`` on the way out — a total order with
    the primary key as tie-break, so the band never depends on the order the
    database happened to return rows in (§13).
    """
    kept: list[EvidenceRow] = []
    dropped: list[Exclusion] = []
    for row in rows:
        reason = is_knowable(row.recorded_at, knowable_by=knowable_by,
                             backfill_before=backfill_before)
        if reason is None:
            kept.append(row)
        else:
            dropped.append(Exclusion(row.evidence_id, row.source_table, reason,
                                     _visibility_detail(row.recorded_at, reason)))
    kept.sort(key=lambda r: (r.event_date, r.evidence_id))
    dropped.sort(key=lambda e: (e.reason, e.evidence_id))
    return kept, dropped


def costs_knowable_at(rows: Iterable[CostObservation], *, knowable_by: datetime,
                      backfill_before: Optional[date] = None,
                      ) -> tuple[list[CostObservation], list[Exclusion]]:
    """The same filter over purchases. Separate because the types are separate.

    Not a generic over a protocol: two four-line functions are cheaper to read
    than the abstraction that unifies them, and the shared decision — which is
    the part that must not diverge — is ``is_knowable``, which both call.
    """
    kept: list[CostObservation] = []
    dropped: list[Exclusion] = []
    for row in rows:
        reason = is_knowable(row.recorded_at, knowable_by=knowable_by,
                             backfill_before=backfill_before)
        if reason is None:
            kept.append(row)
        else:
            dropped.append(Exclusion(row.evidence_id, row.source_table, reason,
                                     _visibility_detail(row.recorded_at, reason)))
    kept.sort(key=lambda r: (r.event_date, r.evidence_id))
    dropped.sort(key=lambda e: (e.reason, e.evidence_id))
    return kept, dropped


def impute_recorded_at(rows: Iterable[EvidenceRow], *, lag_days: int,
                       ) -> list[EvidenceRow]:
    """Estimate a missing visibility stamp as ``event_date + lag_days``.

    §3's documented, conservative fallback for historical rows a source never
    gave a creation stamp for, and it is **opt-in**: nothing in this engine calls
    it. The default is to exclude such a row and count it, because an estimate
    that lands in a band is indistinguishable from an observation once it is
    there — and the measured lag on these books has a p99 twenty times its own
    p90, so an estimate built from a percentile is wrong in the tail by months.

    ``lag_days`` is meant to be the p90 entry lag measured for *that* source and
    *that* document kind, not a constant: an invoice is authored in the ERP and
    lags by nothing, while a bill is transcribed and lags by days. One number for
    both would make the sell side look slower than it is and the buy side faster.

    Every row it touches is flagged, and the flag is what caps confidence. A row
    that already has a stamp is returned unchanged rather than overwritten —
    a measured visibility always beats an estimated one.
    """
    out: list[EvidenceRow] = []
    for row in rows:
        if row.recorded_at is not None:
            out.append(row)
            continue
        out.append(replace(
            row,
            recorded_at=datetime.combine(row.event_date + timedelta(days=lag_days),
                                         time.min, tzinfo=timezone.utc),
            recorded_at_imputed=True))
    return out


def _visibility_detail(recorded_at: Optional[datetime], reason: str) -> str:
    if reason == NO_RECORDED_AT:
        return "the source system gave no creation stamp"
    if reason == BACKFILLED:
        return f"bulk-loaded on {recorded_at.date().isoformat()}"  # type: ignore[union-attr]
    return f"recorded {recorded_at.isoformat()}"  # type: ignore[union-attr]


# ── normalisation ────────────────────────────────────────────────────────────

def normalize(rows: Iterable[EvidenceRow], *, subject_unit: Optional[str],
              ) -> tuple[list[EvidenceRow], list[Exclusion]]:
    """Keep the rows that are expressed in the subject's unit and are trades.

    ``subject_unit`` is the canonical unit of the item being quoted. A row whose
    unit is unknown, or is known and different, is excluded and counted — §5's
    ``unnormalizable`` bucket.

    **Currency is not converted here, and that is a guarantee rather than a
    gap.** No money row in this schema carries a currency, because
    ``ingestion.sync._refuses_currency`` rejects a document denominated in
    anything but the book's own currency *at the seam* — its docstring explains
    that a foreign document which gets past that point is indistinguishable from
    a domestic one forever after. So every row reaching here is already in one
    currency by construction, which is stronger than an as-of conversion would
    be, and an FX layer here would be code that can never run. The residual risk
    lives where the seam put it: documents that stated no currency at all are
    counted into the sync report's ``foreign_currency_unknown``.
    """
    kept: list[EvidenceRow] = []
    dropped: list[Exclusion] = []
    for row in rows:
        if row.qty <= 0 or row.unit_price <= 0:
            dropped.append(Exclusion(row.evidence_id, row.source_table, NOT_A_TRADE,
                                     "quantity or price is not positive"))
            continue
        unit = canonical_unit(row.unit)
        if unit is None:
            dropped.append(Exclusion(row.evidence_id, row.source_table, NO_UNIT,
                                     "the item master states no unit"))
            continue
        if subject_unit is not None and unit != subject_unit:
            dropped.append(Exclusion(
                row.evidence_id, row.source_table, UNIT_MISMATCH,
                f"{unit} against {subject_unit}, and no conversion factor exists"))
            continue
        kept.append(row)
    kept.sort(key=lambda r: (r.event_date, r.evidence_id))
    dropped.sort(key=lambda e: (e.reason, e.evidence_id))
    return kept, dropped


def for_baseline(rows: Iterable[EvidenceRow],
                 ) -> tuple[list[EvidenceRow], list[Exclusion]]:
    """The rows a price baseline may be built from (§8).

    ``QUOTED_LOST`` is held back rather than dropped — the caller needs it to
    damp opportunity — so it leaves here as an exclusion with its own reason and
    is read again by ``baselines.resistance``. ``QUOTED_OPEN`` is genuinely
    excluded: a quote nobody has answered says nothing about what is achievable.
    """
    kept: list[EvidenceRow] = []
    dropped: list[Exclusion] = []
    for row in rows:
        if row.evidence_class in BASELINE_CLASSES:
            kept.append(row)
        else:
            dropped.append(Exclusion(
                row.evidence_id, row.source_table, NO_OUTCOME,
                "a lost quote never raises the band"
                if row.evidence_class == QUOTED_LOST
                else "no outcome recorded"))
    return kept, dropped
