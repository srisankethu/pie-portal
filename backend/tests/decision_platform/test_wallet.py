"""Share of wallet — the ladder, and every rung it refuses to climb.

The tests worth having here are almost all about the *refusals*. An estimate
that produces a number is easy to check; what makes this module honest is that
it declines to, in the specific cases where a number would be flattering — and
every one of those is a case somebody will later be tempted to "fix".
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from app.commercial.config import CommercialThresholds
from app.commercial.insight import wallet

TODAY = date(2026, 8, 9)


def _th(**over) -> CommercialThresholds:
    return CommercialThresholds(**over) if over else CommercialThresholds()


def _asks(n: int, value: str = "50000", elsewhere=True) -> list[wallet.LostAsk]:
    return [wallet.LostAsk(f"q{i}", D(value), date(2026, 3, 1), elsewhere)
            for i in range(n)]


# ── the shape of the answer ─────────────────────────────────────────────────
def test_no_rung_ever_emits_a_midpoint():
    """The band's width is the estimate's honesty.

    A midpoint field would be consumed as a measurement within a week — by a
    chart axis, by a sort, by an export — so there is not one to consume.
    """
    cases = [
        wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000")),
        wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("90000"), lost_asks=_asks(3)),
        wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        declaration=wallet.Declaration(0.35, "s1", date(2026, 6, 1))),
        wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        tenders=[wallet.TenderObservation(
                            "T1", D("1000"), D("400"), date(2026, 1, 1), "gem")]),
    ]
    for e in cases:
        keys = set(e.to_dict())
        assert "share" not in keys and "midpoint" not in keys, e.basis
        # Both ends or neither — a band with one end filled is a number in a
        # range's clothing.
        assert (e.share_low is None) == (e.share_high is None)


def test_every_answer_carries_the_thresholds_version():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("1"))
    assert e.thresholds_version.startswith("ci_")


# ── UNKNOWN is the common answer, and it has to teach something ─────────────
def test_a_customer_with_nothing_on_record_is_unknown_not_zero():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"))
    assert e.basis == wallet.UNKNOWN
    assert e.share_low is None and e.share_high is None


def test_the_refusal_names_the_specific_thing_that_would_close_it():
    """"Not enough data" sends nobody to do anything."""
    thin = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                           quoted_revenue=D("90000"), lost_asks=_asks(1))
    assert "Recording the next declined enquiry" in thin.refusal

    unquoted = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                               quoted_revenue=D("1000"), lost_asks=_asks(5))
    assert "Quoting through the platform" in unquoted.refusal

    stale = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                            declaration=wallet.Declaration(0.4, "Priya",
                                                           date(2024, 1, 1)))
    assert "expired" in stale.refusal and "Priya" in stale.refusal


# ── the bound, and the way it fails ─────────────────────────────────────────
def test_the_bound_is_a_ceiling_with_no_floor_invented():
    """Nothing in the evidence establishes how *low* the share might be."""
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("100000"), lost_asks=_asks(3))
    assert e.basis == wallet.BOUNDED_ASKS
    assert e.share_low == 0.0
    # 100000 / (100000 + 150000)
    assert e.share_high == 0.4


def test_thin_quote_coverage_refuses_rather_than_reporting_a_loose_ceiling():
    """The failure that runs the flattering way.

    A customer whose orders never pass through the quote screen has no recorded
    losses, so the ceiling sits near 100% and they read as an account we own —
    when the truth is that nobody wrote down what we lost. This is the single
    most important refusal in the module.
    """
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("5000"), lost_asks=_asks(9))
    assert e.basis == wallet.UNKNOWN
    assert e.quote_coverage == 0.05


def test_a_bound_says_out_loud_how_loose_its_coverage_makes_it():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("40000"), lost_asks=_asks(3))
    assert e.basis == wallet.BOUNDED_ASKS
    assert any("flatters us" in c for c in e.caveats)


def test_a_loss_of_unknown_kind_is_excluded_from_both_sides():
    """None is not "nobody bought it".

    Folding an unrecorded reason into "the requirement died" would shrink the
    denominator and overstate our share — the flattering direction again.
    """
    known = _asks(3)
    murky = known + [wallet.LostAsk("qX", D("900000"), date(2026, 4, 1), None)]
    a = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("90000"), lost_asks=known)
    b = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("90000"), lost_asks=murky)
    assert a.share_high == b.share_high
    assert any("do not say" in c for c in b.caveats)


def test_a_requirement_that_died_is_not_a_competitors_rupee():
    dead = [wallet.LostAsk(f"q{i}", D("50000"), date(2026, 3, 1), False)
            for i in range(5)]
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("90000"), lost_asks=dead)
    assert e.basis == wallet.UNKNOWN


# ── the measured rung ───────────────────────────────────────────────────────
def test_a_measured_share_is_a_ratio_of_sums_not_a_mean_of_ratios():
    """A ₹2L tender won outright and a ₹80L tender lost do not average to 50%."""
    tenders = [
        wallet.TenderObservation("T1", D("200000"), D("200000"), date(2026, 1, 1), "gem"),
        wallet.TenderObservation("T2", D("800000"), D("0"), date(2026, 2, 1), "gem"),
    ]
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        tenders=tenders)
    assert e.basis == wallet.MEASURED_TENDER
    assert e.share_high == 0.2          # 200000 / 1000000, not 0.5


def test_an_open_bid_is_excluded_rather_than_counted_as_lost():
    tenders = [
        wallet.TenderObservation("T1", D("100000"), D("50000"), date(2026, 1, 1), "gem"),
        wallet.TenderObservation("T2", D("900000"), None, date(2026, 2, 1), "gem"),
    ]
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        tenders=tenders)
    assert e.share_high == 0.5
    assert any("still open" in c for c in e.caveats)


def test_a_measured_share_says_it_covers_only_tendered_buying():
    """The scope caveat is the difference between this and a lie."""
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        tenders=[wallet.TenderObservation(
                            "T1", D("100000"), D("50000"), date(2026, 1, 1), "gem")])
    assert any("off-tender" in c for c in e.caveats)


def test_tenders_with_no_award_yet_do_not_produce_a_measured_share():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        tenders=[wallet.TenderObservation(
                            "T1", D("100000"), None, date(2026, 1, 1), "gem")])
    assert e.basis == wallet.UNKNOWN
    assert "none has an award yet" in e.refusal


def test_a_measurement_is_never_demoted_to_a_bound():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("90000"), lost_asks=_asks(5),
                        tenders=[wallet.TenderObservation(
                            "T1", D("100000"), D("40000"), date(2026, 1, 1), "gem")])
    assert e.basis == wallet.MEASURED_TENDER


# ── the declared rung ───────────────────────────────────────────────────────
def test_a_declaration_is_widened_into_a_band_and_never_a_point():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        declaration=wallet.Declaration(0.35, "Priya",
                                                       date(2026, 6, 1)))
    assert e.basis == wallet.DECLARED
    assert (e.share_low, e.share_high) == (0.25, 0.45)
    assert "Priya" in e.denominator_basis


def test_a_declaration_expires_and_the_rung_drops():
    old = wallet.Declaration(0.35, "Priya", date(2024, 1, 1))
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        declaration=old)
    assert e.basis == wallet.UNKNOWN


def test_a_declaration_band_is_clamped_to_a_real_ratio():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        declaration=wallet.Declaration(0.97, "s", date(2026, 6, 1)))
    assert e.share_high == 1.0
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        declaration=wallet.Declaration(0.02, "s", date(2026, 6, 1)))
    assert e.share_low == 0.0


def test_evidence_beats_a_declaration():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("100000"),
                        quoted_revenue=D("90000"), lost_asks=_asks(3),
                        declaration=wallet.Declaration(0.9, "s", date(2026, 6, 1)))
    assert e.basis == wallet.BOUNDED_ASKS


# ── denominators ────────────────────────────────────────────────────────────
def test_no_revenue_means_there_is_nothing_to_be_a_share_of():
    e = wallet.estimate("c", TODAY, thresholds=_th(), revenue=D("0"),
                        lost_asks=_asks(5))
    assert e.basis == wallet.UNKNOWN
    assert "nothing for a share to be a share of" in e.refusal
