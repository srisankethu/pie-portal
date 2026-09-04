"""Say when somebody was told an enquiry arrived, so nobody is told twice.

The demo-request form has always written its rows and `python -m app.contact
list` has always printed them. Between those two facts sat the whole defect:
nothing said an enquiry had *arrived*, so the only thing standing between a
buyer and silence was somebody remembering to run a command. `app/contact.py`'s
own docstring calls a queue nobody reads a form that lies.

The fix is a scheduled alert, and this column is what stops that alert becoming
the same problem in a new costume. Without it every run would re-announce the
whole waiting backlog — twice a day, the same names — which is an alert people
learn to skip. With it, a row is announced once: `contact alert` sends the rows
where this is null and stamps them.

Stamped only on a delivery that succeeded, so a webhook that was down leaves
the rows unannounced and the next run retries them. What can be lost is an
announcement, never an enquiry — the row is in `contact_requests` either way
and every existing reader still sees it.

Nullable with no backfill, deliberately. Every row written before this reads as
"never announced", which is true and is also the useful answer: the first run
after deploying reports the genuine backlog rather than starting from silence.
If that backlog is large, `contact alert --mark-only` stamps it without sending.

Additive: one nullable column, no stored value changes, and a database that has
run this still serves the previous code.

Revision ID: o2alert
Revises: o1op
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "o2alert"
down_revision = "o1op"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contact_requests",
                  sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))
    # Indexed because the alert's only query is "where this is null", which runs
    # on a schedule forever against a table that only grows.
    op.create_index("ix_contact_requests_notified_at", "contact_requests",
                    ["notified_at"])


def downgrade() -> None:
    op.drop_index("ix_contact_requests_notified_at", table_name="contact_requests")
    op.drop_column("contact_requests", "notified_at")
