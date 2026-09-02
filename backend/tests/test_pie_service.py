"""The pie-parser integration: real resolution mapped to portal relationships."""
from __future__ import annotations

import pytest

import piesupport
from app.pie_service import pie_service

# Every test here resolves through the real engine against the real catalogue.
pytestmark = pytest.mark.requires_pie

#: The company these tests resolve for. There is no catalogue that is not a
#: company's now, so a test that wants a real answer has to name one — passing
#: nothing is a resolution that cannot say which item master it read, and the
#: engine answers UNRESOLVED to exactly that.
COMPANY = piesupport.company_id("test-pie-service")


@pytest.fixture(scope="module", autouse=True)
def _company_catalogue():
    """Give COMPANY the shipped catalogue, decoded once for this worker."""
    piesupport.give_company_a_catalogue(COMPANY)
    yield
    piesupport.forget_company_catalogue(COMPANY)


def test_exact_identity_resolves_to_exact():
    # A real MM# from the Kennametal/WIDIA corpus resolves authoritatively.
    res = pie_service.resolve("2001174", connection_id=COMPANY)
    assert res.rel == "EXACT"
    assert res.supplyCode == "2001174"
    assert res.semantics == "IDENTITY"
    assert res.candidates and res.candidates[0].rel == "EXACT"


def test_requirement_surfaces_candidates_and_only_picks_when_ranking_separates():
    """A requirement always surfaces options; it auto-selects a supply product
    only when the ranking actually separates a winner.

    When every candidate carries the same score the "top" one is an artefact of
    ordering, not a technical equivalent — auto-selecting and pricing it would
    put a fabricated match on a customer quote.
    """
    res = pie_service.resolve("CNMG 120408 KCP25", connection_id=COMPANY)
    assert res.semantics == "REQUIREMENT"
    assert res.candidates, "a requirement should surface ranked supply candidates"

    if res.supplyCode is not None:
        # A winner was picked — the ranking must have separated it.
        assert res.rel in ("TECH", "COMPAT", "POSSIBLE")
        assert res.candidates[0].score is not None
    else:
        # Abstained — options are offered, nothing is auto-selected or priced.
        assert res.rel == "AMBIGUOUS"
        assert all(c.rel == "POSSIBLE" for c in res.candidates)
        assert any("could not distinguish" in n for n in res.notes)


def test_non_discriminating_scores_never_auto_select():
    from app.pie_service import Candidate

    same = [Candidate(code="a", desc="A", rel="TECH", grade=None, brand=None,
                      score=1.0, reason=""),
            Candidate(code="b", desc="B", rel="TECH", grade=None, brand=None,
                      score=1.0, reason="")]
    clear = [Candidate(code="a", desc="A", rel="TECH", grade=None, brand=None,
                       score=0.97, reason=""),
             Candidate(code="b", desc="B", rel="COMPAT", grade=None, brand=None,
                       score=0.71, reason="")]
    assert pie_service._is_discriminating(same) is False
    assert pie_service._is_discriminating(clear) is True


def test_unknown_code_is_unresolved():
    res = pie_service.resolve("XZ-CUSTOM-778-NOTREAL", connection_id=COMPANY)
    assert res.rel == "UNRESOLVED"
    assert res.supplyCode is None


def test_relationship_band_mapping():
    assert pie_service._rel_from_score(0.95) == "TECH"
    assert pie_service._rel_from_score(0.70) == "COMPAT"
    assert pie_service._rel_from_score(0.40) == "POSSIBLE"
    assert pie_service._rel_from_score(None) == "POSSIBLE"


def test_the_bands_come_from_commercial_policy_not_this_module():
    """Where "technically equivalent" stops is a policy number, not a constant.

    It decides whether a fuzzy candidate is auto-selected onto a customer quote
    under that label, so it belongs in the versioned thresholds with every other
    number that reaches a customer — and the fallback must read the same
    dataclass rather than restate the values, or the two drift silently.
    """
    from app.commercial.config import CommercialThresholds
    from app.pie_service import Bands

    t = CommercialThresholds()
    assert Bands.default() == Bands(tech=t.equivalence_tech_band,
                                    compat=t.equivalence_compat_band)


def test_a_stricter_policy_actually_narrows_what_counts_as_equivalent():
    from app.pie_service import Bands

    strict = Bands(tech=0.99, compat=0.95)
    # 0.90 is a technical equivalent under the default policy and only a
    # possibility under a stricter one. If this ever stops being true the bands
    # are being read from somewhere other than the policy.
    assert pie_service._rel_from_score(0.90) == "TECH"
    assert pie_service._rel_from_score(0.90, strict) == "POSSIBLE"
    assert pie_service._rel_from_score(0.96, strict) == "COMPAT"


