"""One quote line's diagnosis, and the human saying it was wrong. Both append-only.

Two tables rather than one, for the reason ``quote_decisions`` and
``quote_outcomes`` are two: a diagnosis is a judgement made against particular
evidence on a particular day and must never change afterwards, while a dismissal
is something a person does later. Writing the second onto the first would make
the first editable, and an editable judgement proves nothing about what anybody
knew when the price went out.

``evidence_hash`` is indexed because ``rediagnose`` looks a diagnosis up by line
and then compares; the index is for the reporting query that asks how many
diagnoses on a quote share an evidence set.

No foreign keys to ``quote_drafts``. A diagnosis outlives the draft it was taken
from — a draft is archived, a quote is sent and becomes an ERP document — and a
cascade that removed the judgement when the workspace row went would delete
exactly the history this table exists to hold.

**The policies ship in this revision rather than in a later ``*rls`` one**, which
is the standing rule ``l1grp`` states: a table created today has no window in
which it is uncovered, and a revision interrupted halfway must not leave one. The
leak these two would be is worse than the one ``quote_decisions`` next door is
policied against. A diagnosis row carries the price band a competitor's customer
has been trading at, the ids of the invoices it was read off, and the expected
cost of the item — so a cross-tenant read is that book's buy side and its
customer's negotiating position in one query. A dismissal names the person who
read the card and what they said was wrong with it.

Revision ID: p3diag
Revises: n2recorded
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "p3diag"
down_revision = "n2recorded"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` (§4): this
#: runs against schemas from months ago, and each revision stands on its own.
GUC = "app.current_org"
POLICY = "tenant_isolation"
TABLES = ("quote_diagnoses", "quote_diagnosis_dismissals")


def upgrade() -> None:
    op.create_table(
        "quote_diagnoses",
        sa.Column("quote_diagnosis_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("quote_id", sa.String(64), nullable=False),
        sa.Column("quote_line_id", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.String(64), nullable=True),
        sa.Column("product_id", sa.String(64), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("quantity_band", sa.String(24), nullable=False,
                  server_default=""),
        sa.Column("quoted_unit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("knowable_by", sa.DateTime(timezone=True), nullable=False),
        sa.Column("codes", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("strength", sa.String(16), nullable=False,
                  server_default="INSUFFICIENT"),
        sa.Column("surfaces", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("excluded", sa.JSON(), nullable=False),
        sa.Column("evidence_summary", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(80), nullable=False,
                  server_default=""),
        sa.Column("price_band", sa.JSON(), nullable=False),
        sa.Column("cost_baseline", sa.JSON(), nullable=False),
        sa.Column("peer_band", sa.JSON(), nullable=False),
        sa.Column("opportunity", sa.JSON(), nullable=False),
        sa.Column("thresholds_version", sa.String(32), nullable=False,
                  server_default=""),
        sa.Column("engine_version", sa.String(32), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quote_diagnoses_organization_id", "quote_diagnoses",
                    ["organization_id"])
    op.create_index("ix_quote_diagnoses_quote_id", "quote_diagnoses", ["quote_id"])
    op.create_index("ix_quote_diagnoses_customer_id", "quote_diagnoses",
                    ["customer_id"])
    op.create_index("ix_quote_diagnoses_product_id", "quote_diagnoses",
                    ["product_id"])
    op.create_index("ix_quote_diagnoses_evidence_hash", "quote_diagnoses",
                    ["evidence_hash"])
    op.create_index("ix_quote_diagnoses_created_at", "quote_diagnoses",
                    ["created_at"])
    op.create_index("ix_quote_diagnoses_org_quote", "quote_diagnoses",
                    ["organization_id", "quote_id"])
    op.create_index("ix_quote_diagnoses_org_line", "quote_diagnoses",
                    ["organization_id", "quote_line_id"])
    op.create_index("ix_quote_diagnoses_org_customer_product", "quote_diagnoses",
                    ["organization_id", "customer_id", "product_id"])

    op.create_table(
        "quote_diagnosis_dismissals",
        sa.Column("dismissal_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("quote_diagnosis_id", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("note", sa.String(1024), nullable=True),
        sa.Column("dismissed_by_user_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quote_diagnosis_dismissals_organization_id",
                    "quote_diagnosis_dismissals", ["organization_id"])
    op.create_index("ix_quote_diagnosis_dismissals_quote_diagnosis_id",
                    "quote_diagnosis_dismissals", ["quote_diagnosis_id"])
    op.create_index("ix_quote_diagnosis_dismissals_dismissed_by_user_id",
                    "quote_diagnosis_dismissals", ["dismissed_by_user_id"])
    op.create_index("ix_quote_diagnosis_dismissals_created_at",
                    "quote_diagnosis_dismissals", ["created_at"])
    op.create_index("ix_quote_diagnosis_dismissals_org_diagnosis",
                    "quote_diagnosis_dismissals",
                    ["organization_id", "quote_diagnosis_id"])
    op.create_index("ix_quote_diagnosis_dismissals_org_reason",
                    "quote_diagnosis_dismissals",
                    ["organization_id", "reason_code"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no policies. A no-op is the honest form of that; see
        # ``d1rls`` for what it costs and why it is stated rather than emulated.
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} "
            f"USING (organization_id = current_setting('{GUC}', true)) "
            f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("quote_diagnosis_dismissals")
    op.drop_table("quote_diagnoses")
