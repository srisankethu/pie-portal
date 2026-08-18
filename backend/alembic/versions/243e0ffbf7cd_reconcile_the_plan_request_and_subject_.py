"""Reconcile two release branches that both grew from y5runlog.

A merge revision with no schema in it, which is the whole point. `main` was
carrying **two alembic heads**: `t13plan_requests` (the plan-change queue) and
`z6subject` (the widened subject key) were written against the same parent and
merged a day apart, so `alembic upgrade head` failed on main with "Multiple head
revisions are present" — a deploy would have died on it, and nothing in the gate
catches a fork that only exists once both sides have landed.

Neither is edited. Both were released, and CLAUDE.md §4 is explicit that an
edited migration means two databases that ran "the same" revision have different
schemas and nothing can tell you which is which. So this is the reconciliation
`alembic merge` exists for: it creates a single head, changes no table, and
leaves both histories intact.

Empty `upgrade`/`downgrade` are correct here and not an omission — a merge
revision's only content is the shape of the graph.

Revision ID: 243e0ffbf7cd
Revises: t13plan_requests, z6subject
Create Date: 2026-08-18 13:48:47.196244
"""
from alembic import op
import sqlalchemy as sa


revision = '243e0ffbf7cd'
down_revision = ('t13plan_requests', 'z6subject')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
