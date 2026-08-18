"""The migration system, tested as the thing that actually breaks deployments.

These exist because of a specific incident, and each one pins a fact that was
false at the time:

* ``bootstrap.ensure_schema`` fell back to ``Base.metadata.create_all`` whenever
  Alembic raised. That builds every table and writes no ``alembic_version`` row,
  so the database ends up holding a full schema Alembic thinks was never
  migrated — and ``alembic upgrade head`` then dies with "table already exists".
  Dropping the tables did not help: the next boot repeated the fallback. A
  recoverable failure was being turned into a permanently wedged database.
* ``alembic/env.py`` overwrote ``sqlalchemy.url`` with ``settings.DATABASE_URL``
  unconditionally, so a caller pointing Alembic at another database was ignored
  and the *configured* one was migrated instead — silently, while reporting
  success for the database that was asked about.
* A freshly migrated database differed from the ORM metadata in 130 places.

They are deliberately slow-ish (each builds a real SQLite database through the
real migration chain) because anything cheaper would not have caught any of it.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.db import Base
from app.domain import models  # noqa: F401  (populate metadata)
from app.migration_state import (BEHIND, CURRENT, EMPTY, UNKNOWN_REV, UNSTAMPED,
                                 head_revision, inspect_database)

BACKEND = Path(__file__).resolve().parents[2]


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the real CLI, the way an operator would."""
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=BACKEND, env=env, capture_output=True, text=True)


def _engine(db_path: Path):
    return create_engine(f"sqlite:///{db_path}")


@pytest.fixture()
def db(tmp_path) -> Path:
    return tmp_path / "t.db"


# ── an empty database must migrate cleanly ──────────────────────────────────
def test_a_completely_empty_database_migrates_to_head(db):
    sqlite3.connect(db).close()
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    assert inspect_database(_engine(db)).state == CURRENT


def test_every_model_table_exists_after_migrating(db):
    _alembic(db, "upgrade", "head")
    present = set(inspect(_engine(db)).get_table_names())
    missing = {t.name for t in Base.metadata.sorted_tables} - present
    assert not missing, f"migrations do not create: {sorted(missing)}"


def test_a_fresh_database_matches_the_models_exactly(db):
    """Autogenerate must be trustworthy.

    130 diffs used to appear here — 77 columns nullable that the models declare
    NOT NULL, 4 missing foreign keys, 2 missing indexes and 22 name mismatches.
    Any real drift was invisible inside that noise, and an autogenerate nobody
    reads is one that eventually drops something it should not have.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    _alembic(db, "upgrade", "head")
    with _engine(db).connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)

    kinds: Counter = Counter()
    for d in diffs:
        for sub in (d if isinstance(d, list) else [d]):
            kinds[sub[0]] += 1
    assert not diffs, (
        f"the schema and the models disagree in {sum(kinds.values())} places "
        f"({dict(kinds)}). Add a migration reconciling them — do not edit a "
        f"released one.")


# ── the upgrade path, not just the endpoint ─────────────────────────────────
def _revision_order() -> list[str]:
    """Every revision, in an order they can actually be applied in.

    Read from alembic's own graph rather than assembled here: it topologically
    sorts, which is the part that matters the moment the history is not a line.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def _one_before_head() -> str:
    """The revision immediately before head, by name.

    ``downgrade -1`` said the same thing more briefly and stopped being
    unambiguous when the history gained a merge point: a merge revision has two
    parents, so "one back" is a question with two answers and alembic refuses
    rather than picking. Naming the revision asks what these tests were always
    asking — undo exactly the newest one — and keeps working whatever shape the
    graph is.
    """
    return _revision_order()[-2]


