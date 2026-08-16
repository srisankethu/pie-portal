"""The event log: what happened, in the order the platform read it.

One writer (the sync), one shape, and no mutation. Every event is a normalised
DTO — the same ``…In`` model the read model is written from — plus where it was
read and when. That equality is the point: if the payload were a bespoke
summary, "replay rebuilds the read model" would only prove the summary was
self-consistent.

Idempotency comes from the document, not from the event. A re-read of an edited
invoice supersedes *all* of that invoice's live events and appends the new
reading, so a document that lost a line loses its event too — which appending
alone could never express.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Iterator, Optional

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..clock import now as utc_now
from ..domain import models

# ── the type registry ────────────────────────────────────────────────────────
#
# A new event type is a row here and nothing else: the writer, the ordering and
# the supersede rule are the same for every type. What each type *means* to a
# state belongs in that state's reducer, not in this module — which is why
# nothing below branches on a type name.
#
# Masters are deliberately absent. A customer or an item is not something that
# happened; it is what an event refers to. Replaying a log against an empty
# database is expected to need the masters already there, and pretending
# otherwise would turn every re-import into a rename history nobody asked for.

SALE_LINE_RECORDED = "SALE_LINE_RECORDED"
COST_LINE_RECORDED = "COST_LINE_RECORDED"
PAYMENT_RECEIVED = "PAYMENT_RECEIVED"
PAYMENT_MADE = "PAYMENT_MADE"
PAYABLE_RECORDED = "PAYABLE_RECORDED"
RECEIVABLE_RECORDED = "RECEIVABLE_RECORDED"
SALES_ORDER_PLACED = "SALES_ORDER_PLACED"
PURCHASE_ORDER_PLACED = "PURCHASE_ORDER_PLACED"
STOCK_OBSERVED = "STOCK_OBSERVED"

#: Every type, and the document kind it is read from. The document kind is what
#: the supersede rule groups on, so a type with the wrong one would quietly
#: fail to be replaced on re-read.
EVENT_TYPES: dict[str, str] = {
    SALE_LINE_RECORDED: "invoice",
    COST_LINE_RECORDED: "bill",
    PAYABLE_RECORDED: "bill",
    # Same document as the sale lines, deliberately: superseding groups on
    # (doc_type, doc_id), so re-reading an invoice retires its header event
    # and its line events together and re-records both. A different doc kind
    # here would leave a stale balance behind every edit.
    RECEIVABLE_RECORDED: "invoice",
    PAYMENT_RECEIVED: "customer_payment",
    PAYMENT_MADE: "vendor_payment",
    SALES_ORDER_PLACED: "sales_order",
    PURCHASE_ORDER_PLACED: "purchase_order",
    STOCK_OBSERVED: "stock",
}


class UnknownEventType(ValueError):
    """Emitted for a type not in the registry.

    Loud rather than permissive: an unregistered type would be written, ordered
    and replayed correctly and simply never reach a reducer, which is the kind
    of silence that takes a quarter to notice.
    """


@dataclass(frozen=True)
class Source:
    """Where an event was read from.

    ``modified_at`` is Zoho's stamp, verbatim — the value the resume cursor
    compared before ``IngestedDocument`` stored its UTC rewrite of it
    (``clock.utc_stamp``). Verbatim *here* on purpose: the log is the audit
    record of what the source actually said. It is recorded rather than
    compared: the decision about whether a document changed is the sync's, made
    once per document, and duplicating it per event would give two answers to
    one question.
    """

    doc_type: str
    doc_id: str
    line_id: Optional[str] = None
    modified_at: str = ""


class EventLog:
    """Append-only writer, scoped to one organization and one connection.

    Not a repository method, because the log is not part of the read model: the
    read model is a projection that can be rebuilt, and this is the thing it is
    rebuilt *from*. Keeping them apart is what stops an accidental
    ``upsert_event`` ever being written.
    """

    def __init__(self, session: Session, org: str, *,
                 connector: Optional[str] = None,
                 connection_id: Optional[str] = None) -> None:
        self.s = session
        self.org = org
        self.connector = connector
        self.connection_id = connection_id

    # ── writing ─────────────────────────────────────────────────────────────
    def record(self, event_type: str, occurred_on: date, source: Source,
               payload: BaseModel | dict[str, Any]) -> models.BusinessEvent:
        """Append one event. Never updates, never de-duplicates.

        De-duplication is ``supersede``'s job and happens per document. Doing it
        here would mean comparing payloads, and two readings of a line that
        genuinely did not change are still two readings — the log's value is
        that it says when we looked.
        """
        if event_type not in EVENT_TYPES:
            raise UnknownEventType(
                f"{event_type!r} is not in EVENT_TYPES. Add it there — an "
                "unregistered type is written and ordered correctly and then "
                "silently never reaches a reducer.")
        row = models.BusinessEvent(
            organization_id=self.org,
            connector=self.connector,
            connection_id=self.connection_id,
            event_type=event_type,
            occurred_on=occurred_on,
            recorded_at=utc_now(),
            source_doc_type=source.doc_type,
            source_doc_id=source.doc_id,
            source_line_id=source.line_id,
            source_modified_at=source.modified_at or "",
            # mode="json" so Decimals and dates become strings. The DTOs parse
            # Decimals via str, so the round-trip is exact; a float would not be.
            payload=(payload.model_dump(mode="json")
                     if isinstance(payload, BaseModel) else dict(payload)),
        )
        self.s.add(row)
        return row

    def supersede(self, doc_type: str, doc_id: str) -> int:
        """Retire every live event read from one document, **as this connection
        read it**. Returns how many.

        Called before re-reading a document, not after: the new events must
        land *after* the retirement in sequence order, or a replay reading in
        seq order would apply the old reading last.

        Scoped to the connection because a document id is unique only inside
        the system that issued it. Without that clause one company's sweep
        retires another's events for the same id — and since every folded state
        is replayed from this log, that does not merely hide the document, it
        rebuilds the states without it. `record` has always written both
        columns; only this predicate ignored them.
        """
        result = self.s.execute(
            update(models.BusinessEvent)
            .where(models.BusinessEvent.organization_id == self.org,
                   models.BusinessEvent.connector == self.connector,
                   models.BusinessEvent.connection_id == self.connection_id,
                   models.BusinessEvent.source_doc_type == doc_type,
                   models.BusinessEvent.source_doc_id == doc_id,
                   models.BusinessEvent.superseded_at.is_(None))
            .values(superseded_at=utc_now())
        )
        return int(result.rowcount or 0)

    # ── reading ─────────────────────────────────────────────────────────────
    def live(self, *, types: Optional[Iterable[str]] = None,
             ) -> Iterator[models.BusinessEvent]:
        """Every event still believed, oldest first.

        Ordered by ``seq``, never by ``occurred_on``: two documents can share a
        date, and the order they were read in is the only total order there is.
        """
        stmt = (select(models.BusinessEvent)
                .where(models.BusinessEvent.organization_id == self.org,
                       models.BusinessEvent.superseded_at.is_(None))
                .order_by(models.BusinessEvent.seq))
        if types is not None:
            wanted = list(types)
            stmt = stmt.where(models.BusinessEvent.event_type.in_(wanted))
        yield from self.s.scalars(stmt)

    def count(self, *, event_type: Optional[str] = None) -> int:
        stmt = select(func.count()).select_from(models.BusinessEvent).where(
            models.BusinessEvent.organization_id == self.org,
            models.BusinessEvent.superseded_at.is_(None))
        if event_type is not None:
            stmt = stmt.where(models.BusinessEvent.event_type == event_type)
        return int(self.s.scalar(stmt) or 0)
