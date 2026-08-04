"""Rename the currency-named policy override keys.

``min_material_gap_rupees`` and ``min_quote_exception_impact_rupees`` became
``min_material_gap`` and ``min_quote_exception_impact``: the thresholds carry a
currency of their own now, so the field name asserting one was both redundant
and wrong for any organization not trading in rupees.

This has to be a data migration rather than a rename-in-code, because the
overrides are a JSON blob and ``policy.load_for_org`` drops any key that is not
in ``EDITABLE``. Without it, an organization that had edited either number would
silently fall back to the default on the next request — the policy screen would
show the default, no error would be raised anywhere, and the only visible
symptom would be quotes being judged against a floor nobody chose.

Reversible: ``downgrade`` puts the old keys back.

Revision ID: a7c02f5b8e14
Revises: f3d81a09c7b6
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "a7c02f5b8e14"
down_revision = "f3d81a09c7b6"
branch_labels = None
depends_on = None

RENAMES = {
    "min_material_gap_rupees": "min_material_gap",
    "min_quote_exception_impact_rupees": "min_quote_exception_impact",
}


def _rewrite(mapping: dict[str, str]) -> None:
    """Rewrite override keys in place, leaving every other key untouched."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT organization_id, overrides FROM commercial_policies")
    ).fetchall()

    for organization_id, overrides in rows:
        if not overrides:
            continue
        # SQLite hands back a JSON string; Postgres hands back a dict.
        current = json.loads(overrides) if isinstance(overrides, str) else dict(overrides)
        changed = False
        for old, new in mapping.items():
            if old in current:
                current[new] = current.pop(old)
                changed = True
        if changed:
            bind.execute(
                sa.text(
                    "UPDATE commercial_policies SET overrides = :o "
                    "WHERE organization_id = :id"
                ),
                {"o": json.dumps(current), "id": organization_id},
            )


def upgrade() -> None:
    _rewrite(RENAMES)


def downgrade() -> None:
    _rewrite({new: old for old, new in RENAMES.items()})
