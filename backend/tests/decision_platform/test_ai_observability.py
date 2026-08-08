"""WS3 — AI observability: failure taxonomy, telemetry, and ops metrics.

Acceptance criteria under test:
  * every AI call — including cache hits and up-front suppressions — produces
    exactly one telemetry record;
  * every failure reason code has a test that provokes it;
  * the two-sided health band reports both an excessive and a suspiciously
    absent degraded rate.
"""
from __future__ import annotations

import json

import pytest

from app.ai.contract import AIValidationError, validate_output
from app.ai.interpret import interpret
from app.ai.metrics import health_band, summarize
from app.ai.mock_provider import MockProvider
from app.ai.telemetry import estimate_cost
from app.config import settings
from app.domain import models
from app.domain.enums import AiFailureReason, AiStatus

from .ai_helpers import make_bundle

METRICS = {"pct_change": -0.4, "baseline_revenue": 30000, "recent_revenue": 12000}


def _out(**over):
    base = {"should_surface": True, "concise_title": "Revenue decline: Acme",
            "explanation": "Revenue is materially down versus the prior period.",
            "recommended_action": "Review the account.", "priority_adjustment": 5,
            "cannot_recommend_reliably": False, "cited_fact_labels": ["pct_change"],
            "cited_signal_ids": ["sig1"]}
    base.update(over)
    return json.dumps(base)


def _run(mode, **bundle_kw):
    return interpret(make_bundle(**bundle_kw), MockProvider(mode=mode),
                     signal_type="CUSTOMER_DECLINE", metrics=METRICS,
                     subject_label="Acme")


# ── failure taxonomy: one provoking test per reason code ────────────────────
def test_reason_schema_invalid():
    with pytest.raises(AIValidationError) as e:
        validate_output("not json {", make_bundle())
    assert e.value.reason is AiFailureReason.SCHEMA_INVALID
    assert e.value.code == "malformed_output"       # legacy code preserved


def test_reason_unknown_fact_label():
    with pytest.raises(AIValidationError) as e:
        validate_output(_out(cited_fact_labels=["ghost_fact"]), make_bundle())
    assert e.value.reason is AiFailureReason.UNKNOWN_FACT_LABEL


def test_reason_unknown_signal_id():
    with pytest.raises(AIValidationError) as e:
        validate_output(_out(cited_signal_ids=["ghost_signal"]), make_bundle())
    assert e.value.reason is AiFailureReason.UNKNOWN_SIGNAL_ID


def test_reason_ungrounded_number():
    # 777 bears no scaled relationship to any supplied fact
    with pytest.raises(AIValidationError) as e:
        validate_output(_out(explanation="Offer a discount of 777."), make_bundle())
    assert e.value.reason is AiFailureReason.UNGROUNDED_NUMBER


def test_reason_scale_violation():
    """A supplied fact at the wrong scale is distinguishable from a fabrication:
    12000 (a real fact) rendered as 1200000 is a units/scale problem."""
    with pytest.raises(AIValidationError) as e:
        validate_output(_out(explanation="Revenue fell to 1200000."), make_bundle())
    assert e.value.reason is AiFailureReason.SCALE_VIOLATION
    assert e.value.code == "ungrounded_number"      # still rejected as ungrounded


def test_reason_priority_out_of_range_is_recorded_not_fatal():
    corrections: list = []
    out = validate_output(_out(priority_adjustment=999), make_bundle(), corrections)
    assert out.priority_adjustment == settings.AI_PRIORITY_ADJUST_BOUND   # repaired
    assert AiFailureReason.PRIORITY_OUT_OF_RANGE in corrections           # recorded


def test_reason_action_text_on_withheld_is_recorded_not_fatal():
    corrections: list = []
    out = validate_output(
        _out(cannot_recommend_reliably=True, recommended_action="Do X now."),
        make_bundle(), corrections)
    assert out.recommended_action == ""                                   # repaired
    assert AiFailureReason.ACTION_TEXT_ON_WITHHELD in corrections         # recorded


