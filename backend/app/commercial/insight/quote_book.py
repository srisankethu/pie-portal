"""Every quote the ERP raised, as a list somebody can read.

``unrecorded.py`` next door answers "which unanswered quote is worth an
afternoon" and filters to ``outcome == UNRECORDED``. That was the **only**
listing ``erp_quotes`` had, which meant a book whose quotes Zoho had mostly
marked accepted or invoiced synced correctly, stored correctly, and appeared
nowhere: the sync counted them, the erasure export carried them, the row-level
policy protected them, and no screen in the platform would show them. Somebody
who had just connected their books saw two platform drafts and concluded the
sync was broken.

So this is the plain read, and the split between the two modules is the split
between two questions rather than two filters:

``unrecorded``   which of the unanswered ones to chase, ranked by how long it
                 has been lapsed and what is on it. A worklist that shrinks.
``quote_book``   what is in the book. Every row, newest first, with the ERP's
                 own word for how each ended — and, per quote on request,
                 what was on it.

They share the table and nothing else, which is why the ordering here is a flat
``date DESC`` and not that module's lexicographic sort inside named groups: a
list somebody scrolls to find a quote wants the newest first, and a worklist
wants the most overdue first. Folding them into one parameterised builder would
mean one function with two orderings and two meanings of "top".

**Nothing here is cost or margin.** ``value`` is the quote's own selling total —
the figure that went to the customer — and everything else is dates, the ERP's
own status word, and the outcome ``ingestion.classify_outcome`` read off it.
There is no cost column on ``erp_quotes`` to leak, which is why this list is
shown to every role, narrowed only by which accounts the reader may see.

**The ERP's word is reported, never improved on.** ``source_status`` travels
beside ``outcome`` rather than being replaced by it: the classification collapses
Zoho's vocabulary into three values on purpose, and a reader who wants to know
why a quote reads UNRECORDED needs to see that it was ``sent`` rather than
``expired``. Reporting only the verdict would make the two indistinguishable on
screen, and ``classify_outcome``'s refusal to turn silence into a loss is
exactly the kind of decision a reader should be able to check.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models
from ...domain.origin import Companies
from ...ingestion.normalize import ZOHO
from .. import quote_service

_ZERO = Decimal("0")


def customer_label(customer_id: Optional[str], customer_ref: str,
                   names: dict[str, str]) -> str:
    """What to call the counterparty on a quote the ERP raised.

    The ERP's own typed name beats a generated placeholder: on this table an
    unresolved ``customer_id`` usually means a walk-in or a spelling the contact
    pull did not match, not a missing master row, so ``customer_ref`` is a real
    name and ``"Unnamed customer (id …)"`` is not.

    Shared with ``unrecorded`` rather than written twice. Two screens listing the
    same table under two spellings of the same customer is the kind of difference
    nobody reports as a bug and everybody distrusts.
    """
    return names.get(customer_id or "") or customer_ref or "Unattributed"


@dataclass(frozen=True)
class BookQuote:
    """One row, carrying every value the list sorted or filtered on."""

    quote_document_ref: str
    number: Optional[str]
    customer_id: Optional[str]
    customer_label: str
    raised_on: date
    expires_on: Optional[date]
    #: The ERP's own status word, verbatim.
    source_status: str
    #: WON / LOST / UNRECORDED, as ``ingestion.classify_outcome`` read it.
    outcome: str
    #: Non-null whenever ``outcome`` is not UNRECORDED — a decided quote with no
    #: date is not usable as evidence, and the sync counts how often that
    #: happens rather than inventing one.
    decided_on: Optional[date]
    #: The quote's own selling total. ``None`` where the ERP gave none, which is
    #: not zero and must not be summed as zero.
    value: Optional[Decimal]
    opened_at: Optional[datetime]
    #: Which connected company's books this was raised in. Resolved through
    #: ``origin.Companies``, the one dictionary — ``label_for`` and ``of``
    #: answer the same question for a grouped query and for a record, so two
    #: screens cannot name the same company differently.
    company: str
    #: The same fact in the shape every other list stamps on its rows
    #: (``Companies.of``), so the workspace can filter this tab the way it
    #: filters the directory. ``None`` where the caller passed no companies.
    origin: Optional[dict[str, Any]]
    #: The PIE quote this document was written from, where the platform wrote
    #: it: ``{"quote_id", "number"}`` — joined read-side on the qualified
    #: document identity (``quote_service.erp_documents_for`` in the other
    #: direction), never stored on this table, which the sync rewrites whole.
    #: ``None`` for a quote raised in the ERP by hand, which is most of them.
    platform_quote: Optional[dict[str, str]]
    #: What a person here recorded about this document, if anything — the
    #: ``quote_outcomes`` row naming it by the ERP's own id: its status, the
    #: reason and the winner where it was a loss, and when. ``None`` where
    #: nobody has said. Read-side, like ``platform_quote``: the sync rewrites
    #: this table whole and never opens the human one.
    recorded: Optional[dict[str, Any]]
    #: The outcome of record — ``quote_service.decide`` over the person's row
    #: and the ERP's word: WON / LOST / UNRECORDED. The person wins; the ERP
    #: fills silence. ``outcome`` above stays the ERP's own reading, so a
    #: screen can show both and say which is which.
    outcome_of_record: str
    #: ``QuoteOutcomeSource`` for a decided quote, ``None`` while open.
    outcome_source: Optional[str]
    #: The source's own fields on the quote, as the ERP holds them — quote
    #: type, pricing type, procurement type, branch, and whatever else this
    #: business configured. Only the keys the source actually set: an absent
    #: custom field is not a category, and a quote nobody classified is a
    #: different fact from every unclassified quote sharing a bucket called
    #: "other". ``{}`` here rather than ``None``: a reader wants a mapping to
    #: iterate, and the column's NULL/empty distinction is not one a screen
    #: can act on.
    source_attributes: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Snake_case keys and a ``float`` value, both matching ``unrecorded``.

        Deliberately not this repository's camelCase screen convention: these
        two endpoints list *the same table* and sit on the same router, and one
        table serialised two ways is the difference nobody reports as a bug.
        ``float`` on a ``Numeric(18, 4)`` is the sibling's existing choice for
        this exact column — money stays ``Decimal`` everywhere it is computed,
        which is what CLAUDE.md §1 constrains, and this is the wire.
        """
        return {
            "quote_document_ref": self.quote_document_ref,
            "number": self.number,
            "customer_id": self.customer_id,
            "customer_label": self.customer_label,
            "source_status": self.source_status,
            "outcome": self.outcome,
            "raised_on": self.raised_on.isoformat(),
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "decided_on": self.decided_on.isoformat() if self.decided_on else None,
            "value": float(self.value) if self.value is not None else None,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "company": self.company,
            "origin": self.origin,
            "platform_quote": self.platform_quote,
            "recorded": self.recorded,
            "outcome_of_record": self.outcome_of_record,
            "outcome_source": self.outcome_source,
            # The wire key, not the field name, and deliberately unchanged by
            # the rename behind it: this is a published response field that
            # ``ErpQuoteScreen`` reads, and nothing in the gate binds the two,
            # so renaming it here would be a silent break of a working screen.
            # It moves with its consumer or not at all — the same reasoning
            # this method's docstring already applies to its key convention.
            "attributes": dict(self.source_attributes),
        }


