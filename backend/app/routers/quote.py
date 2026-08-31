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

from .. import approvals, enquiry, resolution
from ..authz import Principal, current_principal
from ..commercial import policy as policy_service, quote_service
from ..domain.enums import QuoteOutcomeStatus
from ..ai import reading
from ..ai.provider import select_provider
from ..config import settings
from ..identity import service as identity_service
from ..db import get_session
from ..trust import audit
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
from ..enquiry import documents
from ..sellable_catalog import sellable_pool_for
from ..store import Line, Quote, store
from ..ingestion.errors import SourceWriteRefused, SourceWriteUnknown
from ..zoho import (
    QuoteWriter,
    ZohoService,
    select_zoho_service,
)

log = logging.getLogger("pie_portal.quote")
router = APIRouter(prefix="/api/v1/quotes", tags=["quotes"])


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

    #: The catalogue half — live list price, stock, item creation. Named
    #: ``zoho`` because every caller of it is a Zoho-era read and renaming them
    #: is churn on a working path; what it *is* is a ``SourceCatalogue``.
    zoho: ZohoService
    contact_id: str = ""
    #: The connector this quote's book belongs to. Recorded on the document the
    #: send produces, so a row says which system holds it rather than assuming
    #: the one connector that could write when the column was added.
    system: str = conn.ZOHO_CONNECTOR
    #: The write half. The same object as ``zoho`` for Zoho, whose adapter is
    #: both; a different one for a connector that can be written to without
    #: being read live. Separate because #8 split the port for exactly this —
    #: a source that can create a quote but has no live item master would
    #: otherwise have to stub reads nobody calls on it.
    writer: Optional[QuoteWriter] = None

    @property
    def quote_writer(self) -> QuoteWriter:
        """Whatever this book writes through. Falls back to the catalogue
        adapter, which for Zoho is the same object and for a refusing adapter
        is the thing that carries the reason."""
        return self.writer if self.writer is not None else self.zoho


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
        # Inside the guard: resolving the adapters is where the credential is
        # actually read, so a rotated-away secret has to arrive as this refusal
        # rather than as a 500 from a dependency.
        return _books_for(session, book)
    except (conn.ConnectionNotFound, conn.CredentialNotUsable) as e:
        return QuoteBooks(zoho=select_zoho_service(reason=str(e)))


def _books_for(session: Session, book: conn.CustomerBook) -> QuoteBooks:
    """The adapters this book reads and writes through.

    One branch, on the one connector that is not in the registry: Zoho's
    connect flow predates it, so its credentials have their own shape and its
    adapter is both halves at once. Everything else is resolved through the
    spec — a connector gaining a writer needs no edit here.

    A registry connector gets a *refusing* catalogue rather than a stub. Its
    item master is synced on a schedule, not read live, so there is no live
    price to answer with — and the honest answer to "what does this cost right
    now" is that we do not know, which is what BOOKS OFFLINE already means.
    """
    connector = conn.connector_of(book.connection)
    if connector == conn.ZOHO_CONNECTOR:
        creds = conn.credentials_for(session, book.connection)
        return QuoteBooks(zoho=select_zoho_service(creds),
                          contact_id=book.contact_id, system=connector)

    from ..ingestion import erp

    material = conn.credential_material(session, book.connection)
    writer = erp.get_spec(connector).build_source(material)
    return QuoteBooks(
        zoho=select_zoho_service(reason=(
            f"This customer's books are {connector}, which this platform syncs "
            f"on a schedule rather than reading live — so there is no live price "
            f"or stock to show here. The quote can still be sent.")),
        contact_id=book.contact_id, system=connector, writer=writer)


def zoho_for_quote(books: QuoteBooks = Depends(books_for_quote)) -> ZohoService:
    """Just the adapter, for the endpoints that only read prices and stock."""
    return books.zoho


def _get_line(quote: Quote, line_id: str) -> Line:
    ln = next((row for row in quote.lines if row.id == line_id), None)
    if ln is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    return ln


@router.post("")
def create_quote(body: CreateQuoteRequest,
                 principal: Principal = Depends(current_principal),
                 session: Session = Depends(get_session)):
    q = store.create(body.customer, body.customer_id, principal.organization_id)
    return _view(session, principal, q)


