"""The financial integrity of the value ledger, pinned as properties.

This is the file that stands between the "What PIE Changed" screen and a number
nobody can defend. It is deliberately written in the shape CLAUDE.md §1 asks
for — sweeps and properties rather than field-level assertions — because that
document records exactly what field-level assertions are worth here: *every*
one of them passed while ``quote-intelligence/assess`` gave up cost. A test that
names the fields it knows about cannot fail on the field somebody adds next
week, and the field somebody adds next week is the whole risk.

So the four things asserted here are asserted **over the surface**, not over a
list:

* a salesperson is swept against every route the router declares and every key
  the response nests, so a fourth route or a fourth money field is covered the
  day it lands rather than the day somebody remembers this file;
* a missing operand is checked by the *absence of a row*, never by a row whose
  amount happens to be zero — the two are indistinguishable to a total, and one
  of them is a lie;
* an empty window and a measured zero are asserted to be **distinguishable**,
  which is the "absence of evidence is not a pass" rule stated as the only thing
  a caller can actually act on;
* the tells §1 names by sight — ``sum(… or 0)`` over nullable rows — are checked
  by parsing the module rather than by grepping it, in the style of the layer
  boundary test next door.
"""
from __future__ import annotations

import ast
import pathlib
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import clock, entitlements
import dbsupport
from app.attribution import detectors as det
from app.attribution import evaluator as ev
from app.attribution import ledger as led
from app.attribution.calculator import roi
from app.commercial.quote_exceptions import BELOW_MARGIN_FLOOR
from app.commercial.references import MARGIN_FLOOR_PRICE
from app.config import settings
from app.db import get_session
from app.domain import models
from app.domain.enums import PlanTier, ValueClass, ValueEventType
from app.passwords import hash_password
from app.routers import attribution as attribution_router
from app.routers import platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"

OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

#: Distinctive five-figure amounts, one per class. They are primes with no
#: leading or trailing zeros so that finding their digits anywhere in a response
#: body means the response really carried *that* figure, rather than colliding
#: with an id, a page size or a timestamp.
ATTRIBUTED_AMOUNT = Decimal("87191.0000")
POTENTIAL_AMOUNT = Decimal("43649.0000")
ESTIMATED_AMOUNT = Decimal("21529.0000")
REALIZED_AMOUNT = Decimal("15013.0000")

#: Every rupee figure this suite puts in the ledger, as the digits a leak would
#: have to print. Checked against the *raw text* of a salesperson's response, so
#: a new field carrying an amount is caught whatever it is called.
SEEDED_DIGITS = ("87191", "43649", "21529", "15013")

#: A key whose name answers a money question. Matched against every key at every
#: depth of a response, so a nested ``{"headline": {"amount": …}}`` is not a way
#: past this. Broad on purpose: a false positive here is a five-second
#: conversation, a false negative is the ``filterCounts.MFLOOR`` incident again.
MONEY_KEY = re.compile(
    r"amount|value|price|cost|margin|profit|revenue|roi|saving|rupee|inr"
    r"|currency|total|attributed|potential|realized|estimated",
    re.IGNORECASE)

TRIAL_STARTED_DAYS_AGO = 10
OCCURRED_DAYS_AGO = 3
THRESHOLDS_VERSION = "ci_testv1"


# ── shared seeding ───────────────────────────────────────────────────────────
def _occurred(days_ago: int = OCCURRED_DAYS_AGO) -> datetime:
    return clock.now() - timedelta(days=days_ago)


def _start_trial(s) -> models.IntelligenceTrial:
    """A live trial window wide enough to contain everything seeded below.

    Written directly rather than through ``entitlements.begin_trial`` because
    that starts the window at *now*, and every event this file records happened
    a few days ago — a window opening after its own evidence would make these
    tests pass for the wrong reason.
    """
    now = clock.now()
    row = models.IntelligenceTrial(
        organization_id=ORG, zoho_organization_id="60001111",
        started_at=now - timedelta(days=TRIAL_STARTED_DAYS_AGO),
        ends_at=now + timedelta(days=20))
    s.add(row)
    s.flush()
    return row


def _draft(value_class: ValueClass, amount: Decimal | None, *,
           event_type: ValueEventType = ValueEventType.MARGIN_PROTECTED,
           identity: tuple[str, ...] = ("q1", "L1"),
           evidence_refs: list[dict[str, Any]] | None = None,
           thresholds_version: str = THRESHOLDS_VERSION,
           occurred_at: datetime | None = None) -> led.ValueEventDraft:
    """One writable event. The identity defaults to the *same* quote line for
    every class, which is the case class-summing would get wrong."""
    return led.ValueEventDraft(
        event_type=event_type,
        value_class=value_class,
        event_key=led.event_key(ORG, event_type, value_class, identity),
        amount=amount,
        basis={"formula": "(floor_price - quoted_unit_price) x quantity",
               "floor_price": "1100", "quoted_unit_price": "800",
               "quantity": "10"},
        evidence_refs=(evidence_refs if evidence_refs is not None
                       else [{"record_type": "quote_decision", "record_id": "qd1",
                              "quote_id": identity[0]}]),
        occurred_at=occurred_at or _occurred(),
        thresholds_version=thresholds_version)


def _seed_ledger(s) -> None:
    """One quote line, recorded under all four classes with distinct amounts.

    The overlap is the point: POTENTIAL and ATTRIBUTED here are two statements
    about the same line, so any total that adds them double counts it.
    """
    led.record(s, ORG, _draft(ValueClass.ATTRIBUTED, ATTRIBUTED_AMOUNT))
    led.record(s, ORG, _draft(ValueClass.POTENTIAL, POTENTIAL_AMOUNT))
    led.record(s, ORG, _draft(ValueClass.ESTIMATED, ESTIMATED_AMOUNT))
    led.record(s, ORG, _draft(ValueClass.REALIZED, REALIZED_AMOUNT))


def _priced_line(s, *, quote_id: str, line_id: str = "L1",
                 price: str | None = "800", cost: str | None = "1000",
                 qty: str = "10", floor: str | None = "1100",
                 flagged: bool = True, overridden: bool = False,
                 created_days_ago: int = 5) -> models.QuoteDecision:
    """One priced snapshot, flagged below its floor unless told otherwise.

    ``cost=None`` is the case this file exists to pin: a line the platform
    priced but for which no purchase cost was on record.
    """
    created = clock.now() - timedelta(days=created_days_ago)
    row = models.QuoteDecision(
        organization_id=ORG, quote_id=quote_id, quote_line_id=line_id,
        customer_id="c1", product_id="p1",
        quantity=Decimal(qty), quantity_band="1-10",
        quoted_unit_price=Decimal(price) if price is not None else None,
        unit_cost=Decimal(cost) if cost is not None else None,
        references=([{"code": MARGIN_FLOOR_PRICE, "value": floor}]
                    if floor is not None else []),
        exceptions=([{"code": BELOW_MARGIN_FLOOR,
                      "reference_code": MARGIN_FLOOR_PRICE}] if flagged else []),
        overridden=overridden,
        as_of=created.date(), created_at=created,
        thresholds_version=THRESHOLDS_VERSION)
    s.add(row)
    s.flush()
    return row


