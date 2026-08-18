"""Share of wallet and the tender store, through the real endpoints.

The pure-function tests in ``test_wallet.py`` cover the ladder's arithmetic and
its refusals. What only an end-to-end test can reach is the wiring: that a lost
quote's *value* comes from the snapshot that was actually put in front of the
customer, that a NULL loss reason survives as unknown rather than becoming
"nobody bought it", that the published tendered value cannot be edited, and that
this whole view is readable by a salesperson because nothing in it is cost.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.domain import models
from app.routers import insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
AS_OF = date(2026, 7, 1)

MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _seed(s) -> None:
    s.add(models.Customer(customer_id="c1", organization_id=ORG, external_id="c1",
                          name="Acme Engineering", assigned_user_id="usr_sales"))
    s.add(models.Product(product_id="p1", organization_id=ORG, external_id="ITEM-900",
                         name="CNMG 120408-MP insert", uom="pcs"))
    for i, days in enumerate([200, 150, 100, 50]):
        q, p = Decimal("100"), Decimal("139")
        s.add(models.SalesTxn(
            organization_id=ORG, external_ref=f"INV-{i}:1", customer_id="c1",
            product_id="p1", date=_d(days), qty=q, unit_price=p,
            line_revenue=q * p, rate=p, discount_percent=Decimal("0"),
            source_ref={"record_type": "invoice", "record_id": f"INV-{i}"}))
    s.flush()


def _quote(s, quote_id: str, value: str, status: str,
           loss_reason: str | None = None) -> None:
    """One priced quote and the outcome it reached."""
    s.add(models.QuoteDecision(
        organization_id=ORG, quote_id=quote_id, quote_line_id=f"{quote_id}-L1",
        customer_id="c1", product_id="p1", quantity=Decimal("10"),
        line_revenue=Decimal(value), as_of=AS_OF))
    s.add(models.QuoteOutcome(
        organization_id=ORG, quote_id=quote_id, customer_id="c1",
        status=status, loss_reason=loss_reason))


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _wallet(c, email=MANAGER, customer_id="c1"):
    """The envelope splices the payload flat — see ``insight._envelope``."""
    r = c.get(f"/api/v1/insight/wallet/{customer_id}", headers=_hdr(c, email))
    assert r.status_code == 200, r.text
    return r.json()


def _add_quotes(client, rows):
    s = client.Maker()
    for row in rows:
        _quote(s, *row)
    s.commit()
    s.close()


def _tender(c, **over):
    body = {"tender_ref": "GEM/2026/B/0001", "customer": "Acme Engineering",
            "tendered_value": 1_000_000, "won_value": 250_000,
            "closed_on": "2026-05-01", "source": "https://gem.gov.in/bid/0001"}
    body.update(over)
    return c.post("/api/v1/insight/tenders", json=body, headers=_hdr(c, MANAGER))


# ── the default answer ──────────────────────────────────────────────────────
def test_a_customer_with_no_evidence_comes_back_unknown(client):
    data = _wallet(client)
    assert data["basis"] == "UNKNOWN"
    assert data["share_low"] is None and data["share_high"] is None
    assert data["refusal"]


def test_the_response_never_carries_a_midpoint(client):
    data = _wallet(client)
    assert "share" not in data and "midpoint" not in data


def test_a_salesperson_may_read_this(client):
    """Nothing in it is cost or margin, so withholding it would be theatre.

    Still scoped to their own accounts, though — the id is the only thing
    between a salesperson and the whole book, and ids travel.
    """
    data = _wallet(client, SALES)
    assert data["basis"] == "UNKNOWN"


# ── the bound, from real quote rows ─────────────────────────────────────────
def test_lost_quotes_are_valued_from_the_snapshot_that_was_quoted(client):
    _add_quotes(client, [
        ("qw", "55600", "WON", None),
        ("q1", "50000", "LOST", "PRICE"),
        ("q2", "50000", "LOST", "DELIVERY"),
        ("q3", "50000", "LOST", "COMPETITOR"),
    ])
    data = _wallet(client)
    assert data["basis"] == "BOUNDED_ASKS"
    # Revenue 55,600 against 150,000 lost → a ceiling of 55600/205600.
    assert data["share_low"] == 0.0
    assert data["share_high"] == pytest.approx(0.2704, abs=1e-4)
    assert sorted(data["evidence_refs"]) == ["q1", "q2", "q3"]


def test_a_loss_recorded_before_the_reason_existed_stays_unknown(client):
    """A NULL reason is not "the requirement died".

    Folding it that way would shrink the competitor's side of the denominator
    and overstate our share — the direction that flatters us.
    """
    _add_quotes(client, [
        ("qw", "55600", "WON", None),
        ("q1", "50000", "LOST", "PRICE"),
        ("q2", "50000", "LOST", "PRICE"),
        ("qold", "900000", "LOST", None),      # predates the field
    ])
    data = _wallet(client)
    # Two competitor losses is below the floor, so no bound — and the huge
    # unknown-kind loss did not silently make one possible either.
    assert data["basis"] == "UNKNOWN"
    assert "do not say" in data["refusal"]


def test_a_lost_quote_with_no_priced_snapshot_is_skipped_not_counted_at_zero(client):
    s = client.Maker()
    _quote(s, "qw", "55600", "WON", None)
    for i in range(3):
        _quote(s, f"q{i}", "50000", "LOST", "PRICE")
    # An outcome with no QuoteDecision behind it — nothing says what it was worth.
    s.add(models.QuoteOutcome(organization_id=ORG, quote_id="qghost",
                              customer_id="c1", status="LOST",
                              loss_reason="PRICE"))
    s.commit()
    s.close()
    data = _wallet(client)
    assert "qghost" not in data["evidence_refs"]
    assert data["share_high"] == pytest.approx(0.2704, abs=1e-4)


# ── the tender store ────────────────────────────────────────────────────────
def test_a_recorded_tender_gives_a_measured_share(client):
    assert _tender(client).status_code == 201
    data = _wallet(client)
    assert data["basis"] == "MEASURED_TENDER"
    assert data["share_low"] == data["share_high"] == 0.25
    assert any("off-tender" in c for c in data["caveats"])


def test_a_tender_without_a_source_is_refused(client):
    """The rung's whole claim is that both figures can be looked up."""
    r = _tender(client, source="   ")
    assert r.status_code == 422
    assert "looked up" in r.json()["detail"]


