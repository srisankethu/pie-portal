"""The morning read: what needs you, what is at risk, what moved.

Every other screen in this product answers a question about a *period* — has
the mix shifted, is the base churning, where did revenue go. This one answers
"what do I do now", which is a different shape: a worklist, not a narrative.

**It computes almost nothing.** Thirteen of the numbers on this page already
exist, and they are read from the builders that the individual screens use
rather than re-derived here. That is deliberate and it is the whole design
constraint: a daily view that worked out "overdue" its own way would eventually
disagree with the Cash screen, and nobody could say which was right. So
``assemble`` takes what those builders returned and selects from it. The only
arithmetic in this module is addition of figures it was handed.

**Freshness is a band, not a footnote.** PIE is a derived mirror — Zoho is the
system of record — and the state fold runs at the end of a sync. There is no
scheduler in this codebase, so "daily" is honestly "as of the last sync". A
page that implies otherwise is worse than one that says how old it is, because
somebody will look for this morning's payment and conclude the product is
broken rather than that the sync has not run. So the first band carries the age
and, past a threshold, says plainly which day's picture this is.

**Every tile is a count with a way in.** A number you cannot click is a
dashboard; a number that opens the worklist is a product. `route` is the same
vocabulary `vizPath` already resolves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

from ... import clock

#: How old the last sync may be before the page stops presenting itself as
#: current. A working day: a sync that ran yesterday evening is a fine basis
#: for this morning, and one that last ran on Friday is not a Tuesday briefing.
STALE_AFTER_HOURS = 30

#: Bands, in the order somebody works them.
FRESHNESS = "FRESHNESS"
NEEDS_YOU = "NEEDS_YOU"
AT_RISK = "AT_RISK"
COMMITTED = "COMMITTED"
MOVED = "MOVED"

BAND_LABEL = {
    FRESHNESS: "Can I trust this",
    NEEDS_YOU: "Needs you",
    AT_RISK: "At risk",
    COMMITTED: "Committed",
    MOVED: "What moved",
}

BAND_QUESTION = {
    FRESHNESS: "How old is this picture",
    NEEDS_YOU: "What is blocked on a decision from you",
    AT_RISK: "What is going wrong right now",
    COMMITTED: "What lands in the next seven days",
    MOVED: "What the last sync brought in",
}


@dataclass
class Tile:
    """One fact, and where to go about it.

    ``count`` and ``amount`` are both optional because the two kinds of tile
    answer differently: "eleven customers past their cycle" is a worklist
    length, "₹1.4Cr already due" is an exposure. A tile carrying both states
    the count first — it is the thing somebody acts on.
    """

    key: str
    label: str
    #: What the number means, in the words the owning screen already uses.
    why: str
    count: Optional[int] = None
    amount: Optional[float] = None
    #: A second figure where the tile is genuinely two-sided (cash in and out).
    amount_out: Optional[float] = None
    #: Destination in the insight layer's own route vocabulary.
    route: Optional[str] = None
    #: Per connected company, where the source can answer it exactly. Omitted
    #: rather than approximated — see ``_breakdown``.
    breakdown: list[dict[str, Any]] = field(default_factory=list)
    #: True when the tile is reporting a good state rather than a problem.
    settled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "why": self.why,
            "count": self.count, "amount": self.amount,
            "amount_out": self.amount_out, "route": self.route,
            "breakdown": self.breakdown, "settled": self.settled,
        }


def _tiles(band: str, tiles: Iterable[Tile],
           question: Optional[str] = None) -> dict[str, Any]:
    rows = [t for t in tiles if t is not None]
    return {
        "key": band,
        "label": BAND_LABEL[band],
        # Overridden only where the band is actually reporting over a window
        # somebody chose. The default question describes the default window, so
        # leaving it in place while the numbers came from a different range
        # would be the screen contradicting itself in its own subtitle.
        "question": question or BAND_QUESTION[band],
        "tiles": [t.to_dict() for t in rows],
        # A band with nothing in it is an answer, and a good one. The screen
        # says so rather than rendering an empty rectangle.
        "empty": not any(
            (t.count or 0) or (t.amount or 0) or (t.amount_out or 0) for t in rows),
    }


def freshness(*, now: datetime, last_sync: Optional[dict],
              state_on: Optional[date]) -> dict[str, Any]:
    """How old this picture is, and whether to trust it as today's.

    Its own function rather than a tile, because it gates the rest of the page
    rather than sitting alongside it: a stale sync does not make one number
    wrong, it makes every number a different day's.
    """
    finished = (last_sync or {}).get("finished_at")
    started = (last_sync or {}).get("started_at")
    stamp = finished or started
    age_hours: Optional[float] = None
    when = _parse(stamp)
    if when is not None:
        age_hours = round((now - when).total_seconds() / 3600, 1)

    stale = age_hours is None or age_hours > STALE_AFTER_HOURS
    return {
        "last_sync_at": stamp,
        "last_sync_status": (last_sync or {}).get("status"),
        "age_hours": age_hours,
        "state_built_on": state_on.isoformat() if state_on else None,
        "stale": stale,
        "stale_after_hours": STALE_AFTER_HOURS,
        "headline": _freshness_headline(age_hours, stale, last_sync),
    }


def _freshness_headline(age_hours: Optional[float], stale: bool,
                        last_sync: Optional[dict]) -> str:
    if not last_sync:
        return ("Nothing has been synced yet, so there is no picture to read. "
                "Connect a company and run a sync.")
    status = str(last_sync.get("status") or "").upper()
    if status and status not in ("SUCCESS", "COMPLETED", "OK"):
        return (f"The last sync finished as {status.lower()}. What follows is "
                f"the picture before it, not after.")
    if age_hours is None:
        return "The last sync did not record when it finished."
    if not stale:
        return f"Synced {_ago(age_hours)}. This is current."
    return (f"Last synced {_ago(age_hours)}. This is that day's picture, not "
            f"today's — run a sync before working from it.")


def _parse(stamp: Any) -> Optional[datetime]:
    """A stored timestamp, comparable to ``clock.now()``.

    Through ``clock.aware`` rather than by hand. Half the timestamps in this
    database are naive — SQLite keeps no offset — and subtracting one of those
    from an aware ``now()`` is a TypeError rather than a wrong answer, which is
    the good case only if somebody runs it. ``clock.aware`` is where that rule
    already lives.
    """
    if not stamp:
        return None
    try:
        return clock.aware(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _ago(hours: float) -> str:
    if hours < 1:
        return "in the last hour"
    if hours < 24:
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''} ago"
    days = int(hours // 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


def _breakdown(rows: Iterable[dict[str, Any]], *, by: str = "company",
               value: str = "count") -> list[dict[str, Any]]:
    """Per-company split of an *exact* source.

    Only ever called with rows the caller counted itself. A builder's own list
    is capped — ``supply`` returns forty open orders beside a count of every
    one — so splitting that list would produce parts that do not add up to the
    headline, which is worse than no breakdown at all.
    """
    out: dict[str, float] = {}
    for row in rows:
        key = str(row.get(by) or "Source not recorded")
        out[key] = out.get(key, 0) + float(row.get(value) or 0)
    # Zero rows dropped: a breakdown reading "Source not recorded — 0" is a
    # line of type that says nothing, and on a tile that is already settled it
    # is the only line there is.
    return [{"company": k, "value": v} for k, v in
            sorted(out.items(), key=lambda kv: kv[1], reverse=True) if v]


def assemble(*, now: datetime, as_of: Optional[date], state_on: Optional[date],
             last_sync: Optional[dict],
             approvals_pending: int = 0,
             decisions_by_band: Optional[dict[str, int]] = None,
             stock: Optional[dict] = None,
             supply: Optional[dict] = None,
             cadence: Optional[dict] = None,
             cash: Optional[dict] = None,
             moved: Optional[dict] = None,
             moved_window: Optional[tuple[Optional[date], Optional[date]]] = None,
             committed_weeks: int = 1,
             currency: str = "INR") -> dict[str, Any]:
    """The five bands, from what the other builders already computed."""
    bands = [
        _tiles(NEEDS_YOU, _needs_you(approvals_pending, decisions_by_band or {})),
        _tiles(AT_RISK, _at_risk(stock or {}, supply or {}, cadence or {}, cash or {})),
        _tiles(COMMITTED, _committed(supply or {}, cash or {}, committed_weeks),
               committed_question(committed_weeks)),
        _tiles(MOVED,
               _moved(moved or {},
                      on_document_dates=bool(moved_window and moved_window[0])),
               moved_question(moved_window)),
    ]
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "currency": currency,
        "freshness": freshness(now=now, last_sync=last_sync, state_on=state_on),
        "bands": bands,
        # The range the MOVED band is reporting over, echoed so the control can
        # show what the server actually used rather than what was asked for.
        # Null both ways means the default: since the previous sync.
        "moved_window": {
            "from": moved_window[0].isoformat() if moved_window and moved_window[0] else None,
            "to": moved_window[1].isoformat() if moved_window and moved_window[1] else None,
        },
        # One number the screen leads with: how many tiles actually want
        # something. Not a sum of money — dead stock and overdue cash are
        # different claims and adding them produces a figure that means nothing.
        "wants_attention": sum(
            1 for b in bands for t in b["tiles"]
            if not t["settled"] and ((t["count"] or 0) or (t["amount"] or 0))),
    }


def _needs_you(approvals_pending: int, by_band: dict[str, int]) -> list[Tile]:
    high = int(by_band.get("HIGH", 0))
    total = sum(int(v) for v in by_band.values())
    return [
        Tile(key="approvals", label="Approvals waiting",
             why="A quote line is held until somebody with the authority signs it.",
             count=approvals_pending, route="approvals",
             settled=approvals_pending == 0),
        Tile(key="decisions", label="Decisions in the queue",
             why=("Detected from persisted rows, not predicted. "
                  f"{high} of them are high priority."),
             count=total, route="list", settled=total == 0),
    ]


def _at_risk(stock: dict, supply: dict, cadence: dict, cash: dict) -> list[Tile]:
    counts = stock.get("counts") or {}
    supply_counts = supply.get("counts") or {}
    overdue = cash.get("overdue") or {}
    tiles = [
        Tile(key="overdue_cash", label="Already due, not settled",
             why=("Invoices and bills past their own due date. Being overdue "
                  "is what disproves treating them as this week's movement."),
             amount=float(overdue.get("inflow") or 0),
             amount_out=float(overdue.get("outflow") or 0),
             route="payments",
             settled=not (overdue.get("inflow") or overdue.get("outflow"))),
        Tile(key="past_cycle", label="Customers past their own cycle",
             why=("Measured against each customer's median gap between orders, "
                  "not one company-wide interval."),
             count=int(cadence.get("overdue_count") or 0), route="cadence",
             settled=not cadence.get("overdue_count")),
        Tile(key="oversold", label="Committed beyond stock",
             why="Open orders promise more of an item than is on the shelf.",
             count=int(counts.get("oversold") or 0), route="stock",
             settled=not counts.get("oversold")),
        Tile(key="ageing_orders", label="Purchase orders ageing",
             why=("Past the ageing threshold with stock still to come. Age is "
                  "the rank because most of these carry no promised date."),
             count=int(supply_counts.get("stale_open_orders") or 0),
             route="supply", settled=not supply_counts.get("stale_open_orders")),
    ]
    return tiles


def _committed(supply: dict, cash: dict, weeks: int = 1) -> list[Tile]:
    """What the committed book does next, over however many weeks were asked for.

    Summed across the buckets rather than reading the first, because the
    horizon is now a choice. The committed book is bucketed by ISO week from
    the Monday of ``as_of`` — which is why the control counts *weeks* and not
    an arbitrary date range: a range of "the next three days" cannot be
    answered from weekly buckets, and answering it approximately would be
    inventing precision the fold does not have.

    ``weeks`` is what was **asked for**, not ``len(buckets)``. Deriving it from
    the buckets read correctly whenever there were any and lied whenever there
    were none: a book with no cash fold yet returned zero buckets, so a reader
    who had chosen eight weeks saw the heading snap back to "this week" while
    the control still said 8w. A label that disagrees with the control beside
    it is worse than one that admits the horizon is empty.
    """
    buckets = cash.get("buckets") or []
    supply_counts = supply.get("counts") or {}
    return [
        Tile(key="cash_this_week",
             label=("Cash due this week" if weeks <= 1
                    else f"Cash due in {weeks} weeks"),
             why=("Invoices and bills falling due in the weeks their own "
                  "documents name. Movement, never a balance — the platform "
                  "reads payments, not bank positions."),
             amount=sum(float(b.get("inflow") or 0) for b in buckets),
             amount_out=sum(float(b.get("outflow") or 0) for b in buckets),
             route="payments", settled=not buckets),
        Tile(key="open_orders", label="Purchase orders still open",
             why="Placed with a supplier, with stock still to arrive.",
             count=int(supply_counts.get("open_orders") or 0), route="supply",
             settled=not supply_counts.get("open_orders")),
    ]


def _moved(moved: dict, *, on_document_dates: bool = False) -> list[Tile]:
    """What moved — read on one of two calendars, and honest about which.

    The default is ingest time: a purchase order dated last week that arrived
    in this morning's sync counts, because it is new *to you*. That makes the
    band "what PIE learned", not "what the business did", and the tiles say so.

    ``on_document_dates`` is the reading for a chosen day, and it exists
    because the default was wrong for exactly the control that looks most
    natural: pressing **Today** the morning after a first sync showed the whole
    book, since every row was "first seen" that day. A person choosing a day
    means documents *dated* that day. Customers and items have no business
    date, so they stay on first-seen in both modes — and keep saying so, which
    is what stops the one band from quietly meaning two things.
    """
    dated = "The document's own date — not when the platform first saw it."
    first_seen = ("First seen by the platform in the most recent sync."
                  if not on_document_dates else
                  "First seen by the platform in the chosen window — a master "
                  "record has no business date to be counted by.")
    spec = [
        ("invoices", "Invoices raised", "customer", dated),
        ("payments", "Payments received", "payments", dated),
        ("purchase_orders", "Purchase orders placed", "supply", dated),
        ("customers", "New customers", "customer", first_seen),
        ("products", "New items", "stock", first_seen),
    ]
    out = []
    for key, label, route, dated_why in spec:
        entry = moved.get(key) or {}
        out.append(Tile(
            key=key, label=label,
            why=(dated_why if on_document_dates else
                 "First seen by the platform in the most recent sync."),
            count=int(entry.get("count") or 0),
            amount=(float(entry["amount"]) if entry.get("amount") is not None else None),
            route=route,
            breakdown=_breakdown(entry.get("by_company") or [], value="count"),
            settled=not entry.get("count")))
    return out


def committed_question(weeks: int) -> Optional[str]:
    """How far ahead the committed band is looking, or None for the default.

    Counted in weeks rather than days because the underlying fold is bucketed
    by ISO week — see ``_committed``. Saying "the next 28 days" over four weekly
    buckets would promise a precision the data does not carry, and the first
    bucket is the week ``as_of`` falls in rather than the seven days from it.
    """
    if weeks <= 1:
        return None
    return f"What lands in the next {weeks} weeks"


def moved_question(window: Optional[tuple[Optional[date], Optional[date]]]
                   ) -> Optional[str]:
    """What the MOVED band is reporting over, in words, or None for the default.

    A chosen window is counted on the documents' own dates — the subtitle says
    "dated", and means it. It used to say "first seen by the platform", which
    was the truthful description of counting ``created_at`` and the wrong
    behaviour behind it: the morning after a first sync, **Today** showed the
    whole book, because every row the platform holds had been first seen that
    day. Only the *default* view (no window chosen) still reads by ingest —
    "what the last sync brought in" is genuinely a question about the platform,
    and its subtitle stays the default band question for that reason.
    """
    if not window or window[0] is None:
        return None
    frm, to = window
    if to == frm:
        return f"Documents dated {frm.isoformat()}"
    if to is None:
        # Open at the top — "last 30 days" runs to now, and calling that a day
        # is how a caught-in-review wording bug reads on a live screen.
        return f"Documents dated {frm.isoformat()} or later"
    return f"Documents dated {frm.isoformat()} to {to.isoformat()}"


def window_since(last_sync: Optional[dict], previous_sync: Optional[dict],
                 *, now: datetime,
                 frm: Optional[date] = None, to: Optional[date] = None,
                 ) -> tuple[Optional[datetime], Optional[datetime]]:
    """The ingest window the "what moved" band reports over.

    Defaults to the end of the previous sync through now, rather than the
    *start* of the last one: a sync writes throughout its run, so anything keyed
    to its start would count rows the run before it had already reported.

    ``frm``/``to`` override that with a day or a range somebody chose. Both are
    inclusive dates and ``to`` is taken to the end of its day, because a reader
    picking "the 7th" means the whole of the 7th and a half-open bound would
    silently drop everything that arrived after midnight.

    **This window governs the ``MOVED`` band alone**, and the reason is worth
    stating where somebody might be tempted to widen it. The other three bands
    are not periods: an approval is waiting *now*, an invoice is overdue *now*,
    a commitment lands in the next seven days *from now*. Answering "what was
    overdue last Tuesday" would mean reconstructing a past state, which this
    module does not do and must not appear to. A control that silently
    re-scoped them would return three numbers that either ignored it or lied.
    """
    if frm is not None:
        end = (datetime.combine(to, datetime.max.time(), tzinfo=now.tzinfo)
               if to is not None else now)
        return datetime.combine(frm, datetime.min.time(), tzinfo=now.tzinfo), end
    start = (_parse((previous_sync or {}).get("finished_at"))
             or _parse((last_sync or {}).get("started_at")))
    if start is None:
        # No previous run to measure from — report the last day rather than
        # every row the platform has ever held, which would read as a flood on
        # the first morning and be true of nothing.
        start = now - timedelta(days=1)
    return start, now
