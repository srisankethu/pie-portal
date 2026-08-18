"""Connections and credentials gain a connector discriminator.

The connection tables were built when Zoho Books was the only system the
platform could read. US clients run NetSuite, Dynamics 365 Business Central,
Acumatica, Epicor Prophet 21 and Sage — so both tables now say which system a
row belongs to, and credentials gain a generic encrypted-JSON secret store for
the auth shapes that do not fit Zoho's OAuth triple (NetSuite token-based auth
is four secrets; Acumatica is a username and password).

Existing rows are all Zoho rows, which is what the server default backfills.
The connection uniqueness widens from (organization, company) to
(organization, connector, company): two systems may issue the same id string,
and neither may be connected twice.

The three Zoho OAuth columns on credentials become nullable — a NetSuite
credential has no refresh token, and an empty string pretending to be one is
the kind of placeholder the working agreement bans.

Revision ID: u1erp_connectors
Revises: t12login_throttle
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "u1erp_connectors"
down_revision = "t12login_throttle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("zoho_credentials") as batch:
        batch.add_column(sa.Column("connector", sa.String(length=32),
                                   nullable=False, server_default="zoho"))
        batch.add_column(sa.Column("secrets_encrypted", sa.String(length=4096),
                                   nullable=True))
        batch.add_column(sa.Column("config", sa.JSON(), nullable=True))
        batch.alter_column("client_id", existing_type=sa.String(length=255),
                           nullable=True)
        batch.alter_column("client_secret_encrypted",
                           existing_type=sa.String(length=2048), nullable=True)
        batch.alter_column("refresh_token_encrypted",
                           existing_type=sa.String(length=2048), nullable=True)

    with op.batch_alter_table("zoho_connections") as batch:
        batch.add_column(sa.Column("connector", sa.String(length=32),
                                   nullable=False, server_default="zoho"))
        batch.add_column(sa.Column("config", sa.JSON(), nullable=True))
        batch.drop_constraint("uq_zoho_connection_org_company", type_="unique")
        batch.create_unique_constraint(
            "uq_zoho_connection_org_company",
            ["organization_id", "connector", "zoho_organization_id"])


def downgrade() -> None:
    with op.batch_alter_table("zoho_connections") as batch:
        batch.drop_constraint("uq_zoho_connection_org_company", type_="unique")
        batch.create_unique_constraint(
            "uq_zoho_connection_org_company",
            ["organization_id", "zoho_organization_id"])
        batch.drop_column("config")
        batch.drop_column("connector")

    with op.batch_alter_table("zoho_credentials") as batch:
        batch.alter_column("refresh_token_encrypted",
                           existing_type=sa.String(length=2048), nullable=False)
        batch.alter_column("client_secret_encrypted",
                           existing_type=sa.String(length=2048), nullable=False)
        batch.alter_column("client_id", existing_type=sa.String(length=255),
                           nullable=False)
        batch.drop_column("config")
        batch.drop_column("secrets_encrypted")
        batch.drop_column("connector")
