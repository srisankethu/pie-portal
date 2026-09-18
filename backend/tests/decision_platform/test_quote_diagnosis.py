"""The Quote Diagnosis Engine, phases 1-4: evidence, comparables, baselines, rules.

Scenarios 21-32 of the build specification, plus the two structural tests that
hold the invariants the scenarios cannot reach: a generalised look-ahead check
that mutates every post-quote row rather than asserting on one of them, and a
field-list assertion on the operations view.

Pure functions throughout — no database, no fixtures beyond a row builder. That
is the point of keeping the engine's layers free of a session.
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal

from app.commercial import dispersion
from app.commercial.config import CommercialThresholds
from app.commercial.quantity import band_for, bands
from app.commercial.quote_diagnosis import (baselines, comparables, cutover,
                                            evidence, opportunity, render, rules)

TH = CommercialThresholds()

QUOTE_DAY = date(2026, 6, 1)
KNOWABLE_BY = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def row(evidence_id: str, *, price: str, day: date,
        recorded: date | None = None, customer: str = "cst_a",
        product: str = "prd_1", qty: str = "10", unit: str = "Nos",
        cls: str = evidence.REALIZED, family: str | None = "Turning",
        imputed: bool = False) -> evidence.EvidenceRow:
    """One evidence row. ``recorded`` defaults to the event date, same-day."""
    stamp = _at(recorded if recorded is not None else day)
    return evidence.EvidenceRow(
        evidence_id=evidence_id, source_table="sales_txns",
        customer_id=customer, product_id=product, event_date=day,
        recorded_at=stamp, evidence_class=cls, qty=Decimal(qty),
        unit_price=Decimal(price), unit=unit,
        source_ref={comparables.FAMILY_KEY: family} if family else {},
        recorded_at_imputed=imputed)


def cost(evidence_id: str, *, unit_cost: str, day: date,
         recorded: date | None = None) -> evidence.CostObservation:
    stamp = _at(recorded if recorded is not None else day)
    return evidence.CostObservation(
        evidence_id=evidence_id, source_table="cost_records", product_id="prd_1",
        vendor_id="vnd_1", event_date=day, recorded_at=stamp,
        qty=Decimal("10"), unit_cost=Decimal(unit_cost), unit="Nos",
        source_ref={})


def _subject(qty: str = "10", customer: str = "cst_a") -> comparables.Subject:
    q = Decimal(qty)
    return comparables.Subject(
        customer_id=customer, product_id="prd_1", family="Turning", qty=q,
        band=band_for(q, TH), unit="each", as_of=QUOTE_DAY)


def _run(rows, *, quoted: str | None, costs=(), subject=None,
         segment=None, peer_rows=None, lost=()):
    """The whole phase 1-4 pipeline over one set of rows. Mirrors the service."""
    subj = subject or _subject()
    kept, dropped = evidence.knowable_at(rows, knowable_by=KNOWABLE_BY)
    kept, unnorm = evidence.normalize(kept, subject_unit=subj.unit)
    dropped = dropped + unnorm

    kept_costs, cost_dropped = evidence.costs_knowable_at(
        costs, knowable_by=KNOWABLE_BY)
    dropped = dropped + cost_dropped

    ladder = bands(TH)
    axis = comparables.select_customer_axis(kept, subject=subj, bands=ladder,
                                            segment=segment)
    peer = comparables.select_peer_axis(
        peer_rows if peer_rows is not None else kept, subject=subj,
        bands=ladder, segment=segment, recent_days=TH.diagnosis_recent_days)

    own = [r for r in axis.rows if r.customer_id == subj.customer_id]
    for_band, held = evidence.for_baseline(own)
    price = baselines.price_baseline(for_band, as_of=subj.as_of, th=TH, lost=lost)
    cost_base = baselines.cost_baseline(kept_costs, as_of=subj.as_of, th=TH)

    evset = evidence.EvidenceSet(rows=kept, costs=kept_costs,
                                 excluded=dropped + held)
    return rules.diagnose(
        line_id="ln_1", subject_customer_id=subj.customer_id,
        product_id=subj.product_id, qty=subj.qty,
        quantity_band=subj.band.label,
        quoted_unit_price=Decimal(quoted) if quoted is not None else None,
        as_of=subj.as_of, knowable_by=KNOWABLE_BY, axis=axis, peer=peer,
        price=price, cost=cost_base, evidence=evset, th=TH)


def _steady(n: int = 10, *, price: str = "1000", start: date = date(2026, 1, 5)):
    """``n`` unremarkable purchases at the same price, a week apart."""
    from datetime import timedelta
    return [row(f"s{i}", price=price, day=start + timedelta(days=7 * i))
            for i in range(n)]


# ── 21. recorded after the quote ─────────────────────────────────────────────

def test_evidence_dated_before_the_quote_but_recorded_after_it_is_excluded():
    """The correction the whole engine turns on.

    A bill dated 3 March that was keyed in on 28 March could not have informed a
    quote written on 10 March, and filtering on the event date says it could.
    """
    late = row("late", price="1000", day=date(2026, 5, 1),
               recorded=date(2026, 6, 20))
    kept, dropped = evidence.knowable_at([late], knowable_by=KNOWABLE_BY)

    assert kept == []
    assert [e.reason for e in dropped] == [evidence.RECORDED_AFTER]
    assert dropped[0].evidence_id == "late"


def test_a_row_with_no_recorded_time_is_excluded_rather_than_imputed():
    """Absence of evidence is not a pass: unknown visibility is not visibility."""
    unknown = dataclasses.replace(row("u", price="1000", day=date(2026, 1, 1)),
                                  recorded_at=None)
    kept, dropped = evidence.knowable_at([unknown], knowable_by=KNOWABLE_BY)

    assert kept == []
    assert dropped[0].reason == evidence.NO_RECORDED_AT


def test_a_backfilled_row_is_excluded_when_the_cutover_is_known():
    """Two thirds of one live book was bulk-loaded in a single month."""
    loaded = row("m", price="1000", day=date(2025, 5, 1),
                 recorded=date(2026, 3, 18))
    kept, dropped = evidence.knowable_at(
        [loaded], knowable_by=KNOWABLE_BY, backfill_before=date(2026, 4, 1))

    assert kept == []
    assert dropped[0].reason == evidence.BACKFILLED


# ── 22. imputed recorded_at downgrades confidence ────────────────────────────

def test_an_imputed_recorded_time_caps_confidence_at_weak():
    """§3: flag every imputed row and downgrade anything leaning on one.

    The band is otherwise a textbook STRONG — twelve tight, recent, same-band
    transactions — so the only thing moving the grade is the imputation.
    """
    clean = _steady(12)
    strong = _run(clean, quoted="1000")
    assert strong.strength == rules.STRONG

    tainted = list(clean)
    tainted[0] = dataclasses.replace(tainted[0], recorded_at_imputed=True)
    assert _run(tainted, quoted="1000").strength == rules.WEAK


def test_imputation_flags_what_it_touches_and_leaves_measured_stamps_alone():
    measured = row("a", price="1000", day=date(2026, 1, 5))
    missing = dataclasses.replace(row("b", price="1000", day=date(2026, 1, 5)),
                                  recorded_at=None)

    out = evidence.impute_recorded_at([measured, missing], lag_days=7)

    assert out[0] == measured and out[0].recorded_at_imputed is False
    assert out[1].recorded_at_imputed is True
    assert out[1].recorded_at.date() == date(2026, 1, 12)


# ── 23. structurally below the peer band ─────────────────────────────────────

def test_a_consistent_history_entirely_below_the_peer_band_is_an_account_finding():
    """The most expensive failure this engine can have, caught.

    This customer's own history is perfectly consistent, so the customer axis
    alone says "within range" and stops. The peer axis is what notices that the
    whole account is priced a fifth under everyone else.
    """
    own = _steady(10, price="800")
    peers = [row(f"p{i}", price="1000", day=date(2026, 3, 1),
                 customer=f"cst_{i}") for i in range(4)]

    out = _run(own + peers, quoted="800", peer_rows=own + peers)

    assert rules.WITHIN_HISTORICAL_RANGE in out.codes
    assert rules.BELOW_PEER_BAND_STRUCTURAL in out.codes


def test_the_structural_peer_finding_never_reaches_the_operations_view():
    """It routes to the owner. There is nothing the desk can do on this line,
    and telling them the account is under-priced is how a customer finds out."""
    own = _steady(10, price="800")
    peers = [row(f"p{i}", price="1000", day=date(2026, 3, 1),
                 customer=f"cst_{i}") for i in range(4)]

    out = _run(own + peers, quoted="800", peer_rows=own + peers)
    ops = rules.operations_view(out)

    assert rules.BELOW_PEER_BAND_STRUCTURAL in out.codes
    assert rules.BELOW_PEER_BAND_STRUCTURAL not in ops.codes
    assert ops.surfaces is False


# ── 24 & 25. symmetric trimming ──────────────────────────────────────────────

def test_one_extreme_high_and_one_extreme_low_are_both_removed():
    """Asymmetry here drifts every band upward and inflates every opportunity."""
    from datetime import timedelta
    start = date(2026, 1, 5)
    rows = [row(f"s{i}", price="1000", day=start + timedelta(days=7 * i))
            for i in range(10)]
    rows.append(row("lo", price="100", day=start))
    rows.append(row("hi", price="9000", day=start))
    # A spread the MAD can see: eleven identical prices give a zero MAD.
    rows[1] = dataclasses.replace(rows[1], unit_price=Decimal("1010"))
    rows[2] = dataclasses.replace(rows[2], unit_price=Decimal("990"))

    for_band, _ = evidence.for_baseline(rows)
    band = baselines.price_baseline(for_band, as_of=QUOTE_DAY, th=TH)

    assert band.excluded_low == 1 and band.excluded_high == 1
    dropped = {e.evidence_id for e in band.excluded}
    assert dropped == {"lo", "hi"}
    assert "lo" not in band.cited and "hi" not in band.cited


def test_a_zero_spread_keeps_ordinary_variation_and_still_removes_the_absurd():
    """Eleven identical prices and one a rupee off is not eleven outliers — but
    nor is a ₹100 among eleven ₹1,000s the bottom of the band."""
    rows = _steady(11)
    rows.append(row("odd", price="1010", day=date(2026, 4, 1)))

    for_band, _ = evidence.for_baseline(rows)
    band = baselines.price_baseline(for_band, as_of=QUOTE_DAY, th=TH)

    assert band.zero_spread_fallback is True
    assert band.excluded == ()
    assert band.band.high == Decimal("1010")

    rows.append(row("absurd", price="100", day=date(2026, 4, 8)))
    for_band, _ = evidence.for_baseline(rows)
    band = baselines.price_baseline(for_band, as_of=QUOTE_DAY, th=TH)

    assert band.zero_spread_fallback is True
    assert band.excluded_low == 1
    assert band.band.low == Decimal("1000")


def test_over_a_quarter_excluded_caps_confidence_and_flags_the_band():
    """§7: past this share, the band is not describing a population."""
    from datetime import timedelta
    start = date(2026, 1, 5)
    rows = [row(f"s{i}", price=p, day=start + timedelta(days=7 * i))
            for i, p in enumerate(["1000", "1010", "990", "1005", "995",
                                   "1000", "1002"])]
    rows += [row(f"x{i}", price=p, day=start + timedelta(days=90 + i))
             for i, p in enumerate(["9000", "9500", "80", "70"])]

    out = _run(rows, quoted="1000")

    assert out.price.exclusion_rate > TH.diagnosis_max_exclusion_rate
    assert out.price.over_exclusion_limit is True
    assert rules.POSSIBLE_EXCEPTIONAL_PRICE in out.context
    assert out.strength == rules.WEAK


# ── 26. lost quotes damp, never raise ────────────────────────────────────────

def test_lost_quotes_never_enter_the_band_and_record_resistance():
    """A band that took its top from a refused price would recommend re-quoting
    at a number the customer has already declined."""
    accepted = _steady(8, price="1000")
    declined = [row("l1", price="1000", day=date(2026, 4, 1),
                    cls=evidence.QUOTED_LOST),
                row("l2", price="980", day=date(2026, 4, 8),
                    cls=evidence.QUOTED_LOST)]

    for_band, held = evidence.for_baseline(accepted + declined)
    band = baselines.price_baseline(for_band, as_of=QUOTE_DAY, th=TH,
                                    lost=declined)

    assert {r.evidence_id for r in for_band} == {r.evidence_id for r in accepted}
    assert {e.evidence_id for e in held} == {"l1", "l2"}
    assert band.band.high == Decimal("1000")
    assert band.resistance.observed is True
    assert band.resistance.lost_at_or_below_band == 2
    assert band.resistance.highest_accepted == Decimal("1000")


def test_an_open_quote_is_evidence_of_nothing():
    """54% of the quotes on one live book are `expired`, which is not a loss."""
    for_band, held = evidence.for_baseline(
        [row("o", price="5000", day=date(2026, 4, 1), cls=evidence.QUOTED_OPEN)])

    assert for_band == []
    assert held[0].reason == evidence.NO_OUTCOME


# ── 27 & 28. units ───────────────────────────────────────────────────────────

def test_two_spellings_of_the_same_unit_are_comparable():
    """One live book spells the same unit `pcs` on 456 items and `nos` on 57."""
    rows = [row("a", price="1000", day=date(2026, 3, 1), unit="Nos"),
            row("b", price="1000", day=date(2026, 3, 8), unit="pcs")]

    kept, dropped = evidence.normalize(rows, subject_unit="each")

    assert len(kept) == 2 and dropped == []


def test_a_row_in_another_unit_is_excluded_counted_and_reported():
    """No conversion factor exists anywhere in these books, so there is no
    honest way to reconcile a metre against a piece. Never guessed."""
    rows = [row("a", price="1000", day=date(2026, 3, 1), unit="Nos"),
            row("m", price="1000", day=date(2026, 3, 8), unit="metre")]

    kept, dropped = evidence.normalize(rows, subject_unit="each")
    evset = evidence.EvidenceSet(rows=kept, excluded=dropped)

    assert [r.evidence_id for r in kept] == ["a"]
    assert dropped[0].reason == evidence.UNIT_MISMATCH
    assert evset.summary()["unnormalizable"] == 1


def test_a_row_whose_item_states_no_unit_is_excluded_and_counted():
    rows = [row("u", price="1000", day=date(2026, 3, 1), unit=None)]

    kept, dropped = evidence.normalize(rows, subject_unit="each")

    assert kept == []
    assert dropped[0].reason == evidence.NO_UNIT
    assert evidence.EvidenceSet(excluded=dropped).summary()["unnormalizable"] == 1


# ── 29. currency ─────────────────────────────────────────────────────────────

def test_no_evidence_row_can_carry_a_foreign_price():
    """§5 asks for an as-of FX conversion. These books cannot need one, and the
    guarantee is stronger than a conversion would be: ``_refuses_currency``
    rejects a foreign document at the ingestion seam, so a row that reached an
    evidence set is denominated in the book's own currency by construction.

    Asserted as a property of the type rather than of a value: there is no
    currency field to get wrong, and a future field would fail this test.
    """
    names = {f.name for f in dataclasses.fields(evidence.EvidenceRow)}

    assert "currency" not in names and "currency_code" not in names
    assert "exchange_rate" not in names and "fx_rate" not in names


# ── 30. a cost-driven diagnosis carries no cost figure ───────────────────────

def test_a_cost_driven_diagnosis_renders_no_cost_figure_to_operations():
    rows = _steady(10)
    costs = [cost("c1", unit_cost="700", day=date(2026, 1, 10)),
             cost("c2", unit_cost="700", day=date(2026, 2, 10)),
             cost("c3", unit_cost="700", day=date(2026, 3, 10)),
             cost("c4", unit_cost="900", day=date(2026, 5, 10))]

    out = _run(rows, quoted="1000", costs=costs)
    ops = rules.operations_view(out)

    assert rules.COST_DRIVEN_MARGIN_RISK in out.codes
    assert out.cost.expected_cost == Decimal("900")
    serialised = dataclasses.asdict(ops)
    assert "900" not in str(serialised)
    assert "700" not in str(serialised)


def test_the_operations_view_has_no_economics_field():
    """I3, as a structural property rather than a habit.

    Reads the dataclass's own field list, so adding a cost field to the
    operations view is a failing test rather than a review somebody has to catch.
    """
    names = rules.operations_field_names()

    assert names.isdisjoint(rules.FORBIDDEN_OPERATIONS_FIELDS)
    # And nothing that merely *sounds* like economics either.
    assert not any("cost" in n or "margin" in n or "peer" in n for n in names)


# ── 31. an unexplained low purchase must not become the baseline ─────────────

def test_one_unexplained_low_purchase_does_not_become_the_expected_cost():
    """Otherwise every subsequent normally-priced purchase reads as cost-driven
    erosion, and the engine cries wolf on its own history."""
    costs = [cost("c1", unit_cost="700", day=date(2026, 1, 10)),
             cost("c2", unit_cost="710", day=date(2026, 2, 10)),
             cost("c0", unit_cost="90", day=date(2026, 2, 20)),
             cost("c3", unit_cost="700", day=date(2026, 3, 10)),
             cost("c4", unit_cost="705", day=date(2026, 4, 10)),
             cost("c5", unit_cost="700", day=date(2026, 5, 10))]

    base = baselines.cost_baseline(costs, as_of=QUOTE_DAY, th=TH)

    assert base.expected_cost is not None
    assert base.expected_cost > Decimal("600")
    assert "c0" not in base.cited
    assert base.excluded_low == 1
    assert base.unexplained_low_purchase is True


def test_the_next_normal_purchase_after_an_odd_low_one_is_not_cost_driven():
    rows = _steady(10)
    costs = [cost("c1", unit_cost="700", day=date(2026, 1, 10)),
             cost("c0", unit_cost="90", day=date(2026, 2, 20)),
             cost("c2", unit_cost="700", day=date(2026, 3, 10)),
             cost("c3", unit_cost="705", day=date(2026, 4, 10)),
             cost("c4", unit_cost="700", day=date(2026, 5, 10))]

    out = _run(rows, quoted="1000", costs=costs)

    assert rules.COST_DRIVEN_MARGIN_RISK not in out.codes
    assert rules.KNOWN_COST_CHANGE not in out.codes
    assert rules.POSSIBLE_COST_DRIVEN in out.context


# ── 32. computed, stored, and silent ─────────────────────────────────────────

def test_a_sub_threshold_deviation_is_diagnosed_and_renders_nothing():
    """Default state is silent. Alert fatigue kills this faster than an error."""
    rows = _steady(10, price="1000")
    # A rupee under the band. Real, correct, and nobody's business.
    out = _run(rows, quoted="999")

    assert rules.BELOW_HISTORICAL_RANGE in out.codes
    assert out.deviation_per_unit == Decimal("1")
    assert out.surfaces is False
    assert rules.operations_view(out).surfaces is False


def test_a_material_deviation_on_a_strong_band_does_surface():
    rows = _steady(12, price="1000")
    out = _run(rows, quoted="850")

    assert out.strength == rules.STRONG
    assert rules.BELOW_HISTORICAL_RANGE in out.codes
    assert out.surfaces is True
    assert rules.operations_view(out).surfaces is True


def test_insufficient_evidence_is_a_valid_and_silent_answer():
    out = _run([row("only", price="1000", day=date(2026, 3, 1))], quoted="500")

    assert out.strength == rules.INSUFFICIENT
    assert out.surfaces is False


# ── the generalised look-ahead test ──────────────────────────────────────────

def test_mutating_every_post_quote_record_changes_no_diagnosis():
    """Catches look-ahead bugs the scenario tests miss.

    A scenario test asserts on the one row it planted. This one says the engine
    cannot see *anything* that happened after the quote: every post-quote row is
    mutated — price, quantity, class — and the diagnosis must be byte-identical.
    """
    from datetime import timedelta
    before = _steady(10)
    after = [row(f"post{i}", price="4000",
                 day=QUOTE_DAY + timedelta(days=1 + i),
                 recorded=QUOTE_DAY + timedelta(days=1 + i)) for i in range(5)]
    post_costs = [cost(f"pc{i}", unit_cost="9999",
                       day=QUOTE_DAY + timedelta(days=1 + i)) for i in range(3)]

    baseline_run = _run(before + after, quoted="900", costs=post_costs)

    mutated = [dataclasses.replace(r, unit_price=r.unit_price * 7,
                                   qty=r.qty * 3,
                                   evidence_class=evidence.QUOTED_LOST)
               for r in after]
    mutated_costs = [dataclasses.replace(c, unit_cost=c.unit_cost * 11)
                     for c in post_costs]
    second_run = _run(before + mutated, quoted="900", costs=mutated_costs)

    assert baseline_run.codes == second_run.codes
    assert baseline_run.strength == second_run.strength
    assert baseline_run.price.to_dict() == second_run.price.to_dict()
    assert baseline_run.cost.to_dict() == second_run.cost.to_dict()
    assert baseline_run.deviation_per_unit == second_run.deviation_per_unit


def test_the_same_inputs_produce_the_same_bytes():
    """§13. Sorting is total, with the primary key as tie-break, so the band
    never depends on the order the database happened to return rows in."""
    rows = _steady(9)
    shuffled = list(reversed(rows))

    a = _run(rows, quoted="900")
    b = _run(shuffled, quoted="900")

    assert a.price.to_dict() == b.price.to_dict()
    assert a.price.cited == b.price.cited
    assert a.codes == b.codes


# ── the quantity band is a hard filter ───────────────────────────────────────

def test_a_five_hundred_piece_order_is_not_a_ten_piece_comparable():
    """Quantity is part of a price's identity, and tiers 1-3 enforce it."""
    ten = row("a", price="1000", day=date(2026, 3, 1), qty="10")
    five_hundred = row("b", price="600", day=date(2026, 3, 8), qty="500")
    subj = _subject(qty="10")

    axis = comparables.select_customer_axis([ten, five_hundred], subject=subj,
                                            bands=bands(TH))
    tiers = {c.row.evidence_id: c.tier for c in axis.comparables}

    assert tiers["a"] == comparables.TIER_SAME_CUSTOMER_SKU_BAND
    assert tiers["b"] not in comparables.BAND_FILTERED_TIERS


