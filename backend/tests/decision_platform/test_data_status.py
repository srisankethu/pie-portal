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
    tc = TestClient(app)
    tc.Maker = Maker    # exposed so a test can seed/inspect data outside the API
    return tc


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
    # a fresh sync writes rate/discount on every cost record — nothing pending
    assert after["read_model"]["cost_records_pending_discount_backfill"] == 0


def test_a_failed_sync_is_recorded_not_swallowed(client, monkeypatch):
    """Silence about a failure is what made the connection unreadable before."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")

    class Boom:
        def list_contacts(self):
            raise RuntimeError("token rejected")

        list_items = list_users = list_contacts

        def list_invoices(self, skip=None):
            raise RuntimeError("token rejected")

        list_bills = list_invoices

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda since=None: Boom())
    owner = _hdr(client, "s.menon@sanketh.in")
    run = client.post("/api/v1/data/sync", headers=owner).json()["run"]
    assert run["status"] == "FAILED"
    assert "token rejected" in run["error"]

    # and it is still there on the next page load
    body = client.get("/api/v1/data/status", headers=owner).json()
    assert body["last_sync"]["status"] == "FAILED"


def test_an_interrupted_pull_reports_what_it_wrote(client, monkeypatch):
    """The bug this fixes: a run that pulled 300 rows and then hit the rate
    limiter recorded FAILED with every counter at zero, while the rows sat in
    the database. The audit trail then contradicted the data."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")

    class Throttled:
        def list_contacts(self):
            return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"}]

        def list_items(self):
            return [{"item_id": "i1", "name": "Insert", "status": "active"}]

        def list_users(self):
            return []

        def list_bills(self, skip=None):
            return []

        def list_invoices(self, skip=None):
            raise RuntimeError("HTTP 429 (rate limited)")

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda since=None: Throttled())
    owner = _hdr(client, "s.menon@sanketh.in")
    run = client.post("/api/v1/data/sync", headers=owner).json()["run"]

    assert run["status"] == "PARTIAL", "rows landed — this is not a total failure"
    assert run["customers"] == 1 and run["products"] == 1
    assert "429" in run["error"]
    # and the rows really are there, ready for the next run to build on
    assert client.get("/api/v1/data/status",
                      headers=owner).json()["read_model"]["customers"] == 1


def test_the_operator_chooses_the_start_date(client, monkeypatch):
    """How far back the books are worth reading is a business judgement, so it
    is an input to the run rather than a constant in the code."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    seen: dict = {}

    from app.ingestion import mock_source

    def _capture(since=None):
        seen["since"] = since
        return mock_source.FixtureZohoSource()

    monkeypatch.setattr("app.ingestion.sync.get_source", _capture)
    owner = _hdr(client, "s.menon@sanketh.in")
    run = client.post("/api/v1/data/sync", headers=owner,
                      json={"since": "2025-01-01"}).json()["run"]

    assert seen["since"].isoformat() == "2025-01-01", "the date must reach the source"
    assert run["since"] == "2025-01-01", "and be recorded, so the window is auditable"


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


# ── a real sync must remove leftover demo data ─────────────────────────────
def test_a_live_sync_removes_leftover_demo_data(client, monkeypatch):
    """The bug this guards: fabricated customers/decisions from the demo seed
    were left sitting alongside real Zoho data forever, indistinguishable from
    the real ones once someone actually linked their account."""
    from app.demo import seed_demo

    s = client.Maker()
    seed_demo(s)
    s.commit()
    assert s.query(models.Customer).filter_by(customer_id="cst_rane").count() == 1
    assert s.query(models.Decision).count() > 0
    s.close()

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")

    class Empty:
        def list_contacts(self): return []
        def list_items(self): return []
        def list_users(self): return []
        def list_invoices(self, skip=None): return []
        def list_bills(self, skip=None): return []

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda since=None: Empty())
    owner = _hdr(client, "s.menon@sanketh.in")
    body = client.post("/api/v1/data/sync", headers=owner).json()

    assert body["run"]["status"] == "OK"
    assert body["demo_data_removed"]["customers"] == 5
    assert body["demo_data_removed"]["products"] == 4
    assert body["demo_data_removed"]["decisions"] > 0

    s = client.Maker()
    assert s.query(models.Customer).filter_by(customer_id="cst_rane").count() == 0
    assert s.query(models.Decision).count() == 0
    s.close()


def test_a_second_live_sync_has_nothing_left_to_remove(client, monkeypatch):
    """Idempotent: once the demo data is gone, every count is zero and the key
    is not reported at all — it must not look like something happened."""
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")

    class Empty:
        def list_contacts(self): return []
        def list_items(self): return []
        def list_users(self): return []
        def list_invoices(self, skip=None): return []
        def list_bills(self, skip=None): return []

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda since=None: Empty())
    owner = _hdr(client, "s.menon@sanketh.in")
    body = client.post("/api/v1/data/sync", headers=owner).json()
    assert "demo_data_removed" not in body


def test_a_fixture_source_sync_does_not_purge_demo_data(client, monkeypatch):
    """The purge is specifically tied to a real Zoho pull. Syncing against the
    offline fixture source (dev/demo mode) must leave the demo dataset alone."""
    from app.demo import seed_demo

    s = client.Maker()
    seed_demo(s)
    s.commit()
    s.close()

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    owner = _hdr(client, "s.menon@sanketh.in")
    body = client.post("/api/v1/data/sync", headers=owner).json()

    assert "demo_data_removed" not in body
    s = client.Maker()
    assert s.query(models.Customer).filter_by(customer_id="cst_rane").count() == 1
    s.close()
