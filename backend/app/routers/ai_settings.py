"""Bring-your-own-key AI provider settings (owner only).

Neither ``admin.py`` (accounts and policy) nor ``connections.py`` (Zoho) owns
this concern, and ``internal.py`` is declared not user-facing — so the AI
layer's one writable surface lives here. Thin by the router rule: every
decision is made in ``ai/byok.py``; this file maps HTTP and role scope.

The key itself is write-only. It goes in through PUT and never comes back out:
every read shows the stored last-four hint, so there is nothing to lift from a
network tab. The one endpoint that uses a key (`/test`) sends a fixed one-word
ping to the provider and reports what came back — it exists because "the key is
saved" and "the key works" are different facts, and the second one is the one
an owner is standing there to learn.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status as http
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai import byok
from ..ai.provider import ProviderError, build_provider
from ..authz import Principal, require_owner
from ..config import settings
from ..db import get_session

log = logging.getLogger("pie_portal.routers.ai_settings")

router = APIRouter(prefix="/api/v1/ai", tags=["ai-settings"])


def _provider_or_404(provider: str) -> str:
    if provider not in byok.PROVIDERS:
        raise HTTPException(http.HTTP_404_NOT_FOUND,
                            f"Unknown provider {provider!r}")
    return provider


def _view(session: Session, organization_id: str) -> dict:
    """Everything the Settings card renders. No plaintext key, ever."""
    stored = {row.provider: row for row in byok.list_keys(session, organization_id)}
    return {
        "active": byok.active_provider_name(session, organization_id),
        "environment_provider": (settings.AI_PROVIDER
                                 if settings.AI_PROVIDER in byok.PROVIDERS else "mock"),
        "providers": [
            {
                "provider": name,
                "key_on_file": name in stored,
                "key_hint": stored[name].key_hint if name in stored else "",
                "model": stored[name].model if name in stored else "",
                "default_model": byok.default_model(name),
                "env_key_present": byok.env_key_present(name),
                "rotated_at": (stored[name].rotated_at.isoformat()
                               if name in stored and stored[name].rotated_at else None),
            }
            for name in byok.PROVIDERS
        ],
    }


@router.get("/providers")
def list_providers(
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    return _view(session, principal.organization_id)


class PutKey(BaseModel):
    api_key: str = Field(min_length=1, max_length=1024)
    model: str = Field(default="", max_length=128)


@router.put("/providers/{provider}")
def put_key(
    provider: str,
    body: PutKey,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    _provider_or_404(provider)
    try:
        byok.set_key(session, principal.organization_id, provider,
                     api_key=body.api_key, model=body.model)
    except ValueError as e:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, str(e))
    session.commit()
    log.info("AI key saved org=%s provider=%s by=%s",
             principal.organization_id, provider, principal.user_id)
    return _view(session, principal.organization_id)


@router.delete("/providers/{provider}")
def remove_key(
    provider: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    _provider_or_404(provider)
    existed = byok.delete_key(session, principal.organization_id, provider)
    if not existed:
        raise HTTPException(http.HTTP_404_NOT_FOUND,
                            f"No {provider} key on file for this organization")
    session.commit()
    log.info("AI key removed org=%s provider=%s by=%s",
             principal.organization_id, provider, principal.user_id)
    return _view(session, principal.organization_id)


class SetActive(BaseModel):
    #: One of byok.PROVIDERS, or "" to restore the deployment default.
    provider: str = Field(default="", max_length=32)


@router.put("/active")
def set_active(
    body: SetActive,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    if body.provider and body.provider not in byok.PROVIDERS:
        raise HTTPException(http.HTTP_404_NOT_FOUND,
                            f"Unknown provider {body.provider!r}")
    try:
        byok.set_active_provider(session, principal.organization_id, body.provider)
    except ValueError as e:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, str(e))
    session.commit()
    log.info("AI provider selection org=%s provider=%s by=%s",
             principal.organization_id, body.provider or "(environment default)",
             principal.user_id)
    return _view(session, principal.organization_id)


@router.post("/providers/{provider}/test")
def test_key(
    provider: str,
    principal: Principal = Depends(require_owner),
    session: Session = Depends(get_session),
) -> dict:
    """One live round trip with the stored (or environment) key.

    The only endpoint in the platform that calls a model outside decision
    generation. The ping is a fixed word, carries no business data, and the
    response is reported as ok/failed plus the provider's own error text —
    never the key.
    """
    _provider_or_404(provider)
    row = byok.get_key(session, principal.organization_id, provider)
    if row is None and not byok.env_key_present(provider):
        raise HTTPException(http.HTTP_400_BAD_REQUEST,
                            f"No API key on file for {provider} — save one first.")
    from .. import crypto

    api_key = crypto.decrypt(row.api_key_encrypted) if row is not None else None
    model = (row.model or None) if row is not None else None
    try:
        p = build_provider(provider, api_key=api_key, model=model)
        text = p.complete("Reply with the single word: ok", "ping")
    except ProviderError as e:
        return {"ok": False, "provider": provider, "detail": str(e)[:512]}
    return {"ok": True, "provider": provider, "model": p.model,
            "detail": (text or "")[:64]}
