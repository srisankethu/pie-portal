"""What the next decision run would cost, answered before it runs.

The estimate is only worth having if obtaining it is free. These tests hold that
line: the preflight must reach the same signals the service would, apply the same
cache and the same suppression, and call no provider at all while doing it.
"""
from __future__ import annotations

import pytest

from app.ai.mock_provider import MockProvider
from app.decisions.preflight import estimate
from app.decisions.service import DecisionService
from app.signals.engine import run_detectors

from .test_ai_decision_service import ORG, _seed_readmodel


class RefusingProvider:
    """Any call at all is the failure this suite is about."""

    name = "refusing"
    model = "none"

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("the preflight called a provider")


@pytest.fixture()
def seeded(session):
    _seed_readmodel(session)
    run_detectors(session, ORG)
    return session


def test_the_estimate_costs_nothing_to_obtain(seeded, monkeypatch):
    """No provider is built, none is called, and nothing is billed.

    Belt and braces, because an estimate that quietly performs the run it is
    estimating is worse than having none: ``select_provider`` and ``interpret``
    both explode if reached, and the telemetry table — one row per real
    interpretation — must be untouched afterwards.
    """
    import app.ai.interpret as interpret_module
    import app.ai.provider as provider_module
    from app.domain import models

    def _explode(*a, **k):
        raise AssertionError("the preflight reached the provider path")

    monkeypatch.setattr(provider_module, "select_provider", _explode)
    monkeypatch.setattr(interpret_module, "interpret", _explode)

    e = estimate(seeded, ORG)
    assert e["would_call_provider"] > 0
    assert e["estimated_input_tokens"] > 0
    assert seeded.query(models.AiCallLog).count() == 0


def test_it_predicts_the_number_of_calls_the_run_actually_makes(seeded):
    before = estimate(seeded, ORG)
    provider = MockProvider()
    DecisionService(seeded, ORG, provider=provider).generate()
    assert provider.calls == before["would_call_provider"], (
        "the preflight and the run disagree about how many calls a run is")


def test_a_second_run_is_predicted_as_cached_and_free(seeded):
    DecisionService(seeded, ORG, provider=MockProvider()).generate()
    after = estimate(seeded, ORG)
    assert after["would_call_provider"] == 0
    assert after["would_reuse_cached"] > 0
    assert after["estimated_cost_usd"] == 0.0

    # and the prediction holds when the run is actually repeated
    provider = RefusingProvider()
    DecisionService(seeded, ORG, provider=provider).generate()


def test_the_cost_follows_the_configured_rates(seeded, monkeypatch):
    from app.config import settings

    cheap = estimate(seeded, ORG)
    monkeypatch.setattr(settings, "AI_COST_PER_MTOK_INPUT",
                        settings.AI_COST_PER_MTOK_INPUT * 10)
    monkeypatch.setattr(settings, "AI_COST_PER_MTOK_OUTPUT",
                        settings.AI_COST_PER_MTOK_OUTPUT * 10)
    dear = estimate(seeded, ORG)
    assert dear["estimated_cost_usd"] == pytest.approx(cheap["estimated_cost_usd"] * 10,
                                                       rel=1e-6)
    assert dear["estimated_cost_per_decision_usd"] > 0


def test_it_reports_which_provider_would_really_run(seeded, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    e = estimate(seeded, ORG)
    assert e["provider"]["configured"] == "anthropic"
    assert e["provider"]["effective"] == "mock"


def test_an_organization_with_no_signals_estimates_zero(session):
    e = estimate(session, "org_empty")
    assert e["signals_considered"] == 0
    assert e["estimated_cost_usd"] == 0.0
    assert e["estimated_cost_per_decision_usd"] == 0.0


def test_readiness_endpoint_is_owner_only(api_client):
    from .test_api_authz import _hdr, _login

    client = api_client
    sales = _hdr(_login(client, "r.nair@pie.example"))
    manager = _hdr(_login(client, "m.rao@pie.example"))
    owner = _hdr(_login(client, "s.menon@pie.example"))

    assert client.get("/api/v1/internal/ai-readiness", headers=sales).status_code == 403
    assert client.get("/api/v1/internal/ai-readiness", headers=manager).status_code == 403
    r = client.get("/api/v1/internal/ai-readiness", headers=owner)
    assert r.status_code == 200
    body = r.json()
    assert body["provider"]["effective"] in ("mock", "anthropic")
    assert "estimated_cost_usd" in body and "rates" in body
