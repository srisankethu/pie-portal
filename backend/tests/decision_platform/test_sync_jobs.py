"""Sync as a background job — the properties the blocking version could not have.

The old flow held the request open for the length of a pull and answered a
second click with "Sync is in progress". That sentence is the whole problem:
it does not say which sync, when it started, what it is doing, or whether it is
still alive. These tests pin the behaviour that replaces it.

The job runs inline here through the injectable dispatch rather than in a real
thread. A test that starts a thread and sleeps is a test that fails on a slow
machine and passes on a fast one, which is worse than no test.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_session
from app.domain import models
from app.ingestion import jobs
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_sanketh"
OWNER = "s.menon@sanketh.in"
SALES = "r.nair@sanketh.in"


class Empty:
    """A source that succeeds instantly and pulls nothing."""

    def list_contacts(self): return []
    def list_items(self): return []
    def list_users(self): return []
    def list_invoices(self, skip=None): return []
    def list_bills(self, skip=None): return []


@pytest.fixture()
def client(monkeypatch):
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
    app.include_router(data_status.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    monkeypatch.setattr("app.ingestion.sync.get_source",
                        lambda session, org, since=None, **kw: Empty())

    tc = TestClient(app)
    tc.Maker = Maker

    def inline(run_id, since, full, connection_id):
        js = Maker()
        try:
            run = js.get(models.SyncRun, run_id)
            jobs.execute_sync(js, run, since=since, full=full,
                              connection_id=connection_id)
            js.commit()
        finally:
            js.close()

    tc.inline = inline
    # Default: nothing runs, so a job stays QUEUED and the "already running"
    # behaviour is observable. Individual tests opt into inline execution.
    monkeypatch.setattr(jobs, "thread_dispatch", lambda *a: None)
    tc.monkeypatch = monkeypatch
    return tc


def _hdr(c, email=OWNER):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── starting ────────────────────────────────────────────────────────────────
def test_starting_a_sync_returns_immediately_with_a_job_to_watch(client):
    """202, not 200: the work is accepted, not done."""
    r = client.post("/api/v1/data/sync", headers=_hdr(client))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["started"] is True
    assert body["run"]["status"] == "QUEUED"
    assert body["run"]["active"] is True
    assert body["run"]["sync_run_id"]
    assert "background" in body["note"]


def test_the_state_is_readable_before_the_job_has_done_anything(client):
    """The screen renders from state, so the state has to exist from the start."""
    client.post("/api/v1/data/sync", headers=_hdr(client))
    body = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert body["state"] == "QUEUED"
    assert body["active"] is not None
    assert body["can_start"] is False


def test_an_organization_that_has_never_synced_is_idle_not_broken(client):
    body = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert body["state"] == "IDLE"
    assert body["active"] is None and body["last"] is None
    assert body["can_start"] is True


# ── not starting a second one ───────────────────────────────────────────────
def test_a_second_request_returns_the_running_job_instead_of_a_new_one(client):
    """The replacement for "Sync is in progress" — which sync, since when."""
    first = client.post("/api/v1/data/sync", headers=_hdr(client)).json()
    second = client.post("/api/v1/data/sync", headers=_hdr(client)).json()

    assert second["started"] is False
    assert second["run"]["sync_run_id"] == first["run"]["sync_run_id"], (
        "the caller must be handed the job already in flight, not a new one")
    assert second["run"]["started_at"] == first["run"]["started_at"]

    s = client.Maker()
    assert s.query(models.SyncRun).count() == 1, "no second pull was queued"
    s.close()


def test_a_second_request_is_not_an_error(client):
    """A 409 would make the screen translate a failure into a status. It is not
    a failure — the user asked for a sync and a sync is happening."""
    client.post("/api/v1/data/sync", headers=_hdr(client))
    assert client.post("/api/v1/data/sync", headers=_hdr(client)).status_code == 202


def test_a_finished_job_does_not_block_the_next_one(client):
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    first = client.post("/api/v1/data/sync", headers=_hdr(client)).json()
    assert first["run"]["status"] == "OK"

    second = client.post("/api/v1/data/sync", headers=_hdr(client)).json()
    assert second["started"] is True
    assert second["run"]["sync_run_id"] != first["run"]["sync_run_id"]


# ── a job that died ─────────────────────────────────────────────────────────
def _age(client, run_id, minutes):
    s = client.Maker()
    run = s.get(models.SyncRun, run_id)
    old = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    run.started_at = old
    run.heartbeat_at = old
    s.commit()
    s.close()


def test_a_job_that_stopped_reporting_is_reaped_not_believed(client):
    """The worst possible outcome is a dead job that looks like a live one: it
    blocks every future sync and looks exactly like the feature working."""
    started = client.post("/api/v1/data/sync", headers=_hdr(client)).json()
    _age(client, started["run"]["sync_run_id"], minutes=30)

    body = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert body["active"] is None
    assert body["can_start"] is True
    assert body["last"]["status"] == "FAILED"
    assert "assumed dead" in body["last"]["error"]


def test_a_reaped_job_lets_the_next_sync_start(client):
    first = client.post("/api/v1/data/sync", headers=_hdr(client)).json()
    _age(client, first["run"]["sync_run_id"], minutes=30)

    second = client.post("/api/v1/data/sync", headers=_hdr(client)).json()
    assert second["started"] is True


def test_a_slow_job_is_not_reaped(client):
    """A long pull is normal. Staleness is judged from the heartbeat, not from
    how long the job has been going."""
    started = client.post("/api/v1/data/sync", headers=_hdr(client)).json()
    s = client.Maker()
    run = s.get(models.SyncRun, started["run"]["sync_run_id"])
    run.started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    run.heartbeat_at = datetime.now(timezone.utc)      # still alive
    run.status = "RUNNING"
    s.commit()
    s.close()

    body = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert body["active"] is not None, "three hours of progress is not a dead job"
    assert body["can_start"] is False


# ── progress, completion, failure ───────────────────────────────────────────
def test_the_job_reports_what_it_is_doing(client):
    """No fake percentage. The phases are real stage boundaries."""
    seen: list[str] = []
    s = client.Maker()
    run = models.SyncRun(organization_id=ORG, source="fixture", status="QUEUED",
                         started_at=datetime.now(timezone.utc))
    s.add(run)
    s.commit()

    original = jobs.execute_sync

    # Watch the phase column as the run proceeds.
    real_flush = s.flush

    def record(*a, **kw):
        if run.phase and (not seen or seen[-1] != run.phase):
            seen.append(run.phase)
        return real_flush(*a, **kw)

    s.flush = record
    original(s, run, since=date(2025, 1, 1))
    s.commit()
    s.close()

    assert "Starting" in seen
    assert any("customers" in p.lower() for p in seen), seen
    assert any("invoices" in p.lower() for p in seen), seen
    assert run.phase is None, "a finished run is not doing anything"


def test_a_completed_job_carries_its_summary(client):
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client))

    body = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert body["state"] == "OK"
    assert body["active"] is None and body["can_start"] is True
    assert body["last"]["finished_at"], "the completion time is what the card shows"
    assert body["last_successful_at"] == body["last"]["started_at"]


def test_a_failed_job_persists_its_reason_and_allows_a_retry(client):
    class Boom:
        def list_contacts(self): raise RuntimeError("token rejected")
        list_items = list_users = list_contacts
        def list_invoices(self, skip=None): raise RuntimeError("token rejected")
        list_bills = list_invoices

    client.monkeypatch.setattr("app.ingestion.sync.get_source",
                              lambda session, org, since=None, **kw: Boom())
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client))

    body = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert body["state"] == "FAILED"
    assert "token rejected" in body["last"]["error"], "the diagnostic is kept"
    assert body["can_start"] is True, "a failure must be retryable"


def test_a_partial_run_is_not_reported_as_the_last_successful_sync(client):
    """It wrote rows, but it did not finish. Calling it successful overstates
    what is actually held."""
    s = client.Maker()
    now = datetime.now(timezone.utc)
    s.add(models.SyncRun(organization_id=ORG, source="fixture", status="OK",
                         started_at=now - timedelta(days=2),
                         finished_at=now - timedelta(days=2)))
    s.add(models.SyncRun(organization_id=ORG, source="fixture", status="PARTIAL",
                         started_at=now - timedelta(hours=1),
                         finished_at=now - timedelta(hours=1)))
    s.commit()
    s.close()

    body = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert body["last"]["status"] == "PARTIAL", "the latest run is still the latest"
    assert body["last_successful_at"].startswith(
        (now - timedelta(days=2)).date().isoformat()), (
        "but the last *successful* sync is the older, complete one")


# ── who may do what ─────────────────────────────────────────────────────────
def test_a_salesperson_may_watch_a_sync_without_being_able_to_start_one(client):
    """They cannot refresh the books, but they are entitled to know the figures
    they are reading are mid-refresh."""
    client.post("/api/v1/data/sync", headers=_hdr(client))
    sales = _hdr(client, SALES)

    assert client.get("/api/v1/data/sync", headers=sales).json()["active"] is not None
    assert client.post("/api/v1/data/sync", headers=sales).status_code == 403


def test_the_state_survives_a_page_reload(client):
    """Persistence is the point: the job lives in the database, not in a tab."""
    started = client.post("/api/v1/data/sync", headers=_hdr(client)).json()

    # A completely fresh client — a new tab, a reload, a different device.
    again = client.get("/api/v1/data/sync", headers=_hdr(client)).json()
    assert again["active"]["sync_run_id"] == started["run"]["sync_run_id"]
    assert again["can_start"] is False
