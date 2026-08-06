"""The resume cursor belongs to a connection, not to an organization.

A Zoho document id is unique inside the Zoho organization that issued it and
nowhere else, and this business runs three of them under one PIE organization.
Sharing one cursor across all three lets one company's invoice id suppress
another company's fetch of an unrelated document, and makes a full sync of one
company clear the cursor for all of them — a re-read of every document in every
company, which is what "the data keeps getting repopulated" looks like.

``connection_id`` is nullable: rows written before this existed have no
connection to name, and they match the ``None`` a single-connection deployment
still passes.

Revision ID: b3e91d7a4c26
Revises: a7f31c5e08b2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3e91d7a4c26"
down_revision = "a7f31c5e08b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ingested_documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("connection_id", sa.String(length=64),
                                      nullable=True))
        batch_op.create_index("ix_ingested_documents_connection_id",
                              ["connection_id"], unique=False)
        # The old constraint would refuse two companies holding the same
        # document id, which is exactly the case this migration exists to allow.
        batch_op.drop_constraint("uq_ingested_org_type_doc", type_="unique")
        batch_op.create_unique_constraint(
            "uq_ingested_org_conn_type_doc",
            ["organization_id", "connection_id", "doc_type", "doc_id"])


def downgrade() -> None:
    with op.batch_alter_table("ingested_documents", schema=None) as batch_op:
        batch_op.drop_constraint("uq_ingested_org_conn_type_doc", type_="unique")
        batch_op.create_unique_constraint(
            "uq_ingested_org_type_doc", ["organization_id", "doc_type", "doc_id"])
        batch_op.drop_index("ix_ingested_documents_connection_id")
        batch_op.drop_column("connection_id")
