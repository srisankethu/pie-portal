"""End-to-end API flow: login, intake, resolution grid, role-gated economics,
supply selection, pricing, and estimate creation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.seed import SEED_PASSWORD

client = TestClient(app)


def _token(email: str) -> str:
    r = client.post("/api/auth/login", json={"email": email, "password": "x"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _platform_token(email: str) -> str:
    """The org-scoped platform identity, which the commercial gate needs and the
    Quote Builder's own demo login does not carry."""
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture()
def sales_hdr():
    return {"Authorization": f"Bearer {_token('r.nair@sanketh.in')}"}


@pytest.fixture()
def mgmt_hdr():
    return {"Authorization": f"Bearer {_token('s.menon@sanketh.in')}"}


def test_login_rejects_unknown_account():
    r = client.post("/api/auth/login", json={"email": "nobody@x.com", "password": "x"})
    assert r.status_code == 401


def test_auth_required():
    assert client.get("/api/quotes/nope").status_code == 401


@pytest.mark.requires_pie
def test_intake_builds_resolution_grid(mgmt_hdr):
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


def test_economics_are_role_gated(sales_hdr, mgmt_hdr):
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
def test_supply_selection_and_pricing(mgmt_hdr):
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


def test_estimate_blocked_by_technical_lines(mgmt_hdr):
    q = client.post("/api/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    client.post(f"/api/quotes/{qid}/intake",
                json={"text": "XZ-CUSTOM-778-NOTREAL, 5"}, headers=mgmt_hdr)
    est = client.post(f"/api/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    assert est["ok"] is False
    assert est["blockers"]


def _clean_quote(hdr) -> str:
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


@pytest.mark.requires_pie
def test_sending_a_quote_requires_a_platform_identity_when_approvals_are_on(mgmt_hdr):
    """The Quote Builder's own login carries no organization, so it cannot be
    checked against an approval queue. Sending without the platform token would
    otherwise be the way around every approval in the product."""
    qid = _clean_quote(mgmt_hdr)
    r = client.post(f"/api/quotes/{qid}/estimate", headers=mgmt_hdr)
    assert r.status_code == 403
    assert "Decisions platform" in r.json()["detail"]


@pytest.mark.requires_pie
def test_estimate_created_when_clean_and_nothing_needs_approval(mgmt_hdr):
    """With a platform identity and no line requiring approval, the gate opens.

    Only technical-status lines block on the Quote Builder side; the commercial
    gate adds nothing when no snapshot on this quote asked for sign-off.
    """
    qid = _clean_quote(mgmt_hdr)
    hdr = dict(mgmt_hdr)
    hdr["X-Platform-Authorization"] = f"Bearer {_platform_token('s.menon@sanketh.in')}"
    est = client.post(f"/api/quotes/{qid}/estimate", headers=hdr).json()
    assert est["ok"] is True, est
    assert est["estimateNumber"]
