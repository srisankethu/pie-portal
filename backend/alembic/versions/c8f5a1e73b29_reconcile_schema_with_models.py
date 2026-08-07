"""Reconcile the schema with the models: NOT NULL, foreign keys, indexes.

Sixteen released migrations declared columns without ``nullable=False`` while
their models declared them non-Optional, so a freshly-migrated database differed
from the ORM metadata in 130 places. Nothing was visibly broken, which is why it
accumulated — but it meant ``--autogenerate`` produced hundreds of spurious
operations, and an autogenerate you cannot read is one nobody uses, so the next
real change gets written by hand and drifts a little further.

Three genuine defects were inside that noise:

* **77 columns accept NULL that the models forbid.** SQLAlchemy will not write
  one, so nothing fails today; a hand-written UPDATE, a future migration or a
  second writer would, and the row would then fail to load.
* **4 foreign keys were never created.** SQLite does not enforce them by
  default, so this is invisible in development and is missing referential
  integrity in Postgres.
* **2 indexes were never created** — ``customer_connector_records.gstin`` and
  ``item_connector_records.sku``, the two columns the identity matcher looks
  records up by. Every GSTIN and SKU match has been a full table scan.

Existing releases are not edited; this reconciles forward, which is the rule
that keeps a deployed history trustworthy.

NULLs are filled before each column is tightened. On SQLite the batch operations
rebuild the table (there is no ALTER COLUMN), and a rebuild that hits a NULL in
a NOT NULL column fails the whole migration; on Postgres ``SET NOT NULL`` fails
the same way. The fill values match each column's declared default, so a row
that was never given one ends up with what it would have had.

**The fill literals were edited after release, which the rule in CLAUDE.md §4
forbids. Here is why that was the only available fix.** Thirteen BOOLEAN columns
were filled with ``0`` and one DATE column with ``CURRENT_TIMESTAMP``. SQLite
accepts both — its booleans *are* integers and its dates are text — so the whole
suite passed and a fresh SQLite database migrated cleanly. Postgres type-checks
the statement and rejects it:

    UPDATE "customer_identities" SET "active" = 0 WHERE "active" IS NULL
    DatatypeMismatch: column "active" is of type boolean but expression is of
    type integer

Which meant ``alembic upgrade head`` could not reach head on Postgres at all —
the database documented for production. Reconciling forward is impossible when
the broken revision is the one that will not run: no later migration is ever
reached. Editing this one was the only fix that exists.

It is also, uniquely, a safe one. Postgres rejects the statement while parsing,
so no Postgres database has ever run this revision. And on a fresh database
these tables are still empty when it runs, so the UPDATEs match zero rows and
the literal is never written — the edit changes what the statement *parses* as,
not what any row ends up holding. Databases already stamped past this revision
do not re-run it. ``FALSE`` and ``CURRENT_DATE`` are standard SQL that SQLite
(since 3.23) and Postgres both accept.

Revision ID: c8f5a1e73b29
Revises: b4e17d90c3aa
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8f5a1e73b29"
down_revision = "b4e17d90c3aa"
branch_labels = None
depends_on = None


#: table -> [(column, declared type, SQL literal used to fill existing NULLs)]
NOT_NULL: dict[str, list[tuple[str, str, str]]] = {
    'approval_requests': [
        ('requested_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('required_authority', 'VARCHAR(16)', "''"),
        ('status', 'VARCHAR(24)', "''"),
        ('subject', 'JSON', "'{}'"),
        ('summary', 'VARCHAR(1024)', "''"),
        ('thread', 'JSON', "'{}'"),
        ('thresholds_version', 'VARCHAR(32)', "''"),
        ('title', 'VARCHAR(255)', "''"),
    ],
    'commercial_policies': [
        ('overrides', 'JSON', "'{}'"),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'customer_connector_records': [
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('name', 'VARCHAR(255)', "''"),
        ('source_ref', 'JSON', "'{}'"),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'customer_identities': [
        ('active', 'BOOLEAN', 'FALSE'),
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'identity_events': [
        ('actor', 'VARCHAR(64)', "''"),
        ('at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('detail', 'VARCHAR(512)', "''"),
    ],
    'identity_policies': [
        ('auto_link_customers', 'BOOLEAN', 'FALSE'),
        ('auto_link_items', 'BOOLEAN', 'FALSE'),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'identity_suggestions': [
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('evidence', 'VARCHAR(255)', "''"),
        ('status', 'VARCHAR(16)', "''"),
    ],
    'item_connector_records': [
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('description', 'VARCHAR(512)', "''"),
        ('source_ref', 'JSON', "'{}'"),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'item_identities': [
        ('active', 'BOOLEAN', 'FALSE'),
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'org_policies': [
        ('allow_self_approval', 'BOOLEAN', 'FALSE'),
        ('below_cost_requires_owner', 'BOOLEAN', 'FALSE'),
        ('escalation_creates_approval', 'BOOLEAN', 'FALSE'),
        ('require_approval_below_review_floor', 'BOOLEAN', 'FALSE'),
        ('require_approval_for_quotes', 'BOOLEAN', 'FALSE'),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'quote_decisions': [
        ('as_of', 'DATE', 'CURRENT_DATE'),
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('customer_ref', 'VARCHAR(255)', "''"),
        ('data_sufficiency', 'VARCHAR(16)', "''"),
        ('engine_version', 'VARCHAR(32)', "''"),
        ('evidence_refs', 'JSON', "'{}'"),
        ('exceptions', 'JSON', "'{}'"),
        ('overridden', 'BOOLEAN', 'FALSE'),
        ('overridden_exception_codes', 'JSON', "'{}'"),
        ('product_ref', 'VARCHAR(255)', "''"),
        ('quantity', 'NUMERIC(18, 4)', '0'),
        ('quantity_band', 'VARCHAR(24)', "''"),
        ('references', 'JSON', "'{}'"),
        ('relationship_metrics', 'JSON', "'{}'"),
        ('requires_approval', 'BOOLEAN', 'FALSE'),
        ('sufficiency_reasons', 'JSON', "'{}'"),
        ('thresholds_version', 'VARCHAR(32)', "''"),
    ],
    'quote_outcomes': [
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('customer_ref', 'VARCHAR(255)', "''"),
        ('status', 'VARCHAR(16)', "''"),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'sync_runs': [
        ('notes', 'JSON', "'{}'"),
        ('windows_done', 'INTEGER', '0'),
        ('windows_total', 'INTEGER', '0'),
    ],
    'users': [
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('must_change_password', 'BOOLEAN', 'FALSE'),
    ],
    'zoho_connections': [
        ('accounts_base', 'VARCHAR(255)', "''"),
        ('api_base', 'VARCHAR(255)', "''"),
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('enabled', 'BOOLEAN', 'FALSE'),
        ('label', 'VARCHAR(255)', "''"),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
    'zoho_credentials': [
        ('accounts_base', 'VARCHAR(255)', "''"),
        ('api_base', 'VARCHAR(255)', "''"),
        ('created_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
        ('label', 'VARCHAR(255)', "''"),
        ('shared_with_organization_ids', 'JSON', "'{}'"),
        ('updated_at', 'DATETIME', 'CURRENT_TIMESTAMP'),
    ],
}


#: Declared on the models, never created. ``(table, column, target, name)``.
FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    ("customer_connector_records", "identity_id",
     "customer_identities.identity_id", "fk_ccr_identity"),
    ("item_connector_records", "identity_id",
     "item_identities.identity_id", "fk_icr_identity"),
    ("zoho_connections", "credential_id",
     "zoho_credentials.credential_id", "fk_zconn_credential"),
    ("zoho_connections", "organization_id",
     "organizations.organization_id", "fk_zconn_org"),
]

#: ``(table, old name, new name, column)``. The identity migration abbreviated
#: these index names; SQLAlchemy generates the long form from ``index=True``, so
#: every ``--autogenerate`` wanted to drop and recreate all 22 of them. The
#: indexes themselves were always correct — only the names disagreed, which is
#: exactly the kind of permanent noise that makes autogenerate unreadable.
RENAME_INDEXES: list[tuple[str, str, str, str]] = [
    ('customer_connector_records', 'ix_ccr_connection_id', 'ix_customer_connector_records_connection_id', 'connection_id'),
    ('customer_connector_records', 'ix_ccr_connector', 'ix_customer_connector_records_connector', 'connector'),
    ('customer_connector_records', 'ix_ccr_customer_id', 'ix_customer_connector_records_customer_id', 'customer_id'),
    ('customer_connector_records', 'ix_ccr_external_id', 'ix_customer_connector_records_external_id', 'external_id'),
    ('customer_connector_records', 'ix_ccr_identity_id', 'ix_customer_connector_records_identity_id', 'identity_id'),
    ('customer_connector_records', 'ix_ccr_organization_id', 'ix_customer_connector_records_organization_id', 'organization_id'),
    ('identity_events', 'ix_idev_action', 'ix_identity_events_action', 'action'),
    ('identity_events', 'ix_idev_at', 'ix_identity_events_at', 'at'),
    ('identity_events', 'ix_idev_entity_type', 'ix_identity_events_entity_type', 'entity_type'),
    ('identity_events', 'ix_idev_identity_id', 'ix_identity_events_identity_id', 'identity_id'),
    ('identity_events', 'ix_idev_organization_id', 'ix_identity_events_organization_id', 'organization_id'),
    ('identity_events', 'ix_idev_record_id', 'ix_identity_events_record_id', 'record_id'),
    ('identity_suggestions', 'ix_sugg_organization_id', 'ix_identity_suggestions_organization_id', 'organization_id'),
    ('identity_suggestions', 'ix_sugg_record_id', 'ix_identity_suggestions_record_id', 'record_id'),
    ('identity_suggestions', 'ix_sugg_status', 'ix_identity_suggestions_status', 'status'),
    ('identity_suggestions', 'ix_sugg_target_identity_id', 'ix_identity_suggestions_target_identity_id', 'target_identity_id'),
    ('item_connector_records', 'ix_icr_connection_id', 'ix_item_connector_records_connection_id', 'connection_id'),
    ('item_connector_records', 'ix_icr_connector', 'ix_item_connector_records_connector', 'connector'),
    ('item_connector_records', 'ix_icr_external_id', 'ix_item_connector_records_external_id', 'external_id'),
    ('item_connector_records', 'ix_icr_identity_id', 'ix_item_connector_records_identity_id', 'identity_id'),
    ('item_connector_records', 'ix_icr_organization_id', 'ix_item_connector_records_organization_id', 'organization_id'),
    ('item_connector_records', 'ix_icr_product_id', 'ix_item_connector_records_product_id', 'product_id'),
]

#: The identity matcher's two lookup columns. ``(name, table, column)``.
INDEXES: list[tuple[str, str, str]] = [
    ("ix_customer_connector_records_gstin", "customer_connector_records", "gstin"),
    ("ix_item_connector_records_sku", "item_connector_records", "sku"),
]


def _existing_index_names(table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    present = set(sa.inspect(bind).get_table_names())

    # 1. Fill NULLs, then tighten. Order matters: the ALTER fails on any NULL.
    for table, columns in NOT_NULL.items():
        if table not in present:
            continue
        for column, _type, fill in columns:
            op.execute(
                sa.text(f'UPDATE "{table}" SET "{column}" = {fill} '
                        f'WHERE "{column}" IS NULL'))
        with op.batch_alter_table(table) as batch:
            for column, type_, _fill in columns:
                batch.alter_column(column, existing_type=sa.types.NullType(),
                                   existing_nullable=True, nullable=False,
                                   type_=None)

    # 2. Foreign keys. Batch mode rebuilds the table on SQLite, which is the
    #    only way to add a constraint there.
    for table, column, target, name in FOREIGN_KEYS:
        if table not in present:
            continue
        target_table, target_column = target.split(".")
        if target_table not in present:
            continue
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(name, target_table, [column], [target_column])

    # 3. The two missing lookup indexes.
    for name, table, column in INDEXES:
        if table in present and name not in _existing_index_names(table):
            op.create_index(name, table, [column])

    # 4. Rename to the names the models generate. Drop-then-create: SQLite has
    #    no ALTER INDEX, and an index is cheap to rebuild.
    for table, old, new, column in RENAME_INDEXES:
        if table not in present:
            continue
        names = _existing_index_names(table)
        if old in names:
            op.drop_index(old, table_name=table)
        if new not in _existing_index_names(table):
            op.create_index(new, table, [column])


def downgrade() -> None:
    """Loosen back. The NULLs that were filled are not restored — there is no
    record of which values were substituted, and re-NULLing every filled column
    would destroy data that has been legitimate since this ran."""
    bind = op.get_bind()
    present = set(sa.inspect(bind).get_table_names())

    for table, old, new, column in reversed(RENAME_INDEXES):
        if table not in present:
            continue
        if new in _existing_index_names(table):
            op.drop_index(new, table_name=table)
        if old not in _existing_index_names(table):
            op.create_index(old, table, [column])

    for name, table, _column in reversed(INDEXES):
        if table in present and name in _existing_index_names(table):
            op.drop_index(name, table_name=table)

    for table, _column, _target, name in reversed(FOREIGN_KEYS):
        if table in present:
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(name, type_="foreignkey")

    for table, columns in NOT_NULL.items():
        if table not in present:
            continue
        with op.batch_alter_table(table) as batch:
            for column, _type, _fill in columns:
                batch.alter_column(column, existing_type=sa.types.NullType(),
                                   existing_nullable=False, nullable=True,
                                   type_=None)
