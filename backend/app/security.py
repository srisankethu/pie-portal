"""Demo authentication + role model.

Two roles, as in the design: ``sales`` (no economics ever reach the client) and
``mgmt`` (full economics visible). This is a demo-grade auth: fixed accounts,
any password, stateless signed tokens. A real deployment swaps this for the
organisation's identity provider — the role gate downstream is unchanged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional

from .config import settings

# Demo accounts (email -> role), matching the artifact's sign-in hint.
ACCOUNTS = {
    "r.nair@sanketh.in": {"role": "sales", "name": "R. Nair"},
    "s.menon@sanketh.in": {"role": "mgmt", "name": "S. Menon"},
}

ROLE_SALES = "sales"
ROLE_MGMT = "mgmt"


@dataclass
class Principal:
    email: str
    role: str
    name: str

    @property
    def is_mgmt(self) -> bool:
        return self.role == ROLE_MGMT


def _sign(payload: bytes) -> str:
    sig = hmac.new(settings.AUTH_SECRET.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def issue_token(email: str) -> Optional[str]:
    acct = ACCOUNTS.get(email.lower().strip())
    if not acct:
        return None
    body = {"email": email.lower().strip(), "role": acct["role"],
            "name": acct["name"], "iat": int(time.time())}
    raw = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return f"{raw}.{_sign(raw.encode())}"


def _b64pad(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_token(token: str) -> Optional[Principal]:
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(raw.encode())):
        return None
    try:
        body = json.loads(_b64pad(raw))
    except Exception:  # noqa: BLE001
        return None
    email = body.get("email")
    if email not in ACCOUNTS:
        return None
    return Principal(email=email, role=body["role"], name=body["name"])


def authenticate(email: str, password: str) -> Optional[Principal]:
    """Demo: any non-empty password authenticates a known account."""
    acct = ACCOUNTS.get((email or "").lower().strip())
    if not acct or not password:
        return None
    return Principal(email=email.lower().strip(), role=acct["role"], name=acct["name"])
