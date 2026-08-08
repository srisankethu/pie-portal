"""Helpers to build ContextBundles for AI-layer unit tests (no DB)."""
from __future__ import annotations

import json

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


def valid_output(**over) -> str:
    """A model response that passes every gate — the baseline the rejection
    tests vary one field of.

    The explanation quotes ``40%`` deliberately: it grounds against
    ``pct_change`` -0.4, and the specificity gate rejects a surfaced reading of
    numeric facts that quotes nothing. A baseline that could not pass check 6
    would make every test built on it a test of check 6.

    One copy, in the helper module, because the two AI test files had grown one
    each and had to be edited together.
    """
    base = {"should_surface": True, "concise_title": "Revenue decline: Acme",
            "explanation": "Revenue is down 40% versus the prior period.",
            "recommended_action": "Review the account.", "priority_adjustment": 5,
            "cannot_recommend_reliably": False, "cited_fact_labels": ["pct_change"],
            "cited_signal_ids": ["sig1"]}
    base.update(over)
    return json.dumps(base)
