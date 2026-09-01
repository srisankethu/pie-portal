"""The public site's enquiry form lands somewhere.

The landing page stated a price in two currencies and answered a question it
had not asked the reader anything about. It now describes what each plan is and
ends in a form, and this is the table that form writes to.

**No ``organization_id``, on purpose.** Everything else in this schema belongs
to a tenant because everything else is written by one. Whoever fills in this
form has not signed up — there is no organization to scope the row to and no
principal on the request — which makes ``contact_requests`` the third table
here without a tenant column, alongside ``zoho_credentials`` and
``process_leases``. It therefore takes no row-level policy: a policy keyed on a
column that does not exist is not a control, and
``test_the_two_tables_without_a_tenant_column_are_still_the_same_two`` names all
three so a *fourth* still has to be somebody's decision.

Nothing is backfilled and nothing reads this to decide anything. A row records
that somebody asked; ``entitlements.set_plan`` still grants.

Revision ID: i1contact
Revises: h2rls
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = "i1contact"
down_revision = "h2rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contact_requests",
        sa.Column("contact_request_id", sa.String(64), primary_key=True),
        sa.Column("company", sa.String(255), nullable=False, server_default=""),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("plan", sa.String(32), nullable=False, server_default=""),
        sa.Column("erp", sa.String(64), nullable=False, server_default=""),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="NEW"),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handled_by", sa.String(64), nullable=True),
    )
    # The two questions an operator asks of this table: what is still waiting,
    # and in what order it arrived. One index each rather than a composite —
    # the queue is small by nature, and a composite would imply a query shape
    # nothing here has.
    op.create_index("ix_contact_requests_status", "contact_requests", ["status"])
    op.create_index("ix_contact_requests_created_at", "contact_requests",
                    ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_contact_requests_created_at", table_name="contact_requests")
    op.drop_index("ix_contact_requests_status", table_name="contact_requests")
    op.drop_table("contact_requests")
