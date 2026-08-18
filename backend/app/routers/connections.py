"""Zoho connections — add, check, rename, enable, delete, and pull from any of them.

An organization now has as many Zoho companies as it has books to read. This is
the surface an owner manages them from.

Three things this makes visible that the single-connection screen could not:

**Which company.** "The organization is connected" stops meaning anything once
there are three and one has a revoked token, so health is per connection and the
list says which one is stale.

**Scopes.** A half-granted scope is the most common reason a connection
authenticates and then returns nothing: the token works, the endpoint 401s, and
the sync reports zero rows with no obvious cause. The required scopes are listed
with what each one buys, and a check reports what the grant can actually reach.

**What sharing costs.** Rows from every connection on an organization are
analysed together. That is said on the screen, not left to be discovered.
"""
from __future__ import annotations

import inspect
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .. import clock, entitlements
from ..authz import Principal, require_manager_or_owner, require_owner
from ..commercial import jurisdiction
from ..config import settings
from ..db import get_session
from ..domain import models
from ..domain.enums import Role
from ..ingestion import connections as conn
from ..ingestion.zoho_client import ZohoApiSource, ZohoAuthError

log = logging.getLogger("pie_portal.connections")


class SchemaBehind(RuntimeError):
    """The database is older than the code reading it."""


def _schema_guard(call):
    """Turn a database that is behind into a sentence someone can act on.

    Every endpoint here goes through it, because the failure is not specific
    to one of them: a bare 500 with an empty body is what a browser console
    shows, and it names neither the cause nor the fix.
    """
    # The wrapper must match the endpoint's sync/async nature. A plain `def`
    # wrapper around an `async def` endpoint returns an un-awaited coroutine;
    # FastAPI runs the sync wrapper in a threadpool and tries to serialize that
    # coroutine as the body — a ResponseValidationError (500), the real handler
    # never executing. So await when the wrapped call is a coroutine function and
    # stay synchronous otherwise. Every endpoint here is sync today; this keeps
    # the guard correct if that ever changes.
    if inspect.iscoroutinefunction(call):
        async def wrapper(*args, **kwargs):
            try:
                return await call(*args, **kwargs)
            except SchemaBehind as e:
                log.error("schema behind: %s", e)
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    else:
        def wrapper(*args, **kwargs):
            try:
                return call(*args, **kwargs)
            except SchemaBehind as e:
                log.error("schema behind: %s", e)
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
    wrapper.__name__ = call.__name__
    wrapper.__doc__ = call.__doc__
    # FastAPI reads the signature to build the dependency graph, so it has to
    # survive the wrapping.
    wrapper.__signature__ = inspect.signature(call)
    wrapper.__annotations__ = call.__annotations__
    return wrapper


class _Router(APIRouter):
    """An APIRouter that wraps every endpoint in the schema guard."""

    def add_api_route(self, path, endpoint, **kwargs):  # type: ignore[override]
        return super().add_api_route(path, _schema_guard(endpoint), **kwargs)


router = _Router(prefix="/api/v1/connections", tags=["connections"])


#: How far back a first pull reads when nobody has said otherwise. Eighteen
#: months gives the detectors a full recent window, a full comparison window,
#: and room above the six-month history floor — so the first sync produces an
#: analysis rather than a screen full of "not enough history".
DEFAULT_HISTORY_MONTHS = 18


def _default_since() -> date:
    today = date.today()
    year, month = divmod(today.year * 12 + (today.month - 1) - DEFAULT_HISTORY_MONTHS, 12)
    return date(year, month + 1, 1)


def _covered_from(session: Session, row: models.ZohoConnection) -> Optional[str]:
    """The earliest date this company has been listed from, as an ISO string.

    Delegates rather than re-deriving. ``ReadModelRepository.covered_since`` is
    what the sync itself consults to decide whether the incremental short-circuit
    is valid for a window, and a screen that computed the same floor a second way
    could disagree with the pull it is describing — telling somebody a date is
    cheap while the sync lists those months in full, or the reverse.
    """
    from ..repositories import ReadModelRepository

    floor = ReadModelRepository(session, row.organization_id,
                                connection_id=row.connection_id).covered_since()
    return floor.isoformat() if floor else None


