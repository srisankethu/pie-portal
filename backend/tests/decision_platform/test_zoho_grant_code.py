"""Connecting with the code the Zoho console actually hands you.

Every Zoho grant starts life as a **grant code**: single-use, alive for a few
minutes, exchanged exactly once for the refresh token every later pull runs on.
Until this existed the platform accepted only the second of those, and step 3
of ``docs/zoho-setup.md`` was a ``curl`` an owner ran by hand between the two
screens.

That instruction is the defect these tests exist for, not the inconvenience.
The two values are indistinguishable by sight — both ``1000.xxxxxxxx.yyyyyyyy``
— so the code goes into the box marked "Refresh token" often enough, and the
connection then fails its check with ``invalid_code`` and a sentence about a
*revoked* token, on a credential minted ninety seconds earlier. The remedy the
message implies (regenerate the Self Client) replaces a client pair that was
correct and produces another code, which fails the same way.

So: the server spends the code itself, the request says which of the two it
carries rather than the server sniffing it, and a refusal names the thing that
was actually refused.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import crypto, oauth
from app.db import get_session
from app.domain import models
from app.routers import connections as connections_router, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

OWNER = "s.menon@pie.example"

TOKEN_PATH = "/oauth/v2/token"
DC = "https://accounts.zoho.in"


# ── the token endpoint, stubbed where only the exchange can see it ──────────
class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _Recorder:
    """Every request that reached Zoho's token endpoint, and one canned reply.

    Stands in for ``httpx.Client`` on the ``oauth`` module alone, so
    ``zoho_client``'s own local import is untouched and a connection check
    behaves exactly as it does in every other test here.
    """

    HTTPError = httpx.HTTPError

    def __init__(self, body, status=200):
        self._body, self._status = body, status
        self.calls: list[dict] = []

    # `httpx.Client(...)` — the module attribute, used as a constructor.
    def Client(self, *args, **kwargs):  # noqa: N802 — it is a class in httpx
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, data=None, **kwargs):
        self.calls.append({"url": url, "data": dict(data or {})})
        return _Response(self._body, self._status)

    @property
    def exchanges(self) -> list[dict]:
        return [c for c in self.calls
                if c["data"].get("grant_type") == "authorization_code"]


@pytest.fixture()
def zoho(monkeypatch):
    """Zoho answers a code exchange with a refresh token, by default."""

    def _install(body=None, status=200):
        rec = _Recorder(body if body is not None else {
            "access_token": "at.short-lived", "refresh_token": "1000.rt.minted",
            "expires_in": 3600}, status)
        monkeypatch.setattr(oauth, "httpx", rec)
        return rec

    return _install


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    for r in (platform_auth.router, connections_router.router):
        app.include_router(r)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c):
    r = c.post("/api/v1/auth/login",
               json={"email": OWNER, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _add(c, **kw):
    body = {"zoho_organization_id": "60036630626", "label": "4U Precision",
            "client_id": "1000.APP", "client_secret": "the-client-secret",
            "accounts_base": DC,
            "api_base": "https://www.zohoapis.in/books/v3"}
    body.update(kw)
    return c.post("/api/v1/connections", json=body, headers=_hdr(c))


def _credentials(c) -> list[models.ZohoCredential]:
    s = c.Maker()
    try:
        return list(s.query(models.ZohoCredential))
    finally:
        s.close()


# ── the add path ────────────────────────────────────────────────────────────
def test_a_grant_code_is_exchanged_and_only_the_refresh_token_is_stored(client, zoho):
    """The whole point: what is persisted is the product of the exchange.

    Storing the code would be worse than the bug this replaces — it dies in
    minutes, so the connection would check green on the way in and be dead by
    the time anything synced.
    """
    rec = zoho()

    r = _add(client, grant_code="1000.code.fresh")
    assert r.status_code == 201, r.text

    (cred,) = _credentials(client)
    assert crypto.decrypt(cred.refresh_token_encrypted) == "1000.rt.minted"
    assert "1000.code.fresh" not in crypto.decrypt(cred.refresh_token_encrypted)
    assert rec.exchanges, "no exchange was attempted"
    assert rec.exchanges[0]["data"]["code"] == "1000.code.fresh"


def test_the_exchange_goes_to_zohos_actual_token_endpoint(client, zoho):
    """``/oauth/token`` is not a Zoho URL. It is what this module posted to for
    as long as the redirect flow existed, which is one reason that flow had
    never completed an authorization end to end — ``zoho_client`` and the setup
    doc had ``/oauth/v2/token`` right the whole time."""
    rec = zoho()
    _add(client, grant_code="1000.code.fresh")

    assert rec.exchanges[0]["url"] == f"{DC}{TOKEN_PATH}"


def test_the_client_secret_travels_in_the_body_not_the_url(client, zoho):
    """The invariant ``zoho_client._access_token`` states at length: httpx logs
    request URLs at INFO, so a secret in a query string is written to stdout, to
    the log file, and from there onto a screen."""
    rec = zoho()
    _add(client, grant_code="1000.code.fresh")

    call = rec.exchanges[0]
    assert "?" not in call["url"]
    assert call["data"]["client_secret"] == "the-client-secret"
    assert call["data"]["client_id"] == "1000.APP"


def test_a_self_client_exchange_sends_no_redirect_uri(client, zoho):
    """A Self Client has none registered — it is the client type you pick
    because there is no browser to come back to — and Zoho refuses an empty
    one."""
    rec = zoho()
    _add(client, grant_code="1000.code.fresh")

    assert "redirect_uri" not in rec.exchanges[0]["data"]


def test_a_refused_code_is_named_as_a_code_not_a_revoked_token(client, zoho):
    """The sentence that sent people to regenerate a correct Self Client.

    ``invalid_code`` is the same string whichever grant was sent, so the help
    text has to key on what this server *asked*, not only on what Zoho
    answered.
    """
    zoho({"error": "invalid_code"})

    r = _add(client, grant_code="1000.code.stale")
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "grant code" in detail
    assert "single-use" in detail
    assert "revoked" not in detail, "that sentence is about a refresh token"


def test_a_refused_code_leaves_no_half_built_connection(client, zoho):
    """A code is spent before anything is written, so a failure writes nothing.

    The other order — create the credential, then token it — puts a dead
    sign-in on the screen every time the exchange fails, and the exchange is
    exactly where a stale code is found out.
    """
    zoho({"error": "invalid_code"})

    assert _add(client, grant_code="1000.code.stale").status_code == 400

    s = client.Maker()
    try:
        assert s.query(models.ZohoCredential).count() == 0
        assert s.query(models.ZohoConnection).count() == 0
    finally:
        s.close()


def test_zoho_refusing_under_http_400_still_explains_the_credential(client, zoho):
    """Zoho reports a refused code with a perfectly good JSON body, sometimes
    under 200 and sometimes under 400. Reading the status first turned the
    second case into "Network error while authorizing: Client error '400 Bad
    Request'" — a network sentence about a credential."""
    zoho({"error": "invalid_code"}, status=400)

    r = _add(client, grant_code="1000.code.stale")
    assert r.status_code == 400, r.text
    assert "grant code" in r.json()["detail"]
    assert "Network error" not in r.json()["detail"]


