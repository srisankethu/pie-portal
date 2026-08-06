"""The deploy runbook's risk analysis.

Worth testing precisely because its only value is being trusted. A risk column
that flags every additive migration teaches the reader to skip it, and a column
nobody reads is worse than no column — they would at least have opened the
file.

So both directions are asserted against the repository's own migrations: the
one that genuinely rewrites sixteen tables must be flagged, and the purely
additive ones must not be.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

# The script lives in scripts/, outside the app package, because it runs in CI
# against a checkout rather than inside the application.
_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))

from deploy_runbook import _upgrade_risks, describe, render  # noqa: E402

VERSIONS = _ROOT / "backend" / "alembic" / "versions"


def _migration(prefix: str) -> pathlib.Path:
    return next(VERSIONS.glob(f"{prefix}*"))


def _ops(prefix: str) -> set[str]:
    return {op for op, _why in describe(_migration(prefix))["risks"]}


# ── the two directions that matter ──────────────────────────────────────────
def test_the_migration_that_rewrites_sixteen_tables_is_flagged():
    """`c8f5a1e73b29` is the one this repository has been telling people to
    back up before, in every deploy note since it landed."""
    assert "batch_alter_table" in _ops("c8f5a1e73b29")


@pytest.mark.parametrize("prefix", ["cca6781337ea", "e2b4c8f19d73",
                                    "b6c3f80a2d51", "d9a4c60e7b18",
                                    "e5b1f30d92c7", "f4d8a1c69e03"])
def test_a_purely_additive_migration_is_not_flagged(prefix):
    """Every one of these creates tables or adds nullable/defaulted columns.
    Calling any of them destructive is the false positive that kills the
    practice."""
    assert _ops(prefix) == set(), prefix


def test_a_batch_block_that_only_creates_an_index_is_not_a_rewrite():
    """`f4d8a1c69e03` uses batch_alter_table for exactly that. Alembic
    recreates a table only when the operation requires it, and treating every
    batch block as a rewrite flagged this one before the analysis was scoped."""
    text = _migration("f4d8a1c69e03").read_text()
    assert "batch_alter_table" in text
    assert "batch_alter_table" not in dict(_upgrade_risks(text))


# ── the analysis reads upgrade(), not the file ──────────────────────────────
def test_a_drop_in_downgrade_is_the_undo_and_is_not_flagged():
    """Almost every additive migration drops what it added, in downgrade().
    Grepping the file would flag all of them."""
    risks = _upgrade_risks('''
def upgrade() -> None:
    op.create_table("t", sa.Column("a", sa.String()))


def downgrade() -> None:
    op.drop_table("t")
''')
    assert risks == []


def test_a_drop_named_in_a_docstring_is_not_flagged():
    """The historical false positive with this kind of check: prose explaining
    why a migration does *not* drop anything setting off the alarm."""
    assert _upgrade_risks('''
"""Adds a column. Deliberately does not drop_table anything."""


def upgrade() -> None:
    op.add_column("t", sa.Column("a", sa.String(), nullable=True))
''') == []


def test_a_real_drop_in_upgrade_is_flagged():
    assert "drop_table" in dict(_upgrade_risks('''
def upgrade() -> None:
    op.drop_table("customers")
'''))


def test_a_not_null_column_with_no_default_is_flagged():
    """It succeeds on an empty test database and fails on any populated one —
    which is every production database, and the exact shape of §4's incident."""
    risks = dict(_upgrade_risks('''
def upgrade() -> None:
    op.add_column("t", sa.Column("a", sa.String(), nullable=False))
'''))
    assert "add_column" in risks
    assert "server_default" in risks["add_column"]


def test_a_not_null_column_with_a_default_is_fine():
    assert _upgrade_risks('''
def upgrade() -> None:
    op.add_column("t", sa.Column("a", sa.Integer(), nullable=False,
                                 server_default="0"))
''') == []


def test_a_file_that_does_not_parse_says_so_rather_than_passing():
    """Silence from a broken analyser reads exactly like a clean bill."""
    assert dict(_upgrade_risks("def upgrade(: pass"))["unparseable"]


# ── the rendered output ─────────────────────────────────────────────────────
#
# Rendered from made-up releases rather than from this repository's history: a
# rebase or a shallow CI clone would move real SHAs under the test, and a test
# that fails for that reason teaches people to skip it.

_ADDITIVE = [{"revision": "aaa1", "summary": "Adds a column.", "risks": []}]
_RISKY = [{"revision": "bbb2", "summary": "Rewrites sixteen tables.",
           "risks": [("batch_alter_table", "rewrites the table")]}]


def test_a_range_with_no_migrations_says_the_deploy_is_code_only():
    text = render([])
    assert "No migrations in this range" in text
    assert "back up" not in text.lower()


def test_an_additive_release_does_not_demand_a_backup():
    """Demanding one every time is how a runbook stops being read."""
    text = render(_ADDITIVE)
    assert "All are additive" in text
    assert "pg_dump" not in text


def test_a_risky_release_puts_the_backup_before_the_migrate():
    """Order is the whole point of a runbook. A backup step printed after the
    migration is a backup nobody takes."""
    text = render(_RISKY)
    assert "back up first" in text
    assert text.index("pg_dump") < text.index("alembic upgrade head")


def test_the_runbook_never_claims_to_know_where_production_is():
    """Assuming production's revision is precisely the mistake §4 is about."""
    text = render(_RISKY)
    assert "/api/health" in text
    assert "UNSTAMPED" in text


def test_an_edited_released_migration_is_called_out_at_the_top():
    """Two databases that ran "the same" revision now differ, and nothing can
    say which is which. It belongs above the instructions, not in them."""
    text = render(_ADDITIVE, edited=["cca6781337ea_supply.py"])
    assert "CAUTION" in text
    assert text.index("CAUTION") < text.index("| Revision |")
    assert "cca6781337ea_supply.py" in text
