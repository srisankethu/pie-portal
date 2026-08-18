"""Subject ids widen to fit a Customer × Item pair.

The same defect ``v2state_key_width`` fixed for state keys, in the four columns
that hold a *signal's* subject. A Customer × Item subject is a composite —
``commercial.subject.encode`` joins two 36-character UUIDs with ``::``, so 74
characters — and every one of these columns was ``String(64)``.

SQLite enforces no declared length, so every deployment and the whole test
suite stored them without complaint. Postgres rejected the batch with
``value too long for type character varying(64)``, and because the failure
surfaced at a *commit* rather than at the insert, it took the sync's own
bookkeeping down with it: the poisoned session could no longer write the run
row, so an hour-long pull that had already imported everything ended as a crash
with no counters.

160 matches what the state keys were widened to, and fits the widest legitimate
composite with room.

Purely a widening — no stored value changes, no index changes. The four tables:

  * ``signals`` — where the composite is produced (``commercial/detectors``)
  * ``decisions`` — carries its signal's subject through unchanged
  * ``ai_call_logs`` — telemetry about a call on that subject
  * ``outcome_snapshots`` — copies the signal's subject verbatim

Revision ID: z6subject
Revises: t13plan_requests
Create Date: 2026-08-18

Re-pointed from ``y5runlog`` onto ``t13plan_requests``: two branches added a
revision on the same parent and both were merged, which left alembic with two
heads and stopped the deploy — ``upgrade head`` refuses to guess between them,
correctly, because applying one would leave the other branch's schema missing.

Re-pointing rather than ``alembic merge``, for the reason ``t13plan_requests``
gives for the same decision twice over: **this revision had run nowhere.** It
reached ``main`` only in the commit that created the ambiguity, and from that
moment every ``alembic upgrade head`` — the deploy's pre-deploy step and any
developer's — failed before applying anything. So there is no database that ran
``z6subject`` under its old parent and would now be told it is at head with
``plan_change_requests`` missing. That check is the whole of the argument;
without it, CLAUDE.md §4 is unambiguous that a released revision is reconciled
forward with a merge and never edited.

The alternative was a merge revision, and the cost is not theoretical: a merge
leaves a permanent branchpoint, so ``upgrade +1`` and ``downgrade -1`` both
become "Ambiguous walk" there for ever. ``test_migrations_apply_one_at_a_time``
and ``test_the_newest_migration_is_reversible`` fail on exactly that, and they
are guarding the deployment that gets interrupted halfway. A linear history
keeps both.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "z6subject"
down_revision = "t13plan_requests"
branch_labels = None
depends_on = None

#: table → (column, nullable). Written out rather than read from the models,
#: because a migration runs against schemas from months ago and models describe
#: today.
_COLUMNS = (
    ("signals", "subject_entity_id", False),
    ("decisions", "subject_entity_id", False),
    ("ai_call_logs", "subject_entity_id", True),
    ("outcome_snapshots", "subject_entity_id", False),
)


def upgrade() -> None:
    for table, column, nullable in _COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch:
            batch.alter_column(column, existing_type=sa.String(64),
                               type_=sa.String(160), existing_nullable=nullable)


def downgrade() -> None:
    # Rows whose subject no longer fits are deleted rather than truncated, for
    # the reason `v2state_key_width` gives: a shortened id would silently point
    # at a different subject — or at none — and a signal about the wrong
    # customer is worse than an absent one. These tables are derived from a
    # re-runnable analysis, except `outcome_snapshots`, which is why the
    # downgrade is a last resort and not a routine.
    for table, column, _nullable in _COLUMNS:
        op.execute(sa.text(f"DELETE FROM {table} WHERE length({column}) > 64"))
    for table, column, nullable in _COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch:
            batch.alter_column(column, existing_type=sa.String(160),
                               type_=sa.String(64), existing_nullable=nullable)
