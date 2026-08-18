"""pie-portal backend — FastAPI application.

The PIE Quote Builder API: RFQ intake resolved through the pie-parser
Product Intelligence Engine (in-process), priced against a Zoho Books adapter,
with role-gated economics.
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import entitlements as plan
from .config import settings
from .observability.instrumentation import api_instrumentation_middleware
from .pie_service import pie_service
from .routers import (accounts, admin, ai_settings, approvals, attribution,
                      commercial, connections, data_status,
                      decisions, entitlements, identity, internal,
                      onboarding, outcomes, platform_auth, quote,
                      insight, quote_intelligence, quote_support, trust)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pie_portal")

# Fail fast in production if the auth-signing secret was never overridden — the
# default is public, so a stale default would let anyone forge a valid token for
# any user/role (full cost/margin access). Refuse to boot rather than run open.
if settings.is_production and settings.AUTH_SECRET == "dev-secret-change-me":
    raise RuntimeError(
        "AUTH_SECRET is still the development default in a production environment. "
        "Set AUTH_SECRET to a strong, secret value before starting."
    )

# Same reasoning, for the key that encrypts every organization's Zoho client
# secret and refresh token at rest — the default is public (it's in the source),
# so a stale default would make every stored connection's credentials
# recoverable by anyone who has read this file.
_DEV_CREDENTIAL_KEY = "sIfoCtwlOtGqxAtOkV5t3Rz-i6ZQ2VuTNQeXHpxTfWA="
if settings.is_production and settings.CREDENTIAL_ENCRYPTION_KEY == _DEV_CREDENTIAL_KEY:
    raise RuntimeError(
        "CREDENTIAL_ENCRYPTION_KEY is still the development default in a production "
        "environment. Generate a real one with:\n"
        "  python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"\n"
        "and set it before starting."
    )


#: Filled in at startup when the database is behind the code, so a screen can
#: say so rather than leaving an operator to read server logs.
#:
#: ``migration`` holds the classified migration state (see ``migration_state``)
#: rather than a guess. The client used to render "the usual cause is a pending
#: alembic upgrade head" for any undescribed 500, which was a plausible sentence
#: printed without evidence — and wrong in the case that actually bit, where the
#: schema was unstamped and upgrading could not work at all.
SCHEMA_GAP: dict[str, object] = {"message": None, "migration": None}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Prepare the database, then warm the pie-parser engine.

    Bootstrapping first means a fresh clone can be started with nothing but
    ``uvicorn app.main:app`` — no separate migrate/seed step, which is easy to
    miss on Windows where the Makefile is unavailable and previously produced
    ``no such table: users`` on the first login. It is skipped in production,
    where migrations are a deliberate deploy step.

    Both steps are non-fatal: the app starts either way, and says what is wrong.
    """
    if settings.AUTO_BOOTSTRAP and not settings.is_production:
        try:
            from .bootstrap import bootstrap

            summary = bootstrap()
            log.info("database ready (schema via %s, org %s).",
                     summary.get("schema"), summary.get("organization_id"))
        except Exception:  # noqa: BLE001
            log.exception("database bootstrap failed; sign-in will not work until "
                          "'python -m app.bootstrap' is run successfully.")

    # Said once, at startup, rather than discovered later as a bare 500 on
    # whichever request first touches a column that does not exist yet. A
    # deployment that pulls new code and forgets the migration otherwise looks
    # healthy until someone opens the one screen that reads the new column.
    #
    # Two checks, because they answer different questions and either can be the
    # one that is wrong: the migration state says where this database sits in the
    # revision history, and the column check says whether the schema can actually
    # serve the code. A database at head with a hand-edited table fails only the
    # second; a database built by ``create_all`` fails only the first.
    try:
        from .db import engine, SessionLocal
        from .migration_state import inspect_database
        from .schema_check import check_at_startup
        from .observability.instrumentation import instrument_database
        from .observability.health import register_health_checks

        # Initialize observability infrastructure
        instrument_database(engine)
        register_health_checks(engine, SessionLocal)
        log.info("observability infrastructure initialized")

        state = inspect_database(engine)
        SCHEMA_GAP["migration"] = state.to_dict()
        if not state.healthy:
            log.error("%s", state.summary)
        SCHEMA_GAP["message"] = check_at_startup(engine)
    except Exception:  # noqa: BLE001
        log.exception("schema check failed; continuing")

    if os.environ.get("PIE_WARM", "1") != "0":
        try:
            pie_service.warm()
            log.info("pie-parser engine warmed and ready.")
        except Exception:  # noqa: BLE001
            log.exception("pie-parser warm-up failed; lines show PIE OFFLINE until fixed.")

    # The automatic sync. Non-fatal like everything above it: a platform that
    # cannot schedule is degraded, and one that will not start over it is down.
    # The function itself declines on a fixture source, so this is safe to call
    # unconditionally.
    try:
        from .ingestion.scheduler import start_scheduler

        start_scheduler()
    except Exception:  # noqa: BLE001
        log.exception("auto-sync scheduler failed to start; manual sync still works.")
    yield


