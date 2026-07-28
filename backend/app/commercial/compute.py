"""Load → compute → persist Customer × Item metrics.

The only module in this package that touches the database. Everything it calls
is pure, so the analysis itself stays testable without one.

Scoping matters for cost. Recomputing one customer still needs *every*
customer's lines for the items that customer buys, because the peer benchmark is
defined across customers — but only for those items, not the whole catalogue.
That is the difference between a targeted recompute and a full scan.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from ..signals.base import CostRow, SaleRow
from .benchmark import ItemBenchmark, compute_benchmark, peer_margin_gap
from .config import CommercialThresholds, load_commercial_thresholds
from .economics import LineEconomics, line_economics
from .metrics import RelationshipMetrics, compute_relationship

log = logging.getLogger("pie_portal.commercial")

_ZERO = Decimal("0")


@dataclass
class RecomputeReport:
    """What a recompute did — enough to see cost coverage and spot a problem."""

    organization_id: str
    relationships: int = 0
    with_cost: int = 0
    missing_cost: int = 0
    signals_by_type: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    failures: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "organization_id": self.organization_id,
            "relationships": self.relationships,
            "relationships_with_cost": self.with_cost,
            "relationships_missing_cost": self.missing_cost,
            "signals_by_type": self.signals_by_type,
            "duration_seconds": round(self.duration_seconds, 3),
            "failures": self.failures,
        }


# ── loading ─────────────────────────────────────────────────────────────────
def _sale_rows(session: Session, org: str, *, customer_ids: Optional[set[str]] = None,
               product_ids: Optional[set[str]] = None) -> list[SaleRow]:
    stmt = select(models.SalesTxn).where(models.SalesTxn.organization_id == org)
    if customer_ids is not None:
        stmt = stmt.where(models.SalesTxn.customer_id.in_(list(customer_ids)))
    if product_ids is not None:
        stmt = stmt.where(models.SalesTxn.product_id.in_(list(product_ids)))
    return [
        SaleRow(customer_id=t.customer_id, product_id=t.product_id, date=t.date,
                qty=Decimal(t.qty), unit_price=Decimal(t.unit_price),
                line_revenue=Decimal(t.line_revenue), source_ref=t.source_ref or {},
                external_ref=t.external_ref,
                rate=(Decimal(t.rate) if t.rate is not None else None),
                discount_percent=(Decimal(t.discount_percent)
                                  if t.discount_percent is not None else None))
        for t in session.scalars(stmt)
    ]


def _costs_by_product(session: Session, org: str,
                      product_ids: Optional[set[str]] = None) -> dict[str, list[CostRow]]:
    stmt = select(models.CostRecord).where(models.CostRecord.organization_id == org)
    if product_ids is not None:
        stmt = stmt.where(models.CostRecord.product_id.in_(list(product_ids)))
    out: dict[str, list[CostRow]] = defaultdict(list)
    for c in session.scalars(stmt):
        out[c.product_id].append(CostRow(
            product_id=c.product_id, date=c.date, qty=Decimal(c.qty),
            unit_cost=Decimal(c.unit_cost), source_ref=c.source_ref or {},
            external_ref=c.external_ref))
    for rows in out.values():
        rows.sort(key=lambda r: r.date)      # cost_basis_asof requires ascending
    return out


def _evidence(lines: Iterable[LineEconomics]) -> list[dict]:
    """Source refs for the lines a conclusion rests on, newest first and
    bounded — a signal should be traceable, not carry a thousand refs."""
    refs: list[dict] = []
    for ln in sorted(lines, key=lambda x: x.date, reverse=True)[:20]:
        refs.append({"source_system": "zoho", "record_type": "invoice",
                     "record_id": ln.invoice_id, "line_id": ln.external_ref})
        if ln.cost_source_ref:
            refs.append({"source_system": "zoho", "record_type": "bill",
                         "record_id": ln.cost_source_ref.get("record_id"),
                         "line_id": ln.cost_source_ref.get("line_id")})
    return refs


# ── the computation ─────────────────────────────────────────────────────────
@dataclass
class ComputedRelationship:
    metrics: RelationshipMetrics
    benchmark: Optional[ItemBenchmark]
    lines: list[LineEconomics]


def compute_for(session: Session, org: str, *, customer_ids: Optional[set[str]] = None,
                as_of: Optional[date] = None,
                th: Optional[CommercialThresholds] = None
                ) -> tuple[list[ComputedRelationship], date]:
    """Compute (without persisting) every relationship in scope.

    ``customer_ids=None`` means the whole organization. Otherwise only those
    customers' relationships are returned — but peer benchmarks are still drawn
    from every customer buying the same items, because a benchmark restricted to
    the subject would be self-referential.
    """
    th = th or load_commercial_thresholds()

    subject_sales = _sale_rows(session, org, customer_ids=customer_ids)
    if not subject_sales:
        return [], as_of or date.today()

    # Only the items actually in scope need their full cross-customer history.
    product_ids = {s.product_id for s in subject_sales}
    peer_sales = (subject_sales if customer_ids is None
                  else _sale_rows(session, org, product_ids=product_ids))
    costs = _costs_by_product(session, org, product_ids=product_ids)

    reference = as_of or max((s.date for s in peer_sales), default=date.today())

    # Cost every line once, then index it two ways: by relationship (for the
    # metrics) and by product→customer (for the benchmark).
    by_pair: dict[tuple[str, str], list[LineEconomics]] = defaultdict(list)
    by_product: dict[str, dict[str, list[LineEconomics]]] = defaultdict(
        lambda: defaultdict(list))
    for sale in peer_sales:
        ln = line_economics(sale, costs.get(sale.product_id, []))
        by_pair[(ln.customer_id, ln.product_id)].append(ln)
        by_product[ln.product_id][ln.customer_id].append(ln)

    in_scope = {(s.customer_id, s.product_id) for s in subject_sales}
    benchmarks: dict[tuple[str, str], ItemBenchmark] = {}
    out: list[ComputedRelationship] = []

    for (customer_id, product_id) in sorted(in_scope):
        lines = by_pair[(customer_id, product_id)]
        m = compute_relationship(customer_id, product_id, lines, reference, th)
        bm = compute_benchmark(product_id, customer_id, by_product[product_id],
                               reference, th)
        benchmarks[(customer_id, product_id)] = bm
        out.append(ComputedRelationship(metrics=m, benchmark=bm, lines=lines))
    return out, reference


# ── persistence ─────────────────────────────────────────────────────────────
def _upsert(session: Session, org: str, c: ComputedRelationship,
            th: CommercialThresholds, signal_types: list[str]) -> models.CustomerItemMetric:
    m, bm = c.metrics, c.benchmark
    row = session.scalar(
        select(models.CustomerItemMetric).where(
            models.CustomerItemMetric.organization_id == org,
            models.CustomerItemMetric.customer_id == m.customer_id,
            models.CustomerItemMetric.product_id == m.product_id,
        ))
    if row is None:
        row = models.CustomerItemMetric(organization_id=org, customer_id=m.customer_id,
                                        product_id=m.product_id)
        session.add(row)

    row.first_transaction_date = m.first_transaction_date
    row.last_transaction_date = m.last_transaction_date
    row.transaction_count = m.transaction_count
    row.history_months = round(m.history_months, 2)

    row.revenue_recent = m.revenue_recent
    row.revenue_12m = m.revenue_12m
    row.gross_profit_recent = m.gross_profit_recent
    row.gross_profit_12m = m.gross_profit_12m
    row.current_sell_price = m.current_sell_price
    row.current_effective_cost = m.current_effective_cost

    row.current_margin = m.current_margin
    row.previous_margin = m.previous_margin
    row.margin_3m = m.margin_3m
    row.margin_6m = m.margin_6m
    row.margin_12m = m.margin_12m
    row.historical_margin = m.historical_margin

    row.margin_change_pp = m.margin_change_pp
    row.price_change_pct = m.price_change_pct
    row.cost_change_pct = m.cost_change_pct
    row.erosion_kind = m.erosion_kind

    row.same_item_median_price = bm.median_price if bm else None
    row.same_item_median_margin = bm.median_margin if bm else None
    row.price_deviation_pct = bm.price_deviation_pct if bm else None
    row.margin_deviation_pp = bm.margin_deviation_pp if bm else None
    row.peer_count = bm.peer_count if bm else 0

    row.qty_recent = m.qty_recent
    row.qty_previous = m.qty_previous
    row.volume_change_pct = m.volume_change_pct

    row.historical_margin_gap = m.historical_margin_gap
    row.peer_margin_gap = (peer_margin_gap(bm, m.revenue_recent, th) if bm else None)
    row.annualized_historical_margin_gap = m.annualized_historical_margin_gap

    row.signals = signal_types
    row.data_sufficiency = m.data_sufficiency.value
    row.sufficiency_reasons = list(m.sufficiency_reasons)
    row.cost_covered_txns = m.cost_covered_txns
    row.cost_missing_txns = m.cost_missing_txns

    row.thresholds_version = th.version
    row.computed_at = datetime.now(timezone.utc)
    return row


def recompute(session: Session, org: str, *, customer_ids: Optional[set[str]] = None,
              as_of: Optional[date] = None, emit_signals: bool = True,
              th: Optional[CommercialThresholds] = None) -> RecomputeReport:
    """Recompute metrics (and optionally signals) for an organization or a subset.

    Idempotent: metric rows are upserted on (org, customer, product), so running
    it twice produces the same rows. Source transactions are never touched.
    """
    from ..config import settings
    from ..repositories import SignalRepository
    from .detectors import detect

    started = time.monotonic()
    th = th or load_commercial_thresholds()
    report = RecomputeReport(organization_id=org)

    computed, _reference = compute_for(session, org, customer_ids=customer_ids,
                                       as_of=as_of, th=th)
    repo = SignalRepository(session, org)

    for c in computed:
        try:
            drafts = detect(c.metrics, c.benchmark, th, _evidence(c.lines)) \
                if emit_signals else []
            for d in drafts:
                d.detector_version = settings.DETECTOR_VERSION
                repo.add(d.to_model(org))
                report.signals_by_type[d.signal_type] = \
                    report.signals_by_type.get(d.signal_type, 0) + 1

            _upsert(session, org, c, th, [d.signal_type for d in drafts])
            report.relationships += 1
            if c.metrics.cost_covered_txns:
                report.with_cost += 1
            else:
                report.missing_cost += 1
        except Exception as e:  # noqa: BLE001 — one bad pair must not lose the run
            log.exception("customer-item recompute failed for %s/%s",
                          c.metrics.customer_id, c.metrics.product_id)
            report.failures.append({
                "customer_id": c.metrics.customer_id,
                "product_id": c.metrics.product_id,
                "error": f"{type(e).__name__}: {e}"[:300],
            })

    session.flush()
    report.duration_seconds = time.monotonic() - started
    # Counts and durations only — never a customer's actual prices or margins.
    log.info("commercial recompute org=%s relationships=%d with_cost=%d "
             "missing_cost=%d signals=%d in %.2fs",
             org, report.relationships, report.with_cost, report.missing_cost,
             sum(report.signals_by_type.values()), report.duration_seconds)
    return report
