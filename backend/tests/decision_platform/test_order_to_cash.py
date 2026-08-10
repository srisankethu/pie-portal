"""Order → invoice → cash, and the refusals that keep the number honest.

Three groups, and the second is the one that matters most.

*Ingestion* pins the many-to-many. The invoice header carried no order
reference at all before this, and both awkward directions are real in this book:
one order is invoiced in parts, and one invoice consolidates several orders. A
model that held Zoho's scalar ``salesorder_id`` alone would pass every
single-order test and lose half the links on the invoices worth looking at.

*The refusals* pin what happens when the evidence is not there. An invoice with
no order behind it has **no** order-to-invoice lag — stock sold across the
counter never had one — and the tempting reading of that is zero, which is the
fastest possible conversion and drags the median toward "we bill instantly".
``CLAUDE.md`` §1: absence of evidence is not a pass. The same trap sits at three
other points, and each has a test here.

*Reuse* pins that stage 2 is ``payments.Settlement.days_to_pay`` rather than a
second subtraction of the same two dates. Two copies would agree until the first
one was fixed.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.commercial.insight import order_to_cash, payments
from app.domain import models
from app.ingestion.normalize import normalize_invoice_terms
from app.ingestion.sync import SyncService

from .test_sync_persistence import _Source

ORG = "org_otc"


# ── ingestion: the link table, and both directions of the many-to-many ───────
class _WithOrders(_Source):
    """The configurable source plus the sales-order pull.

    Subclassed rather than adding a constructor argument to ``_Source`` for the
    reason ``test_credit_notes`` gives about its own subclass: the pull is
    probed for with ``hasattr``, so a source without it must stay possible.
    """

    def __init__(self, sales_orders=None, **kw):
        super().__init__(**kw)
        self._so = sales_orders or []

    def list_sales_orders(self, skip=None):
        return list(self._so)


def _invoice(ref: str, *, day: str, orders: list[dict] | None = None,
             primary: str | None = None, balance: float = 0.0,
             modified: str = "") -> dict:
    raw: dict = {
        "invoice_id": ref, "invoice_number": f"INV-{ref}", "customer_id": "c1",
        "date": day, "status": "paid", "total": 5000, "balance": balance,
        "last_modified_time": modified,
        "line_items": [{"line_item_id": f"l{ref}", "item_id": "i1",
                        "quantity": 10, "rate": 500, "item_total": 5000}],
    }
    if orders is not None:
        raw["salesorders"] = orders
    if primary is not None:
        raw["salesorder_id"] = primary
        raw["salesorder_number"] = f"SO-{primary}"
    return raw


def _source(invoices, sales_orders=None) -> _WithOrders:
    return _WithOrders(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
        invoices=invoices,
        sales_orders=sales_orders or [],
    )


def _links(session, org=ORG):
    return session.query(models.InvoiceSalesOrderLink).filter_by(
        organization_id=org).all()


def test_the_salesorders_array_is_read_and_the_scalar_is_only_flagged(session):
    """Both orders survive, and Zoho's primary is recorded rather than obeyed.

    The scalar is the failure this guards: reading it alone would keep ``so1``
    and drop ``so2`` on exactly the consolidated invoices whose cycle time is
    most worth seeing.
    """
    SyncService(session, _source([
        _invoice("inv1", day="2026-06-10",
                 orders=[{"salesorder_id": "so1", "salesorder_number": "SO-A"},
                         {"salesorder_id": "so2", "salesorder_number": "SO-B"}],
                 primary="so1"),
    ]), ORG).run()
    session.commit()

    links = {link.sales_order_external_ref: link for link in _links(session)}
    assert set(links) == {"so1", "so2"}
    assert links["so1"].is_primary is True
    assert links["so2"].is_primary is False
    assert links["so2"].sales_order_number == "SO-B"


def test_a_scalar_the_array_omits_is_kept_rather_than_dropped(session):
    """Union, not "the array is authoritative and the scalar is noise".

    The two failure modes are not symmetric: a scalar already in the array only
    gets flagged, and a scalar the array omits is a link this platform would
    otherwise never hold.
    """
    SyncService(session, _source([
        _invoice("inv1", day="2026-06-10", orders=[], primary="so9"),
    ]), ORG).run()
    session.commit()

    link = _links(session)[0]
    assert (link.sales_order_external_ref, link.is_primary) == ("so9", True)
    assert link.sales_order_number == "SO-so9"


def test_one_order_invoiced_in_parts_links_to_every_invoice(session):
    """Partial invoicing is normal here — verified on `HYD/FY27/SO-399`, which
    appears on both `INV-696` and `INV-663`. Both links must exist."""
    SyncService(session, _source([
        _invoice("inv1", day="2026-06-10",
                 orders=[{"salesorder_id": "so1", "salesorder_number": "SO-A"}]),
        _invoice("inv2", day="2026-07-02",
                 orders=[{"salesorder_id": "so1", "salesorder_number": "SO-A"}]),
    ]), ORG).run()
    session.commit()

    assert {(link.invoice_external_ref, link.sales_order_external_ref)
            for link in _links(session)} == {("inv1", "so1"), ("inv2", "so1")}


def test_an_invoice_with_no_order_is_a_complete_record_not_a_defect(session):
    """Counter sales have no order. No link, no skip, and the invoice survives."""
    report = SyncService(session, _source([
        _invoice("inv1", day="2026-06-10"),
    ]), ORG).run()
    session.commit()

    assert _links(session) == []
    assert session.query(models.InvoiceDoc).count() == 1
    assert [s for s in report.skipped if s.get("kind") == "receivable"] == []


def test_relinking_an_invoice_removes_the_order_it_no_longer_bills(session):
    """Replace, do not merge — the rule ``_replace_applications`` states.

    A stale link is a phantom in exactly the series that decides how long an
    order takes to be invoiced.

    The two runs carry different ``last_modified_time`` stamps because that is
    what re-linking an invoice in Zoho does. Without one the resume predicate
    correctly skips the document altogether, and the test would be asserting on
    a pull that never happened.
    """
    SyncService(session, _source([
        _invoice("inv1", day="2026-06-10", modified="2026-06-10T09:00:00",
                 orders=[{"salesorder_id": "so1"}, {"salesorder_id": "so2"}]),
    ]), ORG).run()
    session.commit()
    assert len(_links(session)) == 2

    SyncService(session, _source([
        _invoice("inv1", day="2026-06-10", modified="2026-06-12T15:00:00",
                 orders=[{"salesorder_id": "so2"}]),
    ]), ORG).run()
    session.commit()

    assert [link.sales_order_external_ref for link in _links(session)] == ["so2"]


def test_a_repeated_order_on_one_invoice_makes_one_link():
    """Normalisation dedupes, so the unique constraint is never the thing that
    notices. Two links to one order would double-count nothing but would make
    ``consolidated`` claim an invoice covers two orders when it covers one."""
    terms = normalize_invoice_terms({
        "invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-10",
        "salesorders": [{"salesorder_id": "so1"}, {"salesorder_id": "so1"}],
        "salesorder_id": "so1",
    })
    assert [r.external_ref for r in terms.sales_orders] == ["so1"]


# ── measurement ─────────────────────────────────────────────────────────────
def _cycle(ref: str, *, invoice_day: int, order_day: int | None = None,
           orders_linked: int | None = None, days_to_pay: int | None = None,
           outstanding: bool | None = False) -> order_to_cash.Cycle:
    """One invoice, positioned by day-of-month in a single month for legibility."""
    base = date(2026, 6, 1)
    return order_to_cash.Cycle(
        invoice_ref=ref, invoice_number=f"INV-{ref}", customer_id="c1",
        customer_label="Acme",
        invoice_date=base + timedelta(days=invoice_day),
        order_date=(base + timedelta(days=order_day) if order_day is not None
                    else None),
        orders_linked=(orders_linked if orders_linked is not None
                       else (1 if order_day is not None else 0)),
        order_numbers=("SO-A",) if order_day is not None else (),
        days_to_pay=days_to_pay, outstanding=outstanding,
    )


def _stage(built: dict, key: str) -> dict:
    return next(s for s in built["stages"] if s["key"] == key)


def test_a_counter_sale_is_unknown_not_a_zero_day_conversion():
    """The refusal this whole module exists around.

    Three invoices took ten days from order to invoice. A fourth has no order at
    all. Reading that as zero would pull the median to five and flatter the one
    half of the cycle this business controls — so the fourth is not an
    observation, and the reason it is not is named.
    """
    built = order_to_cash.build([
        _cycle("a", invoice_day=10, order_day=0, days_to_pay=30),
        _cycle("b", invoice_day=11, order_day=1, days_to_pay=30),
        _cycle("c", invoice_day=12, order_day=2, days_to_pay=30),
        _cycle("d", invoice_day=13, days_to_pay=30),
    ], date(2026, 8, 1))

    stage = _stage(built, "ORDER_TO_INVOICE")
    assert stage["median_days"] == 10.0
    assert (stage["measured"], stage["unknown"]) == (3, 1)
    assert stage["unknown_by_reason"] == {"NO_ORDER": 1}
    assert built["coverage"]["without_order"] == 1


def test_an_order_the_platform_does_not_hold_is_not_the_same_as_no_order():
    """Two different facts, and only one of them is a gap in the sync window.

    Collapsing them would tell somebody their book is full of counter sales when
    it is full of orders raised before the pull begins.
    """
    built = order_to_cash.build([
        _cycle("a", invoice_day=10, order_day=None, orders_linked=2),
        _cycle("b", invoice_day=11),
    ], date(2026, 8, 1))

    stage = _stage(built, "ORDER_TO_INVOICE")
    assert stage["unknown_by_reason"] == {"ORDER_NOT_HELD": 1, "NO_ORDER": 1}
    assert built["coverage"]["order_not_held"] == 1
    assert built["coverage"]["without_order"] == 1


def test_a_consolidated_invoice_measures_from_the_earliest_order():
    """The stated rule, and the reason it is not Zoho's primary.

    Which order the scalar names is an arbitrary tie-break inside a payload. The
    oldest order is the one that waited longest, and that wait is the finding.
    """
    cycle = _cycle("a", invoice_day=20, order_day=0, orders_linked=3)
    built = order_to_cash.build([cycle], date(2026, 8, 1))

    assert built["invoices"][0]["order_to_invoice_days"] == 20
    assert built["invoices"][0]["consolidated"] is True
    assert built["coverage"]["consolidated"] == 1
    assert built["rule"]["code"] == "EARLIEST_LINKED_ORDER"
    # The screen has to be able to say what the number dropped.
    assert built["rule"]["drops"]


def test_an_unsettled_invoice_reports_no_payment_leg_rather_than_days_so_far():
    """The clock is still running, so there is no cycle time. Days elapsed would
    read as a completed cycle and would shorten every month as it aged."""
    built = order_to_cash.build([
        _cycle("a", invoice_day=0, order_day=0, days_to_pay=None,
               outstanding=True),
    ], date(2026, 8, 1))

    assert _stage(built, "INVOICE_TO_CASH")["unknown_by_reason"] == {"STILL_OWED": 1}
    assert built["invoices"][0]["invoice_to_cash_days"] is None


def test_an_invoice_cleared_without_a_receipt_is_not_an_invoice_paid_instantly():
    """Nothing owed and nothing received — usually a credit note. Cleared is not
    paid, and it carries no payment date to measure to."""
    built = order_to_cash.build([
        _cycle("a", invoice_day=0, order_day=0, days_to_pay=None,
               outstanding=False),
    ], date(2026, 8, 1))

    assert (_stage(built, "INVOICE_TO_CASH")["unknown_by_reason"]
            == {"NO_RECEIPT_ON_RECORD": 1})


def test_an_invoice_with_no_balance_on_record_is_never_read_as_collected():
    """`None` is a third answer. Treating it as settled is the `sum(… or 0)`
    tell ``CLAUDE.md`` §1 names, wearing a boolean."""
    built = order_to_cash.build([
        _cycle("a", invoice_day=0, order_day=0, days_to_pay=12,
               outstanding=None),
    ], date(2026, 8, 1))

    assert (_stage(built, "INVOICE_TO_CASH")["unknown_by_reason"]
            == {"BALANCE_UNKNOWN": 1})
    assert built["invoices"][0]["invoice_to_cash_days"] is None


def test_the_total_stage_names_the_missing_evidence_of_the_leg_that_failed():
    """An invoice with no order and no payment is unmeasurable for the order
    reason first. Saying "still owed" about a counter sale names the wrong
    problem to the person who has to fix it."""
    built = order_to_cash.build([
        _cycle("a", invoice_day=5, days_to_pay=None, outstanding=True),
    ], date(2026, 8, 1))

    assert _stage(built, "ORDER_TO_CASH")["unknown_by_reason"] == {"NO_ORDER": 1}


def test_the_whole_cycle_is_measured_not_two_medians_added():
    """The §1 aggregation rule, in its order-to-cash form.

    These three invoices have order legs 2/4/30 (median 4) and payment legs
    60/10/8 (median 10), so adding the medians gives 14. No invoice took 14
    days: the totals are 62/14/38 and the median of those is 38.
    """
    built = order_to_cash.build([
        _cycle("a", invoice_day=2, order_day=0, days_to_pay=60),
        _cycle("b", invoice_day=4, order_day=0, days_to_pay=10),
        _cycle("c", invoice_day=30, order_day=0, days_to_pay=8),
    ], date(2026, 8, 1))

    assert _stage(built, "ORDER_TO_INVOICE")["median_days"] == 4.0
    assert _stage(built, "INVOICE_TO_CASH")["median_days"] == 10.0
    assert _stage(built, "ORDER_TO_CASH")["median_days"] == 38.0


def test_whose_wait_it_is_totals_the_days_rather_than_averaging_the_shares():
    """Σ ours ÷ Σ total, never the mean of per-invoice shares.

    Ours is 2 + 40 = 42 days against a total of 42 + 8 = 50, so 84% of the wait
    is ours. Averaging the two invoices' own shares — 20% and 100% — would give
    60% and let a two-day invoice weigh as much as a forty-day one.
    """
    built = order_to_cash.build([
        _cycle("a", invoice_day=2, order_day=0, days_to_pay=8),
        _cycle("b", invoice_day=40, order_day=0, days_to_pay=0),
        _cycle("c", invoice_day=0, order_day=0, days_to_pay=0),
    ], date(2026, 8, 1))

    assert built["split"]["ours_share"] == pytest.approx(42 / 50)
    assert built["split"]["invoices"] == 3


def test_the_split_is_measured_only_where_both_legs_are_known():
    """Otherwise the two halves are drawn from different populations and the
    difference between them is presented as a fact about the business."""
    built = order_to_cash.build([
        _cycle("a", invoice_day=5, order_day=0, days_to_pay=10),
        _cycle("b", invoice_day=5, order_day=0, days_to_pay=None,
               outstanding=True),
        _cycle("c", invoice_day=5, days_to_pay=10),
    ], date(2026, 8, 1))

    assert built["split"]["invoices"] == 1
    # One observation is below the floor, so nothing is asserted from it.
    assert built["split"]["estimable"] is False
    assert built["split"]["ours_share"] is None


def test_below_the_evidence_floor_no_typical_cycle_is_asserted():
    """Two invoices are two transactions, not a cycle time. Same floor the
    payment view applies, taken from it rather than restated."""
    built = order_to_cash.build([
        _cycle("a", invoice_day=5, order_day=0, days_to_pay=10),
        _cycle("b", invoice_day=6, order_day=0, days_to_pay=10),
    ], date(2026, 8, 1))

    stage = _stage(built, "ORDER_TO_INVOICE")
    assert stage["estimable"] is False
    assert stage["median_days"] is None
    # The observations are still counted — "we have two" is the useful answer.
    assert stage["measured"] == 2
    assert order_to_cash.MIN_OBSERVATIONS == payments.MIN_SETTLEMENTS


def test_an_invoice_dated_before_its_order_is_surfaced_not_clipped():
    """A back-dated order is a data-entry finding. Clamping it to zero would
    hide the finding and flatter the median at the same time."""
    built = order_to_cash.build([
        _cycle("a", invoice_day=0, order_day=5, days_to_pay=10),
        _cycle("b", invoice_day=10, order_day=0, days_to_pay=10),
        _cycle("c", invoice_day=10, order_day=0, days_to_pay=10),
    ], date(2026, 8, 1))

    assert built["invoices"][0]["order_to_invoice_days"] == -5
    assert _stage(built, "ORDER_TO_INVOICE")["before_start"] == 1


def test_every_stage_says_whose_delay_it_is():
    """The question the screen exists to answer. A stage with no owner on it
    leaves the reader to guess which half they can do something about."""
    built = order_to_cash.build([_cycle("a", invoice_day=1, order_day=0)],
                                date(2026, 8, 1))
    assert [(s["key"], s["owner"]) for s in built["stages"]] == [
        ("ORDER_TO_INVOICE", "US"),
        ("INVOICE_TO_CASH", "CUSTOMER"),
        ("ORDER_TO_CASH", "BOTH"),
    ]
    assert all(s["owner_label"] for s in built["stages"])


# ── the endpoint ────────────────────────────────────────────────────────────
MANAGER = "m.rao@sanketh.in"
SALES = "r.nair@sanketh.in"


def _api(session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import insight as insight_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD, ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight_router.router)
    app.dependency_overrides[get_session] = lambda: session

    client = TestClient(app)

    def token(email: str) -> dict:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": SEED_PASSWORD})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    return client, token


def _book(session):
    """One customer, one order, one invoice against it, settled in two goes.

    Deliberately two payment applications: the invoice is not paid until the
    last one lands, and the router must read that one rather than the first.
    """
    from app.config import settings
    org = settings.DEFAULT_ORG_ID

    customer = models.Customer(organization_id=org, external_id="c1",
                               name="Acme", connector="zoho")
    session.add(customer)
    session.flush()
    session.add_all([
        models.SalesOrderDoc(organization_id=org, external_ref="so1",
                             number="HYD/FY27/SO-399",
                             customer_id=customer.customer_id,
                             date=date(2026, 6, 1), status="closed"),
        models.InvoiceDoc(organization_id=org, external_ref="inv1",
                          number="INV-663", customer_id=customer.customer_id,
                          date=date(2026, 6, 11), due_date=date(2026, 7, 11),
                          status="paid", total=5000, balance=0),
        models.InvoiceSalesOrderLink(organization_id=org,
                                     invoice_external_ref="inv1",
                                     sales_order_external_ref="so1",
                                     sales_order_number="HYD/FY27/SO-399",
                                     is_primary=True),
    ])
    receipt = models.PaymentReceipt(organization_id=org, external_ref="pay1",
                                    customer_id=customer.customer_id,
                                    date=date(2026, 7, 21), amount=5000)
    session.add(receipt)
    session.flush()
    for ref, day, amount in (("app1", date(2026, 6, 21), 2000),
                             ("app2", date(2026, 7, 21), 3000)):
        session.add(models.PaymentApplication(
            organization_id=org, external_ref=ref,
            payment_receipt_id=receipt.payment_receipt_id,
            customer_id=customer.customer_id, invoice_external_ref="inv1",
            invoice_number="INV-663", invoice_date=date(2026, 6, 11),
            invoice_due_date=date(2026, 7, 11), paid_on=day,
            amount_applied=amount))
    session.commit()
    return org


def test_the_cycle_is_reachable_and_joins_the_order_onto_the_invoice(session):
    """The shallowest assertion on the call site, because that is what unit
    tests of ``build`` cannot fail on: the router's own join."""
    _book(session)
    client, token = _api(session)

    r = client.get("/api/v1/insight/order-to-cash", headers=token(MANAGER))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thresholds_version"]
    row = body["invoices"][0]
    assert row["order_date"] == "2026-06-01"
    assert row["order_to_invoice_days"] == 10
    # 21 July minus 11 June: the *last* application, not the first.
    assert row["invoice_to_cash_days"] == 40
    assert row["order_to_cash_days"] == 50
    assert body["coverage"]["with_order"] == 1


