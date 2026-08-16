"""Drop ``organizations.erp`` — written by the seed, read by nothing.

The column was stamped "zoho" on every organization ever created and never
read back by any code path (verified by grep across app/, tests/ and
scripts/). Today it is dead weight; the day a second connector lands it is a
contradictory discriminator — connector identity lives on the connection
records in ``ingestion/``, not on the tenant. The positioning review's
guidance was to delete it before that day arrives.

Downgrade restores the column exactly as revision 41730a334a54 created it
(String(32), NOT NULL), with a server default of 'zoho' so the re-add works
on a populated SQLite database — which is also the only value any row ever
held.

Revision ID: a7c3e91b02d8
Revises: t5jurisdiction
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c3e91b02d8"
down_revision = "t5jurisdiction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.drop_column("erp")


def downgrade() -> None:
    with op.batch_alter_table("organizations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("erp", sa.String(length=32),
                                      nullable=False, server_default="zoho"))