def test_changing_a_band_changes_the_thresholds_version():
    """The point of moving them: a policy change is recorded, not silent."""
    import dataclasses
    from app.commercial.config import CommercialThresholds

    base = CommercialThresholds()
    moved = dataclasses.replace(base, equivalence_tech_band=0.90)
    assert base.version != moved.version


# ── resolving under a customer's identity ───────────────────────────────────
#
# The quote builder now tells the engine which real-world customer the RFQ came
# from, so a confirmed "this customer's code means MM# X" mapping can win over
# re-reading the text. These pin the two halves that matter: naming a customer
# must never turn an answer into an assertion, and it must never turn a working
# line into a dead one.

def test_naming_a_customer_proposes_rather_than_asserts():
    """The same MM# resolves EXACT unscoped and "confirm this" under a customer.

    Before the engine fix this returned nothing at all under a customer scope,
    because the resolver refused to consult the catalogue for a scoped source.
    A quote line that silently stopped resolving the moment we knew who sent it
    was the reason the context was never wired up.
    """
    plain = pie_service.resolve("2001174", connection_id=COMPANY)
    scoped = pie_service.resolve("2001174", "identity-abc", connection_id=COMPANY)

    assert plain.rel == "EXACT" and plain.supplyCode == "2001174"

    # Found, shown, and explicitly not auto-selected: nothing has confirmed that
    # *this customer's* 2001174 is the manufacturer's.
    assert scoped.rel == "AMBIGUOUS"
    assert scoped.supplyCode is None, "an unconfirmed identity must not be priced"
    assert [c.code for c in scoped.candidates] == ["2001174"]
    assert scoped.outcome == "NEEDS_REVIEW"
    assert any("confirm" in (c.reason or "").lower() for c in scoped.candidates)


def test_a_customer_scope_never_loses_a_requirement():
    # A requirement is not an identity, so the scope changes nothing about it.
    plain = pie_service.resolve("CNMG 120408 KCP25", connection_id=COMPANY)
    scoped = pie_service.resolve("CNMG 120408 KCP25", "identity-abc", connection_id=COMPANY)
    assert [c.code for c in scoped.candidates] == [c.code for c in plain.candidates]


def test_an_unknown_code_stays_unresolved_under_a_scope():
    # The fall-through consults the catalogue; it does not invent a match.
    res = pie_service.resolve("XZ-CUSTOM-778-NOTREAL", "identity-abc", connection_id=COMPANY)
    assert res.rel == "UNRESOLVED"
    assert res.supplyCode is None


# ── the decode on the authoritative path ────────────────────────────────────
def test_an_exact_identity_carries_its_decoded_geometry():
    """The 0.97-confidence path must describe the product at least as fully as
    the fuzzy one.

    It used not to. ``IdentityMatch.to_dict`` projects a match down to four
    fields, so geometry never crossed the boundary on an EXACT hit while the
    lower-confidence suggestion path carried it all along — a salesperson got
    *more* about a guess than about a certainty.
    """
    res = pie_service.resolve("2001174", connection_id=COMPANY)
    assert res.rel == "EXACT"
    attrs = res.candidates[0].attributes
    assert attrs, "an exact identity must carry the decode, not just a code"
    assert attrs.get("iso_shape") == "C"
    assert attrs.get("product_family") == "turning_insert"
    # Absent slots are omitted rather than nulled: a null key would claim the
    # engine looked and found nothing.
    assert all(v is not None for v in attrs.values())


def test_attributes_are_shaped_the_same_on_both_paths():
    """One product, described one way, however it was found."""
    from app.pie_service import ATTRIBUTE_FIELDS

    exact = pie_service.resolve("2001174", connection_id=COMPANY).candidates[0]
    assert set(exact.attributes) <= set(ATTRIBUTE_FIELDS)


def test_lookup_record_is_exact_and_never_guesses():
    assert pie_service.lookup_record("2001174", COMPANY)["record_id"] == "2001174"
    # Not a prefix, not a fuzzy neighbour, not an empty string.
    assert pie_service.lookup_record("200117", COMPANY) is None
    assert pie_service.lookup_record("XZ-NOTREAL-778", COMPANY) is None
    assert pie_service.lookup_record("", COMPANY) is None
    assert pie_service.lookup_record(None, COMPANY) is None


def test_a_cross_namespace_ambiguity_is_not_an_exact_record(monkeypatch):
    """A bare index lookup can return the store's structured ambiguity instead
    of a row — possible once a second manufacturer pack is indexed and one
    identifier exists in several namespaces. An ambiguity is short of an exact
    hit, so ``lookup_record`` must answer None rather than hand a resolution
    object to callers expecting a decoded catalogue row."""
    view = pie_service._view(COMPANY)
    assert view is not None, "this company's catalogue must be loaded for this test"
    index = view.index

    class _Ambiguity:  # duck-shape of the store's resolution — deliberately not a dict
        outcome = "AMBIGUOUS"

    monkeypatch.setattr(index, "lookup_material", lambda _key: _Ambiguity())
    assert pie_service.lookup_record("2001174", COMPANY) is None


