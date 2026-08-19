"""Which lines each customer takes, which they do not, and which is worth a call.

The bond strip answers "who is close to this book". This answers the other half
of the growth question — *what are they not buying from us* — and the two are
deliberately different shapes, because one is a distribution and the other is a
grid with a name in every row.

**Three cell states, not two, and the third is the whole point.**

``BUYS``     traded in this line inside the window.
``LAPSED``   traded in this line, but not inside the window. This is the
             strongest cell on the grid and a two-state grid throws it away by
             folding it into "no". You *had* the line and lost it: the product
             was approved, somebody was buying it, and it stopped. That is a
             different phone call from a line they have never taken, and it is
             usually a shorter one.
``NEVER``    no trade in this line, ever.

**A gap is not money, and this module will not say that it is.** An empty cell
means they do not buy the line *from us*. It cannot mean they do not buy it: a
job shop with no measuring room does not need metrology, and a grid that treats
every blank as an opportunity is a list that sends somebody on a wasted drive
and is not trusted again. So what is reported beside a gap is **affinity** — how
many comparable customers do take that line — and the reader judges. The
platform is not entitled to an opinion about a need it cannot observe.

**Affinity is co-occurrence, computed, not learned.** Of the customers who buy
line A, what share also buy line B. It is arithmetic over persisted rows, it
carries the thresholds version like everything else here, and no model is
involved at any point — which is what makes "34% of your cutting-tool customers
also take coolant" a fact somebody can check rather than a claim they have to
believe.

**Confidence, base rate and lift — three numbers, because one of them lies on
its own.** ``confidence`` is that co-occurrence share, P(B│A). It is the figure
a person quotes and the figure this grid displays, and taken alone it is
systematically misleading in exactly the direction that wastes a sales visit: if
almost every customer on the book buys cutting tools, then "90% of your coolant
customers also take cutting tools" is 90% and carries no information at all,
because 90% of *everybody* takes them. ``base_rate`` is P(B) — how common the
line is regardless of the anchor — and ``lift`` is the ratio of the two. Lift
near 1.0 means the pairing tells you nothing; above 1.0 means holding A really
does go with holding B.

So gaps are **ordered by lift and shown with confidence**, and the base rate
travels with both so the reader can see why a big-looking share was demoted.

This matters far more on the principal pivot than on the category one, and it is
worth knowing which you are looking at. There are five lines of the business and
nearly every customer takes cutting tools, so on ``BY_CATEGORY`` the base rate of
the dominant line is close to 1 and lift can only ever say "no information here"
about it. Across principals the base rates are uneven and lift genuinely
reorders the list.

The function behind ``confidence`` was called ``_lift`` and returned P(B│A) —
which is confidence, not lift. Nothing computed the wrong thing; the name did.
It is worth recording because the next person to want a real lift would have
found the name taken, written ``_lift2`` beside it, and left two functions
disagreeing about a word.

**Support before either.** A share computed over two customers is not a pattern,
so a pairing whose anchor is below ``MIN_PEERS`` reports no affinity at all
rather than a number that will be quoted in a meeting. Lift needs the same floor
on the *other* side as well: dividing by a base rate drawn from three customers
produces a large ratio out of nothing, and a rare line would top every list on
noise. Both floors are applied, and a pairing that clears the first but not the
second reports its confidence with ``lift`` left null rather than being dropped —
the co-occurrence is still a fact, and only the comparison is unsupported.

**One grid, two pivots.** The columns are lines of the business or they are
principals, and nothing else in here changes: the cell states, the affinity, the
support floor and the ordering are identical questions asked of a different key.
An authorised distributor needs both — "who has never bought coolant" and "who
has never bought a single Sandvik item" are the same shape of conversation with
different people. Two modules would agree until the first tuning, so this one
takes its columns from the caller and calls the key a ``key``.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
Revenue and counts only — no cost and no margin reach this module, which is why
the whole view is visible to a salesperson.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from ..categories import UNCATEGORISED
from ..config import CommercialThresholds
from . import absence, periods

#: What a cell says.
BUYS = "BUYS"
LAPSED = "LAPSED"
NEVER = "NEVER"

CELL_MEANING: dict[str, str] = {
    BUYS: "Bought this line inside the window.",
    LAPSED: "Bought this line before, but not inside the window. The product "
            "was already approved and the buying stopped — usually a shorter "
            "conversation than a line they have never taken.",
    NEVER: "No trade in this line on record. Whether that is an opportunity "
           "depends on whether they need it, which this platform cannot see.",
}

#: Fewer customers than this behind an affinity figure and it is a coincidence
#: wearing a percentage sign.
MIN_PEERS = 5

#: Fewer customers than this taking a line and its base rate cannot carry a
#: division. Lift is confidence ÷ base rate, so a rare line produces a huge
#: ratio out of two coincidences and tops the list on noise. Separate from
#: ``MIN_PEERS`` because they floor different sides of the comparison: that one
#: guards the anchor a share is computed *over*, this one guards the target the
#: share is compared *against*.
MIN_BASE = 5

#: How long a line has to be quiet before a customer counts as having lapsed
#: out of it, as a multiple of the window. Expressed against the window rather
#: than in fixed days so a one-year view and a two-year view stay consistent
#: about what "stopped" means.
LAPSE_WINDOW_SHARE = 1.0


#: The two things the columns can be. The word "key" throughout the module is
#: deliberate: it is a line of the business or a principal, and every rule below
#: is the same question asked of either.
BY_CATEGORY = "category"
BY_VENDOR = "vendor"


@dataclass(frozen=True)
class Column:
    """One column: what identifies it, and what a person calls it."""

    key: str
    label: str


@dataclass(frozen=True)
class MixLine:
    """One line of trade, reduced to what a mix grid needs."""

    customer_id: str
    date: date
    amount: float
    #: The line of the business, or the principal. Which one is decided by the
    #: caller's ``columns``, and nothing below cares.
    key: str


@dataclass
class Cell:
    key: str
    label: str
    state: str
    revenue: float
    last_traded: Optional[date]
    orders: int
    #: When this customer first took this line, over all history rather than the
    #: window. Added for ``insight/adoption``, which ranks the gaps by the order
    #: the base adopted its lines in and needs the earliest date rather than the
    #: latest — the alternative was a second per-customer index derived from the
    #: same rows, which is the responsibility duplication CLAUDE.md §2 names.
    #: Deliberately not windowed: a first purchase truncated by a window is not
    #: a first purchase.
    first_traded: Optional[date] = None

    def to_dict(self) -> dict:
        # The field is still called ``category`` on the wire. It is what the
        # screen reads, and one grid rendering two pivots should not have to
        # branch on which noun the server used for the same slot.
        return {
            "category": self.key, "label": self.label,
            "state": self.state, "revenue": round(self.revenue, 2),
            "last_traded": self.last_traded.isoformat() if self.last_traded else None,
            "first_traded": self.first_traded.isoformat() if self.first_traded else None,
            "orders": self.orders,
        }


def _empty_reason(all_lines: list[MixLine], order: list[str], dimension: str) -> str:
    """Why this grid is empty, distinguishing the three ways it can be.

    They were served as two, keyed on whether any column existed, and the
    interesting case fell on the wrong side: with 26 sale lines on the book and
    every item's category unset, the screen said "Nothing has been traded yet."
    while the Customers screen in the same session showed the revenue. A wrong
    reason is worse than none — it sends somebody to look at the sync when the
    answer is one field on the item master.
    """
    noun = "line of the business" if dimension == BY_CATEGORY else "supplier"
    if not all_lines:
        return "Nothing has been traded yet, so there is no mix to show."
    where_from = (
        "Item categories come from Zoho, from the HSN ranges, or from an "
        "override in Settings."
        if dimension == BY_CATEGORY else
        "A sale is attributed to a principal through the bills that bought the "
        "item, so this fills in after a full sync.")
    if not order:
        return (f"No {noun} has been defined yet, so trade cannot be grouped "
                f"into one. {where_from}")
    # Trade exists, columns exist, and nothing landed in one.
    return (f"There is trade on the book, but none of it could be placed against "
            f"a {noun}. {where_from}")


def build(lines: Iterable[MixLine], names: dict[str, str], as_of: date, *,
          thresholds: CommercialThresholds,
          columns: Iterable[Column],
          dimension: str = BY_CATEGORY,
          months: int = 12) -> dict:
    """The grid, the affinity behind every gap, and what it will not claim."""
    cols = [c for c in columns if c.key and c.key != UNCATEGORISED]
    order = [c.key for c in cols]
    label_of = {c.key: c.label for c in cols}
    known = set(order)
    # Materialised because the empty state has to be able to tell "nothing was
    # traded" from "trade exists and none of it could be placed in a column",
    # and those differ only in whether there were lines to filter.
    all_lines = list(lines)
    rows = [r for r in all_lines if r.key and r.key in known]
    window = periods.months_back(as_of, months)
    start = window[0].start if window else as_of

    if not rows or not order:
        return {
            "as_of": as_of.isoformat(), "dimension": dimension,
            "categories": [c.__dict__ | {"category": c.key} for c in cols],
            "customers": [], "affinity": [], "counts": {},
            "cell_meanings": CELL_MEANING, "months": months,
            "min_peers": MIN_PEERS, "min_base": MIN_BASE, "population": 0,
            "empty_reason": _empty_reason(all_lines, order, dimension),
            "thresholds_version": thresholds.version,
        }

    # ── one pass to the per-customer, per-line facts ────────────────────────
    per: dict[str, dict[str, Cell]] = {}
    for r in rows:
        cells = per.setdefault(r.customer_id, {})
        cell = cells.get(r.key)
        if cell is None:
            cell = Cell(r.key, label_of.get(r.key, r.key), NEVER, 0.0, None, 0)
            cells[r.key] = cell
        cell.orders += 1
        if r.date >= start:
            cell.revenue += r.amount
        cell.last_traded = max(cell.last_traded or r.date, r.date)
        cell.first_traded = min(cell.first_traded or r.date, r.date)

    for cells in per.values():
        for cell in cells.values():
            cell.state = BUYS if (cell.last_traded and cell.last_traded >= start) else LAPSED

    # ── affinity: of those who take A, how many also take B ─────────────────
    taken: dict[str, set[str]] = {key: set() for key in order}
    for customer, cells in per.items():
        for key, cell in cells.items():
            if cell.state == BUYS and key in taken:
                taken[key].add(customer)

    # The denominator of every base rate: customers with any live trade in the
    # window. Not `len(per)`, which includes customers whose every line has
    # lapsed — they take nothing now, so counting them would depress every base
    # rate and inflate every lift by the same factor.
    population = len({c for holders in taken.values() for c in holders})

    customers = []
    for customer, cells in sorted(
            per.items(),
            key=lambda kv: -sum(c.revenue for c in kv[1].values())):
        present = {k: cells.get(k) or Cell(k, label_of.get(k, k), NEVER, 0.0,
                                           None, 0)
                   for k in order}
        held = [k for k in order if present[k].state == BUYS]
        customers.append({
            "customer_id": customer,
            "label": names.get(customer) or f"Unnamed customer (id {customer})",
            "cells": [present[k].to_dict() for k in order],
            "revenue": round(sum(c.revenue for c in present.values()), 2),
            "lines_held": len(held),
            "lines_sold": len(order),
            # The gaps, ranked by how many comparable customers take them. This
            # is the "worth a call" ordering, and it is a *suggestion of where
            # to look*, never an assertion that the money is there.
            "gaps": _gaps(present, held, taken, order, label_of, population),
        })

    return {
        "as_of": as_of.isoformat(),
        "dimension": dimension,
        "categories": [{"category": c.key, "label": c.label} for c in cols],
        "customers": customers,
        "affinity": _affinity_table(taken, order, label_of, population),
        "counts": {
            "customers": len(customers),
            "full_coverage": sum(1 for c in customers
                                 if c["lines_held"] == len(order)),
            "single_line": sum(1 for c in customers if c["lines_held"] == 1),
            "lapsed_cells": sum(1 for c in customers
                                for cell in c["cells"] if cell["state"] == LAPSED),
        },
        "cell_meanings": CELL_MEANING,
        "months": months,
        "min_peers": MIN_PEERS,
        "min_base": MIN_BASE,
        # What every base rate was computed over, so a reader can check a lift
        # rather than take it.
        "population": population,
        "empty_reason": None,
        "thresholds_version": thresholds.version,
    }


def _gaps(cells: dict[str, Cell], held: list[str],
          taken: dict[str, set[str]], order: list[str],
          label_of: dict[str, str], population: int) -> list[dict]:
    """Every line this customer does not currently buy, with its affinity.

    ``affinity`` is the strongest single pairing: of the customers who take a
    line this one *does* hold, what share also take the missing line. The line
    it is measured from travels with it, because "34%" is meaningless without
    "of your cutting-tool customers".

    **Strongest by lift, not by confidence.** The anchor worth naming is the one
    that tells the reader something they did not already know. Picking by
    confidence picks whichever anchor happens to sit beside the commonest line
    on the book, which is the same answer for every customer and is why an
    affinity column stops being read. Where no pairing supports a lift, the best
    confidence is used instead — a fact with a weaker ordering behind it beats
    an empty cell.
    """
    out = []
    for key in order:
        cell = cells[key]
        if cell.state == BUYS:
            continue
        best: Optional[dict] = None
        best_rank: tuple[int, float] = (-1, -1.0)
        for anchor in held:
            affinity = _affinity(taken, anchor, key, population)
            if affinity.confidence is None:
                continue
            # Lift first where it exists, confidence as the tiebreak and the
            # fallback. The leading flag keeps a pairing that has a lift above
            # every pairing that does not, whatever their shares.
            rank = ((1, affinity.lift) if affinity.lift is not None
                    else (0, affinity.confidence))
            if rank > best_rank:
                best_rank = rank
                best = {"from": anchor,
                        "from_label": label_of.get(anchor, anchor),
                        **affinity.to_dict()}
        out.append({
            "category": key, "label": label_of.get(key, key),
            "state": cell.state,
            "last_traded": cell.last_traded.isoformat() if cell.last_traded else None,
            "affinity": best,
        })
    # Lapsed first — the line was already approved once — then by how much the
    # pairing actually tells you. Ordering on the raw share put whichever line
    # everybody buys at the top of every customer's list.
    out.sort(key=lambda g: (g["state"] != LAPSED,
                            -((g["affinity"] or {}).get("lift") or 0.0),
                            -((g["affinity"] or {}).get("confidence") or 0.0)))
    return out


@dataclass(frozen=True)
class Affinity:
    """One ordered pairing, and the three numbers needed to read it.

    Kept as a value rather than a bare tuple because the three travel together
    everywhere and always have to: ``confidence`` alone is the misleading half,
    and a caller that received only it would have no way of knowing.
    """

    #: P(B│A) — of the customers who take the anchor, the share who also take
    #: the target. The figure a person quotes. ``None`` below ``MIN_PEERS``.
    confidence: Optional[float]
    #: P(B) — how common the target is across the whole book, regardless of the
    #: anchor. What makes a large confidence readable.
    base_rate: Optional[float]
    #: confidence ÷ base_rate. 1.0 means the pairing carries no information;
    #: above 1.0 means holding the anchor really does go with holding the
    #: target. ``None`` when either side is below its floor.
    lift: Optional[float]
    #: How many customers the anchor share was computed over.
    peers: int
    #: How many take the target at all — the denominator of the base rate.
    base: int

    def to_dict(self) -> dict:
        return {"confidence": self.confidence, "base_rate": self.base_rate,
                "lift": self.lift, "peers": self.peers, "base": self.base,
                # The old wire name for confidence. Kept so a client rendering
                # this grid does not break on the same release that adds lift;
                # `share` and `confidence` are the same number by construction.
                "share": self.confidence,
                "estimable": self.confidence is not None}


def _affinity(taken: dict[str, set[str]], anchor: str, target: str,
              population: int) -> Affinity:
    """How much more likely a customer is to take ``target`` given ``anchor``.

    Two independent floors, because the comparison has two sides and each can
    be thin on its own. ``MIN_PEERS`` guards the anchor: a share over four
    customers is an anecdote, and an anecdote rendered as a percentage is how a
    screen ends up quoted in a meeting as though it were evidence. ``MIN_BASE``
    guards the target: lift divides by the base rate, so a line taken by three
    customers yields a huge ratio out of a coincidence and would top every list.

    Clearing the first but not the second returns the confidence with a null
    lift rather than nothing at all — the co-occurrence remains a fact, and it
    is only the comparison against the base rate that is unsupported.
    """
    base_set = taken.get(target) or set()
    base_rate = (round(len(base_set) / population, 4)
                 if population > 0 and len(base_set) >= MIN_BASE else None)

    peers = taken.get(anchor) or set()
    if len(peers) < MIN_PEERS:
        return Affinity(None, base_rate, None, len(peers), len(base_set))

    confidence = round(len(peers & base_set) / len(peers), 4)
    lift = (round(confidence / base_rate, 4)
            if base_rate else None)
    return Affinity(confidence, base_rate, lift, len(peers), len(base_set))


def _affinity_table(taken: dict[str, set[str]], order: list[str],
                    label_of: dict[str, str], population: int) -> list[dict]:
    """Every ordered pair, for the legend under the grid.

    Ordered pairs rather than unordered: "of coolant buyers, 90% take cutting
    tools" and "of cutting-tool buyers, 20% take coolant" are both true, both
    useful, and describe completely different opportunities. Collapsing them
    into one symmetric number would lose the only one worth acting on.

    Lift is *not* symmetric-looking by accident either: it is the same number
    both ways round, since P(B│A)/P(B) equals P(A│B)/P(A). That is a property
    worth knowing rather than a bug — the two confidences differ and say which
    direction is worth selling, while the single shared lift says whether the
    pair is related at all.
    """
    out = []
    for anchor in order:
        for target in order:
            if anchor == target:
                continue
            affinity = _affinity(taken, anchor, target, population)
            out.append({
                "from": anchor, "from_label": label_of.get(anchor, anchor),
                "to": target, "to_label": label_of.get(target, target),
                **affinity.to_dict(),
            })
    return out


def unavailable() -> list[dict]:
    """What this grid refuses to tell you, said where it would be read."""
    return [{
        "what": "Whether a gap is an opportunity",
        # A need the platform cannot observe. No amount of our own data makes
        # a blank cell mean anything more than "not from us".
        "kind": absence.PERMANENT,
        "why": ("An empty cell means they do not buy that line from us. It "
                "cannot mean they do not buy it, or that they need it — a shop "
                "with no measuring room has no metrology gap. The affinity "
                "figure beside each gap says how many comparable customers do "
                "take that line; the judgement stays with the person making "
                "the call."),
    }]
