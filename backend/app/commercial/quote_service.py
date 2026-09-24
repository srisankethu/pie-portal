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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..domain import models
from ..domain.enums import (
    QUOTE_OUTCOME_TRANSITIONS,
    SELECTABLE_LOSS_REASONS,
    EvidenceSufficiency,
    QuoteDocOutcome,
    QuoteDocumentChannel,
    QuoteDocumentWriteState,
    QuoteLossReason,
    QuoteOutcomeSource,
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
    #: The cost a person put on this line by hand, read from the server's own
    #: quote for exactly the reason ``item_master_cost`` is. It outranks both
    #: the bill history and the item master — see ``assess_line``.
    custom_cost: Optional[Decimal] = None


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
            item_master_cost=ln.item_master_cost,
            custom_cost=ln.custom_cost))
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


def _catalog_version(connection_id: Optional[str] = None) -> str:
    """The resolving catalogue's ruleset checksum, or "" if it cannot be read.

    Takes the company whose catalogue answered, because there is no longer one
    per deployment. Absent a company this is "" rather than some other
    company's checksum — a stamp naming the wrong catalogue is worse than no
    stamp, since only the first is believed.

    Provenance is worth recording and never worth failing a quote for, so this
    swallows a missing engine the same way ``pie_service.resolve`` does.
    """
    try:
        from ..pie_service import pie_service
        return pie_service.catalog_version(connection_id)
    except Exception:  # noqa: BLE001 — deliberate: never block a decision
        return ""


