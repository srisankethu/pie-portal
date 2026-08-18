"""Every value this code writes has to fit the column it is written to.

SQLite ignores a declared `String(n)`; Postgres — which production runs — does
not. So a column one character too narrow passes the whole suite, passes every
review, and fails at the first flush against the real database. It has now
happened twice: `business_states.key` (fixed in `v2state_key_width`) and then
`signals.subject_entity_id`, which is the same composite-key mistake in the
table one layer up. The second one killed an hour-long sync — the rejected
flush poisoned the session, so the pull could not even write its own record.

These tests are the check that runs on SQLite anyway, by comparing the value
against the *declared* length rather than asking the database to enforce it.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import String, inspect as sa_inspect

from app.commercial.config import CommercialThresholds
from app.commercial.detectors import detect
from app.commercial.economics import line_economics
from app.commercial.metrics import compute_relationship
from app.commercial.subject import encode
from app.db import Base
from app.domain import models
from app.signals.base import CostRow, SaleRow

#: A real one, so the arithmetic below is the arithmetic production does.
UUID = "180f6b85-692b-4672-87b8-2291cdf756ba"


def _overlong(row) -> list[str]:
    """Every String column of one ORM row whose value does not fit. Declared
    length, not the database's opinion of it — that is the whole point."""
    problems = []
    for column in sa_inspect(type(row)).columns:
        if not isinstance(column.type, String) or column.type.length is None:
            continue
        value = getattr(row, column.key, None)
        if isinstance(value, str) and len(value) > column.type.length:
            problems.append(
                f"{row.__tablename__}.{column.key}: {len(value)} chars into "
                f"String({column.type.length}) — {value!r}")
    return problems


# ── the specific columns, and the arithmetic behind their width ─────────────
def test_a_customer_item_subject_does_not_fit_in_sixty_four_characters():
    """The fact the four widths rest on, stated once so it cannot be forgotten."""
    subject = encode(UUID, UUID)
    assert len(subject) == 74, "two UUIDs and a separator"
    assert len(subject) > 64, "which is why String(64) was always wrong here"


@pytest.mark.parametrize("model", [
    models.Signal, models.Decision, models.AiCallLog, models.OutcomeSnapshot,
])
def test_every_table_that_holds_a_subject_can_hold_a_composite_one(model):
    """All four carry the *same* value — the signal's subject, passed along —
    so all four need the same width. Widening one and missing another is how
    this reappears one table further down the chain."""
    column = sa_inspect(model).columns["subject_entity_id"]
    assert column.type.length >= len(encode(UUID, UUID)), (
        f"{model.__tablename__}.subject_entity_id cannot hold a Customer × "
        f"Item subject; see alembic z6subject")


def test_the_models_and_the_migration_agree_on_the_width():
    """160, the same number `v2state_key_width` chose, for the same reason."""
    for table, column in (("signals", "subject_entity_id"),
                          ("decisions", "subject_entity_id"),
                          ("ai_call_logs", "subject_entity_id"),
                          ("outcome_snapshots", "subject_entity_id"),
                          ("business_states", "key"),
                          ("state_transitions", "key")):
        assert Base.metadata.tables[table].columns[column].type.length == 160


# ── the real path that produced the rejected row ────────────────────────────
def _eroding_lines(customer: str, product: str):
    """A relationship with a real margin decline — enough to emit signals."""
    as_of = date(2026, 7, 1)

    def one(days_ago: int, cost: str):
        when = as_of - timedelta(days=days_ago)
        qty, price = Decimal("100"), Decimal("200")
        sale = SaleRow(customer_id=customer, product_id=product, date=when,
                       qty=qty, unit_price=price, line_revenue=qty * price,
                       source_ref={"record_type": "invoice", "record_id": "INV"},
                       external_ref=f"INV-{days_ago}:1")
        costs = [CostRow(product_id=product, date=when - timedelta(days=1),
                         qty=Decimal("1"), unit_cost=Decimal(cost),
                         source_ref={"record_id": "B"}, external_ref="B:1")]
        return line_economics(sale, costs)

    return ([one(d, "100") for d in (700, 600, 500, 400, 300)]
            + [one(d, "150") for d in (80, 50, 20)])


def test_the_signals_a_real_detection_produces_fit_their_columns(session):
    """The failing case end to end: UUID ids, the real detectors, real rows.

    Not a unit test of a width — a test that the values this code actually
    writes fit where it writes them. The subject here is 74 characters for the
    same reason the production one was.
    """
    org = "org_widths"
    session.add(models.Organization(organization_id=org, name="SLS", currency="INR",
                                    config={}))
    th = CommercialThresholds()
    metrics = compute_relationship(UUID, UUID, _eroding_lines(UUID, UUID),
                                   date(2026, 7, 1), th)
    drafts = detect(metrics, None, th, [])
    assert drafts, "the fixture must actually produce signals, or this proves nothing"

    # Through the real converter, so this is the row the sync writes and not a
    # hand-built lookalike that could differ in exactly the field under test.
    rows = [draft.to_model(org) for draft in drafts]

    problems = [p for row in rows for p in _overlong(row)]
    assert not problems, "values too long for their columns:\n" + "\n".join(problems)

    # And they round-trip, which is what the sync needs of them.
    session.add_all(rows)
    session.flush()
    stored = session.query(models.Signal).first()
    assert stored.subject_entity_id == encode(UUID, UUID)
