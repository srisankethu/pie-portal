"""Folding events into state.

The engine knows three things: how to read events in order, how to apply a
delta, and how to persist the result. It knows nothing about inventory, orders
or money — those live in reducers, which are registered rather than
switched on. A new state is a new reducer module and a line in ``REDUCERS``;
this file does not change.

**Order.** Events are folded by ``(occurred_on, seq)``, not by ``seq`` alone.
A state as of a date is what you get by applying facts in the order they
*happened*, so a correction read today for a March document must land in March.
This is deliberately the opposite of ``replay``, which folds by ``seq`` because
it is rebuilding *what the platform read*, and there the later reading must
win. Two questions, two orders; both are right for their own job.

**Ops.** Four, and no more: SET, ADD, MAX, MIN. A closed vocabulary is what
makes a delta inspectable, and an inspectable delta is what makes
``StateTransition`` worth persisting. Anything a reducer cannot express in
these four is a sign the fact belongs in the event, not in the fold.

**Money.** Deltas carry real ``Decimal`` values and real ``date`` objects
through the fold. They are serialised only at the boundary, as strings — a
float accumulator would put binary noise into a figure the whole platform is
meant to reproduce exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..clock import now as utc_now
from ..domain import models
from . import events as ev

# ── the delta vocabulary ─────────────────────────────────────────────────────
SET = "SET"     #: Last value in fold order wins. For observations.
ADD = "ADD"     #: Accumulate. For counts and money.
MAX = "MAX"     #: Keep the largest. For "most recent" dates.
MIN = "MIN"     #: Keep the smallest. For "oldest" dates.

OPS = (SET, ADD, MAX, MIN)


class UnknownOp(ValueError):
    """A delta using an op the fold cannot apply. Loud, because a silently
    ignored change would show up as a state that is quietly wrong."""


@dataclass(frozen=True)
class Delta:
    """What one event does to one state key.

    Frozen and data-only: a delta that could compute would be a second place
    for the arithmetic to live, and the reducer is the first.
    """

    state: str
    key: str
    #: ``(op, field, value)`` triples.
    changes: tuple[tuple[str, str, Any], ...]


class Reducer(Protocol):
    """One state's rules. Registered, never switched on.

    Deliberately narrow — three members, all of which every reducer uses. A
    protocol where an implementer needs half the methods is two protocols
    (``CLAUDE.md`` §5, I).
    """

    state: str
    handles: frozenset[str]

    #: Whether to record the per-event working for this state.
    #:
    #: Transitions answer one question — ``why()``, the drill-down that walks a
    #: decision card down to the events that made its number — and only a state
    #: some detector reads can ever be asked. A state that feeds a *screen*
    #: rather than a decision is never the subject of that question, so its
    #: working is written and never read: at this book's size the two monthly
    #: trade states alone accounted for about 90,000 such rows per build, and
    #: most of the time the fold spent.
    #:
    #: Defaults to True, so every existing state keeps the audit trail it has.
    #: Turning it off is a claim about a state's readers, and it is wrong the
    #: moment a detector starts reading that state — which is why the flag
    #: lives beside ``handles`` where the next person will see it.
    records_transitions: bool = True

    def apply(self, event: models.BusinessEvent, ctx: "Masters",
              as_of: date) -> Iterable[Delta]:
        """Deltas this event causes, or nothing if it causes none."""
        ...


@dataclass
class Masters:
    """Local ids for the external references events carry, read once per build.

    Events store external ids on purpose: a name is not an identity and a local
    id is a detail of *our* database, not a fact about the business. But a
    state must be keyed on the local id, because the same external id in two
    connected companies is two different things. Resolving per event would be a
    query per event; this is three queries per build.
    """

    products: dict[tuple[Optional[str], Optional[str], str], str] = field(default_factory=dict)
    customers: dict[tuple[Optional[str], Optional[str], str], str] = field(default_factory=dict)
    vendors: dict[tuple[Optional[str], Optional[str], str], str] = field(default_factory=dict)
    #: References no master answers to. Collected here rather than returned by
    #: each reducer, because this is the one place a resolution happens and so
    #: the one place a miss can be seen. Reported, never dropped quietly.
    unresolved: list[dict[str, str]] = field(default_factory=list)

    def product(self, e: models.BusinessEvent, external_id: str) -> Optional[str]:
        return self._find(self.products, e, external_id, "product")

    def customer(self, e: models.BusinessEvent, external_id: str) -> Optional[str]:
        return self._find(self.customers, e, external_id, "customer")

    def vendor(self, e: models.BusinessEvent, external_id: str) -> Optional[str]:
        return self._find(self.vendors, e, external_id, "vendor")

    def _find(self, index: dict[tuple[Optional[str], Optional[str], str], str],
              e: models.BusinessEvent, external_id: str,
              kind: str) -> Optional[str]:
        found = index.get((e.connector, e.connection_id, external_id))
        if found is None:
            # Rows imported before provenance was recorded carry no source.
            # They are still this organization's rows; falling back to them is
            # what keeps a state buildable on a database that predates the
            # source columns.
            found = index.get((None, None, external_id))
        if found is None:
            self.unresolved.append({
                "missing": kind,
                "reference": external_id,
                "event_type": e.event_type,
                "document": f"{e.source_doc_type} {e.source_doc_id}",
            })
        return found


def _load_masters(session: Session, org: str) -> Masters:
    def index(model, id_attr: str) -> dict[tuple[Optional[str], Optional[str], str], str]:
        rows = session.scalars(
            select(model).where(model.organization_id == org)).all()
        return {(r.connector, r.connection_id, r.external_id): getattr(r, id_attr)
                for r in rows}

    return Masters(
        products=index(models.Product, "product_id"),
        customers=index(models.Customer, "customer_id"),
        vendors=index(models.Vendor, "vendor_id"),
    )


# ── applying a delta ─────────────────────────────────────────────────────────
def apply_change(current: dict[str, Any], op: str, field_name: str,
                 value: Any) -> None:
    """Fold one change into an accumulator, in place."""
    if op == SET:
        current[field_name] = value
        return
    existing = current.get(field_name)
    if op == ADD:
        if existing is None:
            current[field_name] = value
        elif isinstance(existing, Decimal) or isinstance(value, Decimal):
            current[field_name] = Decimal(str(existing)) + Decimal(str(value))
        else:
            current[field_name] = existing + value
        return
    if op == MAX:
        current[field_name] = value if existing is None else max(existing, value)
        return
    if op == MIN:
        current[field_name] = value if existing is None else min(existing, value)
        return
    raise UnknownOp(f"{op!r} is not one of {OPS}. A change the fold cannot "
                    "apply would show up as a state that is quietly wrong.")


def as_decimal(raw: Any) -> Optional[Decimal]:
    """A stored payload number back to ``Decimal``. The reader half of the
    contract ``_serialisable`` writes, and here rather than in each reducer so
    the two halves cannot drift apart.

    ``None`` and blank stay unknown. Zoho sends "" for an unset numeric field,
    and coercing that to zero would turn "the source did not report stock" into
    "there is none" — which puts the whole catalogue out of stock.
    """
    if raw is None or raw == "":
        return None
    return raw if isinstance(raw, Decimal) else Decimal(str(raw))


def _serialisable(value: Any) -> Any:
    """Python value → JSON. Money as a string, dates as ISO.

    The same contract as an event payload, for the same reason: a float
    round-trip is exactly the noise this platform exists to not have.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


