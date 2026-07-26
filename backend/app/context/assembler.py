"""Assemble a permission-scoped ContextBundle from a deterministic signal.

Only relevant, compact information is included (no raw DB dumps). RESTRICTED facts
(cost/margin) are removed for a salesperson recipient — absent, not masked — so
they never reach the AI. The set of visible facts also bounds the numbers the AI
may cite (grounding).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..domain import models
from ..domain.enums import RESTRICTED_FACT_FIELDS, Role, SubjectEntityType
from ..signals.config import SignalThresholds, load_thresholds
from .bundle import ContextBundle, FactView, SignalView

_MAX_FACTS = 40


def _is_restricted(path: str) -> bool:
    leaf = path.split(".")[-1].split("[")[0]
    return leaf in RESTRICTED_FACT_FIELDS or any(tok in path for tok in ("cost", "margin"))


def _flatten(prefix: str, value: Any, out: list[tuple[str, Any]]) -> None:
    if len(out) >= _MAX_FACTS:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else k, v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value[:5]):
            _flatten(f"{prefix}[{i}]", v, out)
    else:
        out.append((prefix, value))


def _policies(decision_type: str, th: SignalThresholds) -> list[str]:
    if decision_type == "CUSTOMER_DECLINE":
        return [f"Decline threshold: recent revenue down ≥ {int(th.decline_drop_pct*100)}% "
                f"vs prior {th.comparison_period_days}d (≥{th.decline_min_prior_orders} prior "
                f"orders, ≥{th.decline_min_history_months}mo history)."]
    if decision_type == "CUSTOMER_DORMANCY":
        return [f"Dormancy threshold: gap > {th.dormancy_interval_multiplier}× the customer's "
                f"typical interval (≥{th.dormancy_min_orders} orders to estimate cadence)."]
    if decision_type == "MARGIN_DETERIORATION":
        return [f"Margin threshold: margin down > {int(th.margin_drop_points*100)} points vs baseline."]
    if decision_type == "COST_PASS_THROUGH":
        return [f"Cost threshold: latest cost up > {int(th.cost_increase_pct*100)}% while price lagged."]
    return []


def assemble_from_signal(session: Session, signal: models.Signal, recipient_role: Role,
                         thresholds: SignalThresholds | None = None) -> ContextBundle:
    th = thresholds or load_thresholds()
    is_sales = recipient_role is Role.SALESPERSON

    # subject label from the read model (scoped to the signal's org)
    subject_label = signal.subject_entity_id
    if signal.subject_entity_type == SubjectEntityType.CUSTOMER.value:
        row = session.get(models.Customer, signal.subject_entity_id)
        subject_label = row.name if row else signal.subject_entity_id
    elif signal.subject_entity_type == SubjectEntityType.PRODUCT.value:
        row = session.get(models.Product, signal.subject_entity_id)
        subject_label = row.name if row else signal.subject_entity_id

    # flatten metrics → facts, redacting RESTRICTED for a salesperson
    flat: list[tuple[str, Any]] = []
    _flatten("", signal.metrics or {}, flat)
    facts: list[FactView] = [FactView(label="subject", value=subject_label)]
    redactions: list[str] = []
    for label, value in flat:
        if value is None:
            continue
        if is_sales and _is_restricted(label):
            redactions.append(label)
            continue
        facts.append(FactView(label=label, value=value))

    suff = signal.sufficiency or {}
    unknowns = [{"field": f, "reason": "not available in source data"}
                for f in suff.get("missing_fields", [])]
    for a in suff.get("anomalies", []):
        unknowns.append({"field": "data_quality", "reason": f"{a.get('code')}: {a.get('detail')}"})

    return ContextBundle(
        decision_type=signal.signal_type,   # signal families map 1:1 to proactive decision types
        organization_id=signal.organization_id,
        subject_ref={"entity_type": signal.subject_entity_type,
                     "entity_id": signal.subject_entity_id, "label": subject_label},
        recipient_role=recipient_role.value,
        permitted_data_classes=(["OPERATIONAL"] if is_sales
                                else ["OPERATIONAL", "RESTRICTED"]),
        redactions_applied=redactions,
        signals=[SignalView(signal_id=signal.signal_id, signal_type=signal.signal_type,
                            subject_entity_type=signal.subject_entity_type,
                            subject_entity_id=signal.subject_entity_id,
                            severity_base=signal.severity_base)],
        facts=facts[:_MAX_FACTS],
        evidence_sufficiency={"level": suff.get("level", "SUFFICIENT"),
                              "reasons": suff.get("reasons", [])},
        unknowns=unknowns,
        policies=_policies(signal.signal_type, th),
        evidence_refs=signal.evidence_refs or [],
    )
