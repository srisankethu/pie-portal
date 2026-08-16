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
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
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


class ValueFact(str, Enum):
    """What underlying business fact an event is a measurement *of*.

    The unit the double-count guard operates on. Several event types can measure
    one fact from different angles — ``MARGIN_PROTECTED`` and
    ``DISCOUNT_LEAKAGE_PREVENTED`` are both ways of valuing one line's price
    movement — and those must share an identity or the rollup banks the same
    rupees twice. Types that measure genuinely *different* facts must not share
    one, or the guard silently discards a real second amount.

    That second failure is the one worth stating, because collapsing too much is
    as wrong as collapsing too little and looks tidier: a line whose price moved
    *and* which was sourced through a cheaper equivalent produced two real,
    non-overlapping amounts. They are two facts about one line.
    """

    PRICE_MOVEMENT = "PRICE_MOVEMENT"
    SUBSTITUTION = "SUBSTITUTION"


#: Which fact each event type measures. Every ``ValueEventType`` that a detector
#: can emit must appear here — ``_validate`` refuses a draft whose type is
#: missing, rather than inventing a fact for it.
FACT_OF: dict[ValueEventType, ValueFact] = {
    ValueEventType.MARGIN_PROTECTED: ValueFact.PRICE_MOVEMENT,
    ValueEventType.DISCOUNT_LEAKAGE_PREVENTED: ValueFact.PRICE_MOVEMENT,
    ValueEventType.EQUIVALENT_SAVING: ValueFact.SUBSTITUTION,
}


def event_key(org: str, fact: ValueFact, value_class: ValueClass,
              identity: Sequence[str]) -> str:
    """A stable key for one *fact*, in the ``Decision.decision_key`` idiom.

    ``identity`` is the evidence the event is about — a quote id and line id —
    and its *order* is part of the key, so a detector must pass it
    consistently. Hashed rather than concatenated because the column is 128
    characters and a quote line id is not bounded by anything this module
    controls; a key that silently truncates would collide two facts into one and
    lose the second amount.

    **The event type is deliberately not part of the key**, and that is a
    correction rather than an oversight. It used to be, and the consequence was
    that ``MARGIN_PROTECTED`` and ``DISCOUNT_LEAKAGE_PREVENTED`` — which measure
    overlapping halves of one line's price movement — each wrote a row and the
    rollup summed both. One line flagged at ₹800, repriced to ₹1,100 across 10
    units booked ₹6,000 against ₹3,000 of actual movement, on the flagship
    success path. The underlying fact is "this line's price moved after PIE
    flagged it"; it is one fact, so it gets one row, and ``event_type`` records
    which flag drove it rather than minting a second identity.

    Nor are snapshot ids part of it. Including the final snapshot's id meant a
    further reprice minted a fresh key and left the stale partial claim standing
    — three detection runs over one line booked ₹8,000 for ₹3,000 of movement.
    A re-run now lands on the same key and supersedes rather than accumulates
    (see ``record``), which is what makes this module's promise that detectors
    are "free to re-run after every sync" actually true.
    """
    parts = "|".join(str(p) for p in identity)
    blob = f"{org}|{fact.value}|{value_class.value}|{parts}".encode()
    return "ve_" + hashlib.sha256(blob).hexdigest()[:24]


def _validate(draft: ValueEventDraft) -> None:
    if not isinstance(draft.event_type, ValueEventType):
        raise LedgerRefusal(f"event_type is not a ValueEventType: {draft.event_type!r}")
    if not isinstance(draft.value_class, ValueClass):
        raise LedgerRefusal(f"value_class is not a ValueClass: {draft.value_class!r}")
    if draft.event_type not in FACT_OF:
        # A type with no registered fact cannot be de-duplicated against
        # anything, so a new detector added without a FACT_OF entry would
        # silently reintroduce the double count this registry exists to stop.
        # Refuse loudly at the seam instead of guessing a fact for it.
        raise LedgerRefusal(
            f"{draft.event_type.value} has no entry in FACT_OF; every event type "
            "must declare which underlying fact it measures")
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
    """The *live* row for this fact. Superseded rows are history, not answers."""
    return session.scalars(
        select(models.ValueEvent).where(
            models.ValueEvent.organization_id == org,
            models.ValueEvent.event_key == key,
            models.ValueEvent.superseded_at.is_(None))).first()


def record(session: Session, org: str,
           draft: ValueEventDraft) -> tuple[models.ValueEvent, bool]:
    """Write one event, supersede a stale measurement, or leave things alone.

    Returns ``(row, created)``. ``created=False`` is the ordinary case on a
    re-run and is not an error — it is the guard working.

    Three outcomes, because a stable key needs all three:

    * No row for this fact — insert. ``created=True``.
    * A live row holding the **same** amount — the fact has not changed, so
      nothing is written. ``created=False``.
    * A live row holding a **different** amount — a later snapshot measured this
      same fact differently (the line was repriced again, or a quote's outcome
      landed). Stamp ``superseded_at`` on the old row and insert the new one.
      ``created=True``.

    That third case is the one that makes "detectors are free to re-run after
    every sync" true rather than aspirational. Without it a stable key freezes
    the first measurement forever; with a key that varied by snapshot — which is
    what shipped first — each run banked another overlapping amount for the same
    price movement.
    """
    _validate(draft)

    row = _existing(session, org, draft.event_key)
    if row is not None:
        if row.amount == draft.amount:
            return row, False
        # Superseded, not mutated: the old claim stays readable and every rollup
        # filters it out. `state/` supersedes rather than mutates for the same
        # reason — a corrected number whose predecessor is gone cannot be audited.
        row.superseded_at = clock.now()
        session.flush()

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


def supersede_closed_opportunities(session: Session, org: str,
                                   live_keys: Iterable[str]) -> int:
    """Retire POTENTIAL rows whose opportunity is no longer open.

    A POTENTIAL event says "this is still available". The detectors refuse to
    *create* one for a lost quote — but that only governs new rows, and a row
    written while the quote was still open is never revisited. So a quote that
    was flagged, priced, and then declined kept a permanent row on the
    "Opportunities identified" panel claiming money that is now gone. The
    detector's own docstring gives the reason this must not happen, and enforced
    it in exactly the one place that could not be enough.

    ``live_keys`` is every POTENTIAL key a fresh detection run just produced.
    Anything POTENTIAL in the ledger and *absent* from that set is no longer
    detectable — the quote was lost, the line was repriced out of scope, the
    evidence changed — so it is superseded rather than deleted, and the reason
    it stopped being true stays readable.

    Scoped to POTENTIAL deliberately. An ATTRIBUTED or REALIZED row records that
    money moved, which stays true whatever happens next; only a claim about the
    *future* can be invalidated by the future arriving.
    """
    keys = set(live_keys)
    stale = session.scalars(
        select(models.ValueEvent).where(
            models.ValueEvent.organization_id == org,
            models.ValueEvent.value_class == ValueClass.POTENTIAL.value,
            models.ValueEvent.superseded_at.is_(None))).all()
    now = clock.now()
    retired = 0
    for row in stale:
        if row.event_key not in keys:
            row.superseded_at = now
            retired += 1
    if retired:
        session.flush()
    return retired
