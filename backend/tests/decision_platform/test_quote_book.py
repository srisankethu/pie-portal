"""The plain listing of every quote the ERP raised.

This exists because of a real report: a sync ran, read the book's quotes, and
the person who ran it saw two platform drafts on the Quotes screen and concluded
it was broken. It was not — ``erp_quotes`` had exactly one reader,
``unrecorded``, which filters to ``outcome == UNRECORDED``, so every quote the
ERP had marked accepted or invoiced was synced, stored, policied, exported, and
visible nowhere.

So the case that matters here and cannot be reached from the other file's tests:
**a decided quote appears.** Everything else is the properties that list has to
keep — a missing total is never summed as zero, the role scope holds, and
nothing derived from cost is in the payload.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.commercial.insight import quote_book
from app.db import get_session
from app.domain import models
from app.domain.enums import QuoteDocOutcome
from app.routers import insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"

MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

NAMES = {"c1": "Acme Engineering", "c2": "Beta Works"}


def _doc(s, ref: str, *, status: str = "sent",
         outcome: str = QuoteDocOutcome.UNRECORDED.value,
         customer: str | None = "c1", customer_ref: str = "Acme Engineering",
         raised: date = date(2026, 5, 1), expires: date | None = None,
         total: str | None = "10000", decided: date | None = None,
         opened: datetime | None = None, connection_id: str = "conn1") -> None:
    s.add(models.QuoteDoc(
        organization_id=ORG, connector="zoho", connection_id=connection_id,
        external_ref=ref, number=f"SLS/QTN-{ref}", customer_id=customer,
        customer_ref=customer_ref, date=raised, expires_on=expires,
        source_status=status, outcome=outcome, decided_on=decided,
        total=Decimal(total) if total is not None else None,
        client_viewed_at=opened))


def _seed(s) -> None:
    s.add(models.Customer(customer_id="c1", organization_id=ORG,
                          external_id="c1", name="Acme Engineering",
                          assigned_user_id="usr_sales"))
    s.add(models.Customer(customer_id="c2", organization_id=ORG,
                          external_id="c2", name="Beta Works",
                          assigned_user_id="usr_manager"))
    s.flush()


@pytest.fixture()
def maker():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    s.commit()
    s.close()
    return Maker


def _build(Maker, **kw):
    s = Maker()
    try:
        return quote_book.build(s, ORG, customer_names=NAMES, **kw)
    finally:
        s.close()


# ── the reason this module exists ────────────────────────────────────────────

def _written(s, quote_id: str, number: str, doc_id: str,
             connection_id: str | None) -> None:
    from datetime import datetime, timezone
    s.add(models.QuoteDraft(quote_id=quote_id, organization_id=ORG,
                            customer_name="Acme Engineering", customer_id="c1",
                            number=number, sequence=int(number.split("-")[1]),
                            reference=f"{number}-deadbeef"))
    s.add(models.QuoteDocument(
        organization_id=ORG, quote_id=quote_id, external_system="zoho",
        connection_id=connection_id, external_document_id=doc_id,
        external_document_number=f"SLS/QTN-{doc_id}", reference=f"{number}-deadbeef",
        line_count=1, fingerprint="f", written_at=datetime.now(timezone.utc)))


def test_a_quote_this_platform_wrote_names_its_draft(maker):
    """The reverse of the workspace's join: the ERP row says which PIE quote it
    came from, so the same document is not two unrelated rows on two tabs."""
    s = maker()
    _doc(s, "est-9")
    _doc(s, "est-8")
    _written(s, "q9", "QB-0009", "est-9", connection_id="conn1")
    s.commit()
    s.close()

    by_ref = {q.quote_document_ref: q for q in _build(maker)}
    assert by_ref["est-9"].platform_quote == {"quote_id": "q9", "number": "QB-0009"}
    assert by_ref["est-8"].platform_quote is None
    assert by_ref["est-9"].to_dict()["platform_quote"]["number"] == "QB-0009"


def test_a_document_that_does_not_say_its_book_joins_only_where_the_id_is_unique(maker):
    s = maker()
    _doc(s, "est-7")
    s.add(models.QuoteDoc(
        organization_id=ORG, connector="zoho", connection_id="conn2",
        external_ref="est-7", number="4U/QTN-est-7", customer_id="c1",
        customer_ref="Acme Engineering", date=date(2026, 5, 1),
        source_status="sent", outcome=QuoteDocOutcome.UNRECORDED.value))
    _written(s, "q7", "QB-0007", "est-7", connection_id=None)
    _written(s, "q6", "QB-0006", "est-6", connection_id=None)
    _doc(s, "est-6")
    s.commit()
    s.close()

    rows = _build(maker)
    assert all(q.platform_quote is None for q in rows if q.quote_document_ref == "est-7"), \
        "two books answer to est-7; a company-less document names neither"
    assert next(q for q in rows if q.quote_document_ref == "est-6").platform_quote == {
        "quote_id": "q6", "number": "QB-0006"}


def test_a_decided_quote_is_listed(maker):
    """The defect, pinned.

    ``unrecorded`` cannot return either of these rows by construction, and for
    months it was the only reader of this table. A book that converts most of
    its quotes — which is a good book — was the book that looked empty.
    """
    s = maker()
    _doc(s, "won", status="accepted", outcome=QuoteDocOutcome.WON.value,
         decided=date(2026, 5, 20))
    _doc(s, "lost", status="declined", outcome=QuoteDocOutcome.LOST.value,
         decided=date(2026, 5, 21))
    _doc(s, "open", status="sent")
    s.commit()
    s.close()

    rows = _build(maker)

    assert {r.quote_document_ref for r in rows} == {"won", "lost", "open"}
    assert {r.outcome for r in rows} == {"WON", "LOST", "UNRECORDED"}


def test_the_erps_own_word_travels_beside_the_verdict(maker):
    """``outcome`` collapses the ERP's vocabulary into three values on purpose.

    A reader asking why a quote reads UNRECORDED needs to see that it was
    ``sent`` rather than ``expired`` — the classifier's refusal to turn silence
    into a loss is exactly the decision somebody should be able to check.
    """
    s = maker()
    _doc(s, "a", status="expired")
    _doc(s, "b", status="viewed")
    s.commit()
    s.close()

    by_ref = {r.quote_document_ref: r for r in _build(maker)}
    assert by_ref["a"].source_status == "expired"
    assert by_ref["b"].source_status == "viewed"
    assert by_ref["a"].outcome == by_ref["b"].outcome == "UNRECORDED"


# ── ordering ─────────────────────────────────────────────────────────────────

def test_the_newest_quote_is_first(maker):
    """A list somebody scrolls to find a quote, not a worklist.

    Deliberately a different order from ``unrecorded``'s, which ranks by how
    long a quote has been lapsed. Two orderings is why these are two builders.
    """
    s = maker()
    _doc(s, "old", raised=date(2026, 1, 9))
    _doc(s, "newest", raised=date(2026, 7, 2))
    _doc(s, "middle", raised=date(2026, 4, 4))
    s.commit()
    s.close()

    assert [r.quote_document_ref for r in _build(maker)] == [
        "newest", "middle", "old"]


def test_two_quotes_raised_the_same_day_have_a_stable_order(maker):
    """Left to the database, the same book paginates differently on two engines.

    SQLite and Postgres are both free to return ties in any order, and this list
    is paged — so an unstable tie is a row that appears on page one and page two
    or on neither.
    """
    s = maker()
    for ref in ("c", "a", "b"):
        _doc(s, ref, raised=date(2026, 5, 1))
    s.commit()
    s.close()

    assert ([r.quote_document_ref for r in _build(maker)]
            == [r.quote_document_ref for r in _build(maker)])


# ── the properties a list over money has to keep ─────────────────────────────

def test_a_quote_with_no_total_is_kept_and_never_valued_at_zero(maker):
    s = maker()
    _doc(s, "valued", total="7000")
    _doc(s, "unvalued", total=None)
    s.commit()
    s.close()

    rows = _build(maker)
    totals = quote_book.totals(rows)

    assert len(rows) == 2
    assert totals["value_total"] == 7000.0
    assert totals["quotes_without_a_value"] == 1


def test_a_book_whose_quotes_all_lack_a_total_reports_no_total(maker):
    """Not ``"0"``. A figure that looks complete and is not, with nothing on the
    response to say so, is the failure CLAUDE.md §1 names by sight."""
    s = maker()
    _doc(s, "a", total=None)
    s.commit()
    s.close()

    totals = quote_book.totals(_build(maker))
    assert totals["value_total"] is None
    assert totals["quotes_without_a_value"] == 1


def test_the_counts_partition_the_book(maker):
    s = maker()
    _doc(s, "w1", outcome=QuoteDocOutcome.WON.value, decided=date(2026, 5, 2))
    _doc(s, "w2", outcome=QuoteDocOutcome.WON.value, decided=date(2026, 5, 3))
    _doc(s, "l1", outcome=QuoteDocOutcome.LOST.value, decided=date(2026, 5, 4))
    _doc(s, "u1")
    s.commit()
    s.close()

    t = quote_book.totals(_build(maker))
    assert t["by_outcome"] == {"WON": 2, "LOST": 1, "UNRECORDED": 1}
    assert sum(t["by_outcome"].values()) == t["count"]


# ── naming the counterparty ──────────────────────────────────────────────────

def test_an_unresolved_customer_keeps_the_erps_own_typed_name(maker):
    """On this table an unresolved id is usually a walk-in or a spelling the
    contact pull did not match — a real name, and better than a placeholder."""
    s = maker()
    _doc(s, "walkin", customer=None, customer_ref="Cash sale — Kumar")
    s.commit()
    s.close()

    assert _build(maker)[0].customer_label == "Cash sale — Kumar"


def test_a_quote_with_no_name_at_all_is_labelled_rather_than_blank(maker):
    s = maker()
    _doc(s, "bare", customer=None, customer_ref="")
    s.commit()
    s.close()

    assert _build(maker)[0].customer_label == "Unattributed"


def test_the_label_rule_is_the_one_the_worklist_uses(maker):
    """One table, two screens, one spelling of each customer.

    Asserted by calling the shared function rather than by comparing two
    outputs: the point is that there is one rule, and a test that compared
    results would pass just as well with two copies of it.
    """
    from app.commercial.insight import unrecorded

    assert unrecorded.quote_book.customer_label is quote_book.customer_label


# ── what the drawer needs beyond the grid ────────────────────────────────────

def test_a_quote_carries_the_book_it_was_raised_in(maker):
    """Through ``origin.Companies``, not a second lookup.

    ``label_for`` and ``of`` answer the same question for a grouped query and
    for a record, which is what stops two screens naming one company two ways.
    """
    from app.domain.origin import Companies

    s = maker()
    s.add(models.ZohoConnection(
        connection_id="conn1", organization_id=ORG, connector="zoho",
        zoho_organization_id="600", label="SLS Engineers"))
    _doc(s, "q1")
    s.commit()
    s.close()

    sess = maker()
    try:
        rows = quote_book.build(sess, ORG, customer_names=NAMES,
                                companies=Companies(sess, ORG))
    finally:
        sess.close()

    assert rows[0].company == "SLS Engineers"


def test_a_caller_with_no_company_index_gets_the_absence_named(maker):
    """Not a blank, which reads as "no company", and not a guess."""
    s = maker()
    _doc(s, "q1")
    s.commit()
    s.close()

    assert _build(maker)[0].company == "Source not recorded"


def test_the_organizations_own_fields_on_the_quote_travel(maker):
    """What somebody typed in their ERP about this quote — the classification
    no other table holds."""
    s = maker()
    row = models.QuoteDoc(
        organization_id=ORG, connector="zoho", connection_id="conn1",
        external_ref="q1", number="QT-1", customer_id="c1",
        customer_ref="Acme Engineering", date=date(2026, 5, 1),
        source_status="sent", outcome=QuoteDocOutcome.UNRECORDED.value,
        total=Decimal("1000"),
        source_attributes={"cf_quote_type": "Tender", "branch_id": "b1"})
    s.add(row)
    s.commit()
    s.close()

    assert _build(maker)[0].source_attributes == {
        "cf_quote_type": "Tender", "branch_id": "b1"}


def test_a_quote_nobody_classified_carries_an_empty_set_not_a_bucket(maker):
    """An absent custom field is not a category. A quote with no
    ``cf_quote_type`` is a quote nobody classified, which is a different fact
    from every unclassified quote sharing a bucket called "other"."""
    s = maker()
    _doc(s, "q1")
    s.commit()
    s.close()

    assert _build(maker)[0].source_attributes == {}


# ── role scope ───────────────────────────────────────────────────────────────

def test_a_salesperson_sees_only_their_own_accounts(maker):
    s = maker()
    _doc(s, "mine", customer="c1")
    _doc(s, "theirs", customer="c2", customer_ref="Beta Works")
    s.commit()
    s.close()

    rows = _build(maker, customer_ids=frozenset({"c1"}))
    assert [r.quote_document_ref for r in rows] == ["mine"]


def test_a_salesperson_holding_no_accounts_sees_nothing(maker):
    """An empty set and ``None`` are different answers.

    Collapsing the first into the second is how a narrowed reader gets handed
    the whole book.
    """
    s = maker()
    _doc(s, "any", customer="c1")
    s.commit()
    s.close()

    assert _build(maker, customer_ids=frozenset()) == []
    assert len(_build(maker, customer_ids=None)) == 1


def test_an_unattributed_quote_is_dropped_under_scope_and_kept_without_it(maker):
    """Nobody recorded it against an account, so there is no account to say it
    belongs to — and showing it to everybody puts a stranger's quote on a
    salesperson's list. It stays visible unscoped, where it can be attributed."""
    s = maker()
    _doc(s, "orphan", customer=None, customer_ref="Walk-in")
    s.commit()
    s.close()

    assert _build(maker, customer_ids=frozenset({"c1"})) == []
    assert len(_build(maker, customer_ids=None)) == 1


