"""Coverage for the remaining computation modules, and the behaviours in them
that are load-bearing rather than merely correct."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from incentive_engine import (
    attribution, caf, cli, collection, floor, health, payout, recovery, report,
    rsi, vendor, weighting,
)
from incentive_engine.baseline import CustomerBaseline, build
from incentive_engine.gates import evaluate
from incentive_engine.models import (
    CustomerAttributes, CustomerPoints, FloorPriceSchedule, Payment, Trial,
    ValidationError, VendorYield,
)


# ── floor ───────────────────────────────────────────────────────────────────
def test_the_floor_in_force_is_the_latest_one_effective_on_the_date():
    schedules = [
        FloorPriceSchedule("SLS", "i1", D("250"), date(2026, 1, 1), date(2026, 5, 31)),
        FloorPriceSchedule("SLS", "i1", D("280"), date(2026, 6, 1)),
    ]
    assert floor.published_floor(schedules, "SLS", "i1", date(2026, 4, 1)) == D("250")
    assert floor.published_floor(schedules, "SLS", "i1", date(2026, 7, 1)) == D("280")


def test_an_unpublished_floor_is_none_and_must_never_become_zero():
    """P - 0 would pay the full selling price as contribution and turn every
    unmastered item into a jackpot."""
    assert floor.published_floor([], "SLS", "i1", date(2026, 7, 1)) is None
    assert floor.published_floor(
        [FloorPriceSchedule("OTHER", "i1", D("1"), date(2026, 1, 1))],
        "SLS", "i1", date(2026, 7, 1)) is None


def test_the_aged_schedule_scales_the_floor_not_the_cost(cfg):
    """Expressing it against cost would require cost at the point of computing
    an operations number — the exact leak the design avoids."""
    fresh = floor.resolve(cfg, D("280"), None)
    assert fresh.floor_price == D("280") and not fresh.is_aged
    within = floor.resolve(cfg, D("280"), 100)
    assert within.floor_price == D("280") and not within.is_aged
    aged = floor.resolve(cfg, D("280"), 400)
    assert aged.floor_price == D("196.00") and aged.is_aged
    ancient = floor.resolve(cfg, D("280"), 900)
    assert ancient.floor_price == D("112.00")
    assert "aged 900d" in ancient.basis


def test_an_age_outside_every_band_is_loud_not_silently_full(cfg):
    cfg.raw["floor"]["aged_floor_schedule"] = [
        {"from_days": 0, "to_days": 10, "floor_fraction": "1.00", "is_aged": False}]
    with pytest.raises(ValueError, match="no aged floor band"):
        floor.resolve(cfg, D("280"), 999)


def test_m_floor_falls_back_to_the_family_default(cfg):
    assert floor.m_floor_for_family(cfg, "inserts") == D("0.22")
    assert floor.m_floor_for_family(cfg, "something_new") == D("0.25")


# ── collection ──────────────────────────────────────────────────────────────
def test_partial_payment_is_value_weighted_not_all_or_nothing(cfg):
    """Taking the last receipt would let a token final payment destroy a
    well-collected invoice; taking the first would rescue a bad one."""
    got = collection.collected_fraction(cfg, D("100000"), [
        Payment("SLS", "I", date(2026, 2, 1), D("60000"), date(2026, 2, 1)),
        Payment("SLS", "I", date(2026, 5, 12), D("40000"), date(2026, 2, 1)),
    ])
    assert got == D("0.60") * D("1.00") + D("0.40") * D("0.25")


def test_an_invoice_with_nothing_received_earns_nothing(cfg):
    assert collection.collected_fraction(cfg, D("1000"), []) == D("0")
    assert collection.collected_fraction(cfg, D("0"), []) == D("0")


def test_over_receipt_never_scales_points_above_the_invoice(cfg):
    got = collection.collected_fraction(cfg, D("1000"), [
        Payment("SLS", "I", date(2026, 1, 1), D("1500"), date(2026, 2, 1))])
    assert got <= D("1.10")


def test_an_uncovered_lateness_is_loud(cfg):
    cfg.raw["collection"]["bands"] = [
        {"label": "x", "from": 0, "to": 1, "factor": "1.00"}]
    with pytest.raises(ValueError, match="no collection band"):
        collection.factor(cfg, 99)


# ── health ──────────────────────────────────────────────────────────────────
def test_nothing_to_retain_is_not_a_failure_to_retain():
    assert health.logo_retention(0, 0) == D("1")
    assert health.logo_retention(10, 7) == D("0.7")


def test_concentration_degrades_smoothly_rather_than_at_a_cliff(cfg):
    """A cliff at exactly 40% would create a boundary worth managing to."""
    assert health.concentration(cfg, [D("0.3"), D("0.3"), D("0.4")]) == D("1")
    mid = health.concentration(cfg, [D("0.7"), D("0.3")])
    worse = health.concentration(cfg, [D("0.9"), D("0.1")])
    assert D("0") < worse < mid < D("1")
    assert health.concentration(cfg, []) == D("1")


def test_forecast_accuracy_does_not_reward_over_delivery():
    assert health.forecast_accuracy(D("100"), D("100")) == D("1")
    assert health.forecast_accuracy(D("100"), D("150")) == D("0.5")
    assert health.forecast_accuracy(D("100"), D("50")) == D("0.5")
    assert health.forecast_accuracy(D("0"), D("10")) == D("1")


def test_dispute_rate_and_contact_depth_score_sensibly(cfg):
    assert health.dispute_rate(cfg, D("0"), D("0")) == D("1")
    assert health.dispute_rate(cfg, D("10"), D("100")) == D("0")
    assert health.contact_depth(cfg, D("9")) == D("1")


def test_a_trial_too_young_to_have_converted_is_not_counted(cfg):
    """Including last week's trial in the denominator punishes the calendar."""
    young = Trial("SLS", "c1", "i1", "s1", date(2026, 6, 1), "PC-1")
    score, detail = health.trial_conversion(cfg, [young], date(2026, 7, 1))
    assert score == D("1") and detail["eligible_trials"] == 0


