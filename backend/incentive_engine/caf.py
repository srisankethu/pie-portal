"""CAF — the one payable currency.

    CAF_i = q_i x (P_i - F_i) - K_i - 0.5 x Toolkit_i + Y_i

Every term is a price or a declared amount. Cost appears nowhere, which is what
lets the same number be shown to a salesperson and used in a payout.

**Why maximising CAF maximises gross profit.** For fixed F,

    CAF_i = q(P - C) - q.C.m_floor = GP_i - (a constant per unit)

so dCAF/dP = dGP/dP, and dCAF/dq > 0 exactly when P > F, which implies P > C.
CAF is ordinally equivalent to gross profit on price and strictly more
conservative on quantity. Nothing is lost by hiding cost.

**Why the coefficients are what they are.** K at 1:1 makes S's first-order
condition identical to the owner's — S sets the third-party incentive exactly
where marginal win-probability equals marginal contribution given up. Toolkit
at 0.5 prices the compliant weapon at half the non-compliant one, so S reaches
for it first because it is cheaper rather than because they were told to. Y at
1:1 makes S indifferent to whether a rupee came from holding price or from
squeezing the vendor — the same indifference the owner has, which is the
definition of incentive compatibility here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from .config import Config
from .models import InvoiceLine, ThirdPartyIncentive, ToolkitSpend, VendorYield

_ZERO = Decimal("0")
_PAISE = Decimal("0.01")


@dataclass(frozen=True)
class LineCAF:
    """One line's contribution, with every term kept separately for audit."""

    line: InvoiceLine
    price_contribution: Decimal
    third_party: Decimal
    toolkit_charged: Decimal
    vendor_yield: Decimal
    caf: Decimal
    #: Aged lines are computed but routed to the recovery pool, never into CAF.
    #: Below some age the aged floor sits under cost, so a cash-losing line
    #: would produce positive CAF and corrupt the margin signal S is learning.
    is_aged: bool = False

    def explain(self) -> dict:
        """The audit row. Money at a fixed two places throughout.

        Reconciling an audit trail where the same rupee prints as 37000 on one
        row and 37000.00 on the next is work nobody should have to do, so every
        money field is quantized here rather than wherever it happened to be
        computed.
        """
        def m(v: Decimal) -> str:
            return str(v.quantize(_PAISE))

        return {
            "invoice_id": self.line.invoice_id,
            "item_id": self.line.item_id,
            "qty": str(self.line.qty),
            "price": m(self.line.unit_price_net),
            "floor": m(self.line.floor_price),
            "price_contribution": m(self.price_contribution),
            "third_party_K": m(self.third_party),
            "toolkit_charged": m(self.toolkit_charged),
            "vendor_yield_Y": m(self.vendor_yield),
            "caf": m(self.caf),
            "is_aged": self.is_aged,
        }


def price_contribution(line: InvoiceLine) -> Decimal:
    """q x (P - F). Negative below the floor, and reported as such.

    A line sold under the floor takes points away. That is the entire price
    discipline: there is no "just this once", because the arithmetic charges
    for it whether or not anyone was watching.
    """
    return line.qty * (line.unit_price_net - line.floor_price)


def line_caf(cfg: Config, line: InvoiceLine, *,
             third_party: Decimal = _ZERO,
             toolkit: Decimal = _ZERO,
             vendor_yield: Decimal = _ZERO) -> LineCAF:
    k_rate = cfg.dec("caf", "third_party_charge_rate")
    t_rate = cfg.dec("caf", "toolkit_charge_rate")
    y_rate = cfg.dec("caf", "vendor_yield_credit")

    contribution = price_contribution(line)
    k_charge = third_party * k_rate
    t_charge = toolkit * t_rate
    y_credit = vendor_yield * y_rate
    return LineCAF(
        line=line,
        price_contribution=contribution,
        third_party=k_charge,
        toolkit_charged=t_charge,
        vendor_yield=y_credit,
        caf=contribution - k_charge - t_charge + y_credit,
        is_aged=line.is_aged_stock,
    )


def _by_invoice(items, key: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for x in items:
        ref = getattr(x, key, None)
        if ref:
            out[ref] = out.get(ref, _ZERO) + x.amount
    return out


def compute(cfg: Config, lines: Sequence[InvoiceLine], *,
            incentives: Iterable[ThirdPartyIncentive] = (),
            toolkit: Iterable[ToolkitSpend] = (),
            yields: Iterable[VendorYield] = ()) -> list[LineCAF]:
    """CAF for every line, with deal-level terms spread across the lines.

    K, Toolkit and Y attach to a *deal*, not to a line. They are apportioned by
    each line's share of the invoice's price contribution rather than by value,
    because apportioning by value would let a salesperson park the whole
    kickback on the one line that happens to be furthest above the floor and
    leave the rest untouched. Where an invoice has no positive contribution to
    apportion against, the charge falls on the first line rather than
    disappearing.
    """
    k_by_invoice = _by_invoice(incentives, "invoice_id")
    y_by_invoice = _by_invoice(yields, "invoice_id")
    # Toolkit is customer-level, not invoice-level.
    t_by_customer: dict[str, Decimal] = {}
    for t in toolkit:
        t_by_customer[t.customer_id] = t_by_customer.get(t.customer_id, _ZERO) + t.amount

    grouped: dict[str, list[InvoiceLine]] = {}
    for line in lines:
        grouped.setdefault(line.invoice_id, []).append(line)

    seen_customers: set[str] = set()
    out: list[LineCAF] = []
    for invoice_id, group in grouped.items():
        contributions = [price_contribution(x) for x in group]
        positive_total = sum((c for c in contributions if c > _ZERO), _ZERO)
        k_total = k_by_invoice.get(invoice_id, _ZERO)
        y_total = y_by_invoice.get(invoice_id, _ZERO)

        # Toolkit lands once per customer, on their first invoice in the set,
        # so a customer with ten invoices is not charged ten times.
        customer = group[0].customer_id
        t_total = _ZERO
        if customer not in seen_customers:
            t_total = t_by_customer.get(customer, _ZERO)
            seen_customers.add(customer)

        for i, (line, contribution) in enumerate(zip(group, contributions)):
            if positive_total > _ZERO and contribution > _ZERO:
                share = contribution / positive_total
            else:
                share = Decimal("1") if i == 0 else _ZERO
            out.append(line_caf(
                cfg, line,
                third_party=k_total * share,
                toolkit=t_total * share,
                vendor_yield=y_total * share))
    return out


def total(items: Iterable[LineCAF], *, include_aged: bool = False) -> Decimal:
    """Sum CAF. Aged lines are excluded by default — they are a separate pool."""
    return sum((x.caf for x in items if include_aged or not x.is_aged), _ZERO)