@router.get("/{quote_id}")
def get_quote(quote_id: str,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)):
    return _view(session, principal,
                 _get_quote(quote_id, principal.organization_id))


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
    provider = select_provider(session, principal.organization_id)
    read = reading.read(body.text, provider)
    lines = store.add_rfq(q, body.text, zoho,
                          _customer_scope(session, principal, q.customer_ref),
                          _bands(session, principal),
                          _mapping_store(session, principal),
                          rows=[ln.to_row() for ln in read.lines] or None,
                          # Once per intake, not once per line. Every line of
                          # this RFQ then resolves against one book, so a sync
                          # landing mid-intake cannot make one quote resolve two
                          # ways — the same reason the source snapshots itself.
                          pool=_sellable_pool(session, principal))
    if read.provider_called:
        # ``provider_called``, not ``used_ai``. The gate used to be success, so
        # the three paths where the enquiry was sent and the answer was
        # unusable — a provider exception, unparseable JSON, a clean parse
        # yielding nothing — recorded that the customer's text reached a model
        # nowhere at all. Those are precisely the calls someone asks about.
        log.info("rfq read by %s into %d line(s) for quote %s (status %s)",
                 settings.AI_PROVIDER, len(lines), quote_id, read.status)
        # The enquiry text a customer sent, handed to a model. Audited here
        # rather than inside ``ai/reading`` because that layer is pure and
        # session-free by design — the arrangement ``CallTelemetry`` exists to
        # serve — so the router is the first place that knows the organization.
        #
        # Weaker than an ``AI_CALL`` from the interpretation path, and the entry
        # says which fields it does not have rather than leaving them absent:
        # ``reading.read`` returns a ``ReadResult`` that carries no prompt hash
        # and no token counts, so this records that a model read the enquiry and
        # what it produced, not what it cost. Widening ``ReadResult`` to carry
        # telemetry is the fix; this is the entry that stops the call being
        # invisible in the meantime.
        audit.append(
            session, organization_id=principal.organization_id,
            action=audit.AI_CALL, actor=principal,
            subject_type="QUOTE", subject_id=quote_id,
            detail={
                "decision_type": "RFQ_READING",
                "ai_status": read.status.upper(),
                # Why it was not "ok", when it was not. Without this a failed
                # reading and a successful one differ only by a word.
                "detail": read.detail or "",
                "provider": getattr(provider, "name", ""),
                "model": getattr(provider, "model", ""),
                "provider_called": True,
                "lines_proposed": len(read.lines),
                "lines_created": len(lines),
                "enquiry_chars": len(body.text),
                # Not available on this path — stated, not omitted, so the entry
                # cannot be misread as a call whose prompt hash was zero.
                "prompt_sha256": None,
                "telemetry_unavailable": "reading.ReadResult carries no CallTelemetry",
            })
    # The customer's own words into the corpus, when the desk said how the
    # enquiry arrived. After the audit entry above and before the response,
    # because a capture that refuses must not change what the quote returns.
    captured = _capture_enquiry(session, principal, body, quote_id)
    return {**_view(session, principal, q),
            # How the lines were produced, so the screen can say "read from
            # your message — check each line" rather than presenting a model's
            # reading as though somebody had typed it.
            "intake": {"read_by": "ai" if read.used_ai else "pattern",
                       "detail": read.detail,
                       # Reported rather than silent. A capture that did not
                       # happen because nobody said how the enquiry arrived is
                       # the ordinary case today, and a screen that cannot see
                       # the difference between "captured" and "channel not
                       # stated" is a screen nobody can use to fix it.
                       "captured": captured}}


