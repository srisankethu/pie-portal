"""The single writer of ``ValueEvent``, and the shape of a writable event.

Nothing else in this codebase may insert a row into ``value_events``. That is a
stronger rule than it looks: this table is what a renewal conversation is argued
from, so a second writer is not a duplicated code path, it is a second set of
claims about what the platform is worth, arrived at by rules nobody compared.

Three guarantees live here, and each is structural rather than a convention a
future caller is asked to remember.

**Double counting is impossible, not merely discouraged.** ``event_key`` is
unique per organization (``uq_value_event_key``), and ``record`` upserts on it:
recording the same fact twice returns the existing row with ``created=False``.
Detectors are therefore free to re-run after every sync — which they must be,
because a quote won today changes the classification of a line flagged last week,
and a detector that could only run once would have to be scheduled perfectly.

**An event that cannot name its evidence is refused, not accepted quietly.**
``record`` raises on empty ``evidence_refs`` or a missing ``thresholds_version``.
Both are the same objection: a rupee figure with nothing behind it is an
assertion, and this ledger's entire value is that it holds none.

**The key carries the class.** ``(event_type, evidence identity, value_class)``,
so a line flagged as POTENTIAL during a live quote and re-detected as ATTRIBUTED
after the quote is won writes a *second* row rather than being rejected as a
duplicate — the table is append-only and a reclassification is a new fact, not an
edit to an old one. The consequence is deliberate and must be understood by
every reader: the same underlying line can appear once under POTENTIAL and once
under ATTRIBUTED. **Those two totals are never added together** (§1 of the
design, and ``ValueClass``'s own docstring), so the overlap is two statements
about one line rather than a double count. Adding them would be the defect;
suppressing one of them would lose the record that the intervention happened
before the outcome, which is the only thing that makes ATTRIBUTED mean anything.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain import models
from ..domain.enums import ValueClass, ValueEventType

#: The books are INR. Stored per row rather than assumed, so a total that would
#: mix currencies can be made to refuse instead of silently adding them.
DEFAULT_CURRENCY = "INR"


class LedgerRefusal(ValueError):
    """An event the ledger will not write, and why.

    Its own type rather than a bare ``ValueError`` so a router can map it to a
    500 rather than a 400: every field it checks is supplied by this codebase,
    never by a request body, so reaching it means a detector is wrong — not that
    a caller sent something bad.
    """


@dataclass(frozen=True)
class ValueEventDraft:
    """One event a detector wants recorded, before it is a row.

    Frozen, and separate from the model, for the reason ``QuoteLineIntelligence``
    is separate from ``QuoteDecision``: a detector is a pure function of evidence
    and must be testable without a session. The draft is also what the detectors'
    unit tests assert against, so a change to the arithmetic shows up as a
    changed draft rather than as a changed database row.
    """

    event_type: ValueEventType
    value_class: ValueClass
    event_key: str
    #: ``None`` when the class carries no defensible money. Never ``0`` as a
    #: stand-in for "we could not work it out" — see ``calculator``.
    amount: Optional[Decimal]
    #: The operands, by name, so the drill-down re-derives the amount instead of
    #: restating it. A reader who cannot reproduce the number from this dict
    #: should treat the event as unsupported.
    basis: dict[str, Any]
    #: Never empty. ``record`` refuses an event without one.
    evidence_refs: list[dict[str, Any]]
    #: When the *business fact* happened, not when this was detected.
    occurred_at: datetime
    thresholds_version: str
    currency: str = DEFAULT_CURRENCY
    #: Free-form detail the detector wants preserved but that is not an operand.
    notes: dict[str, Any] = field(default_factory=dict)


def event_key(org: str, event_type: ValueEventType, value_class: ValueClass,
              identity: Sequence[str]) -> str:
    """A stable key for one fact, in the ``Decision.decision_key`` idiom.

    ``identity`` is the evidence the event is about — a quote decision id, a
    quote id and line id — and its *order* is part of the key, so a detector
    must pass it consistently. Hashed rather than concatenated because the
    column is 128 characters and a quote line id is not bounded by anything this
    module controls; a key that silently truncates would collide two facts into
    one and lose the second amount.
    """
    parts = "|".join(str(p) for p in identity)
    blob = f"{org}|{event_type.value}|{value_class.value}|{parts}".encode()
    return "ve_" + hashlib.sha256(blob).hexdigest()[:24]


def _validate(draft: ValueEventDraft) -> None:
    if not isinstance(draft.event_type, ValueEventType):
        raise LedgerRefusal(f"event_type is not a ValueEventType: {draft.event_type!r}")
    if not isinstance(draft.value_class, ValueClass):
        raise LedgerRefusal(f"value_class is not a ValueClass: {draft.value_class!r}")
    if not (draft.event_key or "").strip():
        raise LedgerRefusal("event_key is empty — the double-count guard needs one")
    if not draft.evidence_refs:
        raise LedgerRefusal(
            f"{draft.event_type.value} carries no evidence_refs; an event that "
            "cannot name the rows it was computed from is an assertion")
    if not (draft.thresholds_version or "").strip():
        raise LedgerRefusal(
            f"{draft.event_type.value} carries no thresholds_version; a stored "
            "amount that cannot say which policy judged it is unexplainable later")
    if not (draft.currency or "").strip():
        raise LedgerRefusal("currency is empty")
    # A float here would defeat every Decimal in the calculator at the last
    # step, and SQLite would store the binary artefact without complaint.
    if draft.amount is not None and not isinstance(draft.amount, Decimal):
        raise LedgerRefusal(
            f"amount must be Decimal, got {type(draft.amount).__name__}")


def _existing(session: Session, org: str, key: str) -> Optional[models.ValueEvent]:
    return session.scalars(
        select(models.ValueEvent).where(
            models.ValueEvent.organization_id == org,
            models.ValueEvent.event_key == key)).first()


def record(session: Session, org: str,
           draft: ValueEventDraft) -> tuple[models.ValueEvent, bool]:
    """Write one event, or return the one already recording this fact.

    Returns ``(row, created)``. ``created=False`` is the ordinary case on a
    re-run and is not an error — it is the guard working.
    """
    _validate(draft)

    row = _existing(session, org, draft.event_key)
    if row is not None:
        return row, False

    fresh = models.ValueEvent(
        organization_id=org,
        event_type=draft.event_type.value,
        value_class=draft.value_class.value,
        event_key=draft.event_key,
        amount=draft.amount,
        currency=draft.currency,
        basis=dict(draft.basis),
        evidence_refs=[dict(ref) for ref in draft.evidence_refs],
        occurred_at=draft.occurred_at,
        thresholds_version=draft.thresholds_version,
    )
    try:
        # A savepoint, so that losing a race against a concurrent detector run
        # costs this insert rather than the caller's whole transaction — the
        # same shape ``trust/vault`` uses, and for the same reason.
        with session.begin_nested():
            session.add(fresh)
            session.flush()
    except IntegrityError:
        if fresh in session:
            session.expunge(fresh)
        winner = _existing(session, org, draft.event_key)
        if winner is None:
            raise
        return winner, False
    return fresh, True


def record_all(session: Session, org: str,
               drafts: Iterable[ValueEventDraft]) -> tuple[list[models.ValueEvent], int]:
    """Record a detector's whole output. Returns ``(rows, created_count)``.

    Deliberately not transactional beyond the caller's own transaction: a run
    that records nine events and refuses the tenth has found nine real facts,
    and discarding them to punish the tenth would lose evidence. The refusal
    still raises — it just raises after the good rows are in the session.
    """
    rows: list[models.ValueEvent] = []
    created = 0
    for draft in drafts:
        row, was_created = record(session, org, draft)
        rows.append(row)
        created += int(was_created)
    return rows, created
