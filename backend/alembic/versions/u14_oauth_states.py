"""Hold an in-flight authorization where the redirect back can find it.

The customer-facing Zoho OAuth flow stored its CSRF state in
``Organization.config`` and was removed as dead code because it never completed.
Both reasons it could not complete were about where that state lived: the write
was an in-place mutation of a plain ``JSON`` column, which SQLAlchemy does not
mark dirty and so never persisted; and keying it under the organization meant
the callback had to know the organization before it could look the state up,
which forced a bearer token onto an endpoint the browser reaches by following
Zoho's redirect — carrying no such header, and so refused before its handler ran.

A table keyed by the hash of the state token is findable by the one value the
redirect actually carries. That is what lets the callback be public and still
know whose authorization it completes.

Neither the state token nor the handoff token is stored — only ``sha256`` of
each, because a live token here is a usable half of an authorization and this
table is readable by anything that reaches the database.

Columns are written out literally rather than imported from the models, per
CLAUDE.md §4: this migration runs against schemas from months ago and the models
describe today.

Revision ID: u14oauthstates
Revises: t13plan_requests
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa


revision = "u14oauthstates"
down_revision = "t13plan_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('oauth_states',
    sa.Column('state_hash', sa.String(length=64), nullable=False),
    sa.Column('organization_id', sa.String(length=64), nullable=False),
    sa.Column('connector', sa.String(length=32), server_default='zoho', nullable=False),
    sa.Column('accounts_base', sa.String(length=255), nullable=False),
    sa.Column('api_base', sa.String(length=255), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('handoff_hash', sa.String(length=64), nullable=True),
    sa.Column('credential_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.organization_id'], ),
    sa.PrimaryKeyConstraint('state_hash')
    )
    with op.batch_alter_table('oauth_states', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oauth_states_expires_at'), ['expires_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_oauth_states_handoff_hash'), ['handoff_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_oauth_states_organization_id'), ['organization_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('oauth_states', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oauth_states_organization_id'))
        batch_op.drop_index(batch_op.f('ix_oauth_states_handoff_hash'))
        batch_op.drop_index(batch_op.f('ix_oauth_states_expires_at'))

    op.drop_table('oauth_states')
