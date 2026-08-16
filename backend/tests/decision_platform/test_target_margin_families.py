"""What binds a target-margin key to a family that exists.

``target_margin_by_family`` keys are the PIE pack's family vocabulary, and for
a long time nothing bound them: ``config.target_margin`` matches exactly and
falls through to ``target_margin_default`` for a name it does not recognise, so
a pack rename would silently reprice every line in that family at the blended
default — the exact outcome the per-family map exists to prevent.
``test_floor_families.py`` pins the counterpart claim for ``m_floor_by_family``;
this file is the counterpart that was never written, plus the contract of the
one validator both vocabulary-bound maps now run through.

The claims worth pinning:

* every family the *default* policy names is one the loaded pack declares —
  the binding a rename breaks first;
* the floor table's keys pass the **same** validator against the floor-family
  vocabulary, so there is one mechanism and not two siblings to drift;
* a refusal names the bad key and the valid vocabulary, because "invalid"
  without the valid answers is a puzzle, not an error a person can act on.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.commercial import floor_families as ff
from app.commercial.config import CommercialThresholds
from app.commercial.policy import PolicyError, require_known_families

AS_OF = date(2026, 8, 7)


# ── the binding to the pack ──────────────────────────────────────────────────
@pytest.mark.requires_pie
def test_every_configured_target_margin_family_is_one_the_loaded_pack_declares():
    """A key the pack does not declare never matches a parsed line, so its
    target silently becomes the default. The strings in the policy and the
    strings in the pack manifest are the same strings, or the map buys nothing."""
    from app.pie_service import pack_families

    vocabulary = pack_families()
    assert vocabulary, "the pack is present, so its vocabulary must be readable"
    for family, _ in CommercialThresholds().target_margin_by_family:
        assert family in vocabulary, (
            f"{family!r} is not a family the loaded PIE pack declares — "
            f"target_margin() would fall through to the default for it")


@pytest.mark.requires_pie
def test_the_default_policy_passes_its_own_write_path_validator():
    """The same binding, through the same call ``save_for_org`` makes — so the
    default policy could always be re-saved as-is."""
    from app.commercial.policy import validate

    validate(CommercialThresholds())


# ── one validator, both vocabulary-bound maps ────────────────────────────────
def test_the_floor_table_passes_the_same_validator_not_a_sibling():
    """``m_floor_by_family`` is the other map whose keys are somebody else's
    vocabulary — ``floor_families``' plus the published ``default``. It runs
    through the identical function, so the two checks cannot drift apart."""
    from incentive_engine.config import load_config

    table = load_config(as_of=AS_OF).get("floor", "m_floor_by_family")
    require_known_families(
        "m_floor_by_family", table.keys(), (*ff.FAMILIES, ff.DEFAULT),
        source="the floor families")


def test_a_refusal_names_the_bad_key_and_the_valid_vocabulary():
    """The mechanism inherited from ``incentive_engine.floor.UnknownFamily``:
    the error carries what was wrong and what would have been right."""
    with pytest.raises(PolicyError) as e:
        require_known_families(
            "target_margin_by_family", ["widgets", "reamer"],
            ("reamer", "drill_tip"), source="the pack vocabulary")
    message = str(e.value)
    assert "widgets" in message
    assert "drill_tip" in message and "reamer" in message
    # And the valid key was not reported as a defect.
    assert "'reamer'" not in message.split("Valid families")[0]
