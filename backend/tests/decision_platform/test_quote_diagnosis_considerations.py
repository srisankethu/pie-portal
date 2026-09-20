"""Options a person might weigh, and the far more common answer that there is none.

Three properties this file exists to hold, each with a named failure behind it:

**Nothing surfaces that the line itself did not.** ``render_working_capital``
already holds ``interrupts <= surfaces`` and this module inherits the same rule
across every consideration it can produce. A panel that found its own reasons to
interrupt somebody would be a second gate, and the two would disagree on the
lines nobody looks at. A sweep over every diagnosis shape this suite can build
asserts the implication rather than one example of it.

**An option, never an instruction, and never a supplier.** The cost-side
consideration is the one that would most naturally grow a name — every purchase
behind the baseline carries a vendor — so its wording is asserted verbatim, and
a test reads every label and every detail sentence for the imperative shapes
that would make one an instruction.

**No second dismissal vocabulary.** A consideration a person rejects is the
cheapest labelled feedback this engine gets, and it has to land in the same
table as a dismissed card or the dataset is two datasets nobody can join. So
this module defines no reasons of its own, and every consideration names the
line whose stored diagnosis ``POST /{id}/dismiss`` already points at.

Diagnoses are built by running the real pipeline over real evidence rows, for
the reason ``test_quote_diagnosis_drivers`` gives: a hand-built ``OwnerDiagnosis``
can be handed code and context combinations the engine would never produce, and
a suite that can only speak in the output's vocabulary agrees with a wrong
predicate for as long as it stands.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.commercial import source_concepts as sc
from app.commercial.config import CommercialThresholds
from app.commercial.insight import payments, terms
from app.commercial.quantity import band_for, bands
from app.commercial.quote_diagnosis import (baselines, comparables,
                                            considerations, drivers, evidence,
                                            intent, render, rules)
from app.domain import models
from tests import cost_sweep

TH = CommercialThresholds()
#: The rate is owner-set with no default, so the working-capital reading refuses
#: on ``TH`` and is assessed on this one. Both are exercised below.
FUNDED = dataclasses.replace(TH, cost_of_capital_annual_pct=0.12)

QUOTE_DAY = date(2026, 6, 1)
KNOWABLE_BY = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
CONNECTOR = "epicor_p21"


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def row(evidence_id: str, *, price: str, day: date, recorded: date | None = None,
        customer: str = "cst_a", qty: str = "10",
        cls: str = evidence.REALIZED) -> evidence.EvidenceRow:
    return evidence.EvidenceRow(
        evidence_id=evidence_id, source_table="sales_txns",
        customer_id=customer, product_id="prd_1", event_date=day,
        recorded_at=_at(recorded if recorded is not None else day),
        evidence_class=cls, qty=Decimal(qty), unit_price=Decimal(price),
        unit="Nos", source_ref={comparables.FAMILY_KEY: "Turning"})


def purchase(evidence_id: str, *, unit_cost: str,
             day: date) -> evidence.CostObservation:
    return evidence.CostObservation(
        evidence_id=evidence_id, source_table="cost_records", product_id="prd_1",
        vendor_id="vnd_1", event_date=day, recorded_at=_at(day),
        qty=Decimal("10"), unit_cost=Decimal(unit_cost), unit="Nos",
        source_ref={})


def steady(n: int = 12, *, price: str = "1000",
           start: date = date(2026, 1, 5)) -> list[evidence.EvidenceRow]:
    return [row(f"s{i}", price=price, day=start + timedelta(days=7 * i))
            for i in range(n)]


def steady_cost(n: int = 6, *, unit_cost: str = "600"):
    return [purchase(f"c{i}", unit_cost=unit_cost,
                     day=date(2026, 1, 8) + timedelta(days=21 * i))
            for i in range(n)]


def risen_cost(old: str = "600", new: str = "780", n: int = 5):
    """``n`` purchases at the old level and one, latest and knowable, above it.

    ``n`` is five rather than six so the new purchase really is the latest —
    ``baselines.cost_baseline`` reads ``ordered[-1]``, and a sixth base purchase
    lands after it.
    """
    return steady_cost(n, unit_cost=old) + [
        purchase("c_new", unit_cost=new, day=date(2026, 4, 20))]


def settlement(i: int, *, raised_days_ago: int, due_in: int = 45,
               paid_in: int = 300) -> payments.Settlement:
    raised = QUOTE_DAY - timedelta(days=raised_days_ago)
    return payments.Settlement(
        party_id="cst_a", document_ref=f"inv_{i}", document_number=None,
        document_date=raised, due_date=raised + timedelta(days=due_in),
        paid_on=raised + timedelta(days=paid_in), amount=1000.0)


def slow_payer(n: int = 8) -> list[payments.Settlement]:
    """An account that takes 300 days, inside the 730-day evidence window.

    The same shape ``test_quote_diagnosis_working_capital`` uses to reach a
    MAJOR drag, rather than a second set of numbers tuned to the same boundary.
    """
    return [settlement(i, raised_days_ago=700 - 30 * i) for i in range(n)]


def taxonomy(at: datetime = KNOWABLE_BY) -> sc.Taxonomy:
    """One declared pricing-reason field, held in memory rather than persisted.

    The real ``Taxonomy`` and the real ``read``: what a session would add here is
    a row id and the point-in-time filter, and neither is what these tests are
    about. ``test_quote_diagnosis_intent`` exercises both against the database.
    """
    declared = models.SourceAttributeMapping(
        organization_id="org_test", connector=CONNECTOR, entity=intent.ENTITY,
        pie_concept=sc.QUOTE_INTENT, source_key="cf_quote_type",
        # Keys are ``source_concepts.fold``ed — upper-cased, whitespace
        # collapsed — because case is not meaning and a different spelling is.
        value_map={"TENDER ENQUIRY": "TENDER"})
    return sc.Taxonomy(connector=CONNECTOR, entity=intent.ENTITY, at=at,
                       declarations={sc.QUOTE_INTENT: declared})


def _run(rows, *, quoted: str | None, costs=(), lost=(), th=TH,
         settlements=(), supplier_erp_days=None,
         source_record=None, tax=None,
         customer: str = "cst_a") -> rules.OwnerDiagnosis:
    """The whole pipeline over one set of rows. Mirrors ``service.diagnose_line``."""
    qty = Decimal("10")
    subject = comparables.Subject(
        customer_id=customer, product_id="prd_1", family="Turning", qty=qty,
        band=band_for(qty, th), unit="each", as_of=QUOTE_DAY)

    kept, dropped = evidence.knowable_at(rows, knowable_by=KNOWABLE_BY)
    kept, unnorm = evidence.normalize(kept, subject_unit=subject.unit)
    kept_costs, cost_dropped = evidence.costs_knowable_at(
        costs, knowable_by=KNOWABLE_BY)

    ladder = bands(th)
    axis = comparables.select_customer_axis(kept, subject=subject, bands=ladder)
    peer = comparables.select_peer_axis(kept, subject=subject, bands=ladder,
                                        recent_days=th.diagnosis_recent_days)
    own = [r for r in axis.rows if r.customer_id == customer]
    for_band, held = evidence.for_baseline(own)
    price = baselines.price_baseline(for_band, as_of=QUOTE_DAY, th=th, lost=lost)
    cost = baselines.cost_baseline(kept_costs, as_of=QUOTE_DAY, th=th)

    evset = evidence.EvidenceSet(
        rows=kept, costs=kept_costs,
        excluded=dropped + unnorm + cost_dropped + held)

    return rules.diagnose(
        line_id="ln_1", subject_customer_id=customer, product_id="prd_1",
        qty=qty, quantity_band=subject.band.label,
        quoted_unit_price=Decimal(quoted) if quoted is not None else None,
        as_of=QUOTE_DAY, knowable_by=KNOWABLE_BY, axis=axis, peer=peer,
        price=price, cost=cost, evidence=evset, th=th,
        settlements=settlements, supplier_erp_days=supplier_erp_days,
        supplier_term=None, supplier_term_recorded_at=None,
        source_record=source_record, taxonomy=tax)


def _blank_reason_record() -> intent.SourceRecord:
    """A quote whose declared pricing-reason field is empty."""
    return intent.SourceRecord(found=True, connector=CONNECTOR,
                               attributes={"cf_quote_type": ""})


def _codes(got: considerations.Considerations) -> list[str]:
    return [c.code for c in got.items]


def _of(got: considerations.Considerations, code: str
        ) -> considerations.Consideration:
    return next(c for c in got.items if c.code == code)


# ── the common answer: nothing ───────────────────────────────────────────────

def test_an_unremarkable_line_offers_nothing_and_says_what_was_weighed():
    """A consideration on every quote is alert fatigue with a new name.

    And the empty case is not an empty list: a panel with nothing in it reads as
    "all clear", which is the failure CLAUDE.md §1 names three times.
    """
    out = _run(steady(), quoted="1000", costs=steady_cost())
    got = considerations.propose(out, th=TH)

    assert got.items == ()
    assert got.surfacing == ()
    assert got.line_surfaces is False
    assert got.reason == considerations.NOTHING_TO_WEIGH
    assert "no option is put in front of you" in got.basis
    assert "What was checked, and what could not be, is on the card." in got.basis


def test_a_surfacing_line_with_nothing_further_to_offer_says_so_differently():
    """Two silences, two sentences. "Nothing was found" and "there is a finding
    and no option beyond it" are different facts about the line."""
    out = _run(steady(), quoted="850", costs=steady_cost())
    got = considerations.propose(out, th=TH)

    assert out.surfaces is True
    assert got.items == ()
    # One code over both silences: ``basis`` words them apart because they are
    # different facts about the line, and ``reason`` answers the one question a
    # caller asks — is anything on offer here.
    assert got.reason == considerations.NOTHING_TO_WEIGH
    assert "supports no option beyond reading the finding itself" in got.basis


