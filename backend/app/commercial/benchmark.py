"""Analysis 3 — the same item across other customers.

Where does this customer sit against everyone else buying the same item?

**Median, not mean.** Commercial pricing contains outliers by nature — one
distress deal, one tiny sample, one legacy contract — and a mean lets any of
them move the benchmark. The median does not.

**The selected customer is excluded from its own benchmark.** Comparing a
customer against a population it is part of pulls the benchmark toward the
customer and understates every deviation; on a two-customer item it would be
nearly self-referential.

**This is a benchmark, not a mandate.** Another customer's price is evidence
about the market, not proof that this one is wrong. Volume commitments,
freight, payment terms and relationship history are all outside this data, and
the language everywhere says so.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from .config import CommercialThresholds
from .economics import LineEconomics, aggregate, in_window

_ZERO = Decimal("0")


@dataclass(frozen=True)
class PeerPosition:
    """One customer's position on an item, over the benchmark window."""

    customer_id: str
    net_unit_price: Optional[Decimal]
    margin: Optional[float]
    qty: Decimal
    revenue: Decimal
    txn_count: int
    last_transaction_date: Optional[date]


@dataclass
class ItemBenchmark:
    """The same-item peer picture for one selected customer."""

    product_id: str
    subject_customer_id: str
    peers: list[PeerPosition] = field(default_factory=list)
    median_price: Optional[Decimal] = None
    median_margin: Optional[float] = None
    subject: Optional[PeerPosition] = None
    price_deviation_pct: Optional[float] = None
    margin_deviation_pp: Optional[float] = None

    @property
    def peer_count(self) -> int:
        return len(self.peers)

    def is_reliable(self, th: CommercialThresholds) -> bool:
        """Enough *other* customers to mean something. Two customers buying an
        item is an anecdote, not a market."""
        return self.peer_count >= th.min_peer_customers


def _position(customer_id: str, lines: list[LineEconomics]) -> PeerPosition:
    period = aggregate(lines)
    qty = period.qty
    price = (period.revenue / qty) if qty > _ZERO else None
    return PeerPosition(
        customer_id=customer_id,
        net_unit_price=price,
        margin=period.margin,
        qty=qty,
        revenue=period.revenue,
        txn_count=period.txn_count,
        last_transaction_date=max((ln.date for ln in lines), default=None),
    )


def compute_benchmark(product_id: str, subject_customer_id: str,
                      lines_by_customer: dict[str, list[LineEconomics]],
                      as_of: date, th: CommercialThresholds) -> ItemBenchmark:
    """Position the subject customer against every other customer on this item.

    ``lines_by_customer`` is every customer's costed lines for this product,
    including the subject's. Peers are summarised over the recency window, so a
    customer who stopped buying two years ago is not evidence about today's
    market.
    """
    bm = ItemBenchmark(product_id=product_id, subject_customer_id=subject_customer_id)
    window_start = as_of - timedelta(days=th.peer_recency_days)

    for customer_id, lines in lines_by_customer.items():
        recent = in_window(lines, window_start, as_of)
        if not recent:
            continue                      # stale: no purchases inside the window
        position = _position(customer_id, recent)
        if customer_id == subject_customer_id:
            bm.subject = position
        else:
            bm.peers.append(position)

    bm.peers.sort(key=lambda p: p.customer_id)     # deterministic ordering

    prices = [p.net_unit_price for p in bm.peers if p.net_unit_price is not None]
    margins = [p.margin for p in bm.peers if p.margin is not None]
    if prices:
        bm.median_price = median_decimal(prices)
    if margins:
        bm.median_margin = statistics.median(margins)

    if bm.subject is not None:
        if bm.subject.net_unit_price is not None and bm.median_price:
            bm.price_deviation_pct = float(
                (bm.subject.net_unit_price - bm.median_price) / bm.median_price)
        if bm.subject.margin is not None and bm.median_margin is not None:
            bm.margin_deviation_pp = bm.subject.margin - bm.median_margin
    return bm


def median_decimal(values: list[Decimal]) -> Decimal:
    """Median without leaving Decimal — ``statistics.median`` would average the
    middle pair in float and reintroduce binary noise into a money value.

    Public because ``insight/outcomes`` needs exactly this when it compares the
    price on a lost quote against the price that won. It was private until a
    second caller appeared; a second copy would have been a second rounding
    behaviour for the same kind of number.
    """
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def peer_margin_gap(benchmark: ItemBenchmark, revenue_recent: Decimal,
                    th: CommercialThresholds) -> Optional[Decimal]:
    """What the relationship would earn at the peer benchmark margin.

    Deliberately separate from the historical gap: the historical gap assumes
    the relationship's own past margin was sustainable, this one assumes the
    customer should trade like the median of its peers. Neither is recoverable
    profit, and they are never added together.

    Only computed when the peer population is large enough to be meaningful,
    and only when the subject is actually *below* the benchmark.
    """
    if not benchmark.is_reliable(th):
        return None
    if benchmark.margin_deviation_pp is None or benchmark.margin_deviation_pp >= 0:
        return None
    if revenue_recent <= _ZERO:
        return None
    return revenue_recent * Decimal(str(-benchmark.margin_deviation_pp))
