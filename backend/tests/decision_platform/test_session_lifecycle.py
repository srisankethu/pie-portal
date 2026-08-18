"""A sign-in is a row now, so it can be ended — this is what checks that.

The old token was the whole of the authority: an HMAC over `{uid, oid, iat}`,
valid for thirty days, revocable only by changing the password. "Sign out" was
`localStorage.removeItem`, which ended nothing on the server, and a token copied
off a shared machine kept working long after the person believed they had left.

Every test here is written against the observable behaviour — make a request,
see whether it is refused — rather than against the internals, because the
internals are exactly what changed and a test that reaches into them would have
to be rewritten by the next change rather than defending against it.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import dbsupport


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.routers import admin, platform_auth
    from app.seed import ensure_org_and_users

    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(admin.router)

    def _session():
        db = Maker()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_session] = _session
    c = TestClient(app)
    c.maker = Maker          # a few tests reach past HTTP to age a row
    return c


EMAIL = "s.menon@pie.example"


def _login(client, email: str = EMAIL) -> str:
    from app.seed import SEED_PASSWORD

    r = client.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── the thing that did not work before ───────────────────────────────────────
def test_a_token_stops_working_after_its_session_is_signed_out(client):
    token = _login(client)
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200

    assert client.post("/api/v1/auth/logout", headers=_bearer(token)).status_code == 200

    # The same token, unchanged and still correctly signed, is now refused —
    # which is what "signed out" has to mean for it to be worth clicking.
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401


def test_signing_out_one_session_leaves_the_others_alone(client):
    """Two devices, and leaving one is not leaving both."""
    laptop = _login(client)
    phone = _login(client)

    client.post("/api/v1/auth/logout", headers=_bearer(laptop))

    assert client.get("/api/v1/auth/me", headers=_bearer(laptop)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(phone)).status_code == 200


def test_logout_all_ends_every_session_including_this_one(client):
    """The "someone else has my session" button, which must not leave one behind."""
    a, b, c = _login(client), _login(client), _login(client)

    r = client.post("/api/v1/auth/logout-all", headers=_bearer(b))
    assert r.status_code == 200, r.text
    assert r.json()["ended"] == 3          # its own included, deliberately

    for token in (a, b, c):
        assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401


def test_a_named_session_can_be_revoked_from_another_device(client):
    """"Sign out that other laptop", from the one you still have."""
    laptop = _login(client)
    phone = _login(client)

    listed = client.get("/api/v1/auth/sessions", headers=_bearer(phone))
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert len(rows) == 2
    # Exactly one row is the session doing the asking, and it is not the one
    # about to be revoked.
    assert sum(1 for r in rows if r["current"]) == 1
    other = next(r for r in rows if not r["current"])

    assert client.delete(f"/api/v1/auth/sessions/{other['session_id']}",
                         headers=_bearer(phone)).status_code == 200

    assert client.get("/api/v1/auth/me", headers=_bearer(laptop)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(phone)).status_code == 200


def test_one_person_cannot_revoke_another_persons_session(client):
    """And is told nothing about whether the id was real."""
    mine = _login(client)
    theirs = _login(client, "m.rao@pie.example")

    sid = next(r["session_id"] for r in
               client.get("/api/v1/auth/sessions", headers=_bearer(theirs)).json())

    r = client.delete(f"/api/v1/auth/sessions/{sid}", headers=_bearer(mine))
    # 404, not 403: a different answer for "exists but is not yours" would make
    # this endpoint a way to ask whether a session id is live.
    assert r.status_code == 404

    assert client.get("/api/v1/auth/me", headers=_bearer(theirs)).status_code == 200


def test_the_session_list_only_shows_your_own(client):
    mine = _login(client)
    _login(client, "m.rao@pie.example")

    rows = client.get("/api/v1/auth/sessions", headers=_bearer(mine)).json()
    assert len(rows) == 1


# ── expiry ───────────────────────────────────────────────────────────────────
def _age_session(client, *, last_seen: timedelta | None = None,
                 issued: timedelta | None = None) -> None:
    """Push the one live session's clocks back, as time would."""
    from app import clock
    from app.domain import models

    db = client.maker()
    row = db.query(models.UserSession).filter(models.UserSession.revoked_at.is_(None)).one()
    if last_seen is not None:
        row.last_seen_at = clock.now() - last_seen
    if issued is not None:
        row.issued_at = clock.now() - issued
    db.commit()
    db.close()


def test_a_session_nobody_has_used_expires(client):
    """The idle timeout. A browser left open on a shared desk is not a way in
    the next morning."""
    token = _login(client)
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200

    _age_session(client, last_seen=timedelta(hours=13))    # past the 12h default

    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401


