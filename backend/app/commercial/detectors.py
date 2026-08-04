"""Deterministic Customer × Item detectors.

Six rules over computed metrics. Pure: same metrics in, same signals out, no
database, no AI. They emit ordinary ``SignalDraft``s so Customer × Item findings
flow through the existing Signal → Decision pipeline rather than becoming
UI-only warnings that nobody can action or audit.

Two design rules carried from the existing detectors:

**Nothing is asserted on insufficient evidence.** A relationship whose data
sufficiency is INSUFFICIENT produces no signals at all — one historical
transaction must never become a confident erosion alert.

**Severity is economic, not percentage.** A 3 pp slip on ₹40 lakh outranks a
10 pp collapse on ₹20,000, because the first is worth someone's afternoon. Rank
by rupees; the percentage is context, not the headline.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..domain.enums import EvidenceSufficiency, SignalType, SubjectEntityType
from ..signals.base import SignalDraft, Sufficiency, clamp_severity
from .benchmark import ItemBenchmark
from .config import CommercialThresholds
from .metrics import COST_DRIVEN, MIXED, RelationshipMetrics
from .subject import encode

_ZERO = Decimal("0")

# Severity is driven by rupee impact. This is the gap at which a finding is
# treated as fully severe; below it severity scales down proportionally. Set
# well above the materiality floor so the scale has usable range.
_SEVERITY_FULL_SCALE_RUPEES = Decimal("500000")


def _impact_severity(gap: Optional[Decimal], floor: int = 10) -> int:
    """Map a rupee gap onto 0–100. Economic materiality, not percentage points."""
    if gap is None or gap <= _ZERO:
        return floor
    ratio = float(gap / _SEVERITY_FULL_SCALE_RUPEES)
    return clamp_severity(floor + min(1.0, ratio) * (100 - floor))


def _sufficiency(m: RelationshipMetrics) -> Sufficiency:
    return Sufficiency(
        history_months=m.history_months,
        txn_count=m.transaction_count,
        missing_fields=(["effective_unit_cost"] if m.cost_missing_txns else []),
        level=m.data_sufficiency,
        reasons=list(m.sufficiency_reasons),
    )


def _window(m: RelationshipMetrics, th: CommercialThresholds) -> dict:
    return {
        "recent_days": th.recent_days,
        "previous_days": th.previous_days,
        "historical_lookback_days": th.historical_lookback_days,
        "first_transaction": (m.first_transaction_date.isoformat()
                              if m.first_transaction_date else None),
        "last_transaction": (m.last_transaction_date.isoformat()
                             if m.last_transaction_date else None),
    }


def _base_metrics(m: RelationshipMetrics) -> dict:
    """The metrics every Customer × Item signal carries.

    Margins are emitted as ratios and *_pp movements as point differences, the
    same convention the whole layer uses, so the AI grounding gate sees exactly
    the numbers that were computed.
    """
    return {
        "current_margin_pct": _r(m.current_margin),
        "historical_margin_pct": _r(m.historical_margin),
        "margin_change_pp": _r(m.margin_change_pp),
        "revenue_recent": _money(m.revenue_recent),
        "transaction_count": m.transaction_count,
        "history_months": round(m.history_months, 1),
    }


def _r(value: Optional[float]) -> Optional[float]:
    return round(value, 4) if value is not None else None


def _money(value: Optional[Decimal]) -> Optional[float]:
    return float(round(value, 2)) if value is not None else None


def _draft(signal_type: SignalType, m: RelationshipMetrics, th: CommercialThresholds,
           metrics: dict, severity: int, evidence: list[dict]) -> SignalDraft:
    return SignalDraft(
        signal_type=signal_type.value,
        subject_entity_type=SubjectEntityType.CUSTOMER_ITEM.value,
        subject_entity_id=encode(m.customer_id, m.product_id),
        window=_window(m, th),
        metrics={**_base_metrics(m), **metrics},
        severity_base=severity,
        evidence_refs=evidence,
        sufficiency=_sufficiency(m),
        detector_version="",              # stamped by the engine
        threshold_config_version=th.version,
    )


def detect(m: RelationshipMetrics, benchmark: Optional[ItemBenchmark],
           th: CommercialThresholds, evidence: list[dict]) -> list[SignalDraft]:
    """Every Customer × Item signal this relationship warrants.

    ``evidence`` is the source refs for the lines the metrics were computed
    from, so each signal traces back to the invoices behind it.
    """
    # Weak data produces nothing. This is the guard that stops one historical
    # transaction from becoming a confident alert.
    if m.data_sufficiency is EvidenceSufficiency.INSUFFICIENT:
        return []

    drafts: list[SignalDraft] = []
    eroding = (m.margin_change_pp is not None
               and m.margin_change_pp <= -th.min_margin_deterioration_pp)

    # ── 1. margin erosion vs this relationship's own baseline ───────────────
    if eroding:
        drafts.append(_draft(
            SignalType.CI_MARGIN_EROSION, m, th,
            {"previous_margin_pct": _r(m.previous_margin),
             "margin_3m_pct": _r(m.margin_3m),
             "margin_12m_pct": _r(m.margin_12m),
             "historical_margin_gap": _money(m.historical_margin_gap),
             "erosion_kind": m.erosion_kind},
            _impact_severity(m.historical_margin_gap), evidence))

    # ── 2. cost rose, price did not follow ──────────────────────────────────
    if m.erosion_kind in (COST_DRIVEN, MIXED):
        drafts.append(_draft(
            SignalType.CI_COST_NOT_PASSED, m, th,
            {"cost_change_pct": _r(m.cost_change_pct),
             "price_change_pct": _r(m.price_change_pct),
             "current_unit_cost": _money(m.current_effective_cost),
             "current_sell_price": _money(m.current_sell_price),
             "erosion_kind": m.erosion_kind},
            _impact_severity(m.historical_margin_gap, floor=20), evidence))

    # ── 3. materially below the same-item peer benchmark ────────────────────
    # Requires a real peer population; two customers buying an item is an
    # anecdote, and a benchmark drawn from one is not evidence.
    if (benchmark is not None and benchmark.is_reliable(th)
            and benchmark.margin_deviation_pp is not None
            and benchmark.margin_deviation_pp <= -th.min_margin_deterioration_pp):
        peer_gap = (m.revenue_recent * Decimal(str(-benchmark.margin_deviation_pp))
                    if m.revenue_recent > _ZERO else None)
        drafts.append(_draft(
            SignalType.CI_LOW_PEER_PRICING, m, th,
            {"peer_median_margin_pct": _r(benchmark.median_margin),
             "peer_median_price": _money(benchmark.median_price),
             "margin_deviation_pp": _r(benchmark.margin_deviation_pp),
             "price_deviation_pct": _r(benchmark.price_deviation_pct),
             "peer_count": benchmark.peer_count,
             "peer_margin_gap": _money(peer_gap)},
            _impact_severity(peer_gap, floor=15), evidence))

    # ── 4/5. margin decline, with or without compensating volume ────────────
    # The same margin move means opposite things depending on volume: a
    # deliberate, working trade-off versus pure leakage. Classified separately
    # so the second never hides inside the first.
    if eroding and m.volume_change_pct is not None:
        grew = m.volume_change_pct >= th.meaningful_volume_change_pct
        if grew:
            drafts.append(_draft(
                SignalType.CI_MARGIN_DECLINE_WITH_VOLUME, m, th,
                {"volume_change_pct": _r(m.volume_change_pct),
                 "qty_recent": _money(m.qty_recent),
                 "qty_previous": _money(m.qty_previous),
                 "gross_profit_recent": _money(m.gross_profit_recent)},
                # Deliberately damped: this may be a successful trade, so it is
                # surfaced for review rather than ranked alongside leakage.
                clamp_severity(_impact_severity(m.historical_margin_gap) * 0.5),
                evidence))
        else:
            drafts.append(_draft(
                SignalType.CI_MARGIN_DECLINE_NO_VOLUME, m, th,
                {"volume_change_pct": _r(m.volume_change_pct),
                 "qty_recent": _money(m.qty_recent),
                 "qty_previous": _money(m.qty_previous),
                 "historical_margin_gap": _money(m.historical_margin_gap)},
                _impact_severity(m.historical_margin_gap, floor=25), evidence))

    # ── 6. the gap is materially large in rupees ────────────────────────────
    if (m.historical_margin_gap is not None
            and float(m.historical_margin_gap) >= th.min_material_gap):
        drafts.append(_draft(
            SignalType.CI_MATERIAL_MARGIN_GAP, m, th,
            {"historical_margin_gap": _money(m.historical_margin_gap),
             "annualized_historical_margin_gap":
                 _money(m.annualized_historical_margin_gap),
             "revenue_12m": _money(m.revenue_12m)},
            _impact_severity(m.historical_margin_gap, floor=30), evidence))

    return drafts
