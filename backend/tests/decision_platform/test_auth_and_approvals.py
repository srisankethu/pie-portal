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
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app import memberships
from app.domain import models
from app.domain.enums import DecisionStatus, Role
from app.passwords import hash_password, verify_password
from app.routers import admin, approvals as approvals_router, decisions, platform_auth
from app.routers import quote_intelligence
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
AS_OF = date(2026, 7, 1)

OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"


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
    engine = dbsupport.fresh_engine()
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
    s.add(models.User(user_id="usr_new", organization_id=ORG, email="new@pie.example",
                      name="New", role=Role.OWNER.value, active=True))
    s.commit()
    s.close()
    assert _login(client, "new@pie.example", "anything").status_code == 401


def test_an_inactive_account_cannot_sign_in(client):
    s = client.Maker()
    u = s.get(models.User, "usr_sales")
    u.active = False
    s.commit()
    s.close()
    assert _login(client, SALES).status_code == 401


def test_failure_does_not_reveal_whether_the_account_exists(client):
    """Different messages tell an attacker which addresses are worth attacking."""
    unknown = _login(client, "nobody@pie.example", "whatever")
    wrong = _login(client, OWNER, "whatever")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_repeated_bad_logins_are_throttled(client):
    """Brute-force protection must actually engage — driven through the endpoint.

    The failure counter was incremented and ``flush``ed, then the handler raised
    401; ``get_session`` rolls back on that raise, so the increment was discarded
    and the count could never pass the threshold. Throttling was unreachable: the
    unit test exercised ``record_login_failure`` directly and never saw it. It
    commits now, so the count survives the 401 and the 429 branch is reachable.
    """
    from app.authz import LOGIN_THROTTLE_THRESHOLD

    for _ in range(LOGIN_THROTTLE_THRESHOLD):
        assert _login(client, OWNER, "wrong").status_code == 401
    # The next attempt is throttled — and so is a *correct* password, because the
    # lockout is on the account, not on the particular guess.
    assert _login(client, OWNER, "wrong").status_code == 429
    assert _login(client, OWNER).status_code == 429

    # The counter actually persisted across the 401s — the whole of the bug.
    s = client.Maker()
    try:
        assert s.get(models.User, "usr_owner").login_failures_count >= LOGIN_THROTTLE_THRESHOLD
    finally:
        s.close()


def test_login_hashes_even_for_an_unknown_account(client, monkeypatch):
    """The uniform failure message only hides account existence if the *timing*
    is uniform too. `verify_password` (240k PBKDF2 iterations) must run even when
    the account does not exist — otherwise the ~35x response-time gap between a
    real address and an unknown one enumerates accounts around the message. Pinned
    by call count, not by the clock, so it cannot be flaky."""
    import app.routers.platform_auth as pa

    calls = []
    real = pa.verify_password
    monkeypatch.setattr(pa, "verify_password",
                        lambda pw, stored: (calls.append(1), real(pw, stored))[1])
    assert _login(client, "ghost@nowhere.example", "whatever").status_code == 401
    assert len(calls) == 1, "a verification must run even for an unknown account"


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
    body = {"email": "new@pie.example", "name": "New Person", "role": "SALESPERSON"}
    assert client.post("/api/v1/admin/users", json=body,
                       headers=_hdr(client, SALES)).status_code == 403
    assert client.post("/api/v1/admin/users", json=body,
                       headers=_hdr(client, MANAGER)).status_code == 403
    r = client.post("/api/v1/admin/users", json=body, headers=_hdr(client, OWNER))
    assert r.status_code == 201, r.text


