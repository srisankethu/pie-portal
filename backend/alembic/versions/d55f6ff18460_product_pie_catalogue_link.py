"""products pie catalogue link — point an item at its decoded record

Three nullable columns and an index. Nothing is backfilled, and null is the
correct reading for every row that exists when this runs: no link has been
derived yet, which is exactly what "unlinked" means. The next sync recomputes
them, so this is derived state arriving the way the rest of it does.

Null stays the common case afterwards too. Measured against the live master only
about a tenth of items resolve to a catalogue record, so a reader who meets a
mostly-empty column and assumes a failed migration will be wrong — see
docs/concepts/01-application-engineering.md.

The index is on pie_record_id alone rather than (organization_id, pie_record_id)
because the question it answers — "which items are this catalogue record?" — is
asked across organizations by the coverage reporting, and a record_id is already
globally unique within a catalogue version.

Revision ID: d55f6ff18460
Revises: 0e8d9299b0c7
Create Date: 2026-08-09 18:51:03.318633
"""
from alembic import op
import sqlalchemy as sa


revision = 'd55f6ff18460'
down_revision = '0e8d9299b0c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('pie_record_id', sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column('pie_link_method', sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column('pie_catalog_version', sa.String(length=128), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_products_pie_record_id'), ['pie_record_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_products_pie_record_id'))
        batch_op.drop_column('pie_catalog_version')
        batch_op.drop_column('pie_link_method')
        batch_op.drop_column('pie_record_id')
