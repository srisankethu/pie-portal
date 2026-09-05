"""Connection + ingestion status, and the one-click refresh behind it.

"Is Zoho connected?" had no answer inside the product — the check existed only
as a curl command, and a sync left no trace once its response scrolled past.
This exposes the state the UI needs to answer it plainly, and a single action
that runs the whole cycle (pull → detect → decide) so nobody has to remember
three calls in the right order.
"""
from __future__ import annotations

import csv
import errno
import hashlib
import io
import logging
from datetime import date
from typing import Any, Optional

from fastapi import (APIRouter, Body, Depends, HTTPException, Request,
                     Response, status)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..authz import Principal, current_principal, require_manager_or_owner, require_owner
from ..config import settings
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..ingestion import jobs

log = logging.getLogger("pie_portal.data")

router = APIRouter(prefix="/api/v1/data", tags=["data"])


def _last_run(session: Session, org: str) -> Optional[models.SyncRun]:
    return session.scalar(
        select(models.SyncRun)
        .where(models.SyncRun.organization_id == org)
        .order_by(models.SyncRun.started_at.desc())
        .limit(1))


def _coverage(session: Session, org: str) -> list[dict[str, Any]]:
    """How far back each connected company has actually been listed.

    The answer to "what history do I have", which "last synced 2 hours ago"
    does not give: a nightly pull can run for a year and still only cover the
    window the first run asked for.

    Read from the runs rather than from the rows, and only from runs that
    finished, for the reason ``ReadModelRepository.covered_since`` states — an
    absent document and an unsearched month are indistinguishable in the data,
    so only the searcher can say which it was.
    """
    rows = session.execute(
        select(models.SyncRun.connection_id,
               func.min(models.SyncRun.since),
               func.max(models.SyncRun.finished_at))
        .where(models.SyncRun.organization_id == org,
               models.SyncRun.status == "OK",
               models.SyncRun.since.is_not(None))
        .group_by(models.SyncRun.connection_id)).all()
    names = {c.connection_id: (c.label or "")
             for c in session.scalars(
                 select(models.ZohoConnection).where(
                     models.ZohoConnection.organization_id == org)).all()}
    return [
        {"connection_id": connection_id,
         "label": names.get(connection_id) or "",
         "covered_from": since.isoformat() if since else None,
         "covered_to": finished.date().isoformat() if finished else None}
        for connection_id, since, finished in rows
    ]


def _run_dict(r: Optional[models.SyncRun], *,
              costs_visible: bool = True) -> Optional[dict[str, Any]]:
    """The run row as the screens read it.

    ``costs_visible=False`` withholds the money on the skip reports. It is not
    cosmetic: ``line_value`` on a ``cost_record`` skip is a *purchase* line
    total, and ``unresolved`` sums those per missing item beside the line count
    — so a salesperson reading this endpoint could divide one by the other and
    have a unit cost, from a screen about data quality. The count of blocked
    lines and the name of what is missing are what the reader needs to act;
    the value only ranks the worklist, and ranking is a manager's job anyway.
    """
    if r is None:
        return None
    unresolved = list(r.unresolved or [])
    skipped_sample = list(r.skipped_sample or [])
    if not costs_visible:
        unresolved = [{**g, "value": None,
                       "examples": [{**e, "value": None, "qty": None}
                                    for e in (g.get("examples") or [])]}
                      for g in unresolved]
        # The context carries `line_value` and `qty` per row, which is the same
        # number one document at a time.
        skipped_sample = [{k: v for k, v in row.items() if k != "context"}
                          for row in skipped_sample]
    return {
        "sync_run_id": r.sync_run_id,
        "status": r.status, "source": r.source,
        # Everything the status card renders. Elapsed time is left to the
        # client: it ticks every second, and a number baked into a response is
        # stale before it is painted.
        "phase": r.phase,
        "active": r.status in jobs.ACTIVE,
        # Calendar coverage of the requested window — a real denominator,
        # unlike a document count Zoho will not reveal in advance.
        "windows_total": r.windows_total,
        "windows_done": r.windows_done,
        "heartbeat_at": clock.iso(r.heartbeat_at),
        "connection_id": r.connection_id,
        "started_at": clock.iso(r.started_at),
        "finished_at": clock.iso(r.finished_at),
        "customers": r.customers, "products": r.products,
        "sales_txns": r.sales_txns, "cost_records": r.cost_records,
        "vendors": r.vendors, "stock_snapshots": r.stock_snapshots,
        "payments": r.payments, "purchase_orders": r.purchase_orders,
        "sales_orders": r.sales_orders, "vendor_payments": r.vendor_payments,
        "skipped_count": r.skipped_count, "skipped_sample": skipped_sample,
        # The worklist: one row per thing to fix, not per row skipped.
        "unresolved": unresolved,
        "signals_emitted": r.signals_emitted, "decisions_created": r.decisions_created,
        "error": r.error,
        "since": r.since.isoformat() if r.since else None,
        "documents_fetched": r.documents_fetched,
        "documents_resumed": r.documents_resumed,
        "assignments": r.assignments,
        "notes": r.notes or {},
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
        # The automatic pull's cadence and next expected run. Reported to every
        # signed-in user — knowing whether the numbers refresh themselves is
        # the same entitlement as knowing when they last arrived. Changing it
        # is a manager/owner action (see PUT /auto-sync).
        "auto_sync": _auto_sync_dict(session, org),
        "last_sync": _run_dict(_last_run(session, org),
                               costs_visible=principal.is_manager_or_owner),
        # What history this organization actually holds, per connected company.
        # Distinct from "when did we last sync": a nightly pull that runs for a
        # year still only covers the window the first run asked for, and until
        # this was reported there was no way to tell a book with two years of
        # history from one with two months.
        "coverage": _coverage(session, org),
        # Carried here as well so a page opened mid-pull renders the running job
        # on its first paint, without a second round trip to discover it.
        "sync": _sync_state(session, org,
                            costs_visible=principal.is_manager_or_owner),
        "read_model": counts,
        "can_sync": principal.is_manager_or_owner,
        "can_manage_connection": principal.role is Role.OWNER,
    }


