"""What a customer's business looks like before PIE, after PIE, and the gap.

Three objects and one rule.

``CustomerProfile`` is the business as it is today. ``PieImpact`` is what the
platform changes about it. ``Waterfall`` is the two funnels side by side and
the decomposition of the difference — and it is deliberately a *decomposition*
rather than a single number, because the pricing question turns entirely on
which component a given metric can reach. A margin-share fee touches incremental
gross profit and cannot see procurement savings at all; a GMV fee sees revenue
and is blind to whether any of it was profitable.

**The rule: incremental revenue is not value created.** Value is gross profit,
plus buy-side savings, plus — only when someone has supplied an hourly rate —
labour. Revenue is carried because a transaction-priced model needs it as a
*base*, never because it is what PIE is worth. A model that captured a share of
incremental revenue would be charging a distributor for the cost of goods it
bought from someone else.

Money is ``Decimal`` throughout, margins are ratios (``0.22``), and conversion
*uplifts* are percentage points (``_pp``) — the same conventions as
``commercial/``, so a figure can move between the two without a units bug.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from .config import MonetizationParameters, load_parameters

#: Two decimal places. Money in this model is a projection over a year, not a
#: ledger row: quantising to the paisa keeps two runs byte-identical without
#: implying the fourth decimal of a forecast means anything.
MONEY_EXPONENT = Decimal("0.01")
_ZERO = Decimal("0")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_EXPONENT, rounding=ROUND_HALF_UP)


def _rate(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp a rate into its legal band.

    Uplifts are supplied by a human in a calculator, and a conversion rate of
    1.4 is not a business the model should extrapolate into — it is a typo. The
    clamp is silent by design at the boundary but the caller can see it, because
    ``Waterfall`` reports the post-clamp rates it actually used.
    """
    return max(low, min(high, value))


@dataclass(frozen=True)
class CustomerProfile:
    """One distributor's economics as they stand today, before PIE.

    The funnel is canonical and GMV is derived from it. That direction is
    deliberate: GMV and the funnel are two statements of the same fact, and
    holding both as inputs is the responsibility duplication CLAUDE.md §2 warns
    about — the day they disagree, nothing can say which is right. A caller who
    knows GMV and not the average order value uses :meth:`with_annual_gmv`,
    which back-solves one input instead of storing a second truth.
    """

    name: str
    #: Enquiry lines arriving in a year, across every channel.
    annual_rfqs: int
    #: Share of those routed through PIE. Adoption, not capability.
    pie_rfq_share: float
    #: RFQ -> quote. What fraction of arriving enquiries get quoted at all.
    quote_conversion: float
    #: Quote -> order. What fraction of quotes are won.
    order_conversion: float
    average_order_value: Decimal
    gross_margin: float
    sales_engineers: int
    cost_per_employee_year: Decimal
    #: Minutes of human time per RFQ read, and per quote prepared.
    rfq_processing_minutes: float
    quotation_minutes: float
    #: Drivers of PIE's own cost to serve, not of the customer's economics.
    sku_count: int = 25_000
    erp_rows_millions: float = 1.0

    # ── derived, all from the funnel ────────────────────────────────────────
    @property
    def quotes(self) -> Decimal:
        return Decimal(self.annual_rfqs) * Decimal(str(_rate(self.quote_conversion)))

    @property
    def orders(self) -> Decimal:
        return self.quotes * Decimal(str(_rate(self.order_conversion)))

    @property
    def annual_gmv(self) -> Decimal:
        return money(self.orders * self.average_order_value)

    @property
    def annual_gross_profit(self) -> Decimal:
        return money(self.annual_gmv * Decimal(str(_rate(self.gross_margin))))

    @property
    def gross_profit_per_order(self) -> Optional[Decimal]:
        if self.orders <= 0:
            return None
        return money(self.annual_gross_profit / self.orders)

    @property
    def annual_labour_cost(self) -> Decimal:
        return money(Decimal(self.sales_engineers) * self.cost_per_employee_year)

    def with_annual_gmv(self, gmv: Decimal) -> "CustomerProfile":
        """The same funnel scaled so it produces ``gmv``, by moving order value.

        Returns ``self`` unchanged when the funnel produces no orders — there is
        no order value that turns zero orders into revenue, and inventing one
        would be the benign default CLAUDE.md §1 forbids.
        """
        if self.orders <= 0 or gmv <= 0:
            return self
        return replace(self, average_order_value=money(gmv / self.orders))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "annual_rfqs": self.annual_rfqs,
            "pie_rfq_share": self.pie_rfq_share,
            "quote_conversion": self.quote_conversion,
            "order_conversion": self.order_conversion,
            "average_order_value": str(self.average_order_value),
            "gross_margin": self.gross_margin,
            "sales_engineers": self.sales_engineers,
            "cost_per_employee_year": str(self.cost_per_employee_year),
            "rfq_processing_minutes": self.rfq_processing_minutes,
            "quotation_minutes": self.quotation_minutes,
            "sku_count": self.sku_count,
            "erp_rows_millions": self.erp_rows_millions,
            "quotes": str(money(self.quotes)),
            "orders": str(money(self.orders)),
            "annual_gmv": str(self.annual_gmv),
            "annual_gross_profit": str(self.annual_gross_profit),
            "gross_profit_per_order": (None if self.gross_profit_per_order is None
                                       else str(self.gross_profit_per_order)),
        }


