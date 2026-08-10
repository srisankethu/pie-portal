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
        # `usr_sales` is the id `app.seed` gives r.nair, and every other suite
        # uses it. This one said "u_sales", which matched no user — harmless
        # while `/assess` ignored assignment, and the moment it stopped doing so
        # it would have turned every salesperson case in this file green by
        # resolving no customer at all. Absence of evidence is not a pass.
        s.add(models.Customer(customer_id=cid, organization_id=ORG, external_id=cid,
                              name=name, assigned_user_id="usr_sales"))
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
    """Withholding cost must not mean withholding the control.

    This docstring used to read "they can see the line is under the floor; they
    cannot see how far under". That was true of one response and false of two —
    which rule fired is a predicate on cost, and the caller supplies the price,
    so the boundary can be walked. The named rule is withheld now and a fixed
    substitute carries the control; see ``quote_service._project_exceptions``.
    """
    line = _assess(client, SALES, [_line("L1", price=130.0)])["lines"][0]
    codes = {e["code"] for e in line["exceptions"]}
    assert "BELOW_MIN_MARGIN" not in codes, "naming the rule names its boundary"
    assert "NEGATIVE_MARGIN" not in codes

    fired = next(e for e in line["exceptions"] if e["code"] == "APPROVAL_REQUIRED")
    assert line["requires_approval"] is True
    assert line["blocking"] is True, "the send gate still stops this line"
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


# ── inference across requests ───────────────────────────────────────────────
#
# The tests above ask what one response contains. These ask what a sequence of
# them reveals, which is the question `filterCounts.MFLOOR` failed and the one
# every field-level assertion here passed while the endpoint gave up cost.
def _sales_boundaries(client, prices) -> set[float]:
    """Every price at which the salesperson's view of the line changes.

    The response is walked as the attacker would walk it: assess the same line
    at many prices and watch for the value where the answer flips. Whatever is
    in this set is a number a salesperson can recover to any precision they care
    to spend requests on.
    """
    seen, boundaries = None, set()
    for price in prices:
        line = _assess(client, SALES, [_line("L1", price=price)])["lines"][0]
        # Everything a salesperson could read off this line, minus the price
        # they typed and the figures that move with it by construction.
        state = (tuple(sorted(e["code"] for e in line["exceptions"])),
                 line["worst_severity"], line["requires_approval"],
                 line["blocking"])
        if seen is not None and state != seen:
            boundaries.add(price)
        seen = state
    return boundaries


def test_a_salesperson_cannot_walk_the_price_to_recover_cost(client):
    """The unit cost is 124 and the approval floor is 124/0.88 = 140.91.

    Sweeping the price in one-rupee steps, a salesperson's view must not change
    at 124. It used to: `NEGATIVE_MARGIN` fired at `price <= unit_cost` with no
    policy multiplier in the comparison, so the step where it appeared *was* the
    purchase price, readable without knowing any threshold.
    """
    boundaries = _sales_boundaries(client, [float(p) for p in range(118, 150)])
    assert not any(123 <= b <= 126 for b in boundaries), (
        f"the response changes at {sorted(boundaries)} — a step at the unit "
        f"cost hands it over")


def test_the_manager_view_still_moves_at_cost(client):
    """The counterpart, so the test above cannot pass by flattening the engine.

    A manager may see cost, so their view *should* change at 124 — if it stopped
    doing so, the exception rules would have been broken rather than scoped.
    """
    below = _assess(client, MANAGER, [_line("L1", price=120.0)])["lines"][0]
    above = _assess(client, MANAGER, [_line("L1", price=130.0)])["lines"][0]
    assert "NEGATIVE_MARGIN" in {e["code"] for e in below["exceptions"]}
    assert "BELOW_MIN_MARGIN" in {e["code"] for e in above["exceptions"]}


def test_a_salesperson_cannot_bracket_the_peer_median(client):
    """`PEER_MEDIAN_PRICE` is RESTRICTED and stripped from `references`.

    The below/above pair bracketed it from both sides, and the tolerance that
    sets the bracket width is published to every role by `/thresholds` — so the
    two together inverted to the median exactly.
    """
    codes = set()
    for price in (80.0, 120.0, 160.0, 200.0, 260.0):
        line = _assess(client, SALES, [_line("L1", price=price)])["lines"][0]
        codes |= {e["code"] for e in line["exceptions"]}
    assert not (codes & {"BELOW_PEER_MEDIAN", "ABOVE_PEER_MEDIAN"})


