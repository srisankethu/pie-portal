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

import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.config import settings
from app.db import get_session
from app.domain import models
from app.ingestion import jobs
from app.routers import data_status, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"


class Empty:
    """A source that succeeds instantly and pulls nothing."""

    def list_contacts(self): return []
    def list_items(self): return []
    def list_users(self): return []
    def list_invoices(self, skip=None): return []
    def list_bills(self, skip=None): return []


@pytest.fixture()
def client(monkeypatch):
    engine = dbsupport.fresh_engine()
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


# ── slicing a long pull into calendar windows ───────────────────────────────
#
# Zoho will not say how many documents it holds until they have been paged
# through, so a percentage of documents would be invented. The months between
# the start date and today are known before the first call, which is what makes
# "month 7 of 18" a fact rather than a guess.
def test_a_single_month_is_not_sliced(client):
    """Slicing a short range costs round trips and buys nothing."""
    assert jobs.plan_windows(date(2026, 3, 5), date(2026, 3, 28)) == [
        (date(2026, 3, 5), date(2026, 3, 28))]


def test_a_long_range_is_split_into_whole_months(client):
    w = jobs.plan_windows(date(2025, 11, 20), date(2026, 2, 10))
    assert w == [
        (date(2025, 11, 20), date(2025, 11, 30)),
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 10)),
    ]


def test_windows_are_contiguous_and_never_overlap(client):
    """An overlap double-counts a document into two windows; a gap loses it."""
    w = jobs.plan_windows(date(2024, 1, 15), date(2026, 8, 3))
    assert w[0][0] == date(2024, 1, 15) and w[-1][1] == date(2026, 8, 3)
    for (_, end), (nxt, _) in zip(w, w[1:]):
        assert nxt == end + timedelta(days=1), f"{end} -> {nxt}"


def test_windows_run_oldest_first(client):
    """An interrupted pull should leave the history in place and the recent end
    missing — the recent end is what the next run fetches anyway."""
    w = jobs.plan_windows(date(2024, 1, 1), date(2026, 1, 1))
    assert w == sorted(w)


def test_a_very_long_history_widens_the_slices_rather_than_exploding(client):
    """Twenty years of books must not become 240 round trips."""
    w = jobs.plan_windows(date(2006, 1, 1), date(2026, 1, 1))
    assert len(w) <= jobs.MAX_WINDOWS
    assert w[0][0] == date(2006, 1, 1) and w[-1][1] == date(2026, 1, 1)


def test_a_backwards_range_does_not_hang(client):
    assert jobs.plan_windows(date(2026, 5, 1), date(2025, 5, 1)) == [
        (date(2026, 5, 1), date(2026, 5, 1))]


def test_the_run_records_how_much_of_the_window_it_has_read(client):
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"since": "2025-01-01"})

    last = client.get("/api/v1/data/sync", headers=_hdr(client)).json()["last"]
    assert last["windows_total"] > 1, "a year and a half is worth slicing"
    assert last["windows_done"] == last["windows_total"], (
        "a completed run has read every window it planned")


def test_reference_data_is_read_once_not_once_per_window(client):
    """The contact list is the whole master list whatever the window. Reading it
    per slice would turn a fix for one problem into a worse one."""
    calls = {"contacts": 0, "items": 0, "invoices": 0}

    class Counting(Empty):
        def list_contacts(self):
            calls["contacts"] += 1
            return []

        def list_items(self):
            calls["items"] += 1
            return []

        def list_invoices(self, skip=None):
            calls["invoices"] += 1
            return []

    client.monkeypatch.setattr("app.ingestion.sync.get_source",
                               lambda session, org, since=None, **kw: Counting())
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"since": "2025-01-01"})

    last = client.get("/api/v1/data/sync", headers=_hdr(client)).json()["last"]
    assert calls["contacts"] == 1, f"read {calls['contacts']} times"
    assert calls["items"] == 1, f"read {calls['items']} times"
    assert calls["invoices"] == last["windows_total"], (
        "documents, by contrast, are read once per window")


