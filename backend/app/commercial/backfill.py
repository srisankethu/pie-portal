"""Backfill Customer × Item metrics from already-synced data.

Derived metrics are disposable: they are computed from the ``sales_txns`` and
``cost_records`` already in the database, so a metric-logic change never
requires a Zoho re-sync. This rebuilds them.

    # whole organization
    python -m app.commercial.backfill --org org_sanketh

    # one customer
    python -m app.commercial.backfill --org org_sanketh --customer <customer_id>

    # see what would happen, change nothing
    python -m app.commercial.backfill --org org_sanketh --dry-run

    # rebuild metrics without emitting signals (e.g. re-deriving after a
    # threshold change, when the decision queue should not move)
    python -m app.commercial.backfill --org org_sanketh --no-signals

Idempotent: metric rows are upserted on (organization, customer, product), so
running it twice produces the same rows. It never deletes or duplicates a source
transaction — only the derived projection — and needs no destructive reset.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional

from .compute import RecomputeReport, recompute


def run(org: str, customer_id: Optional[str] = None, *, emit_signals: bool = True,
        dry_run: bool = False) -> RecomputeReport:
    from ..db import SessionLocal

    session = SessionLocal()
    try:
        report = recompute(
            session, org,
            customer_ids={customer_id} if customer_id else None,
            emit_signals=emit_signals and not dry_run,
        )
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return report
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org", required=True, help="organization_id to rebuild")
    parser.add_argument("--customer", help="Limit to one customer_id")
    parser.add_argument("--no-signals", action="store_true",
                        help="Rebuild metrics without emitting signals")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute and report, then roll back — writes nothing")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    report = run(args.org, args.customer, emit_signals=not args.no_signals,
                 dry_run=args.dry_run)

    print(json.dumps(report.to_dict(), indent=2))
    if args.dry_run:
        print("\nDry run — nothing was written.")
    if report.failures:
        print(f"\n{len(report.failures)} relationship(s) failed; "
              "the rest were written.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
