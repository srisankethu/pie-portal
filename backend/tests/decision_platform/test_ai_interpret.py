"""Interpretation orchestration: every failure path degrades safely."""
from __future__ import annotations

from app.ai.interpret import interpret
from app.ai.mock_provider import MockProvider
from app.domain.enums import AiStatus

from .ai_helpers import make_bundle

METRICS = {"pct_change": -0.4, "baseline_revenue": 30000, "recent_revenue": 12000}


def _run(mode, **bundle_kw):
    b = make_bundle(**bundle_kw)
    return interpret(b, MockProvider(mode=mode), signal_type="CUSTOMER_DECLINE",
                     metrics=METRICS, subject_label="Acme")


def test_valid_recommendation():
    r = _run("ok")
    assert r.status is AiStatus.OK
    assert r.recommended_action and r.should_surface
    assert "sig1" in r.cited_signal_ids


def test_timeout_degrades_to_failed_but_actionable():
    r = _run("timeout")
    assert r.status is AiStatus.FAILED
    assert r.should_surface and r.recommended_action == ""
    assert "down 40%" in r.explanation.lower()          # deterministic template


def test_provider_unavailable_is_failed():
    assert _run("unavailable").status is AiStatus.FAILED


def test_malformed_output_degrades():
    r = _run("malformed")
    assert r.status is AiStatus.DEGRADED
    assert r.recommended_action == ""


def test_malformed_then_ok_retries_once():
    p = MockProvider(mode="malformed_then_ok")
    r = interpret(make_bundle(), p, signal_type="CUSTOMER_DECLINE",
                  metrics=METRICS, subject_label="Acme")
    assert r.status is AiStatus.OK and p.calls == 2


def test_hallucinated_number_rejected():
    r = _run("hallucinate")
    assert r.status is AiStatus.DEGRADED
    # no fabricated figure survives into the stored recommendation
    assert "999" not in r.explanation and "12345" not in r.explanation
    assert r.recommended_action == ""


def test_cited_unknown_fact_rejected():
    assert _run("cite_unknown_fact").status is AiStatus.DEGRADED


def test_prompt_injection_obeyed_is_contained():
    r = _run("injection_obeyed")
    assert r.status is AiStatus.DEGRADED
    assert "500000" not in r.explanation
    assert r.recommended_action == ""                    # no autonomous action leaks


def test_withheld_when_model_declines():
    r = _run("withheld")
    assert r.status is AiStatus.SUPPRESSED
    assert r.recommended_action == ""


def test_insufficient_evidence_withholds_without_calling_model():
    p = MockProvider(mode="ok")
    r = interpret(make_bundle(level="INSUFFICIENT"), p, signal_type="CUSTOMER_DECLINE",
                  metrics=METRICS, subject_label="Acme")
    assert r.status is AiStatus.SUPPRESSED
    assert p.calls == 0                                   # cost control: no inference
    assert r.recommended_action == ""