def test_a_failed_pack_read_is_not_memoized(monkeypatch, tmp_path):
    """A pack fetched after boot must be seen on the next call: a memoized
    failure would keep refusing family edits until a restart, for a problem
    that has already been fixed. Only a successful read is cached."""
    import app.pie_service as ps

    real_pack = ps.settings.PIE_PACK
    monkeypatch.setattr(ps, "_families_memo", {})
    assert ps.pack_families(tmp_path / "nowhere") is None   # the honest answer…

    families = ps.pack_families(real_pack)     # …and not a remembered one
    assert families, "the pack became readable and the next call must see it"
    # The memo is per pack: a second company's pack is read on its own terms
    # rather than answered from the first one's vocabulary.
    assert ps.pack_families(tmp_path / "nowhere") is None


# --- an unverified comparison is not an equivalence -------------------------
#
# The engine gates on a field only when *both* sides specify it
# (equivalence/distance.py), so a request it cannot decode is gated out of
# nothing and every candidate scores near the ceiling. It stamps
# ``dimensionally_vacuous`` on exactly that case. Reading it is the fix; the
# defect was that nobody did.
#
# Reproduced before the fix: "6205 2RS C3 bearing" — a deep-groove ball bearing
# — returned carbide inserts and endmills at score 1.0, and on the MIXED path
# square and screw-on inserts came back labelled TECH at 0.96.

def _suggestion(code, combined, *, unverified, dims):
    return {"record_id": code, "description": f"desc {code}", "grade": None,
            "scores": {"combined": combined}, "attributes": {},
            "dimensions_compared": dims, "dimensionally_vacuous": unverified,
            "explanation": "family/shape only" if unverified else "geometry close"}


def test_a_vacuous_comparison_never_claims_a_technical_relationship():
    """Nothing was compared, so the score is a ceiling rather than a fit."""
    from app.pie_service import Bands

    cands = pie_service._candidates_from_suggestions(
        [_suggestion("a", 0.96, unverified=True, dims=0),
         _suggestion("b", 0.70, unverified=True, dims=0)],
        Bands.default())

    assert [c.rel for c in cands] == ["POSSIBLE", "POSSIBLE"], (
        "a comparison with no comparable dimension was reported as a technical "
        "equivalent")
    assert all(c.unverified for c in cands)
    assert all("could be compared" in c.reason for c in cands), (
        "the candidate carries the label but not the reason for it")


def test_the_same_scores_do_claim_one_when_something_was_compared():
    """The guard must cost nothing on a real comparison, or it is a downgrade
    of the whole engine rather than a fix."""
    from app.pie_service import Bands

    cands = pie_service._candidates_from_suggestions(
        [_suggestion("a", 0.96, unverified=False, dims=3),
         _suggestion("b", 0.70, unverified=False, dims=3)],
        Bands.default())

    assert [c.rel for c in cands] == ["TECH", "COMPAT"]
    assert not any(c.unverified for c in cands)


def test_a_vacuous_leader_is_never_auto_selected():
    """``_is_discriminating`` cannot catch this: 0.96 against 0.70 genuinely
    separates. It separates on a comparison containing no dimension."""
    from app.pie_service import Bands

    res = pie_service._map("6205 2RS C3 bearing", {
        "resolution": {"outcome": "AUTO_MATCH", "input_semantics": "REQUIREMENT",
                       "matches": []},
        "identity_role": "NONE",
        "suggestions": [_suggestion("a", 0.96, unverified=True, dims=0),
                        _suggestion("b", 0.70, unverified=True, dims=0)],
        "notes": [],
    }, Bands.default())

    assert res.supplyCode is None, (
        "a candidate nothing was compared against was auto-selected and priced")
    assert res.rel == "AMBIGUOUS"
    assert any("no dimension" in n.lower() for n in res.notes)
    assert not any("could not distinguish" in n for n in res.notes), (
        "reported as a tie, which it is not — the reader is told the wrong "
        "reason to look")


def test_a_real_leader_is_still_selected():
    from app.pie_service import Bands

    res = pie_service._map("CNMG 120404", {
        "resolution": {"outcome": "AUTO_MATCH", "input_semantics": "REQUIREMENT",
                       "matches": []},
        "identity_role": "NONE",
        "suggestions": [_suggestion("a", 0.96, unverified=False, dims=3),
                        _suggestion("b", 0.70, unverified=False, dims=3)],
        "notes": [],
    }, Bands.default())

    assert res.supplyCode == "a"
    assert res.rel == "TECH"


