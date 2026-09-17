"""Diagnosing a quote the ERP already issued, by reference rather than by date.

**The case this file exists for is the old quote.** The diagnosis engine reached
the ERP quote page through `/assess`, which takes `as_of` from the caller — and
bounds it, because a date a caller can move a day at a time reads an item's
price history out of the answers rather than judging a quote. That bound is
right and is not touched here. Its consequence was that a book whose quotes are
mostly older than the window got a diagnosis on almost none of them.

`/erp-quote/{ref}` takes no date. The caller names a document; the server reads
that document's own raised date, its own lines and its own customer. There is
nothing to walk: one quote, one date, and `erp_quotes` is written by the sync
and by no endpoint at all, so a caller cannot mint a quote to obtain a date.

The three properties worth pinning, in order of what they would cost if wrong:
a salesperson gets no cost; a salesperson gets no quote they do not hold; and
reading an issued document writes nothing.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.domain import models
from app.routers import platform_auth, quote_diagnosis
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

#: Comfortably outside `_AS_OF_WINDOW_DAYS`. This is the whole point: on
#: `/assess` this date is refused, and refusing it there is correct.
RAISED = date.today() - timedelta(days=400)
OLD_REF = "erp-old-1"

SELLING_PRICE = 1000
PURCHASE_COST = 371


def _stamp(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)


def _seed(s) -> None:
    # `assigned_user_id` matters: it is what narrows a salesperson's book, and
    # the 404 test below turns on it.
    s.add(models.Customer(customer_id="c1", organization_id=ORG, external_id="c1",
                          name="Acme Engineering", assigned_user_id="usr_sales"))
    s.add(models.Customer(customer_id="c2", organization_id=ORG, external_id="c2",
                          name="Somebody Else Ltd", assigned_user_id="usr_other"))
    s.add(models.Product(product_id="p1", organization_id=ORG,
                         external_id="ITEM-900", name="CNMG 120408-MP insert",
                         uom="pcs", source_item_category="Turning"))

    # History the engine can actually cite, all of it recorded *before* the
    # quote went out — evidence dated after it must not reach a diagnosis of it.
    for i in range(12):
        day = RAISED - timedelta(days=300 - 7 * i)
        s.add(models.SalesTxn(
            sales_txn_id=f"stx_{i}", organization_id=ORG,
            external_ref=f"INV-{i}:1", customer_id="c1", product_id="p1",
            date=day, qty=Decimal("10"), unit_price=Decimal(SELLING_PRICE),
            line_revenue=Decimal(SELLING_PRICE * 10),
            source_recorded_at=_stamp(day),
            source_ref={"record_type": "invoice"}))
    for i in range(3):
        day = RAISED - timedelta(days=200 - 30 * i)
        s.add(models.CostRecord(
            cost_record_id=f"cst_{i}", organization_id=ORG,
            external_ref=f"BILL-{i}:1", product_id="p1", date=day,
            qty=Decimal("10"), unit_cost=Decimal(PURCHASE_COST),
            rate=Decimal(PURCHASE_COST), source_recorded_at=_stamp(day),
            source_ref={"record_type": "bill"}))

    _quote(s, OLD_REF, "c1", "QT-OLD")
    _quote(s, "erp-theirs-1", "c2", "QT-THEIRS")
    s.flush()


def _quote(s, ref: str, customer_id: str, number: str) -> None:
    s.add(models.QuoteDoc(
        organization_id=ORG, external_ref=ref, number=number,
        customer_id=customer_id, customer_ref="Acme Engineering",
        date=RAISED, source_status="accepted", outcome="WON",
        decided_on=RAISED + timedelta(days=3), total=Decimal("20000"),
        connector="zoho"))
    # Two lines, and only the first is diagnosable: the second names no item
    # code, which is the ordinary shape of a freight or handling line.
    s.add(models.ErpQuoteLine(
        organization_id=ORG, quote_ref=ref, external_ref=f"{ref}:l1",
        line_number=0, item_code="ITEM-900", description="CNMG 120408-MP",
        qty=Decimal("10"), unit="pcs", rate=Decimal("850"),
        amount=Decimal("8500"), connector="zoho"))
    s.add(models.ErpQuoteLine(
        organization_id=ORG, quote_ref=ref, external_ref=f"{ref}:l2",
        line_number=1, item_code="", description="Freight",
        qty=Decimal("1"), unit="nos", rate=Decimal("500"),
        amount=Decimal("500"), connector="zoho"))


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


def _erp(c, email, ref=OLD_REF):
    return c.post(f"/api/v1/quote-diagnosis/erp-quote/{ref}", headers=_hdr(c, email))


# ── the reason this endpoint exists ──────────────────────────────────────────

def test_a_quote_older_than_the_assess_window_is_diagnosed(client):
    """The report behind this: almost nothing on the book could be diagnosed."""
    r = _erp(client, MANAGER)

    assert r.status_code == 200, r.text
    assert r.json()["lines"], "an old quote came back with no diagnosed lines"


def test_it_is_diagnosed_as_of_the_day_it_was_raised(client):
    """Invariant I1. A quote judged against evidence that landed after it went
    out is the look-ahead the whole engine is built to refuse — and on a
    document this old the gap is 400 days of prices."""
    assert _erp(client, MANAGER).json()["as_of"] == RAISED.isoformat()


def test_the_same_date_is_still_refused_when_a_caller_names_it(client):
    """The control this endpoint routes around is not weakened, it is avoided.

    Same organization, same quote, same date — posted as a caller-chosen
    `as_of` it is still refused, because a date a caller can choose is a date a
    caller can walk. The difference is who supplies it, and that is the only
    difference.
    """
    r = client.post("/api/v1/quote-diagnosis/assess", headers=_hdr(client, MANAGER),
                    json={"quote_id": OLD_REF, "as_of": RAISED.isoformat(),
                          "record": False,
                          "lines": [{"line_id": "0", "product_id": "ITEM-900",
                                     "customer_id": "c1", "qty": 10,
                                     "quoted_unit_price": 850}]})

    assert r.status_code == 422, r.text
    assert "within" in r.text and "days of today" in r.text


# ── what it must not do ──────────────────────────────────────────────────────

def test_a_salespersons_response_has_no_cost_in_it_anywhere(client):
    """A sweep of the whole payload, not a list of fields.

    Every field-level assertion in this repository's previous leak passed while
    the endpoint gave up cost, because the leak was in a field nobody had
    thought to name. This endpoint is new and reaches the same engine, so it
    gets the same sweep rather than the assumption that the shared projection
    makes it safe.
    """
    payload = _erp(client, SALES).json()
    # Non-empty first. A sweep over `{"lines": []}` passes while proving
    # nothing, which is this repository's own `absence of evidence is not a
    # pass` tell pointed at its own test.
    assert payload["lines"], "nothing was diagnosed, so the sweep proves nothing"
    body = json.dumps(payload)

    assert str(PURCHASE_COST) not in body
    for word in ("cost", "margin", "purchase_price", "opportunity"):
        assert word not in body.lower(), f"{word!r} reached a salesperson"


def test_a_quote_on_an_account_this_salesperson_does_not_hold_is_not_found(client):
    """404, not 403. Whether a quote exists in a book you cannot read is itself
    something you should not learn — the same choice `insight.quote_book_lines`
    makes, through the same scoping helper so the two cannot drift."""
    assert _erp(client, SALES, ref="erp-theirs-1").status_code == 404
    # And the control: the account they do hold is readable.
    assert _erp(client, SALES).status_code == 200


def test_a_reference_naming_no_quote_is_not_found(client):
    assert _erp(client, MANAGER, ref="no-such-quote").status_code == 404


def test_reading_an_issued_document_records_nothing(client):
    """The page this serves says nothing on it is written. A diagnosis row per
    visit would make that false, and `quote_diagnoses` is append-only — there
    would be no tidying it up afterwards."""
    for _ in range(3):
        assert _erp(client, MANAGER).status_code == 200

    s = client.Maker()
    try:
        assert s.query(models.QuoteDiagnosis).count() == 0
    finally:
        s.close()


def test_a_line_naming_no_product_is_not_diagnosed(client):
    """Freight cannot be compared against anything. The filter is the server's
    now rather than the screen's, so that two callers cannot disagree about
    which lines are questions."""
    ids = {ln["line_id"] for ln in _erp(client, MANAGER).json()["lines"]}

    assert ids == {"0"}