# ── through the endpoint ─────────────────────────────────────────────────────

@pytest.fixture()
def client(maker):
    s = maker()
    _doc(s, "won", status="accepted", outcome=QuoteDocOutcome.WON.value,
         decided=date(2026, 5, 20), total="25000")
    _doc(s, "open", status="sent", total="4000")
    _doc(s, "theirs", customer="c2", customer_ref="Beta Works",
         outcome=QuoteDocOutcome.WON.value, decided=date(2026, 5, 9),
         total="900000")
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_endpoint_serves_the_decided_quotes_too(client):
    body = client.get("/api/v1/insight/quote-book",
                      headers=_hdr(client, MANAGER)).json()

    assert body["count"] == 3
    assert body["by_outcome"]["WON"] == 2
    refs = {q["quote_document_ref"] for q in body["quotes_listed"]}
    assert refs == {"won", "open", "theirs"}


def test_the_endpoint_narrows_a_salesperson_to_their_own_book(client):
    body = client.get("/api/v1/insight/quote-book",
                      headers=_hdr(client, SALES)).json()

    assert {q["quote_document_ref"] for q in body["quotes_listed"]} == {
        "won", "open"}
    # The headline is taken over what they may see, not over the book — a count
    # outside the scope leaks the size of a book the reader cannot see.
    assert body["count"] == 2
    assert "900000" not in json.dumps(body)