def _auto_sync_dict(session: Session, org_id: str) -> dict[str, Any]:
    from ..ingestion import scheduler

    org = session.get(models.Organization, org_id)
    covers_from = scheduler.scheduled_since(session, org_id)
    return {
        # 0 means off. `available` separates "off by choice" from "this
        # deployment reads sample data and has nothing to keep fresh" — the
        # screen offers the control in the first case and explains in the second.
        "hours": scheduler.auto_sync_hours(org),
        "available": settings.ZOHO_SOURCE == "api",
        "next_run_at": (clock.iso(scheduler.next_run_at(session, org))
                        if org is not None else None),
        # What the scheduled pull will ask for, so "automatic" never reads as
        # "from some date the machine picked". Null until a first sync covers
        # anything; the run itself then applies the same default a first
        # manual sync gets.
        "covers_from": covers_from.isoformat() if covers_from else None,
    }


class AutoSyncRequest(BaseModel):
    """The cadence, in hours. 0 switches the automatic sync off."""

    hours: int


@router.put("/auto-sync")
def set_auto_sync(
    body: AutoSyncRequest,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Choose how often this organization's books are pulled automatically.

    Manager-or-owner for the same reason starting a sync is: it spends the
    Zoho rate limit and decides how fresh everyone's numbers are. Stored on
    the organization row, so the schedule survives restarts and is one value
    per tenant rather than one per whoever last edited an environment file.
    """
    if not (0 <= body.hours <= 24 * 7):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "hours must be between 0 (off) and 168 (weekly)")
    org = session.get(models.Organization, principal.organization_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such organization.")
    # Assigned, not mutated: SQLAlchemy only sees a JSON column change when the
    # dict identity changes.
    org.config = {**(org.config or {}), "auto_sync_hours": body.hours}
    session.flush()
    return {"auto_sync": _auto_sync_dict(session, principal.organization_id)}


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
        "rotated_at": clock.iso(cred.rotated_at),
        "created_at": clock.iso(cred.created_at),
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
    from ..ingestion.connections import (CredentialNotUsable,
                                          companies_visible_to, get_credential)
    from ..ingestion.zoho_client import ZohoAuthError

    try:
        cred = get_credential(session, principal.organization_id, credential_id)
    except CredentialNotUsable as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e

    # Resolved through ``ingestion`` rather than assembled here. Building
    # ``ZohoCredentials`` and pinging is a Zoho concern, and a router that does
    # it holds a second copy of how a credential becomes a client — the copy
    # that keeps working after the first one changes shape (CLAUDE.md §3).
    try:
        visible = companies_visible_to(cred)
    except ZohoAuthError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Zoho rejected this credential: {e}") from e

    connected = {c.zoho_organization_id
                 for c in session.scalars(select(models.ZohoConnection))}
    return {
        "credential_id": credential_id,
        "visible_organizations": [
            {**o, "already_connected": o["organization_id"] in connected}
            for o in visible
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


# Rotation used to live here, as a "credentials" surface of its own. It has
# moved onto the connection — ``POST /api/v1/connections/{id}/rotate`` — because
# a revocable refresh token is a *Zoho* mechanism, not a platform concept every
# connector will need, and because the control belonged on the screen showing
# the thing somebody had just decided to rotate. Sharing a grant between
# organizations stays here: that is about who may use a credential, which is a
# platform question and not a connector one.


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


@router.delete("/credentials/{credential_id}")
def remove_credential(
    credential_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Remove a sign-in nothing is connected through any more.

    Disconnecting a company deliberately leaves its credential behind
    (``connections.clear_zoho_connection``): other organizations may be using
    it, and keeping it means reconnecting does not mean re-entering a secret.
    That is right, and it is also why an owner needs *this* — without it a
    grant that no longer reaches anything is permanent, and the picker offering
    it reads as a live option. Retention is the default; this is the way out of
    it, not a reversal of it.

    The in-use refusal is the service's, not a check repeated here: a
    credential deleted out from under a live connection leaves an organization
    that looks connected and silently cannot sync.
    """
    from ..ingestion.connections import CredentialNotUsable, delete_credential

    try:
        delete_credential(session, principal.organization_id, credential_id)
    except CredentialNotUsable as e:
        # Same answer for "no such credential" and "not yours" — the service
        # collapses them on purpose, so that this endpoint cannot be used to
        # enumerate another tenant's credential ids.
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return {"removed": True, "credential_id": credential_id}


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


def _sync_state(session: Session, org: str, *, costs_visible: bool = True) -> dict:
    """Everything the sync card needs, in one shape, from one place.

    The screen renders from state rather than from the outcome of whatever
    request it last made — which is the whole point of the redesign. A page
    opened fresh while a pull is running must show that pull, not an idle
    button.
    """
    running = jobs.active_runs(session, org)
    # The headline still needs one job to talk about. Newest first from
    # `active_runs`, so this is the pull that started most recently.
    active = running[0] if running else None
    last = jobs.last_finished_run(session, org)
    ok = jobs.last_successful_run(session, org)
    return {
        # IDLE is the absence of a job, not a stored value — there is no row to
        # invent for an organization that has never synced.
        "state": active.status if active is not None else (last.status if last else "IDLE"),
        "active": _run_dict(active, costs_visible=costs_visible),
        # Every pull in flight, not just the newest. Two connected Zoho
        # companies are two independent pulls against two different APIs, and
        # reporting one of them is what made the other invisible — the screen
        # could not show a second progress bar because it was never told there
        # was a second job.
        "active_runs": [_run_dict(r, costs_visible=costs_visible)
                        for r in running],
        # Which connections are *individually* busy. The screen gates each
        # company's button on its own entry here; a single organization-wide
        # flag is what disabled all three buttons the moment any one of them
        # started, and made the concurrency behind it unreachable.
        "busy_connections": [r.connection_id for r in running
                             if r.connection_id is not None],
        "last": _run_dict(last, costs_visible=costs_visible),
        "last_successful_at": (clock.iso(ok.started_at)
                               if ok is not None and ok.started_at else None),
        # Whether the "sync everything" button can start anything — not the
        # gate for a per-connection button, which reads `busy_connections`
        # above. That pull reads every company, so *any* pull in flight is one
        # it would read a second time and `start_sync` hands it back that job
        # instead of starting. Asking only whether another organization-wide
        # pull was running left the button live for a click the server declines.
        "can_start": not running,
    }


@router.get("/sync")
def sync_state(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """The current sync state. Polled while a job is in flight.

    Readable by anyone signed in — a salesperson cannot start a sync but is
    entitled to know the figures they are looking at are mid-refresh.
    """
    return _sync_state(session, principal.organization_id,
                       costs_visible=principal.is_manager_or_owner)


# ── the skipped rows of one run, in full ─────────────────────────────────────
#
# `SyncRun.skipped_sample` is twenty rows and says so on screen. Twenty is a
# preview of a problem, not a description of one: "first 20 of 1304" cannot be
# reconciled against Zoho, cannot be grouped by supplier, and cannot be handed
# to whoever maintains the item master. These two endpoints serve the whole
# list — the grid reads the JSON, the export button reads the CSV, and both
# come off the same query so the file can never disagree with the screen.
#
# Manager-or-owner, for the reason `models.SyncSkip` states: `line_value` on a
# `cost_record` skip is a purchase line total.

#: The export, column by column. One list, used to build the CSV header, to
#: pull the values out of each row, and to label the grid — three places that
#: would otherwise drift into three different column sets.
SKIP_COLUMNS: list[tuple[str, str]] = [
    ("seq", "Row"),
    ("company", "Company"),
    ("kind", "Kind"),
    ("code", "Reason code"),
    ("detail", "Reason"),
    ("ref", "Reference"),
    ("missing_id", "Missing id"),
    ("label", "Item on the document"),
    ("sku", "SKU"),
    ("document", "Document"),
    ("document_date", "Document date"),
    ("party", "Customer / supplier"),
    ("qty", "Qty"),
    ("line_value", "Line value"),
    ("fix", "What to do"),
]


def _skip_rows(session: Session, org: str, sync_run_id: str) -> tuple[models.SyncRun, list[dict[str, Any]]]:
    """Every skipped row of one run, oldest first, with its company named.

    Scoped to the caller's organization in the query rather than checked after
    it: each organization is a separate tenant, and a run id from another one
    must read as "no such run" rather than as a permission error that confirms
    it exists.
    """
    run = session.scalar(
        select(models.SyncRun)
        .where(models.SyncRun.sync_run_id == sync_run_id,
               models.SyncRun.organization_id == org))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No sync run with that id in this organization.")

    labels = {c.connection_id: (c.label or "")
              for c in session.scalars(
                  select(models.ZohoConnection)
                  .where(models.ZohoConnection.organization_id == org)).all()}
    rows = session.scalars(
        select(models.SyncSkip)
        .where(models.SyncSkip.sync_run_id == sync_run_id,
               models.SyncSkip.organization_id == org)
        .order_by(models.SyncSkip.seq)).all()
    return run, [
        {
            "skip_id": s.skip_id,
            "seq": s.seq,
            "connection_id": s.connection_id,
            "company": labels.get(s.connection_id) or "",
            "kind": s.kind, "code": s.code, "detail": s.detail, "ref": s.ref,
            "missing_id": s.missing_id, "label": s.label, "sku": s.sku,
            "document": s.document, "document_date": s.document_date,
            "party": s.party,
            "qty": float(s.qty) if s.qty is not None else None,
            "line_value": float(s.line_value) if s.line_value is not None else None,
            "fix": s.fix,
        }
        for s in rows
    ]


def _skips_are_complete(run: models.SyncRun, held: int) -> Optional[str]:
    """Whether these rows are the whole story, said out loud when they are not.

    A run from before ``sync_skipped_rows`` existed reports a skip count with no
    rows behind it, and an export of nothing that calls itself complete is worse
    than no export: somebody reconciles an empty sheet and concludes the sync is
    clean. So the response carries what it holds against what the run counted,
    and says which case an empty file is.
    """
    if held == run.skipped_count:
        return None
    if held == 0:
        return ("This run recorded a count but not the rows themselves — it ran "
                "before the full list was kept. Re-sync to produce an exportable "
                "list.")
    return (f"This run reported {run.skipped_count} skipped rows and "
            f"{held} were kept. The difference was not recorded.")


@router.get("/sync-runs/{sync_run_id}/skipped")
def skipped_rows(
    sync_run_id: str,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Every row one sync could not fully resolve."""
    run, rows = _skip_rows(session, principal.organization_id, sync_run_id)
    return {
        "sync_run_id": run.sync_run_id,
        "started_at": clock.iso(run.started_at),
        # What the run counted, beside what this response holds. Two numbers
        # rather than one because they can legitimately differ, and a reader
        # comparing an export against the screen deserves to see why.
        "skipped_count": run.skipped_count,
        "held": len(rows),
        "incomplete": _skips_are_complete(run, len(rows)),
        "columns": [{"field": f, "header": h} for f, h in SKIP_COLUMNS],
        "rows": rows,
    }


@router.get("/sync-runs/{sync_run_id}/skipped.csv")
def skipped_rows_csv(
    sync_run_id: str,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> Response:
    """The same rows as a spreadsheet.

    Built server-side rather than in the browser so the file is the whole list
    and not the page the grid happens to be showing — the export exists exactly
    because a truncated view was the problem.
    """
    run, rows = _skip_rows(session, principal.organization_id, sync_run_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for _field, header in SKIP_COLUMNS])
    for row in rows:
        writer.writerow(["" if row.get(f) is None else row.get(f)
                         for f, _header in SKIP_COLUMNS])
    note = _skips_are_complete(run, len(rows))
    if note:
        # In the file, not only in the JSON. A sheet that travels away from the
        # screen it was downloaded from has to carry its own caveat.
        writer.writerow([])
        writer.writerow([note])

    started = run.started_at.date().isoformat() if run.started_at else "run"
    name = f"skipped-rows-{started}-{run.sync_run_id[:8]}.csv"
    return Response(
        # BOM, so Excel opens a UTF-8 file as UTF-8. Without it an item named
        # in anything but ASCII arrives mojibaked, which for a sheet whose job
        # is matching part names back to Zoho is the whole value gone.
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ── the log of one run ───────────────────────────────────────────────────────
#
# "See the server log" is what a crashed sync used to say, to somebody with a
# browser and no shell. These serve the lines the run itself emitted, kept with
# the run (``models.SyncRunLog``), so the account of an hour-long pull is
# readable from the screen that reports it failed.
#
# Manager-or-owner, for the same reason the skipped rows are: a log line is
# whatever the code passed to it, and lines from the cost-record stage name
# purchase documents.

#: One page of a log. Generous, because the point is to read a run's story
#: rather than to sample it, and a line is a few hundred bytes.
LOG_PAGE = 2000


def _log_rows(session: Session, org: str, sync_run_id: str, *,
              after_seq: int, limit: int,
              levels: Optional[set[str]] = None
              ) -> tuple[models.SyncRun, list[dict[str, Any]], int, int]:
    """One run's log from a cursor, with how many lines it holds in total.

    Scoped to the caller's organization inside the query rather than checked
    after it, like ``_skip_rows``: a run id from another tenant reads as "no
    such run" rather than as a permission error that confirms it exists.

    ``after_seq`` is what makes a live pull watchable — the screen polls for
    what it has not already seen instead of re-fetching an hour of log every
    two seconds.

    **Two counts come back, and they answer different questions.** ``total`` is
    how many lines match what was *asked for*, narrowed by ``levels`` — the
    denominator a reader is paging through. ``stored`` is how many lines the run
    kept at all, which is a property of the run rather than of this request, and
    is what :func:`_log_note` has to read: "this run kept no log" is a claim
    about the run, and building it from the filtered count said it about a
    perfectly clean run that simply had nothing to warn about.
    """
    run = session.scalar(
        select(models.SyncRun)
        .where(models.SyncRun.sync_run_id == sync_run_id,
               models.SyncRun.organization_id == org))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No sync run with that id in this organization.")

    stmt = (select(models.SyncRunLog)
            .where(models.SyncRunLog.sync_run_id == sync_run_id,
                   models.SyncRunLog.organization_id == org))
    if levels:
        stmt = stmt.where(models.SyncRunLog.level.in_(sorted(levels)))
    total = int(session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0)
    # Counted separately only where a filter narrowed the set. Without one the
    # two questions have the same answer, and asking twice would double the
    # queries the whole-log download makes on every one of its pages.
    stored = total if not levels else int(session.scalar(
        select(func.count()).select_from(models.SyncRunLog)
        .where(models.SyncRunLog.sync_run_id == sync_run_id,
               models.SyncRunLog.organization_id == org)) or 0)
    rows = session.scalars(
        stmt.where(models.SyncRunLog.seq > after_seq)
        .order_by(models.SyncRunLog.seq).limit(limit)).all()
    return run, [
        {
            "seq": r.seq,
            "at": clock.iso(r.at),
            "level": r.level,
            "logger": r.logger,
            "message": r.message,
        }
        for r in rows
    ], total, stored


@router.get("/sync-runs/{sync_run_id}/log")
def sync_run_log(
    sync_run_id: str,
    after_seq: int = -1,
    limit: int = LOG_PAGE,
    problems_only: bool = False,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """What one sync actually did, line by line.

    ``problems_only`` filters to warnings and errors — the first question about
    a run that took an hour and failed is usually "what went wrong", and the
    answer is a handful of lines inside several thousand.
    """
    levels = {"WARNING", "ERROR", "CRITICAL"} if problems_only else None
    run, rows, total, stored = _log_rows(
        session, principal.organization_id, sync_run_id,
        after_seq=after_seq, limit=max(1, min(limit, LOG_PAGE)), levels=levels)
    return {
        "sync_run_id": run.sync_run_id,
        "status": run.status,
        "phase": run.phase,
        # Still going, so the reader knows an empty tail means "nothing new"
        # rather than "the log ends here".
        "running": run.status in jobs.ACTIVE,
        "started_at": clock.iso(run.started_at),
        "finished_at": clock.iso(run.finished_at),
        "total": total,
        "lines": rows,
        # Where to resume from. -1 rather than 0 when nothing came back, so a
        # first poll against an empty log asks for the same window again
        # instead of skipping line 0.
        "next_seq": rows[-1]["seq"] if rows else after_seq,
        # Said plainly rather than left to be inferred from a line count: a run
        # from before this table existed holds no log, and an empty panel that
        # cannot say why reads as "the sync did nothing". Built from `stored`
        # and never from `total` — see `_log_note`.
        "note": _log_note(run, stored),
    }


def _log_note(run: models.SyncRun, stored: int) -> Optional[str]:
    """Why a log is empty, when it is. Absence needs a reason, not a blank box.

    ``stored`` is how many lines the run kept **in total**, never how many this
    request returned. The two differ the moment ``problems_only`` is on, and
    this sentence used to be built from the filtered count — so a run that
    completed cleanly *with* a full log was told it "kept no log", because the
    filter had removed every line and nothing here could tell that apart from a
    run that recorded nothing. A claim about the run made out of a fact about
    the view: exactly the benign default CLAUDE.md §1 rules out, and in the
    direction that reads as reassurance.

    What a filter left is the caller's own question and stays with the caller —
    only it knows what it asked for. This answers the run's question.
    """
    if stored:
        return None
    if run.status in jobs.ACTIVE:
        return "This run has not written its first log lines yet."
    return ("This run kept no log — it ran before its log was stored with it. "
            "Runs from here on record what they did; re-sync to produce one.")


@router.get("/sync-runs/{sync_run_id}/log.txt")
def sync_run_log_text(
    sync_run_id: str,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> Response:
    """The whole log as a plain text file, to read elsewhere or send on.

    Built server-side for the reason the skipped-rows CSV is: a file assembled
    from what the screen is showing is a page calling itself the record. This
    walks the run in pages so a very long pull's log does not have to be held
    in memory twice over.
    """
    org = principal.organization_id
    lines: list[str] = []
    # `stored` is the same on every page — it counts the run's lines, not this
    # page's — so the last one read is the whole log's count. Seeded here for
    # the reason `run` is: the note below is written whether or not the loop
    # found anything to page through.
    after, run, stored = -1, None, 0
    while True:
        run, rows, _total, stored = _log_rows(session, org, sync_run_id,
                                              after_seq=after, limit=LOG_PAGE)
        if not rows:
            break
        lines.extend(f"{r['at']} {r['level']:<8} {r['logger']}: {r['message']}"
                     for r in rows)
        after = rows[-1]["seq"]

    header = [
        f"# sync run {sync_run_id}",
        f"# organization {org}",
        f"# status {run.status if run else 'unknown'}",
        f"# started {clock.iso(run.started_at) if run else ''}",
        f"# finished {clock.iso(run.finished_at) if run else ''}",
        "",
    ]
    if run is not None and run.error:
        header[-1:] = [f"# error {run.error}", ""]
    note = _log_note(run, stored) if run is not None else None
    if note:
        header[-1:] = [f"# {note}", ""]

    started = run.started_at.date().isoformat() if run and run.started_at else "run"
    name = f"sync-log-{started}-{sync_run_id[:8]}.txt"
    return Response(
        content="\n".join(header + lines) + "\n",
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ── the catalogue, per connected company ─────────────────────────────────────
#
# The nomenclature side of the data this platform runs on, one catalogue per
# connected company. Until this existed the catalogue was visible only to
# whoever knew to run `python scripts/build_catalog.py` — nothing in the
# product could say whether one existed, what built it, or rebuild it. It lives
# in this router because it is the same question the rest of the file answers
# ("what data is this deployment running on, and how fresh"); a `catalog.py`
# router would be a second home for the data-status concern.
#
# The setup path it gives: a company uploads a price list, saves the config
# that decodes it, and builds. There is no deployment-wide catalogue behind
# these any more — a company with no corpus resolves nothing, which is the
# honest answer (see `docs/per-company-catalogues.md` §6).
#
# **No `python-multipart`.** The corpus arrives as a raw request body rather
# than a multipart form, so the dependency `master_health/__init__.py` refuses
# stays refused. Only the "no upload endpoint" half of that refusal is
# overturned, and only because its own justification does not transfer: it
# reasons that a path argument already serves a person running a diagnostic,
# and an owner in a browser has no shell to supply one from.


def _company_dict(session: Session, connection: models.ZohoConnection,
                  principal: Principal) -> dict[str, Any]:
    """One connected company: every catalogue it keeps, and the union it
    resolves against."""
    from .. import catalog

    return {
        "connection_id": connection.connection_id,
        "label": connection.label or "",
        "enabled": connection.enabled,
        "scope": "company",
        **catalog.company_state(
            session, principal.organization_id, connection.connection_id),
    }


@router.get("/catalog/companies")
def catalog_companies(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(get_session),
) -> dict:
    """Every connected company, each of its catalogues with every price
    list's decoding config and state, and the union each company resolves
    against.

    Readable by any signed-in user, like `/catalog` and `/status`: which
    catalogue answered a resolution is the same entitlement as knowing when the
    books last arrived. Only the setup actions below are owner-scoped.
    """
    from .. import catalog
    from ..ingestion.connections import list_connections

    return {
        "scope": "company",
        "companies": [_company_dict(session, c, principal)
                      for c in list_connections(session, principal.organization_id)],
        # What a decoding config may name. Shipped, never uploaded — a rule
        # set is regexes the engine runs over every row, and accepting one
        # from a tenant is accepting arbitrary patterns to execute.
        "rule_sets": catalog.available_rule_sets(),
        "source": catalog.source_state(),
        "max_corpus_bytes": catalog.MAX_CORPUS_BYTES,
        "max_catalogues": catalog.MAX_CATALOGUES,
        "can_manage": principal.role is Role.OWNER,
    }


@router.get("/catalog/aliases")
def phrase_aliases(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """What the system remembers for this organization's customers: every
    active phrase alias, newest first, with the customer it was quoted for.

    Managers may read it — it is the sales team's own vocabulary, and a
    manager needs to see what a salesperson taught. Retiring one is an
    owner's act, below.
    """
    from ..identity import service as identity_service

    labels = {row.identity_id: row.label for row in session.scalars(
        select(models.CustomerIdentity).where(
            models.CustomerIdentity.organization_id == principal.organization_id))}
    rows = identity_service.active_phrase_aliases(session, principal.organization_id)
    rows.sort(key=lambda r: (r.created_at, r.alias_id), reverse=True)
    return {
        "aliases": [{
            "alias_id": r.alias_id,
            "identity_id": r.identity_id,
            "customer": labels.get(r.identity_id) or r.identity_id,
            "phrase": r.phrase,
            "target_record_id": r.target_record_id,
            "source_ref": r.source_ref,
            "recorded_by": r.recorded_by_user_id,
            "created_at": clock.iso(clock.aware(r.created_at)),
        } for r in rows],
        "can_manage": principal.role is Role.OWNER,
    }


@router.delete("/catalog/aliases/{alias_id}")
def retire_phrase_alias(
    alias_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Stop offering a remembered phrase. Deactivates; never deletes."""
    from ..identity import service as identity_service

    row = identity_service.retire_phrase_alias(
        session, principal.organization_id, alias_id=alias_id,
        user_id=principal.user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No such remembered phrase in this organization.")
    return {"retired": row.alias_id}


@router.get("/catalog/retrieval-report")
def retrieval_report(
    since: Optional[date] = None,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """How the suggestion layers are doing for this organization, from the
    quotes it has stored — the numbers the next investment decisions rest on.
    See ``app/retrieval/report.py`` for what each one counts."""
    from ..retrieval import report

    return report.report_for(session, principal.organization_id, since=since)


def _company(session: Session, principal: Principal, connection_id: str):
    """The caller's own connection, or a 404 that does not confirm it exists."""
    from ..ingestion.connections import ConnectionNotFound, get_connection

    try:
        return get_connection(session, principal.organization_id, connection_id)
    except ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No such company in this organization.") from e


def _catalogue(session: Session, principal: Principal, connection_id: str,
               catalogue_key: str):
    """One of this company's catalogues, or a 404 that confirms nothing.

    The company is checked first, so a connection from another tenant reads as
    "no such company" — the same answer an invented id gets — rather than as a
    catalogue lookup that could tell the two apart.
    """
    from .. import catalog

    connection = _company(session, principal, connection_id)
    row = catalog.catalogue_row(session, principal.organization_id, connection_id,
                                catalogue_key)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "This company has no catalogue by that key.")
    return connection, row


class CatalogueRequest(BaseModel):
    """A new catalogue: the manufacturer's name. Nothing about decoding — each
    price list uploaded into it brings its own decoding config."""

    name: str


@router.post("/catalog/companies/{connection_id}/catalogues")
def create_company_catalogue(
    connection_id: str,
    body: CatalogueRequest,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Add a catalogue to this company — one per manufacturer it sells.

    A distributor carries Kennametal and YG-1 and more, so each is its own
    catalogue: that manufacturer's product universe, built from the price
    lists uploaded into it. The company resolves against all of them at once.
    Nothing is uploaded, built or decided about decoding here — every file
    brings its own decoding config — this only defines the catalogue the files
    and the build then belong to.

    Refused with the reason when the name yields no key, the key is already in
    use, or the company is at its ceiling.
    """
    from .. import catalog

    connection = _company(session, principal, connection_id)
    try:
        catalog.create_catalogue(session, principal.organization_id, connection_id,
                                 body.name)
    except catalog.CatalogueError as e:
        raise HTTPException(e.status, str(e)) from e
    return _company_dict(session, connection, principal)


class CatalogueRenameRequest(BaseModel):
    """The name a catalogue is shown under."""

    name: str


@router.patch("/catalog/companies/{connection_id}/catalogues/{catalogue_key}")
def rename_company_catalogue(
    connection_id: str,
    catalogue_key: str,
    body: CatalogueRenameRequest,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Rename a catalogue. The key — its address on disk and in every corpus
    row — stays; only what the screen calls it changes. The migrated
    catalogue arrives with no name and is shown under its key until it is
    given one here."""
    from .. import catalog

    connection = _company(session, principal, connection_id)
    try:
        catalog.rename_catalogue(session, principal.organization_id, connection_id,
                                 catalogue_key, body.name)
    except catalog.CatalogueError as e:
        raise HTTPException(e.status, str(e)) from e
    return _company_dict(session, connection, principal)


@router.delete("/catalog/companies/{connection_id}/catalogues/{catalogue_key}")
def remove_company_catalogue(
    connection_id: str,
    catalogue_key: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Stop this company resolving against one manufacturer's catalogue.

    Its files are superseded rather than deleted, on the same reasoning as
    every other change to a corpus; its decoded output goes, being derived;
    and the union is refreshed at once, because a manufacturer this company no
    longer sells must not keep answering until somebody rebuilds.
    """
    from .. import catalog

    connection = _company(session, principal, connection_id)
    try:
        catalog.delete_catalogue(session, principal.organization_id, connection_id,
                                 catalogue_key)
    except catalog.CatalogueError as e:
        raise HTTPException(e.status, str(e)) from e
    return _company_dict(session, connection, principal)


@router.post("/catalog/companies/{connection_id}/catalogues/{catalogue_key}/corpus")
def upload_company_corpus(
    connection_id: str,
    catalogue_key: str,
    request: Request,
    filename: str = "",
    source_key: str = "",
    principal: Principal = Depends(require_owner),
    payload: bytes = Body(default=b""),
    session: Session = Depends(get_session),
) -> dict:
    """Store one of the files this catalogue is built from.

    The bytes are kept as a row rather than a file. The container filesystem is
    ephemeral — `railway.json` declares no volume — and today that costs
    nothing because the shipped corpus lives in the image and the catalogue is
    derived from it. An uploaded corpus has no such source: on container disk it
    is gone on the next deploy, and the catalogue could then never be rebuilt.

    Append-only: an upload supersedes rather than overwrites, so a catalogue
    already built keeps a real referent for the corpus its stamp names.

    **`source_key` decides what is being replaced**, and the two cases are
    deliberately different:

    * named — this file *is* that source. Only that one is superseded, so
      uploading a second price list leaves the item master alone. This is how a
      catalogue is built out of several exports.
    * absent — the older meaning: replace the whole export. Every live source
      of this catalogue is superseded. Kept because "Replace export" on a
      catalogue with one file is still the common action, and silently turning
      it into "add a second file" would leave a company resolving against a
      merge it never asked for.

    Scoped to one catalogue: a Kennametal price list goes into the Kennametal
    catalogue and supersedes nothing in YG-1's.

    **Every upload starts as an unknown format.** The file is analysed on its
    own before the row is written — which of its columns are read, and which
    of the engine's shipped rule sets decode it, with the parser's counts for
    each — and what comes back is a *proposal*, stored beside the file for a
    person to check and save. Nothing is decoded until they do, and nothing is
    inherited from the company, the catalogue or a default.

    CSV or Excel. A workbook is read with ``openpyxl``, which is already a
    dependency; ``python-multipart`` is still not, because the browser sends the
    file as a raw body and one file needs no form fields.
    """
    from .. import catalog
    from ..ingestion.item_master import ItemMasterError

    connection, _row = _catalogue(session, principal, connection_id, catalogue_key)

    # The declared length first, so an oversize body is refused by its header
    # rather than after it has been read. The real ceiling belongs at the proxy;
    # this is the honest answer from the application, not the only defence.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > catalog.MAX_CORPUS_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"That file is larger than the {catalog.MAX_CORPUS_BYTES // (1024 * 1024)} MB "
            f"limit for an item-master export.")
    if len(payload) > catalog.MAX_CORPUS_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"That file is larger than the {catalog.MAX_CORPUS_BYTES // (1024 * 1024)} MB "
            f"limit for an item-master export.")

    name = (filename or "item-master.csv")[:255]
    content_type = (request.headers.get("content-type") or "")[:128]
    key = (source_key or name)[:128]

    live = catalog.current_corpora(session, principal.organization_id, connection_id,
                                   catalogue_key)
    # Counted before the file is read, so a catalogue at the ceiling is told so
    # rather than made to wait for the read of a file that will be refused.
    if source_key and len(live) >= catalog.MAX_SOURCES and not any(
            catalog.source_key_of(s) == key for s in live):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This catalogue already has {len(live)} source files, which is the "
            f"limit of {catalog.MAX_SOURCES}. Replace one of them, or remove "
            f"one first.")

    try:
        analysis = catalog.analyze_source(payload, name, content_type)
    except ItemMasterError as e:
        # The reason, verbatim: it names the column it could not find and lists
        # the headers the file does have, which is what makes it actionable.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e

    digest = hashlib.sha256(payload).hexdigest()
    now = clock.now()
    for previous in live:
        if not source_key or catalog.source_key_of(previous) == key:
            previous.superseded_at = now
    session.add(models.CompanyCorpus(
        organization_id=principal.organization_id,
        connection_id=connection_id,
        catalogue_key=catalogue_key,
        source_key=key,
        filename=name,
        content_type=content_type,
        size_bytes=len(payload),
        sha256=digest,
        content=payload,
        mapping=analysis["columns"],
        ingest=analysis["ingest"],
        # The proposal, unconfirmed: a rule set only where the file's own
        # evidence chose one, and never saved on the person's behalf.
        rule_set=analysis["proposed"],
        analysis={k: analysis[k] for k in ("sample_rows", "candidates",
                                            "proposed", "reason")},
        uploaded_by=principal.user_id,
        uploaded_at=now,
    ))
    session.flush()
    return _company_dict(session, connection, principal)


def _source(session: Session, principal: Principal, connection_id: str,
            catalogue_key: str, source_key: str):
    """One of this catalogue's live sources, by key.

    Scoped through ``current_corpora``, which puts the organization in the query
    rather than checking it afterwards — a key from another tenant reads as
    absent rather than as a permission error that confirms it exists.
    """
    from .. import catalog

    for row in catalog.current_corpora(session, principal.organization_id,
                                       connection_id, catalogue_key):
        if catalog.source_key_of(row) == source_key:
            return row
    raise HTTPException(status.HTTP_404_NOT_FOUND,
                        "This catalogue has no source file by that name.")


class DecodingConfigRequest(BaseModel):
    """One file's decoding config, as a person saves it: which of its columns
    hold the record id, description and grade, and what decodes its
    descriptions.

    Exactly one of ``rule_set`` (a shipped pack) and ``decoder`` (an artifact
    built from this file) may be given, and both may be omitted — the honest
    state for a file nothing reads yet, which then waits by name. Both at once
    is refused in ``catalog.confirm_decoding``, where the sentence saying why
    lives.

``decoder`` is the artifact **as ``propose-decoder`` returned it** — its
    own id must still match its contents, so it is a decoder this deployment
    produced. The review a person made arrives beside it: ``bindings`` is the
    confirmed slot and type per capture group, and ``decimal`` is which
    reading of ``11,1`` they chose. Both are applied server-side and the
    result is re-frozen under its own id.

    The patterns are deliberately not editable through this. A binding names
    an attribute and the review exists to catch a wrong one; a pattern is a
    regular expression that runs over every row of every rebuild, and not
    offering that door is better than relying on the safety check behind it.
    """

    record_id: str
    description: str
    grade: Optional[str] = None
    rule_set: Optional[str] = None
    decoder: Optional[dict] = None
    bindings: Optional[list[dict]] = None
    decimal: Optional[str] = None


@router.put("/catalog/companies/{connection_id}/catalogues/{catalogue_key}"
            "/sources/{source_key}/decoding")
def save_source_decoding(
    connection_id: str,
    catalogue_key: str,
    source_key: str,
    body: DecodingConfigRequest,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Save one file's decoding config — the step between shown and decoded.

    The analysis at upload proposes the columns from the headers and a rule
    set from the parser's own counts, and a proposal is sometimes wrong — a
    master carrying both ``Old Code`` and ``Item Code``, a sheet whose
    description column is called ``Particulars``, a file two rule sets both
    read. Saving here re-reads the stored bytes with the columns given and
    refuses one naming a column the file does not have, and refuses a rule
    set the engine does not ship, so a config that cannot build is never
    stored. A ``decoder`` is re-frozen and its content id re-derived, so one
    edited between the review and here is refused rather than stored under an
    id that no longer describes it. Until it is saved, the file is not decoded.

    The bytes are not touched, and the source is not superseded: this changes
    how a file is *read*, and superseding it would say a different file had
    arrived.
    """
    from .. import catalog
    from ..ingestion.item_master import ItemMasterError

    connection, _row = _catalogue(session, principal, connection_id, catalogue_key)
    row = _source(session, principal, connection_id, catalogue_key, source_key)
    wanted = {"record_id": body.record_id, "description": body.description,
              "grade": body.grade or None}
    try:
        catalog.confirm_decoding(session, row, wanted, body.rule_set,
                                 actor=principal.user_id, decoder=body.decoder,
                                 bindings=body.bindings, decimal=body.decimal)
    except ItemMasterError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except catalog.CatalogueError as e:
        raise HTTPException(e.status, str(e)) from e
    return _company_dict(session, connection, principal)


@router.post("/catalog/companies/{connection_id}/catalogues/{catalogue_key}"
             "/sources/{source_key}/analyze")
def analyze_source_decoding(
    connection_id: str,
    catalogue_key: str,
    source_key: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Analyse one stored file again, from its bytes alone.

    Every shipped rule set is run over its first rows and the parser's counts
    reported for each — the same discovery an upload runs — so a file stored
    before rule sets were analysed, or one whose columns were corrected, gets
    a fresh proposal. Writes the proposal beside the file and nothing else:
    a saved config stays saved, and the proposal stays a proposal until a
    person saves it.

    Owner-only, like the build: it spends a parse per rule set on request.
    """
    from .. import catalog
    from ..ingestion.item_master import ItemMasterError

    connection, _row = _catalogue(session, principal, connection_id, catalogue_key)
    row = _source(session, principal, connection_id, catalogue_key, source_key)
    try:
        analysis = catalog.analyze_source(row.content, row.filename,
                                          row.content_type or "",
                                          columns=row.mapping)
    except ItemMasterError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    row.analysis = {k: analysis[k] for k in ("sample_rows", "candidates",
                                              "proposed", "reason")}
    row.ingest = analysis["ingest"]
    if row.decoding_confirmed_at is None:
        row.mapping = analysis["columns"]
        row.rule_set = analysis["proposed"]
    session.flush()
    return _company_dict(session, connection, principal)


@router.post("/catalog/companies/{connection_id}/catalogues/{catalogue_key}"
             "/sources/{source_key}/propose-decoder")
def propose_source_decoder(
    connection_id: str,
    catalogue_key: str,
    source_key: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Read one stored file and propose a decoder built for it alone.

    The other half of discovery, and the one that needs no shipped grammar:
    the file's descriptions are clustered into shapes, a pattern is induced
    per shape and validated over every row, each captured group is measured,
    and the groups whose meaning the file's own text settles are named. The
    rest come back with their evidence and the slots they could be, for a
    person to answer.

    **Proposes and returns; saves nothing.** The source row is not touched —
    ``PUT .../decoding`` with the reviewed artifact is what stores it, exactly
    as the rule-set half works. So a proposal can be asked for twice, and
    compared, without changing what the file currently decodes through.

    Owner-only, and it spends real work on request: inference over the whole
    file plus, where a live provider is configured, a call per batch of groups
    it could not name from the text.
    """
    from .. import catalog
    from ..ingestion.item_master import ItemMasterError

    _connection, _row = _catalogue(session, principal, connection_id, catalogue_key)
    row = _source(session, principal, connection_id, catalogue_key, source_key)
    try:
        return catalog.propose_decoder(session, row, principal.organization_id)
    except ItemMasterError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except catalog.CatalogueError as e:
        raise HTTPException(e.status, str(e)) from e


@router.delete("/catalog/companies/{connection_id}/catalogues/{catalogue_key}"
               "/sources/{source_key}")
def remove_company_source(
    connection_id: str,
    catalogue_key: str,
    source_key: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Stop building this catalogue from one of its files.

    Superseded, not deleted, on the same reasoning as an upload: a catalogue
    built from this source keeps a real referent for what its stamp names. The
    built catalogue is left alone and goes OUT OF DATE — removing a source
    changes what a build *would* read, and rebuilding here would replace what a
    company resolves against as a side effect of tidying a file list.
    """
    connection, _row = _catalogue(session, principal, connection_id, catalogue_key)
    row = _source(session, principal, connection_id, catalogue_key, source_key)
    row.superseded_at = clock.now()
    session.flush()
    return _company_dict(session, connection, principal)


@router.post("/catalog/companies/{connection_id}/catalogues/{catalogue_key}/build")
def build_company_catalog(
    connection_id: str,
    catalogue_key: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Decode this catalogue's stored files, each through its own saved
    decoding config, and refresh the union the company resolves against.

    Refused by name when any file has no saved config — there is no default
    rule set to fall back to, and the answer says which file to open.

    Synchronous, on a measurement rather than an assumption: the shipped
    6,717-row corpus parses and writes in under two seconds in-process, and
    the retrieval index built beside the union (``app/retrieval``) takes under
    three more, so there is no job to poll and no long-running database write
    to phase-commit — the response carries the finished result.
    """
    from .. import catalog
    from ..ingestion.item_master import ItemMasterError

    connection, _row = _catalogue(session, principal, connection_id, catalogue_key)
    try:
        catalog.build_for_company(session, principal.organization_id,
                                  connection_id, actor=principal.user_id,
                                  catalogue_key=catalogue_key)
    except catalog.CatalogueError as e:
        raise HTTPException(e.status, str(e)) from e
    except ItemMasterError as e:
        # One of the files stopped being readable — usually a mapping whose
        # column a re-upload renamed. Its own message, which names the file,
        # rather than the parser failure below: nothing has reached the parser.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise HTTPException(
                status.HTTP_507_INSUFFICIENT_STORAGE,
                f"The catalogue could not be written: the disk is full ({e}).",
            ) from e
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"The catalogue could not be written: {e}") from e
    except Exception as e:  # noqa: BLE001 — a parse failure is reported, not a bare 500
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"The parser failed to build this catalogue: {type(e).__name__}: {e}",
        ) from e
    return _company_dict(session, connection, principal)


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def run_sync(
    req: SyncRequest = Body(default_factory=SyncRequest),
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Queue a pull and return immediately.

    A real organization's first sync reads every invoice and bill individually
    and takes minutes, so this hands back a job to watch rather than holding the
    request open. 202, not 200: the work is accepted, not done.

    A second request while one is running does not error and does not start a
    second pull — it returns the job already in flight, so the screen can show
    that one. "Sync is in progress" without saying which sync, since when, or
    how far along is the message this replaces.
    """
    from ..ingestion.zoho_client import configured_since

    org = principal.organization_id
    since = req.since or configured_since()

    # A database missing sync_runs.connection_id fails on the INSERT below and
    # then again while recording the failure, which escapes as a bare 500 with
    # no body. Checked first so the answer is a sentence with the fix in it.
    from ..schema_check import FIX, missing_columns

    gap = missing_columns(session.get_bind()).get("sync_runs") or []
    if gap:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"This database is missing sync_runs columns ({', '.join(gap)}), so a "
            f"pull cannot record its own progress. Run `{FIX}` against it and try "
            f"again. Nothing already synced is affected.")

    run, started = jobs.start_sync(
        session, org, since=since, full=req.full,
        connection_id=req.connection_id, triggered_by=principal.user_id)

    return {
        **_sync_state(session, org),
        "started": started,
        "run": _run_dict(run),
        "note": ("The sync is running in the background — you can carry on "
                 "using the platform and this will keep itself up to date."
                 if started else
                 "A sync was already running, so this did not start another."),
    }
