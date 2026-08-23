"""A forked chain must be impossible, not unlikely.

``app/lease.py`` refuses, in writing, to be the single writer a hash chain
needs: it has no fence token, so a leader can hold an unexpired claim while
frozen and resume after another worker has taken it, acting in the belief that
it still leads. Its closing paragraph says what to do instead — enforce the
ordering where the write lands — and this file is the check that we did.

Real threads and a real database file in WAL mode, the idiom
``test_process_lease.py`` and ``test_parallel_sync.py`` already use and for the
same reason: none of this reproduces in-memory or single-threaded. An in-memory
SQLite database is private to the connection that opened it, so two "concurrent"
appenders against one would be two chains that never meet.

What is asserted is not "the retries worked". It is the property itself: after N
appenders race, the chain is **linear** — dense positions from 1, each entry
naming its predecessor, every signature intact — and every appender that
believed it succeeded is in it exactly once.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import threading

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.trust import audit

ORG = "org_pie"
OTHER = "org_other"
REPO_BACKEND = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture()
def file_db(tmp_path):
    """A migrated on-disk database, WAL, mirroring ``app/db.py``'s pragmas.

    Migrated through the real Alembic chain rather than ``create_all``, so the
    unique constraints under test are the ones the migration actually ships —
    a chain guarded only by a constraint the models declare and the migration
    forgot would pass every other test in the suite.
    """
    path = tmp_path / "audit.db"
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
    yield Maker
    engine.dispose()


def _race(Maker, *, workers: int, org: str = ORG, per_worker: int = 1):
    """Run ``workers`` appenders through one barrier and collect what happened."""
    gate = threading.Barrier(workers)
    written: list[tuple[str, int]] = []
    # A thread that dies raises into the thread excepthook, which pytest never
    # sees — so "one winner, one crashed" would be indistinguishable from "both
    # succeeded", and this test would keep passing through the regression it
    # exists to catch. Collected and re-asserted below.
    failed: list[BaseException] = []
    lock = threading.Lock()

    def append(who: str) -> None:
        session = Maker()
        try:
            gate.wait(timeout=20)
            for n in range(per_worker):
                row = audit.append(
                    session, organization_id=org, action=audit.LOGIN_SUCCEEDED,
                    actor_label=who, subject_type="USER", subject_id=who,
                    detail={"worker": who, "n": n})
                session.commit()
                with lock:
                    written.append((who, int(row.seq)))
        except BaseException as e:               # noqa: BLE001 — re-asserted
            with lock:
                failed.append(e)
        finally:
            session.close()

    threads = [threading.Thread(target=append, args=(f"worker-{i}",))
               for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not failed, f"an appender raised instead of retrying: {failed!r}"
    assert not [t for t in threads if t.is_alive()], "an appender hung"
    return written


def _assert_linear(session, org: str, expected: int) -> None:
    """The property, stated once and asserted from every angle it can fail."""
    rows = audit.chain(session, org)
    assert len(rows) == expected, "an appender's entry is missing or doubled"

    # Dense, from one. A gap means a position was burned; a repeat means the
    # constraint let a fork through.
    assert [r.seq for r in rows] == list(range(1, expected + 1))

    # Every entry names its predecessor, and no two name the same one.
    parents = [r.prev_hash for r in rows]
    assert len(set(parents)) == len(parents), "two entries share a parent — a fork"
    assert parents[0] == audit.GENESIS
    for earlier, later in zip(rows, rows[1:]):
        assert later.prev_hash == earlier.entry_hash

    # And the whole thing still verifies, signatures included.
    report = audit.verify(session, org)
    assert report["ok"] is True, report["first_break"]
    assert report["entries"] == expected


def test_eight_appenders_at_once_produce_one_linear_chain(file_db):
    written = _race(file_db, workers=8)

    session = file_db()
    try:
        _assert_linear(session, ORG, expected=8)
        # Every worker that returned believes it holds a position, and it does:
        # no two were handed the same one, and none was quietly dropped.
        assert sorted(seq for _who, seq in written) == list(range(1, 9))
        assert len({who for who, _seq in written}) == 8
    finally:
        session.close()


def test_sustained_contention_keeps_the_chain_linear(file_db):
    """Four writers, five entries each — twenty positions fought over in a row.

    One round can be won by luck of scheduling. This one cannot: every appender
    re-reads the head twenty times against three live competitors, so the retry
    path is exercised rather than merely available.
    """
    written = _race(file_db, workers=4, per_worker=5)

    session = file_db()
    try:
        _assert_linear(session, ORG, expected=20)
        assert sorted(seq for _who, seq in written) == list(range(1, 21))
    finally:
        session.close()


def test_two_tenants_writing_at_once_do_not_contend(file_db):
    """Chains are per-organization, so one tenant's traffic is not the other's
    queue. Both chains must be complete and independently linear."""
    done = threading.Event()

    def other():
        session = file_db()
        try:
            for n in range(5):
                audit.append(session, organization_id=OTHER,
                             action=audit.AI_CALL, detail={"n": n})
                session.commit()
        finally:
            session.close()
            done.set()

    thread = threading.Thread(target=other)
    thread.start()
    _race(file_db, workers=4)
    thread.join(timeout=60)
    assert done.is_set(), "the second tenant's writer hung"

    session = file_db()
    try:
        _assert_linear(session, ORG, expected=4)
        _assert_linear(session, OTHER, expected=5)
    finally:
        session.close()


def test_the_database_and_not_the_process_is_what_refuses_a_duplicate(file_db):
    """The constraint, exercised directly rather than through the retry.

    ``append`` swallows the conflict by design, so a broken index would show up
    only as a corrupted chain in the tests above and only sometimes. This asks
    the database the question straight: a second row at a taken position must be
    refused whatever the application believes.
    """
    from sqlalchemy.exc import IntegrityError

    from app.domain import models

    session = file_db()
    try:
        first = audit.append(session, organization_id=ORG,
                             action=audit.LOGIN_SUCCEEDED)
        session.commit()

        session.add(models.AuditEntry(
            organization_id=ORG, seq=first.seq, prev_hash="1" * 64,
            entry_hash="2" * 64, action="FORGED", actor_label="x"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        # …and the same for a second entry claiming the same predecessor.
        session.add(models.AuditEntry(
            organization_id=ORG, seq=first.seq + 1, prev_hash=audit.GENESIS,
            entry_hash="3" * 64, action="FORGED", actor_label="x"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        _assert_linear(session, ORG, expected=1)
    finally:
        session.close()
