"""On-demand QUOTE_CONTEXT decision support (spec §9.5).

Called while a salesperson prepares a quote. For a (customer, product[, proposed
price]) it assembles the deterministic commercial facts, interprets them through
the AI layer (validated, degradable), and — so the recommendation and the
salesperson's eventual decision are captured — persists a QUOTE_CONTEXT Signal +
Decision into the existing Decision Store.

Responsibility boundaries: PIE identifies/resolves the product (upstream, in the
Quote Builder); the deterministic layer computes every number; the AI layer only
interprets; the human still chooses the final price and product. Nothing here
sets a price, selects a product, or sends anything.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.interpret import interpret
from ..ai.provider import AIProvider, select_provider
from ..trust import disclosure, rehydrate
from ..ai.telemetry import CallTelemetry
from ..authz import Principal
from ..commercial import ownership
from ..context.quote_bundle import _label, build_quote_bundle
from ..domain import models
from ..domain.enums import DecisionStatus, PriorityBand, Role, SubjectEntityType
from ..config import settings
from ..repositories import AiTelemetryRepository, DecisionRepository
from ..signals.aggregates import load_snapshot
from ..signals.base import Snapshot
from ..signals.config import SignalThresholds, load_thresholds
from ..signals.quote_context import assemble


def _norm(s: str) -> str:
    """Normalize a code/name for tolerant matching: lowercase alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _resolve_customer(session: Session, org: str, ref: str) -> Optional[models.Customer]:
    if not ref:
        return None
    rows = session.scalars(
        select(models.Customer).where(models.Customer.organization_id == org)).all()
    # exact id / external_id
    for c in rows:
        if ref in (c.customer_id, c.external_id):
            return c
    # exact (case-insensitive) name, then normalized containment
    n = _norm(ref)
    for c in rows:
        if _norm(c.name) == n:
            return c
    for c in rows:
        cn = _norm(c.name)
        if cn and (cn in n or n in cn):
            return c
    return None


def _resolve_product(session: Session, org: str, ref: str) -> Optional[models.Product]:
    if not ref:
        return None
    rows = session.scalars(
        select(models.Product).where(models.Product.organization_id == org)).all()
    for p in rows:
        if ref in (p.product_id, p.external_id):
            return p
    n = _norm(ref)
    if not n:
        return None
    for p in rows:
        if _norm(p.name) == n:
            return p
    # normalized containment either way (handles "CNMG 120408-MP" vs
    # "CNMG 120408-MP insert"); require a reasonably specific token to avoid
    # matching on a trivial shared prefix.
    best: Optional[models.Product] = None
    for p in rows:
        pn = _norm(p.name)
        if pn and len(n) >= 4 and (n in pn or pn in n):
            if best is None or abs(len(pn) - len(n)) < abs(len(_norm(best.name)) - len(n)):
                best = p
    return best


def _decision_key(org: str, customer_id: str, product_ids: list[str]) -> str:
    blob = f"{org}|QUOTE_CONTEXT|{customer_id}|{','.join(sorted(product_ids))}".encode()
    return "dk_" + hashlib.sha256(blob).hexdigest()[:16]


def _flat_metrics(assembled: dict[str, Any]) -> dict[str, Any]:
    """A flat, audit-friendly projection of the assembled facts for the persisted
    Signal (the platform detail screen reads facts from ``signal.metrics``)."""
    out: dict[str, Any] = {}
    for f in assembled.get("customer_facts", []):
        out[f["label"]] = f["value"]
    items = assembled.get("items", [])
    for item in items:
        prefix = "" if len(items) <= 1 else f"{item.get('product_id')}."
        for f in item.get("facts", []):
            out[f"{prefix}{f['label']}"] = f["value"]
    return out


def _band(score: int) -> str:
    if score >= 66:
        return PriorityBand.HIGH.value
    if score >= 33:
        return PriorityBand.MEDIUM.value
    return PriorityBand.LOW.value


