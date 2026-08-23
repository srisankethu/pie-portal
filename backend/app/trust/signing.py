"""One canonicalisation, one keyed signature — shared by everything that signs.

Two things in this package attest to something: an erasure receipt says what
existed when a tenant's key was destroyed, and an audit entry says what happened
and links to the entry before it. Both answer the same question — "has this been
altered since it was written?" — and both answer it the same way: HMAC-SHA256
over a canonical JSON rendering of the covered structure, keyed by the
deployment's ``CREDENTIAL_ENCRYPTION_KEY``.

This module exists because that had one implementation and was about to have
two. A second canonicalisation would not fail loudly; it would agree with the
first on every value either was tested with and disagree on the first dict whose
keys happened to be inserted in a different order, at which point one of the two
verifiers reports a forgery that never happened. ``sort_keys=True`` with the
tightest separators is what makes the bytes a function of the *value* rather
than of how it was built.

**Keyed, not bare.** A plain SHA-256 chain is tamper-*evident* only against
somebody who cannot recompute it, and anybody holding the database can recompute
a bare hash — rewrite a row, rewrite every hash after it, and the chain still
verifies. The HMAC key does not live in the database, so re-signing a doctored
history needs the application's secret as well as its storage. It is not proof
against an attacker who has both; nothing in one system is. It is the difference
between "needs database access" and "needs database access and the key".

Rotating ``CREDENTIAL_ENCRYPTION_KEY`` invalidates every signature made under
the old one — receipts and audit entries alike would read as tampered. That is a
real operational constraint and is stated here rather than discovered: this key
is not rotatable without a re-signing migration that is deliberately not
provided, because a routine that re-signs history is also the routine that
launders it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Optional

from .. import clock
from ..config import settings


def utc_iso(value: Optional[datetime]) -> Optional[str]:
    """A timestamp as ISO-8601 in UTC, whatever offset it arrives carrying.

    Here rather than in each signer, because it has now been the same defect
    twice. A signature covers a *string*, so it must depend only on the instant
    and never on which machine or session rendered it — and ``clock.iso``
    deliberately preserves whatever offset it is handed, which is right for
    display and wrong here.

    Both signers write a UTC-aware ``clock.now()`` and sign that, so the
    signature always covers ``+00:00``. Verification reads the value back from
    the database, and psycopg renders a ``timestamptz`` in the *session*
    TimeZone, which follows the server's. On a Postgres set to Asia/Kolkata —
    the likely zone for this product, whose three legal entities are Indian —
    every row would read back as ``+05:30``, hash differently, and be reported
    altered although nothing had touched it.

    ``audit.covered_body`` was fixed for that; ``erasure.receipt_body`` was not,
    and an erasure receipt is the proof of deletion handed to a departing
    customer. It would have told them their deletion had been tampered with.

    Normalising on *read* invalidates nothing: what was signed was already
    ``+00:00``, so this restores the string rather than changing it.
    """
    if value is None:
        return None
    aware = clock.aware(value)
    return None if aware is None else aware.astimezone(timezone.utc).isoformat()


def canonical(body: Any) -> bytes:
    """The exact bytes a signature covers. Sorted keys, no incidental whitespace."""
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign(body: Any) -> str:
    """HMAC-SHA256 of ``body``, hex. 64 characters, which is what the columns hold."""
    return hmac.new(settings.CREDENTIAL_ENCRYPTION_KEY.encode(),
                    canonical(body), hashlib.sha256).hexdigest()


def matches(body: Any, signature: str) -> bool:
    """Whether ``signature`` is this body's. Constant-time, and ``None``-safe.

    ``compare_digest`` rather than ``==`` so the comparison does not leak, by
    timing, how many leading characters of a forged signature were right.
    """
    return hmac.compare_digest(sign(body), signature or "")