def test_reason_provider_timeout_and_unavailable():
    assert _run("timeout").telemetry.failure_reason == \
        AiFailureReason.PROVIDER_TIMEOUT.value
    assert _run("unavailable").telemetry.failure_reason == \
        AiFailureReason.PROVIDER_UNAVAILABLE.value


# ── telemetry: exactly one record per interpretation, on every path ─────────
def test_telemetry_present_on_success_with_usage_and_cost():
    r = _run("ok")
    t = r.telemetry
    assert t is not None and t.ai_status == AiStatus.OK.value
    assert t.provider_called is True and t.attempts == 1
    assert t.input_tokens and t.output_tokens
    assert t.estimated_cost_usd is not None and t.estimated_cost_usd > 0
    assert t.latency_ms is not None and t.context_hash.startswith("cx_")
    assert t.failure_reason is None


def test_telemetry_on_suppressed_without_provider_call():
    p = MockProvider(mode="ok")
    r = interpret(make_bundle(level="INSUFFICIENT"), p, signal_type="CUSTOMER_DECLINE",
                  metrics=METRICS, subject_label="Acme")
    assert p.calls == 0                                   # no inference, as before
    assert r.telemetry is not None                        # but still observed
    assert r.telemetry.provider_called is False
    assert r.telemetry.ai_status == AiStatus.SUPPRESSED.value
    assert r.telemetry.estimated_cost_usd is None         # unknown, not zero


def test_telemetry_records_retry_attempts():
    r = interpret(make_bundle(), MockProvider(mode="malformed_then_ok"),
                  signal_type="CUSTOMER_DECLINE", metrics=METRICS, subject_label="Acme")
    assert r.status is AiStatus.OK and r.telemetry.attempts == 2


def test_telemetry_degraded_carries_reason():
    r = _run("hallucinate")
    assert r.status is AiStatus.DEGRADED
    assert r.telemetry.failure_reason in {
        AiFailureReason.UNGROUNDED_NUMBER.value, AiFailureReason.SCALE_VIOLATION.value}


def test_estimate_cost_unknown_usage_is_none_not_zero():
    assert estimate_cost(None, None) is None
    assert estimate_cost(1_000_000, 0) == pytest.approx(settings.AI_COST_PER_MTOK_INPUT)


# ── persistence: one row per call, including cache hits ─────────────────────
def _seed_org(session):
    from app.seed import ensure_org_and_users
    return ensure_org_and_users(session)


def test_decision_service_writes_one_row_per_call_and_cache_hit(session):
    from app.decisions.service import DecisionService
    from .signal_fixtures import declining_customer

    org = _seed_org(session)
    session.add(models.Customer(customer_id="c_decline", organization_id=org,
                                external_id="c_decline", name="Acme",
                                assigned_user_id="usr_sales", status="ACTIVE"))
    session.add(models.Product(product_id="p1", organization_id=org,
                               external_id="p1", name="Insert"))
    for s in declining_customer():
        session.add(models.SalesTxn(
            organization_id=org, external_ref=s.external_ref, customer_id=s.customer_id,
            product_id=s.product_id, date=s.date, qty=s.qty, unit_price=s.unit_price,
            line_revenue=s.line_revenue, source_ref=s.source_ref))
    session.flush()

    from app.signals.engine import run_detectors
    run_detectors(session, org)
    svc = DecisionService(session, org, provider=MockProvider("ok"))
    svc.generate()
    session.flush()
    first = session.query(models.AiCallLog).count()
    assert first >= 1
    assert all(r.organization_id == org for r in session.query(models.AiCallLog).all())

    # second run over unchanged context: no new inference, but still observed
    svc2 = DecisionService(session, org, provider=MockProvider("ok"))
    out = svc2.generate()
    session.flush()
    assert out["skipped"] >= 1
    rows = session.query(models.AiCallLog).all()
    assert len(rows) > first
    assert any(r.cache_hit for r in rows)


