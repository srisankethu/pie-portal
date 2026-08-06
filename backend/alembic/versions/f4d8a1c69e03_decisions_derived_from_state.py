"""A decision can now come from Business State, and says what it is worth.

Six additive columns on ``decisions``. No new table, deliberately: a second
"decision opportunity" table would mean two lifecycles to keep in step, two
queues, and two places a role-scoping bug can hide. Everything downstream of a
decision — the queue, the card, the human actions, the scoping, the audit —
already works, and this lets a second producer use it.

``origin`` discriminates the two producers. Existing rows are ``SIGNAL``, which
is what they are: a detector over sales and cost lines, interpreted by the AI
layer. ``STATE`` rows are folded from ``business_states`` and never touch it.

``state_keys`` and ``state_as_of`` are the join that closes the traceability
chain. Decision → ERP already worked through ``evidence_refs``; state →
transition → event has worked since the state engine landed. This is the hop
between them, and without it the chain existed in two halves that could not be
walked end to end.

Purely additive and reversible. Existing rows are unaffected — they get
``SIGNAL``, an empty impact and no state keys, which is honest: they were not
computed from state and backfilling a provenance nobody recorded would be
inventing history.

Revision ID: f4d8a1c69e03
Revises: e5b1f30d92c7
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa

revision = "f4d8a1c69e03"
down_revision = "e5b1f30d92c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decisions",
                  sa.Column("origin", sa.String(length=16), nullable=False,
                            server_default="SIGNAL"))
    # Not nullable with an empty default, like every other JSON collection on
    # this table: a reader forced to tell "no impact" from NULL will get it
    # wrong somewhere.
    op.add_column("decisions",
                  sa.Column("impact", sa.JSON(), nullable=False,
                            server_default="{}"))
    op.add_column("decisions",
                  sa.Column("state_keys", sa.JSON(), nullable=False,
                            server_default="[]"))
    op.add_column("decisions",
                  sa.Column("state_as_of", sa.Date(), nullable=True))
    op.add_column("decisions",
                  sa.Column("rationale", sa.String(length=2048), nullable=True))
    op.add_column("decisions",
                  sa.Column("actions", sa.JSON(), nullable=False,
                            server_default="[]"))
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_decisions_origin"),
                              ["origin"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_decisions_origin"))
    for name in ("actions", "rationale", "state_as_of", "state_keys",
                 "impact", "origin"):
        op.drop_column("decisions", name)