# ── the registry ─────────────────────────────────────────────────────────────
#
# Populated at import time by ``state.reducers``. A dict rather than a
# type-chain: adding a state must not mean editing this file.
REDUCERS: dict[str, Reducer] = {}


def register(reducer: Reducer) -> Reducer:
    """Register a reducer. Refuses a duplicate name and an unknown event type —
    both would produce a state that silently never updates."""
    if reducer.state in REDUCERS:
        raise ValueError(f"two reducers claim the state {reducer.state!r}")
    unknown = set(reducer.handles) - set(ev.EVENT_TYPES)
    if unknown:
        raise ValueError(
            f"{reducer.state} handles unregistered event types {sorted(unknown)}; "
            "add them to events.EVENT_TYPES or the reducer will never fire")
    REDUCERS[reducer.state] = reducer
    return reducer


# ── building ─────────────────────────────────────────────────────────────────
@dataclass
class BuildReport:
    as_of: Optional[date] = None
    events_read: int = 0
    rows_written: dict[str, int] = field(default_factory=dict)
    transitions: int = 0
    #: Events a reducer wanted but could not key, because the master they refer
    #: to is not in this database. Reported, never dropped quietly — a state
    #: built from 90% of the evidence that says so is usable; one that says
    #: nothing is not.
    unresolved: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "events_read": self.events_read,
            "rows_written": dict(self.rows_written),
            "transitions": self.transitions,
            "unresolved": self.unresolved[:20],
            "unresolved_count": len(self.unresolved),
        }