def _last_run(session: Session, row: models.ZohoConnection) -> Optional[models.SyncRun]:
    """The most recent pull aimed at this company specifically.

    Runs that covered every connection are excluded on purpose: this answers
    "when did *this* company last come in", and a company that has never been
    pulled individually should say so rather than borrow another run's date.

    ``sync_runs`` has gained columns since this table existed, so a deployment
    running new code against an un-migrated database fails here — and used to
    fail as a bare 500 with no body, unreadable from a browser console. Worse,
    it only appeared once a connection existed: with none, the query never ran
    and the screen looked healthy.

    The missing columns are looked up rather than guessed. An earlier version
    matched the error text for "connection_id", which appears in the SELECT
    list of *every* such failure — so a database missing ``notes`` was told to
    go and fix ``connection_id``, which it already had. A diagnostic that names
    the wrong thing is worse than a generic one.
    """
    try:
        return session.scalars(
            select(models.SyncRun)
            .where(models.SyncRun.organization_id == row.organization_id,
                   models.SyncRun.connection_id == row.connection_id)
            .order_by(models.SyncRun.started_at.desc())
            .limit(1)).first()
    except OperationalError as e:
        session.rollback()   # Postgres aborts the whole transaction otherwise
        from ..schema_check import FIX, missing_columns

        gaps = missing_columns(session.get_bind())
        if not gaps:
            raise                      # a real database error, not a stale schema
        detail = "; ".join(f"{t} ({', '.join(cols)})" for t, cols in sorted(gaps.items()))
        raise SchemaBehind(
            f"This database is behind the code — missing {detail}. Run "
            f"`{FIX}` against it and reload. No data is lost.") from e


def _dict(session: Session, row: models.ZohoConnection) -> dict:
    from ..domain.origin import CONNECTORS, fallback_company_label

    cred = row.credential
    last = _last_run(session, row)
    connector = getattr(row, "connector", None) or conn.ZOHO_CONNECTOR
    connector_label = CONNECTORS.get(connector, {}).get("label") or connector
    return {
        "connector": connector,
        "connector_label": connector_label,
        # The date this company was last read from, and the date to offer next
        # time. Carried per connection because the answer genuinely differs:
        # one entity may have four years of books worth reading and another
        # four months, and a single date box for all of them either over-reads
        # or under-reads at least one.
        "last_sync": None if last is None else {
            "status": last.status,
            "started_at": clock.iso(last.started_at),
            "since": last.since.isoformat() if last.since else None,
            "sales_txns": last.sales_txns,
            "cost_records": last.cost_records,
            "error": last.error,
        },
        "suggested_since": (last.since.isoformat() if last is not None and last.since
                            else _default_since().isoformat()),
        # How far back this company has actually been *listed*, which is not
        # what the last run asked for. A nightly pull can run for a year and
        # still cover only the window the first run wanted, so "last pulled
        # from 2025-01-01" says nothing about whether 2024 was ever read.
        #
        # It is also what decides the cost of the next pull: a date at or after
        # this one is a cheap incremental, an earlier one lists those months in
        # full. The screen says which before the button is pressed.
        "covered_from": _covered_from(session, row),
        "connection_id": row.connection_id,
        "label": row.label or fallback_company_label(connector,
                                                     row.zoho_organization_id),
        "zoho_organization_id": row.zoho_organization_id,
        "enabled": row.enabled,
        "credential_id": row.credential_id,
        # An identifier, not a secret — it is what tells two grants apart in a list.
        "client_id": cred.client_id if cred else row.client_id,
        "credential_label": ((cred.label or f"{connector_label} connection")
                             if cred else "inline (legacy)"),
        # The connection's own non-secret settings (company GUID, branch,
        # endpoint …) so an owner can see what was entered. Secrets are not
        # here and are not anywhere else in a response either.
        "config": dict(row.config or {}) or None,
        "credential_rotated_at": (clock.iso(cred.rotated_at)
                                  if cred and cred.rotated_at else None),
        "accounts_base": row.accounts_base,
        "api_base": row.api_base,
        # What this company trades in, as Zoho reported it at the last check.
        # null means it has not been checked since this was recorded — which is
        # not the same as "agrees with the roll-up", and the screen should not
        # render it as though it were.
        "base_currency": row.base_currency,
        "last_checked_at": clock.iso(row.last_checked_at),
        "last_check_ok": row.last_check_ok,
        "last_check_detail": row.last_check_detail,
        "created_at": clock.iso(row.created_at),
    }


