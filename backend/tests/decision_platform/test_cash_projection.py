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

from app.commercial.insight import cashflow
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
def test_an_obligation_lands_in_the_week_its_own_document_names(book):
    """Not the week it was raised, not an average — the due date's week."""
    rows = _schedule(book)

    # INV-2: ₹16,700 due 2026-02-04. INV-3: ₹85,200 due 2026-08-19.
    assert Decimal(rows[f"in:{week_key(date(2026, 2, 4))}"]["amount"]) == Decimal("16700")
    assert Decimal(rows[f"in:{week_key(date(2026, 8, 19))}"]["amount"]) == Decimal("85200")
    # BILL-1: ₹3,00,000 due 2026-04-01.
    assert Decimal(rows[f"out:{week_key(date(2026, 4, 1))}"]["amount"]) == Decimal("300000")


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
    assert Decimal(rows["in:undated"]["amount"]) == Decimal("500000")

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
