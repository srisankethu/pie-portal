"""Checking an ID token, and refusing everything that is not one.

This is the security boundary of the whole feature: past it, the caller *is*
whoever the token says. So it is deliberately narrow, it accepts only what it
was told to expect, and every rejection names what failed.

**Why PyJWT rather than a hand-rolled verify.** Sixty lines of base64url and
RSA would avoid a dependency, and would be the wrong sixty lines to write. The
failure modes here are well known and subtle — ``alg: none``, an RS256 token
re-signed HS256 using the *public* key as the HMAC secret, a ``kid`` that
selects a key the caller supplied — and a library that has already been through
them (CVE-2022-29217 among them) is worth more than the absence of a line in
``requirements.txt``. Algorithms are passed explicitly on every call, which is
what closes the confusion class rather than trusting the header.

**What is checked, and none of it is optional:** the signature against a key
from the issuer's own JWKS; ``iss`` exactly equal to the configured issuer;
``aud`` containing the configured client id; ``exp`` and ``iat`` within the
allowed skew; and ``nonce`` equal to the one this application generated for
this sign-in. The nonce is the part people leave out — without it a token
obtained for another session at the same issuer is replayable here.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import jwt
from jwt import PyJWKClient

from ..clock import now as _now
from ..ingestion.url_safety import require_safe_source_url

log = logging.getLogger("pie_portal.sso.verify")

#: Signature algorithms accepted. Asymmetric only, and named rather than read
#: off the token: a symmetric algorithm here would let anyone holding the
#: issuer's *public* key mint a token this function accepts, which is the
#: confusion class in one sentence.
ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384")

#: How far a clock may drift before a token is refused. Sixty seconds is the
#: usual allowance; larger values start extending the life of a replayed token.
LEEWAY_SECONDS = 60

#: How long a fetched discovery document or key set is trusted before it is
#: read again. Long enough that a sign-in does not pay for two round trips,
#: short enough that a rotated key is picked up without a restart.
CACHE_SECONDS = 600

#: Every outbound call here talks to an identity provider on the sign-in path,
#: so it must fail fast rather than hold a request open.
HTTP_TIMEOUT = 10.0

#: Redirects are not followed, matching ``ingestion/erp/transport.RestTransport``
#: and for the same reason: the URL is checked against the SSRF guard *before*
#: the request, and a redirect is a second URL nobody checked. An issuer that
#: needs a redirect to serve its own discovery document is misconfigured, and
#: the fix is the right URL rather than a hop this application cannot vet.
FOLLOW_REDIRECTS = False


class IdTokenInvalid(Exception):
    """The token is not one this organization should be signed in by.

    One exception for every rejection on purpose. The caller turns it into a
    single refusal, because "the signature failed" and "the nonce did not
    match" are the same answer to whoever is trying, and telling them apart is
    a way to probe.
    """


@dataclass(frozen=True)
class OidcDiscovery:
    """The endpoints an issuer publishes, as this application needs them."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    #: What the provider says it supports. Recorded and reported rather than
    #: acted on — a provider that omits PKCE from this list may still accept it,
    #: and refusing on the strength of an advertisement would break a working
    #: setup for a documentation defect.
    code_challenge_methods: tuple[str, ...] = ()

    @classmethod
    def fetch(cls, issuer: str, *, client: Optional[httpx.Client] = None
              ) -> "OidcDiscovery":
        """Read ``/.well-known/openid-configuration`` from an issuer.

        The issuer is compared to what the document declares, because a
        discovery document is fetched from a URL somebody configured and the
        ``issuer`` inside it is what every later ``iss`` check is measured
        against. Letting the document rename itself would mean a misconfigured
        URL silently redefines which provider this organization trusts.
        """
        base = issuer.rstrip("/")
        url = f"{base}/.well-known/openid-configuration"
        # An issuer is owner-supplied configuration that becomes a server-side
        # fetch, which is the class `ingestion/url_safety` exists for — the
        # same guard `zoho_client` and `RestTransport` already run. Without it
        # an owner can point this at `http://169.254.169.254/…` and read the
        # deployment's cloud metadata out of a discovery error message.
        require_safe_source_url(url, field="issuer")
        owned = client is None
        client = client or httpx.Client(timeout=HTTP_TIMEOUT,
                                        follow_redirects=FOLLOW_REDIRECTS)
        try:
            response = client.get(url)
            response.raise_for_status()
            doc = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IdTokenInvalid(
                f"the issuer's discovery document could not be read: {exc}") from exc
        finally:
            if owned:
                client.close()

        declared = str(doc.get("issuer") or "")
        if declared.rstrip("/") != base:
            raise IdTokenInvalid(
                f"the discovery document at {url} declares issuer {declared!r}, "
                f"which is not {issuer!r}")
        missing = [k for k in ("authorization_endpoint", "token_endpoint",
                               "jwks_uri") if not doc.get(k)]
        if missing:
            raise IdTokenInvalid(
                f"the discovery document is missing {', '.join(missing)}")
        return cls(
            issuer=base,
            authorization_endpoint=str(doc["authorization_endpoint"]),
            token_endpoint=str(doc["token_endpoint"]),
            jwks_uri=str(doc["jwks_uri"]),
            code_challenge_methods=tuple(
                str(m) for m in doc.get("code_challenge_methods_supported") or ()),
        )


