"""A key written into a JSON config column survives the commit.

SQLAlchemy decides a row is dirty by *assignment*. ``org.config = {...}`` marks
it; ``org.config["k"] = v`` does not, so on a bare ``JSON`` column an in-place
write is flushed as nothing at all and the next read returns what was there
before — with no error, no warning, and a commit that reports success.

That is not a hypothetical. The removed Zoho OAuth flow stored its CSRF state
as ``org.config["oauth_states"][hash] = {...}`` and then read it back on the
callback; the write never landed, so every authorization failed state
validation. The flow is gone, and the column it was built on is not: four
modules across three layers read and write these three ``config`` columns, and
the next person to reach for the obvious idiom would get the same silence.

So the columns are ``MutableDict``. These tests are the reason it can't be
quietly removed as decoration — they fail on a bare ``JSON``, which is what the
original defect ran on.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.domain import models


def _sessions(engine):
    return sessionmaker(bind=engine, autoflush=False, future=True)


@pytest.fixture()
def make_session(engine):
    maker = _sessions(engine)

    def _open():
        return maker()

    return _open


def test_an_in_place_key_write_to_an_organizations_config_persists(make_session):
    """The exact idiom that lost the OAuth state, on the exact column."""
    with make_session() as s:
        s.add(models.Organization(organization_id="o1", name="T", config={}))
        s.commit()

    with make_session() as s:
        org = s.get(models.Organization, "o1")
        org.config["ai_provider"] = "anthropic"      # in place, no assignment
        s.commit()

    with make_session() as s:
        assert s.get(models.Organization, "o1").config == {"ai_provider": "anthropic"}


def test_the_whole_dict_assignment_every_writer_uses_today_still_works(make_session):
    """The safe idiom must not regress. ``ai/byok.py`` and
    ``routers/data_status.py`` both build a new dict and assign it, and wrapping
    the column must leave that untouched."""
    with make_session() as s:
        s.add(models.Organization(organization_id="o2", name="T",
                                  config={"auto_sync_hours": 6}))
        s.commit()

    with make_session() as s:
        org = s.get(models.Organization, "o2")
        org.config = {**(org.config or {}), "ai_provider": "openai"}
        s.commit()

    with make_session() as s:
        assert s.get(models.Organization, "o2").config == {
            "auto_sync_hours": 6, "ai_provider": "openai"}


def test_a_deleted_key_is_also_a_change(make_session):
    """Consuming a single-use value is a ``pop``, which is the other half of the
    same defect: the OAuth callback popped its state to spend it, and the pop
    did not persist either — so the state stayed valid for its whole TTL and
    could be replayed."""
    with make_session() as s:
        s.add(models.Organization(organization_id="o3", name="T",
                                  config={"a": 1, "b": 2}))
        s.commit()

    with make_session() as s:
        org = s.get(models.Organization, "o3")
        org.config.pop("a")
        s.commit()

    with make_session() as s:
        assert s.get(models.Organization, "o3").config == {"b": 2}


@pytest.mark.parametrize("model, pk_field, extra", [
    (models.ZohoCredential, "credential_id", {"owner_organization_id": "o4"}),
    (models.ZohoConnection, "connection_id",
     {"organization_id": "o4", "zoho_organization_id": "z1"}),
])
def test_the_connector_config_columns_are_covered_too(make_session, model,
                                                      pk_field, extra):
    """A credential's and a connection's ``config`` hold each connector's own
    settings, written by ``ingestion`` and read by the routers. Same column
    shape, same trap, so the same wrapper — a guardrail on one of three is one
    somebody reasonably assumes covers the other two."""
    with make_session() as s:
        s.add(models.Organization(organization_id="o4", name="T", config={}))
        row = model(config={"tenant": "a"}, **extra)
        s.add(row)
        s.commit()
        pk = getattr(row, pk_field)

    with make_session() as s:
        # Bound to a name on purpose: the identity map holds instances weakly,
        # so mutating the return value of `get` without keeping a reference can
        # lose the change to a collection before the commit.
        row = s.get(model, pk)
        row.config["environment"] = "prod"
        s.commit()

    with make_session() as s:
        assert s.get(model, pk).config == {"tenant": "a", "environment": "prod"}


def test_the_guardrail_does_not_reach_a_nested_dict(make_session):
    """The boundary, pinned so nobody widens the claim by accident.

    ``MutableDict`` tracks writes to *this* dict's own keys. A mutation one
    level down is a plain dict again and is still lost. Nothing nests today;
    this test is here so that a writer who starts to finds out from a failing
    assertion rather than from missing data.
    """
    with make_session() as s:
        s.add(models.Organization(organization_id="o5", name="T",
                                  config={"nested": {"a": 1}}))
        s.commit()

    with make_session() as s:
        org = s.get(models.Organization, "o5")
        org.config["nested"]["b"] = 2      # one level down — not tracked
        s.commit()

    with make_session() as s:
        assert s.get(models.Organization, "o5").config == {"nested": {"a": 1}}, (
            "MutableDict now tracks nested mutation — widen the comment in "
            "models.Organization.config, which says it does not.")