def test_a_pull_interrupted_part_way_keeps_the_windows_it_finished(client):
    """Slicing does not create the partial-progress case — it makes it legible."""
    seen: list[int] = []

    class Breaks(Empty):
        def list_invoices(self, skip=None):
            seen.append(1)
            if len(seen) >= 3:
                raise RuntimeError("Zoho rate limit")
            return []

    client.monkeypatch.setattr("app.ingestion.sync.get_source",
                               lambda session, org, since=None, **kw: Breaks())
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"since": "2025-01-01"})

    last = client.get("/api/v1/data/sync", headers=_hdr(client)).json()["last"]
    assert last["status"] in ("PARTIAL", "FAILED")
    assert last["windows_done"] == 2, "the two windows it got through are recorded"
    assert last["windows_done"] < last["windows_total"]
    assert "rate limit" in last["error"]


# ── what a finished run is able to say about itself ─────────────────────────
def test_the_supply_stage_reports_what_it_read(client):
    """These counters existed in the report from the day the supply stage was
    added, but ``SyncRun`` had no columns for them, so nothing the stage did
    reached the screen. From the Data page that is indistinguishable from the
    stage never running — which is exactly how it was reported."""
    class WithSupply(Empty):
        def list_vendors(self):
            return [{"contact_id": "v1", "contact_name": "Kennametal India",
                     "status": "active"}]

        def list_customer_payments(self, skip=None):
            return [{"payment_id": "p1", "customer_id": "c1",
                     "date": "2026-06-10", "amount": 1000, "invoices": []}]

        def list_purchase_orders(self):
            return [{"purchaseorder_id": "po1", "purchaseorder_number": "PO-1",
                     "date": "2026-05-01", "vendor_id": "v1", "status": "issued",
                     "line_items": []}]

    client.monkeypatch.setattr("app.ingestion.sync.get_source",
                               lambda session, org, since=None, **kw: WithSupply())
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client))

    last = client.get("/api/v1/data/sync", headers=_hdr(client)).json()["last"]
    assert last["vendors"] == 1
    assert last["purchase_orders"] == 1
    # The payment names a customer this empty source never returned, so it is
    # skipped — reported, not attached to a fictional account.
    assert last["payments"] == 0
    assert any(u["code"] == "UNKNOWN_CUSTOMER" for u in last["unresolved"])


def test_a_run_with_nothing_to_fix_carries_an_empty_worklist(client):
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client))
    last = client.get("/api/v1/data/sync", headers=_hdr(client)).json()["last"]
    assert last["unresolved"] == []
    assert last["vendors"] == 0 and last["payments"] == 0


def test_a_sync_leaves_business_state_built_for_today(client):
    """State is folded at the end of the pull, not on the next page load. A
    projection nobody builds is a projection nobody can read."""
    from app.clock import today as clock_today
    from app.domain import models
    from app.state.reducers.inventory import INVENTORY

    class WithStock(Empty):
        def list_items(self):
            return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                     "status": "active", "stock_on_hand": 40,
                     "available_stock": 35, "purchase_rate": "401.25"}]

    client.monkeypatch.setattr("app.ingestion.sync.get_source",
                               lambda session, org, since=None, **kw: WithStock())
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client))

    s = client.Maker()
    try:
        rows = s.query(models.BusinessState).filter_by(state=INVENTORY).all()
        assert rows, "the pull recorded stock but built no inventory state"
        assert {r.as_of for r in rows} == {clock_today()}
        # Stamped, like every other computed row in this schema.
        assert all(r.thresholds_version for r in rows)
    finally:
        s.close()


def test_a_state_build_that_fails_never_fails_the_pull(client):
    """The pull is the thing that cannot be redone cheaply; the projection is
    the thing that can."""
    def explode(*a, **kw):
        raise RuntimeError("reducer exploded")

    client.monkeypatch.setattr("app.state.engine.build", explode)
    client.monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    client.post("/api/v1/data/sync", headers=_hdr(client))

    last = client.get("/api/v1/data/sync", headers=_hdr(client)).json()["last"]
    assert last["status"] == "OK"


