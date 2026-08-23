#!/usr/bin/env python3
"""Move an existing SQLite deployment's data into PostgreSQL — safely, once.

    python3 scripts/migrate_to_postgres.py \
        --source backend/data/platform.db \
        --target "$DATABASE_URL"           # a postgresql+psycopg:// URL

What it does, in order:

1. **Backs up** the SQLite file (a plain copy beside it), then opens the
   original strictly read-only — the source cannot be modified, full stop.
2. **Refuses mismatches.** The source must be stamped at this codebase's
   Alembic head (upgrade it first with the code you are migrating from, or
   check out matching code). The target must be an empty database; it is then
   migrated to the same head through the real chain — Alembic remains the only
   thing that creates this schema (CLAUDE.md §4).
3. **Copies every table** in foreign-key order, through the ORM metadata's
   column types, in one target transaction. Timestamps are written under an
   explicit ``SET timezone = 'UTC'``: the app stores UTC (``app.clock``), and
   the session zone must never be allowed to reinterpret it.
4. **Resets sequences.** Every serial column is advanced past the copied rows;
   without this the first INSERT after migration collides with an existing id.
5. **Validates before committing**: per-table row counts, per-table sums of
   every Numeric column (money must survive to the paisa), and full
   representative-row comparisons. Any mismatch rolls the whole copy back —
   a target that fails validation is left with schema and zero rows, never a
   partial import.

Foreign-key integrity needs no separate pass: Postgres enforces every
constraint as rows are inserted, so a completed copy *is* the referential
check — SQLite ran with foreign keys off, so this is the first time some of
this data meets an enforcing database, and a violation aborts the run.

Rerunnable: because a failed run never commits and a fresh target is required,
you can run it against a copy of production as many times as it takes.

Derived rows (customer_item_metrics and friends) are copied like everything
else — but remember Zoho is the system of record for the read model: after
cutover, a full re-sync rebuilds those from source. What this tool is really
for is the state Zoho cannot rebuild: users, approvals, signals, decisions,
quote snapshots, the value ledger, the event log.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.engine import Engine, make_url  # noqa: E402

# The comparison vocabulary — which columns are money, how they are summed, and
# how a row compares across dialects. Shared with scripts/restore_drill.py so
# there is one definition of "the data survived" rather than two that agree
# until they don't.
from dbcompare import decimal_sum, money_columns, normalise  # noqa: E402


class MigrationRefused(RuntimeError):
    """A precondition failed; nothing was changed on the target."""


def _source_engine(path: Path) -> Engine:
    """The SQLite file, opened read-only at the driver level.

    ``mode=ro`` makes writing impossible rather than merely avoided — the
    difference between a promise and a guarantee.
    """
    return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true",
                         connect_args={"uri": True}, future=True)


def _backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.pre-postgres-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _require_head(engine: Engine, what: str) -> str:
    from app.migration_state import inspect_database

    state = inspect_database(engine)
    if state.state != "CURRENT":
        raise MigrationRefused(f"{what}: {state.summary}")
    return state.current or ""


def migrate(source_path: Path, target_url: str, *,
            backup: bool = True, sample_rows: int = 3) -> dict[str, Any]:
    """Copy, validate, and commit — or roll back. Returns the report."""
    from app.bootstrap import _alembic_config, _redact
    from app.db import Base
    from app.domain import models  # noqa: F401  (populate Base.metadata)

    if not source_path.exists():
        raise MigrationRefused(f"source database not found: {source_path}")
    url = make_url(target_url)
    if not url.drivername.startswith("postgresql"):
        raise MigrationRefused(
            f"target must be a postgresql+psycopg:// URL, got {url.drivername!r}")

    report: dict[str, Any] = {
        "source": str(source_path),
        "target": _redact(target_url),
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if backup:
        report["backup"] = str(_backup(source_path))

    source = _source_engine(source_path)
    target = create_engine(target_url, future=True)
    try:
        source_rev = _require_head(source, "source (SQLite)")

        # The target must be empty — this tool initialises a database, it does
        # not merge into one. Anything already there is somebody's data.
        from sqlalchemy import inspect as sa_inspect
        existing = sa_inspect(target).get_table_names()
        if existing:
            raise MigrationRefused(
                f"target already has {len(existing)} table(s) "
                f"({', '.join(sorted(existing)[:5])}…). This tool only migrates "
                f"into an empty database — create a fresh one and rerun.")

        from alembic import command
        command.upgrade(_alembic_config(target_url), "head")
        target_rev = _require_head(target, "target (Postgres, freshly migrated)")
        if target_rev != source_rev:
            raise MigrationRefused(
                f"revision mismatch after migrating the target: source is at "
                f"{source_rev}, target reached {target_rev}. The checkouts differ.")
        report["alembic_revision"] = source_rev

        tables = Base.metadata.sorted_tables  # parents before children
        counts: dict[str, dict[str, int]] = {}
        sums: dict[str, dict[str, list[str]]] = {}
        mismatches: list[str] = []

        with source.connect() as src, target.connect() as dst:
            # Naive datetimes leaving SQLite are UTC by convention; make the
            # session agree so timestamptz stores exactly that instant.
            dst.execute(text("SET timezone = 'UTC'"))

            for table in tables:
                money_cols = money_columns(table)
                src_sums: dict[str, Optional[Decimal]] = dict.fromkeys(money_cols)

                rows = src.execute(select(table)).mappings().all()
                if rows:
                    payload = [dict(r) for r in rows]
                    src_sums = decimal_sum(payload, money_cols)
                    for i in range(0, len(payload), 1000):
                        dst.execute(table.insert(), payload[i:i + 1000])
                n_target = dst.execute(
                    select(func.count()).select_from(table)).scalar_one()
                counts[table.name] = {"source": len(rows), "target": n_target}
                if len(rows) != n_target:
                    mismatches.append(
                        f"{table.name}: {len(rows)} source rows, {n_target} copied")

                # Money must survive to the paisa. The source side is summed in
                # Python over the exact values handed to the INSERT — never by
                # SQLite, whose SUM is float arithmetic and would produce a
                # phantom mismatch against Postgres' exact numeric Σ.
                for cname in money_cols:
                    d = dst.execute(
                        select(func.sum(table.c[cname]))).scalar()
                    d_dec = None if d is None else Decimal(str(d))
                    s_dec = src_sums[cname]
                    sums.setdefault(table.name, {})[cname] = [str(s_dec), str(d_dec)]
                    if s_dec != d_dec:
                        mismatches.append(
                            f"{table.name}.{cname}: Σ source {s_dec} ≠ Σ target {d_dec}")

            # Representative rows: the first few by primary key, compared
            # column-for-column. Counts can match while values rot; this can't.
            sampled = 0
            for table in tables:
                pk = list(table.primary_key.columns)
                if not pk or counts[table.name]["source"] == 0:
                    continue
                src_rows = src.execute(
                    select(table).order_by(*pk).limit(sample_rows)).mappings().all()
                dst_rows = dst.execute(
                    select(table).order_by(*pk).limit(sample_rows)).mappings().all()
                for a, b in zip(src_rows, dst_rows):
                    if normalise(dict(a)) != normalise(dict(b)):
                        mismatches.append(
                            f"{table.name}: representative row differs at "
                            f"pk={[a[c.name] for c in pk]}")
                sampled += len(src_rows)
            report["rows_sampled"] = sampled

            # Serial sequences: advance past the copied ids, or the first
            # insert after cutover collides with an existing primary key.
            serial_cols = dst.execute(text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND column_default LIKE 'nextval%'
            """)).all()
            reset = []
            for tname, cname in serial_cols:
                max_val = dst.execute(
                    text(f'SELECT max("{cname}") FROM "{tname}"')).scalar()
                if max_val is not None:
                    dst.execute(text(
                        "SELECT setval(pg_get_serial_sequence(:t, :c), :v)"),
                        {"t": f'public."{tname}"', "c": cname, "v": max_val})
                    reset.append(f"{tname}.{cname}→{max_val}")
            report["sequences_reset"] = reset

            report["tables"] = counts
            report["numeric_sums"] = sums
            report["mismatches"] = mismatches

            if mismatches:
                dst.rollback()
                report["result"] = "ROLLED BACK — validation failed"
            else:
                dst.commit()
                report["result"] = "COMMITTED"
        return report
    finally:
        source.dispose()
        target.dispose()


