"""The database seam for quote intelligence: load once, assess many, record.

A quote is not one line. An RFQ of forty items assessed line by line is forty
snapshot loads, forty benchmark passes and forty cost queries — the classic N+1
that makes a feature feel fine in a demo and unusable on a real RFQ. So this
module does the opposite: **one** pass over the customer's sales history, **one**
pass over the peer history for exactly the items on the quote, **one** cost load,
and then N pure assessments over in-memory structures.

Everything commercial it computes it computes by calling the same functions the
Customer × Item analysis screen calls. It adds no analysis of its own.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from ..domain.enums import (
    QUOTE_OUTCOME_TRANSITIONS,
    EvidenceSufficiency,
    QuoteOutcomeStatus,
    Role,
)
from ..signals.base import CostRow, SaleRow
from .benchmark import ItemBenchmark, compute_benchmark
from .config import CommercialThresholds, load_commercial_thresholds
from .economics import LineEconomics, line_economics
from .metrics import RelationshipMetrics, compute_relationship
from .quote_exceptions import CRITICAL, RESTRICTED
from .quote_intelligence import QuoteLineIntelligence, assess_line

log = logging.getLogger("pie_portal.commercial.quote")

# Bumped when the deterministic assessment changes shape, so a persisted
# snapshot always says which engine produced it.
ENGINE_VERSION = "qi-1"

_ZERO = Decimal("0")


@dataclass
class QuoteLineInput:
    """One line as the Quote Builder holds it, before resolution."""

    line_id: str
    product_ref: str
    qty: Decimal = Decimal("1")
    proposed_price: Optional[Decimal] = None
    family: Optional[str] = None


@dataclass
class QuoteAssessment:
    """The result for a whole quote — resolution, per-line intelligence, totals."""

    customer_id: Optional[str]
    customer_label: Optional[str]
    as_of: date
    lines: list[QuoteLineIntelligence] = field(default_factory=list)
    unresolved: dict[str, str] = field(default_factory=dict)   # line_id → ref
    product_ids: dict[str, str] = field(default_factory=dict)  # line_id → product_id
    thresholds_version: str = ""


# ── resolution ──────────────────────────────────────────────────────────────
def resolve_customer(session: Session, org: str, ref: str) -> Optional[models.Customer]:
    """Reuses the Quote Builder's existing tolerant matcher — one behaviour."""
    from ..decisions.quote_support import _resolve_customer
    return _resolve_customer(session, org, ref)


def _resolve_products(session: Session, org: str,
                      refs: Iterable[str]) -> dict[str, Optional[models.Product]]:
    """Resolve every distinct product ref in one pass over the catalogue.

    ``_resolve_product`` loads the whole product table per call, which is
    exactly the N+1 this module exists to avoid on a forty-line RFQ.
    """
    from ..decisions.quote_support import _norm

    rows = list(session.scalars(
        select(models.Product).where(models.Product.organization_id == org)))
    by_id: dict[str, models.Product] = {}
    by_ext: dict[str, models.Product] = {}
    by_norm: dict[str, models.Product] = {}
    for p in rows:
        by_id[p.product_id] = p
        if p.external_id:
            by_ext[p.external_id] = p
        by_norm.setdefault(_norm(p.name), p)

    out: dict[str, Optional[models.Product]] = {}
    for ref in refs:
        ref = (ref or "").strip()
        if not ref:
            out[ref] = None
            continue
        match = by_id.get(ref) or by_ext.get(ref)
        if match is None:
            n = _norm(ref)
            match = by_norm.get(n)
            if match is None and len(n) >= 4:
                # Normalized containment either way ("CNMG 120408-MP" vs
                # "CNMG 120408-MP insert"), closest length wins.
                best = None
                for pn, p in by_norm.items():
                    if pn and (n in pn or pn in n):
                        if best is None or abs(len(pn) - len(n)) < abs(
                                len(_norm(best.name)) - len(n)):
                            best = p
                match = best
        out[ref] = match
    return out