# ── each consideration, and what stops it ────────────────────────────────────

def test_a_blank_pricing_reason_on_a_surfacing_line_offers_recording_it():
    out = _run(steady(), quoted="850", costs=steady_cost(),
               source_record=_blank_reason_record(), tax=taxonomy())
    got = considerations.propose(out, th=TH)

    assert rules.NO_PRICING_REASON_RECORDED in out.intent.reading.codes
    assert _codes(got) == [considerations.RECORD_THE_PRICING_REASON]

    one = _of(got, considerations.RECORD_THE_PRICING_REASON)
    assert one.surfaces is True
    assert one.label == "Record why this price was set"
    assert one.rests_on == (rules.NO_PRICING_REASON_RECORDED,
                            rules.BELOW_HISTORICAL_RANGE)
    assert one.strength == out.strength
    assert "an unrecorded reason is not an absent one" in one.detail


def test_a_blank_pricing_reason_on_an_ordinary_line_offers_nothing():
    """The gate that keeps this off every line of every quote.

    A book with a declared pricing-reason field has a blank one on most quotes.
    Ungated, this would be the panel a desk learns to skip in a week.
    """
    out = _run(steady(), quoted="1000", costs=steady_cost(),
               source_record=_blank_reason_record(), tax=taxonomy())
    got = considerations.propose(out, th=TH)

    assert rules.NO_PRICING_REASON_RECORDED in out.intent.reading.codes
    assert out.surfaces is False
    # Computed, as everything in this package is, and put in front of nobody.
    assert _codes(got) == [considerations.RECORD_THE_PRICING_REASON]
    assert got.surfacing == ()


