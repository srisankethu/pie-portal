"""Merge inbound-line capture with the background queue.

Two branches each added a migration from ``z6subject`` — the inbound enquiry
tables on one side, ``queued_messages`` and ``process_leases`` on the other —
so the history had two heads. This is the join, made the way CLAUDE.md §4 says
to make it: a merge revision, never by editing ``down_revision`` on a released
one. Two databases that ran "the same" revision and disagree about their schema
is the state nothing can diagnose afterwards.

Empty on purpose. The two branches touch disjoint tables; nothing needs
reconciling, and the merge exists to give the chain a single head again.

Revision ID: cdc08a6ca322
Revises: a7inbound, b8lease
Create Date: 2026-08-23
"""
revision = 'cdc08a6ca322'
down_revision = ('a7inbound', 'b8lease')
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nothing to do: this revision only rejoins the history."""


def downgrade() -> None:
    """Likewise — splitting the head back apart is the caller's business."""
