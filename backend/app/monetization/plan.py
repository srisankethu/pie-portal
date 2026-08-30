"""The five-year business PIE becomes under a given price. Three scenarios.

This module answers the question the pricing choice is actually *for*: which
metric lets PIE become a large company rather than a good piece of software.
It does that by taking an ACV per segment — computed by ``report`` from the
recommended structure, never written down here, so the plan can never disagree
with the pricing model that produced it — and running it forward.

**Customer counts are averaged within the year, not taken at year end.** A
cohort that lands in month eleven bills one month, and a plan that charges it
twelve overstates year-one revenue by more than any pricing decision in this
repository changes it. The mid-year convention is crude and it is stated;
what it is not is silently optimistic.

**EBITDA is after the cost of acquiring that year's customers.** Capitalising
CAC would make every scenario profitable in year two, which is the standard way
a plan stops being useful.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from .config import MonetizationParameters, load_parameters
from .customer import money
from .segments import ARCHETYPES
from .unitecon import acquisition_cost, cost_to_serve

_ZERO = Decimal("0")

#: Customer counts the brief asks ARR to be reported at.
ARR_MILESTONES = (10, 50, 100, 500, 1000)


@dataclass(frozen=True)
class GrowthScenario:
    """One five-year trajectory. Prices are supplied, not stored here."""

    name: str
    #: New customers landed in each of the five years.
    new_customers: tuple[int, ...]
    #: Share of new customers by archetype key. Must sum to 1; normalised if not.
    segment_mix: dict[str, float]
    #: Annual gross logo retention applied to the opening base.
    gross_retention: float
    #: Price and seat expansion within retained accounts, per year.
    acv_growth: float
    #: Everything that is not COGS, CS or CAC: engineering, product, G&A.
    fixed_opex_year_one: Decimal
    fixed_opex_growth: float
    #: And the floor under it as a share of revenue. A purely fixed opex base
    #: is what produced an 86% EBITDA margin in year five of the base case —
    #: revenue compounding past a cost line growing at 40%. Real engineering
    #: and G&A scale with the business, so opex is
    #: ``max(fixed base, share x revenue)`` and the share is what binds at
    #: scale. Nothing in this business measures it; it is the range B2B
    #: software at this growth rate actually runs at.
    opex_floor_pct_of_revenue: float = 0.45
    note: str = ""

    def mix(self) -> dict[str, float]:
        total = sum(max(0.0, v) for v in self.segment_mix.values())
        if total <= 0:
            return {"mid": 1.0}
        return {k: max(0.0, v) / total for k, v in self.segment_mix.items()}


SCENARIOS: tuple[GrowthScenario, ...] = (
    GrowthScenario(
        name="conservative",
        new_customers=(4, 9, 18, 30, 45),
        segment_mix={"small": 0.55, "mid": 0.40, "large": 0.05},
        gross_retention=0.80, acv_growth=0.05,
        fixed_opex_year_one=Decimal("28000000"), fixed_opex_growth=0.30,
        opex_floor_pct_of_revenue=0.52,
        note="Founder-led sales throughout. Small-heavy mix, weak retention "
             "because onboarding stays manual and the value report is thin."),
    GrowthScenario(
        name="base",
        new_customers=(6, 16, 34, 62, 100),
        segment_mix={"small": 0.40, "mid": 0.50, "large": 0.10},
        gross_retention=0.85, acv_growth=0.10,
        fixed_opex_year_one=Decimal("35000000"), fixed_opex_growth=0.40,
        opex_floor_pct_of_revenue=0.45,
        note="A repeatable motion from year two, connector coverage widening, "
             "and the value report carrying the renewal."),
    GrowthScenario(
        name="aggressive",
        new_customers=(8, 26, 62, 130, 240),
        segment_mix={"small": 0.30, "mid": 0.50, "large": 0.20},
        gross_retention=0.90, acv_growth=0.18,
        fixed_opex_year_one=Decimal("48000000"), fixed_opex_growth=0.50,
        opex_floor_pct_of_revenue=0.40,
        note="Transaction participation live from year three, so ACV grows "
             "with the customer's book rather than only at renewal."),
)


@dataclass(frozen=True)
class YearRow:
    year: int
    opening_customers: float
    new_customers: int
    churned_customers: float
    closing_customers: float
    billed_customers: float
    rfqs: Decimal
    gmv: Decimal
    transactions: Decimal
    revenue: Decimal
    cogs: Decimal
    gross_profit: Decimal
    gross_margin: Optional[float]
    customer_success: Decimal
    cac_spend: Decimal
    fixed_opex: Decimal
    ebitda: Decimal
    ebitda_margin: Optional[float]
    #: EBITDA with this year's customer-acquisition spend added back.
    #:
    #: Not a nicer version of EBITDA and never to be quoted as one. It answers
    #: exactly one question — *is the installed base profitable, or is the
    #: product itself losing money?* — because every scenario here is EBITDA-
    #: negative through year five and the two possible reasons are opposite.
    #: Growth spending is a choice that can stop; a book that loses money on
    #: the customers it already has cannot be fixed by stopping anything.
    ebitda_before_growth: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "opening_customers": round(self.opening_customers, 2),
            "new_customers": self.new_customers,
            "churned_customers": round(self.churned_customers, 2),
            "closing_customers": round(self.closing_customers, 2),
            "billed_customers": round(self.billed_customers, 2),
            "rfqs": str(self.rfqs), "gmv": str(self.gmv),
            "transactions": str(self.transactions),
            "revenue": str(self.revenue), "cogs": str(self.cogs),
            "gross_profit": str(self.gross_profit),
            "gross_margin": self.gross_margin,
            "customer_success": str(self.customer_success),
            "cac_spend": str(self.cac_spend), "fixed_opex": str(self.fixed_opex),
            "ebitda": str(self.ebitda), "ebitda_margin": self.ebitda_margin,
            "ebitda_before_growth": str(self.ebitda_before_growth),
        }


def _blended(mix: dict[str, float], per_segment: dict[str, Decimal]) -> Decimal:
    return sum((Decimal(str(share)) * per_segment.get(key, _ZERO)
                for key, share in mix.items()), _ZERO)


def project(scenario: GrowthScenario, acv: dict[str, Decimal],
            params: Optional[MonetizationParameters] = None,
            *, starting_customers: float = 0.0) -> dict[str, Any]:
    """Five years of one scenario at one price book.

    ``acv`` maps archetype key to annual contract value. It is an argument
    rather than a field on the scenario because the whole point of the exercise
    is to run the same trajectory under different pricing models; a scenario
    that carried its own prices would have to be duplicated per model, and the
    copies would drift the first time a rate moved.
    """
    params = params or load_parameters()
    mix = scenario.mix()

    per_customer_cogs = {
        key: cost_to_serve(profile, params).total_cogs
        for key, profile in ARCHETYPES.items()}
    per_customer_rfqs = {
        key: Decimal(p.annual_rfqs) * Decimal(str(p.pie_rfq_share))
        for key, p in ARCHETYPES.items()}
    per_customer_gmv = {key: p.annual_gmv for key, p in ARCHETYPES.items()}
    per_customer_orders = {key: p.orders for key, p in ARCHETYPES.items()}

    blended_cogs = _blended(mix, per_customer_cogs)
    blended_rfqs = _blended(mix, per_customer_rfqs)
    blended_gmv = _blended(mix, per_customer_gmv)
    blended_orders = _blended(mix, per_customer_orders)
    blended_acv = _blended(mix, acv)

    rows: list[YearRow] = []
    opening = float(starting_customers)
    fixed = scenario.fixed_opex_year_one

    for index, new in enumerate(scenario.new_customers):
        churned = opening * (1.0 - scenario.gross_retention)
        closing = opening - churned + new
        # Mid-year convention: the year's new cohort bills half a year.
        billed = max(0.0, opening - churned) + new / 2.0
        price = blended_acv * (Decimal("1") + Decimal(str(scenario.acv_growth))) ** index

        revenue = money(Decimal(str(billed)) * price)
        cogs = money(Decimal(str(billed)) * blended_cogs)
        gp = money(revenue - cogs)
        cs = money(Decimal(str(billed)) * params.customer_success_cost_per_customer_year)
        # The same CAC function the per-customer view uses, on this year's
        # price. Two definitions of what a customer costs to win is exactly the
        # duplication that lets a plan and a unit-economics deck disagree.
        cac_spend = money(Decimal(new) * acquisition_cost(price, params))
        opex = money(max(fixed, revenue
                         * Decimal(str(max(0.0, scenario.opex_floor_pct_of_revenue)))))
        ebitda = money(gp - cs - cac_spend - opex)

        rows.append(YearRow(
            year=index + 1, opening_customers=opening, new_customers=new,
            churned_customers=churned, closing_customers=closing,
            billed_customers=billed,
            rfqs=money(Decimal(str(billed)) * blended_rfqs),
            gmv=money(Decimal(str(billed)) * blended_gmv),
            transactions=money(Decimal(str(billed)) * blended_orders),
            revenue=revenue, cogs=cogs, gross_profit=gp,
            gross_margin=(float(gp / revenue) if revenue > 0 else None),
            customer_success=cs, cac_spend=cac_spend, fixed_opex=opex,
            ebitda=ebitda,
            ebitda_margin=(float(ebitda / revenue) if revenue > 0 else None),
            ebitda_before_growth=money(ebitda + cac_spend)))

        opening = closing
        fixed = money(fixed * (Decimal("1") + Decimal(str(scenario.fixed_opex_growth))))

    return {
        "scenario": scenario.name, "note": scenario.note,
        "segment_mix": mix,
        "blended_acv_year_one": str(blended_acv),
        "acv_by_segment": {k: str(v) for k, v in acv.items()},
        "years": [r.as_dict() for r in rows],
        "arr_at_milestones": arr_at(acv, mix, scenario.acv_growth),
        "ending_customers": round(opening, 2),
        "five_year_revenue": str(money(sum((r.revenue for r in rows), _ZERO))),
        "five_year_ebitda": str(money(sum((r.ebitda for r in rows), _ZERO))),
    }


def arr_at(acv: dict[str, Decimal], mix: dict[str, float],
           acv_growth: float = 0.0) -> list[dict[str, str]]:
    """ARR at 10 / 50 / 100 / 500 / 1,000 customers, at today's price book.

    Deliberately *not* grown by ``acv_growth``: this is a scale question, not a
    time question, and compounding a price into it would make the milestone
    depend on how long the company took to get there.
    """
    blended = _blended(mix, acv)
    return [{"customers": str(n), "arr": str(money(blended * Decimal(n)))}
            for n in ARR_MILESTONES]


def five_year(acv: dict[str, Decimal],
              params: Optional[MonetizationParameters] = None) -> dict[str, Any]:
    """All three scenarios under one price book."""
    params = params or load_parameters()
    return {s.name: project(s, acv, params) for s in SCENARIOS}
