"""Password hashing for platform users.

PBKDF2-HMAC-SHA256 from the standard library. Not because it is the best
available — Argon2id is — but because it is the best available *without adding a
dependency*, and a deployment that cannot install a wheel must still not be
storing plaintext. The stored format carries its own parameters, so the work
factor can be raised later and old hashes keep verifying.

Format: ``pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>``.

Two rules worth stating because both are easy to get wrong:

**Comparison is constant-time.** ``==`` on a digest leaks, through timing, how
many leading bytes were right.

**A missing hash is a failed login, never a free pass.** The previous
implementation accepted any non-empty password for any known email, which meant
the three roles were a display preference rather than a boundary: anyone who
knew the owner's address was the owner. A user row with no password set cannot
authenticate at all until one is issued.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import string
from typing import Optional

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 240_000
_SALT_BYTES = 16

MIN_PASSWORD_LENGTH = 10


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS) -> str:
    if not password:
        raise ValueError("A password is required")
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    """True only if ``password`` produced ``stored``.

    Every failure path — no hash on the row, a malformed hash, an empty
    password — returns False rather than raising, so a caller cannot
    accidentally treat "this user has no password" as "this password is right".
    """
    if not password or not stored:
        return False
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != ALGORITHM:
        return False
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(_b64(digest), expected)


def needs_rehash(stored: Optional[str], *, iterations: int = DEFAULT_ITERATIONS) -> bool:
    """Whether a verified hash was made with a weaker work factor than current."""
    if not stored:
        return False
    parts = stored.split("$", 3)
    if len(parts) != 4 or parts[0] != ALGORITHM:
        return True
    try:
        return int(parts[1]) < iterations
    except ValueError:
        return True


def password_problem(password: str) -> Optional[str]:
    """Why this password is unacceptable, or None.

    Length only. Composition rules (a digit, a symbol, a capital) push people
    toward `Password1!` and are worse than a longer passphrase; the length floor
    is the part that actually buys anything.
    """
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if password.strip() != password:
        return "Password must not start or end with a space"
    return None


def generate_password(length: int = 14) -> str:
    """A temporary password for a newly created or reset account.

    Unambiguous alphabet: no O/0, l/1/I. These get read aloud and typed from a
    sticky note, and a character nobody can transcribe is a support call.
    """
    alphabet = (string.ascii_lowercase.replace("l", "")
                + string.ascii_uppercase.replace("O", "").replace("I", "")
                + "23456789")
    return "".join(secrets.choice(alphabet) for _ in range(length))