@dataclass(frozen=True)
class PieImpact:
    """What the platform changes, expressed as movements on the funnel.

    Every field is a *mechanism* that already exists in this product, named for
    what causes it, so that a claim can be argued from a screen rather than
    asserted. The two response-time fields are the exception and are carried for
    reporting only: a faster reply is why conversion moves, and counting both
    the cause and the effect would double the same rupee.
    """

    #: Enquiries that today are never quoted and now are, as percentage points
    #: added to quote conversion on the PIE-covered share. This is "previously
    #: unquotable RFQs recovered" and "additional RFQs processed" — one
    #: mechanism, not two, and modelled once.
    quote_conversion_uplift_pp: float = 0.0
    #: Quotes won that would have been lost — faster turnaround, better match.
    order_conversion_uplift_pp: float = 0.0
    #: Larger baskets from cross-sell and substitution, as a ratio on AOV.
    aov_uplift: float = 0.0
    #: Margin held that would have leaked, as percentage points on gross margin.
    #: This is the floor/approval mechanism; it is *not* added again as a
    #: separate "leakage prevented" line.
    gross_margin_uplift_pp: float = 0.0
    #: Buy-side saving from resolved equivalents, as a ratio on the cost of
    #: goods for PIE-covered orders. Accrues to cost, never to quoted margin.
    procurement_saving_rate: float = 0.0
    #: Human minutes removed per covered RFQ and per covered quote.
    minutes_saved_per_rfq: float = 0.0
    minutes_saved_per_quote: float = 0.0
    #: Reported, never valued. See the class docstring.
    response_time_improvement: float = 0.0
    #: Substitution funnel, reported alongside the value it already produces
    #: through ``aov_uplift`` and ``procurement_saving_rate``.
    substitution_opportunity_rate: float = 0.0
    substitution_success_rate: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class Funnel:
    """One side of the comparison: RFQs -> quotes -> orders -> revenue -> GP."""

    rfqs: Decimal
    quotes: Decimal
    orders: Decimal
    revenue: Decimal
    cogs: Decimal
    gross_profit: Decimal

    def as_dict(self) -> dict[str, str]:
        return {"rfqs": str(money(self.rfqs)), "quotes": str(money(self.quotes)),
                "orders": str(money(self.orders)), "revenue": str(self.revenue),
                "cogs": str(self.cogs), "gross_profit": str(self.gross_profit)}


def _funnel(rfqs: Decimal, quote_rate: float, order_rate: float,
            aov: Decimal, margin: float) -> Funnel:
    quotes = rfqs * Decimal(str(_rate(quote_rate)))
    orders = quotes * Decimal(str(_rate(order_rate)))
    revenue = money(orders * aov)
    gp = money(revenue * Decimal(str(_rate(margin))))
    return Funnel(rfqs=rfqs, quotes=quotes, orders=orders, revenue=revenue,
                  cogs=money(revenue - gp), gross_profit=gp)