def test_a_refresh_token_still_connects_without_an_exchange(client, zoho):
    """The path that already worked keeps working, and spends no code."""
    rec = zoho()

    assert _add(client, refresh_token="1000.rt.byhand").status_code == 201
    (cred,) = _credentials(client)
    assert crypto.decrypt(cred.refresh_token_encrypted) == "1000.rt.byhand"
    assert rec.exchanges == []


@pytest.mark.parametrize("supplied, why", [
    ({"grant_code": "1000.c", "refresh_token": "1000.r"}, "both"),
    ({}, "neither"),
])
def test_the_request_says_which_credential_it_carries(client, zoho, supplied, why):
    """No sniffing, in either direction. The two look identical, so a body
    carrying both has not said which it means, and one carrying neither has not
    supplied a credential at all."""
    zoho()

    r = _add(client, **supplied)
    assert r.status_code == 400, f"{why}: {r.text}"
    assert "one, not both" in r.json()["detail"]


# ── the rotate path ─────────────────────────────────────────────────────────
def _connected(c, zoho) -> str:
    zoho()
    r = _add(c, refresh_token="1000.rt.original")
    assert r.status_code == 201, r.text
    return r.json()["connection_id"]


def test_rotating_from_a_grant_code_replaces_the_stored_token(client, zoho):
    """The case that needs this most: the console's answer to a revoked token
    is a fresh *code*, and rotation was the one screen that could not take
    one."""
    connection_id = _connected(client, zoho)
    rec = zoho({"access_token": "at", "refresh_token": "1000.rt.rotated",
                "expires_in": 3600})

    r = client.post(f"/api/v1/connections/{connection_id}/rotate",
                    json={"grant_code": "1000.code.fresh"}, headers=_hdr(client))
    assert r.status_code == 200, r.text

    (cred,) = _credentials(client)
    assert crypto.decrypt(cred.refresh_token_encrypted) == "1000.rt.rotated"
    assert rec.exchanges[0]["data"]["client_secret"] == "the-client-secret", \
        "the stored client pair is what a code generated under it must be spent against"
    assert rec.exchanges[0]["url"].startswith(DC), "and at the stored data centre"