def build(session: Session, org: str, *, customer_names: dict[str, str],
          customer_ids: Optional[frozenset[str]] = None,
          companies: Optional["Companies"] = None) -> list[BookQuote]:
    """The whole book, newest quote first.

    ``customer_ids`` is the caller's role scope and follows the rule
    ``unrecorded._load`` states: ``None`` means the whole book, a set means those
    accounts and nothing else, and the two are different answers — collapsing an
    empty set into ``None`` would hand a salesperson who holds no accounts
    everything.

    A quote whose ``customer_id`` never resolved is dropped under a narrowed
    scope and kept unscoped, for that module's reason: nobody recorded it
    against an account, so there is no account to say it belongs to, and showing
    it to everybody would put a stranger's quote on a salesperson's list.

    ``companies`` attributes each row to the book it was raised in. Optional
    because it costs a query the caller may already have made, and a caller
    without one gets ``"Source not recorded"`` — the same words ``label_for``
    uses for a row whose connection is unknown, rather than a blank that reads
    as "no company" or a guess that names the wrong one.

    Returns the whole list rather than a page. The caller slices it, so a screen
    can say how many it is not showing — a builder that truncated would make the
    headline agree with the visible rows and the book would look the size of the
    page. Same arrangement as ``unrecorded.build``, and for the same reason.
    """
    stmt = select(models.QuoteDoc).where(models.QuoteDoc.organization_id == org)
    if customer_ids is not None:
        stmt = stmt.where(models.QuoteDoc.customer_id.in_(customer_ids))
    # Ordered in SQL, then tie-broken in Python on a stable key. Two quotes
    # raised the same day are ordinary, and leaving their order to the database
    # would make the same book paginate differently on two engines.
    stmt = stmt.order_by(models.QuoteDoc.date.desc())

    docs = session.scalars(stmt).all()
    written = _platform_quotes(session, org, docs)
    records = quote_service.erp_outcomes_of_record(session, org, docs)
    rows = [
        BookQuote(
            quote_document_ref=row.external_ref,
            number=row.number,
            customer_id=row.customer_id,
            customer_label=customer_label(row.customer_id, row.customer_ref,
                                          customer_names),
            raised_on=row.date,
            expires_on=row.expires_on,
            source_status=row.source_status or "",
            outcome=row.outcome,
            decided_on=row.decided_on,
            value=Decimal(row.total) if row.total is not None else None,
            company=(companies.label_for(row.connection_id) if companies
                     else "Source not recorded"),
            origin=companies.of(row).to_dict() if companies else None,
            platform_quote=written.get(row.quote_document_id),
            recorded=_recorded(records[row.quote_document_id].human),
            outcome_of_record=_of_record(records[row.quote_document_id]),
            outcome_source=(records[row.quote_document_id].source.value
                            if records[row.quote_document_id].source else None),
            source_attributes=dict(row.source_attributes or {}),
            opened_at=row.client_viewed_at,
        )
        for row in docs
    ]
    rows.sort(key=lambda q: (q.raised_on, q.number or "",
                             q.quote_document_ref), reverse=True)
    return rows


