"""Does this organization's own book actually reach the engine, on every path?

`app/sellable_catalog.py` builds the pool and `PieService.resolve` composes it
into the source list, but for one commit neither fact meant anything in
production: **nothing built one.** Only tests passed a pool, and the module said
so in its own docstring rather than leaving it to a grep. This file is what
turns that sentence into a check.

**Why it does not use a real pool.** `SellableCatalogSource.__init__` imports
`CanonicalRecord` from pie-parser, so building one needs the engine — and an
engine-backed test runs in neither `make verify` on a checkout without the
submodule nor a credential-less CI job, which is where a wiring regression would
actually have to be caught. `tests/decision_platform/test_sellable_catalog_pool.py`
is the engine-backed half and asserts what a real pool *contains*. This half
asserts only that the object `sellable_pool_for` returns is the object
`pie_service.resolve` receives, which needs no engine and is the property that
broke.

**Why a recorder rather than the usual stub.** Every other engine stub in this
suite is `lambda *a, **k: <fixed Resolution>` and records nothing, so not one of
them could tell a wiring that passes the pool from a wiring that builds it and
drops it on the floor. A green suite would have read as "correct" for either.
These stubs remember what they were called with, which is the whole point.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import dbsupport
from app import api_keys, resolution
from app.db import get_session
from app.domain import models  # noqa: F401  (populate metadata)
from app.domain.enums import Role
from app.pie_service import Candidate, Resolution
from app.routers import platform_auth, quote as quote_router, resolve as resolve_router
from app.seed import SEED_PASSWORD, ensure_org_and_users

API_ORG = "org_test"

#: The company whose catalogue answers. Nothing in this module reads a real
#: catalogue — the engine is a recorder — but a resolution has to name a
#: company to get past `company_for`, and the pool is asserted per organization
#: rather than per company, so one constant is enough.
COMPANY = "conn_test"
#: The seeded owner, as `test_quote_flow.py` names them.
OWNER = "s.menon@pie.example"


class _Sentinel:
    """Stands in for a built pool. Identity is the whole of its behaviour.

    Not a `SellableCatalogSource`: constructing one imports the engine, and the
    assertion here is `is`, not `==`. A pool that arrived by being rebuilt at
    the callee — the plausible wrong implementation — would be an equal object
    and a different one, and `is` is what tells them apart.
    """

    def __init__(self, organization_id: str) -> None:
        self.organization_id = organization_id

    def __repr__(self) -> str:  # pragma: no cover — diagnostics only
        return f"<pool for {self.organization_id}>"


class _Recorder:
    """A `pie_service.resolve` that answers plainly and remembers the pool."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, text: str, customer_scope: Any = None, bands: Any = None,
                 mapping_store: Any = None, connection_id: Any = None,
                 pool: Any = None) -> Resolution:
        # `connection_id` is recorded as well as accepted. It is the catalogue
        # half of "what could the engine see" and the pool is the book half;
        # a stub that swallowed the first would let a caller drop it silently.
        self.calls.append({"text": text, "pool": pool,
                           "connection_id": connection_id,
                           "customer_scope": customer_scope})
        return Resolution(
            input_text=text, reqCode=text, reqDesc="A product", rel="EXACT",
            supplyCode="2576285",
            candidates=[Candidate(code="2576285", desc="A product", rel="EXACT")],
            outcome="AUTO_MATCH", semantics="IDENTITY")

    @property
    def pools(self) -> List[Any]:
        return [c["pool"] for c in self.calls]

    @property
    def companies(self) -> List[Any]:
        return [c["connection_id"] for c in self.calls]


class _PoolSource:
    """A `sellable_pool_for` that hands out one sentinel per organization.

    It also records the arguments, because "the pool arrived" and "the pool for
    the RIGHT organization arrived" are different claims and only the second one
    is worth having. A wiring that passed a hard-coded org would satisfy the
    first.
    """

    def __init__(self) -> None:
        self.asked: List[str] = []
        self._by_org: Dict[str, _Sentinel] = {}

    def __call__(self, session: Session, organization_id: str) -> Optional[_Sentinel]:
        assert isinstance(session, Session), (
            "the pool is obtained with the request's own session; a caller "
            "reaching for a new one would resolve outside this transaction")
        self.asked.append(organization_id)
        return self._by_org.setdefault(organization_id, _Sentinel(organization_id))

    def of(self, organization_id: str) -> _Sentinel:
        return self._by_org.setdefault(organization_id,
                                       _Sentinel(organization_id))


# ── the public resolution API ────────────────────────────────────────────────

