"""Sync service: pull raw Zoho → normalize → upsert the read model.

Idempotent (keys on natural external ids, so re-runs never duplicate) and
org-scoped. Malformed rows are collected in the report and skipped — never
silently dropped, never auto-corrected.

Three properties matter as much as the mapping itself:

**Order.** Contacts + items first, so sales and cost lines can resolve their
customer/product. Then **bills**, then invoices. Bills come first deliberately:
they are far fewer, and they are the only thing that makes margin and cost
pass-through computable at all — they should not be hostage to a long invoice
pull completing.

**Resumability.** Each document is recorded once its lines are written, and a
later run tells the source to skip what is already held. A pull that is cut
short therefore costs its successor almost nothing, and the rows it did write
are kept rather than thrown away.

**Progress is readable even on failure.** The report is built up in place and
exposed as ``self.report``, so a caller whose ``run()`` raised can still say
exactly how far the pull got.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional

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
    assignments: int = 0
    documents_fetched: int = 0
    documents_resumed: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    # Relationships this pull actually moved. Lets the Customer × Item
    # recompute afterwards target what changed instead of rebuilding the whole
    # organization — the difference between seconds and minutes at scale.
    touched_customer_ids: set[str] = field(default_factory=set)
    touched_product_ids: set[str] = field(default_factory=set)
    # What the identity layer did with what arrived. Suggestions are the number
    # waiting for a person, which is the figure worth surfacing — a sync that
    # quietly parks forty decisions has not finished the job.
    identity_suggestions: int = 0
    identity_links: int = 0

    def skip(self, kind: str, ref: str, code: str, detail: str) -> None:
        self.skipped.append({"kind": kind, "ref": ref, "code": code, "detail": detail})

    @property
    def wrote_anything(self) -> bool:
        return bool(self.customers or self.products or self.sales_txns
                    or self.cost_records or self.assignments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "customers": self.customers, "products": self.products,
            "sales_txns": self.sales_txns, "cost_records": self.cost_records,
            "assignments": self.assignments,
            "documents_fetched": self.documents_fetched,
            "documents_resumed": self.documents_resumed,
            "skipped_count": len(self.skipped), "skipped": self.skipped,
        }


class SyncService:
    """Pull and project one organization's Zoho data.

    ``resume=False`` forgets the document cursor first, so every document is
    re-fetched — the escape hatch for "the read model looks wrong, pull it all
    again".
    """

    def __init__(self, session: Session, source: ZohoSource, organization_id: str,
                 resume: bool = True,
                 on_phase: Optional[Callable[[str], None]] = None,
                 connector: str = "zoho",
                 connection_id: Optional[str] = None) -> None:
        self.s = session
        self.source = source
        self.org = organization_id
        self.resume = resume
        # Reports what the pull is doing, so a job that takes minutes can say
        # so in words. Optional: a scripted caller that does not care passes
        # nothing and the stages run exactly as before.
        self._on_phase = on_phase
        # Which system this pull is reading, and which instance of it. Passed
        # straight through to the identity layer and used nowhere else here —
        # the sync knows it is Zoho; the resolver must never need to.
        self.connector = connector
        self.connection_id = connection_id
        self.repo = ReadModelRepository(session, organization_id)
        self.report = SyncReport(organization_id=organization_id)
        # customer_external_id -> (invoice date, salesperson_id, salesperson_name)
        self._owners: dict[str, tuple[date, str, str]] = {}

    def _phase(self, name: str) -> None:
        if self._on_phase is not None:
            self._on_phase(name)

    def run(self) -> SyncReport:
        """The whole pull in one pass, against this service's own source."""
        self.begin()
        self.run_reference()
        self.run_documents()
        self.finish()
        return self.report

    # ── the three stages, separable so a long pull can be sliced ─────────────
    #
    # Split because only *documents* are date-scoped. Customers and items are
    # the whole master list however narrow the window, so a pull sliced into
    # eighteen months would otherwise re-read the entire contact list eighteen
    # times — turning a fix for one problem into a worse one.

    def begin(self) -> None:
        if not self.resume:
            self._phase("Clearing the document cursor")
            self.repo.clear_ingested()
            self.s.flush()

    def run_reference(self) -> None:
        """Customers and items — pulled once per sync, not once per window."""
        self._phase("Reading customers")
        self._sync_customers()
        self._phase("Reading items")
        self._sync_products()
        self.s.flush()  # ensure customers/products have ids for FK resolution

    def run_documents(self, source: Optional[ZohoSource] = None,
                      label: str = "") -> None:
        """Bills and invoices for one window. ``label`` names it on screen."""
        previous, self.source = self.source, (source or self.source)
        try:
            suffix = f" · {label}" if label else ""
            self._phase(f"Reading bills{suffix}")
            self._sync_bills()
            self._phase(f"Reading invoices{suffix}")
            self._sync_invoices()
            self._count_documents()
        finally:
            self.source = previous

    def finish(self) -> None:
        """Assignments last: they are decided from the newest invoice found,
        which is only known once every window has been read."""
        self._phase("Assigning accounts")
        self._sync_assignments()

    def _count_documents(self) -> None:
        """Add this source's fetch/resume tally to the running total.

        Accumulated rather than assigned: a sliced pull has one source per
        window, and overwriting would report only the last slice's calls.
        """
        self.report.documents_fetched += getattr(self.source, "documents_fetched", 0)
        self.report.documents_resumed += getattr(self.source, "documents_resumed", 0)

    def _skipper(self, doc_type: str):
        """A predicate the source can use to avoid re-fetching known documents."""
        if not self.resume:
            return None
        known = self.repo.ingested_index(doc_type)

        def already_have(doc_id: str, modified_at: str) -> bool:
            if doc_id not in known:
                return False
            # No stamp on either side means we cannot tell it apart — trust the
            # record we already hold rather than pay for the call again.
            return not modified_at or modified_at == known[doc_id]

        return already_have

    def _sync_customers(self) -> None:
        from ..identity import service as identity

        for raw in self.source.list_contacts():
            ref = str(raw.get("contact_id", "?"))
            try:
                row = self.repo.upsert_customer(normalize_customer(raw))
                # Flushed so the row has its id: the connector record points at
                # the read-model row, and a null pointer here would silently
                # decouple the two layers for every newly seen record.
                self.s.flush()
                self.report.customers += 1
            except NormalizationError as e:
                self.report.skip("contact", ref, e.code, e.detail)
                continue
            # One call, no branching. Everything the resolver needs is a fact
            # about the record; nothing about Zoho reaches past this line.
            result = identity.ingest_customer(
                self.s, self.org, connector=self.connector,
                connection_id=self.connection_id, external_id=ref,
                name=str(raw.get("contact_name") or ""),
                gstin=raw.get("gst_no"),
                source_ref={"contact_id": ref, "gst_no": raw.get("gst_no")},
                local_id=getattr(row, "customer_id", None))
            self.report.identity_suggestions += len(result.suggestions)
            if result.linked:
                self.report.identity_links += 1

    def _sync_products(self) -> None:
        from ..identity import service as identity

        for raw in self.source.list_items():
            ref = str(raw.get("item_id", "?"))
            try:
                row = self.repo.upsert_product(normalize_product(raw))
                # Flushed so the row has its id: the connector record points at
                # the read-model row, and a null pointer here would silently
                # decouple the two layers for every newly seen record.
                self.s.flush()
                self.report.products += 1
            except NormalizationError as e:
                self.report.skip("item", ref, e.code, e.detail)
                continue
            result = identity.ingest_item(
                self.s, self.org, connector=self.connector,
                connection_id=self.connection_id, external_id=ref,
                name=str(raw.get("name") or ""),
                sku=raw.get("sku"),
                description=str(raw.get("name") or ""),
                source_ref={"item_id": ref, "sku": raw.get("sku")},
                local_id=getattr(row, "product_id", None))
            self.report.identity_suggestions += len(result.suggestions)
            if result.linked:
                self.report.identity_links += 1

    def _sync_invoices(self) -> None:
        for raw in self.source.list_invoices(skip=self._skipper("invoice")):
            ref = str(raw.get("invoice_id", "?"))
            try:
                lines = normalize_invoice(raw)
            except NormalizationError as e:
                self.report.skip("invoice", ref, e.code, e.detail)
                continue
            for t in lines:
                cust = self.repo.get_customer_by_external(t.customer_external_id)
                prod = self.repo.get_product_by_external(t.product_external_id)
                if cust is None:
                    self.report.skip("sales_txn", t.external_ref, "UNKNOWN_CUSTOMER",
                                     f"no customer {t.customer_external_id}")
                    continue
                if prod is None:
                    self.report.skip("sales_txn", t.external_ref, "UNKNOWN_PRODUCT",
                                     f"no product {t.product_external_id}")
                    continue
                self.repo.upsert_sales_txn(t, cust.customer_id, prod.product_id)
                self.report.sales_txns += 1
                # Which relationships this pull actually moved, so the
                # Customer × Item recompute afterwards is targeted rather than
                # a full rebuild of the organization.
                self.report.touched_customer_ids.add(cust.customer_id)
                self.report.touched_product_ids.add(prod.product_id)
            self._note_owner(raw, lines[0].customer_external_id, lines[0].date)
            self.repo.mark_ingested("invoice", ref, str(raw.get("last_modified_time") or ""))
        self._store_owners()

    def _note_owner(self, raw: dict[str, Any], customer_ext: str, when: date) -> None:
        """Remember the salesperson on the customer's most recent invoice."""
        sp_id = raw.get("salesperson_id")
        if not sp_id:
            return
        current = self._owners.get(customer_ext)
        if current is None or when >= current[0]:
            self._owners[customer_ext] = (when, str(sp_id),
                                          str(raw.get("salesperson_name") or ""))

    def _store_owners(self) -> None:
        """Persist Zoho's salesperson on the customer.

        Kept on the customer rather than recomputed from invoices each run, so a
        resumed pull — which by design never re-reads the invoices it already
        holds — does not lose the ownership those invoices carried.
        """
        for customer_ext, (when, sp_id, _name) in self._owners.items():
            customer = self.repo.get_customer_by_external(customer_ext)
            if customer is None:
                continue
            if customer.source_owner_at is None or when >= customer.source_owner_at:
                customer.source_owner_id = sp_id
                customer.source_owner_at = when
        self.s.flush()

    def _sync_bills(self) -> None:
        for raw in self.source.list_bills(skip=self._skipper("bill")):
            ref = str(raw.get("bill_id", "?"))
            try:
                lines = normalize_bill(raw)
            except NormalizationError as e:
                self.report.skip("bill", ref, e.code, e.detail)
                continue
            for r in lines:
                prod = self.repo.get_product_by_external(r.product_external_id)
                if prod is None:
                    self.report.skip("cost_record", r.external_ref, "UNKNOWN_PRODUCT",
                                     f"no product {r.product_external_id}")
                    continue
                self.repo.upsert_cost_record(r, prod.product_id)
                self.report.cost_records += 1
                # A new cost changes the margin of every customer buying this
                # item, not just the buyer of this bill.
                self.report.touched_product_ids.add(prod.product_id)
            self.repo.mark_ingested("bill", ref, str(raw.get("last_modified_time") or ""))

    def _sync_assignments(self) -> None:
        """Map Zoho's invoice salesperson onto ``customer.assigned_user_id``.

        This decides what a salesperson sees, so it is deliberately narrow: an
        owner is set only when Zoho names a salesperson **and** that person's
        Zoho email matches a user of this organization exactly. Anything else is
        recorded as a skip and leaves the existing assignment untouched — an
        unassigned account is visible to managers and owners, whereas a wrongly
        assigned one is quietly invisible to the person who should act on it.
        """
        owned = [c for c in self.repo.list_customers() if c.source_owner_id]
        if not owned:
            return
        try:
            zoho_users = list(self.source.list_users())
        except Exception as e:  # noqa: BLE001 — never fail a pull over ownership
            self.report.skip("assignment", "users", "ASSIGNMENT_UNAVAILABLE",
                             f"could not read Zoho users ({type(e).__name__}: {e}). "
                             "Customer ownership is unchanged; add the "
                             "ZohoBooks.users.READ scope and sync again to enable it.")
            return

        email_of = {u["user_id"]: str(u.get("email") or "").strip().lower()
                    for u in zoho_users}
        local = self.repo.users_by_email()
        unmapped: set[str] = set()

        for customer in owned:
            sp_id = customer.source_owner_id
            user = local.get(email_of.get(sp_id, ""))
            if user is None:
                if sp_id not in unmapped:
                    unmapped.add(sp_id)
                    self.report.skip(
                        "assignment", sp_id, "UNMAPPED_SALESPERSON",
                        f"Zoho salesperson {sp_id} has no platform user with a matching "
                        f"email ({email_of.get(sp_id) or 'no email in Zoho'})")
                continue
            if customer.assigned_user_id != user.user_id:
                customer.assigned_user_id = user.user_id
            self.report.assignments += 1


class ZohoNotConfiguredError(RuntimeError):
    """This organization has no Zoho connection — distinct from a connection
    that exists but was rejected, because the remedy is different: connect
    one, rather than fix a credential."""


def get_source(session: Session, organization_id: str,
               since: Optional[date] = None,
               connection_id: Optional[str] = None) -> ZohoSource:
    """Select this organization's Zoho source (fixture offline, or its own live
    connection). ``ZOHO_SOURCE=fixture`` is a process-wide dev/test switch and
    applies to every org identically; ``api`` pulls each org's own credentials
    (see ``connections.get_zoho_credentials``) — never another org's, and never
    a silent fall-through to another org's leftover settings.
    """
    if settings.ZOHO_SOURCE == "api":
        from .connections import get_zoho_credentials
        from .zoho_client import ZohoApiSource

        creds = get_zoho_credentials(session, organization_id,
                                     connection_id=connection_id)
        if creds is None:
            raise ZohoNotConfiguredError(
                f"Organization {organization_id!r} has no Zoho connection. "
                "Connect one via PUT /api/v1/data/connection before syncing.")
        return ZohoApiSource(since=since, credentials=creds)
    from .mock_source import FixtureZohoSource
    return FixtureZohoSource()
