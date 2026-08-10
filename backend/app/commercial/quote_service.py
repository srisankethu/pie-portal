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

from .. import clock
from ..domain import models
from ..domain.enums import (
    QUOTE_OUTCOME_TRANSITIONS,
    SELECTABLE_LOSS_REASONS,
    EvidenceSufficiency,
    QuoteLossReason,
    QuoteOutcomeStatus,
    Role,
)
from ..signals.base import UNRECORDED_SOURCE, CostRow, SaleRow
from .benchmark import ItemBenchmark, compute_benchmark
from .config import CommercialThresholds
from .policy import load_for_org
from .economics import LineEconomics, line_economics
from .metrics import RelationshipMetrics, compute_relationship
from .quote_exceptions import (
    APPROVAL_REQUIRED,
    COST_BASIS,
    CRITICAL,
    RESTRICTED,
    REVIEW_EXPECTED,
    WARNING,
)
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
    #: The landed cost the books hold against this item, read from the server's
    #: own quote — never from a request body, for the reason
    #: ``request_quote_line_approval`` gives: an approval whose numbers came from
    #: the requester is a request to approve whatever they typed. Used only when
    #: no bill-derived cost record exists; see ``assess_line``.
    item_master_cost: Optional[Decimal] = None


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
    th = th or load_for_org(session, org)
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
            th=th,
            item_master_cost=ln.item_master_cost))
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
def _withheld_boundaries(reference_dicts: list[dict], is_sales: bool) -> frozenset[str]:
    """What this recipient may not learn the value of, on this line.

    Derived from the line's own references rather than from a list of codes kept
    here, so a reference whose ``data_class`` changes moves this with it. A
    second copy of that classification is a second thing to forget.
    """
    if not is_sales:
        return frozenset()
    return frozenset(
        {r["code"] for r in reference_dicts if r.get("data_class") == RESTRICTED}
    ) | {COST_BASIS}


def _substitute(code: str, severity: str, title: str, detail: str, *,
                requires_approval: bool = False) -> dict:
    """The one exception standing in for every withheld cost rule.

    Fixed text, no impact figure, no reference code: it has to say the same
    thing however far below the floor the price sits, or the wording becomes the
    oracle the code was withheld to close.
    """
    return {
        "code": code, "severity": severity, "title": title, "detail": detail,
        "impact_amount": None, "impact_data_class": RESTRICTED,
        "reference_code": None, "requires_approval": requires_approval,
        "policy": True,
    }


def _project_exceptions(raw: list[dict], reference_dicts: list[dict],
                        is_sales: bool) -> list[dict]:
    """Strip the reasoning, then withhold the rules whose boundary is restricted.

    The first half is the old behaviour and was never enough. Removing
    ``manager_detail`` and a RESTRICTED ``impact_amount`` still leaves the fact
    that a *named* rule fired — and the caller chooses ``proposed_price``, so
    that fact is a probe. Walking the price finds the value at which the answer
    changes, and for the cost rules that value is cost, or cost over one minus a
    margin the same response deliberately withholds. Two hundred lines fit in
    one request, so the walk was two round trips.

    So a rule is withheld outright when its ``boundary_refs`` name anything this
    recipient may not see, and one substitute is emitted in its place. The
    substitute is what keeps the control: a salesperson must still learn that a
    line needs approval, and hiding that would be a worse defect than the one
    being closed — they would send it.

    **The residual, stated rather than left to be discovered.** A substitute is
    itself a boundary; anything that tells somebody "this needs approval" has to
    be. What changes is how many, and which. ``NEGATIVE_MARGIN`` and
    ``BELOW_MIN_MARGIN`` collapse into one, and that collapse is the point: cost
    is always at or below the approval floor, so the union of the two fires at
    the approval floor alone and the ``price <= unit_cost`` edge — the one that
    yielded cost with no policy parameter attached — stops existing. What is
    left is two boundaries, ``cost/(1 - min_margin)`` and
    ``cost/(1 - margin_floor)``, in three unknowns. Underdetermined; cost does
    not come out.

    Read that as the standing bound on this function. It may disclose one
    boundary per distinct action the recipient can take, because that is the
    information the control exists to convey. Anything past that is a leak.
    """
    withheld = _withheld_boundaries(reference_dicts, is_sales)
    out: list[dict] = []
    approval = review = False
    for entry in raw:
        d = dict(entry)
        if is_sales:
            d.pop("manager_detail", None)
            d.pop("inputs", None)
            if d.get("impact_data_class") == RESTRICTED:
                d["impact_amount"] = None
            if withheld & frozenset(d.get("boundary_refs") or ()):
                # Dropped, and remembered as the state it stood for.
                approval = approval or bool(d.get("requires_approval"))
                review = review or bool(d.get("policy"))
                continue
        d.pop("boundary_refs", None)
        out.append(d)

    if approval:
        out.insert(0, _substitute(
            APPROVAL_REQUIRED, CRITICAL,
            "Needs approval before it can go out",
            "This price is below what this item may be sold at without "
            "approval. Send it for approval, or raise the price.",
            requires_approval=True))
    elif review:
        out.insert(0, _substitute(
            REVIEW_EXPECTED, WARNING, "Thin on this line",
            "This price is below the level the business normally reviews. It "
            "can go out, but it will be looked at."))
    return out