# ── the batched assessment ──────────────────────────────────────────────────
def assess_quote(
    session: Session, org: str, *,
    customer_ref: str,
    lines: list[QuoteLineInput],
    as_of: Optional[date] = None,
    th: Optional[CommercialThresholds] = None,
) -> QuoteAssessment:
    """Assess every line of a quote with a bounded number of queries.

    Query count is constant in the number of lines: products (1), the customer's
    sales (1), peer sales for the quoted items (1), costs for those items (1).
    """
    th = th or load_commercial_thresholds()
    customer = resolve_customer(session, org, customer_ref)
    resolved = _resolve_products(session, org, {ln.product_ref for ln in lines})

    product_ids = {p.product_id for p in resolved.values() if p is not None}
    reference = as_of or date.today()

    result = QuoteAssessment(
        customer_id=customer.customer_id if customer else None,
        customer_label=customer.name if customer else None,
        as_of=reference, thresholds_version=th.version)

    # ── one load for the whole quote ─────────────────────────────────────────
    peer_sales = _sales_for_products(session, org, product_ids) if product_ids else []
    costs = _costs_for_products(session, org, product_ids) if product_ids else {}

    by_pair: dict[tuple[str, str], list[LineEconomics]] = defaultdict(list)
    by_product: dict[str, dict[str, list[LineEconomics]]] = defaultdict(
        lambda: defaultdict(list))
    for sale in peer_sales:
        ln = line_economics(sale, costs.get(sale.product_id, []))
        by_pair[(ln.customer_id, ln.product_id)].append(ln)
        by_product[ln.product_id][ln.customer_id].append(ln)

    # Metrics and benchmarks are per (customer, product), not per line — two
    # lines quoting the same item at different quantities share both.
    metric_cache: dict[str, RelationshipMetrics] = {}
    benchmark_cache: dict[str, ItemBenchmark] = {}

    for ln in lines:
        product = resolved.get(ln.product_ref.strip())
        if product is None:
            result.unresolved[ln.line_id] = ln.product_ref
        pid = product.product_id if product is not None else None
        if pid:
            result.product_ids[ln.line_id] = pid

        history: list[LineEconomics] = []
        metrics: Optional[RelationshipMetrics] = None
        benchmark: Optional[ItemBenchmark] = None
        if pid and customer is not None:
            history = by_pair.get((customer.customer_id, pid), [])
            if pid not in metric_cache:
                metric_cache[pid] = compute_relationship(
                    customer.customer_id, pid, history, reference, th)
                benchmark_cache[pid] = compute_benchmark(
                    pid, customer.customer_id, by_product.get(pid, {}), reference, th)
            metrics = metric_cache[pid]
            benchmark = benchmark_cache[pid]

        result.lines.append(assess_line(
            line_id=ln.line_id,
            customer_id=result.customer_id,
            product_id=pid,
            qty=ln.qty if ln.qty > _ZERO else Decimal("1"),
            proposed_price=ln.proposed_price,
            lines=history,
            costs=costs.get(pid, []) if pid else [],
            metrics=metrics,
            benchmark=benchmark,
            family=ln.family,
            as_of=reference,
            th=th))
    return result


def _sales_for_products(session: Session, org: str,
                        product_ids: set[str]) -> list[SaleRow]:
    stmt = select(models.SalesTxn).where(
        models.SalesTxn.organization_id == org,
        models.SalesTxn.product_id.in_(list(product_ids)))
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


def _costs_for_products(session: Session, org: str,
                        product_ids: set[str]) -> dict[str, list[CostRow]]:
    stmt = select(models.CostRecord).where(
        models.CostRecord.organization_id == org,
        models.CostRecord.product_id.in_(list(product_ids)))
    out: dict[str, list[CostRow]] = defaultdict(list)
    for c in session.scalars(stmt):
        out[c.product_id].append(CostRow(
            product_id=c.product_id, date=c.date, qty=Decimal(c.qty),
            unit_cost=Decimal(c.unit_cost), source_ref=c.source_ref or {},
            external_ref=c.external_ref))
    for rows in out.values():
        rows.sort(key=lambda r: r.date)     # cost_basis_asof requires ascending
    return out


