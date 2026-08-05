"""Payout and gates — 100% coverage required, plus the config discipline."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from incentive_engine import gates, payout
from incentive_engine.config import ConfigError, load_config
from incentive_engine.models import (
    CustomerPoints, ThirdPartyIncentive,
)
from incentive_engine.report import assert_ops_clean, operations_payload


def _points(caf_value, weighted):
    return CustomerPoints("c1", "s1", "2026Q2", 42, "Developing", D("0"),
                          caf_value, caf_value, weighted)


def _run(cfg, weighted, q=D("1.05"), gate=None, **kw):
    return payout.compute(cfg, payout.PayoutInputs(
        "s1", "SLS", "2026Q2", [_points(D("37000"), weighted)], q,
        gate or gates.evaluate(), **kw))


# ── the worked baselines, end to end ────────────────────────────────────────
def test_s1_payout_reproduces_the_brief(cfg):
    """37,000 CAF x 0.90 x 1.60 = 53,280 points, x 1.05 Q x 8% r."""
    got = _run(cfg, D("37000") * D("0.90") * D("1.60"))
    assert got.payout_gross == D("4475.52")
    assert got.payout_cash_70 == D("3132.86")
    assert got.payout_cash_70 + got.relationship_bank_30 == got.payout_gross


def test_s2_payout_reproduces_the_brief(cfg):
    got = _run(cfg, D("18100") * D("0.90") * D("1.60"))
    assert got.payout_gross == D("2189.38")


def test_s3_hiding_the_kickback_is_loss_making_before_any_gate_fires(cfg):
    got = _run(cfg, D("21500") * D("0.90") * D("1.60"))
    assert got.payout_gross - D("3400") < D("0")


def test_s1_is_at_least_twice_s2(cfg):
    s1 = _run(cfg, D("37000") * D("0.90") * D("1.60")).payout_gross
    s2 = _run(cfg, D("18100") * D("0.90") * D("1.60")).payout_gross
    assert s1 / s2 >= 2


# ── gates withhold, they do not destroy ─────────────────────────────────────
def test_a_failed_gate_withholds_the_cash_but_keeps_the_computation(cfg):
    """Permanent forfeiture drives concealment; deferral drives correction."""
    failed = gates.evaluate(gates.g3_receivables(cfg, [("I1", 200, False)]))
    got = _run(cfg, D("50000"), gate=failed)
    assert got.payout_gross > D("0")          # computed in full
    assert got.payout_cash_70 == D("0")       # and held
    assert got.relationship_bank_30 == D("0")
    assert got.gate_status["passed"] is False


def test_an_escalated_receivable_passes_the_gate(cfg):
    assert gates.g3_receivables(cfg, [("I1", 200, True)]).passed


def test_declaring_k_after_invoicing_is_not_declaring(cfg):
    """The point of the pre-invoice requirement is that the number was
    committed to before anyone knew whether it would be noticed."""
    late = ThirdPartyIncentive("SLS", "d", "I1", "c1", D("100"), "cash",
                               date(2026, 6, 10))
    result = gates.g1_compliance(cfg, [late], [],
                                 invoice_dates={"I1": date(2026, 6, 1)})
    assert not result.passed and "after invoicing" in result.reasons[0]


def test_an_undeclared_k_fails_g1(cfg):
    found = ThirdPartyIncentive("SLS", "d", "I1", "c1", D("3400"), "cash",
                                date(2026, 6, 1))
    assert not gates.g1_compliance(cfg, [], [found]).passed


def test_a_line_with_no_published_floor_fails_g2(cfg, line):
    """It would pay the full selling price as contribution — every unmastered
    item becomes a jackpot."""
    result = gates.g2_data_integrity(cfg, [line(floor_price=D("0"))])
    assert not result.passed and "no published floor" in result.reasons[0]


def test_g2_catches_the_master_data_backlog(cfg, line):
    result = gates.g2_data_integrity(
        cfg, [line()], unmastered_items=["X"], missing_hsn=["Y"],
        missing_customer_po=["INV-9"])
    assert not result.passed and len(result.reasons) == 3


def test_gates_all_clean_passes(cfg, line):
    assert gates.evaluate(gates.g1_compliance(cfg, [], []),
                          gates.g2_data_integrity(cfg, [line()]),
                          gates.g3_receivables(cfg, [])).passed


def test_the_undeclared_k_clawback_is_three_times(cfg):
    """3x makes concealment negative-expected-value above ~25% detection."""
    assert gates.undeclared_k_clawback(cfg, D("3400")) == D("10200.00")


# ── the accelerator creates no boundary worth gaming ────────────────────────
def test_the_accelerator_applies_only_to_the_excess(cfg):
    """Crossing the threshold changes the rate on the NEXT rupee, never on the
    rupees already earned. A whole-balance accelerator would make the boundary
    worth hoarding orders around."""
    r = D("0.08")
    base, acc = payout.apply_accelerator(cfg, r, D("100000"), D("100000"))
    assert acc == D("0")
    base2, acc2 = payout.apply_accelerator(cfg, r, D("140000"), D("100000"))
    assert base2 == D("130000") * r and acc2 == D("10000") * r * D("1.15")


def test_no_target_means_no_accelerator(cfg):
    base, acc = payout.apply_accelerator(cfg, D("0.08"), D("999999"), None)
    assert acc == D("0")


# ── r is per entity, and refuses to guess ───────────────────────────────────
def test_an_uncalibrated_entity_refuses_to_pay_rather_than_inherit_a_rate(cfg):
    with pytest.raises(ValueError, match="per-entity"):
        payout.rate_for_entity(cfg, "SOME-NEW-ENTITY")


def test_the_shipped_config_pays_nothing_until_calibrated():
    """A scheme nobody has calibrated must not silently pay whatever a
    developer typed."""
    shipped = load_config(as_of=date(2026, 7, 1))
    assert payout.rate_for_entity(shipped, "SLS") == D("0")


def test_a_payout_split_that_does_not_sum_to_one_is_refused(cfg):
    cfg.raw["payout"]["relationship_bank_share"] = "0.20"
    with pytest.raises(ValueError, match="sum to 1"):
        _run(cfg, D("50000"))


def test_clawbacks_never_drive_a_payout_negative(cfg):
    got = _run(cfg, D("1000"), clawbacks=D("999999"))
    assert got.payout_gross == D("0")


def test_the_recovery_bounty_is_not_scaled_by_portfolio_health(cfg):
    """It would be perverse to pay less for clearing dead stock because the
    book is concentrated."""
    low_q = _run(cfg, D("0"), q=D("0.85"), recovery_bounty=D("5000"))
    high_q = _run(cfg, D("0"), q=D("1.15"), recovery_bounty=D("5000"))
    assert low_q.payout_gross == high_q.payout_gross == D("5000.00")


# ── determinism and the zone boundary ───────────────────────────────────────
def test_the_same_inputs_produce_byte_identical_output(cfg):
    a = _run(cfg, D("53280"))
    b = _run(cfg, D("53280"))
    assert a == b and a.config_version == cfg.version


def test_an_operations_payload_carries_no_cost_anywhere(cfg):
    got = _run(cfg, D("53280"))
    payload = operations_payload(got, [_points(D("37000"), D("53280"))])
    assert_ops_clean(payload)              # raises if it does
    assert "cost" not in str(payload).lower()


def test_the_ops_guard_actually_catches_a_planted_leak(cfg):
    """A test that cannot fail proves nothing."""
    got = _run(cfg, D("53280"))
    payload = operations_payload(got, [_points(D("37000"), D("53280"))])
    payload["audit"].append({"note": "landed cost was 210"})
    with pytest.raises(AssertionError, match="I1"):
        assert_ops_clean(payload)


# ── config discipline ───────────────────────────────────────────────────────
def test_a_float_parameter_is_refused_rather_than_coerced(cfg):
    """0.1 has already lost precision by the time it reaches Decimal()."""
    cfg.raw["caf"]["toolkit_charge_rate"] = 0.5
    with pytest.raises(ConfigError, match="float"):
        cfg.dec("caf", "toolkit_charge_rate")


def test_a_historical_period_cannot_be_recomputed_under_todays_rates():
    with pytest.raises(ConfigError, match="in force"):
        load_config(as_of=date(2020, 1, 1))


def test_a_missing_parameter_is_named_not_defaulted(cfg):
    with pytest.raises(ConfigError, match="missing parameter"):
        cfg.dec("payout", "no_such_parameter")