# ── two companies, two pulls ────────────────────────────────────────────────
#
# The start guard was scoped to a connection so that three connected Zoho
# companies could pull at once. That change was invisible from the screen,
# because the status endpoint still reported a single active run and a single
# organization-wide `can_start`, and every company's button was disabled by it.
# These four tests hold the whole path — guard, status, and the per-company
# flags the screen actually reads — so the concurrency cannot go quietly
# unreachable again.

def test_two_companies_pull_at_once(client):
    """Two connections are two independent APIs. Neither waits for the other."""
    a = client.post("/api/v1/data/sync", headers=_hdr(client),
                    json={"connection_id": "conn_sls"}).json()
    b = client.post("/api/v1/data/sync", headers=_hdr(client),
                    json={"connection_id": "conn_4u"}).json()

    assert a["started"] is True
    assert b["started"] is True, (
        "the second company must start its own pull, not be handed the first's")
    assert a["run"]["sync_run_id"] != b["run"]["sync_run_id"]

    s = client.Maker()
    assert s.query(models.SyncRun).count() == 2
    s.close()


def test_the_status_reports_every_pull_in_flight(client):
    """One active run in the response is what made the second company invisible:
    the screen had nothing to draw a second progress bar from."""
    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"connection_id": "conn_sls"})
    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"connection_id": "conn_4u"})

    state = client.get("/api/v1/data/sync", headers=_hdr(client)).json()

    assert len(state["active_runs"]) == 2
    assert sorted(state["busy_connections"]) == ["conn_4u", "conn_sls"]
    # The headline still names one job, and it is one of the two real ones.
    assert state["active"]["sync_run_id"] in {r["sync_run_id"] for r in state["active_runs"]}


def test_one_company_pulling_does_not_mark_another_busy(client):
    """The flag each button gates on. An organization-wide one is what disabled
    all three companies the moment any one of them started."""
    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"connection_id": "conn_sls"})

    state = client.get("/api/v1/data/sync", headers=_hdr(client)).json()

    assert state["busy_connections"] == ["conn_sls"]
    assert "conn_4u" not in state["busy_connections"]


def test_the_same_company_still_refuses_a_second_pull(client):
    """Concurrency across companies, never within one: two pulls of the same
    book would fight over the same rows."""
    first = client.post("/api/v1/data/sync", headers=_hdr(client),
                        json={"connection_id": "conn_sls"}).json()
    again = client.post("/api/v1/data/sync", headers=_hdr(client),
                        json={"connection_id": "conn_sls"}).json()

    assert again["started"] is False
    assert again["run"]["sync_run_id"] == first["run"]["sync_run_id"]
    assert client.get("/api/v1/data/sync",
                      headers=_hdr(client)).json()["busy_connections"] == ["conn_sls"]


# ── "Sync every company" means every company ─────────────────────────────────
#
# The button said so and its tooltip said "Runs one pull per enabled company, in
# turn". The server did not: a run with no connection named resolved credentials
# through `get_zoho_credentials`, whose no-argument form falls back to the
# organization's *first* enabled row. A three-company organization pulled one
# company for ever, and — because the rows it wrote carried no connection at all
# — the Company filter, which builds its options from the rows, had nothing to
# offer and hid itself. One connector visible in a product built for three.

def _connect(client, *companies: tuple[str, str, bool]) -> None:
    """Give the organization some connected Zoho companies.

    `(connection_id, label, enabled)`. No credentials: `get_source` is patched
    in every test here, so nothing ever decrypts one.
    """
    s = client.Maker()
    for connection_id, label, enabled in companies:
        s.add(models.ZohoConnection(
            connection_id=connection_id, organization_id=ORG, label=label,
            enabled=enabled, zoho_organization_id=f"zoho-{connection_id}"))
    s.commit()
    s.close()


def _pulled_connections(client, monkeypatch) -> list[str | None]:
    """The connection each source was built for, in order, as the run asked."""
    seen: list[str | None] = []

    def record(session, org, since=None, connection_id=None, **kw):
        seen.append(connection_id)
        return Empty()

    monkeypatch.setattr("app.ingestion.sync.get_source", record)
    monkeypatch.setattr(jobs, "thread_dispatch", client.inline)
    return seen


