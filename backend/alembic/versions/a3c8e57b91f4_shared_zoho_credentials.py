"""separate Zoho credentials from connections, and share them

A Zoho refresh token belongs to a *user*, not to a company: ``organization_id``
is a request parameter and ``GET /organizations`` lists every company that user
can see. Storing the client id, secret and refresh token on each connection row
therefore forced the same grant to be entered — and later rotated — once per
legal entity, multiplying a one-time job by the number of companies for no
security benefit, since it was the same secret either way.

This splits the grant into ``zoho_credentials`` and leaves ``zoho_connections``
as the pairing of a platform organization with a Zoho company id.

The backfill gives every existing connection its own credential row — it does
NOT try to merge duplicates. Fernet is randomised, so two encryptions of the
same secret differ, and a migration cannot tell them apart without the
encryption key; making schema movement depend on that key being present and
correct is a worse trade than leaving the merge to a separate, reversible step.

A deployment that entered the same OAuth app once per entity therefore comes out
of this migration unchanged in behaviour, and can then collapse the duplicates
deliberately with::

    python -m app.ingestion.merge_credentials --dry-run
    python -m app.ingestion.merge_credentials

after which there is one credential, three connections, and one rotation.
New connections dedupe on the way in and never create the problem again.

Revision ID: a3c8e57b91f4
Revises: f19d3b6c4a25
Create Date: 2026-07-29
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = 'a3c8e57b91f4'
down_revision = 'f19d3b6c4a25'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zoho_credentials",
        sa.Column("credential_id", sa.String(64), primary_key=True),
        sa.Column("owner_organization_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), server_default=""),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("client_secret_encrypted", sa.String(2048), nullable=False),
        sa.Column("refresh_token_encrypted", sa.String(2048), nullable=False),
        sa.Column("accounts_base", sa.String(255),
                  server_default="https://accounts.zoho.in"),
        sa.Column("api_base", sa.String(255),
                  server_default="https://www.zohoapis.in/books/v3"),
        sa.Column("shared_with_organization_ids", sa.JSON()),
        sa.Column("rotated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_zoho_credentials_owner_organization_id", "zoho_credentials",
                    ["owner_organization_id"])

    with op.batch_alter_table("zoho_connections") as batch:
        batch.add_column(sa.Column("credential_id", sa.String(64)))
        # The inline secrets become nullable: nothing new is written to them,
        # and a connection made through a shared credential has none of its own.
        batch.alter_column("client_id", existing_type=sa.String(255), nullable=True)
        batch.alter_column("client_secret_encrypted", existing_type=sa.String(2048),
                           nullable=True)
        batch.alter_column("refresh_token_encrypted", existing_type=sa.String(2048),
                           nullable=True)
    op.create_index("ix_zoho_connections_credential_id", "zoho_connections",
                    ["credential_id"])

    _backfill()


def _backfill() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT organization_id, client_id, client_secret_encrypted, "
        "       refresh_token_encrypted, accounts_base, api_base "
        "FROM zoho_connections "
        "WHERE client_id IS NOT NULL AND client_id != ''"
    )).fetchall()
    if not rows:
        return

    now = datetime.now(timezone.utc)

    for r in rows:
        credential_id = uuid.uuid4().hex
        conn.execute(sa.text(
            "INSERT INTO zoho_credentials (credential_id, owner_organization_id, "
            "  label, client_id, client_secret_encrypted, refresh_token_encrypted, "
            "  accounts_base, api_base, shared_with_organization_ids, rotated_at, "
            "  created_at, updated_at) "
            "VALUES (:cid, :org, :label, :client_id, :secret, :refresh, "
            "        :accounts, :api, :shared, :now, :now, :now)"
        ), {
            "cid": credential_id,
            "org": r.organization_id,
            "label": "Migrated from connection",
            "client_id": r.client_id,
            "secret": r.client_secret_encrypted,
            "refresh": r.refresh_token_encrypted,
            "accounts": r.accounts_base,
            "api": r.api_base,
            "shared": "[]",
            "now": now,
        })
        # The inline copy is left in place, not nulled: until the merge step
        # runs there is exactly one credential per connection either way, and
        # keeping the original means a downgrade needs to restore nothing.
        conn.execute(sa.text(
            "UPDATE zoho_connections SET credential_id = :cid WHERE organization_id = :org"),
            {"cid": credential_id, "org": r.organization_id})


def downgrade() -> None:
    # Copy the secrets back inline so connections keep working without the table.
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE zoho_connections SET "
        "  client_id = (SELECT client_id FROM zoho_credentials c "
        "               WHERE c.credential_id = zoho_connections.credential_id), "
        "  client_secret_encrypted = (SELECT client_secret_encrypted FROM zoho_credentials c "
        "               WHERE c.credential_id = zoho_connections.credential_id), "
        "  refresh_token_encrypted = (SELECT refresh_token_encrypted FROM zoho_credentials c "
        "               WHERE c.credential_id = zoho_connections.credential_id) "
        "WHERE credential_id IS NOT NULL"))
    # The index may already be gone: a later revision recreates this table
    # wholesale, and dropping an index that is not there fails the downgrade.
    try:
        op.drop_index("ix_zoho_connections_credential_id",
                      table_name="zoho_connections")
    except Exception:  # noqa: BLE001
        pass
    with op.batch_alter_table("zoho_connections") as batch:
        batch.drop_column("credential_id")
    op.drop_table("zoho_credentials")
