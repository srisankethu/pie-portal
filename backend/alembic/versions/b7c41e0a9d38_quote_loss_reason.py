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

Revision ID: b7c41e0a9d38
Revises: 0e8d9299b0c7
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c41e0a9d38'
down_revision = '0e8d9299b0c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('quote_outcomes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('loss_reason', sa.String(24), nullable=True))
        batch_op.add_column(sa.Column('lost_to', sa.String(255), nullable=True))
        batch_op.create_index('ix_quote_outcomes_loss_reason', ['loss_reason'])


def downgrade() -> None:
    with op.batch_alter_table('quote_outcomes', schema=None) as batch_op:
        batch_op.drop_index('ix_quote_outcomes_loss_reason')
        batch_op.drop_column('lost_to')
        batch_op.drop_column('loss_reason')