# ── role projection ─────────────────────────────────────────────────────────
def project(intel: QuoteLineIntelligence, role: Role, *,
            product_ref: str = "", unresolved: bool = False) -> dict:
    """Serialize one line's intelligence for a recipient.

    For a salesperson, cost, margin and every reference or impact derived from
    them are **absent** — not zeroed, not masked, not rounded away. What remains
    is what they may act on: prices this customer has actually paid, the rules
    that fired, and whether approval is needed. A salesperson can still see that
    a price is below the floor; they cannot see how far below cost it sits.
    """
    is_sales = role is Role.SALESPERSON

    references = [r.to_dict() for r in intel.references
                  if not (is_sales and r.data_class == RESTRICTED)]
    withheld = [r.label for r in intel.references
                if is_sales and r.data_class == RESTRICTED]

    exceptions = []
    for e in intel.exceptions:
        d = e.to_dict()
        if is_sales:
            d.pop("manager_detail", None)
            d.pop("inputs", None)
            if e.impact_data_class == RESTRICTED:
                d["impact_rupees"] = None
        exceptions.append(d)

    out: dict[str, Any] = {
        "line_id": intel.line_id,
        "product_id": intel.product_id,
        "product_ref": product_ref,
        "resolved": not unresolved,
        "qty": float(intel.qty),
        "quantity_band": intel.band.to_dict(),
        "as_of": intel.as_of.isoformat(),
        "references": references,
        "references_withheld": withheld,
        "exceptions": exceptions,
        "worst_severity": intel.worst_severity,
        "requires_approval": intel.requires_approval,
        "blocking": intel.blocking,
        "data_quality": {
            "data_sufficiency": intel.data_sufficiency.value,
            "reasons": intel.sufficiency_reasons,
            "transaction_count": intel.transaction_count,
        },
        "thresholds_version": intel.thresholds_version,
        "engine_version": ENGINE_VERSION,
    }
    if not is_sales:
        out["economics"] = intel.economics.to_dict() if intel.economics else None
        out["position"] = _position(intel)
        # The drill-down target: the existing Customer × Item analysis screen,
        # so the quote screen never re-explains what that screen already says.
        out["drilldown"] = ({"customer_id": intel.customer_id,
                             "product_id": intel.product_id}
                            if intel.customer_id and intel.product_id else None)
    return out


def _position(intel: QuoteLineIntelligence) -> Optional[dict]:
    """The relationship's standing, for a manager — the same numbers the
    Customer × Item screen shows, not a second calculation of them."""
    m = intel.metrics
    if m is None:
        return None
    return {
        "current_margin": round(m.current_margin, 4) if m.current_margin is not None else None,
        "historical_margin": (round(m.historical_margin, 4)
                              if m.historical_margin is not None else None),
        "margin_change_pp": (round(m.margin_change_pp, 4)
                             if m.margin_change_pp is not None else None),
        "cost_change_pct": (round(m.cost_change_pct, 4)
                            if m.cost_change_pct is not None else None),
        "price_change_pct": (round(m.price_change_pct, 4)
                             if m.price_change_pct is not None else None),
        "erosion_kind": m.erosion_kind,
        "peer_count": intel.benchmark.peer_count if intel.benchmark else 0,
        "transaction_count": m.transaction_count,
    }


def summarize(assessment: QuoteAssessment, role: Role) -> dict:
    """Quote-level totals — counts and approval state only.

    Deliberately no aggregate margin or rupee total here for any role: a single
    blended margin across a mixed quote is the number people quote back at each
    other while the loss-making line stays invisible. The per-line view is the
    honest one.
    """
    exceptions = [e for ln in assessment.lines for e in ln.exceptions]
    return {
        "lines_assessed": len(assessment.lines),
        "lines_unresolved": len(assessment.unresolved),
        "exceptions_total": len(exceptions),
        "critical": sum(1 for e in exceptions if e.severity == CRITICAL),
        "requires_approval": sum(1 for ln in assessment.lines if ln.requires_approval),
        "insufficient_data": sum(
            1 for ln in assessment.lines
            if ln.data_sufficiency is EvidenceSufficiency.INSUFFICIENT),
    }


