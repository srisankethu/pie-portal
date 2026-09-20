"""What the cash tied up in one quote line costs, and the five ways it refuses.

The engine already prices the line against what this customer has paid and
splits the margin movement between the price and the cost. These tests are about
the third question a desk has: the money goes out when the supplier is paid and
comes back when the customer pays, and in between it is funded by somebody.

Two things this file is deliberately built to catch, because they are the
failure modes the module exists to prevent:

**A second financing engine.** Every assertion about money is made against
``insight/financing.financing_cost`` rather than against a literal, so a
re-implementation here would have to break a test that names the function it was
supposed to reuse. Same for the rate, the days floor and the percentile.

**A benign default.** Five inputs can be missing and each one has its own test
asserting that what comes back is a named refusal rather than a zero, a
plausible stand-in or the stock carrying rate.

The cost baseline is built with the real ``baselines.cost_baseline`` from real
purchase rows rather than hand-assembled, for the reason
``test_quote_diagnosis_drivers`` gives: a hand-built baseline can be given pairs
the trim would never produce, and a suite that can only speak in the output's
vocabulary agrees with a wrong predicate for as long as it stands.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.insight import absence, financing, payments, terms
from app.commercial.quote_diagnosis import (baselines, drivers, evidence, rules,
                                            working_capital)

#: The rate is owner-set with no default, so every test that expects a figure
#: has to set one. That is the point, and ``BARE`` below is the platform as it
#: ships.
TH = dataclasses.replace(CommercialThresholds(),
                         cost_of_capital_annual_pct=0.12)
BARE = CommercialThresholds()

QUOTE_DAY = date(2026, 6, 1)
KNOWABLE_BY = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)

PRICE = Decimal("1000")
QTY = Decimal("10")
UNIT_COST = "600"


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def purchase(i: int, *, unit_cost: str = UNIT_COST,
             day: date | None = None) -> evidence.CostObservation:
    when = day or date(2026, 1, 8) + timedelta(days=21 * i)
    return evidence.CostObservation(
        evidence_id=f"c{i}", source_table="cost_records", product_id="prd_1",
        vendor_id="vnd_1", event_date=when, recorded_at=_at(when),
        qty=Decimal("10"), unit_cost=Decimal(unit_cost), unit="each",
        source_ref={})


def cost_of(n: int = 6, *, unit_cost: str = UNIT_COST,
            th: CommercialThresholds = TH) -> baselines.CostBaseline:
    """``n`` purchases at one level, three weeks apart, inside the window."""
    return baselines.cost_baseline(
        [purchase(i, unit_cost=unit_cost) for i in range(n)],
        as_of=QUOTE_DAY, th=th)


def settlement(i: int, *, raised_days_ago: int, due_in: int | None = 45,
               paid_in: int = 70, party: str = "cst_a",
               amount: float = 1000.0) -> payments.Settlement:
    """One settled invoice. ``due_in`` is ``None`` for an invoice with no terms."""
    raised = QUOTE_DAY - timedelta(days=raised_days_ago)
    return payments.Settlement(
        party_id=party, document_ref=f"inv_{i}", document_number=None,
        document_date=raised,
        due_date=None if due_in is None else raised + timedelta(days=due_in),
        paid_on=raised + timedelta(days=paid_in), amount=amount)


def history(n: int = 8, *, oldest: int = 340, **kwargs
            ) -> list[payments.Settlement]:
    """``n`` settled invoices, oldest first, all inside the window and all
    settled before the quote.

    ``oldest`` is how long ago the first one was raised; they run forward in
    twenty-day steps. It has to move with ``paid_in``, because a settlement is
    only read if the money actually moved before the quote was written — which
    is the point of ``_visible_settlements`` and not an inconvenience to work
    around.
    """
    return [settlement(i, raised_days_ago=oldest - 20 * i, **kwargs)
            for i in range(n)]


def assess(*, quoted=PRICE, qty=QTY, cost=None, settlements=None,
           supplier_term=None, supplier_term_recorded_at=None,
           supplier_erp_days: int | None = 15, th: CommercialThresholds = TH,
           knowable_by: datetime = KNOWABLE_BY,
           ) -> working_capital.WorkingCapital:
    return working_capital.assess(
        quoted_unit_price=quoted, qty=qty,
        cost=cost if cost is not None else cost_of(th=th),
        settlements=history() if settlements is None else settlements,
        supplier_term=supplier_term,
        supplier_term_recorded_at=supplier_term_recorded_at,
        supplier_erp_days=supplier_erp_days,
        as_of=QUOTE_DAY, knowable_by=knowable_by, th=th)


MONEY_FIELDS = ("rate", "capital_per_unit", "capital_at_risk",
                "charge_per_unit", "line_charge", "effect_pp")


# ── the arithmetic is borrowed, not rewritten ────────────────────────────────

def test_the_charge_is_the_platforms_one_financing_arithmetic():
    """Asserted against ``financing.financing_cost`` rather than a literal.

    A module that grew its own multiplication would pass a literal assertion and
    fail this one, which is the whole reason it is written this way.
    """
    reading = assess()
    assert reading.assessed
    assert reading.charge_per_unit == financing.financing_cost(
        reading.capital_per_unit, reading.funded_days, reading.rate)
    assert reading.line_charge == reading.charge_per_unit * QTY
    assert reading.capital_at_risk == reading.capital_per_unit * QTY


def test_the_rate_is_the_owners_cost_of_capital_and_nothing_else():
    reading = assess()
    assert reading.rate == Decimal(str(TH.cost_of_capital_annual_pct))


def test_the_capital_is_the_cost_baselines_expected_cost_carried_not_recomputed():
    cost = cost_of()
    reading = assess(cost=cost)
    assert reading.capital_per_unit == cost.expected_cost
    assert reading.cited == tuple(cost.cited)


def test_the_margin_drag_is_the_charge_over_the_quoted_price_and_is_negative():
    """``(P - C)/P`` less ``(P - C - f)/P`` is exactly ``f / P``.

    Negative because it costs margin, which is ``drivers``' sign convention and
    not a second one — a reader who has learnt one sign should not have to learn
    another on the next card.
    """
    reading = assess()
    assert reading.effect_pp < 0
    assert -reading.effect_pp == (
        reading.charge_per_unit / PRICE).quantize(drivers.PP_QUANTUM)


# ── which days, from what to what ────────────────────────────────────────────

def test_the_days_counted_are_the_wait_less_the_suppliers_credit():
    reading = assess(settlements=history(paid_in=70),
                     supplier_term=terms.Term(days=30),
                     supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert reading.days_source == working_capital.MEASURED_LAG
    assert reading.receivable_days == 70
    assert reading.supplier_credit_days == 30
    assert reading.funded_days == 40


def test_the_wait_is_measured_from_the_document_date_not_the_due_date():
    """Days-late answers "were the terms honoured"; this is a funding question.

    A customer on 45-day terms paying on day 45 is never late and has still had
    the money for forty-five days. ``payments.Lag`` carries both figures and only
    one of them belongs here.
    """
    rows = history(due_in=45, paid_in=45)
    reading = assess(settlements=rows)
    assert reading.receivable_days == 45
    assert payments.lag(rows).expected_days == 0


def test_a_customer_below_the_lag_floor_is_charged_to_their_due_date():
    """``payments.lag`` returning ``None`` means "use the due date".

    Its own docstring says so, and the thing it does *not* mean is "assume they
    are prompt". Two settled invoices paid on day 120 against 45-day terms: the
    reading takes 45, not 0 and not 120.
    """
    rows = [settlement(i, raised_days_ago=300 - 40 * i, due_in=45, paid_in=120)
            for i in range(2)]  # both settled well before the quote
    assert payments.lag(rows) is None

    reading = assess(settlements=rows)
    assert reading.assessed
    assert reading.days_source == working_capital.GRANTED_TERM
    assert reading.receivable_days == 45


def test_a_due_date_reading_names_what_would_make_it_stronger():
    rows = [settlement(i, raised_days_ago=300 - 40 * i, due_in=60, paid_in=200)
            for i in range(2)]
    reading = assess(settlements=rows)
    assert reading.surfaces is False
    assert drivers._GRADE_RANK[reading.days_strength] < drivers._GRADE_RANK[
        rules.MODERATE]
    kinds = {u["series"]: u["kind"] for u in reading.unavailable}
    assert kinds["working_capital_credit_term_evidence"] == absence.BUILDABLE


def test_the_credit_term_is_the_median_through_the_one_percentile_definition():
    rows = [settlement(0, raised_days_ago=300, due_in=30, paid_in=150),
            settlement(1, raised_days_ago=250, due_in=90, paid_in=150)]
    reading = assess(settlements=rows)
    assert reading.receivable_days == payments.percentile([30, 90], 0.50)


def test_an_invoice_due_before_it_was_raised_is_not_averaged_into_the_term():
    """``terms.validate`` refuses a negative term in as many words."""
    rows = [settlement(0, raised_days_ago=300, due_in=-10, paid_in=150),
            settlement(1, raised_days_ago=250, due_in=60, paid_in=150)]
    reading = assess(settlements=rows)
    assert reading.receivable_days == 60


# ── the supplier leg ─────────────────────────────────────────────────────────

def test_the_agreed_term_beats_the_erps():
    reading = assess(supplier_term=terms.Term(days=60),
                     supplier_term_recorded_at=_at(date(2025, 1, 1)),
                     supplier_erp_days=15)
    assert reading.terms_source == working_capital.AGREED_TERM
    assert reading.supplier_credit_days == 60


def test_an_end_of_month_basis_collapses_to_its_day_count():
    """``terms.effective_days`` decides this, and it is reused rather than
    re-decided: the basis changes the date of one bill, not the length of the
    credit being described."""
    agreed = terms.Term(days=45, basis=terms.END_OF_MONTH)
    reading = assess(supplier_term=agreed,
                     supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert reading.supplier_credit_days == terms.effective_days(agreed, 15)


def test_the_erps_term_is_used_where_nothing_was_agreed():
    reading = assess(supplier_term=None, supplier_erp_days=20)
    assert reading.terms_source == working_capital.ERP_TERM
    assert reading.supplier_credit_days == 20


def test_zero_days_from_the_erp_is_a_real_term_and_not_a_missing_one():
    """``Vendor.payment_terms_days`` documents nought as "due on receipt"."""
    reading = assess(supplier_term=None, supplier_erp_days=0)
    assert reading.assessed
    assert reading.supplier_credit_days == 0


def test_an_agreed_term_recorded_after_the_quote_is_dropped_with_its_reason():
    """Point-in-time, through ``evidence.is_knowable`` rather than a local test.

    ``VendorPaymentTerm`` holds one row per vendor, so there is no earlier
    version to fall back to — the ERP's value is read and the substitution is
    reported rather than made silently.
    """
    reading = assess(supplier_term=terms.Term(days=90),
                     supplier_term_recorded_at=_at(date(2026, 7, 1)),
                     supplier_erp_days=15)
    assert reading.terms_source == working_capital.ERP_TERM
    assert reading.supplier_credit_days == 15
    history_note = next(u for u in reading.unavailable
                        if u["series"] == "working_capital_supplier_term_history")
    assert evidence.RECORDED_AFTER in history_note["reason"]


def test_an_agreed_term_with_no_stamp_at_all_cannot_be_placed_and_is_dropped():
    reading = assess(supplier_term=terms.Term(days=90),
                     supplier_term_recorded_at=None, supplier_erp_days=15)
    assert reading.terms_source == working_capital.ERP_TERM
    note = next(u for u in reading.unavailable
                if u["series"] == "working_capital_supplier_term_history")
    assert evidence.NO_RECORDED_AT in note["reason"]


# ── never negative ───────────────────────────────────────────────────────────

def test_a_supplier_who_funds_the_line_outright_is_never_a_credit():
    """``financing_cost`` floors the days at zero and says why: a negative charge
    would lift adjusted margin above gross margin. The signed cycle is still
    published, because "the supplier funds this entirely" is the finding."""
    reading = assess(settlements=history(paid_in=30),
                     supplier_term=terms.Term(days=90),
                     supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert reading.funded_days == -60
    assert reading.charge_per_unit == Decimal("0")
    assert reading.line_charge == Decimal("0")
    assert reading.effect_pp == Decimal("0")
    assert reading.to_dict()["effect_pp"] == 0.0
    assert reading.severity == drivers.NEGLIGIBLE
    assert reading.surfaces is False


# ── the refusals ─────────────────────────────────────────────────────────────

def test_no_cost_of_capital_computes_nothing_and_names_the_field():
    reading = assess(th=BARE)
    assert reading.assessed is False
    assert reading.reason == working_capital.NO_RATE
    assert reading.funded_days is None
    assert all(getattr(reading, f) is None for f in MONEY_FIELDS)
    (entry,) = reading.unavailable
    assert entry["kind"] == absence.COLLECTABLE
    assert "Annual cost of capital" in entry["reason"]


def test_the_stock_carrying_rate_is_never_substituted_for_the_missing_one():
    """A receivable occupies no shelf. ``carrying_cost_annual_pct`` has a default
    and this one deliberately does not, so a fallback would always be available
    and would always be wrong."""
    th = dataclasses.replace(BARE, carrying_cost_annual_pct=0.5)
    assert th.cost_of_capital_annual_pct is None
    reading = assess(th=th)
    assert reading.assessed is False
    assert reading.charge_per_unit is None


def test_no_knowable_purchase_is_refused_rather_than_funded_for_nothing():
    empty = baselines.cost_baseline([], as_of=QUOTE_DAY, th=TH)
    reading = assess(cost=empty)
    assert reading.reason == working_capital.NO_COST_BASELINE
    assert reading.charge_per_unit is None
    assert reading.unavailable[0]["kind"] == absence.COLLECTABLE


def test_no_supplier_term_on_either_record_is_refused():
    """Nought days is not the answer — it would charge this line from the day
    the goods land and overstate every figure below it."""
    reading = assess(supplier_term=None, supplier_erp_days=None)
    assert reading.reason == working_capital.NO_SUPPLIER_TERMS
    assert reading.supplier_credit_days is None
    assert reading.unavailable[0]["kind"] == absence.COLLECTABLE


def test_settled_invoices_with_no_due_date_leave_the_wait_unknown():
    """The account is trading, so the gap closes on its own: ``TRANSIENT``."""
    rows = history(6, due_in=None)
    assert payments.lag(rows) is None
    reading = assess(settlements=rows)
    assert reading.reason == working_capital.NO_RECEIVABLE_DAYS
    assert reading.unavailable[0]["kind"] == absence.TRANSIENT
    assert reading.settlements == 6


def test_an_account_with_nothing_settled_is_a_job_somebody_can_do():
    reading = assess(settlements=[])
    assert reading.reason == working_capital.NO_RECEIVABLE_DAYS
    assert reading.unavailable[0]["kind"] == absence.COLLECTABLE
    assert reading.settlements == 0


def test_the_books_median_is_never_borrowed_for_an_account_without_one():
    """Structural, and the assertion is on the signature rather than the output.

    ``assess`` takes one account's settlements and no portfolio figure at all,
    so there is nothing to fall back to. ``insight/financing`` refuses the same
    substitution in prose; here it cannot be written.
    """
    import inspect
    params = set(inspect.signature(working_capital.assess).parameters)
    assert not params & {"book_days", "median_days", "portfolio", "default_days"}


def test_no_quoted_price_refuses_rather_than_grading_an_undefined_margin():
    reading = assess(quoted=None)
    assert reading.reason == working_capital.NO_QUOTED_PRICE
    assert reading.severity == drivers.NEGLIGIBLE
    assert reading.strength == rules.INSUFFICIENT


def test_no_quantity_refuses_because_there_is_no_capital_to_tie_up():
    reading = assess(qty=Decimal("0"))
    assert reading.reason == working_capital.NO_QUANTITY


def test_every_refusal_publishes_the_same_empty_shape():
    """One shape, so a caller reading a single field is never handed a number the
    reading declined to assert."""
    refusals = [
        assess(th=BARE),
        assess(quoted=None),
        assess(qty=Decimal("0")),
        assess(cost=baselines.cost_baseline([], as_of=QUOTE_DAY, th=TH)),
        assess(supplier_erp_days=None),
        assess(settlements=[]),
    ]
    seen = {r.reason for r in refusals}
    assert len(seen) == 6
    for reading in refusals:
        assert reading.assessed is False
        assert reading.surfaces is False
        assert reading.funded_days is None
        assert reading.receivable_days is None
        assert reading.supplier_credit_days is None
        assert reading.days_source is None
        assert reading.terms_source is None
        assert reading.cited == ()
        assert all(getattr(reading, f) is None for f in MONEY_FIELDS)
        # The code and the sentence are separately checkable, which is the
        # point of keeping them apart: ``basis`` is what a person reads and
        # carries no code of its own, ``reason`` is what a caller reads.
        assert reading.basis
        assert reading.reason not in reading.basis
        assert len(reading.unavailable) == 1


def test_every_absence_kind_is_one_of_the_five():
    readings = [assess(), assess(th=BARE), assess(settlements=[]),
                assess(supplier_erp_days=None), assess(quoted=None),
                assess(settlements=[settlement(0, raised_days_ago=100)])]
    for reading in readings:
        for entry in reading.unavailable:
            assert entry["kind"] in absence.KINDS
            assert set(entry) == {"series", "kind", "reason"}


# ── point in time ────────────────────────────────────────────────────────────

def test_a_settlement_the_quoter_could_not_have_seen_does_not_shorten_the_wait():
    """A receipt that had not happened is not evidence the quoter had.

    Four slow invoices plus four paid on the day *after* the quote was written:
    the reading must be the slow one, and the later rows must be reported as
    dropped rather than silently absent.
    """
    slow = history(4, paid_in=120)
    later = [settlement(100 + i, raised_days_ago=10, paid_in=20)
             for i in range(4)]   # settled ten days AFTER the quote was written
    reading = assess(settlements=slow + later)
    assert reading.settlements == 4
    assert reading.receivable_days == 120
    note = next(u for u in reading.unavailable
                if u["series"] == "working_capital_settlement_visibility")
    assert "4 settlement(s)" in note["reason"]


def test_a_settlement_paid_on_the_day_of_the_quote_falls_on_the_strict_side():
    """The boundary has to fall somewhere, and ``evidence.is_knowable`` puts it
    on the conservative side. This one matches it rather than inventing its own."""
    same_day = payments.Settlement(
        party_id="cst_a", document_ref="inv_same", document_number=None,
        document_date=QUOTE_DAY - timedelta(days=10), due_date=None,
        paid_on=QUOTE_DAY, amount=1000.0)
    reading = assess(settlements=history(4, paid_in=120) + [same_day])
    assert reading.settlements == 4


def test_a_settlement_outside_the_engines_own_window_is_not_read():
    """The window is ``historical_lookback_days`` — the same one the price and
    cost evidence are bounded by, so one diagnosis spans one period."""
    inside = history(4, paid_in=120)
    ancient = [settlement(200 + i,
                          raised_days_ago=TH.historical_lookback_days + 30 + i,
                          paid_in=5) for i in range(4)]
    reading = assess(settlements=inside + ancient)
    assert reading.settlements == 4
    assert reading.receivable_days == 120


def test_more_than_one_party_raises_rather_than_naming_the_wrong_account():
    """``payments.lag`` reads the party from the first row, so a mixed list
    measures one customer and labels another."""
    rows = history(4) + [settlement(50, raised_days_ago=100, party="cst_b")]
    with pytest.raises(ValueError, match="one customer's settlements"):
        assess(settlements=rows)


# ── strength, severity and the gate ──────────────────────────────────────────

def test_strength_is_the_weaker_of_the_two_halves_and_never_an_average():
    thin = cost_of(2)
    reading = assess(cost=thin, settlements=history(10))
    assert reading.cost_strength == drivers.cost_strength(thin, TH)
    assert reading.days_strength == rules.STRONG
    assert reading.strength == reading.cost_strength
    assert drivers._GRADE_RANK[reading.strength] < drivers._GRADE_RANK[rules.STRONG]


def test_the_cost_half_is_graded_by_the_existing_cost_grader():
    cost = cost_of(9)
    reading = assess(cost=cost)
    assert reading.cost_strength == drivers.cost_strength(cost, TH)


def test_severity_comes_from_the_existing_grader_and_its_versioned_boundary():
    reading = assess(settlements=history(oldest=700, paid_in=300),
                     supplier_term=terms.Term(days=30),
                     supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert reading.severity == drivers.severity(reading.effect_pp, TH)
    assert reading.severity == drivers.MAJOR

    quiet = dataclasses.replace(TH, diagnosis_driver_major_pp=0.9,
                                diagnosis_driver_minor_pp=0.8)
    milder = assess(th=quiet, settlements=history(oldest=700, paid_in=300),
                    supplier_term=terms.Term(days=30),
                    supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert milder.severity == drivers.NEGLIGIBLE
    assert milder.surfaces is False


def test_a_major_drag_on_believable_evidence_surfaces():
    reading = assess(settlements=history(oldest=700, paid_in=300),
                     supplier_term=terms.Term(days=30),
                     supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert reading.severity == drivers.MAJOR
    assert drivers._GRADE_RANK[reading.strength] >= drivers._GRADE_RANK[
        rules.MODERATE]
    assert reading.surfaces is True


def test_nothing_below_moderate_surfaces_however_large_the_money():
    """The gate governs interruption, not calculation: the figure is still there."""
    reading = assess(cost=cost_of(1), settlements=history(oldest=700, paid_in=300),
                     supplier_term=terms.Term(days=30),
                     supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert reading.strength == rules.INSUFFICIENT
    assert reading.severity == drivers.MAJOR
    assert reading.line_charge > Decimal("0")
    assert reading.surfaces is False


def test_an_ordinary_cycle_does_not_interrupt_anybody():
    """Default state is silent. At a twelve percent rate a sixty-day cycle is a
    point of margin on every line in the book, and a card on every line is a card
    nobody reads."""
    reading = assess()
    assert reading.assessed
    assert reading.severity != drivers.MAJOR
    assert reading.surfaces is False


# ── determinism and disclosure ───────────────────────────────────────────────

def test_the_same_inputs_produce_the_same_bytes_whatever_order_the_rows_arrive():
    rows = history(7, paid_in=95)
    first = json.dumps(assess(settlements=list(rows)).to_dict(), sort_keys=True)
    second = json.dumps(assess(settlements=list(reversed(rows))).to_dict(),
                        sort_keys=True)
    assert first == second


def test_no_money_field_of_this_reading_exists_on_the_operations_view():
    """The desk's type has no field to put any of this in, which is the guarantee
    ``rules.OperationsDiagnosis`` is built to make. Checked here as well as there
    because this module is what would next be tempted onto it."""
    ops = rules.operations_field_names()
    published = set(assess().to_dict())
    assert published & set(MONEY_FIELDS) == set(MONEY_FIELDS)
    for name in MONEY_FIELDS + ("funded_days", "capital_at_risk"):
        assert name not in ops
    assert "effect_pp" in rules.FORBIDDEN_OPERATIONS_FIELDS


def test_the_reading_serialises_to_plain_json():
    payload = assess().to_dict()
    assert json.loads(json.dumps(payload)) == payload


def test_the_basis_says_which_days_were_counted_and_from_what_to_what():
    reading = assess(settlements=history(paid_in=70),
                     supplier_term=terms.Term(days=30),
                     supplier_term_recorded_at=_at(date(2025, 1, 1)))
    assert "40 days" in reading.basis
    assert "70 days from invoice" in reading.basis
    assert "30 days of supplier credit" in reading.basis
    assert "floor" in reading.basis


def test_every_assessed_reading_says_the_figure_leaves_stock_holding_out():
    """The omission is on the row, not only in a docstring: a reader is told the
    number is a floor at the moment they read it."""
    entry = next(u for u in assess().unavailable
                 if u["series"] == "working_capital_stock_holding")
    assert entry["kind"] == absence.PERMANENT
    assert "shelf" in entry["reason"]


def test_this_payload_is_the_cost_and_the_existing_sweep_already_says_so():
    """The reason it is owner-only, pinned rather than asserted in a docstring.

    ``capital_per_unit`` *is* the purchase cost and the charge divides back to
    it, so a projection that ever carried this reading would be a leak. The
    check is made against ``cost_sweep`` — the one definition of what counts as
    one, shared by both endpoints that project a diagnosis — rather than against
    a list written here, so this reading cannot quietly acquire a spelling the
    sweep does not know.
    """
    import cost_sweep

    payload = json.dumps(assess().to_dict())
    assert str(int(Decimal(UNIT_COST))) in payload
    assert [w for w in cost_sweep.WORDS if w in payload]
