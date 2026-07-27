"""Connection status and the one-click sync.

The point of this surface is that a person can tell, without a terminal,
whether the numbers on screen are their books or sample data.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_session
from app.domain import models
from app.routers import data_status, platform_auth
from app.seed import ensure_org_and_users


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(data_status.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "x"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_sample_data_is_named_as_such(client, monkeypatch):
    """Sample data and live data are indistinguishable by eye — so the product
    has to say which it is, or someone trusts a decision built on fixtures."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    body = client.get("/api/v1/data/status", headers=_hdr(client, "s.menon@sanketh.in")).json()
    assert body["connection"]["state"] == "SAMPLE_DATA"
    assert "sample data" in body["connection"]["headline"].lower()


def test_status_is_readable_by_a_salesperson_but_syncing_is_not(client, monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    sales = _hdr(client, "r.nair@sanketh.in")
    body = client.get("/api/v1/data/status", headers=sales).json()
    assert body["can_sync"] is False
    assert client.post("/api/v1/data/sync", headers=sales).status_code == 403


def test_sync_runs_the_whole_cycle_and_is_recorded(client, monkeypatch):
    """One action: pull, detect, decide — and a record that it happened."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    owner = _hdr(client, "s.menon@sanketh.in")

    before = client.get("/api/v1/data/status", headers=owner).json()
    assert before["last_sync"] is None

    run = client.post("/api/v1/data/sync", headers=owner).json()["run"]
    assert run["status"] == "OK"
    assert run["customers"] > 0 and run["sales_txns"] > 0

    after = client.get("/api/v1/data/status", headers=owner).json()
    assert after["last_sync"]["status"] == "OK"
    assert after["read_model"]["customers"] == run["customers"]


def test_a_failed_sync_is_recorded_not_swallowed(client, monkeypatch):
    """Silence about a failure is what made the connection unreadable before."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")

    class Boom:
        def list_contacts(self):
            raise RuntimeError("token rejected")

        list_items = list_invoices = list_bills = list_contacts

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda: Boom())
    owner = _hdr(client, "s.menon@sanketh.in")
    run = client.post("/api/v1/data/sync", headers=owner).json()["run"]
    assert run["status"] == "FAILED"
    assert "token rejected" in run["error"]

    # and it is still there on the next page load
    body = client.get("/api/v1/data/status", headers=owner).json()
    assert body["last_sync"]["status"] == "FAILED"


def test_wrong_organization_id_names_the_right_one(monkeypatch):
    """The most likely misconfiguration should hand back the correct value."""
    import app.ingestion.zoho_client as zc
    from app.routers.data_status import _connection

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    monkeypatch.setattr(settings, "ZOHO_ORGANIZATION_ID", "60036630487")

    class Fake:
        def ping(self):
            return {"authenticated": True, "organization_found": False,
                    "visible_organizations": [
                        {"organization_id": "60036630626", "name": "4U PRECISION"}]}

    monkeypatch.setattr(zc, "ZohoApiSource", lambda *a, **k: Fake())
    c = _connection(None, "org")
    assert c["state"] == "WRONG_ORG"
    assert "60036630626" in c["detail"] and "4U PRECISION" in c["detail"]
