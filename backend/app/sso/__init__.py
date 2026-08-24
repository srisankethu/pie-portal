"""Single sign-on: proving who somebody is, somewhere else.

The one design rule, and everything here follows from it: **an SSO sign-in
produces exactly what a password sign-in produces** — a ``UserSession`` row and
a token from ``authz.issue_token`` — so nothing downstream can tell them apart.
Revocation, the absolute and idle session limits, ``load_principal``, the tenant
GUC announced from the token: all unchanged, because none of them learns that a
second way in exists.

The alternative — an SSO-specific session type with its own lifetime and its own
revocation path — is how a deployment ends up with one door it has audited and
one it has not.
"""
from .verify import (IdTokenInvalid, JwksCache, OidcDiscovery, verify_id_token)

__all__ = ["IdTokenInvalid", "JwksCache", "OidcDiscovery", "verify_id_token"]
