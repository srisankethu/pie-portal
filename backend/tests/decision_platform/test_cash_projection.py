"""The committed book's effect on cash, and the four things it must not claim.

The interesting tests here are the refusals. Arithmetic over a fold is easy to
get right and easy to check; what makes a cash projection trustworthy is that
it never quietly turns an obligation into a date somebody invented. So the
claims under test are:

1. Money lands in the week **its own document** says, and nowhere else.
2. Overdue money is reported **beside** the timeline, never inside it — putting
   it in week one asserts it arrives now, which is what being overdue disproves.
3. An open order is never placed on the timeline at all: it has no due date,
   and this book has no delivery dates to invent one from.
4. The schedule **reconciles** with the party-keyed states it was folded from.

The fixture comes from ``test_decision_intelligence``: one synced book with one
of each situation, including two open invoices and one open bill with real due
dates. Importing it rather than rebuilding it is deliberate — a second 180-line
copy of "a book" would drift from the first, and then two tests would disagree
about what this business looks like.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.commercial.insight import cashflow
from app.domain import models
from app.state.engine import load
from app.state.reducers.cash import CASH_SCHEDULE, week_key
from app.state.reducers.commitments import COMMITMENTS
from app.state.reducers.receivables import RECEIVABLES

from .test_decision_intelligence import ORG, TODAY, _fold, _seed

#: The Monday opening the week that contains TODAY. Derived, not written down:
#: TODAY is the business date now, so a hardcoded Monday would be right for one
#: week and quietly wrong for every other.
THIS_MONDAY = TODAY - timedelta(days=TODAY.isoweekday() - 1)


def _schedule(session) -> dict:
    return load(session, ORG, CASH_SCHEDULE, TODAY)


def _project(session, **kw) -> dict:
    return cashflow.project(
        load(session, ORG, CASH_SCHEDULE, TODAY),
        load(session, ORG, COMMITMENTS, TODAY),
        load(session, ORG, RECEIVABLES, TODAY),
        as_of=TODAY, **kw)


@pytest.fixture()
def book(session):
    _seed(session)
    _fold(session)
    return session


# ── the fold ────────────────────────────────────────────────────────────────
def _amount(rows: dict, prefix: str) -> Decimal:
    """What one direction owes in one week, across every party in it.

    The key carries the party now — `in:2026-W06:c1` — so a test about *when*
    money is due sums the week rather than naming whichever customer happens to
    be in the fixture. Which party a row belongs to is its own test below.
    """
    return sum((Decimal(r["amount"]) for k, r in rows.items()
                if k.startswith(prefix)), Decimal(0))


def test_an_obligation_lands_in_the_week_its_own_document_names(book):
    """Not the week it was raised, not an average — the due date's week."""
    rows = _schedule(book)

    # INV-2: ₹16,700 due 2026-02-04. INV-3: ₹85,200 due 2026-08-19.
    assert _amount(rows, f"in:{week_key(date(2026, 2, 4))}:") == Decimal("16700")
    assert _amount(rows, f"in:{week_key(date(2026, 8, 19))}:") == Decimal("85200")
    # BILL-1: ₹3,00,000 due 2026-04-01.
    assert _amount(rows, f"out:{week_key(date(2026, 4, 1))}:") == Decimal("300000")


def test_the_schedule_says_whose_obligation_each_row_is(book):
    """The grain that lets the projection shift one customer's money by that
    customer's own measured lateness. Keyed by week alone, it could only ever
    move everybody by the same number of days — which is a book average wearing
    a per-customer label."""
    rows = _schedule(book)

    # The *internal* customer id, resolved through `Masters` exactly as the
    # party-keyed states resolve it — and exactly what `PaymentApplication`
    # rows are keyed by, which is what makes the join in the projection line
    # up. Keying on the external id would put the schedule in one namespace and
    # the payment history in another.
    customer = book.scalars(
        select(models.Customer).where(models.Customer.external_id == "c1")).one()

    key = f"in:{week_key(date(2026, 2, 4))}:{customer.customer_id}"
    assert key in rows, sorted(rows)
    assert rows[key]["party_id"] == customer.customer_id


def test_a_settled_document_schedules_nothing(book):
    """INV-1 is paid and three of the four bills are. A fold that counted every
    document rather than every *balance* would schedule money that has already
    moved — the failure that makes a projection worse than no projection."""
    rows = _schedule(book)
    scheduled = sum(int(r["documents"]) for r in rows.values())
    assert scheduled == 3, rows


