"""The self-funding endpoint: who may read it, and what the screen is told.

The arithmetic lives in ``test_self_funding``. What a router can get wrong on
its own is the scope — this is entity economics, not a commercial figure a sales
manager works from — and whether a refusal survives the trip to the client as a
refusal rather than arriving as an empty panel with a zero in it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.commercial import policy
from app.domain import models

MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"
OWNER = "s.menon@pie.example"

#: Closed by the time any of this is read, so it is a candidate year.
CLOSED_FY = "FY2024-25"


def _api(session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import insight as insight_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD, ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight_router.router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    def token(email: str) -> dict:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": SEED_PASSWORD})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    return client, token


def _org() -> str:
    from app.config import settings
    return settings.DEFAULT_ORG_ID


def _entity(session, connection_id: str, label: str):
    from app.seed import ensure_org_and_users

    # The connection row references the organization, which a bare session
    # does not hold. Idempotent, and it flushes — so this also serves the
    # tests that never build the HTTP client.
    ensure_org_and_users(session)
    session.add(models.ZohoConnection(
        connection_id=connection_id, organization_id=_org(), label=label,
        zoho_organization_id=f"zoho-{connection_id}"))
    customer = models.Customer(
        organization_id=_org(), external_id=f"cust-{connection_id}",
        name=f"Customer of {label}", connector="zoho",
        connection_id=connection_id)
    session.add(customer)
    session.flush()
    return customer


def _product(session):
    row = models.Product(organization_id=_org(), external_id="p-1",
                         name="Carbide insert", connector="zoho")
    session.add(row)
    session.flush()
    return row


def _sale(session, customer, product, on: date, revenue: str, ref: str):
    session.add(models.SalesTxn(
        organization_id=_org(), external_ref=ref, customer_id=customer.customer_id,
        product_id=product.product_id, date=on, qty=Decimal(1),
        unit_price=Decimal(revenue), line_revenue=Decimal(revenue)))
    session.flush()


@pytest.fixture()
def book(session):
    """Two trading entities, with revenue either side of the FY2024-25 line."""
    sls = _entity(session, "cx-sls", "SLS Engineers")
    fourU = _entity(session, "cx-4u", "4U Precision")
    item = _product(session)
    _sale(session, sls, item, date(2023, 9, 1), "6000000", "s-1")
    _sale(session, fourU, item, date(2023, 9, 1), "4000000", "s-2")
    _sale(session, sls, item, date(2024, 9, 1), "8000000", "s-3")
    _sale(session, fourU, item, date(2024, 9, 1), "6000000", "s-4")
    session.commit()
    return sls, fourU


def _confirm(session, **by_entity: str):
    policy.save_for_org(session, _org(), {"retained_pat": [
        [eid, CLOSED_FY, amount] for eid, amount in by_entity.items()]})
    session.commit()


# ── scope ───────────────────────────────────────────────────────────────────
def test_a_salesperson_cannot_read_entity_economics(session, book):
    client, token = _api(session)

    assert client.get("/api/v1/insight/self-funding",
                      headers=token(SALES)).status_code == 403


def test_a_sales_manager_cannot_read_it_either(session, book):
    """Manager-or-owner is the wrong line here. What three legal entities kept
    after tax is the owner's figure, not a commercial one a manager works from."""
    client, token = _api(session)

    assert client.get("/api/v1/insight/self-funding",
                      headers=token(MANAGER)).status_code == 403


def test_the_owner_may_read_it(session, book):
    client, token = _api(session)

    assert client.get("/api/v1/insight/self-funding",
                      headers=token(OWNER)).status_code == 200


# ── the envelope ────────────────────────────────────────────────────────────
def test_the_response_is_stamped_with_the_policy_that_produced_it(session, book):
    client, token = _api(session)
    _confirm(session, **{"cx-sls": "900000", "cx-4u": "500000"})

    body = client.get("/api/v1/insight/self-funding",
                      headers=token(OWNER)).json()

    assert body["thresholds_version"].startswith("ci_")
    assert body["currency"]
    assert body["thresholds_version"] == policy.load_for_org(
        session, _org()).version


def test_confirming_a_figure_changes_the_version_the_reading_carries(session, book):
    """A reading rendered before and after an owner corrects a figure has to be
    distinguishable, which is the whole job of the version stamp."""
    client, token = _api(session)
    before = client.get("/api/v1/insight/self-funding",
                        headers=token(OWNER)).json()["thresholds_version"]
    _confirm(session, **{"cx-sls": "900000", "cx-4u": "500000"})
    after = client.get("/api/v1/insight/self-funding",
                       headers=token(OWNER)).json()["thresholds_version"]

    assert before != after


# ── silence, and what the screen is told about it ───────────────────────────
def test_an_unconfirmed_book_reaches_the_screen_as_a_refusal(session, book):
    client, token = _api(session)

    body = client.get("/api/v1/insight/self-funding",
                      headers=token(OWNER)).json()

    assert body["confirmed"] is False
    assert body["verdict"] == "UNKNOWN"
    # The screen's empty state is the reason, not a blank panel.
    assert body["empty_reason"] == body["blocked_by"]
    assert "Profit & Loss" in body["empty_reason"]
    assert sorted(body["missing_entities"]) == ["4U Precision", "SLS Engineers"]
    # And no number at all — not a zero, and nothing derived from gross profit.
    assert "retained_pat" not in body


def test_a_half_answered_year_names_the_entity_still_missing(session, book):
    client, token = _api(session)
    _confirm(session, **{"cx-sls": "900000"})

    body = client.get("/api/v1/insight/self-funding",
                      headers=token(OWNER)).json()

    assert body["confirmed"] is False
    assert body["missing_entities"] == ["4U Precision"]
    assert "retained_pat" not in body


def test_a_fully_confirmed_year_reads_across_both_entities(session, book):
    client, token = _api(session)
    _confirm(session, **{"cx-sls": "900000", "cx-4u": "500000"})

    body = client.get("/api/v1/insight/self-funding",
                      headers=token(OWNER)).json()

    assert body["confirmed"] is True
    assert body["empty_reason"] is None
    assert body["financial_year"] == CLOSED_FY
    assert body["retained_pat"] == 1_400_000.0
    # 1.4 crore against 1.0 crore the year before.
    assert body["revenue"] == 14_000_000.0
    assert body["previous_revenue"] == 10_000_000.0
    assert body["revenue_growth"] == 4_000_000.0
    assert body["verdict"] == "UNDETERMINED"
    assert [e["label"] for e in body["entities"]] == [
        "4U Precision", "SLS Engineers"]
