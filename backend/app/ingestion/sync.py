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

from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import today as _clock_today
from ..config import settings
from ..domain import models
from ..repositories import ReadModelRepository
from ..state import events as ev
from ..state.events import EventLog, Source
from ..trust import vault
from .normalize import (
    NormalizationError,
    normalize_bill,
    normalize_bill_terms,
    normalize_invoice_terms,
    normalize_credit_note,
    normalize_customer,
    normalize_invoice,
    normalize_payment,
    normalize_product,
    normalize_purchase_order,
    normalize_sales_order,
    normalize_stock,
    normalize_vendor,
    normalize_vendor_payment,
)
from .source import ZohoSource

#: Distinct from ``None``, which is a legitimate "this org has no zone set".
_UNSET = object()


@dataclass
class SyncReport:
    organization_id: str
    customers: int = 0
    products: int = 0
    sales_txns: int = 0
    cost_records: int = 0
    assignments: int = 0
    vendors: int = 0
    stock_snapshots: int = 0
    payments: int = 0
    purchase_orders: int = 0
    sales_orders: int = 0
    vendor_payments: int = 0
    credit_notes: int = 0
    documents_fetched: int = 0
    documents_resumed: int = 0
    #: Calendar windows this run listed in full because they had never been
    #: covered before. Counted so a backfill can *say* it was a backfill: a run
    #: that widens the window costs list calls over the new months, and a run
    #: that reports zero here read nothing it had not already read.
    windows_listed_in_full: int = 0
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
    # Items this pull matched to a decoded catalogue record. Worth reporting for
    # the reason the coverage assessment exists: this number is expected to be a
    # minority of `products` — roughly a tenth on the live master — so a reader
    # who meets it without that context will read a working sync as a broken
    # one. See docs/concepts/01-application-engineering.md.
    catalog_links: int = 0
    # Documents this pull retired because Zoho no longer reports them — deleted
    # there, or voided, which for a platform that only counts real trade is the
    # same thing. Reported rather than silent: removing data is the one thing a
    # sync does that cannot be inferred from what appeared.
    retired: list[dict[str, str]] = field(default_factory=list)

    def skip(self, kind: str, ref: str, code: str, detail: str,
             context: Optional[dict[str, Any]] = None) -> None:
        """Record one skipped row, with whatever a person needs to act on it.

        ``context`` exists because ``no product 3452161000001252021`` is a true
        statement that nobody can do anything with. The id alone means opening
        Zoho, searching an id that its own UI does not search on, and repeating
        that per line. The document number, the date, the supplier and the item
        as it was *written on the bill* turn the same skip into a task.
        """
        row: dict[str, Any] = {"kind": kind, "ref": ref, "code": code,
                               "detail": detail}
        if context:
            row["context"] = {k: v for k, v in context.items() if v not in (None, "")}
        self.skipped.append(row)

    def unresolved(self) -> list[dict[str, Any]]:
        """Skips folded by what is actually missing, worst first.

        One retired item on four hundred bill lines is **one** problem, and a
        screen that shows it four hundred times — or, worse, shows the first
        twenty of them and hides the other two — describes the symptom instead
        of the cause. Folding on the missing id means the list is as long as the
        number of things to fix, and the count says how much trade each one is
        holding up.
        """
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in self.skipped:
            ctx = row.get("context") or {}
            missing = str(ctx.get("missing_id") or row.get("ref") or "")
            key = (str(row.get("kind")), str(row.get("code")), missing)
            g = groups.get(key)
            if g is None:
                g = groups[key] = {
                    "kind": row.get("kind"), "code": row.get("code"),
                    "missing_id": missing or None,
                    "label": ctx.get("label"),
                    "sku": ctx.get("sku"),
                    "fix": ctx.get("fix"),
                    "lines": 0,
                    "value": 0.0,
                    "first_seen": ctx.get("document_date"),
                    "last_seen": ctx.get("document_date"),
                    "examples": [],
                }
            g["lines"] += 1
            # A name is often blank on one line and present on the next; the
            # first non-empty one wins so the group is nameable.
            for field_name in ("label", "sku", "fix"):
                if not g.get(field_name) and ctx.get(field_name):
                    g[field_name] = ctx[field_name]
            try:
                g["value"] += float(ctx.get("line_value") or 0)
            except (TypeError, ValueError):
                pass
            when = ctx.get("document_date")
            if when:
                if not g["first_seen"] or when < g["first_seen"]:
                    g["first_seen"] = when
                if not g["last_seen"] or when > g["last_seen"]:
                    g["last_seen"] = when
            if len(g["examples"]) < 3 and ctx.get("document"):
                g["examples"].append({
                    "document": ctx.get("document"),
                    "date": ctx.get("document_date"),
                    "party": ctx.get("party"),
                    "qty": ctx.get("qty"),
                    "value": ctx.get("line_value"),
                })
        # Most lines first: the thing blocking the most trade is the thing to
        # fix first, and value is the tie-break because a hundred ₹40 lines
        # matter less than three ₹2 lakh ones.
        return sorted(groups.values(),
                      key=lambda g: (g["lines"], g["value"]), reverse=True)

    @property
    def wrote_anything(self) -> bool:
        return bool(self.customers or self.products or self.sales_txns
                    or self.cost_records or self.assignments or self.vendors
                    or self.stock_snapshots or self.payments
                    or self.purchase_orders or self.sales_orders
                    or self.vendor_payments or self.credit_notes)

    def merge(self, other: "SyncReport") -> "SyncReport":
        """Fold another connected company's pull into this one.

        A run that covers three Zoho companies is still one row on the sync
        screen, and every number on that row has to be the total rather than
        whichever company happened to go last. Counters add, lists concatenate,
        and the touched-id sets union — the last of those is what keeps the
        Customer × Item recompute afterwards targeting everything that moved
        rather than only the final company's share of it.

        Written by field kind rather than by name so a counter added to this
        dataclass later is summed without anybody having to remember this
        method exists.
        """
        for f in fields(self):
            if f.name == "organization_id":
                continue
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            if isinstance(mine, int):
                setattr(self, f.name, mine + theirs)
            elif isinstance(mine, list):
                mine.extend(theirs)
            elif isinstance(mine, set):
                mine |= theirs
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "customers": self.customers, "products": self.products,
            "sales_txns": self.sales_txns, "cost_records": self.cost_records,
            "assignments": self.assignments,
            "vendors": self.vendors, "stock_snapshots": self.stock_snapshots,
            "payments": self.payments, "purchase_orders": self.purchase_orders,
            "sales_orders": self.sales_orders,
            "vendor_payments": self.vendor_payments,
            "credit_notes": self.credit_notes,
            "documents_fetched": self.documents_fetched,
            "documents_resumed": self.documents_resumed,
            "skipped_count": len(self.skipped), "skipped": self.skipped,
        }


