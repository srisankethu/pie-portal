"""Decisions the SUPPLIER state can describe.

Two situations, and they are about different kinds of exposure:

**Spend concentration** is about money. A large share of everything the
business buys resting on one relationship. Sized on that supplier's spend,
because that is the purchasing that would have to be placed somewhere else.

**Sole source** is about substitutability. An item that has only ever come from
one supplier has no alternative *on record* — and a small supplier can be
irreplaceable while a large one is easy to replace, which is exactly why this
is not the concentration card with a different threshold.

## The sentence that keeps the sole-source card honest

We know who we have bought from. We do not know who we *could* buy from. An
insert that has only ever come from one distributor may have six other
distributors a phone call away, and this platform has no way to know that — it
reads a purchase ledger, not a market.

So the card says "the only supplier in your own purchase history", never "the
only supplier". The difference is the whole value of the card: stated the first
way it prompts somebody to go and find out, stated the second way it asserts
something false and gets believed once.

## What is deliberately not here

**No supplier reliability, lead time or on-time rate.** All three need promised
dates, and ``expected_delivery_date`` is blank on effectively every order in
this book. The ingestion layer reports that rather than substituting an assumed
lead time, and neither this module nor ``supply`` will undo it by calling age
lateness.

**No "switch to supplier X" and no price comparison across suppliers.** Two
suppliers' unit costs for one item are comparable only if the item really is
the same, and this book's item master has known duplicates — the identity
screen exists because of them. A price gap that is really two names for one
part is worse than no comparison.

**No risk score.** Combining share, substitutability and age into one number
would produce something nobody can argue with, which in a queue means something
nobody reads. Each card states its own figure and what it is.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from ...domain.enums import DecisionType, SubjectEntityType
from ..reducers.supplier import SUPPLIER
from .base import (HOLD_BUFFER_STOCK, NEGOTIATE_VOLUME_TERMS,
                   QUALIFY_SECOND_SOURCE, SPREAD_THE_SPEND, DecisionPolicy,
                   Impact, OpportunityDraft, day, money, number, register)

_SUPPLIER_SUBJECT = SubjectEntityType.VENDOR.value


def _pairs(states: dict[str, dict[str, dict[str, Any]]],
           ) -> Iterable[dict[str, Any]]:
    """Every (vendor, product) the purchase fold recorded.

    Reads ``vendor_id`` and ``product_id`` as fields rather than splitting the
    key: the key is an identity the reducer composed, and picking it apart here
    would break the first time an id contained a colon.
    """
    for value in (states.get(SUPPLIER) or {}).values():
        if value.get("vendor_id") and value.get("product_id"):
            yield value


def _by_vendor(states) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in _pairs(states):
        grouped[str(value["vendor_id"])].append(value)
    return grouped


def _spend(value: dict[str, Any]) -> Decimal:
    """The trailing year, which is what both cards are sized and ranked on.

    Not lifetime spend. Every other impact in the queue is a stock — capital on
    a shelf, a balance owed — and ranking a lifetime flow against those puts a
    long-standing supplier above everything else in the book forever, for no
    reason but longevity. See the reducer's docstring.
    """
    return number(value, "spend_recent") or Decimal(0)


def _lifetime(value: dict[str, Any]) -> Decimal:
    """Everything ever bought. Reported as context, never ranked on."""
    return number(value, "spend") or Decimal(0)


class SpendConcentrationDetector:
    """A large share of all purchasing resting on one supplier.

    A standing position rather than a change, and stated as one. It is not by
    itself a problem — a distributor with one excellent principal is a normal
    and often good arrangement — which is why the actions are about terms and
    spread rather than about replacing anybody.
    """

    decision_type = DecisionType.SUP_SPEND_CONCENTRATION.value
    states = frozenset({SUPPLIER})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        grouped = _by_vendor(states)
        book = sum((_spend(v) for rows in grouped.values() for v in rows),
                   Decimal(0))
        # Nothing bought, nothing to be a share of. A guard rather than a
        # division: zero purchasing is a legitimate state on a new connection.
        if book <= 0:
            return
        for vendor_id, rows in grouped.items():
            spend = sum((_spend(v) for v in rows), Decimal(0))
            if spend < policy.min_impact:
                continue
            share = spend / book
            if share < policy.supplier_share:
                continue
            items = len(rows)
            lifetime = sum((_lifetime(v) for v in rows), Decimal(0))
            last = max((d for d in (day(v, "last_purchased_on") for v in rows)
                        if d is not None), default=None)
            pct = (share * 100).quantize(Decimal("0.1"))
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_SUPPLIER_SUBJECT,
                subject_entity_id=vendor_id,
                impact=Impact(
                    financial=money(spend),
                    basis=("Purchase spend placed with this supplier over the "
                           "last year — what would have to be sourced "
                           "elsewhere, per year, without them"),
                    operational={
                        "share_of_spend_pct": str(pct),
                        "items_supplied": items,
                        "purchase_book": str(money(book)),
                        "lifetime_spend": str(money(lifetime)),
                        "last_purchased_on": last.isoformat() if last else None,
                    }),
                rationale=(
                    f"{pct}% of everything this business bought in the last "
                    f"year was placed with this one supplier, across {items} "
                    "item(s). That is a standing position, not a change, and "
                    "not automatically a problem — it is the size of the "
                    "dependency, stated so it is a decision rather than an "
                    "assumption."),
                evidence={
                    "state": SUPPLIER, "key": vendor_id,
                    "spend_recent": str(spend),
                    "spend": str(lifetime),
                    "purchase_book": str(money(book)),
                    "share_of_spend": str(share.quantize(Decimal("0.0001"))),
                    "items_supplied": items,
                    "supplier_share_threshold": str(policy.supplier_share),
                },
                actions=(NEGOTIATE_VOLUME_TERMS, SPREAD_THE_SPEND,
                         QUALIFY_SECOND_SOURCE),
                state_keys=tuple(sorted(
                    f"{vendor_id}:{v['product_id']}" for v in rows)))


class SoleSourceDetector:
    """Items this supplier is the only recorded source for.

    Aggregated to the supplier rather than raised per item. One card per
    sole-sourced part would be hundreds of cards saying the same thing, and the
    decision — qualify an alternative, or hold a buffer — is made about the
    supplier, not about each part in turn.

    The figure is the spend on those items specifically, not the supplier's
    whole spend: the exposure is what stops if they stop, and an item they
    supply alongside somebody else does not stop.
    """

    decision_type = DecisionType.SUP_SOLE_SOURCE.value
    states = frozenset({SUPPLIER})

    def detect(self, states, policy: DecisionPolicy,
               as_of: date) -> Iterable[OpportunityDraft]:
        # How many distinct suppliers each product has ever come from.
        sources: dict[str, set[str]] = defaultdict(set)
        for value in _pairs(states):
            sources[str(value["product_id"])].add(str(value["vendor_id"]))

        exclusive: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for value in _pairs(states):
            product_id = str(value["product_id"])
            if len(sources[product_id]) == 1:
                exclusive[str(value["vendor_id"])].append(value)

        for vendor_id, rows in exclusive.items():
            spend = sum((_spend(v) for v in rows), Decimal(0))
            if spend < policy.min_impact:
                continue
            last = max((d for d in (day(v, "last_purchased_on") for v in rows)
                        if d is not None), default=None)
            since = (as_of - last).days if last else None
            yield OpportunityDraft(
                decision_type=self.decision_type,
                subject_entity_type=_SUPPLIER_SUBJECT,
                subject_entity_id=vendor_id,
                impact=Impact(
                    financial=money(spend),
                    basis=("Spend over the last year on items no other "
                           "supplier has ever invoiced us for"),
                    operational={
                        "sole_sourced_items": len(rows),
                        "lifetime_spend": str(money(
                            sum((_lifetime(v) for v in rows), Decimal(0)))),
                        "last_purchased_on": last.isoformat() if last else None,
                        "days_since_last_purchase": since,
                    }),
                rationale=(
                    f"{len(rows)} item(s) have only ever been bought from this "
                    "supplier. That means no alternative appears anywhere in "
                    "your own purchase history — it does not mean no "
                    "alternative exists. The platform reads a purchase ledger, "
                    "not a market, so this is a prompt to find out rather than "
                    "a finding."
                    + (f" Last bought from them {since} days ago."
                       if since is not None else "")),
                evidence={
                    "state": SUPPLIER, "key": vendor_id,
                    "sole_sourced_items": len(rows),
                    "spend_recent": str(spend),
                    "spend": str(sum((_lifetime(v) for v in rows), Decimal(0))),
                    "last_purchased_on": last.isoformat() if last else None,
                },
                actions=(QUALIFY_SECOND_SOURCE, HOLD_BUFFER_STOCK,
                         NEGOTIATE_VOLUME_TERMS),
                state_keys=tuple(sorted(
                    f"{vendor_id}:{v['product_id']}" for v in rows)))


register(SpendConcentrationDetector())
register(SoleSourceDetector())
