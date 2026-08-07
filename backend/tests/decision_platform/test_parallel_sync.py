"""Three companies pulled at once, and the organization analysed exactly once.

Syncing three connected Zoho companies one after another was not merely slow.
Every pull ran the whole cycle — pull, detect, recompute, project, decide — and
the four phases after the pull are scoped to the *organization*, whose read
model all three connections feed. So the first pass detected signals and spent
AI calls on a book that was two thirds unread, the second on a book that was one
third unread, and only the third saw everything. The end state was right, which
is why nobody noticed.

``start_all`` fans the pulls out and the analysis in. These tests pin both
halves: that the pulls actually run concurrently and independently, and that the
analysis runs once, after all of them.

Real threads and a real database file, for the same reason
``test_db_concurrency`` uses them — none of this reproduces in-memory or
single-threaded.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading
import time

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.domain import models
from app.ingestion import jobs
from app.seed import ensure_org_and_users

ORG = "org_sanketh"
REPO_BACKEND = pathlib.Path(__file__).resolve().parents[2]


class Empty:
    """A source that succeeds instantly and pulls nothing."""

    def list_contacts(self): return []
    def list_items(self): return []
    def list_users(self): return []
    def list_invoices(self, skip=None): return []
    def list_bills(self, skip=None): return []


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A migrated on-disk database wired in as the app's own SessionLocal.

    ``run_job`` opens its own session per thread through ``app.db.SessionLocal``
    — that is the point of it — so a test that wants to exercise the real
    dispatch has to replace that, not pass a session in.
    """
    path = tmp_path / "parallel.db"
    url = f"sqlite:///{path}"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=REPO_BACKEND, env={**os.environ, "DATABASE_URL": url},
                   capture_output=True, check=True)

    engine = create_engine(url, future=True,
                           connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):     # mirrors app/db.py
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
        finally:
            cur.close()

    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    monkeypatch.setattr("app.db.SessionLocal", Maker)
    monkeypatch.setattr(settings, "ZOHO_SOURCE", "fixture")
    monkeypatch.setattr("app.ingestion.sync.get_source",
                        lambda session, org, since=None, **kw: Empty())

    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    yield Maker
    engine.dispose()


def _connect(session, label: str, zoho_org_id: str, *, enabled: bool = True):
    row = models.ZohoConnection(organization_id=ORG, label=label,
                                zoho_organization_id=zoho_org_id, enabled=enabled)
    session.add(row)
    session.flush()
    return row


# ── fan out ─────────────────────────────────────────────────────────────────
def test_every_enabled_connection_is_pulled(db):
    s = db()
    for i, name in enumerate(("SLS", "4U", "UPS")):
        _connect(s, name, f"zoho-{i}")
    s.commit()

    result = jobs.start_all(s, ORG, triggered_by="test")

    assert result["connections"] == 3
    assert len(result["runs"]) == 3
    assert {r["status"] for r in result["runs"]} == {"OK"}
    s.close()


def test_a_disabled_connection_is_left_alone(db):
    """`enabled` off means "keep the credentials, skip it on a sync-all"."""
    s = db()
    _connect(s, "SLS", "zoho-0")
    _connect(s, "dormant", "zoho-1", enabled=False)
    s.commit()

    result = jobs.start_all(s, ORG, triggered_by="test")

    assert result["connections"] == 1
    assert len(result["runs"]) == 1
    s.close()


def test_the_pulls_actually_overlap(db, monkeypatch):
    """The point of the change. Serial pulls would never be concurrent.

    Asserted on observed overlap rather than on elapsed time: a wall-clock
    threshold is a test that fails on a loaded machine and passes on an idle
    one, which is the flakiness this repository's own test docstrings warn off.
    """
    from app.ingestion.sync import SyncService

    s = db()
    for i in range(3):
        _connect(s, f"c{i}", f"zoho-{i}")
    s.commit()

    live = 0
    peak = 0
    guard = threading.Lock()
    real_run_reference = SyncService.run_reference

    def watched(self, *a, **kw):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        time.sleep(0.15)          # long enough for the others to be dispatched
        try:
            return real_run_reference(self, *a, **kw)
        finally:
            with guard:
                live -= 1

    monkeypatch.setattr(SyncService, "run_reference", watched)
    jobs.start_all(s, ORG, triggered_by="test")

    assert peak > 1, "the pulls ran one after another, not together"
    s.close()


