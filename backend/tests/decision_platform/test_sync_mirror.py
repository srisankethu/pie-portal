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

from app.domain import models
from app.ingestion.sync import SyncService
from app.state.events import EventLog

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


def _sync_as(session, source, connection_id: str) -> SyncService:
    """The same pull, on behalf of one connected company."""
    svc = SyncService(session, source, ORG, connector="zoho",
                      connection_id=connection_id)
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


# ── the third guard: only this connection's documents ───────────────────────
# `_mirror`'s docstring has always named three guards and implemented two. The
# organization above has one connection, so every test to this point exercises
# the sweep with nothing to confuse it. These are the two-company cases.
def test_one_connections_sweep_does_not_retire_anothers_documents(session):
    """The guard `_mirror` documents and, until now, did not implement.

    Three Zoho companies were safe only because Zoho issues globally unique
    ids, so no listing ever omitted a document another company held *under a
    ref this one also used*. That is a property of the source, not of this
    code, and the first connector that numbers per company loses it.
    """
    _sync_as(session, _Source([_invoice("A1", 10)]), "conn_a")
    _sync_as(session, _Source([_invoice("B1", 10)]), "conn_b")
    assert _refs(session, models.InvoiceDoc) == {"A1", "B1"}

    # A pulls again. Its listing has never mentioned B1 and never will.
    svc = _sync_as(session, _Source([_invoice("A1", 10)]), "conn_a")

    assert _refs(session, models.InvoiceDoc) == {"A1", "B1"}
    assert svc.report.retired == []


def test_superseding_one_connections_document_leaves_anothers_events_live(session):
    """The log is what every derived state is replayed from, so an over-broad
    supersede does not merely hide a document — it rebuilds the states without
    it."""
    _sync_as(session, _Source([_invoice("A1", 10)]), "conn_a")
    _sync_as(session, _Source([_invoice("B1", 10)]), "conn_b")

    EventLog(session, ORG, connector="zoho",
             connection_id="conn_a").supersede("invoice", "B1")
    session.commit()

    live_b = [e for e in EventLog(session, ORG, connector="zoho",
                                  connection_id="conn_b").live()
              if e.source_doc_id == "B1"]
    assert live_b, "conn_b's events were superseded by conn_a's sweep"


def test_retiring_a_colliding_ref_does_not_delete_the_other_companys_rows(session):
    """The case the connection-scoped sweep alone could not reach.

    Kept in its original form rather than rewritten now that it passes. It was
    written as `xfail(strict=True)` against `retire_document` matching on
    `(organization_id, external_ref)` — scoping the sweep kept another company's
    document out of the *retire list*, but nothing stopped the *delete* from
    reaching a colliding reference, because the fact tables carried no
    connection to filter on. The provenance migration gave them one, the strict
    xfail turned into a failure the moment it started passing, and the marker
    came off in the same change. That is the whole point of an armed tripwire:
    nobody had to remember it existed.
    """
    _sync_as(session, _Source([_invoice("1", 10)]), "conn_a")
    _sync_as(session, _Source([_invoice("1", 10)]), "conn_b")

    _sync_as(session, _Source([]), "conn_a")      # A's "1" is genuinely gone

    assert _refs(session, models.InvoiceDoc) == {"1"}   # B's survives


def test_two_connections_can_hold_the_same_reference_at_all(session):
    """The widened key, from the other side.

    Every test above asks whether one company's sweep spares another's rows.
    This asks the prior question: can the two rows coexist? Under
    `(organization_id, external_ref)` the second write did not raise — it
    upserted onto the first company's row, and one document quietly became the
    other. Both surviving as distinct rows is the thing the migration bought.
    """
    _sync_as(session, _Source([_invoice("1", 10)]), "conn_a")
    _sync_as(session, _Source([_invoice("1", 10)]), "conn_b")

    rows = session.query(models.InvoiceDoc).filter_by(
        organization_id=ORG, external_ref="1").all()
    assert {r.connection_id for r in rows} == {"conn_a", "conn_b"}


def test_a_document_with_no_recorded_connection_is_not_retired(session):
    """Unknown provenance is not a licence to delete.

    A row nobody recorded a connection for might belong to this company or to
    one whose rows predate connections entirely. Sweeping it assumes the
    friendlier answer, which is the benign default §1 forbids — and the two
    mistakes are not symmetric: a stale document is recoverable by a re-sync,
    another company's history is recoverable only from a backup.
    """
    _sync(session, _Source([_invoice("L1", 10)]))        # legacy: no connection

    svc = _sync_as(session, _Source([]), "conn_a")       # a real connection sweeps

    assert _refs(session, models.InvoiceDoc) == {"L1"}
    assert svc.report.unattributable == 1
