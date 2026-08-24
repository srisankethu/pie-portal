"""Rejoin the two heads main grew, so this schema has one history again.

Two branches landed on ``main`` from ``z6subject`` and neither knew about the
other: ``a7qdoc`` (quote documents) and ``a7inbound`` → ``a7queue`` →
``b8lease`` (inbound capture, the durable queue, the process lease). Alembic
does not pick between heads — ``upgrade head`` on a two-head history fails
outright — so ``main`` has not been able to migrate an empty database since,
and the branch merging into it inherits that.

Nothing to do: a merge revision exists to state that both parents are ancestors
of everything after it. Both sides created their own tables and neither touched
the other's, so there is no reconciliation to write out here — and a merge
revision that quietly *did* something would be the one nobody thinks to read.

Revision ID: e1heads
Revises: a7qdoc, b8lease
Create Date: 2026-08-24
"""

revision = "e1heads"
down_revision = ("a7qdoc", "b8lease")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