def test_no_segment_drawn_leaves_the_segment_tier_empty_rather_than_everyone():
    """Tier 5 already means everyone; a tier that quietly duplicated it would
    inflate every count the strength table reads."""
    peer = row("p", price="1000", day=date(2026, 3, 1), customer="cst_b")
    axis = comparables.select_customer_axis([peer], subject=_subject(),
                                            bands=bands(TH), segment=None)

    assert axis.tier_counts() == {comparables.TIER_ANY_CUSTOMER_SKU_BAND: 1}


def test_the_peer_band_excludes_the_subject_and_says_whether_a_segment_applied():
    own = _steady(3)
    peers = [row(f"p{i}", price="1200", day=date(2026, 3, 1),
                 customer=f"cst_{i}") for i in range(3)]

    band = comparables.select_peer_axis(own + peers, subject=_subject(),
                                        bands=bands(TH), segment=None,
                                        recent_days=TH.diagnosis_recent_days)

    assert band.customer_count == 3
    assert band.stats.median == Decimal("1200")
    assert band.segment_applied is False


# ── the shared dispersion primitive ──────────────────────────────────────────

def test_the_quantile_median_agrees_with_the_decimal_median():
    """One convention, so two medians on one screen cannot disagree."""
    from app.commercial.benchmark import median_decimal
    for values in ([Decimal("1"), Decimal("2")],
                   [Decimal("1"), Decimal("2"), Decimal("3")],
                   [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("41")]):
        assert dispersion.quantile(values, Decimal("0.5")) == median_decimal(values)


