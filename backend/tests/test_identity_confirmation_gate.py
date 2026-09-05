"""What may become a permanent "their code means this product" — and what may not.

A confirmed mapping is asserted identity: once written, the engine resolves that
customer's code AUTHORITATIVELY and never asks again. pie-parser then permits one
thing it otherwise refuses — deriving an effective requirement from the record
and ranking equivalents off it (its MIXED path is guarded on the reference being
AUTHORITATIVE precisely so that hop one carries no tolerance).

Which is why *what is allowed to become a mapping* is a correctness boundary
rather than bookkeeping. Selecting a scored equivalence suggestion is a
substitution on one quote: the engine put it a tolerance band away from the
request and said so. Filing that as identity would make an approximate match
exact by storage, and every later "same as their 7781 but 12 mm" would then
compose two tolerance bands — a wrong part with a defensible-looking explanation
attached. `A ≈ B` within band and `B ≈ C` within band is not `A ≈ C`.

The gate is two narrow conditions, and **two callers now reach it**:

* ``store._identity_candidate`` — a resolution offers a confirmable candidate
  only for the engine's own single-candidate NEEDS_REVIEW proposal, which is an
  *exact* catalogue hit downgraded for namespace safety, never a scored
  suggestion;
* ``identity.service.confirm_proposed_identity`` — refuses unless the selected
  code is exactly that candidate, and unless there is an identity to scope the
  fact to. ``routers.quote._confirm_identity`` (the Quote Builder) and
  ``routers.resolve.confirm`` (the public API) both go through it, which is why
  it is one function rather than the two copies it started as.

The API half is tested separately at the bottom of this file rather than taken
on trust. It has no ``Line`` to read ``identityCandidate`` off, so it derives
the proposal itself from a fresh resolution — a second site for the same
computation, and therefore the one that would drift.

These tests need no engine: they exercise the gate, not resolution.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.authz import Principal
from app.domain import models
from app.domain.enums import Role
from app.pie_service import Bands, Candidate, PieService, Resolution
from app.routers.quote import _confirm_identity
from app.store import Line, Quote, _identity_candidate

ORG = "org_test"
IDENTITY = "identity-pitti"


@pytest.fixture()
def session():
    engine = dbsupport.fresh_engine()          # a fixture, per CLAUDE.md §4
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def principal() -> Principal:
    return Principal(user_id="u1", organization_id=ORG, role=Role.SALESPERSON,
                     name="Tester", email="t@example.com")


def _line(**over) -> Line:
    base = dict(
        id="l1", raw="7781 x 10", reqCode="7781", reqDesc="Confirm this is the right product",
        reqQty=10, rel="AMBIGUOUS", supplyCode=None, candidates=[], outcome="NEEDS_REVIEW",
        semantics="IDENTITY", customerScope=IDENTITY, identityCandidate="2001174")
    base.update(over)
    return Line(**base)


def _mappings(session) -> list:
    return session.query(models.ConfirmedCodeMapping).all()


# ── which resolutions offer a confirmable candidate at all ──────────────────

def _mapped(matches, outcome, semantics, suggestions=()):
    """One engine payload through the real ``PieService._map``.

    **The gate is asked of the mapper, not of a hand-built ``Resolution``.**
    Every assertion below used to construct a ``Resolution`` directly and check
    ``_identity_candidate`` against its shape, and that is exactly how the
    defect this file exists to prevent went unnoticed for as long as it did: the
    shape a test can hand-build is `outcome` plus a candidate list, and those
    two fields do not say whether the candidate came out of ``matches`` (an
    exact catalogue hit) or ``suggestions`` (a scored equivalence). A test that
    can only speak in that vocabulary cannot express the distinction the gate
    turns on, so it agreed with a predicate that was wrong.

    ``lookup_record`` is stubbed to None because provenance is not under test
    here and reaching for it would make this need the engine.
    """
    svc = PieService.__new__(PieService)
    svc.lookup_record = lambda code: None
    return svc._map("7781", {"resolution": {"outcome": outcome,
                                            "input_semantics": semantics,
                                            "matches": list(matches)},
                             "suggestions": list(suggestions), "notes": []},
                    Bands(tech=0.85, compat=0.60))


_CANDIDATE_MATCH = {"record_id": "2001174", "description": "CNMG 120408",
                    "certainty": "CANDIDATE"}
_SECOND_MATCH = {"record_id": "6739214", "description": "CNMG 120404",
                 "certainty": "CANDIDATE"}
_SCORED_SUGGESTION = {
    "record_id": "prod_000144", "description": "VSM17 MILLING INSERT R=1.2 MH",
    "combined_score": 0.93, "attributes": {"corner_radius_mm": 1.2},
    "dimensionally_vacuous": False,
    "field_breakdown": [{"field": "corner_radius_mm", "status": "exact"}]}


def test_only_the_engines_own_single_candidate_proposal_is_confirmable():
    """`_identity_candidate` is the first half of the gate, and is deliberately narrow.

    A single CANDIDATE **match** under NEEDS_REVIEW is the cross-namespace
    proposal: the catalogue holds this exact code, but nobody has confirmed that
    *this customer's* code means it. Answering that question is a durable fact.
    Every other shape is not.
    """
    proposal = _mapped([_CANDIDATE_MATCH], "NEEDS_REVIEW", "IDENTITY")
    assert _identity_candidate(proposal) == "2001174"

    # A ranked requirement is not an identity question, however good the score.
    ranked = _mapped([], "AUTO_MATCH", "REQUIREMENT", [_SCORED_SUGGESTION])
    assert _identity_candidate(ranked) is None

    # Two candidates is an ambiguity, not a proposal — there is no single answer
    # to confirm.
    ambiguous = _mapped([_CANDIDATE_MATCH, _SECOND_MATCH], "NEEDS_REVIEW", "IDENTITY")
    assert _identity_candidate(ambiguous) is None


def test_a_scored_suggestion_is_never_confirmable_however_the_outcome_reads():
    """The defect this gate was believed to prevent, and did not.

    ``_identity_candidate`` decided with ``outcome == "NEEDS_REVIEW" and
    len(candidates) == 1``. The engine's outcome is carried through
    ``_map``'s *suggestion* branch verbatim, so a payload with no match and one
    scored suggestion arrived at that predicate wearing exactly those two
    properties and was published as ``identity_proposal.confirmable: true``.
    ``confirm_proposed_identity`` checks only that the selection equals the
    proposal, so it would then have been written into ``ConfirmedCodeMapping``
    as ASSERTED identity — the ``tolerance ∘ tolerance`` licence CLAUDE.md §1
    says these two conditions hold shut.

    Reproduced against the real ``_map`` before the fix; it returned
    ``'prod_000144'`` for the first case below. The candidate is a POSSIBLE at
    0.93 — the engine put it a band away and said so.

    Every NEEDS_REVIEW semantics is swept rather than the one that was found,
    because the branch that leaks is chosen by semantics: only ``IDENTITY``
    reaches the branch entitled to propose, and both of the others fall through
    to the suggestion branch carrying the same outcome.
    """
    for semantics in ("REQUIREMENT", "MIXED", "IDENTITY"):
        leaked = _mapped([], "NEEDS_REVIEW", semantics, [_SCORED_SUGGESTION])
        assert _identity_candidate(leaked) is None, semantics
        # The suggestion is still OFFERED — refusing to confirm it is not
        # refusing to show it. A gate that hid the candidate would have been a
        # different, worse change.
        assert [c.code for c in leaked.candidates] == ["prod_000144"], semantics


def test_the_proposal_is_set_by_the_mapper_and_by_nothing_else():
    """A ``Resolution`` built anywhere else proposes nothing, by default.

    The field defaults to ``None`` and exactly one branch of ``_map`` assigns
    it. That default is half the fix: the thirteen engine stubs in this suite
    return hand-built ``Resolution`` objects, and under the old predicate any
    of them shaped like a proposal WAS one. Now a stub has to say so on purpose.
    """
    hand_built = Resolution(
        input_text="7781", reqCode="7781", reqDesc="", rel="AMBIGUOUS",
        supplyCode=None,
        candidates=[Candidate(code="2001174", desc="CNMG 120408", rel="POSSIBLE")],
        outcome="NEEDS_REVIEW", semantics="IDENTITY")
    assert _identity_candidate(hand_built) is None

    said_so = Resolution(
        input_text="7781", reqCode="7781", reqDesc="", rel="AMBIGUOUS",
        supplyCode=None,
        candidates=[Candidate(code="2001174", desc="CNMG 120408", rel="POSSIBLE")],
        outcome="NEEDS_REVIEW", semantics="IDENTITY",
        identity_candidate="2001174")
    assert _identity_candidate(said_so) == "2001174"


# ── what the gate lets through ──────────────────────────────────────────────

def test_answering_the_engines_question_is_recorded(session, principal):
    """The positive control: without it the refusals below could pass by the
    path being dead rather than by the gate holding."""
    quote = Quote(id="q1", customer="Pitti", number="QB-1")
    line = _line()

    assert _confirm_identity(session, principal, quote, line, "2001174") is True

    rows = _mappings(session)
    assert len(rows) == 1
    assert rows[0].code == "7781" and rows[0].target_record_id == "2001174"
    # SAME_PRODUCT, not a relationship derived from a score — the caller does
    # not pass one, and this is the reason it must not start to.
    assert rows[0].relationship == "SAME_PRODUCT"


# ── what it refuses ─────────────────────────────────────────────────────────

def test_a_scored_substitution_is_never_filed_as_identity(session, principal):
    """The boundary this module exists for.

    The engine proposed 2001174 and the salesperson supplied 6739214 instead —
    a different product, reached across a tolerance band. Right for this quote;
    not a fact about what the customer's code means. Storing it would make the
    approximate exact, and license the two-hop composition on every later
    resolution of 7781.
    """
    quote = Quote(id="q1", customer="Pitti", number="QB-1")
    line = _line()

    assert _confirm_identity(session, principal, quote, line, "6739214") is False
    assert _mappings(session) == []


def test_a_line_with_no_proposal_confirms_nothing(session, principal):
    """A ranked requirement has `identityCandidate` None. Selecting its top
    candidate is picking a supply, not answering an identity question — and
    `None == None` must not read as a match."""
    quote = Quote(id="q1", customer="Pitti", number="QB-1")
    line = _line(identityCandidate=None, rel="TECH", outcome="AUTO_MATCH",
                 semantics="REQUIREMENT")

    assert _confirm_identity(session, principal, quote, line, "2001174") is False
    assert _confirm_identity(session, principal, quote, line, None) is False
    assert _mappings(session) == []


def test_an_unlinked_customer_confirms_nothing(session, principal):
    """With no identity to scope to there is no namespace the fact belongs in.
    Filing it against the typed customer name would let two Zoho companies'
    "ABC Industries" share one mapping."""
    quote = Quote(id="q1", customer="Pitti", number="QB-1")
    line = _line(customerScope=None)

    assert _confirm_identity(session, principal, quote, line, "2001174") is False
    assert _mappings(session) == []


# ── the same gate, reached over the public API ──────────────────────────────
#
# `POST /api/v1/resolve/confirm` writes the same table from the same decision,
# and a boundary that holds on one caller and not the other is not a boundary.
# The reason these tests exist rather than being taken on trust: the API path
# has no `Line`, so it cannot read `identityCandidate` off one — it re-resolves
# the text and derives the proposal itself. That is a *second place* the
# proposal is computed, which is exactly the shape of thing that drifts, and it
# is why `_identity_candidate` is imported by both rather than reimplemented.
#
# The engine is stubbed, deliberately and in the same spirit as the tests above:
# what is under test is which resolutions may become a mapping, not whether
# pie-parser produces them. The positive control below is what stops the
# refusals passing because the path is dead.

from fastapi import FastAPI                                        # noqa: E402
from fastapi.testclient import TestClient                          # noqa: E402

from app import api_keys, resolution                               # noqa: E402
from app.db import get_session                                     # noqa: E402
from app.routers import resolve as resolve_router                  # noqa: E402


def _proposal(code: str = "2001174") -> Resolution:
    """The engine's own single-candidate NEEDS_REVIEW — the one confirmable shape.

    ``identity_candidate`` is set explicitly, and that is the whole difference
    between this stub and one that merely *looks* like a proposal. Only
    ``PieService._map``'s match branch sets it on a real resolution; a stub that
    omitted it would be a resolution proposing nothing, which is what every
    other engine stub in this suite now is.
    """
    return Resolution(
        input_text="7781", reqCode="7781", reqDesc="", rel="AMBIGUOUS",
        supplyCode=None,
        candidates=[Candidate(code=code, desc="CNMG 120408", rel="POSSIBLE")],
        outcome="NEEDS_REVIEW", semantics="IDENTITY", identity_candidate=code)


@pytest.fixture()
def api(monkeypatch):
    """The resolve router over HTTP, with a live key and a stubbed engine.

    ``customer_scope_for`` is stubbed to a fixed identity rather than seeded
    through a customer row: the scope is an input to the gate, not part of it,
    and the test that the *absence* of a scope refuses sets it to None
    explicitly rather than relying on a fixture happening not to link anybody.
    """
    engine = dbsupport.fresh_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    monkeypatch.setattr(resolution, "customer_scope_for",
                        lambda *a, **k: IDENTITY)
    monkeypatch.setattr(resolution, "bands_for", lambda *a, **k: None)
    monkeypatch.setattr(resolution, "mapping_store_for", lambda *a, **k: None)

    app = FastAPI()
    app.include_router(resolve_router.router)

    session = maker()
    issued = api_keys.issue(session, ORG, name="partner CPQ")
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
    yield client, session
    session.close()


def _confirm(client, code: str, text: str = "7781"):
    return client.post("/api/v1/resolve/confirm",
                       json={"text": text, "customer_ref": "Pitti",
                             "record_id": code})


def test_the_api_records_an_answer_to_the_engines_own_question(api, monkeypatch):
    """The positive control for the API path, for the reason the quote path's is
    there: without it every refusal below could be passing because the endpoint
    is broken rather than because the gate holds."""
    client, session = api
    monkeypatch.setattr(resolve_router.pie_service, "resolve",
                        lambda *a, **k: _proposal())

    body = _confirm(client, "2001174").json()

    assert body["recorded"] is True
    rows = _mappings(session)
    assert len(rows) == 1
    assert rows[0].code == "7781" and rows[0].target_record_id == "2001174"
    assert rows[0].relationship == "SAME_PRODUCT"
    # Sourced to the key rather than to a person. Somebody asking months later
    # who taught the system this must be able to reach an answerable credential.
    assert rows[0].source_ref.startswith("api key ")


def test_the_api_refuses_a_record_the_engine_did_not_propose(api, monkeypatch):
    """The boundary. The engine proposed 2001174; the caller sent 6739214.

    Right for one quote, not a fact about what the customer's code means —
    and filing it would make an approximate match exact by storage, which is
    what licenses `tolerance ∘ tolerance` on the next resolution of 7781.
    """
    client, session = api
    monkeypatch.setattr(resolve_router.pie_service, "resolve",
                        lambda *a, **k: _proposal())

    body = _confirm(client, "6739214").json()

    assert body["recorded"] is False
    assert _mappings(session) == []


def test_the_api_refuses_a_scored_suggestion_however_good(api, monkeypatch):
    """A ranked requirement is not an identity question, whatever it scored.

    This is the case the API makes reachable that the quote screen does not: a
    caller can POST any text and any record id, so "the client would not offer
    it" is not a control.
    """
    client, session = api
    ranked = Resolution(
        input_text="CNMG 120408 insert", reqCode="CNMG 120408 insert",
        reqDesc="", rel="TECH", supplyCode="2001174",
        candidates=[Candidate(code="2001174", desc="A", rel="TECH", score=0.99)],
        outcome="AUTO_MATCH", semantics="REQUIREMENT")
    monkeypatch.setattr(resolve_router.pie_service, "resolve",
                        lambda *a, **k: ranked)

    body = _confirm(client, "2001174", text="CNMG 120408 insert").json()

    assert body["recorded"] is False
    assert _mappings(session) == []


def test_the_api_refuses_an_ambiguous_resolution(api, monkeypatch):
    """Two candidates is an ambiguity, not a proposal: there is no single answer
    to confirm, so naming one of them is a choice rather than a confirmation."""
    client, session = api
    ambiguous = Resolution(
        input_text="7781", reqCode="7781", reqDesc="", rel="AMBIGUOUS",
        supplyCode=None,
        candidates=[Candidate(code="2001174", desc="A", rel="POSSIBLE"),
                    Candidate(code="6739214", desc="B", rel="POSSIBLE")],
        outcome="NEEDS_REVIEW", semantics="IDENTITY")
    monkeypatch.setattr(resolve_router.pie_service, "resolve",
                        lambda *a, **k: ambiguous)

    assert _confirm(client, "2001174").json()["recorded"] is False
    assert _mappings(session) == []


def test_the_api_refuses_an_unlinked_customer(api, monkeypatch):
    """With no identity to scope to there is no namespace the fact belongs in.

    Filing it against the name in the request body would let two Zoho companies'
    "ABC Industries" share one mapping — and over an API the name is whatever
    the caller typed, which makes it worse rather than better.
    """
    client, session = api
    monkeypatch.setattr(resolve_router.pie_service, "resolve",
                        lambda *a, **k: _proposal())
    monkeypatch.setattr(resolution, "customer_scope_for", lambda *a, **k: None)

    assert _confirm(client, "2001174").json()["recorded"] is False
    assert _mappings(session) == []


def test_an_unauthenticated_caller_confirms_nothing(api, monkeypatch):
    """The gate is not the only thing standing here, and this says so: without a
    key the request never reaches it."""
    client, session = api
    monkeypatch.setattr(resolve_router.pie_service, "resolve",
                        lambda *a, **k: _proposal())
    client.headers.pop("Authorization")

    response = _confirm(client, "2001174")

    assert response.status_code == 401
    assert _mappings(session) == []


# ── the other thing a selection teaches: a phrase, for retrieval only ───────
#
# `_learn_phrase` sits beside `_confirm_identity` in `select_supply` and must
# never be mistaken for it. These pin what it records and what it refuses; the
# table it writes is read by `app/retrieval/aliases`, never by the engine
# (`tests/test_confirmed_mappings.py` pins that side).
from app.routers.quote import _learn_phrase  # noqa: E402


def _phrases(session) -> list:
    return session.query(models.CustomerPhraseAlias).all()


def test_a_choice_on_a_requirement_line_is_remembered_for_the_customer(session, principal):
    quote = Quote(id="q1", customer="Pitti", number="QB-1")
    line = _line(reqCode="12mm drill for SS", semantics="REQUIREMENT",
                 identityCandidate=None)

    assert _learn_phrase(session, principal, quote, line, "4149315") is True

    rows = _phrases(session)
    assert len(rows) == 1
    assert rows[0].phrase == "12mm drill for SS" and rows[0].target_record_id == "4149315"
    assert rows[0].identity_id == IDENTITY
    assert rows[0].source_ref == "quote q1 line l1"
    # And nothing was filed as identity: a choice is not a confirmation.
    assert _mappings(session) == []


def test_a_code_line_teaches_the_gate_or_nothing_never_a_phrase(session, principal):
    quote = Quote(id="q1", customer="Pitti", number="QB-1")
    assert _learn_phrase(session, principal, quote, _line(), "2001174") is False
    assert _phrases(session) == []


def test_an_unlinked_customer_teaches_no_phrase(session, principal):
    quote = Quote(id="q1", customer="Walk-in", number="QB-1")
    line = _line(reqCode="12mm drill for SS", semantics="REQUIREMENT",
                 customerScope=None, identityCandidate=None)
    assert _learn_phrase(session, principal, quote, line, "4149315") is False
    assert _phrases(session) == []


def test_selecting_the_line_itself_teaches_nothing(session, principal):
    quote = Quote(id="q1", customer="Pitti", number="QB-1")
    line = _line(reqCode="4149315", semantics="REQUIREMENT", identityCandidate=None)
    assert _learn_phrase(session, principal, quote, line, "4149315") is False
    assert _phrases(session) == []