def test_the_quote_summary_withholds_the_counts_derived_from_cost(client):
    """A count over lines the caller priced is a sharper oracle than the
    per-line flag: two hundred probes in one request turn it into a rank.

    Omitted rather than zeroed, for the reason the MFLOOR fix in `store.py`
    gives — a zero still answers the question.
    """
    sales = _assess(client, SALES, [_line("L1", price=130.0)])["summary"]
    mgmt = _assess(client, MANAGER, [_line("L1", price=130.0)])["summary"]
    assert "critical" not in sales and "requires_approval" not in sales
    assert mgmt["critical"] >= 1 and mgmt["requires_approval"] >= 1
    assert sales["lines_assessed"] == 1, "the operational counts stay"


def test_one_product_cannot_be_priced_many_ways_in_a_single_request(client):
    """The batch is what made the walk two round trips instead of fifty."""
    body = {"customer": "Acme Engineering", "as_of": AS_OF.isoformat(),
            "lines": [_line(f"L{i}", price=100.0 + i) for i in range(12)]}
    r = client.post("/api/v1/quote-intelligence/assess", json=body,
                    headers=_hdr(client, SALES))
    assert r.status_code == 400
    assert "different prices" in r.json()["detail"]


def test_an_assessment_date_far_from_today_is_refused(client):
    """Repeating the walk per date reads the item's cost history, which is
    worth more to a competitor than today's cost is."""
    body = {"customer": "Acme Engineering", "lines": [_line("L1")],
            "as_of": (date.today() - timedelta(days=400)).isoformat()}
    r = client.post("/api/v1/quote-intelligence/assess", json=body,
                    headers=_hdr(client, SALES))
    assert r.status_code == 422, r.text


def test_a_salesperson_cannot_assess_a_customer_they_are_not_assigned(client):
    """The scope rule `/accounts` and the insight timeline both apply. An
    out-of-scope account must be indistinguishable from one we have never seen,
    so this degrades to unresolved rather than answering 403."""
    with client.Maker() as s:
        s.add(models.Customer(customer_id="c9", organization_id=ORG, external_id="c9",
                              name="Zenith Machining", assigned_user_id="u_other"))
        s.commit()
    out = _assess(client, SALES, [_line("L1")], customer="Zenith Machining")
    assert out["customer"]["resolved"] is False
    assert out["customer"]["customer_id"] is None
    unknown = _assess(client, SALES, [_line("L1")], customer="No Such Company")
    assert out["customer"]["resolved"] == unknown["customer"]["resolved"]


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

    # The audit records the real rule; the salesperson's own read-back does not
    # name it. Both halves matter: an audit trail that stored the substitute
    # would have lost what was actually overridden, and a read-back that named
    # the rule would undo the redaction two fields above it.
    assert "BELOW_MIN_MARGIN" not in snap["overridden_exception_codes"]
    audit = client.get("/api/v1/quote-intelligence/quotes/q2",
                       headers=_hdr(client, MANAGER)).json()
    assert "BELOW_MIN_MARGIN" in audit["decisions"][0]["overridden_exception_codes"]


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


@pytest.mark.requires_pie
def test_a_decision_records_which_catalogue_resolved_it(client):
    """The parser's ruleset checksum, alongside the thresholds version.

    The only test in this module that needs the engine: ``catalog_version`` is
    read off the loaded catalogue, and with no engine it is empty on both sides
    of the comparison, which would pass without asserting anything.

    Both answer "why does this row say what it says" for different halves of
    the answer: thresholds decide the price, the catalogue decides the product.
    A quote whose product resolution cannot be reproduced is as unexplainable
    as one whose margin cannot be.
    """
    from app.pie_service import pie_service

    _snapshot(client, MANAGER, [_line("L1")], quote_id="q-catalog")
    s = client.Maker()
    row = s.query(models.QuoteDecision).filter_by(quote_id="q-catalog").one()

    # It is the parser's own checksum, not the quote-intelligence version —
    # those are different concepts and used to be conflated in one column.
    assert row.catalog_version == pie_service.catalog_version
    assert row.catalog_version != row.engine_version
    assert row.thresholds_version, "thresholds provenance must still be stamped"
    s.close()


