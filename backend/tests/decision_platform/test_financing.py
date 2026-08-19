"""Financing-adjusted customer profitability: the join, and its four refusals.

The multiplication is three terms. What is worth pinning is everything that
makes the screen emptier, because those are the parts a later change is tempted
to soften into a number:

* **no cost of capital, no reading at all** — not the stock carrying rate, not a
  conservative guess, not the margins with a blank column beside them;
* **an account below the settlement floor gets nothing, and specifically does
  not borrow the book's median** — that would state a financing cost for the one
  account there is least evidence about;
* **an account with unknown gross profit gets nothing**, because subtracting a
  real charge from an unknown profit prints a confident loss;
* **the charge is levied on the same rupees the profit was earned on** — the
  wave-1 blocker's exact shape, a numerator over one population divided by a
  denominator over another;
* **the book is Σ adjusted profit ÷ Σ costed revenue**, never a mean of
  per-customer margins.

Two tests here exist to fail if a guard is *deleted* rather than merely wrong:
``test_a_thin_account_is_absent_from_the_totals_not_averaged_into_them`` and
``test_the_book_median_is_never_substituted_for_a_thin_account`` both assert on
totals that are exactly one account's, so a silent fallback shows up as a number
moving rather than as a field appearing.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.insight import absence, financing, payments
from app.commercial.portfolio import MarginAggregate
from app.config import settings
from app.domain import models
from app.seed import SEED_PASSWORD

ORG = settings.DEFAULT_ORG_ID
AS_OF = date(2026, 8, 10)

#: A rate that divides cleanly, so every expected figure below is checkable by
#: hand rather than by re-running the implementation.
RATE = CommercialThresholds(cost_of_capital_annual_pct=0.12)
NO_RATE = CommercialThresholds()


# ── fixtures, hand-built so every number in an assertion is arithmetic ───────
def _metric(customer_id: str, product_id: str, revenue: str,
            gross_profit=None) -> models.CustomerItemMetric:
    """One persisted rollup row. ``gross_profit=None`` is revenue with no cost
    behind it, which is what ``aggregate_margin`` treats as uncosted."""
    return models.CustomerItemMetric(
        customer_item_metric_id=f"cim_{customer_id}_{product_id}",
        organization_id=ORG, customer_id=customer_id, product_id=product_id,
        revenue_12m=Decimal(revenue),
        gross_profit_12m=(Decimal(gross_profit)
                          if gross_profit is not None else None))


def _settlement(customer_id: str, *, days: int, raised: date,
                ref: str, amount: str = "10000") -> payments.Settlement:
    """One settled invoice, ``days`` after it was raised."""
    return payments.Settlement(
        party_id=customer_id, document_ref=ref, document_number=ref,
        document_date=raised, due_date=raised + timedelta(days=30),
        paid_on=raised + timedelta(days=days), amount=float(amount))


def _history(customer_id: str, *, days: int, count: int = 6,
             end: date = AS_OF, amount: str = "10000") -> list[payments.Settlement]:
    """``count`` invoices, all settled at the same lag, inside the window.

    ``amount`` is per invoice, and it matters as well as the count: the lag
    prices the account's whole revenue, so the settled documents have to
    account for a real share of it before that is allowed — see
    ``financing_min_settled_share``. A fixture declaring ₹2,00,000 of revenue
    and ₹60,000 of settlements is a book where seventy per cent never settled,
    which is the case the floor exists to refuse.
    """
    return [_settlement(customer_id, days=days, amount=amount,
                        raised=end - timedelta(days=30 * (i + 1)),
                        ref=f"{customer_id}-{i}")
            for i in range(count)]


def _build(metrics, settlements, *, thresholds=RATE, names=None) -> dict:
    return financing.build(metrics=metrics, settlements=settlements,
                           names=names or {}, as_of=AS_OF,
                           thresholds=thresholds)


def _row(result: dict, customer_id: str) -> dict:
    return next(r for r in result["accounts"]
                if r["customer_id"] == customer_id)


# ── the rate is owner-set, and nothing happens without it ────────────────────
def test_the_rate_has_no_default():
    """A plausible-looking default is the whole defect this field avoids.

    Pinned on the dataclass rather than only through ``build``: a later change
    that gives it a number would make every other refusal in this file pass
    while the platform quietly charged receivables at a rate nobody chose.
    """
    assert CommercialThresholds().cost_of_capital_annual_pct is None


def test_no_cost_of_capital_means_no_reading_at_all():
    """Not the margins with a blank column — nothing, and the field named."""
    result = _build([_metric("c1", "p1", "100000", "24000")],
                    _history("c1", days=60), thresholds=NO_RATE)

    assert result["rate_set"] is False
    assert result["measurable"] is False
    assert result["accounts"] == []
    assert result["totals"] == {}
    assert result["rate"] is None

    refusal, = result["unavailable"]
    assert refusal["kind"] == absence.COLLECTABLE
    assert "cost of capital" in refusal["reason"].lower()


def test_the_refusal_does_not_offer_the_stock_carrying_rate_as_a_stand_in():
    """The one substitution available here is also the wrong one.

    ``carrying_cost_annual_pct`` has a default and is a rate, so it is exactly
    what a later change reaches for. It pays for the warehouse as well as the
    money, and a receivable occupies no shelf.
    """
    result = _build([_metric("c1", "p1", "100000", "24000")],
                    _history("c1", days=60), thresholds=NO_RATE)

    refusal, = result["unavailable"]
    assert "warehouse" in refusal["reason"]


def test_a_zero_rate_is_refused_by_policy_rather_than_stored():
    """Zero is not "we borrow for nothing", it is a cleared field mistyped."""
    from app.commercial.policy import PolicyError, validate

    with pytest.raises(PolicyError):
        validate(replace(RATE, cost_of_capital_annual_pct=0.0))
    with pytest.raises(PolicyError):
        validate(replace(RATE, cost_of_capital_annual_pct=1.4))
    validate(replace(RATE, cost_of_capital_annual_pct=None))   # the honest state


# ── the arithmetic ──────────────────────────────────────────────────────────
def test_the_charge_is_revenue_times_days_over_the_year_times_the_rate():
    """₹1,00,000 held for 365 days at 12% costs ₹12,000. Checkable on paper."""
    assert financing.financing_cost(
        Decimal("100000"), 365, Decimal("0.12")) == Decimal("12000.00")
    # Half a year, half the charge — no rounding surprise in between.
    assert financing.financing_cost(
        Decimal("100000"), 73, Decimal("0.12")) == Decimal("2400.00")


def test_money_stays_decimal_end_to_end():
    """No float touches a rupee figure before ``to_dict`` renders it."""
    account = financing.Account(
        customer_id="c1", label="C1",
        margin=MarginAggregate(revenue=Decimal("100000"),
                               costed_revenue=Decimal("100000"),
                               gross_profit=Decimal("24000")),
        days=365, slow_days=365, settlements=6, rate=Decimal("0.12"),
        reason=financing.MEASURED)

    assert isinstance(account.charge, Decimal)
    assert isinstance(account.adjusted_profit, Decimal)
    assert account.adjusted_profit == Decimal("12000.00")
    # The ratio is a ratio, and the movement is percentage points.
    assert account.gross_margin == pytest.approx(0.24)
    assert account.adjusted_margin == pytest.approx(0.12)
    assert account.financing_drag_pp == 12.0


def test_the_slow_case_sits_beside_the_median_never_instead_of_it():
    """A treasurer plans against the tail; a book planned on the tail is wrong.

    Five settlements at 30 days and one at 200: the median stays 30 and the
    ninetieth percentile is the one that moves.
    """
    settled = _history("c1", days=30, count=5) + [
        _settlement("c1", days=200, raised=AS_OF - timedelta(days=300),
                    ref="c1-slow")]
    result = _build([_metric("c1", "p1", "100000", "24000")], settled)

    row = _row(result, "c1")
    assert row["days_to_pay"] == 30
    assert row["slow_days_to_pay"] == 200
    # Both charges are published, and the slow one is the larger.
    assert row["financing_cost"] < row["slow_financing_cost"]
    assert row["adjusted_margin"] > row["slow_adjusted_margin"]


def test_the_join_reorders_the_book_which_is_the_whole_point():
    """A fat margin funded for ten months loses to a thin one funded for one.

    24% settling in 300 days is 24 − 12 × 300/365 = 14.1% after financing; 19%
    settling in 30 days is 18.0%. The ranks swap, which is the sentence this
    module exists to let somebody say.
    """
    metrics = [_metric("slow", "p1", "100000", "24000"),
               _metric("prompt", "p1", "100000", "19000")]
    settled = _history("slow", days=300) + _history("prompt", days=30)
    result = _build(metrics, settled)

    slow, prompt = _row(result, "slow"), _row(result, "prompt")
    assert slow["rank_by_margin"] == 1 and prompt["rank_by_margin"] == 2
    assert slow["gross_margin"] > prompt["gross_margin"]
    assert slow["adjusted_margin"] < prompt["adjusted_margin"]
    assert slow["rank_by_adjusted_margin"] == 2
    assert prompt["rank_by_adjusted_margin"] == 1


def test_a_gap_the_rate_does_not_close_leaves_the_order_alone():
    """The counterweight, pinned so nobody "fixes" the module into agreeing.

    The brief's motivating pair — 24% at 95 days against 19% at 30 — does *not*
    invert at a 12% cost of capital: 65 extra days is 2.1 points of margin and
    the gap is 5. That is the right answer, and a version of this module that
    produced the intuitive one would be exaggerating the charge. Whether the
    join reorders a book is a fact about the owner's rate, not a feature.
    """
    metrics = [_metric("slow", "p1", "100000", "24000"),
               _metric("prompt", "p1", "100000", "19000")]
    settled = _history("slow", days=95) + _history("prompt", days=30)
    result = _build(metrics, settled)

    slow, prompt = _row(result, "slow"), _row(result, "prompt")
    assert slow["rank_by_adjusted_margin"] == 1
    assert prompt["rank_by_adjusted_margin"] == 2
    # The gap narrows even where it does not close, and that is the finding.
    assert (slow["gross_margin"] - prompt["gross_margin"]) > (
        slow["adjusted_margin"] - prompt["adjusted_margin"])


# ── the population rule: same rupees on both sides of the subtraction ────────
def test_the_charge_is_levied_on_costed_revenue_not_on_all_revenue():
    """The wave-1 blocker's shape: two populations in one expression.

    ₹2,00,000 of revenue, ₹1,50,000 of it costed — above the platform's
    ``min_cost_coverage``, because below that there is no figure at all and this
    test is about *which* revenue is charged, not about the floor. ₹30,000 of
    profit on the costed part, settled at 365 days, 12%. The charge is on the
    ₹1,50,000 the profit came from — ₹18,000, leaving ₹12,000. Charging the
    whole ₹2,00,000 would take ₹24,000, which reads as a pricing failure and is
    a cost-coverage gap.
    """
    metrics = [_metric("c1", "p1", "150000", "30000"),
               _metric("c1", "p2", "50000", None)]
    result = _build(metrics, _history("c1", days=365, amount="20000"))

    row = _row(result, "c1")
    assert row["revenue"] == 200000.0
    assert row["costed_revenue"] == 150000.0
    assert row["cost_coverage"] == 0.75
    assert row["financing_cost"] == 18000.0
    assert row["adjusted_profit"] == 12000.0
    assert row["adjusted_margin"] == 0.08
    assert row["reason"] == financing.PARTIAL_COST


def test_a_partly_costed_row_says_its_figure_understates():
    """The direction of the error is on the row, not left to the reader."""
    metrics = [_metric("c1", "p1", "100000", "30000"),
               _metric("c1", "p2", "100000", None)]
    result = _build(metrics, _history("c1", days=90))

    meaning = result["reason_meanings"][financing.PARTIAL_COST]
    assert "understate" in meaning
    assert _row(result, "c1")["cost_coverage"] == 0.5


# ── the refusals, and the guards behind them ────────────────────────────────
def test_a_thin_payment_history_refuses_rather_than_estimating():
    """Two settlements is one transaction wearing a suit — ``payments``' floor."""
    result = _build([_metric("c1", "p1", "100000", "24000")],
                    _history("c1", days=45, count=2))

    row = _row(result, "c1")
    assert row["reason"] == financing.NO_PAYMENT_HISTORY
    assert row["measured"] is False
    assert row["days_to_pay"] is None
    assert row["slow_days_to_pay"] is None
    assert row["financing_cost"] is None
    assert row["adjusted_profit"] is None
    assert row["adjusted_margin"] is None
    # The gross margin it *does* know is still reported — the refusal is about
    # the financing half, not about the account.
    assert row["gross_margin"] == 0.24


