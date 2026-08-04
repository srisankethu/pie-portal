"""The executive roll-up — bands with evidence, not a single score.

The temptation in an executive view is one number: a health score out of 100.
It is the wrong shape. A score compresses independent problems into a
comparison nobody can invert — 68 tells you neither what is wrong nor whether
last month's 71 was better in any way that matters — and the weighting that
produces it is an unstated opinion presented as arithmetic.

So this returns **fronts**: named dimensions, each with its own band, its own
movement, and the figures behind it. A front that has no evidence is reported as
``UNKNOWN`` rather than defaulted to fair, because an executive view that quietly
grades missing data as healthy is worse than one that admits a gap.

Bands come from the organization's own thresholds where the policy has an
opinion (the margin floor is the owner's number, not ours), and from movement
elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

GOOD = "GOOD"
FAIR = "FAIR"
POOR = "POOR"
UNKNOWN = "UNKNOWN"

#: Worst first: an executive scanning this should meet the problems before the
#: reassurance, which is the opposite of how a scorecard usually sorts.
_SEVERITY = {POOR: 0, FAIR: 1, UNKNOWN: 2, GOOD: 3}


@dataclass
class Front:
    key: str
    label: str
    band: str
    headline: str
    detail: str
    value: Optional[float] = None
    previous: Optional[float] = None
    unit: str = ""
    #: Where to go to do something about it. A dimension with nowhere to click
    #: is a dimension that generates meetings rather than actions.
    drill_to: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "band": self.band,
            "headline": self.headline, "detail": self.detail,
            "value": self.value, "previous": self.previous, "unit": self.unit,
            "drill_to": self.drill_to,
        }


def _band_by_movement(delta: Optional[float], *, good_above: float,
                      poor_below: float) -> str:
    if delta is None:
        return UNKNOWN
    if delta >= good_above:
        return GOOD
    if delta <= poor_below:
        return POOR
    return FAIR


def build(*, flow_summary: dict, radar_totals: dict, margin_now: Optional[float],
          margin_prev: Optional[float], margin_floor: float,
          dormant_count: int, active_customers: int,
          coverage: Optional[float]) -> dict:
    """Assemble the fronts from figures other modules already computed."""
    fronts: list[Front] = []

    # ── revenue ──────────────────────────────────────────────────────────────
    pct = flow_summary.get("pct")
    fronts.append(Front(
        key="revenue", label="Revenue", unit="pct",
        value=pct, band=_band_by_movement(pct, good_above=0.02, poor_below=-0.05),
        headline=("no comparable prior period" if pct is None
                  else f"{pct:+.1%} against the previous period"),
        detail=(f"{flow_summary.get('current_total', 0):,.0f} this period against "
                f"{flow_summary.get('previous_total', 0):,.0f}."),
        drill_to="revenue-flow"))

    # ── margin ───────────────────────────────────────────────────────────────
    if margin_now is None:
        band, headline = UNKNOWN, "not enough cost coverage to state a margin"
    elif margin_now < margin_floor:
        band = POOR
        headline = f"{margin_now:.1%}, below the {margin_floor:.0%} review floor"
    else:
        move = None if margin_prev is None else margin_now - margin_prev
        band = GOOD if (move is None or move >= -0.005) else FAIR
        headline = f"{margin_now:.1%}" + (
            f", {move:+.1f} points" if move is not None else "")
    fronts.append(Front(
        key="margin", label="Margin", unit="ratio", value=margin_now,
        previous=margin_prev, band=band, headline=headline,
        detail=("Aggregated as total gross profit over total revenue, not as an "
                "average of per-line margins."),
        drill_to="opportunities"))

    # ── customer base ────────────────────────────────────────────────────────
    churn_share = (dormant_count / active_customers) if active_customers else None
    fronts.append(Front(
        key="base", label="Customer base", unit="count",
        value=float(active_customers), band=(
            UNKNOWN if churn_share is None
            else POOR if churn_share > 0.25 else FAIR if churn_share > 0.1 else GOOD),
        headline=(f"{active_customers} trading, {dormant_count} gone quiet"),
        detail="Quiet means no order for six months — an observation, not a churn "
               "prediction.",
        drill_to="journey"))

    # ── money on the table ───────────────────────────────────────────────────
    confident = radar_totals.get("confident_impact", 0.0)
    fronts.append(Front(
        key="opportunity", label="Recoverable", unit="money", value=confident,
        band=GOOD if confident <= 0 else FAIR,
        headline=f"{confident:,.0f} identified with sufficient evidence",
        detail=(f"{radar_totals.get('count', 0)} relationships flagged; only the "
                f"well-evidenced ones are counted in this figure."),
        drill_to="opportunities"))

    # ── evidence quality ─────────────────────────────────────────────────────
    fronts.append(Front(
        key="evidence", label="Evidence quality", unit="ratio", value=coverage,
        band=(UNKNOWN if coverage is None
              else POOR if coverage < 0.4 else FAIR if coverage < 0.7 else GOOD),
        headline=("unknown" if coverage is None
                  else f"{coverage:.0%} of relationships have usable cost data"),
        detail="Margin is not asserted for a relationship without enough cost "
               "coverage, so this bounds everything else on this page.",
        drill_to="data"))

    fronts.sort(key=lambda f: _SEVERITY[f.band])
    worst = fronts[0].band if fronts else UNKNOWN
    return {
        "overall": worst,
        "fronts": [f.to_dict() for f in fronts],
        "note": ("Dimensions are reported separately on purpose. A single health "
                 "score hides which thing is wrong and buries the weighting that "
                 "produced it."),
    }