def test_using_a_session_keeps_it_alive(client):
    """Idle means idle — the timeout must not fire on a session in active use."""
    token = _login(client)

    _age_session(client, last_seen=timedelta(hours=11))     # inside the window
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200

    # That request advanced `last_seen_at`, so another eleven hours from *now*
    # is still fine. Without the advance this would be 22 hours idle and dead.
    _age_session(client, last_seen=timedelta(hours=11))
    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200


def test_a_session_in_constant_use_still_dies_at_the_absolute_limit(client):
    """The other half: a captured token kept warm must still expire on a known
    date, however busy it is kept."""
    token = _login(client)

    _age_session(client, issued=timedelta(days=31), last_seen=timedelta(seconds=0))

    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401


# ── transport ────────────────────────────────────────────────────────────────
def test_the_cookie_alone_authenticates_a_read(client):
    """The browser's path: no Authorization header anywhere."""
    from app.authz import SESSION_COOKIE

    r = client.post("/api/v1/auth/login",
                    json={"email": EMAIL, "password": __import__("app.seed", fromlist=["x"]).SEED_PASSWORD})
    assert r.status_code == 200
    assert SESSION_COOKIE in r.cookies

    # TestClient keeps the cookie jar, so this request carries only the cookie.
    assert client.get("/api/v1/auth/me").status_code == 200


def test_the_login_cookie_is_not_readable_by_script(client):
    """httpOnly is the reason the token moved out of localStorage at all."""
    from app.authz import SESSION_COOKIE

    r = client.post("/api/v1/auth/login",
                    json={"email": EMAIL, "password": __import__("app.seed", fromlist=["x"]).SEED_PASSWORD})
    setc = r.headers["set-cookie"]
    assert SESSION_COOKIE in setc
    assert "httponly" in setc.lower()
    assert "samesite=lax" in setc.lower().replace(" ", "")


def test_a_cookie_authenticated_write_without_the_app_header_is_refused(client):
    """The CSRF control. A cross-site form POST cannot set a header, so this is
    what stops one being honoured."""
    from app.authz import APP_HEADER

    _login(client)     # cookie is in the jar

    # httpx sends no custom headers unless asked; this is the cross-site shape.
    r = client.post("/api/v1/auth/logout")
    assert r.status_code == 403
    assert APP_HEADER in r.json()["detail"]

    # The app's own request, which always carries it, is fine.
    assert client.post("/api/v1/auth/logout", headers={APP_HEADER: "1"}).status_code == 200


def test_a_cookie_authenticated_read_needs_no_app_header(client):
    """A GET changes nothing, so the guard would only be noise on it."""
    _login(client)
    assert client.get("/api/v1/auth/me").status_code == 200


def test_a_bearer_write_needs_no_app_header(client):
    """A token sent deliberately is not ambient authority, so CSRF does not
    apply to it — and requiring the header would break every API client."""
    token = _login(client)
    client.cookies.clear()          # bearer only, no cookie in play
    assert client.post("/api/v1/auth/logout", headers=_bearer(token)).status_code == 200


# ── credential changes ───────────────────────────────────────────────────────
def test_changing_the_password_ends_every_other_session(client):
    """What you press when you think somebody else is signed in as you."""
    from app.seed import SEED_PASSWORD

    laptop = _login(client)
    phone = _login(client)

    r = client.post("/api/v1/admin/me/password",
                    headers=_bearer(phone),
                    json={"current_password": SEED_PASSWORD,
                          "new_password": "a-longer-replacement-passphrase-9"})
    assert r.status_code == 200, r.text

    # The other device is out.
    assert client.get("/api/v1/auth/me", headers=_bearer(laptop)).status_code == 401
    # The token that arrived with the request is out too — but the change handed
    # back a fresh one, so the person who just changed it is not signed out by
    # their own action.
    assert client.get("/api/v1/auth/me", headers=_bearer(phone)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=_bearer(r.json()["token"])).status_code == 200


def test_a_token_naming_no_session_is_refused(client):
    """Covers the tokens minted before sessions were rows: correctly signed,
    and no longer sufficient."""
    from app.authz import issue_token

    forged = issue_token("usr_whoever", "org_pie", "sess_does_not_exist")
    assert client.get("/api/v1/auth/me", headers=_bearer(forged)).status_code == 401


def test_a_token_with_no_session_id_at_all_is_refused(client):
    """The pre-change token shape, signed with the real secret."""
    import base64
    import json
    import time

    from app.authz import _sign

    body = {"uid": "usr_menon", "oid": "org_pie", "iat": time.time()}
    raw = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    old_style = f"{raw}.{_sign(raw.encode())}"

    assert client.get("/api/v1/auth/me", headers=_bearer(old_style)).status_code == 401
