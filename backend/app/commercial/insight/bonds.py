"""How strong the tie is between this book and each counterparty, over time.

A distributor's real asset is not its stock, it is who keeps coming back. That
is a thing everyone in the business has an opinion about and nobody can point
at, so this module measures it — for customers and for suppliers, in the same
shape, from rows the platform already holds.

**The score is a composite of five published facets, never a black box.** A
single number with hidden weights would be exactly the kind of output §1 exists
to prevent: authoritative-looking and unauditable. So every facet is computed
here, returned alongside the score, and the weights live in
``CommercialThresholds`` — inside the version hash with every other policy
number. Re-tune them and last quarter's bonds stay explainable, because the
version that produced them says what the weights were.

The five, each a ratio in [0, 1] where 1 is the strongest:

``recency``      where they sit in *their own* rhythm — not a fixed number of
                 days. A quarterly buyer is not late in week six.
``consistency``  the share of eligible months they actually traded in. Regular
                 trade is the single most honest evidence of a live tie.
``breadth``      how many of the **lines of the business** they take, out of
                 how many there are. See below — this one changed.
``weight``       their share of this company's book, saturating. Concentration
                 is part of a bond — it is also what makes losing one hurt.
``reliability``  whether they do the thing they promised: pay, for a customer;
                 deliver, for a supplier.

**Breadth counts categories, not SKUs, and that is not a refinement.** It
counted distinct items first, which made twelve cutting-tool inserts score
higher than four items spread across cutting tools, coolant, metrology and
machines. For a distributor trying to grow product mix that is precisely
backwards: the second customer is the deeper relationship and the harder one to
displace, because a competitor has to beat four lines instead of one. So breadth
is now coverage — categories bought ÷ categories sold — and the bond score
rewards the thing the business is actually trying to grow. It also needs no
saturation constant, because the denominator is real.

Items the catalogue cannot place fall in ``UNCATEGORISED`` and are excluded from
both sides of that ratio rather than counted against anybody. A customer is not
narrow because the platform failed to categorise what they bought.

**Momentum is deliberately not a facet.** The frames *are* the trajectory, and
scoring the slope as well would count the same movement twice — a relationship
that recovered would read as both "strong now" and "improving", inflating it
above one that has simply always been strong.

**Below the evidence floor there is no score.** A counterparty with two
documents has no rhythm to be measured against, and ``min_transactions`` already
says what this platform considers enough. Such a bond is returned unscored, with
the reason, rather than given a number derived from one coincidence — lowering
the bar to fill a chart is what §1 calls a defect.

**The bands do not decide who is dormant.** ``cadence.py`` records why: a screen
calling somebody dormant while the decision queue stays silent is a
disagreement nobody can debug from outside. So the bands here are about the
score and are named for it, and the *overdue* flag on every bond comes straight
from ``aggregates.Cadence`` — the same rule ``signals/dormancy.py`` raises on.

**One implementation, two sides.** Customers and suppliers differ in where the
rows come from and in what "reliable" means, and in nothing else — so the
caller normalises both into ``TradeLine`` and ``Reliability`` and everything
below is shared. Two mirrored copies would agree until the first tuning.

**What a supplier's reliability can and cannot be.** ``supply.py`` records that
promised delivery dates are absent from this book, so "late against promise" has
no promise side. What is answerable is whether their open orders go stale, and
that is what is measured. Whether *we* pay *them* on time is the other half of a
supplier bond and is deliberately **not** in the score: ``BillDoc.balance`` is a
current balance with no payment date behind it, so it cannot be reconstructed
for a past month, and a facet that silently means something different in the
last frame than in the first would make the whole play a lie. It is returned
beside the score as a present-day fact instead.

Layer rules, inherited: this is ``commercial/``, so it is deterministic and
never imports ``ai/``. Nothing here computes a margin, and nothing here reads
one.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from ...signals import aggregates as agg
from ...signals.config import SignalThresholds, load_thresholds as load_signal_thresholds
from ..categories import UNCATEGORISED
from ..config import CommercialThresholds
from . import absence, payments, periods

#: Which side of the book a bond describes. The word "side" rather than "type"
#: because it is the same measurement pointed in two directions.
CUSTOMER = "customer"
VENDOR = "vendor"

#: How far back the play runs by default, in whole calendar months.
DEFAULT_MONTHS = 24

#: How many counterparties get a frame series.
#:
#: This started at 80 on the belief that 300 moving dots is not a thing anybody
#: reads. That was true of the radial layout it was written for — and measuring
#: the strip that replaced it showed the readability argument had evaporated,
#: while the cap quietly stayed: the chart drew 80 dots under a lane labelled
#: 139, which is the silent truncation §6 says to report rather than apply.
#:
#: So it is now a *payload* bound and nothing else, set well above a real book's
#: counterparty count. The current-state list has never been capped.
DEFAULT_FRAME_COVER = 400

#: How much of the book one counterparty must hold for ``weight`` to saturate.
#: A tenth is a lot in distribution; above it, more concentration does not make
#: the tie meaningfully stronger, it makes it more dangerous — which is a
#: different screen's question.
WEIGHT_SATURATION = 0.10

#: How long trailing weight is measured over. Lifetime share would let a
#: relationship that ended three years ago keep its weight forever.
WEIGHT_WINDOW_MONTHS = 12

#: Score bands. Upper-exclusive lower edges on a 0–100 score, strongest first.
#: Named for the tie itself and never for dormancy — see the module docstring.
BANDS: tuple[tuple[str, float, str], ...] = (
    ("ANCHORED", 70.0, "Trading steadily, across a real spread of the "
                       "catalogue, and material to this book. Losing one of "
                       "these is a bad quarter."),
    ("STEADY", 50.0, "A working relationship on its own rhythm. Nothing to "
                     "fix; worth knowing which these are before changing "
                     "anything that touches them."),
    ("LOOSENING", 30.0, "Still trading, but thinner or less regular than the "
                        "book's stronger ties. This is the band where a "
                        "conversation still costs less than a recovery."),
    ("THIN", 0.0, "Occasional, narrow, or small. Not necessarily a problem — "
                  "most books have a long tail — but not something to plan on."),
)


def band_of(score: float) -> str:
    for name, edge, _ in BANDS:
        if score >= edge:
            return name
    return BANDS[-1][0]


def bands() -> list[dict]:
    """The bands, described. Rendered as a legend rather than explained twice."""
    return [{"band": name, "min_score": edge, "meaning": meaning}
            for name, edge, meaning in BANDS]


# ── inputs ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TradeLine:
    """One line of trade with one counterparty, on one day.

    The common denominator of an invoice line and a bill line. ``amount`` is
    revenue on the customer side and spend on the supplier side, which is why
    the supplier view is manager-and-above: spend is purchase cost by another
    name, and this module does not decide who may read it — the router does.
    """

    counterparty_id: str
    date: date
    amount: float
    item_id: str
    #: The document this line belongs to. Distinct documents are what "an
    #: order" means for cadence, so a forty-line bill counts once.
    document_ref: str
    #: Which line of the business the item belongs to, already resolved by
    #: ``commercial/categories.py``. Resolved by the caller rather than here
    #: because the resolution needs the product master and an override table,
    #: and this module takes facts rather than fetching them.
    #:
    #: ``None`` or ``UNCATEGORISED`` means the catalogue could not place it —
    #: excluded from coverage rather than counted against the counterparty.
    category: Optional[str] = None


@dataclass(frozen=True)
class Reliability:
    """Whether a counterparty does the thing they promised, as a ratio.

    ``score`` is None where there is nothing to judge on — no due dates on a
    customer's invoices, no orders on a supplier. None is not zero: an
    unmeasured counterparty is not an unreliable one, and the composite drops
    the facet and says so rather than scoring them down for our missing data.

    ``observed_upto`` makes the facet reconstructible for a past frame. Each
    entry is (day, ratio) — the ratio as it stood on that day — so a frame at
    month M reads the last entry at or before M. Anything that cannot supply
    that history supplies a single present-day entry and is excluded from
    earlier frames instead of being back-projected onto them.
    """

    score: Optional[float]
    observations: int
    detail: dict = field(default_factory=dict)
    observed_upto: tuple[tuple[date, float], ...] = ()

    def as_of(self, day: date) -> Optional[float]:
        if not self.observed_upto:
            return None
        best: Optional[float] = None
        for when, ratio in self.observed_upto:
            if when <= day:
                best = ratio
            else:
                break
        return best


# ── the facets ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Facets:
    """The five, each in [0, 1] or None where it cannot be measured."""

    recency: Optional[float]
    consistency: Optional[float]
    breadth: Optional[float]
    weight: Optional[float]
    reliability: Optional[float]

    def to_dict(self) -> dict:
        return {
            "recency": _round(self.recency), "consistency": _round(self.consistency),
            "breadth": _round(self.breadth), "weight": _round(self.weight),
            "reliability": _round(self.reliability),
        }

    def missing(self) -> list[str]:
        return [name for name, value in
                (("recency", self.recency), ("consistency", self.consistency),
                 ("breadth", self.breadth), ("weight", self.weight),
                 ("reliability", self.reliability)) if value is None]


def _round(v: Optional[float]) -> Optional[float]:
    return round(v, 4) if v is not None else None


def recency_of(overdue_ratio: Optional[float]) -> Optional[float]:
    """Position in their own rhythm, as a ratio that decays rather than cuts.

    ``overdue_ratio`` is days-since ÷ their own expected interval, computed by
    ``aggregates.cadence_of``. At or inside the rhythm this is full marks: a
    customer who orders every ninety days and last ordered eighty days ago is
    not weakening. Past it the score is the reciprocal — twice their interval
    halves it, four times quarters it — so a bond decays smoothly instead of
    falling off a cliff on the day a threshold is crossed.
    """
    if overdue_ratio is None:
        return None
    if overdue_ratio <= 1.0:
        return 1.0
    return 1.0 / overdue_ratio


def consistency_of(active_months: int, eligible_months: int) -> Optional[float]:
    """Share of the months they could have traded in that they did.

    Eligible months run from their first document, not from the start of the
    window: a customer who first bought two months ago has not missed the ten
    before that, and scoring them as though they had would mark every new
    relationship as weak on the day it starts.
    """
    if eligible_months <= 0:
        return None
    return min(1.0, active_months / eligible_months)


def breadth_of(categories_bought: int, categories_sold: int) -> Optional[float]:
    """How many lines of the business they take, out of how many there are.

    A real ratio with a real denominator, which is why there is no curve and no
    constant to justify. One line out of five is a fragile relationship however
    large it is — a single purchasing decision ends it — and five out of five is
    a customer a competitor has to beat five times.

    ``None`` when nothing is categorised at all: that is a gap in the catalogue,
    not a narrow customer, and scoring it as zero would blame somebody for the
    platform's own missing data.
    """
    if categories_sold <= 0:
        return None
    return min(1.0, categories_bought / categories_sold)


def weight_of(share: Optional[float]) -> Optional[float]:
    """Their share of this company's book, saturating at ``WEIGHT_SATURATION``.

    Raw share would contribute almost nothing — a healthy book has no customer
    at 50% — so it is scaled against what counts as a lot rather than against
    the largest counterparty present. Against the largest would make one
    relationship's facet move when a *different* one grew.
    """
    if share is None:
        return None
    return min(1.0, max(0.0, share) / WEIGHT_SATURATION)


def composite(facets: Facets, weights: dict[str, float]) -> Optional[float]:
    """The 0–100 score: a weighted mean over the facets that are measurable.

    Renormalised over what is present rather than scoring a missing facet as
    zero. A customer whose invoices carry no due dates has *unknown* payment
    behaviour, and reading that as "unreliable" would punish them for a gap in
    Zoho. The response carries ``missing`` so a reader knows which question the
    score did not answer.
    """
    pairs = [(weights.get(name, 0.0), value)
             for name, value in (("recency", facets.recency),
                                 ("consistency", facets.consistency),
                                 ("breadth", facets.breadth),
                                 ("weight", facets.weight),
                                 ("reliability", facets.reliability))
             if value is not None]
    total = sum(w for w, _ in pairs)
    if total <= 0:
        return None
    return round(100.0 * sum(w * v for w, v in pairs) / total, 1)


# ── the result ───────────────────────────────────────────────────────────────
@dataclass
class Bond:
    counterparty_id: str
    label: str
    side: str
    score: Optional[float]
    band: Optional[str]
    facets: Facets
    money: float
    share: Optional[float]
    documents: int
    distinct_items: int
    #: The lines they take, and how many there are to take. The pair rather
    #: than the ratio, because "two of five" is what a person acts on and a
    #: bare 0.4 is not.
    categories: list[str]
    categories_sold: int
    first_traded: Optional[date]
    last_traded: Optional[date]
    #: Straight from ``aggregates.Cadence`` — the dormancy detector's own rule,
    #: not a second one derived from the score.
    overdue: bool
    typical_interval_days: Optional[float]
    sector: str
    #: Why there is no score, when there is none. A dot nobody can explain is a
    #: dot nobody trusts.
    unscored_reason: Optional[str] = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "counterparty_id": self.counterparty_id, "label": self.label,
            "side": self.side, "score": self.score, "band": self.band,
            "facets": self.facets.to_dict(), "missing_facets": self.facets.missing(),
            "money": round(self.money, 2),
            "share": _round(self.share),
            "documents": self.documents, "distinct_items": self.distinct_items,
            "categories": self.categories, "categories_sold": self.categories_sold,
            "first_traded": self.first_traded.isoformat() if self.first_traded else None,
            "last_traded": self.last_traded.isoformat() if self.last_traded else None,
            "overdue": self.overdue,
            "typical_interval_days": self.typical_interval_days,
            "sector": self.sector,
            "unscored_reason": self.unscored_reason,
            "detail": self.detail,
        }


# ── the build ────────────────────────────────────────────────────────────────
def build(lines: Iterable[TradeLine], names: dict[str, str], as_of: date, *,
          side: str,
          thresholds: CommercialThresholds,
          categories_sold: Optional[Iterable[str]] = None,
          reliability: Optional[dict[str, Reliability]] = None,
          signal_thresholds: Optional[SignalThresholds] = None,
          months: int = DEFAULT_MONTHS,
          frame_cover: int = DEFAULT_FRAME_COVER) -> dict:
    """Every counterparty's bond now, and the same measure at each month-end.

    One pass over the lines, then one pass per month over the counterparties —
    not a full recomputation per frame. Twenty-four frames over a book of three
    hundred counterparties is ``O(lines + 24 × 300)``, which is why the play can
    be computed on request instead of needing a table of its own.
    """
    rows = list(lines)
    sig = signal_thresholds or load_signal_thresholds()
    rel = reliability or {}
    window = periods.months_back(as_of, months)
    if not rows:
        return _empty(as_of, side, window, thresholds)

    by_party = agg.group_by(rows, lambda r: r.counterparty_id)
    # How many lines there are to take. Given by the caller rather than derived
    # from what happens to have been traded: a line nobody has bought yet is
    # still a line on offer, and deriving the denominator from the data would
    # make coverage look complete on exactly the book with the worst mix.
    sold = _sold(categories_sold, rows)
    sectors = _sectors(rows)
    weights = _weights(thresholds)

    # Book totals per month, the denominator ``weight`` is a share of. Computed
    # over the same window the frames use so the share in frame i is the share
    # as the book stood then, not as it stands today.
    book_monthly = [0.0] * len(window)
    for row in rows:
        i = _month_index(row.date, window)
        if i is not None:
            book_monthly[i] += row.amount
    book_trailing = _trailing(book_monthly, WEIGHT_WINDOW_MONTHS)

    bonds: list[Bond] = []
    series: dict[str, list[dict]] = {}
    for party, party_rows in by_party.items():
        party_rows.sort(key=lambda r: r.date)
        bond, points = _one(party, party_rows, names, as_of, side=side,
                            window=window, book_trailing=book_trailing,
                            weights=weights, thresholds=thresholds,
                            signal_thresholds=sig,
                            reliability=rel.get(party),
                            sector=sectors.get(party, _OTHER), sold=sold)
        bonds.append(bond)
        series[party] = points

    bonds.sort(key=lambda b: (-(b.score if b.score is not None else -1.0), -b.money))
    total = sum(b.money for b in bonds)
    for b in bonds:
        b.share = (b.money / total) if total else None

    # The animated set. Ranked by money rather than by score: the play is about
    # what moving would cost, and a dot worth nothing moving a long way is not
    # the story. The cap is reported, never silent.
    covered = sorted(bonds, key=lambda b: -b.money)[:frame_cover]
    frames = _frames(window, [b.counterparty_id for b in covered], series)

    scored = [b for b in bonds if b.score is not None]
    return {
        "as_of": as_of.isoformat(),
        "side": side,
        "bonds": [b.to_dict() for b in bonds],
        "frames": frames,
        "bands": bands(),
        "weights": weights,
        "sectors": sorted({b.sector for b in bonds}),
        "counts": {
            "counterparties": len(bonds),
            "scored": len(scored),
            "unscored": len(bonds) - len(scored),
            "overdue": sum(1 for b in bonds if b.overdue),
            "frames": len(frames),
            "frames_cover": len(covered),
        },
        "total_money": round(total, 2),
        "months": months,
        "min_documents": thresholds.min_transactions,
        "thresholds_version": thresholds.version,
    }


def _empty(as_of: date, side: str, window: list[periods.Period],
           th: CommercialThresholds) -> dict:
    return {
        "as_of": as_of.isoformat(), "side": side, "bonds": [], "frames": [],
        "bands": bands(), "weights": _weights(th), "sectors": [],
        "counts": {"counterparties": 0, "scored": 0, "unscored": 0, "overdue": 0,
                   "frames": 0, "frames_cover": 0},
        "total_money": 0.0, "months": len(window),
        "min_documents": th.min_transactions,
        "thresholds_version": th.version,
    }


def _weights(th: CommercialThresholds) -> dict[str, float]:
    """Policy, not preference — and inside the version hash with the rest."""
    return {
        "recency": th.bond_weight_recency,
        "consistency": th.bond_weight_consistency,
        "breadth": th.bond_weight_breadth,
        "weight": th.bond_weight_share,
        "reliability": th.bond_weight_reliability,
    }


def _one(party: str, rows: list[TradeLine], names: dict[str, str], as_of: date, *,
         side: str, window: list[periods.Period], book_trailing: list[float],
         weights: dict[str, float], thresholds: CommercialThresholds,
         signal_thresholds: SignalThresholds,
         reliability: Optional[Reliability],
         sector: str, sold: int) -> tuple[Bond, list[dict]]:
    """One counterparty: their bond today, and one point per month behind it."""
    doc_days = _document_days(rows)
    monthly = [0.0] * len(window)
    for row in rows:
        i = _month_index(row.date, window)
        if i is not None:
            monthly[i] += row.amount
    trailing = _trailing(monthly, WEIGHT_WINDOW_MONTHS)

    # Walked forward once, accumulating. ``items_upto[i]`` is how many distinct
    # items had been traded by the end of month i — a running set, not a
    # re-scan per frame.
    items_upto: list[int] = []
    cats_upto: list[int] = []
    seen: set[str] = set()
    seen_cats: set[str] = set()
    cursor = 0
    ordered = sorted(rows, key=lambda r: r.date)
    for period in window:
        while cursor < len(ordered) and ordered[cursor].date <= period.end:
            line = ordered[cursor]
            seen.add(line.item_id)
            if line.category and line.category != UNCATEGORISED:
                seen_cats.add(line.category)
            cursor += 1
        items_upto.append(len(seen))
        cats_upto.append(len(seen_cats))

    first = ordered[0].date
    points: list[dict] = []
    for i, period in enumerate(window):
        if period.end < first:
            # Not yet a relationship. A zero here would draw a bond forming
            # before anybody traded.
            points.append({})
            continue
        # Never evaluated past ``as_of``. The last period in the window is the
        # month *containing* the reference date, so its end is usually still in
        # the future — and reading "days since their last order" at a future
        # date charges a relationship for time that has not passed. It made the
        # final frame score lower than the same bond in the list beside it,
        # which reads as a rounding bug and is really a claim about tomorrow.
        when = min(period.end, as_of)
        facets = _facets_at(
            doc_days, when, first=first, window=window, upto=i,
            monthly=monthly, trailing=trailing, book_trailing=book_trailing,
            categories_bought=cats_upto[i], categories_sold=sold,
            reliability=reliability,
            thresholds=thresholds, signal_thresholds=signal_thresholds)
        # The same evidence floor the current view applies, applied to the frame
        # as it stood. Without it the play scores a relationship the list beside
        # it refuses to score, and a screen that disagrees with itself is worse
        # than either half being wrong alone.
        score = (composite(facets, weights)
                 if bisect_right(doc_days, when) >= thresholds.min_transactions
                 else None)
        points.append({
            "score": score,
            "band": band_of(score) if score is not None else None,
            "money": round(sum(monthly[: i + 1]), 2),
        })

    # Today's bond is the last frame's facets recomputed against ``as_of``
    # rather than against the last month end, so "days since" is today's answer.
    facets = _facets_at(
        doc_days, as_of, first=first, window=window, upto=len(window) - 1,
        monthly=monthly, trailing=trailing, book_trailing=book_trailing,
        categories_bought=len(seen_cats), categories_sold=sold,
        reliability=reliability,
        thresholds=thresholds, signal_thresholds=signal_thresholds)
    cadence = agg.cadence_of(
        _as_sale_rows(doc_days), as_of,
        min_orders=signal_thresholds.dormancy_min_orders,
        multiplier=signal_thresholds.dormancy_interval_multiplier)
    score = composite(facets, weights)
    reason = None
    if score is None or len(doc_days) < thresholds.min_transactions:
        score = None
        reason = (f"{len(doc_days)} document(s) on record — fewer than the "
                  f"{thresholds.min_transactions} this platform treats as "
                  f"enough to describe a relationship.")

    detail = dict(reliability.detail) if reliability else {}
    bond = Bond(
        counterparty_id=party,
        label=agg.label_for(names, party, kind=side),
        side=side, score=score,
        band=band_of(score) if score is not None else None,
        facets=facets, money=sum(r.amount for r in rows), share=None,
        documents=len(doc_days), distinct_items=len(seen),
        categories=sorted(seen_cats), categories_sold=sold,
        first_traded=first, last_traded=ordered[-1].date,
        overdue=cadence.overdue,
        typical_interval_days=cadence.typical_interval_days,
        sector=sector, unscored_reason=reason, detail=detail)
    return bond, points


def _facets_at(doc_days: list[date], when: date, *, first: date,
               window: list[periods.Period], upto: int, monthly: list[float],
               trailing: list[float], book_trailing: list[float],
               categories_bought: int, categories_sold: int,
               reliability: Optional[Reliability],
               thresholds: CommercialThresholds,
               signal_thresholds: SignalThresholds) -> Facets:
    """The five facets as they stood on one day."""
    # Only the documents that existed then. ``doc_days`` is sorted, so this is a
    # slice rather than a filter — the difference between one pass and F passes.
    upto_docs = doc_days[: bisect_right(doc_days, when)]
    cadence = agg.cadence_of(
        _as_sale_rows(upto_docs), when,
        min_orders=signal_thresholds.dormancy_min_orders,
        multiplier=signal_thresholds.dormancy_interval_multiplier)

    eligible = _months_between(first, when)
    active = sum(1 for i in range(upto + 1)
                 if monthly[i] > 0 and window[i].end >= first)
    share = ((trailing[upto] / book_trailing[upto])
             if book_trailing[upto] > 0 else None)

    return Facets(
        recency=recency_of(cadence.overdue_ratio),
        consistency=(consistency_of(active, eligible)
                     if eligible >= thresholds.min_history_months else None),
        breadth=breadth_of(categories_bought, categories_sold),
        weight=weight_of(share),
        reliability=(reliability.as_of(when) if reliability else None),
    )


def _frames(window: list[periods.Period], covered: list[str],
            series: dict[str, list[dict]]) -> list[dict]:
    """One entry per month: the label, and every covered bond's place in it.

    A counterparty with no point in a month is *absent* from that frame rather
    than present at zero — they had not started trading yet, and a dot at the
    outer edge would read as a relationship that was weak, not one that did not
    exist.
    """
    out: list[dict] = []
    for i, period in enumerate(window):
        entries = []
        for party in covered:
            point = series.get(party, [])
            if i < len(point) and point[i]:
                entries.append({"counterparty_id": party, **point[i]})
        out.append({"label": period.label, "end": period.end.isoformat(),
                    "bonds": entries})
    return out


# ── the reliability adapters ─────────────────────────────────────────────────
#
# Each turns one already-computed view into the common ``Reliability`` shape.
# They live here rather than in the router because "what counts as reliable" is
# a commercial judgement, and a router that made it would be computing.


def reliability_from_payments(
        settlements: Iterable["payments.Settlement"]) -> dict[str, Reliability]:
    """Customer reliability: the share of datable invoices settled on time.

    Takes settlements rather than ``payments.build``'s summary because a bond
    has to be reconstructible for a past month, and the summary is a present-day
    figure. Each settlement carries the day it was paid, so the ratio can be
    replayed forward — which is what makes the reliability facet mean the same
    thing in the first frame as in the last.

    Nothing here redefines "late": ``Settlement.late`` and ``MIN_SETTLEMENTS``
    are ``payments.py``'s, and a second definition would be the drift §2 warns
    about — a customer called a late payer on this screen and a prompt one on
    the payments screen is a disagreement nobody can debug from outside.
    """
    grouped: dict[str, list] = {}
    for row in settlements:
        grouped.setdefault(row.party_id, []).append(row)

    out: dict[str, Reliability] = {}
    for customer_id, rows in grouped.items():
        rows.sort(key=lambda s: s.paid_on)
        datable = [s for s in rows if s.days_late is not None]
        if len(datable) < payments.MIN_SETTLEMENTS:
            # Unknown, not unreliable. Scoring an unmeasured customer as zero
            # would punish them for a gap in what Zoho holds.
            out[customer_id] = Reliability(
                None, len(datable),
                {"payment": ("Fewer than "
                             f"{payments.MIN_SETTLEMENTS} settled invoices "
                             "carry a due date, so on-time payment cannot be "
                             "measured.")})
            continue

        history: list[tuple[date, float]] = []
        late = 0
        for i, row in enumerate(datable):
            late += 1 if row.late else 0
            history.append((row.paid_on, max(0.0, 1.0 - late / (i + 1))))
        out[customer_id] = Reliability(
            score=history[-1][1],
            observations=len(datable),
            detail={"settlements": len(rows), "datable": len(datable),
                    "late_count": late},
            observed_upto=tuple(history))
    return out


def reliability_from_supply(orders: Iterable, as_of: date, *,
                            stale_after_days: int) -> dict[str, Reliability]:
    """Supplier reliability: the share of their orders not left hanging.

    Measured against staleness rather than against a promised date, because
    ``supply.py`` records that this book has no promised dates to measure
    against. An order still open past ``stale_after_days`` is the answerable
    version of "they did not deliver".

    ``observed_upto`` is built from the orders themselves, so a past frame reads
    the ratio as it stood then rather than today's ratio projected backwards.
    """
    grouped: dict[str, list] = {}
    for order in orders:
        if order.vendor_id:
            grouped.setdefault(order.vendor_id, []).append(order)

    out: dict[str, Reliability] = {}
    for vendor_id, rows in grouped.items():
        rows.sort(key=lambda o: o.ordered_on)
        history: list[tuple[date, float]] = []
        for i, row in enumerate(rows):
            seen = rows[: i + 1]
            stale = sum(1 for o in seen
                        if o.open and (row.ordered_on - o.ordered_on).days
                        >= stale_after_days)
            history.append((row.ordered_on, max(0.0, 1.0 - stale / len(seen))))
        stale_now = sum(1 for o in rows
                        if o.open and o.age_days(as_of) >= stale_after_days)
        out[vendor_id] = Reliability(
            score=max(0.0, 1.0 - stale_now / len(rows)),
            observations=len(rows),
            detail={"orders": len(rows),
                    "open_orders": sum(1 for o in rows if o.open),
                    "stale_open_orders": stale_now,
                    "stale_after_days": stale_after_days},
            observed_upto=tuple(history))
    return out


# ── small shared helpers ─────────────────────────────────────────────────────
#: A counterparty whose every line is uncategorised has no dominant line. Named
#: with the catalogue's own "not categorised" code rather than a second word for
#: the same idea, so one vocabulary reaches the screen.
_OTHER = UNCATEGORISED

def _sold(declared: Optional[Iterable[str]], rows: list[TradeLine]) -> int:
    """How many lines the business sells — the denominator of coverage.

    Taken from the caller, which knows the catalogue. Falls back to the lines
    actually seen in the data only when nothing was declared, and that fallback
    is a poor one on purpose: deriving the denominator from what has been traded
    means a book that only ever sold cutting tools reports perfect coverage,
    which is exactly the book with the worst mix.
    """
    if declared is not None:
        names = {c for c in declared if c and c != UNCATEGORISED}
        if names:
            return len(names)
    return len({r.category for r in rows
                if r.category and r.category != UNCATEGORISED})


def _sectors(rows: list[TradeLine]) -> dict[str, str]:
    """Each counterparty's dominant line of the business.

    This was the book's top few *items*, which measured out at 147 of 204
    counterparties landing in "Other" and the named groups being individual
    SKUs — "Holders — M12 #5" is not something anybody can act on. A line of the
    business is: "my coolant customers are all thin bonds" is a sentence with a
    decision attached to it, and it is what the strip's lanes group by.

    Dominant by money rather than by count, because a customer taking one
    machine and forty inserts is a cutting-tool account with a machine on it,
    not the reverse.
    """
    per_party: dict[str, dict[str, float]] = {}
    for row in rows:
        if not row.category or row.category == UNCATEGORISED:
            continue
        bucket = per_party.setdefault(row.counterparty_id, {})
        bucket[row.category] = bucket.get(row.category, 0.0) + row.amount
    return {party: max(spread.items(), key=lambda kv: kv[1])[0]
            for party, spread in per_party.items()}


def _document_days(rows: list[TradeLine]) -> list[date]:
    """Sorted distinct document dates — one per invoice or bill, not per line."""
    by_doc: dict[str, date] = {}
    for row in rows:
        by_doc[row.document_ref] = row.date
    return sorted(by_doc.values())


@dataclass(frozen=True)
class _Doc:
    """The two fields ``aggregates.order_dates`` reads, and nothing else.

    ``cadence_of`` takes rows and derives distinct documents itself. These are
    already distinct, so each is handed over as its own document — reusing that
    function rather than reimplementing "median of the gaps" is the whole point
    of it being one function (see ``aggregates.Cadence``).
    """

    date: date
    source_ref: dict
    external_ref: str


def _as_sale_rows(days: list[date]) -> list[_Doc]:
    return [_Doc(day, {}, f"{i}") for i, day in enumerate(days)]


def _month_index(day: date, window: list[periods.Period]) -> Optional[int]:
    for i, period in enumerate(window):
        if period.contains(day):
            return i
    return None


def _trailing(monthly: list[float], span: int) -> list[float]:
    """Rolling sum of the last ``span`` months, aligned to ``monthly``."""
    out: list[float] = []
    for i in range(len(monthly)):
        out.append(sum(monthly[max(0, i - span + 1): i + 1]))
    return out


def _months_between(first: date, when: date) -> int:
    """Whole months a relationship has been eligible to trade in, inclusive."""
    if when < first:
        return 0
    return max(1, (when.year - first.year) * 12 + (when.month - first.month) + 1)


def unavailable(side: str, *, has_reliability: bool) -> list[dict]:
    """What this view cannot answer, said plainly rather than left blank.

    Each entry names its side. The two halves raise the same reliability gap for
    different reasons, and two identically-worded lines under one screen read as
    a rendering bug rather than as two facts.
    """
    who = "customers" if side == CUSTOMER else "suppliers"
    out: list[dict] = []
    if not has_reliability:
        out.append({
            "what": f"Reliability ({who})",
            # Conditional on this book, not on the platform: due dates and
            # purchase orders are both modelled and both ingested. What is
            # missing is the entry.
            "kind": absence.COLLECTABLE,
            "why": ("No settled invoice carries a due date, so on-time payment "
                    "cannot be measured."
                    if side == CUSTOMER else
                    "No purchase order is on record, so whether orders are left "
                    "hanging cannot be measured.")
            + (" The remaining four facets are renormalised, and each bond "
               "names the gap rather than being scored down for it."),
        })
    if side == VENDOR:
        out.append({
            "what": "Whether we pay them on time",
            # BUILDABLE, and this reason is very likely stale. It is true of
            # ``bills``, which is what this module reads — but
            # ``BillPaymentApplication`` carries ``bill_date``, ``bill_due_date``
            # and ``paid_on`` at exactly the grain a past month needs, and it is
            # ingested. Left in place rather than narrowed here because the fix
            # changes what the reliability facet scores, which is a bonds
            # change and not a labelling one. Compare ``stock.supplier_and_brand``,
            # which was narrowed once for the same reason.
            "kind": absence.BUILDABLE,
            "why": ("Bills carry a current balance and no payment date, so it "
                    "can be stated for today but not reconstructed for a past "
                    "month. It is shown beside the score rather than inside "
                    "it, so the play means the same thing in every frame."),
        })
    return out
