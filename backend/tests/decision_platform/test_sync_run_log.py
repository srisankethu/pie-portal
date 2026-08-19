"""A sync has to leave an account of itself that somebody can read.

The incident behind these: a pull ran for an hour, failed, and the screen said
*"The sync job stopped unexpectedly. See the server log."* The person reading it
had a browser and no shell, the deployment kept no log file, and the one line
the run row carried named the exception type without a frame of context. The
advice could not be taken, which is worse than no advice — it reads as though
the information exists somewhere.

So these pin the properties that make "see the log" a sentence with a referent:
the lines a run emits are stored with the run, a failure's traceback is among
them, one pull's log is its own, and a log that had to be truncated says so.
"""
from __future__ import annotations

import logging
import pathlib
import threading
from datetime import date

import pytest
from sqlalchemy import select

from app.domain import models
from app.ingestion import jobs
from app.observability import logs

ORG = "org_log"
log = logging.getLogger("pie_portal.test_sync_run_log")


@pytest.fixture()
def run(session):
    session.add(models.Organization(organization_id=ORG, name="SLS Engineers",
                                    currency="INR", config={}))
    row = models.SyncRun(sync_run_id="run_1", organization_id=ORG, source="fixture",
                         status="RUNNING", phase="Starting")
    session.add(row)
    session.flush()
    return row


def _lines(session, sync_run_id: str = "run_1") -> list[models.SyncRunLog]:
    return list(session.scalars(
        select(models.SyncRunLog)
        .where(models.SyncRunLog.sync_run_id == sync_run_id)
        .order_by(models.SyncRunLog.seq)))


# ── capture ─────────────────────────────────────────────────────────────────
def test_what_the_run_logs_is_stored_with_the_run(session, run):
    with logs.capture("run_1") as run_log:
        log.info("Reading invoices · 2026-06")
        jobs.persist_log(session, run, run_log)

    stored = _lines(session)
    assert [row.message for row in stored] == ["Reading invoices · 2026-06"]
    assert stored[0].level == "INFO"
    assert stored[0].logger == "pie_portal.test_sync_run_log"
    assert stored[0].organization_id == ORG


def test_a_traceback_reaches_the_log_not_only_the_exception_type(session, run):
    """`SyncRun.error` is one truncated line. For an hour-long pull that failed,
    the frames are the difference between a report and a diagnosis."""
    with logs.capture("run_1") as run_log:
        try:
            raise ValueError("Zoho said no")
        except ValueError:
            log.exception("sync failed")
        jobs.persist_log(session, run, run_log)

    body = "\n".join(row.message for row in _lines(session))
    assert "sync failed" in body
    assert "ValueError: Zoho said no" in body
    assert "Traceback (most recent call last)" in body, "the frames are the point"
    assert any(row.level == "ERROR" for row in _lines(session))


def test_each_line_keeps_the_order_it_was_written_in(session, run):
    """Two lines inside one millisecond are not rare in a tight loop, so the
    ordering cannot rest on the timestamp."""
    with logs.capture("run_1") as run_log:
        for i in range(20):
            log.info("line %s", i)
        jobs.persist_log(session, run, run_log)

    stored = _lines(session)
    assert [row.seq for row in stored] == list(range(20))
    assert [row.message for row in stored] == [f"line {i}" for i in range(20)]


def test_the_sequence_continues_across_flushes(session, run):
    """A pull flushes at every phase boundary; the log must read as one story."""
    with logs.capture("run_1") as run_log:
        log.info("first")
        jobs.persist_log(session, run, run_log)
        log.info("second")
        jobs.persist_log(session, run, run_log)

    assert [(r.seq, r.message) for r in _lines(session)] == [(0, "first"), (1, "second")]


def test_one_pull_does_not_collect_another_pulls_lines(session, run):
    """Three connected companies sync at once, each on its own thread. Three
    logs, not one interleaved one that describes no particular pull."""
    other = models.SyncRun(sync_run_id="run_2", organization_id=ORG,
                           source="fixture", status="RUNNING")
    session.add(other)
    session.flush()

    with logs.capture("run_1") as first:
        log.info("belongs to run 1")

        def elsewhere() -> None:
            with logs.capture("run_2") as second:
                log.info("belongs to run 2")
                second_lines.extend(m.message for m in second.drain())

        second_lines: list[str] = []
        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join()

        jobs.persist_log(session, run, first)

    assert [r.message for r in _lines(session)] == ["belongs to run 1"]
    assert second_lines == ["belongs to run 2"]


def test_a_thread_logging_outside_any_run_is_not_captured(session, run):
    """A request served while a sync is running must not land in the sync's log."""
    log.info("nobody's business")
    with logs.capture("run_1") as run_log:
        jobs.persist_log(session, run, run_log)
    assert _lines(session) == []


# ── the cap, and its honesty ────────────────────────────────────────────────
def test_the_cap_keeps_the_warnings_and_says_it_dropped_the_rest(session, run):
    """Truncation is acceptable; silent truncation is the defect this whole
    change exists to remove. Warnings survive the cap because they are the
    reason anybody opens a log."""
    with logs.capture("run_1", max_lines=5) as run_log:
        for i in range(50):
            log.info("progress %s", i)
        log.warning("Zoho throttled the pull")
        jobs.persist_log(session, run, run_log)

    stored = _lines(session)
    messages = [row.message for row in stored]
    assert "Zoho throttled the pull" in messages, "a warning must survive the cap"
    assert any("capped at 5 lines" in m for m in messages), (
        "a truncated log has to admit it is truncated")
    assert run_log.dropped == 45
    assert len(stored) <= 8


