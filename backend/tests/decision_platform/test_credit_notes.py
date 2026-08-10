"""Credit notes: ingested for history, and deliberately kept out of the fold.

The reason this table exists is narrow and worth restating, because the obvious
reading of it is wrong. Today's receivable is *already* correct without credit
notes: Zoho nets applied credit into ``InvoiceDoc.balance``, and
``state/reducers/receivables`` reads that balance rather than deriving one. What
has no source is the *past* — "what did this customer owe on 31 March" is
invoices raised, minus receipts applied, minus credit applied, and the third
term was missing.

So the tests here pull in two directions on purpose. Several assert the rows are
captured faithfully. One asserts they change nothing about the current position,
because the failure this work could most easily introduce is subtracting the
same credit twice.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain import models
from app.ingestion.normalize import normalize_credit_note
from app.ingestion.sync import SyncService

from .test_sync_persistence import _Source


class _WithCreditNotes(_Source):
    """The configurable source, plus the credit-note pull.

    Subclassed rather than adding a sixth constructor argument to ``_Source``:
    the pull is *probed* for with ``hasattr`` in ``run_supply``, so a source
    without this method must stay possible — that is exactly the older
    connection whose OAuth grant never included the credit-note scope.
    """

    def __init__(self, credit_notes=None, **kw):
        super().__init__(**kw)
        self._cn = credit_notes or []

    def list_credit_notes(self, skip=None):
        return list(self._docs(self._cn, "creditnote_id", skip))


def _source(credit_notes) -> _WithCreditNotes:
    return _WithCreditNotes(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
        invoices=[{"invoice_id": "inv1", "customer_id": "c1", "date": "2026-06-01",
                   "status": "sent", "total": 5000, "balance": 3000,
                   "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                   "quantity": 10, "rate": 500, "item_total": 5000}]}],
        credit_notes=credit_notes,
    )


_APPLIED = {
    "creditnote_id": "cn1", "creditnote_number": "CN-1", "customer_id": "c1",
    "date": "2026-06-20", "status": "closed", "total": 2000, "balance": 0,
    "invoices_credited": [{
        "creditnote_invoice_id": "cni1", "invoice_id": "inv1",
        "invoice_number": "INV-1", "invoice_date": "2026-06-01",
        "date": "2026-06-20", "amount_applied": 2000,
    }],
}


def test_an_applied_credit_note_is_persisted_with_its_application(session):
    report = SyncService(session, _source([_APPLIED]), "org_a").run()
    session.commit()

    assert report.credit_notes == 1
    note = session.query(models.CreditNoteDoc).one()
    assert (note.number, note.total, note.balance) == ("CN-1", Decimal("2000"), Decimal("0"))
    assert note.date == date(2026, 6, 20)

    app = session.query(models.CreditNoteApplication).one()
    assert app.invoice_external_ref == "inv1"
    assert app.amount_applied == Decimal("2000")
    assert app.applied_on == date(2026, 6, 20)
    # Carried on the application rather than joined from ``invoices``: a credit
    # note can settle an invoice raised before the sync window opens.
    assert app.invoice_date == date(2026, 6, 1)
    assert app.credit_note_id == note.credit_note_id


def test_credit_notes_do_not_change_what_a_customer_currently_owes(session):
    """The double-count guard, and the whole reason this file is careful.

    ``InvoiceDoc.balance`` is what Zoho says is still owed and it has *already*
    had the applied credit taken out of it. If anything in this change also
    subtracted the credit — a reducer, an event, a fold — the same 2000 would
    come off twice and every customer would look better than they are. The
    balance below must be untouched by the presence of the credit note.
    """
    without = SyncService(session, _source([]), "org_a").run()
    session.commit()
    balance_without = session.query(models.InvoiceDoc).one().balance
    assert without.credit_notes == 0

    with_note = SyncService(session, _source([_APPLIED]), "org_b").run()
    session.commit()
    invoice = session.query(models.InvoiceDoc).filter_by(organization_id="org_b").one()

    assert with_note.credit_notes == 1
    assert invoice.balance == balance_without == Decimal("3000")


def test_an_unapplied_credit_note_is_a_complete_record_not_a_failure(session):
    """Credit raised and not yet set against anything. Valid, and countable."""
    unapplied = {**_APPLIED, "creditnote_id": "cn2", "balance": 2000,
                 "status": "open", "invoices_credited": []}
    report = SyncService(session, _source([unapplied]), "org_a").run()
    session.commit()

    assert report.credit_notes == 1
    assert report.skipped == []
    assert session.query(models.CreditNoteDoc).one().balance == Decimal("2000")
    assert session.query(models.CreditNoteApplication).count() == 0


def test_an_application_with_no_invoice_date_is_kept_not_dropped():
    """Where this deliberately parts company with ``_applications``.

    That helper drops an application whose document has no date, because for
    days-to-pay a missing date means there is no interval to measure. Here the
    same rule would lose *money* rather than an observation, and lose it in the
    direction that overstates what was historically owed — the precise error
    this table exists to correct.
    """
    raw = {**_APPLIED, "invoices_credited": [
        {"creditnote_invoice_id": "cni9", "invoice_id": "inv1",
         "date": "2026-06-20", "amount_applied": 2000},
    ]}
    _header, applications = normalize_credit_note(raw)

    assert len(applications) == 1
    assert applications[0].invoice_date is None
    assert applications[0].amount_applied == Decimal("2000")


def test_an_application_with_no_amount_is_skipped_rather_than_read_as_zero():
    """A blank amount is "the source did not say", not "applied for nothing"."""
    raw = {**_APPLIED, "invoices_credited": [
        {"invoice_id": "inv1", "date": "2026-06-20", "amount_applied": None},
    ]}
    _header, applications = normalize_credit_note(raw)

    assert applications == []


def test_resyncing_the_same_credit_note_does_not_duplicate_it(session):
    org = "org_a"
    SyncService(session, _source([_APPLIED]), org).run()
    session.commit()
    SyncService(session, _source([_APPLIED]), org).run()
    session.commit()

    assert session.query(models.CreditNoteDoc).count() == 1
    assert session.query(models.CreditNoteApplication).count() == 1


def test_a_source_that_cannot_read_credit_notes_still_syncs(session):
    """The older connection, whose OAuth grant predates the credit-note scope.

    ``run_supply`` probes for the method rather than assuming it, so a source
    without it must produce a clean run — losing only the ability to
    reconstruct a past position, never the pull around it.
    """
    report = SyncService(session, _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
    ), "org_a").run()
    session.commit()

    assert report.credit_notes == 0
    assert report.skipped == []
    assert session.query(models.CreditNoteDoc).count() == 0
