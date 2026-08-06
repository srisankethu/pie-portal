"""What a bill still owes: the payable header of the cost lines.

Bills have been read since the first sync, but only for what the stock cost —
each was normalised into ``cost_records`` at line grain and the header thrown
away. So the platform knew what it had paid per insert and nothing about what
it still owed, and accounts payable and working capital had no source at all.

One new table, header grain. Deliberately not columns on ``cost_records``:
``due_date`` and ``balance`` are facts about one document, and copying them
onto forty lines would make "what is outstanding" a de-duplication problem
rather than a sum.

Purely additive. Existing rows are unaffected; the table fills on the next
pull that reads a bill. A *resumed* pull skips documents it already holds, so
these terms arrive for older bills only on a full re-sync — which is honest,
and stated on the Data screen rather than backfilled from nothing.

Revision ID: c7e2b41a8f36
Revises: b6c3f80a2d51
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e2b41a8f36"
down_revision = "b6c3f80a2d51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bills",
        sa.Column("bill_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("number", sa.String(length=128), nullable=True),
        sa.Column("vendor_id", sa.String(length=64), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("balance", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.vendor_id"]),
        sa.PrimaryKeyConstraint("bill_id"),
        sa.UniqueConstraint("organization_id", "external_ref", name="uq_bill_org_ref"),
    )
    with op.batch_alter_table("bills", schema=None) as batch_op:
        batch_op.create_index("ix_bill_org_due", ["organization_id", "due_date"],
                              unique=False)
        batch_op.create_index(batch_op.f("ix_bills_date"), ["date"], unique=False)
        batch_op.create_index(batch_op.f("ix_bills_external_ref"),
                              ["external_ref"], unique=False)
        batch_op.create_index(batch_op.f("ix_bills_organization_id"),
                              ["organization_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_bills_vendor_id"),
                              ["vendor_id"], unique=False)


def downgrade() -> None:
    op.drop_table("bills")
