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
# The one owner of "which of this ERP's status words mean what", imported
# rather than copied: a second reading of Zoho's vocabulary here is how
# ``approved`` — our own internal sign-off — starts meaning the customer said
# yes in one file and nothing in the other.
from ..ingestion.normalize import ZOHO, reached_the_customer
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


class AmbiguousQuoteDocument(ValueError):
    """An ERP quote reference that names more than one document.

    Its own type for the reason ``MissingLossReason`` has one: a router has to
    say something usable about it, and it is not a lifecycle problem or a
    missing field. It is the caller naming a quote by a reference that does not
    identify one.
    """


def _sole_erp_quote(session: Session, org: str,
                    quote_document_ref: str) -> Optional[models.QuoteDoc]:
    """The one ERP quote this reference names, or ``None`` where it names none.

    **An external reference is unique only inside the system that issued it,
    and only inside one company of that system** — which is why
    ``uq_quote_document_source`` is (org, connector, connection_id,
    external_ref) and not (org, external_ref). Zoho estimate ids happen to be
    globally unique, so this book is safe today; the connectors in
    ``ingestion/erp`` read systems whose quote numbers are per-company
    sequences, and two connected books of one of those will issue the same id
    twice.

    ``quote_outcomes`` carries the bare reference and no qualifier beside it, so
    when two documents answer to one reference there is nothing on the human row
    that could say which. Picking either — which is what a ``session.scalar``
    over (org, external_ref) does, silently, by row order — writes one person's
    loss reason, winner and note onto the other person's quote and then hands
    the next recorder a row that overwrites it in place. So this refuses, and
    names the books it is torn between.

    That refusal is a real loss of function in a two-book organization with
    colliding ids: neither quote's outcome can be recorded, not just the second.
    It is deliberately the trade taken, because the alternative destroys a fact
    only a person held, silently, and afterwards nothing distinguishes it from a
    fact that was entered. The durable fix is a qualified pointer — the
    connection stored beside the reference, the way every document table stores
    it — and it belongs with the capture screen that will need to *supply* the
    qualifier, since nothing calling this today can say which book it means.
    """
    rows = list(session.scalars(
        select(models.QuoteDoc).where(
            models.QuoteDoc.organization_id == org,
            models.QuoteDoc.external_ref == quote_document_ref)).all())
    if len(rows) > 1:
        books = ", ".join(sorted(
            f"{r.connector or 'source not recorded'}/"
            f"{r.connection_id or 'company not recorded'}" for r in rows))
        raise AmbiguousQuoteDocument(
            f"{quote_document_ref!r} names {len(rows)} quotes in this "
            f"organization ({books}). An ERP reference is unique only inside "
            "one connected company's book, and a quote outcome records what a "
            "person knows about one quote — so this one cannot be recorded "
            "until the reference says which book it came from.")
    return rows[0] if rows else None


def _opening_status(document: Optional[models.QuoteDoc]) -> QuoteOutcomeStatus:
    """The status a brand-new outcome row starts in, before its transition.

    DRAFT unless the ERP says otherwise, and the ERP says otherwise only
    through ``normalize.reached_the_customer`` — an allowlist of the status
    words that are evidence of a send. SENT is a positive claim that this quote
    was put in front of a customer, and a claim needs evidence: Zoho's
    ``pending_approval`` and ``approved`` are our own internal sign-off queue,
    where the estimate demonstrably has *not* been sent, and the shape that gets
    this wrong is the denylist — "not the word draft, therefore sent" — which
    reads both as sent and stamps a ``sent_at`` on a quote nobody has seen.
    That is the §1 tell exactly: the guard belongs around the claim, not around
    the objection.

    ``document`` is ``None`` for a platform quote — that is what the Quote
    Builder creates and nothing about it has been sent yet, including a platform
    quote that also names an ERP document, since the only caller naming both is
    the one recording the estimate it has this second created — and ``None``
    again for a reference matching no quote document. The latter is a dangling
    pointer, an estimate deleted in the ERP or one outside the sync window, and
    the honest answer to "what did the ERP say about this" is then nothing
    rather than an assumption.

    Without a SENT opening at all, ``QUOTE_OUTCOME_TRANSITIONS[DRAFT]`` — which
    allows only SENT and LOST — would force a two-call dance to record a WIN on
    a quote the ERP can prove was sent, and the first caller to meet that would
    either relax the transition table or record a fake SENT.

    ``sent_at`` is deliberately left for the transition to fill or not fill.
    The estimate list row carries no send timestamp at all, and
    ``client_viewed_at`` is when the *customer opened* it — reading one as the
    other is precisely the benign default this pull keeps refusing.
    """
    if document is None:
        return QuoteOutcomeStatus.DRAFT
    # A row with no connector recorded predates the column, and every row that
    # predates it came from Zoho — the same reading ``normalize``'s own default
    # takes, and stated here so the default is a decision rather than a fallback
    # somebody inherits.
    if reached_the_customer(document.source_status,
                            system=document.connector or ZOHO):
        return QuoteOutcomeStatus.SENT
    return QuoteOutcomeStatus.DRAFT


