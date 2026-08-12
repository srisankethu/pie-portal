"""The monthly fold must produce the numbers the lines produce.

The discipline the stock-screen move used, for the same reason: it caught a
₹5,00,000 double-count and an inverted offtake window there, and neither would
have survived five minutes of "looks right". So before any screen is switched
over, the old path and the new one are run against one fixture and their
outputs compared field by field.

What is being claimed, precisely: **every window these screens use is a whole
calendar month**, so a month's total lands in the same period its lines would
and the two paths cannot disagree. If somebody adds a rolling 30-day window,
that claim breaks and this file is what says so.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.commercial.insight import flow, periods, series
from app.commercial.policy import load_for_org
from app.ingestion.sync import SyncService
from app.signals.aggregates import load_snapshot
from app.state.engine import build, load
from app.state.reducers.trade import CUSTOMER_MONTH

ORG = "org_pie"     # the seeded org, so the API fixture can sign in


class _Source:
    """A book with trade spread across six months, several customers, and the
    awkward cases: a customer who stops, one who starts, and one who trades in
    exactly one month."""

    def list_contacts(self):
        return [{"contact_id": f"c{i}", "contact_name": f"Customer {i}",
                 "status": "active"} for i in range(1, 5)]

    def list_vendors(self):
        return []

    def list_users(self):
        return []

    def list_items(self):
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active", "purchase_rate": "100"},
                {"item_id": "i2", "name": "Drill", "unit": "pcs",
                 "status": "active", "purchase_rate": "200"}]

    def list_invoices(self, skip=None):
        out = []
        n = 0
        plan = [
            # (customer, month, day, [(item, qty, rate)])
            ("c1", "2026-01", 15, [("i1", 10, "500"), ("i2", 4, "900")]),
            ("c1", "2026-02", 3, [("i1", 6, "500")]),
            ("c1", "2026-02", 27, [("i2", 2, "900")]),   # two invoices, one month
            ("c1", "2026-04", 9, [("i1", 20, "500")]),   # a gap in March
            ("c2", "2026-01", 8, [("i2", 5, "900")]),
            ("c2", "2026-03", 21, [("i1", 3, "500")]),   # then stops
            ("c3", "2026-05", 2, [("i1", 40, "500")]),   # starts late
            ("c3", "2026-06", 11, [("i2", 7, "900")]),
            ("c4", "2026-03", 30, [("i1", 1, "500")]),   # one month only
        ]
        for cust, month, day, lines in plan:
            n += 1
            out.append({
                "invoice_id": f"inv{n}", "invoice_number": f"INV-{n}",
                "customer_id": cust, "date": f"{month}-{day:02d}",
                "status": "sent", "total": "0", "balance": "0",
                "line_items": [
                    {"line_item_id": f"l{j}", "item_id": item, "quantity": qty,
                     "rate": rate, "item_total": str(int(qty) * int(rate))}
                    for j, (item, qty, rate) in enumerate(lines)],
            })
        return out

    def list_bills(self, skip=None):
        return []

    def list_sales_orders(self):
        return []

    def list_purchase_orders(self):
        return []


@pytest.fixture()
def book(session):
    from app.seed import ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()
    SyncService(session, _Source(), ORG).run()
    session.commit()
    th = load_for_org(session, ORG)
    build(session, ORG, as_of=date(2026, 6, 30), thresholds_version=th.version)
    session.commit()
    return session


def _both(session, as_of: date):
    """The same book, read two ways."""
    snapshot = load_snapshot(session, ORG)
    folded = series.month_rows(load(session, ORG, CUSTOMER_MONTH, as_of))
    return snapshot, folded


AS_OF = date(2026, 6, 30)


def test_the_fold_and_the_lines_agree_on_total_revenue(book):
    snapshot, folded = _both(book, AS_OF)
    from_lines = sum(r.line_revenue for r in snapshot.sales)
    from_fold = sum(r.line_revenue for r in folded)
    assert from_fold == from_lines
    assert from_fold > 0, "the fixture must actually trade"


def test_the_fold_and_the_lines_agree_on_the_last_trading_day(book):
    """Every screen hangs its periods off this. A monthly row is dated on its
    own last trading day precisely so the two answers cannot differ."""
    snapshot, folded = _both(book, AS_OF)
    assert series.last_traded_on(folded) == snapshot.as_of()


@pytest.mark.parametrize("months", [1, 2, 3, 6])
def test_revenue_in_a_window_is_identical_from_either_path(book, months):
    snapshot, folded = _both(book, AS_OF)
    comparison = periods.comparison(AS_OF, months=months)
    for period in (comparison.current, comparison.previous):
        assert (periods.revenue_in(folded, period)
                == periods.revenue_in(snapshot.sales, period)), period.label


@pytest.mark.parametrize("months", [1, 2, 3, 6])
def test_the_whole_revenue_flow_is_identical_from_either_path(book, months):
    """The claim that matters. Not a total — the entire decomposition: every
    customer's classification, both period totals, and the movement list."""
    snapshot, folded = _both(book, AS_OF)
    comparison = periods.comparison(AS_OF, months=months)
    names = snapshot.customer_names

    from_lines = flow.compute(snapshot.sales, names, comparison).to_dict()
    from_fold = flow.compute(folded, names, comparison).to_dict()
    assert from_fold == from_lines


