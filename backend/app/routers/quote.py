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

from .. import approvals, clock, enquiry, quote_fields, quote_workspace, resolution
from ..authz import Principal, current_principal
from ..commercial import policy as policy_service, quote_service
from ..domain import models
from ..domain.enums import QuoteDocumentChannel, QuoteDocumentWriteState, QuoteOutcomeStatus
from ..domain.origin import Companies
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
    SetCustomCostRequest,
    SetCustomerRequest,
    SetFieldsRequest,
    SetOwnerRequest,
    SetPriceRequest,
)
from ..pie_service import Bands, pie_service
from ..repositories import ReadModelRepository
from ..enquiry import documents
from ..sellable_catalog import sellable_pool_for
from ..store import Line, Quote, store
from ..ingestion.errors import (IngestionError, SourceUnavailable,
                                SourceWriteRefused, SourceWriteUnknown)
from ..zoho import (
    QuoteWriter,
    ZohoService,
    select_zoho_service,
)

log = logging.getLogger("pie_portal.quote")
router = APIRouter(prefix="/api/v1/quotes", tags=["quotes"])


def _get_quote(session: Session, quote_id: str, org: str,
               user_id: Optional[str] = None) -> Quote:
    """The quote — or the form somebody has open — if it belongs to this tenant.

    Read for this request — it is a row, not a process-wide object — and the
    org check is the whole of quote authorization: without it a signed-in user
    from any tenant could read or mutate another tenant's quote by naming its
    id. A foreign (or absent) id is a 404 — the two are deliberately
    indistinguishable, so the endpoint never confirms that some other org's
    quote exists.

    ``user_id`` scopes the *unsaved* half and only that half: a quote is the
    desk's and a colleague may pick it up, while a form is one person still
    typing into it. Callers that pass none get the tenant check alone, which is
    the rule a saved quote has always had.
    """
    q = quote_workspace.load(session, org, quote_id, user_id=user_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    return q


def _needs_saving(q: Quote, action: str) -> None:
    """Refuse an action that only makes sense once a quote exists.

    Three of them: sending the quote into a ledger, handing it to a colleague,
    and asking for an approval. Each writes a row that keys on a quote id and
    outlives the request — a document, an assignment, an approval trail — and a
    form's id is not one: it is discarded the moment Save mints the quote's own.
    So the refusal names the button rather than letting the row point at
    something that will not be there tomorrow.
    """
    if not q.saved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This quote has not been saved yet, so it cannot {action}. "
            f"Press Save quote first.")


def _get_editable(session: Session, principal: Principal, quote_id: str) -> Quote:
    """The quote, for a change — refused unless this person may change it.

    Every quote has an owner (whoever started it) and only the owner changes
    it, plus managers and owners where the organization's policy allows
    (``quote_workspace.may_edit``). The refusal names the owner, because
    "403" on a quote a colleague can plainly see is a locked door with no
    sign on it. A 404 stays a 404: this runs *after* the tenant check, so an
    outsider still learns nothing.
    """
    q = _get_quote(session, quote_id, principal.organization_id,
                   user_id=principal.user_id)
    policy = approvals.get_policy(session, principal.organization_id)
    if not quote_workspace.may_edit(q, user_id=principal.user_id,
                                    role=principal.role, policy=policy):
        owner = _names(session, principal, [q.ownerId]).get(q.ownerId or "", "its owner")
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{q.number or 'This quote'} belongs to {owner}. Only they"
            + (" or a manager" if policy.managers_may_edit_any_quote else "")
            + " can change it — ask them, or have it handed to you.")
    return q


def _names(session: Session, principal: Principal,
           ids: list[Optional[str]]) -> dict[str, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    from ..memberships import users_in
    return {u.user_id: u.name or "" for u in users_in(session, principal.organization_id)
            if u.user_id in wanted}


def _saved(session: Session, principal: Principal, q: Quote) -> dict[str, Any]:
    """Write the quote back to its row, then answer with it.

    The one line every mutation ends on. The row is what the next request —
    on this desk or a colleague's — reads, so a mutation that answered
    without writing would be a change only the person who made it ever saw.
    """
    quote_workspace.save(session, q, principal.user_id)
    return _view(session, principal, q)


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
    #: The connected company whose book this is — the other half of the
    #: document's identity, recorded beside the connector so the document the
    #: send writes can be joined to the same document once the sync reads it
    #: back, and compared with the company the quote's lines were priced from.
    #: ``None`` in mock mode, where no book is resolved at all.
    connection_id: Optional[str] = None
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
    org = principal.organization_id
    if settings.ZOHO_QUOTE_SERVICE != "live":
        # The stand-in adapter, but the quote's own system's name on what it
        # writes: a mock-mode send on a Business Central organization used to
        # be recorded and announced as a Zoho estimate.
        quote = _get_quote(session, quote_id, org, user_id=principal.user_id)
        return QuoteBooks(zoho=select_zoho_service(),
                          system=_quote_connector(session, org, quote))

    # `user_id` for the same reason the reads above pass it: where this id is an
    # unsaved form, only its own author has one.
    quote = _get_quote(session, quote_id, org, user_id=principal.user_id)
    if not quote.has_customer:
        # Not an error — a quote starts this way. But there is no set of books
        # to read a price from until somebody says whose quote it is, and the
        # reason should say that rather than "'' matches no customer".
        return QuoteBooks(zoho=select_zoho_service(reason=(
            "This quote has no customer yet, so no set of books can be "
            "identified. Choose a customer to read live prices and stock.")))
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
                          contact_id=book.contact_id, system=connector,
                          connection_id=book.connection.connection_id)

    from ..ingestion import erp

    material = conn.credential_material(session, book.connection)
    writer = erp.get_spec(connector).build_source(material)
    return QuoteBooks(
        zoho=select_zoho_service(reason=(
            f"This customer's books are {connector}, which this platform syncs "
            f"on a schedule rather than reading live — so there is no live price "
            f"or stock to show here. The quote can still be sent.")),
        contact_id=book.contact_id, system=connector, writer=writer,
        connection_id=book.connection.connection_id)


def zoho_for_quote(books: QuoteBooks = Depends(books_for_quote)) -> ZohoService:
    """Just the adapter, for the endpoints that only read prices and stock."""
    return books.zoho


def _get_line(quote: Quote, line_id: str) -> Line:
    ln = next((row for row in quote.lines if row.id == line_id), None)
    if ln is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    return ln