def test_the_payment_leg_is_the_figure_the_payments_view_already_reports(session):
    """Reuse, asserted rather than described. If somebody replaces the read of
    ``Settlement.days_to_pay`` with a subtraction here, the two screens can
    disagree about one invoice and nothing else would notice."""
    _book(session)
    client, token = _api(session)
    headers = token(MANAGER)

    cycle = client.get("/api/v1/insight/order-to-cash", headers=headers).json()
    paid = client.get("/api/v1/insight/payments", headers=headers).json()

    assert cycle["invoices"][0]["invoice_to_cash_days"] == (
        paid["customers"][0]["worst_days_to_pay"])


def test_a_salesperson_sees_the_cycle_and_no_money_in_it(session):
    """Dates and day counts carry no commercial position, so this is one of the
    screens every role gets. The assertion is on the *fields*, not on the
    status code: a payload that quietly grew a total would be a leak that a
    403-shaped test could never see."""
    _book(session)
    client, token = _api(session)

    r = client.get("/api/v1/insight/order-to-cash", headers=token(SALES))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invoices"], "a salesperson gets the rows, not an empty screen"
    banned = {"cost", "unit_cost", "margin", "margin_pct", "gross_profit",
              "total", "amount", "balance", "revenue", "purchase_rate"}
    for row in body["invoices"]:
        assert not (banned & set(row)), row


def test_a_book_with_no_order_references_says_so_rather_than_showing_zeroes(session):
    """The empty state names the missing evidence. A screen of zero-day
    conversions would be the same defect the unit tests above refuse, arriving
    through the endpoint instead."""
    from app.config import settings
    org = settings.DEFAULT_ORG_ID
    customer = models.Customer(organization_id=org, external_id="c1",
                               name="Acme", connector="zoho")
    session.add(customer)
    session.flush()
    session.add(models.InvoiceDoc(
        organization_id=org, external_ref="inv1", number="INV-1",
        customer_id=customer.customer_id, date=date(2026, 6, 11),
        status="paid", total=5000, balance=0))
    session.commit()
    client, token = _api(session)

    body = client.get("/api/v1/insight/order-to-cash",
                      headers=token(MANAGER)).json()

    assert "names a sales order" in (body["empty_reason"] or "")
    assert body["stages"][0]["median_days"] is None
