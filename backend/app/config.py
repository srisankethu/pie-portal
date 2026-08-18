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


def _normalize_database_url(url: str) -> str:
    """Name the driver this deployment actually ships: psycopg 3.

    A bare ``postgresql://`` resolves to SQLAlchemy's *default* PostgreSQL
    driver, which is psycopg2 — a package `requirements.txt` deliberately does
    not install, because this codebase uses psycopg 3. The failure is
    ``ModuleNotFoundError: No module named 'psycopg2'`` raised from
    ``create_engine`` at import time, so the process dies before it can log
    anything about the database, and the traceback names a library nobody put
    in the URL.

    That URL form is not a typo — it is what every managed provider hands you.
    Neon, Railway's database linking, Render and Heroku all inject
    ``postgresql://`` (Heroku still emits the older ``postgres://``), so the
    fix cannot be "paste it correctly": the value arrives that way, and a
    linked variable is re-injected over any hand-edit.

    Only a URL that names *no* driver is rewritten. An explicit
    ``postgresql+psycopg2://`` or ``postgresql+asyncpg://`` is someone stating
    a deliberate choice, and it is left exactly as written.
    """
    if url.startswith("postgres://"):          # Heroku's legacy spelling
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):        # no driver named → psycopg 3
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings:
    """Process-wide settings (plain attributes; no external deps)."""

    # Location of the pie-parser package (the ./pie-parser submodule by default).
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

    # Deployment environment. "production" turns on hard guards (real auth secret
    # required, demo-seed disabled). Anything else is treated as dev/test.
    APP_ENV: str = os.environ.get("APP_ENV", "development")

    # Demo auth secret (dev only). A real deployment injects this.
    AUTH_SECRET: str = os.environ.get("AUTH_SECRET", "dev-secret-change-me")

    # ── session lifetime ─────────────────────────────────────────────────────
    # Two limits, because they answer different questions. The idle timeout ends
    # a session nobody is using: a browser left open on a shared desk stops being
    # a way in overnight. The absolute age ends a session no matter how actively
    # it is used, so a token that was captured and is being kept warm still dies
    # on a known date. Only the absolute one existed before, which meant a
    # forgotten sign-in stayed live for a month.
    #
    # 12 hours covers a long working day without asking anyone to sign in twice
    # before dinner, and expires by the next morning.
    SESSION_IDLE_TIMEOUT_SECONDS: int = int(
        os.environ.get("SESSION_IDLE_TIMEOUT_SECONDS", str(12 * 60 * 60)))
    SESSION_MAX_AGE_SECONDS: int = int(
        os.environ.get("SESSION_MAX_AGE_SECONDS", str(30 * 24 * 60 * 60)))

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
    DATABASE_URL: str = _normalize_database_url(os.environ.get(
        "DATABASE_URL", f"sqlite:///{REPO_ROOT / 'backend' / 'data' / 'platform.db'}"
    ))
    SQL_ECHO: bool = os.environ.get("SQL_ECHO", "0") == "1"

    # Postgres connection pool (ignored on SQLite). The defaults are sized for
    # this deployment's actual shape — compose runs UVICORN_WORKERS=2, so the
    # worst case is workers × (size + overflow) = 2 × 15 = 30 connections,
    # comfortably inside stock Postgres' max_connections=100 with room for the
    # release job, psql, and a second replica. Raise deliberately, with that
    # arithmetic redone, not because a benchmark said bigger is faster.
    #   POOL_TIMEOUT  how long a request waits for a free connection before
    #                 failing loudly (better a named 30s failure than a silent
    #                 pile-up);
    #   POOL_RECYCLE  retire connections before typical NAT/proxy idle cutoffs
    #                 (managed Postgres front-ends commonly drop at 30–60 min;
    #                 30 min stays under all of them). pool_pre_ping catches
    #                 what recycling misses.
    DB_POOL_SIZE: int = int(os.environ.get("DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW: int = int(os.environ.get("DB_MAX_OVERFLOW", "10"))
    DB_POOL_TIMEOUT: int = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
    DB_POOL_RECYCLE: int = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

    # Log any statement slower than this many milliseconds (0 = off). Read by
    # observability/instrumentation.py — the one place query timing lives.
    # 1000 keeps the threshold that module always had, now tunable; the
    # compose stack tightens it to 500. The log line carries the statement and
    # duration, never parameter values — bind parameters hold customer names
    # and credentials, and trust/ exists so those never reach a log file.
    DB_SLOW_QUERY_MS: int = int(os.environ.get("DB_SLOW_QUERY_MS", "1000"))

    # ── Redis (provisioned infrastructure; no feature requires it yet) ──────
    # Both compose stacks run a Redis next to the API for the state that must
    # one day live outside a process: cross-replica rate limiting (the signup
    # limiter in routers/onboarding.py is in-process and says so), cache, and
    # background-job coordination if the thread-based sync ever needs to span
    # replicas. Empty means "none configured", and nothing may *require* Redis
    # to serve a request — a candidate consumer degrades to its in-process
    # behaviour, the way the signup limiter behaves today. Kept honest on
    # purpose: config that pretends a dependency is load-bearing before any
    # code reads it teaches operators to ignore this file.
    REDIS_URL: str = os.environ.get("REDIS_URL", "")

    # Create the database + schema + demo users on startup, so a fresh clone
    # runs without a separate migrate/seed step. Always disabled in production,
    # where migrations are a deliberate, reviewed deploy step.
    AUTO_BOOTSTRAP: bool = os.environ.get("AUTO_BOOTSTRAP", "1") != "0"
    # Seed the realistic demo dataset on startup. Never in production.
    DEMO_SEED_ON_START: bool = os.environ.get("DEMO_SEED_ON_START", "1") != "0"
    #: Whether an account the *system* issues a password to must change it before
    #: the account can be used — the seeded demo users, and the owner created when
    #: a tenant is provisioned. On by default and meant to stay on: the seed
    #: password is published in the README, so an account still holding it is an
    #: account anybody can sign into.
    #:
    #: This switches who gets *flagged*, never the gate. `authz.current_principal`
    #: refuses a flagged account unconditionally and no setting turns that off.
    #: The test suite sets this to 0 because 55 fixtures seed accounts in order to
    #: exercise something that is not the credential lifecycle; the forced change
    #: has its own tests, which set the flag explicitly.
    #:
    #: A password an owner issues by hand from Settings is *not* covered here and
    #: is always flagged: that is a deliberate act in a live system, not setup.
    ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD: bool = (
        os.environ.get("ISSUED_ACCOUNTS_MUST_CHANGE_PASSWORD", "1") != "0")

    # ── Plans / entitlements (app/entitlements.py) ───────────────────────────
    # The plan an organization is on when its own row does not say ("free" |
    # "intelligence" | "platform"). Defaults to "platform" so an existing
    # deployment — including dev and the test suite — keeps every feature it
    # has today; a hosted multi-tenant deployment sets DEFAULT_PLAN=free and
    # upgrades organizations explicitly. An unrecognised value resolves to
    # "free" and logs: a typo must never widen what a tenant may use.
    DEFAULT_PLAN: str = os.environ.get("DEFAULT_PLAN", "platform")
    # How long the one-per-books free month of Commercial Intelligence runs.
    INTELLIGENCE_TRIAL_DAYS: int = int(os.environ.get("INTELLIGENCE_TRIAL_DAYS", "30"))

    # ── Self-serve sign-up (app/onboarding.py) ───────────────────────────────
    # Whether anyone who can reach this deployment may create a tenant for
    # themselves. **Off unless a deployment says otherwise**, and that default
    # is the whole point rather than caution: this is the one endpoint here that
    # writes rows without a token, so an existing single-tenant install that
    # pulls new code must not silently start accepting strangers. A hosted
    # deployment sets SELF_SERVE_SIGNUP=1 (and, almost certainly, DEFAULT_PLAN=free).
    #
    # It does not decide the *plan* a sign-up lands on — `onboarding.SIGNUP_PLAN`
    # pins that to free explicitly, because DEFAULT_PLAN defaults to "platform"
    # and inheriting it here would hand every stranger the top tier.
    SELF_SERVE_SIGNUP: bool = os.environ.get("SELF_SERVE_SIGNUP", "0") == "1"
    # Sign-ups accepted from one address per hour, across the process. A speed
    # bump, not a control — see `routers/onboarding.py`, which says plainly what
    # it does and does not stop.
    SIGNUP_RATE_LIMIT_PER_HOUR: int = int(
        os.environ.get("SIGNUP_RATE_LIMIT_PER_HOUR", "5"))

    # The single supported organization for V1 (one org, one ERP). organization_id
    # is carried on every record for future multi-org, but no cross-org logic exists.
    DEFAULT_ORG_ID: str = os.environ.get("DEFAULT_ORG_ID", "org_pie")
    DEFAULT_ORG_NAME: str = os.environ.get("DEFAULT_ORG_NAME", "PIE")
    DEFAULT_CURRENCY: str = os.environ.get("DEFAULT_CURRENCY", "INR")
    # Headline sales-tax rate and what the jurisdiction calls it, applied to a
    # quote subtotal before any ERP has priced it. Defaults are Indian because
    # the first deployment is; they are settings rather than constants because
    # the next one will not be. A rate of 0 renders no tax line at all.
    SALES_TAX_RATE: float = float(os.environ.get("SALES_TAX_RATE", "0.18"))
    SALES_TAX_LABEL: str = os.environ.get("SALES_TAX_LABEL", "GST")

    # There are deliberately no ZOHO_* credential settings. A Zoho grant (client
    # id, secret, refresh token, data centre, organization id) belongs to one
    # tenant and is stored per-connection in the database, encrypted at rest — a
    # multi-tenant platform cannot resolve one tenant's credential from a
    # process-wide environment variable. Connect a company under Settings →
    # Connections. (A live contract test reads real values straight from the
    # environment for the account it exercises; that is test scaffolding, not a
    # runtime path.)

    # "fixture" (deterministic offline source) or "api" (live Zoho).
    ZOHO_SOURCE: str = os.environ.get("ZOHO_SOURCE", "fixture")

    # Which adapter the Quote Builder reads prices from and writes estimates to:
    # "mock" (deterministic, offline — dev/test/demo default) or "live" (the real
    # Zoho Books API). Separate from ZOHO_SOURCE above because the two answer
    # different questions: that one is where analysis *reads history* from, this
    # one is whether the quoting screen may *write*. Defaulting this to live
    # would mean a fresh clone could put an estimate in front of a customer.
    ZOHO_QUOTE_SERVICE: str = os.environ.get("ZOHO_QUOTE_SERVICE", "mock")
    # How long a reachability probe stands for. ``available`` is read once per
    # quote line, so without a cache a fifty-line RFQ spends fifty calls of the
    # rate-limit budget asking whether Zoho is up.
    ZOHO_HEALTH_TTL_SECONDS: float = float(
        os.environ.get("ZOHO_HEALTH_TTL_SECONDS", "60"))

    # Live pull shape. History is bounded because the detectors compare a recent
    # window against a prior one — pulling a decade of ledger costs API calls and
    # buys nothing.
    ZOHO_TIMEOUT_SECONDS: float = float(os.environ.get("ZOHO_TIMEOUT_SECONDS", "30"))
    ZOHO_PAGE_SIZE: int = int(os.environ.get("ZOHO_PAGE_SIZE", "200"))
    ZOHO_MAX_PAGES: int = int(os.environ.get("ZOHO_MAX_PAGES", "50"))
    ZOHO_HISTORY_DAYS: int = int(os.environ.get("ZOHO_HISTORY_DAYS", "730"))
    # How often the automatic sync pulls each organization's books, in hours.
    # The default for organizations that never chose their own cadence — each
    # can override (or switch off with 0) from the Data & connection screen,
    # stored in Organization.config. 0 here disables the default for everyone
    # who has not opted in. Only live deployments schedule at all; the fixture
    # source has nothing to keep fresh.
    SYNC_AUTO_HOURS: int = int(os.environ.get("SYNC_AUTO_HOURS", "6"))
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

    # Connecting a Zoho company is manual (a Self Client refresh token, entered
    # on the connections screen — see docs/zoho-setup.md). There is deliberately
    # no customer-facing browser OAuth authorize/callback flow: it was removed
    # because a Self Client is the right grant for a business connecting its own
    # books (no redirect URI to register, no consent round-trip), and a
    # half-built redirect flow beside the working manual one was only a trap. The
    # runtime auth that turns a stored refresh token into API access still lives
    # in ingestion/zoho_client.py and is untouched by that removal.

    # Version stamped onto deterministic artifacts for provenance/reproducibility.
    # (Threshold-config version is carried by SignalThresholds.version, not here.)
    DETECTOR_VERSION: str = os.environ.get("DETECTOR_VERSION", "v0")

    # ── AI Decision Layer ────────────────────────────────────────────────────
    # Provider is swappable; "mock" (deterministic, offline — dev/test default) or
    # "anthropic" (live). A cheap, fast model suits small-bundle interpretation.
    # Ask the provider not to retain the payload beyond the request. Expressed
    # as configuration rather than as a sentence on a website so it can be read
    # back through the disclosure endpoint and asserted by a test. Turning it
    # off is a deliberate act with a visible consequence.
    AI_ZERO_RETENTION: bool = os.environ.get("AI_ZERO_RETENTION", "1") != "0"
    # Keep the exact text sent to a model, encrypted under the tenant's own key,
    # so "show me what you sent about my business" has an answer.
    AI_LOG_PAYLOADS: bool = os.environ.get("AI_LOG_PAYLOADS", "1") != "0"
    AI_PROVIDER: str = os.environ.get("AI_PROVIDER", "mock")
    AI_MODEL: str = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")
    AI_MAX_TOKENS: int = int(os.environ.get("AI_MAX_TOKENS", "400"))   # bounded output
    AI_TIMEOUT_SECONDS: float = float(os.environ.get("AI_TIMEOUT_SECONDS", "20"))
    # Stamped on every telemetry row, so a change in narrative quality can be
    # attributed to the prompt that produced it. Bumped whenever the system
    # prompt changes: p2 added the per-decision-type guidance.
    PROMPT_VERSION: str = os.environ.get("PROMPT_VERSION", "p2")
    ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_API_BASE: str = os.environ.get("ANTHROPIC_API_BASE", "https://api.anthropic.com")
    # The other two live providers an organization can bring its own key for
    # (see ai/byok.py). Env values are the deployment-wide fallback, exactly as
    # ANTHROPIC_API_KEY is; a per-organization key stored from Settings wins.
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_API_BASE: str = os.environ.get("OPENAI_API_BASE", "https://api.openai.com")
    OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_API_BASE: str = os.environ.get(
        "GEMINI_API_BASE", "https://generativelanguage.googleapis.com")
    GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
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

    # ── detector outcome bands (decisions/outcomes.py) ───────────────────────
    # The share of *judged* decisions a human dismissed, per signal type. A band
    # rather than one ceiling, for the reason the AI band above is two-sided: a
    # detector nothing is ever dismissed from is as much a finding as a noisy one.
    #
    # Observability bounds, not commercial policy — so they live here beside the
    # AI health band and deliberately not in ``SignalThresholds``. They judge the
    # detectors; they do not feed them, and nothing computed from them is stamped
    # onto a row. Putting them in the thresholds hash would move ``th_…`` every
    # time a *report* was tuned, making past signals look re-judged when nothing
    # about what produced them had changed.
    SIGNAL_DISMISSAL_RATE_MAX: float = float(
        os.environ.get("SIGNAL_DISMISSAL_RATE_MAX", "0.40"))
    SIGNAL_DISMISSAL_RATE_MIN: float = float(
        os.environ.get("SIGNAL_DISMISSAL_RATE_MIN", "0.02"))
    # Lower than the AI sample floor: a detector opens far fewer cards than the
    # AI layer makes calls, and 20 judged MARGIN_DETERIORATION decisions could
    # take a quarter to accumulate.
    SIGNAL_OUTCOME_MIN_SAMPLE: int = int(
        os.environ.get("SIGNAL_OUTCOME_MIN_SAMPLE", "10"))


settings = Settings()
# Re-resolve PIE-derived paths in case PIE_PARSER_ROOT came from the env.
settings.PIE_CORPUS = _path_env(
    "PIE_CORPUS", settings.PIE_PARSER_ROOT / "corpora" / "kmt_zcnc_2026-07_nomenclature.csv"
)
settings.PIE_PACK = _path_env("PIE_PACK", settings.PIE_PARSER_ROOT / "packs" / "kennametal_widia")
