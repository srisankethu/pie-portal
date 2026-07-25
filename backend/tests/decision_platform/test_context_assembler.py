"""Context assembly: redaction (defense in depth), unknowns, grounding set."""
from __future__ import annotations

from app.context.assembler import assemble_from_signal
from app.domain import models
from app.domain.enums import Role


def _margin_signal(org="org_test"):
    return models.Signal(
        organization_id=org, signal_type="MARGIN_DETERIORATION",
        subject_entity_type="PRODUCT", subject_entity_id="p1",
        detector_version="v0", threshold_config_version="th_x",
        window={}, severity_base=50,
        metrics={"current_margin_pct": 0.12, "baseline_margin_pct": 0.30,
                 "recent_unit_cost": 70.0, "recent_unit_price": 80.0},
        evidence_refs=[{"record_type": "invoice", "record_id": "i1"}],
        sufficiency={"level": "SUFFICIENT", "missing_fields": [], "anomalies": []})


def test_manager_sees_restricted_facts(session):
    session.add(models.Product(product_id="p1", organization_id="org_test", name="Insert",
                               external_id="p1"))
    sig = _margin_signal()
    session.add(sig)
    session.flush()
    b = assemble_from_signal(session, sig, Role.SALES_MANAGER)
    labels = b.fact_labels()
    assert "current_margin_pct" in labels and "recent_unit_cost" in labels
    assert b.redactions_applied == []
    assert "RESTRICTED" in b.permitted_data_classes


def test_salesperson_gets_restricted_redacted(session):
    session.add(models.Product(product_id="p1", organization_id="org_test", name="Insert",
                               external_id="p1"))
    sig = _margin_signal()
    session.add(sig)
    session.flush()
    b = assemble_from_signal(session, sig, Role.SALESPERSON)
    labels = b.fact_labels()
    # restricted economics are ABSENT (not masked)
    assert "current_margin_pct" not in labels and "recent_unit_cost" not in labels
    assert b.redactions_applied                       # recorded for audit
    # and their numbers are not in the grounding set (can't be cited)
    assert 70.0 not in b.allowed_numbers()


def test_context_hash_is_stable(session):
    session.add(models.Product(product_id="p1", organization_id="org_test", name="Insert",
                               external_id="p1"))
    sig = _margin_signal()
    session.add(sig)
    session.flush()
    a = assemble_from_signal(session, sig, Role.SALES_MANAGER).context_hash()
    b = assemble_from_signal(session, sig, Role.SALES_MANAGER).context_hash()
    assert a == b and a.startswith("cx_")