def test_a_created_user_can_sign_in_with_the_password_returned_once(client):
    r = client.post("/api/v1/admin/users",
                    json={"email": "new@pie.example", "name": "New", "role": "SALES_MANAGER"},
                    headers=_hdr(client, OWNER)).json()
    temp = r["temporary_password"]
    assert temp and len(temp) >= 10

    login = _login(client, "new@pie.example", temp)
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
                        json={"email": "o2@pie.example", "name": "Second Owner",
                              "role": "OWNER"},
                        headers=_hdr(client, OWNER)).json()["user"]
    # two owners now — demoting one is fine
    assert client.patch(f"/api/v1/admin/users/{owner['user_id']}",
                        json={"role": "SALESPERSON"},
                        headers=_hdr(client, OWNER)).status_code == 200
    # back to one; the remaining owner cannot be removed by anyone
    s = client.Maker()
    s.add(models.User(user_id="usr_o3", organization_id=ORG, email="o3@pie.example",
                      name="Third", role=Role.OWNER.value, active=True,
                      password_hash=hash_password(SEED_PASSWORD)))
    s.flush()
    # The grant, and not an optional extra: a user row is an identity, and
    # since memberships landed it is the membership that says this person may
    # open this organization and as what. Written through the service rather
    # than as a second raw row, so this fixture cannot drift from what
    # `POST /admin/users` actually does.
    memberships.add_member(s, organization_id=ORG, user_id="usr_o3",
                           role=Role.OWNER)
    s.commit()
    s.close()
    r = client.patch("/api/v1/admin/users/usr_owner", json={"active": False},
                     headers=_hdr(client, "o3@pie.example"))
    assert r.status_code == 200, "two active owners — removing one is allowed"
    r = client.patch("/api/v1/admin/users/usr_o3", json={"active": False},
                     headers=_hdr(client, "o3@pie.example"))
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


def _approver_view(c, rid, email=OWNER):
    """The request as an approver sees it — the only view that carries the real
    ``required_authority`` and ``requires_rationale``.

    A salesperson's view deliberately no longer does: those fields are OWNER iff
    the line is below cost, a boundary with no policy multiplier, so serving them
    to the requester let a salesperson walk the price and recover cost (the
    resurrected NEGATIVE_MARGIN oracle, CLAUDE.md §1). Tests that care about the
    authority a request *carries* therefore read it from here, not from the
    salesperson response that raised it."""
    return c.get(f"/api/v1/approvals/{rid}", headers=_hdr(c, email)).json()


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
    # The salesperson sees the collapsed authority, never MANAGER-vs-OWNER.
    assert body["required_authority"] == "APPROVAL_REQUIRED"
    assert body["can_decide"] is False, "the salesperson cannot decide their own"
    # The real authority is manager-level, read from the approver's view.
    assert _approver_view(client, body["approval_request_id"],
                          MANAGER)["required_authority"] == "MANAGER"


def test_selling_below_cost_is_escalated_to_the_owner_not_the_manager(client):
    """A thin margin is a commercial judgement. Losing money on purpose is a
    different decision, and it belongs to a different person."""
    r = _raise(client, SALES, 100.0)     # under the ₹124 cost
    assert r.status_code == 201
    # The salesperson is not told this escalated to the owner — that fact is
    # `below_cost`. The owner's own view carries the real authority.
    assert r.json()["required_authority"] == "APPROVAL_REQUIRED"
    rid = r.json()["approval_request_id"]
    assert _approver_view(client, rid, OWNER)["required_authority"] == "OWNER"

    denied = client.post(f"/api/v1/approvals/{rid}/decide",
                         json={"status": "APPROVED"}, headers=_hdr(client, MANAGER))
    assert denied.status_code == 403
    assert "owner" in denied.json()["detail"].lower()

    # The note is required for a below-cost signature — see
    # `test_signing_a_below_cost_price_needs_a_reason_on_the_record`. What this
    # test is about is *who* may sign, so it complies rather than asserting the
    # older, quieter behaviour.
    allowed = client.post(f"/api/v1/approvals/{rid}/decide",
                          json={"status": "APPROVED", "note": "Strategic account."},
                          headers=_hdr(client, OWNER))
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