@pytest.mark.requires_pie
def test_a_bearing_is_not_a_carbide_insert():
    """The report's reproduction, against the real catalogue. The engine has no
    bearing pack, so it must decline rather than rank cutting tools."""
    res = pie_service.resolve("6205 2RS C3 bearing", connection_id=COMPANY)

    assert res.supplyCode is None
    assert not any(c.rel in ("TECH", "COMPAT") for c in res.candidates), (
        "an insert or an endmill was offered as technically equivalent to a "
        "deep-groove ball bearing")
    # Retrieval searched and found nothing near enough: the floor refused the
    # least-unlike endmill rather than offering it as "nearest".
    assert not any(c.retrieved for c in res.candidates)
    assert res.retrieval is not None and res.retrieval["offered"] == 0


def test_a_dimension_the_candidate_does_not_carry_also_counts_as_unverified():
    """The finer-grained half, and the one that survives a decorated master.

    ``dimensionally_vacuous`` is all-or-nothing: it fires only when *nothing*
    was comparable. But `distance.py` skips a dimension the candidate lacks
    rather than penalising it — right for the score, since missing data must
    not read as a mismatch — so a record silent on the one dimension the
    customer changed, while agreeing on two incidental ones, scored 1.0 and was
    neither flagged nor refused.

    Measured at 2.1% of scored candidates on today's homogeneous catalogue, and
    the rate rises with exactly what this programme adds: sparse attributes on
    a decorated master, and a second manufacturer's pack.
    """
    from app.pie_service import Bands

    partial = {
        "record_id": "a", "description": "A", "grade": None,
        "scores": {"combined": 0.96}, "attributes": {},
        "dimensions_compared": 2, "dimensionally_vacuous": False,
        "field_breakdown": [
            {"field": "edge_length_mm", "tier": "dimension", "status": "exact"},
            {"field": "thickness_mm", "tier": "dimension", "status": "exact"},
            # the one the request was actually about
            {"field": "corner_radius_mm", "tier": "dimension",
             "status": "candidate_absent"},
        ],
        "explanation": "two of three",
    }
    cands = pie_service._candidates_from_suggestions([partial], Bands.default())

    assert cands[0].unverified, (
        "a candidate silent on a dimension the request specified was treated "
        "as fully compared")
    assert cands[0].rel == "POSSIBLE"


def test_every_specified_dimension_compared_is_still_a_technical_equivalent():
    """The guard costs nothing where the comparison really was complete."""
    from app.pie_service import Bands

    complete = {
        "record_id": "a", "description": "A", "grade": None,
        "scores": {"combined": 0.96}, "attributes": {},
        "dimensions_compared": 3, "dimensionally_vacuous": False,
        "field_breakdown": [
            {"field": "edge_length_mm", "tier": "dimension", "status": "exact"},
            {"field": "corner_radius_mm", "tier": "dimension", "status": "near"},
            {"field": "iso_shape", "tier": "gate", "status": "match"},
        ],
        "explanation": "close",
    }
    cands = pie_service._candidates_from_suggestions([complete], Bands.default())

    assert not cands[0].unverified
    assert cands[0].rel == "TECH"


def test_a_payload_with_no_markers_at_all_is_taken_at_face_value():
    """Three keys are read and any one of them settles it. A payload carrying
    none is an engine this code cannot reason about, and inventing a verdict
    for it would be its own benign default."""
    from app.pie_service import Bands

    bare = {"record_id": "a", "description": "A", "grade": None,
            "scores": {"combined": 0.96}, "attributes": {}, "explanation": ""}
    assert not pie_service._candidates_from_suggestions(
        [bare], Bands.default())[0].unverified


# --- what "the ranking chose" means ------------------------------------------
#
# `_is_discriminating` used to compare combined scores. The score is the LAST
# of five components the engine orders by, so comparing it alone asks a
# different question — and gets a different answer in both directions.


def test_a_separation_the_score_alone_cannot_see_is_still_a_choice():
    """The direction that cost a correct answer.

    A variation request ("same as X but 1.2 radius") ranks the varied record
    first on geometry while its own reference scores *higher* on the combined
    number. `scores[0] > scores[1]` is False there, so the old rule abstained on
    a ranking that had chosen — and the product the customer asked for went
    unquoted.
    """
    from app.pie_service import Candidate

    varied = Candidate(code="a", desc="A", rel="TECH", grade=None, brand=None,
                       score=1.0, reason="", rank_tier=[-1.0, 0, -3, 0, -1.0])
    reference = Candidate(code="b", desc="B", rel="TECH", grade=None, brand=None,
                          score=1.0713, reason="",
                          rank_tier=[-0.92, 0, -2, 0, -1.0713])

    assert varied.score < reference.score
    assert pie_service._is_discriminating([varied, reference]) is True


