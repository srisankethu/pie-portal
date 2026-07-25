"""Realistic B2B-distributor fixtures for Signal Engine tests.

Builders produce in-memory ``Snapshot`` objects (no DB needed), so detector tests
are pure and deterministic. A single ``AS_OF`` anchors all windows.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.signals.base import CostRow, SaleRow, Snapshot

AS_OF = date(2026, 7, 1)


def sale(cust: str, prod: str, d: str, qty, price, *, invoice: str, line: str = "l1",
         revenue=None) -> SaleRow:
    q = Decimal(str(qty))
    p = Decimal(str(price))
    rev = Decimal(str(revenue)) if revenue is not None else q * p
    return SaleRow(customer_id=cust, product_id=prod, date=date.fromisoformat(d),
                   qty=q, unit_price=p, line_revenue=rev,
                   source_ref={"system": "zoho", "record_type": "invoice",
                               "record_id": invoice, "line_id": line},
                   external_ref=f"{invoice}:{line}")


def cost(prod: str, d: str, qty, unit_cost, *, bill: str, line: str = "l1") -> CostRow:
    return CostRow(product_id=prod, date=date.fromisoformat(d), qty=Decimal(str(qty)),
                   unit_cost=Decimal(str(unit_cost)),
                   source_ref={"system": "zoho", "record_type": "bill",
                               "record_id": bill, "line_id": line},
                   external_ref=f"{bill}:{line}")


def snap(sales=None, costs=None, cust_names=None, prod_names=None) -> Snapshot:
    return Snapshot(organization_id="org_test", sales=sales or [], costs=costs or [],
                    customer_names=cust_names or {}, product_names=prod_names or {})


# ── scenario builders ────────────────────────────────────────────────────────
def steady_customer(cust="c_steady", prod="p1") -> list[SaleRow]:
    """Even revenue across both windows — should NOT trigger decline."""
    out = []
    for i, d in enumerate(["2025-07-01", "2025-08-01", "2025-09-01", "2025-10-01",
                           "2025-11-01", "2025-12-01", "2026-01-01", "2026-02-01",
                           "2026-03-01", "2026-05-01", "2026-06-01", "2026-07-01"]):
        out.append(sale(cust, prod, d, 10, 100, invoice=f"inv-{cust}-{i}"))
    return out


def declining_customer(cust="c_decline", prod="p1") -> list[SaleRow]:
    """High baseline, low recent — should trigger decline."""
    out = [sale(cust, prod, "2025-07-01", 100, 100, invoice=f"inv-{cust}-0")]  # history
    for i, d in enumerate(["2026-02-01", "2026-03-01", "2026-04-01"]):        # baseline hi
        out.append(sale(cust, prod, d, 100, 100, invoice=f"inv-{cust}-b{i}"))  # 10000 each
    for i, d in enumerate(["2026-05-01", "2026-06-01", "2026-07-01"]):        # recent lo
        out.append(sale(cust, prod, d, 40, 100, invoice=f"inv-{cust}-r{i}"))   # 4000 each
    return out


def dormant_customer(cust="c_dormant", prod="p1") -> list[SaleRow]:
    """Regular ~30-day cadence that stopped months ago — should trigger dormancy."""
    out = []
    for i, d in enumerate(["2025-09-01", "2025-10-01", "2025-11-01", "2025-12-01",
                           "2026-01-01", "2026-02-01"]):
        out.append(sale(cust, prod, d, 10, 100, invoice=f"inv-{cust}-{i}"))
    return out  # last order 2026-02-01; AS_OF 2026-07-01 → ~150d gap vs ~30d cadence


def margin_deterioration_product(prod="p_margin") -> tuple[list[SaleRow], list[CostRow]]:
    """Stable cost, price fell recent vs baseline — margin drops."""
    costs = [cost(prod, "2025-06-01", 100, 70, bill="bill-m1")]  # cost 70 throughout
    sales = []
    for i, d in enumerate(["2026-02-01", "2026-03-01", "2026-04-01"]):
        sales.append(sale("cA", prod, d, 10, 100, invoice=f"inv-m-b{i}"))  # margin 30%
    for i, d in enumerate(["2026-05-01", "2026-06-01", "2026-07-01"]):
        sales.append(sale("cA", prod, d, 10, 80, invoice=f"inv-m-r{i}"))   # margin 12.5%
    return sales, costs


def cost_pass_through_product(prod="p_cost") -> tuple[list[SaleRow], list[CostRow]]:
    """Cost jumped 25%, selling price barely moved — pass-through gap."""
    costs = [cost(prod, "2026-01-01", 100, 400, bill="bill-c1"),
             cost(prod, "2026-05-01", 100, 500, bill="bill-c2")]  # +25%
    sales = []
    for i, d in enumerate(["2026-02-01", "2026-03-01", "2026-04-01"]):
        sales.append(sale("cB", prod, d, 10, 530, invoice=f"inv-c-b{i}"))  # before
    for i, d in enumerate(["2026-05-15", "2026-06-01", "2026-07-01"]):
        sales.append(sale("cB", prod, d, 10, 535, invoice=f"inv-c-r{i}"))  # after (flat)
    return sales, costs
