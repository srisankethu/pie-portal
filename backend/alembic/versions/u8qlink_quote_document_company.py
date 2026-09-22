"""Say which company's book a sent document and an outcome's reference belong to.

Two nullable columns, one per table, and the same fact twice: an ERP id is
unique only inside the company that issued it, and neither of the platform's
two pointers at an ERP document carried the company.

``quote_documents.connection_id`` — the send knew which book it wrote into
(``routers.quote.QuoteBooks`` resolves the customer's connection) and recorded
only the connector, so a two-company Zoho organization could not join the
document it wrote to the same document once the sync read it back:
``erp_quotes`` keys on (connector, company, id) and the platform side had only
two of the three.

``quote_outcomes.quote_document_connection_id`` — the qualifier
``quote_service.sole_erp_quote`` deferred until a per-company quote read
existed. The send has the value at the moment it writes, and the ERP-only path
has the document in hand, so both fill it now; every reader of the bare
reference resolves a NULL as "unqualified — while unique".

No backfill. A row written before the column existed cannot be attributed
after the fact, the rule every document table follows (``s6srcattr`` for the
same reason). The unique constraint on the bare outcome reference is left as
it is; widening it is the day the first per-company ``list_quotes`` lands, and
is flagged there.

Additive: a database that has run this serves the previous code unchanged.

Revision ID: u8qlink
Revises: t7concept
Create Date: 2026-09-20
"""
import sqlalchemy as sa
from alembic import op

revision = "u8qlink"
down_revision = "t7concept"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quote_documents",
                  sa.Column("connection_id", sa.String(length=64), nullable=True))
    op.create_index("ix_quote_documents_connection_id", "quote_documents",
                    ["connection_id"])
    op.add_column("quote_outcomes",
                  sa.Column("quote_document_connection_id", sa.String(length=64),
                            nullable=True))
    op.create_index("ix_quote_outcomes_quote_document_connection_id",
                    "quote_outcomes", ["quote_document_connection_id"])


def downgrade() -> None:
    op.drop_index("ix_quote_outcomes_quote_document_connection_id",
                  table_name="quote_outcomes")
    with op.batch_alter_table("quote_outcomes") as batch_op:
        batch_op.drop_column("quote_document_connection_id")
    op.drop_index("ix_quote_documents_connection_id", table_name="quote_documents")
    with op.batch_alter_table("quote_documents") as batch_op:
        batch_op.drop_column("connection_id")