def test_equal_tiers_are_a_refusal_however_the_scores_fall():
    from app.pie_service import Candidate

    tier = [-0.96, 1, 0, 0, -0.96]
    tied = [Candidate(code="a", desc="A", rel="TECH", grade=None, brand=None,
                      score=0.96, reason="", rank_tier=list(tier)),
            Candidate(code="b", desc="B", rel="TECH", grade=None, brand=None,
                      score=0.96, reason="", rank_tier=list(tier))]
    assert pie_service._is_discriminating(tied) is False


def test_a_candidate_without_a_tier_falls_back_to_the_older_rule():
    """The engine is loaded from ``PIE_PARSER_ROOT`` rather than vendored, so a
    payload predating ``rank_tier`` is a live possibility. The fallback errs
    towards abstention, which is the safe direction for a quote."""
    from app.pie_service import Candidate

    same = [Candidate(code="a", desc="A", rel="TECH", grade=None, brand=None,
                      score=1.0, reason=""),
            Candidate(code="b", desc="B", rel="TECH", grade=None, brand=None,
                      score=1.0, reason="")]
    assert pie_service._is_discriminating(same) is False
    # A tier on only one of the two is not a comparison either.
    half = [Candidate(code="a", desc="A", rel="TECH", grade=None, brand=None,
                      score=1.0, reason="", rank_tier=[-1.0, 0, -3, 0, -1.0]),
            Candidate(code="b", desc="B", rel="TECH", grade=None, brand=None,
                      score=1.0, reason="")]
    assert pie_service._is_discriminating(half) is False


def test_the_tier_survives_into_the_candidates_a_caller_reads():
    """It is carried on `Candidate` rather than recomputed, and the AMBIGUOUS
    re-projection must not drop it — a caller re-asking the question of those
    candidates has to get the same answer the refusal was based on."""
    from app.pie_service import Bands

    payload = {"record_id": "a", "description": "A", "grade": None,
               "scores": {"combined": 0.96}, "attributes": {}, "explanation": "",
               "rank_tier": [-0.96, 1, 0, 0, -0.96]}
    cand = pie_service._candidates_from_suggestions([payload], Bands.default())[0]
    assert cand.rank_tier == [-0.96, 1, 0, 0, -0.96]


# ── retrieval: the nearest descriptions as extra options, never the answer ───
#
# `app/retrieval` finds the catalogue records whose descriptions read most like
# the text, and `_map` hands each to the engine's own `compare_geometry`. What
# these pin is the line that keeps it a candidate *generator*: a retrieved
# record is appended after the ranking, is POSSIBLE whatever it scored, carries
# no score, is never the supply, and is dropped when the engine's gates reject
# it. The floor and the determinism of the search itself are in
# `tests/decision_platform/test_retrieval.py`.

_TURNING = {"record_id": "2001174", "description_raw": "CNMG 120408-49 - TN2000",
            "grade": "TN2000", "brand": "WIDIA", "product_family": "turning_insert",
            "iso_shape": "C", "edge_length_mm": 12, "corner_radius_mm": 0.8,
            "thickness_mm": 4.76}
_TURNING_2 = {**_TURNING, "record_id": "2559490",
              "description_raw": "CNMG 120408 - TN4000", "grade": "TN4000"}
_REAMER = {"record_id": "3668915", "description_raw": "RMS REAMER CNMG 120408 Ø 10.00",
           "grade": "KC6305", "product_family": "reamer", "cutting_dia_mm": 10}
_DRILL = {"record_id": "4149315", "description_raw": "SC DRILL 12mm/.4724/ 5xD COOLANT",
          "grade": "KC7325", "product_family": "solid_carbide_drill",
          "cutting_dia_mm": 12}


class _Index:
    """The two methods of pie-parser's ``AuthoritativeIndex`` that ``_map`` uses."""

    def __init__(self, records):
        self._by_id = {r["record_id"]: r for r in records}

    def lookup_material(self, key):
        return self._by_id.get(str(key))


def _view_with(records, retriever="build"):
    from pathlib import Path

    from app.pie_service import _View
    from app.retrieval import RetrievalIndex

    view = _View(path=Path("/nonexistent/products.jsonl"), version="v1",
                 index=_Index(records))
    view.retriever = RetrievalIndex.build(records) if retriever == "build" else retriever
    return view


def _requirement(suggestions, spec, outcome="AUTO_MATCH"):
    return {
        "resolution": {"outcome": outcome, "input_semantics": "REQUIREMENT",
                       "matches": []},
        "identity_role": "NONE",
        "understood_spec": {"engine_spec": spec},
        "suggestions": suggestions,
        "notes": [],
    }


