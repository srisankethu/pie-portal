"""A product named in order to be changed is a reference, never the supply.

The defect this pins, reproduced against the real engine before it was fixed::

    "same as 2001174 but 0.4 corner radius"
      -> rel=EXACT, supplyCode=2001174     # the 0.8 mm insert

The customer asked for 0.4. The line auto-selected the 0.8, labelled it an
exact match, and priced it. Nothing in the response said the number had been
read and discarded.

The cause was a reasonable reading of an ambiguous contract. On two paths the
engine resolves an identity *exactly* and that identity is still not the
answer:

* **MIXED** — "same as X but Y". X is a reference; the answer is derived from
  it and ranked.
* **Unsellable namespace** — an exact hit on a product this business does not
  sell. Answering "here is that product" would be worse than not resolving.

On both, ``resolution.outcome`` is a truthful ``AUTO_MATCH`` over a truthful
``AUTHORITATIVE`` match, so a consumer reading only those two fields concludes
it has the product. ``identity_role`` is the engine saying which it is, and
``_map`` must read it rather than inferring an answer from a match.

The fallbacks matter as much as the field. An engine that predates
``identity_role`` must not thereby be read as claiming ANSWER — absence is not
a pass (CLAUDE.md §1), so a MIXED input or a derived ``effective_requirement``
is treated as a reference on its own.

Sibling invariant: ``tests/test_equivalence_not_transitive.py``, which keeps
hop one exact. This one keeps hop one from being mistaken for the destination.
"""
from __future__ import annotations

import pytest

import piesupport
from app.pie_service import Bands, pie_service

#: The company these tests resolve for — a catalogue belongs to a company now,
#: so a test that wants a real answer names one.
COMPANY = piesupport.company_id("test-reference")

#: Every test here needs the engine, because the fixture below is autouse and
#: decodes the shipped corpus. Stated once for the module rather than on each
#: test: the requirement belongs to the fixture, and marking it per test is how
#: ten of these fifteen came to be unmarked and to ERROR — rather than skip —
#: on a checkout without the private submodule. The `pie-contract` job selects
#: on this marker, so the module is still run for real against the engine.
pytestmark = pytest.mark.requires_pie


@pytest.fixture(autouse=True)
def _company_catalogue():
    piesupport.give_company_a_catalogue(COMPANY)

_REF = {
    "record_id": "2001174",
    "description": "CNMG 120408-49 - TN2000",
    "certainty": "AUTHORITATIVE",
    "grade": "TN2000",
    "brand": "Kennametal",
}


def _result(**over):
    """An engine payload shaped like the MIXED path's, overridable per case."""
    base = {
        "resolution": {
            "outcome": "AUTO_MATCH",
            "input_semantics": "MIXED",
            "matches": [dict(_REF)],
        },
        "identity_role": "REFERENCE",
        "effective_requirement": {"corner_radius_mm": 0.4},
        "suggestions": [
            {"record_id": "9000001", "description": "CNMG 120404 - TN2000",
             "grade": "TN2000", "scores": {"combined": 0.94},
             "attributes": {"corner_radius_mm": 0.4}, "dimensions_compared": 3,
             "dimensionally_vacuous": False, "explanation": "close geometry"},
            {"record_id": "9000002", "description": "CNMG 120402 - TN2000",
             "grade": "TN2000", "scores": {"combined": 0.71},
             "attributes": {"corner_radius_mm": 0.2}, "dimensions_compared": 3,
             "dimensionally_vacuous": False, "explanation": "further"},
        ],
        "notes": [],
    }
    base.update(over)
    return base


def _map(result):
    return pie_service._map("same as 2001174 but 0.4 corner radius",
                            result, Bands.default())


def test_the_reference_is_never_the_supply():
    """The whole defect, in one assertion."""
    res = _map(_result())
    assert res.supplyCode != "2001174", (
        "the product the customer asked to change was auto-selected as the "
        "product to supply")
    assert res.rel != "EXACT", (
        "an exact identity on the reference was reported as an exact match for "
        "a requirement it does not meet")


def test_the_derived_requirement_is_what_gets_answered():
    """Abstaining is not the fix — the *variation* must still be matched."""
    res = _map(_result())
    codes = [c.code for c in res.candidates]
    assert "9000001" in codes, "the derived candidates were dropped"
    assert res.supplyCode == "9000001", (
        "the ranking separates a winner on the derived requirement, so it "
        "should be selected; only the reference is barred from selection")


