"""The check endpoint, and what happens when the database is behind the code.

Both exist because of one bug reported from a real deployment: pressing
"Check" on a connection returned a bare ``500 Internal Server Error`` with a
plain-text body. The endpoint's own error handling was fine — it was the row
serialiser underneath, reading a column a pending migration had not added yet.

Two properties are locked here:

**A check reports, it does not crash.** Whatever Zoho or the network does, the
answer is a result with a readable reason. An endpoint whose job is to
diagnose a connection is worthless if it can itself fail opaquely.

**A database that is behind says so.** It used to fail selectively — the query
only ran once a connection row existed, so an organization with none looked
healthy while an organization with one could not open the screen. A fault that
appears to depend on the data is a fault nobody diagnoses quickly.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import Base, get_session
from app.ingestion.zoho_client import ZohoAuthError
from app.routers import connections as cr, data_status, platform_auth
from app.schema_check import describe, missing_columns
from app.seed import SEED_PASSWORD, ensure_org_and_users

OWNER = "s.menon@pie.example"


def _app(engine):
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    for r in (platform_auth.router, cr.router, data_status.router):
        app.include_router(r)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    # raise_server_exceptions=False so an unhandled crash arrives as a real 500
    # response, the way the browser saw it, instead of blowing up the test.
    tc = TestClient(app, raise_server_exceptions=False)
    tc.Maker = Maker
    return tc


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    return _app(engine)


@pytest.fixture()
def stale_client():
    """A database from before sync_runs gained connection_id.

    Deliberately NOT dbsupport.fresh_engine(): this fixture damages the schema,
    and in Postgres mode that engine is a shared per-worker database — DDL run
    against it would outlive this test. The staleness being simulated is
    backend-neutral (the inspector-based schema check is what's under test), so
    a private in-memory SQLite database is the right tool on every backend.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("DROP INDEX ix_sync_runs_connection_id"))
        # Both halves of the start guard name connection_id — the umbrella one
        # in its predicate rather than its columns — and SQLite refuses to drop
        # a column an index still references. A database from before the column
        # existed had neither index either, so dropping them is part of the
        # staleness being simulated, not a workaround.
        c.execute(text("DROP INDEX uq_sync_run_active_connection"))
        c.execute(text("DROP INDEX uq_sync_run_active_umbrella"))
        c.execute(text("ALTER TABLE sync_runs DROP COLUMN connection_id"))
    return _app(engine)


def _hdr(c):
    r = c.post("/api/v1/auth/login", json={"email": OWNER, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _add(c, zoho_org="60036630626", label="4U Precision"):
    return c.post("/api/v1/connections", headers=_hdr(c), json={
        "zoho_organization_id": zoho_org, "label": label,
        "client_id": "1000.APP", "client_secret": "s", "refresh_token": "r"})


# ── the check reports; it never crashes ─────────────────────────────────────
def test_a_check_against_a_non_api_source_says_so_rather_than_pretending(client):
    r = _add(client)
    assert r.status_code == 201, r.text
    cid = r.json()["connection_id"]

    got = client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client))
    assert got.status_code == 200, got.text
    assert got.json()["checked"] is False
    assert "nothing was contacted" in got.json()["detail"]


@pytest.mark.parametrize("boom", [
    ZohoAuthError("invalid_client"),
    ConnectionResetError("connection reset by peer"),
    ValueError("something nobody anticipated"),
    RuntimeError("x" * 4000),          # longer than the column it is stored in
])
def test_a_check_survives_whatever_zoho_does(client, monkeypatch, boom):
    """The endpoint whose job is to diagnose a connection must not be the thing
    that fails opaquely. A 500 here reads as 'the platform is broken' when the
    truth is 'your token expired'."""
    cid = _add(client).json()["connection_id"]
    monkeypatch.setattr(cr.settings, "ZOHO_SOURCE", "api")
    monkeypatch.setattr(cr.ZohoApiSource, "ping",
                        lambda self: (_ for _ in ()).throw(boom))

    got = client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["ok"] is False
    assert body["detail"], "a failed check has to say why"


