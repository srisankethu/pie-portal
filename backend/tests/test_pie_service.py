"""The pie-parser integration: real resolution mapped to portal relationships."""
from __future__ import annotations

import pytest

from app.pie_service import pie_service

# Every test here resolves through the real engine against the real catalogue.
pytestmark = pytest.mark.requires_pie


def test_exact_identity_resolves_to_exact():
    # A real MM# from the Kennametal/WIDIA corpus resolves authoritatively.
    res = pie_service.resolve("2001174")
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
    res = pie_service.resolve("CNMG 120408 KCP25")
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
    res = pie_service.resolve("XZ-CUSTOM-778-NOTREAL")
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
    plain = pie_service.resolve("2001174")
    scoped = pie_service.resolve("2001174", "identity-abc")

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
    plain = pie_service.resolve("CNMG 120408 KCP25")
    scoped = pie_service.resolve("CNMG 120408 KCP25", "identity-abc")
    assert [c.code for c in scoped.candidates] == [c.code for c in plain.candidates]


def test_an_unknown_code_stays_unresolved_under_a_scope():
    # The fall-through consults the catalogue; it does not invent a match.
    res = pie_service.resolve("XZ-CUSTOM-778-NOTREAL", "identity-abc")
    assert res.rel == "UNRESOLVED"
    assert res.supplyCode is None
