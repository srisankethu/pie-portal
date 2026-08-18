"""Pull every connected Zoho company at once. The scheduled-sync entry point.

    python -m app.sync_all

Why a CLI and not an endpoint: a cron needs an exit code and no bearer token,
and it needs to *block* until the work is done so two nightly runs cannot
overlap. ``POST /api/v1/data/sync`` still exists and still returns immediately —
that is what the Data screen's button wants.

**What makes it faster than running one sync after another.** Zoho meters its
API per company, and every imported table is keyed on ``connection_id``, so the
pulls are genuinely independent: three companies that took three hours in
sequence take about as long as the slowest one. What is *not* independent is
everything after the pull — detectors, metrics, business state, decisions are
scoped to the organization, whose read model all of its connections feed. Those
run once, after every pull has landed. ``jobs.execute_analysis`` has the long
version, including why the old sequential behaviour was not merely slow but
analysed an incomplete book twice before getting it right.

Parallelism is across *connections*, wherever they live. Three companies on one
organization and three organizations of one company each both end up with three
concurrent pulls — the difference between those two shapes is whether the
numbers roll up together, which is a modelling decision, not a scheduling one.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

from sqlalchemy import select

from .domain import models
from .observability import logs

log = logging.getLogger("pie_portal.sync_all")


def organizations_with_connections(session) -> list[str]:
    """Every organization that has something enabled to pull from."""
    return list(session.scalars(
        select(models.ZohoConnection.organization_id)
        .where(models.ZohoConnection.enabled.is_(True))
        .distinct()
        .order_by(models.ZohoConnection.organization_id)))


def _sync_one_organization(organization_id: str, *, since: Optional[date],
                           full: bool, triggered_by: str,
                           incremental: bool = True) -> dict:
    """One organization, on its own session — this runs in its own thread."""
    from .db import SessionLocal
    from .ingestion import jobs

    session = SessionLocal()
    try:
        result = jobs.start_all(session, organization_id, since=since, full=full,
                                triggered_by=triggered_by, incremental=incremental)
        session.commit()
        return result
    except Exception as e:  # noqa: BLE001 — one organization must not sink the rest
        log.exception("sync_all failed for organization %s", organization_id)
        session.rollback()
        return {"organization_id": organization_id, "connections": 0, "runs": [],
                "analysed": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        session.close()


def sync_all(organization_ids: list[str], *, since: Optional[date] = None,
             full: bool = False, triggered_by: str = "schedule",
             incremental: bool = True) -> list[dict]:
    """Every organization concurrently; every connection within one concurrently."""
    if not organization_ids:
        return []
    kw = dict(since=since, full=full, triggered_by=triggered_by,
              incremental=incremental)
    if len(organization_ids) == 1:
        return [_sync_one_organization(organization_ids[0], **kw)]
    with ThreadPoolExecutor(max_workers=len(organization_ids),
                            thread_name_prefix="sync-org") as pool:
        futures = [pool.submit(_sync_one_organization, oid, **kw)
                   for oid in organization_ids]
        return [f.result() for f in futures]


def _summarise(results: list[dict]) -> str:
    lines = []
    for r in results:
        org = r.get("organization_id")
        if r.get("error"):
            lines.append(f"{org}: FAILED — {r['error']}")
            continue
        if not r.get("connections"):
            lines.append(f"{org}: nothing to pull — {r.get('detail', 'no connections')}")
            continue
        for run in r.get("runs", []):
            lines.append(
                f"{org}  {run.get('connection_id') or '(default)'}  "
                f"{run.get('status')}  fetched={run.get('documents_fetched')} "
                f"resumed={run.get('documents_resumed')}"
                + (f"  {run['error']}" if run.get("error") else ""))
        if r.get("analysed"):
            lines.append(f"{org}: analysed — {r.get('signals_emitted', 0)} signals, "
                         f"{r.get('decisions_created', 0)} decisions")
        else:
            lines.append(f"{org}: NOT analysed — {r.get('detail', '')}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """Exit 0 when every pull landed, 1 when any did not, 2 when there was nothing.

    A cron reads the exit code, so a partial pull has to be non-zero — otherwise
    the one night Zoho rate-limited the run into PARTIAL looks the same as a
    clean one, and nobody finds out until a number looks wrong on a screen.
    """
    from .config import settings
    from .db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--organization", action="append", metavar="ORG_ID",
                        help="Sync only this organization. Repeatable. "
                             "Default: every organization with an enabled connection.")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Start date for the pull. Default: the resume cursor, "
                             "or the rolling ZOHO_HISTORY_DAYS window.")
    parser.add_argument("--reconcile", action="store_true",
                        help="List the whole book instead of stopping at what "
                             "changed. Still skips unchanged documents, so it "
                             "costs list calls only — but it is the only mode "
                             "that notices a document deleted or voided in "
                             "Zoho. Run it weekly.")
    parser.add_argument("--full", action="store_true",
                        help="Ignore the resume cursor and re-fetch every "
                             "document. Hours, not minutes — for a read model "
                             "you no longer trust, not for a schedule.")
    parser.add_argument("--json", action="store_true",
                        help="Emit the raw result as JSON instead of a summary.")
    args = parser.parse_args(argv)

    # The same configuration the API process uses, so a nightly pull's lines
    # look like every other line and land in the same file when LOG_FILE names
    # one. Each run's own log is kept with the run either way.
    logs.configure()

    since = date.fromisoformat(args.since) if args.since else None

    session = SessionLocal()
    try:
        org_ids = args.organization or organizations_with_connections(session)
        # A deployment on fixture data has no connection rows at all, and should
        # still be able to run the cycle rather than silently doing nothing.
        if not org_ids and settings.ZOHO_SOURCE != "api":
            org_ids = [settings.DEFAULT_ORG_ID]
    finally:
        session.close()

    if not org_ids:
        print("Nothing to sync: no organization has an enabled Zoho connection.",
              file=sys.stderr)
        return 2

    results = sync_all(org_ids, since=since, full=args.full,
                       incremental=not args.reconcile)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(_summarise(results))

    failed = any(r.get("error") for r in results) or any(
        run.get("status") not in ("OK",)
        for r in results for run in r.get("runs", []))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
