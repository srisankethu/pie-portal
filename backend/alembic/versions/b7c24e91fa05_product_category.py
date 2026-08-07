"""items carry a line of the business, and a person can correct it

The growth question this platform could not answer was "who takes one line and
not the others" — a cutting-tool customer with no coolant, a machine buyer with
no metrology. Answering it needs every item placed in a line, and nothing in the
read model placed one.

Two additions, and they are deliberately different kinds of thing:

``products.category`` is what the catalogue itself says, synced from Zoho and
stored **raw**. It is derived data: a full re-sync rebuilds it, and that is
correct. It is not normalised on the way in, because the mapping from those
words to a line is versioned policy — a value rewritten at sync time could never
be re-read under a corrected map without another full sync.

``item_category_overrides`` is what a person said, and it is the opposite: the
only copy. It lives in its own table precisely so a re-sync cannot destroy it,
which putting it on ``products`` would guarantee (§4 — products are derived,
Zoho is the system of record).

No backfill. ``category`` is null until the next sync fetches it, and the
resolution order in ``commercial/categories.py`` falls through to the HSN map,
which reads a column that is already populated. So the mix views work on the
existing data and get better after a sync rather than being blocked on one.

Revision ID: b7c24e91fa05
Revises: a3f61d80b492
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op

revision = 'b7c24e91fa05'
down_revision = 'a3f61d80b492'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.add_column(sa.Column('category', sa.String(128), nullable=True))

    op.create_table(
        'item_category_overrides',
        sa.Column('override_id', sa.String(64), primary_key=True),
        sa.Column('organization_id', sa.String(64), nullable=False),
        sa.Column('product_id', sa.String(64), nullable=False),
        sa.Column('category', sa.String(48), nullable=False),
        sa.Column('set_by_user_id', sa.String(64), nullable=True),
        sa.Column('note', sa.String(512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['products.product_id']),
        sa.UniqueConstraint('organization_id', 'product_id',
                            name='uq_item_category_override'),
    )
    op.create_index('ix_item_category_overrides_organization_id',
                    'item_category_overrides', ['organization_id'])
    op.create_index('ix_item_category_overrides_product_id',
                    'item_category_overrides', ['product_id'])


def downgrade() -> None:
    op.drop_index('ix_item_category_overrides_product_id',
                  table_name='item_category_overrides')
    op.drop_index('ix_item_category_overrides_organization_id',
                  table_name='item_category_overrides')
    op.drop_table('item_category_overrides')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_column('category')
