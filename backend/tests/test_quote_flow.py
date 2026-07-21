"""End-to-end API flow: login, intake, resolution grid, role-gated economics,
supply selection, pricing, and estimate creation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _token(email: str) -> str:
    r = client.post("/api/auth/login", json={"email": email, "password": "x"})
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


def test_estimate_created_when_clean(mgmt_hdr):
    q = client.post("/api/quotes", json={"customer": "Pitti"}, headers=mgmt_hdr).json()
    qid = q["id"]
    client.post(f"/api/quotes/{qid}/intake", json={"text": "2001174, 10"}, headers=mgmt_hdr)
    # ensure the line is in books + priced (create item if needed)
    qd = client.get(f"/api/quotes/{qid}", headers=mgmt_hdr).json()
    ln = qd["lines"][0]
    if ln["inBooks"] is False:
        client.post(f"/api/quotes/{qid}/lines/{ln['id']}/create-item", headers=mgmt_hdr)
    if ln["quoted"] is None:
        client.post(f"/api/quotes/{qid}/lines/{ln['id']}/price", json={"price": 500}, headers=mgmt_hdr)
    est = client.post(f"/api/quotes/{qid}/estimate", headers=mgmt_hdr).json()
    # Only technical-status lines block; an in-books priced exact line should pass.
    if not est["ok"]:
        # if the seeded item happened to be NOT IN BOOKS (operational, not technical),
        # that does not block — so ok must be True here.
        assert est["ok"] is True, est
