"""Named sets of customers, vendors and items, so a question can be asked of a set.

The platform could answer "how is this account doing" and "how is the book
doing" and nothing in between. Every question a distributor actually asks sits
in that gap — the PSU accounts, the aerospace book, one principal's items, the
vendors on 90-day terms — and answering it meant scanning a directory by eye.

Two tables. ``entity_groups`` is the definition somebody drew; its members are
in ``entity_group_members``. Neither is derived: a full re-sync rebuilds every
customer, vendor and product row from the source system and would delete a
grouping typed by hand if it lived as a column on one of them, which is the
argument ``item_category_overrides`` already makes and the reason this is not
``products.group_id``.

**The policies ship in this revision rather than in a later ``*rls`` one.** The
``d1``–``d5`` revisions retrofit policies onto tables that existed first, and
that ordering is what made them necessary. A table created today has no such
excuse: between creating it and policying it there is a window where a
cross-tenant read is not a leak only because nothing has written a row yet, and
a revision interrupted halfway leaves exactly that state on a real deployment.
A group names this book's customers and how the business segments them, so a
cross-tenant read is a leak on ``d2rls``'s rule.

Two tables, no columns changed elsewhere, nothing backfilled. A database that
has run this serves the previous code unchanged.

Revision ID: l1grp
Revises: k2form
Create Date: 2026-09-13
"""
import sqlalchemy as sa
from alembic import op

revision = "l1grp"
down_revision = "k2form"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` (§4): this
#: runs against schemas from months ago, and each revision stands on its own.
GUC = "app.current_org"
POLICY = "tenant_isolation"
TABLES = ("entity_groups", "entity_group_members")


def upgrade() -> None:
    # Columns written out literally rather than imported from
    # ``app.domain.models`` (§4): the models describe today.
    op.create_table(
        "entity_groups",
        sa.Column("group_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("entity_kind", sa.String(32), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("membership", sa.String(16), nullable=False,
                  server_default="ROSTER"),
        sa.Column("rule", sa.JSON(), nullable=True),
        sa.Column("version", sa.String(64), nullable=False, server_default=""),
        sa.Column("visibility", sa.String(16), nullable=False,
                  server_default="OPERATIONAL"),
        sa.Column("boundary_refs", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(64), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "entity_kind", "slug",
                            name="uq_entity_group_slug"),
    )
    op.create_index("ix_entity_groups_organization_id", "entity_groups",
                    ["organization_id"])
    op.create_index("ix_entity_groups_entity_kind", "entity_groups", ["entity_kind"])

    op.create_table(
        "entity_group_members",
        sa.Column("membership_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64), nullable=False),
        sa.Column("group_id", sa.String(64),
                  sa.ForeignKey("entity_groups.group_id"), nullable=False),
        # No foreign key: this holds a customer_id, a vendor_id or a product_id
        # depending on the group's kind, so no one constraint can cover it. The
        # writer validates instead — see ``EntityGroupMember``'s docstring for
        # what that costs and why the trade was made.
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("added_by_user_id", sa.String(64), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "group_id", "entity_id",
                            name="uq_entity_group_member"),
    )
    op.create_index("ix_entity_group_members_organization_id",
                    "entity_group_members", ["organization_id"])
    op.create_index("ix_entity_group_members_group_id", "entity_group_members",
                    ["group_id"])
    op.create_index("ix_entity_group_member_group", "entity_group_members",
                    ["organization_id", "group_id"])
    op.create_index("ix_entity_group_member_entity", "entity_group_members",
                    ["organization_id", "entity_id"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no policies. A no-op is the honest form of that; see
        # ``d1rls`` for what it costs and why it is stated rather than emulated.
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} "
            f"USING (organization_id = current_setting('{GUC}', true)) "
            f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_entity_group_member_entity", table_name="entity_group_members")
    op.drop_index("ix_entity_group_member_group", table_name="entity_group_members")
    op.drop_index("ix_entity_group_members_group_id",
                  table_name="entity_group_members")
    op.drop_index("ix_entity_group_members_organization_id",
                  table_name="entity_group_members")
    op.drop_table("entity_group_members")
    op.drop_index("ix_entity_groups_entity_kind", table_name="entity_groups")
    op.drop_index("ix_entity_groups_organization_id", table_name="entity_groups")
    op.drop_table("entity_groups")
