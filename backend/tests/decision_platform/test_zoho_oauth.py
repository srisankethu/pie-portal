"""The customer-facing Zoho authorization, and the two defects that killed it.

This flow existed once, never completed an authorization, and was removed as
dead code. Both reasons were about where the CSRF state lived, and each has a
test here that fails against the old design:

**The state was never stored.** It was written as
``org.config["oauth_states"][hash] = {...}`` — an in-place mutation of a plain
``JSON`` column, which SQLAlchemy does not mark dirty, so the flush emitted
nothing and every callback then failed state validation. The test that catches
that reads the state back **in a different session**; an assertion inside the
writing session passes on a write that was never made, which is exactly why
nothing caught it.

**The callback demanded a bearer token.** It carried ``Depends(require_owner)``,
and the browser reaches it by following Zoho's redirect — a top-level navigation
that sends no ``Authorization`` header. It answered 401 before its handler ran.
The test that catches that makes the request with no credentials at all and
asserts a redirect.

The rest pin the properties that let the callback be public: the state is
single-use, it expires, and what the authorization produced can only be picked
up by an authenticated owner in the organization the state named.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import clock, crypto, oauth
from app.config import settings
from app.db import get_session
from app.domain import models

ORG = "org_oauth"
OTHER = "org_other"


@pytest.fixture()
def sessions(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                        future=True)


@pytest.fixture()
def orgs(sessions):
    with sessions() as s:
        s.add(models.Organization(organization_id=ORG, name="Ours", config={}))
        s.add(models.Organization(organization_id=OTHER, name="Theirs", config={}))
        s.commit()


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(settings, "ZOHO_OAUTH_CLIENT_ID", "cid.zoho")
    monkeypatch.setattr(settings, "ZOHO_OAUTH_CLIENT_SECRET", "shh")
    monkeypatch.setattr(settings, "ZOHO_OAUTH_REDIRECT_URI",
                        "https://pie.example.com/api/v1/connections/zoho/callback")
    monkeypatch.setattr(settings, "FRONTEND_ORIGIN", "https://app.example.com")


@pytest.fixture()
def client(engine, sessions, orgs):
    """The connections router alone, with no authentication configured.

    Deliberately no auth override: every test here that touches the callback is
    asserting what happens to a request that carries *nothing*, which is the
    condition a browser following a redirect arrives in.
    """
    from app.routers import connections

    app = FastAPI()
    app.include_router(connections.router)

    def _override():
        s = sessions()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app, follow_redirects=False)


# ── defect 1: the state has to survive the request that issued it ────────────
def test_an_issued_state_is_readable_in_another_session(sessions, orgs):
    """Read back in a *different* session, which is the whole point.

    The original stored this in `Organization.config` by mutating the dict in
    place. Every assertion made inside the writing session passed; the row was
    never updated, and the callback found nothing.
    """
    with sessions() as s:
        token, row = oauth.issue_state(
            s, organization_id=ORG, accounts_base="https://accounts.zoho.in",
            api_base="https://www.zohoapis.in/books/v3")
        s.commit()
        state_hash = row.state_hash

    with sessions() as s:
        found = s.get(models.OAuthState, state_hash)
        assert found is not None, "the state did not survive the commit"
        assert found.organization_id == ORG
        assert found.consumed_at is None
    # And the token itself is not what was stored: a live state token in a
    # readable table is a usable half of an authorization.
    assert token != state_hash
    assert token not in state_hash


def test_a_state_is_spendable_once(sessions, orgs):
    with sessions() as s:
        token, _ = oauth.issue_state(
            s, organization_id=ORG, accounts_base="a", api_base="b")
        s.commit()

    with sessions() as s:
        assert oauth.consume_state(s, token).organization_id == ORG
        s.commit()

    with sessions() as s:
        with pytest.raises(oauth.StateInvalid):
            oauth.consume_state(s, token)


def test_an_expired_state_is_refused(sessions, orgs):
    with sessions() as s:
        token, row = oauth.issue_state(
            s, organization_id=ORG, accounts_base="a", api_base="b")
        row.expires_at = clock.now() - timedelta(seconds=1)
        s.commit()

    with sessions() as s:
        with pytest.raises(oauth.StateInvalid):
            oauth.consume_state(s, token)


def test_a_state_nobody_issued_is_refused(sessions, orgs):
    with sessions() as s:
        with pytest.raises(oauth.StateInvalid):
            oauth.consume_state(s, "not-a-token-we-minted")


def test_the_sweep_clears_states_long_past_use(sessions, orgs):
    with sessions() as s:
        _, old = oauth.issue_state(s, organization_id=ORG, accounts_base="a",
                                   api_base="b")
        old.expires_at = clock.now() - timedelta(days=3)
        oauth.issue_state(s, organization_id=ORG, accounts_base="a", api_base="b")
        s.commit()

    with sessions() as s:
        assert oauth.sweep_expired(s) == 1
        s.commit()

    with sessions() as s:
        assert s.query(models.OAuthState).count() == 1


# ── defect 2: the callback is reached by a browser, not by a client ──────────
def test_the_callback_needs_no_bearer_token(client, sessions, configured,
                                            monkeypatch):
    """The defect, stated as an assertion.

    `require_owner` on this endpoint meant a browser following Zoho's redirect
    got 401 before the handler ran. Nothing about the flow could work, and no
    test existed to say so.
    """
    async def _exchange(code, accounts_base):
        return oauth.OAuthTokens(access_token="at", refresh_token="rt",
                                 expires_in=3600)

    monkeypatch.setattr(oauth, "exchange_code_for_tokens", _exchange)

    with sessions() as s:
        token, _ = oauth.issue_state(
            s, organization_id=ORG, accounts_base="https://accounts.zoho.in",
            api_base="https://www.zohoapis.in/books/v3")
        s.commit()

    r = client.get("/api/v1/connections/zoho/callback",
                   params={"code": "abc", "state": token})

    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith("https://app.example.com/#/data?")
    assert "oauth=ok" in r.headers["location"]
    assert "handoff=" in r.headers["location"]


def test_the_callback_answers_a_refusal_with_a_screen_not_a_body(client, orgs,
                                                                 configured):
    """Zoho reports a declined consent by redirecting here with `error`. A
    person is following a link, so the only useful outcome is a screen — the
    previous implementation raised a 403 with a JSON body into a popup."""
    r = client.get("/api/v1/connections/zoho/callback",
                   params={"error": "access_denied",
                           "error_description": "User declined"})
    assert r.status_code == 303
    assert "oauth=error" in r.headers["location"]
    assert "User+declined" in r.headers["location"]


def test_a_replayed_callback_does_not_run_a_second_exchange(client, sessions,
                                                            configured,
                                                            monkeypatch):
    calls: list[str] = []

    async def _exchange(code, accounts_base):
        calls.append(code)
        return oauth.OAuthTokens(access_token="at", refresh_token="rt",
                                 expires_in=3600)

    monkeypatch.setattr(oauth, "exchange_code_for_tokens", _exchange)

    with sessions() as s:
        token, _ = oauth.issue_state(s, organization_id=ORG,
                                     accounts_base="a", api_base="b")
        s.commit()

    first = client.get("/api/v1/connections/zoho/callback",
                       params={"code": "abc", "state": token})
    second = client.get("/api/v1/connections/zoho/callback",
                        params={"code": "abc", "state": token})

    assert "oauth=ok" in first.headers["location"]
    assert "oauth=error" in second.headers["location"]
    assert calls == ["abc"], "the exchange ran twice for one authorization"


# ── what the public half may not hand out ───────────────────────────────────
def test_a_handoff_cannot_be_claimed_by_another_organization(sessions, orgs):
    """The control the public callback leans on. The redirect can prove an
    authorization finished; only a session inside the right tenant can pick up
    what it produced."""
    with sessions() as s:
        _, row = oauth.issue_state(s, organization_id=ORG, accounts_base="a",
                                   api_base="b")
        handoff = oauth.issue_handoff(s, row, "cred_1")
        s.commit()

    with sessions() as s:
        assert oauth.claim_handoff(
            s, organization_id=ORG, token=handoff).credential_id == "cred_1"
        with pytest.raises(oauth.StateInvalid):
            oauth.claim_handoff(s, organization_id=OTHER, token=handoff)


# ── the credential the exchange produces ────────────────────────────────────
def test_authorizing_does_not_overwrite_a_hand_entered_credential(sessions, orgs,
                                                                  configured):
    """The removed implementation took *any* credential the organization had and
    replaced its refresh token. A business that had typed in a Self Client token
    and then tried the authorize button lost the working connection, silently."""
    with sessions() as s:
        manual = models.ZohoCredential(
            owner_organization_id=ORG, label="Typed in by hand",
            client_id="a-different-client",
            client_secret_encrypted=crypto.encrypt("s"),
            refresh_token_encrypted=crypto.encrypt("the-good-one"),
            accounts_base="https://accounts.zoho.in",
            api_base="https://www.zohoapis.in/books/v3")
        s.add(manual)
        s.commit()
        manual_id = manual.credential_id

    with sessions() as s:
        cred = oauth.credential_from_oauth(
            s, organization_id=ORG,
            tokens=oauth.OAuthTokens("at", "rt-from-oauth", 3600),
            accounts_base="https://accounts.zoho.in",
            api_base="https://www.zohoapis.in/books/v3")
        s.commit()
        assert cred.credential_id != manual_id

    with sessions() as s:
        untouched = s.get(models.ZohoCredential, manual_id)
        assert crypto.decrypt(untouched.refresh_token_encrypted) == "the-good-one"


def test_authorizing_again_rotates_the_grant_it_issued(sessions, orgs, configured):
    """Its own, though — a second authorization is a rotation, not a second row
    for a future rotation to miss."""
    with sessions() as s:
        first = oauth.credential_from_oauth(
            s, organization_id=ORG, tokens=oauth.OAuthTokens("at", "rt-1", 3600),
            accounts_base="https://accounts.zoho.in", api_base="api")
        s.commit()
        first_id = first.credential_id

    with sessions() as s:
        second = oauth.credential_from_oauth(
            s, organization_id=ORG, tokens=oauth.OAuthTokens("at", "rt-2", 3600),
            accounts_base="https://accounts.zoho.in", api_base="api")
        s.commit()
        assert second.credential_id == first_id
        assert crypto.decrypt(second.refresh_token_encrypted) == "rt-2"


# ── the dead button the first implementation shipped ────────────────────────
def test_an_unconfigured_deployment_says_so_rather_than_offering_the_flow(
        monkeypatch):
    """`configured()` gates both the endpoint and the screen. The first
    implementation needed application credentials no deployment had set and
    offered the button anyway, which is what got it deleted."""
    monkeypatch.setattr(settings, "ZOHO_OAUTH_CLIENT_ID", "")
    assert oauth.configured() is False
    with pytest.raises(oauth.OAuthNotConfigured):
        oauth.authorization_url("t", "https://accounts.zoho.in", scope="x")


def test_the_authorization_url_is_a_zoho_endpoint(configured):
    """`/oauth/authorize` is not one, and is where this pointed.

    The module's own docstring says this flow "never completed an
    authorization end to end" and blames the CSRF state for it. Both halves of
    the conversation with Zoho were also addressed to paths that do not exist:
    a consent screen that 404s, and a token exchange that could not have
    returned a token. `zoho_client._access_token` and docs/zoho-setup.md had
    `/oauth/v2/token` right throughout, which is the tell — one flow spelled
    the same host two ways and only the untested spelling was wrong.
    """
    url = oauth.authorization_url("t", "https://accounts.zoho.in", scope="x")
    assert url.startswith("https://accounts.zoho.in/oauth/v2/auth?")


def test_the_authorization_url_asks_for_a_refresh_token(configured):
    """Zoho issues one only when asked. Without `access_type=offline` the
    exchange returns an access token that dies in an hour, and the connection
    stops working the same afternoon."""
    url = oauth.authorization_url("state-token", "https://accounts.zoho.in",
                                  scope="ZohoBooks.bills.READ")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=state-token" in url
    assert "response_type=code" in url