@router.get("")
def list_connections(
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    org = principal.organization_id
    rows = conn.list_connections(session, org)
    credentials = conn.usable_credentials(session, org)
    return {
        "connections": [_dict(session, r) for r in rows],
        "credentials": [
            {"credential_id": c.credential_id,
             "connector": getattr(c, "connector", None) or conn.ZOHO_CONNECTOR,
             "label": c.label or "Zoho connection",
             "client_id": c.client_id,
             "is_owner": c.owner_organization_id == org,
             "rotated_at": clock.iso(c.rotated_at),
             "used_by": len(conn.connections_using(session, c.credential_id))}
            for c in credentials
        ],
        "can_manage": principal.role is Role.OWNER,
        "source_mode": settings.ZOHO_SOURCE,
        # Said on the screen rather than left to be discovered.
        "pooling_note": (
            "Every enabled connection here feeds this organization's analysis. "
            "Revenue and margin roll up across all of them. To keep legal "
            "entities apart, give each its own organization instead."),
    }


class NewConnection(BaseModel):
    """Either supply a credential already on file, or a fresh set of secrets."""

    zoho_organization_id: str = Field(min_length=1, max_length=64)
    label: str = ""
    credential_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    accounts_base: str = "https://accounts.zoho.in"
    api_base: str = "https://www.zohoapis.in/books/v3"


@router.post("", status_code=status.HTTP_201_CREATED)
def add_connection(
    body: NewConnection,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Add a Zoho company.

    Reusing a credential is the normal path for the second and third company: a
    Zoho refresh token belongs to a user and already reaches every company that
    user can see, so re-entering it would only create a copy for a future
    rotation to miss.
    """
    org = principal.organization_id
    zoho_org = body.zoho_organization_id.strip()

    try:
        if body.credential_id:
            row = conn.add_connection(
                session, org, credential_id=body.credential_id,
                zoho_organization_id=zoho_org, label=body.label)
        else:
            missing = [n for n, v in (("client_id", body.client_id),
                                      ("client_secret", body.client_secret),
                                      ("refresh_token", body.refresh_token))
                       if not (v or "").strip()]
            if missing:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Choose an existing connection, or supply "
                    + ", ".join(missing) + ".")
            row = conn.set_zoho_credentials(
                session, org, zoho_organization_id=zoho_org,
                client_id=body.client_id.strip(),
                client_secret=body.client_secret.strip(),
                refresh_token=body.refresh_token.strip(),
                accounts_base=body.accounts_base.strip().rstrip("/"),
                api_base=body.api_base.strip().rstrip("/"),
                label=body.label,
                # The grant is named after itself, not after the first company
                # it happened to connect — it will very likely serve others.
                credential_label=f"Zoho sign-in {body.client_id.strip()[:14]}")
    except conn.CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except entitlements.PlanRefused as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    _check(session, row)
    return _dict(session, row)


class EditConnection(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = None


@router.patch("/{connection_id}")
def edit_connection(
    connection_id: str,
    body: EditConnection,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    try:
        row = conn.update_connection(session, principal.organization_id, connection_id,
                                     label=body.label, enabled=body.enabled)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return _dict(session, row)


@router.delete("/{connection_id}")
def delete_connection(
    connection_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Remove a connection. Data already pulled through it stays.

    Deliberately not a cascade: rows already synced are facts about what was
    invoiced, and disconnecting is an administrative act about credentials, not
    a decision to forget trading history.
    """
    try:
        row = conn.delete_connection(session, principal.organization_id, connection_id)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return {"removed": True, "connection_id": row.connection_id,
            "note": "Data already pulled through this connection is unchanged."}


class RotateToken(BaseModel):
    """A fresh Zoho grant. The refresh token is the thing that actually expires
    or gets revoked; the client pair is optional because it usually has not
    changed and re-typing a secret that is already correct is how a working
    connection gets broken."""

    refresh_token: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


@router.post("/{connection_id}/rotate")
def rotate_connection_token(
    connection_id: str,
    body: RotateToken,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Replace the Zoho grant this connection signs in with.

    **Rotation lives here, on the connection, because it is a Zoho mechanism
    rather than a platform one.** A refresh token that can be revoked and
    reissued is how Zoho's OAuth works; another connector might use a static
    key, a certificate, or nothing that is ever rotated at all. It was
    previously a "credentials" surface of its own, which made a Zoho detail
    look like a concept every connector would need — and put the control on a
    different screen from the thing somebody was looking at when they decided
    to rotate.

    **One grant can reach several companies, and rotating from any of them
    rotates it for all of them.** That is deliberate — it is what makes
    rotating after a leak one operation rather than three — but it must not be
    a surprise, so the response names every connection that just changed
    underneath. The arithmetic is not repeated here: this delegates to
    ``ingestion.connections.rotate_credential``, which is where the ownership
    rule lives.
    """
    if not body.refresh_token.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "A refresh token is required")
    try:
        row = conn.get_connection(session, principal.organization_id, connection_id)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    if (getattr(row, "connector", None) or conn.ZOHO_CONNECTOR) != conn.ZOHO_CONNECTOR:
        # Writing a refresh token onto another connector's credential would
        # corrupt a working sign-in with a value its client never reads.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This connection does not sign in with a Zoho refresh token — "
            "rotate it with /rotate-erp, which takes its own fields.")
    if not row.credential_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This connection has no stored grant to rotate. Re-enter it with "
            "Edit instead.")

    also = [c for c in conn.connections_using(session, row.credential_id)
            if c.connection_id != row.connection_id]
    try:
        conn.rotate_credential(
            session, principal.organization_id, row.credential_id,
            refresh_token=body.refresh_token.strip(),
            client_id=(body.client_id or "").strip() or None,
            client_secret=(body.client_secret or "").strip() or None)
    except conn.CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    # Checked immediately: a rotation that silently left the connection broken
    # is worse than no rotation, because the next failure looks like Zoho's.
    checked = _check(session, row)
    return {
        **checked,
        "rotated": True,
        "also_rotated": [
            {"connection_id": c.connection_id, "label": c.label,
             "zoho_organization_id": c.zoho_organization_id}
            for c in also
        ],
        "note": ("Rotated." if not also else
                 f"Rotated. {len(also)} other compan"
                 f"{'y' if len(also) == 1 else 'ies'} sign in through the same "
                 f"grant and now use the new token too."),
    }


@router.post("/{connection_id}/check")
def check_connection(
    connection_id: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Ask Zoho whether this connection works, and what it can reach."""
    try:
        row = conn.get_connection(session, principal.organization_id, connection_id)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return _check(session, row)


def _scope_note(scopes: list[dict]) -> tuple[list[str], list[str], str]:
    """``(missing required, untested, sentence)`` from a grant probe.

    Required and optional are kept apart because they call for different acts: a
    refused *required* scope means every sync fails until the connection is
    re-authorised, while a refused optional one means one screen stays empty and
    nothing else changes. Only the first decides the verdict.

    Untested is a third thing and is returned as itself rather than folded into
    either. A probe that could not reach an endpoint has established neither
    that the scope is missing nor that it is granted.

    **The trade-off, stated rather than hidden.** An untested *required* scope
    does not flip ``ok`` to false. What ``ok`` answers is "was anything
    definitely found wrong", and a transient 5xx on one probe is not evidence of
    a bad grant — failing the connection on it would cry wolf on every blip and
    teach an owner to ignore the chip, which costs more than it saves. The
    honesty is paid for in disclosure instead: the scope is named in the
    sentence and returned in ``untested_scopes``, so nothing claims to have
    checked what it did not.
    """
    required = {p.name for p in conn.REQUIRED_SCOPES if p.required}
    refused = [r["scope"] for r in scopes if r["granted"] is False]
    untested = [r["scope"] for r in scopes if r["granted"] is None]
    missing_required = [s for s in refused if s in required]

    parts: list[str] = []
    if missing_required:
        parts.append("the pull cannot run without " + ", ".join(missing_required))
    optional_gap = [s for s in refused if s not in required]
    if optional_gap:
        parts.append("granted without " + ", ".join(optional_gap)
                     + ", so what those read stays empty")
    if untested:
        parts.append("could not test " + ", ".join(untested))
    if not parts:
        return [], [], ""
    return missing_required, untested, " Permissions: " + "; ".join(parts) + "."


def _check(session: Session, row: models.ZohoConnection) -> dict:
    """Ping, probe the grant, record the result, and report what it can reach.

    Recorded even on failure: a connection that has never been checked and one
    that failed an hour ago look the same on a list, and only one of them is a
    problem.

    The probe is the half this used to be missing. ``ping`` reads
    ``organizations``, which no scope gates, so a connection granted the login
    and nothing else passed a check and then failed every sync — and the failure
    named a credential that was perfectly fine. Every caller of this function is
    a deliberate act on one connection (adding, rotating, pressing Check), which
    is what makes the extra calls affordable here and nowhere else.
    """
    if settings.ZOHO_SOURCE != "api":
        detail = f"Source is {settings.ZOHO_SOURCE!r}, so nothing was contacted."
        conn.record_check(session, row, ok=False, detail=detail)
        return {**_dict(session, row), "checked": False, "detail": detail}

    if (getattr(row, "connector", None) or conn.ZOHO_CONNECTOR) != conn.ZOHO_CONNECTOR:
        return _check_erp(session, row)

    try:
        creds = conn.credentials_for(session, row)
        source = ZohoApiSource(credentials=creds)
        info = source.ping()
    except (ZohoAuthError, conn.CredentialNotUsable) as e:
        conn.record_check(session, row, ok=False, detail=str(e))
        return {**_dict(session, row), "checked": True, "ok": False, "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — a check must report, not 500
        conn.record_check(session, row, ok=False, detail=f"{type(e).__name__}: {e}")
        return {**_dict(session, row), "checked": True, "ok": False,
                "detail": f"{type(e).__name__}: {e}"}

    found = info.get("organization_found")
    # Zoho knows which zone the books are kept in. Recorded on the platform's
    # organization while we have it, so "which day is it for this business?"
    # stops depending on where the server happens to be hosted. Only ever
    # filled in, never overwritten — an owner who has set it meant it.
    zone = str(info.get("time_zone") or "").strip()
    if found and zone:
        org = session.get(models.Organization, row.organization_id)
        if org is not None and not (org.timezone or "").strip():
            org.timezone = zone
            session.flush()

    # Same treatment for the country the books are kept in: Zoho states it on
    # the organization profile, and the statutory screens gate on it
    # (``commercial/jurisdiction``) — without this fill there is no path in
    # the product that records it at all, and the gate's refusal would name a
    # fix nobody can perform. Filled in only when unset, like the zone, and
    # only when the label places *exactly* — a mis-placed country would turn
    # the gate's refusal into a confidently wrong answer.
    code = jurisdiction.alpha2_from_label(info.get("country"))
    if found and code:
        org = session.get(models.Organization, row.organization_id)
        if org is not None and not (org.country or "").strip():
            org.country = code
            session.flush()

    # The currency this company keeps its books in, recorded on the connection.
    #
    # Overwritten on every check, unlike the timezone above, and the difference
    # is deliberate: the zone is written to the *organization* as a default a
    # person may then have meant to change, while this is a fact about one
    # connected company that nothing here is entitled to override. A stale
    # value would be worse than none, because it is what the sync compares
    # every document against.
    currency_note = ""
    currency = str(info.get("currency") or "").strip().upper()
    if found and currency:
        row.base_currency = currency
        session.flush()
        org = session.get(models.Organization, row.organization_id)
        rollup = str(getattr(org, "currency", "") or "").strip().upper()
        if rollup and currency != rollup:
            # Named on the connection rather than refused outright. Refusing
            # would be a judgement this cannot make — the operator may be about
            # to change the roll-up currency, or may want the company connected
            # and quiet. What must not happen is the two disagreeing *silently*,
            # because no money row in this platform records a currency, so a
            # foreign document read from here would be summed with local ones
            # and could never be told apart afterwards. The sync refuses those
            # documents; this is what tells somebody why they are missing.
            currency_note = (
                f" This company keeps its books in {currency} and this "
                f"platform reports in {rollup}, so its documents are not read "
                f"— see the sync report.")

    # Only worth asking once the login is known to reach this company: against
    # the wrong company every answer would describe a grant nobody is going to
    # sync with, at ten calls a time.
    scopes: list[dict] = []
    missing_required: list[str] = []
    untested: list[str] = []
    scope_note = ""
    if found:
        try:
            scopes = source.probe_scopes()
        except Exception as e:  # noqa: BLE001 — a check must report, not 500
            # The ping worked, so this is not the credential. Reported as an
            # untested grant rather than a failed connection: saying "not
            # reachable" about a company we just reached sends somebody to look
            # at the one thing that is fine.
            untested = [p.name for p in conn.REQUIRED_SCOPES]
            scope_note = f" Permissions could not be tested ({type(e).__name__}: {e})."
        else:
            missing_required, untested, scope_note = _scope_note(scopes)

    detail = ("Reached this company." if found else
              f"Authenticated, but company {row.zoho_organization_id} is not among "
              f"the ones this login can see.") + scope_note + currency_note
    # A currency mismatch does not flip `ok`, for the reason `_scope_note`
    # gives about untested scopes: `ok` answers "is this connection usable",
    # and it is — the credential works and the company is reachable. What is
    # wrong is what can be *done* with it, which is what the detail says.
    ok = bool(found) and not missing_required
    conn.record_check(session, row, ok=ok, detail=detail)
    return {
        **_dict(session, row),
        "checked": True,
        "ok": ok,
        "detail": detail,
        # What the grant answered for, scope by scope. `granted: null` is a
        # question the probe could not get an answer to — not a quiet pass.
        "scopes": scopes,
        "missing_required_scopes": missing_required,
        "untested_scopes": untested,
        "organization_name": info.get("organization_name"),
        "currency": info.get("currency"),
        "time_zone": info.get("time_zone"),
        # Every company this grant reaches — the answer to "do I need another
        # credential for the next entity?", which is almost always no.
        "visible_organizations": info.get("visible_organizations", []),
    }


def _check_erp(session: Session, row: models.ZohoConnection) -> dict:
    """The check for a registered connector: sign in, reach the company,
    record the outcome.

    No scope probe — none of the five speaks Zoho's per-scope grant model, and
    a probe list that is always empty would read as "all permissions verified".
    What their APIs *do* refuse per-permission arrives as a
    ``SourceScopeError`` at sync time and is reported per stage there.
    """
    from ..ingestion.errors import IngestionError

    try:
        source = conn.build_erp_source(session, row)
        info = source.ping()
    except (IngestionError, conn.CredentialNotUsable, ValueError) as e:
        conn.record_check(session, row, ok=False, detail=str(e))
        return {**_dict(session, row), "checked": True, "ok": False,
                "detail": str(e)}
    except Exception as e:  # noqa: BLE001 — a check must report, not 500
        conn.record_check(session, row, ok=False,
                          detail=f"{type(e).__name__}: {e}")
        return {**_dict(session, row), "checked": True, "ok": False,
                "detail": f"{type(e).__name__}: {e}"}

    found = bool(info.get("organization_found"))
    detail = ("Reached this company." if found else
              f"Authenticated, but company {row.zoho_organization_id} was not "
              "found by this sign-in.")
    conn.record_check(session, row, ok=found, detail=detail)
    return {
        **_dict(session, row),
        "checked": True,
        "ok": found,
        "detail": detail,
        "scopes": [],
        "missing_required_scopes": [],
        "untested_scopes": [],
        "organization_name": info.get("organization_name"),
        "currency": info.get("currency"),
        "time_zone": info.get("time_zone"),
        "visible_organizations": info.get("visible_organizations", []),
    }


# ── Registered ERP connectors (NetSuite, Business Central, Acumatica, P21,
#    Sage) ──────────────────────────────────────────────────────────────────

def _zoho_catalog_entry() -> dict:
    """Zoho's row in the same list as the registered connectors.

    It is not in ``ingestion/erp``'s registry — its connect flow predates that
    and is richer than a field list (browser OAuth, a data-centre picker,
    per-scope probing), which is why its form is written out on the screen
    rather than rendered from fields. But *what it must be granted* is the same
    fact every other connector states, so it is published in the same list
    rather than beside it: the panel that names the access requirements is the
    panel that picks the system, and one list is what stops the two disagreeing.
    """
    from ..domain.origin import CONNECTORS
    return {
        "key": conn.ZOHO_CONNECTOR,
        "label": CONNECTORS[conn.ZOHO_CONNECTOR]["label"],
        "company_term": "organization",
        "icon": CONNECTORS[conn.ZOHO_CONNECTOR]["icon"],
        "setup_note": (
            "Reads Zoho Books over its v3 API with an OAuth refresh token. One "
            "sign-in reaches every company that Zoho user can see, so a second "
            "and third company reuse it rather than needing their own."),
        # Empty because this form is not rendered from a field list. A reader
        # who takes that as "asks for nothing" has it backwards.
        "credential_fields": [],
        "connection_fields": [],
        "external_id_field": "zoho_organization_id",
        "can_discover": True,
        "permissions": [p.to_dict() for p in conn.REQUIRED_SCOPES],
        "permission_note": conn.ZOHO_PERMISSION_NOTE,
        "permission_string": conn.SCOPE_STRING,
    }


@router.get("/catalog")
def connector_catalog(
    principal: Principal = Depends(require_manager_or_owner),
) -> dict:
    """Every system a company can be connected from: the form to render, and
    the access its sign-in must already hold.

    Both come from each connector's own spec, so the UI never hardcodes what
    NetSuite needs — connector number seven appears here the day its module
    registers, permissions included.

    Zoho leads because it is the incumbent, and because a list that opens on
    the system most owners want spares them a click.
    """
    from ..domain.origin import CONNECTORS
    from ..ingestion import erp

    return {
        "connectors": [_zoho_catalog_entry()] + [
            {
                "key": spec.key,
                "label": spec.label,
                "company_term": spec.company_term,
                "icon": CONNECTORS.get(spec.key, {}).get("icon", "◇"),
                "setup_note": spec.setup_note,
                "credential_fields": [f.to_dict() for f in spec.credential_fields],
                "connection_fields": [f.to_dict() for f in spec.connection_fields],
                "external_id_field": spec.external_id_field,
                "can_discover": spec.discover is not None,
                "permissions": [p.to_dict() for p in spec.permissions],
                "permission_note": spec.permission_note,
                "permission_string": spec.permission_string,
            }
            for spec in erp.catalog()
        ],
    }


class ErpConnect(BaseModel):
    """One registered connector's entered form values, verbatim.

    ``values`` is keyed by the field names the catalog declared — validation
    happens against the spec, so a missing secret is refused by its label
    before anything is stored.
    """

    connector: str = Field(min_length=1, max_length=32)
    values: dict = Field(default_factory=dict)
    label: str = ""
    credential_label: str = ""


@router.post("/erp", status_code=status.HTTP_201_CREATED)
def add_erp_connection(
    body: ErpConnect,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Connect one company of a registered ERP.

    The same lifecycle as adding a Zoho company: identical secrets attach to
    the credential already on file, the multi-company plan gate applies to a
    second distinct company whichever system it lives in, and the connection
    is checked immediately so a typo fails here rather than on the first
    nightly sync.
    """
    from ..ingestion import erp

    org = principal.organization_id
    try:
        row = conn.connect_erp(session, org, connector=body.connector.strip(),
                               values=body.values, label=body.label,
                               credential_label=body.credential_label)
    except erp.UnknownConnectorError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except conn.CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except entitlements.PlanRefused as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    return _check(session, row)


class ErpDiscover(BaseModel):
    """Credential fields only — enough to sign in and ask what is visible."""

    connector: str = Field(min_length=1, max_length=32)
    values: dict = Field(default_factory=dict)


@router.post("/erp/discover")
def discover_erp_companies(
    body: ErpDiscover,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """The companies a credential can see, before anything is stored.

    For the connectors whose company identity is a GUID nobody knows by heart
    (Business Central): enter the sign-in, get the list, pick by name.
    Nothing is persisted — the values are used for this one call and dropped.
    """
    from ..ingestion import erp
    from ..ingestion.errors import IngestionError

    try:
        spec = erp.get_spec(body.connector.strip())
    except erp.UnknownConnectorError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    if spec.discover is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{spec.label} sign-ins are already scoped to one "
            f"{spec.company_term}; there is no list to discover.")
    try:
        secrets, config = erp.split_credential_inputs(spec, body.values)
        companies = spec.discover(erp.CredentialMaterial(
            connector=spec.key, secrets=secrets, config=config))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except IngestionError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return {"connector": spec.key, "companies": companies}


class ErpRotate(BaseModel):
    """A registered connector's fresh credential values, all of them — which
    fields exist comes from the catalog, same as connecting."""

    values: dict = Field(default_factory=dict)


@router.post("/{connection_id}/rotate-erp")
def rotate_erp_connection(
    connection_id: str,
    body: ErpRotate,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Replace the sign-in behind a registered connector's connection.

    The sibling of ``/rotate``, which is Zoho's refresh-token shape. One
    credential can serve several companies here too, so the response names
    every connection that just changed underneath — the same disclosure, for
    the same reason.
    """
    try:
        row = conn.get_connection(session, principal.organization_id, connection_id)
    except conn.ConnectionNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    if (getattr(row, "connector", None) or conn.ZOHO_CONNECTOR) == conn.ZOHO_CONNECTOR:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This is a Zoho connection — rotate it with /rotate, which takes "
            "the refresh token.")
    if not row.credential_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This connection has no stored credential to rotate. Reconnect "
            "it with the sign-in details instead.")

    also = [c for c in conn.connections_using(session, row.credential_id)
            if c.connection_id != row.connection_id]
    try:
        conn.rotate_erp_credential(session, principal.organization_id,
                                   row.credential_id, values=body.values)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except conn.CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    checked = _check(session, row)
    return {
        **checked,
        "rotated": True,
        "also_rotated": [
            {"connection_id": c.connection_id, "label": c.label,
             "zoho_organization_id": c.zoho_organization_id}
            for c in also
        ],
        "note": ("Rotated." if not also else
                 f"Rotated. {len(also)} other compan"
                 f"{'y' if len(also) == 1 else 'ies'} sign in through the same "
                 f"credential and now use the new values too."),
    }