def test_signing_a_below_cost_price_needs_a_reason_on_the_record(client):
    """The one irreversible concession here took no reason at all.

    "Ask for a different price" and the decision screen's "Do something
    different" both refuse to proceed without text. Approving a line priced below
    what the item cost us — where the money is gone the moment the quote goes out
    — decided immediately and stored `decision_note = None`.
    """
    _snapshot(client, SALES, 100.0)                 # under the 124.0 unit cost
    raised = _raise(client, SALES, 100.0).json()
    # The salesperson does not see the below-cost escalation or the rationale
    # flag — both flip at cost. The owner's view carries them, and enforcement
    # (below) is on the decide path regardless of what the requester was shown.
    assert raised["required_authority"] == "APPROVAL_REQUIRED"
    assert raised["requires_rationale"] is False
    owner_view = _approver_view(client, raised["approval_request_id"], OWNER)
    assert owner_view["required_authority"] == "OWNER"
    assert owner_view["requires_rationale"] is True, "the server says so, not the browser"

    bare = client.post(f"/api/v1/approvals/{raised['approval_request_id']}/decide",
                       json={"status": "APPROVED"}, headers=_hdr(client, OWNER))
    assert bare.status_code == 400, bare.text
    assert "needs a reason" in bare.json()["detail"]

    blank = client.post(f"/api/v1/approvals/{raised['approval_request_id']}/decide",
                        json={"status": "APPROVED", "note": "   "},
                        headers=_hdr(client, OWNER))
    assert blank.status_code == 400, "whitespace is not a reason"

    signed = client.post(f"/api/v1/approvals/{raised['approval_request_id']}/decide",
                         json={"status": "APPROVED",
                               "note": "Strategic account; recovering it on the holder."},
                         headers=_hdr(client, OWNER))
    assert signed.status_code == 200
    body = signed.json()
    assert body["status"] == "APPROVED"
    assert body["decision_note"] == "Strategic account; recovering it on the holder."
    assert body["thread"][-1]["note"] == body["decision_note"], "and it is in the thread"


def test_a_thin_price_can_still_be_approved_without_a_note(client):
    """Only the irreversible one is gated. A manager signing an ordinary thin
    margin should not be made to write a sentence to clear their queue."""
    raised = _raise(client, SALES, 135.0).json()
    assert raised["required_authority"] == "APPROVAL_REQUIRED"
    mgr_view = _approver_view(client, raised["approval_request_id"], MANAGER)
    assert mgr_view["required_authority"] == "MANAGER"
    assert mgr_view["requires_rationale"] is False

    r = client.post(f"/api/v1/approvals/{raised['approval_request_id']}/decide",
                    json={"status": "APPROVED"}, headers=_hdr(client, MANAGER))
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"


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
    # Both come back to the salesperson as the same collapsed value; the split
    # that this test is about is proven by the approver-side counts below.
    assert thin.json()["required_authority"] == "APPROVAL_REQUIRED"

    _snapshot(client, SALES, 100.0, line_id="L2")     # under the 124.0 unit cost
    below = _raise(client, SALES, 100.0, line_id="L2")
    assert below.status_code == 201, below.text
    assert below.json()["required_authority"] == "APPROVAL_REQUIRED"

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


def test_a_salesperson_cannot_walk_the_price_to_recover_cost(client):
    """The approvals surface must not become the NEGATIVE_MARGIN oracle again.

    ``required_authority`` escalates to OWNER exactly when the line is priced at
    or below what the item cost us — a boundary with no policy multiplier — and
    the rationale flag, the cannot-decide reason and the title all move with it.
    Served to the requesting salesperson, those four let them binary-search the
    price across ``unit_cost`` (124 here) and recover cost to the paisa. So every
    one of them must read identically on both sides of cost.

    The approver control at the end is not decoration: without it this passes
    just as well if the flow produced no request at all — the "absence of
    evidence is not a pass" trap CLAUDE.md §1 records three times.
    """
    sensitive = ("required_authority", "requires_rationale",
                 "cannot_decide_reason", "title")
    seen = set()
    for price in (100.0, 118.0, 123.0, 124.0, 130.0, 135.0):   # straddles cost 124
        body = _raise(client, SALES, price, line_id="L1").json()
        assert body["status"] == "PENDING", body
        seen.add(tuple(body[k] for k in sensitive))
    assert len(seen) == 1, (
        f"a salesperson-visible field changed with price across cost: {seen}. "
        "That is the cost oracle — none of these fields may vary at unit_cost.")

    # Control: the approver's view *does* carry the real, cost-varying authority,
    # so the collapse above is hiding a distinction that genuinely exists.
    below = _raise(client, SALES, 100.0, line_id="L2").json()["approval_request_id"]
    assert _approver_view(client, below, OWNER)["required_authority"] == "OWNER"
    thin = _raise(client, SALES, 135.0, line_id="L3").json()["approval_request_id"]
    assert _approver_view(client, thin, MANAGER)["required_authority"] == "MANAGER"


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
