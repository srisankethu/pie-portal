"""How the mix changed, month by month — revenue or orders, customers or items.

The specification asks for a "Revenue Composition Evolution" and an "Order Flow
River". They are one chart: a stacked series over time, differing only in what
is summed. One module, two parameters — because two modules would mean two
top-N rules, two ways of handling the tail, and eventually two different answers
to "how much of this is Other".

**The tail is folded, never cycled.** A stacked chart with twenty bands is
twenty colours nobody can tell apart, so everything outside the top few becomes
a single *Other* band. It is named and counted, so the reader knows what was
folded rather than wondering whether the chart is complete. Generating a ninth
hue would be the alternative and is worse: colour would stop identifying anyone.

**Share and amount are both returned.** A composition chart answers two
different questions depending on which is drawn — "is the business growing" and
"is it getting more concentrated" — and a reader flipping between them should
not be refetching. The series carry both.
"""
from __future__ import annotations

from typing import Iterable, Literal, Optional

from ...signals.base import SaleRow
from .periods import months_back

BY_CUSTOMER = "customer"
BY_PRODUCT = "product"
REVENUE = "revenue"
ORDERS = "orders"

#: Bands before the tail is folded. Chosen against the palette: beyond this the
#: chart would need a colour it does not have, and a repeated hue in a stack is
#: worse than an honest "Other".
TOP_N = 6
OTHER = "__other__"


def _key(row: SaleRow, dimension: str) -> str:
    return row.customer_id if dimension == BY_CUSTOMER else row.product_id


def _order_key(row: SaleRow) -> str:
    """One source invoice, so a ten-line order counts once."""
    return str((row.source_ref or {}).get("record_id") or row.external_ref)


def build(sales: Iterable[SaleRow], names: dict[str, str], as_of, *,
          dimension: Literal["customer", "product"] = BY_CUSTOMER,
          measure: Literal["revenue", "orders"] = REVENUE,
          months: int = 12, top_n: int = TOP_N) -> dict:
    """Stacked monthly series for the top contributors, plus a folded tail."""
    rows = list(sales)
    periods = months_back(as_of, months)
    if not rows or not periods:
        return {"dimension": dimension, "measure": measure,
                "periods": [p.to_dict() for p in periods], "series": [],
                "totals": []}

    # Rank over the whole window, not per month: a band that changes identity
    # month to month is a band whose colour means nothing.
    lifetime: dict[str, float] = {}
    for row in rows:
        if any(p.contains(row.date) for p in periods):
            lifetime[_key(row, dimension)] = (
                lifetime.get(_key(row, dimension), 0.0) + float(row.line_revenue))
    ranked = sorted(lifetime, key=lambda k: lifetime[k], reverse=True)
    keep = set(ranked[:top_n])
    folded = len(ranked) - len(keep)

    buckets: dict[str, list[float]] = {k: [0.0] * len(periods) for k in keep}
    buckets[OTHER] = [0.0] * len(periods)
    # Orders are counted as distinct invoices per bucket per month, so the same
    # invoice touching three items is one order, not three.
    seen: list[dict[str, set]] = [{} for _ in periods]

    for row in rows:
        for i, period in enumerate(periods):
            if not period.contains(row.date):
                continue
            key = _key(row, dimension)
            bucket = key if key in keep else OTHER
            if measure == REVENUE:
                buckets[bucket][i] += float(row.line_revenue)
            else:
                seen[i].setdefault(bucket, set()).add(_order_key(row))
            break

    if measure == ORDERS:
        for i, per_bucket in enumerate(seen):
            for bucket, orders in per_bucket.items():
                buckets[bucket][i] = float(len(orders))

    totals = [sum(buckets[k][i] for k in buckets) for i in range(len(periods))]

    series = []
    for key in ranked[:top_n]:
        values = buckets[key]
        series.append({
            "key": key,
            "label": names.get(key, key),
            "values": [round(v, 2) for v in values],
            "shares": [round(v / t, 4) if t else 0.0 for v, t in zip(values, totals)],
            "total": round(sum(values), 2),
        })
    if folded > 0 and any(buckets[OTHER]):
        series.append({
            "key": OTHER,
            "label": f"Other ({folded})",
            "values": [round(v, 2) for v in buckets[OTHER]],
            "shares": [round(v / t, 4) if t else 0.0
                       for v, t in zip(buckets[OTHER], totals)],
            "total": round(sum(buckets[OTHER]), 2),
            "folded_count": folded,
        })

    return {
        "dimension": dimension,
        "measure": measure,
        "periods": [p.to_dict() for p in periods],
        "series": series,
        "totals": [round(t, 2) for t in totals],
        "folded_count": folded,
        # What changed, in one sentence the screen can print rather than derive.
        "movement": _movement(series, totals),
    }


def _movement(series: list[dict], totals: list[float]) -> Optional[dict]:
    """The largest share change across the window — the chart's headline.

    A chart with twelve months and seven rows has a lot of ink and one story,
    and leaving the reader to find it is how these end up decorative.

    **Halves, not endpoints.** The first version compared each band's first
    non-zero month against its last month, and it produced "went from 68% to 0%"
    for a customer who simply happened to trade in a quiet month early on — the
    denominator, not the customer, had moved. A share measured over half a window
    cannot be swung by one thin month, and it is also the comparison the sentence
    already implies. An odd number of periods gives the later half the extra
    month, so the most recent evidence is never the one that gets dropped.
    """
    if len(totals) < 2:
        return None
    mid = len(totals) // 2

    def share(values: list[float], lo: int, hi: int) -> Optional[float]:
        denominator = sum(totals[lo:hi])
        return (sum(values[lo:hi]) / denominator) if denominator else None

    movers = []
    for s in series:
        values = s["values"]
        before = share(values, 0, mid)
        after = share(values, mid, len(totals))
        # A half with no trade at all has no share to compare against — that is
        # a window too short to describe, not a move from zero.
        if before is None or after is None:
            continue
        movers.append({"key": s["key"], "label": s["label"],
                       "from": round(before, 4), "to": round(after, 4),
                       "change": round(after - before, 4)})
    if not movers:
        return None
    movers.sort(key=lambda m: abs(m["change"]), reverse=True)
    return {
        "biggest_mover": movers[0],
        "basis": "share of the first half of the window against the second",
        "total_change": (round((totals[-1] - totals[0]) / totals[0], 4)
                         if totals[0] else None),
        "others": movers[1:4],
    }
