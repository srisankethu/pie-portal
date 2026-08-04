"""QUOTE_CONTEXT assembler (§9.5) — on-demand, not a periodic detector.

Assembles the deterministic commercial facts a human should weigh before pricing
a quote for a customer + item(s): this customer's history for the item, last
price paid, price trend, purchase cadence, current decline/dormancy status, and
— tagged RESTRICTED — the cost basis / standard margin plus a direction-only
cost-movement flag (OPERATIONAL) suitable for a salesperson.

Every fact is tagged with a ``data_class`` so the later Context Assembler can
redact RESTRICTED facts for salesperson scope. This module does NOT redact and
does NOT recommend — it states facts with source references, or an explicit
"unknown" when a fact cannot be established.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from . import aggregates as agg
from .base import Snapshot, evidence_ref
from .config import SignalThresholds

OPERATIONAL = "OPERATIONAL"
RESTRICTED = "RESTRICTED"


def _fact(label: str, value: Any, unit: Optional[str], data_class: str,
          source_refs: list[dict], as_of: date) -> dict[str, Any]:
    return {"label": label, "value": value, "unit": unit, "data_class": data_class,
            "source_refs": source_refs, "as_of": as_of.isoformat()}


def _direction(delta: float, tol: float = 0.01) -> str:
    return "up" if delta > tol else "down" if delta < -tol else "flat"


def assemble(snapshot: Snapshot, customer_id: str, product_ids: list[str],
             th: SignalThresholds, as_of: date) -> dict[str, Any]:
    recent_w = agg.recent_window(as_of, th)
    prior_w = agg.prior_window(as_of, th)
    cust_sales = snapshot.sales_for_customer(customer_id)
    facts: list[dict] = []
    unknowns: list[dict] = []

    # ── customer-level context ───────────────────────────────────────────────
    baseline_rev = agg.revenue_in(cust_sales, prior_w)
    recent_rev = agg.revenue_in(cust_sales, recent_w)
    if baseline_rev > 0:
        change = float((recent_rev - baseline_rev) / baseline_rev)
        facts.append(_fact("revenue_trend_pct", round(change, 4), "ratio", OPERATIONAL,
                           [], as_of))
        facts.append(_fact("revenue_trend_direction", _direction(change), None, OPERATIONAL,
                           [], as_of))
    else:
        unknowns.append({"field": "revenue_trend", "reason": "no baseline-period revenue"})

    # Two orders is enough to *describe* a rhythm on a quote, where the reader
    # sees the order count alongside it — the detector needs four before it will
    # raise a signal off the same arithmetic. Same function, different bar.
    cadence = agg.cadence_of(cust_sales, as_of, min_orders=2,
                             multiplier=th.dormancy_interval_multiplier)
    if cadence.estimable:
        facts.append(_fact("typical_interval_days", cadence.typical_interval_days,
                           "days", OPERATIONAL, [], as_of))
        facts.append(_fact("days_since_last_order", cadence.days_since_last,
                           "days", OPERATIONAL, [], as_of))
        facts.append(_fact("is_overdue", cadence.overdue, None, OPERATIONAL, [], as_of))
    else:
        # Previously this branch was only taken below two orders, so a customer
        # whose orders all landed on one day got a typical interval of zero and
        # `is_overdue` true on every quote thereafter — the shared guard now
        # reports that as not estimable, which is what it always was.
        unknowns.append({"field": "purchase_cadence",
                         "reason": ("fewer than 2 orders, or no measurable gap "
                                    "between them — cadence not estimable")})

    # ── per-item context ─────────────────────────────────────────────────────
    items: list[dict] = []
    for pid in product_ids:
        item_facts: list[dict] = []
        cp_sales = sorted((s for s in cust_sales if s.product_id == pid), key=lambda s: s.date)
        if cp_sales:
            last = cp_sales[-1]
            item_facts.append(_fact("last_price_paid", float(round(last.unit_price, 2)), "currency",
                                    OPERATIONAL, [evidence_ref(last.source_ref)], as_of))
            item_facts.append(_fact("times_purchased", len(cp_sales), "count", OPERATIONAL,
                                    [evidence_ref(s.source_ref) for s in cp_sales], as_of))
            recent_p = agg.avg_unit_price(cp_sales, recent_w)
            prior_p = agg.avg_unit_price(cp_sales, prior_w)
            if recent_p is not None and prior_p is not None and prior_p > 0:
                tr = float((recent_p - prior_p) / prior_p)
                item_facts.append(_fact("price_trend_pct", round(tr, 4), "ratio", OPERATIONAL,
                                        [], as_of))
                item_facts.append(_fact("price_trend_direction", _direction(tr), None,
                                        OPERATIONAL, [], as_of))
        else:
            unknowns.append({"field": f"item_history:{pid}",
                             "reason": "this customer has not purchased this item"})

        # RESTRICTED cost/margin + direction-only flag (OPERATIONAL)
        costs = snapshot.costs_for_product(pid)
        cost_row = agg.cost_basis_asof(costs, as_of)
        if cost_row is not None and cost_row.unit_cost > 0:
            item_facts.append(_fact("current_unit_cost", float(round(cost_row.unit_cost, 2)),
                                    "currency", RESTRICTED, [evidence_ref(cost_row.source_ref)],
                                    as_of))
            if cp_sales:
                last_price = cp_sales[-1].unit_price
                margin = float((last_price - cost_row.unit_cost) / last_price) if last_price > 0 else None
                if margin is not None:
                    item_facts.append(_fact("standard_margin_pct", round(margin, 4), "ratio",
                                            RESTRICTED, [], as_of))
            prior_cost = agg.prior_cost_basis(costs)
            if prior_cost is not None and prior_cost.unit_cost > 0:
                cdelta = float((cost_row.unit_cost - prior_cost.unit_cost) / prior_cost.unit_cost)
                # RESTRICTED value + OPERATIONAL direction-only flag for salespeople
                item_facts.append(_fact("cost_delta_pct", round(cdelta, 4), "ratio", RESTRICTED,
                                        [], as_of))
                item_facts.append(_fact("cost_movement_direction", _direction(cdelta), None,
                                        OPERATIONAL, [], as_of))
        else:
            unknowns.append({"field": f"cost_basis:{pid}",
                             "reason": "no reliable cost on record for this item"})

        items.append({"product_id": pid, "label": snapshot.product_names.get(pid, pid),
                      "facts": item_facts})

    return {
        "decision_type": "QUOTE_CONTEXT",
        "organization_id": snapshot.organization_id,
        "subject_ref": {"entity_type": "QUOTE", "customer_id": customer_id,
                        "product_ids": product_ids},
        "as_of": as_of.isoformat(),
        "customer_facts": facts,
        "items": items,
        "unknowns": unknowns,
    }