def test_telemetry_disabled_writes_nothing(session, monkeypatch):
    from app.repositories import AiTelemetryRepository
    from app.ai.telemetry import CallTelemetry

    _seed_org(session)
    monkeypatch.setattr(settings, "AI_TELEMETRY_ENABLED", False)
    repo = AiTelemetryRepository(session, settings.DEFAULT_ORG_ID)
    assert repo.record(CallTelemetry(decision_type="X", ai_status="OK")) is None
    session.flush()
    assert session.query(models.AiCallLog).count() == 0


# ── metrics + two-sided health band ────────────────────────────────────────
def _log(**kw):
    base = dict(ai_call_log_id=None, organization_id="org", decision_type="CUSTOMER_DECLINE",
                ai_status="OK", provider_called=True, cache_hit=False, attempts=1,
                latency_ms=100, input_tokens=1000, output_tokens=100,
                estimated_cost_usd=0.001, failure_reason=None, corrections=[])
    base.update(kw)
    base.pop("ai_call_log_id")
    return models.AiCallLog(**base)


def test_summarize_rates_costs_and_reasons():
    rows = ([_log() for _ in range(7)]
            + [_log(ai_status="DEGRADED", failure_reason="UNGROUNDED_NUMBER") for _ in range(2)]
            + [_log(ai_status="SUPPRESSED", provider_called=False,
                    estimated_cost_usd=None, latency_ms=None)]
            + [_log(cache_hit=True, provider_called=False)])
    s = summarize(rows, days=7)
    assert s["calls"] == 11
    assert s["rates"]["degraded"] == pytest.approx(2 / 11, abs=1e-3)
    assert s["failure_reasons"]["UNGROUNDED_NUMBER"] == 2
    assert s["rates"]["cache_hit"] == pytest.approx(1 / 11, abs=1e-3)
    # calls with unknown usage are excluded from the cost total, not zero-filled
    assert s["cost"]["calls_with_known_usage"] == 10
    assert s["cost"]["total_estimated"] > 0


def test_health_band_flags_high_and_suspiciously_low(monkeypatch):
    monkeypatch.setattr(settings, "AI_HEALTH_MIN_SAMPLE", 10)
    monkeypatch.setattr(settings, "AI_DEGRADED_RATE_MAX", 0.25)
    monkeypatch.setattr(settings, "AI_DEGRADED_RATE_MIN", 0.005)
    assert health_band(0.60, 100)[0] == "HIGH"
    assert health_band(0.0, 100)[0] == "SUSPICIOUSLY_LOW"
    assert health_band(0.10, 100)[0] == "OK"
    # below the minimum sample no inference is drawn in either direction
    assert health_band(0.0, 3)[0] == "INSUFFICIENT_DATA"


@pytest.fixture()
def api_client():
    """A minimal app wired to an isolated DB (mirrors the authz test harness)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.routers import internal, platform_auth
    from app.seed import ensure_org_and_users

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(internal.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def test_ai_metrics_endpoint_is_owner_only(api_client):
    client = api_client
    from .test_api_authz import _hdr, _login

    sales = _hdr(_login(client, "r.nair@sanketh.in"))
    manager = _hdr(_login(client, "m.rao@sanketh.in"))
    owner = _hdr(_login(client, "s.menon@sanketh.in"))
    assert client.get("/api/v1/internal/ai-metrics", headers=sales).status_code == 403
    assert client.get("/api/v1/internal/ai-metrics", headers=manager).status_code == 403
    r = client.get("/api/v1/internal/ai-metrics", headers=owner)
    assert r.status_code == 200
    body = r.json()
    assert "7d" in body["windows"] and "30d" in body["windows"]
    assert "health" in body["windows"]["7d"]