@pytest.fixture()
def api(monkeypatch):
    engine = dbsupport.fresh_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    svc = resolve_router.pie_service
    recorder = _Recorder()
    pools = _PoolSource()

    # A catalogue belongs to a company now, so these three take one. Patched as
    # plain methods rather than properties for that reason, and `company_for`
    # answers with a company so the router gets past the refusal it raises when
    # an organization has several and named none — which is not what is under
    # test here.
    monkeypatch.setattr(type(svc), "catalog_available",
                        lambda self, connection_id=None: True)
    monkeypatch.setattr(type(svc), "catalog_version",
                        lambda self, connection_id=None: "abc123")
    monkeypatch.setattr(svc, "resolve", recorder)
    monkeypatch.setattr(svc, "lookup_record",
                        lambda code, connection_id=None: None)
    monkeypatch.setattr(resolution, "company_for", lambda *a, **k: COMPANY)
    monkeypatch.setattr(resolution, "customer_scope_for", lambda *a, **k: None)
    monkeypatch.setattr(resolution, "bands_for", lambda *a, **k: None)
    monkeypatch.setattr(resolution, "mapping_store_for", lambda *a, **k: None)
    # Patched in the ROUTER's namespace, which is where it is looked up.
    monkeypatch.setattr(resolve_router, "sellable_pool_for", pools)

    app = FastAPI()
    app.include_router(resolve_router.router)

    session = maker()
    issued = api_keys.issue(session, API_ORG, name="partner", role=Role.SALESPERSON)
    session.commit()

    def _session():
        s = maker()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issued.secret}"
    yield client, recorder, pools
    session.close()


def test_the_public_resolve_api_searches_this_organizations_book(api):
    client, recorder, pools = api

    response = client.post("/api/v1/resolve",
                           json={"text": "7781", "customer_ref": "Pitti"})

    assert response.status_code == 200, response.text
    assert len(recorder.calls) == 1
    assert recorder.pools[0] is pools.of(API_ORG)
    assert pools.asked == [API_ORG]


def test_confirm_re_resolves_against_the_same_book_the_answer_came_from(api):
    """Both calls about one line must be answers to one question.

    `confirm` re-resolves rather than trusting a proposal echoed back by the
    caller — that refusal is the gate. But re-resolving against a *different*
    pool would make the recomputed proposal a proposal about a different
    catalogue, and the caller's selection would be checked against an answer
    they were never shown. Asserted as object identity across the two requests,
    because `sellable_pool_for` caches on the organization and its book version
    and that cache is exactly what makes the two calls agree.
    """
    client, recorder, pools = api

    client.post("/api/v1/resolve", json={"text": "7781", "customer_ref": "Pitti"})
    client.post("/api/v1/resolve/confirm",
                json={"text": "7781", "customer_ref": "Pitti",
                      "record_id": "2576285"})

    assert len(recorder.calls) == 2, "confirm must re-resolve, not trust the caller"
    resolved, confirmed = recorder.pools
    assert confirmed is resolved
    assert confirmed is pools.of(API_ORG)


def test_the_pool_is_built_once_per_request_and_not_once_per_call(api):
    """Two requests, two builds — and one build inside each.

    `sellable_pool_for` is cached, so a per-line build would still return the
    right object and would still cost 154-164 ms of scanning per line. Counting
    the asks is the only way to see that from outside.
    """
    client, _recorder, pools = api

    client.post("/api/v1/resolve", json={"text": "7781", "customer_ref": "Pitti"})

    assert pools.asked == [API_ORG]


# ── the Quote Builder ────────────────────────────────────────────────────────

@pytest.fixture()
def quote_client(monkeypatch):
    engine = dbsupport.fresh_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    org = s.query(models.Organization).first().organization_id
    s.close()

    recorder = _Recorder()
    pools = _PoolSource()
    monkeypatch.setattr(quote_router.store.__class__, "_enrich_from_zoho",
                        lambda self, ln, zoho, code_changed=True: None)
    monkeypatch.setattr("app.store.pie_service.resolve", recorder)
    monkeypatch.setattr(quote_router, "sellable_pool_for", pools)

    api = FastAPI()
    api.include_router(platform_auth.router)
    api.include_router(quote_router.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    api.dependency_overrides[get_session] = _override
    yield TestClient(api), recorder, pools, org


def _login(client: TestClient, email: str) -> dict:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_every_line_of_an_rfq_resolves_against_the_book_and_against_one_book(
        quote_client, monkeypatch):
    """Three lines, three resolutions, one pool object — obtained once.

    The per-line assertion is the one that matters for correctness: a quote
    whose lines resolved against two different books resolved two ways, and the
    source snapshots itself at construction for exactly that reason. The
    obtained-once assertion is the one that matters for latency.
    """
    client, recorder, pools, org = quote_client
    hdr = _login(client, OWNER)

    quote = client.post("/api/v1/quotes", json={"customer": "Pitti"},
                        headers=hdr).json()
    response = client.post(f"/api/v1/quotes/{quote['id']}/intake",
                           json={"text": "7781, 10\n7782, 5\n7783, 2"},
                           headers=hdr)

    assert response.status_code == 200, response.text
    assert len(recorder.calls) == 3, "one resolution per line"
    assert pools.asked == [org], "the pool is built once for the whole intake"
    assert all(p is pools.of(org) for p in recorder.pools)