# ── the outcome path ────────────────────────────────────────────────────────
def _outcome(c, email, status, quote_id="q1", note=None,
             loss_reason="PRICE", lost_to=None):
    """A LOST call carries a reason by default so the lifecycle tests below
    stay about the lifecycle. The reason rule has its own tests."""
    body = {"quote_id": quote_id, "status": status,
            "customer": "Acme Engineering", "note": note}
    if status == "LOST" and loss_reason is not None:
        body["loss_reason"] = loss_reason
    if lost_to is not None:
        body["lost_to"] = lost_to
    return c.post("/api/v1/quote-intelligence/outcome", json=body,
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


# ── product resolution: the length floor on containment matching ─────────────
def test_a_four_character_code_still_matches_by_containment(client):
    """Four normalized characters is the shortest fragment allowed to match a
    product by containment, and "shortest allowed" means four resolves.

    The floor exists because a two- or three-character fragment is inside half
    the catalogue, so it would resolve to whichever row happened to be closest in
    length — a confident wrong product on a quote. Both sides are asserted here:
    raise the floor by one and the first case silently stops resolving; remove it
    and the second silently starts.
    """
    from app.commercial.quote_service import _resolve_products

    s = client.Maker()
    try:
        # "CNMG" normalizes to exactly 4 characters and is a fragment of
        # "CNMG 120408-MP insert".
        out = _resolve_products(s, ORG, ["CNMG"])
        assert out["CNMG"] is not None, "a four-character fragment must resolve"
        assert out["CNMG"].product_id == "p1"

        # Three characters is below the floor — and it is a fragment of the same
        # product, so only the floor is stopping it.
        assert _resolve_products(s, ORG, ["CNM"])["CNM"] is None
    finally:
        s.close()


# ── why it was lost ─────────────────────────────────────────────────────────
#
# A bare LOST cannot say whether another supplier took the order or the
# requirement went away. Those are opposite facts about what this customer
# spends elsewhere, and the person recording the loss is the one person who
# knows — so the answer is taken then, not defaulted and reconstructed later.

def test_a_loss_cannot_be_recorded_without_saying_which_kind_it_was(client):
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q20")
    _outcome(client, MANAGER, "SENT", "q20")
    r = _outcome(client, MANAGER, "LOST", "q20", loss_reason=None)
    # 422, not 409: the transition is legal and one required field is absent.
    assert r.status_code == 422
    # And the message names the choices, so the form can be filled from it.
    assert "PRICE" in r.json()["detail"]
    # The quote is untouched — a refused loss must not half-decide it.
    audit = client.get("/api/v1/quote-intelligence/quotes/q20",
                       headers=_hdr(client, MANAGER)).json()
    assert audit["outcome"]["status"] == "SENT"


def test_the_not_recorded_bucket_cannot_be_chosen(client):
    """It lives outside the enum, so it is unreachable rather than rejected —
    Pydantic refuses the value before the service is even reached."""
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q21")
    _outcome(client, MANAGER, "SENT", "q21")
    r = _outcome(client, MANAGER, "LOST", "q21", loss_reason="NOT_RECORDED")
    assert r.status_code == 422


def test_a_recorded_loss_keeps_the_reason_and_the_winner(client):
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q22")
    _outcome(client, MANAGER, "SENT", "q22")
    lost = _outcome(client, MANAGER, "LOST", "q22",
                    loss_reason="DELIVERY", lost_to="Bright Tools").json()
    assert lost["loss_reason"] == "DELIVERY"
    assert lost["lost_to"] == "Bright Tools"


def test_a_won_quote_carries_no_loss_reason(client):
    """Set only on the LOST edge, so a won quote cannot keep a stale one from
    an earlier attempt at the form."""
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q23")
    _outcome(client, MANAGER, "SENT", "q23")
    won = _outcome(client, MANAGER, "WON", "q23",
                   loss_reason="PRICE").json()
    assert won["status"] == "WON"
    assert won["loss_reason"] is None


def test_the_selectable_reasons_travel_with_the_outcome(client):
    """The form should not hold its own copy of the list."""
    _snapshot(client, MANAGER, [_line("L1")], quote_id="q24")
    out = client.get("/api/v1/quote-intelligence/quotes/q24",
                     headers=_hdr(client, MANAGER)).json()["outcome"]
    assert "PRICE" in out["loss_reasons"]
    assert "NOT_RECORDED" not in out["loss_reasons"]