def test_migrations_apply_one_at_a_time(db):
    """Each revision must stand on its own.

    A migration that only works when run in the same batch as its neighbour
    fails on exactly one deployment: the one that was interrupted halfway.

    Walks *named* revisions rather than stepping ``upgrade +1``. The relative
    form was simpler and stopped working the day this history stopped being a
    line: two branches grew from ``y5runlog``, were reconciled by a merge
    revision, and ``+1`` from that parent is then ambiguous — alembic says so,
    in those words, and refuses.

    The assertion is unchanged and is if anything stronger. Every revision is
    still applied on its own, in order, with nothing else in the batch; naming
    each one also means a failure reports *which* revision failed instead of
    how far the walk got. The intent — no migration that needs its neighbour —
    is a property of the revisions, not of how the test steps between them.
    """
    sqlite3.connect(db).close()
    order = _revision_order()
    assert len(order) > 10, "expected the full chain to be walked"

    for rev in order:
        r = _alembic(db, "upgrade", rev)
        assert r.returncode == 0, f"revision {rev}: {r.stderr[-1500:]}"

    assert inspect_database(_engine(db)).current == head_revision()


def test_the_chain_has_exactly_one_head():
    """Two heads make `upgrade head` ambiguous and it refuses to run."""
    r = subprocess.run([sys.executable, "-m", "alembic", "heads"],
                       cwd=BACKEND, capture_output=True, text=True,
                       env={**os.environ})
    heads = [ln for ln in r.stdout.splitlines() if "(head)" in ln]
    assert len(heads) == 1, f"expected one head, got: {heads}"


def test_downgrade_to_base_then_up_again(db):
    """Reversibility, end to end."""
    assert _alembic(db, "upgrade", "head").returncode == 0
    assert _alembic(db, "downgrade", "base").returncode == 0
    remaining = set(inspect(_engine(db)).get_table_names()) - {"alembic_version"}
    assert not remaining, f"downgrade left tables behind: {sorted(remaining)}"
    assert _alembic(db, "upgrade", "head").returncode == 0
    assert inspect_database(_engine(db)).state == CURRENT


def test_the_newest_migration_is_reversible(db):
    assert _alembic(db, "upgrade", "head").returncode == 0
    assert _alembic(db, "downgrade", _one_before_head()).returncode == 0
    assert _alembic(db, "upgrade", "head").returncode == 0


# ── state detection: the four cases that need different fixes ───────────────
def test_empty_is_distinguished_from_unstamped(db, tmp_path):
    sqlite3.connect(db).close()
    assert inspect_database(_engine(db)).state == EMPTY

    wedged = tmp_path / "wedged.db"
    Base.metadata.create_all(_engine(wedged))       # what the old fallback did
    assert inspect_database(_engine(wedged)).state == UNSTAMPED


def test_the_unstamped_diagnosis_does_not_tell_you_to_upgrade(tmp_path):
    """This is the whole point of the incident.

    A database built outside Alembic cannot be fixed by `alembic upgrade head` —
    it fails with "table already exists". Advising it wastes the one thing an
    operator has least of during an outage.
    """
    wedged = tmp_path / "wedged.db"
    Base.metadata.create_all(_engine(wedged))
    state = inspect_database(_engine(wedged))

    assert state.state == UNSTAMPED
    assert "stamp" in state.summary
    assert "alembic_version" in state.summary


def test_upgrade_really_does_fail_on_an_unstamped_database(tmp_path):
    """Pins the premise the diagnosis rests on, rather than assuming it."""
    wedged = tmp_path / "wedged.db"
    Base.metadata.create_all(_engine(wedged))
    r = _alembic(wedged, "upgrade", "head")
    assert r.returncode != 0
    assert "already exists" in (r.stderr + r.stdout)


def test_behind_is_detected_and_counted(db):
    # Named for the same reason `_one_before_head` is: "two back" from a merge
    # revision is ambiguous, and the thing being asserted is that a database two
    # revisions short reports as BEHIND with two pending — which is a fact about
    # the count, not about how it got there.
    assert _alembic(db, "upgrade", "head").returncode == 0
    assert _alembic(db, "downgrade", _revision_order()[-3]).returncode == 0
    state = inspect_database(_engine(db))
    assert state.state == BEHIND
    assert state.pending and len(state.pending) == 2
    assert "upgrade head" in state.summary


