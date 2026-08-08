"""cost records carry their supplier

``CostRecordIn`` has carried ``vendor_external_id`` since the supply pull
landed — the normaliser copies it down from the bill header and the state
reducers read it off the event payload — but the read model dropped it. So the
one table that holds *what an item cost, line by line* could not answer "which
items does this supplier actually supply", and supplier breadth had to be
re-derived by splitting the bill id back out of ``external_ref`` and joining to
``bills``. That join is a string operation on a composite key, which is a thing
to do once in a migration and never in a query.

The backfill takes the same route, deliberately and only once: ``source_ref``
carries ``record_id`` — the bill id — so each cost line is matched to
``bills.external_ref`` and takes that bill's vendor. Lines whose bill is not in
the table, or whose bill has no vendor, are left null. That is the honest
result: a bill from a supplier the vendor pull never returned is still a real
cost, and inventing an attribution for it would put spend against the wrong
name.

Rows written from now on get the vendor at sync time; a re-sync or a state
replay fills in anything this cannot.

Revision ID: a3f61d80b492
Revises: bcf507053964
Create Date: 2026-08-07
"""
import json

import sqlalchemy as sa
from alembic import op

revision = 'a3f61d80b492'
down_revision = 'bcf507053964'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('cost_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vendor_id', sa.String(64), nullable=True))
        batch_op.create_index('ix_cost_records_vendor_id', ['vendor_id'])
        batch_op.create_index('ix_cost_records_org_vendor_date',
                              ['organization_id', 'vendor_id', 'date'])
        batch_op.create_foreign_key('fk_cost_records_vendor_id', 'vendors',
                                    ['vendor_id'], ['vendor_id'])
    _backfill()


def _backfill() -> None:
    """Cost line → its bill → that bill's vendor.

    Done in Python rather than one ``UPDATE ... FROM``: ``source_ref`` is a JSON
    column and extracting a key from it is spelled differently on SQLite and on
    Postgres. Reading the two columns and matching in memory is the same answer
    on both, and this table is bounded by the number of bill lines an
    organization has ever synced.
    """
    bind = op.get_bind()
    vendor_of_bill = {
        (org, ref): vendor
        for org, ref, vendor in bind.execute(sa.text(
            "SELECT organization_id, external_ref, vendor_id FROM bills "
            "WHERE vendor_id IS NOT NULL"))
    }
    if not vendor_of_bill:
        return

    updates: list[dict] = []
    for cid, org, source_ref in bind.execute(sa.text(
            "SELECT cost_record_id, organization_id, source_ref FROM cost_records")):
        # Written by SQLAlchemy's JSON type: text on SQLite, already decoded on
        # Postgres. Anything else is a row this cannot place, which is fine.
        if isinstance(source_ref, str):
            try:
                source_ref = json.loads(source_ref)
            except ValueError:
                continue
        if not isinstance(source_ref, dict):
            continue
        vendor = vendor_of_bill.get((org, str(source_ref.get("record_id") or "")))
        if vendor:
            updates.append({"vid": vendor, "cid": cid})

    if updates:
        bind.execute(
            sa.text("UPDATE cost_records SET vendor_id = :vid "
                    "WHERE cost_record_id = :cid"),
            updates)


def downgrade() -> None:
    with op.batch_alter_table('cost_records', schema=None) as batch_op:
        batch_op.drop_constraint('fk_cost_records_vendor_id', type_='foreignkey')
        batch_op.drop_index('ix_cost_records_org_vendor_date')
        batch_op.drop_index('ix_cost_records_vendor_id')
        batch_op.drop_column('vendor_id')