def test_the_trim_names_direction_and_distance_for_every_row_it_drops():
    """§I4: a diagnosis carries the ids of the rows it excluded and why."""
    values = [Decimal(x) for x in
              ["1000", "1010", "990", "1005", "995", "100", "9000"]]

    result = dispersion.trim(values, k=Decimal("3"))

    assert result.excluded_low == 1 and result.excluded_high == 1
    directions = {d.direction for d in result.excluded}
    assert directions == {dispersion.LOW, dispersion.HIGH}
    assert all(d.deviations > 3 for d in result.excluded)


# ── phase 6: opportunity is potential, never missed ──────────────────────────

def test_the_opportunity_is_a_range_from_the_band_bottom_to_its_median():
    rows = _steady(12, price="1000")
    rows[3] = dataclasses.replace(rows[3], unit_price=Decimal("980"))
    rows[7] = dataclasses.replace(rows[7], unit_price=Decimal("1020"))
    out = _run(rows, quoted="850", costs=[cost("c1", unit_cost="600",
                                               day=date(2026, 1, 10))])

    opp = opportunity.compute(out, th=TH)

    assert opp.exists
    assert opp.per_unit_low == out.price.band.low - Decimal("850")
    assert opp.per_unit_high == out.price.band.median - Decimal("850")
    assert opp.low == opp.per_unit_low * out.qty
    assert opp.cost_on_record is True


