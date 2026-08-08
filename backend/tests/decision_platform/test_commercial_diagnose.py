"""Why the Customer × Item screen is empty — the funnel, and its verdict.

Several honest reasons produce an identical blank screen: nothing synced, bills
missing, metrics never built, history too thin, or genuinely nothing wrong. The
diagnostic exists to tell them apart, so the tests that matter are the ones
where it could send someone to fix the wrong thing.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal


from app.commercial.compute import recompute
from app.commercial.diagnose import diagnose
from app.domain import models
from app.seed import ensure_org_and_users

AS_OF = date(2026, 7, 1)


def _d(n: int) -> date:
    return AS_OF - timedelta(days=n)


def _org(session) -> str:
    return ensure_org_and_users(session)


def _customer_and_item(session, org):
    session.add(models.Customer(customer_id="c1", organization_id=org,
                                external_id="c1", name="Ashok Leyland"))
    session.add(models.Product(product_id="p1", organization_id=org,
                               external_id="p1", name="Drill 8mm"))


def _sales(session, org, n=6, price="420"):
    for i, days in enumerate([300, 240, 180, 120, 60, 20][:n]):
        q, p = Decimal("50"), Decimal(price)
        session.add(models.SalesTxn(
            organization_id=org, external_ref=f"INV{i}:1", customer_id="c1",
            product_id="p1", date=_d(days), qty=q, unit_price=p, line_revenue=q * p,
            rate=p, discount_percent=Decimal("0"),
            source_ref={"record_type": "invoice", "record_id": f"INV{i}"}))


def _bills(session, org, unit_cost="300"):
    session.add(models.CostRecord(
        organization_id=org, external_ref="B1:1", product_id="p1", date=_d(320),
        qty=Decimal("100"), unit_cost=Decimal(unit_cost),
        source_ref={"record_type": "bill", "record_id": "B1"}))


def test_nothing_synced_says_so_rather_than_blaming_the_analysis(session):
    org = _org(session)
    d = diagnose(session, org)
    assert d["sales_lines"] == 0
    assert "No invoice lines have been synced" in d["verdict"]


def test_invoices_without_metrics_points_at_the_backfill(session):
    """Data landed but the derived table was never built — a different fault
    from having no data, and a different fix."""
    org = _org(session)
    _customer_and_item(session, org)
    _sales(session, org)
    session.flush()

    d = diagnose(session, org)
    assert d["sales_lines"] == 6 and d["metric_rows"] == 0
    assert "backfill" in d["verdict"] or "recompute" in d["verdict"]


def test_invoices_but_no_bills_blames_the_bills_not_the_thresholds(session):
    """The most common real cause, and the one most easily misdiagnosed: with no
    purchase cost there is no margin, so nothing can ever be flagged."""
    org = _org(session)
    _customer_and_item(session, org)
    _sales(session, org)
    session.flush()
    recompute(session, org, as_of=AS_OF)
    session.flush()

    d = diagnose(session, org)
    assert d["cost_records"] == 0
    assert d["relationships"] == 1 and d["relationships_with_cost"] == 0
    assert d["rows_with_signals"] == 0
    assert "no bills were synced" in d["verdict"]
    assert "uncostable" in d["verdict"]


def test_a_cost_coverage_gap_is_not_reported_as_too_few_transactions(session):
    """The reason string 'cost known for only 0% of transactions' contains the
    word 'transactions'. Classifying on that first sent people to sync more
    invoices when what was missing was bills."""
    org = _org(session)
    _customer_and_item(session, org)
    _sales(session, org, n=6)          # comfortably above the transaction floor
    session.flush()
    recompute(session, org, as_of=AS_OF)
    session.flush()

    reasons = dict(diagnose(session, org)["top_reasons"])
    assert "cost missing on most transactions" in reasons
    assert "too few transactions" not in reasons


def test_thin_history_is_named_as_thin_history(session):
    org = _org(session)
    _customer_and_item(session, org)
    _bills(session, org)
    for i, days in enumerate([20, 15, 10]):     # three orders inside three weeks
        q, p = Decimal("50"), Decimal("420")
        session.add(models.SalesTxn(
            organization_id=org, external_ref=f"INV{i}:1", customer_id="c1",
            product_id="p1", date=_d(days), qty=q, unit_price=p, line_revenue=q * p,
            rate=p, discount_percent=Decimal("0"),
            source_ref={"record_type": "invoice", "record_id": f"INV{i}"}))
    session.flush()
    recompute(session, org, as_of=AS_OF)
    session.flush()

    d = diagnose(session, org)
    assert d["relationships_with_cost"] == 1, "cost is known — this is not that problem"
    assert "too little history" in dict(d["top_reasons"])
    assert "INSUFFICIENT" in d["verdict"] or "history" in d["verdict"]


def test_a_healthy_relationship_reports_no_fault(session):
    """Nothing flagged is a real answer, not a malfunction — and must not read
    like one."""
    org = _org(session)
    _customer_and_item(session, org)
    _bills(session, org)
    _sales(session, org)
    session.flush()
    recompute(session, org, as_of=AS_OF)
    session.flush()

    d = diagnose(session, org)
    assert d["relationships_with_cost"] == 1
    assert d["rows_with_signals"] == 0
    assert "not a fault" in d["verdict"]


def test_a_flagged_relationship_tells_you_where_to_look(session):
    org = _org(session)
    _customer_and_item(session, org)
    # cost rises, price does not — the cost-not-passed-through story
    session.add(models.CostRecord(
        organization_id=org, external_ref="B1:1", product_id="p1", date=_d(320),
        qty=Decimal("100"), unit_cost=Decimal("300"),
        source_ref={"record_type": "bill", "record_id": "B1"}))
    session.add(models.CostRecord(
        organization_id=org, external_ref="B2:1", product_id="p1", date=_d(100),
        qty=Decimal("100"), unit_cost=Decimal("380"),
        source_ref={"record_type": "bill", "record_id": "B2"}))
    _sales(session, org)
    session.flush()
    recompute(session, org, as_of=AS_OF)
    session.flush()

    d = diagnose(session, org)
    assert d["rows_with_signals"] >= 1
    assert "Items requiring attention" in d["verdict"]
    assert d["signal_counts"], "the verdict should name what was found"


def test_the_report_carries_no_prices_or_margins(session):
    """Safe to paste into a support conversation — counts and structure only."""
    import json

    org = _org(session)
    _customer_and_item(session, org)
    _bills(session, org)
    _sales(session, org, price="1234.56")
    session.flush()
    recompute(session, org, as_of=AS_OF)
    session.flush()

    blob = json.dumps(diagnose(session, org))
    assert "1234.56" not in blob and "1234" not in blob
    assert "300" not in blob.replace("thresholds_version", "")
