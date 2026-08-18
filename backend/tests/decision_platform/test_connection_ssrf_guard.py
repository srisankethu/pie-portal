"""A source URL may not be aimed back inside our own network.

Every ERP connection an owner adds becomes a server-side fetch target when the
connection is checked or synced. Without a guard, ``api_base`` /
``accounts_base`` (Zoho, manual path) and a connector's ``base_url`` are an SSRF
primitive: point one at the cloud metadata endpoint or a loopback service, press
*Check*, and the fetched body is reflected in the check result. These pin the
guard in ``ingestion/url_safety`` and its wiring at every connect seam.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.ingestion import connections as conn
from app.ingestion.url_safety import UnsafeSourceUrl, require_safe_source_url
from app.routers import connections as connections_router, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
OWNER = "s.menon@pie.example"

# Literal targets an SSRF actually aims at; none of these touch the network to
# decide, so the test is fast and deterministic.
_BLOCKED = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://127.0.0.1:8000/books/v3",
    "http://[::1]/books/v3",
    "http://localhost/books/v3",
    "http://10.1.2.3/x",
    "http://192.168.0.1/x",
    "http://[::ffff:169.254.169.254]/x",
    "file:///etc/passwd",
    "gopher://127.0.0.1:11211/x",
]
_ALLOWED = [
    "https://www.zohoapis.in/books/v3",
    "https://p21.example",                       # reserved TLD: never resolves
    "https://1234567-sb1.suitetalk.api.netsuite.com",
]


@pytest.mark.parametrize("url", _BLOCKED)
def test_internal_targets_are_refused(url):
    with pytest.raises(UnsafeSourceUrl):
        require_safe_source_url(url, field="API URL")


@pytest.mark.parametrize("url", _ALLOWED)
def test_public_targets_are_allowed(url):
    require_safe_source_url(url, field="API URL")   # must not raise


@pytest.fixture()
def db():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    try:
        yield s
    finally:
        s.close()


def test_set_zoho_credentials_refuses_an_internal_api_base(db):
    with pytest.raises(UnsafeSourceUrl):
        conn.set_zoho_credentials(
            db, ORG, zoho_organization_id="42", client_id="cid",
            client_secret="secret", refresh_token="rtok",
            api_base="http://169.254.169.254/latest/meta-data")


def test_connect_erp_refuses_an_internal_base_url(db):
    with pytest.raises(UnsafeSourceUrl):
        conn.connect_erp(db, ORG, connector="prophet21", values={
            "base_url": "http://127.0.0.1:8080", "username": "api",
            "password": "p", "company_id": "PIE"})


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
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_api_refuses_a_zoho_connection_aimed_at_the_metadata_endpoint(client):
    r = client.post("/api/v1/connections", headers=_hdr(client, OWNER), json={
        "zoho_organization_id": "42", "client_id": "cid",
        "client_secret": "secret", "refresh_token": "rtok",
        "api_base": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400, r.text
    assert "internal" in r.json()["detail"].lower()


def test_api_refuses_an_erp_connection_aimed_at_loopback(client):
    r = client.post("/api/v1/connections/erp", headers=_hdr(client, OWNER), json={
        "connector": "prophet21",
        "values": {"base_url": "http://127.0.0.1:8080", "username": "api",
                   "password": "p", "company_id": "PIE"}})
    assert r.status_code == 400, r.text