def _protected_line(s, *, quote_id: str, line_id: str = "L1",
                    flagged_price: str = "800", final_price: str = "1200",
                    cost: str | None = "600", qty: str = "10",
                    floor: str | None = "1100",
                    overridden: bool = False,
                    created_days_ago: int = 5) -> models.QuoteDecision:
    """A line that was flagged below its floor **and then actually repriced**.

    The case a ``MARGIN_PROTECTED`` event is allowed to exist for, and the one
    ``_priced_line`` alone is not. A single below-floor snapshot means the price
    was flagged; it says nothing about what went out. An audit found the
    detector claiming the full shortfall on lines that shipped unchanged and
    lost money, so a fixture that seeds one snapshot and expects an event is
    encoding the defect rather than testing the feature.

    Returns the *final* snapshot, because that is the one carrying the price
    that was actually sent.
    """
    _priced_line(s, quote_id=quote_id, line_id=line_id, price=flagged_price,
                 cost=cost, qty=qty, floor=floor, flagged=True,
                 overridden=overridden, created_days_ago=created_days_ago)
    return _priced_line(s, quote_id=quote_id, line_id=line_id,
                        price=final_price, cost=cost, qty=qty, floor=floor,
                        flagged=False, overridden=overridden,
                        created_days_ago=max(0, created_days_ago - 1))


def _run_and_record(s) -> int:
    """The whole pipeline — detect, then write. Returns events created."""
    results = det.run_all(s, ORG)
    created = 0
    for result in results.values():
        _, n = led.record_all(s, ORG, result.events)
        created += n
    return created


def _ledger_rows(s) -> list[models.ValueEvent]:
    return list(s.scalars(select(models.ValueEvent).where(
        models.ValueEvent.organization_id == ORG)))


# ── fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def trial(session):
    """An organization with a live trial, for the module-level rollups."""
    session.add(models.Organization(organization_id=ORG, name="PIE"))
    # Flush the parent before the trial row references it: without mapped
    # relationship()s the unit of work does not order inserts across these
    # tables, and both backends now enforce the foreign key at flush time.
    session.flush()
    return _start_trial(session)


