"""Which quotes were won, which were lost, and whether the price is the reason.

The platform has recorded quote outcomes since the outcome table was added and
has never once read them. Beside them sits ``quote_decisions``: an append-only
snapshot of every line at the moment it was priced — the cost basis used, the
price quoted, the margin that produced, and the thresholds version that judged
it. That pairing is unusually good evidence, because it means a lost quote can
be examined **at the numbers the salesperson could actually see on the day**
rather than re-judged against today's cost.

The owner's question is the shape of this module: *losing at 8% below my quote
is a pricing problem; losing on delivery is a stock problem — I cannot
currently tell which*. So there are two halves here, and they answer that
sentence in order.

**Counted per quote, never per line.** A customer accepts or declines a quote;
they do not accept four of its lines. A forty-line tender that lost is one
loss, and counting lines would let one big RFQ outvote a quarter of trading.
The line grain still exists — the price comparison below needs it — but no win
rate is ever computed from it.

**A quote appears in every slice it belongs to.** A quote carrying three
principals' products counts once in each of those three principals' win rates,
so the slice counts deliberately sum to more than the total. The alternative is
attributing the whole quote to whichever principal happens to be on line one,
which is worse in a way nobody would ever notice.

**The evidence floor, and why it is higher here than for payments.**
``payments.MIN_SETTLEMENTS`` is three, because three settled invoices really do
describe a rhythm. Three quotes describe nothing: a win rate over three quotes
can only be 0%, 33%, 67% or 100%, and every one of those reads as a finding.
So the floor is higher, and below it the rate is ``None`` with the count
visible beside it — an absent figure with a reason, rather than a confident
percentage over four quotes.

**Where the price comparison refuses.** The comparison that matters is *this
lost quote against the price that wins*, and it is only meaningful within one
product and one quantity band: the same insert at 10 pieces and at 1,000 is two
different prices for good reasons, and pooling them would manufacture a gap out
of the mix. So a comparison exists only where one product-and-band has enough
won observations *and* enough lost ones. Most products will not qualify, and a
book with a thin quote history produces no comparisons at all. That is the
correct answer to "are we losing on price" when the evidence cannot say.

**What is RESTRICTED, and what is not.** A win rate carries no cost: a
salesperson may see their own, and the whole first half of this module is
computed without a single cost figure. The second half — margin on what we win
against margin on what we lose, and how far above the winning price a lost
quote sat — is commercial position, and the router keeps it behind a
manager-or-owner endpoint the way ``/payables`` is kept behind one. Nothing
here decides that; ``build`` and ``pricing`` are separate functions precisely so
the router can serve one and not the other, rather than serving one dictionary
with fields stripped out of it.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable, Optional

from ...domain.enums import LOSS_REASON_NOT_RECORDED, QuoteLossReason
from ..benchmark import median_decimal
from . import periods

_ZERO = Decimal("0")

#: Fewer decided quotes than this and a win rate is arithmetic rather than
#: evidence — see the module docstring. Applied to every slice independently,
#: so the book can have a rate while a single customer does not.
MIN_DECIDED_QUOTES = 8

#: Won *and* lost observations needed on one product-and-quantity-band before
#: the two medians are compared. Three each, matching
#: ``payments.MIN_SETTLEMENTS``: the question here is "what does this item
#: normally go out at", which two prices genuinely cannot answer and three
#: begin to.
MIN_PRICE_OBSERVATIONS = 3


#: What each reason means, and — the part that answers the owner — whose
#: problem it is. A reason with no owner attached is a label; these are the
#: four different places the fix lives.
REASONS: dict[str, dict[str, str]] = {
    QuoteLossReason.PRICE.value: {
        "label": "Price",
        "owner": "PRICING",
        "meaning": "They said the number was too high. The one reason that can "
                   "be checked against evidence rather than believed: the "
                   "pricing half of this screen compares what was quoted "
                   "against what wins.",
    },
    QuoteLossReason.DELIVERY.value: {
        "label": "Delivery or availability",
        "owner": "SUPPLY",
        "meaning": "We could not supply it when they needed it. A stock and "
                   "lead-time problem, and repricing will not touch it.",
    },
    QuoteLossReason.COMPETITOR.value: {
        "label": "Went to a competitor",
        "owner": "POSITION",
        "meaning": "Somebody else took it, for a reason other than the number "
                   "alone — an approval, a relationship, a make we do not "
                   "carry. Worth separating from price precisely because it is "
                   "so often recorded as price.",
    },
    QuoteLossReason.CUSTOMER_CANCELLED.value: {
        "label": "Customer cancelled",
        "owner": "NOT_OURS",
        "meaning": "The requirement went away — the job was cancelled, the "
                   "budget went. Counted, and excluded from nothing, but it is "
                   "not a signal about how this business quotes.",
    },
    QuoteLossReason.NO_DECISION.value: {
        "label": "No decision",
        "owner": "NOT_OURS",
        "meaning": "They never came back. Honest, common, and the reason this "
                   "list has five entries rather than four — without it, every "
                   "silent enquiry would be filed under something it was not.",
    },
    LOSS_REASON_NOT_RECORDED: {
        "label": "Not recorded",
        "owner": "UNKNOWN",
        "meaning": "Lost before a reason was asked for. Shown rather than "
                   "hidden, because these quotes are in the win rate and their "
                   "silence should be visible beside it. The API refuses new "
                   "losses without a reason, so this bucket cannot grow.",
    },
}

#: What each ``owner`` above means as a sentence. Kept beside ``REASONS``
#: rather than repeated inside every entry of it: five reasons share four
#: owners today, and a sixth reason should name an existing owner rather than
#: restate its prose.
OWNERS: dict[str, str] = {
    "PRICING": "A pricing problem",
    "SUPPLY": "A stock and lead-time problem",
    "POSITION": "A position problem — not the number alone",
    "NOT_OURS": "Not a signal about how we quote",
    "UNKNOWN": "Lost before we asked why",
}


@dataclass(frozen=True)
class DecidedQuote:
    """One quote the customer has answered, and everything counted about it.

    The grain of every win rate in this module. ``value`` is what was quoted —
    the sum of the priced lines' revenue at the price that went out — which is
    a customer-facing number and carries no cost.

    ``gross_profit`` is ``None`` unless *every* line on the quote had a cost
    basis. A partially-costed quote would understate its own profit, and a
    won-against-lost margin comparison built from a mixture of complete and
    incomplete quotes is a comparison of coverage rather than of margin.
    """

    quote_id: str
    customer_id: str
    customer_label: str
    won: bool
    #: A ``QuoteLossReason`` value, or ``LOSS_REASON_NOT_RECORDED``. Empty on a
    #: won quote — there is nothing to explain.
    loss_reason: str
    decided_on: date
    lines: int
    value: Decimal
    gross_profit: Optional[Decimal] = None
    #: Facet keys the quote belongs to. A quote spanning two principals carries
    #: both and is counted in both — see the module docstring.
    principals: tuple[str, ...] = ()
    product_lines: tuple[str, ...] = ()

    @property
    def costed(self) -> bool:
        return self.gross_profit is not None


@dataclass(frozen=True)
class PricedLine:
    """One priced line on a decided quote, as the price comparison needs it.

    Taken from the *latest* snapshot of that line — a re-priced line has
    several, and the negotiation's last word is the price the customer answered.
    Earlier rows stay in the audit trail; they are simply not what was accepted
    or refused.
    """

    quote_id: str
    product_id: str
    product_label: str
    quantity_band: str
    unit_price: Decimal
    won: bool
    customer_id: str
    #: What this customer has historically paid for this item, where the
    #: metrics layer knows. ``None`` is common and is left as ``None``.
    customer_paid: Optional[Decimal] = None


@dataclass(frozen=True)
class Slice:
    """One cut of the book — a customer, a principal, a product line, a month.

    ``win_rate`` is ``None`` below the floor and the counts are shown anyway,
    so a thin slice reads as "3 decided, no rate yet" instead of "0%".
    """

    key: str
    label: str
    decided: int
    won: int
    won_value: Decimal
    lost_value: Decimal

    @property
    def lost(self) -> int:
        return self.decided - self.won

    @property
    def estimable(self) -> bool:
        return self.decided >= MIN_DECIDED_QUOTES

    @property
    def win_rate(self) -> Optional[float]:
        if not self.estimable:
            return None
        return round(self.won / self.decided, 4)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label,
            "decided": self.decided, "won": self.won, "lost": self.lost,
            "win_rate": self.win_rate, "estimable": self.estimable,
            "won_value": float(self.won_value),
            "lost_value": float(self.lost_value),
            "quoted_value": float(self.won_value + self.lost_value),
        }


def _slice(key: str, label: str, quotes: list[DecidedQuote]) -> Slice:
    return Slice(
        key=key, label=label,
        decided=len(quotes),
        won=sum(1 for q in quotes if q.won),
        won_value=sum((q.value for q in quotes if q.won), _ZERO),
        lost_value=sum((q.value for q in quotes if not q.won), _ZERO),
    )


def by_facet(quotes: Iterable[DecidedQuote],
             facets: Callable[[DecidedQuote], Iterable[str]],
             labels: dict[str, str]) -> list[Slice]:
    """Slice the decided quotes by whatever ``facets`` names them.

    One function rather than four near-identical ones, because customer,
    principal and product line differ only in which keys a quote carries — and
    the moment the floor or the value arithmetic is fixed in one of four copies,
    the four stop agreeing. Sorted by decided count then by lost value, so the
    thing worth looking at is first whether or not it clears the floor.
    """
    grouped: dict[str, list[DecidedQuote]] = {}
    for quote in quotes:
        for key in facets(quote):
            if key:
                grouped.setdefault(key, []).append(quote)
    rows = [_slice(key, labels.get(key, key), group)
            for key, group in grouped.items()]
    rows.sort(key=lambda s: (s.decided, s.lost_value), reverse=True)
    return rows


def over_time(quotes: Iterable[DecidedQuote],
              months: list[periods.Period]) -> list[Slice]:
    """The same slice, per calendar month, by the date the quote was decided.

    Decided rather than sent, because the month in question is the one the
    business found out. A quote sent in March and answered in May says nothing
    about March, and indexing it there would move a loss into a month whose
    numbers have already been read.
    """
    rows = list(quotes)
    return [_slice(period.label, period.label,
                   [q for q in rows if period.contains(q.decided_on)])
            for period in months]


def reason_mix(quotes: Iterable[DecidedQuote]) -> list[dict]:
    """How the losses divide, by reason and by whose problem it is.

    Value as well as count, because five small losses on price and one large
    one on delivery are not the same book, and a count alone says they are.
    """
    lost = [q for q in quotes if not q.won]
    grouped: dict[str, list[DecidedQuote]] = {}
    for quote in lost:
        grouped.setdefault(quote.loss_reason or LOSS_REASON_NOT_RECORDED,
                           []).append(quote)
    rows = [
        {
            "reason": code,
            "label": REASONS.get(code, {}).get("label", code),
            "owner": REASONS.get(code, {}).get("owner", "UNKNOWN"),
            "count": len(group),
            "share": round(len(group) / len(lost), 4) if lost else None,
            "value": float(sum((q.value for q in group), _ZERO)),
        }
        for code, group in grouped.items()
    ]
    rows.sort(key=lambda r: (r["count"], r["value"]), reverse=True)
    return rows


def build(quotes: Iterable[DecidedQuote], *, as_of: date,
          customer_names: dict[str, str], principal_names: dict[str, str],
          product_line_names: dict[str, str], months: int = 12,
          open_quotes: int = 0) -> dict:
    """The win-rate view: counts, rates, slices and the loss mix.

    Cost-free by construction — nothing in this function reads
    ``gross_profit`` — which is what makes it servable to a salesperson without
    a field ever being stripped out of the result.

    ``open_quotes`` is the number still in flight. Reported beside the decided
    count rather than folded into it: a quote nobody has answered is not a loss,
    and counting it as one is how a win rate quietly becomes a measure of how
    fast customers reply.
    """
    rows = list(quotes)
    decided = len(rows)
    won = sum(1 for q in rows if q.won)
    overall = _slice("ALL", "The whole book", rows)

    return {
        "as_of": as_of.isoformat(),
        "decided": decided,
        "won": won,
        "lost": decided - won,
        "open": open_quotes,
        "win_rate": overall.win_rate,
        "estimable": overall.estimable,
        "won_value": float(overall.won_value),
        "lost_value": float(overall.lost_value),
        "customers": [s.to_dict() for s in
                      by_facet(rows, lambda q: (q.customer_id,), customer_names)],
        "principals": [s.to_dict() for s in
                       by_facet(rows, lambda q: q.principals, principal_names)],
        "product_lines": [s.to_dict() for s in
                          by_facet(rows, lambda q: q.product_lines,
                                   product_line_names)],
        "months": [s.to_dict() for s in
                   over_time(rows, periods.months_back(as_of, months))],
        "reasons": reason_mix(rows),
        "reason_catalogue": REASONS,
        "owners": OWNERS,
        "min_decided_quotes": MIN_DECIDED_QUOTES,
        "quotes": [
            {
                "quote_id": q.quote_id,
                "customer_id": q.customer_id,
                "customer_label": q.customer_label,
                "status": "WON" if q.won else "LOST",
                "loss_reason": q.loss_reason or None,
                "loss_reason_label": (REASONS.get(q.loss_reason, {}).get("label")
                                      if not q.won else None),
                "decided_on": q.decided_on.isoformat(),
                "lines": q.lines,
                "value": float(q.value),
            }
            for q in sorted(rows, key=lambda q: q.decided_on, reverse=True)
        ],
        "note": ("Counted per quote, not per line — a customer accepts or "
                 "declines a whole quote. A quote spanning several principals "
                 "counts once in each of their rates, so the slices sum to "
                 "more than the total."),
    }


# ── the price question ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class PriceComparison:
    """One product at one quantity band: what wins, and what lost.

    ``gap_pct`` is the lost median against the won median — positive means the
    losses were quoted *above* where this item wins, which is the finding the
    owner is asking for. It is a ratio like every other proportion in this
    codebase, and becomes a percentage only at the presentation edge.
    """

    product_id: str
    product_label: str
    quantity_band: str
    won_observations: int
    lost_observations: int
    won_median: Decimal
    lost_median: Decimal

    @property
    def gap_pct(self) -> Optional[float]:
        if self.won_median <= _ZERO:
            return None
        return float((self.lost_median - self.won_median) / self.won_median)

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_label": self.product_label,
            "quantity_band": self.quantity_band,
            "won_observations": self.won_observations,
            "lost_observations": self.lost_observations,
            "won_median_price": float(self.won_median),
            "lost_median_price": float(self.lost_median),
            "gap_pct": self.gap_pct,
        }


def comparisons(lines: Iterable[PricedLine]) -> list[PriceComparison]:
    """Every product-and-band where both sides clear the floor.

    Medians rather than means throughout: one line quoted at ten times the
    normal price because somebody typed an extra zero, or one deliberate
    loss-leader, would otherwise redefine what the item "wins at" — and the
    whole comparison is then driven by the row that least deserves to drive it.
    """
    grouped: dict[tuple[str, str], list[PricedLine]] = {}
    labels: dict[str, str] = {}
    for line in lines:
        if not line.product_id or line.unit_price is None:
            continue
        grouped.setdefault((line.product_id, line.quantity_band), []).append(line)
        labels[line.product_id] = line.product_label

    out: list[PriceComparison] = []
    for (product_id, band), group in grouped.items():
        won = [ln.unit_price for ln in group if ln.won]
        lost = [ln.unit_price for ln in group if not ln.won]
        if len(won) < MIN_PRICE_OBSERVATIONS or len(lost) < MIN_PRICE_OBSERVATIONS:
            continue
        out.append(PriceComparison(
            product_id=product_id, product_label=labels.get(product_id, product_id),
            quantity_band=band,
            won_observations=len(won), lost_observations=len(lost),
            won_median=median_decimal(won), lost_median=median_decimal(lost)))
    out.sort(key=lambda c: (c.gap_pct or 0.0), reverse=True)
    return out


def versus_own_history(lines: Iterable[PricedLine]) -> Optional[dict]:
    """How far a lost quote sat from what that customer has actually paid.

    A different question from the one ``comparisons`` answers, and worth both:
    the winning price says where the market is, this says whether we moved
    against *this* relationship. ``None`` when too few lost lines have a price
    history behind them, rather than a figure computed from one.
    """
    observed = [
        float((ln.unit_price - ln.customer_paid) / ln.customer_paid)
        for ln in lines
        if not ln.won and ln.customer_paid is not None and ln.customer_paid > _ZERO
    ]
    if len(observed) < MIN_PRICE_OBSERVATIONS:
        return None
    return {
        "observations": len(observed),
        "median_gap_pct": round(statistics.median(observed), 4),
    }


def margin_of(quotes: Iterable[DecidedQuote]) -> Optional[float]:
    """Σ gross profit ÷ Σ revenue over the fully-costed quotes.

    Never the mean of per-quote margins: a one-line quote for a screwdriver
    would otherwise weigh the same as a forty-line tender, which is how an
    aggregate margin ends up describing a book nobody trades.
    """
    costed = [q for q in quotes if q.costed]
    revenue = sum((q.value for q in costed), _ZERO)
    if revenue <= _ZERO:
        return None
    profit = sum((q.gross_profit or _ZERO for q in costed), _ZERO)
    return round(float(profit / revenue), 4)


def pricing(quotes: Iterable[DecidedQuote], lines: Iterable[PricedLine], *,
            as_of: date) -> dict:
    """The RESTRICTED half: margin on what we win, and where the losses sat.

    Everything here compares one customer's price against another's or against
    a margin, which is commercial position rather than a call list — so the
    router serves it to a manager or owner only, exactly as it does
    ``/payables``. Kept as its own function rather than extra keys on ``build``
    so that "who may see this" is a routing decision about a whole response
    instead of a redaction loop somebody has to remember to run.
    """
    rows = list(quotes)
    line_rows = list(lines)
    won_quotes = [q for q in rows if q.won]
    lost_quotes = [q for q in rows if not q.won]

    won_margin = margin_of(won_quotes)
    lost_margin = margin_of(lost_quotes)
    compared = comparisons(line_rows)
    gaps = [c.gap_pct for c in compared if c.gap_pct is not None]

    return {
        "as_of": as_of.isoformat(),
        "won_margin": won_margin,
        "lost_margin": lost_margin,
        # Percentage POINTS, per the house convention: the difference between
        # two ratios is never expressed as a percentage change of a percentage.
        "margin_gap_pp": (round(lost_margin - won_margin, 4)
                          if won_margin is not None and lost_margin is not None
                          else None),
        "costed_quotes": sum(1 for q in rows if q.costed),
        "decided_quotes": len(rows),
        "comparisons": [c.to_dict() for c in compared],
        # The headline the owner asked for, when it can be said at all: the
        # typical distance between a losing price and a winning one, across
        # every product that qualified. Median of the per-product gaps rather
        # than a gap of pooled prices — pooling would compare a book of cheap
        # items against a book of expensive ones.
        "median_gap_pct": (round(statistics.median(gaps), 4) if gaps else None),
        "compared_products": len(compared),
        "versus_own_history": versus_own_history(line_rows),
        "min_price_observations": MIN_PRICE_OBSERVATIONS,
        "priced_lines": len(line_rows),
        "note": ("Compared within one product and one quantity band only. The "
                 "same item at 10 pieces and at 1,000 is two prices for good "
                 "reasons, and pooling them would manufacture a gap out of the "
                 "mix. A product with fewer than "
                 f"{MIN_PRICE_OBSERVATIONS} won or {MIN_PRICE_OBSERVATIONS} "
                 "lost observations is absent rather than estimated."),
    }
