"""Bring-your-own-key AI configuration, per organization.

The environment variables (``AI_PROVIDER``, ``ANTHROPIC_API_KEY``, …) configure
the *deployment*; this module lets one organization configure *itself* — enter
its own Anthropic, OpenAI, Gemini or OpenRouter key from Settings, choose which
of them runs, and fall back to the deployment default by choosing nothing.
Resolution order everywhere is therefore: organization's active BYOK provider, then the
environment, then the offline mock — and, like ``select_provider``, nothing
here lets a misconfiguration escalate into an error page.

Two facts are stored separately on purpose. A **key** (``AIProviderKey``,
encrypted with ``app/crypto.py``, one row per organization+provider) can be
entered, tested and rotated without being run. The **choice** of which provider
runs lives in ``Organization.config["ai_provider"]`` — the same home as the
org's other operational preferences — so switching is one small fact, not a
mutation of credential rows.

Keys are write-only: no function here returns a plaintext key except
``active_config``, whose one caller is provider construction. The stored
``key_hint`` (last four characters) is what every read path shows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..config import settings
from ..domain import models

#: The providers an organization may bring a key for, in display order.
#: OpenRouter is last because it is a gateway rather than a lab: one key reaches
#: models from all three of the others, so the model an organization names
#: against it matters more than it does anywhere else on this list.
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini", "openrouter")

_ACTIVE_KEY = "ai_provider"


class UnknownProvider(ValueError):
    """A provider name outside PROVIDERS."""


def _require_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise UnknownProvider(
            f"Unknown AI provider {provider!r} — expected one of {', '.join(PROVIDERS)}")
    return provider


def default_model(provider: str) -> str:
    """The model a provider runs when the organization has not named one."""
    _require_provider(provider)
    return {"anthropic": settings.AI_MODEL,
            "openai": settings.OPENAI_MODEL,
            "gemini": settings.GEMINI_MODEL,
            "openrouter": settings.OPENROUTER_MODEL}[provider]


def env_key_name(provider: str) -> str:
    """The environment variable a deployment-level key lives in — named in
    status text so "set the key" says which one."""
    _require_provider(provider)
    return {"anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY"}[provider]


def env_key_present(provider: str) -> bool:
    """Whether the deployment itself holds a key for this provider."""
    _require_provider(provider)
    return bool({"anthropic": settings.ANTHROPIC_API_KEY,
                 "openai": settings.OPENAI_API_KEY,
                 "gemini": settings.GEMINI_API_KEY,
                 "openrouter": settings.OPENROUTER_API_KEY}[provider])


# ── keys ────────────────────────────────────────────────────────────────────
def list_keys(session: Session, organization_id: str) -> list[models.AIProviderKey]:
    return list(session.scalars(
        select(models.AIProviderKey)
        .where(models.AIProviderKey.organization_id == organization_id)
        .order_by(models.AIProviderKey.created_at)))


def get_key(session: Session, organization_id: str,
            provider: str) -> Optional[models.AIProviderKey]:
    _require_provider(provider)
    return session.scalar(select(models.AIProviderKey).where(
        models.AIProviderKey.organization_id == organization_id,
        models.AIProviderKey.provider == provider))


def set_key(session: Session, organization_id: str, provider: str, *,
            api_key: str, model: str = "") -> models.AIProviderKey:
    """Store or rotate this organization's key for one provider.

    An existing row is rotated in place — two rows holding one secret is the
    state that makes a rotation miss one of them (``ZohoCredential``'s lesson).
    """
    _require_provider(provider)
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("An API key cannot be empty")
    row = get_key(session, organization_id, provider)
    if row is None:
        row = models.AIProviderKey(organization_id=organization_id, provider=provider)
        session.add(row)
    else:
        row.rotated_at = datetime.now(timezone.utc)
    row.api_key_encrypted = crypto.encrypt(api_key)
    row.key_hint = api_key[-4:]
    row.model = (model or "").strip()[:128]
    session.flush()
    return row


def delete_key(session: Session, organization_id: str, provider: str) -> bool:
    """Remove a stored key. Returns whether one existed.

    Deleting the key of the *active* provider also clears the choice: an
    organization pointed at a provider it no longer holds a key for would fall
    back to the environment silently, and a fallback nobody chose should not
    look like a choice somebody made.
    """
    row = get_key(session, organization_id, provider)
    if row is None:
        return False
    session.delete(row)
    if active_provider_name(session, organization_id) == provider:
        _write_active(session, organization_id, "")
    session.flush()
    return True


# ── the active choice ───────────────────────────────────────────────────────
def active_provider_name(session: Session, organization_id: str) -> str:
    """Which BYOK provider this organization chose, or "" for the env default."""
    org = session.get(models.Organization, organization_id)
    if org is None:
        return ""
    value = (org.config or {}).get(_ACTIVE_KEY, "")
    return value if value in PROVIDERS else ""


def _write_active(session: Session, organization_id: str, value: str) -> None:
    org = session.get(models.Organization, organization_id)
    if org is None:
        raise LookupError(f"No such organization {organization_id!r}")
    # Reassigned rather than mutated: SQLAlchemy does not watch the inside of a
    # JSON column, so an in-place update would never be written.
    org.config = {**(org.config or {}), _ACTIVE_KEY: value}
    session.flush()


def set_active_provider(session: Session, organization_id: str, provider: str) -> str:
    """Choose which provider runs for this organization. "" restores the env default.

    Refused when nothing could actually run under the choice — no stored key
    and no environment key is a configuration that looks live and writes
    deterministic templates, which is exactly the silent state the status
    endpoint exists to prevent.
    """
    if provider == "":
        _write_active(session, organization_id, "")
        return ""
    _require_provider(provider)
    if get_key(session, organization_id, provider) is None and not env_key_present(provider):
        raise ValueError(
            f"No API key on file for {provider} — save a key first, then switch to it.")
    _write_active(session, organization_id, provider)
    return provider


# ── resolution (the one decrypting read path) ───────────────────────────────
@dataclass(frozen=True)
class ByokConfig:
    """Everything provider construction needs, decrypted. Never serialized."""

    provider: str
    api_key: str
    model: str  # "" means the provider default


def active_config(session: Session, organization_id: str) -> Optional[ByokConfig]:
    """The organization's chosen provider with its stored key, if both exist.

    ``None`` means "no BYOK choice" and the caller falls back to the
    environment. A chosen provider whose key row is gone resolves to ``None``
    the same way — that state cannot be reached through this module (deleting
    the active key clears the choice), so if it exists the row was lost some
    other way and the honest answer is the fallback, not an error.
    """
    name = active_provider_name(session, organization_id)
    if not name:
        return None
    row = get_key(session, organization_id, name)
    if row is None:
        return ByokConfig(provider=name, api_key="", model="") if env_key_present(name) \
            else None
    return ByokConfig(provider=name, api_key=crypto.decrypt(row.api_key_encrypted),
                      model=row.model or "")
