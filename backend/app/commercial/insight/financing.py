"""What a customer earns after the cost of waiting to be paid.

Zoho holds two facts about every account and never puts them in one sentence.
Ageing says when the money arrived; the item metrics say what the margin was.
So a customer at 24% settling in 300 days outranks one at 19% settling in 30 on
every screen this platform has, and at a 12% cost of capital the second is worth
four points more — the first is being lent most of a year's revenue, free, and
nothing anywhere charges them for it. This module is that join and nothing else.

How wide the gap has to be before the order actually inverts is a fact about the
owner's rate, not about this module, and the module is deliberately built so it
cannot flatter the point: at 12% the more quotable pair — 24% at 95 days against
19% at 30 — does *not* invert, because 65 extra days is two points of margin and
the gap is five. ``test_a_gap_the_rate_does_not_close_leaves_the_order_alone``
pins that, so a later change that made the charge look more impressive would
have to break a test that says why it should not.

**Neither half is recomputed here.** ``portfolio.aggregate_margin`` rolls the
persisted ``CustomerItemMetric`` rows into revenue, *costed* revenue and gross
profit — one implementation, because "what is this book's margin" must have one
answer. ``payments.lag`` measures the days-to-pay distribution over settled
documents and refuses below its own evidence floor. What is new here is one
multiplication and the discipline around which rows it is allowed to run on.

**Days from the document date, not from the due date.** ``payments`` draws that
distinction and it decides which of its four numbers this module reads: the
three percentiles on ``Lag`` are days *late*, which answers "were the terms
honoured", and ``expected_days_to_pay`` is measured from the invoice date, which
answers "how long is the cash tied up". Only the second is a financing question.
A customer on 90-day terms who pays on day 90 is never late and is funded for
ninety days all the same.

**The median is the case, the ninetieth percentile is beside it — never
instead.** The median is what half this account's rupees waited behind, and it
is a median rather than a mean for the reason ``payments._spread`` gives: one
invoice settled nine months late is a story about that invoice, and a mean would
let it redefine the customer. But a treasurer plans against the bad month, not
the middle one, so every row also carries the charge at the ninetieth percentile
of the same distribution. Publishing only the p90 would overstate the book
systematically; publishing only the median hides the accounts whose tail is the
whole problem. Both, labelled, on the same row.

The p90 comes through ``payments.percentile`` — the same nearest-rank function
``lag`` itself calls, which that module made public for exactly this reason. The
alternative considered was a fourth field on ``Lag``: rejected because it would
widen a dataclass four other screens read for a figure only this one wants, and
this way there is still one definition of a percentile in the package. The
evidence floor, the grouping and the central figure are all still ``lag``'s.

**The charge is levied on costed revenue, because that is what the profit was
earned on.** This is the rule CLAUDE.md states for a margin, applied to a
subtraction: numerator and denominator must span the same population. Gross
profit here comes from the metric rows that carry a cost, so charging financing
on *all* of an account's revenue would subtract the funding cost of rupees whose
profit was never counted, and the result would read as a pricing failure when it
is a cost-coverage gap. Every row therefore states its coverage, and an account
whose costed share falls below ``MIN_COST_COVERAGE`` gets no adjusted figure at
all rather than one that speaks for a minority of the relationship while
carrying the customer's whole name.

The honest cost of that choice, stated rather than buried: the rupee figure on a
partially-costed row is the funding cost of the costed part only, so it
*understates* what that account actually ties up. The coverage share is on the
row so the direction of the error is legible. The same understatement applies to
every row for a second reason — revenue here is line revenue, and the receivable
the bank actually funds carries GST on top of it.

**A run-rate charge, not a replay.** ``revenue × days ÷ 365 × rate`` says: this
much revenue turned over once at this measured lag. It does not reconstruct each
invoice's outstanding balance day by day — ``insight/cycle`` is where a replayed
receivable position lives, per legal entity, and it is a different and heavier
thing. What is here is deliberately the arithmetic a person can check on paper.

**Same window on both halves.** The metric rows are a twelve-month rollup, so
the settlements are filtered to the same ``WINDOW_DAYS`` ending at ``as_of``
before any percentile is taken. A median drawn from four years of history
applied to one year of revenue would be two windows in one product, which is the
shape of mistake ``gmroi`` exists to demonstrate. The residual is that the
metrics were computed at some earlier moment; the response carries
``metrics_computed_at`` so a reader can see how far apart the two halves are
rather than assuming they are aligned.

**Four refusals, and none of them is a zero.**

no rate           The whole reading. ``cost_of_capital_annual_pct`` is
                  owner-set with no default, and until it is set there is no
                  rate to charge — not a conservative one, not the stock
                  carrying rate. Nothing is computed and the field is named.
below the floor   An account with fewer settled invoices than ``payments``
                  requires gets no adjusted figure, and specifically does *not*
                  borrow the book's median. Applying the book's days to an
                  account whose own days are unknown produces a confident
                  number about the one customer nobody has evidence for.
no gross profit   An account with no costed revenue has an unknown margin.
                  Subtracting a real financing cost from an unknown gross
                  profit yields a confident negative, which is the benign
                  default in reverse and just as wrong.
thin coverage     Above zero and below ``MIN_COST_COVERAGE`` — see above.

Aggregation is Σ adjusted profit ÷ Σ costed revenue over the accounts that
produced a figure, never the mean of their margins, and the totals name how many
accounts and how much revenue they leave out.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
Everything here is gross profit and a funding rate, so the whole module is
RESTRICTED — the endpoint refuses a salesperson outright rather than serving a
version with the economics removed, because there is nothing left of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from ...domain import models
from ..config import CommercialThresholds
from ..portfolio import MarginAggregate, aggregate_margin
from . import absence, payments

_ZERO = Decimal("0")
#: Days in the year the rate is quoted per. A plain 365 rather than 360: the
#: days on the other side of the multiplication are calendar days counted from
#: one date to another, and mixing a banker's year with calendar days would
#: overstate every charge by about 1.4% for no reason anybody could explain on
#: the screen.
YEAR_DAYS = Decimal(365)

#: The window both halves are measured over. Matches the twelve months the
#: ``revenue_12m`` rollup covers; see the module docstring on why one window is
#: not a detail.
WINDOW_DAYS = 365

#: How much of an account's revenue must have a cost behind it before an
#: adjusted figure is allowed to speak for the account.
#:
#: Half, and the threshold is about what the row *claims* rather than about
#: arithmetic. The subtraction is sound at any coverage — profit and charge are
#: both levied on the costed rupees — but the row is titled with the customer's
#: name and gets ranked against rows that cover everything. Below half, the
#: figure describes the minority of a relationship while presenting as the
#: relationship.
#:
#: A module constant rather than a threshold in Settings, for the reason
#: ``gmroi.MIN_OBSERVED_DAYS`` is one: it is not commercial policy an owner
#: should be able to lower, and making it editable would make "produce a number
#: for the demo" a supported operation.
MIN_COST_COVERAGE = 0.5

#: What a row's figures rest on.
MEASURED = "MEASURED"
#: Measured, on a costed share below 1.0 — the charge and the profit both speak
#: for that share only, so the rupee figure understates. The share is on the row.
PARTIAL_COST = "PARTIAL_COST"

#: Why a row carries no adjusted figure. Four, because they want four different
#: responses: one is time, two are clerical, one is arithmetic.
NO_PAYMENT_HISTORY = "NO_PAYMENT_HISTORY"
NO_COSTED_REVENUE = "NO_COSTED_REVENUE"
THIN_COST_COVERAGE = "THIN_COST_COVERAGE"
NO_REVENUE = "NO_REVENUE"

REASON_MEANING: dict[str, str] = {
    MEASURED: ("Every rupee of this account's revenue in the window has a cost "
               "record behind it, and its days-to-pay is measured over the same "
               "window."),
    PARTIAL_COST: ("Some of this account's revenue has no bill behind it. Both "
                   "the profit and the financing charge are computed over the "
                   "covered part, so the rupee figures understate what the "
                   "account earns and what it ties up. The covered share is on "
                   "the row."),
    NO_PAYMENT_HISTORY: (
        f"Fewer than {payments.MIN_SETTLEMENTS} settled invoices inside the "
        "window, so how long this account takes to pay is not established. The "
        "book's median is deliberately not substituted — that would state a "
        "financing cost for the one account there is no evidence about."),
    NO_COSTED_REVENUE: (
        "Nothing this account bought in the window has a cost record behind it, "
        "so its gross profit is unknown. Unknown, not zero: subtracting a real "
        "financing cost from it would produce a confident loss."),
    THIN_COST_COVERAGE: (
        f"Less than {MIN_COST_COVERAGE:.0%} of this account's revenue has a cost "
        "behind it. The figure would describe a minority of the relationship "
        "while carrying the whole account's name, so it is withheld and the "
        "bills are worth chasing instead."),
    NO_REVENUE: "No revenue recorded for this account inside the window.",
}


def financing_cost(costed_revenue: Decimal, days: int,
                   rate: Decimal) -> Decimal:
    """What funding ``costed_revenue`` for ``days`` costs, in rupees.

    The one piece of arithmetic in the module, extracted so it can be read and
    checked on its own. ``Decimal`` end to end because it is money — a float
    here would put a rounding error into a figure somebody plans a payment run
    around.
    """
    return costed_revenue * (Decimal(days) / YEAR_DAYS) * rate


@dataclass(frozen=True)
class Account:
    """One customer's profitability with the cost of their credit taken out.

    Holds the margin half as the ``MarginAggregate`` ``portfolio`` produced —
    not unpacked into three fields — because the four figures a reader wants
    before financing (revenue, costed revenue, gross profit, margin) and the
    coverage share are all already on it, correctly. Copying them out meant
    re-deriving ``Σ gross profit ÷ Σ costed revenue`` and ``costed ÷ revenue``
    here, which a duplicate scan found immediately and which is CLAUDE.md's
    responsibility duplication exactly: one rule, two owners, drifting on the
    first change to what "costed" means.

    What is genuinely this module's is the days, the rate and everything
    downstream of the subtraction. Those are the fields, and every derived
    figure is a property so what is on screen and what a test asserts come from
    the same expression.
    """

    customer_id: str
    label: str
    #: Revenue, costed revenue, gross profit and the margin over the costed
    #: part — ``portfolio.aggregate_margin``'s answer, carried rather than
    #: recomputed.
    margin: MarginAggregate
    #: Median days from invoice date to settlement; ``None`` below the floor.
    days: Optional[int]
    #: The ninetieth percentile of the same distribution — the bad case, beside
    #: the median rather than instead of it.
    slow_days: Optional[int]
    settlements: int
    rate: Decimal
    reason: str

    @property
    def measured(self) -> bool:
        return self.reason in (MEASURED, PARTIAL_COST)

    @property
    def revenue(self) -> Decimal:
        """All revenue in the window — reported for context, never divided by."""
        return self.margin.revenue

    @property
    def costed_revenue(self) -> Decimal:
        """The revenue the profit and the charge both speak for."""
        return self.margin.costed_revenue

    @property
    def gross_profit(self) -> Optional[Decimal]:
        """``None`` where no row carried a cost — unknown, not zero."""
        return self.margin.gross_profit

    @property
    def cost_coverage(self) -> Optional[float]:
        """Share of this account's revenue the figures actually speak for."""
        return self.margin.revenue_coverage

    @property
    def gross_margin(self) -> Optional[float]:
        """Σ gross profit ÷ Σ costed revenue — the before figure."""
        return self.margin.margin

    @property
    def charge(self) -> Optional[Decimal]:
        """The financing cost at the median lag, in rupees."""
        if not self.measured or self.days is None:
            return None
        return financing_cost(self.costed_revenue, self.days, self.rate)

    @property
    def slow_charge(self) -> Optional[Decimal]:
        """The same, at the ninetieth percentile — the bad case."""
        if not self.measured or self.slow_days is None:
            return None
        return financing_cost(self.costed_revenue, self.slow_days, self.rate)

    @property
    def adjusted_profit(self) -> Optional[Decimal]:
        if self.charge is None or self.gross_profit is None:
            return None
        return self.gross_profit - self.charge

    @property
    def slow_adjusted_profit(self) -> Optional[Decimal]:
        if self.slow_charge is None or self.gross_profit is None:
            return None
        return self.gross_profit - self.slow_charge

    @property
    def adjusted_margin(self) -> Optional[float]:
        profit = self.adjusted_profit
        if profit is None or self.costed_revenue <= _ZERO:
            return None
        return float(profit / self.costed_revenue)

    @property
    def slow_adjusted_margin(self) -> Optional[float]:
        profit = self.slow_adjusted_profit
        if profit is None or self.costed_revenue <= _ZERO:
            return None
        return float(profit / self.costed_revenue)

    @property
    def financing_drag_pp(self) -> Optional[float]:
        """How many percentage points of margin the wait costs.

        Percentage *points*, per CLAUDE.md: this is a movement between two
        margins, not a margin.
        """
        before, after = self.gross_margin, self.adjusted_margin
        if before is None or after is None:
            return None
        return round((before - after) * 100, 2)

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id,
            "label": self.label,
            "revenue": _money(self.revenue),
            "costed_revenue": _money(self.costed_revenue),
            "cost_coverage": _ratio(self.cost_coverage),
            "gross_profit": _money(self.gross_profit),
            "gross_margin": _ratio(self.gross_margin),
            "days_to_pay": self.days,
            "slow_days_to_pay": self.slow_days,
            "settlements": self.settlements,
            "financing_cost": _money(self.charge),
            "adjusted_profit": _money(self.adjusted_profit),
            "adjusted_margin": _ratio(self.adjusted_margin),
            "financing_drag_pp": self.financing_drag_pp,
            "slow_financing_cost": _money(self.slow_charge),
            "slow_adjusted_profit": _money(self.slow_adjusted_profit),
            "slow_adjusted_margin": _ratio(self.slow_adjusted_margin),
            "reason": self.reason,
            "measured": self.measured,
        }


