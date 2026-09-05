"""The public resolution API — text in, a structured resolution out.

``POST /api/v1/resolve`` is the endpoint that makes the "PIE is infrastructure,
not an application" claim testable: an ERP or a CPQ holding an API key can
resolve a line of a customer's enquiry without a browser, a session or a screen.
``POST /api/v1/resolve/confirm`` records the one durable fact a caller can
teach it — that a customer's own code means a particular product.

**This router is thin on purpose, and the boundary is worth naming.** It maps a
request body to arguments, hands them to ``app.resolution``, and maps the result
to a status code and headers. It decides nothing: not what resolves, not what
abstains, not what a recipient may see, not whether a confirmation is allowed.
Every one of those is a rule that already exists somewhere with tests around it
(``app.resolution``, ``commercial.quote_service.project``,
``identity.service.confirm_proposed_identity``), and a router that re-decided
any of them would be the second implementation that eventually disagrees.

**An API key is a recipient like any other** (CLAUDE.md §1). ``current_caller``
resolves the key to an ordinary ``Principal`` carrying the key's role, and every
projection downstream acts on it exactly as it does on a signed-in person's. A
key minted at ``SALESPERSON`` gets no cost, no margin, and no rule whose
boundary is either — the substitute ``APPROVAL_REQUIRED`` instead, from the same
``_project_exceptions`` the Quote Builder goes through.

**The published spec.** ``GET /api/v1/resolve/openapi.json`` is generated from
these routes rather than hand-written, so it cannot drift from what the server
does, and it is served without authentication because a contract a partner
cannot read before they have a key is not published. The prose that goes with it
is ``docs/resolution-api.md``.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .. import resolution
from ..api_keys import ApiCaller, RATE_WINDOW_SECONDS, current_caller
from ..db import get_session
from ..identity import service as identity_service
from ..pie_service import pie_service
from ..sellable_catalog import sellable_pool_for
from ..store import _identity_candidate

log = logging.getLogger("pie_portal.resolve")
router = APIRouter(prefix="/api/v1/resolve", tags=["resolution-api"])

#: The longest line of enquiry text this will read. A line, not a document:
#: callers with a whole email split it themselves, because a splitter that
#: guessed line boundaries would be making a decision this endpoint has no way
#: to explain afterwards. Generous enough for the longest real description
#: anyone has sent.
MAX_TEXT = 512


def _bounded_text(value: str) -> str:
    """One enquiry line, non-empty and within the cap.

    A module function rather than a validator inherited by both bodies: a
    pydantic ``field_validator`` is a descriptor on the class it decorates and
    calling it from a sibling model works by accident when it works at all.
    One function, called from both, is the version that keeps working.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("text is required")
    if len(value) > MAX_TEXT:
        raise ValueError(
            f"text is limited to {MAX_TEXT} characters — send one enquiry "
            f"line per request rather than a whole document.")
    return value


class ResolveRequest(BaseModel):
    """One line of a customer's own words, and optionally what to price it at."""

    text: str = Field(description="The enquiry text to resolve — one line.")
    customer_ref: str = Field(
        default="",
        description="The customer this line is for, as your system names them. "
                    "Used to read confirmed code mappings and, when a price is "
                    "supplied, to price against that relationship's history. "
                    "Optional; an unknown name resolves the text alone.")
    company_id: Optional[str] = Field(
        default=None,
        description="Which connected company's catalogue answers this line. "
                    "Each company decodes its own item master, so the same "
                    "text can resolve differently for two of them. Optional "
                    "where your organization reads one company's books; "
                    "required where it reads several, and a 422 names the "
                    "valid ids when it is missing.")
    quantity: Optional[Decimal] = Field(
        default=None, description="Quantity, for the quantity band. Ignored "
                                  "without proposed_price.")
    proposed_price: Optional[Decimal] = Field(
        default=None,
        description="Unit price you intend to quote. Supplying it adds the "
                    "`commercial` block; omitting it keeps this a pure "
                    "nomenclature call. What that block contains depends on "
                    "the role your key holds.")

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _bounded_text(value)


