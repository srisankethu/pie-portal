"""The book in the pool: this organization's own products as candidate records.

Before this, ``resolve_rfq._build_sources`` built the candidate pool from the
decoded manufacturer catalogue and nothing else — ``PieService._make_args``
passes ``zoho_fixture=None`` — so a Zoho item reached the ranking only through
``products.pie_record_id``, which is set on about 9% of the master. The other
~91% could not be offered however well it matched. The first test here is that
claim, stated as a before and an after against one product that is deliberately
**not** catalogue-linked; everything after it is a way that could go wrong.

The engine is the real one and so is the decoder. A stub that returned the
attributes the stub chose would prove the plumbing and nothing about whether a
decoded item name actually survives the round trip into a comparison the
scoring layer can make — which is the whole question.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

import pytest

import piesupport

from app import sellable_catalog as sellable
from app.attributes import CATALOGUE_LINK, DECODED_NAME
from app.attributes import decorate_products as _decorate_products
from app.config import settings as _settings


# See the note in test_product_attributes.py: `decode_names` has no default
# rule set, so the caller names one. Every caller here names the organization
# layer, which is what these fixtures' product names are written in.
#: A company's ruleset checksum. Any non-empty value will do — these tests are
#: about the pool half of the key — but it must be non-empty, because an
#: unreadable checksum is deliberately never cached.
VERSION = "rs-test"

#: The company these tests resolve for. A catalogue belongs to a company now —
#: ``PieService._view(None)`` is None and answers "No catalogue" rather than
#: falling back to a deployment-wide default — so a test that wants a real
#: answer names one. The pool is the organization's book; the connection is
#: whose manufacturer catalogue it sits beside, and this module needs both.
COMPANY = piesupport.company_id("test-sellable-pool")


@pytest.fixture(autouse=True)
def _company_catalogue():
    piesupport.give_company_a_catalogue(COMPANY)


def decorate_products(session, organization_id, **kw):
    kw.setdefault("rule_set", _settings.PIE_PACK)
    return _decorate_products(session, organization_id, **kw)
from app.domain import models
from app.pie_service import Bands, pie_service

pytestmark = pytest.mark.requires_pie

ORG = "org_book"
OTHER = "org_rival"

#: An item name in the master's own notation whose geometry decodes out of the
#: name alone — the case this whole change is for: an item the catalogue link
#: never found, whose *description* the pack can still read.
#:
#: **36 mm is chosen, and it is the reason these tests are deterministic.** The
#: catalogue carries 287 distinct cutting diameters and none of them is 36, so
#: this record is the only candidate that pins the request's diameter exactly
#: and it wins on ``rank_tier`` — measured at rank 0 with a ``record_id`` of
#: "0000-b" and again with "zzzz-b". A record tying the catalogue on geometry
#: would instead be ordered by ``record_id``, which is sort order rather than
#: evidence (``query._sort_key`` says so), and a fixture whose product_id is a
#: random uuid would then pass or fail on where that uuid landed among 6,717
#: catalogue ids. That is a flaky test, and it is flaky for the same reason
#: ``_is_discriminating`` exists.
BOOK_NAME = "ENDMILL 771E 36x36x90x200 RAD 0,5"
BOOK_REQUEST = "endmill 36mm r0.5"

#: The same geometry under another series, so a second organization's book is a
#: different book rather than the same rows under a different id — and ranks in
#: its own pool exactly as the first one does in its own.
OTHER_BOOK_NAME = "ENDMILL 4XNE 36x36x90x200 R0.5"

#: Decodes to nothing. ``test_attribute_decoration_run`` uses the same string,
#: for the same reason: the "no evidence, no row" half has to be real.
UNDECODABLE_NAME = "MISC BRACKET ASSEMBLY 4 OFF"


def _bands() -> Bands:
    return Bands(tech=0.85, compat=0.5)


@pytest.fixture(autouse=True)
def _clean_caches():
    """Both caches are module-level and process-wide, which is exactly what
    these tests are about. Clearing them per test keeps one test's pool from
    answering the next one's question — the same failure the tenant assertions
    below are checking for, arriving from the test harness instead."""
    from app import pie_service as ps

    sellable._pool_cache.clear()
    ps._resolution_cache.clear()
    yield
    sellable._pool_cache.clear()
    ps._resolution_cache.clear()


def _org(session, organization_id: str = ORG) -> None:
    if session.get(models.Organization, organization_id) is None:
        session.add(models.Organization(organization_id=organization_id,
                                        name=organization_id))
        session.flush()


def _product(session, name: str, *, organization_id: str = ORG,
             external_id: str = "i1", active: bool = True) -> models.Product:
    """One item of the master, deliberately **not** catalogue-linked.

    ``pie_record_id`` stays NULL, which is the state of ~91% of the live master
    and the state this whole module exists to make useful. A test that linked it
    would be testing the path that already worked.
    """
    _org(session, organization_id)
    row = models.Product(organization_id=organization_id, connector="zoho",
                         connection_id="c1", external_id=external_id, name=name,
                         active=active)
    session.add(row)
    session.flush()
    assert row.pie_record_id is None
    return row


def _decorate(session, organization_id: str = ORG) -> None:
    decorate_products(session, organization_id)
    session.flush()


def _codes(resolution) -> List[str]:
    return [c.code for c in resolution.candidates]


def _resolve(text: str, pool: Any = None):
    from app import pie_service as ps

    ps._resolution_cache.clear()
    return pie_service.resolve(text, bands=_bands(), connection_id=COMPANY,
                               pool=pool)


# ── the claim ────────────────────────────────────────────────────────────────

def test_an_unlinked_product_goes_from_unofferable_to_offered(session):
    """The whole change, as a before and an after on one product.

    Before: the item is in the books, its name decodes, it matches the request
    — and it is not in the pool, so it cannot be offered at all. After: it is a
    ranked candidate. Nothing else about the resolution has to change for this
    to be the fix; the product being *offerable* is the fix.
    """
    row = _product(session, BOOK_NAME)
    _decorate(session)

    before = _resolve(BOOK_REQUEST)
    assert row.product_id not in _codes(before), (
        "the manufacturer catalogue cannot contain a portal product id — if it "
        "does, this test is not measuring what it says")

    pool = sellable.sellable_pool_for(session, ORG)
    assert pool is not None and len(pool) == 1
    after = _resolve(BOOK_REQUEST, pool)

    assert row.product_id in _codes(after)
    offered = after.candidates[0]
    assert offered.code == row.product_id, (
        "nothing in the catalogue pins a 36 mm diameter, so this record is the "
        "only one that matched the request's dimensions exactly")
    assert offered.desc == BOOK_NAME
    assert offered.brand == sellable.SELLABLE_LABEL


def test_the_decoded_geometry_is_what_makes_it_match(session):
    """Not merely present in the pool — present with the facts that scored it.

    A record the engine could not compare would still appear (nothing gates it
    out), so "it is in the candidate list" is not on its own evidence that the
    store round-tripped. The comparison having been made is.
    """
    row = _product(session, BOOK_NAME)
    _decorate(session)
    pool = sellable.sellable_pool_for(session, ORG)

    result = _resolve(BOOK_REQUEST, pool)
    offered = next(c for c in result.candidates if c.code == row.product_id)
    assert offered.unverified is False, (
        "the request named a diameter and a corner radius, and the store held "
        "both, so the engine had something to compare")
    assert offered.attributes.get("cutting_dia_mm") == 36.0
    assert offered.attributes.get("corner_radius_mm") == 0.5


# ── the tenant boundary ──────────────────────────────────────────────────────

def test_one_organizations_pool_never_contains_anothers_product(session):
    mine = _product(session, BOOK_NAME)
    theirs = _product(session, OTHER_BOOK_NAME, organization_id=OTHER,
                      external_id="r1")
    _decorate(session)
    _decorate(session, OTHER)

    ids = {r.record_id for r in sellable.sellable_pool_for(session, ORG).load()}
    assert ids == {mine.product_id}
    other_ids = {r.record_id
                 for r in sellable.sellable_pool_for(session, OTHER).load()}
    assert other_ids == {theirs.product_id}


def test_the_resolution_cache_does_not_serve_one_tenant_from_another(session):
    """``_resolution_cache`` is process-wide and every organization reads it.

    The same request text, resolved for two organizations whose books differ,
    must not be answered twice from the first one's entry — that is a
    cross-tenant read wearing a cache hit's clothes. Asserted on the *answer*
    rather than on the key, because a key that happened to differ for another
    reason would make the assertion pass while the property was gone.
    """
    from app import pie_service as ps

    mine = _product(session, BOOK_NAME)
    theirs = _product(session, OTHER_BOOK_NAME, organization_id=OTHER,
                      external_id="r1")
    _decorate(session)
    _decorate(session, OTHER)

    ps._resolution_cache.clear()
    # One company's catalogue, two organizations' books: the catalogue half of
    # the key is deliberately held constant, so the only thing that can
    # separate these two answers is the pool.
    first = pie_service.resolve(
        BOOK_REQUEST, bands=_bands(), connection_id=COMPANY,
        pool=sellable.sellable_pool_for(session, ORG))
    # Deliberately NOT cleared: the second call has to miss on its own merits.
    second = pie_service.resolve(
        BOOK_REQUEST, bands=_bands(), connection_id=COMPANY,
        pool=sellable.sellable_pool_for(session, OTHER))

    assert mine.product_id in _codes(first)
    assert mine.product_id not in _codes(second)
    assert theirs.product_id in _codes(second)
    assert theirs.product_id not in _codes(first)


# ── an undecorated organization resolves exactly as it does today ────────────

def test_an_organization_with_no_attributes_has_no_pool_and_resolves_as_before(session):
    _product(session, BOOK_NAME)          # products, but nobody decorated them

    assert sellable.sellable_pool_for(session, ORG) is None
    assert _resolve(BOOK_REQUEST, None).to_dict() == _resolve(
        BOOK_REQUEST, sellable.sellable_pool_for(session, ORG)).to_dict()


def test_an_organization_that_does_not_exist_has_no_pool(session):
    assert sellable.sellable_pool_for(session, "org_nobody") is None
    assert sellable.sellable_pool_for(session, "") is None


# ── what is in the pool, and what is not ─────────────────────────────────────

def test_a_product_with_no_attribute_is_counted_rather_than_offered(session):
    """It cannot be matched on and it would take one of ``top_n`` slots.

    Nothing gates a record with no comparable field out of a comparison, so it
    scores the 1.0 that means "no evidence against" — the top of the same scale
    a real match is measured on. Counted, not dropped in silence: the number is
    how much of the book is waiting on decoding.
    """
    kept = _product(session, BOOK_NAME, external_id="i1")
    bare = _product(session, UNDECODABLE_NAME, external_id="i2")
    _decorate(session)

    pool = sellable.sellable_pool_for(session, ORG)
    assert {r.record_id for r in pool.load()} == {kept.product_id}
    assert pool.without_attributes == 1
    assert bare.product_id not in _codes(_resolve(BOOK_REQUEST, pool))


def test_a_product_the_business_stopped_selling_is_not_offered(session):
    """A stale pool offers a product the business stopped selling. ``active``
    is the books' own statement that it did."""
    row = _product(session, BOOK_NAME)
    _decorate(session)
    assert len(sellable.sellable_pool_for(session, ORG)) == 1

    row.active = False
    session.flush()
    sellable._pool_cache.clear()
    assert sellable.sellable_pool_for(session, ORG) is None


