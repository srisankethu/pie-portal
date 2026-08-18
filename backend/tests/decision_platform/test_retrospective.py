"""The first-run look-back, and the property that makes it worth showing.

A retrospective exists to be the first honest thing a newly connected book sees.
The failure mode it must not have is the one CLAUDE.md §1 names: reporting "no
findings" over a history nothing could be judged in, which states only true
numbers and tells the reader something false. Every test here is about keeping
"we looked and it is fine" separable from "we could not look".
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import entitlements
from app.db import get_session
from app.domain import models
from app.domain.enums import PlanTier
from app.routers import platform_auth
from app.routers import retrospective as retrospective_router
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.signals import retrospective
from app.signals.base import Withholding

from .signal_fixtures import (
    declining_customer,
    dormant_customer,
    margin_deterioration_product,
)

ORG = "org_pie"
OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"


def _insert(session, org, sales=(), costs=()):
    for s in sales:
        session.add(models.SalesTxn(
            organization_id=org, external_ref=s.external_ref,
            customer_id=s.customer_id, product_id=s.product_id, date=s.date,
            qty=s.qty, unit_price=s.unit_price, line_revenue=s.line_revenue,
            source_ref=s.source_ref))
    for c in costs:
        session.add(models.CostRecord(
            organization_id=org, external_ref=c.external_ref,
            product_id=c.product_id, date=c.date, qty=c.qty,
            unit_cost=c.unit_cost, source_ref=c.source_ref))
    session.flush()


@pytest.fixture()
def org(session):
    session.add(models.Organization(organization_id=ORG, name="PIE"))
    session.flush()


def _margin_view(report) -> dict:
    return next(d for d in report["detectors"]
                if d["detector"] == "MARGIN_DETERIORATION")


# ── the property the screen exists for ───────────────────────────────────────
def test_a_book_with_no_costs_is_not_reported_as_a_book_with_no_problems(
        session, org):
    """Invoices synced, bills not. The margin check finds nothing, necessarily.

    Reported as a bare count that is a clean bill of health, and it is the most
    likely first-run state there is — purchase bills are the half of a Zoho book
    that a distributor is least likely to have kept up to date.
    """
    sales, _ = margin_deterioration_product()
    _insert(session, ORG, sales=sales)
    report = retrospective.look_back(session, ORG)

    margin = _margin_view(report)
    assert margin["found"] == 0
    assert margin["clear"] == 0, "nothing was judged, so nothing is clear"
    assert margin["judged_share"] == 0.0
    assert [w["reason"] for w in margin["withheld"]] == [
        Withholding.NO_COST_ON_RECORD]
    assert report["verdict"] in (retrospective.UNEXAMINED, retrospective.PARTIAL)


def test_an_empty_book_is_unexamined_rather_than_clean(session, org):
    """Nothing synced at all. Every count is zero and none of them mean anything."""
    report = retrospective.look_back(session, ORG)

    assert report["verdict"] == retrospective.UNEXAMINED
    assert report["found"] == 0
    assert report["judged"] == 0
    assert report["judged_share"] is None, "no share exists over nothing considered"
    assert report["history"]["first_document"] is None
    assert report["history"]["detail"], "the absence has to name itself"


def test_the_verdict_is_computed_from_what_was_judged_not_what_was_found(
        session, org):
    """A book with real findings is still only PARTIAL if part of it was unjudgeable.

    The inverse — findings driving the verdict — is how a screen ends up
    confident because it happened to find something.
    """
    sales, costs = margin_deterioration_product()
    _insert(session, ORG, sales=declining_customer() + dormant_customer() + sales,
            costs=costs)
    report = retrospective.look_back(session, ORG)

    assert report["found"] > 0, "this book has real findings"
    assert report["judged"] < report["considered"]
    assert report["verdict"] == retrospective.PARTIAL
    assert "not the same as nothing being there" in report["verdict_detail"]


def test_the_history_span_is_read_from_the_rows_not_from_the_configured_intent(
        session, org):
    """`DEFAULT_HISTORY_MONTHS` is 18 and the landing page says "about 18 months".

    What a given book *has* is whatever the sync managed to pull, and claiming
    the configured intent over four months of invoices is a coverage boast
    standing on nothing — the same shape as the catalogue claim the landing
    page's own audit caught.
    """
    sales, costs = margin_deterioration_product()
    _insert(session, ORG, sales=sales, costs=costs)
    history = retrospective.look_back(session, ORG)["history"]

    spans = [s.date for s in sales] + [c.date for c in costs]
    assert history["first_document"] == min(spans).isoformat()
    assert history["last_document"] == max(spans).isoformat()
    assert history["months"] == round((max(spans) - min(spans)).days / 30.44, 1)
    assert history["sales_lines"] == len(sales)
    assert history["cost_lines"] == len(costs)


def test_every_withheld_reason_reaches_the_report_as_a_sentence(session, org):
    """A reason code that arrives without its sentence reaches a screen as a code."""
    sales, _ = margin_deterioration_product()
    _insert(session, ORG, sales=sales)
    for row in _margin_view(retrospective.look_back(session, ORG))["withheld"]:
        assert row["detail"] and row["detail"] != row["reason"]
        assert row["count"] >= 1


# ── role ─────────────────────────────────────────────────────────────────────
@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    entitlements.set_plan(s, ORG, PlanTier.INTELLIGENCE)
    sales, costs = margin_deterioration_product()
    _insert(s, ORG, sales=sales, costs=costs)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    # Mirrors main.py: gated with the other intelligence surfaces at inclusion.
    app.include_router(
        retrospective_router.router,
        dependencies=[Depends(entitlements.require_feature("intelligence"))])

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_salesperson_gets_no_body_at_all_from_the_retrospective(client):
    """A count of margin findings is a count of products whose margin fell.

    There is no salesperson-safe projection of a screen whose subject is that,
    so the refusal is the whole response — absent, not hidden in the browser.
    """
    r = client.get("/api/v1/retrospective", headers=_hdr(client, SALES))
    assert r.status_code == 403
    assert "MARGIN" not in r.text and "margin" not in r.text.lower()


def test_an_owner_reads_coverage_and_findings_together(client):
    body = client.get("/api/v1/retrospective", headers=_hdr(client, OWNER)).json()
    assert body["verdict"] in (retrospective.UNEXAMINED, retrospective.PARTIAL,
                               retrospective.EXAMINED)
    assert body["thresholds_version"]
    for detector in body["detectors"]:
        assert "considered" in detector and "judged_share" in detector