def set_outcome(session: Session, org: str, *, quote_id: Optional[str] = None,
                quote_document_ref: Optional[str] = None,
                status: QuoteOutcomeStatus, customer_ref: str = "",
                customer_id: Optional[str] = None, note: Optional[str] = None,
                loss_reason: Optional[QuoteLossReason] = None,
                lost_to: Optional[str] = None,
                user_id: Optional[str] = None) -> models.QuoteOutcome:
    """Move a quote along DRAFT → SENT → WON/LOST.

    **Two kinds of quote can be named.** ``quote_id`` is a quote this platform
    priced and holds lines for; ``quote_document_ref`` is the id an ERP gave a
    quote it raised itself, which is most of the book — the platform never saw
    those, and until this argument existed there was no way to record why one
    of them was lost. Extended rather than sibling-ed on purpose: every refusal
    below is the refusal an ERP-raised quote needs too, and a second writer
    would be a second place for "a loss must say why" to be forgotten.

    Both may be given, and that is the normal shape for a platform quote that
    was pushed to the ERP: ``quote_id`` is then the lookup key and
    ``quote_document_ref`` records which ERP document it became. What is
    refused is *neither* — an outcome about no document at all — and moving an
    outcome already recorded against one ERP quote onto a different one.

    ``quote_document_ref`` holds the *source system's* own id — the value in
    ``quote_documents.external_ref``, never ``quote_document_id``, which is a
    surrogate minted at insert. That is what makes the harshest rebuild safe:
    ``quote_documents`` is derived and a full re-sync re-mints every surrogate,
    while a pointer written as a value still resolves afterwards. What a person
    typed lives on this table alone, and the sync never opens it.

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

    "Not recorded" is unreachable rather than rejected: it lives outside
    ``QuoteLossReason`` as a plain sentinel, so there is no value a caller could
    pass to mean it. A state history can be in, but not one a new record can be
    created in — enforced by construction rather than by a guard that has to be
    remembered.
    """
    if quote_id is None and quote_document_ref is None:
        # An outcome about nothing. Refused here rather than by a CHECK
        # constraint: this repo has no CHECK precedent and ``compare_metadata``
        # does not compare them, so the drift test could never police one —
        # whereas this function is provably the only thing that writes the
        # table, which makes a guard on this line as strong as a constraint and
        # visible to the person who has to satisfy it.
        raise ValueError(
            "Recording a quote outcome needs a document to be about: quote_id "
            "(a quote this platform priced) or quote_document_ref (the id its "
            "own ERP gave one), or both when a platform quote was pushed to "
            "the ERP and became one.")

    if status is QuoteOutcomeStatus.LOST:
        if loss_reason is None:
            raise MissingLossReason(
                "Recording a quote as lost needs a reason: "
                + ", ".join(r.value for r in SELECTABLE_LOSS_REASONS)
                + ". Whether the order went to another supplier or the "
                  "requirement went away are opposite facts about this "
                  "customer, and a lost quote with neither recorded cannot be "
                  "counted as either.")

    # Resolved before the lookup and before any write, because on the ERP-only
    # path the reference *is* the identity: a reference answering to two of this
    # organization's books identifies no quote at all, and the row it would
    # otherwise be written onto belongs to somebody else. Not asked on the
    # platform-quote path — ``quote_id`` is the identity there, the caller has
    # just created the estimate it names, and refusing a link the ERP write
    # already made would leave the two tables describing one estimate twice.
    document = (_sole_erp_quote(session, org, quote_document_ref)
                if quote_id is None and quote_document_ref is not None else None)

    # Looked up by the platform quote when there is one, because that is the
    # identity the caller holds and the row it may already have written. The
    # ERP ref is the key only for a quote this platform never priced.
    key = (models.QuoteOutcome.quote_id == quote_id if quote_id is not None
           else models.QuoteOutcome.quote_document_ref == quote_document_ref)
    row = session.scalar(
        select(models.QuoteOutcome).where(
            models.QuoteOutcome.organization_id == org, key))

    if row is None:
        row = models.QuoteOutcome(
            organization_id=org, quote_id=quote_id,
            quote_document_ref=quote_document_ref,
            customer_ref=customer_ref[:255], customer_id=customer_id,
            status=_opening_status(document).value)
        session.add(row)
        session.flush()

    # Refused before a single field is written, so a rejected call leaves the
    # row exactly as it was rather than half-moved in the session.
    #
    # The loss reason, the winner and the decision on this row were recorded
    # about one quote; silently repointing them at another would produce a loss
    # nobody entered against a customer nobody spoke to, and afterwards it is
    # indistinguishable from a real one.
    if (quote_document_ref is not None
            and row.quote_document_ref not in (None, quote_document_ref)):
        raise ValueError(
            f"This outcome already describes ERP quote "
            f"{row.quote_document_ref}; it cannot be moved onto "
            f"{quote_document_ref}. Record the second quote's outcome "
            "against its own reference.")

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
    if quote_document_ref is not None:
        # Both keys on one row is the *normal* shape for a platform quote that
        # was pushed to the ERP, and it is the only thing that stops the same
        # quote being counted twice — once from the row a person wrote and once
        # from the document the pull read.
        row.quote_document_ref = quote_document_ref
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
        # The ERP's own id for the quote this outcome is about, where the
        # outcome is about one. Null on a quote this platform priced itself.
        "quote_document_ref": row.quote_document_ref,
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