def project(intel: QuoteLineIntelligence, role: Role, *,
            product_ref: str = "", unresolved: bool = False) -> dict:
    """Serialize one line's intelligence for a recipient.

    For a salesperson, cost, margin and every reference or impact derived from
    them are **absent** — not zeroed, not masked, not rounded away. What remains
    is what they may act on: prices this customer has actually paid, the rules
    that fired, and whether approval is needed. A salesperson can still see that
    a line needs approval; they cannot walk the price to find where approval
    begins and read cost off it — see ``_project_exceptions``.
    """
    is_sales = role is Role.SALESPERSON

    reference_dicts = [r.to_dict() for r in intel.references]
    references = [r for r in reference_dicts
                  if not (is_sales and r["data_class"] == RESTRICTED)]
    withheld = [r.label for r in intel.references
                if is_sales and r.data_class == RESTRICTED]

    exceptions = _project_exceptions(
        [e.to_dict() for e in intel.exceptions], reference_dicts, is_sales)

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
        # Read off the *projected* list, not the assessment. Taken from `intel`
        # these are a second, finer copy of what the withheld rules said:
        # CRITICAL rather than WARNING separates "below the approval floor" from
        # "below the review floor", which is a second boundary arriving through
        # a field nobody was looking at. That is how `filterCounts.MFLOOR`
        # happened — the guard was right and the line below it was not.
        "worst_severity": (exceptions[0]["severity"] if exceptions else None),
        "requires_approval": any(e["requires_approval"] for e in exceptions),
        "blocking": any(e["severity"] == CRITICAL for e in exceptions),
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

    ``role`` was taken and ignored, which is how the two cost-derived counts came
    to be computed for everybody. A count over lines the caller priced is a
    *sharper* oracle than the per-line flag, not a blunter one: two hundred
    probe lines in one request turn "how many are below the floor" into a
    rank, and a rank bisects in a single round trip. This is the shape of
    ``filterCounts.MFLOOR`` exactly, at quote scale.

    Omitted for a salesperson rather than zeroed, for the reason the MFLOOR fix
    in ``store.py`` gives: a zero still answers the question.
    """
    exceptions = [e for ln in assessment.lines for e in ln.exceptions]
    out = {
        "lines_assessed": len(assessment.lines),
        "lines_unresolved": len(assessment.unresolved),
        "exceptions_total": len(exceptions),
        "insufficient_data": sum(
            1 for ln in assessment.lines
            if ln.data_sufficiency is EvidenceSufficiency.INSUFFICIENT),
    }
    if role is not Role.SALESPERSON:
        out["critical"] = sum(1 for e in exceptions if e.severity == CRITICAL)
        out["requires_approval"] = sum(
            1 for ln in assessment.lines if ln.requires_approval)
    return out


def _catalog_version() -> str:
    """The resolving catalogue's ruleset checksum, or "" if it cannot be read.

    Provenance is worth recording and never worth failing a quote for, so this
    swallows a missing engine the same way ``pie_service.resolve`` does.
    """
    try:
        from ..pie_service import pie_service
        return pie_service.catalog_version
    except Exception:  # noqa: BLE001 — deliberate: never block a decision
        return ""


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
        # Read here rather than threaded down from intake: the catalogue is
        # loaded once per process and never reloaded, so its checksum is
        # constant for the life of every resolution this row could describe.
        # Imported inside the function for the same reason `resolve_customer`
        # does — the module-level import would be a cycle.
        catalog_version=_catalog_version(),
        as_of=intel.as_of,
        created_by_user_id=user_id,
    )
    session.add(row)
    session.flush()
    return row


def assess_and_record(
    session: Session, org: str, *,
    quote_id: str,
    customer_ref: str,
    lines: list[QuoteLineInput],
    user_id: Optional[str],
    overrides: Optional[dict[str, tuple[Optional[str], Optional[str]]]] = None,
    as_of: Optional[date] = None,
) -> tuple[QuoteAssessment, list[models.QuoteDecision]]:
    """Assess these lines and freeze each one into the audit trail.

    The two steps belong together and are never useful apart: a snapshot is only
    meaningful if its numbers were derived here rather than supplied by the
    caller, and an assessment nobody recorded is invisible to every control that
    reads the trail — which is exactly how the approval gate came to be inert.
    ``quote_submission_block`` judges the *latest snapshot per line*, and the
    only thing writing snapshots was a salesperson voluntarily opening a drawer
    and recording an override. Nobody doing that meant no snapshots, no snapshots
    meant nothing to judge, and a line at 0% margin against a 15% floor was
    reported ``can_submit: true`` and sent.

    So the send path records too, and it goes through here rather than through a
    second copy of the loop — one implementation, so the row the gate reads is
    the same row the audit screen shows.
    """
    overrides = overrides or {}
    result = assess_quote(session, org, customer_ref=customer_ref,
                          lines=lines, as_of=as_of)
    refs = {ln.line_id: ln.product_ref for ln in lines}
    rows = []
    for intel in result.lines:
        reason, reason_code = overrides.get(intel.line_id, (None, None))
        rows.append(record_snapshot(
            session, org, quote_id=quote_id, intel=intel,
            customer_ref=customer_ref, product_ref=refs.get(intel.line_id, ""),
            user_id=user_id,
            override_reason=reason, override_reason_code=reason_code))
    return result, rows


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
    # The system comes off the record, not off an assumption about which ERP
    # this deployment happens to run. Hardcoding "zoho" made every quote
    # decision's evidence claim Zoho for a bill that a second connector might
    # have written — and a citation that names the wrong system is worse than
    # one that admits it does not know.
    return [{"source_system": ref.get("system") or UNRECORDED_SOURCE,
             "record_type": "bill",
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
    references = list(row.references or [])
    # The same projection the live path uses, so the audit screen and the quote
    # screen cannot disagree about what a salesperson may read. A snapshot
    # written before ``boundary_refs`` existed carries none, so nothing is
    # withheld from it — acceptable, and worth saying why: a stored row holds
    # one price somebody actually quoted. It cannot be walked, so it is a single
    # historical fact rather than an oracle.
    exceptions = _project_exceptions(
        [dict(e) for e in (row.exceptions or [])], references, is_sales)

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
        # Narrowed to the codes that survived projection. This list is built
        # from the exceptions requiring approval, so unfiltered it names
        # BELOW_MIN_MARGIN straight back to the reader the line above just
        # withheld it from — the redaction and its undo in one payload.
        "overridden_exception_codes": [
            c for c in (row.overridden_exception_codes or [])
            if not is_sales or c in {e["code"] for e in exceptions}],
        "thresholds_version": row.thresholds_version,
        "engine_version": row.engine_version,
        "catalog_version": row.catalog_version,
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "created_at": clock.iso(row.created_at),
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


class MissingLossReason(ValueError):
    """A loss recorded without saying which kind of loss it was.

    Its own type rather than a ``ValueError``, because a router has to map it
    to a 422 with a usable message while ``InvalidTransition`` is a 409 — and a
    caller that cannot tell them apart will collapse both into "bad request"
    and lose the only part the person filling the form can act on.
    """


def set_outcome(session: Session, org: str, *, quote_id: str,
                status: QuoteOutcomeStatus, customer_ref: str = "",
                customer_id: Optional[str] = None, note: Optional[str] = None,
                loss_reason: Optional[QuoteLossReason] = None,
                lost_to: Optional[str] = None,
                user_id: Optional[str] = None) -> models.QuoteOutcome:
    """Move a quote along DRAFT → SENT → WON/LOST.

    Won and lost are terminal. Reopening a decided quote would rewrite history a
    margin analysis has already counted, so it is refused rather than silently
    allowed.

    **A loss must say which kind of loss it was.** Recording LOST without a
    reason raises rather than storing a benign default, because the two things
    a bare LOST can mean — a competitor supplied it, or nobody did — point in
    opposite directions for every later question about what this customer buys
    elsewhere. Defaulting to UNKNOWN would make the gap invisible at exactly
    the moment it is cheapest to close: the person recording the loss is the
    one person who knows.

    ``UNKNOWN`` is rejected as an explicit choice for the same reason. It is a
    state history can be in, not one a new record may be created in.
    """
    if status is QuoteOutcomeStatus.LOST:
        if loss_reason is None:
            raise MissingLossReason(
                "Recording a quote as lost needs a reason: "
                + ", ".join(r.value for r in SELECTABLE_LOSS_REASONS)
                + ". Whether the order went to another supplier or the "
                  "requirement went away are opposite facts about this "
                  "customer, and a lost quote with neither recorded cannot be "
                  "counted as either.")
        if loss_reason is QuoteLossReason.UNKNOWN:
            raise MissingLossReason(
                "UNKNOWN is not a reason a new loss may be recorded with — it "
                "exists only for quotes decided before the reason was asked "
                "for. Choose one of: "
                + ", ".join(r.value for r in SELECTABLE_LOSS_REASONS))

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
    if status is QuoteOutcomeStatus.LOST:
        # Only on the LOST edge. Setting these on any status would let a WON
        # quote carry a stale reason from an earlier attempt at the form.
        row.loss_reason = loss_reason.value if loss_reason else None
        row.lost_to = (lost_to or "").strip()[:255] or None
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
        # Null means the loss predates the field being asked for — not that
        # somebody answered "unknown". A screen should render the two
        # differently, and it cannot if the API folds them together.
        "loss_reason": row.loss_reason,
        "lost_to": row.lost_to,
        "customer_ref": row.customer_ref,
        "customer_id": row.customer_id,
        "sent_at": clock.iso(row.sent_at),
        "decided_at": clock.iso(row.decided_at),
        "allowed_next": sorted(
            s.value for s in QUOTE_OUTCOME_TRANSITIONS[QuoteOutcomeStatus(row.status)]),
        "loss_reasons": [r.value for r in SELECTABLE_LOSS_REASONS],
    }


def get_outcome(session: Session, org: str, quote_id: str) -> Optional[models.QuoteOutcome]:
    return session.scalar(
        select(models.QuoteOutcome).where(
            models.QuoteOutcome.organization_id == org,
            models.QuoteOutcome.quote_id == quote_id))
