"""AI provider boundary — a thin, swappable seam (no framework, no agent).

``AIProvider.complete(system, user) -> raw_text`` is the entire contract. Two
implementations: ``MockProvider`` (deterministic, offline — dev/test default) and
``AnthropicProvider`` (live). Selection is config-driven.

**Selection never raises.** ``AI_PROVIDER=anthropic`` with no key used to raise
out of ``AnthropicProvider.__init__``, through ``select_provider()``, and out of
``DecisionService.__init__`` — so the one misconfiguration a person is most
likely to make turned the whole decisions screen into a 500. The floor is the
deterministic signal, not an error page: a provider that cannot be built falls
back to the mock and says so, loudly, in the log and in ``provider_status()``.
"""
from __future__ import annotations

import logging
from typing import Protocol

from ..config import settings

log = logging.getLogger("pie_portal.ai.provider")


class ProviderError(Exception):
    """Base class for provider failures (degrade to deterministic fallback)."""


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class AIProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str) -> str: ...


def select_provider() -> AIProvider:
    """The configured provider, or the mock when it cannot be built.

    Never raises. A caller that gets the mock back gets working, honest,
    deterministic text; a caller that got an exception got nothing at all.
    """
    if settings.AI_PROVIDER == "anthropic":
        from .anthropic_provider import AnthropicProvider

        try:
            return AnthropicProvider()
        except ProviderError as e:
            log.warning(
                "AI_PROVIDER=anthropic but the live provider could not be built "
                "(%s); falling back to the offline mock. Narratives will be "
                "deterministic templates, not model output.", e)
    from .mock_provider import MockProvider

    return MockProvider()


def provider_status() -> dict:
    """What is configured, what will actually run, and why they differ.

    Read by the readiness endpoint so "the AI is on" is a fact a person can
    check on a screen rather than an assumption about an environment variable.
    Pure config inspection — it builds nothing and calls nothing.
    """
    configured = settings.AI_PROVIDER
    key_present = bool(settings.ANTHROPIC_API_KEY)
    if configured != "anthropic":
        return {
            "configured": configured,
            "effective": "mock",
            "model": "mock-1",
            "api_key_present": key_present,
            "live": False,
            "detail": ("AI_PROVIDER is not 'anthropic'. Decision narratives come "
                       "from the offline mock, which reads the same facts but is "
                       "not a model. Set AI_PROVIDER=anthropic and "
                       "ANTHROPIC_API_KEY to run the real one."),
        }
    if not key_present:
        return {
            "configured": configured,
            "effective": "mock",
            "model": "mock-1",
            "api_key_present": False,
            "live": False,
            "detail": ("AI_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty, so "
                       "the offline mock is running instead. Set the key and "
                       "restart."),
        }
    return {
        "configured": configured,
        "effective": "anthropic",
        "model": settings.AI_MODEL,
        "api_key_present": True,
        "live": True,
        "detail": None,
    }