def _recorded(row: Optional[models.QuoteOutcome]) -> Optional[dict[str, Any]]:
    """A person's row about an ERP document, as the screen reads it. Only a
    decision is worth showing beside the ERP's word — a SENT a person recorded
    says nothing the ERP's own status does not."""
    if row is None or row.status not in ("WON", "LOST"):
        return None
    return {
        "status": row.status,
        "loss_reason": row.loss_reason,
        "lost_to": row.lost_to,
        "note": row.note,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


def _of_record(rec: quote_service.OutcomeOfRecord) -> str:
    """WON / LOST / UNRECORDED — the ERP tab's vocabulary for the outcome of
    record. An open quote is UNRECORDED here whatever its lifecycle state,
    which is what this tab has always meant by the word."""
    return rec.status.value if rec.decided else "UNRECORDED"


def _platform_quotes(session: Session, org: str,
                     docs: list[models.QuoteDoc]) -> dict[str, dict[str, str]]:
    """Which of these ERP quotes this platform wrote, keyed by the ERP row's id.

    The reverse of ``quote_service.erp_documents_for`` and the same join: a
    ``quote_documents`` row matches an ERP quote on (system, company, the id
    the system gave it). A document written before the company was recorded
    matches on (system, id) alone while that names one ERP row; two companies
    answering to one id match nothing, rather than either. Newest document per
    quote wins, so a quote re-sent as a new document names the new one.

    Two queries for the whole book, whatever its size: the documents whose ids
    appear in it, then the numbers of the drafts they came from.
    """
    if not docs:
        return {}
    by_id = {d.external_ref for d in docs}
    written = session.scalars(
        select(models.QuoteDocument)
        .where(models.QuoteDocument.organization_id == org,
               models.QuoteDocument.external_document_id.in_(by_id))
        .order_by(models.QuoteDocument.written_at.desc(),
                  models.QuoteDocument.quote_document_id.desc())).all()
    if not written:
        return {}
    numbers = {d.quote_id: d.number for d in session.scalars(
        select(models.QuoteDraft).where(
            models.QuoteDraft.organization_id == org,
            models.QuoteDraft.quote_id.in_({w.quote_id for w in written})))}
    # How many ERP rows answer to each (system, id): a company-less document
    # may only join where that is exactly one.
    per_system_id: dict[tuple[str, str], list[models.QuoteDoc]] = {}
    for d in docs:
        per_system_id.setdefault((d.connector or ZOHO, d.external_ref), []).append(d)
    out: dict[str, dict[str, str]] = {}
    for w in written:                       # newest first; first claim wins
        candidates = per_system_id.get((w.external_system or ZOHO,
                                        w.external_document_id or ""), [])
        if w.connection_id:
            candidates = [d for d in candidates if d.connection_id == w.connection_id]
        if len(candidates) != 1 or candidates[0].quote_document_id in out:
            continue
        out[candidates[0].quote_document_id] = {
            "quote_id": w.quote_id, "number": numbers.get(w.quote_id, "")}
    return out


@dataclass(frozen=True)
class BookQuoteLine:
    """One line of a quote, as the ERP wrote it."""

    line_number: int
    item_code: str
    #: The item master's name for this line, where the code resolved to one.
    #: Read from ``Product`` rather than stored on the line: the ERP's own
    #: ``name`` is on the payload, but a copy taken at quote time is a name that
    #: drifts from the master the next time somebody renames the item, and this
    #: screen is read against the catalogue rather than against the document.
    #: Empty where the line resolved to no product, which is a real state — a
    #: quote naming something that never became a catalogue item is still real
    #: quoting activity, and the code is shown on its own rather than blanked.
    item_name: str
    description: str
    product_id: Optional[str]
    qty: Optional[Decimal]
    unit: str
    rate: Optional[Decimal]
    amount: Optional[Decimal]

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_number": self.line_number,
            "item_code": self.item_code,
            "item_name": self.item_name,
            "description": self.description,
            "product_id": self.product_id,
            "qty": float(self.qty) if self.qty is not None else None,
            "unit": self.unit,
            "rate": float(self.rate) if self.rate is not None else None,
            "amount": float(self.amount) if self.amount is not None else None,
        }


