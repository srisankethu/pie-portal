"""A connected company records the currency it keeps its books in.

``Organization.currency`` is what this platform's totals are spelled in. It was
also, until now, the only currency anywhere in the schema — no invoice, bill,
sales line or cost line carries one. That is safe exactly while every connected
company trades in the same currency, and nothing checked that they did.

The gap is reachable through the ordinary flow rather than hypothetical. One
organization deliberately holds several connections and rolls revenue and margin
up across them; ``ZohoApiSource.ping`` has always read the company's
``currency_code`` and the connections screen has always displayed it; and it was
never stored, never compared, and thrown away on every check. Connect a company
that keeps its books in another currency and ``economics.line_economics``
subtracts a cost denominated in one from revenue denominated in another and
stamps a clean ``thresholds_version`` on the result.

This column is the fact the comparison needs. It is **nullable and nothing is
backfilled**: a connection nobody has checked since this shipped has not told us
its currency, and writing the organization's own currency into it would assert
agreement that was never observed — which is exactly the benign default the
platform's §1 forbids. NULL reads as "not checked", and the next check fills it.

Deliberately not on ``Organization``: that is the currency of the roll-up, and
this is a property of one book among several. Collapsing the two is what makes
the disagreement invisible.

Revision ID: c1e4f80b7a92
Revises: 2beaeba8b59c
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = "c1e4f80b7a92"
down_revision = "2beaeba8b59c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns written out literally rather than imported from the model: this
    # runs against schemas from months ago and the model describes today.
    op.add_column("zoho_connections",
                  sa.Column("base_currency", sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column("zoho_connections", "base_currency")
