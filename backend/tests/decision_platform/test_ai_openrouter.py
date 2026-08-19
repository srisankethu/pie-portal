"""OpenRouter: the gateway provider, and the three ways it is not OpenAI.

OpenRouter speaks the OpenAI Chat Completions shape, so ``OpenRouterProvider``
subclasses ``OpenAIProvider`` rather than copying the request. These tests pin
the parts that would silently rot if that subclassing drifted — the endpoint it
posts to, the output-cap field it uses, and the fact that an error returned with
HTTP 200 raises rather than reading as an empty answer.

Nothing here opens a socket. ``httpx.post`` is replaced, which is also the only
way to assert on a request body without sending one.
"""
from __future__ import annotations

import httpx
import pytest

from app.ai import byok
from app.ai.openrouter_provider import OpenRouterProvider
from app.ai.provider import (
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    build_provider,
    provider_status,
    select_provider,
)
from app.config import settings


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


def _capture(monkeypatch, status_code: int = 200, body: dict | None = None) -> dict:
    """Replace ``httpx.post`` and hand back the call it recorded."""
    seen: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        return _Response(status_code, body if body is not None else {
            "choices": [{"message": {"content": " ok "}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


# ── registration: the gateway is a first-class provider everywhere ───────────
def test_openrouter_is_a_provider_an_organization_can_bring_a_key_for():
    assert "openrouter" in byok.PROVIDERS
    assert byok.env_key_name("openrouter") == "OPENROUTER_API_KEY"
    assert byok.default_model("openrouter") == settings.OPENROUTER_MODEL


def test_the_environment_can_select_it(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "openrouter")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-not-a-real-key")

    provider = select_provider()
    assert provider.name == "openrouter"
    assert provider.model == settings.OPENROUTER_MODEL

    status = provider_status()
    assert status["live"] is True and status["detail"] is None


def test_without_a_key_it_degrades_to_the_mock_and_names_the_variable(monkeypatch):
    """The same containment every other provider gets — a missing key is a
    deterministic narrative and a readable reason, never a 500."""
    monkeypatch.setattr(settings, "AI_PROVIDER", "openrouter")
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

    assert select_provider().name == "mock"
    status = provider_status()
    assert status["effective"] == "mock" and status["live"] is False
    assert "OPENROUTER_API_KEY" in status["detail"]


def test_build_provider_raises_for_the_caller_that_wants_the_failure(monkeypatch):
    """``/test`` in Settings must learn that the key is missing, not get a mock."""
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    with pytest.raises(ProviderUnavailable):
        build_provider("openrouter")


# ── the request: OpenAI's shape, OpenRouter's spelling ───────────────────────
def test_the_call_goes_to_openrouters_chat_completions_endpoint(monkeypatch):
    seen = _capture(monkeypatch)
    OpenRouterProvider(api_key="sk-or-v1-x", model="openai/gpt-4o").complete("sys", "usr")

    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer sk-or-v1-x"
    assert seen["payload"]["model"] == "openai/gpt-4o"
    assert seen["payload"]["messages"] == [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert seen["payload"]["temperature"] == 0


def test_the_output_cap_uses_the_parameter_openrouter_documents(monkeypatch):
    """``max_completion_tokens`` is OpenAI's spelling. OpenRouter normalises
    ``max_tokens`` across every upstream vendor and forwards what it does not
    recognise, so the OpenAI-only name reaches — and is rejected by — a
    non-OpenAI model."""
    seen = _capture(monkeypatch)
    OpenRouterProvider(api_key="sk-or-v1-x").complete("sys", "usr")

    assert seen["payload"]["max_tokens"] == settings.AI_MAX_TOKENS
    assert "max_completion_tokens" not in seen["payload"]


def test_the_openai_provider_still_sends_its_own_parameter(monkeypatch):
    """The subclassing must not have changed the path it was extracted from."""
    from app.ai.openai_provider import OpenAIProvider

    seen = _capture(monkeypatch)
    OpenAIProvider(api_key="sk-x", model="gpt-4o-mini").complete("sys", "usr")

    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["payload"]["max_completion_tokens"] == settings.AI_MAX_TOKENS
    assert "max_tokens" not in seen["payload"]


def test_usage_is_read_back_so_a_call_can_be_costed(monkeypatch):
    _capture(monkeypatch)
    p = OpenRouterProvider(api_key="sk-or-v1-x")
    assert p.complete("sys", "usr") == "ok"
    assert p.last_usage == {"input_tokens": 11, "output_tokens": 3}


# ── failure is reported as failure ───────────────────────────────────────────
def test_an_error_envelope_returned_with_http_200_is_a_provider_error(monkeypatch):
    """OpenRouter answers 200 with an ``error`` body when the upstream vendor
    refuses — no credit, no provider for the model, a moderation block. Read as
    text that is ``""``, which the contract layer records as a model that said
    nothing rather than a provider that failed. Absence of an answer is not an
    answer (CLAUDE.md §1)."""
    _capture(monkeypatch, body={"error": {"code": 402, "message": "Insufficient credits"}})

    with pytest.raises(ProviderError) as e:
        OpenRouterProvider(api_key="sk-or-v1-x").complete("sys", "usr")
    assert "Insufficient credits" in str(e.value)


def test_an_error_alongside_a_real_answer_does_not_discard_the_answer(monkeypatch):
    """A per-provider note beside a filled choice is metadata, not a failure."""
    _capture(monkeypatch, body={
        "choices": [{"message": {"content": "the reading"}}],
        "error": {"code": 200, "message": "one upstream attempt was retried"},
    })
    assert OpenRouterProvider(api_key="sk-or-v1-x").complete("s", "u") == "the reading"


@pytest.mark.parametrize("status_code,expected", [
    (429, ProviderUnavailable), (503, ProviderUnavailable), (401, ProviderError)])
def test_http_failures_are_typed_so_the_decision_layer_can_degrade(
        monkeypatch, status_code, expected):
    _capture(monkeypatch, status_code=status_code, body={})
    with pytest.raises(expected):
        OpenRouterProvider(api_key="sk-or-v1-x").complete("sys", "usr")


def test_a_timeout_is_a_timeout(monkeypatch):
    def fake_post(*a, **k):
        raise httpx.TimeoutException("too slow")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(ProviderTimeout):
        OpenRouterProvider(api_key="sk-or-v1-x").complete("sys", "usr")


def test_the_key_never_travels_in_the_url(monkeypatch):
    """It goes in a header, so it cannot land in an access log."""
    seen = _capture(monkeypatch)
    OpenRouterProvider(api_key="sk-or-v1-secret").complete("sys", "usr")
    assert "secret" not in seen["url"]