# ── which source wins ────────────────────────────────────────────────────────

def _write(session, product_id: str, key: str, text: str, num: Optional[float],
           source_kind: str, organization_id: str = ORG) -> None:
    session.add(models.ProductAttributeValue(
        organization_id=organization_id, product_id=product_id,
        attribute_key=key, value_text=text, value_num=num,
        source_kind=source_kind))
    session.flush()


def test_the_manufacturers_own_data_beats_a_reading_of_the_name(session):
    """Both kinds claim ``corner_radius_mm`` and they disagree.

    CATALOGUE_LINK is the maker's own row for the item; DECODED_NAME is the
    parser's guess at what that row would say. The model's docstring settles
    which is the stronger claim, and :data:`SOURCE_PRECEDENCE` is that sentence
    written as a ranking — in one place, so it cannot be "whichever the query
    returned last".
    """
    row = _product(session, BOOK_NAME)
    _write(session, row.product_id, "corner_radius_mm", "0.4", 0.4, DECODED_NAME)
    _write(session, row.product_id, "corner_radius_mm", "0.8", 0.8, CATALOGUE_LINK)

    values = sellable._live_attributes(session, ORG)
    assert values[row.product_id]["corner_radius_mm"] == 0.8


def test_a_human_correction_outranks_every_decoder(session):
    """The two kinds this package does not write are still ranked, because an
    unranked kind falls to the bottom — which would make a person's correction
    lose to a name decode."""
    row = _product(session, BOOK_NAME)
    _write(session, row.product_id, "corner_radius_mm", "0.8", 0.8, CATALOGUE_LINK)
    _write(session, row.product_id, "corner_radius_mm", "1.2", 1.2, "HUMAN")

    values = sellable._live_attributes(session, ORG)
    assert values[row.product_id]["corner_radius_mm"] == 1.2


