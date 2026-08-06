"""Reading the monthly fold instead of the lines that made it.

``load_snapshot`` reads every sale line the organization has ever recorded,
with no date bound, and four call sites hit it per page load. That is what the
Business State evolution set out to remove, and the comment above the loader
says as much: *"the right answer for the screens that need whole-book
aggregates is to read the folded state rows instead of the lines."* This is
that reader.

**Why this is safe, in one sentence.** Every window in ``periods`` is a whole
calendar month — the module says so and explains why (a distributor's customers
order against month-ends, so a rolling window slices order cycles in half). A
month is therefore entirely inside a period or entirely outside it, and the
month's total answers the same question the month's lines do. If any screen
ever asks for a rolling 30-day window, this reader stops being correct and the
equality test in ``test_series_equality.py`` is what will say so.

**What a monthly row is not.** It is deliberately *not* a ``SaleRow`` with the
missing fields filled in. A monthly bucket has no product, no quantity and no
invoice reference — inventing a blank product id and a zero quantity would
produce an object that type-checks everywhere and is wrong wherever those
fields are read. ``MonthRow`` carries the three facts it actually has, and the
functions that consume it are typed against ``TradeRow``, which is exactly
those three. A screen needing a fourth cannot accidentally be handed one of
these.

**Not every screen can move.** ``cohorts`` and ``composition`` read
``product_id``, ``qty`` and the invoice reference, which a customer-month
bucket structurally does not hold. They keep reading lines. Moving them would
need the item-grain state and, for margin, a cost basis a fold cannot compute —
see ``state/reducers/trade.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Protocol


class TradeRow(Protocol):
    """What the period-comparison screens actually read off a sale.

    Narrow on purpose. ``SaleRow`` satisfies this structurally and so does
    ``MonthRow``, which is the whole point: the functions typed against it work
    on lines or on monthly totals without knowing which they were given, and a
    function that grows a fourth requirement stops accepting monthly rows at
    the type level rather than silently reading a zero.
    """

    customer_id: str
    date: date
    line_revenue: Decimal


@dataclass(frozen=True)
class MonthRow:
    """One customer's trade in one month, read from ``CUSTOMER_MONTH``.

    ``date`` is the month's *last* trading day rather than its first or its
    midpoint. Every period boundary is a month boundary, so any day inside the
    month places the row in the same window — but the last trading day is also
    the true answer to "when did they last buy", so nothing downstream has to
    special-case a synthetic date.
    """

    customer_id: str
    date: date
    line_revenue: Decimal
    month: str
    orders: int = 0
    units: Decimal = Decimal(0)


def month_rows(state: dict[str, dict[str, Any]]) -> list[MonthRow]:
    """Build monthly rows from a loaded ``CUSTOMER_MONTH`` fold.

    Takes the plain dict the engine returns rather than a session, so this
    module stays a pure function of its input and ``commercial/`` keeps holding
    no database handle — the same arrangement ``stock.lines_from_state`` uses.

    Rows are returned oldest first. Nothing downstream depends on the order,
    but a stable one makes a failing equality test readable.
    """
    rows: list[MonthRow] = []
    for value in state.values():
        customer_id = value.get("customer_id")
        last_sold = value.get("last_sold_on")
        if not customer_id or not last_sold:
            # A row with no customer or no trading day is not a month anybody
            # traded in. Skipped rather than dated to the 1st, which would put
            # revenue in a month that has none.
            continue
        rows.append(MonthRow(
            customer_id=str(customer_id),
            date=date.fromisoformat(str(last_sold)),
            line_revenue=Decimal(str(value.get("revenue") or 0)),
            month=str(value.get("month") or ""),
            orders=int(value.get("orders") or 0),
            units=Decimal(str(value.get("units") or 0)),
        ))
    rows.sort(key=lambda r: (r.date, r.customer_id))
    return rows


def last_traded_on(rows: Iterable[TradeRow]) -> date | None:
    """The last day anybody traded, from monthly rows or from lines.

    The screens hang their periods off this. Reading it from the fold gives the
    same answer as reading it from the lines because a month's row is dated on
    its own last trading day.
    """
    days = [r.date for r in rows]
    return max(days) if days else None