def test_a_code_from_a_different_app_is_exchanged_against_that_app(client, zoho):
    """Supplying a new client pair means the code came from a new Self Client.
    Spending it against the old id is the failure this screen already explains
    at length: Zoho answers ``invalid_client_secret``, which reads as "your
    secret is wrong" and sends people to change the data centre."""
    connection_id = _connected(client, zoho)
    rec = zoho({"access_token": "at", "refresh_token": "1000.rt.newapp",
                "expires_in": 3600})

    r = client.post(f"/api/v1/connections/{connection_id}/rotate",
                    json={"grant_code": "1000.code.fresh",
                          "client_id": "1000.OTHERAPP",
                          "client_secret": "the-other-secret"},
                    headers=_hdr(client))
    assert r.status_code == 200, r.text

    assert rec.exchanges[0]["data"]["client_id"] == "1000.OTHERAPP"
    assert rec.exchanges[0]["data"]["client_secret"] == "the-other-secret"
    (cred,) = _credentials(client)
    assert cred.client_id == "1000.OTHERAPP"
    assert crypto.decrypt(cred.refresh_token_encrypted) == "1000.rt.newapp"


def test_a_refused_code_leaves_the_working_token_in_place(client, zoho):
    """A rotation that fails must not take the connection down with it — the
    old token is still good until Zoho says otherwise."""
    connection_id = _connected(client, zoho)
    zoho({"error": "invalid_code"})

    r = client.post(f"/api/v1/connections/{connection_id}/rotate",
                    json={"grant_code": "1000.code.stale"}, headers=_hdr(client))
    assert r.status_code == 400, r.text

    (cred,) = _credentials(client)
    assert crypto.decrypt(cred.refresh_token_encrypted) == "1000.rt.original"


def test_rotating_says_which_credential_it_carries(client, zoho):
    connection_id = _connected(client, zoho)

    r = client.post(f"/api/v1/connections/{connection_id}/rotate",
                    json={}, headers=_hdr(client))
    assert r.status_code == 400
    assert "one, not both" in r.json()["detail"]


def test_half_a_client_pair_is_refused_rather_than_merged(client, zoho):
    """A client keeps one id across data centres and has a *separate secret in
    each*, so filling the missing half from the stored row builds a pair Zoho
    refuses — and reports as ``invalid_client_secret``, the message this screen
    already works hard to keep people from acting on."""
    connection_id = _connected(client, zoho)
    zoho()

    r = client.post(f"/api/v1/connections/{connection_id}/rotate",
                    json={"grant_code": "1000.code.fresh",
                          "client_id": "1000.OTHERAPP"},
                    headers=_hdr(client))
    assert r.status_code == 400, r.text
    assert "together or not at all" in r.json()["detail"]

    (cred,) = _credentials(client)
    assert cred.client_id == "1000.APP", "nothing was half-replaced"
    assert crypto.decrypt(cred.refresh_token_encrypted) == "1000.rt.original"
