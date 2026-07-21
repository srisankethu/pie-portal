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


def test_requirement_resolves_to_ranked_candidates():
    res = pie_service.resolve("CNMG 120408 KCP25")
    assert res.semantics == "REQUIREMENT"
    assert res.supplyCode is not None
    assert res.candidates, "a requirement should surface ranked supply candidates"
    # Top candidate relationship is derived from the equivalence score band.
    assert res.candidates[0].rel in ("TECH", "COMPAT", "POSSIBLE")
    assert res.candidates[0].score is not None


def test_unknown_code_is_unresolved():
    res = pie_service.resolve("XZ-CUSTOM-778-NOTREAL")
    assert res.rel == "UNRESOLVED"
    assert res.supplyCode is None


def test_relationship_band_mapping():
    assert pie_service._rel_from_score(0.95) == "TECH"
    assert pie_service._rel_from_score(0.70) == "COMPAT"
    assert pie_service._rel_from_score(0.40) == "POSSIBLE"
    assert pie_service._rel_from_score(None) == "POSSIBLE"