def _capture_enquiry(session: Session, principal: Principal,
                     body: IntakeRequest, quote_id: str) -> bool:
    """Put the customer's own words into the corpus, when the channel is stated.

    **Why here.** ``inbound_lines`` was designed in ``a7inbound`` and had never
    held a row, so every text technique in ``14-machine-learning.md`` waited on
    an empty table. This is the one place in the platform where real customer
    text already arrives — a salesperson pastes the enquiry to have it resolved
    — and it was being read into lines and then dropped. Capturing it costs the
    person one dropdown and no new habit.

    **What this subset is, said plainly because the model's docstring makes a
    stronger claim than this path can support.** ``InboundLine`` is described as
    the coverage denominator that *does not condition on success*, and a line
    captured here does: it reached the Quote Builder, so somebody chose to work
    it. The enquiries nobody worked — the ones a coverage report exists to
    count — never come through this door and never will. So this fills the
    **benchmark** corpus (§5.17: real customer text with the reading it
    produced) and it does **not** make coverage answerable. A coverage report
    built by dividing by these rows would report that we answer nearly
    everything, which is `CLAUDE.md` §1's *absence of evidence is not a pass*
    with a percentage on it. ``source_ref`` carries the quote id precisely so
    the worked subset stays identifiable and a later adapter's rows can be told
    apart from it.

    **A refusal here never fails the intake.** The quote is the work; the corpus
    is a by-product. A bad channel string costs a log line, not somebody's RFQ.

    It also never rolls anything back, and that is deliberate rather than
    careless: both of ``capture``'s refusals — an unknown channel, an empty ask
    — raise before it touches the session, so there is nothing written to undo.
    A ``rollback()`` here would discard whatever else this request had done to
    protect a by-product, which is the wrong trade in the wrong direction.
    ``get_session`` commits the successful path with the rest of the request.
    """
    if not body.channel:
        return False
    try:
        enquiry.capture(
            session, principal.organization_id,
            raw_text=body.text,
            channel=body.channel,
            customer_ref=_quote_customer_ref(session, principal, quote_id),
            # Which quote this arrived on, and — when the desk attached one —
            # which document it arrived as. Appended rather than substituted:
            # the quote handle is what marks this row as part of the worked
            # subset, and replacing it would make the subset unidentifiable to
            # buy a link that fits beside it. `source_ref` is free text and is
            # documented as "a message id, a file name, a portal request id",
            # so two space-separated handles is the field used as designed.
            source_ref=_intake_source_ref(session, principal, quote_id, body))
    except enquiry.CaptureRefusal:
        log.warning("enquiry not captured for quote %s: channel %r is not in "
                    "the closed set", quote_id, body.channel)
        return False
    return True


def _intake_source_ref(session: Session, principal: Principal, quote_id: str,
                       body: IntakeRequest) -> str:
    """`quote:<id>`, plus `doc:<id>` when a document was attached and is ours.

    **Ownership is checked before the id is written down.** A caller can put any
    string in `rfq_document_id`, and an unchecked one would file this enquiry
    against another tenant's document — a cross-tenant reference stored
    permanently in a corpus row, which is worse than a failed lookup because
    nothing later would question it. `documents.read` filters on the
    organization, so a foreign id simply finds nothing and the handle is
    omitted.

    Omitted rather than refused: the quote is the work and the corpus is a
    by-product, which is the same trade `_capture_enquiry` makes about a bad
    channel. An intake must not fail because a document reference was wrong.
    """
    ref = f"quote:{quote_id}"
    document_id = (body.rfq_document_id or "").strip()
    if not document_id:
        return ref
    if documents.read(session, principal.organization_id, document_id) is None:
        log.warning("intake for quote %s named document %r, which this "
                    "organization does not have; the enquiry is captured "
                    "without it", quote_id, document_id)
        return ref
    return f"{ref} doc:{document_id}"


def _quote_customer_ref(session: Session, principal: Principal,
                        quote_id: str) -> str:
    """The sender as the desk named them, verbatim and free text.

    Not a ``Customer`` id: enquiries arrive from people who are not customers
    yet, and resolving at capture means either dropping the unresolvable — the
    exact rows this table is for — or writing a guess into a key.
    """
    try:
        return _get_quote(quote_id, principal.organization_id).customer_ref or ""
    except HTTPException:
        return ""


# The four org-scoped facts an engine call needs — the bands, the confirmed
# mappings, the customer's identity scope and this organization's own sellable
# book. The first three are implemented in ``app/resolution`` because the public
# resolution API needs exactly the same setup; these lines are the adapter from
# this router's ``Principal`` to them, kept so the call sites below read as they
# always have.
#
# The fourth is *not* re-exported through ``app/resolution``, and the asymmetry
# is deliberate rather than an oversight. The other three are per-request
# lookups; ``sellable_pool_for`` is a cache over a 154-164 ms build, shared
# across requests and keyed on the book's own version. An alias in
# ``resolution`` would be a second name for one piece of state, which is the
# wrapper CLAUDE.md §2 calls abstraction redundancy — so both routers reach the
# one function directly.
def _mapping_store(session: Session, principal: Principal) -> Optional[Any]:
    return resolution.mapping_store_for(session, principal.organization_id)


def _sellable_pool(session: Session, principal: Principal) -> Optional[Any]:
    return sellable_pool_for(session, principal.organization_id)


def _bands(session: Session, principal: Principal) -> Optional[Bands]:
    return resolution.bands_for(session, principal.organization_id)