def test_syncing_every_company_reads_every_enabled_connection(client, monkeypatch):
    seen = _pulled_connections(client, monkeypatch)
    _connect(client, ("conn_sls", "SLS Engineers", True), ("conn_4u", "4U Precision", True))

    client.post("/api/v1/data/sync", headers=_hdr(client), json={})

    assert set(seen) == {"conn_sls", "conn_4u"}, (
        "a run with no connection named must read every enabled company, not "
        "the first one")


def test_a_disabled_company_is_skipped_by_the_all_companies_run(client, monkeypatch):
    """Disabling is how somebody says 'keep the credentials, skip it'."""
    seen = _pulled_connections(client, monkeypatch)
    _connect(client, ("conn_sls", "SLS Engineers", True), ("conn_ups", "UPS", False))

    client.post("/api/v1/data/sync", headers=_hdr(client), json={})

    assert set(seen) == {"conn_sls"}


def test_naming_one_company_still_reads_only_that_one(client, monkeypatch):
    seen = _pulled_connections(client, monkeypatch)
    _connect(client, ("conn_sls", "SLS Engineers", True), ("conn_4u", "4U Precision", True))

    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"connection_id": "conn_4u"})

    assert set(seen) == {"conn_4u"}


def test_an_organization_with_no_connections_still_pulls_once(client, monkeypatch):
    """The fixture source in development, and the legacy environment-variable
    credentials. Both want exactly one unattributed pass, which is what an empty
    connection list has always produced."""
    seen = _pulled_connections(client, monkeypatch)

    client.post("/api/v1/data/sync", headers=_hdr(client), json={})

    assert seen == [None] * len(seen) and seen, "one pass, attributed to nobody"


def test_the_progress_counter_counts_every_company(client, monkeypatch):
    """`windows_done / windows_total` is what the sync card draws. Counting one
    company's windows while reading three would sit at 33% and call it done."""
    seen = _pulled_connections(client, monkeypatch)
    _connect(client, ("conn_sls", "SLS Engineers", True), ("conn_4u", "4U Precision", True))

    client.post("/api/v1/data/sync", headers=_hdr(client), json={})

    s = client.Maker()
    run = s.query(models.SyncRun).one()
    assert run.windows_total == run.windows_done
    assert run.windows_total % 2 == 0, "an even number of windows across two companies"
    s.close()


def test_one_company_cannot_pull_while_every_company_is_pulling(client):
    """The umbrella run now covers this connection too, so a second job on it
    would be two pulls of the same book against the same API."""
    every = client.post("/api/v1/data/sync", headers=_hdr(client), json={}).json()
    one = client.post("/api/v1/data/sync", headers=_hdr(client),
                      json={"connection_id": "conn_sls"}).json()

    assert every["started"] is True
    assert one["started"] is False, (
        "a single-company pull must be handed the all-companies job already "
        "reading that company")
    assert one["run"]["sync_run_id"] == every["run"]["sync_run_id"]


def test_every_company_cannot_pull_while_one_company_is_pulling(client):
    """The same overlap, the other way round — and the direction that was left
    open. A single-company pull in flight is a book the all-companies run would
    read again from the same API, so it must be handed that pull rather than
    starting beside it. The guard used to ask only whether another *umbrella*
    run was live, which no single-company pull ever is.
    """
    one = client.post("/api/v1/data/sync", headers=_hdr(client),
                      json={"connection_id": "conn_sls"}).json()
    every = client.post("/api/v1/data/sync", headers=_hdr(client), json={}).json()

    assert one["started"] is True
    assert every["started"] is False, (
        "an all-companies pull must be handed the single-company job already "
        "reading one of the books it covers")
    assert every["run"]["sync_run_id"] == one["run"]["sync_run_id"]

    s = client.Maker()
    assert s.query(models.SyncRun).count() == 1, "one row, so one pull"
    s.close()


