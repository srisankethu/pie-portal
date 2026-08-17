"""Quote-building endpoints: intake, resolution grid, supply selection,
pricing, item creation, and estimate creation.

Every response is serialized through ``Quote.to_dict(mgmt=…)``, so a sales
principal never receives per-line economics.

**One identity.** These endpoints authenticate the platform user in
``Authorization``, the same token every ``/api/v1`` endpoint takes. They used to
take a Quote Builder login of their own — two fixed demo accounts, any password
— and read the *real* identity out of a second ``X-Platform-Authorization``
header when the browser happened to send one. That is why opening the Quote
Builder asked you to sign in again and then showed somebody else's name: the two
logins were unrelated, and the one on screen was the demo one. It also meant the
org-scoped half of resolution (confirmed mappings, equivalence bands, the
approval gate) silently degraded to packaged defaults whenever the second header
was missing, which looks exactly like the feature not working.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import approvals
from ..authz import Principal, current_principal
from ..commercial import policy as policy_service, quote_service
from ..domain.enums import QuoteOutcomeStatus
from ..ai import reading
from ..ai.provider import select_provider
from ..config import settings
from ..identity import service as identity_service
from ..identity.mapping_store import OrgMappingStore
from ..db import get_session
from ..ingestion import connections as conn
from ..schemas import (
    CreateQuoteRequest,
    DiscountRequest,
    EstimateResponse,
    IntakeRequest,
    SelectSupplyRequest,
    SetPriceRequest,
)
from ..pie_service import Bands
from ..store import Line, Quote, store
from ..zoho import (
    ZohoService,
    ZohoWriteRefused,
    ZohoWriteUnknown,
    select_zoho_service,
)

log = logging.getLogger("pie_portal.quote")
router = APIRouter(prefix="/api/quotes", tags=["quotes"])


def _get_quote(quote_id: str, org: str) -> Quote:
    """The quote, only if it belongs to this tenant.

    The store is one process-wide dict with enumerable ids and no tenant
    column of its own, so the org check is the whole of quote authorization:
    without it a signed-in user from any tenant could read or mutate another
    tenant's quote by guessing its id. A foreign (or absent) id is a 404 —
    the two are deliberately indistinguishable, so the endpoint never confirms
    that some other org's quote exists.
    """
    q = store.get(quote_id)
    if q is None or q.organizationId != org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    return q


@dataclass(frozen=True)
class QuoteBooks:
    """The Zoho adapter this quote may use, and the account it writes to.

    Resolved together, once, because they are one fact: which company's books
    this quote belongs to. Reading a list price out of SLS and writing the
    estimate into 4U looks identical on screen and is wrong in a way nobody can
    reproduce later — so both come from the same connection, or neither does.
    ``contact_id`` is empty exactly when ``zoho`` is the refusing adapter.
    """

    zoho: ZohoService
    contact_id: str = ""


def books_for_quote(quote_id: str,
                    principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)) -> QuoteBooks:
    """Bind this quote to one connected company, or to an adapter that says it cannot.

    In mock mode nothing is resolved and nothing is queried: the demo, the test
    suite and a fresh clone keep the deterministic adapter they have always had.

    In live mode the quote's customer is resolved through the same tolerant
    matcher the analysis uses — so a customer resolvable on one screen is
    resolvable on the other — and then to the company that customer was
    imported from. Anything unresolvable yields ``UnavailableZoho`` carrying
    why, which reads as BOOKS OFFLINE on a line and as a refusal on a write. It
    never falls back to a book.
    """
    if settings.ZOHO_QUOTE_SERVICE != "live":
        return QuoteBooks(zoho=select_zoho_service())

    org = principal.organization_id
    quote = _get_quote(quote_id, org)
    customer = quote_service.resolve_customer(session, org, quote.customer_ref)
    if customer is None:
        return QuoteBooks(zoho=select_zoho_service(reason=(
            f"{quote.customer!r} does not match any customer in this "
            f"organization, so no set of books can be identified.")))
    try:
        book = conn.book_for_customer(session, org, customer)
        creds = conn.credentials_for(session, book.connection)
    except (conn.ConnectionNotFound, conn.CredentialNotUsable) as e:
        return QuoteBooks(zoho=select_zoho_service(reason=str(e)))
    return QuoteBooks(zoho=select_zoho_service(creds), contact_id=book.contact_id)


def zoho_for_quote(books: QuoteBooks = Depends(books_for_quote)) -> ZohoService:
    """Just the adapter, for the endpoints that only read prices and stock."""
    return books.zoho


def _get_line(quote: Quote, line_id: str) -> Line:
    ln = next((row for row in quote.lines if row.id == line_id), None)
    if ln is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    return ln


@router.post("")
def create_quote(body: CreateQuoteRequest, principal: Principal = Depends(current_principal)):
    q = store.create(body.customer, body.customer_id, principal.organization_id)
    return q.to_dict(principal.is_manager_or_owner)


@router.get("/{quote_id}")
def get_quote(quote_id: str, principal: Principal = Depends(current_principal)):
    return _get_quote(quote_id, principal.organization_id).to_dict(
        principal.is_manager_or_owner)


@router.post("/{quote_id}/intake")
def intake(quote_id: str, body: IntakeRequest,
           principal: Principal = Depends(current_principal),
           zoho: ZohoService = Depends(zoho_for_quote),
           session: Session = Depends(get_session)):
    q = _get_quote(quote_id, principal.organization_id)
    if not body.text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No RFQ text provided")
    # Read the enquiry into rows first. This is the one place in the quote
    # path where a model touches the input, and what it produces is a *code and
    # a quantity* — pie-parser still decides what the code means, against the
    # same catalogue and the same score bands as typed input. A reading that
    # fails for any reason falls through to the regex, so the worst case is the
    # product exactly as it was before.
    read = reading.read(body.text, select_provider(session, principal.organization_id))
    lines = store.add_rfq(q, body.text, zoho,
                          _customer_scope(session, principal, q.customer_ref),
                          _bands(session, principal),
                          _mapping_store(session, principal),
                          rows=[ln.to_row() for ln in read.lines] or None)
    if read.used_ai:
        log.info("rfq read by %s into %d line(s) for quote %s",
                 settings.AI_PROVIDER, len(lines), quote_id)
    return {**q.to_dict(principal.is_manager_or_owner),
            # How the lines were produced, so the screen can say "read from
            # your message — check each line" rather than presenting a model's
            # reading as though somebody had typed it.
            "intake": {"read_by": "ai" if read.used_ai else "pattern",
                       "detail": read.detail}}


def _mapping_store(session: Session, principal: Principal) -> Optional[Any]:
    """This organization's confirmed code mappings, for the engine to read.

    None only when they could not be loaded: pie-parser then falls back to its
    packaged store, which is empty, so resolution degrades to the codes alone
    rather than failing the intake.
    """
    try:
        return OrgMappingStore(session, principal.organization_id)
    except Exception:  # noqa: BLE001 — resolution proceeds without them
        log.exception("could not load confirmed mappings for %s",
                      principal.organization_id)
        return None


def _bands(session: Session, principal: Principal) -> Optional[Bands]:
    """This organization's equivalence bands, or None for the packaged defaults.

    What counts as a technical equivalent is commercial policy, so it belongs to
    the org and moves with its threshold version — the same reason the pricing
    floors stopped being module constants.
    """
    try:
        t = policy_service.load_for_org(session, principal.organization_id)
        return Bands(tech=t.equivalence_tech_band, compat=t.equivalence_compat_band)
    except Exception:  # noqa: BLE001 — policy is never a reason to fail intake
        log.exception("could not load equivalence bands for %s",
                      principal.organization_id)
        return None


def _customer_scope(session: Session, principal: Principal,
                    reference: str) -> Optional[str]:
    """The identity to resolve this quote's lines under, or None.

    Two ways to get None, and both mean "resolve on the codes alone": no
    customer matched, or a customer who has not been linked across connectors
    yet. That last one is the normal early state — linking is manual by design —
    so the fallback has to be the unscoped behaviour rather than a stand-in key.
    Substituting the connector's own id would mean every mapping confirmed today
    is filed under a name we intend to replace the moment somebody links the
    record.
    """
    try:
        customer = quote_service.resolve_customer(
            session, principal.organization_id, reference)
        return identity_service.identity_for_customer(
            session, principal.organization_id, customer)
    except Exception:  # noqa: BLE001 — scope is an optimisation, never a blocker
        log.exception("could not resolve an identity scope for %r", reference)
        return None


@router.get("/{quote_id}/lines/{line_id}/options")
def line_options(quote_id: str, line_id: str,
                 principal: Principal = Depends(current_principal)):
    """Ranked supply candidates for a line (the design's supply drawer)."""
    q = _get_quote(quote_id, principal.organization_id)
    ln = _get_line(q, line_id)
    return {
        "lineId": ln.id,
        "reqCode": ln.reqCode,
        "reqDesc": ln.reqDesc,
        "supplyCode": ln.supplyCode,
        "candidates": [c.to_dict() for c in ln.candidates],
        "notes": ln.notes,
    }


@router.post("/{quote_id}/lines/{line_id}/supply")
def select_supply(quote_id: str, line_id: str, body: SelectSupplyRequest,
                  principal: Principal = Depends(current_principal),
                  zoho: ZohoService = Depends(zoho_for_quote),
                  session: Session = Depends(get_session)):
    q = _get_quote(quote_id, principal.organization_id)
    ln = _get_line(q, line_id)
    confirmed = _confirm_identity(session, principal, q, ln, body.code)
    store.select_supply(ln, body.code, zoho, manual=body.manual)
    out = q.to_dict(principal.is_manager_or_owner)
    if confirmed:
        # Worth saying out loud: the person has just taught the system something
        # permanent, and a change with no feedback reads as a change that did
        # not happen.
        out["note"] = (f"Recorded: this customer's {ln.reqCode} means {body.code}. "
                       "It will resolve on its own from now on.")
    return out


def _confirm_identity(session: Session, principal: Principal,
                      quote: Quote, ln: Line, code: str) -> bool:
    """Record a confirmation when the user answers the engine's own question.

    Only when they select the record the engine *proposed* as this line's
    identity. Picking a different product is a substitution on one quote, and
    filing that as "their code means this" would teach the system something the
    person did not say — and would then resolve it that way silently forever.
    """
    if not ln.customerScope or ln.identityCandidate != code:
        return False
    try:
        row = identity_service.confirm_code_mapping(
            session, principal.organization_id,
            identity_id=ln.customerScope, code=ln.reqCode,
            target_record_id=code,
            source_ref=f"quote {quote.id} line {ln.id}",
            user_id=principal.user_id)
        if row is not None:
            session.commit()
        return row is not None
    except Exception:  # noqa: BLE001 — a quote must not fail over bookkeeping
        log.exception("could not record a confirmed mapping for line %s", ln.id)
        session.rollback()
        return False


@router.post("/{quote_id}/lines/{line_id}/confirm-reading")
def confirm_reading(quote_id: str, line_id: str,
                    principal: Principal = Depends(current_principal)):
    """A person has checked this line against the customer's own words.

    Every role, because the salesperson who received the enquiry is the one who
    knows what was meant — and because a confirmation queue that only a manager
    can clear is a quote that waits for a manager.
    """
    q = _get_quote(quote_id, principal.organization_id)
    store.confirm_reading(_get_line(q, line_id))
    return q.to_dict(principal.is_manager_or_owner)


@router.post("/{quote_id}/lines/{line_id}/price")
def set_price(quote_id: str, line_id: str, body: SetPriceRequest,
              principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id, principal.organization_id)
    ln = _get_line(q, line_id)
    store.set_price(ln, body.price)
    return q.to_dict(principal.is_manager_or_owner)


@router.delete("/{quote_id}/lines/{line_id}")
def delete_line(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id, principal.organization_id)
    store.delete_line(q, line_id)
    return q.to_dict(principal.is_manager_or_owner)


@router.post("/{quote_id}/discount")
def apply_discount(quote_id: str, body: DiscountRequest,
                   principal: Principal = Depends(current_principal)):
    q = _get_quote(quote_id, principal.organization_id)
    selected = [ln for ln in q.lines if ln.id in set(body.lineIds)]
    n = store.apply_discount(selected, body.percent)
    result = q.to_dict(principal.is_manager_or_owner)
    result["applied"] = n
    return result


@router.post("/{quote_id}/lines/{line_id}/create-item")
def create_item(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal),
                zoho: ZohoService = Depends(zoho_for_quote)):
    q = _get_quote(quote_id, principal.organization_id)
    ln = _get_line(q, line_id)
    if not ln.supplyCode:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Line has no supply product to create")
    # A failed write is reported as state on the line (CREATE FAILED) rather
    # than as an error status, because the rest of the quote is untouched and
    # still worth looking at. The reason travels with it so the screen does not
    # have to say "something went wrong".
    failure = store.create_item(ln, zoho)
    result = q.to_dict(principal.is_manager_or_owner)
    if failure:
        result["createItemError"] = failure
    return result


