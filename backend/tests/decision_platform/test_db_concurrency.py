"""One process writing for minutes must not take the app down.

Reported as ``/api/health`` returning::

    "OperationalError: (sqlite3.OperationalError) database is locked
     [SQL: SELECT name FROM sqlite_master WHERE type='table' ...]"

Two causes, one shape. The sync job ran the whole pull — every window, the
detectors, the metrics recompute, the decision generation — inside a single
transaction, committing once at the very end. A large SQLite write spills its
page cache, escalates to an EXCLUSIVE lock, and in the default rollback-journal
mode holds it until commit, blocking every reader for the duration.

That single transaction also made the sync progress counter unreadable: a flush
is invisible outside its own transaction, so the window count the UI polls for
sat at 0 for the entire run and then jumped to the total.

Fixed at both levels — WAL so readers never block on a writer, and a commit at
each phase boundary so no write window lasts minutes. These tests use real
threads and a real database file because neither fault reproduces in-memory or
single-threaded.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.domain import models


@pytest.fixture()
def file_db(tmp_path, monkeypatch):
    """A migrated on-disk database with the app's own engine configuration.

    On disk, not in-memory: SQLite's locking behaviour is a property of the file
    and none of this reproduces on ``sqlite://``.
    """
    path = tmp_path / "conc.db"
    url = f"sqlite:///{path}"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=__import__("pathlib").Path(__file__).resolve().parents[2],
                   env={**__import__("os").environ, "DATABASE_URL": url},
                   capture_output=True, check=True)

    engine = create_engine(url, future=True,
                           connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):     # mirrors app/db.py
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

    yield engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                               future=True)
    engine.dispose()


def _txn(org: str, ref: str) -> models.SalesTxn:
    return models.SalesTxn(
        organization_id=org, external_ref=ref, customer_id="c1", product_id="p1",
        date=datetime.now(timezone.utc).date(), qty=1, unit_price=100,
        line_revenue=100, source_ref={})


# ── the engine configuration itself ─────────────────────────────────────────
def test_the_app_engine_puts_sqlite_in_wal_mode():
    """Without WAL a writer blocks every reader, which is the whole fault."""
    from app.config import settings
    from app.db import engine

    if not settings.DATABASE_URL.startswith("sqlite"):
        pytest.skip("Postgres does not need this")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_the_app_engine_waits_rather_than_failing_immediately():
    """WAL still serialises writer against writer; the five-second default is
    short enough that a request colliding with a sync's commit gives up."""
    from app.config import settings
    from app.db import engine

    if not settings.DATABASE_URL.startswith("sqlite"):
        pytest.skip("Postgres does not need this")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() >= 30000


# ── the fault, reproduced ───────────────────────────────────────────────────
def test_a_reader_is_not_blocked_by_a_long_bulk_write(file_db):
    """The reported failure, as a test.

    18,000 rows is enough to spill the page cache, which is what escalates the
    lock. The query is the one from the error message.
    """
    engine, Session = file_db
    done = threading.Event()
    errors: list[str] = []

    def writer():
        session = Session()
        try:
            for window in range(6):
                for i in range(3000):
                    session.add(_txn("o1", f"w{window}:{i}"))
                session.commit()          # a phase boundary
        except Exception as exc:          # noqa: BLE001
            errors.append(f"writer: {exc}")
        finally:
            session.close()
            done.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()

    reads = 0
    while not done.is_set():
        try:
            inspect(engine).get_table_names()
            reads += 1
        except Exception as exc:          # noqa: BLE001
            errors.append(f"reader: {exc}")
            break
    thread.join(timeout=60)

    assert not errors, errors
    assert reads > 0, "the writer finished too fast to prove anything"


def test_a_writer_can_still_commit_while_reads_are_in_flight(file_db):
    """WAL must not have traded one deadlock for another."""
    engine, Session = file_db
    stop = threading.Event()
    errors: list[str] = []

    def reader():
        while not stop.is_set():
            session = Session()
            try:
                session.query(models.SalesTxn).count()
            except Exception as exc:      # noqa: BLE001
                errors.append(f"reader: {exc}")
                return
            finally:
                session.close()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    session = Session()
    try:
        for i in range(2000):
            session.add(_txn("o1", f"x{i}"))
        session.commit()
    except Exception as exc:              # noqa: BLE001
        errors.append(f"writer: {exc}")
    finally:
        session.close()
        stop.set()
        thread.join(timeout=30)

    assert not errors, errors


# ── progress has to be visible while it is happening ────────────────────────
def test_sync_progress_is_visible_to_another_session(file_db):
    """A flush is invisible outside its transaction.

    The window counter is polled by the sync screen from a different session, so
    recording progress with ``flush`` meant the number could not move until the
    whole pull committed — the progress bar went 0, 0, 0 … 18.
    """
    _engine, Session = file_db
    writer = Session()
    run = models.SyncRun(organization_id="o1", source="zoho", status="RUNNING",
                         windows_total=18, windows_done=0,
                         started_at=datetime.now(timezone.utc))
    writer.add(run)
    writer.commit()

    for done in (3, 7, 12):
        run.windows_done = done
        run.heartbeat_at = datetime.now(timezone.utc)
        writer.commit()                   # what ``phase()`` now does

        reader = Session()
        try:
            assert reader.get(models.SyncRun, run.sync_run_id).windows_done == done
        finally:
            reader.close()
    writer.close()


def test_phase_commits_rather_than_flushes():
    """Pinned at the source, so the fix cannot be undone by a tidy-up.

    ``flush`` here is not a smaller version of ``commit`` — it is the difference
    between progress being readable and not, and between a write window lasting
    milliseconds and lasting the whole sync.
    """
    import ast
    import inspect as pyinspect
    import textwrap

    from app.ingestion import jobs

    tree = ast.parse(textwrap.dedent(pyinspect.getsource(jobs.execute_sync)))
    phase = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "phase")
    calls = {
        f"{ast.unparse(node.func)}"
        for node in ast.walk(phase) if isinstance(node, ast.Call)
    }
    assert "session.commit" in calls, (
        "phase() must commit — a flush is invisible to the session polling for "
        "progress, and leaves the write transaction open")
    assert "session.flush" not in calls
