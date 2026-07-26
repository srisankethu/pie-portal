"""Explicit, validated schemas.

Two roles:
- **Canonical ingestion DTOs** (``CustomerIn`` … ``CostRecordIn``) are what the
  normalizer emits from raw Zoho payloads. Validation happens here — malformed
  source rows fail loudly at this boundary and are reported, never silently
  written. Raw Zoho shapes never travel past normalization.
- **API read DTOs** (``DecisionRead``) are the scope-filtered projections the
  service layer returns.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import CustomerStatus


class SourceRef(BaseModel):
    """A pointer back to the originating ERP record (provenance)."""

    system: str = "zoho"
    record_type: str            # contact | item | invoice | bill
    record_id: str
    line_id: Optional[str] = None


class CustomerIn(BaseModel):
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    status: CustomerStatus = CustomerStatus.ACTIVE
    first_seen: Optional[date] = None
    assigned_user_id: Optional[str] = None
    source_ref: SourceRef


class ProductIn(BaseModel):
    external_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    uom: Optional[str] = None
    hsn: Optional[str] = None
    active: bool = True
    source_ref: SourceRef


class SalesTxnIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)          # invoice_id:line_id
    customer_external_id: str = Field(min_length=1)
    product_external_id: str = Field(min_length=1)
    date: date
    qty: Decimal
    unit_price: Decimal
    line_revenue: Decimal
    source_ref: SourceRef

    @field_validator("qty", "unit_price", "line_revenue", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        # Parse via str so floats don't introduce binary-float noise (determinism).
        return v if isinstance(v, Decimal) else Decimal(str(v))


class CostRecordIn(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_ref: str = Field(min_length=1)          # bill_id:line_id
    product_external_id: str = Field(min_length=1)
    date: date
    qty: Decimal
    unit_cost: Decimal
    source_ref: SourceRef

    @field_validator("qty", "unit_cost", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v))


# ── API read DTOs ────────────────────────────────────────────────────────────
class DecisionRead(BaseModel):
    """Scope-filtered decision projection returned by the API."""

    decision_id: str
    organization_id: str
    decision_type: str
    subject_entity_type: str
    subject_entity_id: str
    assigned_user_id: Optional[str]
    assigned_role: str
    detected_at: datetime
    priority_band: str
    priority_score: int
    status: str
    ai_status: str
    human_action: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class ActionRequest(BaseModel):
    action: str                                       # VIEW | ACT | DISMISS | SNOOZE | OVERRIDE
    note: Optional[str] = None
    reason: Optional[str] = None
