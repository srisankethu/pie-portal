"""Persist the document a quote produced, so a send survives the process.

Everything about a sent estimate — its number, its line count, the fingerprint
of the content it was written from — lived on an in-memory dataclass in a
process-wide dict. After a restart the screen could not say a quote had been
sent, the local duplicate check could not answer and pressed the source again,
and nothing linked a quote to the document it created in anybody's ledger.

Append-only history: one row per send, never updated, newest is current. No
unique key on the fingerprint on purpose — a crash between the source write and
this insert has to be recoverable by writing the row late, and a constraint
there would turn a recorded send into an integrity error.

Revision ID: a7qdoc
Revises: z6subject
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "a7qdoc"
down_revision = "z6subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_documents",
        sa.Column("quote_document_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("quote_id", sa.String(64), nullable=False),
        sa.Column("external_system", sa.String(32), nullable=False,
                  server_default=""),
        sa.Column("external_document_id", sa.String(64), nullable=True),
        sa.Column("external_document_number", sa.String(64), nullable=False,
                  server_default=""),
        sa.Column("reference", sa.String(128), nullable=False, server_default=""),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sa.String(128), nullable=False, server_default=""),
        sa.Column("already_existed", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("thresholds_version", sa.String(64), nullable=False,
                  server_default=""),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quote_documents_organization_id", "quote_documents",
                    ["organization_id"])
    op.create_index("ix_quote_documents_quote_id", "quote_documents", ["quote_id"])
    op.create_index("ix_quote_documents_fingerprint", "quote_documents",
                    ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_quote_documents_fingerprint", table_name="quote_documents")
    op.drop_index("ix_quote_documents_quote_id", table_name="quote_documents")
    op.drop_index("ix_quote_documents_organization_id", table_name="quote_documents")
    op.drop_table("quote_documents")
