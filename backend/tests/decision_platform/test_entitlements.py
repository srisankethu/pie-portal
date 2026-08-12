"""Plans, the one-per-books trial, and the boundaries they put teeth behind.

The pricing terms this enforces were prose until now, and prose is a
suggestion to a strategic customer. Three claims get pinned. A plan resolves
safely: NULL falls back to the deployment default, and an unrecognised value
degrades to free — a typo must never widen what a tenant may use. The free
intelligence month belongs to the *connected books*: reconnecting the same
Zoho company under a fresh organization finds the trial already spent. And a
second connected company is refused below the platform plan at the moment of
connection — the licence unit is the one thing that can be enforced
mechanically.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import clock, entitlements
from app.db import Base, get_session
from app.domain import models
from app.domain.enums import PlanTier
from app.ingestion import connections as conn
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_t"
ORG2 = "org_t2"


@pytest.fixture()
def orgs(session):
    session.add(models.Organization(organization_id=ORG, name="T"))
    session.add(models.Organization(organization_id=ORG2, name="T2"))
    session.flush()


def _free_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DEFAULT_PLAN", "free")


# ── plan resolution ──────────────────────────────────────────────────────────
def test_null_plan_resolves_to_the_deployment_default(session, orgs, monkeypatch):
    from app.config import settings

    assert entitlements.effective_plan(session, ORG) is PlanTier(settings.DEFAULT_PLAN)
    _free_default(monkeypatch)
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE


def test_the_rows_own_plan_beats_the_default(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    entitlements.set_plan(session, ORG, PlanTier.INTELLIGENCE)
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE


def test_an_unrecognised_plan_degrades_to_free_never_wider(session, orgs, monkeypatch):
    """A typo in the row or the env must not hand a tenant the platform plan."""
    org = session.get(models.Organization, ORG)
    org.plan = "platinum"  # not a plan
    session.flush()
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE

    from app.config import settings

    org.plan = None
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "unlimited")  # not a plan
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE


# ── the trial belongs to the books ───────────────────────────────────────────
def test_the_free_month_is_per_books_not_per_signup(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    trial = entitlements.begin_trial(session, ORG, "60005555")
    assert trial is not None
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE

    # The farming move: a fresh organization reconnects the same company.
    assert entitlements.begin_trial(session, ORG2, "60005555") is None
    assert entitlements.effective_plan(session, ORG2) is PlanTier.FREE

    # Different books are a different business — they get their own month.
    assert entitlements.begin_trial(session, ORG2, "60007777") is not None


def test_an_expired_trial_is_spent_not_deleted(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    trial = entitlements.begin_trial(session, ORG, "60005555")
    trial.ends_at = clock.now() - timedelta(days=1)
    session.flush()
    assert entitlements.effective_plan(session, ORG) is PlanTier.FREE
    # The record remains, which is exactly what the next attempt must find.
    assert entitlements.begin_trial(session, ORG, "60005555") is None


def test_a_trial_lifts_to_intelligence_never_to_platform(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    entitlements.begin_trial(session, ORG, "60005555")
    entitlements.assert_feature(session, ORG, "intelligence")  # no raise
    with pytest.raises(entitlements.PlanRefused):
        entitlements.assert_feature(session, ORG, "multi_company")


# ── the connection gate ──────────────────────────────────────────────────────
def _connect(session, org, zoho_org):
    return conn.set_zoho_credentials(
        session, org, zoho_organization_id=zoho_org,
        client_id="cid", client_secret="sec", refresh_token="tok")


def test_a_second_company_needs_the_platform_plan(session, orgs, monkeypatch):
    _free_default(monkeypatch)
    _connect(session, ORG, "60001111")
    with pytest.raises(entitlements.PlanRefused) as e:
        _connect(session, ORG, "60002222")
    assert "Platform" in str(e.value)

    # Reconnecting the SAME company is an update, never a second company.
    _connect(session, ORG, "60001111")

    entitlements.set_plan(session, ORG, PlanTier.PLATFORM)
    _connect(session, ORG, "60002222")
    assert len(conn.list_connections(session, ORG)) == 2


def test_connecting_starts_the_trial_and_reconnecting_elsewhere_does_not(
        session, orgs, monkeypatch):
    _free_default(monkeypatch)
    _connect(session, ORG, "60001111")
    assert entitlements.effective_plan(session, ORG) is PlanTier.INTELLIGENCE

    _connect(session, ORG2, "60001111")
    assert entitlements.effective_plan(session, ORG2) is PlanTier.FREE


# ── over HTTP: the gate answers in plan language ─────────────────────────────
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

    from app.routers import decisions as decisions_router
    from app.routers import entitlements as entitlements_router
    from app.routers import platform_auth

    # Mirrors main.py: the decisions surface is gated at inclusion.
    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(entitlements_router.router)
    app.include_router(
        decisions_router.router,
        dependencies=[Depends(entitlements.require_feature("intelligence"))])

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


def _hdr(client, email="s.menon@sanketh.in"):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_free_org_is_refused_in_plan_language_and_a_paid_one_passes(
        client, monkeypatch):
    tc, Maker = client
    _free_default(monkeypatch)
    hdr = _hdr(tc)

    r = tc.get("/api/v1/decisions", headers=hdr)
    assert r.status_code == 403
    assert "Commercial Intelligence" in r.json()["detail"]
    assert "Quote Desk" in r.json()["detail"]

    s = Maker()
    entitlements.set_plan(s, "org_sanketh", PlanTier.INTELLIGENCE)
    s.commit()
    s.close()
    assert tc.get("/api/v1/decisions", headers=hdr).status_code == 200


def test_the_entitlements_read_says_what_a_screen_needs(client, monkeypatch):
    tc, _ = client
    _free_default(monkeypatch)
    body = tc.get("/api/v1/entitlements", headers=_hdr(tc)).json()
    assert body["plan"] == "free"
    assert body["effective_plan"] == "free"
    assert body["trial"] is None
    assert body["features"] == {"intelligence": False, "multi_company": False}
