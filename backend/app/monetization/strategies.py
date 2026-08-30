"""Pricing strategies — one interface, eleven implementations, no literals.

Every way PIE could charge is a ``PricingStrategy``. Each one answers the same
question — *given this customer's waterfall, what is the annual fee* — and each
carries the operands it used, by name, in ``Fee.basis``. That last part is what
makes a comparison auditable rather than a table of numbers: two strategies
that produce ₹18,00,000 for very different reasons are not the same offer, and
the basis says which is which.

Three design rules, and the reasons are the ones this codebase already holds:

**A strategy owns its metric and nothing else.** It does not decide whether the
fee is acceptable, whether ROI clears a threshold, or whether the customer will
sign. ``evaluate`` does that. A strategy that also judged itself would be the
"extracts and decides" split CLAUDE.md warns about, and the judgement would end
up implemented once per strategy.

**Bounds are applied by the base class, never by a subclass.** A minimum
commitment, a cap and a floor are the same mechanism wherever they appear, so
``quote`` is the template: it calls ``_components``, applies the bounds, and
records in ``basis`` when a bound bound. A subclass that clamped its own number
would be an LSP break — the caps would compose differently per strategy and the
comparison would silently stop being like-for-like.

**Rounding is not applied here.** ``Fee.annual_fee`` is exact so that a 0.01%
ladder step is still distinguishable from a 0.025% one; ``round_to`` produces
the number you would actually put on a contract. Rounding inside the strategy
would make the whole low end of the hypothesis sweep collapse to zero and read
as a finding.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from .config import MonetizationParameters, load_parameters
from .customer import Waterfall, money

_ZERO = Decimal("0")


class FeeBase(str, Enum):
    """What a percentage-of-something fee is a percentage *of*.

    The distinction the brief calls "radically different" is here, as two
    members rather than as a comment: ``TOTAL_GROSS_MARGIN`` is the customer's
    whole gross profit, ``INCREMENTAL_GROSS_MARGIN`` is only the part PIE
    created. At a 60% adoption share and a base-case uplift the two differ by
    roughly an order of magnitude, so "0.1% of margin" is not one price.
    """

    PIE_TOUCHED_GMV = "PIE_TOUCHED_GMV"
    TOTAL_GMV = "TOTAL_GMV"
    PIE_TOUCHED_GROSS_MARGIN = "PIE_TOUCHED_GROSS_MARGIN"
    TOTAL_GROSS_MARGIN = "TOTAL_GROSS_MARGIN"
    INCREMENTAL_GROSS_MARGIN = "INCREMENTAL_GROSS_MARGIN"
    TOTAL_ECONOMIC_VALUE = "TOTAL_ECONOMIC_VALUE"
    SAVINGS = "SAVINGS"


def base_amount(wf: Waterfall, base: FeeBase) -> Decimal:
    """The rupee amount a rate is applied to. One definition per base, here.

    ``SAVINGS`` is the savings-share base and is deliberately narrow: buy-side
    procurement savings plus the margin the floors held, and nothing from
    conversion. A savings-share deal that quietly included won revenue would be
    an incremental-margin deal wearing a friendlier word.
    """
    if base is FeeBase.PIE_TOUCHED_GMV:
        return wf.pie_touched_gmv
    if base is FeeBase.TOTAL_GMV:
        return wf.with_pie.revenue
    if base is FeeBase.PIE_TOUCHED_GROSS_MARGIN:
        return wf.pie_touched_gross_margin
    if base is FeeBase.TOTAL_GROSS_MARGIN:
        return wf.total_gross_margin
    if base is FeeBase.INCREMENTAL_GROSS_MARGIN:
        return wf.incremental_gross_profit
    if base is FeeBase.TOTAL_ECONOMIC_VALUE:
        return wf.total_economic_value
    if base is FeeBase.SAVINGS:
        return money(wf.procurement_savings + wf.gp_from_margin)
    raise ValueError(f"unhandled fee base: {base}")  # pragma: no cover


@dataclass(frozen=True)
class Fee:
    """One strategy's answer for one customer, with its operands attached."""

    strategy: str
    label: str
    metric: str
    annual_fee: Decimal
    fixed_component: Decimal
    variable_component: Decimal
    basis: dict[str, str] = field(default_factory=dict)
    #: Set when a floor, cap or minimum commitment changed the answer. A bound
    #: that bound is the single most important thing about a quoted fee and it
    #: must never be invisible in the output.
    bound_applied: Optional[str] = None

    def round_to(self, params: Optional[MonetizationParameters] = None) -> Decimal:
        return (params or load_parameters()).round_fee(self.annual_fee)

    def as_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "label": self.label,
                "metric": self.metric, "annual_fee": str(self.annual_fee),
                "fixed_component": str(self.fixed_component),
                "variable_component": str(self.variable_component),
                "basis": dict(self.basis), "bound_applied": self.bound_applied}