def _print_report(report: dict[str, Any]) -> None:
    print("── SQLite → PostgreSQL migration report ─────────────────────────")
    for key in ("source", "backup", "target", "alembic_revision", "started_utc"):
        if key in report:
            print(f"{key:>18}: {report[key]}")
    total_src = sum(t["source"] for t in report["tables"].values())
    nonempty = {n: t for n, t in report["tables"].items() if t["source"]}
    print(f"{'rows copied':>18}: {total_src} across {len(nonempty)} non-empty "
          f"tables ({len(report['tables'])} total)")
    for name, t in sorted(nonempty.items()):
        marker = "" if t["source"] == t["target"] else "   ← MISMATCH"
        print(f"{'':>20}{name}: {t['source']} → {t['target']}{marker}")
    print(f"{'rows sampled':>18}: {report['rows_sampled']} compared column-for-column")
    print(f"{'sequences reset':>18}: {len(report['sequences_reset'])}")
    print(f"{'foreign keys':>18}: enforced by Postgres on every inserted row")
    if report["mismatches"]:
        print(f"{'MISMATCHES':>18}:")
        for m in report["mismatches"]:
            print(f"{'':>20}{m}")
    print(f"{'result':>18}: {report['result']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path,
                    default=REPO_ROOT / "backend" / "data" / "platform.db",
                    help="SQLite database file (default: backend/data/platform.db)")
    ap.add_argument("--target", required=True,
                    help="postgresql+psycopg:// URL of an EMPTY database")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip the SQLite file backup (it is opened read-only regardless)")
    args = ap.parse_args()
    try:
        report = migrate(args.source, args.target, backup=not args.no_backup)
    except MigrationRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0 if report["result"] == "COMMITTED" else 1


if __name__ == "__main__":
    sys.exit(main())
