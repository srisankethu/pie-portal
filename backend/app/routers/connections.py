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
from fastapi.responses import RedirectResponse
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
from ..ingestion.url_safety import UnsafeSourceUrl
from ..ingestion.zoho_client import ZohoApiSource, ZohoAuthError, ZohoError

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


#: How far back a first pull reads when nobody has said otherwise. Owned by
#: ``ingestion.connections`` because the onboarding checklist states the same
#: figure to a new owner in words; re-exported under the name this module has
#: always used it by.
DEFAULT_HISTORY_MONTHS = conn.DEFAULT_HISTORY_MONTHS


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
    """Either supply a credential already on file, or a fresh set of secrets.

    ``grant_code`` and ``refresh_token`` are the same credential one step
    apart, and exactly one is supplied. The grant code is what the Zoho API
    console actually hands you — ``Generate Code`` on a Self Client — and
    turning it into a refresh token is a single HTTP call this server can make
    perfectly well, which is the whole reason it is accepted here: the setup
    doc's step 3 was a ``curl`` an owner ran by hand, and the value they pasted
    back was, often enough, the code rather than the token it produces. The two
    are indistinguishable by sight (both ``1000.xxxx.yyyy``), so the caller
    says which it is rather than this guessing.
    """

    zoho_organization_id: str = Field(min_length=1, max_length=64)
    label: str = ""
    credential_id: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    refresh_token: Optional[str] = None
    grant_code: Optional[str] = None
    accounts_base: str = "https://accounts.zoho.in"
    api_base: str = "https://www.zohoapis.in/books/v3"


def _refresh_token_from_grant_code(
    code: str, *, client_id: str, client_secret: str, accounts_base: str,
) -> str:
    """Spend a one-time Zoho grant code, or say why it could not be spent.

    Thin by design — the exchange itself is ``oauth.exchange_code``, shared with
    the redirect flow. What lives here is only the mapping from its exceptions
    to status codes, which is a router's job: a refused code is the caller's
    input (400), an unreachable Zoho is not (502).
    """
    from .. import oauth

    try:
        return oauth.exchange_code(
            code, accounts_base, client_id=client_id,
            client_secret=client_secret).refresh_token
    except UnsafeSourceUrl as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except ZohoAuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except ZohoError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


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
                                      ("client_secret", body.client_secret))
                       if not (v or "").strip()]
            if missing:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Choose an existing connection, or supply "
                    + ", ".join(missing) + ".")
            grant_code = (body.grant_code or "").strip()
            refresh_token = (body.refresh_token or "").strip()
            if bool(grant_code) == bool(refresh_token):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Supply a grant code or a refresh token — one, not both. "
                    "The grant code is what the Zoho API console gives you "
                    "under Generate Code; a refresh token is what exchanging "
                    "one produces.")
            accounts_base = body.accounts_base.strip().rstrip("/")
            if grant_code:
                # Spent before anything is written. A code lives for minutes
                # and once, so a credential row created first and tokened
                # second would leave a dead sign-in on the screen every time
                # the exchange failed — and the exchange is the step that
                # fails, because that is where a stale code is found out.
                refresh_token = _refresh_token_from_grant_code(
                    grant_code, client_id=body.client_id.strip(),
                    client_secret=body.client_secret.strip(),
                    accounts_base=accounts_base)
            row = conn.set_zoho_credentials(
                session, org, zoho_organization_id=zoho_org,
                client_id=body.client_id.strip(),
                client_secret=body.client_secret.strip(),
                refresh_token=refresh_token,
                accounts_base=accounts_base,
                api_base=body.api_base.strip().rstrip("/"),
                label=body.label,
                # The grant is named after itself, not after the first company
                # it happened to connect — it will very likely serve others.
                credential_label=f"Zoho sign-in {body.client_id.strip()[:14]}")
    except UnsafeSourceUrl as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    except conn.CredentialNotUsable as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
    except entitlements.PlanRefused as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    _check(session, row)
    return _dict(session, row)


# ── the customer-facing authorization ────────────────────────────────────────
# Four endpoints, and exactly one of them is public. That asymmetry is the
# design: the browser arrives at the callback having followed Zoho's redirect,
# carrying no session, so the callback proves *which* authorization finished
# from the state token and proves nothing else. What the authorization produced
# is picked up separately, by an authenticated owner in the organization the
# state named. The previous implementation put `require_owner` on the callback
# itself, which a top-level redirect can never satisfy, and 401'd before its
# handler ran.


