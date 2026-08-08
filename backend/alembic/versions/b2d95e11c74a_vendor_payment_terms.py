"""vendor_payment_terms — the term actually agreed, beside the one Zoho holds

Revision ID: b2d95e11c74a
Revises: a1c8f42e6b03
Create Date: 2026-08-08

Zoho's payment terms are a fixed dropdown, so a real agreement of "net 37" or
"45 days from month end" is filed under the nearest entry on that list. Every
due date derived from it is then wrong by days, in a direction nobody chose,
and the cash projection places money on exactly those dates.

A table rather than a column on ``vendors``, and the distinction is the point:
``upsert_vendor`` rewrites ``payment_terms_days`` from the payload on every
sync, so a negotiated term stored there would survive until the next pull and
no longer. This is typed, it is the only copy, and it must survive a complete
re-sync — the same reason ``item_category_overrides`` and ``vendor_targets``
are their own tables.

Unique on (organization, vendor): a term gets renegotiated, so the write path
is an upsert. Two rows for one supplier would make "the term" a question about
which row won, and a schedule cannot be drawn from an ambiguous answer.

``basis`` is ``NET`` or ``END_OF_MONTH``, validated in
``commercial/insight/terms.py``. Stored as a string rather than a database enum
so adding a third basis is a code change and not a migration on a table whose
values are read by name.

Columns are written out literally rather than imported from the models, per the
rule that a migration runs against schemas from months ago while models describe
today. Nothing is backfilled and nothing needs to be: no term has been agreed in
this table yet, and the absence of a row means "use what Zoho says", which is
exactly the behaviour before this revision.
"""
from alembic import op
import sqlalchemy as sa

revision = "b2d95e11c74a"
down_revision = "a1c8f42e6b03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_payment_terms",
        sa.Column("vendor_payment_term_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("vendor_id", sa.String(length=64),
                  sa.ForeignKey("vendors.vendor_id"), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("basis", sa.String(length=16), nullable=False),
        sa.Column("set_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "vendor_id",
                            name="uq_vendor_payment_term_vendor"),
    )
    op.create_index("ix_vendor_payment_terms_organization_id",
                    "vendor_payment_terms", ["organization_id"])
    op.create_index("ix_vendor_payment_terms_vendor_id",
                    "vendor_payment_terms", ["vendor_id"])


def downgrade() -> None:
    op.drop_index("ix_vendor_payment_terms_vendor_id",
                  table_name="vendor_payment_terms")
    op.drop_index("ix_vendor_payment_terms_organization_id",
                  table_name="vendor_payment_terms")
    op.drop_table("vendor_payment_terms")