def test_the_all_companies_button_is_shut_while_one_company_pulls(client):
    """`can_start` gates the "Sync every company" button, and the server would
    decline that click. A True here is a button that appears to do nothing."""
    client.post("/api/v1/data/sync", headers=_hdr(client),
                json={"connection_id": "conn_sls"})

    state = client.get("/api/v1/data/sync", headers=_hdr(client)).json()

    assert state["busy_connections"] == ["conn_sls"], "the company itself is busy"
    assert state["can_start"] is False


# ── the guard the in-process lock cannot be ─────────────────────────────────
#
# `start_sync` read the active runs and then inserted, holding a
# `threading.Lock`. That lock is process-local and the deployment runs two
# uvicorn workers by default (UVICORN_WORKERS=2), so both workers' reads
# returned "nothing running" before either insert committed, both inserted, and
# `run_job` never re-checks — both pulls ran. The guard is now two partial
# unique indexes on `sync_runs`; the lock stays as the cheap in-process case.

def _run_row(**kw) -> models.SyncRun:
    now = datetime.now(timezone.utc)
    return models.SyncRun(**{
        "organization_id": ORG, "source": "fixture", "status": "QUEUED",
        "started_at": now, "heartbeat_at": now, **kw})


def test_the_database_refuses_a_second_active_run_for_one_connection(client):
    """Not the application refusing it — the database. This is the assertion
    that would still hold with `start_sync` deleted."""
    s = client.Maker()
    s.add(_run_row(connection_id="conn_sls"))
    s.commit()

    s.add(_run_row(connection_id="conn_sls", status="RUNNING"))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()
    s.close()


def test_the_database_refuses_a_second_active_umbrella_run(client):
    """`connection_id` is NULL on an all-companies run, and NULL is not equal to
    NULL — so one index over the pair would have let two of these through. This
    is the half that catches them."""
    s = client.Maker()
    s.add(_run_row(connection_id=None))
    s.commit()

    s.add(_run_row(connection_id=None, status="RUNNING"))
    with pytest.raises(IntegrityError):
        s.commit()
    s.rollback()
    s.close()


def test_a_finished_run_never_blocks_the_next_one_at_the_database(client):
    """The index is partial for this reason. A unique index over every row would
    make the second pull of a connection impossible for ever, which is the
    dead-job-blocks-everything failure the heartbeat exists to avoid."""
    s = client.Maker()
    for status in ("OK", "PARTIAL", "FAILED"):
        s.add(_run_row(connection_id="conn_sls", status=status))
        s.commit()

    s.add(_run_row(connection_id="conn_sls", status="RUNNING"))
    s.commit()                       # the point: no IntegrityError
    assert s.query(models.SyncRun).count() == 4
    s.close()


def test_two_connections_still_pull_at_once_under_the_index(client):
    """Two connected Zoho companies are two independent APIs. An index that
    serialised them would undo the per-connection scoping deliberately, and
    silently — three companies pulled one after another looks like it works."""
    s = client.Maker()
    s.add(_run_row(connection_id="conn_sls", status="RUNNING"))
    s.add(_run_row(connection_id="conn_4u", status="RUNNING"))
    s.add(_run_row(organization_id="org_other", connection_id="conn_sls"))
    s.commit()                       # three active rows, no collision
    assert s.query(models.SyncRun).count() == 3
    s.close()


def test_a_pull_that_loses_the_insert_race_is_handed_the_winner(client):
    """Losing to the index must look exactly like losing to the lock.

    The other worker's row lands *between* this caller's check and its insert —
    the window the lock does not cover — so the INSERT is refused. The caller
    must get `started: False` and the winning run, not a 500 and not a run that
    does not exist. Simulated rather than threaded: a test that starts a thread
    and sleeps fails on a slow machine and passes on a fast one.
    """
    real = jobs._active_for_start
    raced = []

    def racing(session, organization_id, connection_id):
        found = real(session, organization_id, connection_id)
        if not raced:
            raced.append(True)
            other = client.Maker()          # the other worker's session
            other.add(_run_row(sync_run_id="run_other_worker",
                               organization_id=organization_id,
                               connection_id=connection_id))
            other.commit()
            other.close()
            return None                     # what the losing worker actually saw
        return found

    client.monkeypatch.setattr(jobs, "_active_for_start", racing)

    r = client.post("/api/v1/data/sync", headers=_hdr(client),
                    json={"connection_id": "conn_sls"})

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["started"] is False, "the loser must not claim to have started one"
    assert body["run"]["sync_run_id"] == "run_other_worker"

    s = client.Maker()
    assert s.query(models.SyncRun).count() == 1, "no second pull was queued"
    s.close()


