"""The backfill reads stored drafts through the live writer's rules."""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.domain import models
from app.retrieval.backfill import backfill_from_drafts, pairs_in


@pytest.fixture()
def session():
    s = sessionmaker(bind=dbsupport.fresh_engine())()
    yield s
    s.close()


def _line(**over):
    base = {"id": "l1", "reqCode": "12mm drill for SS", "supplyCode": "4149315",
            "semantics": "REQUIREMENT", "sel": "USER", "customerScope": "identity-pitti"}
    base.update(over)
    return base


def test_only_a_persons_choice_on_a_scoped_requirement_qualifies():
    lines = [
        _line(),                                          # yes
        _line(id="l2", sel="AUTO"),                       # the engine's pick
        _line(id="l3", semantics="IDENTITY"),             # a code: the gate's
        _line(id="l4", customerScope=None),               # unlinked
        _line(id="l5", supplyCode="12mm drill for SS"),   # the line itself
        _line(id="l6", supplyCode=None),                  # nothing chosen
        _line(id="l7", sel="MANUAL"),                     # yes
    ]
    assert [p["line_id"] for p in pairs_in(lines)] == ["l1", "l7"]


def test_the_backfill_records_once_and_is_idempotent(session):
    session.add(models.QuoteDraft(
        quote_id="q1", organization_id="org_a", customer_name="Pitti",
        lines=[_line(), _line(id="l2", sel="AUTO")], updated_by_user_id="u1"))
    session.add(models.QuoteDraft(
        quote_id="q2", organization_id="org_b", customer_name="Other",
        lines=[_line(reqCode="end mill 10mm", supplyCode="3668915")]))
    session.commit()

    assert backfill_from_drafts(session, "org_a", dry_run=True) == {
        "drafts": 1, "qualified": 1, "recorded": 0}
    assert session.query(models.CustomerPhraseAlias).count() == 0

    assert backfill_from_drafts(session, "org_a") == {
        "drafts": 1, "qualified": 1, "recorded": 1}
    (row,) = session.query(models.CustomerPhraseAlias).all()
    assert row.organization_id == "org_a" and row.phrase == "12mm drill for SS"
    assert row.source_ref == "backfill quote q1 line l1"
    assert row.recorded_by_user_id == "u1"

    assert backfill_from_drafts(session, "org_a")["recorded"] == 0

    # Every organization, when none is named — and each stays its own.
    assert backfill_from_drafts(session) == {"drafts": 2, "qualified": 2, "recorded": 1}
    orgs = {r.organization_id for r in session.query(models.CustomerPhraseAlias)}
    assert orgs == {"org_a", "org_b"}