@router.post("/{quote_id}/estimate", response_model=EstimateResponse)
def create_estimate(quote_id: str,
                    principal: Principal = Depends(current_principal),
                    books: QuoteBooks = Depends(books_for_quote),
                    session: Session = Depends(get_session)):
    q = _get_quote(quote_id, principal.organization_id)
    blockers = store.blockers(q)
    if blockers:
        return EstimateResponse(
            ok=False,
            blockers=[ln.id for ln in blockers],
            # Which lines, not merely how many. The client shows this sentence
            # and switches the grid to them, and "3 critical line(s) must be
            # resolved first" left the reader to work out which three.
            message=(f"{len(blockers)} line(s) must be resolved before this quote "
                     f"can be sent: {_name_lines(blockers)}."),
        )
    # A resolved line with no rate would go to Zoho as a line with no rate.
    unpriced = [ln for ln in q.lines if ln.supplyCode and ln.quoted is None]
    if unpriced:
        return EstimateResponse(
            ok=False,
            blockers=[ln.id for ln in unpriced],
            message=(f"{len(unpriced)} line(s) have no rate yet: "
                     f"{_name_lines(unpriced)}."),
        )

    org = principal.organization_id

    # ── the commercial gate ──────────────────────────────────────────────────
    # Record the quote's own assessment *first*, then judge it. The gate reads
    # the latest snapshot per line, and until this call the only thing writing
    # snapshots was a salesperson choosing to open a drawer and record an
    # override — so the ordinary path wrote none, the gate found nothing to
    # judge, and `can_submit` was true no matter what the margins were. A line
    # priced at 0% against a 15% floor was reported sendable and sent. The
    # control the paragraph below describes existed; nothing ever reached it.
    #
    # It also means the audit trail records every quote that was *sent*, not
    # only the ones somebody happened to annotate.
    #
    # The gate needs an organization. It reads it from the signed-in principal
    # rather than from a second optional header — an identity the caller could
    # omit was an approval gate the caller could skip.
    quote_service.assess_and_record(
        session, org, quote_id=quote_id, customer_ref=q.customer_ref,
        lines=[quote_service.QuoteLineInput(
            line_id=ln.id,
            # The code, matching what the screen's own assessment sends. The
            # description is prose and, on an unresolved line, a status message.
            product_ref=ln.supplyCode or ln.reqCode,
            qty=Decimal(str(ln.reqQty)),
            proposed_price=Decimal(str(ln.quoted)) if ln.quoted is not None else None,
            family=ln.family)
            for ln in q.lines],
        user_id=principal.user_id)
    session.flush()

    approval_policy = approvals.get_policy(session, org)
    if approval_policy.require_approval_for_quotes:
        blocked = approvals.quote_submission_block(
            session, org, quote_id,
            # The lines this screen is already showing a below-floor warning
            # about. Its margin uses the item's cost from the books, which is
            # present for items the assessment has no synced bill rows for — so
            # a line the person can see flagged in an alert was sendable.
            also_requiring={ln.id: ln.reqCode
                            for ln in q.lines if ln.economics().below_floor})
        if blocked:
            raise HTTPException(status.HTTP_403_FORBIDDEN, blocked)
    # ── sending twice ────────────────────────────────────────────────────────
    # Three presses used to create three estimates in Zoho, because nothing on
    # the quote remembered that it had been sent and the button never changed.
    # Re-sending an *amended* quote is ordinary work, so this is not a lock: the
    # same content returns the estimate it already produced, and changed content
    # produces a new one.
    #
    # This is the local half. The write below also carries ``q.reference``, so
    # Zoho can recognise a repeat whose reply we never heard. Both are needed:
    # this one saves the round trip, that one survives a lost answer.
    fingerprint = store.priced_fingerprint(q)
    if q.estimateNumber and q.estimateFingerprint == fingerprint:
        return EstimateResponse(
            ok=True, estimateNumber=q.estimateNumber, lineCount=q.estimateLineCount,
            message=(f"Zoho estimate {q.estimateNumber} already covers this quote — "
                     "nothing has changed since it was created."))

    lines = [{"code": ln.supplyCode, "itemId": ln.itemId,
              "qty": ln.reqQty, "rate": ln.quoted}
             for ln in q.lines if ln.supplyCode]

    # ── the write, and the three answers it is allowed to give ───────────────
    # Never a fourth. A refusal names the lines so the screen can point at them;
    # an unresolvable outcome says so and carries the reference to look up. What
    # this must not do is report a created estimate that may not exist, which is
    # exactly what the mock could never get wrong and a real ledger can.
    try:
        est = books.zoho.create_estimate(q.customer, lines,
                                         customer_ref=books.contact_id,
                                         reference=q.reference)
    except ZohoWriteRefused as e:
        refused = {c for c in e.codes if c}
        return EstimateResponse(
            ok=False,
            blockers=[ln.id for ln in q.lines if ln.supplyCode in refused],
            message=str(e))
    except ZohoWriteUnknown as e:
        return EstimateResponse(ok=False, message=str(e))

    # Past here the estimate exists — including when Zoho recognised the
    # reference as one it had already landed. Recorded either way, so the local
    # check above can answer the next press without a round trip, and so the
    # quote leaves DRAFT: the DRAFT → SENT → WON/LOST path is modelled, served
    # and typed on the client, and nothing ever moved a quote off DRAFT.
    store.record_estimate(q, number=est.number, line_count=est.line_count,
                          fingerprint=fingerprint)
    try:
        quote_service.set_outcome(
            session, org, quote_id=quote_id, status=QuoteOutcomeStatus.SENT,
            customer_ref=q.customer_ref, user_id=principal.user_id)
    except Exception:  # noqa: BLE001 — the estimate exists; bookkeeping must not undo it
        log.exception("could not mark quote %s as sent", quote_id)

    if est.already_existed:
        return EstimateResponse(
            ok=True, estimateNumber=est.number, lineCount=est.line_count,
            message=(f"This quote was already sent — Zoho estimate {est.number} "
                     f"exists under reference {q.reference}. Nothing was created twice."))
    return EstimateResponse(ok=True, estimateNumber=est.number,
                            lineCount=est.line_count,
                            message=f"Zoho estimate {est.number} created — {est.line_count} lines.")


def _name_lines(lines: list[Line], limit: int = 4) -> str:
    """The codes on these lines, for a sentence that has to fit in an alert."""
    codes = [ln.reqCode for ln in lines[:limit]]
    more = len(lines) - len(codes)
    return ", ".join(codes) + (f" and {more} more" if more > 0 else "")