def test_a_recorded_pricing_reason_offers_nothing_to_record():
    out = _run(steady(), quoted="850", costs=steady_cost(),
               source_record=intent.SourceRecord(
                   found=True, connector=CONNECTOR,
                   attributes={"cf_quote_type": "tender enquiry"}),
               tax=taxonomy())
    got = considerations.propose(out, th=TH)

    assert rules.PRICING_REASON_RECORDED in out.intent.reading.codes
    assert considerations.RECORD_THE_PRICING_REASON not in _codes(got)


def test_a_book_with_nothing_declared_is_never_told_to_record_a_reason():
    """``PRICING_REASON_NOT_DECLARED`` is a configuration gap, not a quote's.

    Every quote on such a book reads the same way, so an option here would fire
    on all of them — this engine's own *absence of evidence is not a pass*
    pointed at a whole book.
    """
    out = _run(steady(), quoted="850", costs=steady_cost(),
               source_record=intent.SourceRecord(found=True, connector=CONNECTOR,
                                                 attributes={"whatever": "x"}),
               tax=sc.Taxonomy(connector=CONNECTOR, entity=intent.ENTITY,
                               at=KNOWABLE_BY, declarations={}))
    got = considerations.propose(out, th=TH)

    assert rules.PRICING_REASON_NOT_DECLARED in out.intent.reading.codes
    assert got.items == ()


