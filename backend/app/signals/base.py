"""Shared types for detectors: the read-model snapshot, evidence, anomalies,
sufficiency, and the ``SignalDraft`` a detector emits.

A snapshot is an immutable, in-memory view of one org's sales + cost lines, so
detectors are pure functions of (snapshot, thresholds, as_of) — trivially
testable without a database, and byte-deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from ..domain import models
from ..domain.enums import EvidenceSufficiency


# ── anomaly codes (§19) ──────────────────────────────────────────────────────
class Anomaly:
    COST_GT_PRICE = "COST_GT_PRICE"
    ZERO_OR_PLACEHOLDER_COST = "ZERO_OR_PLACEHOLDER_COST"
    NEGATIVE_MARGIN = "NEGATIVE_MARGIN"
    QTY_SPIKE = "QTY_SPIKE"
    PRICE_OUTLIER = "PRICE_OUTLIER"
    NEGATIVE_VALUE = "NEGATIVE_VALUE"


# ── source rows (a normalized projection of the read model) ──────────────────
@dataclass(frozen=True)
class SaleRow:
    customer_id: str
    product_id: str
    date: date
    qty: Decimal
    unit_price: Decimal
    line_revenue: Decimal
    source_ref: dict[str, Any]
    external_ref: str


@dataclass(frozen=True)
class CostRow:
    product_id: str
    date: date
    qty: Decimal
    unit_cost: Decimal
    source_ref: dict[str, Any]
    external_ref: str


@dataclass
class Snapshot:
    organization_id: str
    sales: list[SaleRow]
    costs: list[CostRow]
    customer_names: dict[str, str] = field(default_factory=dict)
    product_names: dict[str, str] = field(default_factory=dict)

    def as_of(self) -> Optional[date]:
        """Deterministic reference date: the latest sale date in the data."""
        dates = [s.date for s in self.sales]
        return max(dates) if dates else None

    def sales_for_customer(self, cid: str) -> list[SaleRow]:
        return [s for s in self.sales if s.customer_id == cid]

    def sales_for_product(self, pid: str) -> list[SaleRow]:
        return [s for s in self.sales if s.product_id == pid]

    def costs_for_product(self, pid: str) -> list[CostRow]:
        return sorted((c for c in self.costs if c.product_id == pid), key=lambda c: c.date)

    def customer_ids(self) -> list[str]:
        return sorted({s.customer_id for s in self.sales})

    def product_ids(self) -> list[str]:
        return sorted({s.product_id for s in self.sales} | {c.product_id for c in self.costs})


# ── evidence + sufficiency ───────────────────────────────────────────────────
def evidence_ref(source_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_system": source_ref.get("system", "zoho"),
        "record_type": source_ref.get("record_type"),
        "record_id": source_ref.get("record_id"),
        "line_id": source_ref.get("line_id"),
    }


def dedup_evidence(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for r in refs:
        key = (r.get("record_type"), r.get("record_id"), r.get("line_id"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


@dataclass
class Sufficiency:
    history_months: float
    txn_count: int
    missing_fields: list[str] = field(default_factory=list)
    anomalies: list[dict[str, str]] = field(default_factory=list)
    level: EvidenceSufficiency = EvidenceSufficiency.SUFFICIENT
    reasons: list[str] = field(default_factory=list)

    def add_anomaly(self, code: str, detail: str) -> None:
        self.anomalies.append({"code": code, "detail": detail})

    def to_dict(self) -> dict[str, Any]:
        return {
            "history_months": round(self.history_months, 1),
            "txn_count": self.txn_count,
            "missing_fields": self.missing_fields,
            "anomalies": self.anomalies,
            "level": self.level.value,
            "reasons": self.reasons,
        }


# ── the emitted draft ────────────────────────────────────────────────────────
@dataclass
class SignalDraft:
    signal_type: str
    subject_entity_type: str
    subject_entity_id: str
    window: dict[str, Any]
    metrics: dict[str, Any]
    severity_base: int
    evidence_refs: list[dict[str, Any]]
    sufficiency: Sufficiency
    detector_version: str
    threshold_config_version: str

    def to_model(self, organization_id: str) -> models.Signal:
        return models.Signal(
            organization_id=organization_id,
            signal_type=self.signal_type,
            subject_entity_type=self.subject_entity_type,
            subject_entity_id=self.subject_entity_id,
            detector_version=self.detector_version,
            threshold_config_version=self.threshold_config_version,
            window=self.window,
            metrics=self.metrics,
            severity_base=self.severity_base,
            evidence_refs=dedup_evidence(self.evidence_refs),
            sufficiency=self.sufficiency.to_dict(),
        )


# ── numeric helpers (avoid false precision; deterministic) ───────────────────
def clamp_severity(value: float) -> int:
    return max(0, min(100, int(round(value))))