def test_a_conversion_after_the_window_does_not_count(cfg):
    late = Trial("SLS", "c1", "i1", "s1", date(2026, 1, 1), "PC-1", True,
                 date(2026, 11, 1), lock_in_factors=("multi_threaded", "rate_contract"))
    score, _ = health.trial_conversion(cfg, [late], date(2027, 1, 1))
    assert score == D("0")


def test_a_missing_health_component_scores_one_rather_than_zero(cfg):
    """Q must measure portfolio health, not instrumentation coverage."""
    assert health.compute(cfg, {}).q == cfg.dec("health", "q_max")
    assert health.compute(cfg, {"logo_retention": D("0")}).q < cfg.dec("health", "q_max")


# ── rsi / weighting / baseline ──────────────────────────────────────────────
def test_rsi_advances_on_tenure_so_a_band_cannot_be_held_by_underselling(cfg):
    thin = CustomerAttributes("c", "g", ("SLS",), date(2020, 1, 1), 1, 1,
                              D("0.05"), D("0"), 1)
    young = rsi.compute(cfg, thin, tenure_months=2)
    old = rsi.compute(cfg, thin, tenure_months=60)
    assert old.score > young.score


def test_an_rsi_outside_every_band_is_loud(cfg):
    cfg.raw["rsi"]["bands"] = [{"name": "only", "from": 0, "to": 10,
                               "w_base": None, "w_inc": "1.0",
                               "toolkit_cap_pct": "0.1", "retention_gate": "none"}]
    with pytest.raises(ValueError, match="no RSI band"):
        rsi.band_for(cfg, 50)


