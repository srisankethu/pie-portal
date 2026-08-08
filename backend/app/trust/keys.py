"""Per-tenant data keys — envelope encryption, so deletion can be proved.

``crypto.py`` encrypts credentials under one process-wide key. That is right for
credentials and wrong for customer data, because a single key means "delete my
data" can only ever be a promise about rows: backups cannot be selectively
edited, so a deleted row lives on in every snapshot taken before it went.

Envelope encryption fixes the exit story rather than the perimeter one. Each
organization gets a data key (DEK). The DEK is stored wrapped by the process
master key (KEK) and never at rest in the clear. Tenant data is encrypted under
the tenant's DEK. Destroying the DEK renders every ciphertext written under it
inert *everywhere it exists* — live tables, replicas, and the backups nobody can
reach into. That is the difference between "we deleted your rows" and "we can no
longer read your data, and neither can anyone who takes our backups."

What this is not: customer-managed keys. The KEK is still ours, so this defends
against a stolen backup and makes erasure provable; it does not defend against
us. CMK is the enterprise tier and a different control (see ``docs``).

Destruction is deliberately irreversible and deliberately loud: ``destroy`` is
the only operation here that cannot be undone, it demands a reason, and it
leaves the row in place — a tombstone with the key material gone, so the
question "was this tenant erased, and when, and who asked?" has an answer.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..domain import models


class KeyDestroyed(RuntimeError):
    """The tenant's data key was destroyed. The ciphertext is unreadable, on
    purpose, and no amount of retrying changes that."""


class KeyUnavailable(RuntimeError):
    """The DEK could not be unwrapped — almost always the master key changing
    since the DEK was written."""


def _kek() -> Fernet:
    return Fernet(settings.CREDENTIAL_ENCRYPTION_KEY.encode())


def _new_dek() -> bytes:
    """A Fernet key is 32 random bytes, urlsafe-base64'd."""
    return base64.urlsafe_b64encode(os.urandom(32))


def _row(session: Session, organization_id: str) -> Optional[models.TenantKey]:
    return session.get(models.TenantKey, organization_id)


def ensure_key(session: Session, organization_id: str) -> models.TenantKey:
    """The tenant's key row, created on first use.

    Created lazily rather than at provisioning so an organization that predates
    this feature picks one up the first time it needs one, with no backfill step
    and no window where a write has nowhere to go.

    **Safe against a concurrent first use**, which lazily-created-on-first-write
    has to be: read-then-insert is a race, and this organization's very first
    write is exactly when two writers are most likely to arrive together — a
    sync-all pulls every connected company at once, and each pull is the first
    thing its own thread does. Both saw no key, both inserted, one got
    ``UNIQUE constraint failed: tenant_keys.organization_id`` and took its whole
    pull down with it.

    The insert is attempted inside a SAVEPOINT so losing the race costs only
    that statement. Rolling the outer transaction back instead would discard the
    caller's work for a row that now exists — which is the opposite of what the
    loser of this race wants.
    """
    row = _row(session, organization_id)
    if row is not None:
        return row
    row = models.TenantKey(
        organization_id=organization_id,
        wrapped_dek=_kek().encrypt(_new_dek()).decode(),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        # Rolling the SAVEPOINT back already detaches the pending row on current
        # SQLAlchemy, and expunging something already gone raises in its own
        # right — which is how this landed in the test suite the first time.
        if row in session:
            session.expunge(row)
        winner = _row(session, organization_id)
        if winner is None:
            # The constraint fired for some other reason, or the winner's
            # transaction is not visible yet. Either way this is not the race
            # described above and must not be swallowed.
            raise
        return winner
    return row


def _dek(session: Session, organization_id: str) -> Fernet:
    row = ensure_key(session, organization_id)
    if row.destroyed_at is not None or not row.wrapped_dek:
        raise KeyDestroyed(
            f"The data key for {organization_id} was destroyed on "
            f"{row.destroyed_at:%Y-%m-%d} and cannot be recovered. Anything "
            f"encrypted under it is permanently unreadable — which is what "
            f"destroying it was for.")
    try:
        return Fernet(_kek().decrypt(row.wrapped_dek.encode()))
    except InvalidToken as e:
        raise KeyUnavailable(
            "Could not unwrap this organization's data key. CREDENTIAL_ENCRYPTION_KEY "
            "has almost certainly changed since it was written; restore the previous "
            "value — the data is not lost, it is unreadable under the current master "
            "key.") from e


def encrypt_for(session: Session, organization_id: str, plaintext: str) -> str:
    return _dek(session, organization_id).encrypt(plaintext.encode()).decode()


def decrypt_for(session: Session, organization_id: str, ciphertext: str) -> str:
    try:
        return _dek(session, organization_id).decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise KeyUnavailable(
            "Stored ciphertext did not decrypt under this organization's data key."
        ) from e


def is_destroyed(session: Session, organization_id: str) -> bool:
    row = _row(session, organization_id)
    return row is not None and row.destroyed_at is not None


def destroy(session: Session, organization_id: str, *, reason: str,
            actor_user_id: Optional[str]) -> models.TenantKey:
    """Crypto-shred this tenant. Irreversible by design.

    The row survives with its key material gone. A deleted row would leave no
    way to answer "was this erased?", and an erasure nobody can evidence is
    worth about as much as one that never happened.
    """
    if not (reason or "").strip():
        raise ValueError("Destroying a tenant's data key requires a stated reason.")
    row = ensure_key(session, organization_id)
    if row.destroyed_at is not None:
        return row                       # idempotent: already inert
    row.wrapped_dek = ""
    row.destroyed_at = datetime.now(timezone.utc)
    row.destroyed_by_user_id = actor_user_id
    row.destroy_reason = reason.strip()
    session.flush()
    return row
