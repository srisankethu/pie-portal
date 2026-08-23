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

    # ── logging ──────────────────────────────────────────────────────────────
    # What the process writes, and where it can be read from afterwards. See
    # `app/observability/logs.py`; the reason these exist is that "check the
    # server log" was advice nobody hosting this could act on.
    #
    # LOG_LEVEL     root level for everything (DEBUG|INFO|WARNING|ERROR).
    # LOG_FILE      also write to this file, rotating, for a deployment with a
    #               disk. Empty means stdout only, which is right where the
    #               platform captures stdout and wrong where nothing does.
    # LOG_FILE_MAX_BYTES / LOG_FILE_KEEP  the rotation, so a long-running box
    #               cannot fill its disk with logs from March.
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")
    LOG_FILE: str = os.environ.get("LOG_FILE", "")
    LOG_FILE_MAX_BYTES: int = int(os.environ.get("LOG_FILE_MAX_BYTES",
                                                 str(10 * 1024 * 1024)))
    LOG_FILE_KEEP: int = int(os.environ.get("LOG_FILE_KEEP", "5"))

    # How many lines of one sync's log are kept in the database for the screen
    # that reads it. A cap, because a pull of five years at INFO is unbounded
    # and a log table nobody can page through is its own kind of missing log.
    # Warnings and errors are *never* dropped by it — see `observability/logs`.
    SYNC_LOG_MAX_LINES: int = int(os.environ.get("SYNC_LOG_MAX_LINES", "5000"))

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() == "production"

    @property
    def queue_dispatch(self) -> bool:
        """Whether background work is dispatched through the durable queue."""
        return self.SYNC_DISPATCH == "queue"

    @property
    def queue_worker_enabled(self) -> bool:
        """Whether this process should run a queue worker.

        Derived rather than a second switch to keep in step: a deployment that
        chose the queue needs something to drain it, and one that did not would
        be polling a table nobody writes to. ``QUEUE_WORKER`` overrides in both
        directions, which is what lets an API process and a worker process run
        the same image with different jobs.
        """
        if self.QUEUE_WORKER in ("1", "true", "yes", "on"):
            return True
        if self.QUEUE_WORKER in ("0", "false", "no", "off"):
            return False
        return self.queue_dispatch

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

    # ── The public demonstration workspace (app/routers/onboarding.py) ───────
    # A tenant a stranger may enter without an account, to see what the product
    # does before deciding whether it is worth signing up for. **Empty unless a
    # deployment names one**, and empty means the endpoint is not there at all
    # rather than there and refusing — a single-tenant install pulling new code
    # must not acquire an unauthenticated door it never asked for.
    #
    # Naming the organization *and* the user explicitly rather than inferring
    # "the owner of that org": inference would pick a different account the day
    # somebody adds a second owner, and the account a stranger signs in as is
    # not a thing to decide by whichever row sorts first.
    #
    # A demo principal is refused every unsafe method in `authz`, so what this
    # opens is a read of fabricated data. Point it at an organization that was
    # seeded by `app.demo` and holds no connection and no credentials; nothing
    # here checks that, because a settings module cannot, and
    # `test_public_demo.py` does.
    PUBLIC_DEMO_ORG_ID: str = os.environ.get("PUBLIC_DEMO_ORG_ID", "")
    PUBLIC_DEMO_EMAIL: str = os.environ.get("PUBLIC_DEMO_EMAIL", "")

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

    # ── Zoho OAuth: this deployment's own registered application ───────────────
    # The customer-facing authorization flow. Unset by default and *checked*
    # rather than assumed — `oauth.configured()` gates the endpoints and the
    # screen, so a deployment without an application says the flow is
    # unavailable instead of offering a button that cannot complete. Offering
    # that button anyway is what got the first implementation deleted.
    #
    # These are the platform's credentials, registered once in the Zoho API
    # console, and they are not a tenant's: the grant an authorization produces
    # belongs to the Zoho user who consented. A business connecting its own
    # books can still use a Self Client refresh token on the manual path and
    # needs none of this; a Marketplace listing cannot, because a stranger
    # installing from Zoho has no way to register a redirect URI.
    ZOHO_OAUTH_CLIENT_ID: str = os.environ.get("ZOHO_OAUTH_CLIENT_ID", "")
    ZOHO_OAUTH_CLIENT_SECRET: str = os.environ.get("ZOHO_OAUTH_CLIENT_SECRET", "")
    # Must match the redirect URI registered against that application, exactly.
    # Example: "https://pie.example.com/api/v1/connections/zoho/callback"
    ZOHO_OAUTH_REDIRECT_URI: str = os.environ.get("ZOHO_OAUTH_REDIRECT_URI", "")
    # How long an authorization may stay in flight. Ten minutes is the usual
    # figure and is longer than a consent screen takes to read.
    ZOHO_OAUTH_STATE_TTL_SECONDS: int = int(
        os.environ.get("ZOHO_OAUTH_STATE_TTL_SECONDS", "600"))

    # Where the browser is sent after the callback has done its work. The
    # callback is reached by following Zoho's redirect, so it answers a person
    # rather than a script and has to hand them back to a screen. Empty means
    # same-origin, which is right where the API and the app are served together
    # (the Caddy self-host) and wrong where they are not (Vercel in front of
    # Railway) — set it there.
    FRONTEND_ORIGIN: str = os.environ.get("FRONTEND_ORIGIN", "")
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

    # ── Background work: threads, or the durable queue ────────────────────────
    # How a background sync is dispatched once its run row exists.
    #
    #   "thread" — start a daemon thread, the behaviour since the first sync
    #              took minutes. Nothing else to run; the work dies with the
    #              process.
    #   "queue"  — commit a message to `queued_messages` and let the worker
    #              claim it. The request survives a restart, retries with a
    #              bounded backoff, and is safe with more than one process.
    #
    # "thread" is the default because it is what this deployment has always
    # done and the queue path needs a worker running to do anything at all — a
    # default that silently requires a second moving part is a default that
    # makes a fresh clone's Sync button do nothing. A deployment that replaces
    # containers under load, or runs more than one, wants "queue".
    SYNC_DISPATCH: str = os.environ.get("SYNC_DISPATCH", "thread").strip().lower()
    # Force the worker on (or off) independently of the dispatch above: an API
    # process with SYNC_DISPATCH=queue and QUEUE_WORKER=0, beside a worker
    # process that runs nothing else, is the shape this splits into. Empty
    # means "follow SYNC_DISPATCH".
    QUEUE_WORKER: str = os.environ.get("QUEUE_WORKER", "").strip().lower()
    # How long the idle worker waits before looking again. One indexed query
    # per wake, so this is the latency of starting a queued job.
    QUEUE_POLL_SECONDS: float = float(os.environ.get("QUEUE_POLL_SECONDS", "2"))
    # How long a claimed message may go without a heartbeat before it is
    # treated as a dead worker's and handed back. Matches the sync job's own
    # patience: a slow pull is not a crash.
    QUEUE_STALE_MINUTES: int = int(os.environ.get("QUEUE_STALE_MINUTES", "10"))
    # Attempts before a message is dead-lettered with its last error. Bounded
    # because a job that fails identically forever should be readable, not
    # busy.
    QUEUE_MAX_ATTEMPTS: int = int(os.environ.get("QUEUE_MAX_ATTEMPTS", "3"))
    QUEUE_BACKOFF_SECONDS: float = float(os.environ.get("QUEUE_BACKOFF_SECONDS", "30"))
    # How long a finished message is kept. Rows survive completion on purpose —
    # a queue with no history cannot answer "did that run, and when" — but kept
    # and kept forever are different promises, and only one of them is a table
    # that stops growing. The two differ because the rows do: a DONE message is
    # a receipt, a dead-lettered one is unfinished business somebody may still
    # act on, and deleting that is deleting the evidence of a failure. 0 on
    # either means keep forever.
    QUEUE_DONE_RETENTION_DAYS: int = int(
        os.environ.get("QUEUE_DONE_RETENTION_DAYS", "14"))
    QUEUE_DEAD_LETTER_RETENTION_DAYS: int = int(
        os.environ.get("QUEUE_DEAD_LETTER_RETENTION_DAYS", "90"))
    # How often the worker sweeps them out. Not per pass: it is a delete over a
    # date range, and running it every two seconds would be the loop's main
    # activity on an idle queue.
    QUEUE_PRUNE_INTERVAL_SECONDS: float = float(
        os.environ.get("QUEUE_PRUNE_INTERVAL_SECONDS", "3600"))
    QUEUE_MAX_BACKOFF_SECONDS: float = float(
        os.environ.get("QUEUE_MAX_BACKOFF_SECONDS", "900"))

    # How long ``/api/health`` may answer the schema question from memory. The
    # cache key already carries the database's Alembic revision, so a migration
    # is reflected on the next poll whatever this is; this bounds only the case
    # the key cannot see — a schema altered by hand under an unchanged stamp.
    # 0 disables it and every poll reflects all ~70 tables again.
    SCHEMA_GAP_CACHE_TTL_SECONDS: float = float(
        os.environ.get("SCHEMA_GAP_CACHE_TTL_SECONDS", "30"))

    # ── Resolution cache ──────────────────────────────────────────────────────
    # The nomenclature engine's verdict for one product code, keyed by the
    # catalogue ruleset and the confirmed mappings it read — see app/cache.py
    # for why those belong in the key. A quote re-resolves the same codes every
    # time it is rebuilt, and a resolution is a scan of the whole catalogue.
    # 0 disables the cache without a code change, which is the off switch a
    # deployment needs if it ever suspects a stale answer.
    PIE_CACHE_SIZE: int = int(os.environ.get("PIE_CACHE_SIZE", "2048"))
    # A ceiling on staleness for the one input the key cannot see: a catalogue
    # rebuilt in place under an unchanged ruleset version.
    PIE_CACHE_TTL_SECONDS: float = float(
        os.environ.get("PIE_CACHE_TTL_SECONDS", "1800"))

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
    # OpenRouter is a gateway rather than a lab: one key, an OpenAI-compatible
    # Chat Completions endpoint, and a model id that names the upstream vendor
    # ("openai/gpt-4o", "anthropic/claude-sonnet-4"). That makes the model the
    # setting worth changing, so the default is stated as a fully qualified id.
    #
    # It defaults to "openrouter/free" — the free router, which picks among the
    # zero-cost models rather than naming one. Interpretation is a small bounded
    # task and the deterministic engine computes every figure either way, so the
    # cheapest thing that can read a fact set is the right default; a deployment
    # that wants a specific model names one. Two consequences worth knowing:
    # free models carry the tightest rate limits on the account, and
    # AI_COST_PER_MTOK_* still bill this at whatever they say — set them to 0
    # while running free, or the AI-spend screen reports a confident wrong
    # number for calls that cost nothing.
    OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_API_BASE: str = os.environ.get(
        "OPENROUTER_API_BASE", "https://openrouter.ai/api")
    OPENROUTER_MODEL: str = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
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
