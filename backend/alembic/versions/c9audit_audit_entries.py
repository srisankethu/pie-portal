"""A hash-chained, principal-bearing audit log, ordered by the database.

The platform's claim is that ``commercial/`` computes every number
deterministically and stamps a versioned policy hash on it. Nothing recorded who
*acted*. Six append-only surfaces existed and not one carried a principal, so
"who saw this customer's economics, when, under which policy, and what did the
model say?" had no answer — and audit coverage cannot be backfilled, so each day
without this table is permanently unauditable.

``audit_entries`` is one chain per organization. Each row names the previous
row's hash, and its own hash is an HMAC over a canonical rendering of its body,
keyed by the deployment secret — so editing one row invalidates it and every row
after it, and re-signing the lot needs more than database access.

**The ordering is the constraint, not a lock.** ``b8lease`` added a process lease
and said in its own docstring that it is not sufficient for this: it has no fence
token, so a leader can hold an unexpired claim while frozen and resume after
another worker has taken it. So the position is enforced where the write lands.
``uq_audit_entry_org_seq`` refuses two entries at one position;
``uq_audit_entry_org_prev`` refuses two entries naming one predecessor, which is
what a fork *is* rather than what it follows from. The loser of a race gets an
IntegrityError, re-reads the head and retries — the ``a7syncguard`` idiom, where
the database refuses rather than a process-local lock being trusted.

``prev_hash`` is sixty-four zeros for the first entry rather than NULL: NULL is
not equal to NULL, so a nullable column would let a unique index admit two
genesis entries.

Revision ID: c9audit
Revises: b8lease
Create Date: 2026-08-23
"""
from alembic import op
import sqlalchemy as sa

revision = "c9audit"
down_revision = "b8lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns written out literally rather than imported from the models (§4):
    # this runs against schemas from months ago, and the models describe today.
    op.create_table(
        "audit_entries",
        sa.Column("entry_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        # Position in this organization's chain, 1-based and dense.
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        # Null where no account acted: a failed sign-in against an address that
        # has none, or work no person asked for.
        sa.Column("actor_user_id", sa.String(length=64), nullable=True),
        sa.Column("actor_label", sa.String(length=255), nullable=False),
        # The role held at the time. A later promotion must not rewrite what
        # authority a past act carried.
        sa.Column("actor_role", sa.String(length=32), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        # ``ci_…`` or ``th_…``. §1: different stamps, and the prefix is what
        # keeps them from being read as one.
        sa.Column("thresholds_version", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("entry_id"),
        # The whole ordering argument. Not partial — every row here is live
        # forever, and an entry that could be superseded could be edited.
        sa.UniqueConstraint("organization_id", "seq",
                            name="uq_audit_entry_org_seq"),
        sa.UniqueConstraint("organization_id", "prev_hash",
                            name="uq_audit_entry_org_prev"),
    )
    op.create_index("ix_audit_entries_organization_id", "audit_entries",
                    ["organization_id"])
    op.create_index("ix_audit_entries_actor_user_id", "audit_entries",
                    ["actor_user_id"])
    op.create_index("ix_audit_entries_subject_id", "audit_entries",
                    ["subject_id"])
    op.create_index("ix_audit_entries_org_seq", "audit_entries",
                    ["organization_id", "seq"])
    op.create_index("ix_audit_entries_org_action", "audit_entries",
                    ["organization_id", "action"])
    # The anchor against tail truncation. Separate from audit_entries on
    # purpose: a DELETE that shortens the chain must not be able to take its own
    # evidence with it in the same statement.
    op.create_table(
        "audit_chain_heads",
        sa.Column("organization_id", sa.String(length=64), primary_key=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_entries_org_at", "audit_entries",
                    ["organization_id", "at"])


def downgrade() -> None:
    # Dropping this destroys history that cannot be rebuilt — the point of the
    # table is that nothing else holds what it holds. The downgrade exists so
    # the revision is reversible in a development database and for no other
    # reason; take a copy of the table before running it anywhere real.
    op.drop_table("audit_chain_heads")
    op.drop_index("ix_audit_entries_org_at", table_name="audit_entries")
    op.drop_index("ix_audit_entries_org_action", table_name="audit_entries")
    op.drop_index("ix_audit_entries_org_seq", table_name="audit_entries")
    op.drop_index("ix_audit_entries_subject_id", table_name="audit_entries")
    op.drop_index("ix_audit_entries_actor_user_id", table_name="audit_entries")
    op.drop_index("ix_audit_entries_organization_id", table_name="audit_entries")
    op.drop_table("audit_entries")
