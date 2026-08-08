"""vendor_scheme_slabs — what hitting a principal's target actually pays

Revision ID: d4e7b18f2a95
Revises: b2d95e11c74a
Create Date: 2026-08-08

``vendor_targets`` holds what a principal expects — "buy fifty lakh this
quarter" — and stops there. The half that decides whether anybody chases it is
the rebate: two to three percent of purchases on this book, which is real money
and, missed repeatedly, is the distributorship. Nothing in Zoho holds a scheme
any more than it holds a target, so this is typed rather than synced, it is the
only copy, and it must survive a complete re-sync — the same reason
``vendor_targets``, ``item_category_overrides`` and ``vendor_payment_terms`` are
their own tables.

**A row per slab, and no header table.** The ordinary scheme here is not one
number: it is 2% at forty lakh and 3% at sixty. Slabs are therefore the model
and a flat percentage is the one-slab case, so there is one arithmetic rather
than a flat path and a slab path that could settle differently on the same
scheme. A header row would carry only what the target already carries.

Keyed to ``vendor_targets.target_id`` rather than to the vendor: the vendor, the
period and the basis are stated once, on the target, and a scheme that repeated
them could disagree with it about which quarter it belongs to. Unique on
(target, threshold) — two rungs starting at the same amount make "which rate
applies" unanswerable.

``rate`` is ``Numeric(9, 6)`` and holds a **ratio** (``0.025``), the convention
every rate in this platform follows. Six decimal places because a slab rate of
2.75% is ``0.0275`` and a principal occasionally splits a quarter point further;
nothing here needs more, and the validator in
``commercial/insight/schemes.py`` quantises to exactly this so what is computed
from is what was stored.

Columns are written out literally rather than imported from the models, per the
rule that a migration runs against schemas from months ago while models describe
today. Nothing is backfilled and nothing needs to be: no scheme exists yet, and
the absence of slabs for a target means "a number with no rebate attached",
which is exactly the behaviour before this revision.
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e7b18f2a95"
down_revision = "b2d95e11c74a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_scheme_slabs",
        sa.Column("slab_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64),
                  sa.ForeignKey("vendor_targets.target_id"), nullable=False),
        sa.Column("threshold_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("rate", sa.Numeric(9, 6), nullable=False),
        sa.Column("set_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target_id", "threshold_amount",
                            name="uq_vendor_scheme_slab_threshold"),
    )
    op.create_index("ix_vendor_scheme_slabs_organization_id",
                    "vendor_scheme_slabs", ["organization_id"])
    op.create_index("ix_vendor_scheme_slabs_target_id",
                    "vendor_scheme_slabs", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_vendor_scheme_slabs_target_id",
                  table_name="vendor_scheme_slabs")
    op.drop_index("ix_vendor_scheme_slabs_organization_id",
                  table_name="vendor_scheme_slabs")
    op.drop_table("vendor_scheme_slabs")
