"""The recorded-reason reading through the real routes, and the line it stops at.

One question this file exists for and one that could not be reached by a pure
function: **a salesperson is told what the record says and is never told what it
might mean about margin.** Both halves are asserted, because a withholding test
that passes because the endpoint returns nothing to anybody is not evidence of
withholding.

The leak definition is imported from ``cost_sweep`` rather than written again
here. Three endpoints now project a diagnosis and two definitions of "a leak" is
one more than this can afford — and the sweep already screens for the word
"margin", so the claim's own code name trips it. One test proves that rather
than asserting it: it splices the owner's block into the desk's payload and
requires the sweep to object, which is the only way to know the check is live on
a field added after it was written.

The fixture is its own rather than borrowed from ``test_quote_diagnosis_api``
because the seed is a different one: this needs a quote the ERP issued, carrying
the source fields an administrator configured, with a declaration in force over
them. What the two files must not have two of is the sweep, and they do not.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import cost_sweep
import dbsupport
from app.commercial import source_concepts as sc
from app.db import get_session
from app.domain import models
from app.routers import platform_auth, quote_diagnosis
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
AS_OF = date.today()

MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

SELLING_PRICE = 1000
PURCHASE_COST = 371

#: The reference the ERP gave the quote, and the key an administrator on this
#: book configured. The second appears in no assertion about an engine output —
#: it is here to be declared and then never seen again.
QUOTE_REF = "QT-2026-0042"
SOURCE_KEY = "cf_quote_type"


def _stamp(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)


def _seed(s, *, attributes: dict) -> None:
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
            source_recorded_at=_stamp(day), source_ref={"record_type": "invoice"}))
    for i in range(3):
        day = AS_OF - timedelta(days=200 - 30 * i)
        s.add(models.CostRecord(
            cost_record_id=f"cst_{i}", organization_id=ORG,
            external_ref=f"BILL-{i}:1", product_id="p1", date=day,
            qty=Decimal("10"), unit_cost=Decimal(PURCHASE_COST),
            rate=Decimal(PURCHASE_COST), source_recorded_at=_stamp(day),
            source_ref={"record_type": "bill"}))
    # The quote as its own system holds it, with whatever its administrator put
    # in the field. ``source_attributes`` is carried verbatim under the source's
    # own key: nothing below ever names it.
    s.add(models.QuoteDoc(
        quote_document_id="qd_1", organization_id=ORG, connector="zoho",
        connection_id="cn_1", external_ref=QUOTE_REF, number=QUOTE_REF,
        customer_id="c1", customer_ref="Acme Engineering", date=AS_OF,
        source_status="sent", outcome="UNRECORDED",
        total=Decimal(SELLING_PRICE * 10), source_attributes=attributes,
        source_recorded_at=_stamp(AS_OF)))
    s.flush()


def _client(*, attributes: dict, declare: bool) -> TestClient:
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    _seed(s, attributes=attributes)
    if declare:
        sc.declare(s, ORG, connector="zoho", entity="quote",
                   source_key=SOURCE_KEY, concept=sc.QUOTE_INTENT,
                   value_map={"Tender enquiry": "TENDER"},
                   source_ref="seeded from the field audit")
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


@pytest.fixture()
def blank():
    """The field is declared and this quote is empty in it — the only state in
    which the possibility is even available."""
    return _client(attributes={SOURCE_KEY: ""}, declare=True)


@pytest.fixture()
def recorded():
    """Somebody wrote down why."""
    return _client(attributes={SOURCE_KEY: "Tender enquiry"}, declare=True)


@pytest.fixture()
def undeclared():
    """The live book: fields carried, nothing declared over them."""
    return _client(attributes={SOURCE_KEY: "Tender enquiry"}, declare=False)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _assess(c, email, *, price=850.0, quote_id=QUOTE_REF, record=True):
    r = c.post("/api/v1/quote-diagnosis/assess", headers=_hdr(c, email), json={
        "quote_id": quote_id, "record": record,
        "lines": [{"line_id": "L1", "product_id": "p1", "customer_id": "c1",
                   "qty": 10, "quoted_unit_price": price}]})
    assert r.status_code == 200, r.text
    return r.json()["lines"][0]


# ── what reaches the desk ────────────────────────────────────────────────────

def test_a_salesperson_is_told_the_quote_was_recorded_as_a_tender(recorded):
    """The half that reaches the desk, and the argument for the line.

    ``quote_intent = TENDER`` is a fact about a field on a document. It explains
    a low price without revealing anything about cost, and a salesperson who
    knows it argues the price better — so it is served, worded exactly as the
    owner's copy words it.
    """
    line = _assess(recorded, SALES)

    assert line["view"] == "OPERATIONS"
    block = line["intent"]
    assert block["read"] is True
    assert block["headline"] == "Recorded on this quote: the quote type is TENDER."
    assert "PRICING_REASON_RECORDED" in block["codes"]
    assert "This quote records the quote type as TENDER." in block["lines"]

    # And the whole payload still carries no economics at all.
    cost_sweep.assert_no_cost({"lines": [line]}, cost=PURCHASE_COST)


def test_a_salesperson_is_told_nothing_was_recorded_and_nothing_more(blank):
    """The sentence, verbatim, and the one it must never become."""
    line = _assess(blank, SALES)
    block = line["intent"]

    assert block["headline"] == (
        "No pricing reason has been recorded for this quote.")
    assert "NO_PRICING_REASON_RECORDED" in block["codes"]
    assert "POSSIBLE_MARGIN_LEAKAGE" not in block["codes"]
    body = " ".join([block["headline"], block["note"], *block["lines"]]).lower()
    assert "leakage" not in body
    assert "unintentional" not in body
    # The qualification travels with it. Without that sentence the headline is
    # read as a finding about how this quote was priced rather than as a fact
    # about the record.
    assert "not evidence that there was no reason" in block["note"]

    cost_sweep.assert_no_cost({"lines": [line]}, cost=PURCHASE_COST)


def test_the_owner_gets_the_possibility_and_the_desk_has_no_trace_of_it(blank):
    """Both halves of the split, on one quote.

    The line is below the range this customer's history supports and the field
    this organization records a reason in is empty, so the possibility is
    available. It goes to the owner and stops there.
    """
    owner = _assess(blank, MANAGER, quote_id=QUOTE_REF)
    desk = _assess(blank, SALES, quote_id=QUOTE_REF, record=False)

    claim = owner["intent"]["lines"][0]
    assert claim.startswith("POSSIBLE_MARGIN_LEAKAGE:")
    assert "potential margin leakage and no further" in claim
    assert "POSSIBLE_MARGIN_LEAKAGE" in owner["intent"]["codes"]

    # The desk gets the same four sentences and not the fifth.
    assert desk["intent"]["lines"] == owner["intent"]["lines"][1:]
    assert "POSSIBLE_MARGIN_LEAKAGE" not in desk["intent"]["codes"]
    cost_sweep.assert_no_cost({"lines": [desk]}, cost=PURCHASE_COST)


def test_the_cost_sweep_would_catch_the_claim_if_it_ever_reached_the_desk(blank):
    """The negative control, without which the sweeps above prove nothing about
    this field.

    A sweep that passes is only evidence if the same sweep fails on the payload
    it is meant to reject. So: take the desk's payload, which passes, splice the
    owner's block into it, and require the sweep to object — by the word, not by
    the cost figure, because the claim carries no number at all. That is the
    guard that would catch a future author widening ``OPERATIONS_CODES`` or
    growing a field on ``intent.Reading``.
    """
    owner = _assess(blank, MANAGER)
    desk = _assess(blank, SALES, record=False)
    cost_sweep.assert_no_cost({"lines": [desk]}, cost=PURCHASE_COST)

    leaked = {**desk, "intent": owner["intent"]}
    with pytest.raises(AssertionError) as caught:
        cost_sweep.assert_no_cost({"lines": [leaked]}, cost=PURCHASE_COST)
    assert "'margin' reached a salesperson" in str(caught.value)


# ── the book as it actually is ───────────────────────────────────────────────

def test_a_book_with_nothing_declared_reads_as_not_recorded_and_claims_nothing(
        undeclared):
    """The honest limit, asserted rather than described.

    On the live book these fields are mostly empty and nothing is declared over
    them, so this is the ordinary answer. It is "nothing has been declared", not
    "nothing was recorded" and certainly not a classification — and no claim is
    made about a below-band line, because there is no declared field it could be
    missing from.
    """
    owner = _assess(undeclared, MANAGER)
    block = owner["intent"]

    assert block["read"] is True
    assert block["codes"] == ["PRICING_REASON_NOT_DECLARED"]
    assert block["headline"] == (
        "No pricing reason has been recorded for this quote.")
    assert "until somebody declares one" in block["note"]
    assert all("POSSIBLE_MARGIN_LEAKAGE" not in line for line in block["lines"])


def test_a_quote_drafted_here_says_it_has_no_source_record(blank):
    """A quote in the builder exists here and in no ERP, so there is nothing to
    read. Different from "nothing was recorded", and a reader given the second
    about the first has been told something false."""
    line = _assess(blank, MANAGER, quote_id="draft-not-in-any-erp",
                   record=False)
    block = line["intent"]

    assert block["read"] is False
    assert block["headline"] == ""
    assert block["lines"] == []
    assert block["note"].startswith("NO_SOURCE_RECORD:")
    # It still draws. An empty block would read as "no reason was recorded".
    assert block["renders"] is True


def test_a_stored_diagnosis_says_it_has_no_reading_rather_than_showing_none(blank):
    """The second projection site, and the one where an absence would lie.

    ``quote_diagnoses`` has no column for this — see ``rules.ENGINE_VERSION`` —
    so a row read back cannot answer. Re-running the reading against today's
    declarations would be worse: the taxonomy can have moved since, and a stored
    diagnosis must not be re-judged by a mapping written after it.
    """
    _assess(blank, MANAGER)
    line = blank.get(f"/api/v1/quote-diagnosis/quote/{QUOTE_REF}",
                     headers=_hdr(blank, MANAGER)).json()["lines"][0]

    block = line["intent"]
    assert block["read"] is False
    assert block["note"].startswith("NOT_ON_STORED_RECORD:")
    assert "Re-assess this line to see it." in block["note"]
    # The stored row's own columns are unchanged by any of this.
    assert line["engine_version"] == "qd-1"
    assert "POSSIBLE_MARGIN_LEAKAGE" not in line["codes"]
    assert "POSSIBLE_MARGIN_LEAKAGE" not in line["context"]


def test_no_source_key_reaches_either_payload(recorded):
    """The multi-ERP promise at the one place it could break. The administrator
    declared ``cf_quote_type``; nothing downstream has ever been given it."""
    import json

    for email in (SALES, MANAGER):
        body = json.dumps(_assess(recorded, email, record=False)).lower()
        assert SOURCE_KEY not in body