# ── fan in ──────────────────────────────────────────────────────────────────
def test_the_organization_is_analysed_exactly_once(db, monkeypatch):
    """Not once per connection. This is the correctness half, not the speed half."""
    s = db()
    for i in range(3):
        _connect(s, f"c{i}", f"zoho-{i}")
    s.commit()

    calls = []
    real = jobs.execute_analysis

    def counted(session, run, organization_id, **kw):
        calls.append(organization_id)
        return real(session, run, organization_id, **kw)

    monkeypatch.setattr(jobs, "execute_analysis", counted)
    result = jobs.start_all(s, ORG, triggered_by="test")

    assert calls == [ORG], f"analysed {len(calls)} times, expected once"
    assert result["analysed"] is True
    s.close()


def test_the_analysis_lands_on_one_run_and_that_run_is_the_last_to_finish(db):
    s = db()
    for i in range(3):
        _connect(s, f"c{i}", f"zoho-{i}")
    s.commit()

    result = jobs.start_all(s, ORG, triggered_by="test")
    s.expire_all()
    runs = list(s.scalars(select(models.SyncRun)))

    host_id = result["analysis_run_id"]
    assert host_id in {r.sync_run_id for r in runs}
    finished = [r for r in runs if r.finished_at is not None]
    host = next(r for r in runs if r.sync_run_id == host_id)
    assert host.finished_at >= max(r.finished_at for r in finished)
    s.close()


def test_a_pull_only_run_keeps_no_analysis_scratch_in_its_notes(db):
    """`touched_customer_ids` is a hand-off between the two halves, not a result."""
    s = db()
    for i in range(2):
        _connect(s, f"c{i}", f"zoho-{i}")
    s.commit()

    result = jobs.start_all(s, ORG, triggered_by="test")
    s.expire_all()
    host = s.get(models.SyncRun, result["analysis_run_id"])

    assert "pull_only" not in (host.notes or {})
    assert "touched_customer_ids" not in (host.notes or {})
    s.close()


def test_nothing_is_analysed_when_every_pull_failed(db, monkeypatch):
    """Detecting over an unchanged read model restates old signals and spends
    AI calls doing it."""
    s = db()
    for i in range(2):
        _connect(s, f"c{i}", f"zoho-{i}")
    s.commit()

    def boom(session, org, since=None, **kw):
        raise RuntimeError("Zoho is down")

    calls = []
    monkeypatch.setattr("app.ingestion.sync.get_source", boom)
    monkeypatch.setattr(jobs, "execute_analysis",
                        lambda *a, **kw: calls.append(1) or {})

    result = jobs.start_all(s, ORG, triggered_by="test")

    assert calls == []
    assert result["analysed"] is False
    assert {r["status"] for r in result["runs"]} == {"FAILED"}
    s.close()


def test_no_connections_is_reported_not_crashed(db):
    s = db()
    result = jobs.start_all(s, ORG, triggered_by="test")
    assert result["connections"] == 0
    assert result["analysed"] is False
    assert "No enabled Zoho connection" in result["detail"]
    s.close()


# ── the split itself ────────────────────────────────────────────────────────
def test_a_pull_without_analysis_does_not_run_the_detectors(db, monkeypatch):
    s = db()
    run = models.SyncRun(organization_id=ORG, source="fixture", status="QUEUED")
    s.add(run)
    s.commit()

    calls = []
    monkeypatch.setattr("app.signals.engine.run_detectors",
                        lambda *a, **kw: calls.append(1) or {})

    jobs.execute_sync(s, run, analysis=False)

    assert calls == []
    assert run.status == "OK"
    assert (run.notes or {}).get("pull_only") is True
    s.close()


def test_a_pull_with_analysis_still_runs_the_whole_cycle(db):
    """The default path is unchanged — every existing caller relies on it."""
    s = db()
    run = models.SyncRun(organization_id=ORG, source="fixture", status="QUEUED")
    s.add(run)
    s.commit()

    jobs.execute_sync(s, run)

    assert run.status == "OK"
    assert "pull_only" not in (run.notes or {})
    s.close()


