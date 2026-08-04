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
from dataclasses import asdict, dataclass, fields
from typing import Optional


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _default(name: str):
    return next(f.default for f in fields(CommercialThresholds) if f.name == name)


def _edges() -> tuple[int, ...]:
    """``CI_QUANTITY_BAND_EDGES=1,10,50,200`` — ascending, positive, deduped."""
    raw = os.environ.get("CI_QUANTITY_BAND_EDGES")
    if not raw:
        return _default("quantity_band_edges")
    edges = sorted({int(p) for p in raw.split(",") if p.strip()})
    return tuple(e for e in edges if e > 0) or _default("quantity_band_edges")


def _families() -> tuple[tuple[str, float], ...]:
    """``CI_TARGET_MARGIN_BY_FAMILY=milling_insert:0.30,reamer:0.27``."""
    raw = os.environ.get("CI_TARGET_MARGIN_BY_FAMILY")
    if not raw:
        return _default("target_margin_by_family")
    out = []
    for part in raw.split(","):
        if ":" in part:
            name, _, value = part.partition(":")
            out.append((name.strip(), float(value)))
    return tuple(sorted(out)) or _default("target_margin_by_family")


@dataclass(frozen=True)
class CommercialThresholds:
    # ── currency ─────────────────────────────────────────────────────────────
    # The unit every money threshold below is denominated in, and the unit the
    # numbers computed against them are reported in. It sits inside the version
    # hash on purpose: a 10,000 floor in rupees and a 10,000 floor in dollars
    # are different policies, and without this they would stamp the same
    # version onto rows that are not comparable. ``policy.load_for_org``
    # replaces this with the organization's own currency.
    currency: str = "INR"

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
    min_material_gap: float = 10_000.0

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

    # ── pricing policy (the authority; ``app.pricing`` reads it from here) ────
    # These used to live as module constants in ``app/pricing.py``, where the
    # Quote Builder read one set of numbers and the commercial analysis another.
    # One definition, one version hash, one place to change them.
    target_margin_default: float = 0.24
    # Tuple-of-pairs rather than a dict so the dataclass stays frozen, hashable
    # and JSON-stable for the version hash.
    target_margin_by_family: tuple[tuple[str, float], ...] = (
        ("solid_carbide_drill", 0.28),
        ("solid_carbide_endmill", 0.28),
        ("milling_insert", 0.30),
        ("drill_tip", 0.30),
        ("reamer", 0.27),
    )
    min_margin: float = 0.12            # hard floor — below this needs approval
    margin_floor: float = 0.15          # soft floor — below this is flagged
    sales_discretion_band: float = 0.03  # ±band off recommended without approval
    # A recommended price is rounded to a multiple of this before it is shown,
    # because quoting ₹1,847.31 invites a conversation about the 31 paise. The
    # increment is currency-scaled, not universal: ₹5 is a sensible tick on a
    # ₹2,000 insert and $5 is a 5% distortion on a $100 one. 0 disables
    # rounding. Denominated in ``currency`` above.
    price_rounding_increment: float = 5.0

    # ── quote-time quantity bands ────────────────────────────────────────────
    # Upper edges, inclusive. (1, 10, 50, 200) gives 1 / 2–10 / 11–50 / 51–200 /
    # 201+. Quantity is part of the identity of a price: the same item at 5
    # pieces and at 500 is not the same commercial question, and comparing a
    # quote against an all-quantities average silently mixes the two.
    quantity_band_edges: tuple[int, ...] = (1, 10, 50, 200)
    # A band reference drawn from a single past line is a coincidence.
    min_band_transactions: int = 2

    # ── quote exceptions ─────────────────────────────────────────────────────
    # A gap smaller than this is inside the noise of freight and rounding; a
    # quote screen that flags every ₹40 becomes a screen nobody reads.
    min_quote_exception_impact: float = 500.0
    # How far below a reference price counts as materially below.
    quote_price_tolerance_pct: float = 0.02

    @classmethod
    def from_env(cls) -> "CommercialThresholds":
        return cls(
            currency=os.environ.get("DEFAULT_CURRENCY", "INR").strip().upper() or "INR",
            recent_days=_i("CI_RECENT_DAYS", 90),
            previous_days=_i("CI_PREVIOUS_DAYS", 90),
            historical_lookback_days=_i("CI_HISTORICAL_LOOKBACK_DAYS", 730),
            min_margin_deterioration_pp=_f("CI_MIN_MARGIN_DETERIORATION_PP", 0.03),
            meaningful_cost_increase_pct=_f("CI_MEANINGFUL_COST_INCREASE_PCT", 0.05),
            meaningful_price_change_pct=_f("CI_MEANINGFUL_PRICE_CHANGE_PCT", 0.02),
            meaningful_volume_change_pct=_f("CI_MEANINGFUL_VOLUME_CHANGE_PCT", 0.15),
            min_material_gap=_f("CI_MIN_MATERIAL_GAP", 10_000.0),
            min_transactions=_i("CI_MIN_TRANSACTIONS", 3),
            min_transactions_strong=_i("CI_MIN_TRANSACTIONS_STRONG", 6),
            min_history_months=_f("CI_MIN_HISTORY_MONTHS", 3.0),
            min_history_months_strong=_f("CI_MIN_HISTORY_MONTHS_STRONG", 6.0),
            min_peer_customers=_i("CI_MIN_PEER_CUSTOMERS", 3),
            min_cost_coverage=_f("CI_MIN_COST_COVERAGE", 0.6),
            annualize_min_history_months=_f("CI_ANNUALIZE_MIN_HISTORY_MONTHS", 6.0),
            annualize_min_transactions=_i("CI_ANNUALIZE_MIN_TRANSACTIONS", 4),
            peer_recency_days=_i("CI_PEER_RECENCY_DAYS", 365),
            target_margin_default=_f("CI_TARGET_MARGIN_DEFAULT", 0.24),
            target_margin_by_family=_families(),
            min_margin=_f("CI_MIN_MARGIN", 0.12),
            margin_floor=_f("CI_MARGIN_FLOOR", 0.15),
            sales_discretion_band=_f("CI_SALES_DISCRETION_BAND", 0.03),
            price_rounding_increment=_f("CI_PRICE_ROUNDING_INCREMENT", 5.0),
            quantity_band_edges=_edges(),
            min_band_transactions=_i("CI_MIN_BAND_TRANSACTIONS", 2),
            min_quote_exception_impact=_f("CI_MIN_QUOTE_EXCEPTION_IMPACT", 500.0),
            quote_price_tolerance_pct=_f("CI_QUOTE_PRICE_TOLERANCE_PCT", 0.02),
        )

    # ── pricing-policy lookups ───────────────────────────────────────────────
    def target_margin(self, family: Optional[str]) -> float:
        """The target margin for a tool family, or the default."""
        if family:
            for name, value in self.target_margin_by_family:
                if name == family:
                    return value
        return self.target_margin_default

    def money(self, amount, unknown: str = "unknown") -> str:
        """An amount spelled in this policy's currency — ``₹4,00,000``.

        Lives here for the same reason ``target_margin`` does: the currency is
        part of the policy, so the thing that knows the policy is the thing
        that can spell an amount without being told twice.
        """
        from .money import money as _fmt
        return _fmt(amount, self.currency, unknown=unknown)

    @property
    def version(self) -> str:
        """Stable short hash of the threshold values (reproducibility)."""
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return "ci_" + hashlib.sha256(blob).hexdigest()[:10]


def load_commercial_thresholds() -> CommercialThresholds:
    return CommercialThresholds.from_env()
