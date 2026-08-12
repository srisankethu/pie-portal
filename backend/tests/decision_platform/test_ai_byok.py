"""BYOK: an organization's own AI key — storage, selection, and the write-only rule.

Three claims worth pinning. A stored key is encrypted at rest and *write-only*
through the API: no response body ever contains it, only the last-four hint.
An organization's choice beats the environment: with a stored key and an active
provider, ``select_provider(session, org)`` builds that provider even when the
deployment's own ``AI_PROVIDER`` says otherwise — and the no-argument call keeps
its exact pre-BYOK behaviour. And the choice cannot outlive the key: deleting
the active provider's key clears the selection rather than leaving the org
pointed at a provider it cannot run.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import crypto
from app.ai import byok
from app.ai.provider import provider_status, select_provider
from app.db import Base, get_session
from app.domain import models
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_t"


@pytest.fixture()
def org(session):
    row = models.Organization(organization_id=ORG, name="Test Org")
    session.add(row)
    session.flush()
    return row


# ── storage ──────────────────────────────────────────────────────────────────
def test_a_key_is_encrypted_at_rest_and_the_hint_never_needs_decryption(session, org):
    row = byok.set_key(session, ORG, "openai", api_key="sk-test-abcd1234")
    assert row.api_key_encrypted != "sk-test-abcd1234"
    assert "sk-test" not in row.api_key_encrypted
    assert crypto.decrypt(row.api_key_encrypted) == "sk-test-abcd1234"
    assert row.key_hint == "1234"


def test_reentering_a_key_rotates_the_row_rather_than_minting_a_second(session, org):
    first = byok.set_key(session, ORG, "gemini", api_key="AIza-old-key-0001")
    second = byok.set_key(session, ORG, "gemini", api_key="AIza-new-key-0002")
    assert second.key_id == first.key_id
    assert second.rotated_at is not None
    assert len(byok.list_keys(session, ORG)) == 1


def test_an_unknown_provider_is_refused_by_name(session, org):
    with pytest.raises(byok.UnknownProvider):
        byok.set_key(session, ORG, "skynet", api_key="k")


# ── selection ────────────────────────────────────────────────────────────────
def test_the_organizations_choice_beats_the_environment(session, org, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    byok.set_key(session, ORG, "openai", api_key="sk-test-abcd1234",
                 model="gpt-4o-mini")
    byok.set_active_provider(session, ORG, "openai")

    provider = select_provider(session, ORG)
    assert provider.name == "openai"
    assert provider.model == "gpt-4o-mini"

    # The no-argument call is the deployment's answer and must not move.
    assert select_provider().name == "mock"


def test_a_stored_model_override_is_used_and_empty_means_the_default(session, org):
    byok.set_key(session, ORG, "anthropic", api_key="sk-ant-xyz", model="")
    byok.set_active_provider(session, ORG, "anthropic")
    from app.config import settings

    assert select_provider(session, ORG).model == settings.AI_MODEL


def test_choosing_a_provider_with_no_key_anywhere_is_refused(session, org, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    with pytest.raises(ValueError):
        byok.set_active_provider(session, ORG, "gemini")


def test_deleting_the_active_key_clears_the_choice(session, org, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    byok.set_key(session, ORG, "gemini", api_key="AIza-test-1")
    byok.set_active_provider(session, ORG, "gemini")
    assert byok.active_provider_name(session, ORG) == "gemini"

    byok.delete_key(session, ORG, "gemini")
    assert byok.active_provider_name(session, ORG) == ""
    # With the choice gone the environment decides again — here, the mock.
    assert select_provider(session, ORG).name == "mock"


def test_status_names_the_source_that_won(session, org, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    status = provider_status(session, ORG)
    assert status["source"] == "environment" and status["live"] is False

    byok.set_key(session, ORG, "openai", api_key="sk-test-1", model="gpt-4o")
    byok.set_active_provider(session, ORG, "openai")
    status = provider_status(session, ORG)
    assert status == {"configured": "openai", "effective": "openai",
                      "model": "gpt-4o", "api_key_present": True, "live": True,
                      "source": "organization", "detail": None}


def test_a_broken_stored_key_degrades_to_the_environment_not_a_500(
        session, org, monkeypatch):
    """An undecryptable row (rotated CREDENTIAL_ENCRYPTION_KEY) must not take
    the decisions screen down — same floor as every other selection failure."""
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    row = byok.set_key(session, ORG, "openai", api_key="sk-test-1")
    byok.set_active_provider(session, ORG, "openai")
    row.api_key_encrypted = "not-fernet-ciphertext"
    session.flush()
    assert select_provider(session, ORG).name == "mock"


# ── the router: who may ask, and what never comes back ───────────────────────
@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    s.commit()
    s.close()

    from app.routers import ai_settings, platform_auth

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(ai_settings.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(client, email):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.parametrize("email", ["m.rao@pie.example", "r.nair@pie.example"])
def test_only_an_owner_touches_ai_keys(client, email):
    hdr = _hdr(client, email)
    assert client.get("/api/v1/ai/providers", headers=hdr).status_code == 403
    assert client.put("/api/v1/ai/providers/openai", headers=hdr,
                      json={"api_key": "sk-x"}).status_code == 403


def test_the_surface_needs_a_token_at_all(client):
    assert client.get("/api/v1/ai/providers").status_code in (401, 403)


def test_a_saved_key_never_appears_in_any_response(client):
    hdr = _hdr(client, "s.menon@pie.example")
    secret = "sk-live-do-not-echo-Zx9Qw7"

    r = client.put("/api/v1/ai/providers/anthropic", headers=hdr,
                   json={"api_key": secret, "model": ""})
    assert r.status_code == 200, r.text
    assert secret not in r.text
    row = next(p for p in r.json()["providers"] if p["provider"] == "anthropic")
    assert row["key_on_file"] is True and row["key_hint"] == "9Qw7"

    r = client.get("/api/v1/ai/providers", headers=hdr)
    assert secret not in r.text


def test_the_full_lifecycle_over_http(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    hdr = _hdr(client, "s.menon@pie.example")

    # Choosing a provider before any key exists anywhere is refused.
    r = client.put("/api/v1/ai/active", headers=hdr, json={"provider": "gemini"})
    assert r.status_code == 400

    r = client.put("/api/v1/ai/providers/gemini", headers=hdr,
                   json={"api_key": "AIza-test-77", "model": "gemini-2.5-pro"})
    assert r.status_code == 200
    r = client.put("/api/v1/ai/active", headers=hdr, json={"provider": "gemini"})
    assert r.status_code == 200 and r.json()["active"] == "gemini"

    # Back to the deployment default is an empty choice, not a delete.
    r = client.put("/api/v1/ai/active", headers=hdr, json={"provider": ""})
    assert r.status_code == 200 and r.json()["active"] == ""

    r = client.delete("/api/v1/ai/providers/gemini", headers=hdr)
    assert r.status_code == 200
    row = next(p for p in r.json()["providers"] if p["provider"] == "gemini")
    assert row["key_on_file"] is False and row["key_hint"] == ""
    assert client.delete("/api/v1/ai/providers/gemini",
                         headers=hdr).status_code == 404


def test_the_test_button_reports_without_echoing_the_key(client, monkeypatch):
    hdr = _hdr(client, "s.menon@pie.example")
    client.put("/api/v1/ai/providers/openai", headers=hdr,
               json={"api_key": "sk-test-ping-1"})

    class Fake:
        model = "fake-1"

        def complete(self, system, user):
            return "ok"

    from app.routers import ai_settings as mod

    monkeypatch.setattr(mod, "build_provider", lambda *a, **k: Fake())
    r = client.post("/api/v1/ai/providers/openai/test", headers=hdr)
    assert r.status_code == 200
    assert r.json()["ok"] is True and "sk-test-ping-1" not in r.text

    # No key on file and none in the environment: an honest 400, not a call.
    monkeypatch.setattr(settings_module(), "ANTHROPIC_API_KEY", "")
    assert client.post("/api/v1/ai/providers/anthropic/test",
                       headers=hdr).status_code == 400


def settings_module():
    from app.config import settings

    return settings