def test_two_first_writers_do_not_collide_on_the_tenant_key(db):
    """The bug the first end-to-end run of `sync_all` actually hit.

    A tenant's data key is created lazily on first use, by read-then-insert.
    Three pulls starting together all read no key and all inserted one, and two
    of the three died on ``UNIQUE constraint failed: tenant_keys.organization_id``
    — taking their whole pull with them.
    """
    from app.trust.keys import ensure_key

    Maker = db
    errors: list[BaseException] = []

    # Several rounds, because whether two writers genuinely overlap is a matter
    # of scheduling: a round the race does not happen in passes trivially, and
    # only a round it does happen in proves anything. Rounds are cheap, and the
    # assertion is on the outcome either way — so this can be trivially green
    # but never falsely red.
    for round_no in range(5):
        org = f"org_race_{round_no}"
        barrier = threading.Barrier(4)
        keys: list[str] = []

        def racer(org=org, barrier=barrier, keys=keys):
            session = Maker()
            try:
                barrier.wait(timeout=10)
                row = ensure_key(session, org)
                session.commit()
                keys.append(row.wrapped_dek)
            except BaseException as e:  # noqa: BLE001 — asserted on below
                errors.append(e)
                session.rollback()
            finally:
                session.close()

        threads = [threading.Thread(target=racer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"a concurrent first write failed: {errors[0]!r}"
        assert len(set(keys)) == 1, f"{org} ended up with more than one data key"


# ── the scheduled entry point ───────────────────────────────────────────────
def test_the_scheduler_finds_organizations_by_their_enabled_connections(db):
    from app.sync_all import organizations_with_connections

    s = db()
    _connect(s, "SLS", "zoho-0")
    _connect(s, "dormant", "zoho-1", enabled=False)
    s.commit()

    assert organizations_with_connections(s) == [ORG]
    s.close()


def test_the_scheduler_exits_non_zero_when_a_pull_did_not_land(db, monkeypatch,
                                                               capsys):
    """A cron reads the exit code. A PARTIAL night must not look like a clean one."""
    from app import sync_all as cli

    s = db()
    _connect(s, "SLS", "zoho-0")
    s.commit()
    s.close()

    def boom(session, org, since=None, **kw):
        raise RuntimeError("Zoho is down")

    monkeypatch.setattr("app.ingestion.sync.get_source", boom)
    assert cli.main([]) == 1

    out = capsys.readouterr().out
    assert "FAILED" in out


def test_the_scheduler_exits_zero_on_a_clean_run(db, capsys):
    s = db()
    _connect(s, "SLS", "zoho-0")
    s.commit()
    s.close()

    from app import sync_all as cli

    assert cli.main([]) == 0
    assert "analysed" in capsys.readouterr().out


def test_two_pulls_may_vault_the_same_name_at_once(db):
    """The second race the first end-to-end run hit, in a different table.

    ``name_vault`` is upserted per entity while the pull runs, by read-then-
    insert. Two connections vaulting the same entity together both read nothing,
    both inserted, and one died on the unique constraint.
    """
    from app.trust.vault import put, resolve

    Maker = db
    errors: list[BaseException] = []

    for round_no in range(5):
        entity = f"cust-{round_no}"
        barrier = threading.Barrier(4)

        def racer(entity=entity, barrier=barrier, n=[0]):
            session = Maker()
            try:
                barrier.wait(timeout=10)
                put(session, ORG, "CUSTOMER", entity, "Bharat Forge")
                session.commit()
            except BaseException as e:  # noqa: BLE001 — asserted on below
                errors.append(e)
                session.rollback()
            finally:
                session.close()

        threads = [threading.Thread(target=racer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"a concurrent vault write failed: {errors[0]!r}"

    check = Maker()
    assert resolve(check, ORG, "CUSTOMER", "cust-0") == "Bharat Forge"
    check.close()


def test_the_analysis_summaries_survive_onto_the_run(db):
    """They used to be built, stored, and then replaced by the finally block."""
    s = db()
    run = models.SyncRun(organization_id=ORG, source="fixture", status="QUEUED")
    s.add(run)
    s.commit()

    jobs.execute_sync(s, run)

    notes = run.notes or {}
    assert "state" in notes, f"the state summary was dropped again: {sorted(notes)}"
    s.close()
