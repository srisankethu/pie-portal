"""What was on each quote the ERP raised, line by line.

``erp_quotes`` has been header grain since it landed, deliberately: the line
breakdown costs one API call per quote, and the questions the table was built
for — how many did we quote, how did each end, what was the denominator of the
win rate — are answered entirely by the list row.

What changed is the question. A person looking at a won quote for ₹2,25,171
wants to know *what was on it*, and no amount of header carries that. So the
detail call is bought, and ``skip`` keeps a resumed pull at one list call
rather than hundreds — the arrangement ``list_vendor_payments`` has used since
the payables side needed the same thing.

**The policies ship in this revision rather than in a later ``*rls`` one**, per
the rule ``l1grp`` states and ``p3diag`` followed: a table created today has no
window in which it is uncovered. A cross-tenant read here is a competitor's
customers, the parts they buy and the prices they were offered, at line grain —
strictly worse than the header next door, which is already policied.

One table, nothing backfilled, no column changed elsewhere. A database that has
run this serves the previous code unchanged: the lines simply stay empty until a
sync fills them.

Revision ID: q4qline
Revises: p3diag
Create Date: 2026-09-17
"""
import sqlalchemy as sa
from alembic import op

revision = "q4qline"
down_revision = "p3diag"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` (§4): this
#: runs against schemas from months ago, and each revision stands on its own.
GUC = "app.current_org"
POLICY = "tenant_isolation"
TABLE = "erp_quote_lines"


def upgrade() -> None:
    # Columns written out literally rather than imported from
    # ``app.domain.models`` (§4): the models describe today.
    op.create_table(
        TABLE,
        sa.Column("erp_quote_line_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("connector", sa.String(32), nullable=True),
        sa.Column("connection_id", sa.String(64), nullable=True),
        sa.Column("external_ref", sa.String(160), nullable=False),
        sa.Column("quote_ref", sa.String(128), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_id", sa.String(64), nullable=True),
        sa.Column("item_code", sa.String(255), nullable=False, server_default=""),
        sa.Column("description", sa.String(2048), nullable=False, server_default=""),
        sa.Column("qty", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False, server_default=""),
        sa.Column("rate", sa.Numeric(18, 4), nullable=True),
        sa.Column("amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("discount_percent", sa.Numeric(9, 4), nullable=True),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "connector", "connection_id",
                            "external_ref", name="uq_erp_quote_line_ref"),
    )
    op.create_index("ix_erp_quote_lines_organization_id", TABLE, ["organization_id"])
    op.create_index("ix_erp_quote_lines_connector", TABLE, ["connector"])
    op.create_index("ix_erp_quote_lines_connection_id", TABLE, ["connection_id"])
    op.create_index("ix_erp_quote_lines_external_ref", TABLE, ["external_ref"])
    op.create_index("ix_erp_quote_lines_quote_ref", TABLE, ["quote_ref"])
    op.create_index("ix_erp_quote_lines_product_id", TABLE, ["product_id"])
    op.create_index("ix_erp_quote_lines_org_quote", TABLE,
                    ["organization_id", "quote_ref"])
    op.create_index("ix_erp_quote_lines_org_product", TABLE,
                    ["organization_id", "product_id"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no policies. A no-op is the honest form of that; see
        # ``d1rls`` for what it costs and why it is stated rather than emulated.
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY} ON {TABLE} "
        f"USING (organization_id = current_setting('{GUC}', true)) "
        f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
        op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_erp_quote_lines_org_product", table_name=TABLE)
    op.drop_index("ix_erp_quote_lines_org_quote", table_name=TABLE)
    op.drop_index("ix_erp_quote_lines_product_id", table_name=TABLE)
    op.drop_index("ix_erp_quote_lines_quote_ref", table_name=TABLE)
    op.drop_index("ix_erp_quote_lines_external_ref", table_name=TABLE)
    op.drop_index("ix_erp_quote_lines_connection_id", table_name=TABLE)
    op.drop_index("ix_erp_quote_lines_connector", table_name=TABLE)
    op.drop_index("ix_erp_quote_lines_organization_id", table_name=TABLE)
    op.drop_table(TABLE)