def test_an_unknown_revision_is_not_reported_as_behind(db):
    """A database migrated by newer code must not be told to upgrade."""
    _alembic(db, "upgrade", "head")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE alembic_version SET version_num='deadbeefcafe'")
    conn.commit()
    conn.close()

    state = inspect_database(_engine(db))
    assert state.state == UNKNOWN_REV
    assert "Do not upgrade" in state.summary


def test_current_is_healthy(db):
    _alembic(db, "upgrade", "head")
    state = inspect_database(_engine(db))
    assert state.healthy and state.state == CURRENT


# ── the URL bug: Alembic must migrate the database it was given ─────────────
def test_an_explicit_url_wins_over_the_configured_one(tmp_path):
    """``env.py`` used to discard the caller's URL and use settings.DATABASE_URL.

    The consequence was not a failure but something worse: it reported success
    for a database it had never touched, and migrated a different one. In
    testing it stamped a live development database to head without applying
    anything to it.
    """
    configured = tmp_path / "configured.db"
    target = tmp_path / "target.db"

    script = (
        "from app.bootstrap import ensure_schema\n"
        f"ensure_schema('sqlite:///{target}')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND, capture_output=True, text=True,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{configured}",
             "AUTO_BOOTSTRAP": "0"})
    assert r.returncode == 0, r.stderr[-2000:]

    assert target.exists(), "the database we asked for was never created"
    assert inspect_database(_engine(target)).state == CURRENT
    assert not configured.exists() or not inspect(
        _engine(configured)).get_table_names(), (
        "migrating one database must not touch the configured one")


# ── bootstrap must not invent a schema ──────────────────────────────────────
def test_bootstrap_never_creates_tables_outside_alembic(tmp_path, monkeypatch):
    """The removed fallback, pinned so it cannot come back.

    If Alembic cannot run, the correct outcome is a raised error — not a
    ``create_all`` that leaves the database unmigratable forever.
    """
    from app import bootstrap as bs

    db = tmp_path / "boom.db"
    sqlite3.connect(db).close()

    def explode(*_a, **_k):
        raise RuntimeError("alembic is broken")

    monkeypatch.setattr("alembic.command.upgrade", explode)
    with pytest.raises(RuntimeError):
        bs.ensure_schema(f"sqlite:///{db}")

    tables = set(inspect(_engine(db)).get_table_names())
    assert not tables, (
        f"a failed bootstrap created {sorted(tables)} outside Alembic — this is "
        f"exactly the state that cannot be migrated afterwards")


def test_bootstrap_adopts_a_complete_unstamped_schema(tmp_path):
    """Installations wedged by the old fallback must be able to recover."""
    from app.bootstrap import ensure_schema

    db = tmp_path / "legacy.db"
    Base.metadata.create_all(_engine(db))
    assert inspect_database(_engine(db)).state == UNSTAMPED

    action = ensure_schema(f"sqlite:///{db}")
    assert action == "stamped"
    assert inspect_database(_engine(db)).state == CURRENT


def test_bootstrap_refuses_to_stamp_an_incomplete_schema(tmp_path):
    """Stamping claims every migration has been applied. Claiming that of a
    schema that is missing tables makes every later migration skip work it
    needed to do — worse than leaving it unstamped."""
    from app.bootstrap import SchemaBootstrapError, ensure_schema

    db = tmp_path / "partial.db"
    engine = _engine(db)
    subset = [t for t in Base.metadata.sorted_tables if t.name != "customers"]
    Base.metadata.create_all(engine, tables=subset)

    with pytest.raises(SchemaBootstrapError) as exc:
        ensure_schema(f"sqlite:///{db}")
    assert "customers" in str(exc.value)
    assert inspect_database(engine).state == UNSTAMPED, "must be left untouched"


# ── configuration hygiene ───────────────────────────────────────────────────
def test_alembic_ini_carries_no_database_url():
    """A URL in the ini is a second source of truth, and the one that wins by
    accident on whichever machine forgot to set the environment."""
    ini = (BACKEND / "alembic.ini").read_text()
    for line in ini.splitlines():
        stripped = line.strip()
        if stripped.startswith("sqlalchemy.url"):
            pytest.fail(f"alembic.ini sets a database URL: {stripped!r}")


