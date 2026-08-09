"""The data contracts, with the zone separation enforced by type.

Section 6 of the brief asks for two distinct output types rather than one type
filtered on the way out. That distinction is the whole of I1's enforcement: an
operations output physically cannot carry a cost field because the dataclass
has none, so no serialiser, no debug dump and no future contributor adding "one
more field for convenience" can leak it. Filtering is a discipline; a type is a
guarantee.

Inputs validate on construction. A payout built from a line with a negative
quantity or a `K` on a restricted customer is not a payout to correct later —
it is a computation that must not have happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

_ZERO = Decimal("0")


class ValidationError(ValueError):
    """An input that must not reach the computation path."""


def _money(value, where: str) -> Decimal:
    if isinstance(value, float):
        raise ValidationError(f"{where}: floats are not permitted in the payout path")
    return value if isinstance(value, Decimal) else Decimal(str(value))


# ── inputs ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class InvoiceLine:
    entity_id: str
    invoice_id: str
    invoice_date: date
    customer_id: str
    customer_group_id: str
    item_id: str
    item_family: str
    brand: str
    qty: Decimal
    #: Net realised — after every discount, freight and packing concession and
    #: credit-period loading. If a rupee was given away in any form it is gone
    #: from here, which is what makes "route the kickback as a discount"
    #: cost exactly as much as declaring it.
    unit_price_net: Decimal
    #: F, resolved from the published schedule at invoice date. In the
    #: SELLING-PRICE domain — the salesperson sees it, the margin behind it is
    #: never in this record.
    floor_price: Decimal
    salesperson_id: str
    is_aged_stock: bool = False
    stock_age_days: Optional[int] = None
    #: Who drove the procurement of this stock. The Q1(d) causation control and
    #: the Q3(b) commitment residual are the same rule and both read this.
    aged_causer_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("qty", "unit_price_net", "floor_price"):
            object.__setattr__(self, name, _money(getattr(self, name), name))
        if self.qty <= _ZERO:
            raise ValidationError(f"{self.invoice_id}: qty must be positive")
        if self.unit_price_net < _ZERO:
            raise ValidationError(f"{self.invoice_id}: negative net price")
        if self.is_aged_stock and self.stock_age_days is None:
            raise ValidationError(
                f"{self.invoice_id}: aged stock with no age — the bounty rate "
                "and the aged floor are both functions of age")

    @property
    def line_value_net(self) -> Decimal:
        return self.qty * self.unit_price_net


@dataclass(frozen=True)
class Payment:
    entity_id: str
    invoice_id: str
    receipt_date: date
    amount: Decimal
    due_date: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount, "amount"))

    @property
    def days_late(self) -> int:
        """Negative for advance, 0 on the due date, positive when late."""
        return (self.receipt_date - self.due_date).days


@dataclass(frozen=True)
class ThirdPartyIncentive:
    """K — declared, deducted 1:1, and funded entirely by the salesperson."""

    entity_id: str
    deal_id: str
    invoice_id: str
    customer_id: str
    amount: Decimal
    form: str
    declared_at: date
    #: PSU / government / defence supply chain. I2 is a hard block, not a
    #: penalty: the record cannot be constructed at all.
    customer_is_restricted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount, "amount"))
        if self.amount < _ZERO:
            raise ValidationError("K cannot be negative")
        if self.customer_is_restricted and self.amount > _ZERO:
            raise ValidationError(
                f"{self.customer_id}: a third-party incentive on a PSU, "
                "government or defence-supply-chain customer is unsaveable. "
                "The Prevention of Corruption Act covers the giver, and the "
                "realistic downside is GeM blacklisting — which permanently "
                "ends the revenue line. This is I2 and it is not a policy "
                "message; the record does not exist.")


@dataclass(frozen=True)
class ToolkitSpend:
    entity_id: str
    customer_id: str
    salesperson_id: str
    amount: Decimal
    category: str
    receipt_ref: str
    date: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount, "amount"))
        if not self.receipt_ref:
            raise ValidationError(
                "toolkit spend with no receipt reference — the cap is "
                "meaningless without attribution, and unattributed spend is "
                "exploit 32 wearing a legitimate label")


@dataclass(frozen=True)
class VendorYield:
    """Y — credited 1:1, documentary proof mandatory."""

    entity_id: str
    brand: str
    amount: Decimal
    proof_type: str
    proof_ref: str
    po_id: Optional[str] = None
    invoice_id: Optional[str] = None
    salesperson_id: str = ""
    is_commitment_linked: bool = False
    committed_qty: Decimal = _ZERO
    committed_qty_sold_to_date: Decimal = _ZERO
    causer_id: Optional[str] = None
    first_credited_on: Optional[date] = None

    VALID_PROOF = ("credit_note", "debit_note", "vendor_mail", "po_price_delta")

    def __post_init__(self) -> None:
        for name in ("amount", "committed_qty", "committed_qty_sold_to_date"):
            object.__setattr__(self, name, _money(getattr(self, name), name))
        if self.proof_type not in self.VALID_PROOF:
            raise ValidationError(
                f"{self.proof_type!r} is not a proof type. Y without a "
                "document is exploit 29 — claiming credit for support the "
                "vendor was giving anyway.")
        if not self.proof_ref:
            raise ValidationError("Y requires a proof reference")
        if self.is_commitment_linked and self.committed_qty <= _ZERO:
            raise ValidationError(
                "commitment-linked Y with no committed quantity — the release "
                "schedule has no denominator")


@dataclass(frozen=True)
class VendorSpiff:
    entity_id: str
    salesperson_id: str
    brand: str
    amount: Decimal
    surrendered: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", _money(self.amount, "amount"))


@dataclass(frozen=True)
class StandardBuyPrice:
    """The reference Y is measured against. OWNER ZONE — it is a buy price."""

    entity_id: str
    brand: str
    item_id: str
    standard_price: Decimal
    effective_from: date
    effective_to: Optional[date] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "standard_price",
                           _money(self.standard_price, "standard_price"))


@dataclass(frozen=True)
class FloorPriceSchedule:
    entity_id: str
    item_id: str
    floor_price: Decimal
    effective_from: date
    effective_to: Optional[date] = None
    #: OWNER ZONE ONLY. Present on the input record because the owner
    #: reconciliation needs it; never serialised to an operations output, and
    #: the ops dataclasses below have no field it could be written into.
    m_floor_family: Optional[Decimal] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "floor_price", _money(self.floor_price, "floor_price"))
        if self.m_floor_family is not None:
            object.__setattr__(self, "m_floor_family",
                               _money(self.m_floor_family, "m_floor_family"))


@dataclass(frozen=True)
class Trial:
    entity_id: str
    customer_id: str
    item_id: str
    salesperson_id: str
    trial_date: date
    #: Q2(a). No pre-commitment document and the trial can never count in the
    #: conversion numerator — it still counts in the denominator, so proving
    #: without protection is directly costly through Q rather than banned.
    pre_commitment_doc_ref: Optional[str] = None
    converted: bool = False
    first_repeat_order_date: Optional[date] = None
    switched_away_date: Optional[date] = None
    #: Q2(c). Which structural locks the proving created.
    lock_in_factors: tuple[str, ...] = ()

    LOCK_INS = ("process_level_proving", "multi_threaded", "rate_contract",
                "exclusive_part", "consignment_vmi")

    @property
    def protected(self) -> bool:
        return bool(self.pre_commitment_doc_ref)


@dataclass(frozen=True)
class WalletDeclaration:
    """Somebody's stated view of what share of a customer's spend we hold.

    A declaration, never a measurement. The platform sees what a customer buys
    here and has nothing that says what they buy elsewhere — ``insight/
    dependency.py`` states the same limit for the same reason — so this is the
    only shape share-of-wallet can honestly take today: a number with a name
    and a date on it, so a reader can weigh who said it and how long ago.

    ``share`` is a ratio in [0, 1], matching the convention everywhere else in
    this codebase: margin is ``0.24``, never ``24``.

    It is deliberately not a bare ``Decimal`` on ``CustomerAttributes``. It was
    one, and a bare number is exactly what let it be scored in the RSI without
    anyone being able to ask where it came from.
    """

    share: Decimal
    declared_by: str
    declared_on: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "share", _money(self.share, "share"))
        if not self.declared_by:
            raise ValidationError(
                "share: a wallet declaration with no author cannot be weighed")
        if not Decimal("0") <= self.share <= Decimal("1"):
            raise ValidationError(
                f"share: {self.share} is not a ratio in [0, 1] — margin and "
                "share are ratios in this codebase, never percentages")


@dataclass(frozen=True)
class CustomerAttributes:
    customer_id: str
    customer_group_id: str
    entity_ids: tuple[str, ...]
    first_purchase_date: Optional[date]
    active_months_12: int
    families_bought: int
    avg_days_beyond_terms_12: Decimal
    live_contacts: int
    is_restricted: bool = False
    #: ``None`` means nobody has declared one — which is the true state for
    #: every customer today, since nothing in the application produces it.
    #: None is not zero: an undeclared customer is not a customer we hold no
    #: share of, and defaulting it to ``Decimal("0")`` would be exactly the
    #: benign default the working agreement forbids.
    share_of_wallet_declared: Optional[WalletDeclaration] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "avg_days_beyond_terms_12",
                           _money(self.avg_days_beyond_terms_12,
                                  "avg_days_beyond_terms_12"))


# ── outputs ──────────────────────────────────────────────────────────────────
#
# The zone boundary. Read the field lists side by side: the operations types
# below have no cost, no margin and no m_floor to populate. That is not an
# omission to be careful about — it is the enforcement mechanism.

@dataclass(frozen=True)
class CustomerPoints:
    """OPERATIONS ZONE. Prices and points only."""

    customer_id: str
    salesperson_id: str
    period: str
    rsi: int
    rsi_band: str
    baseline_caf: Decimal
    incremental_caf: Decimal
    collected_caf: Decimal
    weighted_points: Decimal
    #: Aged-stock recovery is a separate pool and is reported separately, so a
    #: reader can never mistake a one-off clearance for recurring contribution.
    recovery_bounty: Decimal = _ZERO
    provisional_hold: Decimal = _ZERO
    # NO cost. NO margin. NO m_floor. Absent from the type, not unpopulated.


@dataclass(frozen=True)
class SalespersonPayout:
    """OPERATIONS ZONE."""

    salesperson_id: str
    period: str
    entity_id: str
    total_weighted_points: Decimal
    q_multiplier: Decimal
    gate_status: dict
    payout_gross: Decimal
    payout_cash_70: Decimal
    relationship_bank_30: Decimal
    clawbacks_applied: Decimal
    config_version: str
    #: Every payout decomposes to the lines and receipts that produced it, each
    #: multiplier shown as applied. A payout nobody can trace is a payout
    #: nobody will believe.
    audit: tuple = ()


@dataclass(frozen=True)
class OwnerReconciliation:
    """OWNER ZONE. Everything above, plus the economics."""

    payout: SalespersonPayout
    customer_points: tuple[CustomerPoints, ...]
    gross_profit: Decimal
    cost_of_goods: Decimal
    m_floor_by_family: dict
    leakage: dict