@dataclass(frozen=True, kw_only=True)
class PricingStrategy(ABC):
    """The one interface. Subclasses supply ``_components`` and nothing else.

    ``kw_only`` throughout so a subclass can add a required field after the
    base's defaulted bounds, and so every construction names what it is setting
    — a strategy built positionally as ``(0.001, None, 500000)`` is unreadable
    at exactly the moment somebody is checking whether the cap is right.
    """

    key: str
    label: str
    metric: str
    #: A contractual minimum the customer commits to regardless of usage.
    minimum_fee: Optional[Decimal] = None
    #: A ceiling. Present on every usage-linked model that PIE would actually
    #: sign: an uncapped share of a growing business is how a vendor gets
    #: renegotiated out in year three.
    maximum_fee: Optional[Decimal] = None

    @abstractmethod
    def _components(self, wf: Waterfall,
                    params: MonetizationParameters
                    ) -> tuple[Decimal, Decimal, dict[str, str]]:
        """``(fixed, variable, basis)`` before any bound is applied."""

    def quote(self, wf: Waterfall,
              params: Optional[MonetizationParameters] = None) -> Fee:
        params = params or load_parameters()
        fixed, variable, basis = self._components(wf, params)
        fixed, variable = money(max(_ZERO, fixed)), money(max(_ZERO, variable))
        raw = money(fixed + variable)
        total, bound = raw, None

        # Cap first, then floor. The order is not arbitrary: a floor above a cap
        # is a contradiction somebody will eventually configure, and applying
        # the floor last means the customer is charged the number they were
        # promised as a minimum rather than the one they were promised as a
        # maximum. The basis records both so the contradiction is visible.
        if self.maximum_fee is not None and total > self.maximum_fee:
            total, bound = money(self.maximum_fee), "cap"
        if self.minimum_fee is not None and total < self.minimum_fee:
            total, bound = money(self.minimum_fee), (
                "floor over cap (check the contract)" if bound == "cap" else "minimum")

        if bound is not None:
            basis = {**basis, "uncapped_fee": str(raw)}
        # The split is rescaled so fixed + variable always reconciles to the fee
        # actually charged. A bounded fee whose components still sum to the
        # unbounded number is the kind of small lie that makes a whole model
        # untrustworthy the first time somebody adds the columns up.
        if total != raw:
            if raw > _ZERO:
                ratio = total / raw
                fixed, variable = money(fixed * ratio), money(total - money(fixed * ratio))
            else:
                fixed, variable = total, _ZERO

        return Fee(strategy=self.key, label=self.label, metric=self.metric,
                   annual_fee=total, fixed_component=fixed,
                   variable_component=variable, basis=basis, bound_applied=bound)


# ── 1. Fixed subscription ───────────────────────────────────────────────────
@dataclass(frozen=True, kw_only=True)
class SubscriptionPricing(PricingStrategy):
    """A flat annual fee. The number is an input, not a derivation."""

    annual_fee: Decimal
    key: str = "subscription"
    label: str = "Fixed annual subscription"
    metric: str = "flat"

    def _components(self, wf, params):
        return self.annual_fee, _ZERO, {"annual_fee": str(self.annual_fee)}


@dataclass(frozen=True, kw_only=True)
class ValueDerivedSubscription(PricingStrategy):
    """A flat fee *derived* from value: capture rate x economic value created.

    This is §7's question and it is a different object from ``SubscriptionPricing``
    even though both bill a flat annual sum. The distinction is where the number
    comes from — one is picked, one is computed — and keeping them apart is what
    lets the model answer "what should the subscription be" rather than only
    "what does this subscription do".

    The fee is fixed *for the term*: it is set from the value observed at
    signing and does not move with the customer's month-to-month flow, which is
    the whole predictability argument for a subscription.
    """

    capture_rate: float
    base: FeeBase = FeeBase.TOTAL_ECONOMIC_VALUE
    key: str = "value_subscription"
    label: str = "Value-derived subscription"
    metric: str = "flat, sized from value"

    def _components(self, wf, params):
        amount = base_amount(wf, self.base)
        fee = money(amount * Decimal(str(self.capture_rate)))
        return fee, _ZERO, {"base": self.base.value, "base_amount": str(amount),
                            "capture_rate": str(self.capture_rate)}