def quote_support(
    session: Session,
    principal: Principal,
    *,
    customer_ref: str,
    product_refs: list[str],
    proposed_price: Optional[float] = None,
    as_of: Optional[date] = None,
    provider: Optional[AIProvider] = None,
    thresholds: Optional[SignalThresholds] = None,
    persist: bool = True,
) -> dict[str, Any]:
    org = principal.organization_id
    th = thresholds or load_thresholds()
    provider = provider or select_provider(session, org)

    customer = _resolve_customer(session, org, customer_ref)
    resolved_products = [(_resolve_product(session, org, r), r) for r in product_refs]
    product_models = [p for p, _ in resolved_products if p is not None]
    unresolved_products = [r for p, r in resolved_products if p is None]

    resolution = {
        "customer_resolved": customer is not None,
        "customer_label": customer.name if customer else None,
        "products_resolved": [p.name for p in product_models],
        "products_unresolved": unresolved_products,
    }

    # No customer match ⇒ no commercial history to stand on (new customer / no
    # match). Return an honest "insufficient" shape without inventing anything.
    if customer is None:
        return {
            "resolution": resolution,
            "customer_facts": [],
            "items": [],
            "unknowns": [{"field": "customer",
                          "reason": "Customer not found in commercial history — treat as a new "
                                    "relationship; no prior context to weigh."}],
            "interpretation": {
                "status": "SUPPRESSED",
                "title": "New or unmatched customer",
                "recommendation": None,
                "explanation": "No commercial history matches this customer. There is nothing to "
                               "interpret; price on first principles and your judgement.",
                "caveat": None,
                "should_surface": True,
            },
            "confidence": {"evidence_sufficiency": "INSUFFICIENT", "reasons": []},
            "restricted_absent": principal.role is Role.SALESPERSON,
            "decision_id": None,
        }

    product_ids = [p.product_id for p in product_models]
    # Bounded to what ``assemble`` actually reads: *every* line this customer
    # ever bought — their buying rhythm is measured from all of it, not just
    # the items being quoted — and costs for the quoted items only. Unbounded,
    # a quote screen loaded the organization's entire trading history on every
    # keystroke.
    snapshot: Snapshot = load_snapshot(session, org,
                                       sales_for_customers=[customer.customer_id],
                                       costs_for_products=product_ids)
    # Still the whole book's last trading day: the loader supplies it from its
    # own query rather than from the rows above, so bounding the load does not
    # move the reference date to whenever this one customer last bought.
    ref_date = as_of or snapshot.as_of() or date.today()

    assembled = assemble(snapshot, customer.customer_id, product_ids, th, ref_date)
    assembled["customer_label"] = customer.name
    # keep product labels for the bundle
    label_by_id = {p.product_id: p.name for p in product_models}
    for item in assembled.get("items", []):
        item["label"] = label_by_id.get(item["product_id"], item["product_id"])
    for r in unresolved_products:
        assembled.setdefault("unknowns", []).append(
            {"field": f"item:{r}", "reason": f"'{r}' not found in sales history — no prior "
                                             f"price/margin context for this item."})

    bundle = build_quote_bundle(
        assembled, principal.role,
        proposed_price=proposed_price,
        proposed_product_id=product_models[0].product_id if len(product_models) == 1 else None,
    )

    # Cost control: if an OPEN decision for this exact (customer, items) already
    # holds an interpretation of identical context, reuse it — no second model
    # call, no duplicate signal row. Opening the same drawer repeatedly, or
    # re-nudging the same price, must not spend AI budget or grow the audit log.
    repo = DecisionRepository(session, org)
    existing = repo.get_by_key(_decision_key(org, customer.customer_id, product_ids)) if persist else None
    cached = (existing is not None and existing.status == DecisionStatus.OPEN.value
              and (existing.ai or {}).get("context_hash") == bundle.context_hash())

    tel_repo = AiTelemetryRepository(session, org)
    decision_id = None
    if cached:
        ai = existing.ai or {}
        interp = {"status": ai.get("status", "PENDING"), "title": ai.get("title"),
                  "recommendation": ai.get("recommendation") or None,
                  "explanation": ai.get("explanation"), "caveat": ai.get("caveat"),
                  "should_surface": ai.get("should_surface", True), "model": ai.get("model")}
        conf = existing.confidence or {}
        decision_id = existing.decision_id
        tel_repo.record(CallTelemetry(
            decision_type="QUOTE_CONTEXT", ai_status=ai.get("status", "PENDING"),
            provider=getattr(provider, "name", ""), model=getattr(provider, "model", ""),
            prompt_version=settings.PROMPT_VERSION, context_hash=bundle.context_hash(),
            subject_entity_id=customer.customer_id, recipient_role=principal.role.value,
        ), cache_hit=True)
    else:
        # The pseudonym goes in — including to the deterministic fallback, so
        # both paths produce the same shape of text and only one of them has to
        # be re-hydrated on the way out.
        result = interpret(bundle, provider, signal_type="QUOTE_CONTEXT",
                           metrics=_flat_metrics(assembled),
                           subject_label=bundle.subject_ref.get("label", ""))
        rehydrate.result(result, bundle.display_names)
        disclosure.log_result(session, organization_id=org,
                              decision_type="QUOTE_CONTEXT", result=result)
        tel_repo.record(result.telemetry)
        interp = {"status": result.status.value, "title": result.concise_title,
                  "recommendation": result.recommended_action or None,
                  "explanation": result.explanation, "caveat": result.caveat,
                  "should_surface": result.should_surface, "model": result.model}
        conf = {"evidence_sufficiency": bundle.evidence_sufficiency.get("level"),
                "reasons": bundle.evidence_sufficiency.get("reasons", [])}
        if persist:
            decision_id = _persist(session, principal, customer, product_ids, assembled,
                                   bundle, result, ref_date, th)

    # facts split into the two clearly-separated regions the UI renders.
    is_sales = principal.role is Role.SALESPERSON
    customer_facts = _project_facts(assembled.get("customer_facts", []), is_sales)
    items_out = []
    for item in assembled.get("items", []):
        items_out.append({
            "product_id": item["product_id"],
            "label": item.get("label", item["product_id"]),
            "facts": _project_facts(item.get("facts", []), is_sales),
        })

    return {
        "resolution": resolution,
        "customer_facts": customer_facts,
        "items": items_out,
        "unknowns": bundle.unknowns,
        "interpretation": interp,
        "confidence": conf,
        "restricted_absent": is_sales and bool(bundle.redactions_applied),
        "decision_id": decision_id,
    }


