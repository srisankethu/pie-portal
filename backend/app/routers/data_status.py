"""Connection + ingestion status, and the one-click refresh behind it.

"Is Zoho connected?" had no answer inside the product — the check existed only
as a curl command, and a sync left no trace once its response scrolled past.
This exposes the state the UI needs to answer it plainly, and a single action
that runs the whole cycle (pull → detect → decide) so nobody has to remember
three calls in the right order.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal, require_manager_or_owner
from ..config import settings
from ..db import get_session
from ..domain import models

log = logging.getLogger("pie_portal.data")

router = APIRouter(prefix="/api/v1/data", tags=["data"])


def _last_run(session: Session, org: str) -> Optional[models.SyncRun]:
    return session.scalar(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == org)
        .order_by(models.SyncRun.started_at.desc())
        .limit(1))


def _run_dict(r: Optional[models.SyncRun]) -> Optional[dict[str, Any]]:
    if r is None:
        return None
    return {
        "status": r.status, "source": r.source,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "customers": r.customers, "products": r.products,
        "sales_txns": r.sales_txns, "cost_records": r.cost_records,
        "skipped_count": r.skipped_count, "skipped_sample": r.skipped_sample or [],
        "signals_emitted": r.signals_emitted, "decisions_created": r.decisions_created,
        "error": r.error,
        "since": r.since.isoformat() if r.since else None,
        "documents_fetched": r.documents_fetched,
        "documents_resumed": r.documents_resumed,
        "assignments": r.assignments,
    }


def _connection(session: Session, org: str) -> dict[str, Any]:
    """Live connection state, described so a non-engineer can act on it."""
    if settings.ZOHO_SOURCE != "api":
        return {
            "state": "SAMPLE_DATA",
            "headline": "Not connected to Zoho — showing sample data",
            "detail": "ZOHO_SOURCE is not set to 'api', so the offline sample source is in "
                      "use. Everything you see is demonstration data, not your books.",
            "source": settings.ZOHO_SOURCE,
        }

    from ..ingestion.zoho_client import ZohoApiSource, ZohoError

    try:
        ping = ZohoApiSource().ping()
    except ZohoError as e:
        return {
            "state": "ERROR",
            "headline": "Zoho credentials were rejected",
            "detail": str(e),
            "source": "api",
            "api_base": settings.ZOHO_API_BASE,
            "accounts_base": settings.ZOHO_ACCOUNTS_BASE,
        }
    except Exception as e:  # noqa: BLE001 — network/DNS/proxy problems
        return {
            "state": "UNREACHABLE",
            "headline": "Could not reach Zoho",
            "detail": f"{type(e).__name__}: {e}. Check outbound network access to "
                      f"{settings.ZOHO_API_BASE}.",
            "source": "api",
            "api_base": settings.ZOHO_API_BASE,
        }

    if not ping.get("organization_found"):
        visible = ", ".join(
            f"{o['name']} ({o['organization_id']})" for o in ping.get("visible_organizations", [])
        ) or "none"
        return {
            "state": "WRONG_ORG",
            "headline": "Signed in to Zoho, but the organization id does not match",
            "detail": f"ZOHO_ORGANIZATION_ID is {settings.ZOHO_ORGANIZATION_ID}, which this "
                      f"login cannot see. Visible: {visible}.",
            "source": "api",
            "organization_id": settings.ZOHO_ORGANIZATION_ID,
            "visible_organizations": ping.get("visible_organizations", []),
        }

    return {
        "state": "CONNECTED",
        "headline": f"Connected to {ping.get('organization_name')}",
        "detail": None,
        "source": "api",
        "organization_id": ping.get("organization_id"),
        "organization_name": ping.get("organization_name"),
        "currency": ping.get("currency"),
        "api_base": settings.ZOHO_API_BASE,
        "history_days": settings.ZOHO_HISTORY_DAYS,
    }


@router.get("/status")
def data_status(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Connection + last-ingestion state. Readable by any signed-in user; only
    managers and owners can act on it."""
    from ..repositories import ReadModelRepository

    org = principal.organization_id
    counts = {
        "customers": session.query(models.Customer).filter_by(organization_id=org).count(),
        "products": session.query(models.Product).filter_by(organization_id=org).count(),
        "sales_txns": session.query(models.SalesTxn).filter_by(organization_id=org).count(),
        "cost_records": session.query(models.CostRecord).filter_by(organization_id=org).count(),
        "decisions": session.query(models.Decision).filter_by(organization_id=org).count(),
        # Rows written before the bill-discount fix — re-sync with full=true to
        # re-fetch these bills from Zoho and correct them (see zoho-setup.md).
        "cost_records_pending_discount_backfill":
            ReadModelRepository(session, org).count_cost_records_pending_discount_backfill(),
    }
    return {
        "connection": _connection(session, org),
        "last_sync": _run_dict(_last_run(session, org)),
        "read_model": counts,
        "can_sync": principal.is_manager_or_owner,
    }


