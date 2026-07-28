"""A plain-language commercial diagnosis, assembled from computed metrics only.

Every number in the sentence comes from a value that was already calculated and
persisted. Nothing here estimates, rounds creatively, or infers — it is a
rendering of ``RelationshipMetrics``, not an analysis of it.

This is deliberately **not** an AI surface. The AI layer may later interpret
these facts (that is what the existing Decision pipeline does), but it never
produces the numbers: a model that invents a margin figure is worse than no
figure at all, and the platform's grounding gate exists precisely to stop that.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..domain.enums import EvidenceSufficiency
from .benchmark import ItemBenchmark
from .config import CommercialThresholds
from .metrics import COST_DRIVEN, MIXED, PRICE_DRIVEN, RelationshipMetrics


def _pct(ratio: Optional[float]) -> str:
    return f"{ratio * 100:.1f}%" if ratio is not None else "unknown"


def _pp(points: Optional[float]) -> str:
    return f"{abs(points) * 100:.1f} percentage points" if points is not None else "unknown"


def _rupees(amount: Optional[Decimal]) -> str:
    return f"₹{float(amount):,.0f}" if amount is not None else "unknown"


def _change(pct: Optional[float]) -> str:
    if pct is None:
        return "is unknown"
    # A move that rounds to nothing must not be described as a decrease — the
    # displayed figure and the verb have to agree.
    if abs(pct) < 0.0005:
        return "was unchanged"
    direction = "increased" if pct > 0 else "decreased"
    return f"{direction} {abs(pct) * 100:.1f}%"


def diagnose(m: RelationshipMetrics, benchmark: Optional[ItemBenchmark],
             th: CommercialThresholds) -> list[str]:
    """The diagnosis as sentences, in the order a reader needs them.

    Returned as a list rather than one blob so the UI can lay them out, and so a
    test can assert on an individual claim.
    """
    out: list[str] = []

    if m.data_sufficiency is EvidenceSufficiency.INSUFFICIENT:
        reasons = "; ".join(m.sufficiency_reasons) or "not enough history"
        return [f"Not enough data to judge this relationship — {reasons}. "
                f"The transactions below are everything that is known."]

    # 1. what happened to margin
    if m.margin_change_pp is not None and m.historical_margin is not None:
        direction = "declined" if m.margin_change_pp < 0 else "improved"
        span = f" over {m.history_months:.0f} months" if m.history_months >= 1 else ""
        out.append(
            f"Margin {direction} from {_pct(m.historical_margin)} to "
            f"{_pct(m.current_margin)}{span} — {_pp(m.margin_change_pp)}.")
    elif m.current_margin is not None:
        out.append(f"Current margin is {_pct(m.current_margin)}. There is no "
                   f"comparable earlier period to measure a change against.")
    else:
        out.append("Margin cannot be calculated — no reliable purchase cost is "
                   "recorded for this item in the period.")

    # 2. why — cost against price
    if m.cost_change_pct is not None and m.price_change_pct is not None:
        out.append(f"Effective unit cost {_change(m.cost_change_pct)}, while net "
                   f"selling price {_change(m.price_change_pct)}.")
        if m.erosion_kind == COST_DRIVEN:
            out.append("The cost increase has not been passed through to this "
                       "customer's price.")
        elif m.erosion_kind == PRICE_DRIVEN:
            out.append("Cost is broadly stable — the movement is on the selling "
                       "price.")
        elif m.erosion_kind == MIXED:
            out.append("Cost rose and the selling price fell at the same time.")

    # 3. how this customer sits against the others
    if benchmark is not None and benchmark.is_reliable(th):
        if benchmark.median_margin is not None:
            comparison = "below" if (benchmark.margin_deviation_pp or 0) < 0 else "above"
            out.append(
                f"Across {benchmark.peer_count} other customers buying this item, "
                f"the median margin is {_pct(benchmark.median_margin)} — this "
                f"customer is {_pp(benchmark.margin_deviation_pp)} {comparison} it. "
                f"That is a benchmark, not a target: volume, freight and terms "
                f"differ between accounts.")
    elif benchmark is not None:
        out.append(
            "No other customer bought this item recently, so there is nothing to "
            "compare this price against."
            if benchmark.peer_count == 0 else
            f"Only {benchmark.peer_count} other customer"
            f"{'' if benchmark.peer_count == 1 else 's'} bought this item recently "
            f"— too few for a meaningful price comparison.")

    # 4. volume — did the margin buy anything
    if m.volume_change_pct is not None:
        if m.volume_change_pct >= th.meaningful_volume_change_pct:
            out.append(f"Volume grew {m.volume_change_pct * 100:.0f}% over the same "
                       f"period, so the lower margin may have been a deliberate "
                       f"trade rather than leakage.")
        elif m.volume_change_pct <= -th.meaningful_volume_change_pct:
            out.append(f"Volume also fell {abs(m.volume_change_pct) * 100:.0f}% — "
                       f"the lower margin bought nothing.")
        else:
            out.append("Volume is approximately unchanged, so the lower margin has "
                       "not been offset by additional business.")

    # 5. what it is worth
    if m.historical_margin_gap is not None:
        annual = ""
        if m.annualized_historical_margin_gap is not None:
            annual = (f", roughly {_rupees(m.annualized_historical_margin_gap)} "
                      f"annualized")
        out.append(
            f"At the historical margin, recent revenue would have earned "
            f"{_rupees(m.historical_margin_gap)} more{annual}. This is an "
            f"estimated gap, not recoverable profit.")

    if m.data_sufficiency is EvidenceSufficiency.PARTIAL:
        out.append(f"Read this with care — {'; '.join(m.sufficiency_reasons)}.")
    return out
