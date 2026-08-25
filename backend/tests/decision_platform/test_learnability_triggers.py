"""The §8 triggers, and the one distinction they exist to keep.

`14-machine-learning.md` §8 gates eight techniques on conditions it calls
"checkable rather than arguable". Nothing checked them until
``measure_learnability.triggers`` did, and the failure mode of a status field
like this one is the same failure mode §1 is about everywhere else: a bar that
reads *0 of 2,000* looks like a measurement with a distance, and on a book that
has never captured an enquiry it is an absence with no distance at all.

So the property under test is not the arithmetic. It is that **UNKNOWN and
NOT_YET stay apart** — and that the one trigger whose second half is a
judgement says so rather than answering FIRED on the countable half.

The script is a CLI entry point and §7's size floor exempts it from the SOLID
checks. It does not exempt this: a verdict that could quietly collapse into a
softer no is exactly what this repository tests.
"""
from __future__ import annotations

import importlib.util
import pathlib
from datetime import date, timedelta

import pytest

from app.domain import models

ORG = "org_trig"
_SCRIPT = (pathlib.Path(__file__).resolve().parents[3]
           / "scripts" / "measure_learnability.py")


@pytest.fixture(scope="module")
def census_module():
    spec = importlib.util.spec_from_file_location("measure_learnability", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _empty_census() -> dict:
    """The two sections ``triggers`` reads, as an untouched book produces them."""
    return {
        "quote_outcomes": {"losses_with_a_reason": 0,
                           "quotes_with_an_outcome_row": 0},
        "enquiry_corpus": {"inbound_lines": 0, "with_a_live_disposition": 0},
    }


def _by_technique(rows) -> dict[str, dict]:
    return {r["section"]: r for r in rows}


# ── the distinction ─────────────────────────────────────────────────────────
def test_an_untouched_book_answers_unknown_and_never_not_yet(
        session, census_module):
    """Every measurable trigger, on a book with nothing in it.

    ``NOT_YET`` would assert a distance to a bar. There is no distance: no quote
    has been read, no enquiry captured, no decision judged, and each of those
    absences is indistinguishable from a scope nobody granted.
    """
    rows = census_module.triggers(session, ORG, _empty_census())
    measurable = [r for r in rows if r["status"] != census_module.NEVER]

    assert measurable, "the report named no measurable trigger at all"
    assert all(r["status"] == census_module.UNKNOWN for r in measurable), (
        "a trigger reported a distance to its bar on a book that has measured "
        f"nothing: {[(r['section'], r['status']) for r in measurable]}")


def test_the_two_that_are_not_waiting_on_data_never_expire(
        session, census_module):
    """Item grain is closed by argument; the salesperson gate is a gate. Both
    are listed so the report is the whole of §8 rather than its countable
    part — a technique missing from a status report reads as one nobody
    gated."""
    by = _by_technique(census_module.triggers(session, ORG, _empty_census()))

    assert by["§5.7–§5.10"]["status"] == census_module.NEVER
    assert by["§4.3"]["status"] == census_module.NEVER


# ── the countable half, once there is something to count ────────────────────
def test_recorded_losses_below_the_floor_report_the_real_distance(
        session, census_module):
    """Once quotes are being read, NOT_YET is the honest answer and the number
    beside it is a measurement rather than an absence."""
    session.add(models.QuoteDoc(
        organization_id=ORG, external_ref="est-1", customer_ref="Acme",
        date=date(2026, 5, 1), source_status="sent", outcome="UNRECORDED"))
    session.commit()
    census = _empty_census()
    census["quote_outcomes"] = {"losses_with_a_reason": 6,
                                "quotes_with_an_outcome_row": 12}

    row = _by_technique(census_module.triggers(session, ORG, census))["§5.1"]

    assert row["status"] == census_module.NOT_YET
    assert (row["measured"], row["needs"]) == (6, census_module.MIN_MINORITY_EVENTS)


def test_clearing_the_loss_floor_asks_a_person_rather_than_firing(
        session, census_module):
    """§5.1's trigger has two halves and only one is countable. The other —
    "a loss-reason table that has stopped being surprising" — is a judgement,
    and a script that answered FIRED on the count alone would have made it."""
    session.add(models.QuoteDoc(
        organization_id=ORG, external_ref="est-2", customer_ref="Acme",
        date=date(2026, 5, 1), source_status="sent", outcome="UNRECORDED"))
    session.commit()
    census = _empty_census()
    census["quote_outcomes"] = {"losses_with_a_reason": 140,
                                "quotes_with_an_outcome_row": 400}

    row = _by_technique(census_module.triggers(session, ORG, census))["§5.1"]

    assert row["status"] == census_module.JUDGEMENT
    assert row["status"] != census_module.FIRED
    assert "surprising" in row["why"]


def test_an_unworked_queue_is_unknown_not_a_detector_within_its_band(
        session, census_module):
    """The reused half. ``dismissal_band`` already orders its answers so the two
    "we do not know" cases come before any rate, and this delegates to it rather
    than restating the bands — a second copy is the one that would forget."""
    for i in range(3):
        session.add(models.Decision(
            organization_id=ORG, decision_type="CUSTOMER_DORMANCY",
            decision_key=f"{ORG}:dorm:{i}", subject_entity_type="CUSTOMER",
            subject_entity_id=f"c{i}", assigned_role="OWNER",
            priority_band="MEDIUM", priority_score=50, status="OPEN"))
    session.commit()

    row = _by_technique(census_module.triggers(
        session, ORG, _empty_census()))["§5.6"]

    assert row["status"] == census_module.UNKNOWN
    assert row["measured"] == "NOT_REVIEWED"


def test_captured_wording_separates_the_worked_subset_from_the_rest(
        session, census_module):
    """§7a.4's finding, carried into the trigger that would otherwise hide it.

    A line captured at the Quote Builder is an ask somebody chose to work. That
    subset is the right corpus for a recall benchmark and the wrong one for a
    coverage denominator, so the count says how much of it is which.
    """
    for i in range(2):
        session.add(models.InboundLine(
            organization_id=ORG, raw_text=f"pls quote {i}", channel="WHATSAPP",
            source_ref=f"quote:q-{i}"))
    session.add(models.InboundLine(
        organization_id=ORG, raw_text="from an adapter", channel="EMAIL",
        source_ref="wa:msg-9"))
    session.commit()
    census = _empty_census()
    census["enquiry_corpus"] = {"inbound_lines": 3, "with_a_live_disposition": 0}

    row = _by_technique(census_module.triggers(session, ORG, census))["§5.17"]

    assert row["status"] == census_module.NOT_YET
    assert row["measured"] == 3
    assert "2 of them from a worked quote" in row["why"]
    assert "not* a coverage" in row["why"]


def test_the_arrival_rate_is_measured_over_the_span_the_quotes_cover(
        session, census_module):
    """§5.25's volume condition. Reported as a rate rather than a total, because
    290 quotes over five years and over five months are different books."""
    for i in range(60):
        session.add(models.QuoteDoc(
            organization_id=ORG, external_ref=f"q-{i}", customer_ref="Acme",
            date=date(2026, 5, 1) + timedelta(days=i // 2),
            source_status="sent", outcome="UNRECORDED"))
    session.commit()

    row = _by_technique(census_module.triggers(
        session, ORG, _empty_census()))["§5.25"]

    assert row["status"] == census_module.NOT_YET
    assert row["measured"] > 0
    assert row["needs"] == census_module.RANDOMISED_POLICY_MIN_QUOTES_PER_MONTH
    # Volume alone would not reopen it, and the reason travels with the number.
    assert "does not leak" in row["why"]