def test_no_cost_or_margin_reaches_the_response(client):
    """Swept rather than asserted field by field.

    ``erp_quotes`` has no cost column, so this is a guard against one being
    added and quietly travelling, not against a leak that exists today.
    """
    body = json.dumps(client.get("/api/v1/insight/quote-book",
                                 headers=_hdr(client, SALES)).json()).lower()

    for word in ("cost", "margin", "unit_cost", "gross_profit"):
        assert word not in body, f"{word!r} reached the quote book"


def test_the_page_says_how_much_of_the_book_it_is_not_showing(client):
    body = client.get("/api/v1/insight/quote-book?limit=1",
                      headers=_hdr(client, MANAGER)).json()

    assert body["listed"] == 1
    assert body["count"] == 3


def test_an_empty_book_says_what_would_have_filled_it(maker):
    """The answer somebody with an empty screen actually needs.

    A bare empty list is what sent the original report: nothing on the screen
    distinguished "no quotes synced" from "the quote stage was refused a
    permission".
    """
    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    c = TestClient(app)

    body = c.get("/api/v1/insight/quote-book",
                 headers=_hdr(c, MANAGER)).json()

    assert body["count"] == 0
    assert "permission" in body["empty_reason"]


# ── the lines on one quote ───────────────────────────────────────────────────

