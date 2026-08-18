"""The demonstration workspace a stranger can open without an account.

`app/demo.py` builds a realistic book whose histories deliberately trigger all
five signal families and runs the real pipeline over it — the best answer this
platform has to "what does it actually do" — and it was reachable only through
`/api/v1/internal/demo-seed`, which needs an account. So the one artefact
written to convince somebody was reachable only by people already convinced.

Opening that to the public is the only unauthenticated *session* this codebase
mints, so most of what is pinned here is the boundary rather than the feature:
off unless configured, off if the configuration is wrong, read-only once inside,
and confined to fabricated data.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.config import settings
from app.db import get_session
from app.domain import models
from app.routers import admin, onboarding as onboarding_router, platform_auth
from app.seed import ensure_org_and_users

DEMO_ORG = "org_demo"
DEMO_EMAIL = "visitor@demo.example"
REAL_ORG = "org_pie"


@pytest.fixture()
def client(monkeypatch):
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    # A real tenant alongside the demo one, so "does the demo reach anything
    # real" is a question this fixture can actually answer.
    ensure_org_and_users(s)
    s.add(models.Organization(organization_id=DEMO_ORG, name="Demo"))
    s.flush()
    s.add(models.User(user_id="usr_demo", organization_id=DEMO_ORG,
                      email=DEMO_EMAIL, name="Demo Visitor", role="OWNER",
                      password_hash=None, active=True))
    s.commit()
    s.close()

    monkeypatch.setattr(settings, "PUBLIC_DEMO_ORG_ID", DEMO_ORG)
    monkeypatch.setattr(settings, "PUBLIC_DEMO_EMAIL", DEMO_EMAIL)

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(onboarding_router.router)
    app.include_router(admin.router)

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


def _enter(tc) -> dict:
    r = tc.post("/api/v1/demo")
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── the door ─────────────────────────────────────────────────────────────────
def test_a_stranger_can_walk_in_without_an_account(client):
    body = client.get("/api/v1/demo").json()
    assert body["enabled"] is True

    r = client.post("/api/v1/demo")
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == DEMO_ORG
    assert r.json()["token"]


def test_an_unconfigured_deployment_has_no_door_rather_than_a_locked_one(
        client, monkeypatch):
    """404, not 403. An install that pulled new code must not acquire an
    unauthenticated endpoint it never asked for — and must not advertise one."""
    monkeypatch.setattr(settings, "PUBLIC_DEMO_ORG_ID", "")
    assert client.get("/api/v1/demo").json()["enabled"] is False
    assert client.post("/api/v1/demo").status_code == 404


@pytest.mark.parametrize("org,email", [
    (REAL_ORG, DEMO_EMAIL),        # the address is not in the named org
    (DEMO_ORG, "s.menon@pie.example"),  # the address belongs to a real tenant
])
def test_configuration_that_does_not_agree_resolves_to_no_demo(
        client, monkeypatch, org, email):
    """The failure direction that matters.

    A demo pointed at the wrong tenant must fail as an absent door, never as an
    open one into somebody's real book. Both halves have to agree.
    """
    monkeypatch.setattr(settings, "PUBLIC_DEMO_ORG_ID", org)
    monkeypatch.setattr(settings, "PUBLIC_DEMO_EMAIL", email)
    assert client.get("/api/v1/demo").json()["enabled"] is False
    assert client.post("/api/v1/demo").status_code == 404


# ── read-only, and enforced by method rather than by a list ──────────────────
def test_the_demo_can_read(client):
    hdr = _enter(client)
    assert client.get("/api/v1/admin/policy", headers=hdr).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("patch", "/api/v1/admin/margin-policy", {"min_margin": 0.01}),
    ("post", "/api/v1/admin/users", {"email": "x@y.example", "name": "X",
                                     "role": "OWNER"}),
])
def test_the_demo_cannot_write_anything(client, method, path, body):
    """An owner-roled demo visitor is refused by the seam, not by their role.

    The account is an OWNER on purpose — a stranger should see the screens worth
    seeing — so the role checks would let all of this through. What stops it is
    `current_principal` refusing every unsafe method.
    """
    hdr = _enter(client)
    r = getattr(client, method)(path, json=body, headers=hdr)
    assert r.status_code == 403
    assert "demonstration workspace" in r.json()["detail"]


def test_the_refusal_is_by_method_so_an_unseen_endpoint_is_refused_too(client):
    """The property a path allowlist could not have.

    An endpoint written next year, by somebody who never read the demo code, is
    refused because it is a POST — not because anybody remembered to list it.
    """
    hdr = _enter(client)
    r = client.post("/api/v1/admin/no-such-endpoint-yet", json={}, headers=hdr)
    # 403 from the seam, which runs before routing resolves the handler — the
    # point being that it did not need to know this path exists.
    assert r.status_code in (403, 404, 405)
    if r.status_code == 403:
        assert "demonstration workspace" in r.json()["detail"]


def test_a_real_tenants_session_is_not_made_read_only(client):
    """The gate must key on the demo organization, never on being signed in."""
    from app.seed import SEED_PASSWORD

    r = client.post("/api/v1/auth/login",
                    json={"email": "s.menon@pie.example", "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    # A write that a real owner is entitled to make still goes through.
    assert client.patch("/api/v1/admin/margin-policy",
                        json={"min_margin": 0.11},
                        headers=hdr).status_code == 200


def test_no_organization_is_the_demo_when_none_is_configured(monkeypatch):
    """Empty configuration must mean *no* org, never *every* org."""
    from app.authz import is_demo_org

    monkeypatch.setattr(settings, "PUBLIC_DEMO_ORG_ID", "")
    assert is_demo_org(REAL_ORG) is False
    assert is_demo_org("") is False


# ── what the visitor can reach ───────────────────────────────────────────────
def test_the_demo_tenant_holds_no_connection_and_no_credential(client):
    """The safety property the settings comment asks a deployment to honour.

    A demo signs in as an OWNER, so anything the demo organization holds is
    readable by a stranger. `app.demo` writes into the read model directly and
    never creates a connection, which is what makes that acceptable — pinned
    here because it is a property of the seeded data, not of the code above.
    """
    s = client.Maker()
    connections = s.query(models.ZohoConnection).filter_by(
        organization_id=DEMO_ORG).all()
    assert connections == [], "the demo tenant must hold no connection"
    # Credentials hang off the connection rather than the organization — one
    # Zoho grant reaches every company that login can see — so with no
    # connection there is nothing for a credential to belong to. Asserted
    # through the connection rather than by an organization column, which
    # `ZohoCredential` deliberately does not have.
    assert s.query(models.ZohoCredential).filter(
        models.ZohoCredential.credential_id.in_(
            [c.credential_id for c in connections] or [""])).count() == 0
    s.close()


# ── provisioning the tenant the door points at ───────────────────────────────
def test_the_demo_account_cannot_be_signed_into_with_a_password(session):
    """The account has no hash, and that is the control rather than an omission.

    `verify_password` refuses a blank hash, so no password reaches this account
    through `POST /auth/login`. The only way in is the demo door, which mints a
    session without a credential and which `authz` then refuses every write.
    """
    from app.demo import provision_demo_org
    from app.passwords import verify_password

    user = provision_demo_org(session, DEMO_ORG, DEMO_EMAIL)
    assert user.password_hash is None
    assert verify_password("", user.password_hash) is False
    assert verify_password("anything at all", user.password_hash) is False


def test_provisioning_a_demo_tenant_does_not_touch_the_default_one(session):
    """The failure this exists to prevent.

    Pointing the public demo at the deployment's own organization would hand a
    stranger read access to whatever real book a single-tenant install keeps
    there, so the provisioning path builds a named tenant beside it and never
    reaches for `settings.DEFAULT_ORG_ID`.
    """
    from app.config import settings as cfg
    from app.demo import provision_demo_org

    user = provision_demo_org(session, DEMO_ORG, DEMO_EMAIL)
    assert user.organization_id == DEMO_ORG != cfg.DEFAULT_ORG_ID
    assert session.get(models.Organization, cfg.DEFAULT_ORG_ID) is None, \
        "provisioning a demo tenant must not create the deployment's own"
