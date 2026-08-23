"""Write an inbound line, decide it later, export the lot.

The whole surface of the inbound-demand record. Four writes and three reads, in
one module rather than a class: these are functions over a session, which is
what the rest of this codebase is (``attribution/ledger``, ``identity/service``),
and a class here would hold no state worth holding.

Two rules run through all of it:

* **Nothing is normalised on the way in.** ``capture`` stores the bytes it was
  given. It inspects ``raw_text`` to refuse an empty one, and inspecting is not
  storing — the value written is the argument, never a cleaned copy of it.
* **Nothing is mutated.** A line is written once. A disposition is superseded by
  a new row and the old one stays readable, which is the ``ValueEvent`` /
  ``BusinessEvent`` rule and not a third pattern.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Union

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import now as utc_now
from ..domain import enums, models


class CaptureRefusal(ValueError):
    """A write this module will not perform.

    Loud rather than permissive, for the reason ``LedgerRefusal`` is: every
    caller here is an adapter turning somebody else's message into a row, and an
    adapter that silently drops or silently coerces produces a corpus whose gaps
    are invisible. A refusal names what was wrong with the input.
    """


ChannelArg = Union[enums.InboundChannel, str]
DispositionArg = Union[enums.LineDisposition, str]


def _channel(value: ChannelArg) -> str:
    """The stored form of a channel, or a refusal naming the closed set."""
    try:
        return enums.InboundChannel(value).value
    except ValueError:
        known = ", ".join(c.value for c in enums.InboundChannel)
        raise CaptureRefusal(
            f"{value!r} is not an inbound channel; the closed set is {known}. "
            "A new arrival route is a schema decision, not a free-text value"
        ) from None


def _disposition(value: DispositionArg) -> str:
    """The stored form of a disposition, or a refusal naming the closed set."""
    try:
        return enums.LineDisposition(value).value
    except ValueError:
        known = ", ".join(d.value for d in enums.LineDisposition)
        raise CaptureRefusal(
            f"{value!r} is not a disposition; the closed set is {known}. "
            "A line whose fate is not yet known has no disposition row at all"
        ) from None


# ── writing ──────────────────────────────────────────────────────────────────
def capture(session: Session, organization_id: str, *,
            raw_text: str,
            channel: ChannelArg,
            customer_ref: str = "",
            source_ref: str = "",
            received_at: Optional[datetime] = None) -> models.InboundLine:
    """Record one enquiry line exactly as it arrived. Returns the written row.

    ``raw_text`` is stored verbatim — the argument object, not a copy that has
    been through ``strip()``, ``upper()`` or a unicode normalisation. Callers
    that want tidy text tidy it when they read.

    Every call appends. There is no upsert and no content key: two identical
    asks a week apart are two enquiries, and a table that merged them would
    under-report repeat demand. An adapter that can redeliver one message is the
    right place to notice that, on ``source_ref``.

    ``received_at`` is when the customer sent it and should come from the
    message. It defaults to now, which is right for a live webhook and wrong for
    a backlog import — an importer that lets it default files a year of demand
    on one afternoon.
    """
    if not (organization_id or "").strip():
        raise CaptureRefusal("organization_id is empty; every row is one tenant's")
    if not raw_text or not raw_text.strip():
        raise CaptureRefusal(
            "raw_text is empty; a captured line with no text inflates the "
            "coverage denominator with an ask nobody made")

    row = models.InboundLine(
        organization_id=organization_id,
        raw_text=raw_text,
        channel=_channel(channel),
        customer_ref=customer_ref,
        source_ref=source_ref,
        received_at=received_at or utc_now(),
        captured_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def set_disposition(session: Session, organization_id: str, inbound_line_id: str,
                    disposition: DispositionArg, *,
                    source_ref: str = "",
                    decided_by_user_id: Optional[str] = None,
                    decided_at: Optional[datetime] = None
                    ) -> tuple[models.InboundLineDisposition, bool]:
    """Decide a line, or correct an earlier decision. Returns ``(row, written)``.

    Three outcomes, the same three ``attribution.ledger.record`` has and for the
    same reason — a re-runnable job must be able to call this every night:

    * No live disposition — append one. ``written=True``.
    * A live one saying the same thing, from the same source — nothing is
      written and the existing row comes back. ``written=False``. This is the
      guard working, not a failure.
    * A live one saying something different — stamp ``superseded_at`` on it and
      append the new row. ``written=True``. **Both stay readable**; that is the
      entire point of the second table.

    The line must exist and must belong to ``organization_id``. Checked rather
    than assumed: a disposition filed against another tenant's line would be a
    cross-tenant write, and it would also put the coverage numerator in a
    different population from its denominator.
    """
    stored = _disposition(disposition)

    line = session.scalars(
        select(models.InboundLine).where(
            models.InboundLine.inbound_line_id == inbound_line_id,
            models.InboundLine.organization_id == organization_id)).first()
    if line is None:
        raise CaptureRefusal(
            f"no inbound line {inbound_line_id!r} in organization "
            f"{organization_id!r}; a disposition for a line this tenant does "
            "not have is not history, it is corruption")

    live = live_disposition(session, organization_id, inbound_line_id)
    if live is not None:
        if live.disposition == stored and live.source_ref == source_ref:
            return live, False
        # Superseded, not mutated. The old claim stays and every current-state
        # read filters it out, so a correction rewrites the answer without
        # destroying what the report said last week.
        live.superseded_at = utc_now()
        session.flush()

    row = models.InboundLineDisposition(
        organization_id=organization_id,
        inbound_line_id=inbound_line_id,
        disposition=stored,
        source_ref=source_ref,
        decided_by_user_id=decided_by_user_id,
        decided_at=decided_at or utc_now(),
        recorded_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row, True


# ── reading ──────────────────────────────────────────────────────────────────
def live_disposition(session: Session, organization_id: str,
                     inbound_line_id: str
                     ) -> Optional[models.InboundLineDisposition]:
    """This line's current answer, or ``None`` if it has not been decided.

    ``None`` means *undecided*, which is not the same as "answered with
    nothing" — that would be a row saying ABSTAINED or NO_STOCK. Absence of a
    disposition is UNKNOWN, and no reader may band it as a failure (§1).
    """
    return session.scalars(
        select(models.InboundLineDisposition).where(
            models.InboundLineDisposition.organization_id == organization_id,
            models.InboundLineDisposition.inbound_line_id == inbound_line_id,
            models.InboundLineDisposition.superseded_at.is_(None))).first()


def disposition_history(session: Session, organization_id: str,
                        inbound_line_id: str
                        ) -> list[models.InboundLineDisposition]:
    """Every version ever recorded for this line, oldest first, live one last.

    Superseded rows included — they are the reason the table exists. Ordered by
    ``recorded_at`` then ``disposition_id`` so two corrections inside one clock
    tick still come back in one fixed order rather than the database's.
    """
    return list(session.scalars(
        select(models.InboundLineDisposition).where(
            models.InboundLineDisposition.organization_id == organization_id,
            models.InboundLineDisposition.inbound_line_id == inbound_line_id)
        .order_by(models.InboundLineDisposition.recorded_at,
                  models.InboundLineDisposition.disposition_id)).all())


@dataclass(frozen=True)
class ExportedLine:
    """One line as it leaves: the capture, its verdict, and how it got there.

    A dataclass rather than the ORM rows, so an export is a value that survives
    the session it was read in and cannot lazily load a fourth table halfway
    through being written to a file.

    ``raw_text`` is the same ``str`` object the database returned, passed
    through untouched. No field here is a cost or a margin, and none may become
    one — this is the surface most likely to be handed to somebody outside the
    business, and it carries text and outcomes only.
    """

    inbound_line_id: str
    raw_text: str
    channel: str
    customer_ref: str
    source_ref: str
    received_at: datetime
    captured_at: datetime
    #: The live disposition's value, or ``None`` — undecided, not failed.
    disposition: Optional[str]
    disposition_decided_at: Optional[datetime]
    #: Every version, including superseded ones, oldest first. An export that
    #: kept only the current answer would be unable to explain a report run
    #: before the correction.
    disposition_history: tuple[models.InboundLineDisposition, ...]


def export(session: Session, organization_id: str) -> list[ExportedLine]:
    """One tenant's complete inbound record, raw text byte-intact.

    The whole set, not a page. This is the corpus a benchmark reads and the
    denominator a coverage report divides by, and both are wrong if the export
    silently stops — ``data_status``'s CSV export exists for exactly that reason
    on a different table.

    Scoped to one organization and to nothing else. There is no argument that
    widens it and there must not be: these rows are a tenant's customers'
    own words, and a cross-tenant aggregate would need a consent primitive
    this codebase does not have (see ``models.InboundLine``).

    Ordered by ``received_at`` then ``inbound_line_id`` — a total order that
    does not depend on insertion order or the backend, so the same data exports
    to the same bytes twice.
    """
    lines = list(session.scalars(
        select(models.InboundLine)
        .where(models.InboundLine.organization_id == organization_id)
        .order_by(models.InboundLine.received_at,
                  models.InboundLine.inbound_line_id)).all())

    # One query for the dispositions rather than two per line: an export of a
    # year of enquiries is the one read here that is allowed to be large.
    rows = session.scalars(
        select(models.InboundLineDisposition)
        .where(models.InboundLineDisposition.organization_id == organization_id)
        .order_by(models.InboundLineDisposition.recorded_at,
                  models.InboundLineDisposition.disposition_id)).all()
    by_line: dict[str, list[models.InboundLineDisposition]] = {}
    for row in rows:
        by_line.setdefault(row.inbound_line_id, []).append(row)

    return [_exported(line, by_line.get(line.inbound_line_id, ())) for line in lines]


def _exported(line: models.InboundLine,
              history: Sequence[models.InboundLineDisposition]) -> ExportedLine:
    live = next((r for r in history if r.superseded_at is None), None)
    return ExportedLine(
        inbound_line_id=line.inbound_line_id,
        raw_text=line.raw_text,
        channel=line.channel,
        customer_ref=line.customer_ref,
        source_ref=line.source_ref,
        received_at=line.received_at,
        captured_at=line.captured_at,
        disposition=live.disposition if live is not None else None,
        disposition_decided_at=live.decided_at if live is not None else None,
        disposition_history=tuple(history),
    )
