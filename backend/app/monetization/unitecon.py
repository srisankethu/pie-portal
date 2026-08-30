"""PIE's own unit economics: what a customer costs to serve, and what it is worth.

The cost drivers are read off this architecture rather than invented for the
model — inference per interpreted RFQ, catalogue embedding per SKU, a
multi-tenant slice of infrastructure, ERP polling, storage of synced rows, and
the two human lines that dominate all of them. Every rate lives in
``config.MonetizationParameters`` and every one carries a provenance grade,
because the two largest (support and customer success) are the two nobody has
measured and the ones LTV is most sensitive to.

Two definitional choices, stated because they change the headline:

**Onboarding sits in COGS, amortised over the LTV horizon** — not in CAC. It is
work done to make one customer's product function, not to win them; putting it
in CAC would flatter gross margin and understate payback at exactly the same
time. A deployment that sells implementation separately would move it.

**Contribution margin is gross profit less customer success.** Success is a
retention cost, not a delivery cost: a customer who needed no account management
would still need inference and infrastructure. Keeping the two lines apart is
what makes "the product is profitable but the account is not" a visible state
rather than one blended number.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from .config import (Evidence, MonetizationParameters, PROVENANCE, load_parameters,
                     weakest)
from .customer import CustomerProfile, money

_ZERO = Decimal("0")

#: Above this, the ratio is reporting a bad CAC rather than a good business.
#: 20x is roughly three times the healthiest ratio a real B2B software company
#: sustains, so nothing below it is treated as suspicious.
IMPLAUSIBLE_LTV_CAC = 20.0

#: The parameters every COGS figure depends on. Named so the weakest grade
#: among them can be attached to the answer instead of the reader having to
#: work out which assumption the number rests on.
_COGS_INPUTS = ("inference_cost_per_rfq", "embedding_cost_per_sku",
                "embedding_refresh_cost_per_sku_year",
                "infra_cost_per_customer_month",
                "storage_cost_per_million_rows_year",
                "api_cost_per_customer_year", "support_cost_per_customer_year",
                "onboarding_cost_per_customer")


@dataclass(frozen=True)
class CostToServe:
    """One customer's annual cost to serve, by driver."""

    inference: Decimal
    embedding_refresh: Decimal
    onboarding_amortised: Decimal
    infrastructure: Decimal
    storage: Decimal
    third_party_api: Decimal
    support: Decimal
    total_cogs: Decimal
    customer_success: Decimal
    #: One-off, shown separately from the amortised slice above so the cash
    #: cost of landing a customer is never hidden inside an annual average.
    onboarding_one_off: Decimal
    embedding_one_off: Decimal

    def as_dict(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.__dict__.items()}


def cost_to_serve(profile: CustomerProfile,
                  params: Optional[MonetizationParameters] = None) -> CostToServe:
    """Annual COGS for one customer, driven by its own volumes.

    Inference is charged on the RFQs actually routed through PIE, not on the
    customer's whole enquiry book: an organisation at 20% adoption costs a fifth
    of the model spend of one at 100%, and averaging that away would price the
    early customers — who are the low-adoption ones — as though they were the
    late ones.
    """
    params = params or load_parameters()
    covered_rfqs = Decimal(profile.annual_rfqs) * Decimal(str(max(0.0, min(
        1.0, profile.pie_rfq_share))))
    skus = Decimal(profile.sku_count)

    inference = money(covered_rfqs * params.inference_cost_per_rfq)
    refresh = money(skus * params.embedding_refresh_cost_per_sku_year)
    embedding_one_off = money(skus * params.embedding_cost_per_sku)
    infra = money(params.infra_cost_per_customer_month * Decimal("12"))
    storage = money(Decimal(str(profile.erp_rows_millions))
                    * params.storage_cost_per_million_rows_year)
    api = money(params.api_cost_per_customer_year)
    support = money(params.support_cost_per_customer_year)
    horizon = max(1, params.ltv_horizon_years)
    onboarding_amortised = money(
        (params.onboarding_cost_per_customer + embedding_one_off) / Decimal(horizon))

    total = money(inference + refresh + onboarding_amortised + infra + storage
                  + api + support)
    return CostToServe(
        inference=inference, embedding_refresh=refresh,
        onboarding_amortised=onboarding_amortised, infrastructure=infra,
        storage=storage, third_party_api=api, support=support, total_cogs=total,
        customer_success=money(params.customer_success_cost_per_customer_year),
        onboarding_one_off=money(params.onboarding_cost_per_customer),
        embedding_one_off=embedding_one_off)


