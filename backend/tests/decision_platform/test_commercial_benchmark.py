"""Analysis 3 — the same item across other customers.

The failures that would matter: including the subject in its own benchmark
(which pulls the benchmark toward the subject and understates every deviation),
letting a mean be dragged by one outlier, or asserting a benchmark from two
customers.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.commercial.benchmark import compute_benchmark, peer_margin_gap
from app.commercial.config import CommercialThresholds
from app.commercial.economics import line_economics
from app.signals.base import CostRow, SaleRow

AS_OF = date(2026, 7, 1)
TH = CommercialThresholds()


def _lines(customer, prices, cost=100, qty=10, days=(60, 30)):
    """One costed line per (price, day) for a customer."""
    out = []
    for price, day in zip(prices, days):
        when = AS_OF - timedelta(days=day)
        q, p = Decimal(str(qty)), Decimal(str(price))
        sale = SaleRow(customer_id=customer, product_id="p1", date=when, qty=q,
                       unit_price=p, line_revenue=q * p,
                       source_ref={"record_type": "invoice", "record_id": f"I{customer}{day}"},
                       external_ref=f"I{customer}{day}:1")
        costs = [CostRow(product_id="p1", date=when - timedelta(days=1),
                         qty=Decimal("1"), unit_cost=Decimal(str(cost)),
                         source_ref={"record_id": "B"}, external_ref="B:1")]
        out.append(line_economics(sale, costs))
    return out


def test_the_subject_is_excluded_from_its_own_benchmark():
    """Including it would pull the benchmark toward the subject and understate
    every deviation — on a small population, nearly self-referential."""
    by_customer = {
        "c1": _lines("c1", [120, 120]),        # subject, priced low
        "c2": _lines("c2", [200, 200]),
        "c3": _lines("c3", [200, 200]),
        "c4": _lines("c4", [200, 200]),
    }
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)

    assert bm.peer_count == 3, "three peers, not four"
    assert "c1" not in [p.customer_id for p in bm.peers]
    assert bm.median_price == Decimal("200"), "the subject's 120 must not drag it"
    assert bm.subject is not None and bm.subject.customer_id == "c1"


def test_median_not_mean_resists_an_outlier():
    """One distress deal must not move the benchmark. The mean here would be
    ~157; the median is 150."""
    by_customer = {
        "c1": _lines("c1", [150, 150]),
        "c2": _lines("c2", [148, 148]),
        "c3": _lines("c3", [150, 150]),
        "c4": _lines("c4", [300, 300]),        # the outlier
    }
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    assert bm.median_price == Decimal("150")
    mean = sum(float(p.net_unit_price) for p in bm.peers) / bm.peer_count
    assert mean > 190, "a mean would have been dragged well above the median"


def test_median_of_an_even_population_averages_the_middle_pair_in_decimal():
    by_customer = {
        "c1": _lines("c1", [100, 100]),
        "c2": _lines("c2", [140, 140]),
        "c3": _lines("c3", [160, 160]),
    }
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    assert bm.peer_count == 2
    assert bm.median_price == Decimal("150"), "exactly (140+160)/2, in Decimal"


def test_a_thin_peer_population_is_not_reliable():
    """Two customers buying an item is an anecdote, not a market."""
    by_customer = {"c1": _lines("c1", [150, 150]), "c2": _lines("c2", [200, 200])}
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    assert bm.peer_count == 1
    assert bm.is_reliable(TH) is False


def test_an_adequate_peer_population_is_reliable():
    by_customer = {f"c{i}": _lines(f"c{i}", [150, 150]) for i in range(1, 6)}
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    assert bm.peer_count == 4 and bm.is_reliable(TH) is True


def test_stale_peers_are_excluded_from_the_benchmark():
    """A customer who stopped buying two years ago is not evidence about today's
    market."""
    by_customer = {
        "c1": _lines("c1", [150, 150]),
        "c2": _lines("c2", [200, 200], days=(900, 880)),    # stale
        "c3": _lines("c3", [200, 200]),
    }
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    assert [p.customer_id for p in bm.peers] == ["c3"]


def test_deviation_is_reported_as_price_percent_and_margin_points():
    by_customer = {
        "c1": _lines("c1", [150, 150], cost=100),   # 33.3% margin
        "c2": _lines("c2", [200, 200], cost=100),   # 50%
        "c3": _lines("c3", [200, 200], cost=100),
        "c4": _lines("c4", [200, 200], cost=100),
    }
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)

    assert abs(bm.price_deviation_pct - (-0.25)) < 1e-6, "150 vs 200 is -25%"
    assert abs(bm.margin_deviation_pp - (0.3333 - 0.5)) < 1e-3, "in POINTS"


def test_peer_positions_carry_volume_recency_and_transaction_count():
    """A peer must be inspectable — a stale or trivial one has to be visible as
    such rather than silently counted as equal evidence."""
    by_customer = {"c1": _lines("c1", [150, 150]), "c2": _lines("c2", [200, 200])}
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    peer = bm.peers[0]
    assert peer.qty == Decimal("20") and peer.txn_count == 2
    assert peer.last_transaction_date == AS_OF - timedelta(days=30)
    assert peer.revenue == Decimal("4000")


def test_peer_ordering_is_deterministic():
    by_customer = {f"c{i}": _lines(f"c{i}", [150, 150]) for i in (5, 2, 9, 1)}
    ids = [p.customer_id for p in
           compute_benchmark("p1", "c1", by_customer, AS_OF, TH).peers]
    assert ids == sorted(ids)


# ── the peer gap ────────────────────────────────────────────────────────────
def test_peer_gap_is_withheld_without_a_reliable_population():
    by_customer = {"c1": _lines("c1", [150, 150]), "c2": _lines("c2", [200, 200])}
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    assert peer_margin_gap(bm, Decimal("100000"), TH) is None


def test_peer_gap_is_revenue_times_the_margin_shortfall():
    by_customer = {
        "c1": _lines("c1", [150, 150], cost=100),
        "c2": _lines("c2", [200, 200], cost=100),
        "c3": _lines("c3", [200, 200], cost=100),
        "c4": _lines("c4", [200, 200], cost=100),
    }
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    gap = peer_margin_gap(bm, Decimal("100000"), TH)
    # ~16.7 points below the peer median, on ₹1,00,000 of revenue
    assert abs(float(gap) - 16_667) < 50


def test_no_peer_gap_when_the_customer_is_above_the_benchmark():
    """A customer priced better than its peers has no gap — the figure must not
    go negative and read as a shortfall."""
    by_customer = {
        "c1": _lines("c1", [250, 250], cost=100),
        "c2": _lines("c2", [200, 200], cost=100),
        "c3": _lines("c3", [200, 200], cost=100),
        "c4": _lines("c4", [200, 200], cost=100),
    }
    bm = compute_benchmark("p1", "c1", by_customer, AS_OF, TH)
    assert bm.margin_deviation_pp > 0
    assert peer_margin_gap(bm, Decimal("100000"), TH) is None
