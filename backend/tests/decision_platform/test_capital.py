"""Capital in the cycle: the arithmetic, and the refusals it must not soften.

The sums are easy — three balances added up, one profit divided by one average.
What is worth pinning is everything that makes the screen emptier, because a
capital screen with holes in it is exactly the kind of chart somebody wants
filled, and every plausible way of filling one here is wrong in the direction
that reads as good news:

1. **An unobserved month has no capital employed.** ``None``, never zero — a
   zero would report an entity as funding its trade out of nothing, which is
   both a smaller number and a flattering one.
2. **The cost-coverage refusal propagates.** Below the floor there is no cost of
   sales, so there is no gross profit, no return and no money-in-cycle figure.
   The one thing that survives is capital employed itself, because a sum of
   balances does not depend on how many sale lines carried a bill — and that
   exception is asserted too, so nobody "fixes" it later in either direction.
3. **The numerator and the denominator cover the same span.** A window missing
   one of its month-end balances is not averaged over the two that survived:
   gross profit would span three months of trading and the average whichever
   month ends somebody happened to look at.
4. **Nothing is annualised.** A quarter's return is a quarter's return.
5. **Nothing is pooled.** Two books are two series, and there is no group total
   for the same reason ``cycle`` has none.
6. **Money is ``Decimal``** all the way to the rounding in ``to_dict``.

Plus the two readings that only exist here: what a day off DSO releases, capped
at the collection period actually measured, and the capital a rupee of extra
revenue would need.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.insight import absence, capital, cycle, periods

# The two-connection book next door, imported rather than copied. It exists to
# exercise the router's resolution of a customer, a vendor and an item into the
# company whose balance sheet they belong to, and that resolution is now shared
# by both endpoints — so a second fixture would be the responsibility
# duplication CLAUDE.md §2 asks to prevent, and the copy that drifted would be
# the one describing the screen nobody was looking at.
from .test_cash_cycle import two_books  # noqa: F401

#: A month end well inside a year, so ``months_back`` never straddles a
#: boundary a reader has to check by hand. The same anchor ``test_cash_cycle``
#: uses, deliberately — the two files describe one replay.
AS_OF = date(2026, 6, 30)

SLS, FOURU = "cx_sls", "cx_4u"
BOOKS = {SLS: "SLS Engineers", FOURU: "4U Precision"}

TH = CommercialThresholds()

#: Month ends reported, and month ends replayed. The gap is the two leading
#: months every reported point's window average needs — the router applies the
#: same arithmetic, and a test that replayed only what it reports would be
#: testing a series whose first two rows refuse for a reason the caller caused.
MONTHS = 4
REPLAYED = MONTHS + cycle.WINDOW_MONTHS - 1


def _points() -> list[date]:
    """The month ends the replay covers, oldest first."""
    return [p.end for p in periods.months_back(AS_OF, REPLAYED)]


def _sold(book: str, when: date, revenue: str, cogs: str | None) -> cycle.Sold:
    return cycle.Sold(book=book, date=when, revenue=Decimal(revenue),
                      cogs=None if cogs is None else Decimal(cogs))


def _daily_sales(book: str, start: date, end: date, *, per_day: str = "1000",
                 cogs: str | None = "750") -> list[cycle.Sold]:
    """One costed line a day, so every window has a denominator."""
    out, day = [], start
    while day <= end:
        out.append(_sold(book, day, per_day, cogs))
        day += timedelta(days=1)
    return out


def _monthly(book: str, first: date, *, amount: str, prefix: str
             ) -> list[cycle.Document]:
    """One document on the 10th of every month from ``first`` to ``AS_OF``."""
    out: list[cycle.Document] = []
    when = date(first.year, first.month, 10)
    while when <= AS_OF:
        out.append(cycle.Document(book=book, date=when,
                                  ref=f"{prefix}-{when.isoformat()}",
                                  total=Decimal(amount)))
        when = date(when.year + when.month // 12, when.month % 12 + 1, 10)
    return out


def _settled(documents: list[cycle.Document], *, after_days: int = 30,
             skip_last: int = 1) -> list[cycle.Applied]:
    """Each document settled ``after_days`` later, bar the newest few.

    With one document a month settled a month later, exactly one is outstanding
    at every month end — which makes the position constant across the series and
    every assertion below checkable by hand.
    """
    keep = documents[:len(documents) - skip_last] if skip_last else documents
    return [cycle.Applied(book=d.book, on=d.date + timedelta(days=after_days),
                          document_ref=d.ref, amount=d.total) for d in keep]


def _held(book: str, on: date, units: str = "100",
          unit_cost: str | None = "500") -> cycle.Held:
    return cycle.Held(book=book, on=on, units=Decimal(units),
                      unit_cost=None if unit_cost is None else Decimal(unit_cost))


def _inputs(**kw) -> dict:
    base = dict(invoices=[], receipts=[], credits=[], bills=[],
                bill_payments=[], sold=[], held=[], books=BOOKS, as_of=AS_OF,
                thresholds=TH, months=REPLAYED)
    base.update(kw)
    return base


def _build(**kw) -> dict:
    """The module under test, over the replay the router would hand it."""
    base = _inputs(**kw)
    return capital.build(replays=cycle.replay(**base), sold=base["sold"],
                         as_of=AS_OF, thresholds=TH, months=MONTHS)


def _entity(result: dict, connection_id: str) -> dict:
    return next(e for e in result["entities"]
                if e["connection_id"] == connection_id)


def _month(entity: dict, ends_on: date) -> dict:
    return next(m for m in entity["months"] if m["ends_on"] == ends_on.isoformat())


# ── a complete book, so the arithmetic has somewhere to stand ───────────────
#
# One invoice and one bill a month, each settled a month later, so exactly one
# of each is outstanding at every month end: receivables ₹1,00,000, payables
# ₹60,000. The shelf is 100 units at ₹500 on every month end: ₹50,000. So
# capital employed is ₹90,000 at every point, and an average over any window of
# it is ₹90,000 too — which means a wrong window shows up as a wrong *average*
# rather than hiding inside a plausible drift.
RECEIVABLE = Decimal("100000")
PAYABLE = Decimal("60000")
SHELF = Decimal("50000")
CAPITAL = RECEIVABLE + SHELF - PAYABLE


@pytest.fixture()
def whole_book() -> dict:
    """One entity with every leg backed, over the full replayed series."""
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    bills = _monthly(SLS, date(2025, 1, 1), amount="60000", prefix="bill")
    return _build(
        invoices=invoices, receipts=_settled(invoices),
        bills=bills, bill_payments=_settled(bills),
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in _points()])


# ── 1. capital employed is net of payables ──────────────────────────────────
def test_capital_employed_is_receivables_plus_stock_less_payables(whole_book):
    """The definition, and the sign the cycle beside it already uses.

    ``ccc`` subtracts the payable leg on the ground that supplier credit funds
    part of the cycle. A capital figure that added it back would contradict the
    days figure printed next to it, so the two are checked against each other
    here rather than left to agree by intention.
    """
    month = _month(_entity(whole_book, SLS), _points()[-1])

    assert month["receivables"] == float(RECEIVABLE)
    assert month["payables"] == float(PAYABLE)
    assert month["inventory"] == float(SHELF)
    assert month["capital_employed"] == float(CAPITAL) == 90000.0
    # Gross of payables would be ₹1,50,000. Pinned as a number rather than as a
    # subtraction, because the failure is a sign flip and a sign flip passes any
    # test written as `a + b - c`.
    assert month["capital_employed"] != float(RECEIVABLE + SHELF)


def test_the_average_is_the_mean_of_the_windows_own_month_ends(whole_book):
    """Checkable from the response itself, which is why it is a closing-balance
    mean rather than something a reader has to take on trust."""
    entity = _entity(whole_book, SLS)
    ends = _points()
    month = _month(entity, ends[-1])

    assert month["window_points"] == cycle.WINDOW_MONTHS
    assert month["capital_points"] == cycle.WINDOW_MONTHS
    assert month["avg_capital_employed"] == float(CAPITAL)


# ── 2. the return, over exactly the window its profit came from ─────────────
def test_the_return_is_window_profit_over_window_capital(whole_book):
    """The identity a reader has to be able to check by hand.

    Both halves are on the row: gross profit is the window's costed revenue less
    its cost of sales, and the denominator is the average printed beside it. A
    leg that used a different span shows up here as a factor of three.
    """
    month = _month(_entity(whole_book, SLS), _points()[-1])
    days = month["window"]["days"]

    # 91 days at ₹1,000 revenue and ₹750 cost: ₹91,000 and ₹68,250.
    assert days == 91
    assert month["revenue"] == 1000.0 * days
    assert month["cogs"] == 750.0 * days
    assert month["gross_profit"] == 250.0 * days
    assert month["return_on_capital"] == pytest.approx(
        month["gross_profit"] / month["avg_capital_employed"], abs=1e-4)


def test_nothing_is_annualised(whole_book):
    """A quarter's return is a quarter's return.

    Scaling it to a year would print a projection about three quarters that have
    not happened, in the same typeface as the measurement. The check is that the
    figure is the window's own ratio and not that ratio times 365/91.
    """
    month = _month(_entity(whole_book, SLS), _points()[-1])
    window_ratio = month["gross_profit"] / month["avg_capital_employed"]

    assert month["return_on_capital"] == pytest.approx(window_ratio, abs=1e-4)
    assert month["return_on_capital"] < window_ratio * 2
    assert "projection" in _build()["basis"]["not_annualised"]


def test_a_window_missing_a_month_end_states_no_return_at_all(whole_book):
    """Requirement 3: numerator and denominator cannot span different spans.

    March's shelf is removed, so March has no capital employed. Every window
    containing March — March, April and May — then has gross profit from three
    months of trading and capital from two, and an average over the two would be
    a denominator describing a different period from its numerator. All three
    refuse; June, whose window opens in April, is untouched.
    """
    ends = _points()
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    bills = _monthly(SLS, date(2025, 1, 1), amount="60000", prefix="bill")
    entity = _entity(_build(
        invoices=invoices, receipts=_settled(invoices),
        bills=bills, bill_payments=_settled(bills),
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in ends if end != ends[2]],
    ), SLS)

    march = _month(entity, ends[2])
    assert march["capital_employed"] is None
    assert march["avg_capital_employed"] is None
    assert march["return_on_capital"] is None
    assert march["working_capital_per_rupee"] is None

    for end in (ends[3], ends[4]):
        row = _month(entity, end)
        assert row["capital_employed"] is not None   # its own shelf was seen
        assert row["avg_capital_employed"] is None, row["month"]
        assert row["capital_points"] == cycle.WINDOW_MONTHS - 1
        assert "month ends" in row["why"]["avg_capital_employed"]

    june = _month(entity, ends[-1])
    assert june["avg_capital_employed"] == float(CAPITAL)
    assert june["return_on_capital"] is not None


def test_an_unobserved_month_has_no_capital_rather_than_none_held(whole_book):
    """Requirement 6, and the one that would flatter the book most.

    Zero capital employed reads as an entity funding its trade out of nothing —
    a smaller number and a better-looking one. The refusal carries the cycle's
    own reason for it rather than inventing a second wording.
    """
    ends = _points()
    entity = _entity(_build(
        invoices=_monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv"),
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, ends[0])],
    ), SLS)

    for row in entity["months"]:
        assert row["capital_employed"] is None
        assert row["inventory"] is None
        assert "capital_employed" in row["unknown"]
        assert "no stock" in row["why"]["capital_employed"].lower()


def test_a_month_before_the_reliability_boundary_states_no_capital():
    """The refusal ``cycle`` already makes for DSO and DPO, inherited.

    This is the one guard on this module that cannot be written against a value.
    ``cycle._position`` returns ``raised - settled`` unconditionally, so a month
    end before the first document on record comes back as a *number* — nil, or
    negative where a receipt settles an invoice older than the sync window —
    rather than as ``None``. A guard on ``is None`` therefore never fires, and
    the screen states a headline capital figure for exactly the months the cash
    cycle beside it has just refused to state a day count for.

    Two books, because the two legs fail independently: one has no bill on
    record at all, the other's bills start late. Both must refuse, and the
    reason must name the leg — a reader who sees ``None`` with no cause reads it
    as a gap in the sync rather than as a boundary.
    """
    ends = _points()
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    held = [_held(SLS, end) for end in ends]
    sold = _daily_sales(SLS, date(2025, 1, 1), AS_OF)

    # (a) No bill on record: the payable leg is unknown, not nil.
    no_bills = _entity(_build(invoices=invoices, receipts=_settled(invoices),
                              sold=sold, held=held), SLS)
    for row in no_bills["months"]:
        assert row["capital_employed"] is None
        assert "capital_employed" in row["unknown"]
        assert "Payables cannot be reconstructed" in row["why"]["capital_employed"]

    # (b) Bills that begin after the earliest reported window opens. The last
    #     month end is far enough past the boundary to still be stated, so this
    #     pins the boundary rather than a blanket refusal.
    late = _monthly(SLS, ends[-1].replace(day=1), amount="60000", prefix="late")
    partial = _entity(_build(
        invoices=invoices, receipts=_settled(invoices), sold=sold, held=held,
        bills=late, bill_payments=_settled(late)), SLS)
    refused = [r for r in partial["months"] if r["capital_employed"] is None]
    assert refused, "the boundary refused nothing — the guard is not firing"
    assert all("cannot be reconstructed" in r["why"]["capital_employed"]
               for r in refused)
    # And everything that leans on it goes too, rather than averaging over the
    # month ends that happened to survive.
    for row in refused:
        assert row["avg_capital_employed"] is None
        assert row["return_on_capital"] is None
        assert row["working_capital_per_rupee"] is None


# ── 3. the cost-coverage floor, propagated ──────────────────────────────────
def test_below_the_cost_coverage_floor_no_earning_is_stated(whole_book):
    """Requirement 6, second half: nothing that needs cost survives the floor.

    One line in three carries a cost record — a third, below the 60% the
    platform treats as enough. ``cycle`` refuses cost of sales for the window,
    and gross profit, the return and the money-in-cycle figure all go with it.
    Summing the costed third would be a smaller number that looks real.
    """
    ends = _points()
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    sold: list[cycle.Sold] = []
    day = date(2025, 1, 1)
    while day <= AS_OF:
        sold.append(_sold(SLS, day, "1000", "750" if day.day % 3 == 0 else None))
        day += timedelta(days=1)

    bills = _monthly(SLS, date(2025, 1, 1), amount="60000", prefix="bill")
    row = _month(_entity(_build(
        invoices=invoices, receipts=_settled(invoices), sold=sold,
        bills=bills, bill_payments=_settled(bills),
        held=[_held(SLS, end) for end in ends]), SLS), ends[-1])

    assert row["cost_coverage"] < TH.min_cost_coverage
    assert row["cogs"] is None
    assert row["gross_profit"] is None
    assert row["return_on_capital"] is None
    assert row["cash_in_cycle"] is None
    assert "cost record" in row["why"]["gross_profit"]

    # And the exception, asserted so it cannot be "tidied" in either direction:
    # capital employed is a sum of balances and does not depend on how many sale
    # lines carried a bill, so it is still stated below the coverage floor.
    assert row["capital_employed"] == float(CAPITAL)
    assert row["working_capital_per_rupee"] is not None


def test_partial_cost_coverage_is_marked_rather_than_compensated_for(whole_book):
    """``gmroi``'s rule, applied here: say which lines the figure speaks for.

    Above the floor, gross profit is summed over the costed lines only, so the
    return is an understatement of known size. The fix is not a different
    denominator — that is the ``weather`` defect — it is the coverage share
    travelling with the number.
    """
    ends = _points()
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    sold: list[cycle.Sold] = []
    day = date(2025, 1, 1)
    while day <= AS_OF:
        sold.append(_sold(SLS, day, "1000", None if day.day % 4 == 0 else "750"))
        day += timedelta(days=1)

    bills = _monthly(SLS, date(2025, 1, 1), amount="60000", prefix="bill")
    row = _month(_entity(_build(
        invoices=invoices, receipts=_settled(invoices), sold=sold,
        bills=bills, bill_payments=_settled(bills),
        held=[_held(SLS, end) for end in ends]), SLS), ends[-1])

    assert row["cost_coverage"] >= TH.min_cost_coverage
    assert row["confidence"] == "PARTIAL_COST"
    assert row["return_on_capital"] is not None
    # Gross profit speaks for the costed lines; revenue is every line, and the
    # two are reported separately so the gap is visible rather than resolved.
    assert row["costed_revenue"] < row["revenue"]
    assert row["gross_profit"] == pytest.approx(
        row["costed_revenue"] - row["cogs"], abs=0.01)


def test_a_fully_costed_window_is_measured(whole_book):
    row = _month(_entity(whole_book, SLS), _points()[-1])
    assert row["cost_coverage"] == 1.0
    assert row["confidence"] == "MEASURED"


# ── 4. money in the cycle, and what a day of collection releases ────────────
def test_money_in_the_cycle_is_cycle_days_times_daily_cost_of_sales(whole_book):
    """The cycle restated as money, beside the direct sum rather than instead.

    They are the same quantity by two routes and they do not agree, because the
    receivable leg divides a gross balance by billings while the other two run
    on cost net of input tax. Both are asserted, including that they differ, so
    nobody later "reconciles" them by dropping one.
    """
    month = _month(_entity(whole_book, SLS), _points()[-1])
    daily_cogs = month["cogs"] / month["window"]["days"]

    assert daily_cogs == 750.0
    assert month["cash_in_cycle"] == pytest.approx(month["ccc"] * daily_cogs,
                                                   abs=0.01)
    assert month["cash_in_cycle"] != month["capital_employed"]
    assert "net of input tax" in _build()["basis"]["tax"]


def test_an_unstated_cycle_names_why_the_money_figure_is_missing():
    """A blank with no reason beside it is what every refusal here exists to
    avoid.

    With no stock observed there is no inventory leg and therefore no composite,
    so the money-in-cycle figure has no days to price. The reason carried onto
    the row is the *cycle's own* wording — a second sentence written here about
    the same gap is how two screens come to explain one absence two ways.
    """
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    row = _month(_entity(_build(
        invoices=invoices, receipts=_settled(invoices),
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF), held=[]), SLS),
        _points()[-1])

    assert row["ccc"] is None
    assert row["cash_in_cycle"] is None
    assert "cash_in_cycle" in row["unknown"]
    assert "no stock was observed" in row["why"]["cash_in_cycle"].lower()


def test_a_day_off_dso_releases_one_day_of_billings(whole_book):
    """Where the sensitivity comes from: the definition of DSO, not a heuristic.

    ``DSO = receivables ÷ billings × days``, so one day of the leg is one day of
    its own denominator. Three invoices of ₹1,00,000 fall in the window, over 91
    days.
    """
    month = _month(_entity(whole_book, SLS), _points()[-1])

    assert month["billed"] == 300000.0
    assert month["release_per_dso_day"] == pytest.approx(300000 / 91, abs=0.01)
    for scenario in month["dso_releases"]:
        # Against the unrounded rate, not the rounded one on the row: comparing
        # two figures that were each rounded to the paisa proves only that both
        # rounded.
        assert scenario["cash_released"] == pytest.approx(
            scenario["days_applied"] * month["billed"] / 91, abs=0.01)
    assert [s["days"] for s in month["dso_releases"]] == \
        list(TH.capital_dso_reduction_days)


def test_a_reduction_larger_than_the_measured_dso_is_capped_and_says_so():
    """Cash that is not owed cannot be released by collecting faster.

    A book collecting in eight days cannot free fifteen days of billings — there
    is not fifteen days of receivable there. The scenario is capped at what is
    outstanding and carries the flag, so a reader sees a target nearly reached
    rather than a rupee figure that cannot arrive.
    """
    ends = _points()
    # Invoiced monthly and collected in a week, so almost nothing is ever open.
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    entity = _entity(_build(
        invoices=invoices, receipts=_settled(invoices, after_days=3, skip_last=0),
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in ends]), SLS)
    month = _month(entity, ends[-1])

    assert month["dso"] == 0.0 or month["dso"] < max(TH.capital_dso_reduction_days)
    for scenario in month["dso_releases"]:
        assert scenario["days_applied"] <= month["dso"] + 0.05
        if scenario["days"] > month["dso"]:
            assert scenario["capped"] is True


# ── 5. working capital per rupee of incremental revenue ─────────────────────
def test_working_capital_per_rupee_is_average_capital_over_window_revenue(
        whole_book):
    """CS4, and the same window as everything else on the row."""
    month = _month(_entity(whole_book, SLS), _points()[-1])

    assert month["working_capital_per_rupee"] == pytest.approx(
        month["avg_capital_employed"] / month["revenue"], abs=1e-4)
    # ₹90,000 of capital carrying ₹91,000 of quarterly revenue.
    assert month["working_capital_per_rupee"] == pytest.approx(0.989, abs=0.001)


def test_a_supplier_funded_cycle_keeps_its_sign_and_states_no_return():
    """The asymmetry between an intensity and a return, asserted deliberately.

    With payables larger than receivables and stock together, capital employed
    is negative: growth *releases* cash, and the intensity says so with its
    sign. A return divided by that base would print as a loss on a book that is
    making money, so it is refused with the reason on the row.
    """
    ends = _points()
    invoices = _monthly(SLS, date(2025, 1, 1), amount="100000", prefix="inv")
    bills = _monthly(SLS, date(2025, 1, 1), amount="400000", prefix="bill")
    month = _month(_entity(_build(
        invoices=invoices, receipts=_settled(invoices),
        bills=bills, bill_payments=_settled(bills),
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in ends]), SLS), ends[-1])

    assert month["capital_employed"] < 0
    assert month["working_capital_per_rupee"] < 0
    assert month["return_on_capital"] is None
    assert "no capital standing" in month["why"]["return_on_capital"]


# ── 6. never pooled ─────────────────────────────────────────────────────────
def test_two_books_are_two_series_and_there_is_no_group_total():
    """``cycle``'s rule, inherited without exception.

    Three balance sheets added together produce a capital figure belonging to no
    legal entity, and a return on it would divide one book's profit by another
    book's stock. The refusal is also *stated*, because a missing total with
    nothing said reads as an omission somebody should fix.
    """
    ends = _points()
    sold, invoices, bills, held = [], [], [], []
    for book, amount in ((SLS, "100000"), (FOURU, "250000")):
        invoices += _monthly(book, date(2025, 1, 1), amount=amount, prefix=book)
        bills += _monthly(book, date(2025, 1, 1), amount="60000",
                          prefix=f"bill-{book}")
        sold += _daily_sales(book, date(2025, 1, 1), AS_OF)
        held += [_held(book, end) for end in ends]
    result = _build(invoices=invoices, receipts=_settled(invoices), sold=sold,
                    bills=bills, bill_payments=_settled(bills), held=held)

    assert {e["connection_id"] for e in result["entities"]} == {SLS, FOURU}
    assert [e["label"] for e in result["entities"]] == ["4U Precision",
                                                        "SLS Engineers"]
    # No total, under any of the names one would be given.
    for forbidden in ("total", "totals", "group", "all_books", "consolidated"):
        assert forbidden not in result

    sls = _month(_entity(result, SLS), ends[-1])
    fouru = _month(_entity(result, FOURU), ends[-1])
    assert fouru["receivables"] == 2.5 * sls["receivables"]
    assert fouru["capital_employed"] != sls["capital_employed"]

    refusal = next(u for u in result["unavailable"]
                   if u["series"] == "group_capital_employed")
    assert refusal["kind"] == absence.PERMANENT


# ── 7. money is Decimal, and the refusals are typed ─────────────────────────
def test_every_position_stays_decimal_until_the_response_rounds_it():
    """Money is ``Decimal`` — a float in the middle of a division of two money
    figures is a rounding error nobody can trace back.

    Asserted on the ``Point`` rather than on the payload, because the payload is
    floats by design and a test written against it could not tell the difference
    between arithmetic in ``Decimal`` and arithmetic in ``float``.
    """
    month = cycle.Month(
        period=periods.Period(date(2026, 6, 1), date(2026, 6, 30), "Jun 2026"),
        window=periods.Period(date(2026, 4, 1), date(2026, 6, 30), "Apr–Jun 2026"),
        window_days=91,
        receivables=Decimal("0.10"), payables=Decimal("0.30"),
        inventory=Decimal("0.20"), billed=Decimal("91"),
        cogs=Decimal("0.30"), cost_coverage=1.0)
    point = capital._point(month, [_sold(SLS, date(2026, 6, 1), "0.50", "0.30")],
                           receivable_from=date(2020, 1, 1),
                           payable_from=date(2020, 1, 1))

    # 0.10 + 0.20 − 0.30 is exactly nil in Decimal and 2.7e-17 in float.
    assert isinstance(point.capital_employed, Decimal)
    assert point.capital_employed == Decimal("0")
    assert isinstance(point.gross_profit, Decimal)
    assert point.gross_profit == Decimal("0.20")


def test_the_per_principal_question_is_refused_rather_than_allocated():
    """CS4's second half, and the line ``commercial/principals`` draws.

    A bill names its vendor and stock attributes to a principal off the purchase
    bill, but a receipt is applied to an *invoice* rather than to its lines — so
    whose rupee has come back cannot be recovered. Splitting the largest of the
    three legs by revenue share would be an allocation with no evidence behind
    it, and the manufacturer fallback ``principals`` keeps off purchase-side
    numbers would not help: it answers a different question.
    """
    refusal = next(u for u in _build()["unavailable"]
                   if u["series"] == "working_capital_per_principal")

    assert refusal["kind"] == absence.PERMANENT
    assert "applied to" in refusal["reason"]


def test_this_is_not_roce_and_says_so():
    """Gross profit is not operating profit, and operating working capital is
    not capital employed. Both gaps are named so the figure is never compared
    with a published ratio computed on a different base."""
    result = _build()
    refusal = next(u for u in result["unavailable"]
                   if u["series"] == "return_on_capital_employed_as_published")

    # BUILDABLE, not PERMANENT: nothing here is unknowable, it is un-ingested.
    assert refusal["kind"] == absence.BUILDABLE
    assert "must not be compared" in refusal["reason"]
    assert "not ROCE" in result["definition"]["return_on_capital"]


def test_every_refusal_carries_a_kind_from_the_closed_set():
    for entry in _build()["unavailable"]:
        assert entry["kind"] in absence.KINDS, entry
        assert entry["reason"].strip()


def test_the_response_carries_the_policy_that_produced_it():
    """The reduction grid is policy, so it is inside the version hash — a rupee
    figure whose scenario nobody can name is unexplainable a quarter later."""
    result = _build()

    assert result["thresholds_version"] == TH.version
    assert result["dso_reduction_days"] == list(TH.capital_dso_reduction_days)
    other = CommercialThresholds(capital_dso_reduction_days=(7,))
    assert other.version != TH.version


# ── 8. the endpoint ─────────────────────────────────────────────────────────
def _token(api_client, email: str) -> str:
    return api_client.post("/api/v1/auth/login", json={
        "email": email, "password": "change-me-now"}).json()["token"]


def test_the_screen_is_manager_and_owner_only(api_client):
    """Scoped like ``/payables`` and ``/quote-pricing``, for a stronger reason.

    The denominator is the shelf at what it cost and the numerator is gross
    profit. There is no version of this with the economics removed — what would
    be left is a day count, which ``/cash-cycle`` already is — so an honest 403
    beats a page stripped to nothing.
    """
    path = "/api/v1/insight/capital"
    for email, expected in (("r.nair@pie.example", 403),
                            ("m.rao@pie.example", 200),
                            ("s.menon@pie.example", 200)):
        r = api_client.get(path, headers={
            "Authorization": f"Bearer {_token(api_client, email)}"})
        assert r.status_code == expected, (email, r.text)
        # The *role* gate, named. `current_principal` also 403s an account still
        # holding the password it was issued, so a bare status check would pass
        # just as happily against a book where the role rule had gone.
        if expected == 403:
            assert r.json()["detail"] == "Manager or owner role required"


def test_the_endpoint_files_each_book_separately_over_real_rows(two_books):
    """The router half, which the builder's own tests structurally cannot reach.

    Resolving a customer, a vendor and an item into the connected company whose
    balance sheet the row belongs to only exists in ``_cycle_inputs``, and only
    runs against rows. Two connections of identical rows must come back as two
    identical series — if everything were filed under one book, one entity would
    carry double and the other nothing, which a fixture with different amounts
    could hide behind a plausible-looking difference.

    That book carries a single stock observation, so it also pins the honest
    consequence: one month has capital employed, no window has three, and every
    figure that needs an average is withheld across the whole series.
    """
    token = two_books.post("/api/v1/auth/login", json={
        "email": "s.menon@pie.example",
        "password": "change-me-now"}).json()["token"]
    r = two_books.get("/api/v1/insight/capital",
                      headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["empty_reason"] is None
    entities = {e["connection_id"]: e for e in body["entities"]}
    assert set(entities) == {SLS, FOURU}

    for entity in entities.values():
        observed = [m for m in entity["months"]
                    if m["capital_employed"] is not None]
        assert len(observed) == 1, [m["month"] for m in observed]
        assert observed[0]["ends_on"] == "2026-06-30"
        for month in entity["months"]:
            assert month["avg_capital_employed"] is None
            assert month["return_on_capital"] is None
            assert month["working_capital_per_rupee"] is None
        assert entity["latest"]["capital_employed"] == \
            observed[0]["capital_employed"]

    assert (entities[SLS]["latest"]["capital_employed"]
            == entities[FOURU]["latest"]["capital_employed"])


def test_the_endpoint_stamps_a_version_and_explains_an_empty_book(api_client):
    """Shallow on purpose: the arithmetic is covered above through the builder,
    and what that cannot catch is a call site that never runs."""
    r = api_client.get("/api/v1/insight/capital", headers={
        "Authorization": f"Bearer {_token(api_client, 's.menon@pie.example')}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thresholds_version"]
    assert body["empty_reason"]           # nothing connected on a seeded book
    assert body["definition"]["capital_employed"]
    assert body["basis"]["not_a_balance_sheet"]