@dataclass(frozen=True)
class Waterfall:
    """Baseline, with-PIE, and the difference broken into its causes.

    ``total_economic_value`` is the only figure a fee is ever sized against, and
    it is assembled from named components rather than from a revenue delta. The
    components do not overlap: conversion, order value and margin are three
    disjoint movements on one funnel, computed sequentially so each is credited
    once; procurement is on the buy side; productivity is time.

    ``productivity_savings`` is ``None`` — not ``0`` — whenever no hourly rate
    has been supplied. That is the "absence of evidence is not a pass" rule in
    the form it takes here: a zero would silently lower every ROI in the model
    while reading as a measurement, and an UNKNOWN forces the reader to decide.
    """

    profile: CustomerProfile
    impact: PieImpact
    baseline: Funnel
    with_pie: Funnel
    #: The covered funnel's own two sides, for the metrics that only see flow
    #: that actually passed through the platform.
    covered_baseline: Funnel
    covered_with_pie: Funnel

    incremental_orders: Decimal
    incremental_revenue: Decimal
    incremental_gross_profit: Decimal
    gp_from_conversion: Decimal
    gp_from_order_value: Decimal
    gp_from_margin: Decimal
    procurement_savings: Decimal
    hours_saved: Decimal
    productivity_savings: Optional[Decimal]
    productivity_excluded_reason: Optional[str]

    total_economic_value: Decimal

    # ── the bases a pricing metric can be applied to ────────────────────────
    @property
    def pie_touched_gmv(self) -> Decimal:
        """Revenue on orders that went through PIE. The base for a GMV fee."""
        return self.covered_with_pie.revenue

    @property
    def pie_touched_gross_margin(self) -> Decimal:
        """Gross profit on PIE-touched orders. The base for a margin-share fee.

        Note what this is *not*: it is not the customer's whole gross profit,
        and a fee quoted as "0.1% of margin" is ambiguous between the two by a
        factor of ``1 / pie_rfq_share``. The model reports both and the
        ambiguity is one of the findings, not a detail.
        """
        return self.covered_with_pie.gross_profit

    @property
    def total_gross_margin(self) -> Decimal:
        return self.with_pie.gross_profit

    @property
    def substitution_opportunities(self) -> Decimal:
        return money(self.covered_with_pie.quotes
                     * Decimal(str(_rate(self.impact.substitution_opportunity_rate))))

    @property
    def successful_substitutions(self) -> Decimal:
        return money(self.substitution_opportunities
                     * Decimal(str(_rate(self.impact.substitution_success_rate))))

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.as_dict(),
            "impact": self.impact.as_dict(),
            "baseline": self.baseline.as_dict(),
            "with_pie": self.with_pie.as_dict(),
            "covered_baseline": self.covered_baseline.as_dict(),
            "covered_with_pie": self.covered_with_pie.as_dict(),
            "incremental_orders": str(money(self.incremental_orders)),
            "incremental_revenue": str(self.incremental_revenue),
            "incremental_gross_profit": str(self.incremental_gross_profit),
            "components": {
                "gp_from_conversion": str(self.gp_from_conversion),
                "gp_from_order_value": str(self.gp_from_order_value),
                "gp_from_margin": str(self.gp_from_margin),
                "procurement_savings": str(self.procurement_savings),
                "productivity_savings": (None if self.productivity_savings is None
                                         else str(self.productivity_savings)),
            },
            "productivity_excluded_reason": self.productivity_excluded_reason,
            "hours_saved": str(money(self.hours_saved)),
            "total_economic_value": str(self.total_economic_value),
            "bases": {
                "pie_touched_gmv": str(self.pie_touched_gmv),
                "pie_touched_gross_margin": str(self.pie_touched_gross_margin),
                "total_gmv": str(self.with_pie.revenue),
                "total_gross_margin": str(self.total_gross_margin),
                "incremental_gross_margin": str(self.incremental_gross_profit),
            },
            "substitution": {
                "opportunities": str(self.substitution_opportunities),
                "successful": str(self.successful_substitutions),
            },
        }


