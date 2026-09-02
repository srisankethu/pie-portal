"""Confirming a customer's code, once, and never being asked again.

The loop: pie-parser proposes "your 7781 is probably MM# X" and refuses to
assert it (identity/resolver.py); a person confirms by selecting exactly that
record; the confirmation is recorded here; the next resolution answers
authoritatively without asking. Before this the confirmation was made on the
quote screen and discarded, so the question came back every quarter.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# pie-parser's own `identity` package, which app.pie_service puts on sys.path
# when it loads the engine. These tests assert against the engine's key shape
# without paying for a catalogue load, so they add it directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pie-parser"))
from sqlalchemy.orm import sessionmaker

import dbsupport
import piesupport
from app.domain import models
from app.identity import service as identity_service
from app.identity.mapping_store import OrgMappingStore

ORG = "org_test"
IDENTITY = "identity-pitti"
#: The company whose catalogue the two end-to-end tests resolve against. A
#: mapping is the organization's; the catalogue it resolves into is a
#: company's, and both have to be present for the confirmation to mean
#: anything.
COMPANY = piesupport.company_id("cx_confirmed_mappings")


@pytest.fixture()
def session():
    engine = dbsupport.fresh_engine()          # a fixture, per CLAUDE.md §4
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _confirm(session, code="7781", target="2001174", **kw):
    return identity_service.confirm_code_mapping(
        session, ORG, identity_id=IDENTITY, code=code,
        target_record_id=target, source_ref="quote q1 line l1",
        user_id="u1", **kw)


def test_a_confirmation_is_recorded_and_auditable(session):
    row = _confirm(session)
    assert row is not None
    assert row.code == "7781" and row.target_record_id == "2001174"
    assert row.active is True

    # The identity layer's own audit trail carries it too — a wrong mapping has
    # to be traceable to the moment somebody made it.
    events = session.query(models.IdentityEvent).all()
    assert [e.action for e in events] == ["CODE_MAPPING_CONFIRMED"]
    assert "2001174" in events[0].detail


def test_confirming_the_same_thing_twice_does_not_stack(session):
    first = _confirm(session)
    again = _confirm(session)
    assert again.mapping_id == first.mapping_id
    assert session.query(models.ConfirmedCodeMapping).count() == 1


def test_a_correction_supersedes_rather_than_overwrites(session):
    first = _confirm(session, target="2001174")
    second = _confirm(session, target="6739214")

    session.refresh(first)
    assert first.active is False and first.superseded_by == second.mapping_id
    assert second.active is True
    # The old row survives: a quote sent under it must stay explainable.
    assert first.target_record_id == "2001174"


def test_the_code_is_normalized_the_way_the_engine_normalizes_it(session):
    _confirm(session, code="  7781-a  ")
    row = session.query(models.ConfirmedCodeMapping).one()
    # Trimmed and upper-cased, punctuation intact — stripping the dash would
    # merge two genuinely different part numbers.
    assert row.code == "7781-A"


def test_nothing_is_recorded_without_a_scope(session):
    assert identity_service.confirm_code_mapping(
        session, ORG, identity_id="", code="7781",
        target_record_id="2001174") is None
    assert session.query(models.ConfirmedCodeMapping).count() == 0


# ── what the engine actually reads ──────────────────────────────────────────
#
# From here down the engine has to be present: the store indexes its rows with
# the engine's own ScopedIdentifier, so constructing one needs it on sys.path.
# The tests above exercise the write side, which does not, and keep running in
# a checkout with no access to the private submodule.


@pytest.mark.requires_pie
def test_the_store_answers_in_the_engine_s_own_key_shape(session):
    from identity.model import Namespace, ScopedIdentifier

    _confirm(session)
    store = OrgMappingStore(session, ORG)
    assert len(store) == 1

    hit = store.lookup(ScopedIdentifier(Namespace.CUSTOMER_ITEM, "7781", IDENTITY))
    assert hit is not None and hit.target_record_id == "2001174"

    # A different customer's identical code is a different key — this is the
    # whole reason the namespace is scoped.
    assert store.lookup(
        ScopedIdentifier(Namespace.CUSTOMER_ITEM, "7781", "identity-other")) is None
    assert store.lookup(
        ScopedIdentifier(Namespace.CUSTOMER_ITEM, "9999", IDENTITY)) is None


@pytest.mark.requires_pie
def test_a_superseded_mapping_is_not_served(session):
    _confirm(session, target="2001174")
    _confirm(session, target="6739214")
    store = OrgMappingStore(session, ORG)
    assert len(store) == 1

    from identity.model import Namespace, ScopedIdentifier
    hit = store.lookup(ScopedIdentifier(Namespace.CUSTOMER_ITEM, "7781", IDENTITY))
    assert hit.target_record_id == "6739214"


@pytest.mark.requires_pie
def test_mappings_do_not_leak_between_organizations(session):
    _confirm(session)
    assert len(OrgMappingStore(session, "org_other")) == 0


# ── the loop, end to end through the real engine ────────────────────────────

@pytest.mark.requires_pie
def test_a_confirmed_mapping_changes_what_the_engine_resolves(session):
    """The point of all of it: confirm once, resolve authoritatively after.

    Runs the real pie-parser against the real catalogue, so this asserts the
    portal's store is actually reachable from inside the engine — not that the
    two halves look compatible.
    """
    from app.pie_service import pie_service

    piesupport.give_company_a_catalogue(COMPANY)
    CODE = "PITTI-77-XY"          # the customer's own code; not in the catalogue

    # Before: the customer's code means nothing to anyone.
    before = pie_service.resolve(CODE, IDENTITY, None, OrgMappingStore(session, ORG),
                                 connection_id=COMPANY)
    assert before.rel == "UNRESOLVED"
    assert before.supplyCode is None

    _confirm(session, code=CODE, target="2001174")

    after = pie_service.resolve(CODE, IDENTITY, None, OrgMappingStore(session, ORG),
                                 connection_id=COMPANY)
    assert after.rel == "EXACT", "a confirmed mapping must resolve authoritatively"
    assert after.supplyCode == "2001174"
    assert after.outcome == "AUTO_MATCH"


@pytest.mark.requires_pie
def test_one_customer_s_confirmation_does_not_answer_for_another(session):
    """The namespace rule, proven end to end rather than by construction."""
    from app.pie_service import pie_service

    piesupport.give_company_a_catalogue(COMPANY)
    CODE = "PITTI-77-XY"
    _confirm(session, code=CODE, target="2001174")
    store = OrgMappingStore(session, ORG)

    assert pie_service.resolve(CODE, IDENTITY, None, store,
                               connection_id=COMPANY).supplyCode == "2001174"
    # Same code, a different real-world customer: still unknown.
    assert pie_service.resolve(CODE, "identity-someone-else", None, store,
                               connection_id=COMPANY).rel == "UNRESOLVED"


@pytest.mark.requires_pie
def test_the_fingerprint_is_computed_once_per_store(session, monkeypatch):
    """It is in a cache *key*, so it runs per line unless it is memoised.

    ``pie_service.resolve`` builds a key per resolved line and the key carries
    this digest, so a 200-line RFQ against 5,000 confirmed mappings hashed a
    million fields purely to look something up — added work on the exact hot
    path the cache exists to make faster. The method's own docstring claimed it
    ran "once per resolution batch", which was simply not true of any caller.

    Memoised on the store rather than fixed at the call site, because
    ``_by_key`` is built in ``__init__`` and never mutated afterwards: the
    answer cannot change under a caller, and every caller wants the same one.
    """
    import hashlib as _hashlib

    from app.identity import mapping_store as module

    _confirm(session)
    store = OrgMappingStore(session, ORG)

    built = []
    real = _hashlib.sha256
    monkeypatch.setattr(module.hashlib, "sha256",
                        lambda *a, **k: (built.append(1), real(*a, **k))[1])

    first = store.fingerprint()
    for _ in range(50):
        assert store.fingerprint() == first

    assert len(built) == 1, (
        f"the digest was rebuilt {len(built)} times for one snapshot")


@pytest.mark.requires_pie
def test_two_stores_over_different_mappings_still_fingerprint_differently(
        session):
    """The memo must not become a shared answer. A confirmation that supersedes
    another leaves the row count identical while changing what the store
    answers, which is the whole reason this is content and not a count."""
    store_before = OrgMappingStore(session, ORG)
    _confirm(session)
    store_after = OrgMappingStore(session, ORG)

    assert store_before.fingerprint() != store_after.fingerprint()


def test_the_store_hands_its_active_rows_to_retrieval_as_aliases(session):
    """Same snapshot, same fingerprint: what the engine resolves exactly and
    what retrieval offers as near are one set of facts."""
    _confirm(session, code="7781", target="2001174")
    superseded = _confirm(session, code="7782", target="2001174")
    _confirm(session, code="7782", target="6739214")     # supersedes the above
    session.refresh(superseded)
    assert superseded.active is False

    store = OrgMappingStore(session, ORG)
    assert store.aliases() == [(IDENTITY, "7781", "2001174", "code"),
                               (IDENTITY, "7782", "6739214", "code")]


# ── phrases: what a person chose, remembered for retrieval only ─────────────

def _choose(session, phrase="12mm drill for SS", target="4149315", **kw):
    return identity_service.record_phrase_alias(
        session, ORG, identity_id=IDENTITY, phrase=phrase,
        target_record_id=target, source_ref="quote q1 line l2", user_id="u1", **kw)


def test_a_choice_is_recorded_under_its_own_key_and_audited(session):
    row = _choose(session, phrase="  12mm  drill for SS ")
    assert row is not None
    assert row.phrase == "12mm  drill for SS"
    assert row.phrase_key == "12MM DRILL FOR SS"
    assert row.target_record_id == "4149315" and row.active is True
    events = session.query(models.IdentityEvent).all()
    assert [e.action for e in events] == ["PHRASE_ALIAS_RECORDED"]


def test_choosing_the_same_thing_again_does_not_stack(session):
    first = _choose(session)
    again = _choose(session, phrase="12MM DRILL FOR SS")
    assert again.alias_id == first.alias_id
    assert session.query(models.CustomerPhraseAlias).count() == 1


def test_a_different_choice_supersedes_the_old_one(session):
    first = _choose(session, target="4149315")
    second = _choose(session, target="4151229")
    session.refresh(first)
    assert first.active is False and first.superseded_by == second.alias_id
    assert second.active is True


def test_nothing_is_recorded_without_a_scope_a_phrase_or_a_target(session):
    assert identity_service.record_phrase_alias(
        session, ORG, identity_id=None, phrase="x", target_record_id="1") is None
    assert identity_service.record_phrase_alias(
        session, ORG, identity_id=IDENTITY, phrase="   ", target_record_id="1") is None
    assert identity_service.record_phrase_alias(
        session, ORG, identity_id=IDENTITY, phrase="x", target_record_id=None) is None
    assert session.query(models.CustomerPhraseAlias).count() == 0


def test_a_phrase_reaches_retrieval_and_the_fingerprint_but_never_the_engine(session):
    """The line that keeps a choice from becoming an identity: the store hands
    it to retrieval as a ``phrase``, changes its fingerprint so the alias
    index is rebuilt, and answers the engine's lookup with nothing."""
    from identity.model import Namespace, ScopedIdentifier

    _confirm(session, code="7781", target="2001174")
    before = OrgMappingStore(session, ORG).fingerprint()
    _choose(session)

    store = OrgMappingStore(session, ORG)
    assert store.aliases() == [(IDENTITY, "12mm drill for SS", "4149315", "phrase"),
                               (IDENTITY, "7781", "2001174", "code")]
    assert store.fingerprint() != before
    assert len(store) == 1, "a phrase must not count as a mapping"
    assert store.lookup(ScopedIdentifier(
        namespace=Namespace.CUSTOMER_ITEM, value="12mm drill for SS",
        scope=IDENTITY)) is None
