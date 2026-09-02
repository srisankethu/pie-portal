"""The quote workspace: drafts that persist, are numbered, are shared, and
start with no customer.

Three defects with one cause — nothing durable held a quote — and these are
the tests that pin the fix from the outside:

* the number was ``QB-`` plus the clock modulo 100000, so ``QB-37491`` on a
  desk's first quote of the day; it is a per-organization sequence now;
* every quote opened against whichever customer the last draft carried, and
  an empty customer was unrepresentable; empty is the default now, and the
  customer is chosen — or changed — in place;
* a draft lived in one process's memory and one browser's ``localStorage``;
  it is a row now, on every desk's list, that a restart does not forget.

Same fixture shape as ``test_quote_flow``: the quote endpoints on their own
database, signed in through the product's only login.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
import piesupport
from app import quote_workspace
from app.db import get_session
from app.domain import models  # noqa: F401  (populate metadata)
from app.routers import admin, platform_auth, quote
from app.seed import SEED_PASSWORD, ensure_org_and_users, provision_organization
from app.store import Line

OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"
ORG = "org_pie"
COMPANY = piesupport.company_id("cx_quote_workspace")
#: The same test conftest.py runs: most of this file needs no engine, and the
#: one intake test is marked ``requires_pie`` and skipped without it.
PIE_AVAILABLE = (Path(os.environ["PIE_PARSER_ROOT"]) / "tools" / "resolve_rfq.py").exists()


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id=COMPANY, organization_id=ORG,
                                label="SLS Engineers", zoho_organization_id="z1"))
    s.commit()
    s.close()
    if PIE_AVAILABLE:
        piesupport.give_company_a_catalogue(COMPANY)

    api = FastAPI()
    api.include_router(platform_auth.router)
    api.include_router(quote.router)
    api.include_router(admin.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    api.dependency_overrides[get_session] = _override
    tc = TestClient(api)
    tc.Maker = Maker
    return tc


def _hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def owner(client):
    return _hdr(client, OWNER)


@pytest.fixture()
def sales(client):
    return _hdr(client, SALES)


def _line(id_: str, code: str = "2001174", price: float | None = 500.0) -> Line:
    return Line(id=id_, raw=f"{code} x10", reqCode=code, reqDesc=code, reqQty=10,
                rel="EXACT", supplyCode=code, candidates=[], outcome="OK",
                semantics="EXACT", inBooks=True, listPrice=520.0, quoted=price,
                priceSource="USER" if price is not None else "LIST")


def _seed_lines(client, quote_id: str, *lines: Line) -> None:
    """Put lines on a draft without the engine, straight through the workspace."""
    with client.Maker() as s:
        q = quote_workspace.load(s, ORG, quote_id)
        q.lines.extend(lines)
        quote_workspace.save(s, q, None)
        s.commit()


# ── the number ───────────────────────────────────────────────────────────────
def test_quotes_are_numbered_in_sequence_per_organization(client, owner):
    """QB-0001, QB-0002, QB-0003 — not the clock."""
    numbers = [client.post("/api/v1/quotes", json={}, headers=owner).json()["number"]
               for _ in range(3)]
    assert numbers == ["QB-0001", "QB-0002", "QB-0003"], numbers


def test_another_organization_starts_its_own_sequence(client, owner):
    client.post("/api/v1/quotes", json={}, headers=owner)
    client.post("/api/v1/quotes", json={}, headers=owner)

    s = client.Maker()
    provision_organization(s, name="Rival Distributors", owner_email="o@rival.example",
                           owner_name="Rival Owner", org_id="org_rival",
                           password=SEED_PASSWORD, must_change_password=False)
    s.add(models.ZohoConnection(connection_id="cx_rival", organization_id="org_rival",
                                label="Rival", zoho_organization_id="z9"))
    s.commit()
    s.close()

    rival = _hdr(client, "o@rival.example")
    assert client.post("/api/v1/quotes", json={}, headers=rival).json()["number"] == "QB-0001"
    # And it did not disturb ours.
    assert client.post("/api/v1/quotes", json={}, headers=owner).json()["number"] == "QB-0003"


def test_a_removed_draft_does_not_give_its_number_back(client, owner):
    """A number is minted once. Reusing QB-0002 after the first QB-0002 was
    removed would put two quotes behind one number in anybody's notes — which
    is why "remove" archives the row rather than deleting it."""
    first = client.post("/api/v1/quotes", json={}, headers=owner).json()
    second = client.post("/api/v1/quotes", json={}, headers=owner).json()
    assert client.delete(f"/api/v1/quotes/{second['id']}", headers=owner).status_code == 200
    third = client.post("/api/v1/quotes", json={}, headers=owner).json()
    assert [first["number"], second["number"], third["number"]] == [
        "QB-0001", "QB-0002", "QB-0003"]


def test_the_reference_still_carries_a_tail_beyond_the_number(client, owner):
    """The ERP idempotency key is not the bare number: two tenants can both be
    QB-0001, and a shared ledger must tell them apart."""
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    assert q["reference"].startswith(q["number"] + "-")
    assert len(q["reference"]) > len(q["number"]) + 1


# ── the customer ─────────────────────────────────────────────────────────────
def test_a_new_quote_has_no_customer(client, owner):
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    assert q["customer"] == ""
    assert q["customerId"] is None
    got = client.get(f"/api/v1/quotes/{q['id']}", headers=owner).json()
    assert got["customer"] == "", "the placeholder is back"


def test_the_customer_is_chosen_in_place(client, owner):
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    r = client.put(f"/api/v1/quotes/{q['id']}/customer",
                   json={"customer": "Pitti Engineering", "customer_id": "c-pitti"},
                   headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["customer"] == "Pitti Engineering"
    assert r.json()["customerId"] == "c-pitti"
    # Same quote, same number — not a new one.
    assert r.json()["id"] == q["id"] and r.json()["number"] == q["number"]
    got = client.get(f"/api/v1/quotes/{q['id']}", headers=owner).json()
    assert got["customer"] == "Pitti Engineering"


def test_an_empty_customer_cannot_be_chosen(client, owner):
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    r = client.put(f"/api/v1/quotes/{q['id']}/customer",
                   json={"customer": "   "}, headers=owner)
    assert r.status_code == 400


def test_a_quote_with_no_customer_cannot_be_sent(client, owner):
    """The send names why rather than writing a document for nobody."""
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    _seed_lines(client, q["id"], _line("L1"))
    r = client.post(f"/api/v1/quotes/{q['id']}/estimate", headers=owner).json()
    assert r["ok"] is False
    assert "customer" in r["message"].lower()


@pytest.mark.requires_pie
def test_choosing_a_customer_re_resolves_the_lines_and_keeps_typed_prices(client, owner):
    """The RFQ was pasted first, the customer named second — the ordinary
    order. The lines are resolved again for the customer, and the price the
    desk typed stays where the same product came back."""
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    q = client.post(f"/api/v1/quotes/{q['id']}/intake", json={"text": "2001174, 20"},
                    headers=owner).json()
    line = q["lines"][0]
    assert line["rel"] == "EXACT"
    client.post(f"/api/v1/quotes/{q['id']}/lines/{line['id']}/price",
                json={"price": 431}, headers=owner)

    r = client.put(f"/api/v1/quotes/{q['id']}/customer",
                   json={"customer": "Pitti Engineering"}, headers=owner)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["customer"] == "Pitti Engineering"
    assert len(body["lines"]) == 1
    after = body["lines"][0]
    assert after["id"] == line["id"], "the line kept its id — snapshots key on it"
    assert after["rel"] == "EXACT"
    assert after["quoted"] == 431 and after["priceSource"] == "USER"
    assert "1 price(s) you typed kept" in body["note"]


# ── the workspace ────────────────────────────────────────────────────────────
def test_the_list_is_shared_across_the_organization(client, owner, sales):
    mine = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=sales).json()
    theirs = client.post("/api/v1/quotes", json={}, headers=owner).json()

    for hdr in (owner, sales):
        rows = client.get("/api/v1/quotes", headers=hdr).json()["quotes"]
        assert {r["id"] for r in rows} == {mine["id"], theirs["id"]}
    by_id = {r["id"]: r for r in client.get("/api/v1/quotes", headers=owner).json()["quotes"]}
    assert by_id[mine["id"]]["createdBy"], "the row says whose it is"
    assert by_id[mine["id"]]["customer"] == "Pitti"
    assert by_id[theirs["id"]]["customer"] == ""
    assert by_id[theirs["id"]]["readiness"] == "EMPTY"


def test_the_list_carries_no_cost_or_margin(client, owner):
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=owner).json()
    ln = _line("L1")
    ln.cost = 300.0
    _seed_lines(client, q["id"], ln)
    row = client.get("/api/v1/quotes", headers=owner).json()["quotes"][0]
    flat = " ".join(row.keys()).lower()
    assert "cost" not in flat and "margin" not in flat and "floor" not in flat
    assert row["total"] > 0, "the selling total is the one figure the list carries"


def test_another_tenant_sees_nothing_of_ours(client, owner):
    client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=owner)
    s = client.Maker()
    provision_organization(s, name="Rival Distributors", owner_email="o@rival.example",
                           owner_name="Rival Owner", org_id="org_rival",
                           password=SEED_PASSWORD, must_change_password=False)
    s.commit()
    s.close()
    rival = _hdr(client, "o@rival.example")
    assert client.get("/api/v1/quotes", headers=rival).json()["quotes"] == []


def test_a_draft_survives_the_process_that_made_it(client, owner):
    """The whole point. A fresh session reads the row back into the same
    object the request that wrote it held — lines, price, cost and all."""
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=owner).json()
    ln = _line("L1")
    ln.cost = 300.0
    _seed_lines(client, q["id"], ln)

    with client.Maker() as s:
        again = quote_workspace.load(s, ORG, q["id"])
    assert again is not None
    assert again.number == q["number"]
    assert [x.id for x in again.lines] == ["L1"]
    assert again.lines[0].quoted == 500.0
    assert again.lines[0].cost == 300.0, "the server's copy keeps the cost"
    # And it is the API's answer too, on a request that shares nothing with
    # the one that wrote it.
    got = client.get(f"/api/v1/quotes/{q['id']}", headers=owner).json()
    assert got["lines"][0]["quoted"] == 500.0
    assert got["savedAt"], "the row says when it was written"


def test_a_salesperson_reads_the_persisted_line_without_its_cost(client, owner, sales):
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=owner).json()
    ln = _line("L1")
    ln.cost = 300.0
    _seed_lines(client, q["id"], ln)
    got = client.get(f"/api/v1/quotes/{q['id']}", headers=sales).json()
    assert "economics" not in got["lines"][0]
    assert "marginFloor" not in got


def test_removing_a_draft(client, owner):
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    assert client.delete(f"/api/v1/quotes/{q['id']}", headers=owner).status_code == 200
    assert client.get(f"/api/v1/quotes/{q['id']}", headers=owner).status_code == 404
    assert client.get("/api/v1/quotes", headers=owner).json()["quotes"] == []
    # Gone is gone; a second removal is a 404 like any unknown id.
    assert client.delete(f"/api/v1/quotes/{q['id']}", headers=owner).status_code == 404


def test_a_sent_quote_cannot_be_removed(client, owner):
    from app.commercial import quote_service

    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=owner).json()
    _seed_lines(client, q["id"], _line("L1"))
    with client.Maker() as s:
        quote_service.record_document(
            s, ORG, quote_id=q["id"], external_system="zoho", number="EST-1",
            line_count=1, fingerprint="whatever")
        s.commit()
    r = client.delete(f"/api/v1/quotes/{q['id']}", headers=owner)
    assert r.status_code == 409
    assert "sent" in r.json()["detail"].lower()


# ── readiness ────────────────────────────────────────────────────────────────
def _readiness(client, hdr, quote_id: str) -> str:
    rows = client.get("/api/v1/quotes", headers=hdr).json()["quotes"]
    return next(r for r in rows if r["id"] == quote_id)["readiness"]


def test_readiness_follows_the_send_gates_own_order(client, owner):
    from app.commercial import quote_service
    from app.store import store

    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    assert _readiness(client, owner, q["id"]) == "EMPTY"

    # An unpriced line is attention before it is anything else.
    _seed_lines(client, q["id"], _line("L1", price=None))
    assert _readiness(client, owner, q["id"]) == "NEEDS_ATTENTION"

    # Priced, but nobody has said whose quote it is.
    with client.Maker() as s:
        draft = quote_workspace.load(s, ORG, q["id"])
        draft.lines[0].quoted, draft.lines[0].priceSource = 500.0, "USER"
        quote_workspace.save(s, draft, None)
        s.commit()
    assert _readiness(client, owner, q["id"]) == "NO_CUSTOMER"

    # Named through the workspace rather than the endpoint: the endpoint
    # re-resolves the lines against the catalogue (its own test above), and
    # this test is about the order of the gates, on a line it controls.
    with client.Maker() as s:
        draft = quote_workspace.load(s, ORG, q["id"])
        draft.customer = "Pitti"
        quote_workspace.save(s, draft, None)
        s.commit()
    # No snapshot has judged this price and the line carries no cost, so the
    # gate has nothing to hold it on — READY is the send's own answer here too.
    assert _readiness(client, owner, q["id"]) == "READY"

    # Sent, for exactly this content.
    with client.Maker() as s:
        draft = quote_workspace.load(s, ORG, q["id"])
        quote_service.record_document(
            s, ORG, quote_id=q["id"], external_system="zoho", number="EST-1",
            line_count=1, fingerprint=store.priced_fingerprint(draft))
        s.commit()
    assert _readiness(client, owner, q["id"]) == "SENT"

    # Re-priced since: the document no longer covers it.
    client.post(f"/api/v1/quotes/{q['id']}/lines/L1/price", json={"price": 480},
                headers=owner)
    assert _readiness(client, owner, q["id"]) == "READY"


def test_readiness_reports_the_approval_the_gate_is_waiting_on(client, owner):
    """A line the screen shows below the floor needs approval, and the list
    says so — then says it is waiting once a request has been raised."""
    from app.domain.enums import ApprovalKind, ApprovalStatus

    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=owner).json()
    ln = _line("L1", price=310.0)
    ln.cost = 300.0   # 3% margin: below any floor the policy ships with
    _seed_lines(client, q["id"], ln)
    assert _readiness(client, owner, q["id"]) == "NEEDS_APPROVAL"

    with client.Maker() as s:
        s.add(models.ApprovalRequest(
            organization_id=ORG, kind=ApprovalKind.QUOTE_LINE_PRICE.value,
            status=ApprovalStatus.PENDING.value, subject_id=q["id"],
            subject_line_id="L1", requested_by_user_id="usr_owner"))
        s.commit()
    assert _readiness(client, owner, q["id"]) == "AWAITING_APPROVAL"


def test_readiness_is_ready_when_the_policy_does_not_require_approval(client, owner):
    from app import approvals

    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=owner).json()
    ln = _line("L1", price=310.0)
    ln.cost = 300.0
    _seed_lines(client, q["id"], ln)
    with client.Maker() as s:
        approvals.get_policy(s, ORG).require_approval_for_quotes = False
        s.commit()
    assert _readiness(client, owner, q["id"]) == "READY"


# ── ownership ────────────────────────────────────────────────────────────────
MANAGER = "m.rao@pie.example"


@pytest.fixture()
def manager(client):
    return _hdr(client, MANAGER)


def _second_salesperson(client) -> dict:
    """Another salesperson in the same organization, signed in."""
    from app import memberships
    from app.domain.enums import Role
    from app.passwords import hash_password

    s = client.Maker()
    s.add(models.User(user_id="usr_iyer", organization_id=ORG,
                      email="k.iyer@pie.example", name="K. Iyer",
                      role="SALESPERSON", password_hash=hash_password(SEED_PASSWORD),
                      active=True, must_change_password=False))
    s.flush()
    memberships.add_member(s, organization_id=ORG, user_id="usr_iyer",
                           role=Role.SALESPERSON)
    s.commit()
    s.close()
    return _hdr(client, "k.iyer@pie.example")


def test_whoever_starts_a_quote_owns_it(client, sales):
    q = client.post("/api/v1/quotes", json={}, headers=sales).json()
    assert q["owner"]["id"] == "usr_sales"
    assert q["owner"]["name"]
    assert q["canEdit"] is True
    row = client.get("/api/v1/quotes", headers=sales).json()["quotes"][0]
    assert row["ownerId"] == "usr_sales" and row["owner"] and row["canEdit"] is True


def test_a_colleague_may_read_but_not_change_somebody_elses_quote(client, sales):
    other = _second_salesperson(client)
    q = client.post("/api/v1/quotes", json={}, headers=sales).json()
    _seed_lines(client, q["id"], _line("L1"))

    got = client.get(f"/api/v1/quotes/{q['id']}", headers=other)
    assert got.status_code == 200
    assert got.json()["canEdit"] is False
    assert client.get("/api/v1/quotes", headers=other).json()["quotes"][0]["canEdit"] is False

    refusals = [
        client.post(f"/api/v1/quotes/{q['id']}/lines/L1/price", json={"price": 1},
                    headers=other),
        client.post(f"/api/v1/quotes/{q['id']}/intake", json={"text": "2001174, 1"},
                    headers=other),
        client.put(f"/api/v1/quotes/{q['id']}/customer", json={"customer": "X"},
                   headers=other),
        client.put(f"/api/v1/quotes/{q['id']}/fields", json={"fields": {}},
                   headers=other),
        client.delete(f"/api/v1/quotes/{q['id']}/lines/L1", headers=other),
        client.post(f"/api/v1/quotes/{q['id']}/discount",
                    json={"lineIds": ["L1"], "percent": 5}, headers=other),
        client.post(f"/api/v1/quotes/{q['id']}/estimate", headers=other),
        client.delete(f"/api/v1/quotes/{q['id']}", headers=other),
    ]
    for r in refusals:
        assert r.status_code == 403, (r.request.url, r.status_code, r.text)
        assert "belongs to" in r.json()["detail"]
    # And nothing moved.
    assert client.get(f"/api/v1/quotes/{q['id']}", headers=sales).json()["lines"][0]["quoted"] == 500.0


def test_a_manager_may_change_any_quote_while_the_policy_allows(client, sales, manager):
    from app import approvals

    q = client.post("/api/v1/quotes", json={}, headers=sales).json()
    _seed_lines(client, q["id"], _line("L1"))
    r = client.post(f"/api/v1/quotes/{q['id']}/lines/L1/price", json={"price": 450},
                    headers=manager)
    assert r.status_code == 200 and r.json()["canEdit"] is True

    with client.Maker() as s:
        approvals.get_policy(s, ORG).managers_may_edit_any_quote = False
        s.commit()
    r = client.post(f"/api/v1/quotes/{q['id']}/lines/L1/price", json={"price": 440},
                    headers=manager)
    assert r.status_code == 403
    assert "or a manager" not in r.json()["detail"]
    assert client.get(f"/api/v1/quotes/{q['id']}", headers=manager).json()["canEdit"] is False


def test_the_owner_can_hand_the_quote_over(client, sales):
    other = _second_salesperson(client)
    q = client.post("/api/v1/quotes", json={}, headers=sales).json()
    members = client.get("/api/v1/quotes/assignees", headers=sales).json()["members"]
    new_owner = next(m for m in members if m["name"] == "K. Iyer")

    r = client.put(f"/api/v1/quotes/{q['id']}/owner", json={"user_id": new_owner["id"]},
                   headers=sales)
    assert r.status_code == 200, r.text
    assert r.json()["owner"]["id"] == new_owner["id"]
    assert "K. Iyer" in r.json()["note"]
    # The roles have swapped exactly.
    assert client.get(f"/api/v1/quotes/{q['id']}", headers=sales).json()["canEdit"] is False
    assert client.get(f"/api/v1/quotes/{q['id']}", headers=other).json()["canEdit"] is True
    # A stranger to the organization cannot be given it.
    r = client.put(f"/api/v1/quotes/{q['id']}/owner", json={"user_id": "nobody"},
                   headers=other)
    assert r.status_code == 400


# ── fields ───────────────────────────────────────────────────────────────────
def test_an_organization_starts_with_the_builtin_fields_none_required(client, owner):
    r = client.get("/api/v1/quotes/field-definitions", headers=owner).json()["fields"]
    assert [f["key"] for f in r] == ["customer_reference", "valid_until", "payment_terms",
                                     "delivery_terms", "notes"]
    assert all(f["builtin"] and not f["required"] for f in r)


def test_an_owner_makes_fields_mandatory_and_adds_custom_ones(client, owner, sales):
    admin = client.get("/api/v1/admin/quote-fields", headers=owner).json()
    assert admin["can_manage"] is True
    specs = admin["fields"]
    specs[0]["required"] = True                              # customer reference
    specs.append({"label": "Incoterm", "kind": "CHOICE", "required": True,
                  "choices": ["EXW", "FOB", "CIF"]})
    specs.append({"label": "Site contact", "kind": "TEXT"})
    r = client.put("/api/v1/admin/quote-fields", json={"fields": specs}, headers=owner)
    assert r.status_code == 200, r.text
    keys = [f["key"] for f in r.json()["fields"]]
    assert keys[-2:] == ["incoterm", "site_contact"]

    # A salesperson sees the same definitions and may not edit them.
    seen = client.get("/api/v1/quotes/field-definitions", headers=sales).json()["fields"]
    assert [f["key"] for f in seen] == keys
    assert client.put("/api/v1/admin/quote-fields", json={"fields": specs},
                      headers=sales).status_code == 403

    # The quote knows which mandatory details it is missing, and the send
    # refuses by name until they are answered.
    q = client.post("/api/v1/quotes", json={"customer": "Pitti"}, headers=sales).json()
    assert q["missingFields"] == ["Customer reference", "Incoterm"]
    _seed_lines(client, q["id"], _line("L1"))
    assert _readiness(client, sales, q["id"]) == "MISSING_DETAILS"
    sent = client.post(f"/api/v1/quotes/{q['id']}/estimate", headers=sales).json()
    assert sent["ok"] is False and "Customer reference, Incoterm" in sent["message"]

    bad = client.put(f"/api/v1/quotes/{q['id']}/fields",
                     json={"fields": {"incoterm": "DDP"}}, headers=sales)
    assert bad.status_code == 400 and "Incoterm must be one of" in bad.json()["detail"]

    r = client.put(f"/api/v1/quotes/{q['id']}/fields",
                   json={"fields": {"customer_reference": "PO-778", "incoterm": "FOB",
                                    "valid_until": "2026-10-01", "unknown": "x"}},
                   headers=sales)
    assert r.status_code == 200, r.text
    assert r.json()["fields"] == {"customer_reference": "PO-778", "incoterm": "FOB",
                                  "valid_until": "2026-10-01"}
    assert r.json()["missingFields"] == []
    assert _readiness(client, sales, q["id"]) == "READY"


def test_a_removed_custom_field_keeps_its_value_on_old_drafts(client, owner):
    specs = client.get("/api/v1/admin/quote-fields", headers=owner).json()["fields"]
    specs.append({"label": "Site contact", "kind": "TEXT"})
    client.put("/api/v1/admin/quote-fields", json={"fields": specs}, headers=owner)
    q = client.post("/api/v1/quotes", json={}, headers=owner).json()
    client.put(f"/api/v1/quotes/{q['id']}/fields",
               json={"fields": {"site_contact": "Ravi"}}, headers=owner)

    r = client.put("/api/v1/admin/quote-fields", json={"fields": specs[:-1]}, headers=owner)
    assert [f["key"] for f in r.json()["fields"]] == [s["key"] for s in specs[:-1]]
    assert client.get(f"/api/v1/quotes/{q['id']}", headers=owner).json()["fields"] == {
        "site_contact": "Ravi"}


def test_a_field_definition_is_refused_with_the_reason(client, owner):
    specs = client.get("/api/v1/admin/quote-fields", headers=owner).json()["fields"]
    r = client.put("/api/v1/admin/quote-fields",
                   json={"fields": specs + [{"label": "Mode", "kind": "CHOICE"}]},
                   headers=owner)
    assert r.status_code == 400 and "at least one option" in r.json()["detail"]
    r = client.put("/api/v1/admin/quote-fields",
                   json={"fields": specs + [{"label": "", "kind": "TEXT"}]}, headers=owner)
    assert r.status_code == 400
