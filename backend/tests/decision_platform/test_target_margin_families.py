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
    """The same binding ``save_for_org`` runs for an edit that touches the
    family map — so the default policy could always be re-saved as-is. The
    binding deliberately does NOT live in ``validate``: there it would run on
    the whole merged policy for every save, letting one stale stored key block
    an owner's edit of an unrelated field."""
    from app.pie_service import pack_families

    vocabulary = pack_families()
    assert vocabulary, "the pack is present, so its vocabulary must be readable"
    require_known_families(
        "target_margin_by_family",
        (family for family, _ in CommercialThresholds().target_margin_by_family),
        vocabulary,
        source="the family vocabulary the loaded PIE pack declares")


@pytest.mark.requires_pie
def test_a_stale_stored_family_key_does_not_block_unrelated_edits(session, monkeypatch):
    """A pack rename must not lock an owner out of the rest of their margin
    policy. The stored key was valid under the pack that wrote it; after the
    rename an edit of an unrelated field still saves — the read path already
    tolerates the stale key — and only an edit touching the map itself is
    asked to answer for the current vocabulary."""
    import app.pie_service as pie_service
    from app.commercial import policy as policy_mod
    from app.domain import models

    from app.config import settings

    org = "org_stale_family_key"
    session.add(models.Organization(organization_id=org, name="Stale",
                                    currency="INR", config={}))
    # The vocabulary is the union of this organization's companies' packs, so
    # the organization needs a company before any family edit can be judged.
    session.add(models.ZohoConnection(connection_id="cx_stale", organization_id=org,
                                      label="Stale Co", zoho_organization_id="zs",
                                      config={"pie_pack": settings.PIE_PACK.name}))
    session.flush()

    # History: the override was saved under an older pack that declared the
    # name — simulated by widening the vocabulary for that one save.
    real = pie_service.pack_families
    vocab_then = tuple(real() or ()) + ("family_the_pack_since_renamed",)
    monkeypatch.setattr(pie_service, "pack_families", lambda _pack=None: vocab_then)
    policy_mod.save_for_org(
        session, org,
        {"target_margin_by_family": {"family_the_pack_since_renamed": 0.30}})
    monkeypatch.setattr(pie_service, "pack_families", real)

    # The rename happened. An unrelated edit must still save.
    policy_mod.save_for_org(session, org, {"effective_tax_rate": 0.25})

    # But an edit touching the map itself answers for the vocabulary.
    with pytest.raises(PolicyError) as e:
        policy_mod.save_for_org(
            session, org,
            {"target_margin_by_family": {"family_the_pack_since_renamed": 0.30}})
    assert "family_the_pack_since_renamed" in str(e.value)


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