def test_withheld_comparables_on_a_surfacing_line_offer_checking_them():
    """And the option maps onto a dismissal reason that already exists."""
    rows = steady()
    # One row the quoter could not have seen, so the engine withholds it and
    # says so. It caps no grade, which is why this qualifier can reach somebody.
    rows.append(row("late", price="1000", day=date(2026, 5, 20),
                    recorded=date(2026, 6, 20)))
    out = _run(rows, quoted="850", costs=steady_cost())
    got = considerations.propose(out, th=TH)

    assert rules.EVIDENCE_WITHHELD in out.context
    one = _of(got, considerations.CHECK_THE_COMPARISON)
    assert one.surfaces is True
    assert one.rests_on == (rules.EVIDENCE_WITHHELD, rules.BELOW_HISTORICAL_RANGE)
    assert "COMPARISON_IS_WRONG" in render.DISMISS_REASONS


def test_a_heavily_trimmed_band_offers_the_same_option_and_interrupts_nobody():
    """Computed, not interrupting. ``rules.strength`` caps an over-trimmed band
    at WEAK and ``_surfaces`` needs MODERATE, so this option exists for a reader
    who opens the line and reaches nobody who does not."""
    start = date(2026, 1, 5)
    rows = [row(f"s{i}", price=p, day=start + timedelta(days=7 * i))
            for i, p in enumerate(["1000", "1010", "990", "1005", "995",
                                   "1000", "1002"])]
    rows += [row(f"x{i}", price=p, day=start + timedelta(days=90 + i))
             for i, p in enumerate(["9000", "9500", "80", "70"])]
    out = _run(rows, quoted="1000", costs=steady_cost())
    got = considerations.propose(out, th=TH)

    assert rules.POSSIBLE_EXCEPTIONAL_PRICE in out.context
    assert out.strength == rules.WEAK
    one = _of(got, considerations.CHECK_THE_COMPARISON)
    assert one.surfaces is False
    assert one.strength == rules.WEAK


def test_a_price_above_the_band_to_a_customer_who_has_declined_offers_a_check():
    declined = [row("l1", price="1000", day=date(2026, 4, 1),
                    cls=evidence.QUOTED_LOST)]
    out = _run(steady() + declined, quoted="1200", costs=steady_cost(),
               lost=declined)
    got = considerations.propose(out, th=TH)

    assert rules.ABOVE_HISTORICAL_RANGE in out.codes
    assert rules.PRICE_RESISTANCE_OBSERVED in out.context
    one = _of(got, considerations.CHECK_THE_PRICE_IS_WINNABLE)
    assert one.surfaces is True
    assert "does not know whether a high price wins the order" in one.detail


