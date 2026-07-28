"""The six deterministic Customer × Item detectors.

Two properties matter more than any individual rule:

- weak data produces **no** signal, however dramatic the percentage looks;
- ranking is by **rupees**, so a small percentage move on a large account
  outranks a large one on a trivial account.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.commercial.benchmark import compute_benchmark
from app.commercial.config import CommercialThresholds
from app.commercial.detectors import detect
from app.commercial.economics import line_economics
from app.commercial.metrics import compute_relationship
from app.commercial.subject import decode, encode
from app.domain.enums import SignalType, SubjectEntityType
from app.signals.base import CostRow, SaleRow

AS_OF = date(2026, 7, 1)
TH = CommercialThresholds()


def _line(days_ago, qty, price, cost=None, customer="c1"):
    when = AS_OF - timedelta(days=days_ago)
    q, p = Decimal(str(qty)), Decimal(str(price))
    ref = f"INV-{customer}-{days_ago}"
    sale = SaleRow(customer_id=customer, product_id="p1", date=when, qty=q,
                   unit_price=p, line_revenue=q * p,
                   source_ref={"record_type": "invoice", "record_id": ref},
                   external_ref=f"{ref}:1")
    costs = ([CostRow(product_id="p1", date=when - timedelta(days=1), qty=Decimal("1"),
                      unit_cost=Decimal(str(cost)), source_ref={"record_id": "B"},
                      external_ref="B:1")]
             if cost is not None else [])
    return line_economics(sale, costs)


def _detect(lines, benchmark=None, th=TH):
    m = compute_relationship("c1", "p1", lines, AS_OF, th)
    return detect(m, benchmark, th, []), m


def _types(drafts):
    return {d.signal_type for d in drafts}


def _eroding(qty=100, price=200, base_cost=100, recent_cost=150):
    """A relationship with a real, well-evidenced margin decline."""
    lines = [_line(d, qty, price, cost=base_cost) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, qty, price, cost=recent_cost) for d in (80, 50, 20)]
    return lines


# ── the sufficiency gate ────────────────────────────────────────────────────
def test_insufficient_data_produces_no_signals_at_all():
    """One historical transaction must never become a confident alert, however
    dramatic the apparent move."""
    drafts, m = _detect([_line(400, 100, 200, cost=100), _line(20, 100, 200, cost=190)])
    assert m.data_sufficiency.value == "INSUFFICIENT"
    assert drafts == []


def test_a_relationship_without_cost_produces_no_margin_signals():
    drafts, _ = _detect([_line(d, 100, 200) for d in (700, 500, 300, 100, 20)])
    assert drafts == []


# ── 1. margin erosion ───────────────────────────────────────────────────────
def test_margin_erosion_fires_on_a_material_decline():
    drafts, m = _detect(_eroding())
    assert SignalType.CI_MARGIN_EROSION.value in _types(drafts)
    assert m.margin_change_pp <= -TH.min_margin_deterioration_pp


def test_no_erosion_signal_for_a_move_below_the_threshold():
    # 50% -> 49%: real, but one point is inside the noise floor.
    lines = [_line(d, 100, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 200, cost=102) for d in (80, 50, 20)]
    drafts, _ = _detect(lines)
    assert SignalType.CI_MARGIN_EROSION.value not in _types(drafts)


def test_no_erosion_signal_when_margin_improved():
    lines = [_line(d, 100, 200, cost=150) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 200, cost=100) for d in (80, 50, 20)]
    drafts, _ = _detect(lines)
    assert SignalType.CI_MARGIN_EROSION.value not in _types(drafts)


# ── 2. cost not passed through ──────────────────────────────────────────────
def test_cost_not_passed_fires_when_cost_rose_and_price_did_not():
    drafts, _ = _detect(_eroding())
    assert SignalType.CI_COST_NOT_PASSED.value in _types(drafts)


def test_cost_not_passed_does_not_fire_when_price_kept_pace():
    """Cost up 50%, price up 50% — margin percentage holds, nothing to flag."""
    lines = [_line(d, 100, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 300, cost=150) for d in (80, 50, 20)]
    drafts, _ = _detect(lines)
    assert SignalType.CI_COST_NOT_PASSED.value not in _types(drafts)


def test_price_driven_erosion_is_not_reported_as_a_cost_problem():
    lines = [_line(d, 100, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    lines += [_line(d, 100, 150, cost=100) for d in (80, 50, 20)]
    drafts, _ = _detect(lines)
    types = _types(drafts)
    assert SignalType.CI_MARGIN_EROSION.value in types
    assert SignalType.CI_COST_NOT_PASSED.value not in types, "cost did not move"


# ── 3. low peer pricing ─────────────────────────────────────────────────────
def _benchmark(subject_price, peer_price, peers=3):
    by_customer = {"c1": [_line(d, 100, subject_price, cost=100) for d in (60, 30)]}
    for i in range(2, 2 + peers):
        by_customer[f"c{i}"] = [_line(d, 100, peer_price, cost=100, customer=f"c{i}")
                                for d in (60, 30)]
    return compute_benchmark("p1", "c1", by_customer, AS_OF, TH)


def test_low_peer_pricing_fires_with_an_adequate_peer_population():
    bm = _benchmark(subject_price=130, peer_price=200, peers=3)
    drafts, _ = _detect(_eroding(price=130), benchmark=bm)
    assert SignalType.CI_LOW_PEER_PRICING.value in _types(drafts)


def test_low_peer_pricing_is_withheld_when_peers_are_too_few():
    """Below the peer floor the comparison is an anecdote — no signal, even
    though the subject really is cheaper."""
    bm = _benchmark(subject_price=130, peer_price=200, peers=1)
    assert bm.is_reliable(TH) is False
    drafts, _ = _detect(_eroding(price=130), benchmark=bm)
    assert SignalType.CI_LOW_PEER_PRICING.value not in _types(drafts)


def test_no_peer_signal_when_the_customer_prices_above_its_peers():
    bm = _benchmark(subject_price=260, peer_price=200, peers=3)
    drafts, _ = _detect(_eroding(price=260), benchmark=bm)
    assert SignalType.CI_LOW_PEER_PRICING.value not in _types(drafts)


# ── 4/5. volume classification ──────────────────────────────────────────────
def test_margin_decline_with_volume_growth_is_classified_separately():
    """27% -> 21% with volume 100 -> 310 may be a deliberate, working trade. It
    must never be filed as pure leakage."""
    lines = [_line(d, 100, 200, cost=146) for d in (700, 600, 500, 400)]
    lines += [_line(d, 100, 200, cost=146) for d in (170, 140, 100)]
    lines += [_line(d, 310, 200, cost=158) for d in (80, 50, 20)]
    drafts, m = _detect(lines)
    types = _types(drafts)

    assert m.volume_change_pct >= TH.meaningful_volume_change_pct
    assert SignalType.CI_MARGIN_DECLINE_WITH_VOLUME.value in types
    assert SignalType.CI_MARGIN_DECLINE_NO_VOLUME.value not in types


def test_margin_decline_without_volume_growth_is_the_leakage_case():
    """The same margin move on flat volume is much stronger evidence that the
    margin bought nothing."""
    lines = [_line(d, 100, 200, cost=146) for d in (700, 600, 500, 400)]
    lines += [_line(d, 100, 200, cost=146) for d in (170, 140, 100)]
    lines += [_line(d, 105, 200, cost=158) for d in (80, 50, 20)]
    drafts, m = _detect(lines)
    types = _types(drafts)

    assert m.volume_change_pct < TH.meaningful_volume_change_pct
    assert SignalType.CI_MARGIN_DECLINE_NO_VOLUME.value in types
    assert SignalType.CI_MARGIN_DECLINE_WITH_VOLUME.value not in types


def test_declining_volume_is_also_the_leakage_case():
    lines = [_line(d, 100, 200, cost=146) for d in (700, 600, 500, 400)]
    lines += [_line(d, 100, 200, cost=146) for d in (170, 140, 100)]
    lines += [_line(d, 60, 200, cost=158) for d in (80, 50, 20)]
    drafts, _ = _detect(lines)
    assert SignalType.CI_MARGIN_DECLINE_NO_VOLUME.value in _types(drafts)


def test_the_volume_signal_severity_is_damped_when_volume_grew():
    """A possibly-successful trade is surfaced for review, not ranked alongside
    leakage of the same rupee size."""
    grew = [_line(d, 100, 200, cost=146) for d in (700, 600, 500, 400)]
    grew += [_line(d, 100, 200, cost=146) for d in (170, 140, 100)]
    flat = list(grew)
    grew += [_line(d, 310, 200, cost=158) for d in (80, 50, 20)]
    flat += [_line(d, 105, 200, cost=158) for d in (80, 50, 20)]

    with_volume = next(d for d in _detect(grew)[0]
                       if d.signal_type == SignalType.CI_MARGIN_DECLINE_WITH_VOLUME.value)
    without = next(d for d in _detect(flat)[0]
                   if d.signal_type == SignalType.CI_MARGIN_DECLINE_NO_VOLUME.value)
    assert with_volume.severity_base < without.severity_base


# ── 6. materiality drives priority ──────────────────────────────────────────
def test_material_gap_fires_only_above_the_rupee_floor():
    small, _ = _detect(_eroding(qty=1, price=20))     # a few hundred rupees
    big, _ = _detect(_eroding(qty=1000, price=500))   # lakhs
    assert SignalType.CI_MATERIAL_MARGIN_GAP.value not in _types(small)
    assert SignalType.CI_MATERIAL_MARGIN_GAP.value in _types(big)


def test_a_small_percentage_move_on_a_large_account_outranks_the_reverse():
    """3 pp on ₹40 lakh must outrank 10 pp on ₹20,000 — economic materiality,
    not percentage deterioration, is what decides what to review first."""
    # ~3 points lost, on very large revenue
    large = [_line(d, 4000, 500, cost=250) for d in (700, 600, 500, 400, 300)]
    large += [_line(d, 4000, 500, cost=265) for d in (80, 50, 20)]
    # ~10 points lost, on trivial revenue
    small = [_line(d, 10, 200, cost=100) for d in (700, 600, 500, 400, 300)]
    small += [_line(d, 10, 200, cost=120) for d in (80, 50, 20)]

    large_drafts, large_m = _detect(large)
    small_drafts, small_m = _detect(small)

    assert abs(large_m.margin_change_pp) < abs(small_m.margin_change_pp), \
        "the large account lost FEWER points"
    large_sev = max(d.severity_base for d in large_drafts)
    small_sev = max(d.severity_base for d in small_drafts)
    assert large_sev > small_sev, "but it must still rank higher"


# ── shape and traceability ──────────────────────────────────────────────────
def test_signals_carry_the_customer_item_subject_and_are_decodable():
    drafts, _ = _detect(_eroding())
    d = drafts[0]
    assert d.subject_entity_type == SubjectEntityType.CUSTOMER_ITEM.value
    assert d.subject_entity_id == encode("c1", "p1")
    assert decode(d.subject_entity_id) == ("c1", "p1")


def test_signals_carry_metrics_thresholds_version_and_sufficiency():
    drafts, _ = _detect(_eroding())
    d = drafts[0]
    assert d.threshold_config_version == TH.version
    assert d.metrics["current_margin_pct"] is not None
    assert d.metrics["margin_change_pp"] is not None
    assert d.metrics["revenue_recent"] is not None
    assert d.sufficiency.level.value in {"PARTIAL", "SUFFICIENT"}
    assert d.window["recent_days"] == TH.recent_days


def test_evidence_refs_are_carried_through_to_every_signal():
    evidence = [{"source_system": "zoho", "record_type": "invoice",
                 "record_id": "INV-1", "line_id": "INV-1:1"}]
    m = compute_relationship("c1", "p1", _eroding(), AS_OF, TH)
    for d in detect(m, None, TH, evidence):
        assert d.evidence_refs == evidence, "a signal must trace to its invoices"


def test_thresholds_are_honoured_from_config_not_hardcoded():
    """Raising the deterioration floor must silence a finding that was firing —
    proof the number lives in config, not in the detector."""
    lines = _eroding(base_cost=100, recent_cost=112)     # ~6 points
    assert SignalType.CI_MARGIN_EROSION.value in _types(_detect(lines)[0])

    strict = CommercialThresholds(min_margin_deterioration_pp=0.50)
    assert SignalType.CI_MARGIN_EROSION.value not in _types(_detect(lines, th=strict)[0])
