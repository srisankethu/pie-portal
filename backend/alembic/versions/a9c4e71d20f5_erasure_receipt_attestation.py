"""The erasure receipt carries its own attestation.

The receipt used to claim ``method: "data key destroyed (crypto-shredding)"``
as a constant in code, while ``trust/vault.py`` conceded that ``customers.name``
and ``products.name`` stay plaintext — a signed document overstating what the
operation did. The honest receipt enumerates what key destruction reached (the
two DEK-encrypted field classes) and what it could not touch, and that
enumeration is a fact about the moment of erasure, so it is stored on the row
and covered by the signature rather than re-read from today's code — which
would change an old receipt's story, or break its verification, whenever the
code's list was edited.

NOT NULL with a ``'{}'`` server default so the revision stands alone on a
database that already holds receipt rows. Any such pre-existing receipt was
signed over the old body shape and will report as unverifiable under the new
one; that is the honest answer — its signature never covered the claims the
platform now makes — and no deployed database is known to hold one.

Revision ID: a9c4e71d20f5
Revises: a7c3e91b02d8
"""
from alembic import op
import sqlalchemy as sa

revision = "a9c4e71d20f5"
down_revision = "a7c3e91b02d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "erasure_receipts",
        sa.Column("attestation", sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("erasure_receipts", "attestation")