# ── immutable snapshots ─────────────────────────────────────────────────────
def record_snapshot(
    session: Session, org: str, *,
    quote_id: str,
    connection_id: Optional[str] = None,
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
        # The catalogue of the company this quote was raised from. Threaded
        # in rather than read from a process-wide value: with one catalogue per
        # company there is no such value, and stamping a row with whichever
        # catalogue happened to be resident would name the wrong item master on
        # an audit row that is believed precisely because it is stamped. A
        # caller that cannot say leaves it empty, which reads as "not recorded"
        # rather than as a claim.
        catalog_version=_catalog_version(connection_id),
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
    connection_id: Optional[str] = None,
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
            session, org, quote_id=quote_id, connection_id=connection_id,
            intel=intel,
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
    # The record type comes off the ref for the same reason the system does.
    # It was a literal "bill", which was true of the only basis that existed
    # when it was written and is now false of two: an item-master landed cost
    # is not a bill, and a cost somebody typed on the quote line is a long way
    # from one. An audit row that names the wrong kind of evidence is worse
    # than one that says it does not know.
    return [{"source_system": ref.get("system") or UNRECORDED_SOURCE,
             "record_type": ref.get("record_type") or "bill",
             "basis": ref.get("basis"),
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


class QuoteOutcomeRepointed(ValueError):
    """An outcome moved off the ERP quote it was recorded about.

    Its own type for the reason ``MissingLossReason`` has one, and the router
    is the caller that needs the distinction: this is a conflict with a row
    that already exists — nothing the caller puts in the body fixes it — while
    the "names neither document" refusal one guard up is a missing field. A
    bare ``ValueError`` collapses a 409 and a 422 into one clause.
    """


def sole_erp_quote(session: Session, org: str,
                    quote_document_ref: str,
                    connection_id: Optional[str] = None) -> Optional[models.QuoteDoc]:
    """The one ERP quote this reference names, or ``None`` where it names none.

    ``connection_id`` is the qualifier the paragraphs below deferred. Given, it
    narrows the lookup to that company's book and the collision cannot arise;
    absent, the bare reference is looked up exactly as before and refused when
    two books answer. Callers that know the book — the send, and any reader
    holding a row that already carries ``quote_document_connection_id`` — pass
    it; the ones that do not still get the refusal rather than a guess.

    **An external reference is unique only inside the system that issued it,
    and only inside one company of that system** — which is why
    ``uq_quote_document_source`` is (org, connector, connection_id,
    external_ref) and not (org, external_ref). Zoho estimate ids happen to be
    globally unique, so this book is safe today; the connectors in
    ``ingestion/erp`` read systems whose quote numbers are per-company
    sequences, and two connected books of one of those will issue the same id
    twice.

    ``quote_outcomes`` carries ``quote_document_connection_id`` beside the
    reference now, so a row written since then says which book it meant and
    resolves without ever reaching the refusal below. A row written *before*
    that column existed does not, and for those there is nothing on the human
    row that could say which document was meant. Picking either — which is
    what a ``session.scalar`` over (org, external_ref) does, silently, by row
    order — writes one person's loss reason, winner and note onto the other
    person's quote and then hands the next recorder a row that overwrites it
    in place. So this refuses, and names the books it is torn between.

    That refusal is a real loss of function in a two-book organization with
    colliding ids: neither quote's outcome can be recorded, not just the second.
    It is deliberately the trade taken, because the alternative destroys a fact
    only a person held, silently, and afterwards nothing distinguishes it from a
    fact that was entered.

    **The collision is still not reachable through any connector shipped
    today, and the reason is worth knowing because it is one line away from
    being lost.** Every source that reads quotes keys ``external_ref`` on a
    system-wide surrogate rather than the per-company number printed on the
    document: Zoho's ``estimate_id``, Business Central's and Acumatica's row
    GUIDs, NetSuite's internal ``t.id``. Each of those was chosen so a quote
    this platform *wrote* is recognisable as the same document when the sync
    reads it back — the join is on the id the writer returned — and disjoint
    id spaces across connected books are a property that choice happens to
    carry, not the reason for it. Re-key any of them onto the human number,
    which a capture screen is exactly the kind of screen to ask for, and two
    books of one system issue ``SQ-1001`` twice on the same day.
    ``normalize.normalize_quote_document`` is where that property is asserted;
    this paragraph and the capture grid point there.

    Public rather than private because a second caller now needs the document
    itself rather than the outcome written from it: ``routers.quote_intelligence``
    asks whether the principal may record against the quote a reference names,
    and *which accounts a role may see* is a question ``commercial/`` does not
    answer. Two resolutions of one reference in a request is the cost, and it is
    an indexed lookup on the column the human table already points at.
    """
    stmt = select(models.QuoteDoc).where(
        models.QuoteDoc.organization_id == org,
        models.QuoteDoc.external_ref == quote_document_ref)
    if connection_id:
        stmt = stmt.where(models.QuoteDoc.connection_id == connection_id)
    rows = list(session.scalars(stmt).all())
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


def allowed_transitions(status: QuoteOutcomeStatus, *,
                        names_erp_document: bool) -> frozenset[QuoteOutcomeStatus]:
    """What an outcome row in ``status`` may become next.

    ``QUOTE_OUTCOME_TRANSITIONS`` plus one addition, and the addition is the
    whole reason this is a function rather than three lookups of that table.

    **A person may record WON on a quote that exists in an ERP, straight from
    DRAFT.** The table forbids DRAFT → WON because a quote that never went out
    could not have come back won, which is right for a platform quote: DRAFT
    there means *we have not sent it*. It is wrong for a quote the ERP raised,
    where DRAFT means only that this platform cannot read the source's word for
    "sent" — ``_QUOTE_SENT_STATUSES`` has an entry for Zoho and for nobody
    else, on purpose, because the other vocabularies are cited and not
    verified. Without this, recording a win on any Business Central, Acumatica
    or NetSuite quote was refused 409 and only a loss could be recorded, so
    those books' win rates read zero wins and all losses.

    The claim is the person's, which is what makes it admissible: a customer
    cannot accept a quote they never received, so somebody recording WON is
    asserting the send as well. Nothing derived is widened — no SENT the ERP
    never said is written, and ``sent_at`` stays empty, which is the honest
    record of "we know it was won and never learned when it went out".
    ``OutcomeOfRecord.ever_sent`` already counts any ERP-raised quote as sent,
    so the win-rate denominator is unchanged.

    One function because three places ask: the refusal in ``set_outcome`` and
    the two ``allowed_next`` lists the screens offer. Re-deriving the predicate
    at each would let the API accept what the buttons never offer, or the
    reverse — which is the shape of defect this module's own docstrings keep
    naming.
    """
    allowed = set(QUOTE_OUTCOME_TRANSITIONS[status])
    if status is QuoteOutcomeStatus.DRAFT and names_erp_document:
        allowed.add(QuoteOutcomeStatus.WON)
    return frozenset(allowed)


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


#: Business Central's ``externalDocumentNumber`` is a ``Code[35]`` and the
#: smallest field any of these systems stores a reference in; a revision
#: suffix has to fit inside it beside the reference the quote already carries
#: (``QB-0042-<8 hex>``, 16 characters). ``-r99`` leaves nineteen to spare.
_REVISION_SUFFIX = "-r{n}"


def revision_reference(reference: str, revision: int) -> str:
    """The reference a revision goes out under.

    Revision 1 keeps the quote's own reference, unchanged, so every document
    already written stays findable by the key it was written with. Every later
    revision appends ``-rN``: a reference no source has seen, which is what
    makes the source *create* the amended document instead of answering with
    the one it already holds. Pure, so the send and the tests agree on it.
    """
    return reference if revision <= 1 else reference + _REVISION_SUFFIX.format(n=revision)


def record_document(session: Session, org: str, *, quote_id: str,
                    external_system: str, number: str, line_count: int,
                    fingerprint: str, reference: str = "",
                    document_id: Optional[str] = None,
                    connection_id: Optional[str] = None,
                    already_existed: bool = False,
                    revision: int = 1,
                    channel: QuoteDocumentChannel = QuoteDocumentChannel.ERP,
                    write_state: QuoteDocumentWriteState = QuoteDocumentWriteState.WRITTEN,
                    thresholds_version: str = "") -> models.QuoteDocument:
    """Record that this quote was written into a source system.

    Append-only: one row per send, never updated. A re-send of amended content
    writes another and the newest wins, so a quote's whole send history is
    readable — which is what nothing had, the previous document being three
    attributes on an in-memory object that a restart erased.

    Deliberately does not commit. The caller has just performed an external
    write it cannot take back, and the transaction boundary belongs to it.
    """
    row = models.QuoteDocument(
        organization_id=org, quote_id=quote_id,
        external_system=external_system,
        connection_id=connection_id,
        external_document_id=document_id,
        external_document_number=number,
        reference=reference, line_count=line_count,
        fingerprint=fingerprint, already_existed=already_existed,
        revision=revision, channel=channel.value, write_state=write_state.value,
        thresholds_version=thresholds_version)
    session.add(row)
    session.flush()
    return row


def latest_document(session: Session, org: str, *,
                    quote_id: str) -> Optional[models.QuoteDocument]:
    """The document this quote most recently produced, if any.

    Ordered by the id as well as the timestamp. Two sends inside one clock tick
    is not a real scenario, but an unordered tie would make the answer depend on
    row order — and "which document is current" deciding differently on two
    reads is the kind of defect that is only ever seen once, in production.
    """
    return session.scalars(
        select(models.QuoteDocument)
        .where(models.QuoteDocument.organization_id == org,
               models.QuoteDocument.quote_id == quote_id)
        .order_by(models.QuoteDocument.written_at.desc(),
                  models.QuoteDocument.quote_document_id.desc())
        .limit(1)).first()


def latest_written_document(session: Session, org: str, *,
                            quote_id: str,
                            channel: Optional[QuoteDocumentChannel] = None,
                            ) -> Optional[models.QuoteDocument]:
    """The newest document the source *confirmed* — what "sent" means on
    screen and what the fingerprint check compares against.

    ``latest_document`` answers "what did the last press leave behind", which
    may be an UNVERIFIED row with a reference and no number; this answers
    "which document does the customer's book actually hold". Two questions,
    two functions, and every reader has to know which it is asking.

    ``channel`` narrows to one way out: the send asks for the newest *ERP*
    document when it names what a revision supersedes and moves the outcome
    off it — a MANUAL row in between has no document id to move from, and
    naming it would refuse the move and leave the outcome on a document two
    revisions old.
    """
    stmt = (select(models.QuoteDocument)
            .where(models.QuoteDocument.organization_id == org,
                   models.QuoteDocument.quote_id == quote_id,
                   models.QuoteDocument.write_state
                   == QuoteDocumentWriteState.WRITTEN.value))
    if channel is not None:
        stmt = stmt.where(models.QuoteDocument.channel == channel.value)
    return session.scalars(
        stmt.order_by(models.QuoteDocument.written_at.desc(),
                      models.QuoteDocument.quote_document_id.desc())
        .limit(1)).first()


def _own_document(session: Session, org: str, quote_id: str,
                  document_ref: str) -> bool:
    """Whether this quote itself wrote the document a reference names."""
    return session.scalar(
        select(models.QuoteDocument.quote_document_id).where(
            models.QuoteDocument.organization_id == org,
            models.QuoteDocument.quote_id == quote_id,
            models.QuoteDocument.external_document_id == document_ref)
        .limit(1)) is not None


def erp_documents_for(session: Session, org: str,
                      documents: list[Optional[models.QuoteDocument]],
                      ) -> dict[str, models.QuoteDoc]:
    """The ERP's own row for each sent document, keyed by the quote's id.

    The join between the two halves of one document — the row the send wrote
    and the row the sync later read back. A **value join on the qualified
    identity**: (system, company, the id the system gave it) against
    ``erp_quotes`` (connector, connection_id, external_ref). Never a surrogate,
    so ``DELETE FROM erp_quotes`` plus a full re-sync re-mints every
    ``quote_document_id`` there and this still resolves — the reason
    ``QuoteOutcome.quote_document_ref`` is a value too.

    A document written before ``quote_documents.connection_id`` existed carries
    no company and joins on (system, id) alone **while that is unique**. Two
    companies answering to one id is answered with nothing rather than with
    either — the rule ``sole_erp_quote`` applies to a reference, applied to a
    join. Read-side only, in ``commercial/``: the sync never learns that
    ``quote_documents`` exists, for the reason it must never name
    ``quote_outcomes``.

    One query for the list, which is what keeps the workspace from paying a
    lookup per row on top of the one it already pays for the document.
    """
    docs = [d for d in documents if d is not None and d.external_document_id]
    rows = _erp_rows_for(session, org, docs)
    return {d.quote_id: rows[d.quote_document_id]
            for d in docs if d.quote_document_id in rows}


def _erp_rows_for(session: Session, org: str,
                  docs: list[models.QuoteDocument]) -> dict[str, models.QuoteDoc]:
    """The qualified value join ``erp_documents_for`` describes, one ERP row
    per *document* (keyed by ``quote_document_id``) — so a quote with two
    documents can be read through both."""
    if not docs:
        return {}
    rows = session.scalars(
        select(models.QuoteDoc).where(
            models.QuoteDoc.organization_id == org,
            models.QuoteDoc.external_ref.in_(
                {d.external_document_id for d in docs}))).all()
    by_ref: dict[str, list[models.QuoteDoc]] = defaultdict(list)
    for r in rows:
        by_ref[r.external_ref].append(r)
    out: dict[str, models.QuoteDoc] = {}
    for d in docs:
        # A row with no connector recorded predates the column and came from
        # Zoho — the reading ``_opening_status`` takes, taken here too.
        system = d.external_system or ZOHO
        found = [r for r in by_ref.get(d.external_document_id, [])
                 if (r.connector or ZOHO) == system]
        if d.connection_id:
            found = [r for r in found if r.connection_id == d.connection_id]
        if len(found) == 1:
            out[d.quote_document_id] = found[0]
    return out


def erp_document_for(session: Session, org: str, *,
                     quote_id: str) -> Optional[models.QuoteDoc]:
    """The ERP's own row for this quote's newest document — ``erp_documents_for``
    for one quote, so a screen showing one draft and a list showing forty read
    the same join."""
    doc = latest_document(session, org, quote_id=quote_id)
    return erp_documents_for(session, org, [doc]).get(quote_id) if doc else None


@dataclass(frozen=True)
class OutcomeOfRecord:
    """How one quote ended, from both sources, under one rule.

    The answer every reader gives — Won & lost, the attribution evaluator,
    the diagnosis replay, the wallet, the ERP tab's headline and the workspace
    list. Before this each computed its own from the human table alone, so a
    quote the ERP had marked accepted stayed "awaiting an answer" on one
    screen while the ERP tab counted it won: three win rates over one book.

    ``status`` is the state of record; ``source`` says who decided it and is
    ``None`` while the quote is open. ``loss_reason`` and ``lost_to`` come
    only from a person — the ERP holds neither, and an ERP-decided loss
    reads ``LOSS_REASON_NOT_RECORDED`` downstream rather than a guess.
    """

    status: QuoteOutcomeStatus
    source: Optional[QuoteOutcomeSource]
    decided_on: Optional[date]
    loss_reason: Optional[str]
    lost_to: Optional[str]
    #: The row a person wrote, if any.
    human: Optional[models.QuoteOutcome]
    #: The ERP's own row for the document, once a sync has read it.
    erp: Optional[models.QuoteDoc]
    #: The document this platform wrote or recorded, for a platform quote.
    document: Optional[models.QuoteDocument] = None

    @property
    def decided(self) -> bool:
        return self.status in (QuoteOutcomeStatus.WON, QuoteOutcomeStatus.LOST)

    @property
    def ever_sent(self) -> bool:
        """Whether a win was ever reachable: a person or a document says the
        quote went out. A LOST straight from DRAFT never could have won."""
        return ((self.human is not None and self.human.sent_at is not None)
                or self.document is not None or self.erp is not None)


def decide(human: Optional[models.QuoteOutcome],
           erp: Optional[models.QuoteDoc],
           document: Optional[models.QuoteDocument] = None) -> OutcomeOfRecord:
    """The one rule (plan §2.5), pure so every reader agrees by construction.

    1. A human WON or LOST wins, always, with its reason and winner.
    2. Otherwise the ERP's classification of the same document, when it is
       WON or LOST *with a date* — an undated decision is not evidence, which
       is the line ``classify_outcome`` already draws.
    3. Otherwise the quote is open: SENT when a person, a document or the
       ERP's own row says it went out, DRAFT when nothing does.

    Derived, never written back. ``unrecorded._load`` states the same rule as
    a SQL filter for the worklist — a quote leaves the pile once either
    source has decided it — and a change to one is a change to both.
    """
    if human is not None and human.status in (QuoteOutcomeStatus.WON.value,
                                              QuoteOutcomeStatus.LOST.value):
        return OutcomeOfRecord(
            status=QuoteOutcomeStatus(human.status),
            source=QuoteOutcomeSource.HUMAN,
            decided_on=(clock.aware(human.decided_at).date()
                        if human.decided_at else None),
            loss_reason=human.loss_reason, lost_to=human.lost_to,
            human=human, erp=erp, document=document)
    if (erp is not None and erp.decided_on is not None
            and erp.outcome in (QuoteDocOutcome.WON.value, QuoteDocOutcome.LOST.value)):
        return OutcomeOfRecord(
            status=QuoteOutcomeStatus(erp.outcome),
            source=QuoteOutcomeSource.ERP,
            decided_on=erp.decided_on, loss_reason=None, lost_to=None,
            human=human, erp=erp, document=document)
    # Open. SENT when a person, a document or the ERP's own row says the
    # quote went out — a human row still reading DRAFT beside a confirmed
    # document is the send's bookkeeping having failed after the write, and
    # the document is the fact.
    if human is not None and human.status != QuoteOutcomeStatus.DRAFT.value:
        status = QuoteOutcomeStatus(human.status)
    elif document is not None or erp is not None:
        status = QuoteOutcomeStatus.SENT
    else:
        status = QuoteOutcomeStatus.DRAFT
    return OutcomeOfRecord(status=status, source=None, decided_on=None,
                           loss_reason=None, lost_to=None,
                           human=human, erp=erp, document=document)


def _written_documents(session: Session, org: str, quote_ids: Iterable[str], *,
                       channel: Optional[QuoteDocumentChannel] = None,
                       ) -> list[models.QuoteDocument]:
    """Every confirmed document of these quotes, newest first, ``channel``
    narrowing it the way ``latest_written_document`` does."""
    wanted = {q for q in quote_ids if q}
    if not wanted:
        return []
    stmt = (select(models.QuoteDocument)
            .where(models.QuoteDocument.organization_id == org,
                   models.QuoteDocument.quote_id.in_(wanted),
                   models.QuoteDocument.write_state
                   == QuoteDocumentWriteState.WRITTEN.value))
    if channel is not None:
        stmt = stmt.where(models.QuoteDocument.channel == channel.value)
    return list(session.scalars(
        stmt.order_by(models.QuoteDocument.written_at.desc(),
                      models.QuoteDocument.quote_document_id.desc())))


def latest_written_documents_for(session: Session, org: str,
                                 quote_ids: Iterable[str], *,
                                 channel: Optional[QuoteDocumentChannel] = None,
                                 ) -> dict[str, models.QuoteDocument]:
    """``latest_written_document`` for many quotes in one query, ``channel``
    narrowing it the same way."""
    out: dict[str, models.QuoteDocument] = {}
    for doc in _written_documents(session, org, quote_ids, channel=channel):
        out.setdefault(doc.quote_id, doc)        # newest first; first wins
    return out


def erp_of_record_for(session: Session, org: str,
                      quote_ids: Iterable[str]) -> dict[str, models.QuoteDoc]:
    """The ERP row that speaks for each platform quote, keyed by quote id.

    Its newest ERP-written document's row — unless an *older* document of
    the same quote was decided in the ERP and the newest was not. D2 leaves
    the previous document live in the ERP until the desk voids it, so a
    customer can accept revision 1 after revision 2 went out. The pointer and
    the newest document both name revision 2, unrecorded; the ERP tab shows
    revision 1 accepted, on its own row. One quote, two answers — unless the
    decision on the older document counts here too. It does: a customer who
    accepted any document this quote became has decided the quote, and the
    desk's next move is to void the other, not to keep chasing.
    """
    docs = _written_documents(session, org, quote_ids,
                              channel=QuoteDocumentChannel.ERP)
    rows = _erp_rows_for(session, org, docs)
    out: dict[str, models.QuoteDoc] = {}
    for d in docs:                              # newest first
        row = rows.get(d.quote_document_id)
        if row is None:
            continue
        current = out.get(d.quote_id)
        if current is None or (_erp_decided(row) and not _erp_decided(current)):
            out[d.quote_id] = row
    return out


def _erp_decided(row: models.QuoteDoc) -> bool:
    """Decided *with a date* — the line ``decide`` draws for the ERP's word."""
    return (row.decided_on is not None
            and row.outcome in (QuoteDocOutcome.WON.value, QuoteDocOutcome.LOST.value))


def outcomes_of_record(session: Session, org: str,
                       rows: Iterable[models.QuoteOutcome], *,
                       written: Optional[dict[str, Optional[models.QuoteDocument]]] = None,
                       erp: Optional[dict[str, models.QuoteDoc]] = None,
                       ) -> dict[str, OutcomeOfRecord]:
    """``decide`` for every platform quote among ``rows``, keyed by quote id.

    The human row is the anchor: a platform quote enters the readers through
    the row the send (or a person) wrote, and ``scripts/backfill_sent_outcomes``
    opens one for every document sent before the send recorded anything. The
    ERP side is joined through the quote's newest document **the ERP holds**
    — its newest ERP-channel row, through ``erp_documents_for``, the
    qualified value join — so the ERP's word is only ever read off a document
    this quote actually became.

    Newest *ERP* row, not newest confirmed row of any channel. A MANUAL
    mark-sent on top of an ERP send is confirmed and has no document id, so
    joining through it found nothing and read the quote as SENT — while the
    other direction, ``erp_outcomes_of_record``, still joined the same human
    row to the ERP document through the pointer mark-sent never clears, and
    read the ERP's WON. One quote, two answers, on the one rule that exists
    so every reader agrees. The pointer and the newest ERP row name the same
    document, which is what makes the two directions agree again.

    ``written`` is what says a quote went out when nothing has decided it —
    the newest confirmed row of *any* channel, a mark-sent included. ``erp``
    lets a caller that already holds the ERP-channel join pass it in; the
    workspace list holds the other join and does not.
    """
    rows = [r for r in rows if r.quote_id]
    if not rows:
        return {}
    ids = [r.quote_id for r in rows]
    if written is None:
        written = latest_written_documents_for(session, org, ids)
    if erp is None:
        erp = erp_of_record_for(session, org, ids)
    return {r.quote_id: decide(r, erp.get(r.quote_id), written.get(r.quote_id))
            for r in rows}


def _ambiguous_refs(session: Session, org: str, refs: Iterable[str]) -> set[str]:
    """The references that name more than one ERP document in this
    organization — two connected books issuing the same id. An unqualified
    pointer to one of these names no document, the rule ``sole_erp_quote``
    applies on the write side."""
    wanted = {r for r in refs if r}
    if not wanted:
        return set()
    return {ref for ref, n in session.execute(
        select(models.QuoteDoc.external_ref, func.count())
        .where(models.QuoteDoc.organization_id == org,
               models.QuoteDoc.external_ref.in_(wanted))
        .group_by(models.QuoteDoc.external_ref)) if n > 1}


def erp_documents_by_ref(session: Session, org: str,
                         rows: Iterable[models.QuoteOutcome],
                         ) -> dict[str, Optional[models.QuoteDoc]]:
    """The ERP row each human row about an ERP-raised quote points at, keyed
    by the human row's id — the join for rows that carry no ``quote_id``.

    A row that names the company joins only that company's document; one
    written before the qualifier existed joins on the id alone *while that
    names one document*, and nothing where two books answer to it. The same
    rule as ``erp_documents_for`` on the platform side, applied to the other
    pointer.
    """
    rows = [r for r in rows if r.quote_document_ref]
    if not rows:
        return {}
    refs = {r.quote_document_ref for r in rows}
    by_ref: dict[str, list[models.QuoteDoc]] = defaultdict(list)
    for d in session.scalars(
            select(models.QuoteDoc).where(
                models.QuoteDoc.organization_id == org,
                models.QuoteDoc.external_ref.in_(refs))):
        by_ref[d.external_ref].append(d)
    out: dict[str, Optional[models.QuoteDoc]] = {}
    for r in rows:
        candidates = by_ref.get(r.quote_document_ref, [])
        if r.quote_document_connection_id:
            candidates = [d for d in candidates
                          if d.connection_id == r.quote_document_connection_id]
        out[r.quote_outcome_id] = candidates[0] if len(candidates) == 1 else None
    return out


def records_for_rows(session: Session, org: str,
                     rows: Iterable[models.QuoteOutcome],
                     ) -> dict[str, OutcomeOfRecord]:
    """The outcome of record for every human row, keyed by the row's own id.

    Both kinds of row, one answer each: a platform quote's row is joined to
    the ERP through the quote's newest confirmed document
    (``outcomes_of_record``); a row about an ERP-raised quote — no
    ``quote_id``, only the ERP's reference — is joined to that document by
    the reference (``erp_documents_by_ref``). Readers that hold rows of both
    kinds (Won & lost, the attribution evaluator) call this and nothing else,
    so an ERP-raised quote a person marked SENT and the ERP then accepted is
    a win here exactly as it is on the ERP tab.
    """
    rows = list(rows)
    platform = [r for r in rows if r.quote_id]
    by_quote = outcomes_of_record(session, org, platform)
    erp_only = [r for r in rows if not r.quote_id]
    erp_by_row = erp_documents_by_ref(session, org, erp_only)
    out: dict[str, OutcomeOfRecord] = {}
    for r in platform:
        out[r.quote_outcome_id] = by_quote[r.quote_id]
    for r in erp_only:
        out[r.quote_outcome_id] = decide(r, erp_by_row.get(r.quote_outcome_id))
    return out


def erp_outcomes_of_record(session: Session, org: str,
                           docs: Iterable[models.QuoteDoc],
                           ) -> dict[str, OutcomeOfRecord]:
    """``decide`` for every ERP row, keyed by ``quote_document_id``.

    The other direction of the same join: a person's row names an ERP
    document by the ERP's own id (``quote_document_ref``), qualified by the
    connected company where the row knows it. A row that names the company
    joins only that company's document; one written before the qualifier
    existed joins on the id alone — and only while that id names one document
    in the organization. Two books answering to it is the collision
    ``sole_erp_quote`` refuses on the write side, and a read that picked
    either would show one person's loss reason on the other book's quote.
    """
    docs = list(docs)
    if not docs:
        return {}
    refs = {d.external_ref for d in docs}
    by_ref: dict[str, list[models.QuoteOutcome]] = defaultdict(list)
    for row in session.scalars(
            select(models.QuoteOutcome).where(
                models.QuoteOutcome.organization_id == org,
                models.QuoteOutcome.quote_document_ref.in_(refs))):
        by_ref[row.quote_document_ref].append(row)
    unqualified = {ref for ref, rows in by_ref.items()
                   if any(r.quote_document_connection_id is None for r in rows)}
    ambiguous = _ambiguous_refs(session, org, unqualified)
    out: dict[str, OutcomeOfRecord] = {}
    for d in docs:
        human = next(
            (r for r in by_ref.get(d.external_ref, [])
             if r.quote_document_connection_id == d.connection_id
             or (r.quote_document_connection_id is None
                 and d.external_ref not in ambiguous)),
            None)
        out[d.quote_document_id] = decide(human, d)
    return out


def set_outcome(session: Session, org: str, *, quote_id: Optional[str] = None,
                quote_document_ref: Optional[str] = None,
                quote_document_connection_id: Optional[str] = None,
                status: QuoteOutcomeStatus, customer_ref: str = "",
                customer_id: Optional[str] = None, note: Optional[str] = None,
                loss_reason: Optional[QuoteLossReason] = None,
                lost_to: Optional[str] = None,
                user_id: Optional[str] = None,
                repoint_from: Optional[str] = None) -> models.QuoteOutcome:
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
    document = (sole_erp_quote(session, org, quote_document_ref,
                               quote_document_connection_id)
                if quote_id is None and quote_document_ref is not None else None)
    # The qualifier, from whichever side knows it: the send passes the book it
    # wrote into; the ERP-only path has the document and reads it off the row.
    qualifier = quote_document_connection_id or (
        document.connection_id if document is not None else None)

    # Looked up by the platform quote when there is one, because that is the
    # identity the caller holds and the row it may already have written. The
    # ERP ref is the key only for a quote this platform never priced — and
    # then it is the ref **and the book it is unique in**: two connected
    # companies both issue ``SQ-1001``, and looking up on the bare reference
    # found the first book's row and tried to move it, so the second book's
    # loss arrived as "a quote that is LOST cannot become WON" about a quote
    # nobody had asked about.
    #
    # Exact company first, then a row that names none — the graded rule
    # ``repositories._for_upsert`` states for the master tables, and for the
    # same reason: a row written before the qualifier existed is unclaimed and
    # this call adopts it (filling the company below), while a row belonging to
    # a *different* company is never adopted at any setting.
    if quote_id is not None:
        row = session.scalar(
            select(models.QuoteOutcome).where(
                models.QuoteOutcome.organization_id == org,
                models.QuoteOutcome.quote_id == quote_id))
    else:
        candidates = list(session.scalars(
            select(models.QuoteOutcome).where(
                models.QuoteOutcome.organization_id == org,
                models.QuoteOutcome.quote_document_ref == quote_document_ref)))
        row = next(
            (r for r in candidates
             if qualifier is not None
             and r.quote_document_connection_id == qualifier),
            None)
        if row is None:
            # A company-less row is adopted only while the reference names one
            # document in this organization. Where two books answer to it,
            # filling that row with *this* book would assert that somebody's
            # loss reason, winner and note were about this book's quote when
            # nothing on the row says so — the benign default §1 forbids, in
            # the one place it would overwrite a fact only a person held. It is
            # contested instead, by the guard below, which says so in a
            # sentence.
            unclaimed = next(
                (r for r in candidates
                 if r.quote_document_connection_id is None), None)
            if unclaimed is not None and (
                    qualifier is None
                    or quote_document_ref not in _ambiguous_refs(
                        session, org, [quote_document_ref])):
                row = unclaimed
        # No qualifier at all: the reference is this organization's key, as it
        # has always been, and ``sole_erp_quote`` has already refused it where
        # two books answer to it.
        if row is None and qualifier is None:
            row = candidates[0] if candidates else None
        if row is None and any(r.quote_document_connection_id is None
                               for r in candidates):
            # Nothing adoptable, and somebody else's row already holds this
            # reference: a company-less record about a reference two books
            # answer to. The constraint used to refuse this as an
            # IntegrityError at commit — about a quote the caller never named —
            # and widening the constraint to carry the company is what makes
            # the refusal this function's to give, with a sentence.
            raise QuoteOutcomeRepointed(
                f"ERP quote {quote_document_ref} already has a recorded "
                f"outcome that names no connected company, and two of this "
                f"organization's books answer to that reference — so nothing "
                f"says which quote it was about. Correct that row's company "
                f"before recording this one.")

    if row is None:
        row = models.QuoteOutcome(
            organization_id=org, quote_id=quote_id,
            quote_document_ref=quote_document_ref,
            customer_ref=customer_ref[:255], customer_id=customer_id,
            status=_opening_status(document).value)
        session.add(row)
        session.flush()

    # Both refusals below happen before a single field is written, so a
    # rejected call leaves the row exactly as it was rather than half-moved in
    # the session — and the transition is checked FIRST, which is load-bearing
    # rather than cosmetic. The revision repoint a few lines down nulls the
    # pointer, and a decided row has no legal transition at all
    # (``QUOTE_OUTCOME_TRANSITIONS[WON]`` is empty): with the order reversed, a
    # decided quote re-sent as a revision had its pointer cleared and *then*
    # met ``InvalidTransition``, leaving the person's recorded win or loss in
    # the session with nothing naming the document it was about. The exception
    # made it look refused. Nothing downstream could tell that row from one
    # recorded against a quote that was never pushed.
    current = QuoteOutcomeStatus(row.status)
    # The reference this call would leave on the row, not only the one already
    # there: a first recording names the ERP document in the same call that
    # decides it, and asking the stored value alone would refuse that.
    if status is not current and status not in allowed_transitions(
            current,
            names_erp_document=bool(row.quote_document_ref or quote_document_ref)):
        raise InvalidTransition(
            f"A quote that is {current.value} cannot become {status.value}")

    # The loss reason, the winner and the decision on this row were recorded
    # about one quote; silently repointing them at another would produce a loss
    # nobody entered against a customer nobody spoke to, and afterwards it is
    # indistinguishable from a real one.
    if (quote_document_ref is not None
            and row.quote_document_ref not in (None, quote_document_ref)):
        # One exception, and it is narrow: a quote re-sent as a new revision
        # is one quote whose newest document has changed, and its outcome
        # follows the newest document. Allowed only when the caller names the
        # document it is moving *from*, that is the one the row holds, and
        # this quote itself wrote it — so a human record about a different
        # quote's document, or about an ERP-raised one, is never moved.
        if (repoint_from is not None and quote_id is not None
                and row.quote_document_ref == repoint_from
                and _own_document(session, org, quote_id, repoint_from)):
            row.quote_document_ref = None
            row.quote_document_connection_id = None
        else:
            raise QuoteOutcomeRepointed(
                f"This outcome already describes ERP quote "
                f"{row.quote_document_ref}; it cannot be moved onto "
                f"{quote_document_ref}. Record the second quote's outcome "
                "against its own reference.")

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
    if quote_document_ref is not None and row.quote_document_ref is None:
        # Both keys on one row is the *normal* shape for a platform quote that
        # was pushed to the ERP, and it is the only thing that stops the same
        # quote being counted twice — once from the row a person wrote and once
        # from the document the pull read.
        #
        # But the reference is unique per organization, and the guard above only
        # checks *this* row's claim to it. Another row may already hold it —
        # somebody recorded the ERP quote from the Unanswered worklist before
        # the platform quote was pushed — and writing it here raised an
        # IntegrityError at the outer commit, after the Zoho estimate had been
        # created, rolling the whole request back with a 500. Checked here, it
        # is the domain refusal the caller already handles.
        # Qualified by the company, like the constraint behind it: with two
        # connected books issuing ``SQ-1001``, an unqualified guard refused the
        # second book's outcome as "already recorded" about the first book's
        # quote. A row that names no company still contests every one of that
        # reference, because nothing on it says which book it meant.
        held = session.scalars(
            select(models.QuoteOutcome).where(
                models.QuoteOutcome.organization_id == org,
                models.QuoteOutcome.quote_document_ref == quote_document_ref,
                or_(models.QuoteOutcome.quote_document_connection_id.is_(None),
                    qualifier is None,
                    models.QuoteOutcome.quote_document_connection_id == qualifier),
                models.QuoteOutcome.quote_outcome_id
                != row.quote_outcome_id)).first()
        if held is not None:
            raise QuoteOutcomeRepointed(
                f"ERP quote {quote_document_ref} already has a recorded "
                f"outcome of its own. Record this quote's outcome against its "
                f"own reference, or correct the existing row.")
        row.quote_document_ref = quote_document_ref
    if (quote_document_ref is not None and row.quote_document_ref == quote_document_ref
            and qualifier and row.quote_document_connection_id is None):
        # The same reference, now with the company it is unique in. Filled on
        # a row that lacked it and never changed on one that has it — a
        # qualifier is part of the identity, and rewriting it would be the
        # repoint the guard above refuses.
        row.quote_document_connection_id = qualifier
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
            s.value for s in allowed_transitions(
                QuoteOutcomeStatus(row.status),
                names_erp_document=bool(row.quote_document_ref))),
        "loss_reasons": [r.value for r in SELECTABLE_LOSS_REASONS],
    }


def get_outcome(session: Session, org: str, quote_id: str) -> Optional[models.QuoteOutcome]:
    return session.scalar(
        select(models.QuoteOutcome).where(
            models.QuoteOutcome.organization_id == org,
            models.QuoteOutcome.quote_id == quote_id))
