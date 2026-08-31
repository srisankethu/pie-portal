"""RFQ documents: what the customer SENT, beside what they wrote.

Decision 012, Phase 3. It reverses a recorded decision in the open —
`app/master_health/__init__.py:11` says there is no upload endpoint anywhere and
that adding one "is a dependency decision and a new attack surface". That
reasoning is right for a diagnostic CLI and wrong for a salesperson receiving a
PDF, so the reversal is stated rather than eroded, and `python-multipart` joins
the requirements in the same commit.

The bytes are Fernet ciphertext under the tenant data key. That is not defence
in depth for its own sake: `trust/erasure.erase` destroys the key and writes a
signed receipt and deletes NO rows, because key destruction is "the only form of
deletion that also reaches the backups". Plaintext bytes here would sit outside
that promise while appearing inside it, and `erasure.py` calls a receipt that
overstates what it destroyed worse than no receipt at all. So the column is
registered in `erasure.DESTROYED` in this same change.

It is also why the bytes are in Postgres rather than an object store. A bucket
is reached by none of the four gates this table passes through — export
completeness, the erasure manifest, the RLS census, migration drift — and
`erase` would have to grow a network call whose failure mode is a receipt that
lies. The cost is real and stated: ciphertext is about 1.33x the plaintext
(measured, against 1.78x if the bytes went through the cipher's existing text
path), and `BACKUP_RETAIN_DAYS` defaults to 14.

Org-scoped, so it joins the `tenant_isolation` policy list here in the migration
that creates it rather than in a later sweep — the standing rule a new table
outside the policy list is isolated only by Python.

Revision ID: j1doc
Revises: i1fam
Create Date: 2026-08-31
"""
import sqlalchemy as sa
from alembic import op

revision = "j1doc"
down_revision = "i1fam"
branch_labels = None
depends_on = None

TABLE = "rfq_documents"
POLICY = "tenant_isolation"
GUC = "app.current_org"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("rfq_document_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type_declared", sa.String(length=128),
                  nullable=False, server_default=""),
        sa.Column("content_type_sniffed", sa.String(length=128),
                  nullable=False, server_default=""),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        # LargeBinary, which is BYTEA on PostgreSQL and BLOB on SQLite. The
        # columns are written out literally rather than imported from the model,
        # because this runs against schemas from months ago and models describe
        # today (CLAUDE.md §4).
        sa.Column("content_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("licence_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rfq_documents_organization_id", TABLE,
                    ["organization_id"])
    # Indexed to recognise a redelivery of the same document, which is the only
    # lookup that is not by primary key or by organization.
    op.create_index("ix_rfq_documents_content_sha256", TABLE, ["content_sha256"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Policies exist on PostgreSQL only. A no-op is the honest form of that
        # on SQLite rather than an emulation that would give the suite a false
        # sense of coverage — the same reasoning d1rls states at length.
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY} ON {TABLE} "
        f"USING (organization_id = current_setting('{GUC}', true)) "
        f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.drop_index("ix_rfq_documents_content_sha256", table_name=TABLE)
    op.drop_index("ix_rfq_documents_organization_id", table_name=TABLE)
    op.drop_table(TABLE)
