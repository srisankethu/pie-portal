"""Two measures per subject, positioned so the outliers find themselves.

The specification asks for a "Margin vs Revenue Landscape" and a separate
"Product Momentum Galaxy". They are the same chart: a scatter of one subject
against two continuous measures, read for position rather than rank. Building
them as two modules would mean two quadrant rules drifting apart and two
tooltips explaining the same thing differently, so this is one function with the
subject and the vertical measure as parameters.

**Quadrants carry the action.** A scatter on its own is a picture; what makes it
useful is that each corner means something a person can do. High revenue with
low margin is the one to work first — the money is already flowing and only the
price is wrong. Low revenue with low margin is usually not worth a conversation
at all, and saying so is more useful than leaving twenty dots equally weighted.

The vertical split is the organization's own review floor rather than the median
of the plotted points. A median moves when the selection moves, so a relationship
could cross from "fine" to "poor" because a different filter was applied — which
makes the quadrant meaningless. The floor is policy, editable in Settings, and
stamped with the version that produced it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models
from ..config import CommercialThresholds

RELATIONSHIP = "relationship"
PRODUCT = "product"

#: What the vertical axis measures. Margin answers "are we pricing this well";
#: momentum answers "is this growing". Both against revenue on the horizontal.
BY_MARGIN = "margin"
BY_MOMENTUM = "momentum"

#: The four jobs, one per corner. The *names* are the same for both measures on
#: purpose — the shape of the chart is worth learning once — but the meanings are
#: not interchangeable, and shipping one set for both is how a page ends up
#: telling a reader that a shrinking item is "priced below the floor". The
#: vertical axis is a different question in each case, so the words are too.
QUADRANT_LABELS: dict[str, str] = {
    "FIX_FIRST": "Fix first",
    "PROTECT": "Protect",
    "REVIEW": "Review",
    "LEAVE": "Leave",
}

_MARGIN_MEANINGS: dict[str, str] = {
    "FIX_FIRST": "Large, and below the floor. The money is already flowing and "
                 "only the price is wrong — the shortest route to profit on "
                 "this page.",
    "PROTECT": "Large and healthy. Nothing to fix; worth knowing which "
               "relationships these are before changing anything that touches "
               "them.",
    "REVIEW": "Small and below the floor. Worth a look in a batch, not a trip "
              "on its own.",
    "LEAVE": "Small and healthy. Genuinely fine. Shown so the page is honest "
             "about how much of the book needs nothing.",
}

_MOMENTUM_MEANINGS: dict[str, str] = {
    "FIX_FIRST": "Large, and shrinking. The volume was already there and is "
                 "going — the most expensive thing on this page to leave "
                 "alone. Nothing here says the price is wrong.",
    "PROTECT": "Large and growing. Nothing to fix; worth knowing what these "
               "are before changing anything that touches them.",
    "REVIEW": "Small and shrinking. Worth a look in a batch, not a trip on "
              "its own.",
    "LEAVE": "Small and growing. Genuinely fine. Shown so the page is honest "
             "about how much of the book needs nothing.",
}


def quadrants_for(measure: str) -> dict[str, dict[str, str]]:
    """The four corners, described for the measure actually on the vertical."""
    meanings = (_MARGIN_MEANINGS if measure == BY_MARGIN else _MOMENTUM_MEANINGS)
    return {key: {"label": label, "meaning": meanings[key]}
            for key, label in QUADRANT_LABELS.items()}


@dataclass
class Point:
    id: str
    label: str
    sublabel: Optional[str]
    x: float                 # revenue — always the horizontal
    y: Optional[float]       # margin or momentum, as a ratio
    size: float              # transaction count, for weight
    quadrant: str
    confidence: str
    #: The ids a drill-down needs. A dot nobody can open is a dot nobody trusts.
    customer_id: Optional[str] = None
    product_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "sublabel": self.sublabel,
            "x": round(self.x, 2),
            "y": round(self.y, 4) if self.y is not None else None,
            "size": self.size, "quadrant": self.quadrant,
            "confidence": self.confidence,
            "customer_id": self.customer_id, "product_id": self.product_id,
        }


def _f(v) -> Optional[float]:
    return float(v) if v is not None else None


def _quadrant(x: float, y: Optional[float], x_split: float,
              y_split: float) -> str:
    if y is None:
        # Not "poor" — unknown. Grading missing evidence as a problem is how a
        # page invents work; grading it as fine is how it hides some.
        return "REVIEW" if x >= x_split else "LEAVE"
    big = x >= x_split
    healthy = y >= y_split
    if big and not healthy:
        return "FIX_FIRST"
    if big and healthy:
        return "PROTECT"
    if not big and not healthy:
        return "REVIEW"
    return "LEAVE"


def build(session: Session, org: str, th: CommercialThresholds, *,
          subject: Literal["relationship", "product"] = RELATIONSHIP,
          measure: Literal["margin", "momentum"] = BY_MARGIN,
          customer_names: Optional[dict] = None,
          product_names: Optional[dict] = None) -> dict:
    """Position every subject against revenue and the chosen vertical measure."""
    names_c = customer_names or {}
    names_p = product_names or {}
    rows = session.scalars(
        select(models.CustomerItemMetric)
        .where(models.CustomerItemMetric.organization_id == org)).all()

    if subject == PRODUCT:
        points = _by_product(rows, names_p, measure)
    else:
        points = _by_relationship(rows, names_c, names_p, measure)

    if not points:
        return {"subject": subject, "measure": measure, "points": [],
                "x_split": 0.0, "y_split": 0.0,
                "quadrants": quadrants_for(measure)}

    # Horizontal split at the median revenue: "large" is relative to this book,
    # and a fixed money threshold would be wrong in another currency anyway.
    xs = sorted(p.x for p in points)
    x_split = xs[len(xs) // 2]
    # Vertical split is *policy*, not a statistic — see the module docstring.
    y_split = th.margin_floor if measure == BY_MARGIN else 0.0

    for p in points:
        p.quadrant = _quadrant(p.x, p.y, x_split, y_split)

    points.sort(key=lambda p: p.x, reverse=True)
    counts: dict[str, int] = {}
    for p in points:
        counts[p.quadrant] = counts.get(p.quadrant, 0) + 1

    return {
        "subject": subject,
        "measure": measure,
        "x_label": "Revenue, trailing 12 months",
        "y_label": ("Margin" if measure == BY_MARGIN
                    else "Volume change against the prior period"),
        "x_split": round(x_split, 2),
        "y_split": round(y_split, 4),
        "y_split_meaning": ("Your review floor, from Settings — not the median of "
                            "these points, which would move whenever the "
                            "selection did."
                            if measure == BY_MARGIN else
                            "Flat. Above it grew, below it shrank."),
        "points": [p.to_dict() for p in points],
        "counts": counts,
        "quadrants": quadrants_for(measure),
        "thresholds_version": th.version,
    }


def _by_relationship(rows, names_c: dict, names_p: dict, measure: str) -> list[Point]:
    out: list[Point] = []
    for r in rows:
        revenue = _f(r.revenue_12m) or 0.0
        if revenue <= 0:
            continue
        y = _f(r.current_margin) if measure == BY_MARGIN else _f(r.volume_change_pct)
        out.append(Point(
            id=f"{r.customer_id}:{r.product_id}",
            label=names_c.get(r.customer_id, r.customer_id),
            sublabel=names_p.get(r.product_id, r.product_id),
            x=revenue, y=y, size=r.transaction_count or 0, quadrant="",
            confidence=r.data_sufficiency or "INSUFFICIENT",
            customer_id=r.customer_id, product_id=r.product_id))
    return out


def _by_product(rows, names_p: dict, measure: str) -> list[Point]:
    """Roll relationships up to the item.

    Margin is aggregated as Σ gross profit ÷ Σ revenue — never the mean of the
    per-relationship margins, which would let a tiny line count as much as a
    large one and is the single most common way a rolled-up margin goes wrong.
    """
    acc: dict[str, dict] = {}
    for r in rows:
        revenue = _f(r.revenue_12m) or 0.0
        if revenue <= 0:
            continue
        e = acc.setdefault(r.product_id, {
            "revenue": 0.0, "profit": 0.0, "txns": 0,
            "qty_recent": 0.0, "qty_previous": 0.0,
            "confidences": [], "covered_revenue": 0.0})
        e["revenue"] += revenue
        if r.gross_profit_12m is not None:
            e["profit"] += float(r.gross_profit_12m)
            e["covered_revenue"] += revenue
        e["txns"] += r.transaction_count or 0
        e["qty_recent"] += _f(r.qty_recent) or 0.0
        e["qty_previous"] += _f(r.qty_previous) or 0.0
        e["confidences"].append(r.data_sufficiency or "INSUFFICIENT")

    order = {"SUFFICIENT": 3, "PARTIAL": 2, "INSUFFICIENT": 1}
    out: list[Point] = []
    for product_id, e in acc.items():
        if measure == BY_MARGIN:
            # Only assert a margin where enough of the revenue actually has cost
            # behind it; otherwise the point has no vertical position and says so.
            y = (e["profit"] / e["covered_revenue"]) if e["covered_revenue"] else None
        else:
            prev = e["qty_previous"]
            y = ((e["qty_recent"] - prev) / prev) if prev else None
        # The weakest evidence in the roll-up governs it — an aggregate is only
        # as trustworthy as the thinnest row inside it.
        weakest = min(e["confidences"], key=lambda c: order.get(c, 0))
        out.append(Point(
            id=product_id, label=names_p.get(product_id, product_id),
            sublabel=None, x=e["revenue"], y=y, size=e["txns"], quadrant="",
            confidence=weakest, product_id=product_id))
    return out