def _customer_scope(session: Session, principal: Principal,
                    reference: str) -> Optional[str]:
    return resolution.customer_scope_for(session, principal.organization_id,
                                         reference)


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
    out = _view(session, principal, q)
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

    The decision itself is ``identity_service.confirm_proposed_identity`` and
    is deliberately not restated here: the public API confirms mappings too,
    and a correctness boundary with two implementations is one that eventually
    disagrees with itself. What is left in this function is what is genuinely
    the quote screen's — where the line's scope and proposal are held, what the
    mapping is sourced to, and the rule that a quote must not fail over
    bookkeeping.
    """
    try:
        row = identity_service.confirm_proposed_identity(
            session, principal.organization_id,
            identity_id=ln.customerScope, code=ln.reqCode,
            proposed_record_id=ln.identityCandidate,
            selected_record_id=code,
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
                    principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)):
    """A person has checked this line against the customer's own words.

    Every role, because the salesperson who received the enquiry is the one who
    knows what was meant — and because a confirmation queue that only a manager
    can clear is a quote that waits for a manager.
    """
    q = _get_quote(quote_id, principal.organization_id)
    store.confirm_reading(_get_line(q, line_id))
    return _view(session, principal, q)


@router.post("/{quote_id}/lines/{line_id}/price")
def set_price(quote_id: str, line_id: str, body: SetPriceRequest,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)):
    q = _get_quote(quote_id, principal.organization_id)
    ln = _get_line(q, line_id)
    store.set_price(ln, body.price)
    return _view(session, principal, q)


@router.delete("/{quote_id}/lines/{line_id}")
def delete_line(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal),
                session: Session = Depends(get_session)):
    q = _get_quote(quote_id, principal.organization_id)
    store.delete_line(q, line_id)
    return _view(session, principal, q)


@router.post("/{quote_id}/discount")
def apply_discount(quote_id: str, body: DiscountRequest,
                   principal: Principal = Depends(current_principal),
                   session: Session = Depends(get_session)):
    q = _get_quote(quote_id, principal.organization_id)
    selected = [ln for ln in q.lines if ln.id in set(body.lineIds)]
    n = store.apply_discount(selected, body.percent)
    result = _view(session, principal, q)
    result["applied"] = n
    return result


@router.post("/{quote_id}/lines/{line_id}/create-item")
def create_item(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal),
                zoho: ZohoService = Depends(zoho_for_quote),
                session: Session = Depends(get_session)):
    q = _get_quote(quote_id, principal.organization_id)
    ln = _get_line(q, line_id)
    if not ln.supplyCode:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Line has no supply product to create")
    # A failed write is reported as state on the line (CREATE FAILED) rather
    # than as an error status, because the rest of the quote is untouched and
    # still worth looking at. The reason travels with it so the screen does not
    # have to say "something went wrong".
    failure = store.create_item(ln, zoho)
    result = _view(session, principal, q)
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
    # A resolved line with no rate would reach the source as a line with no
    # rate, and every system this writes to prices that from its own item
    # card — quoting a number nobody here chose.
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
    # Answered from the persisted row rather than from the in-memory quote. The
    # in-memory copy is erased by a restart, and the send it was remembering is
    # not — so after one the check said "never sent", pressed the source again,
    # and relied on the reference round trip to undo what it had just asked for.
    sent = quote_service.latest_document(session, org, quote_id=quote_id)
    if sent is not None and sent.fingerprint == fingerprint:
        # Named from the row rather than from ``books``: this answers about the
        # document that was actually written, which may predate a customer
        # being re-pointed at a different system.
        held = sent.external_system or books.system
        return EstimateResponse(
            ok=True, documentNumber=sent.external_document_number,
            lineCount=sent.line_count, alreadyExisted=True,
            **_system_words(held),
            message=(f"{conn.system_label_for(held)} "
                     f"{conn.quote_term_for(held)} "
                     f"{sent.external_document_number} already covers this quote "
                     "— nothing has changed since it was created."))

    lines = [{"code": ln.supplyCode, "itemId": ln.itemId,
              "qty": ln.reqQty, "rate": ln.quoted}
             for ln in q.lines if ln.supplyCode]

    # ── the write, and the three answers it is allowed to give ───────────────
    # Never a fourth. A refusal names the lines so the screen can point at them;
    # an unresolvable outcome says so and carries the reference to look up. What
    # this must not do is report a created estimate that may not exist, which is
    # exactly what the mock could never get wrong and a real ledger can.
    try:
        est = books.quote_writer.create_sales_quotes(q.customer, lines,
                                                 customer_ref=books.contact_id,
                                                 reference=q.reference)
    except SourceWriteRefused as e:
        refused = {c for c in e.codes if c}
        return EstimateResponse(
            ok=False,
            blockers=[ln.id for ln in q.lines if ln.supplyCode in refused],
            message=str(e))
    except SourceWriteUnknown as e:
        return EstimateResponse(ok=False, message=str(e))

    # Past here the estimate exists — including when Zoho recognised the
    # reference as one it had already landed. Recorded either way, so the local
    # check above can answer the next press without a round trip, and so the
    # quote leaves DRAFT: the DRAFT → SENT → WON/LOST path is modelled, served
    # and typed on the client, and nothing ever moved a quote off DRAFT.
    # The durable half, and the reason this whole endpoint can be pressed twice
    # safely. Written before the outcome transition because it is the record of
    # something that has already happened in somebody's ledger: if the status
    # bookkeeping below fails, the document must still be on file.
    try:
        quote_service.record_document(
            session, org, quote_id=quote_id,
            external_system=books.system, number=est.number,
            document_id=est.document_id, line_count=est.line_count,
            fingerprint=fingerprint, reference=q.reference or "",
            already_existed=est.already_existed,
            thresholds_version=policy_service.load_for_org(session, org).version)
        session.commit()
    except Exception:  # noqa: BLE001 — the estimate exists; bookkeeping must not undo it
        log.exception("could not record the document for quote %s", quote_id)
        session.rollback()
    try:
        # ``quote_document_ref`` is the platform recording, at the one moment
        # it learns it, which ERP document its own quote became — and it is the
        # only durable half of that link. ``QuoteStore`` is an in-process dict
        # whose ids are ``q{run}-N`` and whose reference never reaches the
        # database, so without this line the quote pull and the quote builder
        # would describe the same estimate twice with nothing joining them, and
        # every win rate would double-count it.
        quote_service.set_outcome(
            session, org, quote_id=quote_id, status=QuoteOutcomeStatus.SENT,
            quote_document_ref=est.estimate_id,
            customer_ref=q.customer_ref, user_id=principal.user_id)
    except Exception:  # noqa: BLE001 — the estimate exists; bookkeeping must not undo it
        log.exception("could not mark quote %s as sent", quote_id)

    words = _system_words(books.system)
    named = f"{words['systemLabel']} {words['documentTerm']}"
    if est.already_existed:
        return EstimateResponse(
            ok=True, documentNumber=est.number, lineCount=est.line_count,
            alreadyExisted=True, **words,
            message=(f"This quote was already sent — {named} {est.number} exists "
                     f"under reference {q.reference}. Nothing was created twice."))
    return EstimateResponse(ok=True, documentNumber=est.number,
                            lineCount=est.line_count, **words,
                            message=f"{named} {est.number} created — "
                                    f"{est.line_count} lines.")


def _view(session: Session, principal: Principal, q: Quote) -> dict[str, Any]:
    """One quote as the screen reads it, including what it has already sent.

    The ``estimate`` block is assembled here rather than on ``Quote`` because
    it comes from a persisted row and ``Quote`` is an in-memory object with no
    session. It used to be three attributes on that object, which a restart
    erased — so a quote that had been sent looked unsent, and the primary
    button offered to send it again.

    ``current`` is what makes the block worth having: false once the priced
    content has moved, so the chip can say "amended since" rather than implying
    the customer holds what is on screen.
    """
    out = q.to_dict(principal.is_manager_or_owner)
    sent = quote_service.latest_document(session, principal.organization_id,
                                         quote_id=q.id)
    out["estimate"] = None if sent is None else {
        "number": sent.external_document_number,
        "lineCount": sent.line_count,
        "current": sent.fingerprint == store.priced_fingerprint(q),
        # Named, because "Sent · SQ-1001" does not say where it was sent and
        # two connected systems can both answer to that.
        **_system_words(sent.external_system),
    }
    return out


def _system_words(connector: str) -> dict[str, str]:
    """The three naming fields, from one place.

    Built once rather than at each return: five responses carry them, and five
    hand-assembled copies is how one of them ends up saying "estimate" about a
    Business Central document long after the others stopped.
    """
    return {"system": connector,
            "systemLabel": conn.system_label_for(connector),
            "documentTerm": conn.quote_term_for(connector)}


def _name_lines(lines: list[Line], limit: int = 4) -> str:
    """The codes on these lines, for a sentence that has to fit in an alert."""
    codes = [ln.reqCode for ln in lines[:limit]]
    more = len(lines) - len(codes)
    return ", ".join(codes) + (f" and {more} more" if more > 0 else "")
