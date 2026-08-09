"""INVENTORY — what is on the shelf, what moved, and what it last cost.

Keyed by product. Folds three event types and no more:

- ``STOCK_OBSERVED`` is an *observation*, so its fields are SET: the latest
  count of what is physically there replaces the previous one. It never
  accumulates — adding two stock counts together would be nonsense. Its *date*
  is both MAXed and MINed, because when the platform last looked and when it
  first looked are two different facts and the second one is what says whether
  "nothing has sold" has had time to mean anything.
- ``SALE_LINE_RECORDED`` and ``COST_LINE_RECORDED`` are *movements*, so their
  quantities ADD and their dates MAX. Sales also MIN a first-seen date, because
  an offtake *rate* needs a period and the only honest one is between the first
  sale observed and the last.

Deliberately absent, and it matters:

**No weeks of cover, no reorder point, no dead/slow band.** Those are
judgements with policy in them — a reorder level is something a person chooses
with lead time and service level in mind, and a health band is read off
thresholds that carry a version. This module holds the *facts a judgement is
made from*, so that when ``commercial/insight/stock.py`` moves onto state it
brings its own thresholds with it rather than finding them already baked in.

**No windowed figures.** "Units sold in the last 90 days" is a question, not a
state: the window belongs to whoever is asking. What is stored is cumulative up
to ``as_of``, and a window is the difference between two days' rows — which is
the thing storing a series makes possible in the first place.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from ...domain import models
from .. import events as ev
from ..engine import ADD, MAX, MIN, SET, Delta, Masters, as_decimal, register

INVENTORY = "INVENTORY"


class InventoryReducer:
    state = INVENTORY
    handles = frozenset({ev.STOCK_OBSERVED, ev.SALE_LINE_RECORDED,
                         ev.COST_LINE_RECORDED})

    def apply(self, event: models.BusinessEvent, ctx: Masters,
              as_of: date) -> Iterable[Delta]:
        payload = event.payload or {}
        external_id = str(payload.get("product_external_id") or "")
        if not external_id:
            return ()
        product_id = ctx.product(event, external_id)
        if product_id is None:
            # Reported by ``Masters``; a line for an item this database does
            # not hold is a gap in the master, not a reason to invent a key.
            return ()
        if event.event_type == ev.STOCK_OBSERVED:
            return self._observed(product_id, payload, event.occurred_on)
        if event.event_type == ev.SALE_LINE_RECORDED:
            return self._sold(product_id, payload, event.occurred_on)
        return self._purchased(product_id, payload, event.occurred_on)

    # ── an observation replaces; it never accumulates ───────────────────────
    @staticmethod
    def _observed(product_id: str, payload: dict[str, Any],
                  on: date) -> Iterable[Delta]:
        changes: list[tuple[str, str, Any]] = [
            (MAX, "observed_on", on),
            # The other end of the observation window. "Nothing has sold" is
            # only a finding once there has been time for something to sell,
            # and this is the earliest date the platform can show it was
            # watching this item at all. Without it, an item read for the
            # first time yesterday is indistinguishable from one that has sat
            # unsold for two years.
            (MIN, "first_observed_on", on),
        ]
        for field_name, source in (("on_hand", "on_hand"),
                                   ("available", "available"),
                                   ("actual_available", "actual_available"),
                                   ("reorder_level", "reorder_level"),
                                   ("purchase_rate", "purchase_rate")):
            value = as_decimal(payload.get(source))
            # An absent field is "the source did not report it", and SETting
            # None over a known value would lose a fact to a silent source.
            if value is not None:
                changes.append((SET, field_name, value))
        tracked = payload.get("tracked")
        if tracked is not None:
            changes.append((SET, "tracked", bool(tracked)))
        return (Delta(INVENTORY, product_id, tuple(changes)),)

    # ── movements accumulate ────────────────────────────────────────────────
    @staticmethod
    def _sold(product_id: str, payload: dict[str, Any],
              on: date) -> Iterable[Delta]:
        qty = as_decimal(payload.get("qty")) or Decimal(0)
        revenue = as_decimal(payload.get("line_revenue")) or Decimal(0)
        return (Delta(INVENTORY, product_id, (
            (ADD, "units_sold", qty),
            (ADD, "revenue", revenue),
            (MAX, "last_sold_on", on),
            # The other end of the window. Without it, "how fast does this
            # move" has no denominator: a rate needs a period, and the only
            # honest one is between the first sale observed and the last.
            (MIN, "first_sold_on", on),
            (ADD, "sale_lines", 1),
        )),)

    @staticmethod
    def _purchased(product_id: str, payload: dict[str, Any],
                   on: date) -> Iterable[Delta]:
        qty = as_decimal(payload.get("qty")) or Decimal(0)
        unit_cost = as_decimal(payload.get("unit_cost"))
        changes: list[tuple[str, str, Any]] = [
            (ADD, "units_purchased", qty),
            (MAX, "last_purchased_on", on),
            # When we first bought it, which is when it first *could* have been
            # sold. Paired with ``first_observed_on`` above: between them they
            # answer "how long has this line had the chance to move", which is
            # the denominator a "never sold" finding needs and does not
            # otherwise have.
            (MIN, "first_purchased_on", on),
            (ADD, "purchase_lines", 1),
        ]
        if unit_cost is not None:
            # SET, and the fold runs in date order, so this ends up being the
            # cost on the most recent bill — not the most recently *read* one.
            # Those differ every time a historical bill is backfilled.
            changes.append((SET, "last_unit_cost", unit_cost))
            changes.append((ADD, "spend", unit_cost * qty))
        return (Delta(INVENTORY, product_id, tuple(changes)),)


register(InventoryReducer())
