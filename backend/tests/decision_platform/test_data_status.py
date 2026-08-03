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
from datetime import date

from app.ingestion import jobs
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users


@pytest.fixture()
def client(monkeypatch):
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

    # The sync now runs in the background. These tests are about the *cycle* —
    # what a pull writes and what it records — so they run the job inline on the
    # test's own session rather than racing a thread. This is what the
    # injectable dispatch in ``jobs.start_sync`` exists for; the asynchronous
    # path itself is covered in test_sync_jobs.py.
    def _inline(run_id, since, full, connection_id):
        js = Maker()
        try:
            run = js.get(models.SyncRun, run_id)
            jobs.execute_sync(js, run, since=since, full=full,
                              connection_id=connection_id)
            js.commit()
        finally:
            js.close()

    monkeypatch.setattr(jobs, "thread_dispatch", _inline)
    tc = TestClient(app)
    tc.Maker = Maker    # exposed so a test can seed/inspect data outside the API
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
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

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda session, org, since=None: Boom())
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

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda session, org, since=None: Throttled())
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

    starts: list = []

    def _capture(session, org, since=None, **kw):
        starts.append(since)
        seen["since"] = since
        return mock_source.FixtureZohoSource()

    monkeypatch.setattr("app.ingestion.sync.get_source", _capture)
    owner = _hdr(client, "s.menon@sanketh.in")
    run = client.post("/api/v1/data/sync", headers=owner,
                      json={"since": "2025-01-01"}).json()["run"]

    # The pull is read in monthly slices, so there is a source per window and
    # the operator's date is where the first one starts.
    assert starts[0].isoformat() == "2025-01-01", "the date must reach the source"
    assert run["since"] == "2025-01-01", "and be recorded, so the window is auditable"
    assert run["windows_total"] == len(
        jobs.plan_windows(date(2025, 1, 1))), "every planned window was read from"
    assert starts[1].isoformat() == "2025-01-01", (
        "the first slice begins on the requested date, not the month boundary")


def test_wrong_organization_id_names_the_right_one(client, monkeypatch):
    """The most likely misconfiguration should hand back the correct value."""
    import app.routers.data_status as data_status_mod

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    # No stored connection for the default org: falls back to settings, the
    # pre-multi-tenant configuration path this test exercises.
    monkeypatch.setattr(settings, "ZOHO_ORGANIZATION_ID", "60036630487")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "ZOHO_CLIENT_SECRET", "csec")
    monkeypatch.setattr(settings, "ZOHO_REFRESH_TOKEN", "rtok")

    class Fake:
        def __init__(self, *a, **k):
            pass

        def ping(self):
            return {"authenticated": True, "organization_found": False,
                    "visible_organizations": [
                        {"organization_id": "60036630626", "name": "4U PRECISION"}]}

    monkeypatch.setattr("app.ingestion.zoho_client.ZohoApiSource", Fake)
    s = client.Maker()
    c = data_status_mod._connection(s, settings.DEFAULT_ORG_ID)
    s.close()
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

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda session, org, since=None: Empty())
    owner = _hdr(client, "s.menon@sanketh.in")
    body = client.post("/api/v1/data/sync", headers=owner).json()

    # Reported on the run rather than in the response: by the time a real pull
    # has anything to say, the response that started it is long gone.
    assert body["run"]["status"] == "OK"
    removed = body["run"]["notes"]["demo_data_removed"]
    assert removed["customers"] == 5
    assert removed["products"] == 4
    assert removed["decisions"] > 0

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

    monkeypatch.setattr("app.ingestion.sync.get_source", lambda session, org, since=None: Empty())
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


