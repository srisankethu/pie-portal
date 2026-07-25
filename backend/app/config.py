"""Runtime configuration for the pie-portal backend.

Everything the app needs to locate the vendored pie-parser package and the
decoded PIE catalogue is resolved here, from environment variables with sane
defaults for local development.
"""
from __future__ import annotations

import os
from pathlib import Path

# backend/app/config.py -> repo root is three parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _path_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


class Settings:
    """Process-wide settings (plain attributes; no external deps)."""

    # Location of the pie-parser package (a pinned clone at ./pie-parser by default).
    PIE_PARSER_ROOT: Path = _path_env("PIE_PARSER_ROOT", REPO_ROOT / "pie-parser")

    # Decoded PIE product catalogue (products.jsonl) the resolver searches.
    # Built from the pie-parser corpus by scripts/build_catalog.py; gitignored.
    PIE_CATALOG: Path = _path_env("PIE_CATALOG", REPO_ROOT / "backend" / "data" / "products.jsonl")

    # When the catalogue is missing, build it lazily on first use from the
    # pie-parser corpus. Disable in constrained deploys and build it out-of-band.
    AUTO_BUILD_CATALOG: bool = os.environ.get("AUTO_BUILD_CATALOG", "1") != "0"

    # Corpus + pack used to build the catalogue when AUTO_BUILD_CATALOG is on.
    PIE_CORPUS: Path = _path_env(
        "PIE_CORPUS",
        PIE_PARSER_ROOT / "corpora" / "kmt_zcnc_2026-07_nomenclature.csv",
    )
    PIE_PACK: Path = _path_env("PIE_PACK", PIE_PARSER_ROOT / "packs" / "kennametal_widia")

    # Max ranked alternatives returned per line.
    TOP_N: int = int(os.environ.get("PIE_TOP_N", "6"))

    # Demo auth secret (dev only). A real deployment injects this.
    AUTH_SECRET: str = os.environ.get("AUTH_SECRET", "dev-secret-change-me")

    # ── Commercial Decision Platform (Phase 1 foundation) ────────────────────
    # Single primary database. Dev/test default to SQLite; production sets a
    # Postgres URL. SQLAlchemy URL form, e.g. postgresql+psycopg://user:pw@host/db
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{REPO_ROOT / 'backend' / 'data' / 'platform.db'}"
    )
    SQL_ECHO: bool = os.environ.get("SQL_ECHO", "0") == "1"

    # The single supported organization for V1 (one org, one ERP). organization_id
    # is carried on every record for future multi-org, but no cross-org logic exists.
    DEFAULT_ORG_ID: str = os.environ.get("DEFAULT_ORG_ID", "org_sanketh")
    DEFAULT_ORG_NAME: str = os.environ.get("DEFAULT_ORG_NAME", "Sanketh")
    DEFAULT_CURRENCY: str = os.environ.get("DEFAULT_CURRENCY", "INR")

    # Zoho connector credentials (read-only). Unused until live sync is enabled;
    # the fixture source backs dev/test. Never commit real values (.env only).
    ZOHO_ORGANIZATION_ID: str = os.environ.get("ZOHO_ORGANIZATION_ID", "")
    ZOHO_CLIENT_ID: str = os.environ.get("ZOHO_CLIENT_ID", "")
    ZOHO_CLIENT_SECRET: str = os.environ.get("ZOHO_CLIENT_SECRET", "")
    ZOHO_REFRESH_TOKEN: str = os.environ.get("ZOHO_REFRESH_TOKEN", "")
    ZOHO_API_BASE: str = os.environ.get("ZOHO_API_BASE", "https://www.zohoapis.in/books/v3")
    # "fixture" (deterministic offline source) or "api" (live Zoho, deferred).
    ZOHO_SOURCE: str = os.environ.get("ZOHO_SOURCE", "fixture")

    # Versioning stamped onto deterministic artifacts for provenance/reproducibility.
    DETECTOR_VERSION: str = os.environ.get("DETECTOR_VERSION", "v0")
    THRESHOLD_CONFIG_VERSION: str = os.environ.get("THRESHOLD_CONFIG_VERSION", "v0")


settings = Settings()
# Re-resolve PIE-derived paths in case PIE_PARSER_ROOT came from the env.
settings.PIE_CORPUS = _path_env(
    "PIE_CORPUS", settings.PIE_PARSER_ROOT / "corpora" / "kmt_zcnc_2026-07_nomenclature.csv"
)
settings.PIE_PACK = _path_env("PIE_PACK", settings.PIE_PARSER_ROOT / "packs" / "kennametal_widia")