def test_no_opportunity_is_asserted_on_a_line_inside_its_band():
    out = _run(_steady(12), quoted="1000")

    assert opportunity.compute(out, th=TH).exists is False


def test_a_cost_driven_line_gets_no_opportunity_figure():
    """§10: cost-driven erosion is not pricing leakage, and a money figure under
    the word "opportunity" would send somebody to renegotiate the wrong thing."""
    rows = _steady(10)
    costs = [cost("c1", unit_cost="700", day=date(2026, 1, 10)),
             cost("c2", unit_cost="700", day=date(2026, 2, 10)),
             cost("c3", unit_cost="700", day=date(2026, 3, 10)),
             cost("c4", unit_cost="900", day=date(2026, 5, 10))]
    out = _run(rows, quoted="1000", costs=costs)

    assert rules.COST_DRIVEN_MARGIN_RISK in out.codes
    assert opportunity.compute(out, th=TH).exists is False


def test_an_opportunity_without_a_cost_baseline_is_shown_and_qualified():
    """Half of one live item master has no usable cost. Withholding the figure
    there would silence the engine on half the catalogue; asserting it without
    the qualifier would call a cost-driven line an opportunity."""
    out = _run(_steady(12), quoted="850", costs=[])

    opp = opportunity.compute(out, th=TH)

    assert opp.exists is True
    assert opp.cost_on_record is False
    assert "cost-driven cause cannot be ruled out" in opp.basis
    sentence = render._opportunity_sentence(opp, th=TH)
    assert "cannot be ruled out" in sentence