def build_waterfall(profile: CustomerProfile, impact: PieImpact,
                    params: Optional[MonetizationParameters] = None) -> Waterfall:
    """The full baseline -> with-PIE comparison for one customer.

    The uncovered share of the funnel is carried through both sides unchanged.
    That is what keeps the incremental figure honest at low adoption: a platform
    reading 20% of enquiries cannot move the other 80%, and a model that applied
    the uplift to the whole book would overstate value by 5x at exactly the
    moment — the first year — when the number is being used to set a price.
    """
    params = params or load_parameters()
    share = Decimal(str(_rate(profile.pie_rfq_share)))
    rfqs = Decimal(profile.annual_rfqs)
    covered, uncovered = rfqs * share, rfqs * (Decimal("1") - share)

    q0, o0 = _rate(profile.quote_conversion), _rate(profile.order_conversion)
    m0, aov0 = _rate(profile.gross_margin), profile.average_order_value
    q1 = _rate(q0 + impact.quote_conversion_uplift_pp)
    o1 = _rate(o0 + impact.order_conversion_uplift_pp)
    m1 = _rate(m0 + impact.gross_margin_uplift_pp)
    aov1 = money(aov0 * (Decimal("1") + Decimal(str(max(-1.0, impact.aov_uplift)))))

    cov_base = _funnel(covered, q0, o0, aov0, m0)
    cov_pie = _funnel(covered, q1, o1, aov1, m1)
    unc = _funnel(uncovered, q0, o0, aov0, m0)

    baseline = _funnel(rfqs, q0, o0, aov0, m0)
    with_pie = Funnel(
        rfqs=rfqs, quotes=cov_pie.quotes + unc.quotes,
        orders=cov_pie.orders + unc.orders,
        revenue=money(cov_pie.revenue + unc.revenue),
        cogs=money(cov_pie.cogs + unc.cogs),
        gross_profit=money(cov_pie.gross_profit + unc.gross_profit))

    # Sequential attribution over the covered funnel: conversion first, then
    # order value on the post-conversion volume, then margin on the resulting
    # revenue. Sequential rather than one-at-a-time-from-baseline because the
    # three components must sum exactly to the total — an interaction term left
    # unattributed is a rupee the model claims and cannot place.
    step_conv = _funnel(covered, q1, o1, aov0, m0)
    step_aov = _funnel(covered, q1, o1, aov1, m0)
    gp_conv = money(step_conv.gross_profit - cov_base.gross_profit)
    gp_aov = money(step_aov.gross_profit - step_conv.gross_profit)
    gp_margin = money(cov_pie.gross_profit - step_aov.gross_profit)

    procurement = _ZERO
    if params.include_procurement_in_value:
        procurement = money(
            cov_pie.cogs * Decimal(str(_rate(impact.procurement_saving_rate))))

    minutes = (float(cov_pie.rfqs) * impact.minutes_saved_per_rfq
               + float(cov_pie.quotes) * impact.minutes_saved_per_quote)
    hours = money(Decimal(str(minutes / 60.0)))

    productivity: Optional[Decimal] = None
    reason: Optional[str] = None
    if not params.include_productivity_in_value:
        reason = ("Labour savings are counted in hours and deliberately not "
                  "valued: include_productivity_in_value is off, matching the "
                  "attribution ledger's refusal to price time.")
    elif params.productivity_hourly_rate is None:
        reason = ("UNKNOWN, not zero: productivity is switched on but no "
                  "fully-loaded hourly rate has been supplied, and inventing "
                  "one would fabricate the whole component.")
    else:
        productivity = money(hours * params.productivity_hourly_rate)

    incremental_gp = money(with_pie.gross_profit - baseline.gross_profit)
    total = money(incremental_gp + procurement + (productivity or _ZERO))

    return Waterfall(
        profile=profile, impact=impact, baseline=baseline, with_pie=with_pie,
        covered_baseline=cov_base, covered_with_pie=cov_pie,
        incremental_orders=with_pie.orders - baseline.orders,
        incremental_revenue=money(with_pie.revenue - baseline.revenue),
        incremental_gross_profit=incremental_gp,
        gp_from_conversion=gp_conv, gp_from_order_value=gp_aov,
        gp_from_margin=gp_margin, procurement_savings=procurement,
        hours_saved=hours, productivity_savings=productivity,
        productivity_excluded_reason=reason, total_economic_value=total)
