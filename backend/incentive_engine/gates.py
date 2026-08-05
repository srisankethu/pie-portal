"""G1 / G2 / G3 — binary qualifiers, with cure rather than forfeiture.

Failure WITHHOLDS the period; it does not destroy it. Released on cure within
60 days. That asymmetry is deliberate and it is the difference between a
control that works and one that backfires: permanent forfeiture makes
concealment the rational response to a mistake, deferral makes correction the
rational response.

G1 carries the two rules that are not negotiable. Undeclared K is charged back
at 3x plus the Relationship Bank, which makes concealment negative-expected-
value at any detection probability above about a quarter. K on a PSU, defence
or government-supply-chain customer is not a gate failure at all — the record
cannot be constructed (see models.ThirdPartyIncentive), so it never reaches
here. A gate is a check on behaviour; I2 is a check on possibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from .config import Config
from .models import InvoiceLine, ThirdPartyIncentive

_ZERO = Decimal("0")


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reasons: tuple[str, ...]
    cure_days: int
    cured_by: Optional[date] = None

    def to_dict(self) -> dict:
        return {"gate": self.name, "passed": self.passed,
                "reasons": list(self.reasons), "cure_days": self.cure_days}


@dataclass(frozen=True)
class GateStatus:
    results: tuple[GateResult, ...]

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def to_dict(self) -> dict:
        return {"passed": self.passed,
                "gates": [r.to_dict() for r in self.results]}


def g1_compliance(cfg: Config, declared: Sequence[ThirdPartyIncentive],
                  undeclared_found: Sequence[ThirdPartyIncentive] = (),
                  invoice_dates: Optional[dict] = None) -> GateResult:
    reasons: list[str] = []
    for k in undeclared_found:
        reasons.append(
            f"undeclared third-party incentive of {k.amount} on {k.invoice_id}")
    dates = invoice_dates or {}
    for k in declared:
        invoiced = dates.get(k.invoice_id)
        if invoiced is not None and k.declared_at > invoiced:
            # Declaring after the fact is not declaring. The whole point of the
            # pre-invoice requirement is that the number was committed to
            # before anyone knew whether it would be noticed.
            reasons.append(
                f"{k.invoice_id}: K declared {k.declared_at} after invoicing "
                f"on {invoiced}")
    return GateResult("G1_compliance", not reasons, tuple(reasons),
                      cfg.int_("gates", "cure_days", "G1_compliance"))


def g2_data_integrity(cfg: Config, lines: Iterable[InvoiceLine], *,
                      unmastered_items: Iterable[str] = (),
                      missing_hsn: Iterable[str] = (),
                      missing_customer_po: Iterable[str] = ()) -> GateResult:
    reasons: list[str] = []
    for item in sorted(set(unmastered_items)):
        reasons.append(f"unmastered item on an order: {item}")
    for item in sorted(set(missing_hsn)):
        reasons.append(f"item without HSN: {item}")
    for inv in sorted(set(missing_customer_po)):
        reasons.append(f"order without a customer PO reference: {inv}")
    # A zero or negative floor is a pseudo-item or a zero-cost line: it would
    # pay the full selling price as contribution, which is why this gate exists
    # rather than being left to the master-hygiene backlog.
    for line in lines:
        if line.floor_price <= _ZERO:
            reasons.append(
                f"{line.invoice_id}/{line.item_id}: no published floor — the "
                "line would pay full price as contribution")
    return GateResult("G2_data_integrity", not reasons, tuple(reasons),
                      cfg.int_("gates", "cure_days", "G2_data_integrity"))


def g3_receivables(cfg: Config, overdue: Sequence[tuple[str, int, bool]]) -> GateResult:
    """``overdue`` is (invoice_id, days_overdue, has_escalated_plan).

    "Formally escalated" means, operationally: a written recovery plan on the
    invoice record, naming an owner, carrying dated actions, and recording
    either a payment commitment from the customer or a decision to place the
    account on credit hold / refer it. A verbal follow-up is not an escalation.
    """
    limit = cfg.int_("gates", "receivables_escalation_days")
    reasons = [f"{inv}: {days} days overdue with no escalated recovery plan"
               for inv, days, escalated in overdue
               if days > limit and not escalated]
    return GateResult("G3_receivables", not reasons, tuple(reasons),
                      cfg.int_("gates", "cure_days", "G3_receivables"))


def evaluate(*results: GateResult) -> GateStatus:
    return GateStatus(tuple(results))


def undeclared_k_clawback(cfg: Config, amount: Decimal) -> Decimal:
    """3x. Justified in DESIGN_DECISIONS.md Q6."""
    return amount * cfg.dec("gates", "undeclared_k_clawback_multiple")