def _sole_connection(session: Session, organization_id: str) -> bool:
    """Does this organization have exactly one connected company?

    The question that decides whether a connectionless row can be attributed
    without guessing. One connection: it came from there, because there is
    nowhere else. Two: it could be either, and picking one silently merges a
    stranger's customers into a book they never traded with.
    """
    from ..domain import models

    try:
        return session.scalar(
            select(func.count()).select_from(models.ZohoConnection)
            .where(models.ZohoConnection.organization_id == organization_id)) == 1
    except Exception:  # noqa: BLE001 — never fail a sync over an optimisation
        return False


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
                 connection_id: Optional[str] = None,
                 incremental: bool = True) -> None:
        self.s = session
        self.source = source
        self.org = organization_id
        self.resume = resume
        # Whether the listing itself may stop early. Off makes this the periodic
        # reconciliation: still cheap (unchanged documents cost no detail call)
        # but complete, which is the only state in which deletions are visible.
        self.incremental = incremental
        # Reports what the pull is doing, so a job that takes minutes can say
        # so in words. Optional: a scripted caller that does not care passes
        # nothing and the stages run exactly as before.
        self._on_phase = on_phase
        # Which system this pull is reading, and which instance of it. Passed
        # straight through to the identity layer and used nowhere else here —
        # the sync knows it is Zoho; the resolver must never need to.
        self.connector = connector
        self.connection_id = connection_id
        # The repository writes on behalf of this connected company, so every
        # row it upserts carries where it came from. Without this the read
        # model keys customers and items on an external id alone, which is
        # unique only inside the system that issued it.
        self.repo = ReadModelRepository(
            session, organization_id,
            connector=connector, connection_id=connection_id,
            # Rows this connector wrote before connections existed carry no
            # book. With a single connection there is nowhere else they could
            # have come from, so this pull claims them instead of inserting a
            # twin beside every one. With two or more it would be a guess, and
            # a wrong guess pools two companies' customers — so they are left
            # alone and `scripts/diagnose_attribution.py` reports them.
            adopt_connectionless=(connection_id is not None
                                  and _sole_connection(session, organization_id)))
        # The event log is written in this same pass, from the same normalised
        # DTOs. Not a second traversal: a log assembled later from the read
        # model could only ever record what survived, which is the one thing an
        # event log is supposed to be able to contradict.
        self.log = EventLog(session, organization_id, connector=connector,
                            connection_id=connection_id)
        self.report = SyncReport(organization_id=organization_id)
        # customer_external_id -> (invoice date, salesperson_id, salesperson_name)
        self._owners: dict[str, tuple[date, str, str]] = {}
        self._tz: Any = _UNSET

    def _phase(self, name: str) -> None:
        if self._on_phase is not None:
            self._on_phase(name)

    def timezone(self) -> Optional[str]:
        """The zone this organization's *day* is measured in.

        Read once per pull rather than per row. A missing organization row or a
        blank value falls through to the configured business zone, which is the
        same fallback ``clock.zone`` applies — this is not a place to guess UTC.
        """
        if self._tz is _UNSET:
            org = self.s.get(models.Organization, self.org)
            self._tz = (getattr(org, "timezone", None) or None)
        return self._tz

    def run(self) -> SyncReport:
        """The whole pull in one pass, against this service's own source."""
        self.begin()
        self.run_reference()
        self.run_documents()
        self.run_supply()
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
        """Customers, items and suppliers — the masters, pulled once per sync
        rather than once per window.

        Suppliers sit here rather than with the supply *documents* because
        bills carry a vendor and bills are read in the document stage. Read
        later, every payable on a first pull would resolve to a null supplier
        and only acquire one on the next sync — a table that is right the
        second time is a table nobody can trust the first time. It is still
        wrapped in ``_supply_phase``: it needs no scope customers do not, but a
        refusal must degrade the same way rather than abort the masters.
        """
        self._phase("Reading customers")
        self._sync_customers()
        self._phase("Reading items")
        self._sync_products()
        if hasattr(self.source, "list_vendors"):
            self._supply_phase("Reading suppliers", "vendor", self._sync_vendors)
        self.s.flush()  # ensure customers/products have ids for FK resolution
        # Names go into the tenant's vault here rather than in a separate job:
        # a name changed in the ERP has to reach the vault on the same pull that
        # changed it, or the vault becomes a stale second source of truth.
        self._phase("Securing names")
        vault.backfill(self.s, self.org)
        self.s.flush()

    def run_documents(self, source: Optional[ZohoSource] = None,
                      label: str = "") -> None:
        """Bills and invoices for one window. ``label`` names it on screen."""
        previous, self.source = self.source, (source or self.source)
        try:
            # Per window, because each window gets its own source object. The
            # mark is global to the connection, so every window short-circuits
            # against the same floor — an old window whose documents have not
            # been touched since the last pull stops on its first page.
            self.arm_incremental_listing(self.source)
            suffix = f" · {label}" if label else ""
            self._phase(f"Reading bills{suffix}")
            self._sync_bills()
            self._phase(f"Reading invoices{suffix}")
            self._sync_invoices()
            self._count_documents()
        finally:
            self.source = previous

    def run_supply(self) -> None:
        """Money, orders and commitments — what the book had and we did not.

        Neither stock nor suppliers is here. Stock travels on the item payload,
        so it is written by ``_sync_products`` in the one pass that already
        reads the master list; suppliers moved up to ``run_reference`` because
        bills resolve against them and bills are read before this stage.

        A separate stage from ``run_reference`` because these are the only
        pulls that can be absent: a source written before they existed, or a
        Zoho plan without Inventory, simply does not offer them. Each is probed
        rather than assumed, and a source that cannot answer produces no rows
        and no error — the screens above already say "nothing synced yet" in
        their own words, which is more useful than a failed pull.

        **One stage failing must not take the others down with it.** Payments
        and purchase orders need OAuth scopes that bills and invoices do not,
        and a connection authorised before those scopes existed returns 401 on
        exactly those two endpoints. Before this was guarded, that 401 aborted
        ``run_supply`` mid-way: suppliers had been read, payments raised, and
        purchase orders were never attempted — so the one missing permission
        presented as "vendors and payments are not being read at all", with a
        credentials error pointing at the one thing that was fine.
        """
        if hasattr(self.source, "list_customer_payments"):
            self._supply_phase("Reading payments", "payment", self._sync_payments)
        if hasattr(self.source, "list_purchase_orders"):
            self._supply_phase("Reading purchase orders", "purchase_order",
                               self._sync_purchase_orders)
        # Commitments and money out. Both are things the business is exposed to
        # before any accounting entry exists — an open sales order promises a
        # customer something, and a vendor payment is the half of cash that
        # receipts alone cannot show.
        if hasattr(self.source, "list_sales_orders"):
            self._supply_phase("Reading sales orders", "sales_order",
                               self._sync_sales_orders)
        if hasattr(self.source, "list_vendor_payments"):
            self._supply_phase("Reading payments out", "vendor_payment",
                               self._sync_vendor_payments)
        # Credit given back. Probed like every other optional pull: it needs a
        # scope the older connections were never authorised for, and a 401 here
        # must not take the phases around it down. Today's receivable is already
        # correct without this — Zoho nets applied credit into an invoice's
        # balance — so a connection that cannot read credit notes loses only the
        # ability to reconstruct a *past* position, and loses it visibly.
        if hasattr(self.source, "list_credit_notes"):
            self._supply_phase("Reading credit notes", "credit_note",
                               self._sync_credit_notes)
        self.s.flush()

    def _supply_phase(self, label: str, kind: str,
                      run: Callable[[], None]) -> None:
        """Run one supply pull, recording a refusal instead of propagating it.

        A throttle is deliberately *not* caught: it means the whole pull should
        stop and resume later, and swallowing it here would spend the rest of
        the run making calls that are all going to be rejected.
        """
        from .zoho_client import ZohoScopeError, ZohoThrottleError

        self._phase(label)
        try:
            run()
            self.s.flush()
        except ZohoThrottleError:
            raise
        except ZohoScopeError as e:
            # Not rolled back. Whatever this stage managed to read before the
            # refusal is real data, and discarding it would contradict the rest
            # of this module — a run that wrote 336 rows wrote 336 rows.
            self.s.flush()
            self.report.skip(
                kind, e.path, "SCOPE_NOT_GRANTED", str(e),
                context={"scope": e.scope, "endpoint": e.path,
                         "fix": ("Reconnect this Zoho company from Data & "
                                 f"connection and include {e.scope} in the "
                                 "scope list. Nothing else about the "
                                 "connection needs changing.")})
        except Exception as e:                               # noqa: BLE001
            # Everything already written is kept; this one stage is reported as
            # incomplete. A supplier list that 500s must not discard an invoice
            # pull that took twenty minutes.
            self.s.flush()
            self.report.skip(
                kind, label, "SUPPLY_STAGE_FAILED",
                f"{type(e).__name__}: {e}",
                context={"stage": label,
                         "fix": ("The rest of the pull completed. Re-run the "
                                 "sync; if this repeats, the detail here is "
                                 "what to send on.")})

    def _sync_vendors(self) -> None:
        for raw in self.source.list_vendors():
            ref = str(raw.get("contact_id", "?"))
            try:
                self.repo.upsert_vendor(normalize_vendor(raw))
                self.report.vendors += 1
            except NormalizationError as e:
                self.report.skip("vendor", ref, e.code, e.detail)

    def _record_stock(self, product, raw: dict[str, Any], as_of: date) -> None:
        """One stock snapshot for this item, if the payload carries stock at all.

        A payload with no stock fields is a *source* that does not report stock
        — an older fixture, a Zoho plan without Inventory — not an item with
        none of it. Writing zeros for that case would put the entire catalogue
        in the out-of-stock list on day one.
        """
        if all(raw.get(k) is None for k in
               ("stock_on_hand", "available_stock", "actual_available_stock")):
            return
        item_id = str(raw.get("item_id", "?"))
        try:
            snap = normalize_stock(raw, as_of)
        except NormalizationError as e:
            self.report.skip("stock", item_id, e.code, e.detail)
            return
        self.repo.upsert_stock_snapshot(product.product_id, snap)
        # An observation, not a change: the same item observed again tomorrow
        # is a second event, and the day it was taken on is the key. Superseded
        # per (item, day) so a sync run twice in one afternoon is a correction.
        observation = f"{item_id}:{as_of.isoformat()}"
        self.log.supersede("stock", observation)
        self.log.record(ev.STOCK_OBSERVED, snap.as_of,
                        Source("stock", observation), snap)
        self.report.stock_snapshots += 1

    def _sync_payments(self) -> None:
        for raw in self.source.list_customer_payments(
                skip=self._skipper("customerpayment")):
            ref = str(raw.get("payment_id", "?"))
            try:
                payment = normalize_payment(raw)
            except NormalizationError as e:
                self.report.skip("payment", ref, e.code, e.detail)
                continue
            customer = self.repo.get_customer_by_external(payment.customer_external_id)
            if customer is None:
                # Money from somebody the pull has not seen. Skipped and
                # reported rather than attached to a placeholder customer,
                # which would put a real payment against a fictional account.
                self.report.skip("payment", ref, "UNKNOWN_CUSTOMER",
                                 f"no customer {payment.customer_external_id}")
                continue
            self.repo.upsert_payment(customer.customer_id, payment)
            self.log.supersede("customer_payment", ref)
            self.log.record(ev.PAYMENT_RECEIVED, payment.date,
                       Source("customer_payment", ref,
                              modified_at=str(raw.get("last_modified_time") or "")),
                       payment)
            self.repo.mark_ingested("customerpayment", ref,
                                    str(raw.get("last_modified_time") or ""))
            self.report.payments += 1

    def _sync_purchase_orders(self) -> None:
        for raw in self.source.list_purchase_orders():
            ref = str(raw.get("purchaseorder_id", "?"))
            try:
                po = normalize_purchase_order(raw)
            except NormalizationError as e:
                self.report.skip("purchase_order", ref, e.code, e.detail)
                continue
            vendor_id = None
            if po.vendor_external_id:
                vendor = self.repo.get_vendor_by_external(po.vendor_external_id)
                # An order against a supplier the vendor pull did not return
                # still counts as an open order; it just cannot be grouped by
                # supplier. Kept, with a null vendor, rather than dropped.
                vendor_id = vendor.vendor_id if vendor else None
            self.repo.upsert_purchase_order(vendor_id, po)
            self.log.supersede("purchase_order", po.external_ref)
            self.log.record(ev.PURCHASE_ORDER_PLACED, po.date,
                       Source("purchase_order", po.external_ref), po)
            self.report.purchase_orders += 1

    def _sync_sales_orders(self) -> None:
        for raw in self.source.list_sales_orders():
            ref = str(raw.get("salesorder_id", "?"))
            try:
                so = normalize_sales_order(raw)
            except NormalizationError as e:
                self.report.skip("sales_order", ref, e.code, e.detail)
                continue
            customer_id = None
            if so.customer_external_id:
                customer = self.repo.get_customer_by_external(so.customer_external_id)
                # An order against a customer the contact pull did not return is
                # still an order — it is a real promise to somebody. Kept with a
                # null customer rather than dropped, the same way a purchase
                # order against an unknown supplier is kept.
                customer_id = customer.customer_id if customer else None
            self.repo.upsert_sales_order(customer_id, so)
            self.log.supersede("sales_order", so.external_ref)
            self.log.record(ev.SALES_ORDER_PLACED, so.date,
                       Source("sales_order", so.external_ref), so)
            self.report.sales_orders += 1

    def _sync_credit_notes(self) -> None:
        """Credit notes and the invoices they were applied to.

        **No event is emitted, and that is deliberate.** Every event this log
        carries is folded into a state, and the only state a credit note could
        plausibly join is ``RECEIVABLES`` — where it would be *wrong*. That fold
        reads ``InvoiceDoc.balance``, which Zoho has already netted applied
        credit out of, so folding these rows in as well would subtract the same
        credit twice and understate what every customer owes. These are stored
        as plain rows, the same way ``StockSnapshot`` is, and read directly by
        whatever needs to reconstruct a past position.
        """
        for raw in self.source.list_credit_notes(skip=self._skipper("creditnote")):
            ref = str(raw.get("creditnote_id", "?"))
            try:
                note, applications = normalize_credit_note(raw)
            except NormalizationError as e:
                self.report.skip("credit_note", ref, e.code, e.detail)
                continue
            customer_id = None
            if note.customer_external_id:
                customer = self.repo.get_customer_by_external(note.customer_external_id)
                # Credit given to a customer the contact pull did not return is
                # still credit given. Kept with a null customer rather than
                # dropped, exactly as a sales order against an unknown customer
                # is kept — dropping it would overstate what that customer owes.
                customer_id = customer.customer_id if customer else None
            row = self.repo.upsert_credit_note(customer_id, note)
            self.s.flush()
            for app in applications:
                self.repo.upsert_credit_note_application(
                    row.credit_note_id, customer_id, app)
            self.report.credit_notes += 1

    def _sync_vendor_payments(self) -> None:
        for raw in self.source.list_vendor_payments(
                skip=self._skipper("vendorpayment")):
            ref = str(raw.get("payment_id", "?"))
            try:
                vp = normalize_vendor_payment(raw)
            except NormalizationError as e:
                self.report.skip("vendor_payment", ref, e.code, e.detail)
                continue
            vendor_id = None
            if vp.vendor_external_id:
                vendor = self.repo.get_vendor_by_external(vp.vendor_external_id)
                vendor_id = vendor.vendor_id if vendor else None
            self.repo.upsert_vendor_payment(vendor_id, vp)
            self.log.supersede("vendor_payment", vp.external_ref)
            self.log.record(ev.PAYMENT_MADE, vp.date,
                       Source("vendor_payment", vp.external_ref,
                              modified_at=str(raw.get("last_modified_time") or "")),
                       vp)
            self.repo.mark_ingested("vendorpayment", ref,
                                    str(raw.get("last_modified_time") or ""))
            self.report.vendor_payments += 1

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

    def _mirror(self, kind: str, doc_type: str) -> None:
        """Retire what we hold and Zoho no longer reports.

        A sync is supposed to leave PIE a mirror of the source, and until now it
        only ever added: a document deleted in Zoho, or voided there, stayed in
        PIE forever and kept counting as revenue, as cost and as a receivable.

        Three guards, because deleting on absence is the one operation here that
        can destroy real history:

        **Only a listing that finished.** ``listing_complete`` is set at the end
        of the generator, so a pull thrown out by the rate limiter half way
        through never reconciles. Otherwise a bad network afternoon would retire
        whatever the pull had not reached yet.

        **Only inside the window the pull actually covered.** The listing is
        bounded by ``date_start``/``date_end``; a document older than the window
        was never looked for, and absence from a search that did not include it
        says nothing at all.

        **Only this connection's documents.** Another connected company's
        invoice is not missing merely because this company's listing did not
        mention it.

        Retirement supersedes the document's events rather than deleting them.
        The log is what everything else is derived from, so one supersede
        removes the document from the read model on the next replay, from every
        folded state on the next build, and from the decisions built on those —
        without a cascade of deletes to keep in step. The read-model rows are
        cleared too, because the screens read those directly.
        """
        source = self.source
        listed = getattr(source, "listed", {}).get(kind)
        if listed is None or kind not in getattr(source, "listing_complete", set()):
            return
        window = self._covered_window()
        if window is None:
            return
        start, end = window

        held = self.repo.ingested_in_window(doc_type, start, end)
        for doc_id in sorted(set(held) - listed):
            self.log.supersede(doc_type, doc_id)
            self.repo.retire_document(doc_type, doc_id)
            self.report.retired.append({"kind": doc_type, "ref": doc_id})

    def _covered_window(self) -> Optional[tuple[date, date]]:
        """The date range this pull's listing actually asked Zoho for."""
        cutoff = getattr(self.source, "_since", None)
        if cutoff is None:
            cutoff = getattr(self.source, "_cutoff", lambda: None)()
        if cutoff is None:
            return None
        until = getattr(self.source, "_until", None) or date.max
        return cutoff, until

    #: Every document kind that costs a detail call, and so is worth listing
    #: incrementally. Named here rather than discovered, because a kind missing
    #: from this list is merely listed the slow way — while a kind wrongly *in*
    #: it would be listed against another kind's high-water mark.
    INCREMENTAL_KINDS = ("invoice", "bill", "customerpayment")

    def arm_incremental_listing(self, source: Optional[ZohoSource] = None) -> dict[str, str]:
        """Tell one window's source where each kind's listing may stop.

        The other half of the resume cursor. ``_skipper`` already saves the
        *detail* call for a document that has not changed; this saves the *list*
        call too, which is the rest of a nightly pull's bill — two years of
        history is one list call per 200 documents, every night, almost all of
        it spent discovering that nothing moved.

        Three modes, and the difference between the last two is the whole reason
        ``incremental`` is not just ``resume``:

        ``resume``  ``incremental``  what it does
        ----------  ---------------  ------------------------------------------
        True        True             Nightly. Skips unchanged details *and*
                                     stops listing at the high-water mark. Does
                                     not see deletions.
        True        False            The reconciliation. Still skips unchanged
                                     details, so it costs list calls only — but
                                     it lists the whole book, which is what lets
                                     the deletion sweep run.
        False       —                Rebuild. Re-fetches every detail.
        """
        source = source if source is not None else self.source
        if not (self.resume and self.incremental):
            return {}
        if not self._window_already_covered(source):
            self.report.windows_listed_in_full += 1
            return {}
        marks = {kind: mark for kind in self.INCREMENTAL_KINDS
                 if (mark := self.repo.ingested_high_water(kind))}
        # A source that has never heard of this is left alone — the fixture
        # source has no listing to short-circuit and no window to speak of.
        if hasattr(source, "modified_since"):
            source.modified_since = dict(marks)
        return marks

    def _window_already_covered(self, source: ZohoSource) -> bool:
        """Whether the high-water mark says anything about *this* window.

        It does not, for a window that has never been listed — and that was a
        silent, total data-loss bug rather than a missed optimisation.

        The mark is ``max(modified_at)`` over everything held, with no notion of
        which window earned it. An incremental listing sorts newest-modified
        first and stops at the mark. So the first time an operator widens the
        window backwards — synced 2025 in January, wants 2024 in August — every
        document in the new months is *older-modified than the mark precisely
        because it is older*, the listing stops on its first row, and the run
        reports success having fetched nothing. Asking again never helps: the
        mark only moves forward.

        The check is per window rather than per run because ``run_documents``
        already gets one source per calendar slice. A pull that widens from 2025
        back to 2024 lists 2024 in full and keeps the short-circuit for every
        month it has covered before — the backfill costs list calls over the new
        months only, and the detail cursor still skips anything already held.

        A window with no lower bound cannot be compared, and is treated as
        uncovered: listing too much is a cost, listing too little is a hole.
        """
        floor = self.repo.covered_since()
        if floor is None:
            return False
        start = getattr(source, "_since", None)
        if start is None:
            start = getattr(source, "_cutoff", lambda: None)()
        return start is not None and start >= floor

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

        # One pass over the item list, two writes. Stock rides on the same
        # payload, and a second pass to collect it would read the whole master
        # list twice per sync — the exact cost `run_reference` exists to avoid.
        # The business's day, not the server's: a sync that runs at 02:00
        # IST would otherwise stamp yesterday onto today's shelf count.
        stock_as_of = _clock_today(self.timezone())

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
            self._record_stock(row, raw, stock_as_of)
            self._link_catalog(row, raw)
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

    def _link_catalog(self, row, raw: dict[str, Any]) -> None:
        """Point this item at its decoded catalogue record, if it has one.

        Derived state, so it is recomputed every sync rather than written once:
        a rebuilt catalogue may resolve a SKU it previously did not, and a link
        nobody re-derives would keep asserting a superseded ``record_id`` under
        a stamp claiming otherwise. Recomputing means a lost match also *clears*
        the link, which is the half that keeps the column honest.

        The exception is a catalogue that is not there at all. Writing NULL then
        would be the lie CLAUDE.md §1 names: "the pack does not cover this item"
        and "nobody asked the pack" are different facts and only the first is
        evidence, so an unavailable catalogue leaves every existing link as it
        was rather than quietly erasing the lot on one bad deployment.
        """
        from ..pie_service import pie_service

        if not pie_service.catalog_available:
            return                          # assert nothing
        record = pie_service.lookup_record(raw.get("sku"))
        if record is None:
            row.pie_record_id = None
            row.pie_link_method = None
            row.pie_catalog_version = None
            return
        row.pie_record_id = str(record.get("record_id"))
        row.pie_link_method = "SKU_EXACT"
        row.pie_catalog_version = pie_service.catalog_version or None
        self.report.catalog_links += 1

    def _sync_invoices(self) -> None:
        for raw in self.source.list_invoices(skip=self._skipper("invoice")):
            ref = str(raw.get("invoice_id", "?"))
            self.log.supersede("invoice", ref)
            # The receivable header first, and before the line check: an invoice
            # with no usable revenue lines is still money owed, and dropping it
            # here would understate receivables by exactly the invoices that are
            # hardest to see. The same ordering, and the same reason, as bills.
            self._record_receivable(raw, ref)
            try:
                lines = normalize_invoice(raw)
            except NormalizationError as e:
                self.report.skip("invoice", ref, e.code, e.detail)
                continue
            by_line = {str(ln.get("line_item_id") or i): ln
                       for i, ln in enumerate(raw.get("line_items") or [])}
            number = str(raw.get("invoice_number") or ref)
            self._read_lines(ev.SALE_LINE_RECORDED, "invoice", ref, raw, lines)
            for t in lines:
                cust = self.repo.get_customer_by_external(t.customer_external_id)
                prod = self.repo.get_product_by_external(t.product_external_id)
                if cust is None:
                    self.report.skip(
                        "sales_txn", t.external_ref, "UNKNOWN_CUSTOMER",
                        f"no customer {t.customer_external_id}",
                        context={
                            "missing_id": t.customer_external_id,
                            "label": str(raw.get("customer_name") or ""),
                            "document": number,
                            "document_date": t.date.isoformat(),
                            "line_value": float(t.line_revenue),
                            "fix": ("This customer is not in the contact list "
                                    "this pull read. If they were merged or "
                                    "deleted in Zoho, the invoice now points at "
                                    "a contact that no longer exists and needs "
                                    "reassigning there."),
                        })
                    continue
                if prod is None:
                    # The line is kept, against a placeholder. Dropping it is
                    # how real revenue went missing: an item deleted in Zoho
                    # still has invoices pointing at it, and skipping those
                    # lines removed the sale from revenue, from the customer's
                    # history and from margin — visible only in a diagnostics
                    # panel nobody reads daily. The sale happened; the item's
                    # name is what we do not know, and that is a much smaller
                    # thing to be missing.
                    line_raw = by_line.get(t.source_ref.line_id or "") or {}
                    prod = self.repo.placeholder_product(
                        t.product_external_id,
                        hint=str(line_raw.get("name") or line_raw.get("description") or ""))
                # Reported on *every* affected line, not only the one that
                # created the placeholder. Keying this off `prod is None` meant
                # the second line naming the same missing item resolved happily
                # against the row the first had just made, so a worklist that
                # exists to say "this is blocking 39 lines worth ₹41.9L" said
                # "1 line, ₹1,07,000" — technically about the same problem and
                # useless for ranking it.
                if (prod.source_ref or {}).get("provisional"):
                    self.report.skip(
                        "sales_txn", t.external_ref, "UNKNOWN_PRODUCT",
                        f"no product {t.product_external_id}",
                        context=self._missing_item_context(
                            t.product_external_id,
                            by_line.get(t.source_ref.line_id or ""),
                            document=number,
                            document_date=t.date.isoformat(),
                            party=str(raw.get("customer_name") or ""),
                            what="invoice"))
                self.repo.upsert_sales_txn(t, cust.customer_id, prod.product_id)
                self.report.sales_txns += 1
                # Which relationships this pull actually moved, so the
                # Customer × Item recompute afterwards is targeted rather than
                # a full rebuild of the organization.
                self.report.touched_customer_ids.add(cust.customer_id)
                self.report.touched_product_ids.add(prod.product_id)
            self._note_owner(raw, lines[0].customer_external_id, lines[0].date)
            self.repo.mark_ingested("invoice", ref, str(raw.get("last_modified_time") or ""))
        # After the loop, so the listing has run to the end and `_mirror` can
        # tell a finished pull from one the rate limiter cut short.
        self._mirror("invoice", "invoice")
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

    @staticmethod
    def _missing_item_context(item_id: str, line: Optional[dict[str, Any]], *,
                              document: str, document_date: str, party: str,
                              what: str) -> dict[str, Any]:
        """Everything needed to find and fix one unresolvable item, in one place.

        The name and SKU come off the *document line*, not the item master —
        the whole problem is that the master has no such item, and the line is
        the only place its name survives. That is what makes the row findable:
        nobody can search Zoho by ``item_id``, but everybody can search by
        "CNMG 120408" or open bill KTI/25-26/0043.
        """
        ln = line or {}
        try:
            value = float(ln.get("item_total") or ln.get("total")
                          or (float(ln.get("quantity") or 0) * float(ln.get("rate") or 0)))
        except (TypeError, ValueError):
            value = 0.0
        return {
            "missing_id": item_id,
            "label": str(ln.get("name") or ln.get("description") or ""),
            "sku": str(ln.get("sku") or ""),
            "document": document,
            "document_date": document_date,
            "party": party,
            "qty": ln.get("quantity"),
            "line_value": value,
            "fix": (
                f"This item is on the {what} but not in the item master this "
                "pull read. **The line is still counted** — it is attached to "
                "a placeholder item, so the money is in revenue, in this "
                "customer's history and in margin, and only the item's name is "
                "missing. It shows as \"Unnamed product\" until the master "
                "has it. To give it a name: if the item exists in Zoho, the "
                "next sync picks it up and fills this in by itself. If it was "
                "deleted there, either restore it or repoint the document at "
                "the item that replaced it."),
        }

    def _sync_bills(self) -> None:
        for raw in self.source.list_bills(skip=self._skipper("bill")):
            ref = str(raw.get("bill_id", "?"))
            # One supersede per document, before anything is recorded from it.
            # A bill produces two kinds of event — the payable and its cost
            # lines — and superseding inside either emitter would retire the
            # other's freshly written events.
            self.log.supersede("bill", ref)
            # The payable header first, and before the line check: a bill with
            # no usable cost lines is still money owed, and dropping it here
            # would understate accounts payable by exactly the bills that are
            # hardest to see.
            self._record_payable(raw, ref)
            try:
                lines = normalize_bill(raw)
            except NormalizationError as e:
                self.report.skip("bill", ref, e.code, e.detail)
                continue
            self._read_lines(ev.COST_LINE_RECORDED, "bill", ref, raw, lines)
            by_line = {str(ln.get("line_item_id") or i): ln
                       for i, ln in enumerate(raw.get("line_items") or [])}
            # Once per bill, not once per line: every line of a bill carries the
            # same header vendor, and resolving inside the loop would repeat the
            # lookup for each of forty lines.
            vendor = (self.repo.get_vendor_by_external(lines[0].vendor_external_id)
                      if lines[0].vendor_external_id else None)
            for r in lines:
                prod = self.repo.get_product_by_external(r.product_external_id)
                if prod is None:
                    # Kept, for the same reason as the sales line — and this
                    # side matters more, not less: bills are where cost comes
                    # from, so a dropped cost line does not leave a hole, it
                    # leaves a *wrong margin* on trade that otherwise looks
                    # complete. A missing number announces itself; a plausible
                    # one computed from half the costs does not.
                    line_raw = by_line.get(r.source_ref.line_id or "") or {}
                    prod = self.repo.placeholder_product(
                        r.product_external_id,
                        hint=str(line_raw.get("name") or line_raw.get("description") or ""))
                # Every affected line, not only the first — see the invoice side.
                if (prod.source_ref or {}).get("provisional"):
                    self.report.skip(
                        "cost_record", r.external_ref, "UNKNOWN_PRODUCT",
                        f"no product {r.product_external_id}",
                        context=self._missing_item_context(
                            r.product_external_id,
                            by_line.get(r.source_ref.line_id or ""),
                            document=str(raw.get("bill_number") or ref),
                            document_date=r.date.isoformat(),
                            party=str(raw.get("vendor_name") or ""),
                            what="bill"))
                self.repo.upsert_cost_record(
                    r, prod.product_id,
                    vendor.vendor_id if vendor is not None else None)
                self.report.cost_records += 1
                # A new cost changes the margin of every customer buying this
                # item, not just the buyer of this bill.
                self.report.touched_product_ids.add(prod.product_id)
            self.repo.mark_ingested("bill", ref, str(raw.get("last_modified_time") or ""))
        self._mirror("bill", "bill")

    def _read_lines(self, event_type: str, doc_type: str, doc_id: str,
                    raw: dict[str, Any], lines: list[Any]) -> None:
        """Record one event per normalised line of a document.

        Recorded before resolution, and regardless of it. An invoice line for a
        retired item is still a sale that happened — the platform simply cannot
        place it yet — and a log that only kept what resolved could never let
        that line reappear when somebody un-retires the item in Zoho. The read
        model's skip and the event stay consistent because both are derived
        from the same DTO.

        Superseding is the caller's, once per document: a bill emits two kinds
        of event, and retiring inside here would take the other kind with it.
        """
        modified_at = str(raw.get("last_modified_time") or "")
        for line in lines:
            self.log.record(
                event_type, line.date,
                Source(doc_type, doc_id, line_id=line.source_ref.line_id,
                       modified_at=modified_at),
                line)

    def _record_payable(self, raw: dict[str, Any], ref: str) -> None:
        """Store what a bill still owes, from the payload the cost pull already
        holds — no extra call, no extra scope.

        Not counted in its own report figure: a payable is not a separate thing
        that was read, it is the header of a bill ``cost_records`` already
        counts. A second counter over the same documents would make the Data
        screen look like the pull did twice the work.
        """
        try:
            terms = normalize_bill_terms(raw)
        except NormalizationError as e:
            self.report.skip("payable", ref, e.code, e.detail)
            return
        vendor_id = None
        if terms.vendor_external_id:
            vendor = self.repo.get_vendor_by_external(terms.vendor_external_id)
            # A bill from a supplier the vendor pull did not return is still
            # owed. Kept with a null vendor rather than dropped — the same
            # choice purchase orders and payments out make.
            vendor_id = vendor.vendor_id if vendor else None
        self.repo.upsert_bill(vendor_id, terms)
        self.log.record(ev.PAYABLE_RECORDED, terms.date,
                   Source("bill", ref,
                          modified_at=str(raw.get("last_modified_time") or "")),
                   terms)

    def _record_receivable(self, raw: dict[str, Any], ref: str) -> None:
        """The invoice header: what this customer owes and by when.

        The mirror of ``_record_payable``, written from the payload the invoice
        pull already holds — no extra call, no extra scope.

        Not counted in its own report figure: a receivable is not a separate
        thing that was read, it is the header of an invoice ``sales_txns``
        already counts. A second counter over the same documents would make the
        Data screen look like the pull did twice the work.
        """
        try:
            terms = normalize_invoice_terms(raw)
        except NormalizationError as e:
            self.report.skip("receivable", ref, e.code, e.detail)
            return
        customer_id = None
        if terms.customer_external_id:
            customer = self.repo.get_customer_by_external(terms.customer_external_id)
            # An invoice to a customer the contact pull did not return is still
            # owed. Kept with a null customer rather than dropped — the same
            # choice bills, purchase orders and payments out make. It cannot be
            # grouped by party, so the receivables fold will skip it, and that
            # is visible as a total that does not reconcile rather than as a
            # silently smaller one.
            customer_id = customer.customer_id if customer else None
        self.repo.upsert_invoice(customer_id, terms)
        self.log.record(ev.RECEIVABLE_RECORDED, terms.date,
                   Source("invoice", ref,
                          modified_at=str(raw.get("last_modified_time") or "")),
                   terms)

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