def lines_for(session: Session, org: str, *, quote_ref: str,
              connection_id: Optional[str] = None) -> list[BookQuoteLine]:
    """What was on one quote, in the order the ERP wrote it.

    ``connection_id`` is the company the reference is unique inside. Given, it
    narrows to that book's lines; absent, the bare reference is read exactly as
    before — which is correct while it names one quote and is why the caller
    resolves the document first. Two books holding one reference would
    otherwise return both quotes' lines interleaved under one document.

    Fetched per quote rather than carried on every row of the book: 114 quotes
    with their lines is a payload nobody reads most of, and the lines are wanted
    only when somebody opens one.

    An empty list is two different facts — a quote genuinely without lines, and
    a quote whose breakdown this platform has not pulled — and this function
    cannot tell them apart. The caller can: a resumed sync leaves the header
    updated and the lines untouched, so the screen says "not held" rather than
    "none". Do not let an empty list here render as an empty quote.
    """
    # Outer join, not inner: a line whose code resolved to no product still
    # belongs to the quote, and an inner join would silently drop it from a
    # document the reader is holding in their other hand.
    rows = session.execute(
        select(models.ErpQuoteLine, models.Product.name)
        .outerjoin(models.Product,
                   models.ErpQuoteLine.product_id == models.Product.product_id)
        .where(models.ErpQuoteLine.organization_id == org,
               models.ErpQuoteLine.quote_ref == quote_ref,
               *([models.ErpQuoteLine.connection_id == connection_id]
                 if connection_id else []))
        .order_by(models.ErpQuoteLine.line_number,
                  models.ErpQuoteLine.external_ref)).all()
    return [
        BookQuoteLine(
            line_number=row.line_number,
            item_code=row.item_code or "",
            item_name=name or "",
            description=row.description or "",
            product_id=row.product_id,
            qty=Decimal(row.qty) if row.qty is not None else None,
            unit=row.unit or "",
            rate=Decimal(row.rate) if row.rate is not None else None,
            amount=Decimal(row.amount) if row.amount is not None else None,
        )
        for row, name in rows
    ]


def totals(quotes: list[BookQuote]) -> dict[str, Any]:
    """Headline counts, each a count of something stated exactly.

    ``value_total`` sums only the rows that carry a total and
    ``quotes_without_a_value`` says how many it left out. Reading a missing
    total as zero would produce a figure that looks complete and is not, with
    nothing on the response to say so — the ``sum(... or 0)`` tell CLAUDE.md §1
    names by sight.
    """
    valued = [q.value for q in quotes if q.value is not None]
    # Counted on the outcome of record, not on the ERP's word alone: a loss
    # a person recorded here against an ERP quote is a loss on this headline
    # too, which is the difference between one win rate and three.
    by_outcome: dict[str, int] = {}
    for q in quotes:
        by_outcome[q.outcome_of_record] = by_outcome.get(q.outcome_of_record, 0) + 1
    return {
        "count": len(quotes),
        "by_outcome": {
            "WON": by_outcome.get("WON", 0),
            "LOST": by_outcome.get("LOST", 0),
            "UNRECORDED": by_outcome.get("UNRECORDED", 0),
        },
        # ``None`` rather than 0.0 when nothing carries a total: a zero here
        # would read as "this book quoted nothing" instead of "the ERP gave no
        # totals", and nothing on the response would say which.
        "value_total": float(sum(valued, _ZERO)) if valued else None,
        "quotes_without_a_value": len(quotes) - len(valued),
    }
