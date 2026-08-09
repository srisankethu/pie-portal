"""Role separation, the approval system, and owner administration.

Three things that used to be gaps rather than features:

- sign-in accepted any password, so the three roles were a display preference;
- "Escalate to management" set the decision to OVERRIDDEN and told nobody;
- a price below the floor was computed, flagged, recorded — and sent anyway.

The tests that matter here are the ones that fail if any of that comes back.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.domain import models
from app.domain.enums import DecisionStatus, Role
from app.passwords import hash_password, verify_password
from app.routers import admin, approvals as approvals_router, decisions, platform_auth
from app.routers import quote_intelligence
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_sanketh"
AS_OF = date(2026, 7, 1)

OWNER = "s.menon@sanketh.in"
MANAGER = "m.rao@sanketh.in"
SALES = "r.nair@sanketh.in"


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _seed(s) -> None:
    s.add(models.Customer(customer_id="c1", organization_id=ORG, external_id="c1",
                          name="Acme Engineering", assigned_user_id="usr_sales"))
    s.add(models.Product(product_id="p1", organization_id=ORG, external_id="ITEM-900",
                         name="CNMG 120408-MP insert", uom="pcs"))
    s.add(models.CostRecord(
        organization_id=ORG, external_ref="B1:1", product_id="p1", date=_d(200),
        qty=Decimal("100"), unit_cost=Decimal("124"),
        source_ref={"record_type": "bill", "record_id": "B1"}))
    for i, days in enumerate([300, 200, 100, 60, 30, 10]):
        q, p = Decimal("100"), Decimal("180")
        s.add(models.SalesTxn(
            organization_id=ORG, external_ref=f"INV-{i}:1", customer_id="c1",
            product_id="p1", date=_d(days), qty=q, unit_price=p, line_revenue=q * p,
            rate=p, discount_percent=Decimal("0"),
            source_ref={"record_type": "invoice", "record_id": f"INV-{i}"}))
    s.flush()


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    s.commit()
    s.close()

    app = FastAPI()
    for r in (platform_auth.router, admin.router, approvals_router.router,
              quote_intelligence.router, decisions.router):
        app.include_router(r)

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


def _login(c, email, password=SEED_PASSWORD):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password})


def _hdr(c, email, password=SEED_PASSWORD):
    r = _login(c, email, password)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── role separation is a boundary, not a display preference ─────────────────
def test_a_wrong_password_is_refused(client):
    """The regression that matters most. Sign-in previously accepted any
    non-empty password, so every role was one guessable address apart."""
    assert _login(client, OWNER, "anything").status_code == 401
    assert _login(client, OWNER, "").status_code == 401
    assert _login(client, OWNER).status_code == 200


def test_an_account_with_no_password_cannot_sign_in(client):
    """"No credential set" must fail closed. Failing open here would restore
    exactly the behaviour this replaced."""
    s = client.Maker()
    s.add(models.User(user_id="usr_new", organization_id=ORG, email="new@sanketh.in",
                      name="New", role=Role.OWNER.value, active=True))
    s.commit()
    s.close()
    assert _login(client, "new@sanketh.in", "anything").status_code == 401


def test_an_inactive_account_cannot_sign_in(client):
    s = client.Maker()
    u = s.get(models.User, "usr_sales")
    u.active = False
    s.commit()
    s.close()
    assert _login(client, SALES).status_code == 401


def test_failure_does_not_reveal_whether_the_account_exists(client):
    """Different messages tell an attacker which addresses are worth attacking."""
    unknown = _login(client, "nobody@sanketh.in", "whatever")
    wrong = _login(client, OWNER, "whatever")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_the_token_carries_the_role_from_the_user_row(client):
    for email, role in ((OWNER, "OWNER"), (MANAGER, "SALES_MANAGER"),
                        (SALES, "SALESPERSON")):
        assert _login(client, email).json()["role"] == role


def test_passwords_are_stored_hashed_not_in_plaintext(client):
    s = client.Maker()
    stored = s.get(models.User, "usr_owner").password_hash
    s.close()
    assert stored and SEED_PASSWORD not in stored
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password(SEED_PASSWORD, stored)
    assert not verify_password("wrong", stored)


def test_two_hashes_of_the_same_password_differ(client):
    """Unsalted hashing makes one crack break every account that shares it."""
    assert hash_password("the-same-password") != hash_password("the-same-password")


# ── owner as super admin ────────────────────────────────────────────────────
def test_only_an_owner_may_create_a_user(client):
    body = {"email": "new@sanketh.in", "name": "New Person", "role": "SALESPERSON"}
    assert client.post("/api/v1/admin/users", json=body,
                       headers=_hdr(client, SALES)).status_code == 403
    assert client.post("/api/v1/admin/users", json=body,
                       headers=_hdr(client, MANAGER)).status_code == 403
    r = client.post("/api/v1/admin/users", json=body, headers=_hdr(client, OWNER))
    assert r.status_code == 201, r.text


def test_a_created_user_can_sign_in_with_the_password_returned_once(client):
    r = client.post("/api/v1/admin/users",
                    json={"email": "new@sanketh.in", "name": "New", "role": "SALES_MANAGER"},
                    headers=_hdr(client, OWNER)).json()
    temp = r["temporary_password"]
    assert temp and len(temp) >= 10

    login = _login(client, "new@sanketh.in", temp)
    assert login.status_code == 200
    assert login.json()["role"] == "SALES_MANAGER"
    assert login.json()["must_change_password"] is True


def test_a_manager_may_see_the_team_but_not_change_it(client):
    r = client.get("/api/v1/admin/users", headers=_hdr(client, MANAGER))
    assert r.status_code == 200
    assert r.json()["can_manage"] is False
    assert len(r.json()["users"]) == 3


def test_a_salesperson_cannot_see_the_team_at_all(client):
    assert client.get("/api/v1/admin/users",
                      headers=_hdr(client, SALES)).status_code == 403


def test_an_owner_can_change_someone_elses_role(client):
    s = client.Maker()
    uid = s.get(models.User, "usr_sales").user_id
    s.close()
    r = client.patch(f"/api/v1/admin/users/{uid}", json={"role": "SALES_MANAGER"},
                     headers=_hdr(client, OWNER))
    assert r.status_code == 200 and r.json()["role"] == "SALES_MANAGER"
    assert r.json()["role_changed_by"] == "S. Menon", "role changes are attributed"


def test_nobody_changes_their_own_role(client):
    """An account takeover that can also promote itself is a different class of
    problem from one that cannot."""
    r = client.patch("/api/v1/admin/users/usr_owner", json={"role": "SALESPERSON"},
                     headers=_hdr(client, OWNER))
    assert r.status_code == 400


def test_the_last_owner_cannot_be_demoted_or_deactivated(client):
    """The alternative is a tenant nobody can administer."""
    owner = client.post("/api/v1/admin/users",
                        json={"email": "o2@sanketh.in", "name": "Second Owner",
                              "role": "OWNER"},
                        headers=_hdr(client, OWNER)).json()["user"]
    # two owners now — demoting one is fine
    assert client.patch(f"/api/v1/admin/users/{owner['user_id']}",
                        json={"role": "SALESPERSON"},
                        headers=_hdr(client, OWNER)).status_code == 200
    # back to one; the remaining owner cannot be removed by anyone
    s = client.Maker()
    s.add(models.User(user_id="usr_o3", organization_id=ORG, email="o3@sanketh.in",
                      name="Third", role=Role.OWNER.value, active=True,
                      password_hash=hash_password(SEED_PASSWORD)))
    s.commit()
    s.close()
    r = client.patch("/api/v1/admin/users/usr_owner", json={"active": False},
                     headers=_hdr(client, "o3@sanketh.in"))
    assert r.status_code == 200, "two active owners — removing one is allowed"
    r = client.patch("/api/v1/admin/users/usr_o3", json={"active": False},
                     headers=_hdr(client, "o3@sanketh.in"))
    assert r.status_code == 400, "and never your own account"


def test_changing_your_own_password_requires_the_current_one(client):
    hdr = _hdr(client, SALES)
    assert client.post("/api/v1/admin/me/password", headers=hdr,
                       json={"current_password": "wrong",
                             "new_password": "a-much-longer-one"}).status_code == 403
    r = client.post("/api/v1/admin/me/password", headers=hdr,
                    json={"current_password": SEED_PASSWORD,
                          "new_password": "a-much-longer-one"})
    assert r.status_code == 200
    assert _login(client, SALES, "a-much-longer-one").status_code == 200
    assert _login(client, SALES, SEED_PASSWORD).status_code == 401


def test_a_short_password_is_refused(client):
    r = client.post("/api/v1/admin/me/password", headers=_hdr(client, SALES),
                    json={"current_password": SEED_PASSWORD, "new_password": "short"})
    assert r.status_code == 400


def test_only_an_owner_edits_the_approval_policy(client):
    assert client.get("/api/v1/admin/policy",
                      headers=_hdr(client, MANAGER)).status_code == 200
    assert client.patch("/api/v1/admin/policy",
                        json={"require_approval_for_quotes": False},
                        headers=_hdr(client, MANAGER)).status_code == 403
    r = client.patch("/api/v1/admin/policy",
                     json={"require_approval_for_quotes": False},
                     headers=_hdr(client, OWNER))
    assert r.status_code == 200 and r.json()["require_approval_for_quotes"] is False


# ── the approval system ─────────────────────────────────────────────────────
def _line(price, line_id="L1", qty=100):
    return {"line_id": line_id, "product": "ITEM-900", "qty": qty,
            "proposed_price": price}


def _snapshot(c, email, price, quote_id="q1", line_id="L1"):
    r = c.post("/api/v1/quote-intelligence/snapshot",
               json={"quote_id": quote_id, "customer": "Acme Engineering",
                     "as_of": AS_OF.isoformat(), "lines": [_line(price, line_id)]},
               headers=_hdr(c, email))
    assert r.status_code == 201, r.text
    return r.json()


def _raise(c, email, price, quote_id="q1", line_id="L1", reason="Volume commitment"):
    return c.post("/api/v1/approvals/quote-line",
                  json={"quote_id": quote_id, "customer": "Acme Engineering",
                        "line_id": line_id, "product": "ITEM-900", "qty": 100,
                        "proposed_price": price, "reason": reason,
                        "reason_code": "VOLUME_COMMITMENT"},
                  headers=_hdr(c, email))


def test_a_price_within_policy_cannot_have_an_approval_raised_for_it(client):
    """A queue full of requests nobody needed is a queue nobody reads."""
    r = _raise(client, SALES, 180.0)
    assert r.status_code == 400
    assert "within policy" in r.json()["detail"]


def test_a_thin_price_raises_a_manager_level_request(client):
    r = _raise(client, SALES, 135.0)     # cost 124 → 8.1% margin, under the 12% floor
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "PENDING"
    assert body["required_authority"] == "MANAGER"
    assert body["can_decide"] is False, "the salesperson cannot decide their own"


def test_selling_below_cost_is_escalated_to_the_owner_not_the_manager(client):
    """A thin margin is a commercial judgement. Losing money on purpose is a
    different decision, and it belongs to a different person."""
    r = _raise(client, SALES, 100.0)     # under the ₹124 cost
    assert r.status_code == 201
    assert r.json()["required_authority"] == "OWNER"

    rid = r.json()["approval_request_id"]
    denied = client.post(f"/api/v1/approvals/{rid}/decide",
                         json={"status": "APPROVED"}, headers=_hdr(client, MANAGER))
    assert denied.status_code == 403
    assert "owner" in denied.json()["detail"].lower()

    allowed = client.post(f"/api/v1/approvals/{rid}/decide",
                          json={"status": "APPROVED"}, headers=_hdr(client, OWNER))
    assert allowed.status_code == 200 and allowed.json()["status"] == "APPROVED"


def test_a_manager_is_not_offered_an_approval_they_cannot_grant(client):
    """`can_decide` must reflect the rule the decide path actually enforces.

    It answered from role and authority alone, so a manager's own request came
    back `can_decide: true`, the card rendered an enabled Approve, and pressing it
    403'd with "You cannot approve your own request". The same mismatch inflated
    `pending_for_me`, which feeds the nav badge — so the badge counted work the
    manager was then refused.
    """
    rid = _raise(client, MANAGER, 135.0).json()["approval_request_id"]

    mine = client.get(f"/api/v1/approvals/{rid}", headers=_hdr(client, MANAGER)).json()
    assert mine["can_decide"] is False, "a manager cannot decide their own request"
    assert mine["cannot_decide_reason"] == "You cannot approve your own request"

    # The count agrees with the queue: this request is not work waiting on them.
    listed = client.get("/api/v1/approvals", headers=_hdr(client, MANAGER)).json()
    assert listed["pending_for_me"] == 0

    # And the rule the flag now mirrors is still enforced where it matters.
    refused = client.post(f"/api/v1/approvals/{rid}/decide",
                          json={"status": "APPROVED"}, headers=_hdr(client, MANAGER))
    assert refused.status_code == 403
    assert refused.json()["detail"] == "You cannot approve your own request"

    # An owner may decide it, and is told so.
    theirs = client.get(f"/api/v1/approvals/{rid}", headers=_hdr(client, OWNER)).json()
    assert theirs["can_decide"] is True
    assert theirs["cannot_decide_reason"] is None


def test_a_managers_count_excludes_what_only_an_owner_may_sign(client):
    """One number, and it is the number the queue will show.

    The storyboard tile used an unscoped `count(*)` over every PENDING request in
    the organization, so a manager's landing page said 3 while the badge said 2
    and exactly 1 was decidable. The extra one was a below-cost request that
    `inbox` deliberately keeps out of a manager's queue, and the tile's "Work
    through these →" therefore landed on a screen where it did not appear. The
    tile reads `pending_count` now; this is that function's half of the contract.
    """
    _snapshot(client, SALES, 135.0, line_id="L1")
    thin = _raise(client, SALES, 135.0, line_id="L1")
    assert thin.status_code == 201, thin.text
    assert thin.json()["required_authority"] == "MANAGER"

    _snapshot(client, SALES, 100.0, line_id="L2")     # under the 124.0 unit cost
    below = _raise(client, SALES, 100.0, line_id="L2")
    assert below.status_code == 201, below.text
    assert below.json()["required_authority"] == "OWNER", "below cost is the owner's"

    mgr = client.get("/api/v1/approvals", headers=_hdr(client, MANAGER)).json()
    decidable = [r for r in mgr["requests"] if r["can_decide"]]
    assert mgr["pending_for_me"] == 1, "not 2 — one of these is the owner's to sign"
    assert len(decidable) == 1
    assert decidable[0]["approval_request_id"] == thin.json()["approval_request_id"]

    own = client.get("/api/v1/approvals", headers=_hdr(client, OWNER)).json()
    assert own["pending_for_me"] == 2, "the owner can sign both"


def test_a_manager_is_still_offered_someone_elses_thin_price(client):
    """The fix must not withdraw the authority a manager does have."""
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    body = client.get(f"/api/v1/approvals/{rid}", headers=_hdr(client, MANAGER)).json()
    assert body["can_decide"] is True and body["cannot_decide_reason"] is None
    assert client.get("/api/v1/approvals",
                      headers=_hdr(client, MANAGER)).json()["pending_for_me"] == 1


def test_the_request_carries_economics_to_the_approver_and_not_to_the_requester(client):
    """The approver needs the margin to judge; the salesperson still must not
    see it, even on their own request."""
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]

    mgmt = client.get(f"/api/v1/approvals/{rid}", headers=_hdr(client, MANAGER)).json()
    assert mgmt["subject"]["unit_cost"] == 124.0
    assert mgmt["subject"]["margin"] is not None

    sales = client.get(f"/api/v1/approvals/{rid}", headers=_hdr(client, SALES)).json()
    assert "subject" not in sales
    assert sales["summary"] and sales["status"] == "PENDING"


def test_the_approver_sees_the_price_asked_about_not_whatever_it_became(client):
    """Otherwise a request can be re-pointed at a lower price while it sits in
    the queue, and the approval launders it."""
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    _snapshot(client, SALES, 100.0)      # salesperson drops the price meanwhile
    mgmt = client.get(f"/api/v1/approvals/{rid}", headers=_hdr(client, MANAGER)).json()
    assert mgmt["subject"]["quoted_unit_price"] == 135.0


def test_nobody_approves_their_own_request(client):
    rid = _raise(client, MANAGER, 135.0).json()["approval_request_id"]
    r = client.post(f"/api/v1/approvals/{rid}/decide", json={"status": "APPROVED"},
                    headers=_hdr(client, MANAGER))
    assert r.status_code == 403
    assert "your own" in r.json()["detail"]


def test_an_owner_may_self_approve_because_they_are_often_the_only_approver(client):
    rid = _raise(client, OWNER, 135.0).json()["approval_request_id"]
    r = client.post(f"/api/v1/approvals/{rid}/decide", json={"status": "APPROVED"},
                    headers=_hdr(client, OWNER))
    assert r.status_code == 200


def test_changes_requested_returns_it_without_ending_it(client):
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    r = client.post(f"/api/v1/approvals/{rid}/decide",
                    json={"status": "CHANGES_REQUESTED", "note": "Try ₹150"},
                    headers=_hdr(client, MANAGER)).json()
    assert r["status"] == "CHANGES_REQUESTED" and r["is_open"] is True

    again = _raise(client, SALES, 140.0)   # better, but still under the floor
    assert again.status_code == 201
    assert again.json()["approval_request_id"] == rid, "same thread, not a new one"
    assert again.json()["status"] == "PENDING"
    actions = [t["action"] for t in again.json()["thread"]]
    assert actions == ["REQUESTED", "CHANGES_REQUESTED", "RESUBMITTED"]


def test_complying_with_a_returned_request_closes_it(client):
    """A manager who asks for ₹150 and gets it should not be left with an
    unanswerable item in their queue — that is how a queue stops being read."""
    _snapshot(client, SALES, 135.0)
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    client.post(f"/api/v1/approvals/{rid}/decide",
                json={"status": "CHANGES_REQUESTED", "note": "Try ₹180"},
                headers=_hdr(client, MANAGER))

    _snapshot(client, SALES, 180.0)       # complies — now within policy

    after = client.get(f"/api/v1/approvals/{rid}", headers=_hdr(client, MANAGER)).json()
    assert after["status"] == "WITHDRAWN" and after["is_open"] is False
    assert client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()["can_submit"] is True


def test_a_decided_request_cannot_be_decided_again(client):
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    client.post(f"/api/v1/approvals/{rid}/decide", json={"status": "REJECTED"},
                headers=_hdr(client, MANAGER))
    r = client.post(f"/api/v1/approvals/{rid}/decide", json={"status": "APPROVED"},
                    headers=_hdr(client, MANAGER))
    assert r.status_code == 409


def test_pressing_the_button_twice_does_not_queue_two_identical_requests(client):
    first = _raise(client, SALES, 135.0).json()["approval_request_id"]
    second = _raise(client, SALES, 135.0).json()["approval_request_id"]
    assert first == second


def test_a_manager_queue_excludes_what_only_an_owner_may_decide(client):
    _raise(client, SALES, 100.0, line_id="L1")     # below cost → owner
    _raise(client, SALES, 135.0, line_id="L2")     # thin → manager

    mgmt = client.get("/api/v1/approvals", headers=_hdr(client, MANAGER)).json()
    assert {r["subject_line_id"] for r in mgmt["requests"]} == {"L2"}
    assert mgmt["pending_for_me"] == 1

    owner = client.get("/api/v1/approvals", headers=_hdr(client, OWNER)).json()
    assert {r["subject_line_id"] for r in owner["requests"]} == {"L1", "L2"}


def test_a_salesperson_sees_only_their_own_requests(client):
    _raise(client, SALES, 135.0, line_id="L1")
    _raise(client, MANAGER, 135.0, line_id="L2")
    mine = client.get("/api/v1/approvals", headers=_hdr(client, SALES)).json()
    assert {r["subject_line_id"] for r in mine["requests"]} == {"L1"}


# ── the gate: this is what makes the rest matter ────────────────────────────
def test_a_quote_with_an_unapproved_line_cannot_be_sent(client):
    _snapshot(client, SALES, 135.0)
    gate = client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()
    assert gate["can_submit"] is False
    assert "need approval" in gate["blocked_reason"]


def test_approval_opens_the_gate(client):
    _snapshot(client, SALES, 135.0)
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    client.post(f"/api/v1/approvals/{rid}/decide", json={"status": "APPROVED"},
                headers=_hdr(client, MANAGER))
    gate = client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()
    assert gate["can_submit"] is True and gate["blocked_reason"] is None


def test_raising_a_request_is_not_itself_permission_to_send(client):
    """Asking is not being told yes."""
    _snapshot(client, SALES, 135.0)
    _raise(client, SALES, 135.0)
    gate = client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()
    assert gate["can_submit"] is False


def test_a_rejected_request_keeps_the_gate_shut(client):
    _snapshot(client, SALES, 135.0)
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    client.post(f"/api/v1/approvals/{rid}/decide", json={"status": "REJECTED"},
                headers=_hdr(client, MANAGER))
    gate = client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()
    assert gate["can_submit"] is False


def test_an_approval_does_not_carry_over_to_a_lower_price(client):
    """The most obvious way to defeat this control: get ₹135 approved, then
    send ₹100 under the same approval."""
    _snapshot(client, SALES, 135.0)
    rid = _raise(client, SALES, 135.0).json()["approval_request_id"]
    client.post(f"/api/v1/approvals/{rid}/decide", json={"status": "APPROVED"},
                headers=_hdr(client, MANAGER))
    assert client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()["can_submit"] is True

    _snapshot(client, SALES, 100.0)      # re-priced downward after approval
    gate = client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()
    assert gate["can_submit"] is False
    assert "price changed since approval" in gate["blocked_reason"]


def test_re_pricing_above_the_floor_clears_the_block_without_an_approval(client):
    """A salesperson who takes the advice should not then need permission."""
    _snapshot(client, SALES, 135.0)
    assert client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()["can_submit"] is False
    _snapshot(client, SALES, 180.0)
    assert client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()["can_submit"] is True


def test_turning_the_policy_off_stops_the_platform_refusing_anything(client):
    """Enforcement is the owner's choice, and the switch has to actually work."""
    _snapshot(client, SALES, 135.0)
    client.patch("/api/v1/admin/policy", json={"require_approval_for_quotes": False},
                 headers=_hdr(client, OWNER))
    assert client.get("/api/v1/approvals/quotes/q1/gate",
                      headers=_hdr(client, SALES)).json()["can_submit"] is True