def _line(s, quote_ref: str, ref: str, *, number: int = 0, code: str = "CNMG120408",
          desc: str = "Turning insert", product: str | None = None,
          qty: str | None = "10", rate: str | None = "450",
          amount: str | None = "4500", connection_id: str = "conn1") -> None:
    s.add(models.ErpQuoteLine(
        organization_id=ORG, connector="zoho", connection_id=connection_id,
        external_ref=ref, quote_ref=quote_ref, line_number=number,
        product_id=product, item_code=code, description=desc,
        qty=Decimal(qty) if qty is not None else None, unit="pcs",
        rate=Decimal(rate) if rate is not None else None,
        amount=Decimal(amount) if amount is not None else None,
        source_ref={}))


def test_the_lines_come_back_in_the_order_the_erp_wrote_them(maker):
    s = maker()
    _doc(s, "q1")
    _line(s, "q1", "q1:c", number=2, code="C")
    _line(s, "q1", "q1:a", number=0, code="A")
    _line(s, "q1", "q1:b", number=1, code="B")
    s.commit()
    s.close()

    sess = maker()
    try:
        rows = quote_book.lines_for(sess, ORG, quote_ref="q1")
    finally:
        sess.close()

    assert [r.item_code for r in rows] == ["A", "B", "C"]


