"""The pie-parser integration: real resolution mapped to portal relationships."""
from __future__ import annotations

from app.pie_service import pie_service


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
