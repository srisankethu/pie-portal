"""The two supplier dimensions this book can measure, and what they refuse.

``11-procurement.md`` §3 names three answerable supplier dimensions and rules out
the rest — lead time and OTIF stay refused, because promised dates are blank on
effectively every order here. Settlement behaviour already lives in
``insight/payments.py``. The other two are here: the **credit-note rate**, which
needed vendor credits ingested, and **price stability**, which never did.

Both are mostly refusals on this book, and that is the interesting part of each.
Almost every supplier has issued no credit; most of the catalogue is bought once.
A scorecard whose columns were dutifully filled in for every supplier would be
handing out clean records and stable prices nobody measured — `CLAUDE.md` §1's
*absence of evidence is not a pass*, in a place where it flatters the wrong
party. So each dimension states the sample it was allowed to come from, and the
counts travel with the figure rather than behind it.

---

A supplier with no credits against four bills has not earned a clean record.

``11-procurement.md`` §3 names the credit-note rate as one of three supplier
dimensions this book has evidence for — *once vendor credits are ingested*, which
they now are. Building it naively walks straight into the mistake the reorder
group made: almost every supplier in these books has issued no credit, and a
screen dividing zero by four and printing 0% would hand out clean records to
suppliers nobody has measured. That is `CLAUDE.md` §1's *absence of evidence is
not a pass*, in a column where it flatters the wrong party.

So the floor is **derived from the book's own credit rate** rather than picked:
how many bills a supplier would have had to send before a clean run became
surprising. These tests pin the derivation, both refusals it produces, and the
two counts that make a refusal explainable on screen.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from app.commercial.insight import supply

AS_OF = date(2026, 8, 10)


def _order(vendor_id: str = "v1", **over) -> supply.SupplierOrder:
    fields = dict(
        vendor_id=vendor_id, vendor_label=f"Supplier {vendor_id}", number="PO-1",
        ordered_on=date(2026, 7, 1), expected_on=None, received_on=None,
        pending_qty=0.0, ordered_qty=10.0, total=1000.0, status="closed")
    fields.update(over)
    return supply.SupplierOrder(**fields)


def _build(orders, bills=None, ranges=None) -> dict:
    return supply.build(orders, AS_OF, bills_by_vendor=bills,
                        cost_ranges=ranges)


def _row(built: dict, vendor_id: str) -> dict:
    return next(s for s in built["suppliers"] if s["vendor_id"] == vendor_id)


def _note(built: dict):
    return next((u for u in built["unavailable"]
                 if u["series"] == "supplier_credit_rate"), None)


# ── the floor ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("book_rate, expected", [
    # (1 - p) ** n <= 0.05, solved for n. Checked against the arithmetic rather
    # than against the implementation, so a change to the constant has to be
    # deliberate.
    (0.04, 74),
    (0.10, 29),
    (0.50, 5),
])
def test_the_floor_is_derived_from_the_books_own_rate(book_rate, expected):
    assert supply.min_bills_for_a_credit_rate(book_rate) == expected
    # The definition it is derived from, restated: after this many bills, a
    # supplier with none credited would be a 1-in-20 outcome.
    assert (1 - book_rate) ** expected <= supply.CLEAN_RECORD_SURPRISING_AT


def test_a_book_that_has_seen_no_credit_at_all_has_no_floor():
    """Not a large number — no number. A book whose vendor-credit scope was
    never granted has a rate of zero for structural reasons, and any floor
    computed from it would let every supplier report as clean."""
    assert supply.min_bills_for_a_credit_rate(0.0) is None


# ── the two refusals ────────────────────────────────────────────────────────
def test_a_supplier_below_the_floor_gets_counts_and_no_rate():
    """"0 of 4 bills" is four bills, not a clean record."""
    built = _build([_order("v1")], bills={
        "v1": supply.VendorBills("v1", bills=4, credited_bills=0),
        # Enough elsewhere in the book to establish a rate, so the refusal below
        # is about v1's sample and not about the book having no credits.
        "v2": supply.VendorBills("v2", bills=100, credited_bills=10),
    })
    row = _row(built, "v1")

    assert row["credit_rate"] is None
    assert (row["bills"], row["credited_bills"]) == (4, 0)
    # The counts are what make the null explainable on the screen. Without them
    # "no rate" is a bug report waiting to be filed.
    assert row["min_bills_for_credit_rate"] == built["min_bills_for_credit_rate"]
    assert _note(built)["reason"].startswith("1 supplier(s) have sent too few")


def test_a_book_with_no_credits_read_reports_nobody_as_clean():
    """The impossible-zero trap, in the column where it flatters a supplier.

    Every supplier here has zero credits against real bills, and not one of them
    gets a rate — because a book that never read its vendor credits looks exactly
    like a book whose suppliers never made a mistake, and nothing on this screen
    can tell those apart.
    """
    built = _build([_order("v1"), _order("v2")], bills={
        "v1": supply.VendorBills("v1", bills=400, credited_bills=0),
        "v2": supply.VendorBills("v2", bills=300, credited_bills=0),
    })

    assert built["min_bills_for_credit_rate"] is None
    assert all(s["credit_rate"] is None for s in built["suppliers"])
    assert "may mean the vendor-credit scope was never granted" in \
        _note(built)["reason"]


def test_no_bills_read_at_all_is_said_out_loud(): 
    """A connection without the bills grant supplies nothing, and the screen
    says so rather than rendering 0 of 0."""
    built = _build([_order("v1")])

    assert _row(built, "v1")["credit_rate"] is None
    assert _row(built, "v1")["bills"] == 0
    assert _note(built)["reason"].startswith("No supplier bills have been read")


# ── the measurement, where it is earned ─────────────────────────────────────
def test_a_supplier_past_the_floor_gets_its_rate():
    built = _build([_order("v1"), _order("v2")], bills={
        "v1": supply.VendorBills("v1", bills=100, credited_bills=7),
        "v2": supply.VendorBills("v2", bills=100, credited_bills=13),
    })

    assert _row(built, "v1")["credit_rate"] == 0.07
    assert _row(built, "v2")["credit_rate"] == 0.13
    assert built["counts"]["suppliers_with_a_credit_rate"] == 2
    # The book rate the floor came from, published so the floor can be checked
    # rather than taken on trust.
    assert built["book_credit_rate"] == 0.10
    assert built["min_bills_for_credit_rate"] == 29


def test_a_clean_record_past_the_floor_is_a_finding_and_reads_as_zero():
    """The other side of the rule, and the reason it is not simply "hide small
    numbers": a supplier that has sent enough bills for a clean run to be
    surprising has genuinely earned the nought, and must get it."""
    built = _build([_order("v1"), _order("v2")], bills={
        "v1": supply.VendorBills("v1", bills=200, credited_bills=0),
        "v2": supply.VendorBills("v2", bills=100, credited_bills=20),
    })

    assert _row(built, "v1")["credit_rate"] == 0.0
    assert _row(built, "v1")["bills"] == 200


def test_the_book_rate_counts_bills_from_suppliers_this_screen_never_shows():
    """The list is built from purchase orders; the rate is a property of the
    book. A supplier with bills and no orders is invisible above and still
    belongs in the denominator the floor is derived from — otherwise the floor
    moves with who happens to have an order open."""
    with_ghost = _build([_order("v1")], bills={
        "v1": supply.VendorBills("v1", bills=50, credited_bills=5),
        # No purchase order, so no row on the screen.
        "v9": supply.VendorBills("v9", bills=50, credited_bills=15),
    })

    assert [s["vendor_id"] for s in with_ghost["suppliers"]] == ["v1"]
    assert with_ghost["counts"]["bills_read"] == 100
    assert with_ghost["book_credit_rate"] == 0.20


# ── §1 ──────────────────────────────────────────────────────────────────────
def test_the_credit_dimension_is_counts_only_and_carries_no_money():
    """A value ratio would be a fraction of purchase spend — cost by another
    name, in the sense that already scopes this screen — and it would answer a
    worse question: a scorecard asks how often a supplier gets an order wrong,
    not what the corrections came to."""
    fields = set(supply.VendorBills.__dataclass_fields__)

    assert fields == {"vendor_id", "bills", "credited_bills"}
    built = _build([_order("v1")], bills={
        "v1": supply.VendorBills("v1", bills=100, credited_bills=4)})
    row = _row(built, "v1")
    for key in row:
        assert "credit_value" not in key and "credit_amount" not in key


def test_the_floor_never_falls_below_one_bill():
    """Guards the degenerate end of the derivation: a book where every bill
    draws a credit still needs at least one bill before it says anything."""
    assert supply.min_bills_for_a_credit_rate(1.0) == 1
    assert supply.min_bills_for_a_credit_rate(0.999) >= 1
    assert math.isclose(supply.CLEAN_RECORD_SURPRISING_AT, 0.05)


# ── the join, end to end ────────────────────────────────────────────────────
#
# The one part the unit tests above cannot reach. `credited_bills` comes from a
# join between `VendorCreditApplication.bill_external_ref` and
# `BillDoc.external_ref`, and a join that matches nothing returns zeros — which
# on this screen renders as a clean record for every supplier in the book. A
# silent zero is exactly the failure mode this whole dimension is built to
# avoid, so the join is exercised rather than assumed.

def _seed_supplier_book(session, org: str) -> None:
    from app.domain import models

    session.add(models.Vendor(vendor_id="ven1", organization_id=org,
                              external_id="v-ext-1", name="Kennametal"))
    # Flushed before anything points at it: three tables below carry a foreign
    # key to this row, and SQLite checks them on insert.
    session.flush()
    session.add(models.PurchaseOrderDoc(
        organization_id=org, external_ref="po-1", vendor_id="ven1",
        date=date(2026, 7, 1), status="closed", ordered_qty=10, pending_qty=0,
        total=1000))
    # Three bills, one of which draws two separate credits — the shape the
    # Kennametal document has, and the one that breaks a naive count.
    for i in range(3):
        session.add(models.BillDoc(
            organization_id=org, external_ref=f"bill-{i}", number=f"BN-{i}",
            vendor_id="ven1", date=date(2026, 7, 2), status="open",
            total=1000, balance=1000))
    credit = models.VendorCreditDoc(
        organization_id=org, external_ref="vc-1", number="01/FY25",
        vendor_id="ven1", date=date(2026, 7, 10), status="closed",
        total=500, balance=0)
    session.add(credit)
    session.flush()
    for n, amount in ((1, 300), (2, 200)):
        session.add(models.VendorCreditApplication(
            organization_id=org, external_ref=f"vca-{n}",
            vendor_credit_id=credit.vendor_credit_id, vendor_id="ven1",
            bill_external_ref="bill-0", bill_number="BN-0",
            amount_applied=amount))
    session.commit()


def test_two_credits_against_one_bill_is_one_credited_bill(api_client, session):
    """Counted distinctly, or a supplier reports more corrections than invoices.

    One vendor credit is routinely spread over several bills and one bill can
    draw several credits. Counting applications would make this supplier's rate
    2 of 3; the answer is 1 of 3.
    """
    from app.config import settings
    from app.seed import SEED_PASSWORD

    org = settings.DEFAULT_ORG_ID
    _seed_supplier_book(session, org)

    token = api_client.post("/api/v1/auth/login",
                            json={"email": "m.rao@pie.example",
                                  "password": SEED_PASSWORD}).json()["token"]
    body = api_client.get("/api/v1/insight/supply",
                          headers={"Authorization": f"Bearer {token}"}).json()

    row = next(s for s in (body.get("suppliers") or [])
               if s["vendor_id"] == "ven1")
    assert (row["bills"], row["credited_bills"]) == (3, 1), (
        "the bill-to-credit join is counting applications, or matching nothing")
    assert body["counts"]["bills_read"] == 3
    assert body["counts"]["bills_with_a_credit"] == 1


# ── price stability: the third dimension, and its long refusal ──────────────
#
# `11-procurement.md` §3's third answerable supplier dimension. It needs an item
# bought from the same supplier twice, and `08-intermittent-demand.md` measured
# that most of this catalogue moves once — so the refusal is the common case and
# the figure is the exception, which is the opposite of how a scorecard column
# usually behaves and the reason the counts travel with it.

def _range(vendor_id="v1", product_id="p1", purchases=2, lowest="100",
           highest="120") -> supply.ItemCostRange:
    from decimal import Decimal
    return supply.ItemCostRange(
        vendor_id=vendor_id, product_id=product_id, purchases=purchases,
        lowest=Decimal(lowest), highest=Decimal(highest))


def test_a_spread_is_how_far_the_cost_ranged():
    assert _range(lowest="100", highest="120").spread == 0.2
    assert _range(lowest="400", highest="401.25").spread == 0.0031


def test_one_purchase_has_no_movement_to_report():
    """Not zero. A single purchase is not a stable price, it is a price — and a
    0% spread is the strongest possible claim about stability."""
    assert _range(purchases=1).spread is None


def test_a_zero_lowest_cost_reports_nothing_rather_than_infinity():
    """A free line is a data-entry artefact, and dividing by it would put an
    infinite spread on a supplier's headline. `master-hygiene-watch` exists
    because placeholder costs are real in these books."""
    assert _range(lowest="0", highest="120").spread is None
    assert _range(lowest="-5", highest="120").spread is None


def test_a_typical_spread_needs_more_than_two_items():
    """A "typical" over two items is two items — the same rule and the same
    number as the lead time directly above it on this screen."""
    two = _build([_order("v1")], ranges=[_range(product_id="p1"),
                                         _range(product_id="p2")])
    three = _build([_order("v1")], ranges=[_range(product_id="p1"),
                                           _range(product_id="p2"),
                                           _range(product_id="p3")])

    assert _row(two, "v1")["typical_price_spread"] is None
    assert _row(two, "v1")["repeat_bought_items"] == 2
    assert _row(three, "v1")["typical_price_spread"] == 0.2


def test_the_typical_spread_is_a_median_so_one_wild_line_cannot_set_it():
    """One item whose cost trebled must not become the supplier's headline —
    the reason `typical_lead_time` is a median, applied to the same screen."""
    built = _build([_order("v1")], ranges=[
        _range(product_id="p1", lowest="100", highest="105"),
        _range(product_id="p2", lowest="100", highest="110"),
        _range(product_id="p3", lowest="100", highest="400"),
    ])

    assert _row(built, "v1")["typical_price_spread"] == 0.1


def test_a_supplier_with_no_repeat_lines_says_so_and_claims_nothing():
    built = _build([_order("v1")])
    row = _row(built, "v1")

    assert row["typical_price_spread"] is None
    assert row["repeat_bought_items"] == 0
    note = next(u for u in built["unavailable"]
                if u["series"] == "supplier_price_spread")
    assert "bought from them more than once" in note["reason"]


def test_the_spread_carries_no_rupee_figure():
    """§1-adjacent, and a design choice rather than a requirement: /supply is
    manager-and-above so cost would be permitted. A spread is scale-free and the
    levels are not, and a scorecard needs how far a price moved rather than what
    it was — which is what would let this be shown more widely later without the
    question being reopened."""
    built = _build([_order("v1")], ranges=[
        _range(product_id=f"p{i}", lowest="9999", highest="19998")
        for i in range(3)])
    row = _row(built, "v1")

    assert row["typical_price_spread"] == 1.0
    for key, value in row.items():
        assert "9999" not in str(value), f"{key} leaked a cost level"



def test_only_repeat_bought_lines_reach_the_price_spread(api_client, session):
    """The other half the unit tests cannot reach: ``HAVING count >= 2``.

    The catalogue is mostly bought-once, so the ``GROUP BY`` has to drop those
    at the database rather than hand them up to be filtered — and a HAVING that
    is off by one either loads the whole catalogue or silently reports nothing.
    Three items here: one bought twice at different costs, one bought twice at
    the same cost, one bought once.
    """
    from decimal import Decimal

    from app.config import settings
    from app.domain import models
    from app.seed import SEED_PASSWORD

    org = settings.DEFAULT_ORG_ID
    _seed_supplier_book(session, org)
    costs = [("moved", "100"), ("moved", "125"),
             ("flat", "50"), ("flat", "50"),
             ("once", "900")]
    for i, (product, unit) in enumerate(costs):
        session.add(models.CostRecord(
            organization_id=org, external_ref=f"bill-x:{i}", product_id=product,
            vendor_id="ven1", date=date(2026, 7, 3), qty=Decimal("1"),
            unit_cost=Decimal(unit)))
    session.commit()

    token = api_client.post("/api/v1/auth/login",
                            json={"email": "m.rao@pie.example",
                                  "password": SEED_PASSWORD}).json()["token"]
    body = api_client.get("/api/v1/insight/supply",
                          headers={"Authorization": f"Bearer {token}"}).json()

    row = next(s for s in body["suppliers"] if s["vendor_id"] == "ven1")
    # "once" is dropped by the HAVING; "flat" is a real observation of a price
    # that did not move and must count as one, not be mistaken for no data.
    assert row["repeat_bought_items"] == 2
    assert body["counts"]["repeat_bought_items"] == 2
    # Two items is still below the floor, so no typical spread is claimed.
    assert row["typical_price_spread"] is None


def test_a_supplier_with_credits_against_few_bills_is_still_reported():
    """The asymmetry a review caught. The floor answers one question — after how
    many bills is *no* credit surprising — and applying it to every claim hid
    the supplier whose record most warranted opening.

    Three credits against four bills is far past any evidence bar for a non-zero
    rate. The screen's footnote filters on a present rate, so suppressed here
    meant absent from the screen entirely.
    """
    built = _build([_order("v1"), _order("v2")], bills={
        "v1": supply.VendorBills("v1", bills=4, credited_bills=3),
        "v2": supply.VendorBills("v2", bills=200, credited_bills=10),
    })

    assert _row(built, "v1")["credit_rate"] == 0.75
    assert _row(built, "v1")["bills"] == 4
    # And the clean claim still needs the floor, which is the whole point of
    # having one: nothing here has been loosened for a nought.
    clean = _build([_order("v1"), _order("v2")], bills={
        "v1": supply.VendorBills("v1", bills=4, credited_bills=0),
        "v2": supply.VendorBills("v2", bills=200, credited_bills=10),
    })
    assert _row(clean, "v1")["credit_rate"] is None


def test_a_supplier_with_no_bills_at_all_claims_nothing_either_way():
    """Zero over zero is not a rate, however the floor is set."""
    built = _build([_order("v1")], bills={
        "v1": supply.VendorBills("v1", bills=0, credited_bills=0)})

    assert _row(built, "v1")["credit_rate"] is None
