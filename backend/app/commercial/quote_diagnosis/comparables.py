"""Which evidence is comparable to the line being quoted, on two axes.

**The customer axis** answers "what has this customer paid for this thing". It
is tiered, most specific first, and it stops at the first tier with enough
evidence — a flat average across all history is the thing this replaces, because
the same customer buying five pieces and five hundred is answering two different
commercial questions and one number for both is the wrong reference for either.

**The peer axis** answers "what does everyone else pay", and it exists because
the customer axis alone is *descriptive, not normative*. A customer quoted low
for two years has a perfectly consistent history, and an engine that reads only
that history will report "consistent with history" and stay silent on a
structural leak. That is the most expensive failure this engine can have, so the
second band is computed independently and never blended into the first.

Both axes are pure functions over already-normalised, already-knowable evidence.
Nothing here filters by date visibility — ``evidence.knowable_at`` has run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from ..dispersion import quantile
from ..quantity import QuantityBand
from .evidence import EvidenceRow

_Q25 = Decimal("0.25")
_Q50 = Decimal("0.5")
_Q75 = Decimal("0.75")

# ── the tiers (§6.1) ─────────────────────────────────────────────────────────
#
# Numbered, not named-and-ordered-by-accident: the number is what "tier 1–2" in
# the strength table means, it is persisted in the diagnosis, and a tier that
# changed rank between releases would silently re-judge old evidence.

TIER_SAME_CUSTOMER_SKU_BAND = 1       # same customer + same SKU + same band
TIER_SAME_CUSTOMER_SKU_ADJACENT = 2   # same customer + same SKU + adjacent band
TIER_SAME_CUSTOMER_FAMILY_BAND = 3    # same customer + same family + same band
TIER_SEGMENT_SKU_BAND = 4             # same segment, other customers, SKU, band
TIER_ANY_CUSTOMER_SKU_BAND = 5        # any customer + same SKU + same band
TIER_FAMILY = 6                       # product family, any customer, any band

#: Tiers on which the quantity band is a **hard filter** rather than a weight.
#: A ten-piece and a five-hundred-piece transaction for the same insert are
#: different commercial events, and the specific tiers are the ones a diagnosis
#: is allowed to be confident from — so this is where the rule has to bite.
BAND_FILTERED_TIERS = frozenset({TIER_SAME_CUSTOMER_SKU_BAND,
                                 TIER_SAME_CUSTOMER_SKU_ADJACENT,
                                 TIER_SAME_CUSTOMER_FAMILY_BAND})

#: The tiers the evidence-strength table calls "specific". Tier 3 leaves the SKU
#: for the family, which is why STRONG needs 1–2 and MODERATE allows 1–3.
SPECIFIC_TIERS = frozenset({TIER_SAME_CUSTOMER_SKU_BAND,
                            TIER_SAME_CUSTOMER_SKU_ADJACENT,
                            TIER_SAME_CUSTOMER_FAMILY_BAND})


@dataclass(frozen=True)
class Subject:
    """The line being diagnosed, reduced to what comparison needs.

    No price on it. What was quoted is an input to the *rules*, not to the
    selection of evidence — a selector that could see the quoted price could be
    written, one careless day, to prefer the evidence that agrees with it.
    """

    customer_id: Optional[str]
    product_id: str
    #: The product family, as the caller resolved it. A string rather than a
    #: model reference so this layer stays pure and so the *choice* of family
    #: key — the item master's own category today, the parser's decoded
    #: designation later — is the caller's to change without touching this.
    family: Optional[str]
    qty: Decimal
    band: QuantityBand
    #: Canonical unit; ``evidence.normalize`` has already excluded anything else.
    unit: Optional[str]
    #: The moment the quote was written. Recency is measured back from here, not
    #: from today: re-running an old diagnosis must produce the old answer.
    as_of: date


@dataclass(frozen=True)
class Comparable:
    """One evidence row, with the tier that admitted it."""

    row: EvidenceRow
    tier: int


@dataclass(frozen=True)
class BandStats:
    """The shape of a set of comparable prices. Order statistics only.

    No mean, anywhere. A mean over commercial prices is one distress deal away
    from being wrong, and the trim in ``baselines`` is built to remove exactly
    the observations a mean is most sensitive to.
    """

    low: Optional[Decimal]
    high: Optional[Decimal]
    median: Optional[Decimal]
    p25: Optional[Decimal]
    p75: Optional[Decimal]
    sample_count: int
    recent_sample_count: int

    @property
    def iqr_over_median(self) -> Optional[float]:
        """Dispersion, as the strength table reads it. ``None`` without a band."""
        if self.median is None or self.p25 is None or self.p75 is None:
            return None
        if self.median <= 0:
            return None
        return float((self.p75 - self.p25) / self.median)

    def to_dict(self) -> dict:
        def m(v: Optional[Decimal]) -> Optional[float]:
            return float(round(v, 2)) if v is not None else None
        return {"low": m(self.low), "high": m(self.high), "median": m(self.median),
                "p25": m(self.p25), "p75": m(self.p75),
                "sample_count": self.sample_count,
                "recent_sample_count": self.recent_sample_count,
                "iqr_over_median": (round(self.iqr_over_median, 4)
                                    if self.iqr_over_median is not None else None)}


EMPTY_STATS = BandStats(None, None, None, None, None, 0, 0)


def stats(rows: Sequence[EvidenceRow], *, as_of: date,
          recent_days: int) -> BandStats:
    """Order statistics over a set of comparables.

    ``recent_sample_count`` is measured on ``event_date`` — when the trade
    happened — because that is what "recent" means commercially. Visibility was
    settled upstream and is not this function's question.
    """
    if not rows:
        return EMPTY_STATS
    prices = [r.unit_price for r in rows]
    ordered = sorted(prices)
    cutoff = as_of - timedelta(days=recent_days)
    return BandStats(
        low=ordered[0],
        high=ordered[-1],
        median=quantile(ordered, _Q50),
        p25=quantile(ordered, _Q25),
        p75=quantile(ordered, _Q75),
        sample_count=len(ordered),
        recent_sample_count=sum(1 for r in rows if r.event_date > cutoff),
    )


@dataclass
class CustomerAxis:
    """Every comparable the customer axis found, with its tier.

    Reports the whole set *and* the specific tiers separately, as §6.1 asks: a
    band computed over tier 6 is a category-level sanity check and a band
    computed over tiers 1–3 is what this customer actually pays, and a card that
    conflated them would claim the second while showing the first.
    """

    subject: Subject
    comparables: list[Comparable] = field(default_factory=list)

    def at_tiers(self, tiers: frozenset[int]) -> list[EvidenceRow]:
        return [c.row for c in self.comparables if c.tier in tiers]

    @property
    def rows(self) -> list[EvidenceRow]:
        return [c.row for c in self.comparables]

    def tier_counts(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for c in self.comparables:
            out[c.tier] = out.get(c.tier, 0) + 1
        return dict(sorted(out.items()))

    @property
    def best_tier(self) -> Optional[int]:
        """The most specific tier that produced anything."""
        return min((c.tier for c in self.comparables), default=None)


def select_customer_axis(rows: Iterable[EvidenceRow], *, subject: Subject,
                         bands: Sequence[QuantityBand],
                         segment: Optional[frozenset[str]] = None,
                         ) -> CustomerAxis:
    """Assign each row its most specific tier, or drop it as not comparable.

    One pass, one tier per row — ``min`` of the tiers it qualifies for. A row
    that appeared at several tiers would be counted several times in the
    strength table, which is how a single transaction becomes "eight
    comparables".

    ``segment`` is the set of customer ids the caller considers comparable to
    the subject (an ``EntityGroup`` roster). ``None`` means no segment has been
    drawn, and tier 4 is then empty rather than silently meaning "everyone" —
    tier 5 already means everyone and a tier that quietly duplicated it would
    inflate every count the strength table reads.
    """
    band_index = {b.index: b for b in bands}
    subject_index = subject.band.index
    adjacent = {subject_index - 1, subject_index + 1} & set(band_index)

    out: list[Comparable] = []
    for row in rows:
        tier = _tier_for(row, subject=subject, bands=bands,
                         subject_index=subject_index, adjacent=adjacent,
                         segment=segment)
        if tier is not None:
            out.append(Comparable(row=row, tier=tier))
    # Total order: tier, then event date, then primary key (§13).
    out.sort(key=lambda c: (c.tier, c.row.event_date, c.row.evidence_id))
    return CustomerAxis(subject=subject, comparables=out)


def _tier_for(row: EvidenceRow, *, subject: Subject,
              bands: Sequence[QuantityBand], subject_index: int,
              adjacent: set[int], segment: Optional[frozenset[str]],
              ) -> Optional[int]:
    """The most specific tier this row qualifies for, or ``None``."""
    same_customer = (subject.customer_id is not None
                     and row.customer_id == subject.customer_id)
    same_sku = row.product_id == subject.product_id
    row_index = _band_index(row.qty, bands)
    same_band = row_index == subject_index
    adjacent_band = row_index in adjacent

    if same_customer and same_sku and same_band:
        return TIER_SAME_CUSTOMER_SKU_BAND
    if same_customer and same_sku and adjacent_band:
        return TIER_SAME_CUSTOMER_SKU_ADJACENT
    # Tier 3 leaves the SKU but keeps the band, which is why it stays inside the
    # hard-filtered set: a family is already a looser claim, and letting the
    # quantity loose at the same time would make it meaningless.
    if same_customer and _same_family(row, subject) and same_band:
        return TIER_SAME_CUSTOMER_FAMILY_BAND
    if (segment is not None and same_sku and same_band
            and row.customer_id is not None
            and row.customer_id != subject.customer_id
            and row.customer_id in segment):
        return TIER_SEGMENT_SKU_BAND
    if same_sku and same_band:
        return TIER_ANY_CUSTOMER_SKU_BAND
    if _same_family(row, subject):
        return TIER_FAMILY
    return None


def _same_family(row: EvidenceRow, subject: Subject) -> bool:
    """Family membership, and ``False`` whenever the family is unknown.

    A row with no family and a subject with no family are not "the same family".
    They are two items nobody has classified, and treating a shared absence as a
    match is how an unclassified catalogue becomes one enormous comparable set —
    15% of the items on one live master carry no category at all.
    """
    return (subject.family is not None and row_family(row) is not None
            and row_family(row) == subject.family)


#: How a row reports its family. A module-level hook rather than a field on
#: ``EvidenceRow`` because the family is a property of the *product*, resolved by
#: the caller, and the row carries the product id. The caller stamps it into the
#: row's ``source_ref`` under this key when it builds the evidence.
FAMILY_KEY = "family"


def row_family(row: EvidenceRow) -> Optional[str]:
    value = (row.source_ref or {}).get(FAMILY_KEY)
    return str(value) if value else None


def _band_index(qty: Decimal, bands: Sequence[QuantityBand]) -> int:
    for band in bands:
        if band.contains(qty):
            return band.index
    return bands[-1].index if bands else 0


# ── the peer axis (§6.2) ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class PeerBand:
    """What comparable customers pay for the same item at the same quantity.

    **Restricted.** A peer's price is another customer's commercial position, and
    the diagnosis that reads this one routes to the owner. The operations view is
    built from a type that has no field to put it in.
    """

    stats: BandStats
    customer_count: int
    #: True when no segment was drawn and this is "every other customer". Said
    #: out loud rather than left to be inferred: a peer band over the whole book
    #: is a weaker claim than one over a segment, and the reader has to know
    #: which they are looking at.
    segment_applied: bool
    rows: tuple[EvidenceRow, ...] = ()

    def to_dict(self) -> dict:
        return {"band": self.stats.to_dict(),
                "customer_count": self.customer_count,
                "segment_applied": self.segment_applied}


EMPTY_PEER = PeerBand(stats=EMPTY_STATS, customer_count=0, segment_applied=False)


def select_peer_axis(rows: Iterable[EvidenceRow], *, subject: Subject,
                     bands: Sequence[QuantityBand],
                     segment: Optional[frozenset[str]] = None,
                     recent_days: int) -> PeerBand:
    """The independent band: same SKU, same quantity band, **not** this customer.

    Independent is the operative word. It is not tier 4 or tier 5 of the customer
    axis read back under another name — those feed the customer band when the
    specific tiers are thin, and blending the two is exactly what hides a
    structurally under-priced account. This band is computed from its own rows
    and compared against the customer's band as a whole.

    The subject's own rows are excluded, for the reason ``benchmark.py`` already
    gives about a benchmark a customer is part of: it pulls toward the customer
    and understates every deviation, and on a two-customer item it is nearly
    self-referential.
    """
    picked = [
        r for r in rows
        if r.product_id == subject.product_id
        and _band_index(r.qty, bands) == subject.band.index
        and r.customer_id is not None
        and r.customer_id != subject.customer_id
        and (segment is None or r.customer_id in segment)
    ]
    picked.sort(key=lambda r: (r.event_date, r.evidence_id))
    return PeerBand(
        stats=stats(picked, as_of=subject.as_of, recent_days=recent_days),
        customer_count=len({r.customer_id for r in picked}),
        segment_applied=segment is not None,
        rows=tuple(picked),
    )
