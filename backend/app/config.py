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


def _load_dotenv(path: Path) -> None:
    """Load ``KEY=value`` pairs from a .env file into the environment.

    Deliberately tiny and dependency-free. A real environment variable always
    wins, so a shell export or a container's env overrides the file — the file
    is a convenience for local development, never an override of the deployment.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# Local development convenience: a .env at the repo root is applied before any
# setting is read. Never committed (see .gitignore); never overrides real env.
_load_dotenv(REPO_ROOT / ".env")


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
    # Shared secret for the machine-facing /api/v1/pie endpoints (Zoho Deluge).
    # Empty means the endpoint is closed, not open — see routers/pie.py.
    PIE_API_KEY: str = os.environ.get("PIE_API_KEY", "")

    TOP_N: int = int(os.environ.get("PIE_TOP_N", "6"))

    # Deployment environment. "production" turns on hard guards (real auth secret
    # required, demo-seed disabled). Anything else is treated as dev/test.
    APP_ENV: str = os.environ.get("APP_ENV", "development")

    # Demo auth secret (dev only). A real deployment injects this.
    AUTH_SECRET: str = os.environ.get("AUTH_SECRET", "dev-secret-change-me")

    # Encrypts Zoho client secrets/refresh tokens stored per organization (see
    # app/crypto.py). A Fernet key: 32 url-safe base64 bytes. The default below
    # is fixed and public — fine for local dev, never for production, where a
    # stale default would make every stored credential recoverable by anyone
    # who has read the source. Generate a real one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    CREDENTIAL_ENCRYPTION_KEY: str = os.environ.get(
        "CREDENTIAL_ENCRYPTION_KEY", "sIfoCtwlOtGqxAtOkV5t3Rz-i6ZQ2VuTNQeXHpxTfWA=")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() == "production"

    # ── Commercial Decision Platform (Phase 1 foundation) ────────────────────
    # Single primary database. Dev/test default to SQLite; production sets a
    # Postgres URL. SQLAlchemy URL form, e.g. postgresql+psycopg://user:pw@host/db
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL", f"sqlite:///{REPO_ROOT / 'backend' / 'data' / 'platform.db'}"
    )
    SQL_ECHO: bool = os.environ.get("SQL_ECHO", "0") == "1"

    # Create the database + schema + demo users on startup, so a fresh clone
    # runs without a separate migrate/seed step. Always disabled in production,
    # where migrations are a deliberate, reviewed deploy step.
    AUTO_BOOTSTRAP: bool = os.environ.get("AUTO_BOOTSTRAP", "1") != "0"
    # Seed the realistic demo dataset on startup. Never in production.
    DEMO_SEED_ON_START: bool = os.environ.get("DEMO_SEED_ON_START", "1") != "0"

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
    # OAuth token endpoint host. MUST match the data centre the account lives in
    # (.in for India, .com for US, .eu, .com.au, .jp) — a refresh token issued in
    # one DC is rejected by every other.
    ZOHO_ACCOUNTS_BASE: str = os.environ.get("ZOHO_ACCOUNTS_BASE", "https://accounts.zoho.in")
    # "fixture" (deterministic offline source) or "api" (live Zoho).
    ZOHO_SOURCE: str = os.environ.get("ZOHO_SOURCE", "fixture")

    # Live pull shape. History is bounded because the detectors compare a recent
    # window against a prior one — pulling a decade of ledger costs API calls and
    # buys nothing.
    ZOHO_TIMEOUT_SECONDS: float = float(os.environ.get("ZOHO_TIMEOUT_SECONDS", "30"))
    ZOHO_PAGE_SIZE: int = int(os.environ.get("ZOHO_PAGE_SIZE", "200"))
    ZOHO_MAX_PAGES: int = int(os.environ.get("ZOHO_MAX_PAGES", "50"))
    ZOHO_HISTORY_DAYS: int = int(os.environ.get("ZOHO_HISTORY_DAYS", "730"))
    # An explicit start date (ISO, e.g. 2025-01-01) wins over the rolling window.
    # A manager picks this per run in the UI; this is only the default offered.
    ZOHO_SYNC_FROM: str = os.environ.get("ZOHO_SYNC_FROM", "")

    # Throttling. Zoho Books allows on the order of 100 calls per minute per
    # organization, and a pull costs one call per document — so an unpaced pull
    # trips the limiter within seconds and dies mid-ledger. Pacing prevents that;
    # the backoff is the fallback for when the limiter is hit anyway. Backoff is
    # deliberately tens of seconds: a rate limiter is not a transient 5xx and
    # retrying after one second simply burns the retry budget.
    ZOHO_REQUESTS_PER_MINUTE: int = int(os.environ.get("ZOHO_REQUESTS_PER_MINUTE", "90"))
    ZOHO_MAX_RETRIES: int = int(os.environ.get("ZOHO_MAX_RETRIES", "6"))
    ZOHO_THROTTLE_BACKOFF_SECONDS: float = float(
        os.environ.get("ZOHO_THROTTLE_BACKOFF_SECONDS", "15"))
    ZOHO_MAX_BACKOFF_SECONDS: float = float(os.environ.get("ZOHO_MAX_BACKOFF_SECONDS", "90"))

    # Version stamped onto deterministic artifacts for provenance/reproducibility.
    # (Threshold-config version is carried by SignalThresholds.version, not here.)
    DETECTOR_VERSION: str = os.environ.get("DETECTOR_VERSION", "v0")

    # ── AI Decision Layer ────────────────────────────────────────────────────
    # Provider is swappable; "mock" (deterministic, offline — dev/test default) or
    # "anthropic" (live). A cheap, fast model suits small-bundle interpretation.
    AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "mock")
    AI_MODEL: str = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")
    AI_MAX_TOKENS: int = int(os.environ.get("AI_MAX_TOKENS", "400"))   # bounded output
    AI_TIMEOUT_SECONDS: float = float(os.environ.get("AI_TIMEOUT_SECONDS", "20"))
    PROMPT_VERSION: str = os.environ.get("PROMPT_VERSION", "p1")
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_API_BASE: str = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com")
    # Priority banding (deterministic base + bounded AI adjustment).
    PRIORITY_HIGH_AT: int = int(os.environ.get("PRIORITY_HIGH_AT", "70"))
    PRIORITY_MEDIUM_AT: int = int(os.environ.get("PRIORITY_MEDIUM_AT", "40"))
    AI_PRIORITY_ADJUST_BOUND: int = int(os.environ.get("AI_PRIORITY_ADJUST_BOUND", "20"))

    # ── AI observability (WS3) ───────────────────────────────────────────────
    # Telemetry is purely additive: it writes an audit row and changes no
    # decision, no API payload, and no existing behaviour. Off ⇒ no rows.
    AI_TELEMETRY_ENABLED: bool = os.environ.get("AI_TELEMETRY_ENABLED", "1") != "0"
    # Cost estimation rates (USD per million tokens). These MUST be set to the
    # deployment's actual contracted rates; the defaults are indicative only.
    AI_COST_PER_MTOK_INPUT: float = float(os.environ.get("AI_COST_PER_MTOK_INPUT", "1.0"))
    AI_COST_PER_MTOK_OUTPUT: float = float(os.environ.get("AI_COST_PER_MTOK_OUTPUT", "5.0"))
    # Two-sided health band on the DEGRADED rate. A gate that never rejects is
    # as suspicious as one that rejects constantly.
    AI_DEGRADED_RATE_MAX: float = float(os.environ.get("AI_DEGRADED_RATE_MAX", "0.25"))
    AI_DEGRADED_RATE_MIN: float = float(os.environ.get("AI_DEGRADED_RATE_MIN", "0.005"))
    AI_HEALTH_MIN_SAMPLE: int = int(os.environ.get("AI_HEALTH_MIN_SAMPLE", "20"))


settings = Settings()
# Re-resolve PIE-derived paths in case PIE_PARSER_ROOT came from the env.
settings.PIE_CORPUS = _path_env(
    "PIE_CORPUS", settings.PIE_PARSER_ROOT / "corpora" / "kmt_zcnc_2026-07_nomenclature.csv"
)
settings.PIE_PACK = _path_env("PIE_PACK", settings.PIE_PARSER_ROOT / "packs" / "kennametal_widia")
