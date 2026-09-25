"""Pydantic request/response bodies for the portal API."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class CreateQuoteRequest(BaseModel):
    #: Empty by default, and empty means *no customer yet* — never a
    #: placeholder. It used to default to "New customer", a literal the quote
    #: then carried as though somebody had chosen it.
    customer: str = ""
    #: The platform's id for the picked customer. Optional because a quote can
    #: still be started from a typed name — but when it is present, every
    #: downstream resolution is an exact lookup instead of a tolerant name
    #: match, which is what keeps two books' identically-named customers apart.
    customer_id: Optional[str] = None
    #: Which company this quote is raised from. Each connected company has its
    #: own decoded catalogue, so this decides what every line on the quote is
    #: resolved against — and a quote that never names one resolves nothing.
    #: Optional on the wire because an organization reading one company's books
    #: has no choice to make; with several, the server refuses rather than
    #: picking (see ``resolution.company_for``).
    connection_id: Optional[str] = None


class FromErpRequest(BaseModel):
    #: The connected company whose book raised the quote. Required, not
    #: optional as it is on the reads: an ERP reference is unique only inside
    #: one book, and a form is a write.
    connection_id: str
    #: The ERP's own reference for the quote — ``erp_quotes.external_ref``.
    ref: str

class SetCustomerRequest(BaseModel):
    """Who a quote is for, said after it was started — or changed."""

    customer: str
    customer_id: Optional[str] = None


class SetFieldsRequest(BaseModel):
    """Quote-level details, keyed by the organization's field definitions."""

    fields: dict[str, Any]


class SetOwnerRequest(BaseModel):
    """Hand the quote to another member of the organization."""

    user_id: str


class IntakeRequest(BaseModel):
    text: str = Field(..., description="Pasted RFQ text, one requested item per line")
    #: How the enquiry reached the desk, when the person pasting it says so.
    #: Supplying it captures the text into ``inbound_lines`` — the corpus every
    #: text technique waits on — and omitting it captures nothing at all.
    #:
    #: Not defaulted, and that is the whole design of this field.
    #: ``InboundChannel`` has no UNKNOWN member because "an enquiry that arrived
    #: some other way has no honest value to store", so a default here would be
    #: the server picking a route on the sender's behalf and filling the one
    #: index the corpus is grouped by with a guess.
    channel: Optional[str] = None
    #: The document this RFQ arrived as, if one was attached — an
    #: ``rfq_documents`` id from ``POST /api/v1/enquiries/documents``.
    #:
    #: An id rather than the file itself, so the upload keeps its own endpoint
    #: with its own refusals and its own statuses. Folding the bytes in here
    #: would make this route multipart to gain nothing, and would lose the
    #: document whenever the intake failed for an unrelated reason.
    rfq_document_id: Optional[str] = None


class SelectSupplyRequest(BaseModel):
    code: str
    manual: bool = False


class SetPriceRequest(BaseModel):
    price: Optional[float] = None


class SetCustomCostRequest(BaseModel):
    """A cost price a person sourced for one quote line.

    ``cost`` of ``None`` clears the entry and the line goes back to the cost the
    books hold. ``note`` is why that number — the supplier, the offer, how long
    it stands — because a cost with no provenance is one nobody can question.
    """

    cost: Optional[float] = None
    note: str = ""


class DiscountRequest(BaseModel):
    lineIds: List[str]
    percent: float


class EstimateResponse(BaseModel):
    """What became of a send, in the vocabulary of the system it went to.

    Deliberately carries no economics. This is the one payload a salesperson
    sees after pressing send, and §1's rule is that a field whose only purpose
    is to answer a margin question does not belong in it — not a count, not a
    flag, not a rule code. Nothing here is derived from cost.
    """

    ok: bool
    #: The document's number in the system that holds it. Renamed from
    #: ``estimateNumber``: "estimate" is Zoho's word, and the same field now
    #: carries a Business Central sales quote number.
    documentNumber: Optional[str] = None
    lineCount: Optional[int] = None
    blockers: List[str] = []
    message: str = ""
    #: The connector key that holds it, and that system's own name for itself
    #: and for the document. A screen saying "estimate" to a Business Central
    #: user names a record type they cannot find.
    system: str = ""
    systemLabel: str = ""
    #: The same name at the width a grid cell has for it — see
    #: ``connections.system_short_for``.
    systemShort: str = ""
    documentTerm: str = ""
    #: True where the document was already there under this quote's reference
    #: and was reported rather than created. "Sent" and "was already sent" are
    #: different facts, and prose was the only thing distinguishing them.
    alreadyExisted: bool = False
    #: Set when the document was created but the quote's own bookkeeping — the
    #: outcome row that records SENT and which ERP document this quote became —
    #: could not follow it. A successful send with a sentence beside it, never a
    #: failed send: the document exists in somebody's ledger whatever the outcome
    #: table says, and a refusal that lived only in a log was how the link
    #: stayed unwritten for two weeks.
    warning: Optional[str] = None
    #: Which revision of the quote this document is. 1 for a first send; an
    #: amended quote sent again is a new document and a new revision. A
    #: counter, not a figure — nothing about a price is in it.
    revision: Optional[int] = None
    #: On a revision, the number of the document it replaces — which the source
    #: still holds, and which a person voids there. Named so the desk knows
    #: which one; the platform does not void documents (decision D2).
    superseded: Optional[str] = None
