"""The quotes nobody wrote an outcome on, in the order worth asking about.

Roughly three quarters of the quotes this book raises end in no recorded way at
all. ``ingestion`` reads Zoho's own word for each one and classifies it, and it
refuses to turn silence into a loss: ``expired``, ``sent`` and ``viewed`` all
land on ``UNRECORDED`` because silence spans "nobody worked it", "the customer
never answered" and "we lost it to a competitor", and only the last is a loss.
That refusal is correct and it leaves a pile — ~215 on the live SLS book — that
nobody can be asked about all at once.

This module is the ordering. It is the read half of the read-then-suggest shape
in ``docs/concepts/14-machine-learning.md`` §2.1: **the ERP owns the setting,
the platform reads it and says which ones are worth a person's afternoon.** The
one question Zoho has no field for is *why*, and asking it 215 times is how a
capture screen gets ignored; asking it about the eight that lapsed months ago
with the most money on them is a morning's work.

**Nothing here is a probability, and nothing here is a score.** ``bonds.py``
argues the case: a single number with hidden weights is exactly what §1 exists
to prevent, because nobody downstream can say what moved it. ``radar.py`` makes
the narrower point that confidence is *evidence* rather than forecast. So the
order below is a **lexicographic sort over two published quantities** — how long
the offer has been lapsed, then how much was on it — inside three named groups,
and every quantity the sort used is on the row it sorted. A reader can recompute
the list by hand from the response; that is the test this ordering had to pass.

**An absent expiry date is unanswerable, not zero.** A quote with no
``expires_on`` has no age past expiry — not an age of nothing. Treating those as
"0 days past expiry" would file them with the freshest quotes in the book and
they would never be seen again, which is the ``sum(... or 0)`` failure wearing a
sort key. They get their own group, ``EXPIRY_NOT_RECORDED``, ranked between the
lapsed and the live: they are not known to be lapsed, and they are not known to
be live either, and either claim would be one the row cannot support. The count
is a headline for the same reason.

**A quote with no total is kept and counted, never valued at zero.** It is real
quoting activity with a real customer on the other end, and the answer to "why
did this one go quiet" is worth exactly as much as on any other row; what is
missing is only the ranking key. Those rows sort last inside their group, oldest
first, on ``date``, which the ERP always supplies.

**"The customer never opened it" is a claim this module refuses to make.**
``client_viewed_at`` records an *open* the ERP saw. Its absence is not evidence
of no open — the quote may never have been sent, the tracking may be off, or the
customer may have read a forwarded PDF. So the row carries ``opened_at``
verbatim and the headline counts ``opened`` (a positive fact) against
``opening_not_recorded`` (the absence, named as an absence). ``source_status``
travels with every row unchanged, so a reader who wants the sent-versus-draft
split has the ERP's own word for it rather than this module's guess at a
vocabulary that belongs to ``ingestion``.

No cost, no margin, no ratio and no count that answers a margin question.
``value`` is the quote's own selling total — the number that was put in front of
the customer — which is why this list is readable by the salesperson whose
accounts it covers, unlike ``radar`` and ``weather`` beside it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models
from ...domain.enums import QuoteDocOutcome, QuoteOutcomeStatus

#: A quote is off this list once *somebody* has said how it ended — either
#: source. These are the human table's endings; ``QuoteDocOutcome`` covers the
#: ERP's.
_RECORDED = (QuoteOutcomeStatus.WON.value, QuoteOutcomeStatus.LOST.value)

#: The offer has lapsed: an expiry date is on record and the day has passed.
#: The one group where "how long has this been sitting" is answerable.
PAST_EXPIRY = "PAST_EXPIRY"
#: No expiry date on record, so the age past expiry cannot be computed at all.
#: Not lapsed, not known to be live — see the module docstring.
EXPIRY_NOT_RECORDED = "EXPIRY_NOT_RECORDED"
#: An expiry date on record, still in the future. The offer is live and the
#: customer has not run out of time to answer it.
STILL_OPEN = "STILL_OPEN"

#: Group order, and the whole of the "which is worth asking about first"
#: judgement — stated once, here, rather than implied by a comparator. Lapsed
#: first because the answer exists and only a person has it; live last because
#: chasing a quote the customer still has time to answer is a different
#: conversation and usually a premature one.
GROUP_ORDER = (PAST_EXPIRY, EXPIRY_NOT_RECORDED, STILL_OPEN)

_ZERO = Decimal("0")


@dataclass(frozen=True)
class PendingQuote:
    """One quote the ERP holds no outcome for, with the facts a person needs.

    ``quote_document_ref`` is the source system's own id — the value
    ``quote_outcomes.quote_document_ref`` points at — never
    ``quote_documents.quote_document_id``, which a full re-sync re-mints. A
    capture screen that recorded an outcome against the surrogate would lose the
    link on the next rebuild; the whole table is written so that cannot happen,
    and this list is the one place the id crosses to a caller.
    """

    quote_document_ref: str
    number: Optional[str]
    customer_id: Optional[str]
    customer_label: str
    #: The ERP's own word for this quote's state, verbatim. Carried so a reader
    #: can tell a draft from a sent quote without this module owning a
    #: vocabulary that belongs to ``ingestion.normalize``.
    source_status: str
    raised_on: date
    expires_on: Optional[date]
    #: One of ``GROUP_ORDER``.
    group: str
    #: Days since the offer lapsed. ``None`` wherever ``expires_on`` is absent
    #: or still ahead — an unanswerable age and a future one are both refused
    #: rather than folded to zero.
    days_past_expiry: Optional[int]
    #: The quote's own selling total. ``None`` where the ERP gave none.
    value: Optional[Decimal]
    #: When the ERP saw the customer open it. ``None`` means no open was
    #: recorded, which is not the same as no open having happened.
    opened_at: Optional[datetime]

    def to_dict(self) -> dict:
        return {
            "quote_document_ref": self.quote_document_ref,
            "number": self.number,
            "customer_id": self.customer_id,
            "customer_label": self.customer_label,
            "source_status": self.source_status,
            "raised_on": self.raised_on.isoformat(),
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "group": self.group,
            "days_past_expiry": self.days_past_expiry,
            "value": float(self.value) if self.value is not None else None,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
        }


def _group_and_age(expires_on: Optional[date],
                   as_of: date) -> tuple[str, Optional[int]]:
    """Which group a quote falls in, and its age past expiry where there is one.

    The signature is the guard rather than the comment: an age is returned only
    on the branch that has a date to subtract from, so there is no expression in
    this module where a missing expiry could be read as a zero-day-old lapse.
    ``classify_outcome`` is written the same way one layer up, and for the
    harder version of the same reason.
    """
    if expires_on is None:
        return EXPIRY_NOT_RECORDED, None
    if expires_on < as_of:
        return PAST_EXPIRY, (as_of - expires_on).days
    return STILL_OPEN, None


def _ordered(rows: list[PendingQuote], *, group: str) -> list[PendingQuote]:
    """One group's rows, in the order a person should work them.

    Two sorts rather than one comparator, because the two halves are ranked on
    different evidence and a single key would have had to invent a value for the
    half that has none. Rows carrying a total rank by lapsed age then by money,
    both descending. Rows carrying no total rank by the day they were raised,
    oldest first — ``date`` is the one column the ERP always supplies, so the
    fallback is a real ordering rather than the arbitrary one a stable sort
    would leave behind.

    The lapsed age enters the key only on the ``PAST_EXPIRY`` branch, and
    ``_group_and_age`` is the only thing that assigns a group — so on that
    branch every row has an age and on the others no row's age is read. The
    group is taken as an argument rather than sniffed off the first row so that
    guarantee is a condition of calling this, not a property a caller could
    quietly break by grouping differently.
    """
    valued = [r for r in rows if r.value is not None]
    unvalued = [r for r in rows if r.value is None]
    if group == PAST_EXPIRY:
        valued.sort(key=lambda r: (r.days_past_expiry, r.value), reverse=True)
    else:
        valued.sort(key=lambda r: r.value, reverse=True)
    unvalued.sort(key=lambda r: r.raised_on)
    return valued + unvalued


def _load(session: Session, org: str,
          customer_ids: Optional[frozenset[str]]) -> list[models.QuoteDoc]:
    """Every quote with no recorded outcome this reader may see.

    **Two sources of "recorded", and the first version of this read only knew
    one of them.** ``QuoteDoc.outcome`` is the ERP's word, written by the sync
    and by nothing else; ``QuoteOutcome`` is the row a person writes, and
    ``quote_service`` deliberately never touches ``quote_documents`` — the two
    write sets are disjoint *tables* rather than disjoint columns, which is the
    right design and was the whole argument for the split.

    The consequence, missed until a review caught it: filtering on
    ``QuoteDoc.outcome`` alone meant recording a loss through the capture dialog
    changed nothing this query could see. The quote came back on the next reload,
    the headline count never moved, and re-recording it as WON answered 409. A
    worklist whose stated purpose is to shrink could not be worked down at all —
    and the screen exists to grow the six-loss sample §5.1 needs.

    So the ERP's silence and the desk's silence are both required. A ``DRAFT`` or
    ``SENT`` outcome row is *not* an ending and does not clear the quote: the
    platform priced it and nobody has said how it went, which is precisely this
    list's population.

    ``customer_ids`` is the caller's role scope: ``None`` means the whole book,
    a set means those accounts and nothing else. Scoping is decided in the
    router — that is where role lives — and applied here so a count taken
    outside it cannot exist, which is the rule ``_scoped_outcomes`` states for
    the human table.

    A quote whose ``customer_id`` never resolved is dropped under a narrowed
    scope rather than shown to everybody: nobody recorded it against an account,
    so there is no account to say it belongs to, and including it would put a
    stranger's quote on a salesperson's list. It stays visible unscoped, where
    somebody can attribute it.
    """
    answered = (
        select(models.QuoteOutcome.quote_outcome_id)
        .where(models.QuoteOutcome.organization_id == org,
               models.QuoteOutcome.quote_document_ref
               == models.QuoteDoc.external_ref,
               models.QuoteOutcome.status.in_(_RECORDED))
        .exists())
    stmt = select(models.QuoteDoc).where(
        models.QuoteDoc.organization_id == org,
        models.QuoteDoc.outcome == QuoteDocOutcome.UNRECORDED.value,
        # Correlated NOT EXISTS rather than an outer join: the join would
        # duplicate a quote if the human table ever held two rows for one
        # reference, and this list's headline is a count.
        ~answered)
    if customer_ids is not None:
        stmt = stmt.where(models.QuoteDoc.customer_id.in_(customer_ids))
    return list(session.scalars(stmt).all())


def build(session: Session, org: str, *, as_of: date,
          customer_names: dict[str, str],
          customer_ids: Optional[frozenset[str]] = None) -> list[PendingQuote]:
    """The worklist: unanswered quotes, grouped and ranked.

    Returns the whole pile, deliberately. A caller that shows the top fifty
    slices this list itself and takes ``totals`` over all of it, so the screen
    can say how many it is not showing — a build that truncated would make the
    headline count agree with the visible rows and the pile would look the size
    of the page.
    """
    rows = []
    for row in _load(session, org, customer_ids):
        group, days = _group_and_age(row.expires_on, as_of)
        rows.append(PendingQuote(
            quote_document_ref=row.external_ref,
            number=row.number,
            customer_id=row.customer_id,
            # The ERP's own typed customer name is a real name and a better
            # answer than ``label_for``'s "Unnamed customer (id …)": on this
            # table an unresolved ``customer_id`` usually means a walk-in or a
            # spelling the contact pull did not match, not a missing master row.
            customer_label=(customer_names.get(row.customer_id or "")
                            or row.customer_ref or "Unattributed"),
            source_status=row.source_status,
            raised_on=row.date,
            expires_on=row.expires_on,
            group=group,
            days_past_expiry=days,
            value=Decimal(row.total) if row.total is not None else None,
            opened_at=row.client_viewed_at,
        ))

    by_group: dict[str, list[PendingQuote]] = {g: [] for g in GROUP_ORDER}
    for row in rows:
        by_group[row.group].append(row)
    out: list[PendingQuote] = []
    for group in GROUP_ORDER:
        out.extend(_ordered(by_group[group], group=group))
    return out


def totals(quotes: Iterable[PendingQuote]) -> dict:
    """Headline counts, each of them a count of something stated exactly.

    ``value_at_stake`` sums only the rows that carry a total, and
    ``quotes_without_a_value`` says how many it left out. The alternative —
    reading a missing total as zero — would produce a figure that looks complete
    and is not, and nothing on the response would say so.

    ``opened`` counts a fact the ERP recorded. ``opening_not_recorded`` counts
    its absence and is named as an absence: it is *not* "never opened", and the
    difference matters because a screen that says "the customer never looked at
    it" about a quote that was never sent is wrong in a way the reader cannot
    check.
    """
    rows = list(quotes)
    valued = [q.value for q in rows if q.value is not None]
    lapsed = [q.days_past_expiry for q in rows
              if q.days_past_expiry is not None]
    return {
        "count": len(rows),
        "by_group": {g: sum(1 for q in rows if q.group == g)
                     for g in GROUP_ORDER},
        "value_at_stake": float(sum(valued, _ZERO)),
        "quotes_without_a_value": len(rows) - len(valued),
        # The oldest lapse on the list, so an empty-looking screen can still
        # say how long the pile has been growing. ``None`` when nothing on it
        # has an expiry date to have passed.
        "longest_lapse_days": max(lapsed) if lapsed else None,
        "opened": sum(1 for q in rows if q.opened_at is not None),
        "opening_not_recorded": sum(1 for q in rows if q.opened_at is None),
    }
