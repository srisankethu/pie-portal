"""Q — the portfolio-health multiplier, in [0.85, 1.15].

Six components, and one of them is new against the original design. Trial
conversion (Q2(b)) enters at 15%, displacing five points from logo retention,
five from concentration and five from forecast accuracy. That is the answer to
the hold-up problem: proving effort is not paid for directly — paying for
proving funds a free option for the customer — but proving that *converts* pays,
and the conversion rate sits at portfolio level where the salesperson can
influence it without being punished for a single switch they did not control.

A trial with no pre-commitment document counts in the DENOMINATOR and can never
count in the numerator. Undocumented proving is therefore costly without being
banned, which is the right shape: banning it would stop the proving that is the
actual differentiator.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

from .config import Config
from .models import Trial

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class HealthResult:
    q: Decimal
    score: Decimal
    components: dict


def _clamp(v: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, v))


def logo_retention(active_prior: int, still_active: int) -> Decimal:
    if active_prior <= 0:
        return _ONE          # nothing to retain is not a failure to retain
    return _clamp(Decimal(still_active) / Decimal(active_prior))


def contact_depth(cfg: Config, avg_contacts: Decimal) -> Decimal:
    return _clamp(avg_contacts / Decimal(cfg.int_("health", "contacts_full")))


def concentration(cfg: Config, shares: Sequence[Decimal]) -> Decimal:
    """Herfindahl on the salesperson's own book, penalised above the threshold.

    Scored so that a diversified book reaches 1.0 and a book with one account
    over the threshold falls away smoothly. A cliff at exactly 40% would create
    a boundary worth managing to rather than a gradient worth moving along.
    """
    if not shares:
        return _ONE
    biggest = max(shares)
    limit = cfg.dec("health", "concentration_penalty_above")
    if biggest <= limit:
        return _ONE
    # Linear from 1.0 at the threshold to 0.0 at total dependence.
    return _clamp((_ONE - biggest) / (_ONE - limit))


def dispute_rate(cfg: Config, credit_notes: Decimal, caf_total: Decimal) -> Decimal:
    if caf_total <= _ZERO:
        return _ONE
    ratio = credit_notes / caf_total
    zero_at = cfg.dec("health", "dispute_rate_zero_at")
    return _clamp(_ONE - (ratio / zero_at))


def forecast_accuracy(committed: Decimal, delivered: Decimal) -> Decimal:
    """Anti-sandbagging. Over-delivery is not rewarded, only accuracy."""
    if committed <= _ZERO:
        return _ONE
    error = abs(delivered - committed) / committed
    return _clamp(_ONE - error)


def trial_conversion(cfg: Config, trials: Iterable[Trial],
                     as_of: date) -> tuple[Decimal, dict]:
    """Q2(b). Proven items that produced a repeat order within N months.

    Only trials old enough to have had their chance are counted — including a
    trial run last week in the denominator would punish a salesperson for the
    calendar.
    """
    months = cfg.int_("health", "trial_conversion_months")
    need_locks = cfg.int_("health", "trial_lockin_factors_for_full_credit")
    unlocked_credit = cfg.dec("health", "trial_unlocked_credit")

    eligible = [t for t in trials
                if _months_between(t.trial_date, as_of) >= months]
    if not eligible:
        return _ONE, {"eligible_trials": 0}

    numerator = _ZERO
    protected_conversions = 0
    for t in eligible:
        if not t.converted or t.first_repeat_order_date is None:
            continue
        if _months_between(t.trial_date, t.first_repeat_order_date) > months:
            continue
        if not t.protected:
            # Q2(a): no pre-commitment document, no numerator credit. The trial
            # still sits in the denominator, so unprotected proving costs.
            continue
        protected_conversions += 1
        # Q2(c): locks make the conversion durable. A converted-but-unlocked
        # trial is a part-win — the order came, and it can leave just as easily.
        numerator += (_ONE if len(t.lock_in_factors) >= need_locks
                      else unlocked_credit)

    return _clamp(numerator / Decimal(len(eligible))), {
        "eligible_trials": len(eligible),
        "protected_conversions": protected_conversions,
        "weighted_numerator": str(numerator),
    }


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def compute(cfg: Config, components: dict) -> HealthResult:
    """Blend the six components into Q. Missing components score 1.0.

    A component nobody has data for must not drag Q down — that would make the
    multiplier a measure of instrumentation rather than of portfolio health.
    """
    weights = cfg.get("health", "weights")
    total_weight = sum(Decimal(v) for v in weights.values())
    score = _ZERO
    used = {}
    for name, weight in weights.items():
        value = _clamp(components.get(name, _ONE))
        used[name] = str(value.quantize(Decimal("0.001")))
        score += value * Decimal(weight)
    score = score / total_weight

    q_min, q_max = cfg.dec("health", "q_min"), cfg.dec("health", "q_max")
    return HealthResult(
        q=(q_min + (q_max - q_min) * score).quantize(Decimal("0.0001")),
        score=score.quantize(Decimal("0.0001")),
        components=used,
    )
