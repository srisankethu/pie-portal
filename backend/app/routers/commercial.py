"""Customer × Item commercial intelligence API.

Three surfaces:

- the customer **portfolio** — which items are driving this account;
- the Customer × Item **drill-down** — margin history, cost vs price, peers,
  volume, and the transactions every conclusion rests on;
- **recompute** — rebuild the derived metrics from data already synced.

Every response here carries cost and margin, so the whole router is
manager-or-owner only. That is enforced once, at the dependency, rather than
field by field: there is no salesperson-safe projection of a margin analysis,
and pretending otherwise invites a leak.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..authz import Principal, require_manager_or_owner
from ..commercial.compute import compute_for, recompute
from ..commercial.policy import load_for_org
from ..commercial.diagnosis import diagnose
from ..commercial.economics import aggregate, in_window
from ..commercial.portfolio import items_requiring_attention, load_portfolio
from ..db import get_session
from ..domain import models

log = logging.getLogger("pie_portal.commercial.api")

router = APIRouter(prefix="/api/v1/commercial", tags=["commercial"])


def _money(v: Any) -> Optional[float]:
    return float(round(Decimal(str(v)), 2)) if v is not None else None


def _ratio(v: Optional[float]) -> Optional[float]:
    return round(v, 4) if v is not None else None


def _names(session: Session, org: str, product_ids: list[str]) -> dict[str, models.Product]:
    if not product_ids:
        return {}
    rows = session.scalars(
        select(models.Product).where(
            models.Product.organization_id == org,
            models.Product.product_id.in_(product_ids)))
    return {p.product_id: p for p in rows}


def _metric_row(r: models.CustomerItemMetric, product: Optional[models.Product]) -> dict:
    """One row of the "items requiring attention" table."""
    return {
        "product_id": r.product_id,
        "item_name": product.name if product else r.product_id,
        "item_code": product.external_id if product else None,
        "revenue_12m": _money(r.revenue_12m),
        "current_margin": _ratio(r.current_margin),
        "historical_margin": _ratio(r.historical_margin),
        "peer_median_margin": _ratio(r.same_item_median_margin),
        "peer_count": r.peer_count,
        "margin_change_pp": _ratio(r.margin_change_pp),
        "cost_change_pct": _ratio(r.cost_change_pct),
        "price_change_pct": _ratio(r.price_change_pct),
        "volume_change_pct": _ratio(r.volume_change_pct),
        "erosion_kind": r.erosion_kind,
        "historical_margin_gap": _money(r.historical_margin_gap),
        "peer_margin_gap": _money(r.peer_margin_gap),
        "annualized_historical_margin_gap": _money(r.annualized_historical_margin_gap),
        "signals": r.signals or [],
        "data_sufficiency": r.data_sufficiency,
        "sufficiency_reasons": r.sufficiency_reasons or [],
        "last_transaction_date": (r.last_transaction_date.isoformat()
                                  if r.last_transaction_date else None),
        "transaction_count": r.transaction_count,
    }


def _row_versions(rows: list) -> dict:
    """The `thresholds_version` a set of computed rows was judged under."""
    seen = sorted({r.thresholds_version for r in rows if r.thresholds_version})
    return {"thresholds_version": seen[0] if len(seen) == 1 else None,
            "thresholds_versions": seen if len(seen) > 1 else None}


@router.get("/customers/{customer_id}/portfolio")
def customer_portfolio(
    customer_id: str,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """Analysis 6 — this customer's commercial position, decomposed by item.

    Reads the derived metric rows, so the cost of this call does not grow with
    the organization's invoice history.
    """
    org = principal.organization_id
    customer = session.get(models.Customer, customer_id)
    if customer is None or customer.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")

    portfolio = load_portfolio(session, org, customer_id)
    flagged = items_requiring_attention(portfolio)
    products = _names(session, org, [r.product_id for r in portfolio.rows])

    return {
        "customer": {"customer_id": customer.customer_id, "name": customer.name},
        "summary": portfolio.to_dict(),
        # Already ranked by economic materiality — the client should not re-sort
        # by percentage and undo it.
        "items_requiring_attention": [
            _metric_row(r, products.get(r.product_id)) for r in flagged],
        "all_items": [
            _metric_row(r, products.get(r.product_id))
            for r in sorted(portfolio.rows,
                            key=lambda x: -(float(x.revenue_12m or 0)))],
        # Max over the datetimes, then serialise once — not max over ISO strings.
        # Lexicographic order happens to agree with chronological order only while
        # every string carries the same offset and the same precision, which is a
        # property of the serializer rather than of the data.
        "computed_at": clock.iso(max((r.computed_at for r in portfolio.rows),
                                     default=None)),
        # The stamp the rows actually carry, not the policy in force now. One
        # value when they agree; null when they do not, with the set named beside
        # it — a portfolio spanning two policy versions is itself worth seeing
        # rather than something to average away, and `recompute` is per-customer,
        # so it is reachable.
        **_row_versions(portfolio.rows),
    }


@router.get("/customers/{customer_id}/items/{product_id}")
def customer_item_detail(
    customer_id: str,
    product_id: str,
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    """The Customer × Item drill-down — every analysis, plus its evidence.

    Recomputed live for this one pair rather than read from the metric row: the
    drill-down needs the per-transaction series and peer table, which the
    summary row deliberately does not store. The scope is one customer, so the
    cost is bounded regardless of organization size.
    """
    org = principal.organization_id
    customer = session.get(models.Customer, customer_id)
    if customer is None or customer.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    product = session.get(models.Product, product_id)
    if product is None or product.organization_id != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found")

    th = load_for_org(session, org)
    computed, reference = compute_for(session, org, customer_ids={customer_id}, th=th)
    match = next((c for c in computed if c.metrics.product_id == product_id), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "This customer has no transactions for that item")

    m, bm, lines = match.metrics, match.benchmark, match.lines
    ordered = sorted(lines, key=lambda ln: ln.date)

    # Peer names, for the same-item table.
    peer_ids = [p.customer_id for p in (bm.peers if bm else [])]
    peer_names = {
        c.customer_id: c.name
        for c in session.scalars(
            select(models.Customer).where(
                models.Customer.organization_id == org,
                models.Customer.customer_id.in_(peer_ids or [""])))
    }

    return {
        "customer": {"customer_id": customer.customer_id, "name": customer.name},
        "item": {"product_id": product.product_id, "name": product.name,
                 "code": product.external_id, "uom": product.uom},
        "as_of": reference.isoformat(),
        # Which policy produced the numbers below. This is the screen a manager
        # argues a price from, and its floor references — TARGET_MARGIN_PRICE,
        # MARGIN_FLOOR_PRICE, MIN_MARGIN_PRICE — are all threshold-derived, so a
        # figure here could not be traced to the policy behind it without going
        # to the database.
        #
        # `th.version` is the honest stamp *here* precisely because this endpoint
        # recomputes the pair live under `th` rather than reading the stored row —
        # see the docstring. The portfolio below is the opposite case and reports
        # the stamp its rows carry.
        "thresholds_version": th.version,

        "headline": {
            "revenue_recent": _money(m.revenue_recent),
            "revenue_12m": _money(m.revenue_12m),
            "gross_profit_recent": _money(m.gross_profit_recent),
            "gross_profit_12m": _money(m.gross_profit_12m),
            "current_margin": _ratio(m.current_margin),
            "historical_margin": _ratio(m.historical_margin),
            "margin_change_pp": _ratio(m.margin_change_pp),
            "qty_recent": _money(m.qty_recent),
            "current_sell_price": _money(m.current_sell_price),
            "current_effective_cost": _money(m.current_effective_cost),
            "historical_margin_gap": _money(m.historical_margin_gap),
            "annualized_historical_margin_gap":
                _money(m.annualized_historical_margin_gap),
            "peer_median_margin": _ratio(bm.median_margin) if bm else None,
            "peer_count": bm.peer_count if bm else 0,
        },

        # A. every sentence rendered from a calculated value — no AI, no estimate
        "diagnosis": diagnose(m, bm, th),

        "data_quality": {
            "data_sufficiency": m.data_sufficiency.value,
            "reasons": m.sufficiency_reasons,
            "transaction_count": m.transaction_count,
            "cost_covered_txns": m.cost_covered_txns,
            "cost_missing_txns": m.cost_missing_txns,
            "history_months": round(m.history_months, 1),
        },

        # B + C. unit economics and margin through time, per transaction
        "series": [
            {
                "date": ln.date.isoformat(),
                "net_sell_price": _money(ln.net_unit_price),
                "effective_cost": _money(ln.effective_unit_cost),
                "margin": _ratio(ln.gross_margin),
                "qty": _money(ln.qty),
            }
            for ln in ordered
        ],
        "margin_periods": {
            "current": _ratio(m.current_margin),
            "previous": _ratio(m.previous_margin),
            "m3": _ratio(m.margin_3m),
            "m6": _ratio(m.margin_6m),
            "m12": _ratio(m.margin_12m),
            "historical": _ratio(m.historical_margin),
        },

        # D. the same item across other customers — a benchmark, not a mandate
        "peers": {
            "median_price": _money(bm.median_price) if bm else None,
            "median_margin": _ratio(bm.median_margin) if bm else None,
            "price_deviation_pct": _ratio(bm.price_deviation_pct) if bm else None,
            "margin_deviation_pp": _ratio(bm.margin_deviation_pp) if bm else None,
            "peer_count": bm.peer_count if bm else 0,
            "is_reliable": bm.is_reliable(th) if bm else False,
            "window_days": th.peer_recency_days,
            "rows": [
                {
                    "customer_id": p.customer_id,
                    "name": peer_names.get(p.customer_id, p.customer_id),
                    "net_sell_price": _money(p.net_unit_price),
                    "margin": _ratio(p.margin),
                    "qty": _money(p.qty),
                    "txn_count": p.txn_count,
                    "last_transaction_date": (p.last_transaction_date.isoformat()
                                              if p.last_transaction_date else None),
                    "is_subject": False,
                }
                for p in (bm.peers if bm else [])
            ],
            "subject": (
                {
                    "customer_id": bm.subject.customer_id,
                    "name": customer.name,
                    "net_sell_price": _money(bm.subject.net_unit_price),
                    "margin": _ratio(bm.subject.margin),
                    "qty": _money(bm.subject.qty),
                    "txn_count": bm.subject.txn_count,
                    "last_transaction_date": (
                        bm.subject.last_transaction_date.isoformat()
                        if bm.subject.last_transaction_date else None),
                    "is_subject": True,
                }
                if bm and bm.subject else None),
        },

        # E. volume against margin, period by period
        "volume_vs_margin": _volume_periods(ordered, reference),

        # F. the underlying evidence — every conclusion above is an aggregate
        #    of exactly these rows
        "transactions": [
            {
                "date": ln.date.isoformat(),
                "invoice_id": ln.invoice_id,
                "external_ref": ln.external_ref,
                "qty": _money(ln.qty),
                "rate": _money(ln.rate),
                "discount_percent": _money(ln.discount_percent),
                "net_sell_price": _money(ln.net_unit_price),
                "effective_cost": _money(ln.effective_unit_cost),
                "revenue": _money(ln.revenue),
                "cogs": _money(ln.cogs),
                "gross_profit": _money(ln.gross_profit),
                "margin": _ratio(ln.gross_margin),
                "cost_source": (ln.cost_source_ref or {}).get("record_id"),
            }
            for ln in reversed(ordered)
        ],
    }


def _volume_periods(lines, reference: date, months: int = 6) -> list[dict]:
    """Quantity, revenue and margin per 30-day period — the shape that shows
    whether a lower margin actually bought additional business."""
    out = []
    for i in range(months - 1, -1, -1):
        end = reference - timedelta(days=30 * i)
        start = end - timedelta(days=30)
        period = aggregate(in_window(lines, start, end))
        out.append({
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "qty": _money(period.qty),
            "revenue": _money(period.revenue),
            "margin": _ratio(period.margin),
            "txn_count": period.txn_count,
        })
    return out


class RecomputeRequest(BaseModel):
    """Rebuild derived metrics from data already in the database.

    ``customer_id`` scopes it to one account; omitting it rebuilds the whole
    organization. Never re-pulls from Zoho — the source rows are already here.
    """

    customer_id: Optional[str] = None
    emit_signals: bool = True


@router.post("/recompute")
def run_recompute(
    req: RecomputeRequest = Body(default_factory=RecomputeRequest),
    principal: Principal = Depends(require_manager_or_owner),
    session: Session = Depends(get_session),
) -> dict:
    report = recompute(
        session, principal.organization_id,
        customer_ids={req.customer_id} if req.customer_id else None,
        emit_signals=req.emit_signals,
    )
    return report.to_dict()