def _money(value: Optional[Decimal]) -> Optional[float]:
    return float(round(value, 2)) if value is not None else None


def _ratio(value: Optional[float]) -> Optional[float]:
    return round(value, 4) if value is not None else None


def _rank(accounts: list[Account], key) -> dict[str, int]:
    """1-based positions, best margin first, over the measured accounts only.

    Two of these are published side by side and the reordering between them is
    the finding this module exists for: an account that sits fourth on margin
    and eleventh once its credit is charged for is the sentence a reader takes
    away. Ranks rather than a single "moved by" figure, because the direction of
    a signed difference is a thing every reader has to look up.
    """
    ordered = sorted([a for a in accounts if key(a) is not None],
                     key=lambda a: (-key(a), a.customer_id))
    return {a.customer_id: i for i, a in enumerate(ordered, start=1)}


def _accounts(metrics: Iterable[models.CustomerItemMetric],
              lags: dict[str, payments.Lag],
              slow: dict[str, int],
              names: dict[str, str],
              rate: Decimal) -> list[Account]:
    """One row per customer with metric rows, refusals included."""
    by_customer: dict[str, list[models.CustomerItemMetric]] = {}
    for row in metrics:
        by_customer.setdefault(row.customer_id, []).append(row)

    out: list[Account] = []
    for customer_id, rows in by_customer.items():
        # The one aggregation, reused. Recomputing revenue and gross profit here
        # would be a second answer to a question ``portfolio`` already answers,
        # and the two would drift on the first cost-coverage change.
        agg = aggregate_margin(rows)
        measured = lags.get(customer_id)

        # Ordered so the reason a reader is given is the first thing that is
        # actually wrong, not whichever check happened to run last.
        if agg.revenue <= _ZERO:
            reason = NO_REVENUE
        elif agg.gross_profit is None or agg.costed_revenue <= _ZERO:
            reason = NO_COSTED_REVENUE
        elif (agg.revenue_coverage or 0.0) < MIN_COST_COVERAGE:
            reason = THIN_COST_COVERAGE
        elif measured is None:
            reason = NO_PAYMENT_HISTORY
        elif agg.costed_revenue < agg.revenue:
            reason = PARTIAL_COST
        else:
            reason = MEASURED

        usable = reason in (MEASURED, PARTIAL_COST)
        out.append(Account(
            customer_id=customer_id,
            label=names.get(customer_id, customer_id),
            margin=agg,
            # Carried only where the row is usable. A days figure printed beside
            # a withheld charge invites the next reader to do the multiplication
            # the refusal above declined to do.
            days=(measured.expected_days_to_pay
                  if measured is not None and usable else None),
            slow_days=(slow.get(customer_id)
                       if measured is not None and usable else None),
            settlements=(measured.settlements if measured is not None else 0),
            rate=rate,
            reason=reason,
        ))
    return out