# ── the whole job ───────────────────────────────────────────────────────────
class _Boom:
    """A source whose customer list fails, the way a live one does."""

    def list_contacts(self):
        raise RuntimeError("Zoho returned 500 on /contacts")

    def list_items(self): return []
    def list_users(self): return []
    def list_invoices(self, skip=None): return []
    def list_bills(self, skip=None): return []


def test_a_job_that_fails_leaves_a_log_behind_it(session, run, monkeypatch):
    """End to end through `run_job`, because the closing write — the one that
    matters most — happens on a session of its own after the job's has been
    rolled back and closed."""
    import app.db as db_module
    from sqlalchemy.orm import sessionmaker

    session.commit()
    maker = sessionmaker(bind=session.get_bind(), autoflush=False,
                         expire_on_commit=False, future=True)
    monkeypatch.setattr(db_module, "SessionLocal", maker)
    monkeypatch.setattr("app.ingestion.sync.get_source",
                        lambda *a, **kw: _Boom())

    jobs.run_job("run_1", date(2026, 1, 1), False, None, analysis=False)

    session.expire_all()
    stored = _lines(session)
    body = "\n".join(f"{r.level} {r.message}" for r in stored)
    assert stored, "a failed run with no log is the whole reason this exists"
    assert "sync run run_1 starting" in body, "what it was asked to do"
    assert "Zoho returned 500 on /contacts" in body, "what actually broke"
    assert "Traceback (most recent call last)" in body

    row = session.get(models.SyncRun, "run_1")
    assert row.status in ("FAILED", "PARTIAL")
    assert row.error and "RuntimeError" in row.error


# ── the reason there was no server log at all ───────────────────────────────
def test_running_migrations_does_not_switch_the_app_log_off(tmp_path, monkeypatch):
    """The root cause, pinned where it will be noticed if it comes back.

    ``alembic/env.py`` calls ``logging.config.fileConfig``, and the app runs
    migrations **in-process** at startup. ``fileConfig`` replaces the root
    handlers, forces the root level to WARN, and — defaulting to
    ``disable_existing_loggers=True`` — sets ``disabled = True`` on every
    ``pie_portal.*`` logger, all of which exist by then.

    So the server had no log because a migration had turned it off, an hour
    before anybody thought to look for one. The failure is silent, survives
    restarts, and looks exactly like an app that simply logs nothing.
    """
    from alembic import command
    from alembic.config import Config

    logs.configure(force=True)
    app_logger = logging.getLogger("pie_portal.sync_jobs")

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "DATABASE_URL", url, raising=False)
    backend = pathlib.Path(__file__).resolve().parents[2]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")

    assert not app_logger.disabled, (
        "a migration must not disable the application's loggers")
    assert logging.getLogger().level <= logging.INFO, (
        "a migration must not raise the root level past the app's own")

    # And the capture still collects, which is the property that matters.
    with logs.capture("run_after_migration") as run_log:
        app_logger.info("still logging")
    assert [line.message for line in run_log.lines] == ["still logging"]


# ── credentials must never reach the log ────────────────────────────────────
def test_a_credential_in_a_url_is_redacted_before_it_is_stored(session, run):
    """Found in a real run log, pasted into a chat window to ask about sync
    speed: the Zoho token refresh sent the refresh token and the client secret
    as **query parameters**, httpx logs every request URL at INFO, and the run
    log then wrote them to the database and rendered them on a screen.

    The call site is fixed — they travel in the body now — and this pins the
    second line of defence, because the next one will be somebody else's URL.
    """
    with logs.capture("run_1") as run_log:
        # Placeholder values, in the shape the real ones have — all-zero, which
        # is what the scanner in `test_crypto.test_no_live_secret_is_committed`
        # recognises as a placeholder. That scanner caught the first draft of
        # this test, which had pasted the credential straight out of the log
        # that prompted it. Working as intended, on its author.
        secret = "0" * 42
        token = f"1000.{'0' * 32}.{'0' * 32}"
        log.info(
            'HTTP Request: POST https://accounts.zoho.in/oauth/v2/token'
            '?refresh_token=%s&client_id=1000.CLIENTID'
            '&client_secret=%s&grant_type=refresh_token "HTTP/1.1 200"',
            token, secret)
        jobs.persist_log(session, run, run_log)

    stored = "\n".join(row.message for row in _lines(session))
    assert token not in stored, "the refresh token must not be stored"
    assert secret not in stored, "nor the client secret"
    assert "refresh_token=[redacted]" in stored, (
        "the parameter name stays, so the line still says which call this was")
    assert "accounts.zoho.in/oauth/v2/token" in stored, "and remains diagnosable"


def test_a_secret_in_a_json_body_is_redacted_too():
    assert logs.redact('{"access_token": "1000.abc", "expires_in": 3600}') == (
        '{"access_token": "[redacted]", "expires_in": 3600}')


def test_an_ordinary_line_is_left_exactly_as_it_was():
    """A redactor that mangles ordinary lines is one people turn off."""
    line = "zoho items: stopped at the ZOHO_MAX_PAGES limit (50)"
    assert logs.redact(line) == line