def test_a_price_above_the_band_with_no_resistance_on_record_offers_nothing():
    """Above the band on its own is as much a win as a risk and the engine says
    it does not know which. Half the evidence is not an option."""
    out = _run(steady(), quoted="1200", costs=steady_cost())
    got = considerations.propose(out, th=TH)

    assert rules.ABOVE_HISTORICAL_RANGE in out.codes
    assert rules.PRICE_RESISTANCE_OBSERVED not in out.context
    assert considerations.CHECK_THE_PRICE_IS_WINNABLE not in _codes(got)


def test_a_recorded_cost_rise_offers_reviewing_the_source_and_names_no_supplier():
    """The wording is the feature. Every purchase behind the baseline carries a
    vendor, so naming one would be easy — and would be a claim that another
    source exists and is cheaper, which nothing in these books records."""
    out = _run(steady(), quoted="850", costs=risen_cost())
    got = considerations.propose(out, th=TH)

    assert rules.KNOWN_COST_CHANGE in out.codes
    one = _of(got, considerations.REVIEW_THE_PURCHASE_SOURCE)
    assert one.label == "Review the purchase source for this item"
    assert one.surfaces is True
    assert one.strength == drivers.cost_strength(out.cost, TH)
    assert "No supplier is named" in one.detail
    assert "vnd_1" not in one.detail
    # Severity comes from the driver the engine already graded, never a second
    # grader here.
    assert one.severity == next(
        d.severity for d in out.attribution.drivers
        if d.code == drivers.COST_LEVEL_EFFECT)


def test_a_steady_cost_offers_no_purchase_source_review():
    out = _run(steady(), quoted="850", costs=steady_cost())
    got = considerations.propose(out, th=TH)

    assert rules.KNOWN_COST_CHANGE not in out.codes
    assert rules.COST_DRIVEN_MARGIN_RISK not in out.codes
    assert considerations.REVIEW_THE_PURCHASE_SOURCE not in _codes(got)


def test_a_cost_driven_line_carries_the_option_and_interrupts_nobody():
    """``COST_DRIVEN_MARGIN_RISK`` requires the price to be inside its own band,
    and ``rules._surfaces`` refuses that line. Computed, available, silent."""
    out = _run(steady(), quoted="1000", costs=risen_cost())
    got = considerations.propose(out, th=TH)

    assert rules.COST_DRIVEN_MARGIN_RISK in out.codes
    assert out.surfaces is False
    one = _of(got, considerations.REVIEW_THE_PURCHASE_SOURCE)
    assert one.surfaces is False


def test_a_long_funded_window_offers_reviewing_the_terms():
    out = _run(steady(), quoted="850", costs=steady_cost(), th=FUNDED,
               settlements=slow_payer(), supplier_erp_days=30)
    got = considerations.propose(out, th=FUNDED)

    capital = out.working_capital
    assert capital.assessed is True
    assert capital.severity == drivers.MAJOR
    one = _of(got, considerations.REVIEW_THE_PAYMENT_TERMS)
    assert one.surfaces is True
    assert one.strength == capital.strength
    assert one.severity == capital.severity
    assert one.rests_on == (capital.reason,)


def test_a_supplier_who_funds_the_line_is_never_told_to_review_the_terms():
    """The supplier's credit covers the wait outright, so there is no drag.

    ``insight/financing.financing_cost`` floors the funded window at zero days
    and says why — a negative charge would lift adjusted margin above gross
    margin — so the effect is exactly nought rather than a gain. The guard in
    ``propose`` is on the effect being adverse rather than on the days, which is
    the half that keeps reading correctly if that floor is ever revisited.
    """
    out = _run(steady(), quoted="850", costs=steady_cost(), th=FUNDED,
               settlements=[settlement(i, raised_days_ago=700 - 30 * i,
                                       due_in=15, paid_in=10)
                            for i in range(8)],
               supplier_erp_days=180)
    got = considerations.propose(out, th=FUNDED)

    capital = out.working_capital
    assert capital.assessed is True
    assert capital.funded_days < 0
    assert capital.effect_pp == 0
    assert considerations.REVIEW_THE_PAYMENT_TERMS not in _codes(got)


