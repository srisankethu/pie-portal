"""quote_loss_reason — why a quote was lost, from a countable list

Revision ID: c7e41b90d3aa
Revises: b2d95e11c74a
Create Date: 2026-08-08

The platform already recorded that a quote was lost and never recorded *why*,
so every loss looked the same: a row in a table with a note somebody may or may
not have typed. A note cannot be counted, and the question the owner asks —
"losing at 8% below my quote is a pricing problem, losing on delivery is a stock
problem, which is it" — is a counting question.

One nullable column on ``quote_outcomes``, holding a ``QuoteLossReason`` value.
On the outcome and not on ``quote_decisions``, which is append-only: why the
customer walked away is learned weeks after the price was set, and a table whose
whole value is that its rows never change is the wrong place to learn things.

Stored as a string rather than a database enum, matching ``status`` beside it
and ``basis`` on ``vendor_payment_terms``: adding a sixth reason should be a
code change, not a migration against a table whose values are read by name.

Nullable, and nothing is backfilled. A quote decided before this revision has no
recorded reason and inventing one would be fabricating evidence — the analysis
reports those under an explicit "not recorded" bucket instead. New losses are
refused without a reason at the API, so the bucket is closed rather than
growing.

Columns are written out literally rather than imported from the models, per the
rule that a migration runs against schemas from months ago while models describe
today.
"""
from alembic import op
import sqlalchemy as sa

revision = "c7e41b90d3aa"
down_revision = "0e8d9299b0c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quote_outcomes",
                  sa.Column("loss_reason", sa.String(length=32), nullable=True))
    op.create_index("ix_quote_outcomes_loss_reason", "quote_outcomes",
                    ["loss_reason"])


def downgrade() -> None:
    op.drop_index("ix_quote_outcomes_loss_reason", table_name="quote_outcomes")
    op.drop_column("quote_outcomes", "loss_reason")