def test_the_book_median_is_never_substituted_for_a_thin_account():
    """Delete the floor and this test fails on the totals, not on a field.

    ``prompt`` has six settlements at 30 days; ``thin`` has two at 300. If the
    thin account silently borrowed the book's days figure it would acquire a
    charge, and the totals would stop being exactly the one measurable
    account's — which is what is asserted, because a fallback that produced a
    plausible number would satisfy every field-level assertion above.
    """
    metrics = [_metric("prompt", "p1", "100000", "24000"),
               _metric("thin", "p1", "500000", "120000")]
    settled = _history("prompt", days=30) + _history("thin", days=300, count=2)
    result = _build(metrics, settled)

    assert _row(result, "thin")["reason"] == financing.NO_PAYMENT_HISTORY
    totals = result["totals"]
    assert totals["accounts"] == 1
    assert totals["costed_revenue"] == 100000.0
    assert totals["gross_profit"] == 24000.0
    # ₹1,00,000 for 30 days at 12% = ₹986.30.
    assert totals["financing_cost"] == pytest.approx(986.30, abs=0.01)
    assert totals["excluded_accounts"] == 1
    assert totals["excluded_revenue"] == 500000.0

    refusal = next(u for u in result["unavailable"]
                   if u["series"] == "financing_cost_for_thin_payment_history")
    assert refusal["kind"] == absence.TRANSIENT


