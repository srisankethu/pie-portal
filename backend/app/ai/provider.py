"""AI provider boundary — a thin, swappable seam (no framework, no agent).

``AIProvider.complete(system, user) -> raw_text`` is the entire contract. Five
implementations: ``MockProvider`` (deterministic, offline — dev/test default)
and four live ones — ``AnthropicProvider``, ``OpenAIProvider``,
``GeminiProvider`` and ``OpenRouterProvider`` (a gateway onto many of them).
Selection is config-driven, at two levels: an organization that stored its own
key and chose a provider (ai/byok.py) gets that one; otherwise the deployment's
``AI_PROVIDER`` decides, exactly as before.

**Selection never raises.** ``AI_PROVIDER=anthropic`` with no key used to raise
out of ``AnthropicProvider.__init__``, through ``select_provider()``, and out of
``DecisionService.__init__`` — so the one misconfiguration a person is most
likely to make turned the whole decisions screen into a 500. The floor is the
deterministic signal, not an error page: a provider that cannot be built falls
back to the mock and says so, loudly, in the log and in ``provider_status()``.
"""
from __future__ import annotations

import logging
from typing import Optional, Protocol

from sqlalchemy.orm import Session

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


#: Provider name -> class, resolved lazily so importing this module never pulls
#: in httpx. Grown by adding a line here and a module beside the others.
def _provider_class(name: str):
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider
    if name == "openai":
        from .openai_provider import OpenAIProvider
        return OpenAIProvider
    if name == "gemini":
        from .gemini_provider import GeminiProvider
        return GeminiProvider
    if name == "openrouter":
        from .openrouter_provider import OpenRouterProvider
        return OpenRouterProvider
    return None


def build_provider(name: str, *, api_key: Optional[str] = None,
                   model: Optional[str] = None) -> AIProvider:
    """One live provider by name. Raises ProviderError — callers that must not
    fail use ``select_provider`` instead; this is for callers that *want* the
    failure, like the Settings test button."""
    cls = _provider_class(name)
    if cls is None:
        raise ProviderUnavailable(f"Unknown AI provider {name!r}")
    # Only the overrides actually given are passed, so the env-configured path
    # still constructs with no arguments — the contract test doubles rely on.
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if model:
        kwargs["model"] = model
    return cls(**kwargs)


def select_provider(session: Optional[Session] = None,
                    organization_id: Optional[str] = None) -> AIProvider:
    """The configured provider, or the mock when it cannot be built.

    Never raises. A caller that gets the mock back gets working, honest,
    deterministic text; a caller that got an exception got nothing at all.

    With a session and an organization, that organization's own choice (a key
    stored from Settings — ai/byok.py) is honoured first; the environment
    configuration is the fallback, so every existing no-argument caller keeps
    its exact behaviour.
    """
    if session is not None and organization_id:
        from . import byok

        try:
            cfg = byok.active_config(session, organization_id)
        except Exception as e:  # noqa: BLE001 — an undecryptable key must not 500 a screen
            log.warning("BYOK config for org %s could not be read (%s); "
                        "falling back to the environment configuration.",
                        organization_id, e)
            cfg = None
        if cfg is not None:
            try:
                return build_provider(cfg.provider, api_key=cfg.api_key or None,
                                      model=cfg.model or None)
            except ProviderError as e:
                log.warning("Organization %s chose %s but the provider could not "
                            "be built (%s); falling back to the environment "
                            "configuration.", organization_id, cfg.provider, e)

    if _provider_class(settings.AI_PROVIDER) is not None:
        try:
            return build_provider(settings.AI_PROVIDER)
        except ProviderError as e:
            log.warning(
                "AI_PROVIDER=%s but the live provider could not be built "
                "(%s); falling back to the offline mock. Narratives will be "
                "deterministic templates, not model output.", settings.AI_PROVIDER, e)
    from .mock_provider import MockProvider

    return MockProvider()


def _env_status() -> dict:
    """What the environment alone configures — the pre-BYOK answer, unchanged."""
    configured = settings.AI_PROVIDER
    if _provider_class(configured) is None:
        return {
            "configured": configured,
            "effective": "mock",
            "model": "mock-1",
            "api_key_present": bool(settings.ANTHROPIC_API_KEY),
            "live": False,
            "source": "environment",
            "detail": ("AI_PROVIDER is not a live provider. Decision narratives "
                       "come from the offline mock, which reads the same facts "
                       "but is not a model. Set AI_PROVIDER to anthropic, openai, "
                       "gemini or openrouter and the matching API key to run a "
                       "real one — "
                       "or store an organization key under Settings → AI layer."),
        }
    from . import byok

    if not byok.env_key_present(configured):
        return {
            "configured": configured,
            "effective": "mock",
            "model": "mock-1",
            "api_key_present": False,
            "live": False,
            "source": "environment",
            "detail": (f"AI_PROVIDER={configured} but "
                       f"{byok.env_key_name(configured)} is empty, so the "
                       "offline mock is running instead. Set the key and "
                       "restart."),
        }
    return {
        "configured": configured,
        "effective": configured,
        "model": byok.default_model(configured),
        "api_key_present": True,
        "live": True,
        "source": "environment",
        "detail": None,
    }


def provider_status(session: Optional[Session] = None,
                    organization_id: Optional[str] = None) -> dict:
    """What is configured, what will actually run, and why they differ.

    Read by the readiness endpoint so "the AI is on" is a fact a person can
    check on a screen rather than an assumption about an environment variable.
    Pure config inspection — it builds nothing and calls nothing. ``source``
    says which level of configuration won: the organization's own key, or the
    deployment environment.
    """
    if session is not None and organization_id:
        from . import byok

        name = byok.active_provider_name(session, organization_id)
        if name:
            row = byok.get_key(session, organization_id, name)
            key_present = row is not None or byok.env_key_present(name)
            if key_present:
                return {
                    "configured": name,
                    "effective": name,
                    "model": (row.model if row and row.model
                              else byok.default_model(name)),
                    "api_key_present": True,
                    "live": True,
                    "source": "organization",
                    "detail": None,
                }
            status = _env_status()
            status["detail"] = (
                f"This organization chose {name} but no key is on file for it "
                "any more; the deployment configuration is running instead. "
                + (status["detail"] or ""))
            return status
    return _env_status()