def _reached(monkeypatch, scopes=None):
    """A connection whose login reaches the company, with a canned grant probe.

    ``probe_scopes`` is stubbed in every test that gets past ``ping`` — left
    real it would open an httpx client against accounts.zoho.in and sit there
    until the timeout, which is a test suite that fails on a train.
    """
    monkeypatch.setattr(cr.settings, "ZOHO_SOURCE", "api")
    monkeypatch.setattr(cr.ZohoApiSource, "ping", lambda self: {
        "organization_found": True, "organization_name": "4U PRECISION",
        "currency": "INR",
        "visible_organizations": [
            {"organization_id": "60036630626", "name": "4U PRECISION"},
            {"organization_id": "60036630999", "name": "SLS ENGINEERS"},
        ]})
    granted = [{"scope": p.name, "endpoint": p.name, "granted": True,
                "detail": None}
               for p in cr.conn.REQUIRED_SCOPES]
    monkeypatch.setattr(cr.ZohoApiSource, "probe_scopes",
                        lambda self: scopes if scopes is not None else granted)


def test_a_successful_check_reports_every_company_the_grant_reaches(client, monkeypatch):
    cid = _add(client).json()["connection_id"]
    _reached(monkeypatch)

    body = client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client)).json()
    assert body["ok"] is True
    assert len(body["visible_organizations"]) == 2, (
        "the answer to 'do I need another credential for the next entity?'")


# ── the check tests the grant, not just the login ───────────────────────────
#
# `ping` reads `organizations`, which sits behind no scope at all. A connection
# granted the login and nothing else therefore passed this check and then failed
# every sync — with an error naming a credential that was perfectly fine.
def test_a_refused_required_scope_fails_the_check_and_is_named(client, monkeypatch):
    cid = _add(client).json()["connection_id"]
    _reached(monkeypatch, scopes=[
        {"scope": "ZohoBooks.contacts.READ", "endpoint": "contacts",
         "granted": True, "detail": None},
        {"scope": "ZohoBooks.bills.READ", "endpoint": "bills",
         "granted": False, "detail": "not granted"},
    ])

    body = client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client)).json()
    assert body["ok"] is False, "a grant that cannot run a sync is not a pass"
    assert body["missing_required_scopes"] == ["ZohoBooks.bills.READ"]
    assert "ZohoBooks.bills.READ" in body["detail"]
    # Still reached — the detail must not send somebody to the credential.
    assert "Reached this company." in body["detail"]


def test_a_refused_optional_scope_is_reported_without_failing_the_check(client, monkeypatch):
    """Credit notes are the case this was found on. Losing them costs the
    ability to reconstruct a past position and nothing else, so the connection
    is still fit to sync — but the gap has to be visible, because for as long
    as the scope went undeclared the only trace was one line in a sync report."""
    cid = _add(client).json()["connection_id"]
    _reached(monkeypatch, scopes=[
        {"scope": "ZohoBooks.creditnotes.READ", "endpoint": "creditnotes",
         "granted": False, "detail": "not granted"},
    ])

    body = client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client)).json()
    assert body["ok"] is True
    assert body["missing_required_scopes"] == []
    assert "ZohoBooks.creditnotes.READ" in body["detail"]


def test_a_scope_the_probe_could_not_test_is_never_reported_as_granted(client, monkeypatch):
    """Absence of evidence is not a pass (§1). An untested scope is disclosed as
    untested; what it must never do is disappear into a green check."""
    cid = _add(client).json()["connection_id"]
    _reached(monkeypatch, scopes=[
        {"scope": "ZohoBooks.bills.READ", "endpoint": "bills",
         "granted": None, "detail": "ZohoError: HTTP 503"},
    ])

    body = client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client)).json()
    assert body["untested_scopes"] == ["ZohoBooks.bills.READ"]
    assert body["missing_required_scopes"] == [], "unknown is not the same as refused"
    assert "could not test" in body["detail"]


