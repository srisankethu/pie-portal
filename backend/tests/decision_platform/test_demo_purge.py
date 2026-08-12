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