# ── immutable snapshots ─────────────────────────────────────────────────────
def record_snapshot(
    session: Session, org: str, *,
    quote_id: str,
    intel: QuoteLineIntelligence,
    customer_ref: str,
    product_ref: str,
    user_id: Optional[str],
    override_reason: Optional[str] = None,
    override_reason_code: Optional[str] = None,
) -> models.QuoteDecision:
    """Write one immutable quote decision row. Never updates an existing one.

    A second call for the same line is a second decision, not a correction —
    that is how a price that moved three times during a negotiation stays
    legible afterwards.
    """
    overridden_codes = [e.code for e in intel.exceptions
                        if e.requires_approval or e.severity == CRITICAL]
    econ = intel.economics
    row = models.QuoteDecision(
        organization_id=org,
        quote_id=quote_id,
        quote_line_id=intel.line_id,
        customer_id=intel.customer_id,
        product_id=intel.product_id,
        customer_ref=customer_ref[:255],
        product_ref=product_ref[:255],
        quantity=intel.qty,
        quantity_band=intel.band.label,
        quoted_unit_price=(econ.quoted_unit_price if econ else None),
        unit_cost=(econ.unit_cost if econ else None),
        line_revenue=(econ.line_revenue if econ else None),
        cogs=(econ.cogs if econ else None),
        gross_profit=(econ.gross_profit if econ else None),
        margin=(econ.margin if econ else None),
        references=[r.to_dict() for r in intel.references],
        exceptions=[e.to_dict() for e in intel.exceptions],
        relationship_metrics=_metrics_snapshot(intel),
        evidence_refs=_evidence_refs(intel),
        data_sufficiency=intel.data_sufficiency.value,
        sufficiency_reasons=list(intel.sufficiency_reasons),
        requires_approval=intel.requires_approval,
        overridden=bool(override_reason or override_reason_code),
        override_reason=(override_reason or None),
        override_reason_code=(override_reason_code or None),
        overridden_exception_codes=overridden_codes,
        thresholds_version=intel.thresholds_version,
        engine_version=ENGINE_VERSION,
        as_of=intel.as_of,
        created_by_user_id=user_id,
    )
    session.add(row)
    session.flush()
    return row


def _metrics_snapshot(intel: QuoteLineIntelligence) -> dict:
    m = intel.metrics
    if m is None:
        return {}
    return {
        "transaction_count": m.transaction_count,
        "history_months": round(m.history_months, 2),
        "current_margin": m.current_margin,
        "historical_margin": m.historical_margin,
        "margin_change_pp": m.margin_change_pp,
        "cost_change_pct": m.cost_change_pct,
        "price_change_pct": m.price_change_pct,
        "erosion_kind": m.erosion_kind,
        "peer_count": intel.benchmark.peer_count if intel.benchmark else 0,
        "peer_median_price": (float(intel.benchmark.median_price)
                              if intel.benchmark and intel.benchmark.median_price
                              else None),
    }


def _evidence_refs(intel: QuoteLineIntelligence) -> list[dict]:
    """The cost record this line was priced against — the one source ref a
    quote decision genuinely rests on."""
    econ = intel.economics
    if econ is None or not econ.cost_source_ref:
        return []
    ref = econ.cost_source_ref
    return [{"source_system": "zoho", "record_type": "bill",
             "record_id": ref.get("record_id"), "line_id": ref.get("line_id")}]


def snapshots_for_quote(session: Session, org: str, quote_id: str) -> list[models.QuoteDecision]:
    return list(session.scalars(
        select(models.QuoteDecision)
        .where(models.QuoteDecision.organization_id == org,
               models.QuoteDecision.quote_id == quote_id)
        .order_by(models.QuoteDecision.created_at)))