def test_a_superseded_value_is_not_read(session):
    from app import clock

    row = _product(session, BOOK_NAME)
    _write(session, row.product_id, "corner_radius_mm", "0.4", 0.4, DECODED_NAME)
    stale = session.query(models.ProductAttributeValue).one()
    stale.superseded_at = clock.now()
    session.flush()

    assert sellable._live_attributes(session, ORG) == {}


# ── the value, back in the type the engine compares it as ────────────────────

def test_an_integer_field_does_not_come_back_as_a_float():
    """``value_num`` is a Float column, so it cannot say 4 from 4.0 — and the
    engine's soft-signal comparison is ``str(v).upper()``, under which a
    request's ``flute_count`` of 4 *mismatches* a candidate's 4.0 and takes a
    penalty for agreeing. ``value_text`` keeps the distinction."""
    assert sellable._value_of(4.0, "4") == 4
    assert sellable._value_of(0.8, "0.8") == 0.8
    assert sellable._value_of(None, "TiAlN") == "TiAlN"
    assert sellable._value_of(None, "true") == "true"


def test_a_dimension_that_is_not_a_number_is_dropped_rather_than_guessed():
    """``compare_geometry`` calls ``float()`` on both sides of a dimensional
    field, so a text claim on a ``_mm`` key would raise inside the engine and
    take the line to PIE_DOWN. ``ZohoCatalogSource._normalize`` drops the same
    case for the same reason."""
    assert sellable._usable("corner_radius_mm", 0.8) is True
    assert sellable._usable("corner_radius_mm", None) is False
    assert sellable._usable("coating", None) is True