def test_a_probe_that_blows_up_leaves_the_connection_reachable(client, monkeypatch):
    """The ping succeeded, so the company *was* reached. Reporting "not
    reachable" because the follow-up probe fell over points at the one thing
    that is demonstrably fine."""
    cid = _add(client).json()["connection_id"]
    _reached(monkeypatch)
    monkeypatch.setattr(cr.ZohoApiSource, "probe_scopes",
                        lambda self: (_ for _ in ()).throw(RuntimeError("socket died")))

    body = client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client)).json()
    assert body["ok"] is True
    assert "could not be tested" in body["detail"]
    assert body["untested_scopes"], "every scope is unanswered, and says so"


def test_a_check_records_its_result_so_a_stale_connection_is_visible(client, monkeypatch):
    cid = _add(client).json()["connection_id"]
    monkeypatch.setattr(cr.settings, "ZOHO_SOURCE", "api")
    monkeypatch.setattr(cr.ZohoApiSource, "ping",
                        lambda self: (_ for _ in ()).throw(ZohoAuthError("revoked")))
    client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client))

    listed = client.get("/api/v1/connections", headers=_hdr(client)).json()["connections"][0]
    assert listed["last_check_ok"] is False
    assert listed["last_checked_at"], "never-checked and failed must look different"


# ── a database that is behind ───────────────────────────────────────────────
def test_the_gap_is_detected_and_described_with_the_command_that_fixes_it(stale_client):
    engine = stale_client.Maker.kw["bind"]
    gaps = missing_columns(engine)
    assert gaps == {"sync_runs": ["connection_id"]}
    message = describe(gaps)
    assert "alembic upgrade head" in message
    assert "No data is lost" in message


def test_a_healthy_database_reports_no_gap(client):
    assert missing_columns(client.Maker.kw["bind"]) == {}
    assert describe({}) is None


def test_the_check_actually_examines_the_models(client):
    """It reads Base.metadata, which is populated as a side effect of importing
    the model modules. Without that import it compared the database against an
    empty table list and reported every schema healthy — which it did, on a
    database I had just broken by hand."""
    from app.db import Base

    assert len(Base.metadata.sorted_tables) > 10
    names = {t.name for t in Base.metadata.sorted_tables}
    assert {"sync_runs", "zoho_connections", "commercial_policies"} <= names


def test_adding_a_connection_against_a_stale_database_explains_itself(stale_client):
    """This is the reported bug: a bare 500 with a plain-text body, which names
    neither the cause nor the fix."""
    r = _add(stale_client)
    assert r.status_code == 503, f"got {r.status_code}: {r.text[:200]}"
    assert "alembic upgrade head" in r.json()["detail"]


def test_the_stale_message_names_the_column_that_is_actually_missing(stale_client):
    """It used to match the error text for "connection_id", which appears in the
    SELECT list of every such failure — so a database missing a different column
    was told to fix one it already had. A diagnostic that names the wrong thing
    sends someone to check a schema that is fine."""
    engine = stale_client.Maker.kw["bind"]
    with engine.begin() as c:
        c.execute(text("ALTER TABLE sync_runs ADD COLUMN connection_id VARCHAR(64)"))
        c.execute(text("ALTER TABLE sync_runs DROP COLUMN notes"))

    r = _add(stale_client)
    assert r.status_code == 503, f"got {r.status_code}: {r.text[:200]}"
    detail = r.json()["detail"]
    assert "notes" in detail, detail
    assert "connection_id" not in detail, "that column is present — do not blame it"


def test_syncing_against_a_stale_database_explains_itself(stale_client):
    r = stale_client.post("/api/v1/data/sync", headers=_hdr(stale_client), json={})
    assert r.status_code == 503, f"got {r.status_code}: {r.text[:200]}"
    assert "alembic upgrade head" in r.json()["detail"]