def _totals(accounts: list[Account]) -> dict:
    """The book, aggregated over rows rather than over per-account ratios.

    Σ adjusted profit ÷ Σ costed revenue. The mean of per-customer margins would
    give a ₹40,000 account the same say as a ₹40 lakh one — CLAUDE.md's rule,
    and ``payments`` states the same one for days.

    The accumulation is a loop with an explicit refusal rather than
    ``sum(x or 0)``, and that is deliberate. Every measured account has a gross
    profit and both charges by construction — ``_accounts`` files
    ``NO_COSTED_REVENUE`` before a row can be measured without a profit and
    ``NO_PAYMENT_HISTORY`` before it can be measured without days — but
    ``sum(x or 0)`` reads identically whether the ``None`` is impossible or
    merely unhandled, and only one of those is safe. It is the tell CLAUDE.md
    names, and writing it here would leave the next reader unable to tell this
    from the three places it was a real bug.
    """
    measured = [a for a in accounts if a.measured]
    costed = sum((a.costed_revenue for a in measured), _ZERO)
    revenue = sum((a.revenue for a in measured), _ZERO)

    profit = charge = slow_charge = _ZERO
    for account in measured:
        if (account.gross_profit is None or account.charge is None
                or account.slow_charge is None):
            raise AssertionError(
                f"{account.customer_id} is classified {account.reason} but "
                f"carries an incomplete figure — the refusal rules in "
                f"`_accounts` and the arithmetic here have drifted apart, and "
                f"a total computed past this point would be a real number "
                f"missing a real account.")
        profit += account.gross_profit
        charge += account.charge
        slow_charge += account.slow_charge

    gross_margin = float(profit / costed) if costed > _ZERO else None
    adjusted = float((profit - charge) / costed) if costed > _ZERO else None
    slow_adjusted = (float((profit - slow_charge) / costed)
                     if costed > _ZERO else None)

    # What the totals leave out, in the same units as what they contain. A
    # coverage figure a reader has to reconstruct from two lists is a coverage
    # figure nobody reads.
    withheld = [a for a in accounts if not a.measured]
    return {
        "accounts": len(measured),
        "revenue": _money(revenue),
        "costed_revenue": _money(costed),
        "gross_profit": _money(profit),
        "financing_cost": _money(charge),
        "adjusted_profit": _money(profit - charge),
        "gross_margin": _ratio(gross_margin),
        "adjusted_margin": _ratio(adjusted),
        "financing_drag_pp": (round((gross_margin - adjusted) * 100, 2)
                              if gross_margin is not None
                              and adjusted is not None else None),
        "slow_financing_cost": _money(slow_charge),
        "slow_adjusted_profit": _money(profit - slow_charge),
        "slow_adjusted_margin": _ratio(slow_adjusted),
        "cost_coverage": (_ratio(float(costed / revenue))
                          if revenue > _ZERO else None),
        "excluded_accounts": len(withheld),
        "excluded_revenue": _money(sum((a.revenue for a in withheld), _ZERO)),
    }


