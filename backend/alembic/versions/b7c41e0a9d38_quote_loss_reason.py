"""quote loss reason

A ``LOST`` quote outcome said nothing about *why*, and the distinction that
matters commercially is whether the money went to another supplier or the
requirement died. Those look identical in a bare LOST row and mean opposite
things about what the customer spends elsewhere, so anything reasoning about
that has to be able to separate them.

Two nullable columns on ``quote_outcomes``:

- ``loss_reason``  a ``QuoteLossReason`` — which of the two it was, and the
                   route if a competitor took it (price, delivery, approval)
- ``lost_to``      who won it, where known. Free text: a competitor is not an
                   entity this platform holds

Both nullable, and rows written before this are deliberately **not** backfilled
to ``UNKNOWN``. A backfilled UNKNOWN is indistinguishable from somebody having
answered "unknown", and the two must stay distinguishable — NULL reads as "not
recorded", which is the true statement about a row nobody was ever asked about.
There is nothing here to compute a correction from either.

New losses cannot be NULL. That is enforced in ``commercial/quote_service``,
not by a NOT NULL constraint, because the constraint would also reject the
historical rows this migration is careful to leave alone.

``loss_reason`` is indexed: every consumer of this column filters on it before
aggregating, which is the whole reason it is a column rather than a note.

**Overlaps `claude/quote-win-loss` (PR #42), deliberately and compatibly.** That
branch adds the same column, with the same name, the same index name and the same
`String(32)` width, under revision `c7e41b90d3aa`. The vocabulary in
`QuoteLossReason` is that branch's, adopted verbatim here rather than competing
with it — it carries ~1,100 lines of analysis and UI keyed to those exact values,
and a second set of names would have made whichever landed second a rename across
all of it. Whichever of the two merges second should **delete its own migration
file** and keep everything else: the resulting schema is identical either way.
Do not attempt to run both — the second will die on a duplicate column, which is
the loud failure and the one worth having.

Revision ID: b7c41e0a9d38
Revises: d55f6ff18460
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c41e0a9d38'
down_revision = 'd55f6ff18460'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('quote_outcomes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('loss_reason', sa.String(32), nullable=True))
        batch_op.add_column(sa.Column('lost_to', sa.String(255), nullable=True))
        batch_op.create_index('ix_quote_outcomes_loss_reason', ['loss_reason'])


def downgrade() -> None:
    with op.batch_alter_table('quote_outcomes', schema=None) as batch_op:
        batch_op.drop_index('ix_quote_outcomes_loss_reason')
        batch_op.drop_column('lost_to')
        batch_op.drop_column('loss_reason')
