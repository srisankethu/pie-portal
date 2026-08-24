"""The polled schema check answers from memory — without ever lying about it.

``/api/health`` is called by a load balancer, a deploy gate and the app itself,
and behind it ``missing_columns`` reflected every mapped table: 73 statements
per poll here, and 73 round trips to ``information_schema`` on Postgres. It is
now remembered against the revision the database is stamped at.

What these tests pin is the part that makes that safe rather than the speed-up:
the cached answer is keyed on the revision, so a database that has just been
migrated is reflected again on the very next poll — which is what the endpoint's
own docstring promises an operator who migrates a running deployment.
"""
from __future__ import annotations

import pytest
from sqlalchemy import event, text

from app import cache as cache_module
from app import schema_check


@pytest.fixture(autouse=True)
def clean_cache():
    cache_module.clear_all()
    yield
    cache_module.clear_all()


@pytest.fixture()
def counting(engine):
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine, "before_cursor_execute", record)


def test_the_second_poll_does_not_reflect_the_schema_again(engine, counting):
    schema_check.gaps_for(engine, "rev-1")
    first = len(counting)
    counting.clear()

    schema_check.gaps_for(engine, "rev-1")

    assert first > 20, "the uncached call should be reflecting every table"
    assert len(counting) == 0, "the second poll reflected the schema again"


def test_a_migrated_database_is_reflected_again_on_the_next_poll(engine, counting):
    """The property that makes this cacheable at all. The key is the revision,
    so an upgrade invalidates it — no restart, no waiting for a TTL."""
    schema_check.gaps_for(engine, "rev-1")
    counting.clear()

    schema_check.gaps_for(engine, "rev-2")

    assert len(counting) > 20, "a new revision must be reflected, not remembered"


def test_two_databases_do_not_share_an_answer(engine, counting):
    """Keyed on the database as well as the revision: a test suite — and a
    deployment reading a replica — asks the same question of more than one."""
    import dbsupport

    other = dbsupport.fresh_engine()
    schema_check.gaps_for(engine, "rev-1")
    counting.clear()
    schema_check.gaps_for(other, "rev-1")

    # The listener is on the first engine, so silence here means the second
    # engine's answer was not taken from the first one's entry.
    assert len(counting) == 0
    assert schema_check.gaps_for(other, "rev-1") == {}


def test_the_cached_answer_is_the_uncached_one(engine):
    """A cache that answers differently from the function it fronts is not a
    cache, and this check is one whose wrong answer is a green light."""
    assert schema_check.gaps_for(engine, "rev-1") == schema_check.missing_columns(engine)


def test_a_real_gap_is_reported_through_the_cache(engine):
    """The check must still fail when the schema cannot serve the code."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE customers RENAME COLUMN name TO name_old"))

    gaps = schema_check.gaps_for(engine, "rev-gap")
    assert "name" in gaps.get("customers", []), gaps
    assert schema_check.describe(gaps) is not None


def test_the_startup_check_never_reads_from_the_cache(engine, counting):
    """``check_at_startup`` establishes the truth once, and a check that answers
    from memory is the wrong thing to hand to the code whose job that is."""
    schema_check.gaps_for(engine, "rev-1")
    counting.clear()

    schema_check.check_at_startup(engine)

    assert len(counting) > 20


def test_the_head_revision_is_read_from_disk_once(monkeypatch):
    """``head_revision`` walks every migration file. That is code, and code does
    not change under a running process — it was 16 ms of filesystem work per
    health poll."""
    from app import migration_state

    migration_state.head_revision.cache_clear()
    calls = []
    real = migration_state._script_directory

    def counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(migration_state, "_script_directory", counted)
    first = migration_state.head_revision()
    again = migration_state.head_revision()

    assert first == again
    assert len(calls) == 1
    migration_state.head_revision.cache_clear()