# ── multi-tenant Zoho connections ──────────────────────────────────────────
def test_not_configured_when_the_org_has_no_connection(client, monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    monkeypatch.setattr(settings, "ZOHO_ORGANIZATION_ID", "")
    body = client.get("/api/v1/data/status", headers=_hdr(client, "s.menon@sanketh.in")).json()
    assert body["connection"]["state"] == "NOT_CONFIGURED"


class _FakePing:
    """A ZohoApiSource stand-in that never touches the network — just reports
    whatever organization id it was constructed with as findable."""

    def __init__(self, credentials=None, **kw):
        self.credentials = credentials

    def ping(self):
        return {"authenticated": True, "organization_found": True,
                "organization_id": self.credentials.organization_id,
                "organization_name": f"Org {self.credentials.organization_id}",
                "currency": "INR"}


def test_owner_can_connect_zoho_and_status_reflects_it(client, monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    monkeypatch.setattr("app.ingestion.zoho_client.ZohoApiSource", _FakePing)
    owner = _hdr(client, "s.menon@sanketh.in")

    before = client.get("/api/v1/data/status", headers=owner).json()
    assert before["connection"]["state"] == "NOT_CONFIGURED"
    assert before["can_manage_connection"] is True

    r = client.put("/api/v1/data/connection", headers=owner, json={
        "zoho_organization_id": "60036630626", "client_id": "1000.CID",
        "client_secret": "the-secret", "refresh_token": "1000.rtok.abc",
    })
    assert r.status_code == 200
    assert r.json()["connection"]["state"] == "CONNECTED"
    assert r.json()["connection"]["organization_id"] == "60036630626"

    after = client.get("/api/v1/data/status", headers=owner).json()
    assert after["connection"]["state"] == "CONNECTED"

    # the secret is never echoed back anywhere in the response
    assert "the-secret" not in r.text


def test_a_manager_cannot_manage_the_connection(client, monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    manager = _hdr(client, "m.rao@sanketh.in")

    r = client.put("/api/v1/data/connection", headers=manager, json={
        "zoho_organization_id": "1", "client_id": "c", "client_secret": "s",
        "refresh_token": "r",
    })
    assert r.status_code == 403

    body = client.get("/api/v1/data/status", headers=manager).json()
    assert body["can_manage_connection"] is False


def test_owner_can_disconnect_zoho(client, monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    monkeypatch.setattr("app.ingestion.zoho_client.ZohoApiSource", _FakePing)
    owner = _hdr(client, "s.menon@sanketh.in")
    client.put("/api/v1/data/connection", headers=owner, json={
        "zoho_organization_id": "1", "client_id": "c", "client_secret": "s",
        "refresh_token": "r",
    })

    r = client.delete("/api/v1/data/connection", headers=owner)
    assert r.status_code == 200 and r.json()["removed"] is True
    assert r.json()["connection"]["state"] == "NOT_CONFIGURED"

    # a second delete is a no-op, not an error
    assert client.delete("/api/v1/data/connection", headers=owner).json()["removed"] is False


def test_incomplete_connection_fields_are_rejected(client, monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")
    owner = _hdr(client, "s.menon@sanketh.in")
    r = client.put("/api/v1/data/connection", headers=owner, json={
        "zoho_organization_id": "1", "client_id": "", "client_secret": "s", "refresh_token": "r",
    })
    assert r.status_code == 400


def test_two_tenants_sync_from_their_own_zoho_connection_only(client, monkeypatch):
    """The end-to-end multi-tenant proof: two fully separate organizations,
    each with its own Zoho connection, must each pull from their own account —
    never from each other's, even when both syncs run against the same
    process and the same 'api' source setting."""
    from app.provision_org import provision_organization

    s = client.Maker()
    provision_organization(s, organization_id="org_4u", name="4U Precision",
                           owner_email="owner@4u.example", owner_name="4U Owner",
                           owner_password=SEED_PASSWORD)
    s.commit()
    s.close()

    monkeypatch.setattr(settings, "ZOHO_SOURCE", "api")

    class RecordingSource:
        """Records the Zoho organization id it was actually constructed
        with, and returns nothing — the point is proving *which* credentials
        were used, not exercising the pull itself."""
        seen: list[str] = []

        def __init__(self, credentials=None, since=None, **kw):
            RecordingSource.seen.append(credentials.organization_id)
            self.documents_fetched = self.documents_resumed = 0

        def list_contacts(self): return []
        def list_items(self): return []
        def list_users(self): return []
        def list_invoices(self, skip=None): return []
        def list_bills(self, skip=None): return []

    monkeypatch.setattr("app.ingestion.zoho_client.ZohoApiSource", RecordingSource)

    default_owner = _hdr(client, "s.menon@sanketh.in")
    other_owner = _hdr(client, "owner@4u.example")

    client.put("/api/v1/data/connection", headers=default_owner, json={
        "zoho_organization_id": "DEFAULT-ORG-ZOHO-ID", "client_id": "c1",
        "client_secret": "s1", "refresh_token": "r1"})
    client.put("/api/v1/data/connection", headers=other_owner, json={
        "zoho_organization_id": "OTHER-ORG-ZOHO-ID", "client_id": "c2",
        "client_secret": "s2", "refresh_token": "r2"})

    # each sync response also re-checks the connection it just used (for the
    # returned status), so more than one ZohoApiSource may be built per call —
    # what matters is that every one of them was built for the right tenant.
    RecordingSource.seen.clear()
    assert client.post("/api/v1/data/sync", headers=default_owner).json()["run"]["status"] == "OK"
    assert set(RecordingSource.seen) == {"DEFAULT-ORG-ZOHO-ID"}

    RecordingSource.seen.clear()
    assert client.post("/api/v1/data/sync", headers=other_owner).json()["run"]["status"] == "OK"
    assert set(RecordingSource.seen) == {"OTHER-ORG-ZOHO-ID"}
