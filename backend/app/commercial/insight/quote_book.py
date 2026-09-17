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
                 own word for how each ended.

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
        }


def build(session: Session, org: str, *, customer_names: dict[str, str],
          customer_ids: Optional[frozenset[str]] = None) -> list[BookQuote]:
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
            opened_at=row.client_viewed_at,
        )
        for row in session.scalars(stmt).all()
    ]
    rows.sort(key=lambda q: (q.raised_on, q.number or "",
                             q.quote_document_ref), reverse=True)
    return rows


def totals(quotes: list[BookQuote]) -> dict[str, Any]:
    """Headline counts, each a count of something stated exactly.

    ``value_total`` sums only the rows that carry a total and
    ``quotes_without_a_value`` says how many it left out. Reading a missing
    total as zero would produce a figure that looks complete and is not, with
    nothing on the response to say so — the ``sum(... or 0)`` tell CLAUDE.md §1
    names by sight.
    """
    valued = [q.value for q in quotes if q.value is not None]
    by_outcome: dict[str, int] = {}
    for q in quotes:
        by_outcome[q.outcome] = by_outcome.get(q.outcome, 0) + 1
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