@pytest.fixture()
def client():
    """The real router behind the real plan gate, in the ``main.py`` shape.

    The ledger is seeded before the app is built so that the sweep below has
    something to find: a leak test against an empty surface passes for the one
    reason that proves nothing.
    """
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    _start_trial(s)
    _seed_ledger(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    # Mirrors main.py: this surface is *not* gated at inclusion. The plan rule is
    # per route (an organization reads up to the window it was entitled to) and
    # the roles are enforced per route inside it.
    app.include_router(attribution_router.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── walking a response, however it is nested ─────────────────────────────────
def _keys(node: Any) -> Iterator[str]:
    """Every key at every depth. A money field hidden one level down is still
    a money field, and naming the top-level ones is how MFLOOR survived."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _keys(value)


def _routes() -> list[APIRoute]:
    """Every route the attribution router declares, read off the router.

    Enumerated rather than listed, so a fourth endpoint is swept the day it is
    added rather than the day somebody remembers this test exists.
    """
    return [r for r in attribution_router.router.routes if isinstance(r, APIRoute)]


def _url(route: APIRoute) -> str:
    return re.sub(r"\{[^}]+\}", "1", route.path)


# ── 1. the sweep ─────────────────────────────────────────────────────────────
def test_a_salesperson_cannot_recover_a_rupee_figure_from_any_attribution_route(client):
    """The register of ``test_a_salesperson_cannot_walk_the_price_to_recover_cost``.

    Attributed value on a quote line *is* gross-profit arithmetic, and the event
    type is itself the below-floor flag — a ``MARGIN_PROTECTED`` row names a line
    priced under the floor whether or not its amount travels with it. So the
    control here is not redaction, it is a closed door, and this asserts the door
    over the *whole* surface: every route the router declares, every key at every
    depth of what comes back, and the raw text checked for the digits of every
    figure in the ledger.

    Three independent things would each fail it: a new route added without a
    role gate, a new money-shaped field appearing in a body a salesperson can
    read, and an amount reaching them under a name nobody thought to check.
    """
    sales, manager, owner = (_hdr(client, e) for e in (SALES, MANAGER, OWNER))
    routes = _routes()
    assert routes, "the router declares no routes — this sweep would be vacuous"

    money_keys_seen = 0
    for route in routes:
        url = _url(route)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            # The management copy first, so the sweep is known to be looking at
            # a surface that really does carry money.
            for reader in (manager, owner):
                priv = client.request(method, url, headers=reader)
                if priv.status_code == 200:
                    money_keys_seen += sum(
                        1 for k in _keys(priv.json()) if MONEY_KEY.search(k))
                    break
            else:
                pytest.fail(f"{method} {url} answered no privileged role; the "
                            "sweep below would pass against a broken route")

            # And now the salesperson, on the same route, including the query
            # parameters the privileged caller may pass.
            for params in ({}, {"pie_cost": "1", "limit": "500",
                                "value_class": ValueClass.ATTRIBUTED.value}):
                r = client.request(method, url, headers=sales, params=params)
                assert r.status_code == 403, (
                    f"{method} {url} {params} answered {r.status_code} to a "
                    "salesperson. Every route on this surface is manager-or-owner: "
                    "there is no salesperson-safe projection of a ledger whose "
                    "every row is gross-profit arithmetic.")

                offenders = [k for k in _keys(r.json()) if MONEY_KEY.search(k)]
                assert offenders == [], (
                    f"{method} {url} hands a salesperson money-shaped fields "
                    f"{offenders}. Absent from the response, not hidden in the "
                    "browser.")
                leaked = [d for d in SEEDED_DIGITS if d in r.text]
                assert leaked == [], (
                    f"{method} {url} printed ledger figures {leaked} to a "
                    "salesperson.")

    assert money_keys_seen, (
        "no money-shaped key was found on any privileged response, so the "
        "sweep proved nothing. Seed the ledger before asserting the leak.")


# ── 2. a missing cost produces no row, not a zero one ────────────────────────
def test_a_line_with_no_cost_on_record_produces_no_event_at_all(session, trial):
    """Not an event worth ₹0 — no event.

    The distinction is the whole of "absence of evidence is not a pass". A
    zero-amount row is an *amount*: it sums, it counts, and it tells a rollup
    that this line was measured and found to be worth nothing. What actually
    happened is that nobody knows what it was worth.

    The costed twin is seeded alongside deliberately. Without it an empty ledger
    would also be produced by seeding that no detector recognises, and the test
    would pass while asserting nothing.
    """
    _priced_line(session, quote_id="q_nocost", cost=None)
    results = det.run_all(session, ORG)
    _run_and_record(session)

    assert _ledger_rows(session) == [], (
        "an uncosted line produced a ledger row. A line with unit_cost IS NULL "
        "must produce no event; a zero-amount one would read as measured.")

    margin = results[ValueEventType.MARGIN_PROTECTED]
    assert margin.considered == 1, "the flagged line was not even looked at"
    assert margin.skip_counts() == {det.NO_COST_ON_RECORD: 1}, (
        "the uncosted line was dropped without naming why")

    # And a costed line that was genuinely protected does produce one — so the
    # absence above is caused by the missing cost and not by seeding the
    # detector cannot read.
    _protected_line(session, quote_id="q_costed")
    assert _run_and_record(session) == 1
    [row] = _ledger_rows(session)
    assert row.amount is not None and Decimal(str(row.amount)) > 0


# ── 3. the same fact twice is one row ────────────────────────────────────────
def test_recording_the_same_fact_twice_yields_exactly_one_row(session, trial):
    """Structural, not disciplinary — the guard is a unique constraint.

    Detectors must be free to re-run after every sync, because a quote won today
    reclassifies a line flagged last week. That is only safe if re-recording is
    a no-op, so this asserts the second call returns the row it already wrote
    rather than a second claim about the same rupees.
    """
    draft = _draft(ValueClass.ATTRIBUTED, ATTRIBUTED_AMOUNT)

    first, created_first = led.record(session, ORG, draft)
    second, created_second = led.record(session, ORG, draft)

    assert created_first is True
    assert created_second is False
    assert second.value_event_id == first.value_event_id
    assert session.scalar(select(func.count()).select_from(models.ValueEvent)) == 1

    # The same through the pipeline: two detection runs over one line, one row.
    _protected_line(session, quote_id="q_rerun")
    assert _run_and_record(session) == 1
    assert _run_and_record(session) == 0
    assert len(_ledger_rows(session)) == 2  # the hand-written one, plus this


# ── 4. the headline is ATTRIBUTED alone ──────────────────────────────────────
def test_the_headline_never_includes_potential_or_estimated(session, trial):
    """The four classes describe the *same line*, so summing them double counts.

    Asserted two ways because one of them alone is weak. Equality against the
    ATTRIBUTED figure would still pass if a class carried zero; the strict
    inequality against the sum of everything is what fails the day POTENTIAL is
    quietly folded in to make a renewal look better.
    """
    _seed_ledger(session)
    progress = ev.value_summary(session, ORG)

    assert progress["attributed_value"] == ATTRIBUTED_AMOUNT
    assert progress["attributed_events"] == 1

    every_class = (ATTRIBUTED_AMOUNT + POTENTIAL_AMOUNT
                   + ESTIMATED_AMOUNT + REALIZED_AMOUNT)
    assert progress["attributed_value"] < every_class, (
        "the headline is as large as every class added together, which means "
        "something other than ATTRIBUTED is in it")

    # Reported beside it, under their own names, never inside it.
    assert progress["potential_value"] == POTENTIAL_AMOUNT
    assert progress["realized_value"] == REALIZED_AMOUNT
    assert progress["class_totals_are_not_summable"]

    # ESTIMATED is seeded above and deliberately does *not* surface: no detector
    # produces that class, so a field carrying it would invite a tile reading
    # "Estimated — none recorded", which states a measurement nobody attempted.
    # The row still exists in the ledger and still stays out of the headline,
    # which is what the `every_class` assertion above is checking.
    assert "estimated_value" not in progress
    assert "estimated_events" not in progress


def test_the_headline_stays_attributed_only_over_http(client):
    body = client.get("/api/attribution/summary",
                      headers=_hdr(client, MANAGER)).json()
    # Money crosses the wire as a JSON string, never a float — a rupee figure
    # that round-trips through binary floating point is not the figure stored.
    assert isinstance(body["attributed_value"], str)
    assert Decimal(body["attributed_value"]) == ATTRIBUTED_AMOUNT
    assert Decimal(body["potential_value"]) == POTENTIAL_AMOUNT


# ── 5. an empty window and a measured zero are different facts ───────────────
def test_an_empty_window_and_a_measured_zero_stay_distinguishable(session, trial):
    """The rule §1 states three incidents of, reduced to the one thing a caller
    can act on: these two states must not render the same.

    Nothing in the ledger means no detection run is on record — UNKNOWN, and the
    screen should say so. Events present with none ATTRIBUTED is a real measured
    zero, honest and reportable, and deliberately not topped up from POTENTIAL.
    Collapsing them would turn "we have not looked" into "we looked and it was
    worth nothing".
    """
    empty = ev.value_summary(session, ORG)
    assert empty["attributed_value"] is None
    assert ev.NO_EVENTS_RECORDED in {g["reason"] for g in empty["evidence_gaps"]}

    # Now the window holds evidence — just none of it ATTRIBUTED.
    led.record(session, ORG, _draft(ValueClass.POTENTIAL, POTENTIAL_AMOUNT))
    measured = ev.value_summary(session, ORG)

    assert measured["attributed_value"] is not None
    assert measured["attributed_value"] == Decimal("0")
    assert ev.NO_EVENTS_RECORDED not in {g["reason"] for g in measured["evidence_gaps"]}

    # The property, rather than the two readings: whatever the fields are
    # called, the two states must not be the same document.
    assert empty["attributed_value"] != measured["attributed_value"]
    assert not measured["attributed_value"] > 0


def test_the_two_empty_states_read_differently_over_http(client):
    """And they survive serialization: ``null`` is not ``"0"``."""
    manager = _hdr(client, MANAGER)

    s = client.Maker()
    s.query(models.ValueEvent).delete()
    s.commit()
    s.close()
    empty = client.get("/api/attribution/summary", headers=manager).json()
    assert empty["attributed_value"] is None
    assert "not a measured zero" in (empty["empty_reason"] or "")

    s = client.Maker()
    led.record(s, ORG, _draft(ValueClass.POTENTIAL, POTENTIAL_AMOUNT))
    s.commit()
    s.close()
    measured = client.get("/api/attribution/summary", headers=manager).json()
    assert measured["attributed_value"] is not None
    assert Decimal(measured["attributed_value"]) == 0
    assert measured["empty_reason"] is None


# ── 6. no event without a version and evidence ───────────────────────────────
def test_every_persisted_event_names_its_policy_and_its_evidence(session, trial):
    """A rupee figure that cannot say which policy judged it, or which rows it
    was computed over, is an assertion. The ledger's whole value is that it
    holds none, so this is checked as a property of *every* row rather than of
    the rows this test happened to write."""
    _protected_line(session, quote_id="q_a")
    _protected_line(session, quote_id="q_b", line_id="L2")
    _seed_ledger(session)
    _run_and_record(session)

    rows = _ledger_rows(session)
    assert rows, "nothing was recorded, so this property held vacuously"
    for row in rows:
        assert (row.thresholds_version or "").strip(), (
            f"{row.event_type} was persisted with no thresholds_version")
        assert row.evidence_refs, (
            f"{row.event_type} was persisted naming no evidence")


@pytest.mark.parametrize("broken,why", [
    ({"evidence_refs": []}, "no evidence"),
    ({"thresholds_version": ""}, "no thresholds_version"),
    ({"thresholds_version": "   "}, "a blank thresholds_version"),
])
def test_the_ledger_refuses_an_event_that_cannot_defend_itself(
        session, trial, broken, why):
    """Refused, not accepted quietly and cleaned up later.

    The refusal has to happen at the writer, because the writer is the only
    place every future detector passes through. Nothing may be left behind
    either — a half-written refusal is a row nobody knows is there.
    """
    draft = _draft(ValueClass.ATTRIBUTED, ATTRIBUTED_AMOUNT, **broken)
    with pytest.raises(led.LedgerRefusal):
        led.record(session, ORG, draft)
    assert _ledger_rows(session) == [], f"a draft with {why} left a row behind"


# ── 7. ROI is UNKNOWN, never 0x ──────────────────────────────────────────────
@pytest.mark.parametrize("cost", [None, Decimal("0"), Decimal("0.0000"),
                                  Decimal("-1")])
def test_roi_is_unknown_rather_than_zero_when_the_cost_is_not_known(cost):
    """A ratio against a cost nobody supplied is fabricated however carefully it
    was divided, and a zero cost does not make the return infinite — it means
    nobody has told this platform what it costs."""
    assert roi(ATTRIBUTED_AMOUNT, cost) is None


def test_the_report_says_unknown_rather_than_dividing_by_nothing(session, trial):
    _seed_ledger(session)

    for cost in (None, Decimal("0")):
        report = ev.thirty_day_report(session, ORG, pie_cost=cost)
        assert report["roi"] is None, f"pie_cost={cost} produced a ratio"
        assert report["roi_is_unknown"] is True
        assert any(g["subject"] == "roi" for g in report["evidence_gaps"])

    priced = ev.thirty_day_report(session, ORG, pie_cost=Decimal("10000"))
    assert priced["roi"] is not None and priced["roi_is_unknown"] is False


def test_the_evaluation_endpoint_is_owner_only_and_answers_unknown(client):
    owner = _hdr(client, OWNER)
    assert client.get("/api/attribution/evaluation",
                      headers=_hdr(client, MANAGER)).status_code == 403

    for params in ({}, {"pie_cost": "0"}):
        body = client.get("/api/attribution/evaluation",
                          headers=owner, params=params).json()
        assert body["roi"] is None, f"{params} produced a ratio from nothing"
        assert body["roi_is_unknown"] is True


# ── 8. what cannot be measured is named, never counted as nothing ────────────
def test_every_unmeasurable_event_type_is_a_named_gap_not_a_measured_zero(
        session, trial):
    """Three event types produce nothing today, for two different reasons, and
    the report has to say so rather than leaving the reader to read silence as
    ₹0.

    ``LOST_SALE_RECOVERED`` and ``PROCUREMENT_OPPORTUNITY`` have no detector —
    nothing links a lost quote to a later recovered order, or a recommendation
    to a purchase order placed against it. ``EQUIVALENT_SAVING`` has a detector
    and tested arithmetic, but nothing in this codebase records an accepted
    substitution for it to read. Either way the honest sentence is "not
    measurable", and an event type that is simply absent from the breakdown is
    a sentence the screen never prints.

    Written over ``ValueEventType`` rather than over a list of three, so a sixth
    type added without evidence behind it fails here instead of appearing as a
    silent nothing.
    """
    _protected_line(session, quote_id="q_flag")
    _run_and_record(session)

    progress = ev.value_summary(session, ORG)
    measured = {row["event_type"] for row in progress["by_event_type"]}
    named = {gap["subject"] for gap in progress["evidence_gaps"]}

    assert ValueEventType.MARGIN_PROTECTED.value in measured, (
        "the seeded evidence produced no measured type, so the gaps below "
        "would be trivially complete")

    for event_type in ValueEventType:
        if event_type.value in measured:
            continue
        assert event_type.value in named, (
            f"{event_type.value} produced nothing and was not named as a gap. "
            "Silence reads as a measured zero, which is the one thing it is not.")

    # And specifically the three that cannot be measured today, none of which
    # may appear in the breakdown carrying a figure.
    unmeasurable = {ValueEventType.LOST_SALE_RECOVERED,
                    ValueEventType.PROCUREMENT_OPPORTUNITY,
                    ValueEventType.EQUIVALENT_SAVING}
    gaps = {gap["subject"]: gap for gap in progress["evidence_gaps"]}
    for event_type in unmeasurable:
        assert event_type.value not in measured
        gap = gaps[event_type.value]
        assert gap["reason"] == ev.NOT_MEASURABLE
        assert gap["detail"].strip(), f"{event_type.value} named with no reason"

    rows = {row["event_type"]: row for row in progress["by_event_type"]}
    for event_type in unmeasurable:
        assert event_type.value not in rows, (
            f"{event_type.value} appears in the breakdown; it has no evidence, "
            "so any figure beside it — zero included — is invented")


def test_the_named_gaps_survive_to_the_screen(client):
    body = client.get("/api/attribution/summary",
                      headers=_hdr(client, MANAGER)).json()
    named = {gap["subject"] for gap in body["evidence_gaps"]}
    for event_type in (ValueEventType.LOST_SALE_RECOVERED,
                       ValueEventType.PROCUREMENT_OPPORTUNITY,
                       ValueEventType.EQUIVALENT_SAVING):
        assert event_type.value in named


# ── the window the summary measures ──────────────────────────────────────────
# The summary used to be the trial window and only ever the trial window, while
# the screen used it as the general "what PIE changed" figure. So a customer's
# headline froze on the day their trial ended: value attributed months later
# fell outside the window and the screen reported the "no detection run is on
# record" gap — not a measured zero — while the ledger held the events. These
# pin the window to the question being asked rather than to the trial.
def _org_with_a_recent_event(s, org: str, *, plan: str, trial: bool,
                             amount: Decimal = Decimal("50000.0000")):
    """An organization whose only value event is from *yesterday*.

    Yesterday is the point: it is comfortably outside any trial that ended
    months ago, so a summary still pinned to the trial cannot see it and the
    assertion fails for the reason it names.
    """
    s.add(models.Organization(organization_id=org, name=org, plan=plan))
    s.flush()
    now = clock.now()
    if trial:
        ended = now - timedelta(days=90)
        s.add(models.IntelligenceTrial(
            organization_id=org, zoho_organization_id=f"z{org}",
            started_at=ended - timedelta(days=30), ends_at=ended))
        s.flush()
    led.record(s, org, led.ValueEventDraft(
        event_type=ValueEventType.MARGIN_PROTECTED,
        value_class=ValueClass.ATTRIBUTED,
        event_key=led.event_key(org, ValueEventType.MARGIN_PROTECTED,
                                ValueClass.ATTRIBUTED, ("q_recent", "L1")),
        amount=amount, basis={"formula": "recent"},
        evidence_refs=[{"record_type": "quote_decision", "record_id": "qd_recent",
                        "quote_id": "q_recent"}],
        occurred_at=now - timedelta(days=1),
        thresholds_version=THRESHOLDS_VERSION))
    s.flush()


def test_a_paying_customers_headline_advances_past_their_trial(session):
    """The defect this rename exists to fix.

    An organization on the intelligence plan whose trial ended ninety days ago,
    with value attributed yesterday. Pinned to the trial this reported ``None``
    and "no detection run has been recorded" — the platform telling a paying
    customer it had found nothing, while the ledger held ₹50,000.
    """
    _org_with_a_recent_event(session, "org_paying",
                             plan=PlanTier.INTELLIGENCE.value, trial=True)
    result = ev.value_summary(session, "org_paying")

    assert result["attributed_value"] == Decimal("50000.0000")
    assert result["window"]["basis"] == ev.WINDOW_RECENT
    reasons = {g["reason"] for g in result["evidence_gaps"]}
    assert ev.NO_EVENTS_RECORDED not in reasons


def test_an_organization_that_never_had_a_trial_is_measured_not_refused(session):
    """A tenant an operator provisioned has no trial row and never will.

    It used to get "connect a Zoho company to start one", which is a dead end
    for a book that has been connected for a year.
    """
    _org_with_a_recent_event(session, "org_no_trial",
                             plan=PlanTier.PLATFORM.value, trial=False)
    result = ev.value_summary(session, "org_no_trial")

    assert result["trial"] is None
    assert result["attributed_value"] == Decimal("50000.0000")
    reasons = {g["reason"] for g in result["evidence_gaps"]}
    assert ev.NO_TRIAL_ON_RECORD not in reasons


def test_a_lapsed_plan_keeps_the_trial_window_rather_than_a_trailing_one(session):
    """Where the two rules meet.

    A trailing window capped by entitlement would be capped to nothing and
    report an emptiness that is an entitlement, not a fact about the business.
    The window a lapsed organization may see is the one it is shown.
    """
    _org_with_a_recent_event(session, "org_lapsed_w",
                             plan=PlanTier.FREE.value, trial=True)
    trial = ev.current_trial(session, "org_lapsed_w")
    result = ev.value_summary(session, "org_lapsed_w",
                              readable_until=clock.aware(trial.ends_at))

    assert result["window"]["basis"] == ev.WINDOW_TRIAL
    assert result["window"]["frozen_at"] is not None
    assert result["attributed_value"] is None, \
        "yesterday's event is past what this plan entitles them to read"


def test_the_evaluation_report_stays_pinned_to_the_trial(session):
    """The summary moved; the before-and-after must not follow it.

    A report that measured a trailing window would compare the pre-trial
    baseline against a period the trial had nothing to do with.
    """
    _org_with_a_recent_event(session, "org_report",
                             plan=PlanTier.INTELLIGENCE.value, trial=True)
    report = ev.thirty_day_report(session, "org_report")

    assert report["window"]["basis"] == ev.WINDOW_TRIAL
    assert report["attributed_value"] is None, \
        "an event from yesterday is outside a trial that ended 90 days ago"


def test_the_summary_says_which_window_it_measured(session):
    """Carried, never implied.

    Two figures over different periods look identical on a screen, and the one
    that froze looked exactly like the one that was live — which is why the
    defect survived as long as it did.
    """
    _org_with_a_recent_event(session, "org_window",
                             plan=PlanTier.INTELLIGENCE.value, trial=False)
    window = ev.value_summary(session, "org_window")["window"]

    assert window["basis"] in (ev.WINDOW_TRIAL, ev.WINDOW_RECENT)
    assert window["start"] and window["end"] and window["label"]
    assert window["days"] == ev.SUMMARY_DAYS


# ── the window a lapsed plan keeps ───────────────────────────────────────────
# The screen a renewal is argued from used to go dark on the day the trial
# ended: `/attribution` was gated at `include_router` with the other
# intelligence surfaces, so the organization dropped to free and the owner
# deciding whether to pay could no longer read what had been done for them —
# while `jobs.py` went on writing the evidence on every sync. These pin the rule
# that replaced it: an organization always keeps the window it was entitled to,
# and rolling detection past that moment is what the plan buys.
LAPSED_ORG = "org_lapsed"
LAPSED_OWNER = "owner@lapsed.example"


def _lapsed_client(*, with_trial: bool):
    """An organization on the free plan whose trial has already finished.

    Events are seeded on both sides of ``ends_at`` so the cap has something to
    exclude — a window test against a ledger that stops before the boundary
    passes for the one reason that proves nothing.
    """
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.add(models.Organization(organization_id=LAPSED_ORG, name="Lapsed",
                              plan=PlanTier.FREE.value))
    s.flush()
    s.add(models.User(user_id="usr_lapsed", organization_id=LAPSED_ORG,
                      email=LAPSED_OWNER, name="Lapsed Owner", role="OWNER",
                      password_hash=hash_password(SEED_PASSWORD), active=True))
    now = clock.now()
    ended = now - timedelta(days=5)
    if with_trial:
        s.add(models.IntelligenceTrial(
            organization_id=LAPSED_ORG, zoho_organization_id="60002222",
            started_at=ended - timedelta(days=30), ends_at=ended))
    s.flush()
    # One inside the window, one after it. Distinct amounts so the assertion
    # names which row it found rather than counting.
    led.record(s, LAPSED_ORG, led.ValueEventDraft(
        event_type=ValueEventType.MARGIN_PROTECTED,
        value_class=ValueClass.ATTRIBUTED,
        event_key=led.event_key(LAPSED_ORG, ValueEventType.MARGIN_PROTECTED,
                                ValueClass.ATTRIBUTED, ("q_in", "L1")),
        amount=Decimal("1000.0000"),
        basis={"formula": "in-window"},
        evidence_refs=[{"record_type": "quote_decision", "record_id": "qd_in",
                        "quote_id": "q_in"}],
        occurred_at=ended - timedelta(days=2),
        thresholds_version=THRESHOLDS_VERSION))
    led.record(s, LAPSED_ORG, led.ValueEventDraft(
        event_type=ValueEventType.MARGIN_PROTECTED,
        value_class=ValueClass.ATTRIBUTED,
        event_key=led.event_key(LAPSED_ORG, ValueEventType.MARGIN_PROTECTED,
                                ValueClass.ATTRIBUTED, ("q_after", "L1")),
        amount=Decimal("2000.0000"),
        basis={"formula": "after-window"},
        evidence_refs=[{"record_type": "quote_decision", "record_id": "qd_after",
                        "quote_id": "q_after"}],
        occurred_at=now - timedelta(days=1),
        thresholds_version=THRESHOLDS_VERSION))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(attribution_router.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def test_an_expired_trial_can_still_read_what_pie_was_worth(monkeypatch):
    """The renewal argument survives the trial it argues about."""
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "free")
    tc = _lapsed_client(with_trial=True)
    hdr = _hdr(tc, LAPSED_OWNER)

    # The whole surface answers, rather than 403-ing the way it used to.
    assert tc.get("/api/attribution/summary", headers=hdr).status_code == 200
    assert tc.get("/api/attribution/evaluation", headers=hdr).status_code == 200

    body = tc.get("/api/attribution/events", headers=hdr).json()
    quotes = {ref["quote_id"] for row in body["events"]
              for ref in row["evidence_refs"]}
    assert "q_in" in quotes, "the trial window's own evidence must stay readable"


def test_the_ledger_stops_at_the_end_of_the_window_it_was_entitled_to(monkeypatch):
    """And the cap bounds the count, not merely the page.

    A ``total`` counted past a bound the reader cannot page to is a number that
    cannot be opened, which is the one thing this surface exists to avoid.
    """
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "free")
    tc = _lapsed_client(with_trial=True)
    body = tc.get("/api/attribution/events",
                  headers=_hdr(tc, LAPSED_OWNER)).json()

    quotes = {ref["quote_id"] for row in body["events"]
              for ref in row["evidence_refs"]}
    assert "q_after" not in quotes, "detection after the trial is what the plan buys"
    assert body["total"] == 1, "the cap must bound the total, not just the page"
    assert body["readable_until"] is not None
    assert body["frozen_reason"], "a frozen view has to say it is frozen"


def test_a_paid_plan_reads_the_ledger_unbounded(monkeypatch):
    """The cap is a consequence of the plan lapsing, not a property of the route."""
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "free")
    tc = _lapsed_client(with_trial=True)

    s = tc.app.dependency_overrides[get_session]
    session = next(s())
    entitlements.set_plan(session, LAPSED_ORG, PlanTier.INTELLIGENCE)
    session.commit()
    session.close()

    body = tc.get("/api/attribution/events",
                  headers=_hdr(tc, LAPSED_OWNER)).json()
    quotes = {ref["quote_id"] for row in body["events"]
              for ref in row["evidence_refs"]}
    assert {"q_in", "q_after"} <= quotes
    assert body["readable_until"] is None
    assert body["frozen_reason"] is None


def test_no_trial_and_no_plan_is_refused_rather_than_answered_empty(monkeypatch):
    """An empty ledger would say "PIE did nothing for you".

    That is a benign default standing in for "you are not entitled to look" —
    the §1 failure this module is written against. The refusal names the plan.
    """
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "free")
    tc = _lapsed_client(with_trial=False)

    r = tc.get("/api/attribution/events", headers=_hdr(tc, LAPSED_OWNER))
    assert r.status_code == 403
    assert "Commercial Intelligence" in r.json()["detail"]


def test_the_window_rule_never_lets_a_salesperson_past_their_role(monkeypatch):
    """Plan and role are different rules, and loosening one must not touch the other.

    The old plan gate happened to refuse a salesperson at a free organization
    before their role ever came up. Removing it must leave the role refusal
    doing that work alone.
    """
    monkeypatch.setattr(settings, "DEFAULT_PLAN", "free")
    tc = _lapsed_client(with_trial=True)
    session = next(tc.app.dependency_overrides[get_session]())
    session.add(models.User(
        user_id="usr_lapsed_sales", organization_id=LAPSED_ORG,
        email="sales@lapsed.example", name="Lapsed Sales", role="SALESPERSON",
        password_hash=hash_password(SEED_PASSWORD), active=True))
    session.commit()
    session.close()

    hdr = _hdr(tc, "sales@lapsed.example")
    for path in ("/api/attribution/summary", "/api/attribution/events",
                 "/api/attribution/evaluation"):
        assert tc.get(path, headers=hdr).status_code == 403, path


def test_a_ledger_page_says_it_is_not_a_total(client):
    """The rows overlap by design — POTENTIAL and ATTRIBUTED describe one line —
    so the drill-down has to warn the one caller who would otherwise add it up."""
    body = client.get("/api/attribution/events",
                      headers=_hdr(client, MANAGER)).json()
    assert body["total"] == 4
    assert "double count" in body["page_is_not_a_total"]
    # Every row carries what it was computed from, or the page is not an audit.
    for row in body["events"]:
        assert row["basis"] and row["evidence_refs"]
        assert row["thresholds_version"]


# ── 9. the tells §1 names by sight, parsed rather than grepped ───────────────
_ATTRIBUTION = pathlib.Path(__file__).resolve().parents[2] / "app" / "attribution"


def _zeroish(node: ast.AST) -> bool:
    """Whether this expression is a zero standing in for a missing number."""
    if isinstance(node, ast.Constant):
        return node.value in (0, 0.0, "0") and not isinstance(node.value, bool)
    # Decimal("0") / Decimal(0) — the same default wearing the house money type.
    if isinstance(node, ast.Call) and _callee(node.func) == "Decimal":
        return bool(node.args) and _zeroish(node.args[0])
    return False


def _callee(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _sums_defaulting_to_zero(tree: ast.AST) -> list[str]:
    """Every ``sum(… or 0 …)`` and ``sum(coalesce(…, 0))`` in a module.

    Parsed rather than grepped, in the style of the layer-boundary test next
    door: a comment that mentions the idiom — and this module's docstrings
    mention it repeatedly, because it is the thing they exist to warn about —
    must not be able to fail the build.
    """
    hits: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee(node.func) == "sum"):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.BoolOp) and isinstance(inner.op, ast.Or)
                    and any(_zeroish(v) for v in inner.values[1:])):
                hits.append(f"line {inner.lineno}: sum(... or 0)")
            if (isinstance(inner, ast.Call)
                    and _callee(inner.func) in {"coalesce", "ifnull", "nvl"}
                    and any(_zeroish(a) for a in inner.args)):
                hits.append(f"line {inner.lineno}: sum(coalesce(..., 0))")
    return hits


def test_no_module_in_attribution_sums_nullable_rows_defaulting_to_zero():
    """``sum(… or 0)`` over rows that may hold ``None`` is the first tell §1
    lists, and it is the one that reads as good news: it turns "we could not
    price four of these lines" into a total that looks complete.

    ``func.sum`` skipping NULLs is correct SQL and stays — what must not happen
    is a NULL being *replaced* by a zero on the way into an aggregate, because
    from there nothing downstream can tell a measured zero from a missing one.
    """
    modules = sorted(_ATTRIBUTION.glob("*.py"))
    assert modules, "no attribution module was parsed"
    offenders = {
        str(path.name): hits
        for path in modules
        if (hits := _sums_defaulting_to_zero(ast.parse(path.read_text())))
    }
    assert offenders == {}, (
        f"a zero is standing in for a missing number in {offenders}. A missing "
        "operand is UNKNOWN — no event, or None — never a zero that sums.")


def test_the_guard_above_actually_catches_the_idiom_it_names():
    """A check that cannot fail is a check nobody should trust, and this one is
    reading for a shape rather than for a string."""
    caught = _sums_defaulting_to_zero(ast.parse(
        "total = sum(row.amount or 0 for row in rows)\n"
        "other = sum(func.coalesce(t.c.amount, 0))\n"
        'third = sum(x or Decimal("0") for x in xs)\n'))
    assert len(caught) == 3

    clean = _sums_defaulting_to_zero(ast.parse(
        "total = sum(row.amount for row in rows if row.amount is not None)\n"
        "n = int(events or 0)\n"))
    assert clean == []


# ── 10. the detector path, which is where every real defect lived ───────────
#
# An independent audit found six confirmed defects in this module, and the
# 22 tests above it passed throughout — because not one of them ran a detector
# against a won quote. They seeded `ValueEvent` rows by hand, so every fault in
# the path from evidence to draft was invisible. The mutation testing that
# "verified" them could only falsify what they already reached.
#
# These are that path. Each one is an audit finding, reproduced as its failing
# case with the number it used to produce named in the assertion.


def _won(s, quote_id: str, *, days_ago: int = 1) -> models.QuoteOutcome:
    row = models.QuoteOutcome(
        organization_id=ORG, quote_id=quote_id, status="WON",
        decided_at=clock.now() - timedelta(days=days_ago))
    s.add(row)
    s.flush()
    return row


def _attributed_total(s) -> Decimal:
    """The headline, read the way the screen reads it.

    Deliberately the evaluator's own rollup rather than a sum over the rows
    here. A helper that re-implemented the filter would have to remember to
    exclude superseded rows, and a test that forgets that is a test asserting a
    total no user ever sees — which is exactly the mistake the first draft of
    this helper made, quietly turning a corrected ₹3,000 claim into ₹6,000 by
    adding the two measurements it replaced.
    """
    total = ev.value_summary(s, ORG)["attributed_value"]
    return Decimal(0) if total is None else Decimal(str(total))


def test_a_line_that_shipped_below_its_floor_claims_nothing(session, trial):
    """F1, the worst of them: value booked for selling below the floor.

    A line flagged at ₹800 against a ₹1,000 cost and a ₹1,100 floor, never
    repriced, sent as-is and won. The detector claimed the full ₹3,000
    shortfall as "margin protected" on a sale that lost ₹2,000, because it
    valued the gap to the floor and never looked at the price that went out.

    ``overridden`` did not save it: that flag is only set when somebody types an
    override *reason*, while the ordinary below-floor path is a manager
    approval, so a shipped-anyway line looked identical to a corrected one.
    """
    _priced_line(session, quote_id="q_below", price="800", cost="1000",
                 floor="1100")
    _won(session, "q_below")

    results = det.run_all(session, ORG)
    _run_and_record(session)

    assert _ledger_rows(session) == [], (
        "a line that shipped below its floor produced a ledger row. It used to "
        "produce ₹3,000 of 'margin protected' on a sale that lost ₹2,000.")
    margin = results[ValueEventType.MARGIN_PROTECTED]
    assert margin.skip_counts() == {det.FLOOR_NOT_CLEARED: 1}, (
        "the line was dropped without naming that the floor was never cleared")


def test_a_reprice_that_still_misses_the_floor_claims_only_what_moved(session, trial):
    """The partial-reprice variant: 800 -> 1050 against a 1100 floor, won.

    This one is a *line*, not a bug, and it is worth stating because the two
    readings look alike. The line went out below policy, so nothing was
    protected relative to the floor and ``MARGIN_PROTECTED`` must claim nothing
    — under the old code it claimed the whole ₹3,000 gap, and the partial
    reprice made that ₹5,500 across the two detectors.

    But the price genuinely moved ₹250 a unit after PIE flagged it, and the
    customer bought at the higher price. That ₹2,500 is measured on both ends
    and is honestly ``DISCOUNT_LEAKAGE_PREVENTED``. Refusing it would understate
    what the platform did, which is its own kind of dishonesty.

    The invariant that keeps this from resurrecting F1: the credited amount is
    the **observed movement**, never the gap to the floor. Below-policy selling
    is a separate fact and the floor claim stays at zero.
    """
    _protected_line(session, quote_id="q_partial", flagged_price="800",
                    final_price="1050", cost="600", floor="1100")
    _won(session, "q_partial")
    results = det.run_all(session, ORG)
    _run_and_record(session)

    assert results[ValueEventType.MARGIN_PROTECTED].skip_counts() == {
        det.FLOOR_NOT_CLEARED: 1}, "a line below its floor claimed protection"

    moved = (Decimal("1050") - Decimal("800")) * Decimal("10")
    assert _attributed_total(session) == moved, (
        f"credited something other than the {moved} the price actually moved; "
        "it used to claim ₹5,500 by valuing the gap to the floor")


def test_one_price_movement_is_never_banked_by_two_event_types(session, trial):
    """F2: the flagship success path double counted.

    Flagged at ₹800, repriced to ₹1,100, ten units, won. MARGIN_PROTECTED and
    DISCOUNT_LEAKAGE_PREVENTED each wrote a row and the rollup summed both, so
    ₹3,000 of real movement was reported as ₹6,000.
    """
    _protected_line(session, quote_id="q_two", flagged_price="800",
                    final_price="1100", cost="600", floor="1100")
    _won(session, "q_two")
    _run_and_record(session)

    rows = [r for r in _ledger_rows(session)
            if r.value_class == ValueClass.ATTRIBUTED.value]
    assert len(rows) == 1, (
        f"one price movement produced {len(rows)} rows; it used to produce two "
        "and sum them to ₹6,000 against ₹3,000 of movement")
    assert _attributed_total(session) == Decimal("3000.0000")


def test_re_running_detection_does_not_inflate_the_total(session, trial):
    """F3: the ledger's own docstring promised re-runs were safe. They were not.

    Detect at 800->1000, reprice to 1200, detect again. The event key carried
    the final snapshot's id, so the second run minted a fresh key and left the
    stale partial claim standing: ₹8,000 booked for ₹4,000 of movement.
    """
    _protected_line(session, quote_id="q_rr", flagged_price="800",
                    final_price="1000", cost="600", floor="1100")
    _won(session, "q_rr")
    _run_and_record(session)
    first = _attributed_total(session)

    _priced_line(session, quote_id="q_rr", price="1200", cost="600",
                 floor="1100", flagged=False, created_days_ago=0)
    _run_and_record(session)

    live = [r for r in _ledger_rows(session)
            if r.value_class == ValueClass.ATTRIBUTED.value
            and r.superseded_at is None]
    assert len(live) == 1, "a re-run left two live claims about one line"
    assert _attributed_total(session) == Decimal("3000.0000"), (
        f"the total inflated across runs (first run {first}); it used to reach "
        "₹8,000 for ₹4,000 of real movement")

    superseded = [r for r in _ledger_rows(session) if r.superseded_at is not None]
    assert superseded, "the corrected claim was mutated rather than superseded"


def test_a_flag_recorded_after_the_win_is_never_attributed(session, trial):
    """F4: ATTRIBUTED proved observation, not influence.

    Nothing compared the timestamps. A quote won nine days before PIE ever
    priced the line was booked ATTRIBUTED, dating the business fact to before
    the evidence for it existed. Ordering must be demonstrated, not merely
    un-contradicted, so this is REALIZED — which is what that class is for.
    """
    _protected_line(session, quote_id="q_late", flagged_price="800",
                    final_price="1200", cost="600", floor="1100",
                    created_days_ago=1)
    _won(session, "q_late", days_ago=9)
    _run_and_record(session)

    rows = _ledger_rows(session)
    assert rows, "the seeding produced nothing, so this asserts nothing"
    assert all(r.value_class == ValueClass.REALIZED.value for r in rows), (
        "a flag recorded after the win was called ATTRIBUTED")
    assert _attributed_total(session) == 0


def test_a_potential_opportunity_does_not_survive_the_quote_being_lost(session, trial):
    """The stale-POTENTIAL case. Refusing to *create* one was not enough.

    The detector correctly declines a lost quote, but a row written while the
    quote was still open was never revisited — so a declined quote kept a
    permanent row on "Opportunities identified" claiming money that is gone.
    """
    _protected_line(session, quote_id="q_open", flagged_price="800",
                    final_price="1200", cost="600", floor="1100")
    _run_and_record(session)
    live = [r for r in _ledger_rows(session) if r.superseded_at is None]
    assert [r.value_class for r in live] == [ValueClass.POTENTIAL.value]

    session.add(models.QuoteOutcome(
        organization_id=ORG, quote_id="q_open", status="LOST",
        loss_reason="PRICE", decided_at=clock.now()))
    session.flush()

    results = det.run_all(session, ORG)
    drafts = [d for r in results.values() for d in r.events]
    led.record_all(session, ORG, drafts)
    led.supersede_closed_opportunities(
        session, ORG,
        [d.event_key for d in drafts if d.value_class is ValueClass.POTENTIAL])

    live = [r for r in _ledger_rows(session) if r.superseded_at is None]
    assert live == [], (
        "a lost quote left a live POTENTIAL row still claiming the opportunity")


def test_the_attributed_total_for_a_line_never_exceeds_its_real_movement(session, trial):
    """The property the auditor asked for, swept over reprice sequences.

    One line, one price movement: whatever sequence of snapshots and detection
    runs produced it, the attributed total cannot exceed what the price actually
    moved. This is the invariant F1, F2 and F3 each broke a different way, so it
    is asserted directly rather than only through their individual cases.
    """
    qty, flagged, floor = Decimal("10"), Decimal("800"), Decimal("1100")
    _protected_line(session, quote_id="q_prop", flagged_price="800",
                    final_price="900", cost="600", floor="1100")
    _won(session, "q_prop")

    for i, price in enumerate(["1000", "1150", "1120", "1300"]):
        _run_and_record(session)
        _priced_line(session, quote_id="q_prop", price=price, cost="600",
                     floor="1100", flagged=False, created_days_ago=0)
        session.flush()
        final = Decimal(price)
        ceiling = (min(final, floor) - flagged) * qty
        total = _attributed_total(session)
        assert total <= max(ceiling, Decimal(0)), (
            f"after reprice {i} to {price}, attributed {total} exceeds the "
            f"{ceiling} this line's price actually moved within its floor")


# ── 11. what the first remediation broke ────────────────────────────────────
#
# A second independent audit ran after the six findings above were fixed, and
# found that the fix itself had introduced two defects and left one of the six
# only half done. All three are here. The pattern is worth naming: each one
# lived in a path the suite did not execute, which is the same reason the
# original six survived a 2248-test gate.


def _alternative_line(s, *, quote_id: str, line_id: str = "L1",
                      original_cost: str = "1000", alternative_cost: str = "600",
                      price: str = "1500", qty: str = "10",
                      created_days_ago: int = 5) -> models.QuoteDecision:
    """A line carrying a substituted equivalent, the shape EquivalentSaving reads.

    Nothing in the application writes this ref today, which is exactly why the
    detector reading it went unexercised — and why a TypeError in it could sit
    behind a green gate.
    """
    row = _priced_line(s, quote_id=quote_id, line_id=line_id, price=price,
                       cost=original_cost, qty=qty, floor=None, flagged=False,
                       created_days_ago=created_days_ago)
    row.evidence_refs = [{
        "record_type": det.ALTERNATIVE_RECORD_TYPE,
        "record_id": "alt1",
        "original_unit_cost": original_cost,
        "alternative_unit_cost": alternative_cost,
    }]
    s.flush()
    return row


def test_a_line_with_an_alternative_does_not_crash_the_whole_run(session, trial):
    """R1: the ordering fix added a required argument and missed a caller.

    `_classify` gained `intervened_at`; two of its three call sites were
    updated. The third, in EquivalentSavingDetector, raised TypeError — and
    `jobs._run_attribution` catches `Exception`, so one line carrying an
    alternative would have stopped attribution for the entire organization on
    every sync, silently, while the screen reported that no detection run was on
    record. F5's failure mode, re-armed behind an exception handler.

    Ruff does not check call arity and no test exercised this detector, which is
    the whole reason it reached a green gate.
    """
    _alternative_line(session, quote_id="q_alt")
    _protected_line(session, quote_id="q_other", flagged_price="800",
                    final_price="1200", cost="600", floor="1100")
    _won(session, "q_other")

    results = det.run_all(session, ORG)          # must not raise
    created = _run_and_record(session)

    assert created >= 1, "the unrelated, valid event was lost with the crash"
    assert any(r.event_type == ValueEventType.MARGIN_PROTECTED.value
               for r in _ledger_rows(session)), (
        "one bad line took an unrelated organization-wide detection run with it")
    assert ValueEventType.EQUIVALENT_SAVING in results


def test_a_line_sold_below_cost_claims_nothing_from_any_detector(session, trial):
    """F1, one detector over. The fix landed on MarginProtected only.

    `_reconcile` suppresses the discount claim only when the margin detector
    produced a draft — and on exactly these lines it produces none, having
    skipped FLOOR_NOT_CLEARED. So a line flagged at ₹800 against a ₹1,000 cost,
    corrected to ₹900 and won, booked ₹1,000 of "discount recovered" on a sale
    that lost ₹1,000, with the margin detector's honest skip printed beside it.

    The suite blessed the partial-reprice case in
    `test_a_reprice_that_still_misses_the_floor_claims_only_what_moved`, but
    that fixture prices a *profitable* line. No test put the final price below
    cost, which is where the design call stops holding.
    """
    _protected_line(session, quote_id="q_loss", flagged_price="800",
                    final_price="900", cost="1000", floor="1100")
    _won(session, "q_loss")

    results = det.run_all(session, ORG)
    _run_and_record(session)

    assert _attributed_total(session) == 0, (
        "value was booked on a line that lost money on every unit; it used to "
        "claim ₹1,000 on a sale that lost ₹1,000")
    assert det.SOLD_BELOW_COST in results[
        ValueEventType.DISCOUNT_LEAKAGE_PREVENTED].skip_counts()
    assert det.FLOOR_NOT_CLEARED in results[
        ValueEventType.MARGIN_PROTECTED].skip_counts()


def test_a_class_the_system_cannot_produce_never_reaches_the_breakdown(session, trial):
    """F7 residual: withdrawing a class from the headline is not withdrawing it.

    `estimated_value` was removed from the top level, but `by_event_type` groups
    over whatever classes it finds — so a seeded ESTIMATED row travelled to the
    screen inside the breakdown with its amount intact, past the fields that had
    been deleted to keep it out.
    """
    led.record(session, ORG, _draft(ValueClass.ESTIMATED, ESTIMATED_AMOUNT))
    progress = ev.value_summary(session, ORG)

    classes = {row["value_class"] for row in progress["by_event_type"]}
    assert ValueClass.ESTIMATED.value not in classes, (
        "a class no detector can produce reached the per-type breakdown")
    assert str(ESTIMATED_AMOUNT) not in str(progress["by_event_type"])


def test_a_flag_after_the_win_is_not_dated_before_its_own_evidence(session, trial):
    """F4 residual: the class was corrected, the timestamp was not.

    A win-before-flag row is REALIZED, which is honest — but it kept
    `occurred_at` at the win date, so the row still claimed a business fact on a
    day when nothing had been observed.
    """
    _protected_line(session, quote_id="q_ts", flagged_price="800",
                    final_price="1200", cost="600", floor="1100",
                    created_days_ago=1)
    _won(session, "q_ts", days_ago=9)
    _run_and_record(session)

    [row] = _ledger_rows(session)
    assert row.value_class == ValueClass.REALIZED.value
    flagged_at = clock.now() - timedelta(days=1)
    assert clock.aware(row.occurred_at) >= flagged_at - timedelta(minutes=1), (
        "the event is dated before the evidence that produced it existed")
