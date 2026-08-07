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

**Support before lift.** A share computed over two customers is not a pattern,
so a pairing below ``MIN_PEERS`` reports no affinity at all rather than a
number that will be quoted in a meeting. Same discipline as every other floor in
this package.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
Revenue and counts only — no cost and no margin reach this module, which is why
the whole view is visible to a salesperson.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from ..categories import LABELS, ORDER, UNCATEGORISED
from ..config import CommercialThresholds
from . import periods

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

#: How long a line has to be quiet before a customer counts as having lapsed
#: out of it, as a multiple of the window. Expressed against the window rather
#: than in fixed days so a one-year view and a two-year view stay consistent
#: about what "stopped" means.
LAPSE_WINDOW_SHARE = 1.0


@dataclass(frozen=True)
class MixLine:
    """One line of trade, reduced to what a mix grid needs."""

    customer_id: str
    date: date
    amount: float
    category: str


@dataclass
class Cell:
    category: str
    state: str
    revenue: float
    last_traded: Optional[date]
    orders: int

    def to_dict(self) -> dict:
        return {
            "category": self.category, "label": LABELS.get(self.category, self.category),
            "state": self.state, "revenue": round(self.revenue, 2),
            "last_traded": self.last_traded.isoformat() if self.last_traded else None,
            "orders": self.orders,
        }


def build(lines: Iterable[MixLine], names: dict[str, str], as_of: date, *,
          thresholds: CommercialThresholds,
          categories_sold: Optional[Iterable[str]] = None,
          months: int = 12) -> dict:
    """The grid, the affinity behind every gap, and what it will not claim."""
    rows = [r for r in lines if r.category and r.category != UNCATEGORISED]
    sold = _sold(categories_sold, rows)
    window = periods.months_back(as_of, months)
    start = window[0].start if window else as_of

    if not rows or not sold:
        return {
            "as_of": as_of.isoformat(), "categories": _columns(sold),
            "customers": [], "affinity": [], "counts": {},
            "cell_meanings": CELL_MEANING, "months": months,
            "min_peers": MIN_PEERS,
            "empty_reason": (
                "No trade is categorised yet, so there is no product mix to "
                "show. Item categories come from Zoho, from the HSN map, or "
                "from an override in Settings."
                if not sold else
                "Nothing has been traded yet."),
            "thresholds_version": thresholds.version,
        }

    # ── one pass to the per-customer, per-line facts ────────────────────────
    per: dict[str, dict[str, Cell]] = {}
    for r in rows:
        cells = per.setdefault(r.customer_id, {})
        cell = cells.get(r.category)
        if cell is None:
            cell = Cell(r.category, NEVER, 0.0, None, 0)
            cells[r.category] = cell
        cell.orders += 1
        if r.date >= start:
            cell.revenue += r.amount
        cell.last_traded = max(cell.last_traded or r.date, r.date)

    for cells in per.values():
        for cell in cells.values():
            cell.state = BUYS if (cell.last_traded and cell.last_traded >= start) else LAPSED

    # ── affinity: of those who take A, how many also take B ─────────────────
    taken: dict[str, set[str]] = {c: set() for c in sold}
    for customer, cells in per.items():
        for category, cell in cells.items():
            if cell.state == BUYS and category in taken:
                taken[category].add(customer)

    customers = []
    for customer, cells in sorted(
            per.items(),
            key=lambda kv: -sum(c.revenue for c in kv[1].values())):
        present = {c: cells.get(c) or Cell(c, NEVER, 0.0, None, 0) for c in sold}
        held = [c for c in sold if present[c].state == BUYS]
        customers.append({
            "customer_id": customer,
            "label": names.get(customer) or f"Unnamed customer (id {customer})",
            "cells": [present[c].to_dict() for c in sold],
            "revenue": round(sum(c.revenue for c in present.values()), 2),
            "lines_held": len(held),
            "lines_sold": len(sold),
            # The gaps, ranked by how many comparable customers take them. This
            # is the "worth a call" ordering, and it is a *suggestion of where
            # to look*, never an assertion that the money is there.
            "gaps": _gaps(present, held, taken, sold),
        })

    return {
        "as_of": as_of.isoformat(),
        "categories": _columns(sold),
        "customers": customers,
        "affinity": _affinity_table(taken, sold),
        "counts": {
            "customers": len(customers),
            "full_coverage": sum(1 for c in customers
                                 if c["lines_held"] == len(sold)),
            "single_line": sum(1 for c in customers if c["lines_held"] == 1),
            "lapsed_cells": sum(1 for c in customers
                                for cell in c["cells"] if cell["state"] == LAPSED),
        },
        "cell_meanings": CELL_MEANING,
        "months": months,
        "min_peers": MIN_PEERS,
        "empty_reason": None,
        "thresholds_version": thresholds.version,
    }


