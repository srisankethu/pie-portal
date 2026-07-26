"""Sync service: pull raw Zoho → normalize → upsert the read model.

Idempotent (keys on natural external ids, so re-runs never duplicate) and
org-scoped. Malformed rows are collected in the report and skipped — never
silently dropped, never auto-corrected. Returns a :class:`SyncReport` so callers
can see exactly what was written and what was rejected and why.

Order matters: contacts + items first (so sales/cost lines can resolve their
customer/product), then invoices, then bills.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..repositories import ReadModelRepository
from .normalize import (
    NormalizationError,
    normalize_bill,
    normalize_customer,
    normalize_invoice,
    normalize_product,
)
from .source import ZohoSource


@dataclass
class SyncReport:
    organization_id: str
    customers: int = 0
    products: int = 0
    sales_txns: int = 0
    cost_records: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)

    def skip(self, kind: str, ref: str, code: str, detail: str) -> None:
        self.skipped.append({"kind": kind, "ref": ref, "code": code, "detail": detail})

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "customers": self.customers, "products": self.products,
            "sales_txns": self.sales_txns, "cost_records": self.cost_records,
            "skipped_count": len(self.skipped), "skipped": self.skipped,
        }


class SyncService:
    def __init__(self, session: Session, source: ZohoSource, organization_id: str) -> None:
        self.s = session
        self.source = source
        self.org = organization_id
        self.repo = ReadModelRepository(session, organization_id)

    def run(self) -> SyncReport:
        report = SyncReport(organization_id=self.org)
        self._sync_customers(report)
        self._sync_products(report)
        self.s.flush()  # ensure customers/products have ids for FK resolution
        self._sync_invoices(report)
        self._sync_bills(report)
        return report

    def _sync_customers(self, report: SyncReport) -> None:
        for raw in self.source.list_contacts():
            ref = str(raw.get("contact_id", "?"))
            try:
                self.repo.upsert_customer(normalize_customer(raw))
                report.customers += 1
            except NormalizationError as e:
                report.skip("contact", ref, e.code, e.detail)

    def _sync_products(self, report: SyncReport) -> None:
        for raw in self.source.list_items():
            ref = str(raw.get("item_id", "?"))
            try:
                self.repo.upsert_product(normalize_product(raw))
                report.products += 1
            except NormalizationError as e:
                report.skip("item", ref, e.code, e.detail)

    def _sync_invoices(self, report: SyncReport) -> None:
        for raw in self.source.list_invoices():
            ref = str(raw.get("invoice_id", "?"))
            try:
                lines = normalize_invoice(raw)
            except NormalizationError as e:
                report.skip("invoice", ref, e.code, e.detail)
                continue
            for t in lines:
                cust = self.repo.get_customer_by_external(t.customer_external_id)
                prod = self.repo.get_product_by_external(t.product_external_id)
                if cust is None:
                    report.skip("sales_txn", t.external_ref, "UNKNOWN_CUSTOMER",
                                f"no customer {t.customer_external_id}")
                    continue
                if prod is None:
                    report.skip("sales_txn", t.external_ref, "UNKNOWN_PRODUCT",
                                f"no product {t.product_external_id}")
                    continue
                self.repo.upsert_sales_txn(t, cust.customer_id, prod.product_id)
                report.sales_txns += 1

    def _sync_bills(self, report: SyncReport) -> None:
        for raw in self.source.list_bills():
            ref = str(raw.get("bill_id", "?"))
            try:
                lines = normalize_bill(raw)
            except NormalizationError as e:
                report.skip("bill", ref, e.code, e.detail)
                continue
            for r in lines:
                prod = self.repo.get_product_by_external(r.product_external_id)
                if prod is None:
                    report.skip("cost_record", r.external_ref, "UNKNOWN_PRODUCT",
                                f"no product {r.product_external_id}")
                    continue
                self.repo.upsert_cost_record(r, prod.product_id)
                report.cost_records += 1


def get_source() -> ZohoSource:
    """Select the Zoho source from configuration (fixture offline, or live API)."""
    if settings.ZOHO_SOURCE == "api":
        from .zoho_client import ZohoApiSource
        return ZohoApiSource()
    from .mock_source import FixtureZohoSource
    return FixtureZohoSource()