def snapshot_to_dict(row: models.QuoteDecision, role: Role) -> dict:
    """Read a snapshot back. Economics stay RESTRICTED on the way out too —
    a stored margin is still a margin."""
    is_sales = role is Role.SALESPERSON
    exceptions = []
    for e in row.exceptions or []:
        e = dict(e)
        if is_sales:
            e.pop("manager_detail", None)
            e.pop("inputs", None)
            if e.get("impact_data_class") == RESTRICTED:
                e["impact_rupees"] = None
        exceptions.append(e)

    out: dict[str, Any] = {
        "quote_decision_id": row.quote_decision_id,
        "quote_id": row.quote_id,
        "quote_line_id": row.quote_line_id,
        "customer_id": row.customer_id,
        "product_id": row.product_id,
        "product_ref": row.product_ref,
        "quantity": float(row.quantity) if row.quantity is not None else None,
        "quantity_band": row.quantity_band,
        "quoted_unit_price": (float(row.quoted_unit_price)
                              if row.quoted_unit_price is not None else None),
        "references": [r for r in (row.references or [])
                       if not (is_sales and r.get("data_class") == RESTRICTED)],
        "exceptions": exceptions,
        "data_sufficiency": row.data_sufficiency,
        "sufficiency_reasons": row.sufficiency_reasons or [],
        "requires_approval": row.requires_approval,
        "overridden": row.overridden,
        "override_reason": row.override_reason,
        "override_reason_code": row.override_reason_code,
        "overridden_exception_codes": row.overridden_exception_codes or [],
        "thresholds_version": row.thresholds_version,
        "engine_version": row.engine_version,
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by_user_id": row.created_by_user_id,
    }
    if not is_sales:
        out["economics"] = {
            "unit_cost": float(row.unit_cost) if row.unit_cost is not None else None,
            "line_revenue": (float(row.line_revenue)
                             if row.line_revenue is not None else None),
            "cogs": float(row.cogs) if row.cogs is not None else None,
            "gross_profit": (float(row.gross_profit)
                             if row.gross_profit is not None else None),
            "margin": row.margin,
        }
        out["relationship_metrics"] = row.relationship_metrics or {}
        out["evidence_refs"] = row.evidence_refs or []
    return out


# ── outcome path ────────────────────────────────────────────────────────────
class InvalidTransition(ValueError):
    """A quote outcome change the lifecycle does not permit."""


def set_outcome(session: Session, org: str, *, quote_id: str,
                status: QuoteOutcomeStatus, customer_ref: str = "",
                customer_id: Optional[str] = None, note: Optional[str] = None,
                user_id: Optional[str] = None) -> models.QuoteOutcome:
    """Move a quote along DRAFT → SENT → WON/LOST.

    Won and lost are terminal. Reopening a decided quote would rewrite history a
    margin analysis has already counted, so it is refused rather than silently
    allowed.
    """
    row = session.scalar(
        select(models.QuoteOutcome).where(
            models.QuoteOutcome.organization_id == org,
            models.QuoteOutcome.quote_id == quote_id))

    if row is None:
        row = models.QuoteOutcome(
            organization_id=org, quote_id=quote_id,
            customer_ref=customer_ref[:255], customer_id=customer_id,
            status=QuoteOutcomeStatus.DRAFT.value)
        session.add(row)
        session.flush()

    current = QuoteOutcomeStatus(row.status)
    if status is not current and status not in QUOTE_OUTCOME_TRANSITIONS[current]:
        raise InvalidTransition(
            f"A quote that is {current.value} cannot become {status.value}")

    now = datetime.now(timezone.utc)
    row.status = status.value
    if status is QuoteOutcomeStatus.SENT:
        row.sent_at = row.sent_at or now
    if status in (QuoteOutcomeStatus.WON, QuoteOutcomeStatus.LOST):
        row.decided_at = now
    if note is not None:
        row.note = note[:1024]
    if customer_ref:
        row.customer_ref = customer_ref[:255]
    if customer_id:
        row.customer_id = customer_id
    row.updated_by_user_id = user_id
    row.updated_at = now
    session.flush()
    return row


def outcome_to_dict(row: Optional[models.QuoteOutcome]) -> Optional[dict]:
    if row is None:
        return None
    return {
        "quote_id": row.quote_id,
        "status": row.status,
        "note": row.note,
        "customer_ref": row.customer_ref,
        "customer_id": row.customer_id,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "allowed_next": sorted(
            s.value for s in QUOTE_OUTCOME_TRANSITIONS[QuoteOutcomeStatus(row.status)]),
    }


def get_outcome(session: Session, org: str, quote_id: str) -> Optional[models.QuoteOutcome]:
    return session.scalar(
        select(models.QuoteOutcome).where(
            models.QuoteOutcome.organization_id == org,
            models.QuoteOutcome.quote_id == quote_id))
