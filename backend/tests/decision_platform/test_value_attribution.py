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
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import clock, entitlements
from app.attribution import detectors as det
from app.attribution import evaluator as ev
from app.attribution import ledger as led
from app.attribution.calculator import roi
from app.commercial.quote_exceptions import BELOW_MARGIN_FLOOR
from app.commercial.references import MARGIN_FLOOR_PRICE
from app.db import Base, get_session
from app.domain import models
from app.domain.enums import ValueClass, ValueEventType
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
    return _start_trial(session)


@pytest.fixture()
def client():
    """The real router behind the real plan gate, in the ``main.py`` shape.

    The ledger is seeded before the app is built so that the sweep below has
    something to find: a leak test against an empty surface passes for the one
    reason that proves nothing.
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
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
    # Mirrors main.py: the whole surface is gated at inclusion, and the roles
    # are enforced per route inside it.
    app.include_router(
        attribution_router.router,
        dependencies=[Depends(entitlements.require_feature("intelligence"))])

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

    # And the same line, costed, does produce one — so the absence above is
    # caused by the missing cost and not by seeding the detector cannot read.
    _priced_line(session, quote_id="q_costed")
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
    _priced_line(session, quote_id="q_rerun")
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
    progress = ev.trial_progress(session, ORG)

    assert progress["attributed_value"] == ATTRIBUTED_AMOUNT
    assert progress["attributed_events"] == 1

    every_class = (ATTRIBUTED_AMOUNT + POTENTIAL_AMOUNT
                   + ESTIMATED_AMOUNT + REALIZED_AMOUNT)
    assert progress["attributed_value"] < every_class, (
        "the headline is as large as every class added together, which means "
        "something other than ATTRIBUTED is in it")

    # Reported beside it, under their own names, never inside it.
    assert progress["potential_value"] == POTENTIAL_AMOUNT
    assert progress["estimated_value"] == ESTIMATED_AMOUNT
    assert progress["realized_value"] == REALIZED_AMOUNT
    assert progress["class_totals_are_not_summable"]


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
    empty = ev.trial_progress(session, ORG)
    assert empty["attributed_value"] is None
    assert ev.NO_EVENTS_RECORDED in {g["reason"] for g in empty["evidence_gaps"]}

    # Now the window holds evidence — just none of it ATTRIBUTED.
    led.record(session, ORG, _draft(ValueClass.POTENTIAL, POTENTIAL_AMOUNT))
    measured = ev.trial_progress(session, ORG)

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
    _priced_line(session, quote_id="q_a")
    _priced_line(session, quote_id="q_b", line_id="L2")
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
    _priced_line(session, quote_id="q_flag")
    _run_and_record(session)

    progress = ev.trial_progress(session, ORG)
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
