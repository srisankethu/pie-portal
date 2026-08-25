"""ERP-raised quotes: the demand-side document, and the human row that points at one.

The platform has read what customers *committed* to since the first sync —
``sales_orders``, then the invoices behind them — and has never read what was
*offered*. So a win rate had no denominator: the accepted quotes are visible
through the orders they became, and the declined ones leave no trace anywhere.

``erp_quotes`` is that document, at header grain like every other document
table here. Two status columns on purpose: ``source_status`` is the ERP's own
word carried verbatim and never mapped on the way in, ``outcome`` is this
platform's WON / LOST / UNRECORDED classification of it. Keeping only the first
would put the mapping in every reader that ever asks and the third copy is the
one that reads ``expired`` as a loss; keeping only the second would leave
nothing to group by when somebody asks which statuses fell through. ``outcome``
is NOT NULL with a default because a nullable classification invites a
``COALESCE`` and silence would start meaning whatever the last reader chose.

There is no ``loss_reason``, ``lost_to`` or ``note`` column on it, and there is
no cost, margin or unit-economics column either. Every column is rewritten from
the ERP payload on every sync, so a human-supplied fact stored there would
survive exactly until the next pull.

Which is what the second half of this revision is for. ``quote_outcomes`` is the
table a human writes and no sync touches, and it gains ``quote_document_ref`` —
holding ``erp_quotes.external_ref``, the id the *source system* issued, and
deliberately not ``quote_document_id``, which is a surrogate minted at insert.
A value pointer is what makes the harshest rebuild safe: ``DELETE FROM
erp_quotes`` followed by a full re-sync re-mints every surrogate and every
human pointer still resolves. ``payment_applications.invoice_external_ref``
carries an ERP reference for the same reason.

``quote_id`` becomes nullable in the same block, because a row now describes one
of two things: a platform quote (``quote_id`` set, ``quote_document_ref`` filled
in when the quote is pushed to the ERP) or an ERP-raised quote the platform
never priced (``quote_id`` NULL). NULL is the honest encoding of "there is no
platform quote". NULLs are distinct in a unique index on SQLite and on
PostgreSQL alike, so ``uq_quote_outcome_org_quote`` and the new
``uq_quote_outcome_org_document`` coexist over the same rows with no partial
index needed.

**Nothing is backfilled.** An existing ``quote_outcomes`` row cannot be
attributed to an ERP estimate after the fact — the same sentence
``b7c41e0a9d38`` uses about ``loss_reason``, and for the same reason: a guessed
link is indistinguishable from one somebody made.

No CHECK constraint asserting that a row names at least one document. This repo
has no CHECK precedent and ``compare_metadata`` does not compare them, so the
drift test could never police one; the invariant is enforced in
``commercial.quote_service.set_outcome``, which is provably the single writer.

No ``sync_runs`` counter either. The two most recent document pulls — credit
notes and locations — added none, and a per-pull column on that table is a
schema change per feature for a number the run report already carries.

One batch block on ``quote_outcomes``, so SQLite rebuilds the table once.
``alembic/env.py`` sets ``render_as_batch=True`` on both the offline and online
paths, and every existing constraint on that table is named, so reflection
carries them through the rebuild.

Columns are written out literally rather than imported from the models, per the
rule that a migration runs against a schema from months ago.

Revision ID: c9qdoc
Revises: b8lease
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa

revision = "c9qdoc"
# Rechained onto main's head rather than branching beside it. These three
# revisions (c9qdoc -> d1thrv -> 04b3c45610eb) have only ever existed on this
# branch, so re-pointing the first is not editing released history — it is
# the convention "keep the migration history linear: chain after main's
# head, not beside it", and it is what keeps `alembic heads` at one.
down_revision = "d4rls"
branch_labels = None
depends_on = None

#: Per-column indexes on the new table, in the shape SQLAlchemy's ``index=True``
#: names them — so a fresh migrate and the models agree and the drift check has
#: nothing to report.
_QUOTE_DOCUMENT_INDEXES = (
    ("ix_erp_quotes_organization_id", ["organization_id"]),
    ("ix_erp_quotes_connector", ["connector"]),
    ("ix_erp_quotes_connection_id", ["connection_id"]),
    ("ix_erp_quotes_external_ref", ["external_ref"]),
    ("ix_erp_quotes_source_reference", ["source_reference"]),
    ("ix_erp_quotes_customer_id", ["customer_id"]),
    ("ix_erp_quotes_date", ["date"]),
    # Composite, and the two the readers actually filter on: "every decided
    # quote for this org" and "this org's quotes over a window".
    ("ix_erp_quotes_org_outcome", ["organization_id", "outcome"]),
    ("ix_erp_quotes_org_date", ["organization_id", "date"]),
)


def upgrade() -> None:
    op.create_table(
        "erp_quotes",
        sa.Column("quote_document_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("connector", sa.String(length=32), nullable=True),
        sa.Column("connection_id", sa.String(length=64), nullable=True),
        sa.Column("external_ref", sa.String(length=128), nullable=False),
        sa.Column("number", sa.String(length=128), nullable=True),
        sa.Column("source_reference", sa.String(length=128), nullable=True),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("customer_ref", sa.String(length=255), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("source_status", sa.String(length=48), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("decided_on", sa.Date(), nullable=True),
        # The quote's own selling total. Not a cost and not a margin.
        sa.Column("total", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("salesperson_external_id", sa.String(length=64), nullable=True),
        sa.Column("client_viewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.customer_id"]),
        sa.PrimaryKeyConstraint("quote_document_id"),
        sa.UniqueConstraint("organization_id", "connector", "connection_id",
                            "external_ref", name="uq_erp_quote_source"),
    )
    for name, columns in _QUOTE_DOCUMENT_INDEXES:
        op.create_index(name, "erp_quotes", columns)

    with op.batch_alter_table("quote_outcomes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quote_document_ref", sa.String(length=128),
                                      nullable=True))
        batch_op.alter_column("quote_id", existing_type=sa.String(length=64),
                              nullable=True)
        batch_op.create_index("ix_quote_outcomes_quote_document_ref",
                              ["quote_document_ref"])
        batch_op.create_unique_constraint("uq_quote_outcome_org_document",
                                          ["organization_id", "quote_document_ref"])


def downgrade() -> None:
    # Restoring NOT NULL on ``quote_id`` is only safe once the rows that have no
    # platform quote are gone, and those are exactly the rows this revision
    # made possible. They are deleted rather than given a placeholder id: a
    # fabricated ``quote_id`` would point at nothing and would be
    # indistinguishable from a real one on the way back up.
    op.execute("DELETE FROM quote_outcomes WHERE quote_id IS NULL")
    with op.batch_alter_table("quote_outcomes", schema=None) as batch_op:
        batch_op.drop_constraint("uq_quote_outcome_org_document", type_="unique")
        batch_op.drop_index("ix_quote_outcomes_quote_document_ref")
        batch_op.alter_column("quote_id", existing_type=sa.String(length=64),
                              nullable=False)
        batch_op.drop_column("quote_document_ref")

    for name, _columns in reversed(_QUOTE_DOCUMENT_INDEXES):
        op.drop_index(name, table_name="erp_quotes")
    op.drop_table("erp_quotes")
