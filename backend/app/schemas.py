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
    documentTerm: str = ""
    #: True where the document was already there under this quote's reference
    #: and was reported rather than created. "Sent" and "was already sent" are
    #: different facts, and prose was the only thing distinguishing them.
    alreadyExisted: bool = False
