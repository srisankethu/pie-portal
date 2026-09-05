"""What an ID token has to survive before anyone is signed in by it.

Past `verify_id_token` the caller *is* whoever the token says, so this file is
written against the ways a token can look valid and not be one. The four that
matter are not typos — they are the documented failure modes of the format:

* **``alg: none``** — a token with no signature at all, which an implementation
  that reads the algorithm out of the header will happily "verify";
* **algorithm confusion** — an RS256 token re-signed HS256 using the issuer's
  *public* key as the HMAC secret. The public key is public, so anyone can mint
  one. This is CVE-2022-29217's shape, and passing ``algorithms=`` explicitly is
  what closes it;
* **a replayed token** — correctly signed, unexpired, minted by the right
  issuer for the right client, and issued for somebody else's sign-in. Only the
  nonce separates it from a real one;
* **a renamed issuer** — a discovery document fetched from a configured URL
  that declares an ``issuer`` other than the one asked for, which would let a
  wrong URL silently redefine who this organization trusts.

The keys here are generated per run rather than fixtured. A committed private
key is a committed private key whatever the comment above it says.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import clock
from app.sso.verify import (ALGORITHMS, IdTokenInvalid, JwksCache,
                            OidcDiscovery, verify_id_token)

ISSUER = "https://idp.example.test"
CLIENT_ID = "pie-portal-test-client"
NONCE = "n-0S6_WzA2Mj"
KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture()
def discovery():
    return OidcDiscovery(
        issuer=ISSUER,
        authorization_endpoint=f"{ISSUER}/authorize",
        token_endpoint=f"{ISSUER}/token",
        jwks_uri=f"{ISSUER}/jwks",
    )


@pytest.fixture()
def cache(keypair, monkeypatch):
    """A `JwksCache` serving one locally generated key, over no network.

    The real `PyJWKClient` is used and only its *fetch* is replaced, so key
    selection by `kid` — the part that decides which key verifies a token — is
    the library's own and not a stub's.
    """
    _, public = keypair
    numbers = public.public_numbers()

    def _b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return jwt.utils.base64url_encode(raw).decode()

    jwks = {"keys": [{"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256",
                      "n": _b64(numbers.n), "e": _b64(numbers.e)}]}

    real = JwksCache()
    original = real.client_for

    def _client_for(uri: str):
        client = original(uri)
        monkeypatch.setattr(client, "fetch_data", lambda: json.loads(json.dumps(jwks)))
        return client

    monkeypatch.setattr(real, "client_for", _client_for)
    return real


def _token(keypair, *, alg: str = "RS256", nonce: str = NONCE,
           issuer: str = ISSUER, audience: str = CLIENT_ID,
           expires_in: int = 300, key: Any = None,
           headers: dict | None = None, **extra) -> str:
    private, _ = keypair
    issued = clock.now()
    claims = {
        "iss": issuer, "aud": audience, "sub": "idp-user-42",
        "email": "person@example.test", "nonce": nonce,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=expires_in)).timestamp()),
        **extra,
    }
    return jwt.encode(claims, key if key is not None else private,
                      algorithm=alg, headers={"kid": KID, **(headers or {})})


# ── the happy path, so the refusals below mean something ────────────────────
def test_a_correctly_minted_token_is_accepted(keypair, discovery, cache):
    claims = verify_id_token(_token(keypair), discovery=discovery,
                             client_id=CLIENT_ID, nonce=NONCE, cache=cache)
    assert claims["email"] == "person@example.test"
    assert claims["sub"] == "idp-user-42"


# ── the four that matter ────────────────────────────────────────────────────
def test_a_token_with_no_signature_is_refused(keypair, discovery, cache):
    """`alg: none`. An implementation that trusts the header verifies it."""
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "nonce": NONCE,
         "iat": int(clock.now().timestamp()),
         "exp": int((clock.now() + timedelta(seconds=300)).timestamp())},
        key="", algorithm="none", headers={"kid": KID})
    with pytest.raises(IdTokenInvalid):
        verify_id_token(unsigned, discovery=discovery, client_id=CLIENT_ID,
                        nonce=NONCE, cache=cache)


def test_the_public_key_cannot_be_used_as_an_hmac_secret(keypair, discovery,
                                                         cache):
    """Algorithm confusion, CVE-2022-29217's shape.

    An RS256 token re-signed HS256 with the issuer's *public* key as the secret.
    The public key is public, so anyone can mint this; an implementation that
    picks the algorithm from the header will verify it with the same key it
    would have used for RS256 and accept it.
    """
    import hashlib
    import hmac as hmaclib

    from cryptography.hazmat.primitives import serialization

    _, public = keypair
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)

    # Assembled by hand rather than through `jwt.encode`, which refuses to HMAC
    # with a PEM — PyJWT declining to *build* the forgery says nothing about
    # whether this application would *accept* one, and it is the acceptance
    # that matters. An attacker has no such scruples and neither does this
    # test: header, payload, and an HMAC over them keyed with the public key.
    def _seg(obj: dict) -> bytes:
        return jwt.utils.base64url_encode(
            json.dumps(obj, separators=(",", ":")).encode())

    signing_input = b".".join((
        _seg({"alg": "HS256", "typ": "JWT", "kid": KID}),
        _seg({"iss": ISSUER, "aud": CLIENT_ID, "sub": "attacker",
              "nonce": NONCE, "iat": int(clock.now().timestamp()),
              "exp": int((clock.now() + timedelta(seconds=300)).timestamp())}),
    ))
    signature = jwt.utils.base64url_encode(
        hmaclib.new(pem, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    assert "HS256" not in ALGORITHMS, (
        "a symmetric algorithm in the accepted list is this attack, allowed")
    with pytest.raises(IdTokenInvalid):
        verify_id_token(forged, discovery=discovery, client_id=CLIENT_ID,
                        nonce=NONCE, cache=cache)


def test_a_token_minted_for_another_sign_in_is_refused(keypair, discovery,
                                                       cache):
    """Correctly signed, unexpired, right issuer, right client — and issued for
    a different session. The nonce is the only thing that separates it from a
    real one, which is why it is a required argument rather than an option."""
    replayed = _token(keypair, nonce="somebody-elses-nonce")
    with pytest.raises(IdTokenInvalid) as caught:
        verify_id_token(replayed, discovery=discovery, client_id=CLIENT_ID,
                        nonce=NONCE, cache=cache)
    assert "nonce" in str(caught.value)


def test_a_token_with_no_nonce_at_all_is_refused(keypair, discovery, cache):
    """The absent case, separately: an empty nonce must not compare equal to a
    missing one, which is the §1 shape — absence read as a pass."""
    without = _token(keypair, nonce="")
    with pytest.raises(IdTokenInvalid):
        verify_id_token(without, discovery=discovery, client_id=CLIENT_ID,
                        nonce=NONCE, cache=cache)


# ── the ordinary claim checks, which are not optional either ────────────────
@pytest.mark.parametrize("kwargs", [
    {"issuer": "https://someone-else.example.test"},
    {"audience": "a-different-client"},
    {"expires_in": -600},
])
def test_a_token_failing_any_registered_claim_is_refused(keypair, discovery,
                                                         cache, kwargs):
    with pytest.raises(IdTokenInvalid):
        verify_id_token(_token(keypair, **kwargs), discovery=discovery,
                        client_id=CLIENT_ID, nonce=NONCE, cache=cache)


def test_a_missing_required_claim_is_refused(keypair, discovery, cache):
    """`sub` is what identifies the person. A token without one verifies
    cryptographically and identifies nobody."""
    private, _ = keypair
    issued = clock.now()
    token = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "nonce": NONCE,
         "iat": int(issued.timestamp()),
         "exp": int((issued + timedelta(seconds=300)).timestamp())},
        private, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(IdTokenInvalid):
        verify_id_token(token, discovery=discovery, client_id=CLIENT_ID,
                        nonce=NONCE, cache=cache)


def test_the_arguments_are_required_rather_than_defaulted(keypair, discovery,
                                                          cache):
    for bad in ({"token": ""}, {"client_id": ""}, {"nonce": ""}):
        kwargs = {"token": _token(keypair), "client_id": CLIENT_ID,
                  "nonce": NONCE, **bad}
        with pytest.raises(IdTokenInvalid):
            verify_id_token(kwargs.pop("token"), discovery=discovery,
                            cache=cache, **kwargs)


# ── a rotated key costs one re-fetch, not a restart ─────────────────────────
def test_a_rotated_signing_key_is_re_fetched_once(keypair, discovery, cache):
    """A cache with no expiry turns a key rotation into an outage a restart
    fixes — the worst kind, because it looks intermittent."""
    verify_id_token(_token(keypair), discovery=discovery, client_id=CLIENT_ID,
                    nonce=NONCE, cache=cache)

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    stranger = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "nonce": NONCE,
         "iat": int(clock.now().timestamp()),
         "exp": int((clock.now() + timedelta(seconds=300)).timestamp())},
        other, algorithm="RS256", headers={"kid": "a-key-we-do-not-have"})

    # Refused, and the cache was dropped on the way — the next legitimate token
    # still verifies rather than inheriting a poisoned cache.
    with pytest.raises(IdTokenInvalid):
        verify_id_token(stranger, discovery=discovery, client_id=CLIENT_ID,
                        nonce=NONCE, cache=cache)
    verify_id_token(_token(keypair), discovery=discovery, client_id=CLIENT_ID,
                    nonce=NONCE, cache=cache)


# ── the fetches, which are the other half of the boundary ───────────────────
#
# An `issuer` is owner-supplied configuration that becomes a server-side fetch,
# and `jwks_uri` comes out of a document that issuer controls. Both are the
# class `ingestion/url_safety` was written for, and both were unguarded in the
# first version of `verify.py` — found by reading it against the two transports
# that already do this, not by a test failing.
@pytest.mark.parametrize("issuer", [
    "http://169.254.169.254",          # cloud instance metadata
    "http://127.0.0.1:8000",           # the deployment's own API
    "http://localhost/idp",
    "http://[::1]/idp",
    "http://10.0.0.5/idp",             # private range
    "file:///etc/passwd",              # not http(s) at all
])
def test_an_issuer_pointing_inside_the_deployment_is_refused(issuer):
    """Refused before the request, not after the response.

    The metadata address is the one that matters: reached, it hands back cloud
    credentials, and a discovery-document error message is enough of a channel
    to read them through.
    """
    from app.ingestion.url_safety import UnsafeSourceUrl

    # `UnsafeSourceUrl` specifically, not "any error". Without the guard these
    # addresses still fail — with a connection error, or worse, a *success* for
    # the metadata service — and a test that accepted any exception passed
    # cleanly with both guards removed. That is the shape this whole suite is
    # about, found by mutating the module rather than by reading the test.
    with pytest.raises(UnsafeSourceUrl):
        OidcDiscovery.fetch(issuer)


def test_a_jwks_url_pointing_inside_the_deployment_is_refused():
    """The second hop, guarded separately.

    An issuer that passes the check above still writes its own discovery
    document, so it can name any `jwks_uri` it likes. Checking only the first
    URL would leave the second as the way in — which is what the first version
    of this module did.
    """
    from app.ingestion.url_safety import UnsafeSourceUrl

    with pytest.raises((UnsafeSourceUrl, ValueError)):
        JwksCache().client_for("http://169.254.169.254/jwks")


def test_the_discovery_fetch_does_not_follow_redirects():
    """`RestTransport` sets `follow_redirects=False` and this must match it.

    The URL is vetted *before* the request; a redirect is a second URL nobody
    vetted, so following one gives back exactly what the guard took away.
    """
    from app.sso import verify as verify_module

    assert verify_module.FOLLOW_REDIRECTS is False
