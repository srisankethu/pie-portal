"""The confidence configuration is what the input-completeness measurement measures against.

``docs/concepts/13-confidence-and-input-completeness.md`` answers a question about
the *input* — whether ``row_confidence 0.00`` over the Zoho item master is the
engine failing or the engine abstaining — by re-running one corpus through one
pipeline twice with the grade column suppressed. That comparison is only
meaningful while both sides are scored by the same rules, and it is only
*honest* while those rules are the ones that produced the finding it revisits.

So this pins the scoring surface rather than a result. It is here because the
tempting repair is a real one and would look like an improvement: raise a
threshold, or drop ``grade`` from ``critical_slots``, and every one of those
6,717 rows scores ~0.97 instead of 0.00, the measured "gap" collapses, and the
catalogue appears to decode the item master. Nothing about the master would have
changed. That is exactly `CLAUDE.md` §1's "absence of evidence read as a pass",
and the number it produces is worse than no number, because it carries a
provenance stamp and a confidence field and reads as evidence.

The pins are deliberately narrow — the weights, the penalties, the critical-slot
set, and the validator inventory. They are not a checksum over the whole pack:
the pack is meant to grow families and lookups without asking permission here,
and a test that goes red on every legitimate pack edit is a test people learn to
update without reading. What must not move quietly is the arithmetic that turns a
parse into a number, and the validators that feed it.

If a future change *should* move one of these, move it deliberately: change the
value here in the same commit, and say in the message which measurement is being
invalidated. That is the whole purpose of the file.
"""
from __future__ import annotations

import dataclasses

import pytest

pytestmark = pytest.mark.requires_pie


#: The published base score per provenance kind (``engine/quality.py``).
#: ``ABSENT`` at 0.0 is the pin that matters most: it is the term that vetoes a
#: row lacking a critical field, and raising it is how "abstained" would silently
#: become "vouched for".
EXPECTED_BASE_SCORES = {
    "EXPLICIT_COLUMN": 0.99,
    "GRAMMAR_EXACT": 0.97,
    "LOOKUP_CONFIRMED": 0.95,
    "LOOKUP_STRONG": 0.85,
    "LOOKUP_INFERRED": 0.70,
    "DERIVED": 0.90,
    "OBSERVED_TOKEN": 0.50,
    "REPAIRED": 0.80,
    "RESCUED": 0.60,
    "REJECTED": 0.0,
    "ABSENT": 0.0,
}

#: (rule_id, kind, severity) for every validator the pack declares. Order is the
#: pack's declared order, which ``ValidationSuite`` preserves and which decides
#: the order issues appear in an emitted record.
EXPECTED_VALIDATORS = [
    ("KMT-VAL-011", "RequiredFieldValidator", "ERROR"),
    ("KMT-VAL-012", "RequiredFieldValidator", "ERROR"),
    ("KMT-VAL-009", "RequiredFieldValidator", "ERROR"),
    ("KMT-VAL-010", "RequiredFieldValidator", "ERROR"),
    ("KMT-VAL-008", "RequiredFieldValidator", "ERROR"),
    ("KMT-VAL-007", "RangeValidator", "WARN"),
    ("KMT-VAL-006", "RequiredFieldValidator", "ERROR"),
    ("KMT-VAL-005", "RequiredFieldValidator", "ERROR"),
    ("KMT-VAL-001", "DualUnitValidator", "WARN"),
    ("KMT-VAL-002", "AllowedValuesValidator", "WARN"),
    ("KMT-VAL-003", "SignatureValidator", "WARN"),
    ("KMT-VAL-004", "RequiredFieldValidator", "ERROR"),
]


def _pack():
    from app.config import settings
    from engine.pack import load_pack
    return load_pack(settings.PIE_PACK)


def test_the_provenance_base_scores_are_unchanged() -> None:
    from engine.quality import DEFAULT_BASE_SCORES

    assert dict(DEFAULT_BASE_SCORES) == EXPECTED_BASE_SCORES


def test_the_confidence_penalties_and_critical_slots_are_unchanged() -> None:
    from engine.quality import ConfidenceConfig

    cfg = ConfidenceConfig()
    assert cfg.truncation_penalty == 0.85
    assert cfg.repair_penalty == 0.90
    assert cfg.warn_penalty == 0.90
    # The single critical slot. Dropping ``grade`` from this tuple is the
    # one-token edit that would turn every abstention in the measurement into a
    # 0.97, so it is named rather than counted.
    assert cfg.critical_slots == ("grade",)
    assert dict(cfg.base_scores) == EXPECTED_BASE_SCORES


def test_an_absent_critical_field_still_vetoes_the_row_score() -> None:
    """The behaviour the constants above exist to produce, asserted directly.

    Pinning the numbers is not enough on its own: ``score`` could stop consulting
    ``critical_slots``, or take a mean instead of a minimum, and every constant
    would still read as expected. So this drives the scorer — a row whose family
    routed cleanly but whose grade is ABSENT must score 0.0, not 0.97.
    """
    from engine.model import FieldValue, Provenance
    from engine.quality import ConfidenceConfig, ConfidenceScorer

    class _Ctx:
        def __init__(self) -> None:
            self.fields: dict = {}
            self.flags: set = set()
            self.validations: list = []
            self.family = "turning_insert"
            self.row_confidence = None

    ctx = _Ctx()
    ctx.fields["grade"] = FieldValue(slot="grade", value=None,
                                     provenance=Provenance.ABSENT)
    ctx.fields["iso_shape"] = FieldValue(slot="iso_shape", value="C",
                                         provenance=Provenance.GRAMMAR_EXACT)
    ConfidenceScorer(ConfidenceConfig()).score(ctx)  # type: ignore[arg-type]

    assert ctx.row_confidence == 0.0
    # ...and the well-read slot keeps its own confidence. An abstention at the
    # row level is not a claim that nothing was read, which is the distinction
    # the measurement rests on.
    assert ctx.fields["iso_shape"].confidence == 0.97


def test_the_pack_validator_inventory_is_unchanged() -> None:
    suite = _pack().validation
    got = [(v.rule_id, type(v).__name__, v.severity.value)
           for v in suite._validators]  # noqa: SLF001 — the inventory is the assertion
    assert got == EXPECTED_VALIDATORS


def test_the_portal_scores_the_catalogue_with_the_default_configuration() -> None:
    """The pins above are worth nothing if the portal passes its own config.

    ``app.catalog.build_catalog`` constructs a bare ``RunProfile()``. If that
    ever grows a tuned ``ConfidenceConfig``, the catalogue in the database stops
    being scored by the rules this file pins, and the measurement stops
    describing the product.
    """
    import inspect

    from engine.pipeline import RunProfile
    from engine.quality import ConfidenceConfig

    from app import catalog

    assert RunProfile().confidence == ConfidenceConfig()
    source = inspect.getsource(catalog.build_catalog)
    assert "RunProfile()" in source, "the portal must build with the default profile"
    assert "ConfidenceConfig" not in source, (
        "the portal must not supply its own confidence configuration"
    )


def test_the_config_is_frozen_so_a_caller_cannot_retune_it_in_place() -> None:
    from engine.quality import ConfidenceConfig

    assert dataclasses.is_dataclass(ConfidenceConfig)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ConfidenceConfig().truncation_penalty = 0.99  # type: ignore[misc]
