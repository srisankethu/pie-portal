"""The trust surface over HTTP: who may ask, and who is refused.

`app/trust/` is covered thoroughly at the unit level in `test_trust_controls.py`
— receipts verify, ciphertext is unreadable after erasure, a grant is logged.
What was never covered is the *router*, and the router is where the role check
lives. Every route is `require_owner`, and until a screen called them nothing
exercised that: an owner-only surface whose gate had never been asserted.

The manager cases matter most. A manager is the role with full economics, so it
is the plausible mistake — and one of these routes destroys the tenant's data
key, which is not an authority to hand out by accident.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.routers import platform_auth, trust
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"

#: Every read route on the surface, so a new one added without a role check
#: fails here rather than in production.
READ_ROUTES = [
    "/api/v1/trust/disclosure",
    "/api/v1/trust/payloads",
    "/api/v1/trust/access",
    "/api/v1/trust/export",
    "/api/v1/trust/erasure",
]


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(trust.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(client, email):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.parametrize("path", READ_ROUTES)
@pytest.mark.parametrize("email", ["m.rao@pie.example", "r.nair@pie.example"])
def test_only_an_owner_may_read_the_trust_surface(client, path, email):
    assert client.get(path, headers=_hdr(client, email)).status_code == 403


@pytest.mark.parametrize("path", READ_ROUTES)
def test_an_owner_may_read_all_of_it(client, path):
    r = client.get(path, headers=_hdr(client, "s.menon@pie.example"))
    assert r.status_code == 200, r.text


def test_the_surface_is_not_readable_without_a_token(client):
    for path in READ_ROUTES:
        assert client.get(path).status_code == 401


def test_a_manager_cannot_erase_the_organization(client):
    """The one that would be unrecoverable if the check were wrong."""
    r = client.post("/api/v1/trust/erasure",
                    headers=_hdr(client, "m.rao@pie.example"),
                    json={"confirm_organization_id": ORG,
                          "reason": "testing the role check"})
    assert r.status_code == 403
    # And it did not half-happen: the state an owner reads is still un-erased.
    state = client.get("/api/v1/trust/erasure",
                       headers=_hdr(client, "s.menon@pie.example")).json()
    assert state["erased"] is False


def test_the_confirmation_must_match_and_nothing_happens_when_it_does_not(client):
    hdr = _hdr(client, "s.menon@pie.example")
    r = client.post("/api/v1/trust/erasure", headers=hdr,
                    json={"confirm_organization_id": "org_something_else",
                          "reason": "a reason long enough to pass validation"})
    assert r.status_code == 400
    assert "did not match" in r.json()["detail"]
    assert client.get("/api/v1/trust/erasure", headers=hdr).json()["erased"] is False


def test_a_reason_too_short_to_be_a_reason_is_refused(client):
    """Mirrors the ten-character floor the erase form disables its button on."""
    r = client.post("/api/v1/trust/erasure",
                    headers=_hdr(client, "s.menon@pie.example"),
                    json={"confirm_organization_id": ORG, "reason": "no"})
    assert r.status_code == 422


def test_the_disclosure_names_the_provider_that_will_actually_run(client):
    """The screen shows the provider rather than the model, because this reports
    `AI_MODEL` whatever the provider is. Asserted so that stays a known
    property of the payload rather than a surprise to the next reader."""
    body = client.get("/api/v1/trust/disclosure",
                      headers=_hdr(client, "s.menon@pie.example")).json()
    assert body["provider"] == "mock"
    assert body["model"]              # present, and about a provider nothing calls
    assert body["training_on_customer_data"] is False
    assert body["never_sent"] and body["allowed"]
