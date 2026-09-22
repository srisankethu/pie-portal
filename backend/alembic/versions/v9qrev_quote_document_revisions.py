"""Give a sent document a revision, a channel and a write state.

Three columns on ``quote_documents``, each answering something the row could
not say.

``revision`` — an amended quote re-sent under the same reference never became
a new document on a live book: every adapter keys its idempotency on the
reference, so Zoho, Business Central and Acumatica answered with the document
they already held and NetSuite updated in place, and the platform recorded the
new content against the old number. A revision is now a document of its own,
under a reference of its own (the first keeps the bare reference, so nothing
already written stops being findable).

``channel`` — ``ERP`` for a document this platform wrote; ``MANUAL`` for a send
a person recorded because the quote went out another way. The manual row
arrives with the next phase; the column lands here so the schema moves once.

``write_state`` — ``WRITTEN`` when the source confirmed the document, and
``UNVERIFIED`` for the send whose reply was lost and whose settle read failed
too. That state used to live only in the HTTP response; a row with the
reference the write went out under is what lets the next person be told to
look for it before sending again.

Additive, with server defaults, so every existing row reads as revision 1,
channel ERP, WRITTEN — which is what every one of them is.

Revision ID: v9qrev
Revises: u8qlink
Create Date: 2026-09-20
"""
import sqlalchemy as sa
from alembic import op

revision = "v9qrev"
down_revision = "u8qlink"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quote_documents",
                  sa.Column("revision", sa.Integer(), nullable=False,
                            server_default="1"))
    op.add_column("quote_documents",
                  sa.Column("channel", sa.String(length=8), nullable=False,
                            server_default="ERP"))
    op.add_column("quote_documents",
                  sa.Column("write_state", sa.String(length=16), nullable=False,
                            server_default="WRITTEN"))


def downgrade() -> None:
    with op.batch_alter_table("quote_documents") as batch_op:
        batch_op.drop_column("write_state")
        batch_op.drop_column("channel")
        batch_op.drop_column("revision")
