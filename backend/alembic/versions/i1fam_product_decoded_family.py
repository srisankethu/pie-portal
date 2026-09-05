"""Products: the family the parser routed the name to.

A classification rather than a measurement, and deliberately NOT a row in
``product_attribute_values``. The engine emits ``product_family`` on every
routed row including ones it understood nothing else about, so counted as an
attribute it would report Phase 1's coverage as ~100% on day one — which is why
``attributes.ROUTE_FIELDS`` refuses it.

It is added here because refusing to STORE it at all turned out to be unsafe in
a way refusing to COUNT it is not. ``product_family`` is the strongest hard gate
the equivalence engine has. Once this organization's own products became a
candidate pool, records without it matched across families: a real 11.1 mm
drill came back rank 0, scored 1.0 and marked verified, for the request
"endmill 11.1mm 4 flute". Two correct decisions met in one field, and the
resolution is that they were about different things — do not count it, do store
it.

Nullable with no backfill: the next decoration run fills it, and NULL means the
router placed the name nowhere. A NULL here is gated out rather than compared
freely, because "no family" must not read as "any family".

Revision ID: i1fam
Revises: h1attr
Create Date: 2026-08-30
"""
import sqlalchemy as sa
from alembic import op

revision = "i1fam"
down_revision = "h1attr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products",
                  sa.Column("decoded_family", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "decoded_family")