def build(session: Session, org: str, *, as_of: date,
          thresholds_version: str = "",
          states: Optional[Iterable[str]] = None) -> BuildReport:
    """Fold every live event up to ``as_of`` into state rows for that day.

    Rebuilt from the beginning each time, deliberately. An incremental fold
    would need to know which events had already been counted, which is a second
    cursor to keep correct — and the whole point of a derived projection is
    that it is cheap to be sure about. Bounding this is step four of the
    evolution, and it is a snapshot problem, not a correctness one.
    """
    wanted = list(states) if states is not None else list(REDUCERS)
    active = [REDUCERS[s] for s in wanted if s in REDUCERS]
    report = BuildReport(as_of=as_of)
    if not active:
        return report

    masters = _load_masters(session, org)
    handled: set[str] = set()
    for reducer in active:
        handled |= set(reducer.handles)

    # (state, key) -> accumulated value, and how many events contributed.
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    counts: dict[tuple[str, str], int] = {}
    transitions: list[models.StateTransition] = []

    for event in _ordered(session, org, as_of, handled):
        report.events_read += 1
        for reducer in active:
            if event.event_type not in reducer.handles:
                continue
            for d in reducer.apply(event, masters, as_of):
                slot = acc.setdefault((d.state, d.key), {})
                for op, name, value in d.changes:
                    apply_change(slot, op, name, value)
                counts[(d.state, d.key)] = counts.get((d.state, d.key), 0) + 1
                if getattr(reducer, "records_transitions", True):
                    transitions.append(models.StateTransition(
                        organization_id=org, event_seq=event.seq,
                        event_type=event.event_type, state=d.state, key=d.key,
                        as_of=as_of, occurred_on=event.occurred_on,
                        changes=[[op, name, _serialisable(v)]
                                 for op, name, v in d.changes]))
    report.unresolved = masters.unresolved

    _persist(session, org, as_of, wanted, acc, counts, thresholds_version,
             transitions, report)
    return report


def _ordered(session: Session, org: str, as_of: date,
             types: set[str]) -> Iterable[models.BusinessEvent]:
    """Live events up to ``as_of``, in the order they happened.

    ``(occurred_on, seq)``: the business date decides, and the order they were
    read in only breaks ties. See the module docstring for why this differs
    from replay.
    """
    stmt = (select(models.BusinessEvent)
            .where(models.BusinessEvent.organization_id == org,
                   models.BusinessEvent.superseded_at.is_(None),
                   models.BusinessEvent.occurred_on <= as_of,
                   models.BusinessEvent.event_type.in_(sorted(types)))
            .order_by(models.BusinessEvent.occurred_on,
                      models.BusinessEvent.seq))
    return session.scalars(stmt)