def test_the_opportunity_is_never_worded_as_a_loss():
    out = _run(_steady(12), quoted="850")
    sentence = render._opportunity_sentence(opportunity.compute(out, th=TH), th=TH)

    assert "potential" in sentence
    assert "not profit forgone" in sentence
    for forbidden in ("you lost", "lost ", "missed", "forgone profit"):
        assert forbidden not in sentence.lower().replace("not profit forgone", "")


# ── phase 5: the two renderings ──────────────────────────────────────────────

def test_the_operations_card_reads_like_the_specification():
    rows = _steady(12, price="1000")
    rows[3] = dataclasses.replace(rows[3], unit_price=Decimal("980"))
    rows[7] = dataclasses.replace(rows[7], unit_price=Decimal("1020"))
    out = _run(rows, quoted="850")

    card = render.render_operations(rules.operations_view(out), th=TH)

    assert card.renders is True
    assert card.headline == "Below this customer's historical pricing"
    assert card.quoted.endswith("850")
    assert "–" in card.historical
    assert card.evidence in ("Strong", "Moderate")
    assert "comparable transactions" in card.evidence_detail
    assert "purchased this item" in card.why
    # Prose, not the labelled field: "between X – Y" reads as a typo.
    assert " and " in card.why and " – " not in card.why
    assert card.qualification == render.QUALIFICATION
    assert card.actions == (render.REVIEW_PRICE, render.DISMISS)