def test_retrieved_records_are_appended_after_the_ranking_and_never_selected():
    from app.pie_service import Bands

    spec = {"product_family": "turning_insert", "iso_shape": "C",
            "edge_length_mm": 12, "corner_radius_mm": 0.8}
    res = pie_service._map(
        "CNMG 120408 TN2000",
        _requirement([_suggestion("a", 0.96, unverified=False, dims=3),
                      _suggestion("b", 0.70, unverified=False, dims=3)], spec),
        Bands.default(),
        view=_view_with([_TURNING, _TURNING_2, _REAMER, _DRILL]))

    # The engine's answer is exactly what it was without retrieval.
    assert res.supplyCode == "a" and res.rel == "TECH"
    assert [c.code for c in res.candidates[:2]] == ["a", "b"]
    retrieved = [c for c in res.candidates if c.retrieved]
    assert retrieved, "nothing was retrieved for a text that names two records"
    assert {c.code for c in retrieved} == {"2001174", "2559490"}
    assert all(c.rel == "POSSIBLE" and c.score is None for c in retrieved)
    assert all("not a ranked match" in c.reason for c in retrieved)
    assert not any(c.unverified for c in retrieved), (
        "the engine compared three dimensions; the record was verified")
    assert res.retrieval == {"model_id": "hashed-ngram/1", "searched": 4, "offered": 2,
                             "aliases_searched": 0, "aliases_offered": 0}


def test_a_record_the_engine_gates_out_is_never_offered_by_retrieval():
    """The reamer's description *contains* the insert code, so by text it is
    the nearest thing; the engine's family gate says it is not a turning
    insert, and the gate wins."""
    from app.pie_service import Bands

    spec = {"product_family": "turning_insert", "iso_shape": "C"}
    res = pie_service._map(
        "CNMG 120408 reamer", _requirement([], spec, outcome="UNRESOLVED"),
        Bands.default(), view=_view_with([_TURNING, _REAMER]))

    codes = {c.code for c in res.candidates}
    assert "3668915" not in codes
    assert "2001174" in codes


def test_with_nothing_ranked_retrieval_offers_options_but_no_answer():
    from app.pie_service import Bands

    res = pie_service._map(
        "12mm drill coolant", _requirement([], {}, outcome="UNRESOLVED"),
        Bands.default(), view=_view_with([_TURNING, _DRILL, _REAMER]))

    assert res.rel == "AMBIGUOUS" and res.supplyCode is None
    assert res.candidates and all(c.retrieved and c.rel == "POSSIBLE"
                                  for c in res.candidates)
    assert res.candidates[0].code == "4149315"
    assert any("could not rank" in n for n in res.notes), (
        "the reader is not told these are nearest descriptions, not a shortlist")
    # An empty spec compares nothing, and a comparison of nothing is unverified.
    assert all(c.unverified for c in res.candidates)


def test_a_record_the_engine_already_ranked_is_not_offered_twice():
    from app.pie_service import Bands

    spec = {"product_family": "turning_insert"}
    res = pie_service._map(
        "CNMG 120408 TN2000",
        _requirement([_suggestion("2001174", 0.9, unverified=False, dims=3)], spec),
        Bands.default(), view=_view_with([_TURNING, _TURNING_2]))

    assert [c.code for c in res.candidates].count("2001174") == 1
    assert not next(c for c in res.candidates if c.code == "2001174").retrieved


def test_no_index_means_no_retrieval_and_no_provenance():
    from app.pie_service import Bands

    res = pie_service._map(
        "CNMG 120408", _requirement([], {}, outcome="UNRESOLVED"),
        Bands.default(), view=_view_with([_TURNING], retriever=False))

    assert res.rel == "UNRESOLVED" and res.candidates == []
    assert res.retrieval is None, "absence must read as not searched, not as nothing near"


def test_a_retrieval_failure_leaves_the_engine_answer_unchanged():
    from app.pie_service import Bands

    class _Broken:
        stamp = None

        def search(self, *a, **k):
            raise RuntimeError("index corrupt")

    spec = {"product_family": "turning_insert"}
    result = _requirement([_suggestion("a", 0.96, unverified=False, dims=3),
                           _suggestion("b", 0.70, unverified=False, dims=3)], spec)
    with_retrieval = pie_service._map("CNMG 120408", result, Bands.default(),
                                      view=_view_with([_TURNING], retriever=_Broken()))
    without = pie_service._map("CNMG 120408", result, Bands.default())

    assert (with_retrieval.rel, with_retrieval.supplyCode,
            [c.code for c in with_retrieval.candidates]) == (
        without.rel, without.supplyCode, [c.code for c in without.candidates])
    assert with_retrieval.retrieval is None


def test_retrieval_can_be_switched_off(monkeypatch, tmp_path):
    from app.config import settings
    from app.pie_service import PieService

    monkeypatch.setattr(settings, "RETRIEVAL_TOP_K", 0)
    view = _view_with([_TURNING], retriever=None)
    assert PieService._load_retriever(view) is False


