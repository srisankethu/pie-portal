"""Pydantic request/response bodies for the portal API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    role: str
    name: str
    email: str


class CreateQuoteRequest(BaseModel):
    customer: str = "New customer"


class IntakeRequest(BaseModel):
    text: str = Field(..., description="Pasted RFQ text, one requested item per line")


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