def _persist(session: Session, org: str, as_of: date, states: list[str],
             acc: dict[tuple[str, str], dict[str, Any]],
             counts: dict[tuple[str, str], int], thresholds_version: str,
             transitions: list[models.StateTransition],
             report: BuildReport) -> None:
    """Write the fold. Existing rows for this day are replaced, not merged.

    A state row is the whole answer for that key on that day; merging a new
    fold into an old one would leave fields from a reading that no longer
    exists — the exact failure supersession removes from the event log.
    """
    for state in states:
        session.execute(
            delete(models.BusinessState).where(
                models.BusinessState.organization_id == org,
                models.BusinessState.state == state,
                models.BusinessState.as_of == as_of))
        # Transitions are the working of *this* fold, so the previous working
        # for the same state and day goes with the row it explained.
        session.execute(
            delete(models.StateTransition).where(
                models.StateTransition.organization_id == org,
                models.StateTransition.state == state,
                models.StateTransition.as_of == as_of))

    now = utc_now()
    # Every state this fold covered, including the ones that produced no rows.
    # A state absent from the report reads as "not folded"; a state that folded
    # to nothing is a different fact, and one the rows cannot state themselves
    # because there are none of them. So the report states it. The distinction
    # matters downstream: a reader of the *rows* alone — ``latest_as_of`` — sees
    # the same emptiness either way, which is why the decision producer names a
    # state it cannot read rather than reading it as an empty business.
    for state in states:
        report.rows_written.setdefault(state, 0)
    for (state, key), value in acc.items():
        session.add(models.BusinessState(
            organization_id=org, state=state, key=key, as_of=as_of,
            value={k: _serialisable(v) for k, v in value.items()},
            event_count=counts.get((state, key), 0),
            thresholds_version=thresholds_version, computed_at=now))
        report.rows_written[state] = report.rows_written.get(state, 0) + 1
    for t in transitions:
        session.add(t)
    report.transitions = len(transitions)
    session.flush()


def load(session: Session, org: str, state: str, as_of: date,
         ) -> dict[str, dict[str, Any]]:
    """Every key of one state on one day, as stored.

    Values come back exactly as written — money as strings, dates as ISO. The
    caller parses, because the caller knows which fields are money; guessing
    here from the shape of a string is how a part number becomes a number.
    """
    rows = session.scalars(
        select(models.BusinessState).where(
            models.BusinessState.organization_id == org,
            models.BusinessState.state == state,
            models.BusinessState.as_of == as_of)).all()
    return {r.key: dict(r.value or {}) for r in rows}


def latest_as_of(session: Session, org: str, state: str) -> Optional[date]:
    """The most recent day this state was built for, or ``None`` if never.

    A reader asks for this rather than assuming today. A state is built at the
    end of a sync, so "today" is right only until the first day nobody syncs —
    and a screen that asked for today and got nothing would report an empty
    shelf rather than a stale one.
    """
    return session.scalar(
        select(func.max(models.BusinessState.as_of))
        .where(models.BusinessState.organization_id == org,
               models.BusinessState.state == state))


def why(session: Session, org: str, state: str, key: str, as_of: date,
        ) -> list[dict[str, Any]]:
    """Every event that moved one state key on one day, and by how much.

    The question a projection cannot otherwise answer. Ordered the way the fold
    applied them, so the list reads as the arithmetic that produced the number.
    ``as_of`` is required rather than defaulted: two folds over the same events
    to different days are different arithmetics, and silently explaining the
    wrong one is worse than asking.
    """
    rows = session.scalars(
        select(models.StateTransition)
        .where(models.StateTransition.organization_id == org,
               models.StateTransition.state == state,
               models.StateTransition.key == key,
               models.StateTransition.as_of == as_of)
        .order_by(models.StateTransition.occurred_on,
                  models.StateTransition.event_seq)).all()
    return [{
        "event_seq": r.event_seq,
        "event_type": r.event_type,
        "occurred_on": r.occurred_on.isoformat(),
        "changes": r.changes,
    } for r in rows]
