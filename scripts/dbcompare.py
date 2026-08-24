"""What "the data survived" means — one definition, shared by the two tools that check.

Two scripts have to answer the same question about two databases holding the
same rows: ``migrate_to_postgres.py`` (SQLite → Postgres, once) and
``restore_drill.py`` (Postgres → pg_dump → Postgres, every gate run). Left
apart, each would grow its own idea of which columns are money and how a
timestamp compares across a dialect — and the two would agree on every row
either was tested with, right up to the first one they didn't.

Three ideas, and each is here because getting it subtly wrong reads as success:

**Which columns are money.** ``Numeric`` and not ``Float``. ``Float`` is a
``Numeric`` subclass, so the obvious ``isinstance`` check sweeps it in — and a
float Σ is approximate by nature, so an exact comparison over one cries wolf on
a database nothing is wrong with. A check that is usually wrong is a check
people learn to scroll past.

**How money is summed.** In Python, over the exact values, as ``Decimal``.
Never by SQLite, whose ``SUM`` is float arithmetic and would produce a phantom
mismatch against Postgres' exact numeric Σ.

**How a row compares across backends.** A naive datetime is UTC in this codebase
(``app.clock``); an aware one from Postgres is the same instant with the offset
attached. Comparing them raw reports a difference that is a rendering, not a
change.

Nothing here prints a value. Callers report *whether* a Σ matched and name the
column when it did not — a money column's Σ is cost for several of these tables,
and CLAUDE.md §1 keeps cost out of any output a gate writes to a terminal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import Float, Numeric
from sqlalchemy.schema import Table


def money_columns(table: Table) -> list[str]:
    """The exact-decimal columns of ``table`` — money, and never a float."""
    return [c.name for c in table.columns
            if isinstance(c.type, Numeric) and not isinstance(c.type, Float)]


def decimal_sum(rows: Iterable[Mapping[str, Any]],
                columns: Iterable[str]) -> dict[str, Optional[Decimal]]:
    """Exact Σ per column, in Python. ``None`` for a column with no value at all.

    ``None`` rather than zero, because "every row was NULL" and "the values
    cancelled to nothing" are different facts and only one of them is a reason
    to look.
    """
    totals: dict[str, Optional[Decimal]] = dict.fromkeys(columns)
    for row in rows:
        for name in totals:
            value = row[name]
            if value is None:
                continue
            value = value if isinstance(value, Decimal) else Decimal(str(value))
            totals[name] = (totals[name] or Decimal(0)) + value
    return totals


def aware(value: Any) -> Any:
    """Timestamps comparable across backends: naive means UTC here (app.clock)."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def normalise(row: Mapping[str, Any]) -> dict[str, Any]:
    """One row, with every timestamp on the UTC line."""
    return {k: aware(v) for k, v in row.items()}
