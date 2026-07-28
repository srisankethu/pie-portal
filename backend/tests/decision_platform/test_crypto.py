"""Credential encryption at rest — Zoho client secrets/refresh tokens now live
in the database, so unlike a gitignored .env, a backup or a read replica can
expose them if they are ever stored as plaintext."""
from __future__ import annotations

import pytest

from app import crypto
from app.config import settings


def test_round_trips():
    ct = crypto.encrypt("1000.b4c4f03fca0ce059499c92a17cbaf0e2.4be6c15008d72b76ad1882d2f5e7e295")
    assert ct != "1000.b4c4f03fca0ce059499c92a17cbaf0e2.4be6c15008d72b76ad1882d2f5e7e295"
    assert crypto.decrypt(ct) == "1000.b4c4f03fca0ce059499c92a17cbaf0e2.4be6c15008d72b76ad1882d2f5e7e295"


def test_ciphertext_does_not_contain_the_plaintext():
    secret = "super-secret-refresh-token"
    ct = crypto.encrypt(secret)
    assert secret not in ct


def test_a_stale_key_cannot_decrypt(monkeypatch):
    """The failure mode this must produce is loud, not a silently wrong value."""
    from cryptography.fernet import Fernet

    ct = crypto.encrypt("value")
    monkeypatch.setattr(settings, "CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(crypto.CredentialDecryptionError):
        crypto.decrypt(ct)
