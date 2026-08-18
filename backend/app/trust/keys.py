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
longer read what was encrypted under your key, and neither can anyone who takes
our backups."

The scope of that sentence is exactly the ciphertext, and the ciphertext is not
everything. Two field classes are encrypted under the DEK — the vaulted display
names and the AI payload log — and the rest of a tenant's rows, plaintext
display columns included, are untouched by key destruction. The erasure receipt
enumerates both halves (``trust/erasure.DESTROYED`` and
``trust/erasure.SURVIVES_PLAINTEXT``); do not describe ``destroy`` as erasing
"the tenant's data" anywhere a customer might read it.

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
    """The DEK could not be unwrapped, or ciphertext did not decrypt under it.

    A different failure from ``KeyDestroyed`` in the way that matters: nobody
    chose this, and it has a remedy. ``UNREADABLE_EXPLANATION`` carries it.
    """


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


#: Why an unwrap fails, and what to do about it — one text, because this is the
#: sentence an operator acts on and it was wrong in a way that made things worse.
#:
#: It used to assert a single cause ("CREDENTIAL_ENCRYPTION_KEY has almost
#: certainly changed; restore the previous value"). That is one of two states,
#: and prescribing it in the other one breaks a working deployment: a database
#: whose stored Zoho credentials still decrypt is a database whose master key is
#: the *right* one, and rolling it back to make an old key row readable would
#: make every credential unreadable instead. A key row can be older than the
#: rotation the rest of the database already went through — an organization
#: connected under the dev default, then given a real key, then reconnected —
#: and that row is stale rather than the environment being wrong.
#:
#: So this names both states and the test that tells them apart, rather than
#: guessing at one. Absence of the old key is not evidence that the current one
#: is wrong.
UNREADABLE_EXPLANATION = (
    "This organization's data key does not unwrap under the current "
    "CREDENTIAL_ENCRYPTION_KEY. Two states look identical here and have "
    "opposite remedies. If the master key was rotated and the previous value "
    "still exists, restore it: nothing is lost. If the current value is the "
    "right one — which is what it means when stored connection credentials "
    "still decrypt — then this key row predates that rotation and no value "
    "will read it. `python -m app.trust.rekey` reports which state a "
    "deployment is in and can issue a fresh key for the second one.")

#: The four states a tenant's key can be found in. Names rather than booleans
#: because "usable?" collapses the two unusable states, and they need
#: completely different handling: one is a deliberate erasure that must stay
#: irreversible, the other is an accident with a way out.
KEY_ABSENT = "ABSENT"          #: no row — the next write creates one
KEY_READY = "READY"            #: unwraps under the current master key
KEY_DESTROYED = "DESTROYED"    #: erased on purpose; never resurrect it
KEY_UNREADABLE = "UNREADABLE"  #: wrapped under a master key nobody has


def inspect(session: Session, organization_id: str) -> str:
    """Which state this tenant's key is in, **without creating one**.

    ``_dek`` cannot answer this question: it goes through ``ensure_key``, so
    asking it "is this tenant's key usable?" writes a key for a tenant that has
    never had one. A report has to be able to look without touching, and so
    does anything deciding whether a recovery is even applicable.
    """
    row = _row(session, organization_id)
    if row is None:
        return KEY_ABSENT
    if row.destroyed_at is not None or not row.wrapped_dek:
        return KEY_DESTROYED
    try:
        _kek().decrypt(row.wrapped_dek.encode())
    except InvalidToken:
        return KEY_UNREADABLE
    return KEY_READY


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
        raise KeyUnavailable(UNREADABLE_EXPLANATION) from e


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
    """Destroy this tenant's data key. Irreversible by design.

    What that reaches is precisely the ciphertext written under the key — the
    vaulted display names and the AI payload log — everywhere it exists,
    backups included. Plaintext columns (``customers.name``, ``products.name``
    and the rest of ``trust/erasure.SURVIVES_PLAINTEXT``) are not affected;
    calling this "crypto-shredding the tenant" oversold it, and the signed
    receipt built on that wording is what a customer security review would
    have failed.

    The key row survives with its key material gone. A deleted row would leave
    no way to answer "was this erased?", and an erasure nobody can evidence is
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


def reissue(session: Session, organization_id: str, *, reason: str,
            actor_user_id: Optional[str]) -> models.TenantKey:
    """Replace a data key that can no longer be unwrapped. Costly, and guarded.

    The state this exists for: the wrapped DEK was written under a master key
    nobody still has, so every ciphertext under it is unreadable and no retry
    will ever change that. Left alone, the tenant is stuck — the name vault
    cannot be written, and each sync reports the same problem for ever.

    What it costs is exactly the two ciphertext classes ``erasure.DESTROYED``
    enumerates, and they are not equal. The name vault is *derived*: every name
    in it was copied from a plaintext display column, so the next sync rebuilds
    it. The model-payload log is not derived and is not rebuildable — replacing
    the key ends the ability to answer "what was sent to a model about my
    business?" for everything logged before today. So this demands a reason,
    records it, and the CLI counts those rows before it acts. It is never
    called automatically, and nothing in the request path calls it at all.

    Three refusals, each of which is the whole point:

    * **A destroyed key is never resurrected.** Erasure is meant to be
      irreversible, and an operation that hands an erased tenant a working key
      would make every receipt already issued a lie. ``KeyDestroyed``, the
      same answer every other read of that row gives.
    * **A key that works is never replaced.** Shredding a readable key would
      destroy live data to fix nothing. This is why the guard is a positive
      check for ``KEY_UNREADABLE`` rather than "not READY".
    * **A tenant with no key is not a candidate.** There is nothing broken to
      recover; the next write creates one.
    """
    if not (reason or "").strip():
        raise ValueError("Reissuing a tenant's data key requires a stated reason.")

    state = inspect(session, organization_id)
    if state == KEY_DESTROYED:
        raise KeyDestroyed(
            f"The data key for {organization_id} was destroyed. That is "
            f"deliberate and irreversible — issuing a new one here would not "
            f"recover a byte of what was written under the old one, and would "
            f"contradict the erasure receipt that says it is gone.")
    if state != KEY_UNREADABLE:
        raise ValueError(
            f"The data key for {organization_id} is {state}, not "
            f"{KEY_UNREADABLE}. Reissuing only ever applies to a key that "
            f"cannot be unwrapped; replacing any other one destroys readable "
            f"data to fix nothing.")

    row = _row(session, organization_id)
    assert row is not None            # KEY_UNREADABLE implies a row
    row.wrapped_dek = _kek().encrypt(_new_dek()).decode()
    row.reissued_at = datetime.now(timezone.utc)
    row.reissued_by_user_id = actor_user_id
    row.reissue_reason = reason.strip()
    session.flush()
    return row