def build(*, metrics: Iterable[models.CustomerItemMetric],
          settlements: Iterable[payments.Settlement],
          names: dict[str, str],
          as_of: date,
          thresholds: CommercialThresholds,
          window_days: int = WINDOW_DAYS) -> dict:
    """Every customer's margin after the cost of waiting to be paid.

    Takes rows rather than a session, the shape ``gmroi`` and ``stock`` already
    use, so the arithmetic is a function of its inputs and the router keeps the
    database.

    ``settlements`` may be the organization's whole history; it is filtered to
    the window here rather than by the caller, because a caller that filtered it
    differently from the metrics window would reintroduce exactly the mismatch
    the module docstring exists to prevent.
    """
    rows = list(metrics)
    configured = thresholds.cost_of_capital_annual_pct

    if configured is None:
        # Nothing is computed, including the halves that would survive. A screen
        # showing margins and blank financing columns invites somebody to fill
        # the blanks with the carrying rate, which is the one substitution this
        # module refuses by name.
        return {
            "measurable": False,
            "rate_set": False,
            "rate": None,
            "accounts": [],
            "totals": {},
            "counts": {},
            "window": _window(as_of, window_days, settled=0),
            "metrics_computed_at": _computed_at(rows),
            "reason_meanings": REASON_MEANING,
            "min_settlements": payments.MIN_SETTLEMENTS,
            "min_cost_coverage": MIN_COST_COVERAGE,
            "unavailable": _unavailable(rate_set=False, counts={}),
            "thresholds_version": thresholds.version,
        }

    rate = Decimal(str(configured))
    start = as_of - timedelta(days=window_days)
    in_window = [s for s in settlements if start < s.document_date <= as_of]

    # ``lags`` applies the evidence floor and drops every party under it — the
    # absence of a key is the refusal, and it is why nothing downstream needs a
    # second floor of its own.
    lags = payments.lags(in_window)
    slow = _slow_days(in_window, lags)

    accounts = _accounts(rows, lags, slow, names, rate)
    by_margin = _rank(accounts, lambda a: a.gross_margin)
    by_adjusted = _rank(accounts, lambda a: a.adjusted_margin)

    # Ranked by what the wait costs in rupees, worst first: the same
    # materiality-first rule ``portfolio.attention_rank`` applies, because a
    # 2 pp drag on ₹40 lakh is worth an afternoon and a 20 pp drag on ₹20,000 is
    # not. Rows with no figure sort last — they are not cheap, they are unknown,
    # and they must not head a list titled "what your credit costs".
    accounts.sort(key=lambda a: (a.measured, a.charge or _ZERO,
                                 a.costed_revenue),
                  reverse=True)

    counts: dict[str, int] = {}
    for account in accounts:
        counts[account.reason] = counts.get(account.reason, 0) + 1

    return {
        "measurable": bool(by_adjusted),
        "rate_set": True,
        "rate": float(rate),
        "accounts": [{**a.to_dict(),
                      "rank_by_margin": by_margin.get(a.customer_id),
                      "rank_by_adjusted_margin": by_adjusted.get(a.customer_id)}
                     for a in accounts],
        "totals": _totals(accounts),
        "counts": counts,
        "window": _window(as_of, window_days, settled=len(in_window)),
        "metrics_computed_at": _computed_at(rows),
        "reason_meanings": REASON_MEANING,
        "min_settlements": payments.MIN_SETTLEMENTS,
        "min_cost_coverage": MIN_COST_COVERAGE,
        "unavailable": _unavailable(rate_set=True, counts=counts),
        "thresholds_version": thresholds.version,
    }


