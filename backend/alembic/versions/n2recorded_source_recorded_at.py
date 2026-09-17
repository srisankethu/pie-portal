"""Capture when the SOURCE system recorded a document, not when we synced it.

A quote diagnosis may only use evidence that was knowable when the quote was
written, and until now nothing in this schema could answer that. Three clocks
exist and the platform stored the wrong two: ``date`` is when the commercial
fact happened, ``created_at`` is when this platform synced the row — and a
backfill stamps years of history with one value — while the ERP's own
``created_time``, the moment a fact became visible to the business, was read
nowhere.

Measured on the live books before this was written: an invoice is *authored* in
the ERP, so 99.5% of them carry zero lag between the two; a bill is
*transcribed* from a supplier's document that arrived later, and runs a 3-day
median with a 7-day p90. That gap is the whole of the engine's look-ahead
exposure, and it lands on the cost side.

``history_loaded_before`` on the connection is the other half. Two thirds of one
live book was bulk-loaded in a single month, and for those rows ``created_time``
is the load time — it says nothing about when the business knew, because that
happened in a system this one never read. A stored, correctable date is how a
reader tells the two cohorts apart; inferring the boundary from a burst in the
row counts would move it every time the counts did.

Nullable and nothing backfilled. Deriving a recorded time from ``date`` or from
``created_at`` would be indistinguishable from evidence, and the whole point of
the column is that it is evidence. A full re-sync fills it from the source.

Revision ID: n2recorded
Revises: m1mail
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "n2recorded"
down_revision = "m1mail"
branch_labels = None
depends_on = None

#: The line-and-document tables the evidence builder reads, with the index name
#: each one's filter needs. Only these three: a column nothing reads is a column
#: the next migration has to explain, and the invoice and bill *headers* have no
#: reader here — their lines carry the stamp and the headers answer accounts
#: payable and receivable, which are not point-in-time questions.
_TABLES = (
    ("sales_txns", "ix_sales_txns_source_recorded_at"),
    ("cost_records", "ix_cost_records_source_recorded_at"),
    ("erp_quotes", "ix_erp_quotes_source_recorded_at"),
)


def upgrade() -> None:
    for table, index in _TABLES:
        op.add_column(table, sa.Column("source_recorded_at",
                                       sa.DateTime(timezone=True), nullable=True))
        op.create_index(index, table, ["source_recorded_at"])
    op.add_column("zoho_connections",
                  sa.Column("history_loaded_before", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("zoho_connections", "history_loaded_before")
    for table, index in _TABLES:
        op.drop_index(index, table_name=table)
        op.drop_column(table, "source_recorded_at")