def test_only_this_quotes_lines_come_back(maker):
    s = maker()
    _doc(s, "q1")
    _doc(s, "q2")
    _line(s, "q1", "q1:a", code="MINE")
    _line(s, "q2", "q2:a", code="THEIRS")
    s.commit()
    s.close()

    sess = maker()
    try:
        rows = quote_book.lines_for(sess, ORG, quote_ref="q1")
    finally:
        sess.close()

    assert [r.item_code for r in rows] == ["MINE"]


def test_a_line_with_no_price_is_not_read_as_priced_at_nothing(maker):
    s = maker()
    _doc(s, "q1")
    _line(s, "q1", "q1:a", qty=None, rate=None, amount=None)
    s.commit()
    s.close()

    sess = maker()
    try:
        row = quote_book.lines_for(sess, ORG, quote_ref="q1")[0]
    finally:
        sess.close()

    assert (row.qty, row.rate, row.amount) == (None, None, None)
    assert row.to_dict()["rate"] is None


def test_the_endpoint_serves_the_lines_and_says_they_are_held(client):
    body = client.get("/api/v1/insight/quote-book/won/lines",
                      headers=_hdr(client, MANAGER)).json()

    assert body["lines_held"] is False
    assert body["lines"] == []
    # The distinction the empty list cannot carry on its own.
    assert "not been read" in body["empty_reason"]


def test_a_quote_this_reader_may_not_see_is_a_404_rather_than_an_empty_list(client):
    """Not a 403 either: whether a quote exists in a book you cannot read is
    itself something you should not learn."""
    r = client.get("/api/v1/insight/quote-book/theirs/lines",
                   headers=_hdr(client, SALES))

    assert r.status_code == 404