def acquisition_cost(annual_fee: Decimal,
                     params: Optional[MonetizationParameters] = None) -> Decimal:
    """What winning one customer at this price costs.

    ``max(floor, share x ACV)`` rather than a flat figure, and the flat figure
    is what this replaced. It reported an LTV/CAC of 569 on the largest segment
    — a number that reads as an extraordinary business and is actually the
    model saying a ₹5.5 Cr enterprise contract and an ₹11L self-serve one are
    won by the same effort. One function, so the per-customer view and the
    five-year plan cannot disagree about it.
    """
    params = params or load_parameters()
    scaled = max(_ZERO, annual_fee) * Decimal(str(
        max(0.0, params.cac_share_of_first_year_acv)))
    return money(max(params.cac_per_customer, scaled))


@dataclass(frozen=True)
class UnitEconomics:
    """What one customer is worth to PIE at a given annual fee."""

    annual_revenue: Decimal
    cost: CostToServe
    gross_profit: Decimal
    gross_margin: Optional[float]
    contribution: Decimal
    contribution_margin: Optional[float]
    cac: Decimal
    ltv: Decimal
    ltv_cac: Optional[float]
    cac_payback_months: Optional[float]
    revenue_per_rfq: Optional[Decimal]
    revenue_per_order: Optional[Decimal]
    expected_life_years: Optional[float]
    evidence_grade: str
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "annual_revenue": str(self.annual_revenue),
            "cost": self.cost.as_dict(),
            "gross_profit": str(self.gross_profit),
            "gross_margin": self.gross_margin,
            "contribution": str(self.contribution),
            "contribution_margin": self.contribution_margin,
            "cac": str(self.cac), "ltv": str(self.ltv), "ltv_cac": self.ltv_cac,
            "cac_payback_months": self.cac_payback_months,
            "revenue_per_rfq": (None if self.revenue_per_rfq is None
                                else str(self.revenue_per_rfq)),
            "revenue_per_order": (None if self.revenue_per_order is None
                                  else str(self.revenue_per_order)),
            "expected_life_years": self.expected_life_years,
            "evidence_grade": self.evidence_grade,
            "warnings": list(self.warnings),
        }