@router.get("")
def list_quotes(principal: Principal = Depends(current_principal),
                session: Session = Depends(get_session)):
    """The workspace: every draft in the organization and what each is waiting on.

    Shared across the organization by design — see ``quote_workspace``. The
    list carries each quote's own selling total and the send gate's answer,
    and nothing derived from cost.
    """
    return {"quotes": quote_workspace.list_drafts(
        session, principal.organization_id,
        user_id=principal.user_id, role=principal.role)}


@router.get("/field-definitions")
def field_definitions(principal: Principal = Depends(current_principal),
                      session: Session = Depends(get_session)):
    """The quote-level fields this organization asks for, for the builder to
    render. Every role: a salesperson fills them in. Edited under
    ``/admin/quote-fields``."""
    return {"fields": [quote_fields.to_dict(d)
                       for d in quote_fields.definitions_for(
                           session, principal.organization_id)]}


@router.get("/assignees")
def quote_assignees(principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)):
    """Who a quote can be handed to. Names and ids only — the members list
    proper is a manager's screen, and a salesperson handing over their own
    quote needs exactly this much of it."""
    return {"members": quote_workspace.assignees(session, principal.organization_id)}


@router.post("")
def create_quote(body: CreateQuoteRequest,
                 principal: Principal = Depends(current_principal),
                 session: Session = Depends(get_session)):
    """Start a quote against one company's catalogue.

    The company is decided here, once, rather than per line: a quote's lines
    are compared against each other on screen, and two of them decoded by
    different companies' packs would look comparable and not be. Which company
    answers is ``resolution.company_for``'s decision, not this router's — the
    same function the resolution API refuses through, so both surfaces agree on
    what an unnamed company means when the org reads several books.

    The customer is optional, and empty is the default: the enquiry is what
    arrived, and who it is from is a question the desk answers when it has
    the answer — ``PUT /{quote_id}/customer`` below.
    """
    company = _company_for_new_quote(session, principal.organization_id, body)
    q = quote_workspace.create(session, principal.organization_id,
                               user_id=principal.user_id,
                               customer=body.customer, customer_id=body.customer_id,
                               connection_id=company)
    return _view(session, principal, q)


def _company_for_new_quote(session: Session, org: str,
                           body: CreateQuoteRequest) -> Optional[str]:
    """Which company a new quote is raised from, and that its customer belongs
    there.

    The customer's own company answers when none was named: a quote started
    from an account page in a three-company organization used to be asked
    "which book?" about a customer whose row already says. Named or inferred,
    the answer goes through ``resolution.company_for`` — the one place that
    decides what an unnamed company means — and then through
    ``require_same_company``, so a customer from another book is refused by
    name rather than priced from the wrong catalogue.
    """
    try:
        company = resolution.company_for(
            session, org,
            body.connection_id
            or quote_workspace.company_of_customer(session, org, body.customer_id))
        quote_workspace.require_same_company(
            session, org, connection_id=company, customer_id=body.customer_id)
    except resolution.CompanyNotNamed as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"message": str(e), "companies": e.companies})
    except quote_workspace.CompanyMismatch as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    return company


# ── the unsaved form ─────────────────────────────────────────────────────────
# Pressing "New quote" used to run ``POST ""`` above: a row in the shared
# workspace, with QB-0042 minted against it, before anybody had typed anything.
# Open the builder and close it again and that number was spent and that empty
# quote was on every desk's list for good. These three are the lifecycle it
# should have had — open a form, save it, or throw it away — and a quote is
# made at exactly one of them.
#
# Declared above ``/{quote_id}`` so ``form`` is read as the literal it is.
@router.post("/form")
def create_quote_form(body: CreateQuoteRequest,
                      principal: Principal = Depends(current_principal),
                      session: Session = Depends(get_session)):
    """Open a blank quote form. **No quote is created and no number is minted.**

    The company is still decided here, once, for the reason ``create_quote``
    gives: a quote's lines are compared against each other on screen, and two
    of them decoded by different companies' packs would look comparable and not
    be. It is the one decision that cannot wait for Save, because the first
    pasted RFQ line already needs a catalogue to resolve against.

    Answers the same shape ``POST ""`` does, so the builder opens on it
    unchanged — with ``saved: false`` and an empty ``number``.
    """
    company = _company_for_new_quote(session, principal.organization_id, body)
    q = quote_workspace.create_form(session, principal.organization_id,
                                    user_id=principal.user_id,
                                    customer=body.customer,
                                    customer_id=body.customer_id,
                                    connection_id=company)
    return _view(session, principal, q)


@router.post("/form/{form_id}/save")
def save_quote_form(form_id: str,
                    principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)):
    """Save the form: mint the number, write the quote, drop the form.

    **The only place the builder creates a quote.** Validation stays where it
    already is rather than being restated here — ``blockers``, ``missingFields``
    and the approval gate are what the *send* enforces, and a quote that is
    saved but not yet ready is the ordinary state of a quote somebody is still
    working on. Refusing to save one would mean the desk could not put work
    down, which is what the old always-a-row behaviour got right.

    Idempotent: a second click returns the quote the first one made rather than
    minting a second number. ``quote_workspace.save_form`` holds that under a
    unique constraint, so it survives a double-submit and two racing requests,
    not only a disabled button.
    """
    try:
        q = quote_workspace.save_form(session, principal.organization_id, form_id,
                                      user_id=principal.user_id)
    except quote_workspace.FormNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote form not found")
    return _view(session, principal, q)


@router.delete("/form/{form_id}")
def discard_quote_form(form_id: str,
                       principal: Principal = Depends(current_principal),
                       session: Session = Depends(get_session)):
    """Throw the form away. Nothing was ever written to the workspace.

    Idempotent in the way that matters on a Cancel button: discarding a form
    that is already gone answers ``ok`` rather than 404, because the only way
    to reach it twice is to have got what you wanted the first time.
    """
    quote_workspace.discard_form(session, principal.organization_id, form_id,
                                 principal.user_id)
    return {"ok": True}


@router.get("/{quote_id}")
def get_quote(quote_id: str,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)):
    # `user_id`, because this is the read an unsaved form is opened through and
    # a form belongs to the person typing it. Without it a colleague could open
    # somebody's half-written form by naming its id.
    return _view(session, principal,
                 _get_quote(session, quote_id, principal.organization_id,
                            user_id=principal.user_id))


