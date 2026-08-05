"""Y validation, SPIFF surrender, and the commitment-linked release.

Three controls, and the third is the one that ties the vendor sub-game to the
inventory sub-game.

**Documentary proof (Q3(c)).** Y without a credit note, debit note, vendor mail
or a demonstrable PO price delta is exploit 29 — claiming credit for support
the vendor was giving anyway. Enforced at construction in ``models.VendorYield``
so an undocumented claim cannot be built, let alone paid.

**SPIFF surrender.** A personal incentive from the vendor makes V a competing
principal: the salesperson's brand mix starts serving V's targets instead of
the company's margin. Surrendered, it is re-credited as Y and the salesperson
keeps the value. Retained, it is deducted at 1:1. Either way the salesperson is
financially indifferent and V has lost the ability to buy the brand mix. Given
YG1 concentration at 4U and Kennametal at SLS, this is the single most
important control in the design.

**Commitment-linked release (Q3(b)).** "Special price if you commit to 10,000
pieces" transfers inventory risk to us, and it is how dead stock is born. Y
from such a deal is credited PROVISIONALLY and releases only as the committed
stock actually sells. The unsold residual at 18 months is charged back under
the same causer rule that governs the recovery bounty — one mechanism, written
once, in ``recovery.rate_for_age`` and here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .config import Config
from .models import VendorSpiff, VendorYield

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class YieldRelease:
    yield_id: str
    brand: str
    claimed: Decimal
    released: Decimal
    withheld: Decimal
    fraction_sold: Decimal
    is_commitment_linked: bool
    residual_charge: Decimal
    causer_id: str | None

    def explain(self) -> dict:
        return {"brand": self.brand, "claimed": str(self.claimed),
                "released": str(self.released), "withheld": str(self.withheld),
                "fraction_sold": str(self.fraction_sold),
                "residual_charge": str(self.residual_charge)}


def release(cfg: Config, y: VendorYield, *,
            months_since_commitment: int = 0) -> YieldRelease:
    """How much of a claimed Y is earned now."""
    ref = y.po_id or y.invoice_id or y.brand

    if not y.is_commitment_linked:
        return YieldRelease(ref, y.brand, y.amount, y.amount, _ZERO,
                            _ONE, False, _ZERO, y.causer_id)

    if not cfg.get("vendor", "commitment_release_on_sale"):
        return YieldRelease(ref, y.brand, y.amount, y.amount, _ZERO,
                            _ONE, True, _ZERO, y.causer_id)

    sold = min(y.committed_qty_sold_to_date, y.committed_qty)
    fraction = (sold / y.committed_qty) if y.committed_qty > _ZERO else _ZERO
    released = y.amount * fraction
    withheld = y.amount - released

    # At the horizon the withheld remainder stops being "not yet" and becomes
    # "never". It is charged back to whoever accepted the commitment, which is
    # the same attribution the recovery bounty uses.
    residual = _ZERO
    horizon = cfg.int_("vendor", "commitment_residual_months")
    if months_since_commitment >= horizon and withheld > _ZERO:
        residual = withheld

    return YieldRelease(ref, y.brand, y.amount, released, withheld,
                        fraction, True, residual, y.causer_id)


def spiff_adjustment(cfg: Config, spiffs: Iterable[VendorSpiff]) -> tuple[Decimal, Decimal]:
    """Returns (credited as Y, deducted from CAF).

    Surrendered SPIFFs become Y and the salesperson keeps the value. Retained
    SPIFFs are deducted 1:1. The point is not to punish either choice — it is
    that both leave the salesperson with the same money, so V's payment stops
    being able to move the brand mix.
    """
    credited = _ZERO
    deducted = _ZERO
    rate = cfg.dec("vendor", "spiff_retained_charge_rate")
    for s in spiffs:
        if s.surrendered:
            credited += s.amount
        else:
            deducted += s.amount * rate
    return credited, deducted


def value_extended_credit(cfg: Config, amount: Decimal, days: int) -> Decimal:
    """Q3(c): cost of capital x amount x days / 365."""
    rate = cfg.dec("vendor", "valuation", "cost_of_capital_annual")
    return (amount * rate * Decimal(days) / Decimal("365"))


def value_concession(cfg: Config, kind: str, amount: Decimal) -> Decimal:
    """Non-price concessions, at published conventions rather than judgement."""
    table = cfg.get("vendor", "valuation")
    if kind == "free_tooling":
        return amount * Decimal(str(table["free_tooling"]))
    if kind == "demo_stock":
        # It comes back. Half credit.
        return amount * Decimal(str(table["demo_stock"]))
    if kind == "training":
        return min(amount, Decimal(str(table["training_day_cap"])))
    raise ValueError(f"no valuation convention for {kind!r}")