def test_an_account_with_no_costed_revenue_refuses_rather_than_reporting_a_loss():
    """Unknown profit minus a real charge is a confident negative."""
    result = _build([_metric("c1", "p1", "100000", None)],
                    _history("c1", days=60))

    row = _row(result, "c1")
    assert row["reason"] == financing.NO_COSTED_REVENUE
    assert row["gross_profit"] is None
    assert row["adjusted_profit"] is None
    assert row["adjusted_margin"] is None


def test_a_thin_account_is_absent_from_the_totals_not_averaged_into_them():
    """Delete the coverage guard and this test fails on the totals.

    ``thin`` has 20% cost coverage on a large book. Admitting it would move
    every total below, so the assertions are the measurable account's exact
    figures rather than a flag on the thin row.
    """
    metrics = [_metric("full", "p1", "100000", "24000"),
               _metric("thin", "p1", "100000", "30000"),
               _metric("thin", "p2", "400000", None)]
    result = _build(metrics, _history("full", days=30) + _history("thin", days=30))

    thin = _row(result, "thin")
    assert thin["reason"] == financing.THIN_COST_COVERAGE
    assert thin["cost_coverage"] == 0.2
    assert thin["adjusted_profit"] is None
    assert thin["financing_cost"] is None

    totals = result["totals"]
    assert totals["accounts"] == 1
    assert totals["costed_revenue"] == 100000.0
    assert totals["gross_profit"] == 24000.0
    assert totals["excluded_accounts"] == 1

    refusal = next(
        u for u in result["unavailable"]
        if u["series"] == "financing_adjusted_margin_for_uncosted_accounts")
    assert refusal["kind"] == absence.COLLECTABLE


