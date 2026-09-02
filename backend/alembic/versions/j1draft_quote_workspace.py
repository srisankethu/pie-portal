"""quote_drafts becomes the Quote Builder's workspace.

The builder's quotes lived in one process-wide dict, one draft per browser in
``localStorage``, numbered ``QB-`` plus the clock modulo 100000. Three defects
with one cause — nothing durable held a quote — and this is the row that does.
``quote_drafts`` already existed, designed as transient input for the context
bundle and never written by anything; it is extended rather than joined by a
second drafts table, because two tables for "a quote somebody is working on"
is how a workspace ends up showing half of them.

What changes on the table:

  * ``customer_id`` becomes nullable. A quote starts with no customer and gains
    one when the desk chooses; the builder used to open every quote against a
    literal name, and an empty customer was unrepresentable.
  * ``customer_name``, ``number``, ``sequence``, ``connection_id``,
    ``reference``, ``updated_by_user_id``, ``updated_at`` and ``archived_at``
    are added — the quote-level fields ``store.Quote`` carries, written out
    literally, plus the soft-delete stamp that keeps a removed draft's number
    from being minted again.
  * ``(organization_id, sequence)`` is unique. The number is minted from the
    sequence, per organization, and the constraint is what makes two desks
    starting a quote in the same instant a retry rather than a duplicate.

The added NOT NULL columns carry a server default so the statement is valid
against a table with rows in it; the table had none anywhere, but a migration
that only works on an empty table is a migration that fails on the one
deployment it was not tested on.

Revision ID: j1draft
Revises: i1src
Create Date: 2026-09-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "j1draft"
down_revision = "i1src"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quote_drafts", schema=None) as batch:
        batch.alter_column("customer_id", existing_type=sa.String(64),
                           nullable=True)
        batch.add_column(sa.Column("customer_name", sa.String(255),
                                   nullable=False, server_default=""))
        batch.add_column(sa.Column("updated_by_user_id", sa.String(64),
                                   nullable=True))
        batch.add_column(sa.Column("number", sa.String(32),
                                   nullable=False, server_default=""))
        batch.add_column(sa.Column("sequence", sa.Integer(),
                                   nullable=False, server_default="0"))
        batch.add_column(sa.Column("connection_id", sa.String(64),
                                   nullable=True))
        batch.add_column(sa.Column("reference", sa.String(64),
                                   nullable=False, server_default=""))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True),
                                   nullable=False,
                                   server_default=sa.func.now()))
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True),
                                   nullable=True))
        batch.create_unique_constraint("uq_quote_drafts_org_sequence",
                                       ["organization_id", "sequence"])


def downgrade() -> None:
    # Drafts written under this revision have no customer to fall back to, so
    # ``customer_id`` is made NOT NULL again only after those rows are given
    # the empty string the original schema would have accepted.
    op.execute("UPDATE quote_drafts SET customer_id = '' WHERE customer_id IS NULL")
    with op.batch_alter_table("quote_drafts", schema=None) as batch:
        batch.drop_constraint("uq_quote_drafts_org_sequence", type_="unique")
        batch.drop_column("archived_at")
        batch.drop_column("updated_at")
        batch.drop_column("reference")
        batch.drop_column("connection_id")
        batch.drop_column("sequence")
        batch.drop_column("number")
        batch.drop_column("updated_by_user_id")
        batch.drop_column("customer_name")
        batch.alter_column("customer_id", existing_type=sa.String(64),
                           nullable=False)
