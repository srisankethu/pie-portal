"""Rebuild the read model from the event log.

This exists to be *checked*, not to be relied on in the request path. Its job
is to answer one question honestly: **is the event log lossy?** If replaying
every live event into an empty read model does not reproduce the rows the sync
wrote, the events are missing something, and every state derived from them
later would inherit the gap.

So each applier below deliberately calls the *same* repository upsert the sync
calls, with the *same* DTO type. Nothing here re-implements a write. If it did,
"replay reproduces the read model" would only prove two pieces of code agreed
with each other.

Masters are not replayed. A customer or an item is what an event refers to, not
something that happened — replay resolves against whatever masters are present,
exactly as the sync does, and an event whose reference is missing is reported
rather than guessed at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from ..domain import models
from ..domain.schemas import (BillIn, CostRecordIn, InvoiceIn, PaymentReceiptIn,
                              PurchaseOrderIn, SalesOrderIn, SalesTxnIn,
                              StockSnapshotIn, VendorPaymentIn)
from ..repositories import ReadModelRepository
from . import events as ev


@dataclass
class ReplayReport:
    """What a replay did, and what it could not do.

    Unresolved references are counted and sampled rather than swallowed: a
    replay that silently drops a line and reports success is worse than no
    replay, because it makes a lossy log look complete.
    """

    applied: int = 0
    unresolved: list[dict[str, str]] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)

    def _did(self, event_type: str) -> None:
        self.applied += 1
        self.by_type[event_type] = self.by_type.get(event_type, 0) + 1

    def _missing(self, event: models.BusinessEvent, what: str, ref: str) -> None:
        self.unresolved.append({
            "event_type": event.event_type,
            "seq": str(event.seq),
            "document": f"{event.source_doc_type} {event.source_doc_id}",
            "missing": what,
            "reference": ref,
        })


class _Applier:
    """One replay pass, holding the repository the appliers share."""

    def __init__(self, session: Session, org: str, *,
                 connector: Optional[str] = None,
                 connection_id: Optional[str] = None) -> None:
        self.repo = ReadModelRepository(session, org, connector=connector,
                                        connection_id=connection_id)
        self.report = ReplayReport()
        # What to write when an event does not say where it came from — the
        # caller's answer, and NULL unless it gave one.
        self._default_source = (connector, connection_id)

    def _at(self, event: models.BusinessEvent) -> None:
        """Point the shared repository at the book this event was read from.

        Per event and not per replay, because the log spans every connection
        the organization has: one pass over it rebuilds all of their rows, and
        a single provenance for the whole pass would file them all under
        whichever connection the caller happened to name. The event knows —
        ``EventLog.record`` has always written ``connector`` and
        ``connection_id`` onto every row — so replay reads it back rather than
        being told.

        This is what makes "replaying the log reproduces the read model
        exactly" true of the provenance columns as well as the money ones, and
        `test_replaying_the_log_reproduces_the_read_model_exactly` is what
        noticed it was not.
        """
        default_connector, default_connection = self._default_source
        self.repo.connector = event.connector or default_connector
        self.repo.connection_id = event.connection_id or default_connection

    # Each applier takes the event and returns whether it was applied. They are
    # deliberately tiny: the arithmetic and the validation already happened when
    # the payload was normalised, and doing either again here would be a second
    # place for them to be wrong.
    def sale_line(self, e: models.BusinessEvent) -> bool:
        r = SalesTxnIn(**e.payload)
        customer = self.repo.get_customer_by_external(r.customer_external_id)
        if customer is None:
            self.report._missing(e, "customer", r.customer_external_id)
            return False
        product = self.repo.get_product_by_external(r.product_external_id)
        if product is None:
            self.report._missing(e, "product", r.product_external_id)
            return False
        self.repo.upsert_sales_txn(r, customer.customer_id, product.product_id)
        return True

    def cost_line(self, e: models.BusinessEvent) -> bool:
        r = CostRecordIn(**e.payload)
        product = self.repo.get_product_by_external(r.product_external_id)
        if product is None:
            self.report._missing(e, "product", r.product_external_id)
            return False
        self.repo.upsert_cost_record(r, product.product_id,
                                     self._vendor(r.vendor_external_id))
        return True

    def payable(self, e: models.BusinessEvent) -> bool:
        b = BillIn(**e.payload)
        self.repo.upsert_bill(self._vendor(b.vendor_external_id), b)
        return True

    def receivable(self, e: models.BusinessEvent) -> bool:
        inv = InvoiceIn(**e.payload)
        customer_id = None
        if inv.customer_external_id:
            customer = self.repo.get_customer_by_external(inv.customer_external_id)
            # Unlike a sale line, an invoice whose customer this replay has not
            # seen is still money owed and is kept with a null customer — the
            # same choice the sync makes. Not reported as missing, because
            # nothing was lost: the row exists and simply cannot be grouped.
            customer_id = customer.customer_id if customer else None
        self.repo.upsert_invoice(customer_id, inv)
        return True

    def payment_in(self, e: models.BusinessEvent) -> bool:
        p = PaymentReceiptIn(**e.payload)
        customer = self.repo.get_customer_by_external(p.customer_external_id)
        if customer is None:
            self.report._missing(e, "customer", p.customer_external_id)
            return False
        self.repo.upsert_payment(customer.customer_id, p)
        return True

    def payment_out(self, e: models.BusinessEvent) -> bool:
        p = VendorPaymentIn(**e.payload)
        self.repo.upsert_vendor_payment(self._vendor(p.vendor_external_id), p)
        return True

    def sales_order(self, e: models.BusinessEvent) -> bool:
        so = SalesOrderIn(**e.payload)
        customer = (self.repo.get_customer_by_external(so.customer_external_id)
                    if so.customer_external_id else None)
        self.repo.upsert_sales_order(customer.customer_id if customer else None, so)
        return True

    def purchase_order(self, e: models.BusinessEvent) -> bool:
        po = PurchaseOrderIn(**e.payload)
        self.repo.upsert_purchase_order(self._vendor(po.vendor_external_id), po)
        return True

    def stock(self, e: models.BusinessEvent) -> bool:
        snap = StockSnapshotIn(**e.payload)
        product = self.repo.get_product_by_external(snap.product_external_id)
        if product is None:
            self.report._missing(e, "product", snap.product_external_id)
            return False
        self.repo.upsert_stock_snapshot(product.product_id, snap)
        return True

    def _vendor(self, external_id: Optional[str]) -> Optional[str]:
        """A document from a supplier the vendor pull did not return is still
        the document. Null vendor rather than a dropped row — the same choice
        the sync makes."""
        if not external_id:
            return None
        vendor = self.repo.get_vendor_by_external(external_id)
        return vendor.vendor_id if vendor else None


#: Event type → applier method name. A registry rather than an if/elif chain:
#: a new event type is a row here and a method above, and neither touches
#: ``replay``. Every registered type must appear, which the tests assert —
#: an unhandled type would replay as silence.
APPLIERS: dict[str, str] = {
    ev.SALE_LINE_RECORDED: "sale_line",
    ev.COST_LINE_RECORDED: "cost_line",
    ev.PAYABLE_RECORDED: "payable",
    ev.RECEIVABLE_RECORDED: "receivable",
    ev.PAYMENT_RECEIVED: "payment_in",
    ev.PAYMENT_MADE: "payment_out",
    ev.SALES_ORDER_PLACED: "sales_order",
    ev.PURCHASE_ORDER_PLACED: "purchase_order",
    ev.STOCK_OBSERVED: "stock",
}


def replay(session: Session, org: str, *, connector: Optional[str] = None,
           connection_id: Optional[str] = None,
           types: Optional[list[str]] = None) -> ReplayReport:
    """Apply every live event for one organization, in sequence order.

    In ``seq`` order and not by ``occurred_on``, because a document read later
    must win over the one read earlier even when it is dated earlier — that is
    what makes a correction a correction.
    """
    log = ev.EventLog(session, org, connector=connector,
                      connection_id=connection_id)
    applier = _Applier(session, org, connector=connector,
                       connection_id=connection_id)
    for event in log.live(types=types):
        method: Optional[Callable[[models.BusinessEvent], bool]] = getattr(
            applier, APPLIERS.get(event.event_type, ""), None)
        if method is None:
            applier.report._missing(event, "applier", event.event_type)
            continue
        applier._at(event)
        if method(event):
            applier.report._did(event.event_type)
    session.flush()
    return applier.report


def payload_of(event: models.BusinessEvent) -> dict[str, Any]:
    """The event's payload as stored. A named accessor so the JSON column is
    read through one place if it ever needs decoding."""
    return dict(event.payload or {})