def test_a_series_named_in_words_is_surfaced_by_retrieval():
    """Against the real catalogue. The engine ranks every R=1.2 milling insert
    at the same score and cuts the list at TOP_N, so the three records that
    actually *say* VSM11 were never shown; by description they are the nearest
    of all. They are offered — beneath the ranking, as possibilities."""
    res = pie_service.resolve("VSM11 milling insert r1.2", connection_id=COMPANY)

    retrieved = [c for c in res.candidates if c.retrieved]
    assert retrieved, "retrieval offered nothing for a series name"
    assert any(c.desc.startswith("VSM11") for c in retrieved)
    assert all(c.rel == "POSSIBLE" for c in retrieved)
    assert res.supplyCode not in {c.code for c in retrieved}
    ranked = [c for c in res.candidates if not c.retrieved]
    assert res.candidates[:len(ranked)] == ranked, "a retrieved record was ranked"
    assert res.retrieval and res.retrieval["model_id"] == "hashed-ngram/1"


# ── aliases: a customer's confirmed codes, near-missed ──────────────────────
#
# The engine resolves an exact confirmed code authoritatively and retrieval
# never sees it. These are about the line that is *almost* that code — with a
# quantity after it, a hyphen dropped — which the engine cannot read as the
# code. The confirmed record is offered, through the same comparison and under
# the same refusals as any retrieved record, and only to the customer whose
# confirmation it was.

class _Store:
    """The two methods of ``OrgMappingStore`` the retrieval pass reads."""

    def __init__(self, rows, fingerprint="fp1"):
        self._rows = rows
        self._fp = fingerprint

    def aliases(self):
        return list(self._rows)

    def fingerprint(self):
        return self._fp


PITTI = "identity-pitti"
OTHER = "identity-other"


def test_a_near_miss_of_a_confirmed_code_offers_the_confirmed_record():
    from app.pie_service import Bands

    store = _Store([(PITTI, "PITTI-7781", "2001174")])
    res = pie_service._map(
        "PITTI 7781 x 10 pcs", _requirement([], {}, outcome="UNRESOLVED"),
        Bands.default(), view=_view_with([_TURNING, _TURNING_2, _DRILL]),
        customer_scope=PITTI, mapping_store=store)

    assert res.rel == "AMBIGUOUS" and res.supplyCode is None
    first = res.candidates[0]
    assert first.code == "2001174" and first.retrieved and first.alias == "PITTI-7781"
    assert first.rel == "POSSIBLE" and first.score is None
    assert "this customer confirmed" in first.reason
    assert res.retrieval["aliases_searched"] == 1
    assert res.retrieval["aliases_offered"] == 1
    # Found through the code, reported once, as the confirmation.
    assert [c.code for c in res.candidates].count("2001174") == 1


def test_another_customers_code_is_another_customers_part():
    from app.pie_service import Bands

    store = _Store([(PITTI, "PITTI-7781", "2001174")])
    for scope in (OTHER, None):
        res = pie_service._map(
            "PITTI 7781 x 10 pcs", _requirement([], {}, outcome="UNRESOLVED"),
            Bands.default(), view=_view_with([_TURNING, _DRILL]),
            customer_scope=scope, mapping_store=store)
        assert not any(c.alias for c in res.candidates), scope
        assert res.retrieval["aliases_offered"] == 0
        assert res.retrieval["aliases_searched"] == 0, (
            "an unscoped line, or another customer, must search no aliases")


def test_a_confirmed_record_the_engine_already_ranked_is_not_offered_again():
    from app.pie_service import Bands

    store = _Store([(PITTI, "PITTI-7781", "2001174")])
    res = pie_service._map(
        "PITTI 7781",
        _requirement([_suggestion("2001174", 0.9, unverified=False, dims=3)],
                     {"product_family": "turning_insert"}),
        Bands.default(), view=_view_with([_TURNING, _TURNING_2]),
        customer_scope=PITTI, mapping_store=store)

    assert [c.code for c in res.candidates].count("2001174") == 1
    assert not next(c for c in res.candidates if c.code == "2001174").retrieved


def test_a_confirmed_code_for_a_record_not_in_this_catalogue_offers_nothing():
    """The mapping is the organization's; the catalogue is one company's. A
    confirmed record another company's item master carries is not this
    company's to quote."""
    from app.pie_service import Bands

    store = _Store([(PITTI, "PITTI-7781", "9999999")])
    res = pie_service._map(
        "PITTI 7781 x 10", _requirement([], {}, outcome="UNRESOLVED"),
        Bands.default(), view=_view_with([_TURNING]),
        customer_scope=PITTI, mapping_store=store)

    assert not any(c.code == "9999999" for c in res.candidates)


