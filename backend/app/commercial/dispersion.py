"""Order statistics and robust trimming, over `Decimal`, in one place.

Commercial data contains outliers by nature — one distress deal, one legacy
contract, one unexplained purchase — so every summary this package builds is an
order statistic and every trim is a median absolute deviation. Neither a mean
nor a standard deviation appears anywhere near a price band, because both are
wrecked by exactly the observations the trim exists to remove.

**Why this module exists rather than a fourth local helper.** A median absolute
deviation was already here, privately, in ``insight/payments._spread``, with the
same argument written out in its docstring; it delegates to ``mad`` below now.
Nearest-rank percentiles were already here twice — ``insight/payments.percentile``
over whole days and ``observability/metrics._percentile`` over latency floats —
and neither can serve money: ``benchmark.median_decimal`` exists precisely
because ``statistics.median`` averages the middle pair in float and puts binary
noise into a rupee value.

So the split is by *type*, not by caller: days and latencies keep their own
integer and float helpers, and everything denominated in money comes here.

**One quantile convention, so two medians can never disagree.** ``quantile``
averages the two straddling observations, which makes ``quantile(v, 0.5)``
identical to ``median_decimal(v)`` — it delegates to it, so there is no second
rounding behaviour for the same kind of number. A nearest-rank median would have
picked the upper of the middle pair and quietly disagreed with every other
median on the screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Optional, Sequence

from .benchmark import median_decimal

_ZERO = Decimal("0")
_HALF = Decimal("0.5")
_TWO = Decimal("2")


def quantile(values: Sequence[Decimal], q: Decimal) -> Optional[Decimal]:
    """The ``q`` quantile of ``values``, exact in ``Decimal``. ``None`` if empty.

    Averages the two straddling observations rather than interpolating across
    them, which keeps the arithmetic exact — averaging two ``Decimal`` values is
    exact, weighting them by a fraction of a rank is not — and makes the median
    case agree with ``median_decimal`` by construction.

    ``q`` is a ``Decimal`` so that 0.25 and 0.75 are the values they look like.
    Passing a float would reintroduce the binary noise this module avoids.
    """
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    if n == 1:
        return ordered[0]
    if q == _HALF:
        return median_decimal(ordered)
    # R-2: the two ranks straddling n·q + ½, clamped, averaged.
    h = Decimal(n) * q + _HALF
    lo = int((h - _HALF).to_integral_value(rounding=ROUND_CEILING))
    hi = int((h + _HALF).to_integral_value(rounding=ROUND_FLOOR))
    lo = min(max(lo, 1), n)
    hi = min(max(hi, 1), n)
    if lo == hi:
        return ordered[lo - 1]
    return (ordered[lo - 1] + ordered[hi - 1]) / _TWO


def mad(values: Sequence[Decimal]) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """``(median, median absolute deviation)``. Both ``None`` for fewer than two.

    The MAD rather than a standard deviation: one invoice settled nine months
    late, or one insert sold at a tenth of its price, is a story about that row.
    A standard deviation lets it redefine the distribution; the MAD does not.
    """
    ordered = sorted(values)
    if len(ordered) < 2:
        return (ordered[0] if ordered else None), None
    mid = median_decimal(ordered)
    return mid, median_decimal([abs(v - mid) for v in ordered])


#: Which side of the median an excluded observation fell.
LOW = "LOW"
HIGH = "HIGH"


@dataclass(frozen=True)
class Deviation:
    """One observation the trim removed, with everything needed to justify it."""

    index: int            # position in the caller's original sequence
    value: Decimal
    direction: str        # LOW | HIGH
    #: How far past the limit the row sat, as a multiple of it. MADs under the
    #: normal rule; multiples of the relative band under the fallback. Reported
    #: so a reviewer can see whether a row was marginal or absurd — "3.1×" and
    #: "40×" are different findings.
    deviations: Decimal

    def to_dict(self) -> dict:
        return {"index": self.index, "value": float(self.value),
                "direction": self.direction,
                "deviations": float(round(self.deviations, 2))}


@dataclass(frozen=True)
class Trimmed:
    """What survived a symmetric MAD trim, and what did not.

    ``kept`` and ``excluded`` both index into the caller's original sequence, so
    a caller holding evidence rows can cite the rows it dropped by primary key
    (§I4) rather than by value.
    """

    median: Optional[Decimal]
    mad: Optional[Decimal]
    kept: tuple[int, ...]
    excluded: tuple[Deviation, ...]
    #: The MAD was zero, so the trim fell back to a relative band around the
    #: median — see ``trim``. Reported because it changes what the exclusions
    #: mean: a relative band judges distance from the going price rather than
    #: distance from the spread of prices.
    zero_spread_fallback: bool = False

    @property
    def excluded_low(self) -> int:
        return sum(1 for d in self.excluded if d.direction == LOW)

    @property
    def excluded_high(self) -> int:
        return sum(1 for d in self.excluded if d.direction == HIGH)

    @property
    def total(self) -> int:
        return len(self.kept) + len(self.excluded)

    @property
    def exclusion_rate(self) -> float:
        """Share of observations removed. 0.0 when there were none to remove."""
        return (len(self.excluded) / self.total) if self.total else 0.0


def trim(values: Sequence[Decimal], *, k: Decimal,
         relative_band: Optional[Decimal] = None) -> Trimmed:
    """Remove observations far from the median, **symmetrically**.

    Symmetry is the whole point and it is not a detail. Excluding the one-off
    low deal and keeping the one-off high one drifts every band upward and
    inflates every opportunity figure computed from it — which is precisely the
    failure that destroys trust in a pricing tool, because the tool's errors all
    point the same way and all flatter the person reading it.

    **A zero MAD is the common case, not an edge case, and it needs the
    fallback.** When more than half the observations are identical the MAD is
    zero — which is exactly what a customer who has paid the same price ten
    times looks like. ``|x − median| > 0`` would then throw away every row that
    is not precisely the median, and declining to judge at all would leave a
    ₹100 and a ₹9,000 sitting in a band whose every other member is ₹1,000, as
    its own low and high. Both answers are wrong and the second is worse,
    because the band it produces looks computed.

    So with a zero MAD the scale comes from the median itself: anything further
    than ``relative_band`` away, proportionally, is not the going price.
    ``zero_spread_fallback`` records that this is what happened, because the two
    rules answer subtly different questions and a reviewer should know which one
    removed a row. Without a ``relative_band`` the trim still declines to judge.
    """
    mid, spread = mad(values)
    if mid is None or spread is None:
        return Trimmed(median=mid, mad=spread,
                       kept=tuple(range(len(values))), excluded=())

    fallback = False
    if spread == _ZERO:
        if relative_band is None or mid <= _ZERO:
            return Trimmed(median=mid, mad=spread,
                           kept=tuple(range(len(values))), excluded=(),
                           zero_spread_fallback=True)
        fallback = True
        limit = mid * relative_band
    else:
        limit = k * spread

    kept: list[int] = []
    dropped: list[Deviation] = []
    for i, v in enumerate(values):
        delta = abs(v - mid)
        if delta > limit:
            dropped.append(Deviation(index=i, value=v,
                                     direction=(HIGH if v > mid else LOW),
                                     deviations=(delta / limit if fallback
                                                 else delta / spread)))
        else:
            kept.append(i)
    # Total order on the way out: by direction then by index, so two runs over
    # the same inputs produce the same bytes (§13).
    dropped.sort(key=lambda d: (d.direction, d.index))
    return Trimmed(median=mid, mad=spread, kept=tuple(kept),
                   excluded=tuple(dropped), zero_spread_fallback=fallback)
