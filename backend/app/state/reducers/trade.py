"""CUSTOMER_MONTH and CUSTOMER_ITEM_MONTH — trade, bucketed by the month it
happened in.

Two states rather than one, at two grains. ``CUSTOMER_MONTH`` is keyed
``customer_id:YYYY-MM`` and is read by the screens that compare two periods;
``CUSTOMER_ITEM_MONTH`` is keyed ``customer_id:product_id:YYYY-MM`` and is read
only by the revenue-composition screen's product dimension. Folding them
together would be one state, and it would make every reader of the small one
pay for the large one's cardinality — this book has roughly as many
customer-item pairs as it has items.

## Why there is no ``build_series()``

The architecture note that specified this work called for a new engine
function: a second build call that walks the log once and buckets by month,
because "every existing Delta targets one key or accumulator" and a
month-bucketed reducer "needs a composite key ... which engine.py already
supports structurally, but no reducer has exercised that path yet".

That second sentence turned out to be the whole answer. ``build()`` already
folds every live event in ``(occurred_on, seq)`` order into ``(state, key)``
accumulators, in one pass, with no opinion whatever about what a key means —
and the SUPPLIER reducer has since exercised the composite-key path for real.
Putting the month in the key gets the incremental monthly fold with no new
engine capability at all: one pass over the log regardless of how many months
are asked for, which was the actual requirement. A ``build_series()`` beside
``build()`` would have been a second way to do one thing, and the first
divergence between them would have been a bug nobody could see.

## What is folded, and what is not

**Revenue is the sale line's own figure**, matching what the existing screens
compute from ``SalesTxn`` exactly — including invoices of every status, because
that is what they count today and the point of this state is to produce the
same numbers from a cheaper place. A quiet definition change here would show up
as a screen that disagrees with itself across a release.

**Orders are counted from the invoice header event, not from lines.** A fold
has no way to count distinct documents — ``ADD`` over sale lines counts lines —
and ``RECEIVABLE_RECORDED`` is emitted exactly once per invoice. Counting
lines and calling them orders would report a customer who buys forty items at
once as a customer who ordered forty times.

**No gross profit, and therefore no margin.** Margin at this grain needs the
cost basis *as of each sale*, which is a lookup backwards through cost history,
not an accumulation — a fold is the wrong shape for it, and the version that
used the latest cost instead would silently re-price two years of history every
time a supplier raised a rate. The customer-journey screen keeps computing
margin the way it does today. This is a real limit on what these states can
replace, and it is stated here rather than discovered later.

**No cadence.** The gap between orders needs order dates, and a monthly bucket
structurally cannot hold that a month's four orders landed on the 3rd, 9th,
21st and 29th. That screen reads the event log directly or not at all.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

from ...domain import models
from .. import events as ev
from ..engine import ADD, MAX, MIN, SET, Delta, Masters, as_decimal, register

CUSTOMER_MONTH = "CUSTOMER_MONTH"
CUSTOMER_ITEM_MONTH = "CUSTOMER_ITEM_MONTH"


def month_of(day: date) -> str:
    """The bucket a date falls in. ``YYYY-MM``, so keys sort chronologically as
    strings and a reader can see the month without parsing anything."""
    return f"{day.year:04d}-{day.month:02d}"


def _sale(event: models.BusinessEvent, ctx: Masters,
          ) -> tuple[Optional[str], Optional[str], dict[str, Any]]:
    """Resolve a sale line to (customer, product, payload)."""
    payload = event.payload or {}
    customer_id = ctx.customer(event, str(payload.get("customer_external_id") or ""))
    product_id = ctx.product(event, str(payload.get("product_external_id") or ""))
    return customer_id, product_id, payload


class CustomerMonthReducer:
    """One customer's trade in one month."""

    state = CUSTOMER_MONTH
    handles = frozenset({ev.SALE_LINE_RECORDED, ev.RECEIVABLE_RECORDED})
    # No per-event working. Transitions exist for the decision drill-down, and
    # no detector reads this state — it feeds screens. See the engine's Reducer
    # protocol; measured, this was most of what the fold cost.
    records_transitions = False

    def apply(self, event: models.BusinessEvent, ctx: Masters,
              as_of: date) -> Iterable[Delta]:
        bucket = month_of(event.occurred_on)
        if event.event_type == ev.RECEIVABLE_RECORDED:
            return self._invoice(event, ctx, bucket)
        return self._line(event, ctx, bucket)

    def _line(self, event: models.BusinessEvent, ctx: Masters,
              bucket: str) -> Iterable[Delta]:
        customer_id, _, payload = _sale(event, ctx)
        if customer_id is None:
            return ()
        changes: list[tuple[str, str, Any]] = [
            # In the value as well as the key, so a reader groups on fields
            # rather than splitting a string it did not construct.
            (SET, "customer_id", customer_id),
            (SET, "month", bucket),
            (ADD, "lines", 1),
            (MIN, "first_sold_on", event.occurred_on),
            (MAX, "last_sold_on", event.occurred_on),
        ]
        revenue = as_decimal(payload.get("line_revenue"))
        qty = as_decimal(payload.get("qty"))
        if revenue is not None:
            changes.append((ADD, "revenue", revenue))
        if qty is not None:
            changes.append((ADD, "units", qty))
        return (Delta(CUSTOMER_MONTH, f"{customer_id}:{bucket}", tuple(changes)),)

    def _invoice(self, event: models.BusinessEvent, ctx: Masters,
                 bucket: str) -> Iterable[Delta]:
        """One per invoice, which is what makes ``orders`` a document count."""
        payload = event.payload or {}
        customer_id = ctx.customer(event, str(payload.get("customer_external_id") or ""))
        if customer_id is None:
            return ()
        return (Delta(CUSTOMER_MONTH, f"{customer_id}:{bucket}", (
            (SET, "customer_id", customer_id),
            (SET, "month", bucket),
            (ADD, "orders", 1),
        )),)


class CustomerItemMonthReducer:
    """One customer's trade in one item in one month.

    The same arithmetic as above at a finer grain, and deliberately not an
    abstraction over it. Two concrete reducers reading clearly beat a generic
    "time-bucketed reducer" built for a third case that does not exist —
    CLAUDE.md §7 on inventing a variation point before there is a second caller.

    No order count here: an invoice is a document about a customer, not about a
    customer and one line of it, and counting it once per item would multiply
    the same order by however many things were on it.
    """

    state = CUSTOMER_ITEM_MONTH
    handles = frozenset({ev.SALE_LINE_RECORDED})
    records_transitions = False

    def apply(self, event: models.BusinessEvent, ctx: Masters,
              as_of: date) -> Iterable[Delta]:
        bucket = month_of(event.occurred_on)
        customer_id, product_id, payload = _sale(event, ctx)
        if customer_id is None or product_id is None:
            return ()
        changes: list[tuple[str, str, Any]] = [
            (SET, "customer_id", customer_id),
            (SET, "product_id", product_id),
            (SET, "month", bucket),
            (ADD, "lines", 1),
            (MAX, "last_sold_on", event.occurred_on),
        ]
        revenue = as_decimal(payload.get("line_revenue"))
        qty = as_decimal(payload.get("qty"))
        if revenue is not None:
            changes.append((ADD, "revenue", revenue))
        if qty is not None:
            changes.append((ADD, "units", qty))
        return (Delta(CUSTOMER_ITEM_MONTH,
                      f"{customer_id}:{product_id}:{bucket}", tuple(changes)),)


register(CustomerMonthReducer())
register(CustomerItemMonthReducer())
