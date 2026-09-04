"""PIE's own staff credential, and the console's read of the vendor's queues.

This platform is operated through five command-line tools — the enquiry queue
(``app.contact``), plan grants (``app.entitlements``), provisioning
(``app.provision_org``), the demo workspace (``app.demo``) and the syncs
(``app.sync_all``). They work, and every one of them needs an SSH session,
which is what makes them useless to anyone who is not the person who wrote
them. ``docs/operator-console.md`` is the design; this module is its identity
and its read model.

**Why this is not a role.** ``Role`` is explicit that it says what a person may
do *inside one organization*, and every control downstream of it —
``quote_service.project``'s cost withholding, the scope checks in ``authz``,
row-level security — reads ``Principal.organization_id``. An operator has no
organization. Adding a fourth role, or a ``is_staff`` column on ``users``,
would put a tenant-bypassing capability on the same table and the same
verification path as customer sign-in, told apart by one nullable field. That
is the shape of the defect this repository keeps re-finding: the check was
there, on the other branch. So the credential is separate at every level — its
own table, its own prefix, its own verifier — and there is no code path from a
customer credential to an :class:`Operator`.

**What an operator may read, and what stops them reading more.** Two of this
schema's tables carry no row-level security policy and are the console's whole
vendor scope: ``organizations`` (a tenant's name, plan and creation date — the
vendor's own billing metadata) and ``contact_requests`` (an enquiry that
arrived before there was a tenant). The other 58 are fail-closed: a connection
that has not announced a tenant sees nothing, from SQL's three-valued logic
rather than from a check somebody remembered. So an operator cannot read a
customer's quotes, margins or catalogue by holding this credential. Reaching
inside a tenant means ``tenancy.set_tenant``, and the console calls that only
behind :func:`reach_into`, which asserts a live ``trust/access`` grant and
records the reach — with a justification the customer reads on their own
endpoint.

That is the arrangement ``trust/access`` was written for and had never had a
caller for; ``trust/audit``'s docstring says so.

**The credential.** ``pieop_<key_id>_<secret>``, the same two-part shape
``api_keys`` uses and for the same reason its docstring gives: an unprefixed
opaque string means hashing every live credential on every request to discover
which row was presented. Deliberately a *different* prefix, so a key pasted
into the wrong door fails on its shape rather than on a lookup.
"""
from __future__ import annotations

import argparse
import logging
import secrets
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock, ratelimit, tenancy
from .domain import models
from .passwords import hash_password, verify_password
from .trust import access

log = logging.getLogger("pie_portal.operator")

#: The credential's prefix. Not ``pie_`` — that is a tenant API key, and a
#: credential presented at the wrong door should fail on its shape.
PREFIX = "pieop"

_KEY_ID_BYTES = 12
_SECRET_BYTES = 32

#: Failed attempts per key id per minute, as ``api_keys`` bounds them and for
#: the reason given there: guessing a 256-bit secret for an id you already hold
#: is infeasible, and one counter removes the argument.
FAILED_ATTEMPTS_PER_MINUTE = 10
RATE_WINDOW_SECONDS = 60
#: How stale ``last_used_at`` may be before a read is worth a write. Same
#: bargain ``api_keys`` and ``user_sessions`` strike.
LAST_USED_RESOLUTION_SECONDS = 60


class OperatorRefused(PermissionError):
    """The credential presented is not a live operator key."""


@dataclass(frozen=True)
class Operator:
    """Who is operating the platform on this request.

    Deliberately **not** a :class:`~app.authz.Principal`. It has no
    ``organization_id`` and no :class:`~app.domain.enums.Role`, because it is
    not a member of anything — and a dataclass that looked like a principal
    would eventually be passed to something that projects a quote for one.
    """

    operator_id: str
    key_id: str
    name: str


@dataclass(frozen=True)
class IssuedOperatorKey:
    """A freshly minted key: the row, and the secret shown exactly once."""

    row: models.OperatorKey
    secret: str


def _compose(key_id: str, secret: str) -> str:
    return f"{PREFIX}_{key_id}_{secret}"