def test_the_stale_failure_is_not_hidden_by_an_empty_connection_list(stale_client):
    """The original fault only appeared once a connection existed — with none,
    the failing query never ran and the screen looked perfectly healthy. That
    is why it was reported as 'sync is broken' rather than 'we forgot to
    migrate'."""
    r = stale_client.get("/api/v1/connections", headers=_hdr(stale_client))
    assert r.status_code == 200
    assert r.json()["connections"] == []
    # ...and the sync endpoint, which does not depend on any row existing, is
    # where the truth is told instead.
    assert stale_client.post("/api/v1/data/sync", headers=_hdr(stale_client),
                             json={}).status_code == 503


# ── rotating the token, on the connection ───────────────────────────────────
#
# Rotation used to be a "credentials" surface of its own, which made a Zoho
# mechanism — a refresh token that can be revoked and reissued — look like a
# concept every connector would need, and put the control on a different screen
# from the connection somebody had just decided to rotate.

def _cred_token(client, cid: str) -> str:
    from app.crypto import decrypt
    from app.domain import models as m

    s = client.Maker()
    try:
        row = s.get(m.ZohoConnection, cid)
        cred = s.get(m.ZohoCredential, row.credential_id)
        return decrypt(cred.refresh_token_encrypted)
    finally:
        s.close()


def _cred_secret(client, cid: str) -> tuple[str, str]:
    """The client pair a connection signs in with, as (id, secret)."""
    from app.crypto import decrypt
    from app.domain import models as m

    s = client.Maker()
    try:
        row = s.get(m.ZohoConnection, cid)
        cred = s.get(m.ZohoCredential, row.credential_id)
        return cred.client_id, decrypt(cred.client_secret_encrypted)
    finally:
        s.close()


def test_rotating_a_connection_replaces_the_token_it_signs_in_with(client):
    cid = _add(client).json()["connection_id"]
    assert _cred_token(client, cid) == "r"

    got = client.post(f"/api/v1/connections/{cid}/rotate", headers=_hdr(client),
                      json={"refresh_token": "1000.new.token"})
    assert got.status_code == 200, got.text
    assert got.json()["rotated"] is True
    assert _cred_token(client, cid) == "1000.new.token"


def test_a_rotation_names_every_other_company_it_changed(client):
    """One Zoho grant usually reaches every company its user can see, so
    rotating from one connection rotates the others. That is the point of
    sharing a grant — and a nasty surprise if the response does not say so."""
    first = _add(client, "60036630626", "4U Precision").json()
    credential_id = first["credential_id"]
    second = client.post("/api/v1/connections", headers=_hdr(client), json={
        "zoho_organization_id": "60036630999", "label": "SLS Engineers",
        "credential_id": credential_id}).json()

    body = client.post(f"/api/v1/connections/{first['connection_id']}/rotate",
                       headers=_hdr(client),
                       json={"refresh_token": "shared-new"}).json()
    also = body["also_rotated"]
    assert [c["connection_id"] for c in also] == [second["connection_id"]]
    assert "1 other company" in body["note"]
    # And it really did change underneath the other one.
    assert _cred_token(client, second["connection_id"]) == "shared-new"


def test_a_rotation_can_carry_the_client_pair_the_new_token_belongs_to(client):
    """The failure a token-only rotation *causes*, and its remedy.

    A refresh token generated under a different Zoho app does not authenticate
    against the app already on file: Zoho answers ``invalid_client_secret``,
    which reads as "your secret is wrong" and sends an owner to change the data
    centre — the one setting that was right. So the client pair travels with
    the token when it changed, and only when it changed.
    """
    cid = _add(client).json()["connection_id"]

    got = client.post(f"/api/v1/connections/{cid}/rotate", headers=_hdr(client),
                      json={"refresh_token": "1000.new.token",
                            "client_id": "1000.NEWAPP", "client_secret": "new-secret"})
    assert got.status_code == 200, got.text
    assert _cred_secret(client, cid) == ("1000.NEWAPP", "new-secret")
    assert _cred_token(client, cid) == "1000.new.token"


