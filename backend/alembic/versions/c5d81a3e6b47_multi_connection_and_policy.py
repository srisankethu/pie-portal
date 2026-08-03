"""many Zoho connections per organization, and an editable margin policy

Two changes an owner asked for, both removing a restriction that was mine
rather than the domain's.

1. ``zoho_connections`` was keyed on ``organization_id`` — one Zoho company per
   platform tenant. A business with three legal entities therefore needed three
   tenants to see three sets of books, and no screen could show them together.
   The table is re-keyed on a ``connection_id`` and gains a label, an enabled
   flag, and per-connection health, so an owner adds as many companies as they
   have and picks which to pull from.

   Consequence, stated rather than buried: rows from every connection on an
   organization land in that organization's read model and are analysed
   together. Entities kept apart need separate organizations.

2. ``commercial_policies`` — per-organization overrides to the margin policy.
   It was read-only on the grounds that an editable threshold cannot be
   reproduced against. That objection is answered by the version hash, not by
   refusing to edit: ``CommercialThresholds.version`` is a content hash of every
   field, and every metric row, signal and quote snapshot already records the
   version that produced it.

Existing connection rows keep their credentials, their company id and their
history; they are simply given a generated connection_id.

Revision ID: c5d81a3e6b47
Revises: a3c8e57b91f4
Create Date: 2026-07-29
"""
import uuid

import sqlalchemy as sa
from alembic import op


revision = 'c5d81a3e6b47'
down_revision = 'a3c8e57b91f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    existing = conn.execute(sa.text(
        "SELECT organization_id, zoho_organization_id, credential_id, client_id, "
        "       client_secret_encrypted, refresh_token_encrypted, accounts_base, "
        "       api_base, created_at, updated_at FROM zoho_connections"
    )).fetchall()

    op.drop_table("zoho_connections")
    op.create_table(
        "zoho_connections",
        sa.Column("connection_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), server_default=""),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true()),
        sa.Column("zoho_organization_id", sa.String(64), nullable=False),
        sa.Column("credential_id", sa.String(64)),
        sa.Column("client_id", sa.String(255)),
        sa.Column("client_secret_encrypted", sa.String(2048)),
        sa.Column("refresh_token_encrypted", sa.String(2048)),
        sa.Column("accounts_base", sa.String(255),
                  server_default="https://accounts.zoho.in"),
        sa.Column("api_base", sa.String(255),
                  server_default="https://www.zohoapis.in/books/v3"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_check_ok", sa.Boolean()),
        sa.Column("last_check_detail", sa.String(1024)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "zoho_organization_id",
                            name="uq_zoho_connection_org_company"),
    )
    op.create_index("ix_zoho_connections_organization_id", "zoho_connections",
                    ["organization_id"])
    op.create_index("ix_zoho_connections_credential_id", "zoho_connections",
                    ["credential_id"])

    for r in existing:
        conn.execute(sa.text(
            "INSERT INTO zoho_connections (connection_id, organization_id, label, "
            "  enabled, zoho_organization_id, credential_id, client_id, "
            "  client_secret_encrypted, refresh_token_encrypted, accounts_base, "
            "  api_base, created_at, updated_at) "
            "VALUES (:cid, :org, :label, :enabled, :zoho, :cred, :client_id, "
            "        :secret, :refresh, :accounts, :api, :created, :updated)"
        ), {
            "cid": uuid.uuid4().hex,
            "org": r.organization_id,
            # Named after the company it points at, so a list of three is
            # readable before anyone renames them.
            "label": f"Zoho org {r.zoho_organization_id}",
            "enabled": True,
            "zoho": r.zoho_organization_id,
            "cred": r.credential_id,
            "client_id": r.client_id,
            "secret": r.client_secret_encrypted,
            "refresh": r.refresh_token_encrypted,
            "accounts": r.accounts_base,
            "api": r.api_base,
            "created": r.created_at,
            "updated": r.updated_at,
        })

    op.create_table(
        "commercial_policies",
        sa.Column("organization_id", sa.String(64), primary_key=True),
        sa.Column("overrides", sa.JSON()),
        sa.Column("updated_by_user_id", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    conn = op.get_bind()

    # One row per organization survives — the oldest, which is the one the
    # single-connection code would have resolved to anyway.
    rows = conn.execute(sa.text(
        "SELECT organization_id, zoho_organization_id, credential_id, client_id, "
        "       client_secret_encrypted, refresh_token_encrypted, accounts_base, "
        "       api_base, created_at, updated_at FROM zoho_connections "
        "ORDER BY created_at"
    )).fetchall()
    first: dict = {}
    for r in rows:
        first.setdefault(r.organization_id, r)

    op.drop_table("zoho_connections")
    op.create_table(
        "zoho_connections",
        sa.Column("organization_id", sa.String(64), primary_key=True),
        sa.Column("zoho_organization_id", sa.String(64), nullable=False),
        sa.Column("credential_id", sa.String(64)),
        sa.Column("client_id", sa.String(255)),
        sa.Column("client_secret_encrypted", sa.String(2048)),
        sa.Column("refresh_token_encrypted", sa.String(2048)),
        sa.Column("accounts_base", sa.String(255),
                  server_default="https://accounts.zoho.in"),
        sa.Column("api_base", sa.String(255),
                  server_default="https://www.zohoapis.in/books/v3"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    for r in first.values():
        conn.execute(sa.text(
            "INSERT INTO zoho_connections (organization_id, zoho_organization_id, "
            "  credential_id, client_id, client_secret_encrypted, "
            "  refresh_token_encrypted, accounts_base, api_base, created_at, updated_at) "
            "VALUES (:org, :zoho, :cred, :client_id, :secret, :refresh, :accounts, "
            "        :api, :created, :updated)"
        ), {
            "org": r.organization_id, "zoho": r.zoho_organization_id,
            "cred": r.credential_id, "client_id": r.client_id,
            "secret": r.client_secret_encrypted, "refresh": r.refresh_token_encrypted,
            "accounts": r.accounts_base, "api": r.api_base,
            "created": r.created_at, "updated": r.updated_at,
        })

    op.drop_table("commercial_policies")