def test_no_cost_of_capital_set_offers_no_term_review_and_does_not_invent_one():
    out = _run(steady(), quoted="850", costs=steady_cost(),
               settlements=slow_payer(), supplier_erp_days=30)
    got = considerations.propose(out, th=TH)

    assert out.working_capital.assessed is False
    assert out.working_capital.reason == "NO_RATE"
    assert considerations.REVIEW_THE_PAYMENT_TERMS not in _codes(got)


# ── the gate ─────────────────────────────────────────────────────────────────

def _every_shape() -> list[tuple[str, rules.OwnerDiagnosis]]:
    """Every diagnosis shape this suite can build, for the structural sweeps."""
    declined = [row("l1", price="1000", day=date(2026, 4, 1),
                    cls=evidence.QUOTED_LOST)]
    late = steady() + [row("late", price="1000", day=date(2026, 5, 20),
                           recorded=date(2026, 6, 20))]
    trimmed = [row(f"s{i}", price=p, day=date(2026, 1, 5) + timedelta(days=7 * i))
               for i, p in enumerate(["1000", "1010", "990", "1005", "995",
                                      "1000", "1002"])]
    trimmed += [row(f"x{i}", price=p,
                    day=date(2026, 1, 5) + timedelta(days=90 + i))
                for i, p in enumerate(["9000", "9500", "80", "70"])]
    blank = _blank_reason_record()
    return [
        ("in band", _run(steady(), quoted="1000", costs=steady_cost())),
        ("below band", _run(steady(), quoted="850", costs=steady_cost())),
        ("below band, blank reason",
         _run(steady(), quoted="850", costs=steady_cost(),
              source_record=blank, tax=taxonomy())),
        ("in band, blank reason",
         _run(steady(), quoted="1000", costs=steady_cost(),
              source_record=blank, tax=taxonomy())),
        ("above band with resistance",
         _run(steady() + declined, quoted="1200", costs=steady_cost(),
              lost=declined)),
        ("withheld evidence", _run(late, quoted="850", costs=steady_cost())),
        ("over-trimmed band", _run(trimmed, quoted="1000", costs=steady_cost())),
        ("known cost change", _run(steady(), quoted="850", costs=risen_cost())),
        ("cost driven", _run(steady(), quoted="1000", costs=risen_cost())),
        ("no cost at all", _run(steady(), quoted="850", costs=[])),
        ("no price", _run(steady(), quoted=None, costs=steady_cost())),
        ("thin band", _run(steady(1), quoted="500", costs=steady_cost())),
        ("funded window",
         _run(steady(), quoted="850", costs=steady_cost(), th=FUNDED,
              settlements=slow_payer(), supplier_erp_days=30)),
        ("funded window, in band",
         _run(steady(), quoted="1000", costs=steady_cost(), th=FUNDED,
              settlements=slow_payer(), supplier_erp_days=30)),
    ]


def test_no_consideration_ever_interrupts_a_line_the_engine_did_not():
    """The property, swept rather than sampled.

    ``surfaces`` here implies ``OwnerDiagnosis.surfaces`` on every path. A
    consideration that found its own reason to interrupt somebody would be a
    second gate, and ``render_working_capital`` already holds the same
    implication for the reading it draws.
    """
    for name, out in _every_shape():
        th = FUNDED if out.working_capital.assessed else TH
        got = considerations.propose(out, th=th)
        for one in got.items:
            assert one.surfaces <= out.surfaces, (
                f"{one.code} surfaced on a {name} line the engine kept silent")


