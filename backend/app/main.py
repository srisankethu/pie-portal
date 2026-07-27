"""pie-portal backend — FastAPI application.

The Sanketh Quote Builder API: RFQ intake resolved through the pie-parser
Product Intelligence Engine (in-process), priced against a Zoho Books adapter,
with role-gated economics.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .pie_service import pie_service
from .routers import auth, decisions, internal, platform_auth, quote, quote_support

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

    if os.environ.get("PIE_WARM", "1") != "0":
        try:
            pie_service.warm()
            log.info("pie-parser engine warmed and ready.")
        except Exception:  # noqa: BLE001
            log.exception("pie-parser warm-up failed; lines show PIE OFFLINE until fixed.")
    yield


app = FastAPI(title="pie-portal — Sanketh Quote Builder", version="0.1.0", lifespan=lifespan)

# Dev CORS: the Vite frontend runs on a separate origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Legacy Quote Builder (preserved; not part of the V1 decision platform).
app.include_router(auth.router)
app.include_router(quote.router)

# Commercial Decision Platform (Phase 1 foundation).
app.include_router(platform_auth.router)
app.include_router(internal.router)
app.include_router(decisions.router)
app.include_router(quote_support.router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "pie-portal", "version": app.version}
