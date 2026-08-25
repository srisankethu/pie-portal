"""Pydantic request/response bodies for the portal API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CreateQuoteRequest(BaseModel):
    customer: str = "New customer"
    #: The platform's id for the picked customer. Optional because a quote can
    #: still be started from a typed name — but when it is present, every
    #: downstream resolution is an exact lookup instead of a tolerant name
    #: match, which is what keeps two books' identically-named customers apart.
    customer_id: Optional[str] = None


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


class DiscountRequest(BaseModel):
    lineIds: List[str]
    percent: float


class EstimateResponse(BaseModel):
    ok: bool
    estimateNumber: Optional[str] = None
    lineCount: Optional[int] = None
    blockers: List[str] = []
    message: str = ""
