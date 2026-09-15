"""How the last attempt to announce an enquiry went, and what the provider called it.

``notified_at`` (revision ``o2alert``) is the *webhook* channel's once-only
stamp, and this revision does not touch it or change its meaning. Email is a
second channel, added beside it, and it gets a key of its own rather than
sharing that one.

Sharing would have been the obvious thing and it is the wrong thing: a
deployment that turned mail on would quietly stop getting its Slack message,
because the row would already be stamped by the time the sweep composed one. A
channel that switches another one off when you enable it is a surprise nobody
debugs until the week it matters. So the two are independent — either, both or
neither may be configured, and each announces every enquiry exactly once.

``notification_status`` is therefore the email's idempotency key: a message goes
out for any row that is not SENT, which covers both "never attempted" (NULL) and
"tried and failed" (FAILED), and those are the rows a retry should pick up.
Beside it sit the two columns that answer the question somebody asks the morning
after — *the enquiry is here and no mail arrived, did we not try or did the
provider refuse?*:

- ``notification_status``  — NULL (never attempted), SENT, or FAILED.
- ``notification_message_id`` — the provider's own id for the message, which is
  the only handle that ties a row in this database to a delivery in Resend's
  dashboard. Without it "we sent it" is an assertion nobody can check.
- ``notification_error`` — why the last attempt failed, as an exception class
  name or a short reason. Never a provider message and never a key: an SDK
  error string can quote the request it failed on, and that request carries the
  credential. ``app/mailer.py`` truncates to this width before it is stored.

Nullable with no backfill, and the two facts that follow from it are both the
ones we want. Every row written before this reads "never attempted", which is
exactly true — the mailer did not exist. And because not-SENT is what the sweep
sends for, the first run after a deployment configures mail emails the genuine
backlog rather than starting from silence, which is the same choice ``o2alert``
made for the webhook.

Additive — three nullable columns, no stored value changes, no index. A
database that has run this still serves the previous code, and the previous
code still serves a database that has not.

Revision ID: m1mail
Revises: l1grp
Create Date: 2026-09-15
"""
import sqlalchemy as sa
from alembic import op

revision = "m1mail"
down_revision = "l1grp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns written out literally rather than imported from the models: this
    # runs against schemas from months ago, and models describe today.
    with op.batch_alter_table("contact_requests") as batch:
        batch.add_column(
            sa.Column("notification_status", sa.String(16), nullable=True))
        batch.add_column(
            sa.Column("notification_message_id", sa.String(255), nullable=True))
        batch.add_column(
            sa.Column("notification_error", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("contact_requests") as batch:
        batch.drop_column("notification_error")
        batch.drop_column("notification_message_id")
        batch.drop_column("notification_status")