@dataclass
class JwksCache:
    """One ``PyJWKClient`` per issuer, kept for a while.

    A signing key set is fetched over the network, and fetching it on every
    sign-in would put an identity provider's availability on the critical path
    of every request. It is also not free to hold forever: keys rotate, and a
    cache with no expiry turns a rotation into an outage that a restart fixes,
    which is the worst kind — it looks intermittent.

    Keyed by ``jwks_uri`` rather than by organization, because two tenants on
    the same identity provider genuinely share a key set and fetching it twice
    buys nothing.
    """

    seconds: int = CACHE_SECONDS
    _clients: dict[str, tuple[float, PyJWKClient]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def client_for(self, jwks_uri: str) -> PyJWKClient:
        stamp = _now().timestamp()
        with self._lock:
            cached = self._clients.get(jwks_uri)
            if cached is not None and stamp - cached[0] < self.seconds:
                return cached[1]
            # Guarded too, and separately. `jwks_uri` comes out of the
            # discovery document rather than from configuration, so it is
            # attacker-influenced by one more hop than the issuer is: an issuer
            # that passes the check can still name any JWKS URL it likes.
            # Checking only the first URL would leave the second as the way in,
            # which is what the first version of this module did.
            require_safe_source_url(jwks_uri, field="jwks_uri")
            client = PyJWKClient(jwks_uri, cache_keys=True,
                                 timeout=int(HTTP_TIMEOUT))
            self._clients[jwks_uri] = (stamp, client)
            return client

    def forget(self, jwks_uri: str) -> None:
        """Drop one issuer's keys — used after a signature failure, once.

        A key that rotated between the cache being filled and a token being
        signed produces exactly one failure that a re-fetch fixes. Retrying
        blindly would turn a genuinely bad signature into two provider calls
        per attempt, so the caller retries once and only on that failure.
        """
        with self._lock:
            self._clients.pop(jwks_uri, None)


_JWKS = JwksCache()


def verify_id_token(token: str, *, discovery: OidcDiscovery, client_id: str,
                    nonce: str, cache: Optional[JwksCache] = None
                    ) -> dict[str, Any]:
    """The claims of a token this organization should be signed in by, or raise.

    ``nonce`` is required rather than optional. It is the value this
    application generated for this sign-in and handed to the provider, and
    comparing it is what stops a token minted for a different session at the
    same issuer being replayed here. A signature that verifies proves the
    issuer minted *a* token; the nonce proves it minted *this* one.
    """
    cache = cache or _JWKS
    if not token or not client_id or not nonce:
        raise IdTokenInvalid("a token, a client id and a nonce are all required")

    for attempt in (1, 2):
        try:
            key = cache.client_for(discovery.jwks_uri).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key,
                # Named explicitly, never taken from the token's own header.
                algorithms=list(ALGORITHMS),
                audience=client_id,
                issuer=discovery.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"],
                         "verify_signature": True, "verify_exp": True,
                         "verify_iat": True, "verify_aud": True,
                         "verify_iss": True},
            )
            break
        except jwt.PyJWKClientError as exc:
            # The key was not in the set we hold. Once, this is a rotation.
            if attempt == 1:
                cache.forget(discovery.jwks_uri)
                continue
            raise IdTokenInvalid(f"no signing key matched the token: {exc}") from exc
        except jwt.InvalidTokenError as exc:
            raise IdTokenInvalid(f"the token was refused: {exc}") from exc

    presented = str(claims.get("nonce") or "")
    if not presented or presented != nonce:
        raise IdTokenInvalid(
            "the token's nonce does not match the one issued for this sign-in")
    return claims
