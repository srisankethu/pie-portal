"""Vendor credits: read at last, stored only, and touching no cost.

``11-procurement.md`` measured the gap this closes. ``rg -ic "vendor.?credit"``
across ``backend/`` returned nothing while the SLS book held thirteen of them —
one of them ₹4.83 lakh of stock returned to Kennametal across eight bills — and
none of it reduced a line's cost, a principal's slab base or a supplier's
credit-note rate, because none of it was read.

The tests pull in two directions on purpose, the same way ``test_credit_notes``
does. Several assert the rows are captured faithfully. Three assert the pull
changes *nothing*: not a bill's balance, not a line's cost, not a margin. That
second group is the important half. Adjusting cost by the credit that corrected
it is the obvious next step and it is deliberately not taken here — it moves
every per-line margin in the platform and it turns on an accounting question
``11-procurement.md`` §1 puts to an accountant rather than to an engineer.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain import models
from app.ingestion.normalize import normalize_vendor_credit
from app.ingestion.sync import SyncService

from .test_sync_persistence import _Source


class _WithVendorCredits(_Source):
    """The configurable source, plus the vendor pull and the vendor-credit pull.

    Subclassed rather than widened, for the reason ``_WithCreditNotes`` gives:
    ``run_supply`` *probes* for the method with ``hasattr``, so a source without
    it has to stay constructible — that is exactly the older connection whose
    OAuth grant predates ``ZohoBooks.vendorcredits.READ``.
    """

    def __init__(self, vendors=None, vendor_credits=None, **kw):
        super().__init__(**kw)
        self._v = vendors or []
        self._vc = vendor_credits or []

    def list_vendors(self):
        return list(self._v)

    def list_vendor_credits(self, skip=None):
        return list(self._docs(self._vc, "vendor_credit_id", skip))


#: A bill this book actually owes on, so the balance guard has something to
#: watch. 100 pieces at ₹400 is the cost record every margin downstream reads.
_BILL = {"bill_id": "b1", "date": "2026-05-01", "vendor_id": "v1",
         "status": "open", "total": 40000, "balance": 40000,
         "line_items": [{"line_item_id": "bl1", "item_id": "i1",
                         "quantity": 100, "rate": 400, "item_total": 40000}]}

#: The shape of the Kennametal return, shrunk: one credit spread over two bills.
_APPLIED = {
    "vendor_credit_id": "vc1", "vendor_credit_number": "01/FY25",
    "vendor_id": "v1", "date": "2026-06-20", "status": "closed",
    "total": 5000, "balance": 0,
    "bills_credited": [
        {"vendor_credit_bill_id": "vcb1", "bill_id": "b1",
         "bill_number": "BN-1", "amount": 3000, "date": "2026-05-01"},
        {"vendor_credit_bill_id": "vcb2", "bill_id": "b2",
         "bill_number": "BN-2", "amount": 2000, "date": "2026-05-09"},
    ],
}


def _source(vendor_credits) -> _WithVendorCredits:
    return _WithVendorCredits(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
        vendors=[{"contact_id": "v1", "contact_name": "Kennametal", "status": "active"}],
        invoices=[{"invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-01",
                   "status": "sent", "total": 5000, "balance": 5000,
                   "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                   "quantity": 10, "rate": 500, "item_total": 5000}]}],
        bills=[_BILL],
        vendor_credits=vendor_credits,
    )


def test_a_vendor_credit_is_persisted_with_every_bill_it_was_set_against(session):
    report = SyncService(session, _source([_APPLIED]), "org_a").run()
    session.commit()

    assert report.vendor_credits == 1
    vc = session.query(models.VendorCreditDoc).one()
    assert (vc.number, vc.total, vc.balance) == ("01/FY25", Decimal("5000"),
                                                 Decimal("0"))
    assert vc.date == date(2026, 6, 20)
    # The supplier resolved, which is what lets a return be read against the
    # principal whose purchases it reverses.
    assert vc.vendor_id == session.query(models.Vendor).one().vendor_id

    apps = {a.bill_external_ref: a for a in
            session.query(models.VendorCreditApplication).all()}
    assert set(apps) == {"b1", "b2"}
    assert apps["b1"].amount_applied == Decimal("3000")
    assert apps["b2"].amount_applied == Decimal("2000")
    assert all(a.vendor_credit_id == vc.vendor_credit_id for a in apps.values())
    # One credit over several bills is the ordinary case, not the exception —
    # the Kennametal document this is modelled on covers eight.
    assert sum(a.amount_applied for a in apps.values()) == vc.total


def test_a_vendor_credit_does_not_change_what_this_book_owes(session):
    """The double-count guard, mirrored from the sell side.

    ``BillDoc.balance`` is what Zoho says is still payable and it has *already*
    had the applied credit taken out of it. Anything here that subtracted the
    credit again — a reducer, an event, a fold — would take the same ₹3,000 off
    twice and make the payable look smaller than it is.
    """
    without = SyncService(session, _source([]), "org_a").run()
    session.commit()
    balance_without = session.query(models.BillDoc).one().balance
    assert without.vendor_credits == 0

    with_credit = SyncService(session, _source([_APPLIED]), "org_b").run()
    session.commit()
    bill = session.query(models.BillDoc).filter_by(organization_id="org_b").one()

    assert with_credit.vendor_credits == 1
    assert bill.balance == balance_without == Decimal("40000")


def test_a_vendor_credit_does_not_change_what_a_line_cost(session):
    """The invariant this whole change is scoped around.

    Cost has exactly one source — the bill line — and this pull does not become
    a second one. Reducing ``unit_cost`` by the credit that corrected the bill
    is the obvious next step, it is defensible, and it is *not taken here*: it
    moves every per-line margin in the platform, and whether a credit reduces
    purchase cost at all is the accrual-treatment question ``11-procurement.md``
    §1 refers to an accountant. If this assertion ever fails, the change that
    broke it owes the platform that conversation first.
    """
    SyncService(session, _source([]), "org_a").run()
    session.commit()
    cost_without = session.query(models.CostRecord).filter_by(
        organization_id="org_a").one().unit_cost

    SyncService(session, _source([_APPLIED]), "org_b").run()
    session.commit()
    cost_with = session.query(models.CostRecord).filter_by(
        organization_id="org_b").one().unit_cost

    assert cost_with == cost_without == Decimal("400")


def test_an_unapplied_vendor_credit_is_a_complete_record_not_a_failure(session):
    """₹3.87 lakh open against Renishaw with no bill named is the live case."""
    unapplied = {**_APPLIED, "vendor_credit_id": "vc2", "balance": 5000,
                 "status": "open", "bills_credited": []}
    report = SyncService(session, _source([unapplied]), "org_a").run()
    session.commit()

    assert report.vendor_credits == 1
    assert report.skipped == []
    assert session.query(models.VendorCreditDoc).one().balance == Decimal("5000")
    assert session.query(models.VendorCreditApplication).count() == 0


def test_credit_from_a_supplier_the_vendor_pull_did_not_return_is_still_kept(session):
    """Dropping it would overstate what this book owes that supplier."""
    src = _source([_APPLIED])
    src._v = []                     # the vendor pull returned nothing
    report = SyncService(session, src, "org_a").run()
    session.commit()

    assert report.vendor_credits == 1
    vc = session.query(models.VendorCreditDoc).one()
    assert vc.vendor_id is None
    assert vc.total == Decimal("5000")


def test_no_application_date_is_invented_from_the_row_zoho_supplies():
    """The refusal, pinned so a later reader does not quietly reverse it.

    ``bills_credited`` carries one unlabelled ``date`` per row. On the live
    Kennametal document its eight values are spread over five months while the
    document's own system comments record every application made on two days in
    May 2026 — so it is the bill's date, not the application's. A column named
    ``applied_on`` holding a bill's date would put money on a timeline it never
    sat on, so no such column exists and nothing here reads that field.
    """
    _header, applications = normalize_vendor_credit(_APPLIED)

    assert len(applications) == 2
    assert not any(hasattr(a, "applied_on") for a in applications)
    assert not any(hasattr(a, "bill_date") for a in applications)


def test_an_application_with_no_amount_is_skipped_rather_than_read_as_zero():
    """A blank amount is "the source did not say", not "applied for nothing"."""
    raw = {**_APPLIED, "bills_credited": [
        {"vendor_credit_bill_id": "vcb9", "bill_id": "b1", "amount": None},
    ]}
    _header, applications = normalize_vendor_credit(raw)

    assert applications == []


def test_an_application_naming_no_bill_is_unapplied_credit_not_an_error():
    """The header's own ``balance`` already reports it."""
    raw = {**_APPLIED, "bills_credited": [
        {"vendor_credit_bill_id": "vcb9", "bill_id": "", "amount": 3000},
    ]}
    header, applications = normalize_vendor_credit(raw)

    assert applications == []
    assert header.total == Decimal("5000")


def test_resyncing_the_same_vendor_credit_does_not_duplicate_it(session):
    org = "org_a"
    SyncService(session, _source([_APPLIED]), org).run()
    session.commit()
    SyncService(session, _source([_APPLIED]), org).run()
    session.commit()

    assert session.query(models.VendorCreditDoc).count() == 1
    assert session.query(models.VendorCreditApplication).count() == 2


def test_a_source_that_cannot_read_vendor_credits_still_syncs(session):
    """The older connection, whose grant predates the vendor-credit scope.

    ``run_supply`` probes for the method rather than assuming it, so a source
    without it must produce a clean run — losing only the returns and price
    corrections, never the pull around them.
    """
    report = SyncService(session, _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
    ), "org_a").run()
    session.commit()

    assert report.vendor_credits == 0
    assert report.skipped == []
    assert session.query(models.VendorCreditDoc).count() == 0