def test_the_cost_driven_card_carries_the_sentence_and_no_figure():
    rows = _steady(10)
    costs = [cost("c1", unit_cost="700", day=date(2026, 1, 10)),
             cost("c2", unit_cost="700", day=date(2026, 2, 10)),
             cost("c3", unit_cost="700", day=date(2026, 3, 10)),
             cost("c4", unit_cost="900", day=date(2026, 5, 10))]
    out = _run(rows, quoted="1000", costs=costs)

    card = render.render_operations(rules.operations_view(out), th=TH)

    assert card.headline == ("Margin on this line is compressed by supply cost, "
                            "not by your price. No price change needed.")
    rendered = str(dataclasses.asdict(card))
    assert "700" not in rendered and "900" not in rendered


def test_the_operations_renderer_cannot_be_handed_an_owner_diagnosis():
    """The structural half of I3: the desk's renderer takes the desk's type.

    If it could accept the owner object it would only be a filter again, and a
    filter is what was wrong both times this repository leaked a boundary.
    """
    import inspect
    sig = inspect.signature(render.render_operations)
    annotation = sig.parameters["ops"].annotation

    assert annotation in (rules.OperationsDiagnosis, "OperationsDiagnosis")


def test_every_dismissal_reason_is_aggregatable():
    """Free text tunes nothing. The vocabulary is the point."""
    reasons = dict(render.dismissal_reasons())

    assert "PRICE_IS_CORRECT" in reasons
    assert "COMPARISON_IS_WRONG" in reasons
    assert all(code.isupper() for code in reasons)


def test_the_owner_report_says_what_the_desk_is_not_told():
    own = _steady(10, price="800")
    peers = [row(f"p{i}", price="1000", day=date(2026, 3, 1),
                 customer=f"cst_{i}") for i in range(4)]
    out = _run(own + peers, quoted="800", peer_rows=own + peers)

    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)
    body = " ".join(report.lines)

    assert rules.BELOW_PEER_BAND_STRUCTURAL in report.codes
    assert "account-level pricing question" in body
    assert "not shown to the salesperson" in body


def test_the_owner_report_names_the_gap_when_no_cost_is_knowable():
    out = _run(_steady(10), quoted="850", costs=[])
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert any("withheld rather than" in ln for ln in report.lines)


def test_an_evidence_summary_with_no_outcomes_says_so():
    """§8: if outcome data is absent, say so rather than letting a band of
    realized prices imply that all history is acceptance."""
    out = _run(_steady(10), quoted="850")
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert "no quote outcomes recorded" in report.evidence


# ── the migration cut-over, detected and never applied ───────────────────────

def test_a_bulk_load_is_detected_with_the_counts_behind_it():
    from datetime import timedelta
    loaded = [cutover.Observation(
        event_date=date(2025, 4, 1) + timedelta(days=i),
        recorded_at=datetime(2026, 3, 18, tzinfo=timezone.utc)) for i in range(200)]
    live = [cutover.Observation(
        event_date=date(2026, 4, 1) + timedelta(days=i),
        recorded_at=_at(date(2026, 4, 4) + timedelta(days=i))) for i in range(60)]

    found = cutover.detect(loaded + live)

    assert found.peak_month == "2026-03"
    assert found.peak_count == 200
    assert found.suggested == date(2026, 4, 1)
    assert "migration, not a month of work" in found.reason


def test_a_busy_month_of_live_entry_is_not_a_migration():
    """Either half alone is unremarkable: a busy month is just a busy month."""
    from datetime import timedelta
    busy = [cutover.Observation(
        event_date=date(2026, 3, 1) + timedelta(days=i % 28),
        recorded_at=_at(date(2026, 3, 3) + timedelta(days=i % 28)))
        for i in range(200)]

    found = cutover.detect(busy)

    assert found.peak_share > cutover.BULK_MONTH_SHARE
    assert found.suggested is None
    assert "busy month, not a load" in found.reason