def test_nothing_below_moderate_evidence_ever_interrupts_anybody():
    """The MODERATE floor is ``rules._surfaces``' and is inherited, not restated.

    Asserted over every shape because a consideration that graded itself would
    be able to promote a WEAK finding into an interruption, which is the one
    thing the four-grade ladder exists to stop.
    """
    rank = rules._RANK
    for name, out in _every_shape():
        th = FUNDED if out.working_capital.assessed else TH
        for one in considerations.propose(out, th=th).surfacing:
            assert rank[one.strength] >= rank[rules.MODERATE], (
                f"{one.code} interrupted on {name} at {one.strength}")


def test_every_consideration_is_computed_whether_or_not_it_interrupts():
    """The gate governs interruption, not calculation — ``rules._surfaces``'
    own rule, and the reason a silent line still has an answer on demand."""
    out = _run(steady(), quoted="1000", costs=risen_cost(),
               source_record=_blank_reason_record(), tax=taxonomy())
    got = considerations.propose(out, th=TH)

    assert out.surfaces is False
    assert got.items != ()
    assert got.surfacing == ()


# ── the two readers ──────────────────────────────────────────────────────────

def test_the_desk_never_receives_a_consideration_built_on_cost():
    out = _run(steady(), quoted="850", costs=risen_cost(), th=FUNDED,
               settlements=slow_payer(), supplier_erp_days=30,
               source_record=_blank_reason_record(), tax=taxonomy())
    owner = considerations.propose(out, th=FUNDED)
    desk = considerations.for_operations(owner)

    assert considerations.REVIEW_THE_PURCHASE_SOURCE in _codes(owner)
    assert considerations.REVIEW_THE_PAYMENT_TERMS in _codes(owner)
    assert set(_codes(desk)) <= considerations.OPERATIONS_CONSIDERATIONS
    assert considerations.RECORD_THE_PRICING_REASON in _codes(desk)


def test_the_desks_basis_never_mentions_an_option_it_did_not_receive():
    """A sentence saying "one further option is withheld" would be a flag that
    answers a margin question — the shape ``filterCounts.MFLOOR`` had."""
    out = _run(steady(), quoted="850", costs=risen_cost(),
               source_record=_blank_reason_record(), tax=taxonomy())
    desk = considerations.for_operations(considerations.propose(out, th=TH))

    assert considerations.REVIEW_THE_PURCHASE_SOURCE not in desk.basis
    assert "withheld" not in desk.basis.lower()
    assert str(len(desk.items)) in desk.basis
    # And ``reason`` is rebuilt over the desk's own list for the same reason
    # ``basis`` is — it says what this reader was handed, not what was dropped.
    assert desk.reason == considerations.OFFERED


def test_no_operations_consideration_carries_a_severity():
    """Severity is ``drivers.severity``, which grades a movement in margin
    points; a margin grade beside the price the caller sent is the cost in one
    step. Structural rather than reviewed: every allowlisted code, over every
    shape this suite builds."""
    for name, out in _every_shape():
        th = FUNDED if out.working_capital.assessed else TH
        desk = considerations.for_operations(considerations.propose(out, th=th))
        for one in desk.items:
            assert one.severity is None, f"{one.code} graded a margin on {name}"


def test_no_cost_or_economics_word_reaches_the_desks_considerations():
    """The sweep, over the serialised desk payload rather than named fields."""
    out = _run(steady(), quoted="850", costs=risen_cost(), th=FUNDED,
               settlements=slow_payer(), supplier_erp_days=30,
               source_record=_blank_reason_record(), tax=taxonomy())
    desk = considerations.for_operations(considerations.propose(out, th=FUNDED))

    body = json.dumps(desk.to_dict()).lower()
    assert desk.items, "nothing was offered, so this sweep proves nothing"
    for word in cost_sweep.WORDS:
        assert word not in body, f"{word!r} reached a salesperson"
    assert str(out.cost.expected_cost) not in body


