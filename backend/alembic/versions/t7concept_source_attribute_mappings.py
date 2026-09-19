"""What an organization's own ERP fields mean, declared once and superseded.

One table, ``source_attribute_mappings``. ``s6srcattr`` gave every entity a
place to carry the source's own custom fields verbatim, and said in three
docstrings that mapping one of those keys onto a concept this platform reasons
with is "a separate, versioned exercise". This is that exercise: the row that
says ``UD_Field_07`` carries ``quote_intent`` and that ``"TENDER"`` means
``TENDER``, so that no rule downstream ever names the field.

**Superseded, never mutated**, which is why the shape is what it is. A quote
diagnosed in March under one reading of a field has to stay explainable when
somebody re-maps it in June, and an UPDATE in place destroys exactly that —
``confirmed_code_mappings`` states the same reason for the same reason.
``effective_from`` is when a reading became the organization's,
``superseded_at`` is when a later one replaced it (set to *that* declaration's
``effective_from``, so the intervals tile), and ``recorded_at`` is when the
platform wrote it down. Which reading was in force at a date is then one
predicate, which is how ``knowable_by`` is asked everywhere else in this engine.

``uq_source_attribute_mapping_live`` is a partial unique index over the live
rows only — the ``uq_inbound_line_disposition_live`` idiom, chosen for the same
reason: one live reading per concept has to be a database guarantee, and a
plain unique constraint would refuse the correction that supersession exists to
allow. Both backends support partial indexes, so this needs no dialect branch
beyond naming the predicate twice.

**Policied from the first row.** A cross-tenant read here is small but it is a
map of how a competitor configured their ERP and which of their fields carry
commercial intent — and ``l1grp`` set the standing rule that a table created
today has no window in which it is uncovered. So the policy lands in the
revision that creates the table rather than in a later ``*rls`` one, and
``test_row_level_security.EXPECTED_POLICIED`` names it in the same commit.

Additive. A database that has run this serves the previous code unchanged;
nothing reads the table until an organization declares something, and an
organization that has declared nothing reads exactly as it did before — every
concept "not declared", which is the honest answer and not a benign default.

Revision ID: t7concept
Revises: s6srcattr
Create Date: 2026-09-19
"""
import sqlalchemy as sa
from alembic import op

revision = "t7concept"
down_revision = "s6srcattr"
branch_labels = None
depends_on = None

#: Written out literally (§4): each revision stands on its own, and a migration
#: that imported ``app.tenancy`` or a predecessor would fail on the one
#: deployment that ran them out of order.
GUC = "app.current_org"
POLICY = "tenant_isolation"
TABLE = "source_attribute_mappings"
LIVE = "superseded_at IS NULL"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("mapping_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("connector", sa.String(length=32), nullable=False),
        sa.Column("entity", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("pie_concept", sa.String(length=32), nullable=False),
        sa.Column("value_map", sa.JSON(), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("declared_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("mapping_id"),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index("uq_source_attribute_mapping_live",
                              ["organization_id", "connector", "entity",
                               "pie_concept"],
                              unique=True,
                              sqlite_where=sa.text(LIVE),
                              postgresql_where=sa.text(LIVE))
        batch_op.create_index("ix_source_attribute_mapping_lookup",
                              ["organization_id", "connector", "entity",
                               "effective_from"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_source_attribute_mappings_organization_id"),
            ["organization_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_source_attribute_mappings_recorded_at"),
            ["recorded_at"], unique=False)

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return                      # SQLite has no policies; see d1rls
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
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_source_attribute_mappings_recorded_at"))
        batch_op.drop_index(
            batch_op.f("ix_source_attribute_mappings_organization_id"))
        batch_op.drop_index("ix_source_attribute_mapping_lookup")
        batch_op.drop_index("uq_source_attribute_mapping_live")
    op.drop_table(TABLE)