def test_the_migration_closes_duplicate_active_runs_before_taking_the_index(tmp_path):
    """A live database may already hold the duplicates the index forbids, and
    CREATE UNIQUE INDEX would fail on it — so the upgrade closes them first.

    Verified through the real chain: a database stopped one revision short,
    seeded with duplicates of both kinds, then migrated. Newest survives per
    group; the rest are closed the way the reaper closes a run that stopped
    reporting.
    """
    backend = Path(__file__).resolve().parents[2]
    db = tmp_path / "guard.db"

    def alembic(*args):
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}
        return subprocess.run([sys.executable, "-m", "alembic", *args],
                              cwd=backend, env=env, capture_output=True, text=True)

    r = alembic("upgrade", "z6subject")      # the revision a7syncguard revises
    assert r.returncode == 0, r.stderr[-2000:]

    seeded = [
        # (id, org, connection, status, started_at)
        ("dup_old", "o1", "c1", "RUNNING", "2026-08-01 09:00:00"),
        ("dup_mid", "o1", "c1", "QUEUED", "2026-08-01 10:00:00"),
        ("dup_new", "o1", "c1", "RUNNING", "2026-08-01 11:00:00"),   # survives
        ("umb_old", "o1", None, "RUNNING", "2026-08-01 08:00:00"),
        ("umb_new", "o1", None, "QUEUED", "2026-08-01 12:00:00"),    # survives
        ("other_conn", "o1", "c2", "RUNNING", "2026-08-01 09:30:00"),  # untouched
        ("other_org", "o2", "c1", "RUNNING", "2026-08-01 09:30:00"),   # untouched
        ("done", "o1", "c1", "OK", "2026-07-01 09:00:00"),             # untouched
    ]
    # The counters are NOT NULL with Python-side defaults, so raw SQL has to
    # supply them — this is a database from before the code, which is the point.
    zeroed = ("customers", "products", "sales_txns", "cost_records",
              "skipped_count", "signals_emitted", "decisions_created")
    conn = sqlite3.connect(db)
    for run_id, org, connection, status, started in seeded:
        conn.execute(
            "INSERT INTO sync_runs (sync_run_id, organization_id, connection_id,"
            " source, status, phase, started_at, heartbeat_at, notes,"
            f" skipped_sample, {', '.join(zeroed)}) VALUES"
            f" (?, ?, ?, 'api', ?, 'Reading invoices', ?, ?, '{{}}', '[]',"
            f" {', '.join(['0'] * len(zeroed))})",
            (run_id, org, connection, status, started, started))
    conn.commit()

    r = alembic("upgrade", "a7syncguard")
    assert r.returncode == 0, r.stderr[-2000:]

    rows = {row[0]: row[1:] for row in conn.execute(
        "SELECT sync_run_id, status, phase, error, finished_at FROM sync_runs")}

    assert [rows[k][0] for k in ("dup_new", "umb_new")] == ["RUNNING", "QUEUED"], (
        "the newest active run in each group is the one that survives")
    assert [rows[k][0] for k in ("other_conn", "other_org")] == ["RUNNING"] * 2, (
        "another connection and another organization are different groups")
    assert rows["done"] == ("OK", "Reading invoices", None, None), (
        "a run that already finished is not this migration's business")

    seeded_start = {run_id: started for run_id, _, _, _, started in seeded}
    for closed in ("dup_old", "dup_mid", "umb_old"):
        status, phase, error, finished_at = rows[closed]
        assert status == "FAILED", closed
        assert phase is None, "a closed run is not still doing something"
        assert "already written was kept" in error, (
            "the row has to say the work it did survived, like the reaper does")
        assert finished_at == seeded_start[closed], (
            "closed at the last moment it was known to be alive, not at the "
            "moment somebody happened to run the migration")

    # And the index is real afterwards, which is the whole point of the fix.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sync_runs (sync_run_id, organization_id, connection_id,"
            " source, status, started_at) VALUES"
            " ('extra', 'o1', 'c1', 'api', 'QUEUED', '2026-08-02 09:00:00')")
    conn.close()