def test_a_rotation_without_a_client_pair_leaves_the_one_on_file_alone(client):
    """The default, and why it is the default: re-typing a secret that is
    already correct is how a working connection gets broken, so an omitted pair
    means "unchanged" rather than "clear it"."""
    cid = _add(client).json()["connection_id"]
    before = _cred_secret(client, cid)

    client.post(f"/api/v1/connections/{cid}/rotate", headers=_hdr(client),
                json={"refresh_token": "1000.new.token"})
    assert _cred_secret(client, cid) == before


def test_an_empty_token_is_refused_rather_than_stored(client):
    """A blank rotation would leave the connection unable to sign in at all,
    which is a worse outcome than the expired token it replaced."""
    cid = _add(client).json()["connection_id"]
    got = client.post(f"/api/v1/connections/{cid}/rotate", headers=_hdr(client),
                      json={"refresh_token": "   "})
    assert got.status_code == 400
    assert _cred_token(client, cid) == "r"


def test_rotation_no_longer_has_a_second_home(client):
    """One way to do it. The old per-credential route is gone, so there is no
    second path that could drift from this one or be found first."""
    cid = _add(client).json()["connection_id"]
    s = client.Maker()
    from app.domain import models as m
    credential_id = s.get(m.ZohoConnection, cid).credential_id
    s.close()

    gone = client.post(f"/api/v1/data/credentials/{credential_id}/rotate",
                       headers=_hdr(client), json={"refresh_token": "x"})
    assert gone.status_code == 404


# ── the check records the country the statutory screens gate on ─────────────
def _reached_with_country(monkeypatch, country):
    _reached(monkeypatch)
    monkeypatch.setattr(cr.ZohoApiSource, "ping", lambda self: {
        "organization_found": True, "organization_name": "4U PRECISION",
        "currency": "INR", "time_zone": "Asia/Kolkata", "country": country,
        "visible_organizations": [
            {"organization_id": "60036630626", "name": "4U PRECISION"}]})


def _org_country(client):
    from app.config import settings
    from app.domain import models
    s = client.Maker()
    try:
        return s.get(models.Organization, settings.DEFAULT_ORG_ID).country
    finally:
        s.close()


def _set_org_country(client, value):
    from app.config import settings
    from app.domain import models
    s = client.Maker()
    s.get(models.Organization, settings.DEFAULT_ORG_ID).country = value
    s.commit()
    s.close()


def test_a_check_fills_an_unset_country_from_zohos_profile(client, monkeypatch):
    """The statutory screens gate on ``Organization.country`` and this fill is
    the product's one writer for it — without it, the gate's refusal names a
    fix nobody can perform anywhere in the product."""
    _set_org_country(client, None)  # a real tenant that predates the column
    cid = _add(client).json()["connection_id"]
    _reached_with_country(monkeypatch, "India")

    assert client.post(f"/api/v1/connections/{cid}/check",
                       headers=_hdr(client)).json()["ok"] is True
    assert _org_country(client) == "IN"


def test_a_recorded_country_is_never_overwritten_by_a_check(client, monkeypatch):
    """Filled in, never overwritten — the same contract as the timezone above
    it: an owner who set it meant it."""
    _set_org_country(client, "AE")
    cid = _add(client).json()["connection_id"]
    _reached_with_country(monkeypatch, "India")

    client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client))
    assert _org_country(client) == "AE"


def test_an_unplaceable_country_label_leaves_the_column_alone(client, monkeypatch):
    """Only an exact placement writes: a mis-placed country would turn the
    jurisdiction gate's refusal into a confidently wrong answer, which is the
    one trade §1 forbids."""
    _set_org_country(client, None)
    cid = _add(client).json()["connection_id"]
    _reached_with_country(monkeypatch, "Republic of Somewhere")

    client.post(f"/api/v1/connections/{cid}/check", headers=_hdr(client))
    assert _org_country(client) is None