# The interactive docs and the schema behind them are development affordances,
# and in production they are an unauthenticated index of every route and model
# in the platform — including the trust, admin and margin-policy surfaces. On a
# managed host the API has a public hostname of its own, so "internal" is not a
# property anything enforces. They stay on outside production, where they are
# how people read the API.
_docs = None if settings.is_production else "/docs"
_redoc = None if settings.is_production else "/redoc"
_openapi = None if settings.is_production else "/openapi.json"

app = FastAPI(title="PIE — Commercial Decision Platform", version="0.1.0",
              lifespan=lifespan,
              docs_url=_docs, redoc_url=_redoc, openapi_url=_openapi)

@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Say what broke, and give the reader something to search the log for.

    An unhandled error used to reach the browser as a bare "Internal Server
    Error", which told nobody anything — so the client filled the silence by
    naming the most plausible cause it could think of, and pointed at the
    migration state. That is guessing dressed as diagnosis, and it sent an
    operator to `/api/health` for an error that had nothing to do with the
    schema. It reported CURRENT, correctly, and the actual failure stayed
    invisible.

    So: log the traceback with a short id, and return the same id with the
    exception's *type*. The type is safe to show — `IntegrityError`,
    `ZohoAuthError`, `KeyError` each send you somewhere different — while the
    message may carry a row, a token or a name, and belongs in the log where
    access is already controlled.
    """
    error_id = uuid.uuid4().hex[:8]
    # noqa: LOG004 — ruff sees no syntactic `except` block here and assumes the
    # traceback is unavailable. It is not: Starlette invokes exception handlers
    # from inside its own `except`, so `sys.exc_info()` is set and `.exception()`
    # logs the traceback correctly. Downgrading to `.error()` to satisfy the rule
    # would drop the traceback — the exact silence the docstring above is about.
    log.exception("unhandled error %s on %s %s",  # noqa: LOG004
                  error_id, request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (f"{type(exc).__name__} while handling this request "
                       f"(error {error_id}). The server log has the traceback; "
                       f"search it for {error_id}."),
            "error_id": error_id,
            "error_type": type(exc).__name__,
        },
    )


# Dev CORS: the Vite frontend runs on a separate origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Observability instrumentation: metrics, logging, health checks.
#
# `app.middleware("http")`, not `add_middleware`. The latter takes a middleware
# *class* and instantiates it as `cls(app, **kwargs)`; this is a function of
# `(request, call_next)`, so registering it that way raised
# `api_instrumentation_middleware() missing 1 required positional argument:
# 'call_next'` when Starlette built the middleware stack.
#
# That happens on the first request rather than at import, which is why the app
# still started and why the breakage showed up as nine unrelated-looking test
# failures — a 500 with an empty body, a health check that returned nothing —
# rather than as anything naming this line. The instrumentation had never
# actually run.
app.middleware("http")(api_instrumentation_middleware)

# Commercial Decision Platform (Phase 1 foundation).
app.include_router(platform_auth.router)
# The way in for a tenant nobody has provisioned by hand. Beside the sign-in
# router because it answers the same question — how does a person get a session
# — and no plan gate for the same reason: a plan is something an organization
# has, and this runs before there is one.
app.include_router(onboarding.router)

# The Quote Builder. One surface of the same product, and — since the demo login
# beside it was removed — one identity: `/api/quotes` authenticates the same
# platform user every `/api/v1` endpoint does.
app.include_router(quote.router)
app.include_router(internal.router)
# The intelligence surfaces — the decision layer and the insight screens — are
# what the paid plan is. Gated here, at inclusion, so the plan boundary is one
# visible declaration rather than a check sprinkled through the routers; the
# routes inside stay role-scoped exactly as before.
app.include_router(decisions.router,
                   dependencies=[Depends(plan.require_feature("intelligence"))])
# The afterlife of an accepted decision card — realised outcomes. Gated with
# the queue it measures: an outcome is derived from a decision, so it cannot be
# the one intelligence surface a free plan can read.
app.include_router(outcomes.router,
                   dependencies=[Depends(plan.require_feature("intelligence"))])
# Deliberately *not* gated, unlike the four intelligence surfaces above and
# below it. Quote-support is the free Quote Builder's own inline recommendation
# on a line a salesperson is actively quoting (it renders in the supply drawer):
# it phrases the deterministic facts and returns no RESTRICTED cost/margin data,
# which is quoting — the free Quote Desk — not the paid decision layer. It does
# persist a QUOTE_CONTEXT decision, but the surfaces that *read* the decision
# store (decisions, outcomes, insight, attribution) are all gated, so a free
# org can write one and never read it back. Gating this instead would 403 a
# working panel inside the free desk. Left ungated on purpose; stated here so
# the next audit finds a decision rather than an oversight (the absence of both
# a gate and this note is what flagged it once).
app.include_router(quote_support.router)
app.include_router(accounts.router)
app.include_router(data_status.router)
app.include_router(commercial.router)
app.include_router(quote_intelligence.router)
app.include_router(approvals.router)
app.include_router(admin.router)
app.include_router(identity.router)
app.include_router(connections.router)
app.include_router(trust.router)
app.include_router(insight.router,
                   dependencies=[Depends(plan.require_feature("intelligence"))])
# What the intelligence layer was worth, measured. **Deliberately not gated
# here**, unlike every other intelligence surface, and the exception is argued in
# full at the top of `routers/attribution.py`.
#
# In short: gating it at inclusion meant the screen a renewal is argued from went
# dark on the day the trial ended, while the detectors kept writing the evidence
# it would have shown (`jobs.py` runs them on every sync, ungated). The router
# now applies the rule per route — an organization reads the ledger up to the end
# of the window it was entitled to, and rolling detection past that is what the
# plan buys. Role scoping is unchanged and is what keeps cost away from a
# salesperson; that was never the plan gate's job.
app.include_router(attribution.router)
app.include_router(ai_settings.router)
app.include_router(entitlements.router)


@app.get("/api/health")
def health(response: Response) -> dict:
    """Liveness plus the two facts that actually decide whether this process can serve.

    It used to return ``{"ok": true}`` unconditionally, which made it useless for
    the failure it should have caught first: a deployment whose database is
    behind, or whose schema was built outside Alembic, answers this endpoint
    perfectly while every real request 500s. A health check that cannot fail is
    a health check nobody should route on.

    Checked live rather than read from the startup snapshot — an operator who
    migrates a running deployment wants to see it go green without a restart,
    and a database that disappears after boot should stop reporting healthy.

    503 when unhealthy, so a load balancer or deploy gate sees it without having
    to parse the body. The body still explains itself either way; the whole
    point is that the answer names the state and its fix rather than guessing.
    """
    from .db import engine
    from .migration_state import inspect_database
    from .schema_check import describe, missing_columns

    body: dict = {"ok": True, "service": "pie-portal", "version": app.version}
    try:
        state = inspect_database(engine)
        body["migration"] = state.to_dict()
        gap = describe(missing_columns(engine))
        body["schema_gap"] = gap
        # Which dialect this process is actually serving, and how full its
        # pool is. Informational, never part of `ok`: a busy pool is load, not
        # ill health — but when requests start timing out with "QueuePool
        # limit reached", this is the line that says so without a debugger.
        body["database"] = {"dialect": engine.dialect.name,
                            "pool": engine.pool.status()}
        body["ok"] = state.healthy and gap is None
    except Exception as exc:  # noqa: BLE001
        log.exception("health check could not read the database")
        body["ok"] = False
        body["error"] = f"{type(exc).__name__}: {exc}"
        body["migration"] = None

    if not body["ok"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body