# ── escalation actually escalates ───────────────────────────────────────────
def _decision(s, assigned="usr_sales") -> models.Decision:
    d = models.Decision(
        organization_id=ORG, decision_key="dk_test", decision_type="CUSTOMER_DORMANCY",
        subject_entity_type="CUSTOMER", subject_entity_id="c1",
        assigned_user_id=assigned, assigned_role="SALESPERSON",
        status=DecisionStatus.OPEN.value, priority_score=50, priority_band="MEDIUM",
        priority_deterministic_base=50, priority_ai_adjustment=0,
        signal_ids=[], evidence_refs=[], ai={"title": "Acme has gone quiet"},
        confidence={})
    s.add(d)
    s.flush()
    return d


def test_escalating_a_decision_parks_it_instead_of_closing_it(client):
    """It used to set OVERRIDDEN — a closing status — so the queue looked dealt
    with while nobody upstream had been told anything."""
    s = client.Maker()
    did = _decision(s).decision_id
    s.commit()
    s.close()

    r = client.post(f"/api/v1/decisions/{did}/action",
                    json={"action": "ESCALATE", "note": "Need a pricing call"},
                    headers=_hdr(client, SALES))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ESCALATED"


def test_escalating_puts_a_real_request_in_the_managers_queue(client):
    s = client.Maker()
    did = _decision(s).decision_id
    s.commit()
    s.close()
    client.post(f"/api/v1/decisions/{did}/action",
                json={"action": "ESCALATE", "note": "Need a pricing call"},
                headers=_hdr(client, SALES))

    queue = client.get("/api/v1/approvals", headers=_hdr(client, MANAGER)).json()
    escalations = [r for r in queue["requests"] if r["kind"] == "DECISION_ESCALATION"]
    assert len(escalations) == 1
    assert escalations[0]["subject_id"] == did
    assert escalations[0]["reason"] == "Need a pricing call"


