"""Name the ERP quote a platform quote revises.

A quote the ERP raised can now be picked up in the Quote Builder — ``POST
/api/v1/quotes/from-erp`` reads its lines into a form, bound to the company
that issued it and the customer it was for — and the form, and the quote it
becomes on save, has to say so: which book, which reference. Without the
pointer the new quote's first document could not name what it revises, and
the ERP page could not show that a revision exists.

Two nullable columns on each of the two tables a quote lives in, and nothing
else. Every existing row is a quote that revises nothing, which is what NULL
says. ``connection_id`` is carried beside the reference for the reason
``w10qptr`` gives: an ERP reference is unique only inside the book that
issued it.

Additive, so no batch rebuild is needed on SQLite.

Revision ID: x11qsrc
Revises: w10qptr
Create Date: 2026-09-24
"""
import sqlalchemy as sa
from alembic import op

revision = "x11qsrc"
down_revision = "w10qptr"
branch_labels = None
depends_on = None

_TABLES = ("quote_form_drafts", "quote_drafts")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("source_erp_connection_id", sa.String(64),
                                       nullable=True))
        op.add_column(table, sa.Column("source_erp_quote_ref", sa.String(128),
                                       nullable=True))


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("source_erp_quote_ref")
            batch.drop_column("source_erp_connection_id")
