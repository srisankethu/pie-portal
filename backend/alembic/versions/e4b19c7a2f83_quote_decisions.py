"""quote decisions and quote outcomes

Two tables that together make a quoting decision auditable after the fact:

1. ``quote_decisions`` — an immutable, append-only snapshot of one priced quote
   line: the quantity band, the cost basis used, the references it was compared
   against, the exceptions that fired, and any reason a salesperson gave for
   going ahead anyway. Nothing in it is ever updated; re-pricing a line inserts
   a new row. A row that could be edited afterwards would prove nothing about
   the judgement actually made.

2. ``quote_outcomes`` — one row per quote, carrying DRAFT → SENT → WON/LOST.
   Separate precisely so ``quote_decisions`` can stay immutable: the outcome is
   learned later and must change, the priced facts were true at the time and
   must not.

Revision ID: e4b19c7a2f83
Revises: b8e42d17c096
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision = 'e4b19c7a2f83'
down_revision = 'b8e42d17c096'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_decisions",
        sa.Column("quote_decision_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("quote_id", sa.String(64), nullable=False),
        sa.Column("quote_line_id", sa.String(64), nullable=False),

        sa.Column("customer_id", sa.String(64)),
        sa.Column("product_id", sa.String(64)),
        sa.Column("customer_ref", sa.String(255), server_default=""),
        sa.Column("product_ref", sa.String(255), server_default=""),

        sa.Column("quantity", sa.Numeric(18, 4)),
        sa.Column("quantity_band", sa.String(24), server_default=""),
        sa.Column("quoted_unit_price", sa.Numeric(18, 4)),

        sa.Column("unit_cost", sa.Numeric(18, 4)),
        sa.Column("line_revenue", sa.Numeric(18, 4)),
        sa.Column("cogs", sa.Numeric(18, 4)),
        sa.Column("gross_profit", sa.Numeric(18, 4)),
        sa.Column("margin", sa.Float()),

        sa.Column("references", sa.JSON()),
        sa.Column("exceptions", sa.JSON()),
        sa.Column("relationship_metrics", sa.JSON()),
        sa.Column("evidence_refs", sa.JSON()),

        sa.Column("data_sufficiency", sa.String(16), server_default="INSUFFICIENT"),
        sa.Column("sufficiency_reasons", sa.JSON()),

        sa.Column("requires_approval", sa.Boolean(), server_default=sa.false()),
        sa.Column("overridden", sa.Boolean(), server_default=sa.false()),
        sa.Column("override_reason_code", sa.String(48)),
        sa.Column("override_reason", sa.String(1024)),
        sa.Column("overridden_exception_codes", sa.JSON()),

        sa.Column("thresholds_version", sa.String(32), server_default=""),
        sa.Column("engine_version", sa.String(32), server_default=""),
        sa.Column("as_of", sa.Date()),
        sa.Column("created_by_user_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_quote_decisions_organization_id", "quote_decisions",
                    ["organization_id"])
    op.create_index("ix_quote_decisions_quote_id", "quote_decisions", ["quote_id"])
    op.create_index("ix_quote_decisions_customer_id", "quote_decisions", ["customer_id"])
    op.create_index("ix_quote_decisions_product_id", "quote_decisions", ["product_id"])
    op.create_index("ix_quote_decisions_created_by_user_id", "quote_decisions",
                    ["created_by_user_id"])
    op.create_index("ix_quote_decisions_created_at", "quote_decisions", ["created_at"])
    op.create_index("ix_quote_decisions_org_quote", "quote_decisions",
                    ["organization_id", "quote_id"])
    op.create_index("ix_quote_decisions_org_customer_product", "quote_decisions",
                    ["organization_id", "customer_id", "product_id"])

    op.create_table(
        "quote_outcomes",
        sa.Column("quote_outcome_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("quote_id", sa.String(64), nullable=False),
        sa.Column("customer_ref", sa.String(255), server_default=""),
        sa.Column("customer_id", sa.String(64)),
        sa.Column("status", sa.String(16), server_default="DRAFT"),
        sa.Column("note", sa.String(1024)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by_user_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "quote_id",
                            name="uq_quote_outcome_org_quote"),
    )
    op.create_index("ix_quote_outcomes_organization_id", "quote_outcomes",
                    ["organization_id"])
    op.create_index("ix_quote_outcomes_quote_id", "quote_outcomes", ["quote_id"])
    op.create_index("ix_quote_outcomes_customer_id", "quote_outcomes", ["customer_id"])
    op.create_index("ix_quote_outcomes_status", "quote_outcomes", ["status"])


def downgrade() -> None:
    op.drop_table("quote_outcomes")
    op.drop_table("quote_decisions")
