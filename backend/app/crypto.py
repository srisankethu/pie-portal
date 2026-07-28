"""Encryption for credentials at rest.

Zoho client secrets and refresh tokens are now stored in the database (one
connection per organization), not only in a gitignored ``.env`` — so unlike an
environment variable, a database backup or a read replica can expose them in
plaintext. Fernet (AES-128-CBC + HMAC, from the ``cryptography`` package) keyed
by ``CREDENTIAL_ENCRYPTION_KEY`` closes that gap. Ciphertext is what the
database holds; plaintext exists only in memory for the duration of a call.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .config import settings


class CredentialDecryptionError(RuntimeError):
    """Stored ciphertext could not be decrypted — almost always
    CREDENTIAL_ENCRYPTION_KEY changing since the value was written."""


def _fernet() -> Fernet:
    return Fernet(settings.CREDENTIAL_ENCRYPTION_KEY.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise CredentialDecryptionError(
            "Could not decrypt a stored credential — CREDENTIAL_ENCRYPTION_KEY may have "
            "changed since it was saved. Re-enter the connection's credentials.") from e
