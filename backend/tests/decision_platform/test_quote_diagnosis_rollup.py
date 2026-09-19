"""What a quote comes to as a whole, and the line its total must never bury.

Four properties, each with a named failure behind it:

**A negative-margin line is named whatever the total says.** This is the one the
whole file is built around. ``quote_service.summarize`` refuses to publish a
blended margin at all for exactly this reason — "the number people quote back at
each other while the loss-making line stays invisible" — so a roll-up that
publishes one has to carry the invisible lines with it or it is the mistake that
refusal was avoiding. It is asserted as a property of the output rather than as a
sentence somebody could delete: a sweep over quotes whose totals are healthy by
construction, and a structural check that the serialised payload carries the list
unconditionally.

**Aggregated margin is Σ gross profit ÷ Σ costed revenue.** Asserted against a
quote where the mean of the per-line margins is a completely different number, so
a reimplementation would have to break a test that states both.

**Revenue with no cost behind it is counted in the value and left out of the
margin.** Leaving it in the denominator drags the margin toward zero and reads as
a pricing problem when it is a cost-coverage one — the 7.8%-versus-19.7% failure
``portfolio.MarginAggregate`` records.

**The desk's half has no money on it at all.** Not withheld — absent. The field
list is read, and the serialised payload is swept for the cost value and for the
vocabulary of economics rather than for a handful of named fields.

Lines are built by running the real pipeline, and the roll-up is fed through
``from_diagnosis`` rather than by hand wherever the test is about the engine; the
hand-built ``LineFacts`` appear only where the test is about the arithmetic and
needs a margin the pipeline would take a page of fixtures to produce.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.commercial.config import CommercialThresholds
from app.commercial.quantity import band_for, bands
from app.commercial.quote_diagnosis import (baselines, comparables, drivers,
                                            evidence, render, rollup, rules)
from tests import cost_sweep

TH = CommercialThresholds()

QUOTE_DAY = date(2026, 6, 1)
KNOWABLE_BY = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)

QUOTE = "qt_1"
PURCHASE_COST = 600


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def row(evidence_id: str, *, price: str, day: date, product: str = "prd_1",
        qty: str = "10") -> evidence.EvidenceRow:
    return evidence.EvidenceRow(
        evidence_id=evidence_id, source_table="sales_txns", customer_id="cst_a",
        product_id=product, event_date=day, recorded_at=_at(day),
        evidence_class=evidence.REALIZED, qty=Decimal(qty),
        unit_price=Decimal(price), unit="Nos",
        source_ref={comparables.FAMILY_KEY: "Turning"})


def purchase(evidence_id: str, *, unit_cost: str, day: date,
             product: str = "prd_1") -> evidence.CostObservation:
    return evidence.CostObservation(
        evidence_id=evidence_id, source_table="cost_records", product_id=product,
        vendor_id="vnd_1", event_date=day, recorded_at=_at(day),
        qty=Decimal("10"), unit_cost=Decimal(unit_cost), unit="Nos",
        source_ref={})


def steady(n: int = 12, *, price: str = "1000", product: str = "prd_1"):
    return [row(f"{product}_s{i}", price=price, product=product,
                day=date(2026, 1, 5) + timedelta(days=7 * i)) for i in range(n)]


def steady_cost(n: int = 6, *, unit_cost: str = str(PURCHASE_COST),
                product: str = "prd_1"):
    return [purchase(f"{product}_c{i}", unit_cost=unit_cost, product=product,
                     day=date(2026, 1, 8) + timedelta(days=21 * i))
            for i in range(n)]


def risen_cost(old: str = "600", new: str = "780", n: int = 5,
               product: str = "prd_1"):
    return steady_cost(n, unit_cost=old, product=product) + [
        purchase(f"{product}_c_new", unit_cost=new, product=product,
                 day=date(2026, 4, 20))]


def diagnose(*, line_id: str, quoted: str | None, rows=None, costs=(),
             product: str = "prd_1", qty: str = "10") -> rules.OwnerDiagnosis:
    """One line through the real pipeline. Mirrors ``service.diagnose_line``."""
    quantity = Decimal(qty)
    subject = comparables.Subject(
        customer_id="cst_a", product_id=product, family="Turning", qty=quantity,
        band=band_for(quantity, TH), unit="each", as_of=QUOTE_DAY)
    source = steady(product=product) if rows is None else rows

    kept, dropped = evidence.knowable_at(source, knowable_by=KNOWABLE_BY)
    kept, unnorm = evidence.normalize(kept, subject_unit=subject.unit)
    kept_costs, cost_dropped = evidence.costs_knowable_at(
        costs, knowable_by=KNOWABLE_BY)

    ladder = bands(TH)
    axis = comparables.select_customer_axis(kept, subject=subject, bands=ladder)
    peer = comparables.select_peer_axis(kept, subject=subject, bands=ladder,
                                        recent_days=TH.diagnosis_recent_days)
    own = [r for r in axis.rows if r.customer_id == "cst_a"]
    for_band, held = evidence.for_baseline(own)
    price = baselines.price_baseline(for_band, as_of=QUOTE_DAY, th=TH)
    cost = baselines.cost_baseline(kept_costs, as_of=QUOTE_DAY, th=TH)

    return rules.diagnose(
        line_id=line_id, subject_customer_id="cst_a", product_id=product,
        qty=quantity, quantity_band=subject.band.label,
        quoted_unit_price=Decimal(quoted) if quoted is not None else None,
        as_of=QUOTE_DAY, knowable_by=KNOWABLE_BY, axis=axis, peer=peer,
        price=price, cost=cost,
        evidence=evidence.EvidenceSet(
            rows=kept, costs=kept_costs,
            excluded=dropped + unnorm + cost_dropped + held),
        th=TH)


def facts(line_id: str, *, price: str | None, cost: str | None, qty: str = "10",
          product: str = "prd_1", codes=(rules.BELOW_HISTORICAL_RANGE,),
          strength: str = rules.STRONG, surfaces: bool = True,
          attribution=None, version: str | None = "ci_test",
          ) -> rollup.LineFacts:
    """A line stated directly, for the tests that are about the arithmetic.

    The pipeline cannot be steered to an arbitrary margin without a page of
    evidence rows per line, and a test about "Σ profit ÷ Σ revenue, not the mean
    of the margins" needs two specific margins on two specific revenues. Every
    test about what the *engine* produces goes through ``from_diagnosis``.
    """
    return rollup.LineFacts(
        line_id=line_id, product_id=product, qty=Decimal(qty),
        quoted_unit_price=Decimal(price) if price is not None else None,
        unit_cost=Decimal(cost) if cost is not None else None,
        codes=codes, strength=strength, surfaces=surfaces,
        attribution=attribution if attribution is not None else render.NOT_STORED,
        thresholds_version=version)


# ── the property this file exists for ────────────────────────────────────────

def test_a_negative_margin_line_is_not_hidden_behind_a_healthy_total():
    """Nine good lines and one that loses money on every unit.

    The quote totals to a healthy margin — that is the point of the fixture, not
    an accident of it — and the loss is still named, with its own figures, on
    the object and in the payload.
    """
    lines = [facts(f"ln_{i}", price="1000", cost="700") for i in range(9)]
    lines.append(facts("ln_bad", price="500", cost="900", product="prd_2"))

    got = rollup.roll_up(lines, quote_id=QUOTE)

    assert got.margin > 0.2                      # a healthy-looking quote
    assert got.total_hides_a_loss is True
    assert [ln.line_id for ln in got.loss_lines] == ["ln_bad"]

    bad = got.loss_lines[0]
    assert bad.product_id == "prd_2"
    assert bad.economics.gross_profit == Decimal("-4000")
    assert bad.economics.line_revenue == Decimal("5000")
    assert bad.economics.margin == -0.8
    assert got.loss_value == Decimal("4000")

    body = json.loads(json.dumps(got.to_dict()))
    assert body["loss_lines"][0]["line_id"] == "ln_bad"
    assert body["loss_lines"][0]["gross_profit"] == -4000.0
    assert body["total_hides_a_loss"] is True


def test_every_loss_making_line_is_named_whatever_the_total_comes_to():
    """Swept rather than sampled: one loss line among one, ten and a hundred
    healthy ones, and a quote that is itself under water."""
    # Below ten healthy lines the one bad line drags the whole quote under, so
    # the total does show it. The list is the same either way, which is the
    # point: the guarantee does not depend on what the total happens to say.
    for healthy, hidden in ((0, False), (1, False), (10, True), (100, True)):
        lines = [facts(f"ln_{i}", price="1000", cost="700")
                 for i in range(healthy)]
        lines.append(facts("ln_bad", price="500", cost="900"))
        got = rollup.roll_up(lines, quote_id=QUOTE)

        assert [ln.line_id for ln in got.loss_lines] == ["ln_bad"], healthy
        assert "ln_bad" in json.dumps(got.to_dict())
        assert got.total_hides_a_loss is hidden, healthy


def test_the_loss_lines_are_ordered_worst_first_and_broken_by_id():
    lines = [facts("ln_a", price="900", cost="1000"),
             facts("ln_c", price="100", cost="1000"),
             facts("ln_b", price="900", cost="1000"),
             facts("ln_ok", price="1000", cost="700")]
    got = rollup.roll_up(lines, quote_id=QUOTE)

    assert [ln.line_id for ln in got.loss_lines] == ["ln_c", "ln_a", "ln_b"]


def test_the_basis_leads_with_the_loss_and_never_reads_as_all_clear():
    got = rollup.roll_up(
        [facts("ln_ok", price="1000", cost="700"),
         facts("ln_bad", price="500", cost="900")], quote_id=QUOTE)

    assert got.basis.startswith("1 line loses money at the price quoted")
    assert "the quote's own total does not show it" in got.basis


def test_a_quote_with_no_loss_says_so_by_an_empty_list_rather_than_a_missing_key():
    """An absent key would read as "no line loses money", which is the one thing
    it does not mean. Checked and none found is an answer."""
    got = rollup.roll_up([facts("ln_1", price="1000", cost="700")],
                         quote_id=QUOTE)

    assert got.loss_lines == ()
    assert got.total_hides_a_loss is False
    assert "loss_lines" in got.to_dict()
    assert got.to_dict()["loss_lines"] == []


# ── the aggregation rule ─────────────────────────────────────────────────────

def test_margin_is_total_profit_over_total_revenue_and_not_the_mean_of_margins():
    """The two answers are far apart on purpose, so a mean would fail loudly.

    A big line at 20% and a small one at 60%: the mean is 40% and the truth is
    20.1%, which is the example ``economics.aggregate`` states in its own
    docstring.
    """
    lines = [facts("big", price="400000", cost="320000", qty="1"),
             facts("small", price="1000", cost="400", qty="1")]
    got = rollup.roll_up(lines, quote_id=QUOTE)

    assert got.value == Decimal("401000")
    assert got.gross_profit == Decimal("80600")
    assert round(got.margin, 4) == 0.201
    assert round(got.margin, 4) != 0.40


def test_revenue_with_no_cost_is_counted_in_the_value_and_left_out_of_the_margin():
    """Leaving it in the denominator drags the margin toward zero and reads as a
    pricing problem when it is a cost-coverage one."""
    lines = [facts("costed", price="1000", cost="700"),
             facts("uncosted", price="1000", cost=None)]
    got = rollup.roll_up(lines, quote_id=QUOTE)

    assert got.value == Decimal("20000")
    assert got.costed_value == Decimal("10000")
    assert got.gross_profit == Decimal("3000")
    assert round(got.margin, 4) == 0.3
    assert round(got.revenue_coverage, 4) == 0.5
    assert got.lines_without_cost == 1
    assert "left out of the margin" in got.basis


def test_a_quote_with_no_cost_anywhere_asserts_no_margin_rather_than_none_earned():
    """"We cannot say" and "we made nothing" are different answers."""
    got = rollup.roll_up([facts("ln_1", price="1000", cost=None)],
                         quote_id=QUOTE)

    assert got.value == Decimal("10000")
    assert got.gross_profit is None
    assert got.margin is None
    # Nought, not ``None``: there is a value, and the margin speaks for none of
    # it. ``MarginAggregate.revenue_coverage``, unchanged.
    assert got.revenue_coverage == 0.0
    assert "no margin is asserted — not a margin of nothing" in got.basis


def test_an_unpriced_line_is_counted_and_never_valued_at_zero():
    got = rollup.roll_up(
        [facts("priced", price="1000", cost="700"),
         facts("unpriced", price=None, cost="700",
               codes=(rules.INSUFFICIENT_EVIDENCE,),
               strength=rules.INSUFFICIENT, surfaces=False)], quote_id=QUOTE)

    assert got.value == Decimal("10000")
    assert got.coverage.lines_without_price == 1
    assert got.coverage.lines == 2


def test_an_empty_quote_is_not_a_clean_quote():
    got = rollup.roll_up([], quote_id=QUOTE)

    assert got.coverage.lines == 0
    assert got.margin is None
    assert got.loss_lines == ()
    assert "This is not a quote with nothing wrong with it." in got.basis
    assert "nothing was checked" in got.coverage.basis


# ── coverage: what was checked and what could not be ─────────────────────────

def test_coverage_counts_what_was_judged_and_what_could_not_be():
    lines = [rollup.from_diagnosis(diagnose(line_id="ln_1", quoted="850",
                                            costs=steady_cost())),
             rollup.from_diagnosis(diagnose(line_id="ln_2", quoted="1000",
                                            costs=steady_cost())),
             rollup.from_diagnosis(diagnose(
                 line_id="ln_3", quoted="500",
                 rows=[row("only", price="1000", day=date(2026, 3, 1))]))]
    got = rollup.roll_up(lines, quote_id=QUOTE)

    assert got.coverage.lines == 3
    assert got.coverage.lines_compared == 2
    assert got.coverage.lines_not_compared == 1
    assert got.coverage.lines_surfacing == 1
    assert "1 did not, so nothing is claimed about it" in got.coverage.basis
    assert "absence of evidence, not a pass" in got.coverage.basis


def test_coverage_answers_comparability_with_the_one_function_that_answers_it():
    """``rules.had_enough_to_compare``, not a second reading of the codes — the
    re-derivation its own docstring exists to stop."""
    thin = diagnose(line_id="ln_1", quoted="500",
                    rows=[row("only", price="1000", day=date(2026, 3, 1))])
    got = rollup.roll_up([rollup.from_diagnosis(thin)], quote_id=QUOTE)

    assert rules.INSUFFICIENT_EVIDENCE not in thin.codes   # a band did form
    assert thin.strength == rules.INSUFFICIENT
    assert rules.had_enough_to_compare(thin) is False
    assert got.coverage.lines_compared == 0


def test_the_surfacing_count_is_the_one_both_readers_see():
    """Two names for one answer is a bug with a grace period: a manager was once
    served two flagged lines and a summary saying the quote was clean."""
    diagnoses = [diagnose(line_id="ln_1", quoted="850", costs=steady_cost()),
                 diagnose(line_id="ln_2", quoted="1000", costs=steady_cost())]
    got = rollup.roll_up([rollup.from_diagnosis(d) for d in diagnoses],
                         quote_id=QUOTE)

    desk = sum(1 for d in diagnoses if rules.operations_view(d).surfaces)
    assert got.coverage.lines_surfacing == desk == 1


def test_a_quiet_quote_says_what_was_checked_rather_than_nothing():
    """An empty panel reads as "all clear"."""
    got = rollup.roll_up(
        [rollup.from_diagnosis(diagnose(line_id="ln_1", quoted="1000",
                                        costs=steady_cost()))],
        quote_id=QUOTE)

    assert got.coverage.lines_surfacing == 0
    assert got.coverage.basis
    assert "1 of 1 line had enough comparable history" in got.coverage.basis
    assert "unusual enough to interrupt anybody" in got.coverage.basis


# ── the split between the two readers ────────────────────────────────────────

def test_quote_coverage_has_no_economics_field():
    """Structural, like ``test_operations_view_has_no_economics_field``: the
    dataclass's own field list, so adding one is a failing test rather than a
    review somebody has to catch."""
    names = rollup.coverage_field_names()

    assert not (names & rules.FORBIDDEN_OPERATIONS_FIELDS)
    # "price" is deliberately absent from this list. ``lines_without_price``
    # counts lines the caller sent no price for, and the quoted price is what
    # the desk's own card prints — withholding a count of the caller's own input
    # would be theatre. Everything that would answer a margin question is here.
    for word in ("cost", "margin", "profit", "value", "revenue", "loss",
                 "capital", "driver", "attribution", "opportunity", "peer"):
        assert not any(word in name for name in names), word


def test_no_cost_or_economics_word_reaches_the_desks_half_of_the_rollup():
    """The sweep, over the serialised payload rather than named fields — the
    shape every field-level assertion in this repository's previous leak
    passed."""
    lines = [rollup.from_diagnosis(diagnose(line_id="ln_1", quoted="850",
                                            costs=steady_cost())),
             rollup.from_diagnosis(diagnose(line_id="ln_2", quoted="1000",
                                            costs=risen_cost()))]
    got = rollup.roll_up(lines, quote_id=QUOTE)

    body = json.dumps({"lines": [got.coverage.to_dict()]})
    cost_sweep.assert_no_cost(json.loads(body), cost=PURCHASE_COST)


def test_the_owners_half_is_where_every_figure_lives():
    """The negative control the sweep above needs.

    Without it the sweep proves only that this payload is small. The same quote
    plus a line under water, on the owner's object: the purchase cost is there,
    on the loss line, which is exactly what the sweep would have caught.
    """
    got = rollup.roll_up(
        [rollup.from_diagnosis(diagnose(line_id="ln_1", quoted="850",
                                        costs=steady_cost())),
         rollup.from_diagnosis(diagnose(line_id="ln_bad", quoted="400",
                                        costs=steady_cost()))],
        quote_id=QUOTE)

    body = json.dumps(got.to_dict())
    assert got.gross_profit == Decimal("500")
    assert got.loss_lines[0].economics.unit_cost == Decimal(PURCHASE_COST)
    assert str(PURCHASE_COST) in body
    assert "gross_profit" in body and "margin" in body


# ── the dominant factor ──────────────────────────────────────────────────────

def test_the_dominant_driver_is_weighted_by_money_and_not_by_line_count():
    """Adding per-line percentage-point figures would be the mean-of-margins
    mistake in a different hat. One large line outweighs three small ones."""
    lines = [rollup.from_diagnosis(diagnose(line_id="ln_cost", quoted="1000",
                                            costs=risen_cost()))]
    lines += [rollup.from_diagnosis(diagnose(line_id=f"ln_{i}", quoted="990",
                                             costs=steady_cost()))
              for i in range(3)]
    got = rollup.roll_up(lines, quote_id=QUOTE)
    totals = {d.code: d for d in got.driver_totals}

    assert got.lines_attributed == 4
    assert {d.code for d in got.driver_totals} == drivers.DRIVER_CODES
    # One line moved on cost; three moved on price. Counting lines would make
    # price the answer, and it is not: the single cost move is the larger
    # amount of money by a wide margin.
    assert totals[drivers.COST_LEVEL_EFFECT].effect == Decimal("-1800")
    assert totals[drivers.PRICE_POSITION_EFFECT].effect == Decimal("-300")
    assert got.dominant_driver == drivers.COST_LEVEL_EFFECT
    assert "The largest factor across the 4 attributed lines is" in got.basis


def test_driver_totals_are_ordered_by_magnitude_then_code():
    lines = [rollup.from_diagnosis(diagnose(line_id="ln_1", quoted="850",
                                            costs=risen_cost()))]
    got = rollup.roll_up(lines, quote_id=QUOTE)
    magnitudes = [abs(d.effect) for d in got.driver_totals]

    assert magnitudes == sorted(magnitudes, reverse=True)
    assert got.dominant_driver == got.driver_totals[0].code


def test_a_quote_no_line_could_be_attributed_on_names_no_dominant_factor():
    """A refusal, not a silence: a reader who found no factor named would read
    it as "the price and the cost both behaved"."""
    got = rollup.roll_up(
        [rollup.from_diagnosis(diagnose(line_id="ln_1", quoted="850",
                                        costs=[]))], quote_id=QUOTE)

    assert got.driver_totals == ()
    assert got.dominant_driver is None
    assert got.lines_attributed == 0
    assert "no dominant factor is named" in got.basis


def test_a_rollup_over_stored_rows_names_no_factor_and_says_why():
    """The second producer. ``quote_diagnoses`` has no attribution column, so a
    row read back cannot answer the question and must say so rather than
    answering it with silence."""
    got = rollup.roll_up(
        [facts("ln_1", price="1000", cost="700",
               attribution=render.NOT_STORED)], quote_id=QUOTE)

    assert render.NOT_STORED.drivers == ()
    assert got.dominant_driver is None
    assert got.gross_profit == Decimal("3000")     # the money still totals
    assert "no dominant factor is named" in got.basis


# ── provenance ───────────────────────────────────────────────────────────────

def test_one_policy_version_is_carried_and_two_are_not_collapsed_into_one():
    same = rollup.roll_up([facts("ln_1", price="1000", cost="700"),
                           facts("ln_2", price="1000", cost="700")],
                          quote_id=QUOTE)
    mixed = rollup.roll_up([facts("ln_1", price="1000", cost="700"),
                            facts("ln_2", price="1000", cost="700",
                                  version="ci_other")], quote_id=QUOTE)

    assert same.thresholds_version == "ci_test"
    assert mixed.thresholds_version is None
    assert "more than one version of the commercial policy" in mixed.basis


def test_from_diagnosis_carries_the_engines_own_figures_and_computes_nothing():
    out = diagnose(line_id="ln_1", quoted="850", costs=steady_cost())
    line = rollup.from_diagnosis(out)

    assert line.unit_cost == out.cost.expected_cost
    assert line.quoted_unit_price == out.quoted_unit_price
    assert line.qty == out.qty
    assert line.codes == out.codes
    assert line.strength == out.strength
    assert line.surfaces == out.surfaces
    assert line.attribution is out.attribution
    assert line.thresholds_version == out.thresholds_version


# ── determinism ──────────────────────────────────────────────────────────────

def test_the_same_quote_produces_the_same_bytes_whatever_order_it_arrives_in():
    """Only the roll-up's own orderings may decide the output. A quote whose
    lines arrive from a database in a different order is the same quote."""
    lines = [facts("ln_c", price="500", cost="900"),
             facts("ln_a", price="1000", cost="700"),
             facts("ln_b", price="400", cost="900", product="prd_2"),
             facts("ln_d", price="1000", cost=None)]

    first = json.dumps(rollup.roll_up(lines, quote_id=QUOTE).to_dict(),
                       sort_keys=True)
    second = json.dumps(rollup.roll_up(list(reversed(lines)),
                                       quote_id=QUOTE).to_dict(), sort_keys=True)

    assert first == second
    assert json.dumps(rollup.roll_up(lines, quote_id=QUOTE).to_dict(),
                      sort_keys=True) == first