class ConfirmRequest(BaseModel):
    """An answer to the question the engine asked about one line."""

    text: str = Field(description="The same text you resolved.")
    customer_ref: str = Field(
        description="The customer whose code this is. Required: a mapping with "
                    "no customer to scope it to would make two companies' "
                    "identical item codes one fact.")
    record_id: str = Field(
        description="The record you are confirming. It must be exactly the "
                    "`identity_proposal.record_id` the resolve call returned.")
    company_id: Optional[str] = Field(
        default=None,
        description="The company whose catalogue that proposal came from. "
                    "Same rule as on /resolve, and it matters more here: this "
                    "call records an asserted identity, so confirming against "
                    "a catalogue you did not choose would file the mapping "
                    "under the wrong company's namespace.")

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return _bounded_text(value)


def _rate_headers(caller: ApiCaller) -> dict[str, str]:
    """What is left of this key's allowance, so a partner can pace itself.

    A caller that has to discover the limit by being refused will discover it in
    production, at the worst moment. ``X-RateLimit-Limit`` of 0 means the key is
    unlimited, matching ``ratelimit.too_many``'s reading of a zero limit.
    """
    return {
        "X-RateLimit-Limit": str(caller.key.rate_limit_per_minute or 0),
        "X-RateLimit-Remaining": str(caller.remaining),
        "X-RateLimit-Window-Seconds": str(int(RATE_WINDOW_SECONDS)),
    }


@router.post("", summary="Resolve one line of enquiry text")
def resolve_line(body: ResolveRequest, response: Response,
                 caller: ApiCaller = Depends(current_caller),
                 session: Session = Depends(get_session)) -> dict:
    """Resolve one line, or abstain and say which kind of abstention it is.

    ``200`` for an answer *and* for an abstention that is evidence about the
    input (``NO_MATCH``, ``AMBIGUOUS``, ``NEEDS_CONFIRMATION``); ``503`` for
    the two that are not (``CATALOGUE_UNAVAILABLE``, ``ENGINE_ERROR``). Never a
    bare ``500`` and never a null resolution with no reason attached —
    ``app.resolution`` says why at length.
    """
    principal = caller.principal
    org = principal.organization_id
    try:
        document = resolution.resolve(
            session, principal,
            text=body.text,
            customer_scope=resolution.customer_scope_for(session, org,
                                                         body.customer_ref),
            bands=resolution.bands_for(session, org),
            mapping_store=resolution.mapping_store_for(session, org),
            pool=sellable_pool_for(session, org),
            customer_ref=body.customer_ref,
            connection_id=body.company_id,
            quantity=body.quantity,
            proposed_price=body.proposed_price,
        )
    except resolution.CompanyNotNamed as e:
        # 422, not a default. Catalogues are per company, so answering from one
        # the caller did not choose would be a confidently provenanced answer
        # about possibly the wrong company's product. The valid ids are in the
        # body so the caller is told what to pick rather than left to guess.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": str(e), "companies": e.companies}) from e
    response.status_code = resolution.http_status(document)
    response.headers.update(_rate_headers(caller))
    return document