@router.delete("/{quote_id}")
def delete_quote(quote_id: str,
                 principal: Principal = Depends(current_principal),
                 session: Session = Depends(get_session)):
    """Remove a draft from the workspace.

    Only a draft. A quote that has produced a document in somebody's ledger
    is a record — ``quote_documents`` and the outcome row key on its id — and
    removing the draft would leave that document with nothing on this side
    to explain it. The list shows it as sent instead.

    "Remove" is an archive stamp on the row rather than a DELETE, so the
    number it was given is never minted again (``quote_workspace.delete``).

    An unsaved form is discarded instead, which is a real delete: the stamp
    exists to protect a minted number and a form has none. Handled here rather
    than left to the form endpoint because otherwise this would answer ``ok``
    having deleted nothing — the archive stamp does not apply to a table it
    cannot see, and silently succeeding is the shape of lie this file is
    otherwise careful about.
    """
    org = principal.organization_id
    q = _get_editable(session, principal, quote_id)
    if not q.saved:
        quote_workspace.discard_form(session, org, quote_id, principal.user_id)
        return {"ok": True}
    # ``latest_document``, not ``latest_written_document``: an UNVERIFIED send
    # may have landed, and a quote that may be in somebody's ledger stays.
    newest = quote_service.latest_document(session, org, quote_id=quote_id)
    if newest is not None:
        unverified = newest.write_state == QuoteDocumentWriteState.UNVERIFIED.value
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            ("A send of this quote is unverified — look for reference "
             f"{newest.reference} in {conn.system_label_for(newest.external_system)} "
             "first. A quote that may be in the books stays on record."
             if unverified else
             "This quote has been sent, so it stays on record. Only an unsent "
             "draft can be removed."))
    # And a quote somebody has already answered for: a loss recorded straight
    # from draft has no document, and is still a fact an analysis has counted.
    human = quote_service.get_outcome(session, org, quote_id)
    record = quote_service.decide(human, None) if human is not None else None
    if record is not None and record.status is not QuoteOutcomeStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This quote is recorded as {record.status.value.lower()}, so it "
            "stays on record. Only an unsent, undecided draft can be removed.")
    quote_workspace.delete(session, org, quote_id, principal.user_id)
    return {"ok": True}


@router.put("/{quote_id}/customer")
def set_customer(quote_id: str, body: SetCustomerRequest,
                 principal: Principal = Depends(current_principal),
                 session: Session = Depends(get_session)):
    """Say who this quote is for — or change your mind.

    The lines already on the quote are resolved again under the customer's
    identity scope (``store.set_customer`` says what survives that and why),
    so the quote is honest about which customer its resolutions were made
    for. This used to be impossible: changing the customer started a new
    quote and discarded the current one.

    Same books binding as every other line mutation: the re-resolution reads
    live price and stock for the *new* customer's company, which is what
    ``books_for_quote`` resolves from the customer — so the customer is
    written first and the adapter is chosen after.
    """
    q = _get_editable(session, principal, quote_id)
    if not body.customer.strip() and not body.customer_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Name the customer this quote is for.")
    # The customer has to belong to the company this quote prices from —
    # refused here, before anything is written, in the sentence that names
    # both companies. See ``quote_workspace.require_same_company``.
    try:
        quote_workspace.require_same_company(
            session, principal.organization_id,
            connection_id=q.connectionId, customer_id=body.customer_id)
    except quote_workspace.CompanyMismatch as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    # A document already written sits on one customer's account in the source,
    # and nothing in PIE could say so if the quote moved to another. Refused,
    # as removing a sent quote is (decision D4): a different customer is a new
    # quote. Choosing the same customer again is not a change and still
    # re-resolves the lines.
    changing = (body.customer_id or None) != (q.customerId or None) or (
        not body.customer_id and body.customer.strip() != q.customer)
    written = quote_service.latest_written_document(
        session, principal.organization_id, quote_id=quote_id)
    if changing and written is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This quote was sent to {q.customer} as "
            f"{conn.system_label_for(written.external_system)} "
            f"{written.external_document_number}, which sits on their account "
            f"there. Start a new quote for another customer.")
    q.customer, q.customerId = body.customer.strip(), body.customer_id or None
    quote_workspace.save(session, q, principal.user_id)
    books = books_for_quote(quote_id, principal, session)
    kept = store.set_customer(q, body.customer, body.customer_id, books.zoho,
                              _customer_scope(session, principal, q.customer_ref),
                              _bands(session, principal),
                              _mapping_store(session, principal))
    out = _saved(session, principal, q)
    if q.lines:
        out["note"] = (
            f"{len(q.lines)} line(s) resolved again for {q.customer}"
            + (f" — {kept} price(s) you typed kept." if kept else "."))
    return out


@router.put("/{quote_id}/fields")
def set_fields(quote_id: str, body: SetFieldsRequest,
               principal: Principal = Depends(current_principal),
               session: Session = Depends(get_session)):
    """The quote-level details — customer reference, validity, terms, and the
    organization's own fields. Checked against the definitions
    (``quote_fields.normalise``); which are mandatory is judged at the send
    and reported on the quote as ``missingFields``, never enforced here, so a
    half-filled form can still be saved."""
    q = _get_editable(session, principal, quote_id)
    defs = quote_fields.definitions_for(session, principal.organization_id)
    try:
        q.fields = quote_fields.normalise(defs, body.fields)
    except quote_fields.FieldError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return _saved(session, principal, q)


@router.put("/{quote_id}/owner")
def set_owner(quote_id: str, body: SetOwnerRequest,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)):
    """Hand the quote to another member. The owner may, and so may whoever the
    policy lets edit — handing over is a change like any other."""
    q = _get_editable(session, principal, quote_id)
    _needs_saving(q, "be handed to somebody else")
    try:
        name = quote_workspace.set_owner(session, principal.organization_id, q,
                                         body.user_id, by_user_id=principal.user_id)
    except LookupError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    out = _view(session, principal, q)
    out["note"] = f"{q.number} is now {name}'s."
    return out


@router.post("/{quote_id}/intake")
def intake(quote_id: str, body: IntakeRequest,
           principal: Principal = Depends(current_principal),
           zoho: ZohoService = Depends(zoho_for_quote),
           session: Session = Depends(get_session)):
    q = _get_editable(session, principal, quote_id)
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
    return {**_saved(session, principal, q),
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
        return _get_quote(session, quote_id,
                          principal.organization_id).customer_ref or ""
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
                 principal: Principal = Depends(current_principal),
                 session: Session = Depends(get_session)):
    """Ranked supply candidates for a line (the design's supply drawer)."""
    q = _get_quote(session, quote_id, principal.organization_id)
    ln = _get_line(q, line_id)
    return {
        "lineId": ln.id,
        "reqCode": ln.reqCode,
        "reqDesc": ln.reqDesc,
        "supplyCode": ln.supplyCode,
        "candidates": [c.to_dict() for c in ln.candidates],
        "notes": ln.notes,
    }


