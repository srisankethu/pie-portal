"""The automatic sync: a patient finger on the existing button.

Two promises under test. The schedule itself: a connected organization's books
refresh without anyone remembering to press Sync, on a cadence the tenant
chooses, through the same ``start_sync`` a person's click goes through — so
every guard that protects a manual pull protects a scheduled one. And the
window: a scheduled run re-reads the organization's *existing coverage*, never
"since the last sync" — Zoho's list filters are by document date, so a bill
dated the 3rd and entered the 11th falls outside any window that starts at the
10th. The intuitive design is the one that loses documents.
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
from app.ingestion import jobs, scheduler
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


# ── the decision, from facts alone ───────────────────────────────────────────

def test_due_never_fires_when_switched_off():
    assert scheduler.due(None, 0, NOW) is False
    assert scheduler.due(NOW - timedelta(days=30), 0, NOW) is False


def test_a_connected_organization_that_never_synced_is_due_immediately():
    """Connect Zoho, walk away, find the data arrived anyway."""
    assert scheduler.due(None, 6, NOW) is True


def test_due_measures_from_the_start_of_the_last_run():
    assert scheduler.due(NOW - timedelta(hours=5, minutes=59), 6, NOW) is False
    assert scheduler.due(NOW - timedelta(hours=6), 6, NOW) is True


def test_the_cadence_is_the_orgs_own_choice_with_a_clamped_default(monkeypatch):
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    org = models.Organization(organization_id="o", name="O")

    assert scheduler.auto_sync_hours(None) == 6, "no org row: the default"
    assert scheduler.auto_sync_hours(org) == 6, "no choice recorded: the default"
    org.config = {"auto_sync_hours": 12}
    assert scheduler.auto_sync_hours(org) == 12
    org.config = {"auto_sync_hours": 0}
    assert scheduler.auto_sync_hours(org) == 0, "zero is a choice, not absence"
    # Writable JSON is not trusted: garbage degrades to the default, bounds clamp.
    org.config = {"auto_sync_hours": "soon"}
    assert scheduler.auto_sync_hours(org) == 6
    org.config = {"auto_sync_hours": 9999}
    assert scheduler.auto_sync_hours(org) == 24 * 7


# ── the tick, against a real session ─────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    return Maker


@pytest.fixture()
def queued(monkeypatch):
    """Capture what the scheduler queues instead of running real pull threads."""
    runs: list[tuple[str, date, bool]] = []
    monkeypatch.setattr(jobs, "thread_dispatch",
                        lambda run_id, since, full, conn: runs.append(
                            (run_id, since, full)))
    return runs


def _org_with_connection(session, org_id="org_s", hours=None):
    config = {} if hours is None else {"auto_sync_hours": hours}
    session.add(models.Organization(organization_id=org_id, name=org_id,
                                    config=config))
    session.add(models.ZohoConnection(connection_id=f"conn_{org_id}",
                                      organization_id=org_id,
                                      zoho_organization_id="z1", enabled=True))
    session.flush()


def test_tick_queues_a_run_for_a_due_organization(db, queued, monkeypatch):
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    session = db()
    _org_with_connection(session)
    session.commit()

    assert scheduler.tick(session) == 1
    session.commit()

    run = session.query(models.SyncRun).one()
    assert run.triggered_by == "scheduler", "the audit trail says who asked"
    assert len(queued) == 1


def test_tick_does_not_pile_onto_a_running_or_recent_sync(db, queued, monkeypatch):
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    session = db()
    _org_with_connection(session)
    session.commit()

    assert scheduler.tick(session) == 1
    session.commit()
    # The run is QUEUED/RUNNING: the next tick hands the existing job back
    # rather than starting a second pull of the same books.
    assert scheduler.tick(session) == 0
    # And once it finishes, "recent" still means not due.
    run = session.query(models.SyncRun).one()
    run.status = "OK"
    session.commit()
    assert scheduler.tick(session) == 0
    assert len(queued) == 1


def test_an_organization_that_switched_off_stays_off(db, queued, monkeypatch):
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    session = db()
    _org_with_connection(session, hours=0)
    session.commit()

    assert scheduler.tick(session) == 0
    assert queued == []


def test_the_scheduled_window_is_the_existing_coverage_not_the_last_sync(
        db, queued, monkeypatch):
    """The whole point of the design. A run that asked only for the days since
    the last sync would silently drop every backdated entry — and accountants
    backdate routinely, because paper arrives after the goods."""
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    session = db()
    _org_with_connection(session)
    old = jobs._now() - timedelta(days=30)
    session.add(models.SyncRun(organization_id="org_s", source="api",
                               status="OK", since=date(2025, 1, 1),
                               started_at=old, heartbeat_at=old))
    session.add(models.SyncRun(organization_id="org_s", source="api",
                               status="OK", since=date(2025, 6, 1),
                               started_at=old, heartbeat_at=old))
    session.commit()

    assert scheduler.tick(session) == 1
    session.commit()

    run = (session.query(models.SyncRun)
           .filter_by(triggered_by="scheduler").one())
    assert run.since == date(2025, 1, 1), (
        "the earliest window ever covered — never narrowed to the last run")


def test_the_scheduler_declines_a_sample_data_deployment(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    assert scheduler.start_scheduler() is False, (
        "sample data has nothing to keep fresh")


# ── the control surface ──────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    s = db()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(data_status.router)

    def _override():
        sess = db()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = db
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_cadence_is_readable_by_all_and_settable_by_managers(client, monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    monkeypatch.setattr(settings, "SYNC_AUTO_HOURS", 6)
    owner = _hdr(client, "s.menon@sanketh.in")
    sales = _hdr(client, "r.nair@sanketh.in")

    body = client.get("/api/v1/data/status", headers=sales).json()
    assert body["auto_sync"]["hours"] == 6
    assert body["auto_sync"]["available"] is False, "sample data: nothing to schedule"

    assert client.put("/api/v1/data/auto-sync", headers=sales,
                      json={"hours": 12}).status_code == 403

    r = client.put("/api/v1/data/auto-sync", headers=owner, json={"hours": 12})
    assert r.status_code == 200 and r.json()["auto_sync"]["hours"] == 12
    # Persisted on the organization, so it survives a restart.
    assert client.get("/api/v1/data/status",
                      headers=owner).json()["auto_sync"]["hours"] == 12

    assert client.put("/api/v1/data/auto-sync", headers=owner,
                      json={"hours": 500}).status_code == 400
    assert client.put("/api/v1/data/auto-sync", headers=owner,
                      json={"hours": 0}).status_code == 200, "off is a valid choice"
