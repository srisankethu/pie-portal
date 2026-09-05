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
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.domain import models
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
    engine = dbsupport.fresh_engine()
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
    tc = TestClient(app)
    # Kept on the client so a test can seed rows the router then reports on,
    # the same way `test_sync_jobs` does. Both list endpoints truncate, and a
    # truncation is only observable against rows somebody put there.
    tc.Maker = Maker
    return tc


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


# ── how much of the log is on screen ────────────────────────────────────────
#
# Both list endpoints truncate server-side — payloads at 50, access at 200 —
# and neither used to say so. A screen holding fifty rows could not tell a
# complete log from the head of a longer one, so every sentence on it had to be
# worded as a claim about the rows in hand: "every payload in this report",
# never "every payload logged". On a surface whose entire purpose is that a
# promise is checkable, "we show you everything" and "we show you the last two
# hundred" are different promises, and the screen could not tell them apart.
#
# So each response carries a total beside its rows. These pin that it counts the
# whole set rather than the page, and that it counts only this tenant's — a
# count with the organization left off its filter is a cross-tenant leak that
# no per-row assertion in this file would notice.


def _seed_payloads(client, n, org=ORG):
    s = client.Maker()
    for i in range(n):
        s.add(models.ModelPayload(
            organization_id=org, decision_type="TEST", provider="mock",
            model="mock-1", payload_ciphertext=f"ciphertext {i}"))
    s.commit()
    s.close()


def _seed_access_events(client, n, org=ORG):
    s = client.Maker()
    for i in range(n):
        s.add(models.AccessEvent(
            organization_id=org, access_grant_id=f"grant_{i}",
            staff_user_id="support@pie.example", action="ACCESSED",
            detail=f"opened decision {i}"))
    s.commit()
    s.close()


def test_the_payload_list_says_how_many_it_is_showing_of(client):
    _seed_payloads(client, 3)
    body = client.get("/api/v1/trust/payloads?limit=2",
                      headers=_hdr(client, "s.menon@pie.example")).json()

    assert len(body["payloads"]) == 2, "the cap is unchanged"
    assert body["total"] == 3, "and the total is of the log, not of the page"
    # `summary` still describes the rows in hand — it is the set the checker's
    # findings were read off, and it is not a fraction of the total.
    assert body["summary"] == {"payloads": 2, "flagged": 0}


def test_the_access_log_says_how_many_it_is_showing_of(client):
    _seed_access_events(client, 3)
    body = client.get("/api/v1/trust/access?limit=2",
                      headers=_hdr(client, "s.menon@pie.example")).json()

    assert len(body["events"]) == 2
    assert body["total"] == 3
    assert body["note"], "the server's own sentence is untouched"


def test_neither_total_counts_another_tenants_rows(client):
    """The one way a count can be worse than no count. Asserted for both, from
    an empty tenant, so a missing organization filter shows up as a number
    rather than as a row somebody would have spotted."""
    _seed_payloads(client, 4, org="org_someone_else")
    _seed_access_events(client, 4, org="org_someone_else")
    hdr = _hdr(client, "s.menon@pie.example")

    assert client.get("/api/v1/trust/payloads", headers=hdr).json()["total"] == 0
    assert client.get("/api/v1/trust/access", headers=hdr).json()["total"] == 0
