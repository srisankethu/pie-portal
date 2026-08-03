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

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal, require_manager_or_owner, require_owner
from ..config import settings
from ..db import get_session
from ..domain import models
from ..domain.enums import Role

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
        "connection_id": r.connection_id,
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
    """Live connection state, described so a non-engineer can act on it.

    Each organization is a fully separate tenant with (at most) its own Zoho
    connection — this never reads or reports on any other org's credentials.
    """
    if settings.ZOHO_SOURCE != "api":
        return {
            "state": "SAMPLE_DATA",
            "headline": "Not connected to Zoho — showing sample data",
            "detail": "ZOHO_SOURCE is not set to 'api', so the offline sample source is in "
                      "use. Everything you see is demonstration data, not your books.",
            "source": settings.ZOHO_SOURCE,
        }

    from ..ingestion.connections import get_zoho_credentials
    from ..ingestion.zoho_client import ZohoApiSource, ZohoError

    creds = get_zoho_credentials(session, org)
    if creds is None:
        return {
            "state": "NOT_CONFIGURED",
            "headline": "This organization has not connected a Zoho account",
            "detail": "An owner can connect one from Data & connection, or "
                      "PUT /api/v1/data/connection.",
            "source": "api",
        }

    try:
        ping = ZohoApiSource(credentials=creds).ping()
    except ZohoError as e:
        return {
            "state": "ERROR",
            "headline": "Zoho credentials were rejected",
            "detail": str(e),
            "source": "api",
            "api_base": creds.api_base,
            "accounts_base": creds.accounts_base,
        }
    except Exception as e:  # noqa: BLE001 — network/DNS/proxy problems
        return {
            "state": "UNREACHABLE",
            "headline": "Could not reach Zoho",
            "detail": f"{type(e).__name__}: {e}. Check outbound network access to "
                      f"{creds.api_base}.",
            "source": "api",
            "api_base": creds.api_base,
        }

    if not ping.get("organization_found"):
        visible = ", ".join(
            f"{o['name']} ({o['organization_id']})" for o in ping.get("visible_organizations", [])
        ) or "none"
        return {
            "state": "WRONG_ORG",
            "headline": "Signed in to Zoho, but the organization id does not match",
            "detail": f"This connection's Zoho organization id is {creds.organization_id}, "
                      f"which this login cannot see. Visible: {visible}.",
            "source": "api",
            "organization_id": creds.organization_id,
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
        "api_base": creds.api_base,
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
        "can_manage_connection": principal.role is Role.OWNER,
    }


class ZohoConnectionRequest(BaseModel):
    """What an owner supplies to link this organization's own Zoho Books
    account. Always a full replace (see ``connections.set_zoho_credentials``)."""

    zoho_organization_id: str
    client_id: str
    client_secret: str
    refresh_token: str
    accounts_base: str = "https://accounts.zoho.in"
    api_base: str = "https://www.zohoapis.in/books/v3"
    label: str = ""