def test_a_refused_account_never_carries_a_days_figure_to_multiply():
    """A days column beside a withheld charge invites the reader to finish it."""
    metrics = [_metric("c1", "p1", "100000", "10000"),
               _metric("c1", "p2", "900000", None)]
    result = _build(metrics, _history("c1", days=45))

    row = _row(result, "c1")
    assert row["reason"] == financing.THIN_COST_COVERAGE
    assert row["days_to_pay"] is None
    assert row["slow_days_to_pay"] is None


def test_measured_and_refused_rows_are_all_or_nothing():
    """The classification and the arithmetic cannot drift apart quietly.

    Every row is either fully figured or fully blank. A half-populated row is
    how a refusal turns into a number nobody chose — the reader sees three of
    four fields and fills in the fourth. Written over a book holding one of
    each refusal so it exercises all four branches at once.
    """
    metrics = [_metric("full", "p1", "100000", "24000"),
               _metric("thin_history", "p1", "100000", "24000"),
               _metric("uncosted", "p1", "100000", None),
               _metric("thin_cover", "p1", "10000", "3000"),
               _metric("thin_cover", "p2", "90000", None)]
    settled = (_history("full", days=30)
               + _history("thin_history", days=30, count=2)
               + _history("uncosted", days=30)
               + _history("thin_cover", days=30))
    result = _build(metrics, settled)

    figures = ("days_to_pay", "slow_days_to_pay", "financing_cost",
               "adjusted_profit", "adjusted_margin", "financing_drag_pp",
               "slow_financing_cost", "slow_adjusted_profit",
               "slow_adjusted_margin")
    for row in result["accounts"]:
        present = [f for f in figures if row[f] is not None]
        assert (len(present) == len(figures) if row["measured"]
                else present == []), row

    assert set(result["counts"]) == {
        financing.MEASURED, financing.NO_PAYMENT_HISTORY,
        financing.NO_COSTED_REVENUE, financing.THIN_COST_COVERAGE}


