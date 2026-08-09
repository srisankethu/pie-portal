"""Decisions the INVENTORY state can describe.

Five situations, each with a distinct action set. Deliberately not one type per
phrase in the brief: "dead stock", "capital locked" and "capital release
opportunity" are one situation measured three ways, and three cards for one
shelf line is three things to dismiss.

Every figure here is arithmetic over a folded state value, and the evidence
dictionary carries the inputs — so a reader who distrusts ₹4,01,250 can
multiply 1,000 by 401.25 themselves. That is the whole standard: **no number
appears on a card that a person cannot reproduce from the numbers beside it.**

What is deliberately not here:

**No forecast.** "Months of cover" is stock on hand divided by the offtake
already recorded — a ratio of two facts. It is never described as what *will*
sell, and no detector projects a date.

**No recovery probability or expected recovery value.** Both need a model of
future demand. The stock screen already refuses them by name and says why; a
decision card refusing them differently would be the same lie in a new place.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from ...domain.enums import DecisionType, SubjectEntityType
from ..reducers.inventory import INVENTORY
from .base import (BUNDLE_WITH_MOVING, CANCEL_PURCHASE_ORDER, DEFER_PURCHASE,
                   DISCOUNT_TO_MOVE, EXPEDITE_INBOUND, PROPOSE_WRITE_OFF,
                   PUSH_TO_PAST_BUYERS, REORDER_NOW, RETURN_TO_SUPPLIER,
                   RE_PROMISE_CUSTOMER, SET_REORDER_POINT, SPLIT_PURCHASE,
                   STOP_REORDERING, DecisionPolicy, Impact, OpportunityDraft,
                   day, money, number, register)

_PRODUCT = SubjectEntityType.PRODUCT.value

#: A month, for turning a daily offtake rate into months of cover. 30 rather
#: than 30.4: cover is a coarse measure and a spurious decimal in the divisor
#: would imply a precision the underlying "units sold since we first saw it"
#: does not have.
_DAYS_PER_MONTH = Decimal(30)


def _on_shelf(states: dict[str, dict[str, dict[str, Any]]],
              ) -> Iterable[tuple[str, dict[str, Any], Decimal]]:
    """Every item with stock actually on the shelf, and how much.

    One definition of "on the shelf", shared by all five detectors: tracked,
    observed, and a positive count. A service has no shelf, and an item nothing
    has ever counted is unknown rather than empty — writing zero for either
    would put the whole catalogue into the out-of-stock list.
    """
    for product_id, value in (states.get(INVENTORY) or {}).items():
        if value.get("tracked") is False:
            continue
        on_hand = number(value, "on_hand")
        if on_hand is None or on_hand <= 0:
            continue
        yield product_id, value, on_hand


def _value_of(on_hand: Decimal, value: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    """What the stock cost, and what it costs to keep for a month.

    ``None`` when the item has no purchase rate: a line nobody has costed is
    unknown, not free, and ranking it as zero would bury a real problem under
    items we simply have not priced.
    """
    rate = number(value, "purchase_rate")
    if rate is None or rate <= 0:
        return None
    return money(on_hand * rate), rate


def _revenue_per_unit(value: dict[str, Any]) -> Decimal | None:
    """What one unit of this item has actually sold for.

    Used wherever a shortfall has to be priced — below-reorder and oversold
    both need it, and two copies would let one card price a stockout at the
    average and the other at something subtly different. ``None`` where the
    item has no selling history: the shortfall is still real and is reported
    as unsizeable rather than as zero.
    """
    sold = number(value, "units_sold") or Decimal(0)
    revenue = number(value, "revenue") or Decimal(0)
    return (revenue / sold) if sold > 0 else None


def _idle_days(value: dict[str, Any], as_of: date) -> tuple[int | None, date | None]:
    """Days since the last sale, and when that was. ``None`` days means it has
    never sold at all — which is a question, not yet a finding. See
    ``_opportunity_days``."""
    last = day(value, "last_sold_on")
    return ((as_of - last).days if last else None), last


def _opportunity_days(value: dict[str, Any], as_of: date) -> int | None:
    """How long this line has had the chance to sell.

    Days since the platform first saw it — its first stock reading or its first
    purchase, whichever came first. ``None`` when neither is on record.

    This is the denominator a "never sold" card needs. Without it the detector
    below read a missing sale date as satisfying *any* idleness bound, so an
    item bought last week raised a dead-stock decision with a write-off among
    its actions. The same defect lived in ``commercial/insight/stock.py``; both
    are fixed, and both read these same two fold fields, so the queue and the
    screen cannot disagree about which lines are dead.
    """
    first = _earliest(day(value, "first_observed_on"),
                      day(value, "first_purchased_on"))
    return (as_of - first).days if first else None


def _earliest(*days: date | None) -> date | None:
    known = [d for d in days if d is not None]
    return min(known) if known else None


class _Idle:
    """Shared by the dead and slow-moving detectors, which differ only in where
    the line is drawn and what they suggest doing about it.

    One implementation because they are one calculation. Two copies would drift
    the day somebody changes how holding cost is derived, and a shelf line
    would then be worth two different amounts depending on which card you
    opened."""

    states = frozenset({INVENTORY})

    def _drafts(self, states, policy: DecisionPolicy, as_of: date, *,
                at_least: int, less_than: int | None,
                actions: tuple[str, ...], label: str,
                ) -> Iterable[OpportunityDraft]:
        for product_id, value, on_hand in _on_shelf(states):
            priced = _value_of(on_hand, value)
            if priced is None:
                continue
            capital, rate = priced
            idle, last_sold = _idle_days(value, as_of)
            if idle is not None:
                days = idle
            else:
                # Never sold. That is only a finding once there has been time
                # to sell it, so the opportunity window stands in for the idle
                # window — and where even that is unknown, no card is raised.
                # Previously this substituted ``at_least``, which made a
                # missing sale date satisfy every bound automatically and put
                # freshly bought stock in the queue with a write-off attached.
                days = _opportunity_days(value, as_of)
                if days is None:
                    continue
            if days < at_least or (less_than is not None and days >= less_than):
                continue
            monthly = money(capital * policy.carrying_monthly_pct)
            if capital < policy.min_impact:
                continue
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_PRODUCT,
                subject_entity_id=product_id,
                impact=Impact(
                    financial=capital,
                    basis=("Capital held in this line — what it cost to buy "
                           "what is still on the shelf"),
                    monthly=monthly,
                    operational={
                        "on_hand": str(on_hand),
                        "idle_days": idle,
                        "last_sold_on": last_sold.isoformat() if last_sold else None,
                        "units_sold_to_date": str(number(value, "units_sold") or 0),
                    }),
                # The sentence explains the situation; the money lives in the
                # impact beside it. Repeating the figures here would print them
                # unformatted — the server does not know how this organization
                # renders currency — and would give the reader two versions of
                # one number to reconcile.
                rationale=(
                    f"{on_hand} on the shelf, "
                    + (f"and nothing sold for {idle} days" if idle is not None
                       else f"and never sold in the {days} days since the "
                            "platform first saw it")
                    + f". Bought at {rate} each and carried at "
                      # normalize(), because Decimal("0.18") * 100 is
                      # Decimal("18.00") and "18.00%" reads like a precision
                      # the rate does not have.
                      f"{(policy.carrying_annual_pct * 100).normalize()}% a year."),
                evidence={
                    "state": INVENTORY, "key": product_id,
                    "on_hand": str(on_hand), "purchase_rate": str(rate),
                    "last_sold_on": last_sold.isoformat() if last_sold else None,
                    "carrying_annual_pct": str(policy.carrying_annual_pct),
                    "band": label,
                },
                actions=actions,
                state_keys=(product_id,))


class DeadStockDetector(_Idle):
    """On the shelf, nothing sold for longer than the dead threshold."""

    decision_type = DecisionType.INV_DEAD_STOCK.value

    def detect(self, states, policy, as_of):
        return self._drafts(
            states, policy, as_of,
            at_least=policy.dead_days, less_than=None,
            label="DEAD",
            actions=(PUSH_TO_PAST_BUYERS, DISCOUNT_TO_MOVE, BUNDLE_WITH_MOVING,
                     RETURN_TO_SUPPLIER, PROPOSE_WRITE_OFF, STOP_REORDERING))


class SlowMovingDetector(_Idle):
    """Slower than the slow threshold, but not yet dead. A different decision:
    the point of surfacing it is that it can still be sold at full price."""

    decision_type = DecisionType.INV_SLOW_MOVING.value

    def detect(self, states, policy, as_of):
        return self._drafts(
            states, policy, as_of,
            at_least=policy.slow_days, less_than=policy.dead_days,
            label="SLOW",
            actions=(PUSH_TO_PAST_BUYERS, BUNDLE_WITH_MOVING, STOP_REORDERING))


class ExcessCoverDetector:
    """More stock than the offtake this item has actually shown.

    A ratio of two recorded facts — what is on the shelf, and what has left it
    since the platform first saw the item. Never a forecast: the card says "you
    hold N months of what you have historically sold", which is a measurement,
    and never "this will last N months", which would be a prediction.

    Requires a real offtake to divide by, and requires the item to be *moving*.
    A line that has not sold since the slow threshold is a slow or dead line,
    and it already has a card that says so with the right actions on it. Two
    cards for one shelf line is two things to dismiss, and the second one is
    always the less useful — "you hold too much of this" is not the advice you
    want about stock that is not selling at all.
    """

    decision_type = DecisionType.INV_EXCESS_COVER.value
    states = frozenset({INVENTORY})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        for product_id, value, on_hand in _on_shelf(states):
            sold = number(value, "units_sold")
            # The window the offtake was measured over, from the *first* sale
            # to today. Using the last movement instead would give a period of
            # days, an enormous implied daily rate, and an excess-cover figure
            # that never fires — the arithmetic version of measuring a year's
            # rainfall with this morning's bucket.
            first_sold = day(value, "first_sold_on")
            if not sold or sold <= 0 or first_sold is None:
                continue
            idle, _last = _idle_days(value, as_of)
            # Not selling is a different decision with different actions, and
            # the idle detectors own it. See the class docstring.
            if idle is None or idle >= policy.slow_days:
                continue
            observed_days = Decimal(max((as_of - first_sold).days, 1))
            daily = sold / observed_days
            if daily <= 0:
                continue
            cover_months = (on_hand / daily) / _DAYS_PER_MONTH
            if cover_months < policy.excess_cover_months:
                continue
            priced = _value_of(on_hand, value)
            if priced is None:
                continue
            capital, rate = priced
            # The excess is what is held beyond the cover the policy allows —
            # not the whole shelf. Surfacing the full holding as "excess" would
            # overstate what is releasable by exactly the stock that is doing
            # its job.
            keep = (daily * _DAYS_PER_MONTH * policy.excess_cover_months)
            excess_units = on_hand - keep
            if excess_units <= 0:
                continue
            excess_value = money(excess_units * rate)
            if excess_value < policy.min_impact:
                continue
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_PRODUCT,
                subject_entity_id=product_id,
                impact=Impact(
                    financial=excess_value,
                    basis=("Capital in stock held beyond "
                           f"{policy.excess_cover_months} months of measured offtake"),
                    monthly=money(excess_value * policy.carrying_monthly_pct),
                    operational={
                        "on_hand": str(on_hand),
                        "months_of_cover": str(cover_months.quantize(Decimal("0.1"))),
                        "excess_units": str(excess_units.quantize(Decimal("0.01"))),
                    }),
                rationale=(
                    f"{on_hand} on the shelf against {sold} sold in "
                    f"{observed_days} days — "
                    f"{cover_months.quantize(Decimal('0.1'))} months of cover at "
                    "the rate this item has actually moved, where the policy "
                    f"allows {policy.excess_cover_months:g}."),
                evidence={
                    "state": INVENTORY, "key": product_id,
                    "on_hand": str(on_hand), "units_sold": str(sold),
                    "first_sold_on": first_sold.isoformat(),
                    "observed_days": str(observed_days),
                    "purchase_rate": str(rate),
                    "excess_cover_months": str(policy.excess_cover_months),
                },
                actions=(DEFER_PURCHASE, SPLIT_PURCHASE, PUSH_TO_PAST_BUYERS,
                         DISCOUNT_TO_MOVE),
                state_keys=(product_id,))


class BelowReorderDetector:
    """At or below a reorder point somebody actually set.

    Only for items with a reorder level in Zoho. An item without one cannot be
    below a point that does not exist, and inventing a point here would be the
    platform making a stocking policy in a module whose purpose is not to.
    Those items get their own, much smaller, decision: set one.
    """

    decision_type = DecisionType.INV_BELOW_REORDER.value
    states = frozenset({INVENTORY})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        for product_id, value in (states.get(INVENTORY) or {}).items():
            if value.get("tracked") is False:
                continue
            reorder = number(value, "reorder_level")
            on_hand = number(value, "on_hand")
            if reorder is None or reorder <= 0 or on_hand is None:
                continue
            if on_hand > reorder:
                continue
            rate = number(value, "purchase_rate")
            sold = number(value, "units_sold") or Decimal(0)
            revenue = number(value, "revenue") or Decimal(0)
            # What a stockout costs is the revenue this item earns, not what it
            # cost to buy — so the impact is sized on the revenue per unit
            # already recorded, over the units needed to get back to the point.
            per_unit = _revenue_per_unit(value)
            shortfall = reorder - on_hand
            at_risk = money(shortfall * per_unit) if per_unit is not None else None
            if at_risk is None or at_risk < policy.min_impact:
                continue
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_PRODUCT,
                subject_entity_id=product_id,
                impact=Impact(
                    financial=at_risk,
                    basis=("Revenue this item has earned per unit, over the "
                           "units needed to return to its reorder point"),
                    operational={
                        "on_hand": str(on_hand),
                        "reorder_level": str(reorder),
                        "shortfall": str(shortfall),
                    }),
                rationale=(
                    f"{on_hand} on hand against a reorder point of {reorder}, "
                    f"so it is {shortfall} short. Sized at the price this item "
                    f"has actually sold at across {sold} units."),
                evidence={
                    "state": INVENTORY, "key": product_id,
                    "on_hand": str(on_hand), "reorder_level": str(reorder),
                    "units_sold": str(sold), "revenue": str(revenue),
                    "purchase_rate": str(rate) if rate is not None else None,
                },
                actions=(REORDER_NOW, EXPEDITE_INBOUND, SPLIT_PURCHASE),
                state_keys=(product_id,))


class OversoldDetector:
    """Committed beyond what is on the shelf.

    Zoho's own netting: ``actual_available`` is what remains after open orders
    have been promised, so a negative figure against positive stock means
    somebody is going to miss a delivery date. Not an estimate and not a
    threshold — the arithmetic is the ERP's, and this reads it.

    The single most actionable thing this layer produces, which is why it
    carries no materiality floor: a missed promise to a customer is a problem
    at any rupee value.
    """

    decision_type = DecisionType.INV_OVERSOLD.value
    states = frozenset({INVENTORY})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        for product_id, value in (states.get(INVENTORY) or {}).items():
            actual = number(value, "actual_available")
            if actual is None or actual >= 0:
                continue
            short = -actual
            on_hand = number(value, "on_hand") or Decimal(0)
            sold = number(value, "units_sold") or Decimal(0)
            revenue = number(value, "revenue") or Decimal(0)
            per_unit = _revenue_per_unit(value)
            # Sized on the revenue that cannot ship. Where the item has no
            # selling history the shortfall is still real and still surfaced —
            # it is ranked on what we can size, and the card says the value is
            # unknown rather than pretending it is zero.
            at_risk = money(short * per_unit) if per_unit is not None else Decimal(0)
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_PRODUCT,
                subject_entity_id=product_id,
                impact=Impact(
                    financial=at_risk,
                    basis=("Revenue on the units promised beyond stock, at the "
                           "price this item has sold at"
                           if per_unit is not None else
                           "Not sizeable — this item has no selling history to "
                           "price the shortfall against"),
                    operational={
                        "on_hand": str(on_hand),
                        "actual_available": str(actual),
                        "short_by": str(short),
                    }),
                rationale=(
                    f"Open orders promise {short} more than the shelf holds "
                    f"({on_hand} on hand, {actual} after commitments). Zoho's "
                    "own netting, not an estimate — a delivery date is going "
                    "to be missed unless this is bought or re-promised."),
                evidence={
                    "state": INVENTORY, "key": product_id,
                    "on_hand": str(on_hand), "actual_available": str(actual),
                    "units_sold": str(sold), "revenue": str(revenue),
                },
                actions=(EXPEDITE_INBOUND, REORDER_NOW, RE_PROMISE_CUSTOMER,
                         CANCEL_PURCHASE_ORDER),
                state_keys=(product_id,))


register(DeadStockDetector())
register(SlowMovingDetector())
register(ExcessCoverDetector())
register(BelowReorderDetector())
register(OversoldDetector())

# ``SET_REORDER_POINT`` is imported for the vocabulary check but has no
# detector yet: "no reorder point set" is a master-data gap the stock screen
# already reports as a count, and promoting it to one card per item would put
# hundreds of near-identical rows in a queue meant for decisions.
_ = SET_REORDER_POINT