@dataclass(frozen=True, kw_only=True)
class EnterpriseLicensePricing(PricingStrategy):
    """An unlimited-usage licence, priced off value with a volume discount.

    Modelled as its own strategy rather than as a large subscription because
    the discount is the product: what an enterprise buys is the *absence* of a
    meter, and it pays a premium over the value-derived fee for that certainty
    while receiving a discount against what usage pricing would have billed.
    """

    capture_rate: float
    #: What the buyer saves versus the metered equivalent, as a ratio.
    unlimited_discount: float = 0.20
    base: FeeBase = FeeBase.TOTAL_ECONOMIC_VALUE
    key: str = "enterprise_license"
    label: str = "Enterprise licence, unlimited usage"
    metric: str = "flat, negotiated"

    def _components(self, wf, params):
        amount = base_amount(wf, self.base)
        gross = amount * Decimal(str(self.capture_rate))
        fee = money(gross * (Decimal("1") - Decimal(str(self.unlimited_discount))))
        return fee, _ZERO, {"base": self.base.value, "base_amount": str(amount),
                            "capture_rate": str(self.capture_rate),
                            "unlimited_discount": str(self.unlimited_discount)}


# ── 2. Usage metrics ────────────────────────────────────────────────────────
@dataclass(frozen=True, kw_only=True)
class RFQPricing(PricingStrategy):
    """Per enquiry routed through PIE.

    The incentive this creates is the reason it is in the comparison and not in
    the recommendation: it charges for the one behaviour the platform most needs
    — putting every RFQ in — and a customer optimising its bill will route only
    the enquiries it already expects to win, which is precisely the set PIE adds
    least to.
    """

    rate: Decimal
    key: str = "per_rfq"
    label: str = "Per RFQ processed"
    metric: str = "RFQs through PIE"

    def _components(self, wf, params):
        volume = wf.covered_with_pie.rfqs
        return _ZERO, money(volume * self.rate), {
            "rfqs_through_pie": str(money(volume)), "rate_per_rfq": str(self.rate)}


@dataclass(frozen=True, kw_only=True)
class QuotePricing(PricingStrategy):
    """Per quote produced. One step further down the funnel than per-RFQ."""

    rate: Decimal
    key: str = "per_quote"
    label: str = "Per quote produced"
    metric: str = "quotes through PIE"

    def _components(self, wf, params):
        volume = wf.covered_with_pie.quotes
        return _ZERO, money(volume * self.rate), {
            "quotes_through_pie": str(money(volume)), "rate_per_quote": str(self.rate)}


@dataclass(frozen=True, kw_only=True)
class MatchPricing(PricingStrategy):
    """Per successful match — an enquiry line resolved to a sellable product.

    ``match_rate`` is what share of quoted lines count as a billable resolution.
    It defaults to 1.0 because a quote that went out *is* a resolved match; a
    deployment that wanted to bill only equivalence hits would set it lower.
    """

    rate: Decimal
    match_rate: float = 1.0
    key: str = "per_match"
    label: str = "Per successful match"
    metric: str = "resolved matches"

    def _components(self, wf, params):
        volume = wf.covered_with_pie.quotes * Decimal(str(self.match_rate))
        return _ZERO, money(volume * self.rate), {
            "matches": str(money(volume)), "rate_per_match": str(self.rate),
            "match_rate": str(self.match_rate)}


@dataclass(frozen=True, kw_only=True)
class OrderPricing(PricingStrategy):
    """Per order won on a PIE-touched enquiry. Charges only on success."""

    rate: Decimal
    key: str = "per_order"
    label: str = "Per order"
    metric: str = "orders from PIE-touched RFQs"

    def _components(self, wf, params):
        volume = wf.covered_with_pie.orders
        return _ZERO, money(volume * self.rate), {
            "orders": str(money(volume)), "rate_per_order": str(self.rate)}


