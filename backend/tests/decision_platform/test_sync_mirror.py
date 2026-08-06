"""A sync leaves PIE a mirror of the source.

Until this existed a sync only ever added. An invoice deleted in Zoho, or
voided there, stayed in PIE forever — still counting as revenue, still counting
as a receivable, still feeding the decisions built on both.

Retiring on absence is the one thing a sync does that can destroy real history,
so most of this file is about the cases where it must **not** fire. The guards
matter more than the feature.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain import models
from app.ingestion.sync import SyncService

ORG = "org_mirror"
TODAY = date.today()


def _day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


class _Source:
    """A source that reports whichever invoices it is told to, and tracks what
    its listing saw — the same contract `ZohoApiSource` fulfils."""

    def __init__(self, invoices, *, complete=True, since=None):
        self._invoices = invoices
        self._complete = complete
        self.listed: dict[str, set[str]] = {}
        self.listing_complete: set[str] = set()
        self._since = since or (TODAY - timedelta(days=365))
        self._until = None

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"}]

    def list_items(self):
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active", "purchase_rate": "100"}]

    def list_vendors(self):
        return []

    def list_users(self):
        return []

    def list_bills(self, skip=None):
        return []

    def list_sales_orders(self):
        return []

    def list_purchase_orders(self):
        return []

    def list_invoices(self, skip=None):
        seen = self.listed.setdefault("invoice", set())
        for inv in self._invoices:
            seen.add(inv["invoice_id"])
            yield inv
        if self._complete:
            self.listing_complete.add("invoice")


def _invoice(ref: str, days_ago: int, total="5000"):
    return {"invoice_id": ref, "invoice_number": f"INV-{ref}", "customer_id": "c1",
            "date": _day(days_ago), "status": "sent", "total": total,
            "balance": total, "due_date": _day(days_ago - 30),
            "last_modified_time": f"stamp-{ref}",
            "line_items": [{"line_item_id": "l1", "item_id": "i1",
                            "quantity": 10, "rate": "500", "item_total": total}]}


def _sync(session, source) -> SyncService:
    svc = SyncService(session, source, ORG)
    svc.run()
    session.commit()
    return svc


def _refs(session, model) -> set[str]:
    return {r.external_ref for r in session.query(model).filter_by(
        organization_id=ORG).all()}


# ── the feature ─────────────────────────────────────────────────────────────
def test_an_invoice_deleted_in_zoho_is_removed_from_pie(session):
    _sync(session, _Source([_invoice("A", 10), _invoice("B", 20)]))
    assert _refs(session, models.InvoiceDoc) == {"A", "B"}

    # B is gone from Zoho on the next pull.
    svc = _sync(session, _Source([_invoice("A", 10)]))

    assert _refs(session, models.InvoiceDoc) == {"A"}
    assert {r["ref"] for r in svc.report.retired} == {"B"}
    # Its lines go with it — a header removed alone would leave revenue with
    # nothing to attribute it to.
    assert not [t for t in _refs(session, models.SalesTxn) if t.startswith("B:")]


def test_a_retired_invoice_stops_counting_everywhere_derived(session):
    """Retirement supersedes the document's events, which is what makes every
    derived thing forget it — without a cascade of deletes to keep in step."""
    from app.state.engine import build, load
    from app.state.reducers.trade import CUSTOMER_MONTH

    _sync(session, _Source([_invoice("A", 10), _invoice("B", 20)]))
    build(session, ORG, as_of=TODAY)
    session.commit()
    before = sum(float(v["revenue"]) for v in
                 load(session, ORG, CUSTOMER_MONTH, TODAY).values())

    _sync(session, _Source([_invoice("A", 10)]))
    build(session, ORG, as_of=TODAY)
    session.commit()
    after = sum(float(v["revenue"]) for v in
                load(session, ORG, CUSTOMER_MONTH, TODAY).values())

    assert after == before - 5000, "the fold must forget a retired invoice"


def test_an_invoice_voided_in_zoho_is_treated_as_absent(session):
    """`ZohoApiSource` drops void and draft documents before recording them as
    seen, so voiding in Zoho removes it here. Asserted at the seam this test can
    reach: a source that stops listing it produces the same outcome."""
    _sync(session, _Source([_invoice("A", 10), _invoice("V", 15)]))
    assert "V" in _refs(session, models.InvoiceDoc)

    _sync(session, _Source([_invoice("A", 10)]))
    assert "V" not in _refs(session, models.InvoiceDoc)


# ── the guards, which matter more ───────────────────────────────────────────
def test_a_pull_cut_short_retires_nothing(session):
    """The dangerous case. A pull thrown out by the rate limiter has seen part
    of the book, and treating the part it never reached as deleted would
    destroy real history on a bad network afternoon."""
    _sync(session, _Source([_invoice("A", 10), _invoice("B", 20)]))

    svc = _sync(session, _Source([_invoice("A", 10)], complete=False))

    assert _refs(session, models.InvoiceDoc) == {"A", "B"}
    assert svc.report.retired == []


def test_a_document_older_than_the_window_is_never_retired(session):
    """Absence from a search that did not include it says nothing at all.

    The first pull reaches back a year; the second only thirty days. The old
    invoice is not in the second listing because nobody looked for it.
    """
    _sync(session, _Source([_invoice("OLD", 200), _invoice("NEW", 5)]))
    assert _refs(session, models.InvoiceDoc) == {"OLD", "NEW"}

    narrow = _Source([_invoice("NEW", 5)], since=TODAY - timedelta(days=30))
    svc = _sync(session, narrow)

    assert "OLD" in _refs(session, models.InvoiceDoc), (
        "a document the pull never looked for is not a document Zoho deleted")
    assert svc.report.retired == []


def test_a_source_that_reports_no_listing_retires_nothing(session):
    """The mock source and every test double predate this contract. A source
    that does not say what it saw must produce no retirement at all, rather
    than an empty set that reads as "Zoho has nothing"."""
    class Silent(_Source):
        def list_invoices(self, skip=None):
            return list(self._invoices)     # never touches `listed`

    _sync(session, _Source([_invoice("A", 10), _invoice("B", 20)]))
    svc = _sync(session, Silent([_invoice("A", 10)]))

    assert _refs(session, models.InvoiceDoc) == {"A", "B"}
    assert svc.report.retired == []


def test_retirement_clears_the_resume_cursor_too(session):
    """Otherwise the next pull believes it still holds the document, skips it,
    and never notices it has come back."""
    _sync(session, _Source([_invoice("A", 10), _invoice("B", 20)]))
    _sync(session, _Source([_invoice("A", 10)]))

    held = {r.doc_id for r in session.query(models.IngestedDocument).filter_by(
        organization_id=ORG, doc_type="invoice").all()}
    assert held == {"A"}


def test_a_document_that_comes_back_is_read_again(session):
    """The round trip. Retiring must not be sticky — an invoice restored in
    Zoho has to reappear here."""
    _sync(session, _Source([_invoice("A", 10), _invoice("B", 20)]))
    _sync(session, _Source([_invoice("A", 10)]))
    assert _refs(session, models.InvoiceDoc) == {"A"}

    _sync(session, _Source([_invoice("A", 10), _invoice("B", 20)]))
    assert _refs(session, models.InvoiceDoc) == {"A", "B"}


def test_an_unchanged_book_retires_nothing(session):
    """The everyday case, and the one a bug here would be loudest in."""
    invoices = [_invoice("A", 10), _invoice("B", 20), _invoice("C", 30)]
    _sync(session, _Source(invoices))
    svc = _sync(session, _Source(invoices))

    assert svc.report.retired == []
    assert _refs(session, models.InvoiceDoc) == {"A", "B", "C"}
