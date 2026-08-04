"""Quote intelligence end to end: batching, role scoping, snapshots, outcomes.

Through the real database and the real endpoints. The cases that matter here
are the ones a pure-function test cannot reach: that a forty-line RFQ does not
issue forty round trips, that a salesperson's response has no cost in it
anywhere, that a snapshot cannot be edited after the fact, and that a decided
quote cannot be quietly reopened.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.domain import models
from app.routers import platform_auth, quote_intelligence
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_sanketh"
AS_OF = date(2026, 7, 1)

MANAGER = "m.rao@sanketh.in"
SALES = "r.nair@sanketh.in"


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


def _seed(s) -> None:
    """c1 buys p1 (eroding, cost 100 → 124); c2..c4 pay more. p2 has no cost."""
    for cid, name in (("c1", "Acme Engineering"), ("c2", "Beta Works"),
                      ("c3", "Gamma Tools"), ("c4", "Delta Precision")):
        s.add(models.Customer(customer_id=cid, organization_id=ORG, external_id=cid,
                              name=name, assigned_user_id="u_sales"))
    s.add(models.Product(product_id="p1", organization_id=ORG, external_id="ITEM-900",
                         name="CNMG 120408-MP insert", uom="pcs"))
    s.add(models.Product(product_id="p2", organization_id=ORG, external_id="ITEM-901",
                         name="HSS reamer 12mm", uom="pcs"))

    for ref, days, cost in (("B1", 400, "100"), ("B2", 120, "124")):
        s.add(models.CostRecord(
            organization_id=ORG, external_ref=f"{ref}:1", product_id="p1",
            date=_d(days), qty=Decimal("100"), unit_cost=Decimal(cost),
            source_ref={"record_type": "bill", "record_id": ref}))

    def sale(cust, pid, days, qty, price, ref):
        q, p = Decimal(str(qty)), Decimal(str(price))
        s.add(models.SalesTxn(
            organization_id=ORG, external_ref=f"{ref}:1", customer_id=cust,
            product_id=pid, date=_d(days), qty=q, unit_price=p, line_revenue=q * p,
            rate=p, discount_percent=Decimal("0"),
            source_ref={"record_type": "invoice", "record_id": ref}))

    for i, days in enumerate([600, 500, 400, 300, 200]):
        sale("c1", "p1", days, 100, 135, f"INV-H{i}")
    for i, days in enumerate([80, 50, 20]):
        sale("c1", "p1", days, 100, 139, f"INV-R{i}")
    for i, days in enumerate([80, 40]):
        sale("c2", "p1", days, 50, 165, f"INV-C2{i}")
        sale("c3", "p1", days, 50, 170, f"INV-C3{i}")
        sale("c4", "p1", days, 50, 168, f"INV-C4{i}")
    # p2: sold, never bought — a real and common gap
    sale("c1", "p2", 60, 5, 900, "INV-P2")
    s.flush()


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(quote_intelligence.router)

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
    tc.engine = engine
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _assess(c, email, lines, customer="Acme Engineering", quote_id=None):
    body = {"customer": customer, "lines": lines, "as_of": AS_OF.isoformat()}
    if quote_id:
        body["quote_id"] = quote_id
    r = c.post("/api/v1/quote-intelligence/assess", json=body, headers=_hdr(c, email))
    assert r.status_code == 200, r.text
    return r.json()


def _line(line_id, product="ITEM-900", qty=100, price=139.0):
    return {"line_id": line_id, "product": product, "qty": qty,
            "proposed_price": price}


# ── the assessment ──────────────────────────────────────────────────────────
def test_a_quote_line_is_assessed_against_the_customers_own_history(client):
    data = _assess(client, MANAGER, [_line("L1", price=120.0)])
    line = data["lines"][0]

    assert data["customer"]["resolved"] and data["customer"]["customer_id"] == "c1"
    codes = {r["code"] for r in line["references"]}
    assert "LAST_PRICE_PAID" in codes and "BAND_PRICE" in codes
    last = next(r for r in line["references"] if r["code"] == "LAST_PRICE_PAID")
    assert last["value"] == 139.0

    assert "BELOW_LAST_PRICE" in {e["code"] for e in line["exceptions"]}


def test_the_quantity_band_is_part_of_the_assessment(client):
    small = _assess(client, MANAGER, [_line("L1", qty=2)])["lines"][0]
    bulk = _assess(client, MANAGER, [_line("L1", qty=500)])["lines"][0]
    assert small["quantity_band"]["label"] == "2–10"
    assert bulk["quantity_band"]["label"] == "201+"
    # c1's history is all 100-unit orders, so only the matching band has a
    # band reference — a 500-unit line has no precedent to be measured against
    assert "BAND_PRICE" in {r["code"] for r in
                            _assess(client, MANAGER, [_line("L1", qty=100)])
                            ["lines"][0]["references"]}
    assert "BAND_PRICE" not in {r["code"] for r in bulk["references"]}


def test_cost_is_taken_as_of_today_so_the_margin_reflects_the_current_bill(client):
    line = _assess(client, MANAGER, [_line("L1", price=139.0)])["lines"][0]
    econ = line["economics"]
    assert econ["unit_cost"] == 124.0, "the later bill, not the ₹100 one"
    assert abs(econ["margin"] - 0.1079) < 1e-3
    assert econ["line_revenue"] == 13900.0 and econ["cogs"] == 12400.0


def test_a_price_below_the_approval_floor_is_flagged_and_needs_approval(client):
    line = _assess(client, MANAGER, [_line("L1", price=130.0)])["lines"][0]
    codes = {e["code"] for e in line["exceptions"]}
    assert "BELOW_MIN_MARGIN" in codes
    assert line["requires_approval"] and line["blocking"]
    assert line["worst_severity"] == "CRITICAL"


def test_an_item_we_have_never_bought_reports_the_gap_instead_of_a_margin(client):
    line = _assess(client, MANAGER,
                   [_line("L1", product="ITEM-901", qty=5, price=900.0)])["lines"][0]
    assert "NO_COST_BASIS" in {e["code"] for e in line["exceptions"]}
    assert line["economics"]["margin"] is None
    assert line["economics"]["unit_cost"] is None


def test_an_unresolved_item_is_reported_not_dropped(client):
    data = _assess(client, MANAGER, [_line("L1", product="WIDGET-NOT-REAL")])
    line = data["lines"][0]
    assert line["resolved"] is False and line["product_id"] is None
    assert data["summary"]["lines_unresolved"] == 1
    assert "NEW_RELATIONSHIP" in {e["code"] for e in line["exceptions"]}


def test_an_unknown_customer_still_returns_an_assessment_per_line(client):
    data = _assess(client, MANAGER, [_line("L1")], customer="Nobody Ltd")
    assert data["customer"]["resolved"] is False
    line = data["lines"][0]
    # no relationship history, but today's cost is still known
    assert line["economics"]["unit_cost"] == 124.0
    assert "NEW_RELATIONSHIP" in {e["code"] for e in line["exceptions"]}


def test_the_drilldown_points_at_the_existing_customer_item_analysis(client):
    line = _assess(client, MANAGER, [_line("L1")])["lines"][0]
    assert line["drilldown"] == {"customer_id": "c1", "product_id": "p1"}


# ── role scoping ────────────────────────────────────────────────────────────
def test_a_salesperson_receives_no_cost_or_margin_anywhere_in_the_response(client):
    import json
    data = _assess(client, SALES, [_line("L1", price=130.0)])
    line = data["lines"][0]

    assert "economics" not in line and "position" not in line
    blob = json.dumps(data)
    assert "124" not in blob, "the unit cost must not appear at all"
    for ref in line["references"]:
        assert ref["data_class"] == "OPERATIONAL"
    assert {"Approval floor", "Target margin price"} <= set(line["references_withheld"])


def test_a_salesperson_still_learns_that_approval_is_needed(client):
    """Withholding cost must not mean withholding the control. They can see
    the line is under the floor; they cannot see how far under."""
    line = _assess(client, SALES, [_line("L1", price=130.0)])["lines"][0]
    fired = next(e for e in line["exceptions"] if e["code"] == "BELOW_MIN_MARGIN")
    assert line["requires_approval"] is True
    assert "manager_detail" not in fired, "the reasoning names cost — absent, not masked"
    assert fired["impact_amount"] is None, "the gap to the floor reveals cost"
    assert fired["detail"], "but the salesperson is told, in plain words"


def test_a_manager_receives_the_numbers_the_salesperson_does_not(client):
    line = _assess(client, MANAGER, [_line("L1", price=130.0)])["lines"][0]
    fired = next(e for e in line["exceptions"] if e["code"] == "BELOW_MIN_MARGIN")
    assert fired["manager_detail"] and "%" in fired["manager_detail"]
    assert fired["impact_amount"] is not None
    assert line["position"]["erosion_kind"] == "COST_DRIVEN"


def test_the_peer_median_price_is_not_shown_to_a_salesperson(client):
    """Another customer's price is competitive information, not this line's."""
    sales = _assess(client, SALES, [_line("L1", price=120.0)])["lines"][0]
    mgmt = _assess(client, MANAGER, [_line("L1", price=120.0)])["lines"][0]
    assert "PEER_MEDIAN_PRICE" in {r["code"] for r in mgmt["references"]}
    assert "PEER_MEDIAN_PRICE" not in {r["code"] for r in sales["references"]}


def test_the_threshold_endpoint_withholds_margin_policy_from_a_salesperson(client):
    s = client.get("/api/v1/quote-intelligence/thresholds",
                   headers=_hdr(client, SALES)).json()
    m = client.get("/api/v1/quote-intelligence/thresholds",
                   headers=_hdr(client, MANAGER)).json()
    assert "min_margin" not in s and "quantity_band_edges" in s
    assert m["min_margin"] == 0.12 and m["margin_floor"] == 0.15


# ── batching ────────────────────────────────────────────────────────────────
def test_a_forty_line_rfq_does_not_issue_forty_round_trips(client):
    """The N+1 this service exists to avoid. Query count must be flat in the
    number of lines, not proportional to it."""
    counts: list[int] = []

    @event.listens_for(client.engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        counts.append(1)

    _assess(client, MANAGER, [_line(f"L{i}") for i in range(1, 4)])
    three = len(counts)
    counts.clear()
    _assess(client, MANAGER, [_line(f"L{i}") for i in range(1, 41)])
    forty = len(counts)

    event.remove(client.engine, "before_cursor_execute", _count)
    assert forty <= three + 2, (
        f"{three} queries for 3 lines but {forty} for 40 — this is N+1")


def test_too_many_lines_is_refused_rather_than_silently_truncated(client):
    r = client.post("/api/v1/quote-intelligence/assess",
                    json={"customer": "Acme Engineering",
                          "lines": [_line(f"L{i}") for i in range(300)]},
                    headers=_hdr(client, MANAGER))
    assert r.status_code == 400


# ── immutable snapshots ─────────────────────────────────────────────────────
def _snapshot(c, email, lines, quote_id="q1", customer="Acme Engineering"):
    r = c.post("/api/v1/quote-intelligence/snapshot",
               json={"quote_id": quote_id, "customer": customer, "lines": lines,
                     "as_of": AS_OF.isoformat()},
               headers=_hdr(c, email))
    assert r.status_code == 201, r.text
    return r.json()


def test_a_snapshot_freezes_the_facts_the_decision_was_made_against(client):
    out = _snapshot(client, MANAGER, [_line("L1", price=130.0)])
    snap = out["snapshots"][0]

    assert snap["quoted_unit_price"] == 130.0
    assert snap["economics"]["unit_cost"] == 124.0
    assert snap["quantity_band"] == "51–200"
    assert "BELOW_MIN_MARGIN" in {e["code"] for e in snap["exceptions"]}
    assert snap["thresholds_version"].startswith("ci_")
    assert snap["engine_version"] == "qi-1"
    assert snap["references"], "the comparisons are frozen too, not just the verdict"


def test_the_server_recomputes_the_snapshot_rather_than_trusting_the_client(client):
    """A snapshot whose numbers came from the browser records what the browser
    claimed, which is precisely what an audit trail must not do."""
    r = client.post("/api/v1/quote-intelligence/snapshot",
                    json={"quote_id": "q9", "customer": "Acme Engineering",
                          "as_of": AS_OF.isoformat(),
                          "lines": [{"line_id": "L1", "product": "ITEM-900",
                                     "qty": 100, "proposed_price": 130.0,
                                     "unit_cost": 1.0, "margin": 0.99}]},
                    headers=_hdr(client, MANAGER))
    assert r.status_code == 201
    assert r.json()["snapshots"][0]["economics"]["unit_cost"] == 124.0


def test_repricing_a_line_appends_rather_than_rewriting_history(client):
    _snapshot(client, MANAGER, [_line("L1", price=130.0)])
    _snapshot(client, MANAGER, [_line("L1", price=145.0)])

    r = client.get("/api/v1/quote-intelligence/quotes/q1",
                   headers=_hdr(client, MANAGER)).json()
    prices = [d["quoted_unit_price"] for d in r["decisions"]]
    assert prices == [130.0, 145.0], "the negotiation must stay legible"


def test_an_override_reason_is_captured_with_the_rules_it_overrode(client):
    out = _snapshot(client, SALES, [dict(_line("L1", price=130.0),
                                         override_reason="Volume commitment for Q3",
                                         override_reason_code="VOLUME_COMMITMENT")],
                    quote_id="q2")
    snap = out["snapshots"][0]
    assert snap["overridden"] is True
    assert snap["override_reason"] == "Volume commitment for Q3"
    assert snap["override_reason_code"] == "VOLUME_COMMITMENT"
    assert "BELOW_MIN_MARGIN" in snap["overridden_exception_codes"]


def test_a_stored_margin_is_still_restricted_when_read_back(client):
    _snapshot(client, MANAGER, [_line("L1", price=130.0)], quote_id="q3")
    sales = client.get("/api/v1/quote-intelligence/quotes/q3",
                       headers=_hdr(client, SALES)).json()
    assert "economics" not in sales["decisions"][0]
    assert all(r["data_class"] == "OPERATIONAL"
               for r in sales["decisions"][0]["references"])


def test_snapshots_are_scoped_to_the_organization(client):
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q4")
    s = client.Maker()
    row = s.query(models.QuoteDecision).filter_by(quote_id="q4").one()
    assert row.organization_id == ORG
    s.close()


# ── the outcome path ────────────────────────────────────────────────────────
def _outcome(c, email, status, quote_id="q1", note=None):
    return c.post("/api/v1/quote-intelligence/outcome",
                  json={"quote_id": quote_id, "status": status,
                        "customer": "Acme Engineering", "note": note},
                  headers=_hdr(c, email))


def test_recording_a_quote_starts_it_as_a_draft(client):
    out = _snapshot(client, MANAGER, [_line("L1")], quote_id="q5")
    r = client.get("/api/v1/quote-intelligence/quotes/q5",
                   headers=_hdr(client, MANAGER)).json()
    assert r["outcome"]["status"] == "DRAFT"
    assert set(r["outcome"]["allowed_next"]) == {"SENT", "LOST"}


def test_a_quote_moves_draft_to_sent_to_won(client):
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q6")
    assert _outcome(client, MANAGER, "SENT", "q6").json()["status"] == "SENT"
    won = _outcome(client, MANAGER, "WON", "q6", note="PO 4471").json()
    assert won["status"] == "WON" and won["note"] == "PO 4471"
    assert won["sent_at"] and won["decided_at"]
    assert won["allowed_next"] == []


def test_a_decided_quote_cannot_be_quietly_reopened(client):
    """Reopening would rewrite history a margin analysis has already counted."""
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q7")
    _outcome(client, MANAGER, "SENT", "q7")
    _outcome(client, MANAGER, "LOST", "q7")
    r = _outcome(client, MANAGER, "WON", "q7")
    assert r.status_code == 409
    assert "LOST" in r.json()["detail"]


def test_a_quote_cannot_skip_straight_from_draft_to_won(client):
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q8")
    assert _outcome(client, MANAGER, "WON", "q8").status_code == 409


def test_a_revised_quote_may_be_sent_again(client):
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q10")
    _outcome(client, MANAGER, "SENT", "q10")
    assert _outcome(client, MANAGER, "SENT", "q10").status_code == 200


def test_the_outcome_endpoint_requires_authentication(client):
    r = client.post("/api/v1/quote-intelligence/outcome",
                    json={"quote_id": "q1", "status": "SENT"})
    assert r.status_code in (401, 403)
