"""Item-name decoding for external callers — the seam Zoho Deluge talks to.

Deluge cannot run pie-parser: it is Python with a rules pack, and Zoho's
scripting language cannot import it. The alternative — porting the grammar into
Deluge — would produce a second parser that disagrees with the first within a
month. So the parser stays here and Zoho calls it over HTTP.

``POST /api/v1/pie/describe`` takes an item name and returns what the parser
made of it, including a ``confident`` flag. The flag is the contract: it is
false when the engine recognised nothing specific, and a caller that writes the
description anyway will overwrite a human's words with an empty string.

Authenticated by a shared secret in ``X-Pie-Key`` rather than a platform user
token, because the caller is a Zoho workflow with no person behind it. The
endpoint reads nothing and writes nothing — it decodes a string — so a leaked
key exposes the grammar, not the books.
"""
from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from ..config import settings
from ..pie_describe import describe

log = logging.getLogger("pie_portal.pie_api")

router = APIRouter(prefix="/api/v1/pie", tags=["pie"])

_MAX_BATCH = 100


def require_pie_key(x_pie_key: Optional[str] = Header(default=None)) -> None:
    """Shared-secret gate for machine callers.

    Refuses outright when no key is configured, rather than running open: an
    endpoint that silently needs no authentication because someone forgot to set
    an environment variable is the kind of thing nobody discovers until it is
    being scraped.
    """
    expected = (settings.PIE_API_KEY or "").strip()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PIE_API_KEY is not configured on this deployment, so the describe "
            "endpoint is closed. Set it before pointing a workflow at this.")
    if not x_pie_key or not hmac.compare_digest(x_pie_key.strip(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-Pie-Key")


class DescribeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=500)


@router.post("/describe", dependencies=[Depends(require_pie_key)])
def describe_one(body: DescribeRequest) -> dict:
    """Decode one item name.

    Always 200 with a result — an engine failure comes back as ``confident:
    false`` with an ``error``, because a workflow that gets a 500 will either
    retry forever or mark the item failed, and neither is right for "the parser
    did not recognise this".
    """
    return describe(body.name).to_dict()


class DescribeBatchRequest(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=_MAX_BATCH)


@router.post("/describe-batch", dependencies=[Depends(require_pie_key)])
def describe_many(body: DescribeBatchRequest) -> dict:
    """Decode many names in one call — for backfilling an existing catalogue.

    Worth having separately: doing this one HTTP call per item across a few
    thousand Zoho items is slow enough that people give up halfway and leave the
    catalogue half-described, which is worse than not starting.
    """
    if not body.names:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No names supplied")
    results = [describe(n).to_dict() for n in body.names]
    return {
        "count": len(results),
        "confident": sum(1 for r in results if r["confident"]),
        "results": results,
    }
