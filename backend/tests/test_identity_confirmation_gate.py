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

The gate is two narrow conditions, in two files, neither previously tested:

* ``store._identity_candidate`` — a line carries a confirmable candidate only for
  the engine's own single-candidate NEEDS_REVIEW proposal, which is an *exact*
  catalogue hit downgraded for namespace safety, never a scored suggestion;
* ``routers.quote._confirm_identity`` — refuses unless the selected code is
  exactly that candidate.

These tests need no engine: they exercise the gate, not resolution.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.authz import Principal
from app.db import Base
from app.domain import models
from app.domain.enums import Role
from app.pie_service import Candidate, Resolution
from app.routers.quote import _confirm_identity
from app.store import Line, Quote, _identity_candidate

ORG = "org_test"
IDENTITY = "identity-pitti"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)          # a fixture, per CLAUDE.md §4
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

def test_only_the_engines_own_single_candidate_proposal_is_confirmable():
    """`_identity_candidate` is the first half of the gate, and is deliberately narrow.

    NEEDS_REVIEW with exactly one match is the cross-namespace proposal: the
    catalogue holds this exact code, but nobody has confirmed that *this
    customer's* code means it. Answering that question is a durable fact. Every
    other shape is not.
    """
    proposal = Resolution(
        input_text="7781", reqCode="7781", reqDesc="", rel="AMBIGUOUS", supplyCode=None,
        candidates=[Candidate(code="2001174", desc="CNMG 120408", rel="POSSIBLE")],
        outcome="NEEDS_REVIEW", semantics="IDENTITY")
    assert _identity_candidate(proposal) == "2001174"

    # A ranked requirement is not an identity question, however good the score.
    ranked = Resolution(
        input_text="CNMG 120408 insert", reqCode="CNMG 120408 insert", reqDesc="",
        rel="TECH", supplyCode="2001174",
        candidates=[Candidate(code="2001174", desc="A", rel="TECH", score=0.97)],
        outcome="AUTO_MATCH", semantics="REQUIREMENT")
    assert _identity_candidate(ranked) is None

    # Two candidates is an ambiguity, not a proposal — there is no single answer
    # to confirm.
    ambiguous = Resolution(
        input_text="7781", reqCode="7781", reqDesc="", rel="AMBIGUOUS", supplyCode=None,
        candidates=[Candidate(code="2001174", desc="A", rel="POSSIBLE"),
                    Candidate(code="6739214", desc="B", rel="POSSIBLE")],
        outcome="NEEDS_REVIEW", semantics="IDENTITY")
    assert _identity_candidate(ambiguous) is None


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