def test_no_creation_stamps_says_what_to_do_about_it():
    found = cutover.detect([])

    assert found.suggested is None
    assert "re-sync" in found.reason


def test_a_flat_price_history_does_not_produce_a_sentence_saying_between_one_value():
    """"between ₹218." is a sentence with an operand missing.

    A customer who pays the same price on every order collapses the band to a
    single value, and that is the ordinary shape of a repeat account here, not
    an edge case: the first real quote this engine surfaced a STRONG card on
    had eight prior purchases at one price. ``_range_words`` already returned
    the bare figure for that case — it was the caller that went on prefixing
    "between" to it.
    """
    out = _run(_steady(10, price="218"), quoted="339")
    card = render.render_operations(rules.operations_view(out), th=TH)

    assert card.renders is True
    assert "between" not in card.why
    assert "at " in card.why
    assert card.why.endswith(".")
    # The figure itself still has to be in the sentence.
    assert "218" in card.why


def test_one_prior_transaction_is_not_reported_as_a_line_that_was_compared():
    """``comparable`` must follow the grade, not the codes.

    One prior sale still yields a usable band, so ``_diagnose`` takes the price
    branch and appends ``ABOVE_HISTORICAL_RANGE``; ``INSUFFICIENT_EVIDENCE``
    never reaches ``codes``. Reading the codes for "was there enough to
    compare" therefore answered yes on a line the engine had just graded
    INSUFFICIENT, and the quote summary counted it among the lines compared.
    """
    out = _run(_steady(1, price="1446"), quoted="1974")
    card = render.render_operations(rules.operations_view(out), th=TH)

    assert out.strength == rules.INSUFFICIENT
    assert rules.INSUFFICIENT_EVIDENCE not in out.codes  # the trap
    assert card.renders is False
    assert card.comparable is False


def test_a_graded_line_is_reported_as_compared():
    """The other direction, so the fix cannot be "always false"."""
    out = _run(_steady(10, price="218"), quoted="339")
    card = render.render_operations(rules.operations_view(out), th=TH)

    assert out.strength in (rules.STRONG, rules.MODERATE)
    assert card.comparable is True


def test_declining_to_assert_an_opportunity_gives_a_reason_not_the_conclusion():
    """"No opportunity is asserted — no opportunity is asserted."

    `_opportunity_sentence` reads `basis` as a reason and writes "No
    opportunity is asserted — {basis}." A single module constant carried the
    conclusion in that slot, so the sentence restated itself. It went unread
    for as long as nothing in the front end drew the owner projection, which
    is the whole reason it survived: the string was generated on every
    manager's quote and rendered on none of them.
    """
    out = _run(_steady(10, price="218"), quoted="339")   # above the band
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert report.opportunity.startswith("No opportunity is asserted — ")
    reason = report.opportunity.split(" — ", 1)[1]
    assert "no opportunity is asserted" not in reason.lower()
    assert "not below the range" in reason


def test_a_line_with_no_history_is_not_told_it_sits_above_a_range():
    """There is no range. Saying it is "not below" one asserts that there is.

    Found by rendering a real quote: the fourth line of QT-095 is a new item
    with nothing on record, and its card read "No comparable transaction was
    knowable when this quote was written" immediately above "this line is not
    below the range its history supports". The first guard in `compute` caught
    both "above a real band" and "no band at all", and only one of those is
    what its reason described.
    """
    out = _run([], quoted="900")          # nothing comparable at all
    opp = opportunity.compute(out, th=TH)

    assert rules.INSUFFICIENT_EVIDENCE in out.codes
    assert not opp.exists
    assert "no range to sit below" in opp.basis
    assert "not below the range" not in opp.basis


# ── driver attribution, carried and said ─────────────────────────────────────
#
# The split itself is tested in ``test_quote_diagnosis_drivers``. What is tested
# here is the other half: that the diagnosis carries one, that the owner report
# says it in a sentence naming *both* factors, that a refusal is visible rather
# than an empty block, and that none of it can reach the desk.

def _risen() -> list:
    """Four purchases at 700 and one, latest, at 900 — a knowable cost rise.

    Four at the old level is ``diagnosis_moderate_min_comparables``: one fewer
    and the cost baseline grades WEAK and the attribution refuses, which is a
    different test.
    """
    return [cost("c1", unit_cost="700", day=date(2026, 1, 10)),
            cost("c2", unit_cost="700", day=date(2026, 2, 10)),
            cost("c3", unit_cost="700", day=date(2026, 3, 10)),
            cost("c4", unit_cost="700", day=date(2026, 4, 10)),
            cost("c5", unit_cost="900", day=date(2026, 5, 10))]


def test_the_diagnosis_carries_the_split_of_its_own_margin_movement():
    """``attribute`` is reached through ``diagnose``, not only directly.

    A computation nothing calls is a computation that drifts from the engine it
    was written for, and the grade it is handed has to be the grade the
    diagnosis publishes — not a second one derived on the way in.
    """
    out = _run(_steady(10), quoted="850", costs=_risen())

    codes = [d.code for d in out.attribution.drivers]
    assert codes == ["PRICE_POSITION_EFFECT", "COST_LEVEL_EFFECT"]
    assert out.attribution.reconciles is True
    assert all(d.strength in (rules.STRONG, rules.MODERATE)
               for d in out.attribution.drivers)
    # The grade the diagnosis publishes is the grade the price driver carries.
    assert next(d for d in out.attribution.drivers
                if d.code == "PRICE_POSITION_EFFECT").strength == out.strength


