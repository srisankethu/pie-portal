"""The two scenarios `simulate.py` used to declare blocked.

They were blocked on data — stock levels and open purchase orders — and the
Business State work read both. What is asserted here is that they compute
mechanically and refuse behaviourally, which is the contract the module's own
docstring sets and the reason a simulator is worth trusting at all:

    Mechanical effects are computed. Behavioural effects are elicited, never
    invented.

So every test below either checks arithmetic against numbers it does itself, or
checks that an assumption is reported as the user's rather than absorbed into a
headline.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.commercial.insight import simulate
from app import clock
from app.commercial.policy import load_for_org
from app.db import Base, get_session
from app.ingestion.sync import SyncService
from app.routers import insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.state.engine import build

ORG = "org_sanketh"
OWNER = "s.menon@sanketh.in"
SALESPERSON = "r.nair@sanketh.in"


class _Source:
    """Dead stock worth ₹5,00,000, and a supplier owed ₹2,50,000."""

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Pitti", "status": "active"}]

    def list_vendors(self):
        return [{"contact_id": "v1", "contact_name": "Kennametal India",
                 "status": "active", "payment_terms": 45},
                # No terms recorded — the cash effect of delaying this one
                # cannot be dated, and the response has to say so.
                {"contact_id": "v2", "contact_name": "Local Traders",
                 "status": "active"}]

    def list_items(self):
        return [
            {"item_id": "dead", "name": "Reamer 12H7", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 1000,
             "available_stock": 1000, "actual_available_stock": 1000,
             "purchase_rate": "500"},
            {"item_id": "fresh", "name": "CNMG 120408", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 100,
             "available_stock": 100, "actual_available_stock": 100,
             "purchase_rate": "300"},
            # Nobody has costed this one. It must be excluded, not valued zero.
            {"item_id": "unpriced", "name": "Mystery tool", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 50,
             "available_stock": 50, "actual_available_stock": 50},
        ]

    def list_users(self):
        return []

    def list_invoices(self, skip=None):
        return [
            {"invoice_id": "i1", "invoice_number": "INV-1", "customer_id": "c1",
             "date": "2024-06-01",
             "line_items": [{"line_item_id": "l1", "item_id": "dead",
                             "quantity": 5, "rate": "900", "item_total": "4500"}]},
            {"invoice_id": "i2", "invoice_number": "INV-2", "customer_id": "c1",
             "date": "2026-07-25",
             "line_items": [{"line_item_id": "l1", "item_id": "fresh",
                             "quantity": 20, "rate": "500", "item_total": "10000"}]},
        ]

    def list_bills(self, skip=None):
        return []

    def list_purchase_orders(self):
        return [{"purchaseorder_id": "po1", "vendor_id": "v1",
                 "date": "2026-05-01", "status": "issued",
                 "received_status": "pending", "quantity_yet_to_receive": 500,
                 "total": "250000"},
                {"purchaseorder_id": "po2", "vendor_id": "v2",
                 "date": "2026-06-15", "status": "issued",
                 "received_status": "pending", "total": "40000"}]


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
    # The business date the endpoints read, never the machine's — see
    # test_stock_on_state for what the two hours of disagreement look like.
    build(s, ORG, as_of=clock.today(load_for_org(s, ORG).timezone),
          thresholds_version=load_for_org(s, ORG).version)
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


def _run(client, email=OWNER, **body) -> dict:
    return client.post("/api/v1/insight/simulate", json=body,
                       headers=_hdr(client, email)).json()


# ── the scenarios are offered at all ────────────────────────────────────────
def test_both_scenarios_are_now_offered(client):
    body = client.get("/api/v1/insight/simulate/scenarios",
                      headers=_hdr(client, OWNER)).json()
    offered = {s["scenario"] for s in body["available"]}
    assert simulate.INVENTORY_CHANGE in offered
    assert simulate.SUPPLIER_DELAY in offered


def test_what_is_still_refused_is_narrower_than_it_was(client):
    """"We cannot do supplier delay" was true and is not any more. Leaving it
    up would stop somebody asking for what the platform can now answer."""
    body = client.get("/api/v1/insight/simulate/scenarios",
                      headers=_hdr(client, OWNER)).json()
    refused = {u["scenario"] for u in body["unavailable"]}
    assert "SUPPLIER_DELAY" not in refused
    assert "INVENTORY_CHANGE" not in refused
    # What remains is the item attribution and the missing promised date.
    assert refused == {"SUPPLIER_DELAY_BY_ITEM", "SUPPLIER_DELAY_AGAINST_PROMISE"}


# ── inventory change: arithmetic ────────────────────────────────────────────
def test_clearing_the_shelf_returns_cost_less_the_discount(client):
    """1,000 × 500 + 100 × 300 = ₹5,30,000 held. Half of it at 40% off
    returns ₹1,59,000 and writes off ₹1,06,000."""
    out = _run(client, scenario=simulate.INVENTORY_CHANGE,
               share_moved=0.5, discount=0.4)
    assert out["capital_held"] == 530000.0
    assert out["capital_released"] == pytest.approx(159000.0)
    assert out["written_off"] == pytest.approx(106000.0)
    assert out["capital_remaining"] == pytest.approx(265000.0)
    # Released + written off = what moved. If those three ever disagree the
    # screen is showing a discount that came from nowhere.
    assert (out["capital_released"] + out["written_off"]
            == pytest.approx(out["capital_held"] * 0.5))


def test_the_recurring_saving_is_the_carrying_rate_on_what_moves(client):
    out = _run(client, scenario=simulate.INVENTORY_CHANGE, share_moved=1.0)
    s = client.Maker()
    try:
        monthly_pct = load_for_org(s, ORG).carrying_cost_annual_pct / 12
    finally:
        s.close()
    assert out["monthly_carrying_saved"] == pytest.approx(530000.0 * monthly_pct)
    assert out["annual_carrying_saved"] == pytest.approx(
        out["monthly_carrying_saved"] * 12, rel=1e-6)


def test_an_uncosted_line_is_excluded_rather_than_valued_at_zero(client):
    """Nobody has priced it. That is not the same as it being free, and folding
    it in at zero understates the shelf by exactly the lines nobody looked at."""
    out = _run(client, scenario=simulate.INVENTORY_CHANGE)
    assert out["unpriced_lines"] == 1
    assert out["unpriced_note"]
    assert out["line_count"] == 2
    assert all(r["capital"] > 0 for r in out["lines"])


def test_the_assumptions_are_reported_as_the_users_not_absorbed(client):
    """The module's whole contract: mechanical computed, behavioural elicited.
    A headline that hid the discount would be a projection wearing arithmetic."""
    out = _run(client, scenario=simulate.INVENTORY_CHANGE,
               share_moved=0.3, discount=0.25)
    assert out["assumptions"]["share_moved"] == 0.3
    assert out["assumptions"]["discount"] == 0.25
    assert "yours" in out["assumptions"]["note"]


def test_a_band_narrows_to_exactly_what_the_stock_screen_calls_dead(client):
    """Same banding function, so a scenario run against dead stock covers the
    rows that screen calls dead — not a second, subtly different definition."""
    whole = _run(client, scenario=simulate.INVENTORY_CHANGE)
    dead = _run(client, scenario=simulate.INVENTORY_CHANGE, band="DEAD")
    assert dead["capital_held"] == 500000.0          # the reamer only
    assert dead["capital_held"] < whole["capital_held"]
    assert [r["label"] for r in dead["lines"]] == ["Reamer 12H7"]


def test_nothing_in_the_result_predicts_whether_it_will_sell(client):
    """The refusal that makes the rest trustworthy."""
    out = _run(client, scenario=simulate.INVENTORY_CHANGE)
    forbidden = {"probability", "expected_recovery", "likely", "forecast",
                 "projected", "will_sell"}
    assert forbidden.isdisjoint(out.keys())
    for row in out["lines"]:
        assert forbidden.isdisjoint(row.keys())


# ── supplier delay: arithmetic ──────────────────────────────────────────────
def test_delaying_orders_defers_the_cash_of_suppliers_with_terms(client):
    out = _run(client, scenario=simulate.SUPPLIER_DELAY, delay_days=30)
    assert out["committed_value"] == 290000.0        # 250,000 + 40,000
    # Only the supplier with terms can have its payable dated.
    assert out["cash_deferred"] == 250000.0
    assert out["cash_deferred_days"] == 30
    assert out["suppliers_without_terms"] == 1
    assert out["terms_note"]


def test_the_age_moves_by_exactly_the_delay(client):
    out = _run(client, scenario=simulate.SUPPLIER_DELAY, delay_days=45)
    row = next(r for r in out["suppliers"] if r["label"] == "Kennametal India")
    assert row["oldest_age_after"] == row["oldest_age_days"] + 45


def test_a_supplier_with_no_terms_is_counted_but_not_dated(client):
    """A payable with no term cannot be pushed out by a number nobody agreed."""
    out = _run(client, scenario=simulate.SUPPLIER_DELAY)
    row = next(r for r in out["suppliers"] if r["label"] == "Local Traders")
    assert row["open_value"] == 40000.0
    assert row["payment_terms_days"] is None
    assert row["payable_deferred_days"] is None


def test_the_delay_is_never_described_as_lateness(client):
    """This book records no promised dates. Age is a fact; lateness would be an
    invention, and the module says so in the result rather than only in a
    docstring nobody reading the screen will see."""
    out = _run(client, scenario=simulate.SUPPLIER_DELAY)
    note = out["assumptions"]["note"].lower()
    assert "how much later, not how much late" in note
    assert any(u["scenario"] == "SUPPLIER_DELAY_AGAINST_PROMISE"
               for u in out["unavailable"])


def test_the_result_says_it_cannot_attribute_a_delay_to_items(client):
    """Purchase orders are header grain. A reader who assumes otherwise would
    take "₹2,50,000 delayed" as "these items run short"."""
    out = _run(client, scenario=simulate.SUPPLIER_DELAY)
    by_item = next(u for u in out["unavailable"]
                   if u["scenario"] == "SUPPLIER_DELAY_BY_ITEM")
    assert "header grain" in by_item["why"]


# ── the guards ──────────────────────────────────────────────────────────────
def test_a_salesperson_cannot_run_either_scenario(client):
    """Both quantify from purchase cost. Manager and above, like the rest of
    the simulator."""
    for scenario in (simulate.INVENTORY_CHANGE, simulate.SUPPLIER_DELAY):
        r = client.post("/api/v1/insight/simulate", json={"scenario": scenario},
                        headers=_hdr(client, SALESPERSON))
        assert r.status_code == 403


def test_an_unfolded_book_says_so_rather_than_returning_zeroes(client):
    """A scenario reporting ₹0 released is indistinguishable from an empty
    shelf. Degrade, do not lie."""
    from app.domain import models

    s = client.Maker()
    try:
        s.query(models.BusinessState).delete()
        s.commit()
    finally:
        s.close()

    out = _run(client, scenario=simulate.INVENTORY_CHANGE)
    assert out["empty_reason"]
    assert "sync" in out["empty_reason"].lower()
    assert "capital_released" not in out


def test_the_scenarios_are_deterministic(client):
    """Same inputs, same answer, every time — the module's first line."""
    a = _run(client, scenario=simulate.INVENTORY_CHANGE, share_moved=0.4,
             discount=0.15)
    b = _run(client, scenario=simulate.INVENTORY_CHANGE, share_moved=0.4,
             discount=0.15)
    assert a == b