def unit_economics(profile: CustomerProfile, annual_fee: Decimal,
                   params: Optional[MonetizationParameters] = None,
                   *, orders: Optional[Decimal] = None) -> UnitEconomics:
    """PIE's own P&L for one customer at one price.

    ``ltv`` compounds the cohort's revenue at ``net_revenue_retention`` rather
    than at gross retention, and applies contribution margin to it. NRR is
    already net of churn *and* expansion, so multiplying it by a separate
    survival curve would count the churn twice — the most common arithmetic
    error in a SaaS model and one that produces a comfortingly large number.
    """
    params = params or load_parameters()
    cost = cost_to_serve(profile, params)
    revenue = money(max(_ZERO, annual_fee))
    gp = money(revenue - cost.total_cogs)
    contribution = money(gp - cost.customer_success)

    gm = float(gp / revenue) if revenue > 0 else None
    cm = float(contribution / revenue) if revenue > 0 else None

    horizon = max(1, params.ltv_horizon_years)
    nrr = Decimal(str(max(0.0, params.net_revenue_retention)))
    ltv = _ZERO
    if revenue > 0 and cm is not None:
        cumulative_revenue = sum((revenue * nrr ** t for t in range(horizon)), _ZERO)
        ltv = money(cumulative_revenue * Decimal(str(cm)))

    cac = acquisition_cost(revenue, params)
    ltv_cac = float(ltv / cac) if cac > 0 else None
    payback = (float(cac / (contribution / Decimal("12")))
               if contribution > 0 else None)

    covered_rfqs = Decimal(profile.annual_rfqs) * Decimal(str(max(0.0, min(
        1.0, profile.pie_rfq_share))))
    order_count = orders if orders is not None else profile.orders

    warnings: list[str] = []
    if contribution <= 0:
        warnings.append(
            "Contribution is not positive: this price does not cover the cost "
            "to serve. CAC payback is UNKNOWN rather than large — there is no "
            "number of months in which a negative margin repays anything.")
    if ltv_cac is not None and ltv_cac < 3.0:
        warnings.append(
            f"LTV/CAC of {ltv_cac:.2f} is below the 3.0 a venture-fundable "
            "motion needs. Either the price is too low or CAC is too high, and "
            "CAC here is NEEDS_VALIDATION.")
    if payback is not None and payback > 24:
        warnings.append(
            f"CAC payback of {payback:.0f} months is beyond what can be funded "
            "from revenue.")
    if ltv_cac is not None and ltv_cac > IMPLAUSIBLE_LTV_CAC:
        # An implausibly good ratio is evidence about the inputs, not about the
        # business — the same reading as an implausibly bad one. The flat-CAC
        # version of this model reported 569 on the largest segment before
        # ``acquisition_cost`` scaled CAC with contract value; anything still
        # above the bar means retention or cost to serve is now the optimistic
        # input, and both are NEEDS_VALIDATION.
        warnings.append(
            f"LTV/CAC of {ltv_cac:.0f} is above {IMPLAUSIBLE_LTV_CAC:.0f} and "
            "should be read as a statement about the inputs rather than about "
            "the business: net revenue retention and cost to serve are both "
            "unmeasured, and this ratio is where their optimism accumulates.")

    grade = weakest([PROVENANCE[p][0] for p in _COGS_INPUTS]
                    + [PROVENANCE["cac_per_customer"][0],
                       PROVENANCE["net_revenue_retention"][0]])

    life = (1.0 / (1.0 - params.annual_gross_retention)
            if 0.0 < params.annual_gross_retention < 1.0 else None)

    return UnitEconomics(
        annual_revenue=revenue, cost=cost, gross_profit=gp, gross_margin=gm,
        contribution=contribution, contribution_margin=cm, cac=cac, ltv=ltv,
        ltv_cac=ltv_cac, cac_payback_months=payback,
        revenue_per_rfq=(money(revenue / covered_rfqs) if covered_rfqs > 0 else None),
        revenue_per_order=(money(revenue / order_count) if order_count > 0 else None),
        expected_life_years=life,
        evidence_grade=grade.value,
        warnings=tuple(warnings))


def floor_price(profile: CustomerProfile,
                params: Optional[MonetizationParameters] = None,
                *, target_gross_margin: float = 0.75) -> Decimal:
    """The lowest annual fee that still clears a target gross margin.

    This is the number that kills the 0.1%-of-margin hypothesis outright for the
    small and mid segments, and it does so without any argument about value: a
    fee below the cost of serving the customer is not a pricing choice, it is a
    subsidy. Rounded up to the quoting increment, because a floor rounded down
    is not a floor.
    """
    params = params or load_parameters()
    cost = cost_to_serve(profile, params)
    gm = min(0.99, max(0.0, target_gross_margin))
    raw = cost.total_cogs / Decimal(str(1.0 - gm))
    step = params.fee_rounding_increment
    if step <= 0:
        return money(raw)
    return money((raw / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
                 if raw % step == 0 else
                 ((raw // step) + 1) * step)


#: Re-exported so callers can grade a unit-economics answer without importing
#: config directly. The grade is the weakest input, never the average.
UNKNOWN_GRADE = Evidence.NEEDS_VALIDATION
