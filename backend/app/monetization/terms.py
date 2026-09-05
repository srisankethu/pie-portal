"""What turns a fee into a contract.

``strategies`` computes what a customer should pay in a year. Nothing computed
what they actually sign: how long for, when they pay, what the implementation
costs, what happens at renewal, how far a salesperson may discount, and how a
group of legal entities is treated. Those are seven separate commercial terms,
and their absence was the substance of "the pricing model is incomplete" — a
price list is not a price book.

The module is deliberately downstream of everything else here. It takes an
annual fee as given and never recomputes one, so there stays exactly one answer
to "what should this customer pay" and this file only decorates it. A term that
changed the fee would be a second pricing model wearing a contract's clothes.

**The discount floor is the tenant-side margin floor, pointed the other way.**
``commercial/incentive.discount_to_floor`` stops a salesperson conceding past
the point where a line stops earning; :func:`approve_discount` stops PIE's own
desk conceding past the point where an account stops paying for itself. Same
shape, different operands, one level up — cited rather than reused, because the
tenant's floor is computed from purchase cost and PIE's from cost to serve, and
sharing an implementation would eventually make one of them wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from .config import MonetizationParameters, load_parameters
from .customer import CustomerProfile, money
from .unitecon import cost_to_serve, floor_price

_ZERO = Decimal("0")


class PaymentCadence(str, Enum):
    """When the money actually arrives.

    Ordered by how much cash reaches PIE at signature, which is the only axis
    that matters for a company funding its next customer out of this one.
    """

    ANNUAL_UPFRONT = "ANNUAL_UPFRONT"
    SEMI_ANNUAL = "SEMI_ANNUAL"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"


#: Instalments per year, and the price adjustment for choosing it. Upfront earns
#: a discount because the cash is worth more to PIE than the discount costs;
#: monthly carries the quarterly premium because the administrative load and the
#: default risk are the same shape, only more so.
CADENCE_TERMS: dict[PaymentCadence, tuple[int, str]] = {
    PaymentCadence.ANNUAL_UPFRONT: (1, "discount"),
    PaymentCadence.SEMI_ANNUAL: (2, "neutral"),
    PaymentCadence.QUARTERLY: (4, "premium"),
    PaymentCadence.MONTHLY: (12, "premium"),
}


def implementation_fee(annual_fee: Decimal, profile: CustomerProfile,
                       params: Optional[MonetizationParameters] = None) -> Decimal:
    """The one-time charge for getting this customer running.

    ``max(cost recovery, share of annual)``, then capped. Three constraints
    rather than one because the work and the fee scale on different axes: the
    effort is driven by SKU count and connector complexity, the willingness to
    pay by size. A share alone under-recovers on a small customer with a large
    catalogue; a cost-plus alone leaves money on the table at the top and reads
    as arbitrary to a buyer who expects implementation to scale with the deal.
    """
    params = params or load_parameters()
    cost = cost_to_serve(profile, params)
    recovery = money((cost.onboarding_one_off + cost.embedding_one_off)
                     * Decimal(str(max(1.0, params.implementation_cost_markup))))
    share = money(max(_ZERO, annual_fee)
                  * Decimal(str(max(0.0, params.implementation_fee_share_of_annual))))
    cap = money(max(_ZERO, annual_fee)
                * Decimal(str(max(0.0, params.implementation_fee_cap_multiple))))
    fee = max(recovery, share)
    return money(min(fee, cap)) if cap > 0 else money(fee)


def term_discount(term_years: int,
                  params: Optional[MonetizationParameters] = None) -> float:
    """What a longer commitment buys the customer, as a share off the fee.

    Matters more here than in a metered model: a flat banded fee has no meter to
    grow into, so the multi-year term is the only structural defence against an
    annual renegotiation of the whole number.
    """
    params = params or load_parameters()
    years = max(1, min(int(term_years), max(1, params.max_term_years)))
    return max(0.0, params.multi_year_discount_per_extra_year) * (years - 1)


def cadence_adjustment(cadence: PaymentCadence,
                       params: Optional[MonetizationParameters] = None) -> float:
    """Signed: negative reduces the fee, positive increases it."""
    params = params or load_parameters()
    _, kind = CADENCE_TERMS[cadence]
    if kind == "discount":
        return -abs(params.upfront_payment_discount)
    if kind == "premium":
        return abs(params.quarterly_payment_premium)
    return 0.0


@dataclass(frozen=True)
class DiscountVerdict:
    """Whether a proposed discount may be given, and what stopped it."""

    requested: float
    allowed: float
    approved: bool
    net_annual_fee: Decimal
    cost_floor: Decimal
    refusal: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        return {"requested": self.requested, "allowed": self.allowed,
                "approved": self.approved,
                "net_annual_fee": str(self.net_annual_fee),
                "cost_floor": str(self.cost_floor), "refusal": self.refusal}


def approve_discount(annual_fee: Decimal, requested: float,
                     profile: CustomerProfile,
                     params: Optional[MonetizationParameters] = None
                     ) -> DiscountVerdict:
    """May this discount be given? Two limits, and the second is absolute.

    ``max_discount_share`` is governance — a number PIE chose and can change.
    The cost floor is not: below it the account consumes more than it pays,
    and a concession past that point is a subsidy the model has already refused
    once when it declined to price the bottom band. Reported as a *refusal with
    the binding reason named*, never as a silently clamped number, because a
    discount quietly reduced to something affordable is one nobody learns from.
    """
    params = params or load_parameters()
    floor = floor_price(profile, params)
    requested = max(0.0, requested)
    policy_cap = max(0.0, params.max_discount_share)

    allowed = min(requested, policy_cap)
    net = money(max(_ZERO, annual_fee) * (Decimal("1") - Decimal(str(allowed))))

    refusal = None
    if requested > policy_cap:
        refusal = (f"Requested {requested:.0%} exceeds the {policy_cap:.0%} "
                   "discount policy; capped.")
    if net < floor:
        # The floor binds. Recompute the largest discount that clears it, and
        # say so — a deal that cannot be done at any allowed discount is a
        # qualification failure, not a pricing negotiation.
        headroom = (float(1 - floor / annual_fee) if annual_fee > 0 else 0.0)
        allowed = max(0.0, min(allowed, headroom))
        net = money(max(_ZERO, annual_fee) * (Decimal("1") - Decimal(str(allowed))))
        refusal = (f"Discount limited to {allowed:.1%}: below that the fee "
                   f"falls under the {floor} cost to serve, which is a subsidy "
                   "rather than a concession.")

    return DiscountVerdict(requested=requested, allowed=allowed,
                           approved=refusal is None, net_annual_fee=net,
                           cost_floor=floor, refusal=refusal)


def fee_schedule(annual_fee: Decimal, term_years: int,
                 params: Optional[MonetizationParameters] = None
                 ) -> list[dict[str, str]]:
    """The fee for each year of the term, with escalation applied.

    Escalation runs *within* a term and within a band. It exists because the
    band re-rates roughly every 3.6 years, and a price held flat in nominal
    terms across those years is a real-terms cut nobody decided to give.
    """
    params = params or load_parameters()
    years = max(1, min(int(term_years), max(1, params.max_term_years)))
    rate = Decimal(str(max(0.0, params.annual_escalation)))
    return [{"year": str(y + 1),
             "annual_fee": str(money(annual_fee * (Decimal("1") + rate) ** y))}
            for y in range(years)]


def downgrade_floor(current_band_index: int, observed_band_index: int,
                    params: Optional[MonetizationParameters] = None) -> int:
    """The lowest band a customer may fall to at one renewal.

    Bands re-rate upward with no limit — a customer who grew two bands pays for
    two bands. Downward movement is rate-limited instead, and the asymmetry is
    deliberate rather than opportunistic: an upward move is the customer's own
    growth arriving, while a single bad year is usually noise, and a fee that
    tracked every dip would be renegotiated in both directions forever. A
    customer whose decline is real reaches the right band in consecutive
    renewals; one whose bad year was noise never has to argue it back up.
    """
    params = params or load_parameters()
    limit = max(0, params.downgrade_bands_per_renewal)
    return max(observed_band_index, current_band_index - limit)


@dataclass(frozen=True)
class GroupPricing:
    """How a group of legal entities is banded and charged."""

    entity_turnovers: tuple[Decimal, ...]
    combined_turnover: Decimal
    fee_if_priced_separately: Decimal
    fee_on_combined_band: Decimal
    implicit_discount: Optional[float]
    entity_uplift: Decimal
    recommended_fee: Decimal
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_turnovers": [str(t) for t in self.entity_turnovers],
            "combined_turnover": str(self.combined_turnover),
            "fee_if_priced_separately": str(self.fee_if_priced_separately),
            "fee_on_combined_band": str(self.fee_on_combined_band),
            "implicit_discount": self.implicit_discount,
            "entity_uplift": str(self.entity_uplift),
            "recommended_fee": str(self.recommended_fee),
            "note": self.note,
        }


def price_group(entity_turnovers: list[Decimal], fee_for_turnover,
                params: Optional[MonetizationParameters] = None) -> GroupPricing:
    """Band a multi-entity group on combined turnover, plus a per-entity uplift.

    ``fee_for_turnover`` is passed in rather than imported so this module never
    reaches back into the band table and create a second path to a price;
    ``report`` owns that lookup and hands it down.

    Summing turnover into one band is right commercially — one relationship, one
    contract, one negotiation — and it quietly hands the group a large discount,
    because the bands are steep at the bottom and three small entities each pay a
    small-band premium separately. Measured on a realistic three-entity shape
    that discount is around 26%. Meanwhile the cost to serve genuinely rises per
    entity: a second sync, a second catalogue, a second support surface. The
    uplift reconciles the two, and is stated rather than buried so a customer can
    be told exactly what they are paying for.
    """
    params = params or load_parameters()
    turnovers = tuple(t for t in entity_turnovers if t > 0)
    if not turnovers:
        return GroupPricing(
            entity_turnovers=(), combined_turnover=_ZERO,
            fee_if_priced_separately=_ZERO, fee_on_combined_band=_ZERO,
            implicit_discount=None, entity_uplift=_ZERO, recommended_fee=_ZERO,
            note="No entity has any turnover; there is nothing to band.")

    combined = money(sum(turnovers, _ZERO))
    separate = money(sum((fee_for_turnover(t) for t in turnovers), _ZERO))
    combined_fee = money(fee_for_turnover(combined))
    implicit = (round(float(1 - combined_fee / separate), 4)
                if separate > 0 else None)

    extra = max(0, len(turnovers) - 1)
    uplift = money(combined_fee * Decimal(str(max(0.0, params.group_entity_uplift)))
                   * Decimal(extra))
    recommended = money(combined_fee + uplift)

    return GroupPricing(
        entity_turnovers=turnovers, combined_turnover=combined,
        fee_if_priced_separately=separate, fee_on_combined_band=combined_fee,
        implicit_discount=implicit, entity_uplift=uplift,
        recommended_fee=recommended,
        note=(f"{len(turnovers)} entities banded on combined turnover, plus "
              f"{params.group_entity_uplift:.0%} for each of the {extra} "
              "additional connected companies. Summing alone would have "
              f"discounted the group by {(implicit or 0) * 100:.1f}% against "
              "separate pricing, on a cost to serve that rises with every "
              "connection."))


@dataclass(frozen=True)
class Contract:
    """One signable agreement: the fee, the terms, and the cash it produces."""

    list_annual_fee: Decimal
    net_annual_fee: Decimal
    term_years: int
    cadence: PaymentCadence
    implementation_fee: Decimal
    term_discount: float
    cadence_adjustment: float
    discount: DiscountVerdict
    schedule: list[dict[str, str]]
    total_contract_value: Decimal
    cash_at_signature: Decimal
    first_year_cash: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "list_annual_fee": str(self.list_annual_fee),
            "net_annual_fee": str(self.net_annual_fee),
            "term_years": self.term_years, "cadence": self.cadence.value,
            "implementation_fee": str(self.implementation_fee),
            "term_discount": self.term_discount,
            "cadence_adjustment": self.cadence_adjustment,
            "discount": self.discount.as_dict(),
            "schedule": list(self.schedule),
            "total_contract_value": str(self.total_contract_value),
            "cash_at_signature": str(self.cash_at_signature),
            "first_year_cash": str(self.first_year_cash),
        }


def quote_contract(annual_fee: Decimal, profile: CustomerProfile,
                   params: Optional[MonetizationParameters] = None, *,
                   term_years: Optional[int] = None,
                   cadence: PaymentCadence = PaymentCadence.ANNUAL_UPFRONT,
                   discount: float = 0.0) -> Contract:
    """Assemble a signable contract from a fee and a set of terms.

    Order of operations matters and is fixed here so two quotes are comparable:
    term and cadence adjust the list fee, the negotiated discount applies to
    that, the cost floor governs the result, and implementation is computed on
    the *net* fee — a discounted deal should not carry an implementation charge
    sized for a deal that was never signed.
    """
    params = params or load_parameters()
    term = max(1, min(int(term_years or params.default_term_years),
                      max(1, params.max_term_years)))

    adjusted = money(max(_ZERO, annual_fee)
                     * (Decimal("1") - Decimal(str(term_discount(term, params))))
                     * (Decimal("1") + Decimal(str(cadence_adjustment(cadence, params)))))
    verdict = approve_discount(adjusted, discount, profile, params)
    net = verdict.net_annual_fee

    impl = implementation_fee(net, profile, params)
    schedule = fee_schedule(net, term, params)
    tcv = money(sum((Decimal(row["annual_fee"]) for row in schedule), _ZERO) + impl)

    instalments, _ = CADENCE_TERMS[cadence]
    first_instalment = money(net / Decimal(instalments))
    return Contract(
        list_annual_fee=money(max(_ZERO, annual_fee)), net_annual_fee=net,
        term_years=term, cadence=cadence, implementation_fee=impl,
        term_discount=term_discount(term, params),
        cadence_adjustment=cadence_adjustment(cadence, params),
        discount=verdict, schedule=schedule, total_contract_value=tcv,
        cash_at_signature=money(impl + first_instalment),
        first_year_cash=money(impl + net))