@router.get("/{quote_id}/item-search")
def item_search(quote_id: str, q: str = "", limit: int = 20,
                principal: Principal = Depends(current_principal),
                session: Session = Depends(get_session)):
    """Find an item by hand, in the catalogue and in the books at once.

    **The door the drawer did not have.** A line the engine could not answer
    offered its ranked candidates and nothing else, so a line with no
    candidates — the engine down, or a product this company has never decoded —
    could not be pointed at an item at all, however plainly the person knew
    which one it was. ``select_supply`` has always accepted a code that is in
    no candidate list (``manual``); there was simply no way to name one.

    **Two sources, kept apart in the response on purpose.** They answer
    different questions and a merged list would blur them: the catalogue says
    *this product exists and here is what it decodes to*, the books say *this
    business already sells it, under this code*. A record can be in one and not
    the other, and which one it is changes what happens next — a catalogue
    record that is not in the books comes back NOT IN BOOKS with no price, and
    the screen offers to create it.

    Each side reports whether it could be searched at all. That is not
    symmetry for its own sake: the usual reason somebody is on this screen is
    that the engine did not answer, and an empty catalogue list that cannot say
    "there was nothing to search" is CLAUDE.md §1's benign default — absence of
    evidence read as evidence of absence.

    Scoped to the quote's own company, for the reason ``create_quote`` decides
    it once: this is the catalogue that resolves this quote's lines and the
    book that prices them, and a search answering out of another company's
    would offer a product this one cannot sell.

    **No money.** Not a price, not a cost, not a margin, and no field for one.
    Search answers *which item*; what it costs is the books' answer and is read
    per line at selection. A salesperson and a manager get identical bytes
    here, which is the cheapest way to be sure of §1's second invariant.
    """
    q = (q or "").strip()
    limit = max(1, min(int(limit or 20), 50))
    quote = _get_quote(session, quote_id, principal.organization_id)
    found = pie_service.search_catalogue(q, quote.connectionId, limit=limit)
    books = ReadModelRepository(session, principal.organization_id).search_items(
        q, connection_id=quote.connectionId, limit=limit)
    return {
        "query": q,
        "catalogue": found.to_dict(),
        # No `available` twin: the read model is this deployment's own database,
        # so "could it be searched" is not a question with two answers the way
        # it is for an engine that may not be installed. An empty list here is
        # evidence — nothing in this company's synced master matches — and the
        # honest shape for evidence is the evidence.
        "books": {"records": books, "searched": len(books)},
    }


@router.post("/{quote_id}/lines/{line_id}/supply")
def select_supply(quote_id: str, line_id: str, body: SelectSupplyRequest,
                  principal: Principal = Depends(current_principal),
                  zoho: ZohoService = Depends(zoho_for_quote),
                  session: Session = Depends(get_session)):
    q = _get_editable(session, principal, quote_id)
    ln = _get_line(q, line_id)
    confirmed = _confirm_identity(session, principal, q, ln, body.code)
    learned = _learn_phrase(session, principal, q, ln, body.code)
    store.select_supply(ln, body.code, zoho, manual=body.manual)
    out = _saved(session, principal, q)
    if confirmed:
        # Worth saying out loud: the person has just taught the system something
        # permanent, and a change with no feedback reads as a change that did
        # not happen.
        out["note"] = (f"Recorded: this customer's {ln.reqCode} means {body.code}. "
                       "It will resolve on its own from now on.")
    elif learned:
        # Weaker, and worded so: nothing will resolve on its own from this.
        out["note"] = (f"Noted: for this customer, \"{ln.reqCode}\" was quoted as "
                       f"{body.code}. It will be offered next time they ask for "
                       "something close.")
    return out


def _learn_phrase(session: Session, principal: Principal,
                  quote: Quote, ln: Line, code: str) -> bool:
    """Remember a person's choice on a requirement line, for retrieval.

    The other half of ``_confirm_identity``, and deliberately the lesser one:
    that records an identity the engine will assert, and refuses all but the
    engine's own proposal; this records a *choice* the engine will never read,
    for a customer, so ``app/retrieval/aliases`` can offer it back as an
    option. Only for a line the engine read as words — a code goes through the
    gate above or nowhere — and only for a linked customer, because the same
    words from another customer are another request.
    """
    if ln.semantics == "IDENTITY" or not ln.customerScope or code == ln.reqCode:
        return False
    try:
        row = identity_service.record_phrase_alias(
            session, principal.organization_id,
            identity_id=ln.customerScope, phrase=ln.reqCode,
            target_record_id=code,
            source_ref=f"quote {quote.id} line {ln.id}",
            user_id=principal.user_id)
        if row is not None:
            session.commit()
        return row is not None
    except Exception:  # noqa: BLE001 — a quote must not fail over bookkeeping
        log.exception("could not record a phrase alias for line %s", ln.id)
        session.rollback()
        return False


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
    q = _get_editable(session, principal, quote_id)
    store.confirm_reading(_get_line(q, line_id))
    return _saved(session, principal, q)


@router.post("/{quote_id}/lines/{line_id}/price")
def set_price(quote_id: str, line_id: str, body: SetPriceRequest,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)):
    q = _get_editable(session, principal, quote_id)
    ln = _get_line(q, line_id)
    store.set_price(ln, body.price)
    return _saved(session, principal, q)


@router.post("/{quote_id}/lines/{line_id}/cost")
def set_custom_cost(quote_id: str, line_id: str, body: SetCustomCostRequest,
                    principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)):
    """Record the cost price a person sourced for this line, or clear it.

    Every role that may edit the quote, salesperson included, and that is the
    point of it rather than a relaxation of §1. The books answer "what have we
    paid for this item"; on a first-time part they answer nothing, and the
    person holding the supplier's offer is the one at the desk. Refusing them
    the field does not keep a cost from the quote — it keeps the *right* cost
    from it, and leaves margin, the floors and the approval gate resting on a
    number that is missing.

    What is written is the caller's own number, not the platform's, so nothing
    here discloses a cost. Reading one back is where the gate lives:
    ``Line.customCostRestricted`` marks an entry management made, and
    ``to_dict`` withholds those from the desk.
    """
    q = _get_editable(session, principal, quote_id)
    ln = _get_line(q, line_id)
    try:
        store.set_custom_cost(
            ln, body.cost, user_id=principal.user_id, note=body.note,
            restricted=principal.is_manager_or_owner)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return _saved(session, principal, q)