# ── 3. Percentage-of-something ──────────────────────────────────────────────
@dataclass(frozen=True, kw_only=True)
class TransactionPricing(PricingStrategy):
    """A share of transaction value. The classic marketplace take rate."""

    rate: float
    base: FeeBase = FeeBase.PIE_TOUCHED_GMV
    key: str = "transaction"
    label: str = "% of transaction value (GMV)"
    metric: str = "GMV"

    def _components(self, wf, params):
        amount = base_amount(wf, self.base)
        return _ZERO, money(amount * Decimal(str(self.rate))), {
            "base": self.base.value, "base_amount": str(amount),
            "rate": str(self.rate)}


@dataclass(frozen=True, kw_only=True)
class MarginSharePricing(PricingStrategy):
    """A share of gross margin. **This is the 0.1% hypothesis's family.**

    ``base`` decides which of the two readings is being priced, and the model
    runs both. Nothing here defaults to a rate: the hypothesis lives in
    ``config.hypothesis_margin_rate`` so that no module has to spell 0.001 as a
    literal in order to test it.
    """

    rate: float
    base: FeeBase = FeeBase.PIE_TOUCHED_GROSS_MARGIN
    key: str = "margin_share"
    label: str = "% of gross margin"
    metric: str = "gross margin"

    def _components(self, wf, params):
        amount = base_amount(wf, self.base)
        return _ZERO, money(amount * Decimal(str(self.rate))), {
            "base": self.base.value, "base_amount": str(amount),
            "rate": str(self.rate)}


@dataclass(frozen=True, kw_only=True)
class IncrementalMarginPricing(PricingStrategy):
    """A share of the gross margin PIE created. Requires a counterfactual.

    A separate class from ``MarginSharePricing`` with a different base, rather
    than a parameter on it, because the *measurement obligation* is different in
    kind: this one is unbillable without a baseline, and a strategy that can be
    unbillable should be impossible to select by accident.
    """

    rate: float
    key: str = "incremental_margin"
    label: str = "% of incremental gross margin"
    metric: str = "incremental gross margin"
    base: FeeBase = FeeBase.INCREMENTAL_GROSS_MARGIN

    def _components(self, wf, params):
        amount = base_amount(wf, self.base)
        return _ZERO, money(amount * Decimal(str(self.rate))), {
            "base": self.base.value, "base_amount": str(amount),
            "rate": str(self.rate),
            "requires": "a pre-PIE baseline captured before go-live"}


@dataclass(frozen=True, kw_only=True)
class SavingsSharePricing(PricingStrategy):
    """A share of demonstrated savings: procurement plus margin held.

    Narrower than incremental margin on purpose — no won revenue in the base.
    It is the easiest value claim to defend line by line and the smallest, which
    is the trade the gainshare literature always makes.
    """

    rate: float
    key: str = "savings_share"
    label: str = "Savings share"
    metric: str = "measured savings"
    base: FeeBase = FeeBase.SAVINGS

    def _components(self, wf, params):
        amount = base_amount(wf, self.base)
        return _ZERO, money(amount * Decimal(str(self.rate))), {
            "base": self.base.value, "base_amount": str(amount),
            "rate": str(self.rate)}


# ── 4. Hybrids ──────────────────────────────────────────────────────────────
@dataclass(frozen=True, kw_only=True)
class HybridPricing(PricingStrategy):
    """A platform fee plus a usage or performance component.

    Composition rather than a fourth copy of the percentage arithmetic: the
    variable half is *any* other strategy, so Hybrid A (platform + transaction),
    B (platform + margin), C (low platform + performance) and D (minimum
    commitment + usage) are four constructions of one class rather than four
    classes. Hybrid E is ``EnterpriseLicensePricing``, which is genuinely a
    different object because it has no variable half at all.

    The minimum-commitment shape (D) is this class with ``minimum_fee`` set and
    a small ``platform_fee``: the commitment is a floor on the total, which is
    what a minimum commitment actually is, rather than a second fixed fee.
    """

    platform_fee: Decimal
    component: PricingStrategy
    key: str = "hybrid"
    label: str = "Hybrid"
    metric: str = "flat + usage"

    def _components(self, wf, params):
        inner = self.component.quote(wf, params)
        return self.platform_fee, inner.annual_fee, {
            "platform_fee": str(self.platform_fee),
            "component": inner.strategy,
            "component_metric": inner.metric,
            "component_fee": str(inner.annual_fee),
            **{f"component.{k}": v for k, v in inner.basis.items()}}
