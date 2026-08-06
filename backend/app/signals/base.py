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
from typing import Any, Callable, Iterable, Optional, TypeVar

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
    unit_price: Decimal          # NET of line discount — what the customer paid
    line_revenue: Decimal        # pre-tax, post-discount
    source_ref: dict[str, Any]
    external_ref: str
    # Audit trail for unit_price. Optional because rows synced before the
    # sales-discount fix have neither until their invoice is re-fetched.
    rate: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None


@dataclass(frozen=True)
class CostRow:
    product_id: str
    date: date
    qty: Decimal
    unit_cost: Decimal
    source_ref: dict[str, Any]
    external_ref: str


_T = TypeVar("_T")


def group_by(rows: Iterable[_T], key: Callable[[_T], str]) -> dict[str, list[_T]]:
    """Group rows by a key, preserving order within each group.

    One implementation, because grouping is not a decision any caller owns and
    three copies is three places a change to what "a customer" means — a merged
    identity, say — would have to land.
    """
    out: dict[str, list[_T]] = {}
    for row in rows:
        out.setdefault(key(row), []).append(row)
    return out


@dataclass
class Snapshot:
    """One organization's sale and cost lines, indexed for lookup.

    **Built once, then read.** The per-entity accessors below index on first
    use and cache. Appending to ``sales`` or ``costs`` after an accessor has
    been called would leave the index stale, which is why nothing does — the
    loader builds a snapshot and hands it over finished.

    The indexes are not an optimisation detail; they are the difference between
    linear and quadratic. Every detector loops over customers or products and
    asks for that entity's rows, so a scan per lookup makes the whole run
    O(lines x entities). Measured on a 40,000-line book with 3,200 items, the
    scanning version spent 6.0 of its 8.2 seconds inside these three methods.
    """

    organization_id: str
    sales: list[SaleRow]
    costs: list[CostRow]
    customer_names: dict[str, str] = field(default_factory=dict)
    product_names: dict[str, str] = field(default_factory=dict)
    #: The organization's latest sale date, supplied by the loader.
    #:
    #: Supplied rather than derived, because a snapshot may be *bounded* to one
    #: customer or one window while "the last day the business traded" is a
    #: fact about the whole book. Deriving it from the rows present would make
    #: every screen's reference date depend on how much was loaded.
    last_sale_on: Optional[date] = None

    _by_customer: Optional[dict[str, list[SaleRow]]] = field(
        default=None, repr=False, compare=False)
    _sales_by_product: Optional[dict[str, list[SaleRow]]] = field(
        default=None, repr=False, compare=False)
    _costs_by_product: Optional[dict[str, list[CostRow]]] = field(
        default=None, repr=False, compare=False)

    def as_of(self) -> Optional[date]:
        """Deterministic reference date: the latest sale date in the data.

        Falls back to the loaded rows when the loader did not supply one, so a
        snapshot built by hand in a test behaves exactly as it always did.
        """
        if self.last_sale_on is not None:
            return self.last_sale_on
        dates = [s.date for s in self.sales]
        return max(dates) if dates else None

    def sales_for_customer(self, cid: str) -> list[SaleRow]:
        if self._by_customer is None:
            self._by_customer = group_by(self.sales, lambda s: s.customer_id)
        return self._by_customer.get(cid, [])

    def sales_for_product(self, pid: str) -> list[SaleRow]:
        if self._sales_by_product is None:
            self._sales_by_product = group_by(self.sales, lambda s: s.product_id)
        return self._sales_by_product.get(pid, [])

    def costs_for_product(self, pid: str) -> list[CostRow]:
        if self._costs_by_product is None:
            # Sorted once per group here rather than per lookup: every caller
            # wants them in date order, and sorting inside the accessor sorted
            # the same list again on every call.
            self._costs_by_product = {
                k: sorted(v, key=lambda c: c.date)
                for k, v in group_by(self.costs, lambda c: c.product_id).items()}
        return self._costs_by_product.get(pid, [])

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
