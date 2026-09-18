"""Say which version of the ingestion contract each sync run wrote under.

One additive, nullable column on ``sync_runs``. ``spec_version`` holds the
``spec_…`` stamp of the canonical ingestion spec — the JSON Schema of the
ingestion DTOs, content-hashed — as it stood when the run was queued. It is the
counterpart on an ingestion run of the ``thresholds_version`` a computed row
carries: not which policy judged a number, but which contract the rows landed
against.

**Nullable, no server default, nothing backfilled**, and that is the whole of
the design decision. Every row already in this table was written before any
spec existed. Stamping them with today's version — the only value a backfill
could use — would say that a contract judged work it never saw, which is
exactly the failure the sibling column ``source_recorded_at`` was added to
avoid: a value indistinguishable from evidence, standing where there is none.
NULL reads as what it is, a run from before the contract, and it is finite:
every future run stamps itself.

Additive and reversible. A database that has run this serves the previous code
unchanged — the column is simply never written — and a database that has *not*
run it is reported by ``schema_check.missing_columns`` and refused a sync with
the 503 that names the gap, rather than failing later on an INSERT.

Revision ID: r5spec
Revises: q4qline
Create Date: 2026-09-18
"""
import sqlalchemy as sa
from alembic import op

revision = "r5spec"
down_revision = "q4qline"
branch_labels = None
depends_on = None

TABLE = "sync_runs"
COLUMN = "spec_version"


def upgrade() -> None:
    # Written out literally rather than imported from ``app.domain.models``
    # (§4): this runs against schemas from months ago, and the models describe
    # today. String(32) for a 15-character stamp, matching the width every
    # other version stamp in this schema is declared at.
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN)
