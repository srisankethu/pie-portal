"""products.brand — the item master's manufacturer

Revision ID: d4e83b21c7f6
Revises: c9a1e4d76b30
Create Date: 2026-08-08

Whose brand an item is, carried straight from the Zoho item master's
``manufacturer`` field.

The column exists because the bill-derived vendor has a horizon and the brand
does not. ``cost_records.vendor_id`` only knows about purchases inside the sync
window, so an item sold today out of stock bought years ago is attributable to
nobody — while the item master has known whose product it is the whole time.
The two gaps do not overlap, which is the entire reason to keep both.

Backfill is deliberately absent. There is nothing in this database to derive a
brand *from*; it arrives on the next item sync and is null until then. Every
screen that reads it reports its own coverage, so an un-synced book shows a
smaller attributed share rather than a wrong one.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e83b21c7f6"
down_revision = "c9a1e4d76b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("brand", sa.String(length=128),
                                        nullable=True))


def downgrade() -> None:
    op.drop_column("products", "brand")