def _columns(sold: list[str]) -> list[dict]:
    return [{"category": c, "label": LABELS.get(c, c)} for c in sold]


def _sold(declared: Optional[Iterable[str]], rows: list[MixLine]) -> list[str]:
    """The columns, in the catalogue's declared order.

    Ordered by ``categories.ORDER`` rather than by anything in the data, so the
    grid's columns are in the same place on every screen and every run — a
    matrix whose columns move is one nobody can compare against last month's
    screenshot.
    """
    if declared is not None:
        names = {c for c in declared if c and c != UNCATEGORISED}
    else:
        names = {r.category for r in rows}
    return [c for c in ORDER if c in names]


def _gaps(cells: dict[str, Cell], held: list[str],
          taken: dict[str, set[str]], sold: list[str]) -> list[dict]:
    """Every line this customer does not currently buy, with its affinity.

    ``affinity`` is the strongest single pairing: of the customers who take a
    line this one *does* hold, what share also take the missing line. The line
    it is measured from travels with it, because "34%" is meaningless without
    "of your cutting-tool customers".
    """
    out = []
    for category in sold:
        cell = cells[category]
        if cell.state == BUYS:
            continue
        best: Optional[dict] = None
        for anchor in held:
            share, peers = _lift(taken, anchor, category)
            if share is None:
                continue
            if best is None or share > best["share"]:
                best = {"from": anchor, "from_label": LABELS.get(anchor, anchor),
                        "share": share, "peers": peers}
        out.append({
            "category": category, "label": LABELS.get(category, category),
            "state": cell.state,
            "last_traded": cell.last_traded.isoformat() if cell.last_traded else None,
            "affinity": best,
        })
    # Lapsed first — the line was already approved once — then by how many
    # comparable customers take it.
    out.sort(key=lambda g: (g["state"] != LAPSED,
                            -((g["affinity"] or {}).get("share") or 0.0)))
    return out


def _lift(taken: dict[str, set[str]], anchor: str, target: str
          ) -> tuple[Optional[float], int]:
    """Share of ``anchor``'s customers who also take ``target``.

    ``None`` below the support floor. A share over four customers is an anecdote,
    and an anecdote rendered as a percentage is how a screen ends up quoted in a
    meeting as though it were evidence.
    """
    base = taken.get(anchor) or set()
    if len(base) < MIN_PEERS:
        return None, len(base)
    both = base & (taken.get(target) or set())
    return round(len(both) / len(base), 4), len(base)


def _affinity_table(taken: dict[str, set[str]], sold: list[str]) -> list[dict]:
    """Every ordered pair, for the legend under the grid.

    Ordered pairs rather than unordered: "of coolant buyers, 90% take cutting
    tools" and "of cutting-tool buyers, 20% take coolant" are both true, both
    useful, and describe completely different opportunities. Collapsing them
    into one symmetric number would lose the only one worth acting on.
    """
    out = []
    for anchor in sold:
        for target in sold:
            if anchor == target:
                continue
            share, peers = _lift(taken, anchor, target)
            out.append({
                "from": anchor, "from_label": LABELS.get(anchor, anchor),
                "to": target, "to_label": LABELS.get(target, target),
                "share": share, "peers": peers,
                "estimable": share is not None,
            })
    return out


def unavailable() -> list[dict]:
    """What this grid refuses to tell you, said where it would be read."""
    return [{
        "what": "Whether a gap is an opportunity",
        "why": ("An empty cell means they do not buy that line from us. It "
                "cannot mean they do not buy it, or that they need it — a shop "
                "with no measuring room has no metrology gap. The affinity "
                "figure beside each gap says how many comparable customers do "
                "take that line; the judgement stays with the person making "
                "the call."),
    }]