@router.get("/zoho/authorize")
def authorize_zoho(
    dc: str = "in",
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Start an authorization: record the state, return where to send the browser.

    ``dc`` is the data centre code, because a Zoho grant is not portable between
    estates — a code issued by ``accounts.zoho.com`` is not redeemable at
    ``accounts.zoho.in``. It is chosen here and carried on the state row rather
    than round-tripped through the redirect, which cannot be trusted to return it.
    """
    from .. import oauth

    if dc not in oauth.DATA_CENTRES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown Zoho data centre {dc!r}. One of: "
            f"{', '.join(sorted(oauth.DATA_CENTRES))}.")
    if not oauth.configured():
        # 503 and a sentence, not a 500 and not a dead button: this deployment
        # has no application registered, and the manual path still works.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This deployment has no Zoho application registered, so it cannot "
            "authorize on your behalf. Connect with a Self Client refresh token "
            "instead.")

    accounts_base, api_base = oauth.DATA_CENTRES[dc]
    token, _ = oauth.issue_state(
        session, organization_id=principal.organization_id,
        accounts_base=accounts_base, api_base=api_base)
    # Housekeeping on the one path that creates these, so the table cannot grow
    # without bound and nothing needs a scheduled job to keep it honest.
    oauth.sweep_expired(session)
    return {
        "authorization_url": oauth.authorization_url(
            token, accounts_base, scope=conn.SCOPE_STRING),
        "expires_in_seconds": settings.ZOHO_OAUTH_STATE_TTL_SECONDS,
    }


@router.get("/zoho/callback", include_in_schema=False)
async def zoho_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Zoho's redirect lands here. **Deliberately unauthenticated.**

    What makes that safe is not that nothing valuable happens — the token
    exchange happens — but that nothing valuable is *handed to the caller*. The
    browser leaves with a one-time handoff code, and redeeming it requires a
    session inside the organization the state was issued to. So a stranger who
    replays this URL can, at most, spend a state that is already spent.

    Always a redirect, never a JSON body, and never an exception that reaches
    the default handler: a person is following a link, and the only useful
    outcome is a screen. Every failure path below ends at the same place with a
    reason attached.
    """
    from .. import oauth

    if error:
        return RedirectResponse(
            oauth.return_url(ok=False, reason=error_description or error),
            status_code=status.HTTP_303_SEE_OTHER)
    if not code or not state:
        return RedirectResponse(
            oauth.return_url(ok=False, reason="Zoho did not return an "
                                              "authorization code."),
            status_code=status.HTTP_303_SEE_OTHER)

    try:
        row = oauth.consume_state(session, state)
    except oauth.StateInvalid as e:
        return RedirectResponse(oauth.return_url(ok=False, reason=str(e)),
                                status_code=status.HTTP_303_SEE_OTHER)

    try:
        tokens = await oauth.exchange_code_for_tokens(code, row.accounts_base)
        cred = oauth.credential_from_oauth(
            session, organization_id=row.organization_id, tokens=tokens,
            accounts_base=row.accounts_base, api_base=row.api_base)
        handoff = oauth.issue_handoff(session, row, cred.credential_id)
    except (ZohoAuthError, oauth.OAuthNotConfigured) as e:
        return RedirectResponse(oauth.return_url(ok=False, reason=str(e)),
                                status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:  # noqa: BLE001 — a redirect must always be produced
        log.exception("zoho callback failed")
        return RedirectResponse(
            oauth.return_url(ok=False, reason=f"{type(e).__name__}: {e}"),
            status_code=status.HTTP_303_SEE_OTHER)

    # The state row now holds the handoff and the credential, and the caller is
    # about to be redirected away from this request — so it has to be durable
    # before the response goes.
    session.commit()
    return RedirectResponse(oauth.return_url(ok=True, handoff=handoff),
                            status_code=status.HTTP_303_SEE_OTHER)


@router.get("/zoho/pending/{handoff}")
def zoho_pending(
    handoff: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Which sign-in the completed authorization produced, for this owner.

    The authenticated half, and deliberately the *whole* of it: this returns a
    credential id and stops. Listing the companies that grant can reach is
    already an endpoint — ``GET /api/v1/data/credentials/{id}/organizations`` —
    which additionally marks the ones this organization has connected already,
    and connecting one is ``POST /api/v1/connections`` with a ``credential_id``.
    An authorization ends by producing a credential; from there it rejoins the
    path a manually-entered credential takes, and a second company picker built
    for this flow would be the copy that stops agreeing with that one.
    """
    from .. import oauth

    try:
        row = oauth.claim_handoff(
            session, organization_id=principal.organization_id, token=handoff)
    except oauth.StateInvalid as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e

    cred = session.get(models.ZohoCredential, row.credential_id or "")
    if cred is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "That authorization no longer has a sign-in to use.")
    return {"credential_id": cred.credential_id, "label": cred.label}


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
    connection gets broken.

    ``grant_code`` is the same credential one step earlier, and rotation is the
    path that needs it most: the API console's answer to a revoked or
    expired-out token is a fresh **code**, and every rotation until now asked
    the owner to run the exchange themselves and paste the result. Exactly one
    of the two is supplied — they are indistinguishable by sight, so the caller
    says which it is.
    """

    refresh_token: Optional[str] = None
    grant_code: Optional[str] = None
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
    grant_code = (body.grant_code or "").strip()
    refresh_token = (body.refresh_token or "").strip()
    if bool(grant_code) == bool(refresh_token):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A grant code or a refresh token is required — one, not both.")
    new_client_id = (body.client_id or "").strip()
    new_client_secret = (body.client_secret or "").strip()
    if bool(new_client_id) != bool(new_client_secret):
        # Half a pair is refused rather than merged with the stored half. A
        # client keeps one id across data centres but has a *separate secret in
        # each*, so the merged pair is wrong far more often than it is right —
        # and Zoho reports it as `invalid_client_secret`, which reads as "your
        # secret is wrong" and sends people to change the data centre, the one
        # setting that was correct. The screen sends both or neither; this is
        # the same rule stated where any caller meets it.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Replace the client id and secret together or not at all — a "
            "client has a separate secret in each data centre, so half a pair "
            "is a credential Zoho will refuse.")
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
        if grant_code:
            # Exchanged against the client pair the code was generated under —
            # the new one where the form supplies it, the stored one otherwise.
            # Getting that the wrong way round is the failure this screen
            # already explains at length: a code from a different Self Client,
            # spent against the old id, comes back `invalid_client_secret` and
            # reads as "your secret is wrong".
            #
            # The stored side is read through `credentials_for`, which is the
            # one place a connection turns into decrypted Zoho credentials.
            stored = conn.credentials_for(session, row)
            refresh_token = _refresh_token_from_grant_code(
                grant_code,
                client_id=new_client_id or stored.client_id,
                client_secret=new_client_secret or stored.client_secret,
                accounts_base=stored.accounts_base)
        conn.rotate_credential(
            session, principal.organization_id, row.credential_id,
            refresh_token=refresh_token,
            client_id=new_client_id or None,
            client_secret=new_client_secret or None)
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

def _oauth_available() -> bool:
    """Whether the customer-facing authorization can complete on this deployment."""
    from .. import oauth

    return oauth.configured()


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
        # The subset that still runs a sync, for the owner whose policy is to
        # grant the least that works. Served beside the full set rather than
        # instead of it: the screen leads with everything, because a scope
        # ungranted fails quietly and later.
        "permission_string_minimum": conn.MINIMUM_SCOPE_STRING,
        # Whether this deployment has a Zoho application registered, and can
        # therefore offer the authorize flow at all. Served rather than assumed
        # so the screen can omit the button instead of showing one that 503s —
        # a dead button beside a working manual path is what got the previous
        # implementation deleted, and the lesson was about the button.
        "can_authorize": _oauth_available(),
        # Served from the same source as every other row. Zoho is the one
        # connector that actually writes and the one the registry cannot hold,
        # so a hand-written ``[]`` here would be the single most misleading
        # value in the whole catalogue.
        "writes": list(conn.writes_for(conn.ZOHO_CONNECTOR)),
        "can_write_quotes": conn.quote_writer_ready(conn.ZOHO_CONNECTOR),
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
                # Empty for every registered ERP, because access in each of
                # their consoles is clicked rather than typed — a system with
                # no pasteable string has no smaller one either. A connector
                # that ever gains the first should gain the second with it.
                "permission_string_minimum": "",
                # No registered ERP has a customer-facing authorization flow;
                # every one of them is a sign-in entered by hand.
                "can_authorize": False,
                # What this platform can create in the system, if anything.
                # Read through the same function the quote router routes on
                # rather than off the spec directly, so the screen and the
                # write path cannot answer this differently.
                "writes": list(conn.writes_for(spec.key)),
                "can_write_quotes": conn.quote_writer_ready(spec.key),
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
        # discover signs in and fetches from the entered base_url, so it is the
        # same server-side-request surface as connecting; refuse an internal one.
        conn.require_safe_source_urls(body.values, label=spec.label)
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

