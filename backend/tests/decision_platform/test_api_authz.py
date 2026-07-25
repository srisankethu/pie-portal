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
from app.routers import decisions, internal, platform_auth
from app.seed import ensure_org_and_users

ORG = "org_sanketh"


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
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "x"})
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
    r = client.post("/api/v1/auth/login", json={"email": "r.nair@sanketh.in", "password": "x"})
    assert r.json()["role"] == "SALESPERSON"


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

    sales = _hdr(_login(client, "r.nair@sanketh.in"))
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
    mgr = _hdr(_login(client, "m.rao@sanketh.in"))
    ids = {d["decision_id"] for d in client.get("/api/v1/decisions", headers=mgr).json()}
    assert {dormancy, margin} <= ids
    assert client.get(f"/api/v1/decisions/{margin}", headers=mgr).status_code == 200


def test_organization_isolation_at_api(client_and_maker):
    client, maker = client_and_maker
    # a decision in a different org must never surface to this org's principal
    foreign = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="c",
                             assigned_user_id="usr_sales", org="org_other")
    owner = _hdr(_login(client, "s.menon@sanketh.in"))
    ids = {d["decision_id"] for d in client.get("/api/v1/decisions", headers=owner).json()}
    assert foreign not in ids
    assert client.get(f"/api/v1/decisions/{foreign}", headers=owner).status_code == 404


def test_human_action_lifecycle(client_and_maker):
    client, maker = client_and_maker
    did = _seed_decision(maker, dtype="CUSTOMER_DORMANCY", subject_id="cust1",
                         assigned_user_id="usr_sales")
    sales = _hdr(_login(client, "r.nair@sanketh.in"))

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


def test_sync_requires_manager_or_owner(client_and_maker):
    client, _ = client_and_maker
    sales = _hdr(_login(client, "r.nair@sanketh.in"))
    owner = _hdr(_login(client, "s.menon@sanketh.in"))
    assert client.post("/api/v1/internal/sync/zoho", headers=sales).status_code == 403
    r = client.post("/api/v1/internal/sync/zoho", headers=owner)
    assert r.status_code == 200
    # fixture source populated the read model for this org
    assert r.json()["customers"] >= 1


def test_health(client_and_maker):
    client, _ = client_and_maker
    r = client.get("/api/v1/internal/health")
    assert r.status_code == 200 and r.json()["database"] is True
