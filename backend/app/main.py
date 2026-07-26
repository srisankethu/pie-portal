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

from .pie_service import pie_service
from .routers import auth, decisions, internal, platform_auth, quote, quote_support

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pie_portal")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Warm the pie-parser engine + catalogue so the first request is fast.

    Non-fatal: if warming fails (e.g. catalogue build unavailable in a
    constrained env), the app still starts and each line degrades to PIE OFFLINE.
    """
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
