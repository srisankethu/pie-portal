"""Every imported document carries which book it came from.

The master tables learned this in ``a8d5e21b6f47``: an external id is unique only
inside the system that issued it, and only inside one company of that system, so
``Customer``, ``Product`` and ``Vendor`` key on ``(organization, connector,
connection, external id)``. The fifteen *document* tables were left on
``(organization_id, external_ref)`` and the reasoning applies to them verbatim —
Zoho issues globally unique document ids, so two connected Zoho companies have
never collided, and the first connector that numbers per company breaks it
silently, by upserting two different documents onto one row.

It is also what makes the deletion sweep containable. ``sync._mirror`` documents
a guard — *only this connection's documents* — that ``repositories.retire_document``
cannot express while the fact tables have no connection to filter on: it matches
``(organization_id, external_ref)`` and so deletes both companies' rows for a
colliding reference. ``test_retiring_a_colliding_ref_does_not_delete_the_other_
companys_rows`` is ``xfail(strict=True)`` against exactly that, and this
revision is what turns it green.

**Nullable, and nothing is backfilled.** Deliberately, and for the reason
``a8d5e21b6f47`` gives at length: a row imported before this existed cannot be
attributed after the fact, because the sync run that wrote it did not record
which connection it came from. Writing the organization's only connection into
it would invent provenance that is right today and wrong the moment a second
company connects — and this organization already runs three. NULL reads as
"source not recorded", which is the true statement; the next full sync attributes
the rows it re-reads, through the adoption rule in ``repositories._for_upsert``.

That is why this revision cannot be squared away with a data migration and does
not try. It widens the key and leaves the history honest about what it does not
know.

The unique constraints are replaced rather than added alongside: two constraints
where one is a strict subset of the other means the narrow one still rejects the
writes the wide one exists to permit.

SQLite cannot drop a constraint in place, so this runs inside
``batch_alter_table``, which rebuilds each table. On Postgres the same calls are
plain DDL.

Revision ID: d7b3e91c4f05
Revises: c1e4f80b7a92
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = "d7b3e91c4f05"
down_revision = "c1e4f80b7a92"
branch_labels = None
depends_on = None

#: table, old constraint, new constraint, and the columns of each key.
#:
#: Both column lists are carried, and the old one is not "organization_id plus
#: external_ref" for every table: ``invoice_sales_orders`` keys on a pair of
#: references and ``stock_location_snapshots`` on product, location and day, and
#: neither has an ``external_ref`` column at all. Assuming the common shape here
#: made ``downgrade`` build a constraint out of columns that do not exist, which
#: `test_the_newest_migration_is_reversible` caught on the way back up.
#:
#: Written out literally rather than derived from the models — this runs against
#: schemas from months ago and the models describe today.
_TABLES = (
    ("sales_txns", "uq_salestxn_org_ref", "uq_salestxn_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("cost_records", "uq_costrecord_org_ref", "uq_costrecord_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("payment_receipts", "uq_payment_org_external", "uq_payment_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("payment_applications", "uq_payment_application_org_external",
     "uq_payment_application_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("sales_orders", "uq_sales_order_org_ref", "uq_sales_order_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("bills", "uq_bill_org_ref", "uq_bill_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("invoices", "uq_invoice_org_ref", "uq_invoice_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("invoice_sales_orders", "uq_invoice_sales_order_org_pair",
     "uq_invoice_sales_order_source",
     ("organization_id", "connector", "connection_id",
      "invoice_external_ref", "sales_order_external_ref"),
     ("organization_id", "invoice_external_ref", "sales_order_external_ref")),
    ("locations", "uq_location_org_ref", "uq_location_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("stock_location_snapshots", "uq_stock_loc_org_product_location_day",
     "uq_stock_loc_source",
     ("organization_id", "connector", "connection_id", "product_id",
      "location_external_ref", "as_of"),
     ("organization_id", "product_id", "location_external_ref", "as_of")),
    ("credit_notes", "uq_credit_note_org_ref", "uq_credit_note_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("credit_note_applications", "uq_credit_note_application_org_external",
     "uq_credit_note_application_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("vendor_payments", "uq_vendor_payment_org_ref", "uq_vendor_payment_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("bill_payment_applications", "uq_bill_payment_application_org_external",
     "uq_bill_payment_application_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
    ("purchase_orders", "uq_purchase_order_org_external",
     "uq_purchase_order_source",
     ("organization_id", "connector", "connection_id", "external_ref"),
     ("organization_id", "external_ref")),
)


def upgrade() -> None:
    for table, old_uq, new_uq, cols, _old_cols in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("connector", sa.String(length=32),
                                       nullable=True))
            batch.add_column(sa.Column("connection_id", sa.String(length=64),
                                       nullable=True))
            batch.drop_constraint(old_uq, type_="unique")
            batch.create_unique_constraint(new_uq, list(cols))
        op.create_index(f"ix_{table}_connector", table, ["connector"])
        op.create_index(f"ix_{table}_connection_id", table, ["connection_id"])


def downgrade() -> None:
    for table, old_uq, new_uq, _cols, old_cols in _TABLES:
        op.drop_index(f"ix_{table}_connection_id", table_name=table)
        op.drop_index(f"ix_{table}_connector", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(new_uq, type_="unique")
            # The narrow key this replaced, with *that table's* own columns.
            # Recreating it can fail where two connections have since written
            # the same reference — which is the collision this revision exists
            # to permit, so a downgrade after a second connector has run is not
            # expected to succeed.
            batch.create_unique_constraint(old_uq, list(old_cols))
            batch.drop_column("connection_id")
            batch.drop_column("connector")