def test_there_is_exactly_one_declarative_base():
    """Two bases means two metadata objects, and Alembic sees only one of them —
    so half the models silently never get migrations."""
    import app.db as db_module
    from sqlalchemy.orm import DeclarativeBase

    bases = [
        obj for name in dir(db_module)
        if isinstance(obj := getattr(db_module, name), type)
        and issubclass(obj, DeclarativeBase) and obj is not DeclarativeBase
    ]
    assert len(bases) == 1, f"expected one declarative Base, found {bases}"


def test_every_model_is_registered_in_the_metadata_alembic_sees():
    """A model Alembic cannot see gets no migration, and its table simply never
    exists in production."""
    import inspect as pyinspect

    declared = {
        cls.__tablename__
        for _, cls in pyinspect.getmembers(models, pyinspect.isclass)
        if hasattr(cls, "__tablename__")
    }
    assert declared - set(Base.metadata.tables) == set()


def test_alembic_env_uses_the_application_metadata():
    """If env.py points at a different metadata object, autogenerate silently
    compares the database against nothing."""
    env = (BACKEND / "alembic" / "env.py").read_text()
    assert "from app.db import Base" in env
    assert "target_metadata = Base.metadata" in env
    assert "from app.domain import models" in env, (
        "without importing the models the metadata is empty")


# ── the health endpoint: it has to be able to say "no" ──────────────────────
def _client(db_path: Path):
    """An app instance bound to one database, with bootstrap off.

    Built in-process against a patched engine rather than by subprocess so the
    assertions can be about the response body.
    """
    from fastapi.testclient import TestClient

    import app.db as db_module
    import app.main as main_module

    engine = create_engine(f"sqlite:///{db_path}",
                           connect_args={"check_same_thread": False})
    original = db_module.engine
    db_module.engine = engine
    return TestClient(main_module.app), (db_module, original)


def test_health_reports_a_migrated_database_as_ok(db):
    _alembic(db, "upgrade", "head")
    client, (mod, original) = _client(db)
    try:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["migration"]["state"] == CURRENT
        assert body["schema_gap"] is None
    finally:
        mod.engine = original


def test_health_fails_loudly_on_a_behind_database(db):
    """The failure the old endpoint could not express.

    It returned ``{"ok": true}`` unconditionally, so a deployment whose database
    was behind answered health checks perfectly while every real request 500ed.
    """
    _alembic(db, "upgrade", "head")
    _alembic(db, "downgrade", _one_before_head())
    client, (mod, original) = _client(db)
    try:
        r = client.get("/api/health")
        assert r.status_code == 503, "a load balancer must be able to see this"
        body = r.json()
        assert body["ok"] is False
        assert body["migration"]["state"] == BEHIND
        # A positive count, not a specific one. This test is about the *health
        # response* — 503, ok:false, BEHIND, and a summary naming the fix — and
        # it hardcoded 1 only because downgrading one revision used to un-apply
        # exactly one. Past a merge point that stopped being true: the named
        # target here has a sibling branch, so two revisions come off. The exact
        # count is pinned by `test_behind_is_detected_and_counted`, which exists
        # for it and now asserts the diamond's real answer.
        assert body["migration"]["pending_count"] >= 1
        assert "upgrade head" in body["migration"]["summary"]
    finally:
        mod.engine = original


def test_health_names_the_unstamped_case_specifically(tmp_path):
    wedged = tmp_path / "wedged.db"
    Base.metadata.create_all(_engine(wedged))
    client, (mod, original) = _client(wedged)
    try:
        body = client.get("/api/health").json()
        assert body["ok"] is False
        assert body["migration"]["state"] == UNSTAMPED
        assert "stamp" in body["migration"]["summary"]
    finally:
        mod.engine = original


def test_health_reports_an_empty_database_rather_than_crashing(db):
    sqlite3.connect(db).close()
    client, (mod, original) = _client(db)
    try:
        r = client.get("/api/health")
        assert r.status_code == 503
        assert r.json()["migration"]["state"] == EMPTY
    finally:
        mod.engine = original