# ── the feedback loop ────────────────────────────────────────────────────────

def test_a_rejection_travels_the_dismissal_path_that_already_exists():
    """One labelled dataset, not two.

    A consideration names the line whose stored diagnosis
    ``POST /{quote_diagnosis_id}/dismiss`` already points at, and rejecting the
    finding rejects the option resting on it — there is no option here that
    survives its finding being wrong. Two of the existing reasons are already
    the exact refusals two of these invite.
    """
    out = _run(steady(), quoted="850", costs=risen_cost(),
               source_record=_blank_reason_record(), tax=taxonomy())
    got = considerations.propose(out, th=TH)

    assert got.items
    assert {c.line_id for c in got.items} == {out.line_id}
    assert {"COMPARISON_IS_WRONG", "COST_HAS_CHANGED"} <= set(render.DISMISS_REASONS)


def test_this_module_defines_no_second_dismissal_vocabulary():
    """A second reason list would split the one labelled dataset this engine has
    into two nobody can join."""
    maps = {name for name, value in vars(considerations).items()
            if not name.startswith("_") and isinstance(value, dict)
            and all(isinstance(k, str) and isinstance(v, str)
                    for k, v in value.items())}
    assert maps == {"CONSIDERATIONS"}


def test_the_option_vocabulary_has_the_shape_the_dismissal_vocabulary_has():
    """Code → human label, so a front end offers both the same way and neither
    can be a dead button the other does not know about."""
    assert isinstance(considerations.CONSIDERATIONS, dict)
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in considerations.CONSIDERATIONS.items())
    assert set(considerations.ORDER) == set(considerations.CONSIDERATIONS)
    assert considerations.OPERATIONS_CONSIDERATIONS <= set(
        considerations.CONSIDERATIONS)


# ── options, never instructions ──────────────────────────────────────────────

def test_no_label_or_detail_names_a_party_or_orders_anybody_to_do_anything():
    """PIE does not make the pricing decision. "Review the purchase source",
    never "buy from Supplier X" — and never "drop the price to"."""
    banned = ("buy from", "switch to", "drop the price", "raise the price",
              "increase the price", "reduce the price", "you should",
              "you must", "we recommend", "quote at ")
    for name, out in _every_shape():
        th = FUNDED if out.working_capital.assessed else TH
        for one in considerations.propose(out, th=th).items:
            text = f"{one.label} {one.detail}".lower()
            for phrase in banned:
                assert phrase not in text, f"{one.code} instructs, on {name}"
            assert "₹" not in text


# ── determinism ──────────────────────────────────────────────────────────────

def test_two_runs_over_one_line_produce_the_same_bytes():
    for name, out in _every_shape():
        th = FUNDED if out.working_capital.assessed else TH
        first = json.dumps(considerations.propose(out, th=th).to_dict(),
                           sort_keys=True)
        second = json.dumps(considerations.propose(out, th=th).to_dict(),
                            sort_keys=True)
        assert first == second, name


def test_options_are_emitted_in_one_fixed_order():
    """A reader scanning two lines of one quote finds the same option in the
    same place on both, and a dict iteration order is not an ordering."""
    out = _run(steady(), quoted="850", costs=risen_cost(), th=FUNDED,
               settlements=slow_payer(), supplier_erp_days=30,
               source_record=_blank_reason_record(), tax=taxonomy())
    got = considerations.propose(out, th=FUNDED)

    positions = [considerations.ORDER.index(c.code) for c in got.items]
    assert positions == sorted(positions)
    assert len(positions) >= 3


def test_terms_is_imported_only_to_keep_the_signature_honest():
    """``terms.Term`` is what ``supplier_term`` takes; these tests pass the
    ERP's day count instead, which is the other of the two sources."""
    assert terms.effective_days(None, 15) == 15