# ── aggregation ─────────────────────────────────────────────────────────────
def test_the_book_is_summed_never_averaged_across_customers():
    """Σ adjusted profit ÷ Σ costed revenue, so size gets its say.

    A ₹9,00,000 account at 20% settling in 365 days and a ₹1,00,000 account at
    40% settling in 365 days. The mean of the two adjusted margins is 16%; the
    aggregate is (180000 + 40000 − 132000) ÷ 1000000 = 8.8%.
    """
    metrics = [_metric("big", "p1", "900000", "180000"),
               _metric("small", "p1", "100000", "40000")]
    settled = (_history("big", days=365, amount="70000")
               + _history("small", days=365))
    totals = _build(metrics, settled)["totals"]

    assert totals["costed_revenue"] == 1000000.0
    assert totals["gross_profit"] == 220000.0
    assert totals["financing_cost"] == 120000.0
    assert totals["adjusted_profit"] == 100000.0
    assert totals["adjusted_margin"] == 0.1
    assert totals["gross_margin"] == 0.22
    assert totals["financing_drag_pp"] == 12.0
    # The mean of 0.08 and 0.28 is 0.18. It is not what came back.
    assert totals["adjusted_margin"] != pytest.approx(0.18)


def test_a_large_unpaid_invoice_does_not_read_as_a_fast_payer():
    """The count floor alone lets the worst account look like the best.

    Six ₹10,000 invoices settled on the day, and ₹9,00,000 of revenue behind
    them — so 93% of what this account bought in the window has not settled and
    is not in the lag at all. ``payments`` is satisfied: six settlements clears
    its count floor and the median lag is zero days. Charged on that lag the
    account costs nothing to fund and heads a list titled "what your credit
    costs", while holding more of our cash than anyone on it.

    Pinned as a refusal with a named reason rather than as "not first", because
    a ranking assertion passes for the wrong reason the moment the fixture
    changes.
    """
    metrics = [_metric("slow", "p1", "900000", "180000")]
    result = _build(metrics, _history("slow", days=0, amount="10000"))

    row = _row(result, "slow")
    assert row["financing_cost"] is None
    assert row["reason"] == financing.THIN_SETTLED_VALUE
    # And no days figure to finish the multiplication with.
    assert row["days_to_pay"] is None


# ── window alignment ────────────────────────────────────────────────────────
def test_settlements_outside_the_metrics_window_are_not_measured():
    """A median from four years of history over one year of revenue is two
    windows in one product — the discipline ``gmroi`` exists to demonstrate."""
    old = [_settlement("c1", days=10, raised=AS_OF - timedelta(days=800 + i),
                       ref=f"old-{i}") for i in range(6)]
    recent = _history("c1", days=90, count=4)
    result = _build([_metric("c1", "p1", "100000", "24000")], old + recent)

    assert result["window"]["days"] == financing.WINDOW_DAYS
    assert result["window"]["settlements_in_window"] == 4
    # 90 days, not the 10 the out-of-window half would have pulled it toward.
    assert _row(result, "c1")["days_to_pay"] == 90


def test_a_book_whose_settlements_all_fall_outside_the_window_refuses():
    """The window filter is a guard, so it has to be able to empty the screen."""
    old = [_settlement("c1", days=10, raised=AS_OF - timedelta(days=800 + i),
                       ref=f"old-{i}") for i in range(6)]
    result = _build([_metric("c1", "p1", "100000", "24000")], old)

    assert result["measurable"] is False
    assert _row(result, "c1")["reason"] == financing.NO_PAYMENT_HISTORY


# ── over HTTP, at the role this reading needs ───────────────────────────────
PATH = "/api/v1/insight/customer-financing"


def _token(api_client, email: str) -> str:
    return api_client.post("/api/v1/auth/login", json={
        "email": email, "password": SEED_PASSWORD}).json()["token"]


def test_a_salesperson_cannot_reach_it(api_client):
    """Margin by construction: there is no version of this with cost removed."""
    for email, expected in (("r.nair@pie.example", 403),
                            ("m.rao@pie.example", 200),
                            ("s.menon@pie.example", 200)):
        r = api_client.get(PATH, headers={
            "Authorization": f"Bearer {_token(api_client, email)}"})
        assert r.status_code == expected, (email, r.text)


def test_the_endpoint_stamps_a_thresholds_version(api_client):
    """``_envelope`` exists to do this; a response that 500s stamps nothing."""
    r = api_client.get(PATH, headers={
        "Authorization": f"Bearer {_token(api_client, 's.menon@pie.example')}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thresholds_version"]
    assert body["empty_reason"]           # nothing synced on a seeded book
