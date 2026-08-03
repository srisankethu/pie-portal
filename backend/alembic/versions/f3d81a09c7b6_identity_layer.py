"""identity layer: link records across connectors, never merge them

The platform reads from several ERPs at once. The same customer exists in Zoho
and in ERPNext under different ids; the same item under different codes. Until
now the read model keyed customers on ``(organization_id, external_id)``, which
quietly assumes one id space — true of one Zoho company, false the moment a
second connector arrives with its own ``CUST-102``.

These tables add a linking layer rather than a de-duplicating one. Connector
records are stored exactly as they arrive and are never rewritten from another
connector's values; an identity is a thin node that says "these are the same
business entity". Merging would destroy the property an ERP integration exists
to preserve — being able to point at a figure and say which system it came from.

Nothing existing is altered. ``customers`` and ``products`` keep their shape and
keep working; the identity layer sits alongside and is populated by the sync.

Revision ID: f3d81a09c7b6
Revises: e91b4c7d2a58
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op


revision = 'f3d81a09c7b6'
down_revision = 'e91b4c7d2a58'
branch_labels = None
depends_on = None


def _identity(name: str) -> None:
    op.create_table(
        name,
        sa.Column("identity_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        # The only field on an identity that is a person's rather than a
        # connector's. Everything else about the entity lives on the records.
        sa.Column("label", sa.String(255)),
        sa.Column("active", sa.Boolean(), server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(f"ix_{name}_organization_id", name, ["organization_id"])
    op.create_index(f"ix_{name}_active", name, ["active"])


def upgrade() -> None:
    _identity("customer_identities")
    _identity("item_identities")

    op.create_table(
        "customer_connector_records",
        sa.Column("record_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("identity_id", sa.String(64), nullable=False),
        sa.Column("connector", sa.String(32), nullable=False),
        sa.Column("connection_id", sa.String(64)),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), server_default=""),
        sa.Column("gstin", sa.String(20)),
        sa.Column("customer_id", sa.String(64)),
        sa.Column("source_ref", sa.JSON()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        # Keyed on the source triple, not on the external id alone: two
        # connectors can each issue "12345", and two Zoho companies under one
        # organization each have their own id space.
        sa.UniqueConstraint("organization_id", "connector", "connection_id",
                            "external_id", name="uq_customer_record_source"),
    )
    op.create_index("ix_customer_record_gstin", "customer_connector_records",
                    ["organization_id", "gstin"])
    for col in ("organization_id", "identity_id", "connector", "connection_id",
                "external_id", "customer_id"):
        op.create_index(f"ix_ccr_{col}", "customer_connector_records", [col])

    op.create_table(
        "item_connector_records",
        sa.Column("record_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("identity_id", sa.String(64), nullable=False),
        sa.Column("connector", sa.String(32), nullable=False),
        sa.Column("connection_id", sa.String(64)),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("sku", sa.String(128)),
        sa.Column("description", sa.String(512), server_default=""),
        sa.Column("product_id", sa.String(64)),
        sa.Column("source_ref", sa.JSON()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "connector", "connection_id",
                            "external_id", name="uq_item_record_source"),
    )
    op.create_index("ix_item_record_sku", "item_connector_records",
                    ["organization_id", "sku"])
    for col in ("organization_id", "identity_id", "connector", "connection_id",
                "external_id", "product_id"):
        op.create_index(f"ix_icr_{col}", "item_connector_records", [col])

    op.create_table(
        "identity_suggestions",
        sa.Column("suggestion_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("record_id", sa.String(64), nullable=False),
        sa.Column("target_identity_id", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("evidence", sa.String(255), server_default=""),
        sa.Column("status", sa.String(16), server_default="PENDING"),
        sa.Column("decided_by_user_id", sa.String(64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("record_id", "target_identity_id",
                            name="uq_suggestion_record_target"),
    )
    op.create_index("ix_suggestion_open", "identity_suggestions",
                    ["organization_id", "entity_type", "status"])
    for col in ("organization_id", "record_id", "target_identity_id", "status"):
        op.create_index(f"ix_sugg_{col}", "identity_suggestions", [col])

    # Append-only. An audit trail that can be edited cannot settle an argument.
    op.create_table(
        "identity_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("identity_id", sa.String(64), nullable=False),
        sa.Column("record_id", sa.String(64)),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64), server_default="SYSTEM"),
        sa.Column("detail", sa.String(512), server_default=""),
        sa.Column("at", sa.DateTime(timezone=True)),
    )
    for col in ("organization_id", "entity_type", "identity_id", "record_id",
                "action", "at"):
        op.create_index(f"ix_idev_{col}", "identity_events", [col])

    op.create_table(
        "identity_policies",
        sa.Column("organization_id", sa.String(64), primary_key=True),
        # Off by default, deliberately. An exact GSTIN match is strong evidence,
        # not proof, and an unreviewed suggestion costs a click while an
        # unreviewed merge costs the evidence needed to spot it.
        sa.Column("auto_link_customers", sa.Boolean(), server_default=sa.false()),
        sa.Column("auto_link_items", sa.Boolean(), server_default=sa.false()),
        sa.Column("updated_by_user_id", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for t in ("identity_policies", "identity_events", "identity_suggestions",
              "item_connector_records", "customer_connector_records",
              "item_identities", "customer_identities"):
        op.drop_table(t)
