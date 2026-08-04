"""Ranked opportunities: how much is at stake, and how sure we are.

Two axes, and the second is the one most tools omit. A list ranked by size alone
puts a ₹4L opportunity computed from two invoices above a ₹90k one computed from
sixty, and a salesperson who chases the first and finds nothing stops trusting
the list. ``CustomerItemMetric`` already records both — the money-denominated
gaps and ``data_sufficiency`` — so this ranks on what is persisted rather than
inventing a score.

**Confidence is evidence, not probability.** ``SUFFICIENT`` does not mean "this
will convert"; it means the figure rests on enough transactions over enough
months, with enough cost coverage, to be worth acting on. Presenting it as a
percentage would imply a forecast the platform has no basis for, so it stays a
band with its reasons attached.

Nothing here decides what to *do* about an opportunity. The action comes from
``decisions/``, where deterministic facts meet interpretation. This is the
deterministic half: which relationships have money on the table, and how solid
the evidence is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models
from ..config import CommercialThresholds

#: Opportunity kinds, in the order a reader should think about them: the ones
#: with a named cause first, the residual last.
MARGIN_EROSION = "MARGIN_EROSION"
BELOW_PEERS = "BELOW_PEERS"
COST_NOT_PASSED = "COST_NOT_PASSED"
VOLUME_DECLINE = "VOLUME_DECLINE"

_CONFIDENCE_RANK = {"SUFFICIENT": 3, "PARTIAL": 2, "INSUFFICIENT": 1}


@dataclass
class Opportunity:
    customer_id: str
    product_id: str
    customer_label: str
    product_label: str
    kind: str
    #: Money on the table per year where the metric supports annualising, else
    #: the observed gap. Never a projection the row cannot evidence.
    impact: float
    annualized: bool
    confidence: str
    confidence_reasons: list[str]
    #: The facts a person would want before acting, already computed.
    margin_change_pp: Optional[float]
    current_margin: Optional[float]
    peer_margin: Optional[float]
    revenue_12m: float
    last_transaction: Optional[str]
    transaction_count: int
    thresholds_version: str

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id, "product_id": self.product_id,
            "customer_label": self.customer_label, "product_label": self.product_label,
            "kind": self.kind,
            "impact": round(self.impact, 2), "annualized": self.annualized,
            "confidence": self.confidence,
            "confidence_reasons": self.confidence_reasons,
            "margin_change_pp": self.margin_change_pp,
            "current_margin": self.current_margin,
            "peer_margin": self.peer_margin,
            "revenue_12m": round(self.revenue_12m, 2),
            "last_transaction": self.last_transaction,
            "transaction_count": self.transaction_count,
            "thresholds_version": self.thresholds_version,
        }


def _f(value) -> Optional[float]:
    return float(value) if value is not None else None


def _kind_of(row: models.CustomerItemMetric) -> Optional[str]:
    """The named cause, from what the metric row already classified.

    Deliberately returns None rather than a catch-all: an opportunity nobody can
    name a cause for is one a salesperson cannot open a conversation about, and
    padding the list with those is how a radar becomes a table nobody reads.
    """
    if row.erosion_kind == "COST_DRIVEN":
        return COST_NOT_PASSED
    if (row.margin_change_pp or 0) < 0:
        return MARGIN_EROSION
    if (row.margin_deviation_pp or 0) < 0 and (row.peer_count or 0) >= 3:
        return BELOW_PEERS
    if (row.volume_change_pct or 0) < 0:
        return VOLUME_DECLINE
    return None


def _impact(row: models.CustomerItemMetric) -> tuple[float, bool]:
    """Money at stake, and whether it is an annual figure.

    Prefers the annualized gap the metric computed, which is only populated when
    the row cleared the span and transaction floors — so using it here inherits
    that discipline instead of re-deciding it.
    """
    annual = _f(row.annualized_historical_margin_gap)
    if annual and annual > 0:
        return annual, True
    gap = _f(row.historical_margin_gap) or 0.0
    peer = _f(row.peer_margin_gap) or 0.0
    return max(gap, peer, 0.0), False


def below_floor(session: Session, org: str, th: CommercialThresholds) -> dict:
    """What the materiality floor excluded, so an empty radar can explain itself.

    A screen that is empty because every gap is small should say exactly that,
    with the largest one it rejected and the floor it was measured against. The
    fix for an always-empty radar on a small book is a better empty state, never
    a lower floor — lowering it to make the screen look busy would make every
    figure on it untrustworthy.
    """
    floor = th.min_material_gap
    rows = session.scalars(
        select(models.CustomerItemMetric)
        .where(models.CustomerItemMetric.organization_id == org)).all()

    excluded = []
    for row in rows:
        if _kind_of(row) is None:
            continue
        impact, _ = _impact(row)
        if 0 < impact < floor:
            excluded.append(impact)

    return {
        "floor": floor,
        "excluded_count": len(excluded),
        "largest_excluded": round(max(excluded), 2) if excluded else None,
        "total_excluded": round(sum(excluded), 2) if excluded else 0.0,
        "relationships_examined": len(rows),
    }


def build(session: Session, org: str, th: CommercialThresholds, *,
          customer_names: dict[str, str], product_names: dict[str, str],
          limit: int = 100) -> list[Opportunity]:
    """Every relationship with a material, named, evidenced gap."""
    floor = th.min_material_gap
    rows = session.scalars(
        select(models.CustomerItemMetric)
        .where(models.CustomerItemMetric.organization_id == org)).all()

    out: list[Opportunity] = []
    for row in rows:
        kind = _kind_of(row)
        if kind is None:
            continue
        impact, annualized = _impact(row)
        if impact < floor:
            # Not a threshold lowered to make the screen look busy — below this
            # the gap is real and not worth an afternoon, which is the owner's
            # own policy figure, editable in Settings.
            continue
        out.append(Opportunity(
            customer_id=row.customer_id, product_id=row.product_id,
            customer_label=customer_names.get(row.customer_id, row.customer_id),
            product_label=product_names.get(row.product_id, row.product_id),
            kind=kind, impact=impact, annualized=annualized,
            confidence=row.data_sufficiency or "INSUFFICIENT",
            confidence_reasons=list(row.sufficiency_reasons or []),
            margin_change_pp=_f(row.margin_change_pp),
            current_margin=_f(row.current_margin),
            peer_margin=_f(row.same_item_median_margin),
            revenue_12m=_f(row.revenue_12m) or 0.0,
            last_transaction=(row.last_transaction_date.isoformat()
                              if row.last_transaction_date else None),
            transaction_count=row.transaction_count or 0,
            thresholds_version=row.thresholds_version or "",
        ))

    # Confidence first, then money. Ranking by money alone puts a large figure
    # drawn from two invoices above a smaller one drawn from sixty, and the
    # first wasted trip is what stops the list being used.
    out.sort(key=lambda o: (_CONFIDENCE_RANK.get(o.confidence, 0), o.impact),
             reverse=True)
    return out[:limit]


def totals(opportunities: Iterable[Opportunity]) -> dict:
    """Headline numbers, split by confidence so the total is never overstated."""
    rows = list(opportunities)
    by_confidence: dict[str, float] = {}
    by_kind: dict[str, float] = {}
    for o in rows:
        by_confidence[o.confidence] = by_confidence.get(o.confidence, 0.0) + o.impact
        by_kind[o.kind] = by_kind.get(o.kind, 0.0) + o.impact
    return {
        "count": len(rows),
        "total_impact": round(sum(o.impact for o in rows), 2),
        # The figure worth quoting: everything else is a lead, not a number.
        "confident_impact": round(by_confidence.get("SUFFICIENT", 0.0), 2),
        "by_confidence": {k: round(v, 2) for k, v in by_confidence.items()},
        "by_kind": {k: round(v, 2) for k, v in by_kind.items()},
    }