def test_two_books_holding_one_reference_serve_their_own_lines(maker):
    """An ERP reference is unique only inside the book that issued it.

    Two connected Business Central companies both raise ``SQ-1001``. Read on
    the bare reference the endpoint returned both quotes' lines interleaved
    under one document — a breakdown that matches no quote anybody holds.
    """
    s = maker()
    _doc(s, "SQ-1001", connection_id="conn1")
    _doc(s, "SQ-1001", connection_id="conn2")
    _line(s, "SQ-1001", "a:1", code="CNMG120408", connection_id="conn1")
    _line(s, "SQ-1001", "b:1", code="DNMG150608", connection_id="conn2")
    s.commit()
    s.close()

    s = maker()
    try:
        first = quote_book.lines_for(s, ORG, quote_ref="SQ-1001",
                                     connection_id="conn1")
        second = quote_book.lines_for(s, ORG, quote_ref="SQ-1001",
                                      connection_id="conn2")
        unqualified = quote_book.lines_for(s, ORG, quote_ref="SQ-1001")
    finally:
        s.close()
    assert [ln.item_code for ln in first] == ["CNMG120408"]
    assert [ln.item_code for ln in second] == ["DNMG150608"]
    # Unqualified it still reads both, which is why the caller resolves the
    # document first and the endpoint 404s a reference it cannot place.
    assert len(unqualified) == 2


def test_the_lines_endpoint_refuses_a_book_this_reader_cannot_see(client):
    """The qualifier is scoped the way the reference is: naming a company
    whose quotes this reader may not see is the same 404 as naming nothing."""
    ok = client.get("/api/v1/insight/quote-book/won/lines?connection=conn1",
                    headers=_hdr(client, MANAGER))
    assert ok.status_code == 200, ok.text
    assert ok.json()["connection_id"] == "conn1"

    wrong = client.get("/api/v1/insight/quote-book/won/lines?connection=conn9",
                       headers=_hdr(client, MANAGER))
    assert wrong.status_code == 404


def test_a_quote_that_does_not_exist_is_also_a_404(client):
    assert client.get("/api/v1/insight/quote-book/nope/lines",
                      headers=_hdr(client, MANAGER)).status_code == 404


def test_a_line_carries_the_item_masters_name_beside_its_code(maker):
    """The code alone is not what a reader recognises.

    Read from ``Product`` rather than copied onto the line when the quote was
    synced: the ERP payload does carry a name, but a copy taken at quote time
    is a name that drifts the next time somebody renames the item, and this
    screen is read against the catalogue rather than against the document.
    """
    s = maker()
    _doc(s, "q1")
    s.add(models.Product(product_id="p1", organization_id=ORG,
                         external_id="22000865", name="CNMG120408-UC-D2 YC0014",
                         uom="pcs"))
    _line(s, "q1", "q1:a", code="22000865", product="p1")
    s.commit()
    s.close()

    sess = maker()
    try:
        row = quote_book.lines_for(sess, ORG, quote_ref="q1")[0]
    finally:
        sess.close()

    assert row.item_name == "CNMG120408-UC-D2 YC0014"
    assert row.item_code == "22000865"
    assert row.to_dict()["item_name"] == "CNMG120408-UC-D2 YC0014"


def test_a_line_no_catalogue_item_matched_is_kept_and_simply_unnamed(maker):
    """An outer join, not an inner one.

    A quote line naming something that never became a catalogue item is real
    quoting activity — freight, a one-off buy-in — and an inner join would drop
    it from a document the reader is holding in their other hand. The name is
    empty and the code still says what it was.
    """
    s = maker()
    _doc(s, "q1")
    _line(s, "q1", "q1:a", code="FREIGHT", product=None)
    s.commit()
    s.close()

    sess = maker()
    try:
        rows = quote_book.lines_for(sess, ORG, quote_ref="q1")
    finally:
        sess.close()

    assert len(rows) == 1
    assert rows[0].item_name == ""
    assert rows[0].item_code == "FREIGHT"
