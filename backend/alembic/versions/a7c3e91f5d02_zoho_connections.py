"""zoho connections

Adds per-organization Zoho Books credentials, so more than one organization can
each connect its own Zoho company — previously a single, process-wide
ZOHO_* configuration served every org. One row per organization (each tenant is
fully separate); the platform's original default organization keeps working
with no row here, falling back to the ZOHO_* environment variables.

Revision ID: a7c3e91f5d02
Revises: f2a9c4b6d1e7
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c3e91f5d02'
down_revision = 'f2a9c4b6d1e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'zoho_connections',
        sa.Column('organization_id', sa.String(length=64), nullable=False),
        sa.Column('zoho_organization_id', sa.String(length=64), nullable=False),
        sa.Column('client_id', sa.String(length=255), nullable=False),
        sa.Column('client_secret_encrypted', sa.String(length=2048), nullable=False),
        sa.Column('refresh_token_encrypted', sa.String(length=2048), nullable=False),
        sa.Column('accounts_base', sa.String(length=255), nullable=False,
                  server_default='https://accounts.zoho.in'),
        sa.Column('api_base', sa.String(length=255), nullable=False,
                  server_default='https://www.zohoapis.in/books/v3'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.organization_id']),
        sa.PrimaryKeyConstraint('organization_id'),
    )


def downgrade() -> None:
    op.drop_table('zoho_connections')
