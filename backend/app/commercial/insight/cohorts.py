"""Customer states over time, and movement between revenue bands.

Two views of the same underlying fact — that a customer base is not a number,
it is a population whose members change state — cut two ways:

``journey``   how many customers were in each state, month by month. Answers
              "is the base growing or churning?" without averaging the answer
              away, because a base that gains ten and loses ten is flat in
              revenue and in serious trouble.

``migration`` which revenue band each customer moved between, from one period
              to the next. Answers "are our big customers getting bigger?" —
              a question total revenue cannot answer at all, since one large
              customer growing can mask twenty small ones shrinking.

Bands are quantile-based rather than fixed dice. Fixed rupee bands make a
migration matrix meaningless the moment the currency or the customer size
distribution differs, and this platform now runs in more than one currency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from ...signals import aggregates as agg
from ...signals.base import SaleRow
from . import absence
from .flow import LOST, classify
from .periods import Comparison, Period, months_back, revenue_in

#: A customer with no order for this long is dormant rather than merely quiet.
#: Expressed in months and deliberately generous: a distributor's customer who
#: orders quarterly is not lost in month four.
DORMANT_AFTER_MONTHS = 6

ACTIVE = "ACTIVE"
DORMANT = "DORMANT"

#: Ordered smallest to largest, so a matrix renders in a natural direction.
BANDS = ("NONE", "SMALL", "MID", "LARGE", "KEY")

#: How many customers travel with a drilled-into cell or band.
#:
#: A cell is a drill-down *target*, not a table dump — the whole population of
#: one state in one month can be thousands of rows, and sending twelve months
#: of them to draw a chart is a payload nobody asked for. The ``count`` beside
#: the members is always the honest total, so a screen can say "the 20 largest
#: of 214" rather than quietly implying 20 is all there is.
MEMBERS_PER_CELL = 20


@dataclass
class JourneyPoint:
    period: Period
    counts: dict[str, int]
    revenue: float
    #: state -> the largest customers in it that month, capped at
    #: ``MEMBERS_PER_CELL``. ``counts`` remains the honest total.
    members: dict[str, list[dict]]

    def to_dict(self) -> dict:
        return {**self.period.to_dict(), "counts": self.counts,
                "revenue": round(self.revenue, 2), "members": self.members}


def journey(sales: Iterable[SaleRow], as_of: date, months: int = 12, *,
            names: Optional[dict[str, str]] = None) -> list[JourneyPoint]:
    """Per month, how many customers were in each state — and which ones.

    Each month is classified against the month before it, using the same
    ``classify`` the revenue waterfall uses — so the two views cannot disagree
    about whether a customer grew.

    The members travel with the counts because a band on this chart is a
    question ("who are the 21 that shrank?") that a count alone cannot answer,
    and the classification that produced the count is right here. Recomputing
    it behind a second endpoint would be a second place for the two answers to
    drift apart. They are capped and shaped exactly like ``migration``'s.
    """
    rows = list(sales)
    grouped = agg.by_customer(rows)
    periods = months_back(as_of, months)
    labels = names or {}
    out: list[JourneyPoint] = []

    for i, period in enumerate(periods):
        if i == 0:
            continue                     # no prior month inside the window
        prior = periods[i - 1]
        counts: dict[str, int] = {}
        members: dict[str, list[dict]] = {}
        for customer_id, customer_rows in grouped.items():
            prev = revenue_in(customer_rows, prior)
            cur = revenue_in(customer_rows, period)
            if prev <= 0 and cur <= 0:
                continue
            traded_earlier = any(r.date < prior.start for r in customer_rows)
            kind = classify(prev, cur, traded_earlier)
            counts[kind] = counts.get(kind, 0) + 1
            members.setdefault(kind, []).append({
                "customer_id": customer_id,
                "label": labels.get(customer_id, customer_id),
                "previous": round(prev, 2), "current": round(cur, 2),
                "delta": round(cur - prev, 2),
            })
        out.append(JourneyPoint(
            period=period, counts=counts, revenue=revenue_in(rows, period),
            # Largest movement first: the reason to open a band is to find the
            # customer worth a phone call, not to read the state alphabetically.
            members={k: sorted(v, key=lambda m: abs(m["delta"]),
                               reverse=True)[:MEMBERS_PER_CELL]
                     for k, v in members.items()}))
    return out


def _quantile_edges(values: list[float]) -> list[float]:
    """Three cuts giving four non-empty bands, from the data itself.

    Fixed rupee edges would be wrong in another currency and wrong again for a
    distributor whose customers are all one size. Quantiles adapt; the labels
    stay comparable because they describe rank, not amount.
    """
    ordered = sorted(v for v in values if v > 0)
    if len(ordered) < 4:
        return []
    def at(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * q))]
    return [at(0.25), at(0.50), at(0.80)]


def band_of(value: float, edges: list[float]) -> str:
    if value <= 0:
        return "NONE"
    if not edges:
        return "MID"
    if value <= edges[0]:
        return "SMALL"
    if value <= edges[1]:
        return "MID"
    if value <= edges[2]:
        return "LARGE"
    return "KEY"


def migration(sales: Iterable[SaleRow], names: dict[str, str],
              comparison: Comparison) -> dict:
    """Which band each customer moved from and to, between the two periods."""
    rows = list(sales)
    grouped = agg.by_customer(rows)

    current = {cid: revenue_in(r, comparison.current) for cid, r in grouped.items()}
    previous = {cid: revenue_in(r, comparison.previous) for cid, r in grouped.items()}
    # One set of edges for both periods, from the union: banding each period
    # separately would let a customer "move up" while spending less, purely
    # because the cohort around them shrank.
    edges = _quantile_edges(list(current.values()) + list(previous.values()))

    cells: dict[tuple[str, str], list[dict]] = {}
    for customer_id in grouped:
        prev, cur = previous[customer_id], current[customer_id]
        if prev <= 0 and cur <= 0:
            continue
        key = (band_of(prev, edges), band_of(cur, edges))
        cells.setdefault(key, []).append({
            "customer_id": customer_id,
            "label": names.get(customer_id, customer_id),
            "previous": round(prev, 2), "current": round(cur, 2),
            "delta": round(cur - prev, 2),
        })

    return {
        "comparison": comparison.to_dict(),
        "bands": list(BANDS),
        "band_edges": [round(e, 2) for e in edges],
        "cells": [
            {"from": frm, "to": to, "count": len(members),
             "revenue_delta": round(sum(m["delta"] for m in members), 2),
             # Capped — see MEMBERS_PER_CELL. `journey` caps the same way, and
             # the two drill-downs staying the same size is what lets one
             # screen component read either.
             "members": sorted(members, key=lambda m: abs(m["delta"]),
                               reverse=True)[:MEMBERS_PER_CELL]}
            for (frm, to), members in sorted(cells.items())
        ],
    }


def dormancy(sales: Iterable[SaleRow], names: dict[str, str], as_of: date) -> dict:
    """Customers whose last order is old enough to be a decision.

    Not a churn prediction — an observation with a date attached. The platform
    has no basis for the former and every basis for the latter.
    """
    grouped = agg.by_customer(sales)
    cutoff_month = as_of.year * 12 + as_of.month - DORMANT_AFTER_MONTHS
    dormant = []
    for customer_id, rows in grouped.items():
        last = max(r.date for r in rows)
        if last.year * 12 + last.month < cutoff_month:
            dormant.append({
                "customer_id": customer_id,
                "label": names.get(customer_id, customer_id),
                "last_transaction": last.isoformat(),
                "months_quiet": (as_of.year * 12 + as_of.month) - (last.year * 12 + last.month),
                "lifetime_revenue": round(float(sum(r.line_revenue for r in rows)), 2),
            })
    dormant.sort(key=lambda d: d["lifetime_revenue"], reverse=True)
    return {"threshold_months": DORMANT_AFTER_MONTHS, "count": len(dormant),
            "customers": dormant[:50]}


def lost_revenue(sales: Iterable[SaleRow], names: dict[str, str],
                 comparison: Comparison, metrics_by_customer: dict[str, list]) -> dict:
    """Revenue that stopped, grouped by what the data says caused it.

    Causes come from persisted metric rows, not from a model and not from a
    guess. Where the rows do not support a cause, the bucket is named
    ``UNEXPLAINED`` and counted — a category a screen should show honestly
    rather than distribute across the others to make a pie look complete.
    """
    rows = list(sales)
    grouped = agg.by_customer(rows)
    causes: dict[str, dict] = {}

    for customer_id, customer_rows in grouped.items():
        prev = revenue_in(customer_rows, comparison.previous)
        cur = revenue_in(customer_rows, comparison.current)
        if prev <= 0 or cur >= prev:
            continue
        traded_earlier = any(r.date < comparison.previous.start for r in customer_rows)
        kind = classify(prev, cur, traded_earlier)
        if kind not in (LOST, "SHRUNK"):
            continue

        lost = prev - cur
        metrics = metrics_by_customer.get(customer_id) or []
        eroding = [m for m in metrics if (m.margin_change_pp or 0) < 0]
        cost_driven = [m for m in metrics if m.erosion_kind == "COST_DRIVEN"]

        if kind == LOST:
            cause = "STOPPED_BUYING"
        elif cost_driven:
            cause = "COST_INCREASE_NOT_PASSED"
        elif eroding:
            cause = "MARGIN_EROSION"
        else:
            cause = "UNEXPLAINED"

        bucket = causes.setdefault(cause, {"cause": cause, "amount": 0.0,
                                           "customers": []})
        bucket["amount"] += lost
        bucket["customers"].append({
            "customer_id": customer_id,
            "label": names.get(customer_id, customer_id),
            "lost": round(lost, 2),
            "previous": round(prev, 2), "current": round(cur, 2),
        })

    for bucket in causes.values():
        bucket["amount"] = round(bucket["amount"], 2)
        bucket["customers"].sort(key=lambda c: c["lost"], reverse=True)
        bucket["count"] = len(bucket["customers"])
        bucket["customers"] = bucket["customers"][:20]

    ordered = sorted(causes.values(), key=lambda b: b["amount"], reverse=True)
    return {
        "comparison": comparison.to_dict(),
        "total_lost": round(sum(b["amount"] for b in ordered), 2),
        "causes": ordered,
    }


def health_timeline(sales: Iterable[SaleRow], costs_by_product: dict,
                    as_of: date, months: int = 18,
                    payment_series: Optional[list[dict]] = None) -> dict:
    """One customer's revenue, order cadence, margin and days-to-pay, by month.

    Payment behaviour used to be declared unavailable here, because the
    platform held invoice and bill lines and no receipts. It holds receipts
    now, so the series is computed and the refusal comes off — but only when
    the caller actually passes one. An organization that has not synced
    payments gets the same honest gap it always did, rather than an empty row
    implying nobody has ever paid.
    """
    rows = list(sales)
    periods = months_back(as_of, months)
    series = []
    for period in periods:
        in_period = [r for r in rows if period.contains(r.date)]
        revenue = float(sum(r.line_revenue for r in in_period))
        orders = len({(r.source_ref or {}).get("record_id") or r.external_ref
                      for r in in_period})
        cost = 0.0
        covered = 0
        for row in in_period:
            unit = costs_by_product.get(row.product_id)
            if unit is not None:
                cost += float(unit) * float(row.qty)
                covered += 1
        margin = ((revenue - cost) / revenue) if revenue and covered else None
        series.append({
            **period.to_dict(),
            "revenue": round(revenue, 2),
            "orders": orders,
            "margin": round(margin, 4) if margin is not None else None,
            # Coverage travels with the margin so a screen can grey out a point
            # computed from a third of the lines instead of drawing it solid.
            "cost_coverage": round(covered / len(in_period), 2) if in_period else None,
        })
    if payment_series is not None:
        # Keyed by the month the invoice was raised, so the point sits beside
        # that month's own revenue and orders rather than beside the month the
        # cash happened to land in.
        by_label = {p["label"]: p for p in payment_series}
        for point in series:
            paid = by_label.get(point["label"])
            point["median_days_to_pay"] = (paid or {}).get("median_days_to_pay")
            point["settled"] = (paid or {}).get("settled", 0)

    return {
        "months": months,
        "series": series,
        "unavailable": ([] if payment_series is not None else [
            {"series": "payment_behaviour",
             # It names its own remedy — "run a sync" — which is what
             # COLLECTABLE means.
             "kind": absence.COLLECTABLE,
             "reason": "No customer payments have synced yet, so days-to-pay "
                       "cannot be computed without inventing it. Run a sync — "
                       "the platform reads receipts now."}
        ]),
    }
