"""Credential encryption at rest — Zoho client secrets/refresh tokens now live
in the database, so unlike a gitignored .env, a backup or a read replica can
expose them if they are ever stored as plaintext."""
from __future__ import annotations

import pytest

from app import crypto
from app.config import settings


#: Shaped like a Zoho refresh token and deliberately all zeroes. A *real*
#: token lived here once and rode into every commit since — the round-trip
#: test needs the shape, never the secret, and `test_no_live_secret_is_
#: committed` below now fails the build if one comes back.
FAKE_REFRESH_TOKEN = "1000.0000000000000000000000000000000.00000000000000000000000000000000"


def test_round_trips():
    ct = crypto.encrypt(FAKE_REFRESH_TOKEN)
    assert ct != FAKE_REFRESH_TOKEN
    assert crypto.decrypt(ct) == FAKE_REFRESH_TOKEN


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


def test_no_live_secret_is_committed():
    """A real Zoho refresh token was once pasted into this very file, and rode
    into every commit after it — a leak no `.gitignore` covers, because the
    file was meant to be committed.

    Scanned by *shape*, not by value: pinning the one token that leaked would
    catch that token and nothing else, and the next one would look different.
    A Zoho refresh token is ``1000.<32 hex>.<32 hex>``; a client secret is a
    bare 41+ hex run. Neither has any business in source.
    """
    import re
    import subprocess
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[3]

    # Over what git tracks, not what happens to be on disk. The leak this
    # guards is a secret that *rides into a commit*; a tool installed under a
    # gitignored directory ships fixtures that are neither committed nor ours
    # to rotate, and flagging them only teaches people to scroll past this.
    # `git ls-files` reads the index as well as HEAD, so a pasted secret is
    # still caught at `git add` — one step before it can become a commit.
    listing = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True
    )
    # An empty scan is not a clean scan: if git cannot answer, this check has
    # no evidence and must say so rather than report the good news by default.
    assert listing.returncode == 0, (
        "git ls-files failed, so nothing was scanned: "
        + listing.stderr.decode(errors="ignore")[:200]
    )
    tracked = [ROOT / name for name in listing.stdout.decode().split("\0") if name]
    assert tracked, "git ls-files returned no files — this check scanned nothing."
    PATTERNS = {
        "zoho refresh token": re.compile(r"\b1000\.[0-9a-f]{32}\.[0-9a-f]{32}\b"),
        # 41+, not 40+: a git commit SHA is exactly 40 hex and this repo pins
        # one in its setup script and its docs. A Zoho client secret is 42.
        # Bounding it below at 41 keeps the check on secrets and off SHAs
        # without an allow-list that would need maintaining.
        "zoho client secret": re.compile(r"\b[0-9a-f]{41,}\b"),
    }
    SKIP_DIRS = {".git", "node_modules", "dist", "__pycache__", ".venv", "data"}
    # Lock files are full of long hex digests, and a digest is not a secret.
    # The suffix covers poetry.lock and yarn.lock; the `-lock.json` test below
    # covers package-lock.json, skills-lock.json and whatever the next tool
    # writes, so this stays a rule rather than a list somebody has to extend.
    SKIP_SUFFIX = {".lock", ".png", ".jpg", ".svg", ".ico", ".db", ".log"}

    offenders: list[str] = []
    for path in tracked:
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIX or path.name.endswith("-lock.json"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in PATTERNS.items():
            for hit in pattern.findall(text):
                # An all-zero placeholder is the point of the fixture above.
                if set(hit.replace("1000.", "").replace(".", "")) <= {"0"}:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {label} {hit[:12]}…")

    assert not offenders, (
        "Something shaped like a live credential is in the source tree:\n  "
        + "\n  ".join(sorted(set(offenders))[:20])
        + "\nRotate it in Zoho, then replace it here with a placeholder — "
          "deleting the line does not remove it from git history."
    )
