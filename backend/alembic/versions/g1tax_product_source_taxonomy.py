"""Products: the source's own tool class and operation.

Two columns for a taxonomy that already exists upstream and was discarded at
ingest. The ERP's own catalogue category — ``products.category``, filled from
Zoho's Inventory categories — is set on none of the 800 items on the live SLS
master, while the two item custom fields a person in that business actually
maintains are set on every item sampled. So the platform's category column was
empty because it was reading the wrong field, not because nobody had classified
anything.

Nullable, with no backfill and no default. A default would be a benign one: it
would say "Insert" or "General" about items nobody has classified, and every
consumer would have to know that a value here might be the map rather than the
territory. NULL means nobody said, and the next sync fills in what the source
knows.

Revision ID: g1tax
Revises: f1api
Create Date: 2026-08-30
"""
import sqlalchemy as sa
from alembic import op

revision = "g1tax"
down_revision = "f1api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("source_item_type", sa.String(length=128),
                                        nullable=True))
    op.add_column("products", sa.Column("source_item_category", sa.String(length=128),
                                        nullable=True))


def downgrade() -> None:
    op.drop_column("products", "source_item_category")
    op.drop_column("products", "source_item_type")
