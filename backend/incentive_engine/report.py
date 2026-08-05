"""Zone-separated outputs.

Two builders, two return types, and no shared serialiser between them. The
operations report cannot leak cost because ``CustomerPoints`` and
``SalespersonPayout`` have no field to leak it into — the enforcement is the
type, and this module simply respects it rather than filtering anything.

``owner_reconciliation`` is the only function in the package that touches cost,
and it is never called on the operations path.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Iterable, Sequence

from .caf import LineCAF
from .config import Config
from .floor import m_floor_for_family
from .models import CustomerPoints, OwnerReconciliation, SalespersonPayout

_ZERO = Decimal("0")

#: Any of these appearing in an operations payload is a defect, not a warning.
FORBIDDEN_IN_OPS = ("cost", "margin", "m_floor", "gross_profit", "landed",
                    "buy_price", "purchase_rate")


def operations_payload(payout: SalespersonPayout,
                       points: Sequence[CustomerPoints]) -> dict:
    """What a salesperson and the ops team see. Prices and points only."""
    return {
        "salesperson_id": payout.salesperson_id,
        "period": payout.period,
        "entity_id": payout.entity_id,
        "config_version": payout.config_version,
        "total_weighted_points": str(payout.total_weighted_points),
        "q_multiplier": str(payout.q_multiplier),
        "gate_status": payout.gate_status,
        "payout_gross": str(payout.payout_gross),
        "payout_cash": str(payout.payout_cash_70),
        "relationship_bank": str(payout.relationship_bank_30),
        "clawbacks_applied": str(payout.clawbacks_applied),
        "customers": [
            {"customer_id": p.customer_id, "rsi": p.rsi, "band": p.rsi_band,
             "baseline_caf": str(p.baseline_caf),
             "incremental_caf": str(p.incremental_caf),
             "collected_caf": str(p.collected_caf),
             "weighted_points": str(p.weighted_points),
             "recovery_bounty": str(p.recovery_bounty),
             "provisional_hold": str(p.provisional_hold)}
            for p in points
        ],
        "audit": list(payout.audit),
    }


def assert_ops_clean(payload: dict) -> None:
    """Belt as well as braces.

    The type already makes a cost field impossible; this catches the case where
    somebody stuffs one into a free-form audit dict — which is the only route
    left, and therefore the one worth guarding.
    """
    blob = json.dumps(payload).lower()
    found = [word for word in FORBIDDEN_IN_OPS if word in blob]
    if found:
        raise AssertionError(
            f"operations payload mentions {found}. I1 is enforced by type; a "
            "leak here means something was written into a free-form field.")


def owner_reconciliation(cfg: Config, payout: SalespersonPayout,
                         points: Sequence[CustomerPoints],
                         lines: Iterable[LineCAF],
                         cost_by_line: dict[str, Decimal]) -> OwnerReconciliation:
    """OWNER ZONE. The only place cost and CAF appear in one object."""
    rows = list(lines)
    cost_total = _ZERO
    revenue = _ZERO
    families: dict[str, Any] = {}
    for lc in rows:
        key = f"{lc.line.invoice_id}:{lc.line.item_id}"
        unit_cost = cost_by_line.get(key)
        if unit_cost is not None:
            cost_total += unit_cost * lc.line.qty
        revenue += lc.line.line_value_net
        families.setdefault(lc.line.item_family,
                            str(m_floor_for_family(cfg, lc.line.item_family)))

    gross_profit = revenue - cost_total
    caf_total = sum((lc.caf for lc in rows), _ZERO)
    return OwnerReconciliation(
        payout=payout,
        customer_points=tuple(points),
        gross_profit=gross_profit,
        cost_of_goods=cost_total,
        m_floor_by_family=families,
        leakage={
            "revenue": str(revenue),
            "caf": str(caf_total),
            # The gap between GP and CAF is exactly the floor premium — the
            # constant per unit that makes CAF strictly more conservative than
            # gross profit on quantity.
            "floor_premium": str(gross_profit - caf_total),
            "third_party_K": str(sum((lc.third_party for lc in rows), _ZERO)),
            "toolkit_charged": str(sum((lc.toolkit_charged for lc in rows), _ZERO)),
            "vendor_yield_Y": str(sum((lc.vendor_yield for lc in rows), _ZERO)),
            "payout_as_pct_of_caf": (
                str((payout.payout_gross / caf_total).quantize(Decimal("0.0001")))
                if caf_total > _ZERO else None),
        },
    )