# ── a vacuous candidate is never a technical equivalence ─────────────────────

def test_a_book_record_with_nothing_comparable_is_never_an_equivalence(session,
                                                                       monkeypatch):
    """``_unverified`` has to fire on these exactly as it does on a catalogue
    record, and the cap has to survive into what gets priced.

    A record carrying a coating and no geometry answers a geometric request
    with the 1.0 that means "no evidence against" — above every band — and
    nothing gates it out, because ``compare_geometry`` gates only on what both
    sides specify. So the honest ceiling is POSSIBLE and it must never be the
    auto-selected supply.

    ``TOP_N`` is raised because this record ties every other vacuous candidate
    in the catalogue and the order among them is ``record_id``. Truncation
    would make the candidate *absent*, and a loop over an empty list is the
    benign default this file is here to refuse: the assertion below is that it
    is present **and** capped.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "TOP_N", 200)
    row = _product(session, "SOME TOOL WITH A COATING")
    _write(session, row.product_id, "coating", "TiAlN", None, DECODED_NAME)

    pool = sellable.sellable_pool_for(session, ORG)
    result = _resolve(BOOK_REQUEST, pool)
    offered = [c for c in result.candidates if c.code == row.product_id]
    assert offered, "the record is in the pool, so it must have been scored"
    assert offered[0].unverified is True
    assert offered[0].rel not in ("TECH", "COMPAT"), (
        "nothing about the request was compared, so its score is not a measure "
        "of fit and may not be read as equivalence")
    assert offered[0].score == 1.0, (
        "and it scored a perfect 1.0 while being worth nothing — which is why "
        "the cap is read off the engine's marker rather than off the score")
    assert result.supplyCode != row.product_id


# ── sellability goes through the restriction, not around it ──────────────────

def _catalogue_namespace(catalogue: Any) -> str:
    """The manufacturer pack's own id, read from the catalogue rather than
    typed: the portal holds no manufacturer literals worth pinning a test to.

    Read from *this company's* union catalogue, which is the file the engine
    was pointed at above. There is no process-wide `ensure_catalog()` to ask
    any more — a catalogue belongs to a company — and asking a different one
    for the namespace would restrict against a pack the run never loaded.
    """
    with open(catalogue, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                return str(json.loads(line)["pack_id"])
    raise AssertionError("the catalogue is empty")


def test_the_book_is_sellable_because_it_is_in_the_pool_not_because_it_is_the_book(session):
    """Decision 003: a Zoho item is sellable by definition and must still pass
    *through* ``_sellable_namespaces`` rather than around it.

    So the proof is a deployment that names its sellable namespaces and leaves
    this one out: the book records must then be excluded, exactly as any other
    namespace would be. If they survived that, the source would be bypassing the
    restriction rather than passing it.
    """
    row = _product(session, BOOK_NAME)
    _decorate(session)
    pool = sellable.sellable_pool_for(session, ORG)

    # The company's catalogue and its sources, composed exactly as `resolve`
    # composes them. There is no process-wide `_sources` to borrow any more —
    # a catalogue belongs to a company — so the view is what this reaches for,
    # and the pool is appended to its list rather than handed to the engine,
    # which is the whole property under test.
    mod = pie_service._ensure_module()
    view = pie_service._view(COMPANY)
    assert view is not None, "the autouse fixture should have given it one"
    if view.sources is None:
        view.sources = mod._build_sources(
            pie_service._make_args("", catalog_path=view.path))
    args = pie_service._make_args(BOOK_REQUEST, catalog_path=view.path)
    sources = [*view.sources, pool]

    open_result, _ = mod.run(args, sources)
    assert row.product_id in [s["record_id"] for s in open_result["suggestions"]]
    assert sellable.SELLABLE_LABEL in open_result["pool_brands"]

    args.sellable_namespaces = _catalogue_namespace(view.path)
    restricted, _ = mod.run(args, sources)
    assert row.product_id not in [s["record_id"] for s in restricted["suggestions"]]
    assert sellable.SELLABLE_LABEL not in restricted["pool_brands"]


# ── the pool is scored, never asserted ───────────────────────────────────────

def test_a_book_record_can_never_become_an_asserted_identity(session):
    """Non-transitivity, at the one place storage could smuggle a second hop in.

    A confirmed mapping is *asserted* identity, and the engine will derive a
    requirement from an asserted record. These records never enter identity
    resolution at all — ``AuthoritativeIndex`` is built from the catalogue JSONL
    — so a book product can be a suggestion and can never be the exact reference
    a later requirement is ranked off.
    """
    row = _product(session, BOOK_NAME)
    _decorate(session)
    pool = sellable.sellable_pool_for(session, ORG)

    assert pie_service.lookup_record(row.product_id) is None
    result = _resolve(BOOK_REQUEST, pool)
    assert result.rel != "EXACT"
    for cand in result.candidates:
        if cand.code == row.product_id:
            assert cand.rel != "EXACT"


# ── invalidation ─────────────────────────────────────────────────────────────

def test_an_unchanged_book_is_not_rebuilt(session):
    _product(session, BOOK_NAME)
    _decorate(session)
    first = sellable.sellable_pool_for(session, ORG)
    assert sellable.sellable_pool_for(session, ORG) is first


def test_decorating_the_book_moves_the_version_and_rebuilds_the_pool(session):
    row = _product(session, BOOK_NAME)
    _decorate(session)
    first = sellable.sellable_pool_for(session, ORG)

    _write(session, row.product_id, "flute_count", "4", 4.0, "HUMAN")
    second = sellable.sellable_pool_for(session, ORG)
    assert second is not first
    assert second.fingerprint() != first.fingerprint()
    assert second.load()[0].get("flute_count") == 4


def test_a_new_product_moves_the_version(session):
    _product(session, BOOK_NAME, external_id="i1")
    _decorate(session)
    before = sellable.pool_version(session, ORG)

    _product(session, OTHER_BOOK_NAME, external_id="i2")
    _decorate(session)
    assert sellable.pool_version(session, ORG) != before


def test_a_retraction_with_no_replacement_moves_the_version(session):
    """The live count falls. This is the case a "newest timestamp" key alone
    would miss, and the one that leaves a withdrawn value on offer."""
    from app import clock

    row = _product(session, BOOK_NAME)
    _write(session, row.product_id, "corner_radius_mm", "0.8", 0.8, DECODED_NAME)
    before = sellable.pool_version(session, ORG)

    live = session.query(models.ProductAttributeValue).one()
    live.superseded_at = clock.now()
    session.flush()
    assert sellable.pool_version(session, ORG) != before


def test_the_pool_fingerprint_is_in_the_resolution_cache_key(session):
    """A resolution answered from an entry keyed without the pool would keep
    serving the book as it was before the sync decorated it."""
    row = _product(session, BOOK_NAME)
    _decorate(session)
    pool = sellable.sellable_pool_for(session, ORG)

    # `version` is the company's ruleset checksum and sits between the mapping
    # store and the pool in the key; held constant here so the only thing that
    # moves is the pool.
    keyed_without = pie_service._cache_key(BOOK_REQUEST, None, None, VERSION, None)
    keyed_with = pie_service._cache_key(BOOK_REQUEST, None, None, VERSION, pool)
    assert keyed_with != keyed_without

    _write(session, row.product_id, "flute_count", "4", 4.0, "HUMAN")
    moved = sellable.sellable_pool_for(session, ORG)
    assert pie_service._cache_key(BOOK_REQUEST, None, None, VERSION, moved) != keyed_with


def test_a_pool_that_cannot_be_fingerprinted_is_never_cached():
    """The mapping store's rule, now shared: caching against an input nothing
    can see costs the customer a stale answer that looks authoritative."""
    assert pie_service._cache_key("x", None, None, VERSION, object()) is None
    assert pie_service._cache_key("x", None, None, VERSION, None) is not None


def test_a_resolution_with_no_ruleset_version_is_never_cached():
    """The other half of the same rule, and it predates the pool: an unreadable
    checksum must not put every such company on one shared key."""
    assert pie_service._cache_key("x", None, None, "") is None


# ── determinism ──────────────────────────────────────────────────────────────

def test_two_builds_of_one_unchanged_book_are_identical(session):
    """The engine breaks its last ranking tie on ``record_id``, so a pool in
    scan order would let two runs over one database disagree about which of two
    identical products is named first."""
    _product(session, BOOK_NAME, external_id="i1")
    _product(session, BOOK_NAME, external_id="i2")
    _decorate(session)

    def shape() -> List[Any]:
        return [(r.record_id, r.brand_label, r.pack_id,
                 sorted(r.record.items(), key=str))
                for r in sellable.build_pool(session, ORG).load()]

    assert shape() == shape()


# ── the hard gate ───────────────────────────────────────────────────────────

def test_a_drill_in_the_book_is_not_offered_for_an_endmill_request(session):
    """The defect that made ``products.decoded_family`` exist.

    ``product_family`` is the strongest of ``distance.HARD_GATE_FIELDS``, and
    ``attributes.ROUTE_FIELDS`` refuses to store a route as an attribute — for
    a good reason, since the engine emits one on every routed row and counting
    it would report Phase 1 coverage as ~100%. The consequence, once this
    organization's own products became a candidate pool, was that pool records
    carried no family and matched ACROSS families: this exact drill came back
    rank 0 for this exact request, scored 1.0, and marked **verified** — because
    a dimension genuinely was compared, so nothing downstream had grounds to
    doubt it.

    A wrong part, top of the list, labelled as checked. Pinned with the real
    corpus names rather than synthetic ones so the test fails if the decode
    that produces the two families ever stops producing them.
    """
    from app.sellable_catalog import build_pool

    for pid, name in (("p-drill", "SC DRILL 11,1mm/.4370/ 5xD COOLANT"),
                      ("p-mill", "GP SC End Mill 4FL 10x10x22x72")):
        session.add(models.Product(
            product_id=pid, organization_id=ORG, external_id=pid, name=name,
            source_ref={}))
    session.commit()
    decorate_products(session, ORG)
    session.commit()

    assert session.get(models.Product, "p-drill").decoded_family \
        == "solid_carbide_drill", (
        "the route is not being stored, so the gate below cannot fire and the "
        "assertion after it would pass for the wrong reason")

    res = pie_service.resolve("endmill 11.1mm 4 flute",
                              pool=build_pool(session, ORG))
    assert "p-drill" not in [c.code for c in res.candidates], (
        "a drill was offered for an endmill request — the family gate did not "
        "fire on a pool record")


def test_the_route_is_stored_on_the_product_and_never_as_an_attribute(session):
    """Both halves, because they are one decision that had to split.

    Storing the route as an attribute would take the Phase 1 coverage number to
    ~100% with an office chair counted as a decorated product. Not storing it
    at all put a drill at rank 0 for an endmill request. Do not count it; do
    store it — and a test that checked only one half would let the other back.
    """
    session.add(models.Product(
        product_id="p-x", organization_id=ORG, external_id="x",
        name="GP SC End Mill 4FL 10x10x22x72", source_ref={}))
    session.commit()
    decorate_products(session, ORG)
    session.commit()

    assert session.get(models.Product, "p-x").decoded_family
    stored = {v.attribute_key for v in session.query(models.ProductAttributeValue)
              .filter_by(product_id="p-x")}
    assert not stored & {"product_family", "product_subfamily"}, (
        "a route reached the attribute store, where it inflates the coverage "
        "number decision 002 judges the phase on")