# ── reading a run's log ─────────────────────────────────────────────────────
#
# "See the server log" was the advice a crashed sync gave to somebody with a
# browser and no shell. These pin the endpoints that make it a real referent.


def _log_a_run(client, *, org=ORG, run_id="run_logged", levels=("INFO", "ERROR")):
    s = client.Maker()
    s.add(models.SyncRun(sync_run_id=run_id, organization_id=org, source="api",
                         status="FAILED", error="RuntimeError: Zoho said no"))
    for seq, level in enumerate(levels):
        s.add(models.SyncRunLog(
            organization_id=org, sync_run_id=run_id, seq=seq, at=datetime.now(timezone.utc),
            level=level, logger="pie_portal.sync_jobs",
            message=f"{level.lower()} line {seq}"))
    s.commit()
    s.close()
    return run_id


def test_a_finished_run_can_be_read_line_by_line(client):
    run_id = _log_a_run(client)
    body = client.get(f"/api/v1/data/sync-runs/{run_id}/log",
                      headers=_hdr(client)).json()

    assert [line["message"] for line in body["lines"]] == ["info line 0", "error line 1"]
    assert body["total"] == 2
    assert body["running"] is False
    assert body["note"] is None


def test_the_log_can_be_followed_from_where_the_reader_left_off(client):
    """A screen watching an hour-long pull asks for what it has not seen, not
    for the whole log every two seconds."""
    run_id = _log_a_run(client)
    body = client.get(f"/api/v1/data/sync-runs/{run_id}/log?after_seq=0",
                      headers=_hdr(client)).json()

    assert [line["seq"] for line in body["lines"]] == [1]
    assert body["next_seq"] == 1


def test_the_problems_can_be_read_without_the_rest(client):
    """The first question about a run that took an hour and failed is what went
    wrong, and the answer is a few lines inside several thousand."""
    run_id = _log_a_run(client, levels=("INFO", "INFO", "WARNING", "ERROR"))
    body = client.get(f"/api/v1/data/sync-runs/{run_id}/log?problems_only=true",
                      headers=_hdr(client)).json()

    assert [line["level"] for line in body["lines"]] == ["WARNING", "ERROR"]
    assert body["total"] == 2, "the total describes what was asked for"


def test_a_run_that_kept_no_log_says_why_rather_than_looking_empty(client):
    """An empty panel that cannot explain itself reads as "the sync did
    nothing" — the same absence-as-good-news failure this codebase keeps
    finding."""
    run_id = _log_a_run(client, levels=())
    body = client.get(f"/api/v1/data/sync-runs/{run_id}/log",
                      headers=_hdr(client)).json()

    assert body["lines"] == []
    assert body["note"] and "before its log was stored" in body["note"]


def test_the_log_downloads_as_a_file_with_the_run_named_on_it(client):
    run_id = _log_a_run(client)
    r = client.get(f"/api/v1/data/sync-runs/{run_id}/log.txt", headers=_hdr(client))

    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert run_id in r.text and "info line 0" in r.text
    assert "RuntimeError: Zoho said no" in r.text, "the failure travels with the log"


def test_another_organizations_run_is_not_readable(client):
    """Scoped in the query, so a foreign id reads as absent rather than as a
    permission error that confirms it exists."""
    _log_a_run(client, org="org_someone_else", run_id="run_theirs")
    r = client.get("/api/v1/data/sync-runs/run_theirs/log", headers=_hdr(client))
    assert r.status_code == 404


def test_a_salesperson_cannot_read_a_run_log(client):
    """A log line is whatever the code passed to it, and the cost-record stage
    names purchase documents. Same gate as the skipped rows."""
    run_id = _log_a_run(client)
    r = client.get(f"/api/v1/data/sync-runs/{run_id}/log",
                   headers=_hdr(client, SALES))
    assert r.status_code == 403
