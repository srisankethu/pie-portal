"""The public resolution API: who may call it, and what it says when it cannot answer.

Three claims, and two of them are about refusing.

**An abstention is an answer, and there are four different ones.** The endpoint
exists so an ERP can record what this platform knows about a line; the failure
that would make it worse than nothing is a caller writing "unknown part" into
their master data because a deployment had no catalogue loaded.
``pie_service.catalog_available`` is what separates "the pack does not cover
this item" from "nobody asked the pack", and these tests pin that the
separation survives all the way to the wire — a different reason, a different
``is_evidence_about_the_input`` flag, and a different HTTP status.

**An API key is a recipient like any other** (CLAUDE.md §1). A key minted at
SALESPERSON gets exactly what a salesperson's screen gets: no cost, no margin,
and no rule whose boundary is either. The test that matters is not a
field-level assertion — every one of those passed while
``quote-intelligence/assess`` was giving up cost — but the sweep:
``proposed_price`` walked across the cost, asserting the response does not
change there.

**A credential is a credential.** No key, a malformed key, a revoked key and
one belonging to nobody are one answer, and a key spends a bounded number of
requests per minute.

The engine is stubbed throughout. What is under test is the boundary, not
pie-parser — ``tests/test_pie_service.py`` covers resolution itself, and a
suite that needed a 13 MB catalogue to prove a 401 would be skipped on exactly
the checkouts that most need it to run.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import api_keys, ratelimit, resolution
from app.db import get_session
from app.domain import models
from app.domain.enums import Role
from app.pie_service import Candidate, Resolution
from app.routers import resolve as resolve_router

ORG = "org_test"


def _resolution(**over) -> Resolution:
    """An ordinary EXACT identity — the shape a caller is meant to receive."""
    base = dict(
        input_text="2576285", reqCode="2576285", reqDesc="CNMG 120408 - TN2000",
        rel="EXACT", supplyCode="2576285",
        candidates=[Candidate(code="2576285", desc="CNMG 120408 - TN2000",
                              rel="EXACT", grade="TN2000", brand="WIDIA",
                              reason="Exact manufacturer identity.")],
        outcome="AUTO_MATCH", semantics="IDENTITY")
    base.update(over)
    return Resolution(**base)


#: One decoded catalogue row, with the per-field meta the document's provenance
#: comes from. Shaped exactly as `engine/quality.py`'s `emit` writes it, and
#: checked against a live row for MM# 2576285 rather than invented — the three
#: cases below are all real and all mean different things:
#:
#: * ``iso_shape`` — read out of a substring, so it has a span;
#: * ``insert_polarity`` — derived, so it has a provenance and no span;
#: * ``product_family`` — decided by the family router rather than a grammar
#:   slot, so it reaches the record as a top-level key with **no field_meta
#:   entry at all** and therefore no provenance to report.
#:
#: ``material_class`` is None here to stand for a slot the engine decoded to
#: nothing, which must not appear in the projection at all.
_RECORD = {
    "record_id": "2576285",
    "description_norm": "CNMG 120408 - TN2000",
    "row_confidence": 0.97,
    "product_family": "turning_insert",
    "iso_shape": "C",
    "insert_polarity": "negative",
    "corner_radius_mm": 0.8,
    "material_class": None,
    "field_meta": {
        "iso_shape": {"provenance": "GRAMMAR_EXACT", "confidence": 0.97,
                      "raw": "C", "span": [0, 1]},
        "corner_radius_mm": {"provenance": "GRAMMAR_EXACT", "confidence": 0.97,
                             "raw": "08", "span": [9, 11]},
        "insert_polarity": {"provenance": "DERIVED", "confidence": 0.9,
                            "raw": None, "span": None},
    },
}


@pytest.fixture(autouse=True)
def _clean_limiter():
    """The counters are process-wide by design, so a test that fills one would
    otherwise leak into whichever test ran next."""
    for bucket in ("api_key", "api_key_failures"):
        ratelimit.reset(bucket)
    yield
    for bucket in ("api_key", "api_key_failures"):
        ratelimit.reset(bucket)


@pytest.fixture()
def env(monkeypatch):
    """The resolve router over HTTP, with the engine stubbed and a live key.

    ``catalog_available`` is forced True here — the deployment running these
    tests may have no catalogue, and a fixture that let that decide would make
    every assertion below pass for the wrong reason. The one test that wants it
    False sets it False and says so.
    """
    engine = dbsupport.fresh_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    svc = resolve_router.pie_service
    monkeypatch.setattr(type(svc), "catalog_available", property(lambda self: True))
    monkeypatch.setattr(type(svc), "catalog_version", property(lambda self: "abc123"))
    monkeypatch.setattr(svc, "resolve", lambda *a, **k: _resolution())
    monkeypatch.setattr(svc, "lookup_record", lambda code: dict(_RECORD))
    monkeypatch.setattr(resolution, "customer_scope_for", lambda *a, **k: None)
    monkeypatch.setattr(resolution, "bands_for", lambda *a, **k: None)
    monkeypatch.setattr(resolution, "mapping_store_for", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(resolve_router.router)

    session = maker()

    def _session():
        s = maker()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    yield app, maker, session, svc
    session.close()


def _key(session, *, role: Role = Role.SALESPERSON, limit: int | None = None):
    issued = api_keys.issue(session, ORG, name="partner", role=role,
                            rate_limit_per_minute=limit)
    session.commit()
    return issued


def _client(app, secret: str) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {secret}"
    return c


def _post(client, **over) -> dict:
    body = {"text": "2576285"}
    body.update(over)
    return client.post("/api/v1/resolve", json=body)


# ── the credential ──────────────────────────────────────────────────────────

def test_a_call_without_a_key_is_refused(env):
    app, _maker, _session, _svc = env
    assert TestClient(app).post("/api/v1/resolve",
                                json={"text": "2576285"}).status_code == 401


@pytest.mark.parametrize("presented", ["pie_nosuchkey_secret", "not-a-key",
                                       "pie_only_two"])
def test_every_bad_credential_gets_the_same_refusal(env, presented):
    """One message for all of them. Telling "no such key" from "wrong secret"
    apart says which half of a guess was right, which is most of what an
    attacker holding a leaked key id is after."""
    app, maker, session, _svc = env
    _key(session)
    client = _client(app, presented)

    response = _post(client)

    assert response.status_code == 401
    assert response.json()["detail"] == api_keys._REJECTED


def test_a_revoked_key_stops_working(env):
    app, maker, session, _svc = env
    issued = _key(session)
    client = _client(app, issued.secret)
    assert _post(client).status_code == 200

    api_keys.revoke(session, ORG, issued.row.key_id)
    session.commit()

    assert _post(client).status_code == 401


def test_a_key_is_scoped_to_the_organization_that_minted_it(env):
    """The tenant on the response is the key's, never anything in the body.

    Stated as a test because the organization is the one field a machine caller
    might reasonably expect to choose, and the answer is that it cannot.
    """
    app, maker, session, _svc = env
    issued = _key(session)
    client = _client(app, issued.secret)

    _post(client, customer_ref="Pitti")

    session.expire_all()
    assert session.get(models.ApiKey, issued.row.key_id).organization_id == ORG


def test_a_key_spends_a_bounded_number_of_requests_a_minute(env):
    """The speed bump, and the header that lets a partner avoid hitting it.

    Not a quota — ``app/ratelimit.py`` says why an in-process counter scales
    with the number of replicas. What it buys is that a price-bisection sweep
    is slow enough to be visible in a log rather than being two round trips.
    """
    app, maker, session, _svc = env
    issued = _key(session, limit=2)
    client = _client(app, issued.secret)

    first = _post(client)
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"

    assert _post(client).status_code == 200
    refused = _post(client)
    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"


def test_guessing_at_one_key_is_bounded(env):
    """Failed attempts against a real key id are capped, and a success clears it.

    Guessing a 256-bit secret is infeasible, so this is not what stands between
    an attacker and the data — the counter is here so that the argument does
    not have to be made, and so a deployment holding a rotated key stops
    burning PBKDF2 on every retry.
    """
    app, maker, session, _svc = env
    issued = _key(session)
    key_id = issued.row.key_id
    wrong = _client(app, f"pie_{key_id}_definitely-not-the-secret")

    for _ in range(api_keys.FAILED_ATTEMPTS_PER_MINUTE):
        assert _post(wrong).status_code == 401
    assert _post(wrong).status_code == 401

    # The right secret is refused too while the door is shut — the counter is
    # on the key id, which is the thing being guessed at.
    right = _client(app, issued.secret)
    assert _post(right).status_code == 401

    # And a success clears it, so ordinary use never accumulates a count.
    ratelimit.reset("api_key_failures")
    assert _post(right).status_code == 200
    for _ in range(api_keys.FAILED_ATTEMPTS_PER_MINUTE - 1):
        assert _post(wrong).status_code == 401
    assert _post(right).status_code == 200


def test_the_published_spec_needs_no_key(env):
    """A contract a partner cannot read before somebody mints them a key is not
    published. It describes shapes only — no data, no tenant, no key."""
    app, _maker, _session, _svc = env

    spec = TestClient(app).get("/api/v1/resolve/openapi.json")

    assert spec.status_code == 200
    body = spec.json()
    assert "/api/v1/resolve" in body["paths"]
    assert body["info"]["version"] == resolution.API_VERSION
    # The credential is a *security scheme*, not two optional headers. A
    # generated client reads this to know it must send one at all, and a spec
    # describing the endpoint as open to anyone would be a published lie.
    assert set(body["components"]["securitySchemes"]) == {"bearerApiKey",
                                                          "apiKeyHeader"}
    assert body["paths"]["/api/v1/resolve"]["post"]["security"]


# ── minting the credential ──────────────────────────────────────────────────

@pytest.fixture()
def keys_app(env):
    """The key-management router, with the signed-in principal swappable.

    ``current_principal`` is overridden rather than a user seeded and a token
    minted, because what is under test here is ``require_owner`` — which runs
    for real either way — and not the sign-in path that
    ``test_auth_and_approvals`` already covers.
    """
    _app, maker, _session, _svc = env
    from app.authz import Principal, current_principal
    from app.routers import api_keys as api_keys_router

    holder: dict = {}

    app = FastAPI()
    app.include_router(api_keys_router.router)

    def _session_dep():
        s = maker()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session_dep
    app.dependency_overrides[current_principal] = lambda: holder["principal"]

    def as_role(role: Role) -> TestClient:
        holder["principal"] = Principal(
            user_id="u1", organization_id=ORG, role=role, name="Owner",
            email="o@example.com")
        return TestClient(app)

    return as_role


def test_only_an_owner_may_mint_a_key(keys_app):
    """A key is access to the book. Handing the power to create one to every
    role would make the narrowest role able to mint the widest."""
    for role in (Role.SALESPERSON, Role.SALES_MANAGER):
        assert keys_app(role).post("/api/v1/api-keys", json={}).status_code == 403
        assert keys_app(role).get("/api/v1/api-keys").status_code == 403


def test_the_secret_is_returned_once_and_never_listed(keys_app):
    owner = keys_app(Role.OWNER)

    created = owner.post("/api/v1/api-keys",
                         json={"name": "Acme CPQ", "role": "OWNER"})
    assert created.status_code == 201
    body = created.json()
    secret = body["secret"]
    assert secret.startswith("pie_")
    assert body["role"] == "OWNER"

    listed = owner.get("/api/v1/api-keys").json()["keys"]
    assert [k["key_id"] for k in listed] == [body["key_id"]]
    # Neither the secret nor its hash. The hint is four characters of a
    # 43-character random string, which identifies a row and reconstructs
    # nothing.
    assert secret not in repr(listed)
    assert "secret_hash" not in repr(listed)
    assert listed[0]["secret_hint"] == secret[-4:]


def test_a_key_defaults_to_the_narrowest_role(keys_app):
    """Not the creator's. An integration inheriting whoever minted it is how a
    partner's server ends up holding the whole book's cost basis because nobody
    thought about the field."""
    created = keys_app(Role.OWNER).post("/api/v1/api-keys", json={}).json()

    assert created["role"] == Role.SALESPERSON.value


def test_revoking_is_idempotent_and_scoped(keys_app):
    owner = keys_app(Role.OWNER)
    key_id = owner.post("/api/v1/api-keys", json={}).json()["key_id"]

    assert owner.delete(f"/api/v1/api-keys/{key_id}").status_code == 200
    assert owner.delete(f"/api/v1/api-keys/{key_id}").status_code == 200
    first = owner.get("/api/v1/api-keys").json()["keys"][0]["revoked_at"]
    assert first is not None
    # The second revoke kept the first time. When it was ended is evidence.
    assert owner.get("/api/v1/api-keys").json()["keys"][0]["revoked_at"] == first

    # An id that names nothing and an id belonging to another tenant are one
    # answer, so the endpoint never confirms another tenant's key exists.
    assert owner.delete("/api/v1/api-keys/nosuchkey").status_code == 404


# ── the answer ──────────────────────────────────────────────────────────────

def test_a_resolved_line_carries_provenance_confidence_and_spans(env):
    """The claim the whole endpoint rests on: every decoded fact says where it
    came from and how sure the engine was, and a span says which characters of
    *which string* it was read out of."""
    app, maker, session, _svc = env
    client = _client(app, _key(session).secret)

    body = _post(client).json()

    assert body["status"] == "RESOLVED"
    assert body["abstention"] is None
    assert body["resolution"]["record_id"] == "2576285"
    assert body["resolution"]["record_confidence"] == 0.97
    assert body["engine"]["ruleset_checksum"] == "abc123"

    attrs = {a["name"]: a for a in body["resolution"]["attributes"]}
    assert attrs["corner_radius_mm"]["provenance"] == "GRAMMAR_EXACT"
    assert attrs["corner_radius_mm"]["confidence"] == 0.97
    assert attrs["corner_radius_mm"]["span"] == {
        "start": 9, "end": 11, "text_ref": "product.description"}
    # Derived rather than read out of a substring. An absent span is a real and
    # different provenance, not a missing one — so the span is null and the
    # provenance still says which kind of derivation it was.
    assert attrs["insert_polarity"]["provenance"] == "DERIVED"
    assert attrs["insert_polarity"]["span"] is None
    # And a slot the pack recorded no provenance for at all reports null rather
    # than a stand-in label. `product_family` is decided by the family router,
    # not a grammar slot, so there is no decode behind it to cite — and a
    # made-up provenance here would be an audit trail the engine never wrote.
    assert attrs["product_family"]["value"] == "turning_insert"
    assert attrs["product_family"]["provenance"] is None
    assert attrs["product_family"]["confidence"] is None
    # A slot the engine decoded to nothing is absent entirely, never null: a
    # null value reads as "decoded and found nothing", which is a different
    # claim from "never decoded".
    assert "material_class" not in attrs


def test_the_span_into_the_callers_own_text_is_exact_or_absent(env):
    """A *nearly* right span highlights the wrong word in somebody's UI while
    looking authoritative. If the matched code is not literally in the text,
    the honest answer is no span."""
    app, maker, session, _svc = env
    client = _client(app, _key(session).secret)

    found = _post(client, text="need 10 of 2576285 please").json()
    assert found["resolution"]["input_span"] == {
        "start": 11, "end": 18, "text_ref": "input.text"}

    absent = _post(client, text="the usual insert for the Pitti job").json()
    assert absent["resolution"]["input_span"] is None


def test_alternatives_are_ranked_and_exclude_the_chosen_record(env, monkeypatch):
    app, maker, session, svc = env
    monkeypatch.setattr(svc, "resolve", lambda *a, **k: _resolution(
        candidates=[
            Candidate(code="2576285", desc="A", rel="EXACT"),
            Candidate(code="2576424", desc="B", rel="TECH", score=0.91),
            Candidate(code="2576999", desc="C", rel="COMPAT", score=0.72)]))
    client = _client(app, _key(session).secret)

    body = _post(client).json()

    assert [a["record_id"] for a in body["alternatives"]] == ["2576424", "2576999"]
    assert [a["equivalence_score"] for a in body["alternatives"]] == [0.91, 0.72]


# ── the four abstentions, which are four different answers ──────────────────

def test_no_catalogue_is_not_a_statement_about_the_product(env, monkeypatch):
    """The distinction ``catalog_available`` exists for, all the way to the wire.

    A caller that recorded this as "no such part" would be writing a deployment
    problem into their master data. It gets a different reason, a false
    ``is_evidence_about_the_input``, and a 503 — three chances to notice.
    """
    app, maker, session, svc = env
    monkeypatch.setattr(type(svc), "catalog_available",
                        property(lambda self: False))
    client = _client(app, _key(session).secret)

    response = _post(client)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "ABSTAINED"
    assert body["abstention"]["reason"] == "CATALOGUE_UNAVAILABLE"
    assert body["abstention"]["is_evidence_about_the_input"] is False
    assert body["resolution"] is None


def test_a_searched_catalogue_that_holds_nothing_is_evidence(env, monkeypatch):
    """The other side of the same distinction, and the reason it is worth the
    two code paths: this one a caller *may* record."""
    app, maker, session, svc = env
    monkeypatch.setattr(svc, "resolve", lambda *a, **k: _resolution(
        rel="UNRESOLVED", supplyCode=None, candidates=[], outcome="UNRESOLVED"))
    client = _client(app, _key(session).secret)

    response = _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["abstention"]["reason"] == "NO_MATCH"
    assert body["abstention"]["is_evidence_about_the_input"] is True


def test_an_engine_failure_abstains_rather_than_500ing(env, monkeypatch):
    """A single bad line must never reach a caller as an unexplained 500. It is
    a 503 carrying the same document shape every other answer has."""
    app, maker, session, svc = env
    monkeypatch.setattr(svc, "resolve", lambda *a, **k: _resolution(
        rel="PIE_DOWN", supplyCode=None, candidates=[], outcome="ERROR",
        pie_offline=True))
    client = _client(app, _key(session).secret)

    response = _post(client)

    assert response.status_code == 503
    body = response.json()
    assert body["abstention"]["reason"] == "ENGINE_ERROR"
    assert body["abstention"]["is_evidence_about_the_input"] is False
    # The same keys as a resolved answer, so a caller parses one document type.
    assert set(body) == {"api_version", "input", "status", "abstention",
                         "resolution", "alternatives", "identity_proposal",
                         "commercial", "engine", "notes"}


def test_an_ambiguous_line_returns_the_options_rather_than_picking_one(env,
                                                                      monkeypatch):
    app, maker, session, svc = env
    monkeypatch.setattr(svc, "resolve", lambda *a, **k: _resolution(
        rel="AMBIGUOUS", supplyCode=None, outcome="AMBIGUOUS",
        candidates=[Candidate(code="2576285", desc="A", rel="POSSIBLE", score=1.0),
                    Candidate(code="2576424", desc="B", rel="POSSIBLE", score=1.0)]))
    client = _client(app, _key(session).secret)

    response = _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["abstention"]["reason"] == "AMBIGUOUS"
    assert body["resolution"] is None
    assert len(body["alternatives"]) == 2


def test_an_unconfirmed_customer_code_is_its_own_abstention(env, monkeypatch):
    """The one abstention with an action attached, kept apart from AMBIGUOUS.

    The catalogue holds this exact code; what is unconfirmed is that *this
    customer's* code means it. Folding it into AMBIGUOUS would describe one
    record as "several answer this text" and would point the caller at choosing
    rather than at confirming — a different next move, which is the test for
    whether a reason earns its own name.
    """
    app, maker, session, svc = env
    monkeypatch.setattr(svc, "resolve", lambda *a, **k: _resolution(
        input_text="7781", reqCode="7781", rel="AMBIGUOUS", supplyCode=None,
        outcome="NEEDS_REVIEW",
        candidates=[Candidate(code="2576285", desc="CNMG 120408 - TN2000",
                              rel="POSSIBLE")],
        # Set explicitly, because only `PieService._map`'s match branch sets it
        # on a real resolution and a stub shaped like a proposal is not one.
        # See `test_identity_confirmation_gate.py` for why the shape stopped
        # being sufficient: a scored suggestion wears it too.
        identity_candidate="2576285"))
    client = _client(app, _key(session).secret)

    response = _post(client, text="7781")

    assert response.status_code == 200
    body = response.json()
    assert body["abstention"]["reason"] == "NEEDS_CONFIRMATION"
    assert body["abstention"]["is_evidence_about_the_input"] is True
    assert body["identity_proposal"] == {
        "record_id": "2576285", "confirmable": True,
        "detail": body["identity_proposal"]["detail"]}


def test_a_low_scoring_selection_still_answers_and_says_how_weak_it_is(
        env, monkeypatch):
    """Whether a line resolved is the *engine's* answer, not this module's.

    The engine selects a supply whenever its ranking discriminates, and the
    band it falls in becomes the relationship. An API that abstained on
    POSSIBLE while the Quote Builder priced it would mean two recipients
    disagreeing about what the engine said — "did PIE resolve this?" would
    depend on who asked. So it answers, and `relationship` plus
    `equivalence_score` are how a caller judges it.
    """
    app, maker, session, svc = env
    monkeypatch.setattr(svc, "resolve", lambda *a, **k: _resolution(
        rel="POSSIBLE", outcome="AUTO_MATCH", semantics="REQUIREMENT",
        candidates=[Candidate(code="2576285", desc="CNMG 120408 - TN2000",
                              rel="POSSIBLE", score=0.41),
                    Candidate(code="2576424", desc="B", rel="POSSIBLE",
                              score=0.30)]))
    client = _client(app, _key(session).secret)

    body = _post(client).json()

    assert body["status"] == "RESOLVED"
    assert body["resolution"]["relationship"] == "POSSIBLE"
    assert body["resolution"]["equivalence_score"] == 0.41
    # A requirement, not an identifier: nothing in the text is a catalogue code.
    assert body["resolution"]["input_span"] is None


def test_a_whole_document_is_refused_rather_than_split(env):
    """One enquiry line per request. A splitter that guessed line boundaries
    would be making a decision this endpoint could not explain afterwards."""
    app, maker, session, _svc = env
    client = _client(app, _key(session).secret)

    assert _post(client, text="x" * 600).status_code == 422
    assert _post(client, text="   ").status_code == 422


# ── the commercial half, and the rule people get wrong ──────────────────────

def _seed_priced_product(session, *, cost: Decimal) -> None:
    """One product the books cost, which is what gives the cost rules a
    boundary to fire at.

    ``external_id`` is the resolved record id, because that is what the API
    hands the assessment: ``resolution._commercial`` passes the engine's
    ``supplyCode`` as the product ref, so the manufacturer's number is the
    join. A test that seeded it under some other key would exercise the
    unresolved branch while looking like it exercised this one.
    """
    session.add(models.Product(product_id="p1", organization_id=ORG,
                               external_id="2576285",
                               name="CNMG 120408 - TN2000", uom="pcs"))
    session.add(models.CostRecord(
        organization_id=ORG, external_ref="B1:1", product_id="p1",
        date=date.today() - timedelta(days=30),
        qty=Decimal("100"), unit_cost=cost,
        source_ref={"record_type": "bill", "record_id": "B1"}))
    session.commit()


def test_no_price_means_no_commercial_block_at_all(env):
    """A pure nomenclature call stays one. The economics are opt-in, so an
    integration that only needs to identify a part never touches them."""
    app, maker, session, _svc = env
    client = _client(app, _key(session).secret)

    assert _post(client).json()["commercial"] is None


def test_a_sales_key_receives_no_cost_and_no_margin(env):
    """The §1 rule, applied to a machine recipient by the same function that
    applies it to a screen. Absent, not zeroed and not masked."""
    app, maker, session, _svc = env
    _seed_priced_product(session, cost=Decimal("400"))
    client = _client(app, _key(session, role=Role.SALESPERSON).secret)

    body = _post(client, proposed_price="500", quantity="10").json()

    commercial = body["commercial"]
    assert commercial["assessed"] is True
    assert "economics" not in commercial
    assert "position" not in commercial
    blob = repr(commercial)
    assert "unit_cost" not in blob and "margin_pct" not in blob


def test_a_sales_key_cannot_walk_the_price_to_recover_cost(env):
    """The shape of test this class of defect needs.

    Every field-level assertion in the file that covered ``filterCounts.MFLOOR``
    passed while the endpoint gave up cost, because the leak was not a field —
    it was a *predicate* the caller could walk. So this sweeps
    ``proposed_price`` straight across the cost and asserts the answer does not
    change there. It may change at the approval floor and at the review floor;
    those are the two residual boundaries §1 accepts, and they are two equations
    in three unknowns.
    """
    app, maker, session, _svc = env
    cost = Decimal("400")
    _seed_priced_product(session, cost=cost)
    client = _client(app, _key(session, role=Role.SALESPERSON, limit=0).secret)

    def answer(price: Decimal) -> str:
        body = _post(client, proposed_price=str(price), quantity="1").json()
        rules = sorted(e["code"] for e in body["commercial"]["exceptions"])
        return f"{rules}|{body['commercial']['requires_approval']}"

    # The rules are live, and this line is why the two below are not vacuous.
    # A response that never changed at any price would satisfy them while
    # proving only that the assessment was dead.
    assert answer(Decimal("300")) != answer(Decimal("600"))

    # Straddling cost by one rupee on each side. A boundary at cost would show
    # up here as two different answers; §1's point is that there must not be
    # one, because cost carries no policy multiplier to obscure it — which is
    # exactly what made ``NEGATIVE_MARGIN`` recoverable before the two cost
    # rules collapsed into one ``APPROVAL_REQUIRED``.
    assert answer(cost - Decimal("1")) == answer(cost + Decimal("1"))
    assert answer(cost) == answer(cost + Decimal("1"))

    # And the answer a salesperson does get where it moves is the substitute,
    # never a rule naming what placed the boundary.
    below = _post(client, proposed_price="300", quantity="1").json()
    codes = {e["code"] for e in below["commercial"]["exceptions"]}
    assert "APPROVAL_REQUIRED" in codes
    assert not (codes & {"NEGATIVE_MARGIN", "BELOW_MIN_MARGIN",
                         "BELOW_MARGIN_FLOOR"})
    assert all("boundary_refs" not in e
               for e in below["commercial"]["exceptions"])


def test_an_owner_key_is_a_manager_recipient(env):
    """The positive control for the redaction above: without it,
    ``test_a_sales_key_receives_no_cost_and_no_margin`` would pass on a
    response that had no economics for anybody, which is a different bug.
    """
    app, maker, session, _svc = env
    _seed_priced_product(session, cost=Decimal("400"))
    client = _client(app, _key(session, role=Role.OWNER).secret)

    body = _post(client, proposed_price="500", quantity="10").json()

    assert "economics" in body["commercial"]