def _slow_days(settled: list[payments.Settlement],
               lags: dict[str, payments.Lag]) -> dict[str, int]:
    """The ninetieth percentile of days-to-pay, per party above the floor.

    Keyed off ``lags`` rather than off the settlements, so there is exactly one
    evidence floor in play — a party ``payments`` refused to describe does not
    acquire a bad case here.
    """
    grouped: dict[str, list[int]] = {}
    for s in settled:
        if s.party_id in lags:
            grouped.setdefault(s.party_id, []).append(s.days_to_pay)
    return {party: payments.percentile(days, 0.90)
            for party, days in grouped.items()}


def _window(as_of: date, window_days: int, *, settled: int) -> dict:
    return {
        "as_of": as_of.isoformat(),
        "start": (as_of - timedelta(days=window_days)).isoformat(),
        "days": window_days,
        "settlements_in_window": settled,
    }


def _computed_at(rows: list[models.CustomerItemMetric]) -> Optional[str]:
    """When the revenue half was last rebuilt.

    Published because the two halves of this join are aligned by *window* and
    not by *moment*: the settlements are read now, the metric rows were computed
    whenever the last backfill ran. Usually the same day and occasionally not,
    and a reader comparing this against today is the only way to see it.
    """
    stamps = [r.computed_at for r in rows
              if isinstance(getattr(r, "computed_at", None), datetime)]
    return max(stamps).isoformat() if stamps else None