def test_the_published_tendered_value_cannot_be_edited(client):
    assert _tender(client).status_code == 201
    r = _tender(client, tendered_value=400_000)
    assert r.status_code == 409
    assert "not editable" in r.json()["detail"]


def test_an_award_larger_than_the_tender_is_refused(client):
    r = _tender(client, won_value=2_000_000)
    assert r.status_code == 422
    assert "above 100% share" in r.json()["detail"]


def test_an_award_can_be_filled_in_after_the_bid(client):
    assert _tender(client, won_value=None).status_code == 201
    assert _wallet(client)["basis"] == "UNKNOWN"
    assert _tender(client, won_value=250_000).status_code == 201
    assert _wallet(client)["basis"] == "MEASURED_TENDER"


def test_a_salesperson_cannot_record_a_tender(client):
    """A denominator anybody can type is a share anybody can move."""
    body = {"tender_ref": "GEM/X", "customer": "Acme Engineering",
            "tendered_value": 1000, "closed_on": "2026-05-01", "source": "x"}
    r = client.post("/api/v1/insight/tenders", json=body, headers=_hdr(client, SALES))
    assert r.status_code in (401, 403)


def test_a_tender_line_must_be_one_of_the_businesss_own(client):
    r = _tender(client, categories=["CUTTING_TOOLS", "SPACESHIPS"])
    assert r.status_code == 422
    assert "SPACESHIPS" in r.json()["detail"]
