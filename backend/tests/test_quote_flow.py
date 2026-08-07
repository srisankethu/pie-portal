"""End-to-end API flow: intake, resolution grid, role-gated economics, supply
selection, pricing, and estimate creation.

Both fixtures sign in through ``/api/v1/auth/login`` — the product's only login.
The Quote Builder used to have a second one at ``/api/auth/login`` with two
fixed demo accounts and no password check, so these tests could pass while the
identity on screen belonged to nobody: the browser signed in twice, and the
second sign-in decided whether cost and margin were sent.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.domain import models  # noqa: F401  (populate metadata)
from app.routers import platform_auth, quote
from app.seed import SEED_PASSWORD, ensure_org_and_users

OWNER = "s.menon@sanketh.in"
SALES = "r.nair@sanketh.in"


@pytest.fixture()
def client():
    """The quote endpoints on their own database.

    Same shape as `decision_platform/test_quote_intelligence_api.py`: an
    in-memory schema, the seeded org and users, and `get_session` overridden.
    These tests used to run against whatever `data/platform.db` happened to hold
    because the login they used consulted no database at all.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    api = FastAPI()
    api.include_router(platform_auth.router)
    api.include_router(quote.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    api.dependency_overrides[get_session] = _override
    return TestClient(api)


def _hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def sales_hdr(client):
    return _hdr(client, SALES)


@pytest.fixture()
def mgmt_hdr(client):
    return _hdr(client, OWNER)


def test_the_quote_builder_has_no_login_of_its_own():
    """The second door is gone from the real app, not merely unused by the client.

    While it existed, a browser could hold a Quote Builder session belonging to
    a demo account nobody had authenticated, and every economics decision on
    these endpoints was taken against *that* role.
    """
    from app.main import app as real_app

    assert not [r for r in real_app.routes
                if getattr(r, "path", "").startswith("/api/auth/")]


def test_auth_required(client):
    assert client.get("/api/quotes/nope").status_code == 401


def test_a_forged_token_is_refused(client):
    assert client.get(
        "/api/quotes/nope",
        headers={"Authorization": "Bearer not.a.real.token"}).status_code == 401


@pytest.mark.requires_pie
def test_intake_builds_resolution_grid(client, mgmt_hdr):
    q = client.post("/api/quotes", json={"customer": "Pitti Engineering"}, headers=mgmt_hdr).json()
    qid = q["id"]
    rfq = "2001174, 20\nCNMG 120408 KCP25  50\nXZ-CUSTOM-778-NOTREAL, 5"
    q = client.post(f"/api/quotes/{qid}/intake", json={"text": rfq}, headers=mgmt_hdr).json()
    assert q["summary"]["total"] == 3
    rels = {ln["reqCode"]: ln["rel"] for ln in q["lines"]}
    assert rels["2001174"] == "EXACT"
    assert any(ln["rel"] == "UNRESOLVED" for ln in q["lines"])
    # filter counts present
    assert set(q["filterCounts"]) >= {"ALL", "NEEDS", "UNRES", "SUBST"}


def test_economics_are_role_gated(client, sales_hdr, mgmt_hdr):
    qs = client.post("/api/quotes", json={"customer": "Pitti"}, headers=sales_hdr).json()
    qid = qs["id"]
    client.post(f"/api/quotes/{qid}/intake", json={"text": "2001174, 10"}, headers=sales_hdr)

    sales_view = client.get(f"/api/quotes/{qid}", headers=sales_hdr).json()
    mgmt_view = client.get(f"/api/quotes/{qid}", headers=mgmt_hdr).json()

    sline = sales_view["lines"][0]
    mline = mgmt_view["lines"][0]
    # sales client never receives economics (cost/margin/below_floor)
    assert "economics" not in sline
    assert sales_view["marginFloor"] is None
    # management sees full economics
    assert "economics" in mline
    assert "cost" in mline["economics"]


@pytest.mark.requires_pie
def test_supply_selection_and_pricing(client, mgmt_hdr):
    q = client.post("/api/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    q = client.post(f"/api/quotes/{qid}/intake",
                    json={"text": "CNMG 120408 KCP25 50"}, headers=mgmt_hdr).json()
    line = q["lines"][0]
    lid = line["id"]
    opts = client.get(f"/api/quotes/{qid}/lines/{lid}/options", headers=mgmt_hdr).json()
    assert opts["candidates"], "requirement should have candidates"
    alt = opts["candidates"][-1]["code"]
    q = client.post(f"/api/quotes/{qid}/lines/{lid}/supply",
                    json={"code": alt}, headers=mgmt_hdr).json()
    assert q["lines"][0]["supplyCode"] == alt
    # set a manual price
    q = client.post(f"/api/quotes/{qid}/lines/{lid}/price",
                    json={"price": 999}, headers=mgmt_hdr).json()
    assert q["lines"][0]["quoted"] == 999
    assert q["lines"][0]["lineTotal"] == 999 * 50


def test_estimate_blocked_by_technical_lines(client, mgmt_hdr):
    q = client.post("/api/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    client.post(f"/api/quotes/{qid}/intake",
                json={"text": "XZ-CUSTOM-778-NOTREAL, 5"}, headers=mgmt_hdr)
    est = client.post(f"/api/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False
    assert est["blockers"]


def _clean_quote(client, hdr) -> str:
    """A quote with one in-books, priced, technically-clean line."""
    q = client.post("/api/quotes", json={"customer": "Pitti"}, headers=hdr).json()
    qid = q["id"]
    client.post(f"/api/quotes/{qid}/intake", json={"text": "2001174, 10"}, headers=hdr)
    qd = client.get(f"/api/quotes/{qid}", headers=hdr).json()
    ln = qd["lines"][0]
    if ln["inBooks"] is False:
        client.post(f"/api/quotes/{qid}/lines/{ln['id']}/create-item", headers=hdr)
    if ln["quoted"] is None:
        client.post(f"/api/quotes/{qid}/lines/{ln['id']}/price",
                    json={"price": 500}, headers=hdr)
    return qid


# `test_sending_a_quote_requires_a_platform_identity_when_approvals_are_on` was
# here on main. It asserted a 403 when the browser sent the Quote Builder's login
# without the platform header — a case that cannot arise now that there is one
# identity, because the organization comes from the signed-in principal rather
# than from a header a caller can omit. The rule it protected is not weaker: it
# moved from "send the second header or be refused" to "there is no request
# without an organization on it".
@pytest.mark.requires_pie
def test_estimate_created_when_clean_and_nothing_needs_approval(client, mgmt_hdr):
    """The gate opens when no line on this quote asked for sign-off.

    It reads the organization off the signed-in principal, so there is no longer
    a header a caller can omit to be judged against a different org — or, as
    before, against the packaged default org with no approvals in it.
    """
    qid = _clean_quote(client, mgmt_hdr)
    est = client.post(f"/api/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is True, est
    assert est["estimateNumber"]
