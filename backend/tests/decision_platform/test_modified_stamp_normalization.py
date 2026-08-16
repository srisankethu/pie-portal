"""The resume cursor's stamps, made one dialect.

``IngestedDocument.modified_at`` is a string and the sync cursor is
``func.max()`` over it — a *lexicographic* max, which is a time max only while
every stamp shares one offset format. Zoho writes the book's own offset
(``+0530``-style, with and without the colon, with and without seconds), so the
day a second dress appears in the table the max starts electing the wrong stamp
and the incremental listing silently stops in the wrong place.

The fix is at write time: ``mark_ingested`` rewrites every stamp it can place
onto the UTC line (``clock.utc_stamp``), and a stamp it cannot place is kept
verbatim — never zeroed, never nulled — and excluded from the max by shape.
These tests pin the rewrite, the max, the resume equality it must not break,
and the migration that brings pre-existing rows onto the same line.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from app import clock
from app.domain import models
from app.repositories import ReadModelRepository


# ── the rewrite itself ──────────────────────────────────────────────────────
def test_every_zoho_offset_dress_lands_on_one_utc_form():
    canonical = "2026-07-02T04:30:00Z"
    assert clock.utc_stamp("2026-07-02T10:00:00+0530") == canonical
    assert clock.utc_stamp("2026-07-02T10:00:00+05:30") == canonical
    assert clock.utc_stamp("2026-07-02T10:00+0530") == canonical       # no seconds
    assert clock.utc_stamp("2026-07-02T04:30:00Z") == canonical        # already there
    assert clock.utc_stamp("2026-07-02T04:30:00+00:00") == canonical
    # Sub-second precision is dropped: the fixed width is what makes the
    # string order the time order.
    assert clock.utc_stamp("2026-07-02T10:00:00.123456+0530") == canonical


def test_a_stamp_without_an_offset_is_unplaceable_not_guessed():
    """A naive stamp is evidence missing. Assuming UTC — or the business zone —
    would store a guess as a fact, so the answer is "cannot place" and the
    caller keeps the verbatim string instead."""
    assert clock.utc_stamp("2026-08-01T10:00:00") is None
    assert clock.utc_stamp("2026-08-01") is None
    assert clock.utc_stamp("stamp-1") is None
    assert clock.utc_stamp("") is None
    assert clock.utc_stamp(None) is None


# ── the cursor ──────────────────────────────────────────────────────────────
def _repo(session, connection_id="c1"):
    return ReadModelRepository(session, "org", connection_id=connection_id)


def test_mixed_offsets_yield_the_truly_latest_cursor(session):
    """The defect this exists for. ``23:30+0530`` is 18:00 UTC and ``19:00Z``
    is an hour later, but as raw strings the ``+0530`` one is the bigger — a
    lexicographic max over the verbatim stamps elects the *earlier* instant,
    and the incremental listing stops short of the newest edit."""
    repo = _repo(session)
    repo.mark_ingested("invoice", "older-but-string-bigger", "2026-07-01T23:30:00+0530")
    repo.mark_ingested("invoice", "truly-latest", "2026-07-01T19:00:00Z")
    session.flush()

    assert repo.ingested_high_water("invoice") == "2026-07-01T19:00:00Z"


def test_an_unplaceable_stamp_is_kept_verbatim_and_left_out_of_the_max(session):
    """Absence of evidence is not a pass, and it is not a zero either. The
    stamp is not zeroed (that invents a modification time) and not nulled
    (that re-fetches the document's detail on every run forever) — it stays
    verbatim, resumes its own document by equality, and may not be the cursor:
    ``stamp-1`` sorts above every digit-led stamp, so letting it into the max
    would wedge the incremental listing permanently."""
    repo = _repo(session)
    repo.mark_ingested("invoice", "odd", "stamp-1")
    session.flush()

    row = session.scalar(select(models.IngestedDocument).where(
        models.IngestedDocument.doc_id == "odd"))
    assert row.modified_at == "stamp-1"
    # Nothing canonical held: no floor, list everything — the honest answer.
    assert repo.ingested_high_water("invoice") is None

    repo.mark_ingested("invoice", "fine", "2026-07-01T10:00:00+0530")
    session.flush()
    assert repo.ingested_high_water("invoice") == "2026-07-01T04:30:00Z"


def test_the_stored_stamp_is_the_canonical_rewrite(session):
    repo = _repo(session)
    repo.mark_ingested("invoice", "A", "2026-07-02T10:00:00+0530")
    session.flush()

    row = session.scalar(select(models.IngestedDocument).where(
        models.IngestedDocument.doc_id == "A"))
    assert row.modified_at == "2026-07-02T04:30:00Z"


# ── the resume equality the rewrite must not break ──────────────────────────
def _skipper(session, doc_type="invoice"):
    from app.ingestion.sync import SyncService

    svc = SyncService.__new__(SyncService)
    svc.resume = True
    svc.repo = _repo(session)
    return svc._skipper(doc_type)


def test_resume_still_skips_the_unchanged_document_in_any_offset_dress(session):
    """The stored side is canonical now and the listed side arrives raw, so
    the predicate rewrites its own side identically — otherwise the same
    instant in a different dress reads as an edit and every document pays its
    detail call again, which is the exact bug the stamp store exists to end."""
    repo = _repo(session)
    repo.mark_ingested("invoice", "A", "2026-07-02T10:00:00+0530")
    session.flush()

    skip = _skipper(session)
    assert skip("A", "2026-07-02T10:00:00+0530") is True    # the same string
    assert skip("A", "2026-07-02T10:00:00+05:30") is True   # same instant, colon dress
    assert skip("A", "2026-07-02T04:30:00Z") is True        # same instant, UTC dress
    assert skip("A", "2026-07-09T18:00:00+0530") is False   # genuinely edited
    assert skip("A", "") is True             # unstamped: trust what we hold
    assert skip("missing", "2026-07-02T10:00:00+0530") is False


def test_a_verbatim_stamp_still_resumes_by_exact_match(session):
    repo = _repo(session)
    repo.mark_ingested("invoice", "odd", "stamp-1")
    session.flush()

    skip = _skipper(session)
    assert skip("odd", "stamp-1") is True
    assert skip("odd", "stamp-2") is False


# ── the rows that already exist ─────────────────────────────────────────────
def test_the_migration_rewrites_held_stamps_and_keeps_the_unplaceable(tmp_path):
    """Existing rows must land on the same line as new writes, or one table
    holds two dialects of the same instant forever. Verified through the real
    chain: a database stopped one revision short, seeded with the old shapes,
    then migrated."""
    backend = Path(__file__).resolve().parents[2]
    db = tmp_path / "t9.db"

    def alembic(*args):
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{db}"}
        return subprocess.run([sys.executable, "-m", "alembic", *args],
                              cwd=backend, env=env, capture_output=True, text=True)

    r = alembic("upgrade", "d5a2e7c31b84")   # the revision t9f2c7a41d0e revises
    assert r.returncode == 0, r.stderr[-2000:]

    conn = sqlite3.connect(db)
    seeded = [
        ("r1", "offset", "2026-07-01T23:30:00+0530"),   # → 2026-07-01T18:00:00Z
        ("r2", "colon", "2026-07-01T23:30:00+05:30"),   # → the same instant
        ("r3", "utc", "2026-07-01T19:00:00Z"),          # already canonical
        ("r4", "odd", "stamp-1"),                       # unparseable: verbatim
        ("r5", "naive", "2026-08-01T10:00:00"),         # no offset: verbatim
        ("r6", "empty", None),                          # stays NULL
    ]
    for pk, doc_id, stamp in seeded:
        conn.execute(
            "INSERT INTO ingested_documents (ingested_document_id,"
            " organization_id, doc_type, doc_id, modified_at, fetched_at)"
            " VALUES (?, 'org', 'invoice', ?, ?, '2026-08-16 00:00:00')",
            (pk, doc_id, stamp))
    conn.commit()

    r = alembic("upgrade", "t9f2c7a41d0e")
    assert r.returncode == 0, r.stderr[-2000:]

    got = dict(conn.execute(
        "SELECT doc_id, modified_at FROM ingested_documents").fetchall())
    conn.close()
    assert got == {
        "offset": "2026-07-01T18:00:00Z",
        "colon": "2026-07-01T18:00:00Z",
        "utc": "2026-07-01T19:00:00Z",
        "odd": "stamp-1",
        "naive": "2026-08-01T10:00:00",
        "empty": None,
    }
