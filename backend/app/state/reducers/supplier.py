"""SUPPLIER — what we have bought, from whom, and for which item.

Keyed ``vendor_id:product_id``. The first composite-key state in this codebase:
the engine has always supported it — nothing in ``engine.py`` inspects a key —
but no reducer had exercised the path.

The grain is the point. ``INVENTORY.spend`` is per product and cannot say who
sold it; ``COMMITMENTS`` holds open promises rather than historical spend. So
"what share of our purchasing is with one supplier" and "which items have only
ever come from one place" were both unanswerable, and ``opportunities/supply``
said so rather than approximating them. This is the state that answers them.

**Vendor and product are stored as fields, not only encoded in the key.** A
detector that had to split the key on a colon would break the first time an id
contained one, and would read as string surgery rather than as a group-by.

**Spend is the line's own arithmetic.** ``qty × unit_cost``, where
``unit_cost`` is the effective, post-discount figure every other cost consumer
already reads. Not the bill total, which includes tax and document-level
adjustments this platform has never counted as cost.

**Two spend figures, and the ranked one is the recent year.** ``spend`` is
everything ever bought from this supplier for this item; ``spend_recent`` is
the trailing year to ``as_of``. The distinction is not decoration — it decides
whether the queue is sane. Every other impact in this platform is a *stock*:
capital sitting on a shelf, a balance still owed, the value of an open order.
Lifetime spend is a *flow*, and ranking a flow against a stock means a supplier
of fifteen years outranks every other situation in the book forever, purely for
having been around. A trailing year is a rate, comparable to an annual carrying
cost, and it is a fair reading of what is actually at risk per year. Lifetime
spend stays because "we have bought ₹2 crore from them since 2019" is worth
knowing — it is just not what the queue should sort on.

**A line whose bill named no supplier is skipped, not bucketed.** Attributing
it to an "unknown" vendor would create a phantom supplier that accumulates
spend and, at enough volume, wins the concentration card outright.

Deliberately absent:

**No lead time, no reliability, no on-time rate.** All three need promised
dates, and ``expected_delivery_date`` is blank on effectively every order in
this book. The ingestion layer records that rather than substituting an assumed
lead time, and this layer will not undo it.

**No price trend per supplier.** Two suppliers' unit costs for one item are
comparable only if the item is genuinely the same, and this book's item master
has known duplicates — the identity screen exists because of them. Comparing
across a duplicate pair would report a price gap that is really two names for
one thing.

**No "alternative supplier" suggestion.** Knowing that nobody else has sold us
an item is not knowing that nobody else can. See ``opportunities/supplier``,
where that distinction is the whole of the sole-source card.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from ...domain import models
from .. import events as ev
from ..engine import ADD, MAX, MIN, SET, Delta, Masters, as_decimal, register

SUPPLIER = "SUPPLIER"

#: The trailing window ``spend_recent`` covers, in days.
#:
#: Not a policy knob, deliberately. A policy threshold decides *whether* a
#: situation is worth raising and belongs in ``CommercialThresholds`` with a
#: version; this decides what the stored number *means*, and a fold whose
#: meaning moved between builds would make two days' rows incomparable. A year
#: because purchasing is seasonal in this trade and anything shorter reads a
#: quiet quarter as a collapsed relationship.
RECENT_DAYS = 365


class SupplierReducer:
    state = SUPPLIER
    handles = frozenset({ev.COST_LINE_RECORDED})

    def apply(self, event: models.BusinessEvent, ctx: Masters,
              as_of: date) -> Iterable[Delta]:
        payload = event.payload or {}
        vendor_external = str(payload.get("vendor_external_id") or "")
        if not vendor_external:
            # Either the bill named no supplier, or the event predates the
            # vendor being carried onto the line at all. Both are "we do not
            # know who sold this", and a state keyed by supplier has nowhere
            # honest to put it. The cost itself is unaffected — INVENTORY still
            # counts it.
            return ()
        vendor_id = ctx.vendor(event, vendor_external)
        product_id = ctx.product(event, str(payload.get("product_external_id") or ""))
        if vendor_id is None or product_id is None:
            return ()

        qty = as_decimal(payload.get("qty"))
        unit_cost = as_decimal(payload.get("unit_cost"))
        changes: list[tuple[str, str, Any]] = [
            # In the value as well as the key, so a detector groups on a field
            # rather than splitting a string it did not construct.
            (SET, "vendor_id", vendor_id),
            (SET, "product_id", product_id),
            (ADD, "lines", 1),
            (MIN, "first_purchased_on", event.occurred_on),
            (MAX, "last_purchased_on", event.occurred_on),
        ]
        if qty is not None:
            changes.append((ADD, "qty", qty))
        if qty is not None and unit_cost is not None:
            line_spend = qty * unit_cost
            changes.append((ADD, "spend", line_spend))
            if (as_of - event.occurred_on).days < RECENT_DAYS:
                changes += [
                    (ADD, "spend_recent", line_spend),
                    (ADD, "lines_recent", 1),
                ]
        return (Delta(SUPPLIER, f"{vendor_id}:{product_id}", tuple(changes)),)


register(SupplierReducer())
