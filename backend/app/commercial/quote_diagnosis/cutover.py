"""Where one connection's bulk load ends and its live history begins.

``ZohoConnection.history_loaded_before`` decides whether a row's creation stamp
is evidence of when the business knew something, or merely the moment somebody
imported a year of history. It is the single most consequential setting this
engine has: with it wrong in one direction the engine is silent, and wrong in
the other it builds bands out of a migration.

**This module reports; it never applies.** It reads the distribution of creation
stamps on a connection and says where the discontinuity is, with the counts
behind it. Nothing here writes the column, and the engine never falls back to a
detected value — a boundary inferred from row counts moves every time the counts
do, and a band that quietly changed shape after a sync would be unexplainable.

So the flow is: this suggests, a person confirms, the column decides. That is
also why the suggestion carries its own evidence rather than just a date. The
number to look at on the live books is stark enough to confirm at a glance —
1,632 of 2,419 bills on one of them were created in a single month, against
document dates spread across the preceding year.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

#: A month has to hold at least this share of a connection's creation stamps
#: before it looks like a load rather than a busy month. Deliberately high: the
#: cost of suggesting a boundary that is not there is a person excluding real
#: history, and the real signal on these books is nowhere near the line.
BULK_MONTH_SHARE = 0.35

#: And the load has to be describing history rather than the present — the
#: median document in the peak month has to be this many days older than the
#: month itself. A genuinely busy month of live entry has a lag of days.
BULK_MIN_MEDIAN_LAG_DAYS = 60


@dataclass(frozen=True)
class Observation:
    """One document's two dates, which is all this needs to see."""

    event_date: date
    recorded_at: datetime


@dataclass(frozen=True)
class CutoverEvidence:
    """What the creation stamps look like, and what that suggests.

    ``suggested`` is a proposal for ``history_loaded_before`` and nothing more.
    It is ``None`` whenever the shape does not clearly show a load, which is the
    honest answer for a book that was never migrated — and for one where the
    load is smeared across several months, where a person has to decide.
    """

    total: int
    by_month: tuple[tuple[str, int], ...]
    peak_month: Optional[str]
    peak_count: int
    peak_share: float
    peak_median_lag_days: Optional[int]
    suggested: Optional[date]
    reason: str

    def to_dict(self) -> dict:
        return {"total": self.total, "by_month": [list(m) for m in self.by_month],
                "peak_month": self.peak_month, "peak_count": self.peak_count,
                "peak_share": round(self.peak_share, 4),
                "peak_median_lag_days": self.peak_median_lag_days,
                "suggested": self.suggested.isoformat() if self.suggested else None,
                "reason": self.reason}


def detect(observations: Iterable[Observation]) -> CutoverEvidence:
    """Look for a bulk load in a connection's creation stamps.

    A migration has a shape that live entry does not: a large share of every
    document a book holds, created inside one month, describing events spread
    across the months *before* it. Either half alone is unremarkable — a busy
    month is just a busy month, and a few late entries are ordinary — so both
    are required.

    The suggestion is the first day of the month *after* the peak, because
    ``history_loaded_before`` is exclusive: everything recorded before it is the
    load, everything on or after it is live.
    """
    rows = [o for o in observations if o.recorded_at is not None]
    total = len(rows)
    if total == 0:
        return CutoverEvidence(
            total=0, by_month=(), peak_month=None, peak_count=0, peak_share=0.0,
            peak_median_lag_days=None, suggested=None,
            reason="no creation stamps on record — re-sync to populate them")

    months = Counter(o.recorded_at.strftime("%Y-%m") for o in rows)
    by_month = tuple(sorted(months.items()))
    peak_month, peak_count = max(sorted(months.items()),
                                 key=lambda kv: (kv[1], kv[0]))
    share = peak_count / total

    lags = sorted((o.recorded_at.date() - o.event_date).days
                  for o in rows if o.recorded_at.strftime("%Y-%m") == peak_month)
    median_lag = _median_int(lags)

    if share < BULK_MONTH_SHARE:
        return CutoverEvidence(
            total=total, by_month=by_month, peak_month=peak_month,
            peak_count=peak_count, peak_share=share,
            peak_median_lag_days=median_lag, suggested=None,
            reason=(f"no month holds enough of this connection's history to "
                    f"look like a load ({share * 100:.0f}% is the largest, and "
                    f"{BULK_MONTH_SHARE * 100:.0f}% is the line)"))

    if median_lag is None or median_lag < BULK_MIN_MEDIAN_LAG_DAYS:
        return CutoverEvidence(
            total=total, by_month=by_month, peak_month=peak_month,
            peak_count=peak_count, peak_share=share,
            peak_median_lag_days=median_lag, suggested=None,
            reason=(f"{peak_month} holds {share * 100:.0f}% of the stamps, but "
                    f"its documents are only {median_lag} days old on median — "
                    f"that is a busy month, not a load"))

    return CutoverEvidence(
        total=total, by_month=by_month, peak_month=peak_month,
        peak_count=peak_count, peak_share=share,
        peak_median_lag_days=median_lag,
        suggested=_first_of_next_month(peak_month),
        reason=(f"{peak_count} of {total} documents ({share * 100:.0f}%) were "
                f"created in {peak_month}, describing events a median of "
                f"{median_lag} days older. That is a migration, not a month of "
                f"work — confirm before setting it."))


def _median_int(values: list[int]) -> Optional[int]:
    """Nearest-rank, on whole days.

    Not ``dispersion.quantile``: these are days, not money, and the convention
    here matches ``insight/payments.percentile`` — an interpolated 17.4 days is
    not an observation anybody made. The split is by type, which is the rule
    ``commercial/dispersion.py`` states.
    """
    if not values:
        return None
    return values[(len(values) - 1) // 2]


def _first_of_next_month(month: str) -> date:
    year, mon = (int(p) for p in month.split("-"))
    return date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