@router.put("/connection")
def set_connection(
    body: ZohoConnectionRequest,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Connect (or replace) this organization's own Zoho Books account.

    Owner-only: this is a write credential for the whole organization's
    commercial data, not an operational action like a sync. Secrets are
    encrypted before they touch the database (see app/crypto.py) and are
    never echoed back — the response confirms the connection by pinging it,
    not by repeating what was sent.
    """
    from ..ingestion.connections import set_zoho_credentials

    if not (body.zoho_organization_id.strip() and body.client_id.strip()
           and body.client_secret.strip() and body.refresh_token.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "zoho_organization_id, client_id, client_secret and "
                            "refresh_token are all required")
    set_zoho_credentials(
        session, principal.organization_id,
        zoho_organization_id=body.zoho_organization_id.strip(),
        client_id=body.client_id.strip(),
        client_secret=body.client_secret.strip(),
        refresh_token=body.refresh_token.strip(),
        accounts_base=body.accounts_base.strip().rstrip("/"),
        api_base=body.api_base.strip().rstrip("/"),
        label=body.label.strip(),
    )
    return {"connection": _connection(session, principal.organization_id)}


# ── credentials (shared across organizations, deliberately) ──────────────────
def _credential_dict(session: Session, cred, org: str) -> dict:
    """A credential, described without ever repeating a secret.

    ``visible_organizations`` is the answer to the question that makes this
    whole split worth having: one Zoho grant already sees every company its
    authorizing user can. It is fetched live rather than cached, because it is
    exactly what someone is looking at when deciding whether they need a second
    credential at all — and a stale answer there sends them to re-enter secrets
    they did not need.
    """
    from ..ingestion.connections import connections_using

    using = connections_using(session, cred.credential_id)
    return {
        "credential_id": cred.credential_id,
        "label": cred.label or "Zoho connection",
        "client_id": cred.client_id,          # an identifier, not a secret
        "owner_organization_id": cred.owner_organization_id,
        "is_owner": cred.owner_organization_id == org,
        "shared_with_organization_ids": cred.shared_with_organization_ids or [],
        "accounts_base": cred.accounts_base,
        "api_base": cred.api_base,
        "rotated_at": cred.rotated_at.isoformat() if cred.rotated_at else None,
        "created_at": cred.created_at.isoformat() if cred.created_at else None,
        "used_by": [
            {"organization_id": c.organization_id,
             "zoho_organization_id": c.zoho_organization_id}
            for c in using
        ],
    }


@router.get("/credentials")
def list_credentials(
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Every Zoho grant this organization may connect through.

    Usually one. A second is needed only when the companies live under genuinely
    separate Zoho logins — not merely because they are separate legal entities,
    which is the assumption that turns one rotation into three.
    """
    from ..ingestion.connections import usable_credentials

    org = principal.organization_id
    return {
        "credentials": [_credential_dict(session, c, org)
                        for c in usable_credentials(session, org)],
        "organizations": [
            {"organization_id": o.organization_id, "name": o.name}
            for o in session.scalars(select(models.Organization)
                                     .order_by(models.Organization.name))
        ],
    }


@router.get("/credentials/{credential_id}/organizations")
def credential_organizations(
    credential_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """The Zoho companies this one grant can already reach.

    Ask Zoho, do not guess. If all three entities appear here, one credential is
    all that is ever needed and the other two connections cost nothing but a
    company id.
    """
    from ..ingestion.connections import CredentialNotUsable, get_credential
    from ..ingestion.zoho_client import ZohoApiSource, ZohoAuthError, ZohoCredentials
    from .. import crypto

    try:
        cred = get_credential(session, principal.organization_id, credential_id)
    except CredentialNotUsable as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e

    creds = ZohoCredentials(
        organization_id="",                    # ping lists all of them
        client_id=cred.client_id,
        client_secret=crypto.decrypt(cred.client_secret_encrypted),
        refresh_token=crypto.decrypt(cred.refresh_token_encrypted),
        accounts_base=cred.accounts_base, api_base=cred.api_base)
    try:
        info = ZohoApiSource(credentials=creds).ping()
    except ZohoAuthError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Zoho rejected this credential: {e}") from e

    connected = {c.zoho_organization_id
                 for c in session.scalars(select(models.ZohoConnection))}
    return {
        "credential_id": credential_id,
        "visible_organizations": [
            {**o, "already_connected": o["organization_id"] in connected}
            for o in info.get("visible_organizations", [])
        ],
    }


class ConnectWithCredential(BaseModel):
    """Connect using a grant already on file — no secret re-entered."""

    credential_id: str
    zoho_organization_id: str


@router.post("/connection/use-credential")
def connect_with_existing_credential(
    body: ConnectWithCredential,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Point this organization at another Zoho company using an existing grant.

    The second and third entity go through here. Nothing is typed twice, so
    there is no second copy of a secret for a future rotation to miss.
    """
    from ..ingestion.connections import CredentialNotUsable, connect_with_credential

    if not body.zoho_organization_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "A Zoho organization id is required")
    try:
        connect_with_credential(
            session, principal.organization_id,
            credential_id=body.credential_id,
            zoho_organization_id=body.zoho_organization_id.strip())
    except CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    return {"connection": _connection(session, principal.organization_id)}


class RotateCredential(BaseModel):
    refresh_token: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


@router.post("/credentials/{credential_id}/rotate")
def rotate(
    credential_id: str,
    body: RotateCredential,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Replace the secrets on one grant. Every connection using it follows.

    One operation, however many companies are connected through it — which is
    what makes rotating after a leak, or on a schedule, something a person will
    actually do rather than put off.
    """
    from ..ingestion.connections import CredentialNotUsable, rotate_credential

    if not body.refresh_token.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A refresh token is required")
    try:
        cred = rotate_credential(
            session, principal.organization_id, credential_id,
            refresh_token=body.refresh_token.strip(),
            client_id=(body.client_id or "").strip() or None,
            client_secret=(body.client_secret or "").strip() or None)
    except CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    return {"credential": _credential_dict(session, cred, principal.organization_id),
            "connection": _connection(session, principal.organization_id)}


class ShareCredential(BaseModel):
    organization_ids: list[str] = []


@router.post("/credentials/{credential_id}/share")
def share(
    credential_id: str,
    body: ShareCredential,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Set which other organizations may connect through this grant.

    A full replace, so revoking is the same operation as granting and cannot be
    forgotten. Sharing a key is not sharing data: each organization keeps its
    own users, decisions and margins.
    """
    from ..ingestion.connections import CredentialNotUsable, share_credential

    try:
        cred = share_credential(session, principal.organization_id, credential_id,
                                with_organization_ids=body.organization_ids)
    except CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    return {"credential": _credential_dict(session, cred, principal.organization_id)}


@router.delete("/connection")
def delete_connection(
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Unlink this organization's Zoho connection. Read-model data already
    pulled is untouched — only the credentials are removed."""
    from ..ingestion.connections import clear_zoho_connection

    removed = clear_zoho_connection(session, principal.organization_id)
    return {"removed": removed, "connection": _connection(session, principal.organization_id)}


class SyncRequest(BaseModel):
    """What to pull, and whether to trust what is already held.

    ``since`` is the operator's choice of start date — how far back the books
    are worth reading. ``full`` discards the resume cursor so every document is
    fetched again.
    """

    since: Optional[date] = None
    full: bool = False
    # Which company to pull. Omitted means every enabled connection, in turn —
    # the usual intent once an organization has more than one, and the thing a
    # person would otherwise do by clicking three times.
    connection_id: Optional[str] = None


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
    from ..ingestion.sync import SyncReport, SyncService, get_source
    from ..ingestion.zoho_client import configured_since
    from ..seed import ensure_org_and_users
    from ..signals.engine import run_detectors

    org = principal.organization_id
    since = req.since or configured_since()
    run = models.SyncRun(organization_id=org, source=settings.ZOHO_SOURCE,
                         status="OK", started_at=datetime.now(timezone.utc),
                         triggered_by=principal.user_id, since=since,
                         connection_id=req.connection_id)
    session.add(run)
    demo_removed: dict[str, int] = {}
    commercial_report: Optional[dict] = None
    svc: Optional[SyncService] = None
    report = SyncReport(organization_id=org)   # placeholder until a source resolves
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

        # Resolved per this org — never another org's connection, and this org
        # must have one of its own before a pull is attempted at all.
        # Passed only when set: a caller that never had connections still calls
        # get_source with the signature it always had.
        source = (get_source(session, org, since=since,
                             connection_id=req.connection_id)
                  if req.connection_id else get_source(session, org, since=since))
        svc = SyncService(session, source, org, resume=not req.full)
        svc.run()
        report = svc.report
        session.flush()

        detected = run_detectors(session, org)
        run.signals_emitted = detected.get("signals_emitted", 0)

        # Customer × Item metrics are derived from what just landed, so they are
        # rebuilt here rather than on the next page load. Targeted at the
        # relationships this pull actually moved — a full rebuild would scan the
        # organization's entire history to re-derive rows nothing changed.
        # Best-effort: a metrics problem must not fail a pull that succeeded.
        try:
            from ..commercial.compute import recompute as recompute_commercial

            ci = recompute_commercial(
                session, org,
                customer_ids=(report.touched_customer_ids or None),
            )
            run.signals_emitted += sum(ci.signals_by_type.values())
            commercial_report = ci.to_dict()
        except Exception:  # noqa: BLE001
            log.exception("customer-item recompute failed; the pull itself is kept")

        generated = DecisionService(session, org).generate()
        run.decisions_created = generated.get("created", 0)
        run.status = "OK"
    except Exception as e:  # noqa: BLE001 — a failed sync must be visible, not silent
        log.exception("sync failed")
        if svc is not None:
            report = svc.report
        run.status = "PARTIAL" if report.wrote_anything else "FAILED"
        run.error = f"{type(e).__name__}: {e}"[:1000]
    finally:
        # Counters come from the report either way: a run that wrote 336 sales
        # lines and then died wrote 336 sales lines, and saying zero would make
        # the database unreadable from its own audit trail.
        run.customers = report.customers
        run.products = report.products
        run.sales_txns = report.sales_txns
        run.cost_records = report.cost_records
        run.assignments = report.assignments
        run.documents_fetched = report.documents_fetched or getattr(
            svc.source if svc is not None else None, "documents_fetched", 0)
        run.documents_resumed = report.documents_resumed or getattr(
            svc.source if svc is not None else None, "documents_resumed", 0)
        run.skipped_count = len(report.skipped)
        run.skipped_sample = report.skipped[:20]
        run.finished_at = datetime.now(timezone.utc)
        session.flush()

    result = {"run": _run_dict(run), "connection": _connection(session, org)}
    if any(demo_removed.values()):
        result["demo_data_removed"] = demo_removed
    if commercial_report is not None:
        result["commercial"] = commercial_report
    return result
