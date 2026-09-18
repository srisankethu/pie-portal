"""Carry every source's own fields, on every entity that can have them.

One additive, nullable JSON column — ``source_attributes`` — on the seven
master and document tables that had nowhere to put them, and the rename of the
one column that already did.

**The rename.** ``erp_quotes.attributes`` held exactly this concept and was the
only column of its kind, so it took the shortest available name. It cannot keep
it: ``attributes`` is already this codebase's word for a *decoded technical fact
about a product* — a corner radius, a grade — held in
``product_attribute_values`` with its own provenance and supersede semantics. A
``products.attributes`` meaning "the ERP's custom fields" would put two
unrelated concepts behind one word on one entity. So the column is renamed
forward here rather than a second name being introduced beside it.

**Nullable, no server default, nothing backfilled**, and that is the whole of
the design decision. ``{}`` and NULL are different claims: ``{}`` asserts *the
source holds no custom fields on this record*, NULL says only that none are
held. Nothing can make the first claim about a row written before this column
existed, and nothing can make it about a row whose payload was projected by a
connector that never read them either — an adapter that dropped them is
indistinguishable downstream from an ERP that has none. Filling the seven new
columns with ``{}`` would put that false claim on every row in the book on the
day this lands, which is the benign default CLAUDE.md §1 is about.

The quote column loses its NOT NULL for the same reason, so one concept has one
shape everywhere. Rows already in ``erp_quotes`` keep the ``{}`` they were
written with; only new writes distinguish.

Additive and reversible. A database that has run this serves the previous code
unchanged apart from the renamed column, which ``schema_check.missing_columns``
reports and the 503 names; the downgrade restores the old name and refills the
NULLs it must, so the NOT NULL it re-imposes can be satisfied.

Revision ID: s6srcattr
Revises: r5spec
Create Date: 2026-09-18
"""
import sqlalchemy as sa
from alembic import op

revision = "s6srcattr"
down_revision = "r5spec"
branch_labels = None
depends_on = None

COLUMN = "source_attributes"
OLD_QUOTE_COLUMN = "attributes"
QUOTE_TABLE = "erp_quotes"

#: The tables gaining the column. Written out literally rather than derived from
#: ``app.domain.models`` (§4): this runs against schemas from months ago and the
#: models describe today.
TABLES = ("customers", "products", "vendors", "sales_orders", "purchase_orders",
          "invoices", "bills")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column(COLUMN, sa.JSON(), nullable=True))
    # Batch, because this is a rename *and* a nullability change and SQLite can
    # only do the pair by recreating the table. ``render_as_batch`` is already
    # set in ``env.py``; asking for it explicitly here is what makes the
    # operation work when this revision is run on its own.
    with op.batch_alter_table(QUOTE_TABLE) as batch_op:
        batch_op.alter_column(OLD_QUOTE_COLUMN, new_column_name=COLUMN,
                              existing_type=sa.JSON(),
                              existing_nullable=False, nullable=True)


def downgrade() -> None:
    # The old column was NOT NULL, so every row written since this revision
    # without source fields has to be given the empty object back before the
    # constraint can be re-imposed. It is the value those rows would have
    # carried under the old code, and the only one that does not fail the
    # downgrade outright.
    op.execute(
        f"UPDATE {QUOTE_TABLE} SET {COLUMN} = '{{}}' WHERE {COLUMN} IS NULL")
    with op.batch_alter_table(QUOTE_TABLE) as batch_op:
        batch_op.alter_column(COLUMN, new_column_name=OLD_QUOTE_COLUMN,
                              existing_type=sa.JSON(),
                              existing_nullable=True, nullable=False)
    for table in reversed(TABLES):
        op.drop_column(table, COLUMN)
