"""Management pricing engine + margin guardrails."""
from __future__ import annotations

from app import pricing


def test_recommend_hits_target_margin():
    rec = pricing.recommend_price(cost=760, family="turning_insert")
    # default target 0.24 -> 760 / 0.76 = 1000
    assert rec == 1000
    assert abs(pricing.margin_pct(rec, 760) - 0.24) < 0.01


def test_family_target_overrides_default():
    # drills target 0.28 -> 720 / 0.72 = 1000
    assert pricing.recommend_price(cost=720, family="solid_carbide_drill") == 1000


def test_below_floor_flag():
    econ = pricing.compute_economics(cost=95, list_price=100, quoted=100, family=None)
    # margin 5% < 15% floor
    assert econ.below_floor is True
    econ2 = pricing.compute_economics(cost=70, list_price=100, quoted=100, family=None)
    assert econ2.below_floor is False


def test_within_authority_band():
    assert pricing.within_authority(requested=97, recommended=100) is True   # -3%
    assert pricing.within_authority(requested=90, recommended=100) is False  # -10%
    assert pricing.within_authority(requested=None, recommended=100) is False


def test_unpriceable_line():
    assert pricing.recommend_price(None, None) is None
    econ = pricing.compute_economics(cost=None, list_price=None, quoted=None, family=None)
    assert econ.below_floor is False