def test_an_obligation_with_no_terms_is_bucketed_undated_never_spread(session):
    """Defaulting a missing due date is how an untermed invoice becomes an
    overdue one. It is counted, named, and left off the timeline."""
    _seed(session, invoices=[
        {"invoice_id": "inv9", "invoice_number": "INV-9", "customer_id": "c1",
         "date": "2026-01-01", "status": "sent",
         "total": "500000", "balance": "500000",
         "line_items": [{"line_item_id": "l1", "item_id": "dead",
                         "quantity": 10, "rate": "50000",
                         "item_total": "500000"}]},
    ], payments=[])
    _fold(session)

    rows = _schedule(session)
    assert _amount(rows, "in:undated:") == Decimal("500000")

    result = _project(session)
    assert result["undated"]["inflow"] == 500000.0
    # Nowhere on the timeline, and not in the overdue figure either — it is not
    # late, because nobody ever said when it was due.
    assert all(b["inflow"] == 0.0 for b in result["buckets"])
    assert result["overdue"]["inflow"] == 0.0


# ── the refusals ────────────────────────────────────────────────────────────
def test_overdue_money_is_reported_beside_the_timeline_not_inside_it(book):
    """₹16,700 was due in February and ₹3,00,000 in April. Both are real, and
    neither is week-one movement: the cumulative must not contain them."""
    result = _project(book)

    assert result["overdue"]["inflow"] == 16700.0
    assert result["overdue"]["outflow"] == 300000.0
    assert result["buckets"][0]["inflow"] == 0.0
    assert result["buckets"][0]["outflow"] == 0.0
    assert result["buckets"][0]["cumulative"] == 0.0
    # The horizon's total is the dated future only.
    assert result["net_over_horizon"] == 85200.0


def test_the_cumulative_is_movement_and_never_a_position(book):
    """It starts at zero because there is no opening balance to start from —
    PIE reads payments, not balances. Every bucket's cumulative is the running
    sum of the buckets before it and nothing else."""
    result = _project(book)

    running = 0.0
    for bucket in result["buckets"]:
        running = round(running + bucket["net"], 2)
        assert bucket["cumulative"] == running
    assert "runway" not in str(result).lower()


def test_an_open_order_is_never_placed_on_the_timeline(book):
    """An order has no due date. It is exposure, it is quantified beside the
    chart, and it is not a bar — scheduling it needs a delivery date, and
    expected_delivery_date is blank across this book."""
    result = _project(book)
    unscheduled = result["unscheduled"]

    assert unscheduled["open_purchase_value"] > 0, "the fixture has an open PO"
    on_timeline = sum(b["inflow"] + b["outflow"] for b in result["buckets"])
    on_timeline += result["overdue"]["inflow"] + result["overdue"]["outflow"]
    on_timeline += result["undated"]["inflow"] + result["undated"]["outflow"]
    on_timeline += result["beyond_horizon"]["inflow"] + result["beyond_horizon"]["outflow"]
    # Everything scheduled is an invoice or a bill. Not one rupee of it is an
    # order, so the two totals cannot overlap.
    assert on_timeline == 16700.0 + 85200.0 + 300000.0


def test_a_dated_obligation_past_the_horizon_is_named_not_dropped(book):
    """A projection whose parts do not add up to the book is one people stop
    trusting. A one-week horizon must push the August invoice into
    `beyond_horizon` rather than losing it."""
    narrow = _project(book, weeks=1)

    assert narrow["beyond_horizon"]["inflow"] == 85200.0
    assert narrow["net_over_horizon"] == 0.0
    assert narrow["empty_reason"], "an empty horizon must say why it is empty"


# ── the reconciliation ──────────────────────────────────────────────────────
def test_the_schedule_reconciles_with_the_states_it_was_folded_from(book):
    """Same events, same guards, two grains. If these disagree, one of the two
    folds is wrong and the screens built on them will argue forever."""
    schedule = _schedule(book)
    scheduled_in = sum(Decimal(str(r["amount"])) for k, r in schedule.items()
                       if k.startswith("in:"))
    scheduled_out = sum(Decimal(str(r["amount"])) for k, r in schedule.items()
                        if k.startswith("out:"))

    receivables = load(book, ORG, RECEIVABLES, TODAY)
    commitments = load(book, ORG, COMMITMENTS, TODAY)
    # `.get` with a zero default, because a supplier whose every bill is settled
    # has a COMMITMENTS row and no payables field at all — an ADD that never
    # fired writes nothing rather than writing zero.
    outstanding = sum(Decimal(str(r.get("outstanding", 0)))
                      for r in receivables.values())
    payables = sum(Decimal(str(r.get("payables_balance", 0)))
                   for r in commitments.values() if r.get("direction") == "supplier")

    assert scheduled_in == outstanding
    assert scheduled_out == payables
    # Every party in this book resolves, so nothing is unattributable — the
    # figure exists for the book where that is not true.
    assert _project(book)["unattributed"] == {"inflow": 0.0, "outflow": 0.0}


