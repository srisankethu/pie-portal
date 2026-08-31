"""The decoded catalogue, per connected company.

Two tables. ``company_corpora`` holds one company's item-master export as
bytes, and ``company_catalogues`` holds what was built from it plus the stamp
that says which catalogue answered.

The corpus is a column rather than a path because the container filesystem is
ephemeral (``railway.json`` declares no volume). That costs nothing today —
``PIE_CORPUS`` ships inside the image, so a lost disk costs a rebuild — but an
uploaded corpus has no such source and would be gone on the next deploy, taking
the ability to rebuild with it. See ``docs/per-company-catalogues.md`` §1.1.

Columns written out literally rather than imported from ``app.domain.models``
(§4): this runs against schemas from months ago, and models describe today.

Revision ID: h1cat
Revises: g1tax
Create Date: 2026-08-30
"""
import sqlalchemy as sa
from alembic import op

revision = "h1cat"
down_revision = "g1tax"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_corpora",
        sa.Column("corpus_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False, index=True),
        sa.Column("connection_id", sa.String(64), nullable=False, index=True),
        sa.Column("filename", sa.String(255), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(128), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, index=True),
        # The export itself. A few megabytes of CSV; see the module docstring
        # for why it is not a path.
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(64), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        # Append-only: a newer upload supersedes rather than overwrites, so a
        # catalogue built from this corpus keeps a real referent.
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_company_corpora_company", "company_corpora",
                    ["organization_id", "connection_id"])

    op.create_table(
        "company_catalogues",
        sa.Column("organization_id", sa.String(64), primary_key=True),
        sa.Column("connection_id", sa.String(64), primary_key=True),
        sa.Column("corpus_id", sa.String(64), nullable=True),
        sa.Column("pack", sa.String(255), nullable=False, server_default=""),
        sa.Column("records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quarantined", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_s", sa.Float(), nullable=True),
        # pie-parser's RunReport, whole. Served verbatim, never recomputed.
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("pack_id", sa.String(128), nullable=True),
        sa.Column("pack_version", sa.String(64), nullable=True),
        sa.Column("org_id", sa.String(128), nullable=True),
        sa.Column("org_version", sa.String(64), nullable=True),
        sa.Column("ruleset_checksum", sa.String(64), nullable=True, index=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=True),
        sa.Column("schema_version", sa.String(32), nullable=True),
        sa.Column("built_by", sa.String(64), nullable=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("company_catalogues")
    op.drop_index("ix_company_corpora_company", table_name="company_corpora")
    op.drop_table("company_corpora")