def test_the_reference_stays_offerable_but_carries_its_role():
    """Hiding it would be its own defect: a person may decide to offer it."""
    res = _map(_result())
    ref = next((c for c in res.candidates if c.code == "2001174"), None)
    assert ref is not None, "the named product vanished from the answer entirely"
    assert ref.rel == "POSSIBLE", (
        "the reference must not claim a relationship to a requirement defined "
        "by changing it")
    assert "reference" in ref.reason.lower()
    assert res.candidates[-1] is ref, (
        "the reference must rank last — it answers no part of the requirement")


def test_a_reference_never_becomes_the_top_of_the_ranking():
    """It carries no score, so it cannot win a comparison it never entered."""
    res = _map(_result())
    ref = next(c for c in res.candidates if c.code == "2001174")
    assert ref.score is None


def test_an_unsellable_reference_is_treated_the_same_way():
    """Branch (b') resolves exactly too, and is a reference for a different
    reason: the record is right and the business does not sell it."""
    res = _map(_result(
        resolution={"outcome": "AUTO_MATCH", "input_semantics": "IDENTITY",
                    "matches": [dict(_REF)]},
        reference_namespace="competitor",
        sellable_namespaces=["kennametal_widia"]))
    assert res.supplyCode != "2001174"
    assert res.rel != "EXACT"


# --- absence must not read as ANSWER ----------------------------------------

def test_a_payload_with_no_role_is_still_a_reference_when_it_is_one():
    """An engine that predates ``identity_role`` must not be read as claiming
    the identity is the answer. Two independent markers each suffice."""
    by_semantics = _map(_result(identity_role=None))
    assert by_semantics.supplyCode != "2001174"
    assert by_semantics.rel != "EXACT"

    by_effective_requirement = _map(_result(
        identity_role=None,
        resolution={"outcome": "AUTO_MATCH", "input_semantics": "IDENTITY",
                    "matches": [dict(_REF)]}))
    assert by_effective_requirement.supplyCode != "2001174"


def test_a_genuine_exact_identity_is_untouched():
    """The guard must not cost the path it does not apply to: a pure identity
    with no reference marker still answers directly."""
    res = pie_service._map("2001174", {
        "resolution": {"outcome": "AUTO_MATCH", "input_semantics": "IDENTITY",
                       "matches": [dict(_REF)]},
        "identity_role": "ANSWER",
        "suggestions": [],
        "notes": [],
    }, Bands.default())
    assert res.rel == "EXACT"
    assert res.supplyCode == "2001174"


def test_end_to_end_against_the_real_engine():
    """The reproduction from the report, run against the real catalogue."""
    res = pie_service.resolve("same as 2001174 but 0.4 corner radius",
                             connection_id=COMPANY)
    assert res.supplyCode != "2001174"
    assert res.rel != "EXACT"
    assert any("REFERENCE" in n for n in res.notes), (
        "nothing told the reader that the product they named was not the answer")


# --- the hole the first version of this fix left -----------------------------
#
# The reference is normally IN `suggestions`, and usually at the top: the
# effective requirement is derived from the reference's own facts, so the
# reference matches it better than anything else does. The first fix appended
# the labelled reference only when no candidate already carried that code —
# which meant that in the common case it appended nothing, the *ranked* copy
# stayed at position 0, and branch (3) auto-selected it with a score band.
#
# "same as 6092627 but 3 flute" came back TECH on 6092627, a 2-flute endmill,
# through the very branch added to stop exactly that. Measured against the real
# catalogue, 21 of 120 sampled variation requests auto-selected their own
# reference.
#
# The fixture above could not catch it because its suggestions never contained
# the reference. This one does.

def _result_with_reference_ranked_first(**over):
    r = _result(**over)
    r["suggestions"] = [
        {"record_id": "2001174", "description": "CNMG 120408-49 - TN2000",
         "grade": "TN2000", "scores": {"combined": 1.13},
         "attributes": {"corner_radius_mm": 0.8}, "dimensions_compared": 4,
         "dimensionally_vacuous": False, "explanation": "matches itself"},
        *r["suggestions"],
    ]
    return r