def test_the_new_band_pays_incremental_only_never_both(cfg):
    """Every rupee from a brand-new account is incremental by definition;
    a base multiplier as well would pay twice for the same rupee."""
    new = rsi.compute(cfg, CustomerAttributes(
        "c", "g", ("SLS",), None, 1, 1, D("0"), D("0"), 0), tenure_months=0)
    assert new.w_base is None
    base = CustomerBaseline("g", D("0"), D("10000"), D("10000"), D("0"))
    clean = weighting.RetentionCheck(False, (), D("1.00"))
    assert weighting.weighted_points(cfg, new, base, clean) == D("10000") * D("1.80")


def test_a_declining_account_never_produces_negative_incremental(cfg, line):
    """The retention gate is the instrument for decline, not a negative
    multiplier that would make the account worth abandoning outright."""
    old = caf.line_caf(cfg, line(invoice_date=date(2025, 6, 1)))
    new = caf.line_caf(cfg, line(invoice_date=date(2026, 6, 1),
                                 unit_price_net=D("300")))
    built = build([(old, D("1")), (new, D("1"))], "g1",
                  date(2026, 1, 1), date(2026, 12, 31),
                  date(2025, 1, 1), date(2025, 12, 31))
    assert built.incremental_caf == D("0")
    assert built.yoy_change < D("0")
    assert CustomerBaseline("g", D("0"), D("0"), D("0"), D("0")).yoy_change == D("0")


# ── vendor / recovery / attribution ─────────────────────────────────────────
def test_non_price_concessions_have_published_valuation_conventions(cfg):
    assert vendor.value_concession(cfg, "free_tooling", D("10000")) == D("10000.00")
    # Demo stock comes back, so half credit.
    assert vendor.value_concession(cfg, "demo_stock", D("10000")) == D("5000.00")
    assert vendor.value_concession(cfg, "training", D("99999")) == D("25000")
    with pytest.raises(ValueError, match="no valuation convention"):
        vendor.value_concession(cfg, "goodwill", D("1"))


def test_a_non_commitment_yield_releases_in_full(cfg):
    y = VendorYield("SLS", "KM", D("9000"), "credit_note", "CN-1")
    assert vendor.release(cfg, y).released == D("9000")


def test_commitment_release_can_be_switched_off_by_config(cfg):
    cfg.raw["vendor"]["commitment_release_on_sale"] = False
    y = VendorYield("4U", "YG1", D("5000"), "credit_note", "CN", 
                    is_commitment_linked=True, committed_qty=D("100"))
    assert vendor.release(cfg, y).released == D("5000")


def test_a_commitment_with_no_quantity_cannot_be_constructed():
    with pytest.raises(ValidationError, match="no committed quantity"):
        VendorYield("4U", "YG1", D("5000"), "credit_note", "CN",
                    is_commitment_linked=True)


def test_a_non_aged_line_earns_no_recovery_bounty(cfg, line):
    assert recovery.bounty(cfg, caf.line_caf(cfg, line()), closer_id="s1",
                           accepted=True) is None


def test_a_clearance_below_the_writedown_floor_earns_nothing(cfg, line):
    aged = caf.line_caf(cfg, line(is_aged_stock=True, stock_age_days=400,
                                  floor_price=D("196"), unit_price_net=D("100")))
    assert recovery.bounty(cfg, aged, closer_id="s1", accepted=True) is None


def test_the_proposer_closer_split_pays_the_closer_more(cfg, line):
    """The closer carries the relationship cost of a bad substitution."""
    aged = caf.line_caf(cfg, line(is_aged_stock=True, stock_age_days=400,
                                  floor_price=D("196")))
    got = recovery.bounty(cfg, aged, closer_id="s2", proposer_id="backoffice",
                          accepted=True)
    assert got.closer_bounty > got.proposer_bounty
    assert got.proposer_bounty + got.closer_bounty == got.total
    assert recovery.rate_for_age(cfg, 10) == D("0")


def test_assist_credit_is_company_funded(cfg):
    """The detail that makes knowledge sharing Pareto-improving: helping a
    colleague costs the account owner nothing."""
    got = attribution.assist(cfg, assisting_id="s2", account_owner_id="s1",
                             customer_group_id="g1", account_points=D("100000"))
    assert got.company_funded and got.points == D("15000.00")


