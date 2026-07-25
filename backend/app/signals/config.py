"""Typed, configurable thresholds for the Signal Engine.

Simple typed configuration — not a rules engine. Every threshold a detector uses
lives here (no scattered magic numbers), with sane B2B-distributor defaults and
per-field environment overrides. ``version`` is stamped onto every emitted signal
so a signal can be reproduced against the exact thresholds that produced it.
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
class SignalThresholds:
    # ── comparison windows (shared) ──────────────────────────────────────────
    basis_period_days: int = 90          # "recent" window length
    comparison_period_days: int = 90     # immediately-preceding "baseline" window

    # ── CUSTOMER_DECLINE ─────────────────────────────────────────────────────
    decline_drop_pct: float = 0.30       # recent revenue down > 30% vs baseline
    decline_min_prior_orders: int = 3    # activity floor (baseline window)
    decline_min_history_months: int = 6  # minimum history to judge a trend

    # ── CUSTOMER_DORMANCY ────────────────────────────────────────────────────
    dormancy_min_orders: int = 4         # need enough orders to establish cadence
    dormancy_interval_multiplier: float = 1.5   # overdue if gap > multiplier × typical

    # ── MARGIN_DETERIORATION (restricted) ────────────────────────────────────
    margin_drop_points: float = 0.05     # current margin down > 5 points vs baseline

    # ── COST_PASS_THROUGH (restricted) ───────────────────────────────────────
    cost_increase_pct: float = 0.05      # latest cost up > 5% vs prior cost basis
    cost_price_lag_points: float = 0.02  # price rose < (cost rise − 2 pts) ⇒ compressed

    # ── data-quality anomaly params (§19) ────────────────────────────────────
    placeholder_cost_max: float = 0.0    # cost ≤ this ⇒ zero/placeholder
    qty_spike_factor: float = 5.0        # line qty > factor × median ⇒ spike
    price_outlier_factor: float = 5.0    # unit price > factor × median ⇒ outlier

    @classmethod
    def from_env(cls) -> "SignalThresholds":
        return cls(
            basis_period_days=_i("SIG_BASIS_DAYS", 90),
            comparison_period_days=_i("SIG_COMPARISON_DAYS", 90),
            decline_drop_pct=_f("SIG_DECLINE_DROP_PCT", 0.30),
            decline_min_prior_orders=_i("SIG_DECLINE_MIN_ORDERS", 3),
            decline_min_history_months=_i("SIG_DECLINE_MIN_HISTORY_MONTHS", 6),
            dormancy_min_orders=_i("SIG_DORMANCY_MIN_ORDERS", 4),
            dormancy_interval_multiplier=_f("SIG_DORMANCY_MULT", 1.5),
            margin_drop_points=_f("SIG_MARGIN_DROP_POINTS", 0.05),
            cost_increase_pct=_f("SIG_COST_INCREASE_PCT", 0.05),
            cost_price_lag_points=_f("SIG_COST_PRICE_LAG_POINTS", 0.02),
            placeholder_cost_max=_f("SIG_PLACEHOLDER_COST_MAX", 0.0),
            qty_spike_factor=_f("SIG_QTY_SPIKE_FACTOR", 5.0),
            price_outlier_factor=_f("SIG_PRICE_OUTLIER_FACTOR", 5.0),
        )

    @property
    def version(self) -> str:
        """Stable short hash of the threshold values (reproducibility)."""
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return "th_" + hashlib.sha256(blob).hexdigest()[:10]


def load_thresholds() -> SignalThresholds:
    return SignalThresholds.from_env()
