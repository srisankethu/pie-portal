"""One definition of a period, and of what "the same length, just before" means.

Every comparative view in this package rests on the same pair of windows, so
they are defined once here. Two screens that each cut their own months would
eventually disagree about what "last quarter" was, and the disagreement would
show up as two different revenue figures on one page — which destroys trust in
both faster than either being wrong on its own.

Calendar months, not rolling 30-day blocks. A distributor's customers order
against month-ends, so a rolling window slices order cycles in half and makes
cadence look erratic when it is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from ...signals.base import SaleRow
from .series import TradeRow


@dataclass(frozen=True)
class Period:
    """A closed calendar range, inclusive at both ends."""

    start: date
    end: date
    label: str

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(),
                "label": self.label}


@dataclass(frozen=True)
class Comparison:
    """A period and the equal-length one immediately before it."""

    current: Period
    previous: Period

    def to_dict(self) -> dict:
        return {"current": self.current.to_dict(), "previous": self.previous.to_dict()}


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_start(day: date) -> date:
    return date(day.year, day.month, 1)


def add_months(day: date, n: int) -> date:
    total = day.year * 12 + (day.month - 1) + n
    return date(total // 12, total % 12 + 1, 1)


def month_end(day: date) -> date:
    """The last day of ``day``'s month — the day before the next month starts."""
    return date.fromordinal(add_months(month_start(day), 1).toordinal() - 1)


def label_for(start: date, end: date) -> str:
    """``Mar 2026`` for a single month, ``Jan–Mar 2026`` for a span."""
    if start.year == end.year and start.month == end.month:
        return f"{_MONTHS[start.month - 1]} {start.year}"
    if start.year == end.year:
        return f"{_MONTHS[start.month - 1]}–{_MONTHS[end.month - 1]} {start.year}"
    return (f"{_MONTHS[start.month - 1]} {start.year}–"
            f"{_MONTHS[end.month - 1]} {end.year}")


def months_back(as_of: date, count: int) -> list[Period]:
    """The ``count`` whole months ending with the one containing ``as_of``.

    Oldest first, because every chart built on this reads left to right.
    """
    out: list[Period] = []
    for i in range(count - 1, -1, -1):
        start = add_months(month_start(as_of), -i)
        end = month_end(start)
        out.append(Period(start=start, end=end, label=label_for(start, end)))
    return out


def comparison(as_of: date, months: int = 3) -> Comparison:
    """The last ``months`` whole months, against the ``months`` before those.

    ``as_of``'s own month is included: a view that silently dropped the current
    month would show a business as having stopped trading for up to four weeks.
    """
    cur_start = add_months(month_start(as_of), -(months - 1))
    cur_end = month_end(as_of)
    prev_start = add_months(cur_start, -months)
    prev_end = month_end(add_months(cur_start, -1))
    return Comparison(
        current=Period(cur_start, cur_end, label_for(cur_start, cur_end)),
        previous=Period(prev_start, prev_end, label_for(prev_start, prev_end)))


def revenue_in(sales: Iterable[TradeRow], period: Period) -> float:
    """Revenue inside one window, from lines or from monthly totals.

    Typed on ``TradeRow`` rather than ``SaleRow`` because it reads exactly two
    fields and every period here is a whole calendar month — so a month's total
    lands in the same window its lines would. That is what lets the
    period-comparison screens read the fold instead of scanning the book."""
    return float(sum(s.line_revenue for s in sales if period.contains(s.date)))


def orders_in(sales: Iterable[SaleRow], period: Period) -> int:
    """Distinct source invoices, so a ten-line invoice counts once."""
    refs = {
        (s.source_ref or {}).get("record_id") or s.external_ref
        for s in sales if period.contains(s.date)
    }
    return len(refs)


def bucket_by_month(sales: Iterable[SaleRow], periods: list[Period]) -> list[float]:
    """Revenue per period, aligned to ``periods``. Missing months are 0.0, not
    absent: a gap in a time series must render as a gap, not close up."""
    totals = [0.0] * len(periods)
    for row in sales:
        for i, period in enumerate(periods):
            if period.contains(row.date):
                totals[i] += float(row.line_revenue)
                break
    return totals


def last_transaction(sales: Iterable[SaleRow]) -> Optional[date]:
    dates = [s.date for s in sales]
    return max(dates) if dates else None
