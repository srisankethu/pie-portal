"""Record every inbound enquiry line, and how each one ended.

Two tables, and they are the first in this schema that a complete re-sync cannot
rebuild. Everything else is a projection of Zoho or something computed from one;
an enquiry that arrived as a WhatsApp message exists in no ERP, so these rows are
canonical and dropping them is data loss rather than a recompute.

``inbound_lines`` is append-only with no supersede stamp and no uniqueness key.
Both absences are deliberate. What arrived, arrived — there is nothing to
supersede. And the same customer asking for the same part twice in a week is two
enquiries, so no key over the content could be correct; a redelivered message is
the adapter's problem to notice on ``source_ref``.

``inbound_line_dispositions`` is append-only with one: a disposition is set later
and corrected by a *new row*, the old one stamped ``superseded_at`` and kept
readable. The partial unique index is what makes "one live answer per line" a
database guarantee. A plain unique constraint over
(organization_id, inbound_line_id) would refuse the correction — which is the
one operation this table is shaped around — so the index is unique over the live
rows only, exactly as ``d5a2e7c31b84`` did for ``value_events``. Both backends
support partial indexes (SQLite since 3.8, Postgres always), so this needs no
dialect branch beyond naming the predicate twice.

``raw_text`` is ``Text`` rather than ``String(n)``: a pasted RFQ can be a
paragraph, and this is the corpus an RFQ parser benchmark reads, so a truncated
row is a silently wrong measurement rather than a visible one.

No column here holds a cost, a margin or a price, and none may be added that
does. The columns are written out literally rather than read from the models,
because a migration runs against schemas from months ago and models describe
today.

Revision ID: a7inbound
Revises: z6subject
Create Date: 2026-08-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7inbound"
down_revision = "z6subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_lines",
        sa.Column("inbound_line_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        # The customer's own words, verbatim. Never normalised at capture.
        sa.Column("raw_text", sa.Text(), nullable=False),
        # An InboundChannel: EMAIL / WHATSAPP / PDF / PORTAL / PHONE_NOTE.
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("customer_ref", sa.String(255), nullable=False,
                  server_default=""),
        sa.Column("source_ref", sa.String(255), nullable=False,
                  server_default=""),
        # When the customer sent it, and when we wrote it down. Two columns
        # because a backlog imported on Friday is not a Friday of demand.
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inbound_lines_organization_id", "inbound_lines",
                    ["organization_id"])
    # The export and the coverage window: one tenant's lines, by arrival.
    op.create_index("ix_inbound_lines_org_received", "inbound_lines",
                    ["organization_id", "received_at"])
    # Coverage by route — "how much of what arrives on WhatsApp do we answer".
    op.create_index("ix_inbound_lines_org_channel", "inbound_lines",
                    ["organization_id", "channel"])

    op.create_table(
        "inbound_line_dispositions",
        sa.Column("disposition_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("inbound_line_id", sa.String(64),
                  sa.ForeignKey("inbound_lines.inbound_line_id"),
                  nullable=False),
        # A LineDisposition: QUOTED / ABSTAINED / NO_STOCK / NO_PRICE / LOST /
        # NO_RESPONSE. Terminal — an undecided line has no row here at all.
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False,
                  server_default=""),
        sa.Column("decided_by_user_id", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_inbound_line_dispositions_organization_id",
                    "inbound_line_dispositions", ["organization_id"])
    op.create_index("ix_inbound_line_dispositions_inbound_line_id",
                    "inbound_line_dispositions", ["inbound_line_id"])
    # One *live* disposition per line — see the module docstring for why this is
    # a partial index and not a unique constraint.
    op.create_index(
        "uq_inbound_line_disposition_live",
        "inbound_line_dispositions",
        ["organization_id", "inbound_line_id"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # The history read: every version for one line, oldest first.
    op.create_index("ix_inbound_line_dispositions_line",
                    "inbound_line_dispositions",
                    ["inbound_line_id", "recorded_at"])
    # The report read: this tenant's outcomes by kind.
    op.create_index("ix_inbound_line_dispositions_org_disposition",
                    "inbound_line_dispositions",
                    ["organization_id", "disposition"])


def downgrade() -> None:
    # Dispositions first: they hold the foreign key.
    #
    # A downgrade here destroys the only copy of this data — unlike every other
    # table in this schema, nothing re-derives these rows from Zoho. It exists
    # because each revision must stand alone and be reversible, not because
    # running it is ever routine.
    op.drop_index("ix_inbound_line_dispositions_org_disposition",
                  table_name="inbound_line_dispositions")
    op.drop_index("ix_inbound_line_dispositions_line",
                  table_name="inbound_line_dispositions")
    op.drop_index("uq_inbound_line_disposition_live",
                  table_name="inbound_line_dispositions")
    op.drop_index("ix_inbound_line_dispositions_inbound_line_id",
                  table_name="inbound_line_dispositions")
    op.drop_index("ix_inbound_line_dispositions_organization_id",
                  table_name="inbound_line_dispositions")
    op.drop_table("inbound_line_dispositions")

    op.drop_index("ix_inbound_lines_org_channel", table_name="inbound_lines")
    op.drop_index("ix_inbound_lines_org_received", table_name="inbound_lines")
    op.drop_index("ix_inbound_lines_organization_id", table_name="inbound_lines")
    op.drop_table("inbound_lines")
