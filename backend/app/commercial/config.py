"""Thresholds for Customer × Item commercial intelligence.

One dataclass, env overrides, a content-hashed ``version`` stamped onto every
metric row and every signal — the same pattern as ``signals/config.py``, for the
same reason: any number the platform reports must be reproducible against the
exact thresholds that produced it.

Nothing here is a magic number scattered through a detector or a component.
Changing a threshold is a config change, not an analytical rewrite.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class CommercialThresholds:
    # ── periods ──────────────────────────────────────────────────────────────
    # "Recent" is the window a current position is read from; "previous" is the
    # equal-length window immediately before it, so the two are comparable.
    recent_days: int = 90
    previous_days: int = 90
    # How far back a relationship's own "historical" baseline reaches. Longer
    # than the comparison window on purpose: the baseline should be what the
    # relationship normally earned, not merely last quarter.
    historical_lookback_days: int = 730

    # ── what counts as a real move ───────────────────────────────────────────
    # Percentage POINTS of margin. 3 pp is roughly where a distributor's margin
    # move stops being noise from mix and freight.
    min_margin_deterioration_pp: float = 0.03
    meaningful_cost_increase_pct: float = 0.05
    meaningful_price_change_pct: float = 0.02
    meaningful_volume_change_pct: float = 0.15

    # ── economic materiality ─────────────────────────────────────────────────
    # Rupees. A gap below this is real but not worth anyone's afternoon, and
    # prioritising by percentage instead of dsize is how teams end up working
    # trivial accounts first.
    min_material_gap_rupees: float = 10_000.0

    # ── evidence floors ──────────────────────────────────────────────────────
    min_transactions: int = 3          # below this: nothing is asserted
    min_transactions_strong: int = 6   # at/above this (with span): SUFFICIENT
    min_history_months: float = 3.0
    min_history_months_strong: float = 6.0
    # A peer benchmark drawn from one or two other customers is an anecdote.
    min_peer_customers: int = 3
    # Cost coverage: the share of a relationship's transactions that have an
    # applicable cost record. Below this, margin is not asserted at all.
    min_cost_coverage: float = 0.6

    # ── annualization ────────────────────────────────────────────────────────
    # A yearly figure extrapolated from six weeks of trading is a guess wearing
    # a suit. Require real span and real transaction count before annualizing.
    annualize_min_history_months: float = 6.0
    annualize_min_transactions: int = 4

    # ── peer benchmark staleness ─────────────────────────────────────────────
    # A peer whose last purchase predates this is not evidence about today.
    peer_recency_days: int = 365

    @classmethod
    def from_env(cls) -> "CommercialThresholds":
        return cls(
            recent_days=_i("CI_RECENT_DAYS", 90),
            previous_days=_i("CI_PREVIOUS_DAYS", 90),
            historical_lookback_days=_i("CI_HISTORICAL_LOOKBACK_DAYS", 730),
            min_margin_deterioration_pp=_f("CI_MIN_MARGIN_DETERIORATION_PP", 0.03),
            meaningful_cost_increase_pct=_f("CI_MEANINGFUL_COST_INCREASE_PCT", 0.05),
            meaningful_price_change_pct=_f("CI_MEANINGFUL_PRICE_CHANGE_PCT", 0.02),
            meaningful_volume_change_pct=_f("CI_MEANINGFUL_VOLUME_CHANGE_PCT", 0.15),
            min_material_gap_rupees=_f("CI_MIN_MATERIAL_GAP_RUPEES", 10_000.0),
            min_transactions=_i("CI_MIN_TRANSACTIONS", 3),
            min_transactions_strong=_i("CI_MIN_TRANSACTIONS_STRONG", 6),
            min_history_months=_f("CI_MIN_HISTORY_MONTHS", 3.0),
            min_history_months_strong=_f("CI_MIN_HISTORY_MONTHS_STRONG", 6.0),
            min_peer_customers=_i("CI_MIN_PEER_CUSTOMERS", 3),
            min_cost_coverage=_f("CI_MIN_COST_COVERAGE", 0.6),
            annualize_min_history_months=_f("CI_ANNUALIZE_MIN_HISTORY_MONTHS", 6.0),
            annualize_min_transactions=_i("CI_ANNUALIZE_MIN_TRANSACTIONS", 4),
            peer_recency_days=_i("CI_PEER_RECENCY_DAYS", 365),
        )

    @property
    def version(self) -> str:
        """Stable short hash of the threshold values (reproducibility)."""
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return "ci_" + hashlib.sha256(blob).hexdigest()[:10]


def load_commercial_thresholds() -> CommercialThresholds:
    return CommercialThresholds.from_env()