def split(presented: str) -> Optional[tuple[str, str]]:
    """``(key_id, secret)`` from a presented credential, or None if malformed.

    ``maxsplit=2`` for the reason ``api_keys.split`` documents at length:
    ``token_urlsafe``'s alphabet includes the underscore, so an unbounded split
    cuts about a third of all valid keys into four pieces and rejects them
    intermittently. The key id being hex is what makes the second separator the
    right one.
    """
    parts = (presented or "").strip().split("_", 2)
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def issue(session: Session, *, operator_id: str, name: str = "") -> IssuedOperatorKey:
    """Mint a key for one member of staff. Does **not** commit.

    Uncommitted for the reason ``api_keys.issue`` gives: the caller owns the
    transaction, and a credential that commits separately from whatever records
    its creation is a credential with no record of who created it.

    There is no endpoint behind this and there is not meant to be. A console
    that can mint its own credentials turns one leaked key into a permanent
    foothold, which is the argument ``routers/api_keys`` already makes about
    tenant keys — and it is stronger here, because this key is not scoped to a
    tenant at all. Minting is a shell on the box.
    """
    who = (operator_id or "").strip()
    if not who:
        raise ValueError("An operator key needs an operator_id — the person it "
                         "names appears in the customer's own access log.")
    key_id = secrets.token_hex(_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    row = models.OperatorKey(
        key_id=key_id,
        operator_id=who[:64],
        name=(name or "").strip()[:120],
        secret_hash=hash_password(secret),
        secret_hint=secret[-4:],
    )
    session.add(row)
    session.flush()
    log.info("operator key %s minted for %s", key_id, row.operator_id)
    return IssuedOperatorKey(row=row, secret=_compose(key_id, secret))


def revoke(session: Session, key_id: str) -> bool:
    """End a key. Idempotent — an already-revoked row keeps its first time."""
    row = session.get(models.OperatorKey, key_id)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = clock.now()
        log.info("operator key %s revoked", key_id)
    return True


def keys(session: Session) -> list[models.OperatorKey]:
    """Every operator key ever minted, newest first. Revoked rows included:
    "who could operate this platform in March" is the question this answers."""
    return list(session.scalars(
        select(models.OperatorKey)
        .order_by(models.OperatorKey.created_at.desc())))


def authenticate(session: Session, presented: str) -> Optional[Operator]:
    """The live operator this credential names, or None. Never says which check
    failed — a caller learning *why* a credential was refused learns which half
    of it was right.
    """
    parsed = split(presented)
    if parsed is None:
        return None
    key_id, secret = parsed

    if ratelimit.too_many("operator_key_failures", key_id,
                          limit=FAILED_ATTEMPTS_PER_MINUTE,
                          window_seconds=RATE_WINDOW_SECONDS):
        log.warning("too many failed attempts against operator key %s", key_id)
        return None

    # No `adopt_tenant_*` call, and its absence is the point: `operator_keys`
    # carries no policy because it is not a tenant's data, so this lookup needs
    # no tenant announced — and the connection still has none announced
    # afterwards, which is what keeps the 58 policied tables empty for the rest
    # of the request unless `reach_into` opens one.
    row = session.get(models.OperatorKey, key_id)
    if row is None:
        return None
    if not verify_password(secret, row.secret_hash):
        # Logged at the id, which is the half that is not a secret.
        log.warning("operator key %s presented with a bad secret", key_id)
        return None
    if row.revoked_at is not None:
        log.info("operator key %s was presented after revocation", key_id)
        return None
    ratelimit.reset_key("operator_key_failures", key_id)
    _touch(session, row)
    return Operator(operator_id=row.operator_id, key_id=row.key_id, name=row.name)


def _touch(session: Session, row: models.OperatorKey) -> None:
    now = clock.now()
    last = clock.aware(row.last_used_at)
    if last is None or (now - last).total_seconds() >= LAST_USED_RESOLUTION_SECONDS:
        row.last_used_at = now
        session.flush()


def reach_into(session: Session, operator: Operator, organization_id: str,
               *, resource: str) -> None:
    """Announce a tenant on this connection, and record that it was reached.

    **The only way the console reads a customer's rows**, and the order of the
    two statements below is the whole control: the grant is asserted and the
    reach is logged *before* the tenant is announced, so a refusal leaves the
    connection exactly as fail-closed as it was. Reversing them would make an
    unlogged read possible in the window between.

    ``record_use`` raises :class:`~app.trust.access.AccessDenied` when there is
    no live grant. It is deliberately not caught here: a caller that forgets to
    handle it gets a 500 rather than silent tenant data, which is the failure
    this module would rather have.
    """
    access.record_use(session, organization_id=organization_id,
                      staff_user_id=operator.operator_id, resource=resource)
    tenancy.set_tenant(session, organization_id)


def _main() -> int:                                  # pragma: no cover — CLI
    from .db import SessionLocal

    parser = argparse.ArgumentParser(
        prog="python -m app.operator",
        description="PIE's own staff credentials for the operator console.")
    sub = parser.add_subparsers(dest="cmd")
    p_mint = sub.add_parser("mint", help="Mint a key. The secret prints once.")
    p_mint.add_argument("operator_id", help="Short, stable, and shown to "
                                            "customers in their access log")
    p_mint.add_argument("--name", default="", help="What you recognise this "
                                                   "particular key by")
    sub.add_parser("list", help="Every key ever minted, newest first")
    p_rev = sub.add_parser("revoke", help="End a key")
    p_rev.add_argument("key_id")
    args = parser.parse_args()

    with SessionLocal() as session:
        if args.cmd == "mint":
            issued = issue(session, operator_id=args.operator_id, name=args.name)
            session.commit()
            print(f"Operator key for {issued.row.operator_id}. This is the only "
                  f"time the secret is shown:\n\n  {issued.secret}\n")
            return 0
        if args.cmd == "revoke":
            found = revoke(session, args.key_id)
            session.commit()
            print("Revoked." if found else f"No such key: {args.key_id}")
            return 0 if found else 1
        rows = keys(session)
        if not rows:
            print("No operator keys. Mint one with "
                  "`python -m app.operator mint <operator_id>`.")
            return 0
        for row in rows:
            state = ("revoked " + clock.iso(row.revoked_at)) if row.revoked_at else "live"
            used = clock.iso(row.last_used_at) if row.last_used_at else "never used"
            print(f"  {row.key_id}  {row.operator_id:<16} …{row.secret_hint}  "
                  f"{state}  {used}  {row.name}")
    return 0


if __name__ == "__main__":                           # pragma: no cover — CLI
    raise SystemExit(_main())
