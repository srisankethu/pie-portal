"""AI provider boundary — a thin, swappable seam (no framework, no agent).

``AIProvider.complete(system, user) -> raw_text`` is the entire contract. Two
implementations: ``MockProvider`` (deterministic, offline — dev/test default) and
``AnthropicProvider`` (live). Selection is config-driven.
"""
from __future__ import annotations

from typing import Protocol

from ..config import settings


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
    if settings.AI_PROVIDER == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    from .mock_provider import MockProvider
    return MockProvider()
