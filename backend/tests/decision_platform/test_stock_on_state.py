"""The stock screen, moved off a whole-book scan and onto folded state.

The analysis assumed the existing tests would serve as the regression harness
for this move. They do not: `commercial/insight/stock.py` is covered thoroughly
as a set of pure functions, and the *endpoint* was covered not at all. So the
harness is here, and it is the strict form — the state-backed screen is compared
against the arithmetic the router used to do inline, row for row.

That comparison is the whole point. Moving a calculation is only safe if you can
show the answer did not move with it.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.commercial.insight import stock
from app import clock
from app.commercial.policy import load_for_org
from app.db import Base, get_session
from app.domain import models
from app.ingestion.sync import SyncService
from app.routers import insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.signals.aggregates import label_for, load_snapshot
from app.state import engine as state_engine
from app.state.reducers.inventory import INVENTORY

ORG = "org_sanketh"
OWNER = "s.menon@sanketh.in"
SALESPERSON = "r.nair@sanketh.in"


class _Source:
    """Two tracked items, one service, one customer, sales and a bill."""

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Acme Engineering",
                 "status": "active"},
                {"contact_id": "c2", "contact_name": "Bharat Tools",
                 "status": "active"}]

    def list_items(self):
        return [
            {"item_id": "i1", "name": "CNMG 120408 Insert", "unit": "pcs",
             "status": "active", "track_inventory": True, "item_type": "inventory",
             "stock_on_hand": 240, "available_stock": 220,
             "actual_available_stock": 180, "reorder_level": 50,
             "purchase_rate": "401.25"},
            {"item_id": "i2", "name": "End mill 12mm", "unit": "pcs",
             "status": "active", "track_inventory": True, "item_type": "inventory",
             "stock_on_hand": 8, "available_stock": 8, "purchase_rate": "880"},
            # A service has no shelf; it must never reach the screen.
            {"item_id": "i3", "name": "Regrinding service", "unit": "job",
             "status": "active", "track_inventory": False, "item_type": "service",
             "stock_on_hand": 0},
            # Sold, but the source reports no stock fields for it at all — an
            # older fixture, or a Zoho plan without Inventory.
            {"item_id": "i4", "name": "Boring bar S16Q", "unit": "pcs",
             "status": "active"},
        ]

    def list_users(self):
        return []

    def list_invoices(self, skip=None):
        return [
            {"invoice_id": "inv1", "invoice_number": "INV-1", "customer_id": "c1",
             "date": "2026-05-10",
             "line_items": [{"line_item_id": "l1", "item_id": "i1", "quantity": 40,
                             "rate": "600", "item_total": "24000"}]},
            {"invoice_id": "inv2", "invoice_number": "INV-2", "customer_id": "c2",
             "date": "2026-06-14",
             "line_items": [{"line_item_id": "l1", "item_id": "i1", "quantity": 12,
                             "rate": "610", "item_total": "7320"},
                            {"line_item_id": "l2", "item_id": "i4", "quantity": 3,
                             "rate": "2400", "item_total": "7200"}]},
        ]

    def list_bills(self, skip=None):
        return [{"bill_id": "b1", "bill_number": "BILL-1", "date": "2026-04-02",
                 "status": "open", "total": "80250", "balance": "0",
                 "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                 "quantity": 200, "rate": "401.25"}]}]


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    SyncService(s, _Source(), ORG).run()
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

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
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _build_state(client) -> date:
    s = client.Maker()
    try:
        # The business date, not the machine's. `clock.today` is what every
        # endpoint under test asks for, and with the organization in
        # Asia/Kolkata the two disagree for five and a half hours of every day:
        # after 18:30 UTC the app reads a state built for "yesterday" and
        # correctly reports an empty shelf. A suite that goes red every evening
        # in India is a suite people stop reading.
        th = load_for_org(s, ORG)
        on = clock.today(th.timezone)
        state_engine.build(s, ORG, as_of=on, thresholds_version=th.version)
        s.commit()
        return on
    finally:
        s.close()


# ── the regression harness ──────────────────────────────────────────────────
def _lines_the_old_way(session, with_cost: bool) -> list[stock.StockLine]:
    """Exactly what the router used to do inline: scan every line, fold in
    Python, and read the latest stock snapshot per item."""
    snapshot = load_snapshot(session, ORG)
    latest: dict[str, models.StockSnapshot] = {}
    for row in session.query(models.StockSnapshot).filter_by(
            organization_id=ORG).order_by(models.StockSnapshot.as_of).all():
        latest[row.product_id] = row

    sold_qty: dict[str, float] = {}
    last_sold: dict[str, date] = {}
    buyer_seen: dict[str, dict[str, date]] = {}
    for sale in snapshot.sales:
        sold_qty[sale.product_id] = sold_qty.get(sale.product_id, 0.0) + float(sale.qty)
        if sale.date > last_sold.get(sale.product_id, date.min):
            last_sold[sale.product_id] = sale.date
        seen = buyer_seen.setdefault(sale.product_id, {})
        if sale.date > seen.get(sale.customer_id, date.min):
            seen[sale.customer_id] = sale.date

    last_purchased: dict[str, date] = {}
    if with_cost:
        for c in snapshot.costs:
            if c.date > last_purchased.get(c.product_id, date.min):
                last_purchased[c.product_id] = c.date

    # The earliest evidence each item existed: first stock reading or first
    # purchase, whichever came first. Computed here as well rather than
    # excluded from the comparison — the fold derives it from the same two
    # facts this scan already holds, so parity stays checkable and the harness
    # keeps its teeth. Not gated on ``with_cost``: the health band reads it and
    # every role sees the band.
    first_seen: dict[str, date] = {}

    def _note_first(pid: str, when: date) -> None:
        if when < first_seen.get(pid, date.max):
            first_seen[pid] = when

    for snap in session.query(models.StockSnapshot).filter_by(
            organization_id=ORG).all():
        _note_first(snap.product_id, snap.as_of)
    for c in snapshot.costs:
        _note_first(c.product_id, c.date)

    return [
        stock.StockLine(
            product_id=pid,
            label=label_for(snapshot.product_names, pid, kind="item"),
            on_hand=float(row.on_hand or 0),
            available=(float(row.available) if row.available is not None else None),
            actual_available=(float(row.actual_available)
                              if row.actual_available is not None else None),
            reorder_level=(float(row.reorder_level)
                           if row.reorder_level is not None else None),
            last_sold=last_sold.get(pid),
            sold_qty_window=sold_qty.get(pid, 0.0),
            purchase_rate=(float(row.purchase_rate)
                           if row.purchase_rate is not None else None),
            last_purchased=last_purchased.get(pid),
            buyers=tuple(
                label_for(snapshot.customer_names, cid, kind="customer")
                for cid, _w in sorted((buyer_seen.get(pid) or {}).items(),
                                      key=lambda kv: kv[1], reverse=True)[:6]),
            first_seen=first_seen.get(pid),
        )
        for pid, row in latest.items() if row.tracked
    ]


@pytest.mark.parametrize("with_cost", [True, False])
def test_the_state_backed_lines_match_the_scan_they_replaced(client, with_cost):
    """The move is only safe if the answer did not move with it."""
    on = _build_state(client)
    s = client.Maker()
    try:
        old = sorted(_lines_the_old_way(s, with_cost), key=lambda r: r.product_id)
        new = sorted(
            stock.lines_from_state(
                state_engine.load(s, ORG, INVENTORY, on),
                labels={p.product_id: p.name for p in s.query(models.Product)
                        .filter_by(organization_id=ORG)},
                buyers=insight._recent_buyers(
                    s, ORG, {c.customer_id: c.name for c in s.query(models.Customer)
                             .filter_by(organization_id=ORG)}),
                with_cost=with_cost),
            key=lambda r: r.product_id)
        assert old, "the fixture must produce rows to compare"
        assert new == old
    finally:
        s.close()


def test_the_screen_renders_from_state(client):
    _build_state(client)
    body = client.get("/api/v1/insight/stock", headers=_hdr(client, OWNER)).json()

    assert body["empty_reason"] is None
    rows = body["items"]
    by_id = {r["product_id"]: r for r in rows}
    inserts = next(r for r in rows if "CNMG" in r["label"])
    assert inserts["on_hand"] == 240
    assert inserts["sold_qty_window"] == 52          # 40 + 12
    assert inserts["last_sold"] == "2026-06-14"
    assert inserts["buyers"][0] == "Bharat Tools"    # most recent first
    assert "Acme Engineering" in inserts["buyers"]
    # The service has no shelf and must not be on it.
    assert not any("Regrinding" in r["label"] for r in rows)
    assert len(by_id) == 2


def test_a_book_with_no_state_yet_says_so_rather_than_showing_an_empty_shelf(client):
    """Degrade, do not lie. A screen that reported nothing on the shelf because
    nobody had run a fold would be indistinguishable from a real stockout."""
    body = client.get("/api/v1/insight/stock", headers=_hdr(client, OWNER)).json()
    assert body["empty_reason"]
    assert "sync" in body["empty_reason"].lower()
    assert not body.get("items")


def test_a_salesperson_s_rows_have_no_cost_to_leak(client):
    """Absent by construction, not masked: the line object is built without a
    purchase rate for that role, so there is nothing to read out of a network
    tab."""
    _build_state(client)
    body = client.get("/api/v1/insight/stock",
                      headers=_hdr(client, SALESPERSON)).json()
    rows = body["items"]
    assert rows
    for row in rows:
        assert "purchase_rate" not in row
        assert "inventory_value" not in row
        assert "last_purchased" not in row
    # But the number this screen exists for is there *and is a real figure*.
    # Withholding the purchase rate from the line object rather than from the
    # response silently zeroed this, and a presence-only assertion missed it.
    drains = [r["monthly_holding_cost"] for r in rows]
    assert all(d is not None for d in drains)
    assert sum(drains) > 0, "Monthly cash drain must survive the role filter"
    assert body["kpis"]


def test_the_screen_reads_the_last_fold_rather_than_assuming_today(client):
    """State is built at the end of a sync, so "today" is right only until the
    first day nobody syncs. A screen that asked for today and got nothing would
    report an empty shelf rather than a stale one."""
    on = _build_state(client)
    s = client.Maker()
    try:
        # An older fold alongside it must not become the one that is read.
        state_engine.build(s, ORG, as_of=on - timedelta(days=30),
                           thresholds_version="v1")
        s.commit()
        assert state_engine.latest_as_of(s, ORG, INVENTORY) == on
    finally:
        s.close()

    body = client.get("/api/v1/insight/stock", headers=_hdr(client, OWNER)).json()
    assert body["empty_reason"] is None
    assert body["items"]


def test_an_item_with_history_but_no_stock_reading_is_not_reported_as_zero(client):
    """Sales and cost lines put an item into INVENTORY state without anything
    ever observing its shelf. Zero on hand is a claim; silence is the truth."""
    on = _build_state(client)
    s = client.Maker()
    try:
        states = state_engine.load(s, ORG, INVENTORY, on)
        product_id = s.query(models.Product).filter_by(
            organization_id=ORG, external_id="i4").one().product_id
        # It is in state — it has sales history.
        assert states[product_id]["units_sold"] == "3"
        assert "on_hand" not in states[product_id]
        # And it is not on the shelf.
        lines = stock.lines_from_state(states, labels={}, buyers={}, with_cost=True)
        assert product_id not in {line.product_id for line in lines}
    finally:
        s.close()


# ── the disclosure coupling ─────────────────────────────────────────────────
#
# Monthly drain = quantity x cost x carrying rate / 12, and the quantity is on
# every row. So the rate is the only thing standing between a salesperson and
# every purchase cost in the catalogue. That was a paragraph asking somebody to
# remember on the day the rate got published; it is now a setting the code
# reads.

def _stock_body(client, email: str) -> dict:
    return client.get("/api/v1/insight/stock", headers=_hdr(client, email)).json()


def _publish_the_rate(monkeypatch) -> None:
    """Mark the carrying rate as published, the way an owner would.

    Wraps the loader rather than poking the frozen dataclass: `dataclasses
    .replace` is how the rest of this codebase varies a threshold, and it keeps
    the version hash honest so the last test here means something."""
    import dataclasses

    from app.routers import insight as insight_router

    original = insight_router.policy.load_for_org
    monkeypatch.setattr(
        insight_router.policy, "load_for_org",
        lambda session, org: dataclasses.replace(
            original(session, org), carrying_rate_is_published=True))


def test_the_drain_is_on_a_salespersons_screen_while_the_rate_is_secret(client):
    """The default, and the reason the column exists at all."""
    _build_state(client)
    body = _stock_body(client, SALESPERSON)
    assert body["items"]
    assert all("monthly_holding_cost" in r for r in body["items"])
    assert any(c["key"] == "MONTHLY_DRAIN" for c in body["kpis"])


def test_publishing_the_rate_takes_the_drain_off_that_screen(client, monkeypatch):
    """Not a reminder — a coupling. Flip the setting and the column, both KPI
    cards derived from it, and nothing else, disappear."""
    _publish_the_rate(monkeypatch)
    _build_state(client)
    body = _stock_body(client, SALESPERSON)

    assert body["items"], "the rest of the screen must survive"
    assert all("monthly_holding_cost" not in r for r in body["items"])
    assert not any(c["key"] in ("MONTHLY_DRAIN", "DEAD_DRAIN") for c in body["kpis"])
    # Named, not silently blank: a column that vanishes with no explanation
    # reads as a bug, and somebody will put it back.
    withheld = next(u for u in body["unavailable"]
                    if u["series"] == "monthly_cash_drain")
    assert "carrying rate" in withheld["reason"]


def test_an_owner_keeps_the_drain_even_once_the_rate_is_public(client, monkeypatch):
    """A reader who can already see cost can compute the drain anyway.
    Withholding it from them would be theatre."""
    _publish_the_rate(monkeypatch)
    _build_state(client)
    body = _stock_body(client, OWNER)
    assert all("monthly_holding_cost" in r for r in body["items"])
    assert any(c["key"] == "MONTHLY_DRAIN" for c in body["kpis"])


def test_the_drain_is_absent_rather_than_zero(client, monkeypatch):
    """Zero would read as "this costs nothing to keep", which is the opposite
    of true and worse than saying nothing."""
    _publish_the_rate(monkeypatch)
    _build_state(client)
    for row in _stock_body(client, SALESPERSON)["items"]:
        assert "monthly_holding_cost" not in row


def test_the_setting_is_inside_the_thresholds_version(client):
    """A screen rendered before and after the change has to be
    distinguishable, or a past number becomes unexplainable."""
    from app.commercial.config import CommercialThresholds

    secret = CommercialThresholds()
    published = CommercialThresholds(carrying_rate_is_published=True)
    assert secret.version != published.version
