"""Judging a fee: what the customer keeps, what PIE captures, and whether it flies.

A strategy computes a fee. This module decides what that fee *means* — the
split of created value, the customer's return, how fast it pays back, and
whether it clears the thresholds PIE has set itself. Keeping the two apart is
the SRP line in §4 of the pricing brief and the practical reason is that the
judgement is one behaviour: implemented per strategy it would be implemented
eleven times and would disagree with itself by the third.

**The value multiple is imported, not reimplemented.** ``attribution.calculator.roi``
already answers "value per rupee of platform cost" — it is the number the
renewal screen shows a customer — and a second definition here would be the
semantic duplication CLAUDE.md §2 names. The one thing this module adds is the
*net* form the brief asks for, ``(value - fee) / fee``, which is that multiple
minus one and is derived from it rather than computed alongside it.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from ..attribution.calculator import roi as value_multiple
from .config import MonetizationParameters, load_parameters
from .customer import Waterfall, money
from .strategies import Fee, PricingStrategy

_ZERO = Decimal("0")


def fee_for_roi(economic_value: Decimal, target_roi: float) -> Optional[Decimal]:
    """The largest fee that still leaves the customer ``target_roi`` net return.

    Inverts ``(value - fee) / fee = roi`` to ``fee = value / (1 + roi)``. Returns
    ``None`` rather than zero when there is no value to share: a fee sized
    against nothing is not ₹0, it is a deal that should not be signed, and the
    caller has to see the difference.
    """
    if economic_value <= _ZERO or target_roi < 0:
        return None
    return money(economic_value / (Decimal("1") + Decimal(str(target_roi))))


def _pct(numerator: Decimal, denominator: Decimal) -> Optional[float]:
    """A ratio, or UNKNOWN when the denominator is not a real base."""
    if denominator <= _ZERO:
        return None
    return float(numerator / denominator)


@dataclass(frozen=True)
class Evaluation:
    """One (customer, strategy) pair, fully judged.

    Every ratio is ``Optional`` and every ``None`` means UNKNOWN rather than
    zero — a customer for whom the model produced no measurable value has no
    ROI, and rendering that as ``0x`` would read as "measured, and bad" instead
    of "not measurable from these inputs".
    """

    customer: str
    fee: Fee
    economic_value: Decimal
    incremental_gross_profit: Decimal

    value_multiple: Optional[Decimal]        # value / fee  (2.5 = 2.5x)
    customer_roi: Optional[float]            # (value - fee) / fee
    retained_value: Decimal
    value_capture_pct: Optional[float]       # fee / value
    payback_months: Optional[float]

    fee_pct_of_incremental_value: Optional[float]
    fee_pct_of_gross_profit: Optional[float]
    fee_pct_of_gmv: Optional[float]

    revenue_per_rfq: Optional[Decimal]
    revenue_per_order: Optional[Decimal]

    clears_min_roi: bool
    clears_payback: bool
    thresholds_cleared: dict[str, bool]
    refusal: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "customer": self.customer, "fee": self.fee.as_dict(),
            "economic_value": str(self.economic_value),
            "incremental_gross_profit": str(self.incremental_gross_profit),
            "value_multiple": (None if self.value_multiple is None
                               else str(self.value_multiple)),
            "customer_roi": self.customer_roi,
            "retained_value": str(self.retained_value),
            "value_capture_pct": self.value_capture_pct,
            "payback_months": self.payback_months,
            "fee_pct_of_incremental_value": self.fee_pct_of_incremental_value,
            "fee_pct_of_gross_profit": self.fee_pct_of_gross_profit,
            "fee_pct_of_gmv": self.fee_pct_of_gmv,
            "revenue_per_rfq": (None if self.revenue_per_rfq is None
                                else str(self.revenue_per_rfq)),
            "revenue_per_order": (None if self.revenue_per_order is None
                                  else str(self.revenue_per_order)),
            "clears_min_roi": self.clears_min_roi,
            "clears_payback": self.clears_payback,
            "thresholds_cleared": dict(self.thresholds_cleared),
            "refusal": self.refusal,
        }


def evaluate(wf: Waterfall, fee: Fee,
             params: Optional[MonetizationParameters] = None) -> Evaluation:
    """Judge one fee against one customer's waterfall."""
    params = params or load_parameters()
    value = wf.total_economic_value
    amount = fee.annual_fee
    retained = money(value - amount)

    multiple = value_multiple(value, amount)
    net_roi = (None if multiple is None
               else float(multiple - Decimal("1")))
    capture = _pct(amount, value)

    # Payback in months against the value the platform produces per month. A
    # fee larger than a year of value has a payback beyond twelve months and the
    # model says so rather than capping it — a 40-month payback is a finding.
    payback = None
    if value > _ZERO and amount > _ZERO:
        payback = float(amount / (value / Decimal("12")))
    elif amount <= _ZERO:
        payback = 0.0

    rfqs = wf.covered_with_pie.rfqs
    orders = wf.covered_with_pie.orders

    refusal = None
    clears_roi = net_roi is not None and net_roi >= params.min_customer_roi
    clears_payback = payback is not None and payback <= params.max_payback_months
    if multiple is None:
        refusal = ("UNKNOWN: no economic value is measurable from these inputs, "
                   "or the fee is zero. Neither is a number to price against.")
    elif not clears_roi:
        refusal = (f"Customer net ROI {net_roi:.2f}x is below the "
                   f"{params.min_customer_roi:.1f}x PIE requires of itself.")
    elif not clears_payback:
        refusal = (f"Payback {payback:.1f} months exceeds the "
                   f"{params.max_payback_months:.0f}-month design limit.")

    return Evaluation(
        customer=wf.profile.name, fee=fee, economic_value=value,
        incremental_gross_profit=wf.incremental_gross_profit,
        value_multiple=multiple, customer_roi=net_roi, retained_value=retained,
        value_capture_pct=capture, payback_months=payback,
        fee_pct_of_incremental_value=_pct(amount, wf.incremental_gross_profit),
        fee_pct_of_gross_profit=_pct(amount, wf.total_gross_margin),
        fee_pct_of_gmv=_pct(amount, wf.with_pie.revenue),
        revenue_per_rfq=(money(amount / rfqs) if rfqs > 0 else None),
        revenue_per_order=(money(amount / orders) if orders > 0 else None),
        clears_min_roi=clears_roi, clears_payback=clears_payback,
        thresholds_cleared={
            f"{t:g}x": (net_roi is not None and net_roi >= t)
            for t in params.roi_thresholds},
        refusal=refusal)


def compare(wf: Waterfall, strategies: list[PricingStrategy],
            params: Optional[MonetizationParameters] = None) -> list[Evaluation]:
    """Every strategy against one customer, in the order given.

    Order is preserved rather than sorted by fee. A comparison sorted by price
    reads as a ranking, and the whole finding of this exercise is that the
    largest fee is not the best model.
    """
    params = params or load_parameters()
    return [evaluate(wf, s.quote(wf, params), params) for s in strategies]
