"""products.brand → products.manufacturer

Revision ID: e5c07f4a9218
Revises: d4e83b21c7f6
Create Date: 2026-08-08

A rename, not a new column. ``d4e83b21c7f6`` called this ``brand``, which was
wrong in a way that mattered: Zoho items carry *both* a ``brand`` field and a
``manufacturer`` field, these books fill only the second, and the column was
always populated from ``manufacturer``. A name that points at the wrong source
field invites the question of whether brand and manufacturer are two rungs of
the attribution fallback — they are not, and the column now says so.

Reconciled forward rather than by editing ``d4e83b21c7f6``, which is released.
Two databases that both ran that revision must have the same schema, and an
edited migration makes that untrue with nothing to detect it.

**No data is at risk.** The column has never held anything: it is populated by
the item sync, and no sync has run since it was added. The rename is written
out anyway rather than being a drop-and-recreate, so that a book which *has*
synced in the interval keeps what it pulled.

``batch_alter_table`` because SQLite could not rename a column at all before
3.25 and Alembic's batch mode is what makes the operation portable across the
dialects this schema runs on.
"""
from alembic import op
import sqlalchemy as sa

revision = "e5c07f4a9218"
down_revision = "d4e83b21c7f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.alter_column("brand", new_column_name="manufacturer",
                           existing_type=sa.String(length=128),
                           existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.alter_column("manufacturer", new_column_name="brand",
                           existing_type=sa.String(length=128),
                           existing_nullable=True)
