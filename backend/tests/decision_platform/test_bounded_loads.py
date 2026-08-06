"""Every bounded load must give the same answer as the unbounded one.

A bound is a claim: *the rows I excluded could not have changed my result*. That
claim is cheap to make and expensive to be wrong about — a screen that quietly
drops half its evidence looks like a speed-up and behaves like a loosened rule.

So each bound in the codebase gets a test here that runs the real consumer both
ways against a book with plenty of rows it must ignore, and compares the output.
The snapshot is also checked for the property that makes bounding safe at all:
its reference date comes from the whole book, not from the rows it happens to
hold.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain import models
from app.signals import aggregates as agg
from app.signals.base import Snapshot
from app.signals.config import load_thresholds
from app.signals.quote_context import assemble

ORG = "org_bounded"


@pytest.fixture()
def book(session):
    """Three customers, four items, and history spread over two years.

    Deliberately more than any bounded caller wants: if a bound leaks, the
    extra rows show up in the comparison.
    """
    for i in range(3):
        session.add(models.Customer(customer_id=f"c{i}", organization_id=ORG,
                                    external_id=f"zc{i}", name=f"Customer {i}",
                                    source_ref={}))
    for i in range(4):
        session.add(models.Product(product_id=f"p{i}", organization_id=ORG,
                                   external_id=f"zp{i}", name=f"Item {i}",
                                   source_ref={}))
    start = date(2024, 6, 1)
    n = 0
    for ci in range(3):
        for pi in range(4):
            for k in range(6):
                when = start + timedelta(days=45 * k + 7 * pi)
                session.add(models.SalesTxn(
                    sales_txn_id=f"t{n}", organization_id=ORG,
                    external_ref=f"inv{n}:l1", customer_id=f"c{ci}",
                    product_id=f"p{pi}", date=when,
                    qty=Decimal("4"), unit_price=Decimal(f"{500 + 10 * k}"),
                    line_revenue=Decimal(f"{4 * (500 + 10 * k)}"),
                    source_ref={"system": "zoho", "record_type": "invoice",
                                "record_id": f"inv{n}", "line_id": "l1"}))
                n += 1
    for pi in range(4):
        for k in range(4):
            session.add(models.CostRecord(
                cost_record_id=f"k{pi}{k}", organization_id=ORG,
                external_ref=f"bill{pi}{k}:l1", product_id=f"p{pi}",
                date=start + timedelta(days=70 * k),
                qty=Decimal("50"), unit_cost=Decimal(f"{380 + 5 * k}"),
                rate=Decimal(f"{380 + 5 * k}"),
                source_ref={"system": "zoho", "record_type": "bill",
                            "record_id": f"bill{pi}{k}"}))
    session.commit()
    return session


# ── the property that makes bounding safe ───────────────────────────────────
def test_the_reference_date_is_the_whole_book_s_even_when_the_load_is_bounded(book):
    """Derived from the loaded rows, a quiet account's own screen would report
    that the business had stopped trading on the day *it* last bought."""
    everything = agg.load_snapshot(book, ORG)
    # c0's last purchase is earlier than the book's, because p3's lines run on.
    book.add(models.SalesTxn(
        sales_txn_id="late", organization_id=ORG, external_ref="invLate:l1",
        customer_id="c1", product_id="p0", date=date(2026, 5, 1),
        qty=Decimal("1"), unit_price=Decimal("900"), line_revenue=Decimal("900"),
        source_ref={"system": "zoho", "record_type": "invoice",
                    "record_id": "invLate", "line_id": "l1"}))
    book.commit()

    bounded = agg.load_snapshot(book, ORG, sales_for_customers=["c0"])
    assert bounded.as_of() == date(2026, 5, 1)
    assert bounded.as_of() == agg.load_snapshot(book, ORG).as_of()
    assert max(s.date for s in bounded.sales) < date(2026, 5, 1)
    assert everything.as_of() is not None


def test_a_snapshot_built_by_hand_still_derives_its_own_reference_date():
    """The loader supplies it; a test fixture does not have to. Detector tests
    build snapshots directly and must keep behaving exactly as they did."""
    snap = Snapshot(organization_id="org_x", sales=[
        agg.SaleRow(customer_id="c", product_id="p", date=date(2026, 3, 4),
                    qty=Decimal(1), unit_price=Decimal(1), line_revenue=Decimal(1),
                    source_ref={}, external_ref="r")], costs=[])
    assert snap.as_of() == date(2026, 3, 4)


def test_an_empty_book_has_no_reference_date_rather_than_today(book):
    assert agg.load_snapshot(book, "org_with_nothing").as_of() is None


# ── the bounds themselves ───────────────────────────────────────────────────
def test_the_quote_bound_gives_the_same_facts_as_loading_everything(book):
    """``assemble`` reads one customer's lines, the requested products' costs
    and the product names. Bounding to exactly those must be invisible."""
    th = load_thresholds()
    as_of = date(2026, 4, 1)
    products = ["p1", "p2"]

    whole = assemble(agg.load_snapshot(book, ORG), "c1", products, th, as_of)
    bounded = assemble(
        agg.load_snapshot(book, ORG, sales_for_customers=["c1"],
                          costs_for_products=products),
        "c1", products, th, as_of)

    assert bounded == whole


def test_narrowing_a_quote_s_sales_by_product_would_move_the_customer_s_rhythm(book):
    """Why the two bounds are separate. The first version of this narrowed both
    by product, which looked tidier and shifted ``days_since_last_order`` by a
    week — the customer's most recent purchase was of an item nobody was
    quoting. This asserts the wrong bound is still wrong, so the tidier version
    cannot come back."""
    th = load_thresholds()
    as_of = date(2026, 4, 1)
    products = ["p1", "p2"]

    whole = assemble(agg.load_snapshot(book, ORG), "c1", products, th, as_of)
    too_tight = agg.load_snapshot(book, ORG, sales_for_customers=["c1"],
                                  costs_for_products=products)
    too_tight.sales = [s for s in too_tight.sales if s.product_id in products]
    too_tight._by_customer = None
    assert assemble(too_tight, "c1", products, th, as_of) != whole


def test_the_quote_bound_actually_excludes_the_rest_of_the_book(book):
    """Guards the test above from passing because the bound did nothing."""
    whole = agg.load_snapshot(book, ORG)
    bounded = agg.load_snapshot(book, ORG, sales_for_customers=["c1"],
                                costs_for_products=["p1", "p2"])
    assert len(bounded.sales) < len(whole.sales)
    assert len(bounded.costs) < len(whole.costs)
    assert {s.customer_id for s in bounded.sales} == {"c1"}
    assert {c.product_id for c in bounded.costs} == {"p1", "p2"}


def test_the_customer_timeline_bound_gives_the_same_rows(book):
    """The timeline reads one customer's lines and nothing else from the
    snapshot; its costs come from ``CustomerItemMetric``."""
    whole = agg.load_snapshot(book, ORG).sales_for_customer("c2")
    bounded = agg.load_snapshot(
        book, ORG, sales_for_customers=["c2"],
        costs_for_products=[]).sales_for_customer("c2")
    assert bounded == whole


def test_a_caller_that_reads_no_costs_can_say_so(book):
    """The customer timeline's margin series comes from ``CustomerItemMetric``,
    so every cost record it loaded was pure waste. An empty list is a real
    bound, not a mistake."""
    bounded = agg.load_snapshot(book, ORG, sales_for_customers=["c2"],
                                costs_for_products=[])
    assert bounded.costs == []
    assert bounded.sales, "sales must be unaffected by a costs-only bound"


def test_names_are_never_bounded_away(book):
    """A bounded screen still has to be able to label a row. The name
    dictionaries are two small indexed reads, not a scan of the lines."""
    bounded = agg.load_snapshot(book, ORG, sales_for_customers=["c0"],
                                costs_for_products=["p0"])
    assert len(bounded.customer_names) == 3
    assert len(bounded.product_names) == 4


# ── the index that turned the detector run from quadratic to linear ─────────
def test_the_per_entity_accessors_agree_with_a_plain_scan(book):
    """The indexes replaced a filter-per-lookup. Same rows, same order."""
    snap = agg.load_snapshot(book, ORG)
    for cid in snap.customer_ids():
        assert snap.sales_for_customer(cid) == [
            s for s in snap.sales if s.customer_id == cid]
    for pid in snap.product_ids():
        assert snap.sales_for_product(pid) == [
            s for s in snap.sales if s.product_id == pid]
        assert snap.costs_for_product(pid) == sorted(
            (c for c in snap.costs if c.product_id == pid), key=lambda c: c.date)


def test_an_entity_with_no_rows_gets_an_empty_list_not_a_key_error(book):
    snap = agg.load_snapshot(book, ORG)
    assert snap.sales_for_customer("nobody") == []
    assert snap.sales_for_product("nothing") == []
    assert snap.costs_for_product("nothing") == []


def test_the_index_is_built_once_and_reused(book):
    """Repeated lookups must not re-group. The detectors ask per entity, in a
    loop, which is exactly the shape that made this quadratic."""
    snap = agg.load_snapshot(book, ORG)
    snap.sales_for_customer("c0")
    first = snap._by_customer
    snap.sales_for_customer("c1")
    assert snap._by_customer is first


def test_the_shared_grouping_is_one_implementation(book):
    """``aggregates.by_customer`` and the snapshot's own index must group the
    same way, or a merged identity would mean two different things."""
    snap = agg.load_snapshot(book, ORG)
    assert agg.by_customer(snap.sales)["c1"] == snap.sales_for_customer("c1")


# ── the detectors, both ways ────────────────────────────────────────────────
def test_detector_output_is_unchanged_by_the_indexing(book):
    """The optimisation is only worth having if it changed nothing. Compared
    against a snapshot whose accessors are forced to scan."""
    from app.signals.engine import compute_drafts

    th = load_thresholds()
    indexed = agg.load_snapshot(book, ORG)

    scanning = agg.load_snapshot(book, ORG)
    scanning.sales_for_customer = lambda cid: [        # type: ignore[method-assign]
        s for s in scanning.sales if s.customer_id == cid]
    scanning.sales_for_product = lambda pid: [        # type: ignore[method-assign]
        s for s in scanning.sales if s.product_id == pid]
    scanning.costs_for_product = lambda pid: sorted(   # type: ignore[method-assign]
        (c for c in scanning.costs if c.product_id == pid), key=lambda c: c.date)

    as_of = date(2026, 4, 1)
    a = [(d.signal_type, d.subject_entity_id, d.metrics, d.sufficiency) for d in
         compute_drafts(indexed, th, as_of)]
    b = [(d.signal_type, d.subject_entity_id, d.metrics, d.sufficiency) for d in
         compute_drafts(scanning, th, as_of)]
    assert a == b
