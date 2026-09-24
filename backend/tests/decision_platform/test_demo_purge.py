"""Demo data must not linger once a real Zoho account is linked.

The demo seed exists so a fresh clone shows a working, decision-rich UI before
anyone connects Zoho. Once a real account is linked, those fabricated
customers/products/decisions have to go — otherwise a manager cannot tell a
demo decision from a real one, and the platform reports numbers that were
never true of the business.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.demo import purge_demo_seed, seed_demo
from app.domain import models


def test_purge_removes_every_demo_row(session):
    seed_demo(session)
    session.commit()
    assert session.query(models.Customer).count() == 5
    assert session.query(models.Decision).count() > 0
    assert session.query(models.AiCallLog).count() > 0, \
        "the demo seed runs the real AI pipeline, so it does leave telemetry"

    removed = purge_demo_seed(session, "org_pie")
    session.commit()

    assert removed["customers"] == 5
    assert removed["products"] == 4
    assert removed["decisions"] > 0
    assert removed["signals"] > 0
    assert removed["ai_call_logs"] > 0
    assert session.query(models.Customer).count() == 0
    assert session.query(models.Product).count() == 0
    assert session.query(models.SalesTxn).count() == 0
    assert session.query(models.CostRecord).count() == 0
    assert session.query(models.Signal).count() == 0
    assert session.query(models.Decision).count() == 0
    assert session.query(models.AiCallLog).count() == 0, \
        "left in place, demo runs would permanently skew the AI cost/health metrics"
    assert session.query(models.Decision).count() == 0
    # The quoting half of the demo goes with it: the platform quote the seed
    # minted, what it sent, how it ended, and the company it all belonged to.
    assert removed["quote_drafts"] == 1 and removed["quote_documents"] == 1
    assert removed["quote_outcomes"] == 1 and removed["zoho_connections"] == 1
    for table in (models.QuoteDraft, models.QuoteDocument, models.QuoteOutcome,
                  models.QuoteDoc, models.ErpQuoteLine, models.ZohoConnection):
        assert session.query(table).count() == 0, table.__tablename__


def test_purge_is_idempotent(session):
    seed_demo(session)
    session.commit()
    purge_demo_seed(session, "org_pie")
    session.commit()

    again = purge_demo_seed(session, "org_pie")
    assert all(v == 0 for v in again.values()), \
        f"a second purge must find nothing left to remove: {again}"


def test_purge_never_touches_a_real_customer_even_with_the_same_name(session):
    """Demo rows are identified by their fixed primary key, not by name — a
    real Zoho customer that happens to be called "Rane Madras" must survive."""
    real = models.Customer(organization_id="org_pie", external_id="60036630626-c-9001",
                           name="Rane Madras", status="ACTIVE")
    session.add(real)
    session.commit()

    seed_demo(session)
    session.commit()
    assert session.query(models.Customer).count() == 6   # 5 demo + 1 real, same name

    purge_demo_seed(session, "org_pie")
    session.commit()

    survivors = session.query(models.Customer).all()
    assert len(survivors) == 1
    assert survivors[0].customer_id == real.customer_id
    assert survivors[0].external_id == "60036630626-c-9001"


def test_purge_is_scoped_to_the_organization(session):
    """A demo dataset seeded under one org must not be reachable — or
    deletable — from another."""
    seed_demo(session)
    session.commit()

    removed = purge_demo_seed(session, "some_other_org")
    session.commit()

    assert all(v == 0 for v in removed.values())
    assert session.query(models.Customer).count() == 5, "untouched — wrong org"


def test_purge_does_not_touch_a_real_cost_record_on_a_shared_product_id_space(session):
    """A defensive check on the id-based matching: only the fixed demo product
    ids are touched, nothing keyed by a real Zoho item id."""
    real_product = models.Product(organization_id="org_pie", external_id="9001",
                                  name="Real Insert")
    session.add(real_product)
    session.flush()
    real_cost = models.CostRecord(organization_id="org_pie", external_ref="bill-real:1",
                                  product_id=real_product.product_id, date=date(2026, 1, 1),
                                  qty=Decimal("1"), unit_cost=Decimal("100"))
    session.add(real_cost)
    session.commit()

    seed_demo(session)
    session.commit()

    purge_demo_seed(session, "org_pie")
    session.commit()

    assert session.query(models.CostRecord).count() == 1
    assert session.query(models.CostRecord).one().cost_record_id == real_cost.cost_record_id



def test_the_demo_holds_a_quote_this_platform_sent_that_the_erp_then_accepted(session):
    """The lifecycle the demo shows from both sides. Sent from here into the
    demo company's book, accepted there: the workspace reads it WON with the
    ERP as the source, the document it became is joined by (system, company,
    id), and every demo document names the company."""
    from app import quote_workspace

    seed_demo(session)
    session.commit()

    rows = quote_workspace.list_drafts(session, "org_pie", user_id="usr_sales")
    assert len(rows) == 1
    row = rows[0]
    assert row["customer"] == "Pitti Engineering Ltd"
    assert row["company"] == "SLS Engineers (demo)"
    assert row["sent"]["number"] == "QT-DEMO-0002" and row["sent"]["current"] is True
    assert row["sent"]["erp"]["outcome"] == "WON"
    assert row["readiness"] == "WON"
    assert {d.connection_id for d in session.query(models.QuoteDoc)} == {"cx_demo"}