def test_answering_the_escalation_moves_the_decision_on(client):
    s = client.Maker()
    did = _decision(s).decision_id
    s.commit()
    s.close()
    client.post(f"/api/v1/decisions/{did}/action", json={"action": "ESCALATE"},
                headers=_hdr(client, SALES))
    rid = [r for r in client.get("/api/v1/approvals",
                                 headers=_hdr(client, MANAGER)).json()["requests"]
           if r["kind"] == "DECISION_ESCALATION"][0]["approval_request_id"]

    client.post(f"/api/v1/approvals/{rid}/decide",
                json={"status": "APPROVED", "note": "Go ahead"},
                headers=_hdr(client, MANAGER))

    s = client.Maker()
    assert s.get(models.Decision, did).status == DecisionStatus.ACTIONED.value
    s.close()


def test_a_returned_escalation_goes_back_to_the_salespersons_queue(client):
    s = client.Maker()
    did = _decision(s).decision_id
    s.commit()
    s.close()
    client.post(f"/api/v1/decisions/{did}/action", json={"action": "ESCALATE"},
                headers=_hdr(client, SALES))
    rid = [r for r in client.get("/api/v1/approvals",
                                 headers=_hdr(client, MANAGER)).json()["requests"]
           if r["kind"] == "DECISION_ESCALATION"][0]["approval_request_id"]
    client.post(f"/api/v1/approvals/{rid}/decide",
                json={"status": "CHANGES_REQUESTED", "note": "What did they say?"},
                headers=_hdr(client, MANAGER))

    s = client.Maker()
    assert s.get(models.Decision, did).status == DecisionStatus.OPEN.value
    s.close()
