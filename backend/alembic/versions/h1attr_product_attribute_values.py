"""Product attribute values: the table Phase 1 exists to fill.

The catalogue this platform sells from carries almost no technical fact. On the
live master, 9.4% of items are identity-linked to a manufacturer catalogue
record and 21.6% carry anything decodable from their own name — so retrieval,
compatibility rules, ranking and learning were all going to be built over rows
with nothing in them. Decision 002 puts this table first for that reason, and
makes Phase 1 succeed or fail on published coverage rather than on accuracy.

A row per (product, attribute) rather than a column per attribute: a turning
insert and a drill share almost none of their fields, so a category-specific
schema becomes sparse columns as soon as a second category arrives. `products`
deliberately gains none.

Org-scoped by decision 026 — the attribute source is a distributor export
licensed to the organization that obtained it — so the table joins the
`tenant_isolation` policy list here, in the migration that creates it, rather
than in a later sweep. That is the standing rule the Phase 0 report leaves
behind: a new table outside the policy list is isolated only by Python.

The partial unique index is the "superseded, never mutated" rule expressed in
the schema rather than in a convention. One live row per (org, product,
attribute, source_kind); a replacement is a new row plus a `superseded_at` on
the old one. SQLite and PostgreSQL both support partial indexes, so this holds
on the development database as well as on production.

Revision ID: h1attr
Revises: o2alert
Create Date: 2026-08-30

Written against g1tax on claude/ai-product-equivalence-engine-1m81to and
re-pointed onto o2alert when that branch was merged, which is what a rebase
before merge would have produced. Sound because this revision had never
shipped: it existed only on that branch, main never carried it, so no database
anywhere is stamped at h1attr, i1fam or j1doc and none can be stranded by the
move. CLAUDE.md §4's "never edit a released migration" is about the databases
that ran one, and there are none.

An `alembic merge` was the alternative and was rejected: a merge head makes
`alembic downgrade -1` an "Ambiguous walk" forever after, and
test_the_newest_migration_is_reversible and test_behind_is_detected_and_counted
both walk backwards by a relative count.

Nothing in this line depends on anything g1tax..o2alert added — the tables here
hang off `products` and `organizations`, both far older — so the new parent
changes what runs before it and nothing about what it does.
"""
import sqlalchemy as sa
from alembic import op

revision = "h1attr"
down_revision = "o2alert"
branch_labels = None
depends_on = None

TABLE = "product_attribute_values"
POLICY = "tenant_isolation"
GUC = "app.current_org"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("attribute_value_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("product_id", sa.String(length=64), nullable=False),
        sa.Column("attribute_key", sa.String(length=64), nullable=False),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(length=255), nullable=True),
        sa.Column("original_value", sa.String(length=255), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("decoder_version", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_product_attribute_values_organization_id", TABLE,
                    ["organization_id"])
    op.create_index("ix_product_attribute_values_product_id", TABLE, ["product_id"])
    op.create_index("ix_pav_org_attr", TABLE, ["organization_id", "attribute_key"])
    op.create_index("ix_pav_org_product", TABLE, ["organization_id", "product_id"])
    op.create_index(
        "uq_product_attribute_live", TABLE,
        ["organization_id", "product_id", "attribute_key", "source_kind"],
        unique=True, postgresql_where=sa.text("superseded_at IS NULL"),
        sqlite_where=sa.text("superseded_at IS NULL"))

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Policies exist on PostgreSQL only. A no-op is the honest form of that
        # on SQLite rather than an emulation that would give the suite a false
        # sense of coverage — the same reasoning d1rls states at length.
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY} ON {TABLE} "
        f"USING (organization_id = current_setting('{GUC}', true)) "
        f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.drop_table(TABLE)