def _project_facts(raw_facts: list[dict], is_sales: bool) -> list[dict]:
    """Facts for the client, RESTRICTED removed for a salesperson (absent, not
    masked), each carrying its data_class + source so the UI can label provenance."""
    out = []
    for f in raw_facts:
        if f.get("value") is None:
            continue
        dc = f.get("data_class", "OPERATIONAL")
        if is_sales and dc == "RESTRICTED":
            continue
        src = ", ".join(sorted({str(r.get("record_type") or "") for r in (f.get("source_refs") or [])
                                if r.get("record_type")}))
        out.append({"label": _label(f["label"]), "value": f["value"], "unit": f.get("unit"),
                    "data_class": dc, "source": src or "derived"})
    return out


def _persist(session: Session, principal: Principal, customer: models.Customer,
             product_ids: list[str], assembled: dict, bundle, result, ref_date: date,
             th: SignalThresholds) -> str:
    """Write-once Signal + upserted Decision so the recommendation and the
    salesperson's eventual accept/modify/reject feed the Decision Store."""
    org = principal.organization_id
    # write-once QUOTE_CONTEXT signal (audit of the exact facts interpreted)
    signal = models.Signal(
        organization_id=org, signal_type="QUOTE_CONTEXT",
        subject_entity_type=SubjectEntityType.CUSTOMER.value,
        subject_entity_id=customer.customer_id,
        detector_version="quote-context-1", threshold_config_version=th.version,
        window={"as_of": ref_date.isoformat(), "product_ids": product_ids},
        metrics=_flat_metrics(assembled), severity_base=10,
        evidence_refs=bundle.evidence_refs,
        sufficiency={"level": bundle.evidence_sufficiency.get("level"),
                     "reasons": bundle.evidence_sufficiency.get("reasons", [])})
    session.add(signal)
    session.flush()

    repo = DecisionRepository(session, org)
    key = _decision_key(org, customer.customer_id, product_ids)
    existing = repo.get_by_key(key)

    ai_obj = result.to_ai_dict()
    ai_obj["context_hash"] = bundle.context_hash()
    ai_obj["redactions_applied"] = bundle.redactions_applied

    d = existing or models.Decision(organization_id=org, decision_key=key)
    if existing is None:
        repo.add(d)
    d.decision_type = "QUOTE_CONTEXT"
    d.subject_entity_type = SubjectEntityType.CUSTOMER.value
    d.subject_entity_id = customer.customer_id
    # route to the requesting salesperson (so it lands in their scope), or the
    # account's effective owner when a manager/owner previews it — the person
    # it was assigned to where somebody assigned one, Zoho's salesperson
    # otherwise. See `commercial/ownership`.
    owner = ownership.owner_of(session, customer)
    d.assigned_user_id = (principal.user_id if principal.role is Role.SALESPERSON
                          else (owner.user_id if owner else None))
    d.assigned_role = Role.SALESPERSON.value
    d.detected_at = datetime.now(timezone.utc)
    d.signal_ids = [signal.signal_id]
    d.evidence_refs = bundle.evidence_refs
    d.ai = ai_obj
    base = 10
    d.priority_deterministic_base = base
    d.priority_ai_adjustment = 0
    d.priority_score = base
    d.priority_band = _band(base)
    d.confidence = {"evidence_sufficiency": bundle.evidence_sufficiency.get("level"),
                    "reasons": bundle.evidence_sufficiency.get("reasons", [])}
    if existing is None:
        d.status = DecisionStatus.OPEN.value
    session.flush()
    return d.decision_id