@router.delete("/{quote_id}/lines/{line_id}")
def delete_line(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal),
                session: Session = Depends(get_session)):
    q = _get_editable(session, principal, quote_id)
    try:
        store.delete_line(q, line_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Line not found")
    return _saved(session, principal, q)


@router.post("/{quote_id}/discount")
def apply_discount(quote_id: str, body: DiscountRequest,
                   principal: Principal = Depends(current_principal),
                   session: Session = Depends(get_session)):
    q = _get_editable(session, principal, quote_id)
    selected = [ln for ln in q.lines if ln.id in set(body.lineIds)]
    n = store.apply_discount(selected, body.percent)
    result = _saved(session, principal, q)
    result["applied"] = n
    return result


@router.post("/{quote_id}/lines/{line_id}/create-item")
def create_item(quote_id: str, line_id: str,
                principal: Principal = Depends(current_principal),
                zoho: ZohoService = Depends(zoho_for_quote),
                session: Session = Depends(get_session)):
    q = _get_editable(session, principal, quote_id)
    ln = _get_line(q, line_id)
    if not ln.supplyCode:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Line has no supply product to create")
    # A failed write is reported as state on the line (CREATE FAILED) rather
    # than as an error status, because the rest of the quote is untouched and
    # still worth looking at. The reason travels with it so the screen does not
    # have to say "something went wrong".
    failure = store.create_item(ln, zoho)
    result = _saved(session, principal, q)
    if failure:
        result["createItemError"] = failure
    return result


def _send_gates(session: Session, principal: Principal, q: Quote,
                words: dict[str, str], *,
                books: Optional[QuoteBooks] = None) -> Optional[EstimateResponse]:
    """Everything a quote must satisfy before it can leave the desk, in the
    order the workspace's ``readiness`` reports it — or ``None`` when it may.

    One function for the two ways out (``create_estimate`` and ``mark_sent``),
    so a quote the ERP send would refuse is one a person cannot mark as sent
    either: the gates are about the quote, not about the writer. ``books`` is
    the ERP send's book; the manual path has none and skips the one check
    that is about it.
    """
    blockers = store.blockers(q)
    if blockers:
        return EstimateResponse(
            ok=False, **words,
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
            ok=False, **words,
            blockers=[ln.id for ln in unpriced],
            message=(f"{len(unpriced)} line(s) have no rate yet: "
                     f"{_name_lines(unpriced)}."),
        )

    if not q.has_customer:
        # Nobody has said whose quote this is. The document is written into
        # the customer's books and priced against their history, and neither
        # exists for a quote with no customer — so the send stops here, before
        # a snapshot is recorded against a customer reference of "".
        return EstimateResponse(
            ok=False, **words,
            message="Choose a customer before sending — the quote is written "
                    "into their books.")
    missing = quote_fields.missing_required(
        quote_fields.definitions_for(session, principal.organization_id), q.fields)
    if missing:
        # The organization made these mandatory. Named, so the desk fills in
        # the right box rather than reading "details missing".
        return EstimateResponse(
            ok=False, **words,
            message=(f"{len(missing)} detail(s) this organization requires on every "
                     f"quote are missing: {', '.join(missing)}."))
    if (books is not None and books.connection_id and q.connectionId
            and books.connection_id != q.connectionId):
        # Belt and braces behind ``require_same_company``: the customer's book
        # and the catalogue that priced the lines disagree — a draft made
        # before the rule existed, or a customer re-attributed by a sync since.
        # Refused rather than written into the book the lines were not priced
        # from, and before any snapshot is recorded for a send that will not
        # happen.
        companies = Companies(session, principal.organization_id)
        return EstimateResponse(
            ok=False, **words,
            message=(f"{q.customer} belongs to "
                     f"{companies.label_for(books.connection_id)}, and this quote's "
                     f"lines were priced from "
                     f"{companies.label_for(q.connectionId)}'s catalogue. A quote is "
                     f"written into the company that priced it — start it again "
                     f"from {companies.label_for(books.connection_id)}."))
    return None


@dataclass(frozen=True)
class _RevisionPlan:
    """Which revision a press would be, or the answer that it would be none."""

    fingerprint: str
    newest: Optional[models.QuoteDocument]
    retrying: bool
    revision: int
    reference: str
    #: Set when the newest document already covers this content: the press
    #: creates nothing, records nothing, and this is its whole answer.
    covers: Optional[EstimateResponse] = None


def _revision_plan(session: Session, org: str, q: Quote, system: str) -> _RevisionPlan:
    """Sending twice, and sending again — decided the same way for both ways out.

    Three presses used to create three estimates in Zoho, because nothing on
    the quote remembered that it had been sent and the button never changed.
    Re-sending an *amended* quote is ordinary work, so this is not a lock:
    unchanged content answers with the document it already produced, and
    changed content is the next revision. Answered *before* the assessment is
    recorded: a press that creates nothing is not a send, and writing a
    snapshot set for it grew the audit trail by a duplicate decision per press.
    """
    fingerprint = store.priced_fingerprint(q)
    newest = quote_service.latest_document(session, org, quote_id=q.id)
    # An UNVERIFIED newest row is a send whose reply was lost and whose settle
    # read failed. This press retries *that* revision under *its* reference,
    # and the source's own pre-flight settles which of the two it was.
    retrying = (newest is not None and
                newest.write_state == QuoteDocumentWriteState.UNVERIFIED.value)
    covers = None
    if newest is not None and not retrying and newest.fingerprint == fingerprint:
        # Named from the row rather than from the caller's book: this answers
        # about the document that was actually written, which may predate a
        # customer being re-pointed at a different system.
        held = newest.external_system or system
        if newest.channel == QuoteDocumentChannel.MANUAL.value:
            message = (f"This quote was marked as sent (revision {newest.revision}) "
                       "and nothing has changed since.")
        else:
            message = (f"{conn.system_label_for(held)} {conn.quote_term_for(held)} "
                       f"{newest.external_document_number} already covers this quote "
                       "— nothing has changed since it was created.")
        covers = EstimateResponse(
            ok=True, documentNumber=newest.external_document_number or None,
            lineCount=newest.line_count, alreadyExisted=True,
            revision=newest.revision, **_system_words(held), message=message)
    revision = (1 if newest is None
                else newest.revision if retrying
                else newest.revision + 1)
    return _RevisionPlan(
        fingerprint=fingerprint, newest=newest, retrying=retrying,
        revision=revision,
        reference=quote_service.revision_reference(q.reference, revision),
        covers=covers)


def _assess_and_gate(session: Session, principal: Principal, q: Quote) -> None:
    """Record the quote's own assessment *first*, then judge it — 403 if it
    needs an approval it does not have.

    The gate reads the latest snapshot per line, and until this call the only
    thing writing snapshots was a salesperson choosing to open a drawer and
    record an override — so the ordinary path wrote none, the gate found
    nothing to judge, and `can_submit` was true no matter what the margins
    were. A line priced at 0% against a 15% floor was reported sendable and
    sent. The control the paragraph below describes existed; nothing ever
    reached it. It also means the audit trail records every quote that was
    *sent*, not only the ones somebody happened to annotate — and, since the
    manual path shares this, every quote a person marked as sent too.

    The gate needs an organization. It reads it from the signed-in principal
    rather than from a second optional header — an identity the caller could
    omit was an approval gate the caller could skip.
    """
    org = principal.organization_id
    quote_service.assess_and_record(
        session, org, quote_id=q.id, customer_ref=q.customer_ref,
        # Which company's catalogue resolved these lines, so the frozen row
        # says what it was judging as well as what it decided.
        connection_id=q.connectionId,
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
            session, org, q.id,
            # The lines this screen is already showing a below-floor warning
            # about. Its margin uses the item's cost from the books, which is
            # present for items the assessment has no synced bill rows for — so
            # a line the person can see flagged in an alert was sendable.
            also_requiring={ln.id: ln.reqCode
                            for ln in q.lines if ln.economics().below_floor})
        if blocked:
            raise HTTPException(status.HTTP_403_FORBIDDEN, blocked)


@router.post("/{quote_id}/mark-sent", response_model=EstimateResponse)
def mark_sent(quote_id: str,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)):
    """A person says this quote went out — by PDF, by hand, into a book this
    platform reads but cannot write to — and the quote is SENT from here on.

    The same gates as the send and the same assessment, with no writer: a
    quote the ERP send would refuse is one nobody can mark as sent either,
    and the audit trail records what was marked exactly as it records what
    was sent. What it leaves behind is a ``quote_documents`` row in channel
    MANUAL — the content, the revision, the policy in force, and no document
    id, because there is none — and the outcome row moved to SENT. Readiness,
    the duplicate check and the delete guard all read that row already, so
    nothing else has to know the difference.

    Unchanged content answers that it is already marked, the way an unchanged
    send answers that the document already covers it, and records nothing.
    """
    q = _get_editable(session, principal, quote_id)
    _needs_saving(q, "be marked as sent")
    org = principal.organization_id
    connector = _quote_connector(session, org, q)
    words = _system_words(connector)
    refused = _send_gates(session, principal, q, words)
    if refused is not None:
        return refused
    plan = _revision_plan(session, org, q, connector)
    if plan.covers is not None:
        return plan.covers
    _assess_and_gate(session, principal, q)
    lines = [ln for ln in q.lines if ln.supplyCode]
    quote_service.record_document(
        session, org, quote_id=quote_id, external_system=connector,
        connection_id=q.connectionId, number="", document_id=None,
        line_count=len(lines), fingerprint=plan.fingerprint,
        reference=plan.reference, revision=plan.revision,
        channel=QuoteDocumentChannel.MANUAL,
        thresholds_version=policy_service.load_for_org(session, org).version)
    session.commit()
    warning: Optional[str] = None
    try:
        quote_service.set_outcome(
            session, org, quote_id=quote_id, status=QuoteOutcomeStatus.SENT,
            customer_ref=q.customer_ref, customer_id=q.customerId,
            user_id=principal.user_id)
    except (quote_service.InvalidTransition,
            quote_service.QuoteOutcomeRepointed) as e:
        # A decided quote, or an outcome about a different document. The
        # marked row stands — the person said it went out — and the refusal
        # travels with the answer rather than into a log.
        warning = f"The outcome could not be updated: {e}"
    session.commit()
    return EstimateResponse(
        ok=True, documentNumber=None, lineCount=len(lines),
        revision=plan.revision, warning=warning, **words,
        message=(f"Marked as sent — revision {plan.revision}, {len(lines)} lines. "
                 f"Nothing was written into {words['systemLabel']} from here."))