# ── who may read it ─────────────────────────────────────────────────────────
def test_the_projection_is_manager_and_above(session):
    """The inflow half is receivables and would be fine for a salesperson. The
    outflow half is what we owe suppliers — purchase cost by another name, in
    exactly the sense that scopes the Suppliers screen."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import insight as insight_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD, ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()
    _seed(session)
    _fold(session)

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight_router.router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    def token(email: str) -> dict:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": SEED_PASSWORD})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    assert client.get("/api/v1/insight/cashflow",
                      headers=token("r.nair@sanketh.in")).status_code == 403
    ok = client.get("/api/v1/insight/cashflow",
                    headers=token("m.rao@sanketh.in"))
    assert ok.status_code == 200
    body = ok.json()
    assert body["overdue"]["outflow"] == 300000.0
    assert len(body["buckets"]) == cashflow.WEEKS
    assert body["buckets"][0]["starts_on"] == THIS_MONDAY.isoformat()

    # Same scope, same reason. How long we string a supplier along is a
    # commercial position, not a call list — the mirror screen is not scoped
    # like /payments, which a salesperson may read.
    assert client.get("/api/v1/insight/payables",
                      headers=token("r.nair@sanketh.in")).status_code == 403
    assert client.get("/api/v1/insight/payables",
                      headers=token("m.rao@sanketh.in")).status_code == 200


# ── the band ────────────────────────────────────────────────────────────────
#
# Due dates answer "when is this money promised", which is not the question
# somebody funding a week is asking. A chart drawing only that line understates
# what the week needs — and says nothing about by how much. The same committed
# book is placed three times, shifted by each party's own measured days-late,
# at the corners named in `cashflow`'s docstring.

def _lag(party_id: str, early: int, expected: int, late: int):
    from app.commercial.insight.payments import Lag
    return Lag(party_id=party_id, early_days=early, expected_days=expected,
               late_days=late, settlements=5)


def _party_of(session, external_id: str) -> str:
    return session.scalars(
        select(models.Customer)
        .where(models.Customer.external_id == external_id)).one().customer_id


def _vendor_of(session, external_id: str) -> str:
    return session.scalars(
        select(models.Vendor)
        .where(models.Vendor.external_id == external_id)).one().vendor_id


def _projected(session, lags=None, weeks: int = 13, payable_lags=None) -> dict:
    return cashflow.project(
        load(session, ORG, CASH_SCHEDULE, TODAY),
        load(session, ORG, COMMITMENTS, TODAY),
        load(session, ORG, RECEIVABLES, TODAY),
        as_of=TODAY, weeks=weeks, lags=lags or {},
        payable_lags=payable_lags or {})


def _due_in(session, weeks_ahead: int, amount: str = "100000") -> None:
    """One unpaid invoice, due a whole number of weeks from the fold date."""
    due = TODAY + timedelta(weeks=weeks_ahead)
    _seed(session, invoices=[
        {"invoice_id": "inv_lag", "invoice_number": "INV-LAG", "customer_id": "c1",
         "date": TODAY.isoformat(), "due_date": due.isoformat(), "status": "sent",
         "total": amount, "balance": amount,
         "line_items": [{"line_item_id": "l1", "item_id": "p1", "quantity": 1,
                         "rate": amount, "item_total": amount}]},
    ], payments=[])
    _fold(session)


def _week_with(series: dict, amount: float) -> int:
    for i, bucket in enumerate(series["buckets"]):
        if bucket["inflow"] == amount:
            return i
    raise AssertionError(f"₹{amount} landed in no week: {series['buckets']}")


def test_a_customers_own_lateness_moves_their_money(session):
    """The whole point. A customer who has never paid inside three weeks of the
    due date does not have their invoice sitting in the due week.

    Inflow arriving sooner is the *best* case for the week that has to be
    funded, so their fastest observed behaviour is the one `best` stands on.
    """
    _due_in(session, 2)
    party = _party_of(session, "c1")

    result = _projected(session, {party: _lag(party, 0, 7, 21)})

    assert _week_with(result["scenarios"]["best"], 100000.0) == 2
    assert _week_with(result["scenarios"]["expected"], 100000.0) == 3
    assert _week_with(result["scenarios"]["worst"], 100000.0) == 5


def test_a_customer_with_no_measured_history_stays_on_their_due_date(session):
    """Not defaulted to prompt, and not defaulted to the book average: left
    exactly where the document put them, in every scenario. A thin-evidence
    customer silently treated as punctual would tighten a band the evidence
    does not tighten."""
    _due_in(session, 2)

    result = _projected(session, lags={})

    for scenario in cashflow.SCENARIOS:
        assert _week_with(result["scenarios"][scenario], 100000.0) == 2
    assert result["basis"]["customers_measured"] == 0
    assert result["basis"]["share_measured"] == 0.0


def test_the_requirement_is_the_deepest_trough_across_the_scenarios(session):
    """The number the screen exists to produce. Inflow arriving later cannot
    make a trough shallower, so the requirement tracks the worst case — taken as
    a minimum over all three rather than assumed, because with nothing measured
    they are identical."""
    _due_in(session, 1)
    party = _party_of(session, "c1")

    result = _projected(session, {party: _lag(party, 0, 14, 28)})

    assert result["requirement"] == min(
        result["scenarios"][s]["lowest_cumulative"] for s in cashflow.SCENARIOS)
    assert result["requirement"] <= result["scenarios"]["best"]["lowest_cumulative"]


def test_lateness_can_push_money_past_the_horizon_and_says_so(session):
    """Money inside the horizon on its due date and outside it once the payer's
    own behaviour is applied. Counted per scenario rather than folded into the
    single beyond-horizon figure, which describes the due dates."""
    _due_in(session, 12)
    party = _party_of(session, "c1")

    result = _projected(session, {party: _lag(party, 0, 0, 56)}, weeks=13)

    assert result["scenarios"]["expected"]["beyond_horizon"]["inflow"] == 0.0
    assert result["scenarios"]["worst"]["beyond_horizon"]["inflow"] == 100000.0


def test_the_response_says_how_much_of_the_inflow_it_could_actually_move(session):
    """A narrow band because these customers are punctual, and a narrow band
    because almost nothing is measured, look identical on the chart. The share
    is what separates them."""
    _due_in(session, 2)
    party = _party_of(session, "c1")

    result = _projected(session, {party: _lag(party, 0, 3, 9)})

    assert result["basis"]["customers_measured"] == 1
    assert result["basis"]["share_measured"] == 1.0
    assert result["basis"]["inflow_unmeasured"] == 0.0
    # The two sides are reported separately because they are measured from
    # different evidence, and one can be thin while the other is not. Nothing
    # measured about suppliers here, so the outflow half stays on its dates.
    assert result["basis"]["outflow_shifted"] is False
    assert result["basis"]["vendors_measured"] == 0


def _bill_due_in(session, weeks_ahead: int, amount: str = "50000") -> None:
    """One unpaid bill, due a whole number of weeks from the fold date."""
    due = TODAY + timedelta(weeks=weeks_ahead)
    _seed(session, invoices=[], bills=[
        {"bill_id": "bill_lag", "bill_number": "BILL-LAG", "vendor_id": "v1",
         "date": TODAY.isoformat(), "due_date": due.isoformat(), "status": "open",
         "total": amount, "balance": amount,
         "line_items": [{"line_item_id": "bl1", "item_id": "p1", "quantity": 1,
                         "rate": amount, "item_total": amount}]},
    ], payments=[])
    _fold(session)


def _week_with_outflow(series: dict, amount: float) -> int:
    return [b["outflow"] for b in series["buckets"]].index(amount)


def test_a_bill_stays_on_its_due_date_when_nothing_measures_this_supplier(session):
    """The refusal that survives the payable side landing. A supplier we have
    not settled often enough to measure is not assumed to be paid late for our
    own convenience — their bill sits exactly where the document put it, in
    every scenario."""
    _bill_due_in(session, 3)
    party = _party_of(session, "c1")

    result = _projected(session, {party: _lag(party, 0, 30, 60)})

    for scenario in cashflow.SCENARIOS:
        assert _week_with_outflow(result["scenarios"][scenario], 50000.0) == 3


def test_our_own_lateness_moves_the_money_we_owe_the_other_way(session):
    """The payable mirror, and the direction is the whole subtlety.

    Money leaving *later* is better for the week that has to fund it, so our
    slowest observed behaviour belongs to `best` and our fastest to `worst` —
    the opposite ends from the customer side, on the same three numbers.
    """
    _bill_due_in(session, 3)
    vendor = _vendor_of(session, "v1")

    result = _projected(session, payable_lags={vendor: _lag(vendor, 0, 7, 21)})

    assert _week_with_outflow(result["scenarios"]["best"], 50000.0) == 6
    assert _week_with_outflow(result["scenarios"]["expected"], 50000.0) == 4
    assert _week_with_outflow(result["scenarios"]["worst"], 50000.0) == 3


def _both_due_in(session, invoice_weeks: int, bill_weeks: int) -> None:
    """One unpaid invoice and one unpaid bill, both inside the horizon.

    Seeded in a single sync rather than by calling the two helpers above in
    turn: each of them replaces the source's whole document list, so a second
    call would retire what the first one wrote and leave only one side on the
    timeline — which is exactly the condition that made an earlier version of
    the corner test below pass whatever the outflow did.
    """
    invoice_due = TODAY + timedelta(weeks=invoice_weeks)
    bill_due = TODAY + timedelta(weeks=bill_weeks)
    _seed(session, invoices=[
        {"invoice_id": "inv_lag", "invoice_number": "INV-LAG", "customer_id": "c1",
         "date": TODAY.isoformat(), "due_date": invoice_due.isoformat(),
         "status": "sent", "total": "100000", "balance": "100000",
         "line_items": [{"line_item_id": "l1", "item_id": "p1", "quantity": 1,
                         "rate": "100000", "item_total": "100000"}]},
    ], bills=[
        {"bill_id": "bill_lag", "bill_number": "BILL-LAG", "vendor_id": "v1",
         "date": TODAY.isoformat(), "due_date": bill_due.isoformat(),
         "status": "open", "total": "90000", "balance": "90000",
         "line_items": [{"line_item_id": "bl1", "item_id": "p1", "quantity": 1,
                         "rate": "90000", "item_total": "90000"}]},
    ], payments=[])
    _fold(session)


def test_the_worst_case_is_a_corner_and_not_everybody_being_slow(session):
    """The reason the scenarios are named for cash rather than for speed.

    Both sides measured, and both moving. "Everybody at their slowest" mixes
    late money in (bad for the week) with late money out (good for it) and is a
    corner of nothing — under that reading the bill leaves in week 6 and the
    trough is *shallower* than `best`. `worst` takes the genuinely worst end of
    each side: customers slowest, us fastest.
    """
    _both_due_in(session, invoice_weeks=2, bill_weeks=2)
    customer = _party_of(session, "c1")
    vendor = _vendor_of(session, "v1")

    result = _projected(session,
                        lags={customer: _lag(customer, 0, 7, 28)},
                        payable_lags={vendor: _lag(vendor, 0, 7, 28)})

    # The bill is at its due week under `worst` (we pay fastest) and four weeks
    # out under `best` (we pay slowest). Money out sooner is the deeper trough.
    assert _week_with_outflow(result["scenarios"]["worst"], 90000.0) == 2
    assert _week_with_outflow(result["scenarios"]["best"], 90000.0) == 6

    worst = result["scenarios"]["worst"]["lowest_cumulative"]
    assert worst == result["requirement"]
    assert worst < result["scenarios"]["best"]["lowest_cumulative"]
    assert result["basis"]["outflow_shifted"] is True
    assert result["basis"]["vendors_measured"] == 1
    assert result["basis"]["outflow_share_measured"] == 1.0


def test_every_scenario_moves_the_same_money_only_at_different_times(session):
    """A scenario that loses a rupee is a bug, not a timing. Each one places the
    identical book — what changes is which week, never how much."""
    _both_due_in(session, invoice_weeks=2, bill_weeks=3)
    customer = _party_of(session, "c1")
    vendor = _vendor_of(session, "v1")

    result = _projected(session,
                        lags={customer: _lag(customer, -3, 7, 28)},
                        payable_lags={vendor: _lag(vendor, 0, 9, 40)})

    def moved(series: dict) -> tuple[float, float]:
        return (round(sum(b["inflow"] for b in series["buckets"])
                      + series["beyond_horizon"]["inflow"], 2),
                round(sum(b["outflow"] for b in series["buckets"])
                      + series["beyond_horizon"]["outflow"], 2))

    totals = {s: moved(result["scenarios"][s]) for s in cashflow.SCENARIOS}
    assert len(set(totals.values())) == 1, totals
