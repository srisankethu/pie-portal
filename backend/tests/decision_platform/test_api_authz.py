"""API + authorization: role scope, decision-type gating, org isolation,
human-action lifecycle. Permissions are enforced server-side (not the UI).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.domain import models
from app.routers import admin, decisions, internal, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"


@pytest.fixture()
def client_and_maker():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    # seed org + users
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(internal.router)
    app.include_router(decisions.router)
    # The forced password change is the one thing a flagged account may
    # reach, so the tests for that gate need the endpoint mounted.
    app.include_router(admin.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


def _login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _seed_decision(maker, *, dtype, subject_id, assigned_user_id, org=ORG, status="OPEN"):
    s = maker()
    d = models.Decision(
        organization_id=org, decision_type=dtype,
        decision_key=f"{org}:{dtype}:{subject_id}", subject_entity_type="CUSTOMER",
        subject_entity_id=subject_id, assigned_user_id=assigned_user_id,
        assigned_role="SALESPERSON" if assigned_user_id else "OWNER",
        priority_band="MEDIUM", priority_score=50, status=status, ai={"status": "PENDING"})
    s.add(d)
    s.commit()
    did = d.decision_id
    s.close()
    return did


def test_login_and_roles(client_and_maker):
    client, _ = client_and_maker
    assert client.post("/api/v1/auth/login",
                       json={"email": "nobody@x.com", "password": "x"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": "r.nair@pie.example", "password": SEED_PASSWORD})
    assert r.json()["role"] == "SALESPERSON"


def test_sign_in_returns_the_address_it_signed_in_with(client_and_maker):
    """The Settings change-password form needs a `username` field, and the only
    correct value is the address the person just used. Without it a password
    manager files the new secret against nothing and can lock somebody out of the
    account they just secured."""
    client, _ = client_and_maker
    body = client.post("/api/v1/auth/login",
                       json={"email": "r.nair@pie.example",
                             "password": SEED_PASSWORD}).json()
    assert body["email"] == "r.nair@pie.example"


def test_auth_required(client_and_maker):
    client, _ = client_and_maker
    assert client.get("/api/v1/decisions").status_code == 401


def test_salesperson_scope_and_restricted_gating(client_and_maker):
    client, maker = client_and_maker
    dormancy = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="cust1",
                              assigned_user_id="usr_sales")
    _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="cust2",
                   assigned_user_id="usr_other")           # assigned to someone else
    margin = _seed_decision(maker, dtype="MARGIN_DETERIORATION", subject_id="prodX",
                            assigned_user_id=None)          # restricted type

    sales = _hdr(_login(client, "r.nair@pie.example"))
    ids = {d["decision_id"] for d in client.get("/api/v1/decisions", headers=sales).json()}
    assert ids == {dormancy}                                # only their own, no restricted, no other's

    # cannot fetch the restricted decision by id (404, not 403 — non-probeable)
    assert client.get(f"/api/v1/decisions/{margin}", headers=sales).status_code == 404


def test_manager_sees_all_including_restricted(client_and_maker):
    client, maker = client_and_maker
    dormancy = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="cust1",
                              assigned_user_id="usr_sales")
    margin = _seed_decision(maker, dtype="MARGIN_DETERIORATION", subject_id="prodX",
                            assigned_user_id=None)
    mgr = _hdr(_login(client, "m.rao@pie.example"))
    ids = {d["decision_id"] for d in client.get("/api/v1/decisions", headers=mgr).json()}
    assert {dormancy, margin} <= ids
    assert client.get(f"/api/v1/decisions/{margin}", headers=mgr).status_code == 200


def test_organization_isolation_at_api(client_and_maker):
    client, maker = client_and_maker
    # a decision in a different org must never surface to this org's principal
    foreign = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="c",
                             assigned_user_id="usr_sales", org="org_other")
    owner = _hdr(_login(client, "s.menon@pie.example"))
    ids = {d["decision_id"] for d in client.get("/api/v1/decisions", headers=owner).json()}
    assert foreign not in ids
    assert client.get(f"/api/v1/decisions/{foreign}", headers=owner).status_code == 404


def test_human_action_lifecycle(client_and_maker):
    client, maker = client_and_maker
    did = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="cust1",
                         assigned_user_id="usr_sales")
    sales = _hdr(_login(client, "r.nair@pie.example"))

    r = client.post(f"/api/v1/decisions/{did}/action", json={"action": "VIEW"}, headers=sales)
    assert r.json()["status"] == "VIEWED"
    r = client.post(f"/api/v1/decisions/{did}/action",
                    json={"action": "ACT", "note": "called customer"}, headers=sales)
    body = r.json()
    assert body["status"] == "ACTIONED"
    assert body["human_action"]["actor_user_id"] == "usr_sales"
    assert body["human_action"]["note"] == "called customer"

    # unknown action rejected
    assert client.post(f"/api/v1/decisions/{did}/action",
                       json={"action": "NOPE"}, headers=sales).status_code == 400


def test_an_account_owing_a_password_change_can_do_nothing_else(client_and_maker):
    """The flag was a label on an admin grid and nothing else.

    It was set by the seed and by every owner-issued reset, read in exactly two
    places — the login response and that label — and enforced nowhere. So
    `change-me-now`, which is published in the README, stayed live on every seeded
    account indefinitely. Enforced on the server rather than in the sign-in
    screen: a rule the client owns is a rule anything not the client ignores.
    """
    from app.domain import models

    client, maker = client_and_maker
    session = maker()
    user = session.get(models.User, "usr_sales")
    user.must_change_password = True
    session.commit()
    session.close()

    sales = _hdr(_login(client, "r.nair@pie.example"))
    assert client.get("/api/v1/decisions", headers=sales).status_code == 403
    blocked = client.get("/api/v1/decisions", headers=sales)
    assert "Change your password" in blocked.json()["detail"]

    # The one thing it may reach is the change itself — otherwise the forced path
    # is a loop with no way out.
    changed = client.post("/api/v1/admin/me/password",
                          json={"current_password": SEED_PASSWORD,
                                "new_password": "Sales-Review-2026!"},
                          headers=sales)
    assert changed.status_code == 200, changed.text
    # And it hands back a token, because the change just retired the one used here.
    fresh = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert client.get("/api/v1/decisions", headers=fresh).status_code == 200


def test_changing_a_password_retires_the_sessions_opened_with_the_old_one(client_and_maker):
    """Otherwise a password change does nothing about the thing it is for.

    A token minted before the change kept working indefinitely, so a credential
    believed to be compromised stayed usable by whoever held a session.
    """
    client, maker = client_and_maker
    stale = _hdr(_login(client, "r.nair@pie.example"))
    assert client.get("/api/v1/decisions", headers=stale).status_code == 200

    changed = client.post("/api/v1/admin/me/password",
                          json={"current_password": SEED_PASSWORD,
                                "new_password": "Sales-Review-2026!"},
                          headers=stale)
    assert changed.status_code == 200, changed.text

    assert client.get("/api/v1/decisions", headers=stale).status_code == 401, (
        "the token that made the change must not outlive it")
    fresh = {"Authorization": f"Bearer {changed.json()['token']}"}
    assert client.get("/api/v1/decisions", headers=fresh).status_code == 200

    # The old password is refused, and the new one works.
    assert client.post("/api/v1/auth/login",
                       json={"email": "r.nair@pie.example",
                             "password": SEED_PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login",
                       json={"email": "r.nair@pie.example",
                             "password": "Sales-Review-2026!"}).status_code == 200


def test_undo_does_not_destroy_the_reason_that_was_recorded(client_and_maker):
    """The reversal joins the trail; it does not overwrite what it reversed.

    `record_human_action` used to assign, so pressing Undo replaced an owner's
    reasoning with "Undone by the user" and the reason they had recorded was gone
    for good — while `api.ts` promised the trail kept both. Reproduced from the
    owner's own note.
    """
    client, maker = client_and_maker
    did = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="cust1",
                         assigned_user_id="usr_sales")
    sales = _hdr(_login(client, "r.nair@pie.example"))
    reason = ("Owner: holding price for this account; renegotiating the supply "
              "cost with the principal instead.")

    client.post(f"/api/v1/decisions/{did}/action",
                json={"action": "ACT", "note": reason}, headers=sales)
    body = client.post(f"/api/v1/decisions/{did}/action",
                       json={"action": "REOPEN", "note": "Undone by the user"},
                       headers=sales).json()

    # The latest action is still denormalised at the top level: that is what a
    # queue row reads, and it is why this field's shape did not change.
    assert body["status"] == "OPEN"
    assert body["human_action"]["action"] == "REOPEN"

    trail = body["human_action"]["trail"]
    assert [e["action"] for e in trail] == ["ACT", "REOPEN"], "oldest first"
    assert trail[0]["note"] == reason, "the reason survives its own reversal"
    assert all(e["actor_user_id"] == "usr_sales" for e in trail)
    assert all(e["acted_at"] for e in trail)


def test_a_decision_written_before_the_trail_existed_keeps_its_action(client_and_maker):
    """Legacy rows are backfilled on touch rather than by a data migration.

    `human_action` is schemaless JSON, so rows already in the wild hold a bare
    action with no trail. The first append must carry it forward instead of
    starting the history at the second thing that ever happened.
    """
    from app.domain.enums import HumanAction
    from app.repositories import DecisionRepository

    client, maker = client_and_maker
    did = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="cust1",
                         assigned_user_id="usr_sales")
    session = maker()
    repo = DecisionRepository(session, "org_pie")
    d = repo.get(did)
    # Exactly the shape the old code wrote: no trail key at all.
    d.human_action = {"action": "ACT", "actor_user_id": "usr_owner",
                      "acted_at": "2026-08-01T10:00:00+00:00", "note": "first call"}
    session.commit()

    repo.record_human_action(d, HumanAction.REOPEN, actor_user_id="usr_owner",
                             note="Undone by the user")
    session.commit()
    trail = d.human_action["trail"]
    assert [e["action"] for e in trail] == ["ACT", "REOPEN"]
    assert trail[0]["note"] == "first call"
    session.close()


def test_sync_requires_manager_or_owner(client_and_maker):
    client, _ = client_and_maker
    sales = _hdr(_login(client, "r.nair@pie.example"))
    owner = _hdr(_login(client, "s.menon@pie.example"))
    assert client.post("/api/v1/internal/sync/zoho", headers=sales).status_code == 403
    r = client.post("/api/v1/internal/sync/zoho", headers=owner)
    assert r.status_code == 200
    # fixture source populated the read model for this org
    assert r.json()["customers"] >= 1


def test_health(client_and_maker):
    client, _ = client_and_maker
    r = client.get("/api/v1/internal/health")
    assert r.status_code == 200 and r.json()["database"] is True


def test_demo_seed_disabled_in_production(client_and_maker, monkeypatch):
    """The demo-seed endpoint writes fabricated data; it must be refused in
    production even for an owner (prevents polluting the real read model)."""
    from app.config import settings
    client, _ = client_and_maker
    owner = _hdr(_login(client, "s.menon@pie.example"))
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert client.post("/api/v1/internal/demo-seed", headers=owner).status_code == 403
    # allowed outside production
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert client.post("/api/v1/internal/demo-seed", headers=owner).status_code == 200