@router.post("/{quote_id}/estimate", response_model=EstimateResponse)
def create_estimate(quote_id: str,
                    principal: Principal = Depends(current_principal),
                    books: QuoteBooks = Depends(books_for_quote),
                    session: Session = Depends(get_session)):
    q = _get_editable(session, principal, quote_id)
    _needs_saving(q, "be sent")
    # The words for the system this quote is bound to, on every answer this
    # endpoint gives — refusals included. They used to be filled in only where
    # a document was actually written, so a screen that wanted to say what it
    # had *failed* to create had nothing to name it with.
    words = _system_words(_quote_connector(session, principal.organization_id, q))
    refused = _send_gates(session, principal, q, words, books=books)
    if refused is not None:
        return refused

    org = principal.organization_id

    # ── sending twice, and sending again ─────────────────────────────────────
    # Three presses used to create three estimates in Zoho, because nothing on
    # the quote remembered that it had been sent and the button never changed.
    # Re-sending an *amended* quote is ordinary work, so this is not a lock:
    # unchanged content answers with the document it already produced, and
    # changed content becomes a new **revision** — a new document under a
    # reference of its own. Every live source keys its idempotency on the
    # reference, so an amendment sent under the first one was answered with the
    # document the source already held, and this platform then recorded the
    # new content against the old number.
    #
    # Answered from the persisted row rather than from the in-memory quote — a
    # restart erases the copy and not the send — and answered *before* the
    # assessment is recorded: a press that creates nothing is not a send, and
    # writing a snapshot set for it grew the audit trail by a duplicate
    # decision per press.
    plan = _revision_plan(session, org, q, books.system)
    if plan.covers is not None:
        return plan.covers
    fingerprint, newest, retrying = plan.fingerprint, plan.newest, plan.retrying
    revision, reference = plan.revision, plan.reference

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
    _assess_and_gate(session, principal, q)
    lines = [{"code": ln.supplyCode, "itemId": ln.itemId,
              "qty": ln.reqQty, "rate": ln.quoted}
             for ln in q.lines if ln.supplyCode]

    # ── the write, and the three answers it is allowed to give ───────────────
    # Never a fourth. A refusal names the lines so the screen can point at them;
    # an unresolvable outcome says so, carries the reference to look up, and is
    # now *recorded* as well as said; a source that could not be reached at all
    # answers as a refusal rather than as a 500 with no words on it. What this
    # must not do is report a created document that may not exist, which is
    # exactly what the mock could never get wrong and a real ledger can.
    #
    # ``reference`` is this revision's, not the quote's: the local half above
    # decided which revision this is, and the source's pre-flight on the
    # reference is the other half — a repeat whose reply was lost is recognised
    # there, and an amendment is not mistaken for one.
    try:
        est = books.quote_writer.create_sales_quotes(q.customer, lines,
                                                 customer_ref=books.contact_id,
                                                 reference=reference)
    except SourceWriteRefused as e:
        refused = {c for c in e.codes if c}
        return EstimateResponse(
            ok=False, **words,
            blockers=[ln.id for ln in q.lines if ln.supplyCode in refused],
            message=str(e))
    except SourceWriteUnknown as e:
        # The request went out and nobody can say what became of it. Written
        # down — the reference it went out under, the revision, the content —
        # so the quote itself says "look for X before sending again" to the
        # next person who opens it, not only to the one who pressed the button
        # and closed the tab. The next press retries this revision under this
        # reference, and the pre-flight settles it.
        quote_service.record_document(
            session, org, quote_id=quote_id,
            external_system=books.system, connection_id=books.connection_id,
            number="", document_id=None, line_count=len(lines),
            fingerprint=fingerprint, reference=reference, revision=revision,
            write_state=QuoteDocumentWriteState.UNVERIFIED,
            thresholds_version=policy_service.load_for_org(session, org).version)
        session.commit()
        return EstimateResponse(ok=False, **words, revision=revision, message=str(e))
    except (SourceUnavailable, IngestionError) as e:
        # A failed pre-flight read, a revoked grant, a throttle: the source was
        # not written to, and the honest answer is that sentence with the
        # system's name on it — not a bare 500 the screen cannot name.
        return EstimateResponse(
            ok=False, **words, revision=revision,
            message=(f"{words['systemLabel']} could not be reached to send this "
                     f"quote: {e} Nothing is recorded as sent — try again once it "
                     f"answers."))

    # Past here the estimate exists — including when Zoho recognised the
    # reference as one it had already landed. Recorded either way, so the local
    # check above can answer the next press without a round trip, and so the
    # quote leaves DRAFT: the DRAFT → SENT → WON/LOST path is modelled, served
    # and typed on the client, and nothing ever moved a quote off DRAFT.
    # The durable half, and the reason this whole endpoint can be pressed twice
    # safely. Written before the outcome transition because it is the record of
    # something that has already happened in somebody's ledger: if the status
    # bookkeeping below fails, the document must still be on file.
    # What was actually sent under this reference. On a retry the source may
    # answer with the document the *lost* press landed — the content this
    # quote held then, not now — so the row records that content, and the
    # chip reads "amended since" if the lines have moved on. Otherwise the
    # write ran now, with these lines.
    sent_fingerprint = (newest.fingerprint if retrying and est.already_existed
                        else fingerprint)
    # The document this revision replaces, for the person who has to void it
    # in the source (decision D2: named, never voided from here).
    previous = (quote_service.latest_written_document(session, org, quote_id=quote_id)
                if revision > 1 else None)
    try:
        quote_service.record_document(
            session, org, quote_id=quote_id,
            external_system=books.system, connection_id=books.connection_id,
            number=est.number,
            document_id=est.document_id, line_count=est.line_count,
            fingerprint=sent_fingerprint, reference=reference,
            revision=revision, already_existed=est.already_existed,
            thresholds_version=policy_service.load_for_org(session, org).version)
        session.commit()
    except Exception:  # noqa: BLE001 — the estimate exists; bookkeeping must not undo it
        log.exception("could not record the document for quote %s", quote_id)
        session.rollback()
    words = _system_words(books.system)
    # ``quote_document_ref`` is the platform recording, at the one moment it
    # learns it, which ERP document its own quote became — and it is the only
    # durable half of that link: the quote pull lands the same document in
    # ``erp_quotes`` under this id, and without it the two tables describe one
    # estimate twice with nothing joining them.
    #
    # ``est.document_id``, not ``est.estimate_id``. Every writer returns
    # ``WrittenDocument`` (``ZohoEstimate`` is an alias of it), and the field was
    # renamed when the write seam stopped being Zoho-shaped. This call kept the
    # old name; the ``AttributeError`` it raised was caught by a blanket
    # ``except`` and logged, and for two weeks every send answered "created"
    # while no quote reached SENT and no link was ever written. So a refusal
    # here now travels in the response — a log line behind a green snackbar is
    # a log line nobody reads.
    #
    # The estimate exists whatever happens below, which is why nothing here
    # turns into a failed send: the person is told the document was created and,
    # separately, why the outcome did not follow it.
    warning: Optional[str] = None
    try:
        quote_service.set_outcome(
            session, org, quote_id=quote_id, status=QuoteOutcomeStatus.SENT,
            quote_document_ref=est.document_id,
            quote_document_connection_id=books.connection_id,
            # The id as well as the name, so the account's holder — not only the
            # person who pressed the button — sees this quote in Won & lost.
            customer_ref=q.customer_ref, customer_id=q.customerId,
            user_id=principal.user_id,
            # The outcome follows the newest document. Allowed only from the
            # document this quote itself wrote last; a record about anything
            # else is refused there and reported below.
            repoint_from=(previous.external_document_id if previous else None))
    except (quote_service.InvalidTransition,
            quote_service.QuoteOutcomeRepointed) as e:
        # The lifecycle refused: a quote already decided, or an outcome already
        # recorded about a different document. Both are facts a person put
        # there, and neither is overwritten by a send.
        warning = f"The outcome could not be updated: {e}"
    except Exception as e:  # noqa: BLE001 — the estimate exists; bookkeeping must not undo it
        log.exception("could not mark quote %s as sent", quote_id)
        warning = (f"The outcome could not be recorded ({type(e).__name__}); the "
                   f"{words['documentTerm']} exists. Report this.")

    named = f"{words['systemLabel']} {words['documentTerm']}"
    superseded = previous.external_document_number if previous else None
    if est.already_existed:
        return EstimateResponse(
            ok=True, documentNumber=est.number, lineCount=est.line_count,
            alreadyExisted=True, warning=warning, revision=revision,
            superseded=superseded, **words,
            message=(f"This quote was already sent — {named} {est.number} exists "
                     f"under reference {reference}. Nothing was created twice."))
    if superseded:
        return EstimateResponse(
            ok=True, documentNumber=est.number, lineCount=est.line_count,
            warning=warning, revision=revision, superseded=superseded, **words,
            message=(f"{named} {est.number} created — revision {revision}, "
                     f"{est.line_count} lines. {superseded} is still in "
                     f"{words['systemLabel']}; void it there."))
    return EstimateResponse(ok=True, documentNumber=est.number,
                            lineCount=est.line_count, warning=warning,
                            revision=revision, **words,
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
    org = principal.organization_id
    policy = approvals.get_policy(session, org)
    out["owner"] = None if not q.ownerId else {
        "id": q.ownerId,
        "name": _names(session, principal, [q.ownerId]).get(q.ownerId, "")}
    # Whether *this* reader may change it — the screen disables what the
    # server would refuse, in the same words `_get_editable` uses.
    out["canEdit"] = quote_workspace.may_edit(
        q, user_id=principal.user_id, role=principal.role, policy=policy)
    # The organization's mandatory details this quote has not answered. On
    # the quote rather than only in the send's refusal, so the form can mark
    # them before anybody presses the button.
    out["missingFields"] = quote_fields.missing_required(
        quote_fields.definitions_for(session, org), q.fields)
    # Which system this quote is bound to, in that system's own words. On the
    # quote rather than only on the document it produces: the screen names the
    # ledger long before anything is sent — "Not in Zoho Books", "+ Create",
    # "Create Zoho Books estimate" — and with nothing here it had no choice but
    # to print the word "Zoho" at every customer, whatever they run.
    out.update(_system_words(_quote_connector(session, org, q)))
    # Whether the item facts on these lines came from that system or from the
    # offline stand-in. ``ZOHO_QUOTE_SERVICE`` defaults to ``mock`` so a fresh
    # clone can never write to a real ledger — correct, and invisible: the
    # mock derives in-books, stock and list price from a hash of the code, so
    # "NOT IN BOOKS" on a screen naming the company's real system is a
    # sentence about nothing. §1 asks for the absence to be stated rather than
    # to read as a pass, and this is the field that states it.
    out["booksLive"] = settings.ZOHO_QUOTE_SERVICE == "live"
    # Which company's catalogue priced this quote, by name. The id is on the
    # quote already; the builder needs the word, beside Quote / Customer /
    # Owner, for the same reason the ERP page names its Book.
    out["company"] = (Companies(session, org).label_for(q.connectionId)
                      if q.connectionId else "")
    newest = quote_service.latest_document(session, org, quote_id=q.id)
    unverified = (newest is not None and
                  newest.write_state == QuoteDocumentWriteState.UNVERIFIED.value)
    sent = (quote_service.latest_written_document(session, org, quote_id=q.id)
            if unverified else newest)
    out["estimate"] = None if sent is None else {
        "number": sent.external_document_number,
        "lineCount": sent.line_count,
        "revision": sent.revision,
        # ERP: this platform wrote it. MANUAL: a person said it went out
        # another way, and there is no number because there is no document.
        "channel": sent.channel,
        "current": sent.fingerprint == store.priced_fingerprint(q),
        # Named, because "Sent · SQ-1001" does not say where it was sent and
        # two connected systems can both answer to that.
        **_system_words(sent.external_system),
        # And what the ERP itself says about that document, once a sync has
        # read it back — its own status word, never this platform's guess.
        "erp": quote_workspace.erp_side(
            quote_service.erp_documents_for(session, org, [sent]).get(q.id)),
    }
    # A send whose fate the source could not confirm, on the quote where the
    # next person finds it. The reference is the whole of what they need: the
    # source either holds a document under it or it does not.
    out["unverifiedSend"] = None if not unverified else {
        "reference": newest.reference,
        "revision": newest.revision,
        "writtenAt": clock.iso(newest.written_at),
        **_system_words(newest.external_system),
    }
    # Whether the Send button can do anything, decided here rather than at
    # the press: a quote whose book this platform only reads shows "Mark as
    # sent" instead of a Send that will refuse, and an unplaceable customer
    # is named on the draft, not after fourteen lines of work.
    out["canSendToErp"], out["sendBlock"] = _send_capability(session, org, q)
    return out


def _send_capability(session: Session, org: str, q: Quote) -> tuple[bool, Optional[str]]:
    """Can this quote be written into its book from here — and if not, why.

    Read at view time and without a credential: the writer's readiness is a
    fact about the connector (``connections.quote_writer_ready``), and whether
    the customer's book resolves is ``book_for_customer``, which lists
    connections and reads no secret. In mock mode nothing is resolved and the
    stand-in writes, as it always has.
    """
    if settings.ZOHO_QUOTE_SERVICE != "live":
        return True, None
    connector = _quote_connector(session, org, q)
    if connector and not conn.quote_writer_ready(connector):
        return False, (
            f"{conn.system_label_for(connector)} is read by this platform but a "
            f"{conn.quote_term_for(connector)} cannot be created there from here. "
            "Send the quote another way and mark it as sent.")
    if q.has_customer:
        customer = quote_service.resolve_customer(session, org, q.customer_ref)
        if customer is not None:
            try:
                conn.book_for_customer(session, org, customer)
            except conn.ConnectionNotFound as e:
                return False, str(e)
    return True, None


#: What to call the ledger when this organization has connected none.
#:
#: A quote can be built with nothing connected — lines resolve against a
#: company's decoded catalogue, which is not a ledger — and every sentence the
#: screen prints still has to name something. "your books" is the honest
#: placeholder: it does not claim a system that is not there, and it reads
#: correctly in the three places it appears ("Not in your books", "no item in
#: your books", "Create quote").
_NO_SYSTEM = {"system": "", "systemLabel": "your books", "systemShort": "books",
              "documentTerm": "quote"}


def _system_words(connector: str) -> dict[str, str]:
    """The naming fields, from one place.

    Built once rather than at each return: every response that names a system
    carries them, and hand-assembled copies are how one of them ends up saying
    "estimate" about a Business Central document long after the others stopped.

    ``systemShort`` is the same name at the width a grid cell has for it —
    see ``connections.system_short_for``.
    """
    if not connector:
        return dict(_NO_SYSTEM)
    return {"system": connector,
            "systemLabel": conn.system_label_for(connector),
            "systemShort": conn.system_short_for(connector),
            "documentTerm": conn.quote_term_for(connector)}


def _quote_connector(session: Session, org: str, quote: Quote) -> str:
    """Which system this quote's company keeps its books in, or "".

    Read off the quote's own company rather than through ``books_for_quote``:
    that resolves the *customer's* book and only in live mode, while this
    question — what to call the ledger on screen — has an answer in mock mode,
    before a customer is chosen, and on a quote that will never be sent. The
    screen asks it on every read, so it must not depend on a credential.

    A connection this organization does not own reads as "" for the same
    reason every other seam refuses one: a quote must not name another
    tenant's system.
    """
    if not quote.connectionId:
        return ""
    try:
        return conn.connector_of(conn.get_connection(session, org, quote.connectionId))
    except conn.ConnectionNotFound:
        return ""


def _name_lines(lines: list[Line], limit: int = 4) -> str:
    """The codes on these lines, for a sentence that has to fit in an alert."""
    codes = [ln.reqCode for ln in lines[:limit]]
    more = len(lines) - len(codes)
    return ", ".join(codes) + (f" and {more} more" if more > 0 else "")