def test_an_undocumented_handover_pays_no_tail(cfg):
    """The tail is payment for a clean handover, not for leaving."""
    documented = attribution.handover(cfg, outgoing_id="s1", customer_group_id="g",
                                      account_points=D("100000"),
                                      quarters_elapsed=1, documented=True)
    undocumented = attribution.handover(cfg, outgoing_id="s1", customer_group_id="g",
                                        account_points=D("100000"),
                                        quarters_elapsed=1, documented=False)
    expired = attribution.handover(cfg, outgoing_id="s1", customer_group_id="g",
                                   account_points=D("100000"),
                                   quarters_elapsed=5, documented=True)
    assert documented.points == D("25000.00")
    assert undocumented.points == D("0") and expired.points == D("0")


def test_concentration_shares_are_computed_on_the_book(cfg):
    assert attribution.concentration_shares({}) == []
    shares = attribution.concentration_shares({"a": D("60"), "b": D("40")})
    assert shares == [D("0.6"), D("0.4")]


# ── owner reconciliation is the only place cost appears ─────────────────────
def test_the_owner_reconciliation_ties_caf_back_to_gross_profit(cfg, line):
    """The gap is exactly the floor premium — the constant per unit that makes
    CAF strictly more conservative than gross profit on quantity."""
    lines = [caf.line_caf(cfg, line())]
    pts = [CustomerPoints("c1", "s1", "2026Q2", 42, "Developing", D("0"),
                          D("30000"), D("30000"), D("43200"))]
    paid = payout.compute(cfg, payout.PayoutInputs(
        "s1", "SLS", "2026Q2", pts, D("1.05"), evaluate()))
    recon = report.owner_reconciliation(
        cfg, paid, pts, lines, {"INV-1:CNMG-120408": D("230")})
    assert recon.cost_of_goods == D("115000")
    assert recon.gross_profit == D("55000")
    # 340 - 280 = 60 x 500 CAF; GP 55,000 - CAF 30,000 = 25,000 floor premium.
    assert D(recon.leakage["floor_premium"]) == D("25000")
    assert recon.m_floor_by_family == {"inserts": "0.22"}
    assert recon.leakage["payout_as_pct_of_caf"] is not None


def test_the_reconciliation_survives_a_line_with_no_cost_on_record(cfg, line):
    lines = [caf.line_caf(cfg, line())]
    paid = payout.compute(cfg, payout.PayoutInputs(
        "s1", "SLS", "2026Q2", [], D("1"), evaluate()))
    recon = report.owner_reconciliation(cfg, paid, [], lines, {})
    assert recon.cost_of_goods == D("0")
    assert recon.leakage["payout_as_pct_of_caf"] == "0.0000"


# ── the CLI ─────────────────────────────────────────────────────────────────
def test_the_cli_verifies_the_shipped_config(capsys):
    assert cli.main(["--as-of", "2026-07-01", "verify"]) == 0
    assert "shadow run" in capsys.readouterr().out


def test_the_cli_reports_a_broken_config(tmp_path, capsys):
    import yaml
    from incentive_engine.config import DEFAULT_PATH
    raw = yaml.safe_load(DEFAULT_PATH.read_text())
    raw["payout"]["relationship_bank_share"] = "0.20"
    raw["health"]["weights"]["logo_retention"] = 99
    raw["rsi"]["weights"]["tenure"] = 99
    raw["recovery"]["proposer_share"] = "0.90"
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(raw))
    assert cli.main(["--config", str(path), "--as-of", "2026-07-01", "verify"]) == 1
    out = capsys.readouterr().out
    assert "does not sum to 1" in out and "not 100" in out


def test_the_cli_shows_the_config(capsys):
    assert cli.main(["--as-of", "2026-07-01", "show-config"]) == 0
    assert "2026.1" in capsys.readouterr().out


def test_the_cli_reports_a_config_error_rather_than_crashing(capsys):
    assert cli.main(["--as-of", "2020-01-01", "verify"]) == 2
    assert "config error" in capsys.readouterr().err
