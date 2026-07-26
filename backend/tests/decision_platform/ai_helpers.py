"""Helpers to build ContextBundles for AI-layer unit tests (no DB)."""
from __future__ import annotations

from app.context.bundle import ContextBundle, FactView, SignalView


def make_bundle(*, decision_type="CUSTOMER_DECLINE", level="SUFFICIENT",
                facts=None, signal_id="sig1", redactions=None,
                role="SALESPERSON") -> ContextBundle:
    facts = facts if facts is not None else [("subject", "Acme"), ("pct_change", -0.4),
                                             ("baseline_revenue", 30000.0),
                                             ("recent_revenue", 12000.0)]
    return ContextBundle(
        decision_type=decision_type, organization_id="org_test",
        subject_ref={"entity_type": "CUSTOMER", "entity_id": "c1", "label": "Acme"},
        recipient_role=role,
        permitted_data_classes=["OPERATIONAL"] if role == "SALESPERSON"
        else ["OPERATIONAL", "RESTRICTED"],
        redactions_applied=redactions or [],
        signals=[SignalView(signal_id=signal_id, signal_type=decision_type,
                            subject_entity_type="CUSTOMER", subject_entity_id="c1",
                            severity_base=60)],
        facts=[FactView(label=k, value=v) for k, v in facts],
        evidence_sufficiency={"level": level, "reasons": []},
        unknowns=[], policies=[], evidence_refs=[],
    )