def test_the_reference_is_not_selected_when_it_ranks_itself_first():
    res = _map(_result_with_reference_ranked_first())

    assert res.supplyCode != "2001174", (
        "the reference out-ranked the alternatives on a requirement derived "
        "from its own facts, and was auto-selected — the exact defect this "
        "module exists to prevent, reintroduced by the ranking")
    # The *line* may well be TECH: 9000001 really is a technical equivalent of
    # the derived requirement. What must never carry a band is the reference.
    ref = next(c for c in res.candidates if c.code == "2001174")
    assert ref.rel == "POSSIBLE"


def test_the_reference_appears_exactly_once_and_last():
    res = _map(_result_with_reference_ranked_first())
    at = [i for i, c in enumerate(res.candidates) if c.code == "2001174"]

    assert len(at) == 1, "the reference was listed twice — ranked and labelled"
    assert at[0] == len(res.candidates) - 1
    assert res.candidates[at[0]].score is None, (
        "the ranked copy survived instead of the labelled one, so the score "
        "that put it first is still there to put it first again")


def test_the_derived_winner_is_still_selected_around_it():
    """Removing the reference must not cost the answer beneath it."""
    res = _map(_result_with_reference_ranked_first())
    assert res.supplyCode == "9000001"


# --- phrasings without a reference word -------------------------------------
#
# `detect_mixed` recognises "same/like/similar ... but/instead/->". A customer
# who writes the same request without one of those reference words was
# classified REQUIREMENT, and the exact identifier inside the text was answered
# as though it were the whole input:
#
#     "2001174 but 0.4 corner radius"  ->  EXACT, 2001174   (the 0.8 mm insert)
#
# The engine now refuses to answer an exact identity unless the identifier
# accounts for the whole input. See pie-parser tests/test_identity_role.py for
# the classification half; this is the portal half.

@pytest.mark.parametrize("text", [
    "2001174 but 0.4 corner radius",
    "2001174 with 0.4 corner radius",
    "need 2001174 in 0.4 mm corner radius",
    "like 2001174 in 0.4",
    "same as 2001174, 0.4 corner",
    "instead of 2001174 give 0.4",
    "2001174 -> 0.4",
    "2001174 but r0.4",
    "2001174 uncoated",
    "2001174 for stainless",
])
def test_a_variation_in_any_phrasing_never_quotes_the_unvaried_product(text):
    res = pie_service.resolve(text, connection_id=COMPANY)
    assert res.supplyCode != "2001174", (
        f"{text!r} auto-selected the product it asked to change")


@pytest.mark.parametrize("text", [
    "2001174", "MM# 2001174", "2001174 x 10 nos", "2001174 qty 10",
    "10 nos 2001174",
])
def test_a_code_with_a_quantity_still_resolves_exactly(text):
    """The guard keys off "the identifier accounts for the input", so a count
    must not read as residue. Ordering ten of a known part is the most ordinary
    input there is, and losing it would be a worse defect than the one fixed."""
    res = pie_service.resolve(text, connection_id=COMPANY)
    assert res.rel == "EXACT"
    assert res.supplyCode == "2001174"


# --- the confirmation gate --------------------------------------------------

def test_a_variation_is_never_offered_as_a_confirmable_identity():
    """A confirmation files "this customer's code means this product" forever.

    For a scoped customer the reference is demoted to CANDIDATE/NEEDS_REVIEW and
    reached the proposal branch, offering to record that the whole sentence
    "same as 2001174 but 0.4 corner radius" means the unvaried 2001174. It could
    never fire at resolution time — the mapping is keyed on the sentence and
    lookups are keyed on identifier tokens — so it was a permanent, audited,
    wrong assertion rather than a wrong answer. Still not a thing anyone should
    be able to file.
    """
    from app.store import _identity_candidate

    for text in ("same as 2001174 but 0.4 corner radius",
                 "2001174 but 0.4 corner radius"):
        res = pie_service.resolve(text, "cust-42", connection_id=COMPANY)
        assert _identity_candidate(res) is None, (
            f"{text!r} offered a confirmable identity for a product it asked "
            f"to change")


def test_a_bare_code_is_still_offered_for_confirmation():
    """The gate must keep doing its job — this is what it is *for*."""
    from app.store import _identity_candidate

    assert _identity_candidate(pie_service.resolve("2001174", "cust-42",
                                            connection_id=COMPANY)) == "2001174"
    assert _identity_candidate(
        pie_service.resolve("2001174 x 10 nos", "cust-42", connection_id=COMPANY)) == "2001174"