def test_a_customer_who_stopped_is_classified_the_same_way(book):
    """The case a bucketing error hides behind: c2 trades in January and March
    and never again, so a window that mis-places March calls them LOST a period
    early or a period late."""
    snapshot, folded = _both(book, AS_OF)
    comparison = periods.comparison(AS_OF, months=3)

    def moves(rows):
        computed = flow.compute(rows, snapshot.customer_names, comparison)
        return {m.customer_id: m.kind for m in computed.moves}

    assert moves(folded) == moves(snapshot.sales)
    # And specifically: somebody is classified LOST, or this fixture is not
    # exercising the case the test is named after.
    assert "LOST" in moves(folded).values()


def test_two_invoices_in_one_month_fold_into_one_row_without_losing_either(book):
    """c1 has two February invoices. The month must carry both, and it must
    count them as two orders rather than as two rows or as one."""
    rows = {k: v for k, v in load(book, ORG, CUSTOMER_MONTH, AS_OF).items()
            if v["month"] == "2026-02"}
    assert len(rows) == 1
    (value,) = rows.values()
    assert value["orders"] == 2
    # 6 x 500 + 2 x 900 = 4,800.
    assert Decimal(value["revenue"]) == Decimal("4800")


def test_a_month_nobody_traded_in_is_absent_rather_than_zero(book):
    """c1 does not trade in March. A fold that emitted a zero row would make
    "months traded" wrong for every customer with a gap."""
    rows = load(book, ORG, CUSTOMER_MONTH, AS_OF)
    c1 = [v for v in rows.values() if v["month"] == "2026-03"]
    assert all(v["customer_id"] != _customer(book, "c1") for v in c1)


def _customer(session, external_id: str) -> str:
    from app.domain import models
    return session.query(models.Customer).filter_by(
        organization_id=ORG, external_id=external_id).one().customer_id


# ── the endpoint, end to end ────────────────────────────────────────────────
@pytest.fixture()
def client(book):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import insight as insight_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight_router.router)
    app.dependency_overrides[get_session] = lambda: book
    tc = TestClient(app)
    r = tc.post("/api/v1/auth/login",
                json={"email": "s.menon@pie.example", "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    tc.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return tc


def _drop_the_fold(session):
    from sqlalchemy import delete

    from app.domain import models
    session.execute(delete(models.BusinessState).where(
        models.BusinessState.organization_id == ORG,
        models.BusinessState.state == CUSTOMER_MONTH))
    session.commit()


def test_the_revenue_flow_endpoint_reads_the_fold_and_says_so(client):
    """The screen must answer from state, and must say which path answered — a
    page that quietly fell back to scanning the book looks identical."""
    r = client.get("/api/v1/insight/revenue-flow?months=3")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "state"


def test_the_endpoint_falls_back_to_lines_rather_than_showing_nothing(client, book):
    """A database not re-synced since the monthly states landed has no fold.
    Slower is a much better failure than "your revenue is zero"."""
    _drop_the_fold(book)
    body = client.get("/api/v1/insight/revenue-flow?months=3").json()
    assert body["source"] == "lines"
    assert body["buckets"], "the fallback must still produce the waterfall"


def test_both_paths_return_the_same_waterfall(client, book):
    """The equality claim at the HTTP boundary, not just in the functions."""
    from_state = client.get("/api/v1/insight/revenue-flow?months=3").json()
    _drop_the_fold(book)
    from_lines = client.get("/api/v1/insight/revenue-flow?months=3").json()

    assert from_state.pop("source") == "state"
    assert from_lines.pop("source") == "lines"
    assert from_state == from_lines