def test_the_alias_index_is_memoised_by_the_store_fingerprint(monkeypatch):
    from app import retrieval
    from app.pie_service import Bands

    builds = []
    real = retrieval.AliasIndex

    def counting(rows, *a, **k):
        builds.append(1)
        return real(rows, *a, **k)

    monkeypatch.setattr(retrieval, "AliasIndex", counting)
    pie_service._alias_indexes.clear()
    same = _Store([(PITTI, "PITTI-7781", "2001174")], fingerprint="fp-same")
    for _ in range(3):
        pie_service._map("PITTI 7781 x 10", _requirement([], {}, outcome="UNRESOLVED"),
                         Bands.default(), view=_view_with([_TURNING]),
                         customer_scope=PITTI, mapping_store=same)
    assert len(builds) == 1, "one store, one index — not one per line"

    changed = _Store([(PITTI, "PITTI-7781", "2559490")], fingerprint="fp-changed")
    res = pie_service._map("PITTI 7781 x 10", _requirement([], {}, outcome="UNRESOLVED"),
                           Bands.default(), view=_view_with([_TURNING, _TURNING_2]),
                           customer_scope=PITTI, mapping_store=changed)
    assert len(builds) == 2
    assert res.candidates[0].code == "2559490", "a correction must be seen at once"


def test_a_store_without_aliases_is_simply_not_searched():
    from app.pie_service import Bands

    class _Old:
        def lookup(self, _identifier):
            return None

    res = pie_service._map(
        "PITTI 7781 x 10", _requirement([], {}, outcome="UNRESOLVED"),
        Bands.default(), view=_view_with([_TURNING]),
        customer_scope=PITTI, mapping_store=_Old())
    assert res.retrieval["aliases_searched"] == 0


def test_a_confirmed_code_near_missed_on_a_quote_is_offered_to_that_customer():
    """End to end: a real confirmation, the real store, the real engine.

    The exact code resolves authoritatively and retrieval never runs; the
    near miss is answered by nothing the engine can rank, and the confirmed
    record is offered — to this customer, and not to another."""
    from sqlalchemy.orm import sessionmaker

    import dbsupport
    from app.identity import service as identity_service
    from app.identity.mapping_store import OrgMappingStore

    session = sessionmaker(bind=dbsupport.fresh_engine())()
    try:
        identity_service.confirm_code_mapping(
            session, "org_test", identity_id=PITTI, code="PITTI-7781",
            target_record_id="2001174", source_ref="quote q1 line l1", user_id="u1")
        store = OrgMappingStore(session, "org_test")
    finally:
        session.close()

    exact = pie_service.resolve("PITTI-7781", customer_scope=PITTI,
                                mapping_store=store, connection_id=COMPANY)
    assert exact.rel == "EXACT" and exact.supplyCode == "2001174"
    assert exact.retrieval is None

    near = pie_service.resolve("PITTI 7781 x 10 pcs urgent", customer_scope=PITTI,
                               mapping_store=store, connection_id=COMPANY)
    assert near.supplyCode is None
    offered = [c for c in near.candidates if c.alias == "PITTI-7781"]
    assert offered and offered[0].code == "2001174" and offered[0].rel == "POSSIBLE"
    assert near.retrieval["aliases_offered"] == 1

    other = pie_service.resolve("PITTI 7781 x 10 pcs urgent", customer_scope=OTHER,
                                mapping_store=store, connection_id=COMPANY)
    assert not any(c.alias for c in other.candidates)


def test_a_confirmed_code_survives_a_gate_the_engine_read_off_the_code_itself():
    """Against the real catalogue the engine reads "PITTI" as an ISO P-shape,
    and the confirmed insert is a C-shape — so the same gate that rightly
    drops a description neighbour would drop the one record the customer
    meant. A confirmation is stronger evidence than a shape guessed from the
    code's own letters: the record is kept, unverified, with the engine's
    objection stated."""
    from app.pie_service import Bands

    store = _Store([(PITTI, "PITTI-7781", "2001174")])
    spec = {"iso_shape": "P", "product_family": "turning_insert"}
    res = pie_service._map(
        "PITTI 7781 x 10 pcs", _requirement([], spec, outcome="UNRESOLVED"),
        Bands.default(), view=_view_with([_TURNING, _TURNING_2]),
        customer_scope=PITTI, mapping_store=store)

    offered = [c for c in res.candidates if c.alias == "PITTI-7781"]
    assert offered and offered[0].code == "2001174"
    assert offered[0].unverified and offered[0].rel == "POSSIBLE"
    assert "different geometry" in offered[0].reason
    assert "iso_shape" in offered[0].reason
    # The description neighbour with the same shape mismatch is still dropped.
    assert not any(c.code == "2559490" for c in res.candidates)