@router.post("/confirm", summary="Confirm what a customer's own code means")
def confirm(body: ConfirmRequest, response: Response,
            caller: ApiCaller = Depends(current_caller),
            session: Session = Depends(get_session)) -> dict:
    """Record that this customer's code means this product — if the engine asked.

    **The gate is the same one the Quote Builder goes through**, and it is a
    correctness boundary rather than bookkeeping. A confirmed mapping is
    *asserted* identity: afterwards the engine resolves that code
    AUTHORITATIVELY and its MIXED path will derive a requirement from the record
    and rank equivalents off it. So only the engine's own single-candidate
    ``NEEDS_REVIEW`` proposal may be confirmed — an exact catalogue hit
    downgraded for namespace safety. A scored equivalence suggestion is a
    substitution on one quote, and filing it here would make the approximate
    exact by storage and license ``tolerance ∘ tolerance`` on every later
    resolution of that code.

    The line is re-resolved here rather than trusting a proposal echoed back by
    the caller. That is the whole point: a client that could name its own
    ``identity_proposal`` could name any record, and the gate would be a field
    in a request body.

    ``recorded: false`` with a ``reason`` rather than a 4xx, for every refusal.
    The caller asked a reasonable question and got a truthful answer; a status
    code that distinguished "not the proposal" from "no proposal at all" would
    tell them which half of a guess was right.
    """
    principal = caller.principal
    org = principal.organization_id
    response.headers.update(_rate_headers(caller))

    scope = resolution.customer_scope_for(session, org, body.customer_ref)
    try:
        company = resolution.company_for(session, org, body.company_id)
    except resolution.CompanyNotNamed as e:
        # The same refusal as `/resolve`, and for a stronger reason: this call
        # *writes* an asserted identity. Confirming a code against a catalogue
        # the caller did not choose would file "their code means this product"
        # under the wrong company's namespace.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": str(e), "companies": e.companies}) from e
    # Both halves of "what could the engine see" have to match the `/resolve`
    # call the caller is answering, and for the same reason: this recomputes
    # the engine's own proposal to check the selection against it, so resolving
    # against a different catalogue OR a different pool would be checking the
    # answer to a different question. `company` is the catalogue the caller
    # named; `sellable_pool_for` is cached on the organization and its book
    # version, so the two calls get the same pool unless the book genuinely
    # moved between them, which is the one case where they SHOULD differ and
    # the confirmation should fail.
    res = pie_service.resolve(
        body.text, scope, resolution.bands_for(session, org),
        resolution.mapping_store_for(session, org),
        connection_id=company, pool=sellable_pool_for(session, org))

    row = identity_service.confirm_proposed_identity(
        session, org,
        identity_id=scope, code=res.reqCode or body.text,
        proposed_record_id=_identity_candidate(res),
        selected_record_id=body.record_id,
        source_ref=f"api key {caller.key.key_id}",
        user_id=principal.user_id)
    if row is None:
        # No rollback: the gate wrote nothing, and rolling back here would also
        # discard the `last_used_at` touch made when the key authenticated —
        # so a key spending its allowance on refused confirmations would look
        # unused. `get_session` owns the transaction either way.
        response.status_code = status.HTTP_200_OK
        return {
            "api_version": resolution.API_VERSION,
            "recorded": False,
            "reason": (
                "Nothing was recorded. A mapping is only ever created when the "
                "engine itself proposed this record as the line's identity and "
                "you confirmed that same record, for a customer this book has "
                "linked across its connected systems. Resolve the line and "
                "check `identity_proposal`."),
        }
    session.commit()
    return {
        "api_version": resolution.API_VERSION,
        "recorded": True,
        "code": row.code,
        "record_id": row.target_record_id,
        "relationship": row.relationship,
        "detail": "This customer's code will resolve to that record from now on.",
    }


#: Built once. The generator walks the app's route table, which does not change
#: after startup, and a spec rebuilt per request would be the same bytes at a
#: cost paid by whoever fetched it.
_spec: Optional[dict[str, Any]] = None


@router.get("/openapi.json", summary="The published contract for these routes")
def openapi_spec() -> dict:
    """The OpenAPI document for this router, generated from the routes.

    Unauthenticated, deliberately: a partner evaluating whether to integrate
    reads this before anybody mints them a key, and a contract you need a
    credential to read is not published. It describes shapes and nothing else —
    no data, no tenant, no key.

    Generated rather than written. The alternative is a checked-in YAML that
    agrees with the handlers on the day it is written, and this repository's
    §6 has the story of what two lists of the same thing do to each other.
    """
    global _spec
    if _spec is None:
        _spec = get_openapi(
            title="PIE Resolution API",
            version=resolution.API_VERSION,
            summary="Resolve a line of cutting-tool enquiry text to a "
                    "manufacturer product, with provenance.",
            description=(
                "Deterministic product-nomenclature resolution. No model is "
                "called on this path and no number in the response is "
                "produced by one.\n\n"
                "Authenticate with `Authorization: Bearer pie_…` (or "
                "`X-API-Key`). Responses are scoped to the organization the "
                "key belongs to and projected to the role it holds.\n\n"
                "An abstention is an answer: check `abstention.reason` and, "
                "in particular, `abstention.is_evidence_about_the_input` "
                "before recording anything as unknown."),
            routes=[r for r in router.routes if isinstance(r, APIRoute)],
        )
    return _spec
