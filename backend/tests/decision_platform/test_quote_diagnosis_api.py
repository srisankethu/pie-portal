"""The diagnosis endpoints, through the real database and the real routes.

The case that matters here and cannot be reached by a pure-function test: a
salesperson's response has no cost anywhere in it — not withheld, not masked,
absent — and a manager's does. Everything else in this file is scaffolding for
that one, plus the two writes: a dismissal, and the refusal of an unsupported
reason.

The withholding test sweeps the whole serialised response rather than asserting
on named fields, because every field-level assertion in this repository's
previous leak passed while the endpoint gave up cost.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import cost_sweep
import dbsupport
from app.db import get_session
from app.domain import models
from app.routers import platform_auth, quote_diagnosis
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
AS_OF = date.today()

MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

#: What this customer has paid, and what the item has cost. Kept far apart so a
#: cost leaking into a salesperson's payload is unmistakable rather than a
#: coincidence of two similar numbers.
SELLING_PRICE = 1000
PURCHASE_COST = 371


def _seed(s) -> None:
    s.add(models.Customer(customer_id="c1", organization_id=ORG, external_id="c1",
                          name="Acme Engineering", assigned_user_id="usr_sales"))
    s.add(models.Product(product_id="p1", organization_id=ORG,
                         external_id="ITEM-900", name="CNMG 120408-MP insert",
                         uom="pcs", source_item_category="Turning"))
    for i in range(12):
        day = AS_OF - timedelta(days=300 - 7 * i)
        s.add(models.SalesTxn(
            sales_txn_id=f"stx_{i}", organization_id=ORG,
            external_ref=f"INV-{i}:1", customer_id="c1", product_id="p1",
            date=day, qty=Decimal("10"), unit_price=Decimal(SELLING_PRICE),
            line_revenue=Decimal(SELLING_PRICE * 10),
            source_recorded_at=_stamp(day),
            source_ref={"record_type": "invoice"}))
    for i in range(3):
        day = AS_OF - timedelta(days=200 - 30 * i)
        s.add(models.CostRecord(
            cost_record_id=f"cst_{i}", organization_id=ORG,
            external_ref=f"BILL-{i}:1", product_id="p1", date=day,
            qty=Decimal("10"), unit_cost=Decimal(PURCHASE_COST),
            rate=Decimal(PURCHASE_COST), source_recorded_at=_stamp(day),
            source_ref={"record_type": "bill"}))
    s.flush()


def _stamp(day: date):
    from datetime import datetime, timezone
    return datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(quote_diagnosis.router)

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
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _assess(c, email, *, price=850.0, quote_id="q1", record=True):
    r = c.post("/api/v1/quote-diagnosis/assess", headers=_hdr(c, email), json={
        "quote_id": quote_id, "record": record,
        "lines": [{"line_id": "L1", "product_id": "p1", "customer_id": "c1",
                   "qty": 10, "quoted_unit_price": price}]})
    assert r.status_code == 200, r.text
    return r.json()


# ── the role split ───────────────────────────────────────────────────────────

def test_a_salespersons_response_has_no_cost_in_it_anywhere(client):
    """The shape of test this class needs.

    Not a list of fields that must be absent — a sweep of the whole payload.
    Every field-level assertion in this repository's previous leak passed while
    the endpoint gave up cost, because the leak was in a field nobody had
    thought to name.

    The sweep lives in `cost_sweep` rather than here because a second endpoint
    projects the same diagnosis, and two definitions of "a leak" is one more
    than this can afford. Its docstring has why opaque ids come out first: this
    assertion failed on CI having found nothing, because `371` is a legal hex
    substring and landed inside a uuid.
    """
    cost_sweep.assert_no_cost(_assess(client, SALES), cost=PURCHASE_COST)


def test_a_manager_receives_the_economics(client):
    """The other half. A withholding test that passes because the endpoint
    returns nothing useful to anyone is not evidence of withholding."""
    line = _assess(client, MANAGER)["lines"][0]

    assert line["view"] == "OWNER"
    assert line["cost_baseline"]["expected_cost"] == float(PURCHASE_COST)
    assert "opportunity_detail" in line
    assert any("Expected cost" in ln for ln in line["lines"])


def test_the_salesperson_gets_a_card_and_the_manager_gets_a_report(client):
    sales_line = _assess(client, SALES)["lines"][0]
    owner_line = _assess(client, MANAGER)["lines"][0]

    assert sales_line["view"] == "OPERATIONS"
    assert sales_line["headline"] == "Below this customer's historical pricing"
    assert sales_line["qualification"].startswith("Historical prices may include")
    assert sales_line["actions"] == ["REVIEW_PRICE", "DISMISS"]
    assert owner_line["view"] == "OWNER"
    assert "potential margin opportunity" in owner_line["opportunity"]


def test_a_stored_diagnosis_is_read_back_under_the_same_role_rules(client):
    """The second projection site, and therefore the one that drifts.

    ``/assess`` projects a freshly computed object; ``/quote/{id}`` projects a
    stored row. Two computations of one withholding rule, so the second needs
    its own test rather than the first's.
    """
    _assess(client, MANAGER)

    r = client.get("/api/v1/quote-diagnosis/quote/q1", headers=_hdr(client, SALES))
    assert r.status_code == 200, r.text

    # The shared definition of a leak rather than a second one written here:
    # this path projects the same diagnosis and must not come to disagree with
    # `/assess` about what may not appear.
    cost_sweep.assert_no_cost(r.json(), cost=PURCHASE_COST)

    owner = client.get("/api/v1/quote-diagnosis/quote/q1",
                       headers=_hdr(client, MANAGER)).json()
    assert owner["lines"][0]["cost_baseline"]["expected_cost"] == float(PURCHASE_COST)
    assert owner["lines"][0]["evidence_hash"].startswith("qdh1:")


def test_the_evaluation_is_refused_to_a_salesperson(client):
    """Counts, not secrets — but a scoreboard of which warnings a salesperson
    dismissed, read by that salesperson, changes what gets dismissed."""
    assert client.get("/api/v1/quote-diagnosis/evaluation",
                      headers=_hdr(client, SALES)).status_code == 403
    assert client.get("/api/v1/quote-diagnosis/evaluation",
                      headers=_hdr(client, MANAGER)).status_code == 200


# ── walking the price ────────────────────────────────────────────────────────

def test_a_salesperson_cannot_walk_the_price_to_recover_cost(client):
    """Sweep the quoted price across the cost and assert the salesperson's
    payload does not change at it.

    The engine's codes turn on the *price band* — prices this customer has
    already seen — so there is no boundary at cost to find. This test is what
    says so rather than the argument that says so.
    """
    seen, echoed = {}, {}
    for price in range(PURCHASE_COST - 3, PURCHASE_COST + 4):
        line = _assess(client, SALES, price=float(price), record=False)["lines"][0]
        # The whole line, not three named fields. Watching only the fields
        # somebody remembered is this repository's original leak with a loop
        # around it, and `cost_sweep.answer` is where that list is kept so both
        # endpoints walk the same one.
        seen[price] = cost_sweep.answer(line)
        echoed[price] = line["quoted"]

    # Every probe either side of cost answers identically; nothing the server
    # decided moves as the price crosses it.
    assert len(set(seen.values())) == 1, seen
    # And the one field that did move is the caller's own number, spelled back —
    # not a boundary hiding behind an exclusion.
    assert all(str(price) in text for price, text in echoed.items()), echoed


# ── driver attribution: owner only, on both projections ──────────────────────

def _enough_purchases(client, extra: int = 2) -> None:
    """Bring the cost baseline up to a grade the attribution will speak from.

    The fixture seeds three purchases, and ``diagnosis_moderate_min_comparables``
    is four — below MODERATE on either side the split is refused rather than
    asserted, which is correct and is tested on its own. Same item, same price,
    so nothing but the count moves.
    """
    with client.Maker() as s:
        for i in range(extra):
            day = AS_OF - timedelta(days=120 - 10 * i)
            s.add(models.CostRecord(
                cost_record_id=f"cst_x{i}", organization_id=ORG,
                external_ref=f"BILL-X{i}:1", product_id="p1", date=day,
                qty=Decimal("10"), unit_cost=Decimal(PURCHASE_COST),
                rate=Decimal(PURCHASE_COST), source_recorded_at=_stamp(day),
                source_ref={"record_type": "bill"}))
        s.commit()


def test_the_owner_is_given_the_split_and_the_desk_has_no_trace_of_it(client):
    """Cost-derived throughout, so it goes on one branch and not the other.

    The desk half is not "the same block with the numbers removed" — the
    salesperson's projection is built from a type with no field to put a driver
    in, so there is nothing here to forget to remove. This asserts both halves,
    because a withholding test that passes because the endpoint returns nothing
    to anyone is not evidence of withholding.
    """
    _enough_purchases(client)
    owner = _assess(client, MANAGER, price=850.0, quote_id="q-attr")["lines"][0]
    desk = _assess(client, SALES, price=850.0, quote_id="q-attr2")["lines"][0]

    block = owner["attribution"]
    assert block["renders"] is True
    assert [d["code"] for d in block["drivers"]] == ["PRICE_POSITION_EFFECT",
                                                     "COST_LEVEL_EFFECT"]
    # Both factors, including the one that did not move. This customer has paid
    # 1000 twelve times and is being quoted 850 at a cost that never changed, so
    # the price is the whole of it — and saying so is the point.
    assert block["headline"].endswith(
        "price -6.55 pp, cost level unchanged.")
    assert block["note"].startswith("PRICE_THEN_COST:")
    # Every figure arrives formatted: the front end may not format money or
    # compute a number, so an `effect` is a string and never a pair of numbers.
    assert block["drivers"][0]["effect"] == "-6.55 pp (-₹150 per unit)"
    assert block["drivers"][0]["strength_word"] == "Strong"

    assert "attribution" not in desk
    cost_sweep.assert_no_cost({"lines": [desk]}, cost=PURCHASE_COST)


def test_a_stored_diagnosis_says_it_has_no_split_rather_than_showing_none(client):
    """The second projection site, and the one where an absence would lie.

    `quote_diagnoses` has no attribution column, so a row read back cannot
    answer the question. A manager who simply found no `attribution` key would
    read that as "the price and the cost both behaved" — the benign default
    CLAUDE.md §1 is about. The block is published, carrying the refusal.
    """
    _assess(client, MANAGER, price=850.0)

    line = client.get("/api/v1/quote-diagnosis/quote/q1",
                      headers=_hdr(client, MANAGER)).json()["lines"][0]

    block = line["attribution"]
    assert block["drivers"] == []
    assert block["headline"] == ""
    assert block["note"].startswith("NOT_ON_STORED_RECORD:")
    assert "Re-assess this line" in block["note"]
    # And it is as visible as the card it sits in.
    assert block["renders"] == line["renders"]


def test_a_line_with_no_purchase_history_is_told_so_rather_than_shown_nothing(
        client):
    """A product the ledger has never priced. The split refuses and names what
    is missing; it does not report a cost effect of zero."""
    r = client.post("/api/v1/quote-diagnosis/assess",
                    headers=_hdr(client, MANAGER),
                    json={"quote_id": "q-nocost", "record": False,
                          "lines": [{"line_id": "L1", "product_id": "p1",
                                     "customer_id": "c1", "qty": 10,
                                     "quoted_unit_price": 850}]})
    assert r.status_code == 200, r.text

    with client.Maker() as s:
        s.query(models.CostRecord).delete()
        s.commit()
    again = client.post("/api/v1/quote-diagnosis/assess",
                        headers=_hdr(client, MANAGER),
                        json={"quote_id": "q-nocost2", "record": False,
                              "lines": [{"line_id": "L1", "product_id": "p1",
                                         "customer_id": "c1", "qty": 10,
                                         "quoted_unit_price": 850}]}).json()

    block = again["lines"][0]["attribution"]
    assert block["renders"] is True
    assert block["drivers"] == []
    assert block["note"].startswith("NO_COST_BASELINE:")
    assert "no purchase was knowable" in block["note"]


def test_a_diagnosis_date_far_from_today_is_refused(client):
    """Diagnosing as of an arbitrary past date reads an item's price history one
    day at a time, which is worth more to a competitor than one quote is."""
    r = client.post("/api/v1/quote-diagnosis/assess", headers=_hdr(client, SALES),
                    json={"quote_id": "q9", "as_of": "2020-01-01",
                          "lines": [{"line_id": "L1", "product_id": "p1",
                                     "qty": 1}]})
    assert r.status_code == 422


# ── dismissal ────────────────────────────────────────────────────────────────

def test_a_salesperson_may_dismiss_the_card_that_interrupted_them(client):
    """A dismissal only they can make but only a manager can record is a
    dismissal nobody records."""
    diagnosis_id = _assess(client, SALES)["lines"][0]["quote_diagnosis_id"]

    r = client.post(f"/api/v1/quote-diagnosis/{diagnosis_id}/dismiss",
                    headers=_hdr(client, SALES),
                    json={"reason_code": "VOLUME_COMMITMENT",
                          "note": "annual contract"})

    assert r.status_code == 200, r.text
    with client.Maker() as s:
        rows = s.query(models.QuoteDiagnosisDismissal).all()
        assert len(rows) == 1
        assert rows[0].reason_code == "VOLUME_COMMITMENT"


def test_an_unsupported_dismissal_reason_is_refused_with_the_list(client):
    diagnosis_id = _assess(client, SALES)["lines"][0]["quote_diagnosis_id"]

    r = client.post(f"/api/v1/quote-diagnosis/{diagnosis_id}/dismiss",
                    headers=_hdr(client, SALES),
                    json={"reason_code": "JUST_BECAUSE"})

    assert r.status_code == 422
    assert "PRICE_IS_CORRECT" in r.json()["detail"]


def test_the_reasons_endpoint_serves_what_the_writer_accepts(client):
    """One list. A front end offering a reason the service refuses is a dead
    button somebody finds in front of a customer."""
    from app.commercial.quote_diagnosis.render import DISMISS_REASONS

    served = client.get("/api/v1/quote-diagnosis/reasons",
                        headers=_hdr(client, SALES)).json()["reasons"]

    assert {r["code"] for r in served} == set(DISMISS_REASONS)
    assert all(r["label"] for r in served)


def test_dismissing_a_diagnosis_from_another_organization_is_a_404(client):
    r = client.post("/api/v1/quote-diagnosis/not-a-real-id/dismiss",
                    headers=_hdr(client, SALES),
                    json={"reason_code": "OTHER"})

    assert r.status_code == 404


# ── the vocabulary its caller actually speaks ────────────────────────────────

def test_a_line_naming_a_product_by_code_is_diagnosed(client):
    """Why this endpoint went unused for a release.

    It demanded platform ids. Its only caller is the Quote Builder, which holds
    the code the desk typed and not an id, so the two screens that assess the
    same line wanted two different vocabularies and the one written second was
    never wired to anything. Both resolve through
    ``quote_service._resolve_products`` now.
    """
    r = client.post("/api/v1/quote-diagnosis/assess", headers=_hdr(client, SALES),
                    json={"quote_id": "q-code", "record": False,
                          "lines": [{"line_id": "L1", "product_id": "ITEM-900",
                                     "customer_id": "Acme Engineering",
                                     "qty": 10, "quoted_unit_price": 850}]})

    assert r.status_code == 200, r.text
    line = r.json()["lines"][0]
    assert line["renders"] is True
    assert line["headline"] == "Below this customer's historical pricing"


def test_a_product_the_master_has_never_held_is_answered_not_refused(client):
    """The desk quotes things the catalogue has never carried. That is ordinary,
    and the engine's answer is that it has no evidence — not an error."""
    r = client.post("/api/v1/quote-diagnosis/assess", headers=_hdr(client, SALES),
                    json={"quote_id": "q-new", "record": False,
                          "lines": [{"line_id": "L1", "product_id": "NOT-A-CODE",
                                     "qty": 1, "quoted_unit_price": 100}]})

    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["renders"] is False
