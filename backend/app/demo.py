"""Realistic demo dataset for the Decision Platform UI.

Builds a multi-account read model whose histories deterministically trigger the
five signal families, then runs the real pipeline (detectors → decision
generation) so the UI renders genuine, role-gated decisions end to end. This is
demo/seed data only — it inserts into the read model exactly as a Zoho sync
would, then lets the deterministic engine and AI layer do their normal work.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from .config import settings
from .domain import models
from .seed import ensure_org_and_users
from .signals.engine import run_detectors

# Reference "today" for the demo histories.
AS_OF = date(2026, 7, 22)

# Demo customers/products get fixed, human-readable primary keys — never the
# random uuid4 a real Zoho sync assigns. That is what makes them unambiguously
# identifiable later: no real synced row can ever collide with one of these
# ids, no matter what a real customer or product happens to be named.
_DEMO_CUSTOMERS = [
    ("cst_rane", "Rane Madras", "usr_sales"),
    ("cst_ace", "ACE Designers", "usr_sales"),
    ("cst_pitti", "Pitti Engineering Ltd", "usr_sales"),
    ("cst_brakes", "Brakes India", "usr_sales"),
    ("cst_tvs", "TVS Sundram Fasteners", "usr_sales"),
]
_DEMO_PRODUCTS = [
    ("prd_cnmg", "CNMG 120408-MP insert"),
    ("prd_dnmg", "DNMG 150608-MP insert"),
    ("prd_holder", "25mm shank turning holder"),
    ("prd_ream", "8.0mm HSS-Co machine reamer"),
]
DEMO_CUSTOMER_IDS = frozenset(c[0] for c in _DEMO_CUSTOMERS)
DEMO_PRODUCT_IDS = frozenset(p[0] for p in _DEMO_PRODUCTS)


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _sale(org, cust, prod, when, qty, price, inv, line="l1"):
    return models.SalesTxn(
        organization_id=org, external_ref=f"{inv}:{line}", customer_id=cust,
        product_id=prod, date=when, qty=Decimal(str(qty)), unit_price=Decimal(str(price)),
        line_revenue=Decimal(str(qty)) * Decimal(str(price)),
        source_ref={"system": "zoho", "record_type": "invoice", "record_id": inv, "line_id": line})


def _cost(org, prod, when, qty, unit_cost, bill, line="l1"):
    # Demo bills carry no discount, so rate == the effective cost and the
    # discount is 0% — the same shape a real, undiscounted bill line produces.
    cost = Decimal(str(unit_cost))
    return models.CostRecord(
        organization_id=org, external_ref=f"{bill}:{line}", product_id=prod, date=when,
        qty=Decimal(str(qty)), unit_cost=cost, rate=cost, discount_percent=Decimal("0"),
        source_ref={"system": "zoho", "record_type": "bill", "record_id": bill, "line_id": line})


def seed_demo(session: Session) -> dict:
    """Idempotent-ish demo seed. Returns the generate summary."""
    org = settings.DEFAULT_ORG_ID
    ensure_org_and_users(session)

    # Skip if already seeded.
    if session.get(models.Customer, "cst_rane"):
        return {"note": "demo already seeded"}

    for cid, name, uid in _DEMO_CUSTOMERS:
        session.add(models.Customer(customer_id=cid, organization_id=org, external_id=cid,
                                    name=name, assigned_user_id=uid, status="ACTIVE"))
    for pid, name in _DEMO_PRODUCTS:
        session.add(models.Product(product_id=pid, organization_id=org, external_id=pid, name=name))

    rows: list = []

    # ── Rane Madras: CUSTOMER_DECLINE (high baseline, low recent) ────────────
    for i, da in enumerate([150, 140]):   # history
        rows.append(_sale(org, "cst_rane", "prd_ream", _d(da), 100, 103, f"inv-rane-h{i}"))
    for i, da in enumerate([120, 100, 95]):  # prior window, strong
        rows.append(_sale(org, "cst_rane", "prd_ream", _d(da), 100, 103, f"inv-rane-b{i}"))
    for i, da in enumerate([50, 30, 20]):     # recent window, down ~41%
        rows.append(_sale(org, "cst_rane", "prd_ream", _d(da), 59, 103, f"inv-rane-r{i}"))

    # ── ACE Designers: CUSTOMER_DORMANCY (regular then silent) ───────────────
    for i, da in enumerate([250, 216, 182, 148, 130, 96]):   # ~34d cadence, last 96d ago
        rows.append(_sale(org, "cst_ace", "prd_holder", _d(da), 12, 1800, f"inv-ace-{i}"))

    # ── Pitti: MARGIN_DETERIORATION on prd_dnmg (price fell, cost stable) ─────
    rows.append(_cost(org, "prd_dnmg", _d(200), 100, 372, "bill-dnmg-1"))
    for i, da in enumerate([120, 100, 95]):
        rows.append(_sale(org, "cst_pitti", "prd_dnmg", _d(da), 20, 506, f"inv-pitti-b{i}"))
    for i, da in enumerate([50, 30, 20]):
        rows.append(_sale(org, "cst_pitti", "prd_dnmg", _d(da), 20, 430, f"inv-pitti-r{i}"))

    # ── Brakes India: COST_PASS_THROUGH on prd_cnmg (cost jumped, price flat) ─
    rows.append(_cost(org, "prd_cnmg", _d(160), 100, 320, "bill-cnmg-1"))
    rows.append(_cost(org, "prd_cnmg", _d(8), 100, 349, "bill-cnmg-2"))   # +9%
    for i, da in enumerate([120, 100, 90]):
        rows.append(_sale(org, "cst_brakes", "prd_cnmg", _d(da), 30, 412, f"inv-brakes-b{i}"))
    for i, da in enumerate([5, 3, 1]):
        rows.append(_sale(org, "cst_brakes", "prd_cnmg", _d(da), 30, 414, f"inv-brakes-r{i}"))

    for r in rows:
        session.add(r)
    session.flush()

    run_detectors(session, org, as_of=AS_OF)
    from .decisions.service import DecisionService
    summary = DecisionService(session, org).generate()
    session.flush()
    return summary


def purge_demo_seed(session: Session, organization_id: str) -> dict[str, int]:
    """Remove the demo dataset for one org, if present.

    Called whenever a real Zoho sync runs, so a fabricated customer or product
    never sits alongside real data — a fresh clone shows demo decisions before
    anyone connects Zoho, and once a real account is linked those decisions
    would otherwise linger forever, indistinguishable from the real ones they
    were only ever a stand-in for.

    Every delete is scoped to the fixed demo ids, so it can never touch a real
    Zoho customer or product, and it is a no-op (all counts zero) once nothing
    demo-seeded remains — safe to call on every sync, not just the first.
    """
    subject_ids = list(DEMO_CUSTOMER_IDS | DEMO_PRODUCT_IDS)
    removed = {
        "decisions": session.query(models.Decision).filter(
            models.Decision.organization_id == organization_id,
            models.Decision.subject_entity_id.in_(subject_ids),
        ).delete(synchronize_session=False),
        "signals": session.query(models.Signal).filter(
            models.Signal.organization_id == organization_id,
            models.Signal.subject_entity_id.in_(subject_ids),
        ).delete(synchronize_session=False),
        # Telemetry from the AI calls the demo decisions triggered — not
        # customer-visible, but left in place it would permanently skew the
        # owner-facing AI cost/health metrics with demo-run numbers.
        "ai_call_logs": session.query(models.AiCallLog).filter(
            models.AiCallLog.organization_id == organization_id,
            models.AiCallLog.subject_entity_id.in_(subject_ids),
        ).delete(synchronize_session=False),
        "sales_txns": session.query(models.SalesTxn).filter(
            models.SalesTxn.organization_id == organization_id,
            models.SalesTxn.customer_id.in_(list(DEMO_CUSTOMER_IDS)),
        ).delete(synchronize_session=False),
        "cost_records": session.query(models.CostRecord).filter(
            models.CostRecord.organization_id == organization_id,
            models.CostRecord.product_id.in_(list(DEMO_PRODUCT_IDS)),
        ).delete(synchronize_session=False),
        "customers": session.query(models.Customer).filter(
            models.Customer.organization_id == organization_id,
            models.Customer.customer_id.in_(list(DEMO_CUSTOMER_IDS)),
        ).delete(synchronize_session=False),
        "products": session.query(models.Product).filter(
            models.Product.organization_id == organization_id,
            models.Product.product_id.in_(list(DEMO_PRODUCT_IDS)),
        ).delete(synchronize_session=False),
    }
    session.flush()
    return removed


def main() -> None:
    from .db import SessionLocal
    s = SessionLocal()
    try:
        out = seed_demo(s)
        s.commit()
        print("Demo seed:", out)
    finally:
        s.close()


if __name__ == "__main__":
    main()
