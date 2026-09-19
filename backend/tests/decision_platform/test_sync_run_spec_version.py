"""Every ingestion run says which contract its rows were written under.

``SyncRun.spec_version`` is to an ingestion run what ``thresholds_version`` is
to a computed row, and it answers a different question: not which policy judged
a number, but which version of the canonical ingestion spec was in force while
the rows landed. The reason it is worth a column is the omission it makes
placeable — PIE's own Zoho client dropped ``created_time`` from three document
projections, every row landed with a NULL ``source_recorded_at``, and the sync
reported success. Once the spec is versioned, "which runs wrote under the
contract that did not yet require that field" is a question with an answer.

Four properties, and each is a way the stamp could be worth nothing:

* **Stamped at QUEUED**, before any work starts. A run that dies in its third
  phase has already written rows; if the stamp were applied at completion,
  exactly the failed runs — the ones this table gets read to understand — would
  carry none.
* **Only there.** A second write site is a second answer to one question.
* **NULL stays NULL.** A run from before the column is not retro-fitted with
  today's version, here or in the migration. That would be a claim about work
  no contract saw (§1: absence of evidence is not a pass).
* **Comparable.** Two runs can only be compared by their stamps if the stamp is
  a property of the code rather than of the run — including across the two
  worker processes this app is deployed with.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.domain import models, spec
from app.ingestion import jobs
from app.routers import data_status

ORG = "org_pie"
BACKEND = pathlib.Path(__file__).resolve().parents[2]

#: Nothing may run: the row must be observable while it is still QUEUED, which
#: is the moment the stamp is about.
_NO_DISPATCH = lambda *a, **kw: None  # noqa: E731


@pytest.fixture()
def maker(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


def _queue(session) -> models.SyncRun:
    run, started = jobs.start_sync(session, ORG, dispatch=_NO_DISPATCH)
    assert started, "the fixture expects a fresh run, not one already in flight"
    return run


def test_a_run_is_stamped_from_the_moment_it_is_queued(session):
    run = _queue(session)

    assert run.status == "QUEUED", "the stamp is being read after work began"
    assert run.spec_version == spec.spec_version()
    assert run.spec_version.startswith("spec_")


def test_the_stamp_is_on_the_row_a_second_session_reads(session, maker):
    """Not only on the in-memory instance: ``start_sync`` commits the row and
    the background worker picks it up through its own session."""
    run_id = _queue(session).sync_run_id

    other = maker()
    try:
        assert other.get(models.SyncRun, run_id).spec_version == spec.spec_version()
    finally:
        other.close()


def test_a_run_that_died_part_way_still_says_what_it_wrote_under(session):
    """The reaper closes a job that stopped reporting. Its rows are already in
    the database, so the contract they were written against is exactly as much
    of a fact for this run as for one that reached OK."""
    run = _queue(session)
    stamped = run.spec_version
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    run.started_at = run.heartbeat_at = old
    session.commit()

    assert jobs.active_runs(session, ORG) == [], "the dead run was not reaped"
    session.refresh(run)
    assert run.status == "FAILED", run.status
    assert run.spec_version == stamped


def test_a_run_from_before_the_stamp_is_left_unknown(session):
    """NULL is the honest value and nothing fills it in.

    A backfill has only one value available — today's — so it would say a
    contract judged rows it never saw. The column is nullable for that reason,
    and the serializer reports the absence rather than substituting.
    """
    old = models.SyncRun(organization_id=ORG, source="fixture", status="OK",
                         started_at=datetime.now(timezone.utc))
    session.add(old)
    session.commit()
    session.refresh(old)

    assert old.spec_version is None
    assert data_status._run_dict(old)["spec_version"] is None


def test_the_stamp_reaches_the_payload_the_sync_screens_read(session):
    """``_run_dict`` is the one serializer behind every sync screen and the
    POST /sync response. The row is unreadable from where the question gets
    asked — a browser, no shell — unless the value leaves the database."""
    run = _queue(session)

    assert data_status._run_dict(run)["spec_version"] == spec.spec_version()


def test_two_runs_started_in_one_process_carry_the_same_stamp(session, maker):
    first = _queue(session)
    first.status = "OK"
    session.commit()

    other = maker()
    try:
        second = jobs.start_sync(other, ORG, dispatch=_NO_DISPATCH)[0]
        stamp = second.spec_version
    finally:
        other.close()

    assert stamp == first.spec_version, (
        "the stamp moved between two runs of unchanged code, so comparing two "
        "runs by it says nothing")


def test_a_second_process_writes_the_same_stamp(session):
    """The deployment runs more than one worker, and a run queued by either has
    to be comparable with one queued by the other. A stamp that depends on
    process state — a set iteration order, a dict order, a clock — would be
    stable within a process and useless between two."""
    here = _queue(session).spec_version

    proc = subprocess.run(
        [sys.executable, "-c",
         "from app.domain.spec import spec_version; print(spec_version())"],
        cwd=BACKEND, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == here


# ── one mint site ───────────────────────────────────────────────────────────
def _spec_version_writes(path: pathlib.Path) -> list[str]:
    """The enclosing function of every place this file writes ``spec_version``.

    Parsed rather than grepped, for the reason ``test_layer_boundaries`` gives:
    a column named in a docstring is not a write, and only an AST can tell the
    difference.
    """
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            written = False
            if isinstance(inner, ast.keyword) and inner.arg == "spec_version":
                written = True
            elif isinstance(inner, ast.Assign):
                written = any(isinstance(t, ast.Attribute)
                              and t.attr == "spec_version" for t in inner.targets)
            if written:
                found.append(node.name)
    return found


def test_the_stamp_is_written_in_exactly_one_place():
    """A second write site is a second answer to "which contract was this?" —
    and the two would agree until one of them was moved to the end of the run.
    """
    assert _spec_version_writes(BACKEND / "app" / "ingestion" / "jobs.py") == [
        "start_sync"]