def test_a_cost_rise_beside_a_price_cut_is_reported_as_both_and_not_the_larger():
    """The sentence, and the defect it exists to prevent.

    Quoted 850 against a band median of 1000, at a cost that moved 700 -> 900.
    The cost term is the larger one; a headline naming it alone would be true
    and would excuse the half somebody chose.
    """
    out = _run(_steady(10), quoted="850", costs=_risen())
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)
    headline = report.attribution.headline

    assert headline == (
        "Margin on this line is 35.88 pp lower than the same line at the band "
        "median price and the historical purchase cost: price -12.35 pp, "
        "cost level -23.53 pp.")
    assert [d.effect for d in report.attribution.drivers] == [
        "-12.35 pp (-₹150 per unit)", "-23.53 pp (-₹200 per unit)"]


def test_a_factor_that_did_not_move_is_stated_as_flat_rather_than_dropped():
    """"cost level unchanged" is the sentence that tells a reader the price is
    the whole of it. A driver left out of the sentence is a split that no longer
    adds up, presented as though it did."""
    steady_cost = [cost("c1", unit_cost="700", day=date(2026, 1, 10)),
                   cost("c2", unit_cost="700", day=date(2026, 2, 10)),
                   cost("c3", unit_cost="700", day=date(2026, 3, 10)),
                   cost("c4", unit_cost="700", day=date(2026, 4, 10)),
                   cost("c5", unit_cost="700", day=date(2026, 5, 10))]
    out = _run(_steady(10), quoted="850", costs=steady_cost)
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert report.attribution.headline.endswith(
        "price -12.35 pp, cost level unchanged.")
    assert report.attribution.drivers[1].effect == "unchanged (₹0 per unit)"


def test_the_counterfactual_the_split_was_taken_in_is_on_the_card():
    """Order-dependent decomposition, so the order is named in the output's own
    words rather than left for a reader to guess — and the residual travels with
    it, because it is never forced to zero and never hidden."""
    out = _run(_steady(10), quoted="850", costs=_risen())
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert report.attribution.note.startswith("PRICE_THEN_COST:")
    assert "at the price actually quoted" in report.attribution.note
    assert "to within" in report.attribution.note


def test_a_refusal_is_rendered_in_words_and_not_as_an_empty_block():
    """Absence of evidence is not a pass, in a new place.

    No purchase on record means no margin at all, so there is no movement to
    split. A block that simply did not draw would read as "the price and the
    cost both behaved", which is the one thing it does not mean.
    """
    out = _run(_steady(10), quoted="850", costs=[])
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert out.surfaces is True
    assert report.attribution.renders is True
    assert report.attribution.headline == ""
    assert report.attribution.drivers == ()
    assert report.attribution.note.startswith("NO_COST_BASELINE:")
    assert "0 usable purchase observations on record" in report.attribution.note


def test_a_thin_band_refuses_the_split_and_names_both_grades():
    """The gate is on both sides, because both enter the movement itself."""
    thin = [row("s0", price="1000", day=date(2026, 2, 1))]
    out = _run(thin, quoted="850", costs=_risen())
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert out.attribution.drivers == ()
    assert report.attribution.note.startswith("EVIDENCE_TOO_THIN:")
    assert "MODERATE" in report.attribution.note


def test_the_attribution_block_is_as_silent_as_the_surfacing_gate():
    """Not a second gate. ``_surfaces`` decides interruption and this can only
    narrow that answer, never widen it — a line quoted inside its own band is
    silent whatever the cost did."""
    out = _run(_steady(10), quoted="1000", costs=_risen())
    report = render.render_owner(out, opportunity.compute(out, th=TH), th=TH)

    assert rules.COST_DRIVEN_MARGIN_RISK in out.codes
    assert out.surfaces is False
    # Computed regardless — the gate governs interruption, not calculation.
    assert out.attribution.reconciles is True
    assert report.attribution.renders is False
    assert report.attribution.headline != ""


def test_no_driver_and_no_attribution_field_can_reach_the_operations_view():
    """RESTRICTED in its entirety, held structurally rather than by a filter.

    ``operations_view`` builds a type with no field to put a driver in, and
    ``FORBIDDEN_OPERATIONS_FIELDS`` names every spelling one could arrive under
    so a field added later fails this rather than a review somebody has to
    catch. The serialised check is the second half: a code or a percentage point
    smuggled into an existing field would pass the field-list assertion.
    """
    out = _run(_steady(10), quoted="850", costs=_risen())
    ops = rules.operations_view(out)
    serialised = str(dataclasses.asdict(ops))

    names = rules.operations_field_names()
    assert names.isdisjoint(rules.FORBIDDEN_OPERATIONS_FIELDS)
    assert not [n for n in names
                if "driver" in n or "attribution" in n or "effect" in n]
    for word in ("PRICE_POSITION_EFFECT", "COST_LEVEL_EFFECT", "pp",
                 "700", "900", "MAJOR"):
        assert word not in serialised, word
    # And the desk's card, which is what is actually served. A percentage point
    # is how an attribution would arrive in an existing string field.
    card = render.render_operations(ops, th=TH)
    assert " pp" not in str(dataclasses.asdict(card))