class SyncRequest(BaseModel):
    """What to pull, and whether to trust what is already held.

    ``since`` is the operator's choice of start date — how far back the books
    are worth reading. ``full`` discards the resume cursor so every document is
    fetched again.
    """

    since: Optional[date] = None
    full: bool = False


@router.post("/sync")
def run_sync(
    req: SyncRequest = Body(default_factory=SyncRequest),
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Pull from Zoho, detect signals, generate decisions — the whole cycle.

    Recorded whatever happens, and recorded *truthfully*: an interrupted pull is
    PARTIAL with the rows it did write, not FAILED with zeros. Zoho rate-limits
    per organization and a pull costs one call per document, so being cut short
    is an ordinary event, not an exception — the rows already written are kept
    and the next run resumes from them.
    """
    from ..decisions.service import DecisionService
    from ..ingestion.sync import SyncService, get_source
    from ..ingestion.zoho_client import configured_since
    from ..seed import ensure_org_and_users
    from ..signals.engine import run_detectors

    org = principal.organization_id
    since = req.since or configured_since()
    run = models.SyncRun(organization_id=org, source=settings.ZOHO_SOURCE,
                         status="OK", started_at=datetime.now(timezone.utc),
                         triggered_by=principal.user_id, since=since)
    session.add(run)
    svc = SyncService(session, get_source(since=since), org, resume=not req.full)
    demo_removed: dict[str, int] = {}
    try:
        ensure_org_and_users(session)

        if settings.ZOHO_SOURCE == "api":
            # A real sync means a real Zoho account is linked — any demo/sample
            # customers, products, or the decisions built from them must not go
            # on sitting alongside real data. This never touches a real Zoho
            # record: demo rows are identifiable by fixed ids no live sync ever
            # produces. Best-effort — a purge problem must not block the pull.
            try:
                from ..demo import purge_demo_seed
                demo_removed = purge_demo_seed(session, org)
            except Exception:  # noqa: BLE001
                log.exception("demo-data purge failed; continuing with the sync")

        svc.run()
        session.flush()

        detected = run_detectors(session, org)
        run.signals_emitted = detected.get("signals_emitted", 0)
        generated = DecisionService(session, org).generate()
        run.decisions_created = generated.get("created", 0)
        run.status = "OK"
    except Exception as e:  # noqa: BLE001 — a failed sync must be visible, not silent
        log.exception("sync failed")
        run.status = "PARTIAL" if svc.report.wrote_anything else "FAILED"
        run.error = f"{type(e).__name__}: {e}"[:1000]
    finally:
        # Counters come from the report either way: a run that wrote 336 sales
        # lines and then died wrote 336 sales lines, and saying zero would make
        # the database unreadable from its own audit trail.
        report = svc.report
        run.customers = report.customers
        run.products = report.products
        run.sales_txns = report.sales_txns
        run.cost_records = report.cost_records
        run.assignments = report.assignments
        run.documents_fetched = report.documents_fetched or getattr(
            svc.source, "documents_fetched", 0)
        run.documents_resumed = report.documents_resumed or getattr(
            svc.source, "documents_resumed", 0)
        run.skipped_count = len(report.skipped)
        run.skipped_sample = report.skipped[:20]
        run.finished_at = datetime.now(timezone.utc)
        session.flush()

    result = {"run": _run_dict(run), "connection": _connection(session, org)}
    if any(demo_removed.values()):
        result["demo_data_removed"] = demo_removed
    return result