def _unavailable(*, rate_set: bool, counts: dict[str, int]) -> list[dict]:
    out: list[dict] = []
    if not rate_set:
        out.append({
            "series": "financing_adjusted_profitability",
            # COLLECTABLE, and the distinction matters: the field exists on the
            # Settings screen and one person typing one number finishes it. A
            # PERMANENT here would tell a reader the platform cannot answer
            # this, and somebody would build a second one.
            "kind": absence.COLLECTABLE,
            "reason": ("No annual cost of capital is set, so there is no rate "
                       "at which to charge the money customers are holding. Set "
                       "'Annual cost of capital' in Settings — what a rupee "
                       "actually costs this business to fund for a year. It is "
                       "deliberately left empty rather than defaulted: a "
                       "plausible-looking rate would produce a figure somebody "
                       "plans a payment run around, and the stock carrying rate "
                       "is not it — that one also pays for the warehouse."),
        })
        return out

    if counts.get(NO_PAYMENT_HISTORY):
        out.append({
            "series": "financing_cost_for_thin_payment_history",
            # TRANSIENT: nobody can record a settlement that has not happened,
            # and the count falls on its own as the accounts trade. Putting
            # "wait" on a worklist is noise.
            "kind": absence.TRANSIENT,
            "reason": (f"{counts[NO_PAYMENT_HISTORY]} account(s) settled fewer "
                       f"than {payments.MIN_SETTLEMENTS} invoices inside the "
                       "window, so how long they take to pay is not "
                       "established. They carry no financing figure rather than "
                       "the book's median, which would state a cost for the "
                       "accounts there is least evidence about."),
        })
    thin = counts.get(NO_COSTED_REVENUE, 0) + counts.get(THIN_COST_COVERAGE, 0)
    if thin:
        out.append({
            "series": "financing_adjusted_margin_for_uncosted_accounts",
            # COLLECTABLE: each of these is a bill not entered or not synced.
            "kind": absence.COLLECTABLE,
            "reason": (f"{thin} account(s) have too little of their revenue "
                       "covered by a bill for a margin to be stated. Their "
                       "financing cost is real and their profit is unknown, so "
                       "subtracting one from the other would print a confident "
                       "loss. They are listed with the reason instead."),
        })
    out.append({
        "series": "financing_cost_on_uncosted_revenue",
        # PERMANENT on the honest reading, and it is not the coverage gap above.
        # Even at complete cost coverage this charge is levied on line revenue,
        # while the receivable a bank funds carries tax on top. Closing that
        # would mean reading invoice totals per customer, which is a different
        # join at a different grain.
        "kind": absence.PERMANENT,
        "reason": ("The charge is levied on line revenue, so it understates "
                   "what the receivable actually ties up — the invoice the bank "
                   "funds carries GST on top, and an account's uncosted revenue "
                   "is funded too without contributing profit. Both push the "
                   "figure the same way: what is shown is a floor under the "
                   "cost of this account's credit, not a ceiling."),
    })
    return out
