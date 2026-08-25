"""Organization-scoped repositories.

Every query is scoped to a single ``organization_id``. This is the enforcement
seam for organization isolation: callers pass their org, and no repository
method can read or write across orgs. There is deliberately **no** cross-org
query surface (V1 is single-org; multi-org is out of scope).

Upserts key on the natural ``(organization_id, external_id/ref)`` so re-running a
sync is idempotent and never duplicates a source record.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import clock
from .domain import models
from .domain.enums import DecisionStatus, HumanAction
from .domain.schemas import (BillIn, CostRecordIn, CreditNoteApplicationIn,
                            CreditNoteIn, CustomerIn, DocumentApplicationIn,
                            InvoiceIn, LocationIn, PaymentReceiptIn, ProductIn,
                            PurchaseOrderIn, QuoteDocIn, SalesOrderIn,
                            SalesTxnIn, StockLocationSnapshotIn,
                            StockSnapshotIn, VendorIn,
                            VendorCreditApplicationIn, VendorCreditIn,
                            VendorPaymentIn)


#: The shape ``clock.utc_stamp`` writes — ``2026-07-02T04:30:00Z``. In SQL LIKE,
#: ``_`` is any single character, which is as precise as portable SQL gets; it
#: is precise enough, because the only writer of a ``Z``-suffixed stamp of this
#: exact width is the rewrite itself. Stamps kept verbatim (unparseable, or
#: carrying no offset) do not match, which is what keeps them out of the
#: high-water max below.
_CANONICAL_STAMP_LIKE = "____-__-__T__:__:__Z"


class ReadModelRepository:
    """Upsert + lookup for the canonical read model, scoped to one org."""

    def __init__(self, session: Session, organization_id: str, *,
                 connector: Optional[str] = None,
                 connection_id: Optional[str] = None,
                 adopt_connectionless: bool = False) -> None:
        self.s = session
        self.org = organization_id
        # Which connected company this repository is writing on behalf of.
        # An imported record's identity is (connector, connection, that
        # system's id) — an external id alone is unique only inside the system
        # that issued it, and two connected companies are two systems.
        #
        # Optional so every existing caller that only *reads* keeps working.
        # A writer that has not said where its rows come from writes NULLs,
        # which read back as "source not recorded" rather than as a guess.
        self.connector = connector
        self.connection_id = connection_id
        # Whether rows this connector wrote before connections existed may be
        # claimed by this one. Only the caller can know: it is unambiguous when
        # the organization has a single connection and a guess the moment it has
        # two, because a connectionless row does not say which book it came
        # from. Off by default — see ``_for_upsert``.
        self.adopt_connectionless = adopt_connectionless

    def _source(self, model) -> list:
        """The clauses that pin a lookup to this repository's own source.

        Written once because it is easy to get subtly wrong per call site: a
        NULL ``connection_id`` has to match a NULL, and ``== None`` does that
        in SQLAlchemy while a Python ``is None`` comparison silently does not.
        """
        return [model.connector == self.connector,
                model.connection_id == self.connection_id]

    def _for_upsert(self, model, external_id: str, *, ref_col: str = "external_id"):
        """The row this pull should write to, adopting an unclaimed one.

        ``ref_col`` is the column holding the source system's reference, and it
        is the only thing that differs between a master and a document: masters
        key on ``external_id``, the fifteen document tables on ``external_ref``.
        Parameterized rather than copied, because the *rule* below — exact
        source first, then two graded kinds of unclaimed, never another
        connection's row — is the thing that must not drift between them. Two
        implementations would be two chances for one side to start adopting
        rows the other would refuse.

        The exact-source match comes first and is unchanged. What is new is the
        second look: a row with the same external id that carries *no*
        provenance at all is unclaimed, and this pull claims it.

        Without that step, provenance can only ever be set at insert — so the
        first pull to run under a new ``(connector, connection)`` pair does not
        recognise anything written under the old one and re-inserts the entire
        master list beside it. That has already happened once in the field: rows
        written before the connector was recorded read "source not recorded",
        and every one of them now has a twin reading "Zoho". Repeating a sync is
        idempotent; it is *changing* the identity of the writer that duplicates,
        and adoption is what makes that change survivable.

        Two grades of unclaimed, and they are not equally safe:

        * **No provenance at all** — neither connector nor connection. Nothing
          contests it, so this pull adopts it unconditionally.
        * **This connector, no connection.** Written by a pull that knew the
          system but not the book. Whether it belongs to *this* connection is
          knowable only from outside: with one connection there is nowhere else
          it could have come from; with two it is a guess, and guessing pools
          two companies' records. Gated on ``adopt_connectionless``, which the
          caller sets only when it has established there is one.

        A row belonging to a *different* connection is never adopted at any
        setting. That is another company's record even when the external id
        matches.
        """
        col = getattr(model, ref_col)
        row = self.s.scalar(
            select(model).where(
                model.organization_id == self.org,
                col == external_id,
                *self._source(model),
            )
        )
        if row is not None or not self.connector:
            return row

        claimable = [model.connector.is_(None)]
        if self.adopt_connectionless:
            claimable.append(model.connector == self.connector)
        unclaimed = self.s.scalar(
            select(model).where(
                model.organization_id == self.org,
                col == external_id,
                model.connection_id.is_(None),
                or_(*claimable),
            )
        )
        if unclaimed is not None:
            unclaimed.connector = self.connector
            unclaimed.connection_id = self.connection_id
        return unclaimed

    # ── customers ────────────────────────────────────────────────────────────
    def upsert_customer(self, c: CustomerIn) -> models.Customer:
        row = self._for_upsert(models.Customer, c.external_id)
        if row is None:
            row = models.Customer(organization_id=self.org, external_id=c.external_id,
                               connector=self.connector,
                               connection_id=self.connection_id)
            self.s.add(row)
        row.name = c.name
        row.status = c.status.value
        row.first_seen = c.first_seen
        if c.assigned_user_id is not None:
            row.assigned_user_id = c.assigned_user_id
        row.source_ref = c.source_ref.model_dump()
        return row

    def get_customer_by_external(self, external_id: str) -> Optional[models.Customer]:
        """Resolve within this repository's own source.

        Scoped deliberately: an invoice from one connected company must resolve
        against that company's customers and not against a same-numbered record
        in another. Falls back to an unsourced match so a pull against rows
        imported before provenance existed still resolves them.
        """
        row = self.s.scalar(
            select(models.Customer).where(
                models.Customer.organization_id == self.org,
                models.Customer.external_id == external_id,
                *self._source(models.Customer),
            )
        )
        if row is not None:
            return row
        # Fall back to a row whose source was never recorded. Rows written
        # before provenance existed are unattributed by definition and nothing
        # can attribute them after the fact, so a pull that now knows its
        # company must still find them — otherwise the first sync after this
        # change orphans every document those rows support.
        return self.s.scalar(
            select(models.Customer).where(
                models.Customer.organization_id == self.org,
                models.Customer.external_id == external_id,
                models.Customer.connection_id.is_(None),
            )
        )

    def list_customers(self) -> Sequence[models.Customer]:
        """Customers of this repository's own source — or of the whole
        organization when the repository has no source to speak of.

        The scope matters to exactly one caller today, and it is the reason it
        exists: ``_sync_assignments`` maps each customer's Zoho salesperson
        onto a platform user via *this book's* user list. Unscoped, a
        three-book organization ran that mapping over every book's customers
        three times — and a salesperson id from one book, looked up in another
        book's users, produced an UNMAPPED_SALESPERSON skip naming an id that
        was never that book's to map. Eight of the ten skips on a real run were
        this: other companies' salespeople, reported as this company's problem.
        """
        clauses = [models.Customer.organization_id == self.org]
        if self.connector:
            clauses.extend(self._source(models.Customer))
        return self.s.scalars(select(models.Customer).where(*clauses)).all()

    # ── products ─────────────────────────────────────────────────────────────
    def upsert_product(self, p: ProductIn) -> models.Product:
        row = self._for_upsert(models.Product, p.external_id)
        if row is None:
            row = models.Product(organization_id=self.org, external_id=p.external_id,
                               connector=self.connector,
                               connection_id=self.connection_id)
            self.s.add(row)
        row.name = p.name
        row.uom = p.uom
        row.hsn = p.hsn
        row.category = p.category
        row.manufacturer = p.manufacturer
        row.active = p.active
        row.source_ref = p.source_ref.model_dump()
        return row

    def placeholder_product(self, external_id: str, *,
                            hint: str = "") -> models.Product:
        """A product row for an item a document references and the master lacks.

        The alternative was dropping the line, and that is how real revenue went
        missing: an item deleted in Zoho still has invoices pointing at it, and
        skipping those lines removed the sale from revenue, from the customer's
        history and from margin — silently, because the only trace was a row in
        a diagnostics panel. On one live book that was 1,256 lines, ₹41.9L on a
        single item.

        A placeholder is not an invention. The item demonstrably exists — a real
        invoice names it — and every field here comes from that document or is
        left empty. What the platform does not know is the item's *name*, and
        ``label_for`` already has a way of saying that: "Unnamed product (id …)".

        ``active=False`` because the master does not list it, which is exactly
        what an item retired in Zoho looks like. ``source_ref.provisional``
        marks the row as standing in for a master entry rather than reflecting
        one, so a screen can say so and the next pull that *does* see the item
        overwrites it in place — same upsert key, no duplicate, and no manual
        repair.
        """
        row = self._for_upsert(models.Product, external_id)
        if row is not None and not (row.source_ref or {}).get("provisional"):
            # Already a real master row. Never downgrade one: a pull that raced
            # the item listing would otherwise blank a name the platform had.
            return row

        provenance = {"provisional": True, "document_description": hint,
                      "reason": ("referenced by a document but absent from the "
                                 "item master this pull read")}
        if row is not None:
            row.source_ref = provenance
            return row

        # Name left empty rather than filled from the document line: the line
        # says what the customer was billed for, which is not reliably the
        # master's name for the item, and `label_for` already renders an empty
        # name as "Unnamed product (id …)". The description travels in
        # `source_ref` where it is labelled as coming from the document.
        row = models.Product(organization_id=self.org, external_id=external_id,
                             connector=self.connector,
                             connection_id=self.connection_id,
                             name="", active=False, source_ref=provenance)
        self.s.add(row)
        # Flushed because the caller needs `product_id` *now* for the line's
        # foreign key, and the primary key is a column default that does not
        # fire until the row reaches the database. One flush per distinct
        # missing item, not per line — the next line naming the same item finds
        # this row.
        self.s.flush()
        return row

    def product_ids_by_external(self) -> dict[str, str]:
        """Every product of *this repository's own source*, keyed by its Zoho id.

        One query rather than a lookup per row: the per-location stock pull asks
        about the whole master, and ``get_product_by_external`` in a loop would
        be one statement per item on an eight-hundred-line catalogue.

        Scoped to the connection, and the scope is the fix for a bug that
        failed a stage on every company at once. This map is what the stock
        pull sends *back to Zoho* as a list of ids to ask about; unscoped, a
        three-book organization asked each book about the other two books'
        items, Zoho answered the first batch containing a foreign id with one
        404 for the whole call, and per-location stock died identically on all
        three connections. An id is unique inside the book that issued it and
        meaningless outside it — the same sentence that scoped
        ``get_product_by_external``, applied to the query that was left behind.

        Placeholder rows are excluded for the same reason in miniature: a
        provisional product is one the master listing did not return, so asking
        Zoho about it by id is asking for the 404 the bisect would then spend
        calls isolating. When the placeholder becomes real, the next master
        pull overwrites it in place and it enters this map by itself.

        Rows with no recorded source are *not* included. They cannot be told
        apart from another book's pre-provenance rows, and the master pull that
        runs before the stock stage adopts this book's own rows anyway — so by
        the time anything reads this map, everything that belongs here carries
        the connection.
        """
        clauses = [
            models.Product.organization_id == self.org,
            models.Product.external_id.is_not(None),
        ]
        if self.connector:
            clauses.extend(self._source(models.Product))
        rows = self.s.execute(
            select(models.Product.external_id, models.Product.product_id,
                   models.Product.source_ref).where(*clauses)
        ).all()
        return {str(external_id): product_id
                for external_id, product_id, source_ref in rows
                if not (source_ref or {}).get("provisional")}

    def get_product_by_external(self, external_id: str) -> Optional[models.Product]:
        """Resolve within this repository's own source.

        Scoped deliberately: an invoice from one connected company must resolve
        against that company's products and not against a same-numbered record
        in another. Falls back to an unsourced match so a pull against rows
        imported before provenance existed still resolves them.
        """
        row = self.s.scalar(
            select(models.Product).where(
                models.Product.organization_id == self.org,
                models.Product.external_id == external_id,
                *self._source(models.Product),
            )
        )
        if row is not None:
            return row
        # Fall back to a row whose source was never recorded. Rows written
        # before provenance existed are unattributed by definition and nothing
        # can attribute them after the fact, so a pull that now knows its
        # company must still find them — otherwise the first sync after this
        # change orphans every document those rows support.
        return self.s.scalar(
            select(models.Product).where(
                models.Product.organization_id == self.org,
                models.Product.external_id == external_id,
                models.Product.connection_id.is_(None),
            )
        )

    # ── sales / cost ─────────────────────────────────────────────────────────
    def upsert_sales_txn(self, t: SalesTxnIn, customer_id: str, product_id: str) -> models.SalesTxn:
        row = self._for_upsert(models.SalesTxn, t.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.SalesTxn(organization_id=self.org, external_ref=t.external_ref,
                                 connector=self.connector,
                                 connection_id=self.connection_id)
            self.s.add(row)
        row.customer_id = customer_id
        row.product_id = product_id
        row.date = t.date
        row.qty = t.qty
        row.unit_price = t.unit_price
        row.line_revenue = t.line_revenue
        row.rate = t.rate
        row.discount_percent = t.discount_percent
        row.source_ref = t.source_ref.model_dump()
        return row

    def upsert_cost_record(self, r: CostRecordIn, product_id: str,
                           vendor_id: Optional[str] = None) -> models.CostRecord:
        row = self._for_upsert(models.CostRecord, r.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.CostRecord(organization_id=self.org, external_ref=r.external_ref,
                                 connector=self.connector,
                                 connection_id=self.connection_id)
            self.s.add(row)
        row.product_id = product_id
        # Resolved by the caller against this repository's own source, the same
        # way every other vendor reference in the sync is. Left as it was when
        # the caller could not resolve one, so a re-sync that *can* fills it in
        # and a pull from a connection with no vendor scope does not blank it.
        if vendor_id is not None:
            row.vendor_id = vendor_id
        row.date = r.date
        row.qty = r.qty
        row.unit_cost = r.unit_cost
        row.rate = r.rate
        row.discount_percent = r.discount_percent
        row.source_ref = r.source_ref.model_dump()
        return row

    def count_cost_records_pending_discount_backfill(self) -> int:
        """Rows synced before the discount-aware fix — ``rate`` is only ever
        null on a legacy row, since every current write sets it. Re-syncing
        with ``full=True`` re-fetches the bill and corrects it."""
        return len(self.s.scalars(
            select(models.CostRecord).where(
                models.CostRecord.organization_id == self.org,
                models.CostRecord.rate.is_(None),
            )).all())

    def count(self, model) -> int:
        return len(self.s.scalars(
            select(model).where(model.organization_id == self.org)).all())

    # ── users (for ownership mapping) ────────────────────────────────────────
    def users_by_email(self) -> dict[str, models.User]:
        return {
            u.email.strip().lower(): u
            for u in self.s.scalars(
                select(models.User).where(models.User.organization_id == self.org))
            if u.email
        }

    # ── resume cursor ────────────────────────────────────────────────────────
    def ingested_index(self, doc_type: str) -> dict[str, str]:
        """``{doc_id: modified_at}`` for documents already pulled *by this
        connection*.

        Scoped to the connection, not just the organization. An external id is
        unique only inside the system that issued it, and this business runs
        three Zoho companies under one PIE organization — so an unscoped cursor
        lets one company's invoice id suppress another company's fetch of a
        completely different document. It also made a full sync of one
        connection wipe the cursor for all of them, which is a re-read of every
        document in every company.
        """
        stmt = select(models.IngestedDocument).where(
            models.IngestedDocument.organization_id == self.org,
            models.IngestedDocument.doc_type == doc_type,
            models.IngestedDocument.connection_id == self.connection_id,
        )
        return {r.doc_id: (r.modified_at or "") for r in self.s.scalars(stmt)}

    def covered_since(self) -> Optional[date]:
        """The earliest document date this connection has ever *listed*.

        The floor of what has been looked at, which is a different question from
        what is held — and the difference is the whole point. ``ingested_*``
        answers "what did we find"; nothing in the rows can answer "where did we
        look and find nothing", because an absent document and an unsearched
        month look identical from the read model. Only the searcher knows, so
        this reads the runs rather than the data.

        **Only runs that finished OK count.** A run that died in its third
        window of twenty covered three months, and reconstructing which three
        from ``windows_done`` would make this a second thing to keep in step
        with the loop that increments it. Forgetting a partial run's coverage
        costs a re-listing — list calls, with the detail cursor still skipping
        everything already held — and never loses a document. The conservative
        direction is the cheap one, so it is the one taken.

        Returns None when this connection has never completed a run, which
        correctly means "nothing has been covered; list everything".
        """
        stmt = select(func.min(models.SyncRun.since)).where(
            models.SyncRun.organization_id == self.org,
            models.SyncRun.connection_id == self.connection_id,
            models.SyncRun.status == "OK",
            models.SyncRun.since.is_not(None),
        )
        return self.s.scalar(stmt)

    def ingested_high_water(self, doc_type: str) -> Optional[str]:
        """The newest modification stamp this connection has already pulled.

        Valid **only inside a window already covered** — see ``covered_since``.
        The mark is the newest modification stamp held, and documents older than
        the window that earned it are older-modified almost by definition, so a
        listing sorted by modification time stops before reaching any of them.
        ``SyncService.arm_incremental_listing`` is what enforces that.

        What an incremental listing stops at. Derived from the rows actually
        held rather than kept as a separate "last synced at" column, and that is
        deliberate: a stored cursor is a second source of truth that can outrun
        the data it claims to describe — a pull that recorded the cursor and then
        died would skip forever the documents it never wrote. This cannot get
        ahead of the rows, because it *is* the rows.

        Returns None when nothing has been pulled yet, which correctly means
        "there is no floor; list everything".

        The max is a *string* max, and that is only a time max because every
        stamp taking part is in the one canonical UTC form ``mark_ingested``
        writes. A stamp kept verbatim (one ``clock.utc_stamp`` could not place)
        is excluded by shape: it still resumes its own document by equality,
        but it may not be the cursor — a verbatim ``+0530`` stamp sorts above
        the same instant written as ``Z``, and a floor that is lexicographic
        junk silently stops the listing in the wrong place. When *nothing* held
        is canonical this returns None, which degrades to a full listing:
        paying list calls is the cost, skipping edits would be the hole.
        """
        stmt = select(func.max(models.IngestedDocument.modified_at)).where(
            models.IngestedDocument.organization_id == self.org,
            models.IngestedDocument.doc_type == doc_type,
            models.IngestedDocument.connection_id == self.connection_id,
            models.IngestedDocument.modified_at.like(_CANONICAL_STAMP_LIKE),
        )
        return self.s.scalar(stmt) or None

    def mark_ingested(self, doc_type: str, doc_id: str, modified_at: str) -> None:
        """Record this document as held, at the stamp we will compare next time.

        ``modified_at`` must be the stamp the *list* endpoint reports, because
        that is what ``ingested_index`` is compared against. Storing the
        detail payload's stamp instead is what made every document look changed
        on every run; ``zoho_client._documents`` now guarantees the two are the
        same string.

        Stored rewritten onto the UTC line (``clock.utc_stamp``) so that
        ``ingested_high_water``'s string max is a time max even when Zoho's
        offset dress varies between rows — and kept **verbatim** when the stamp
        cannot be placed there. Verbatim rather than None, because dropping the
        stamp would make ``already_have`` re-fetch that document on every run
        forever; verbatim rather than a guessed UTC, because a naive stamp's
        offset is evidence we do not have. The comparisons stay honest either
        way: the resume check rewrites its own side identically, and the
        high-water max excludes non-canonical stamps by shape.
        """
        row = self.s.scalar(
            select(models.IngestedDocument).where(
                models.IngestedDocument.organization_id == self.org,
                models.IngestedDocument.doc_type == doc_type,
                models.IngestedDocument.doc_id == doc_id,
                models.IngestedDocument.connection_id == self.connection_id,
            )
        )
        if row is None:
            row = models.IngestedDocument(organization_id=self.org, doc_type=doc_type,
                                          doc_id=doc_id,
                                          connection_id=self.connection_id)
            self.s.add(row)
        row.modified_at = clock.utc_stamp(modified_at) or modified_at or None
        row.fetched_at = datetime.now(timezone.utc)

    def ingested_in_window(self, doc_type: str, start: date,
                           end: date) -> list[str]:
        """Document ids this connection holds whose document date falls inside
        the pull's window.

        Bounded by the *document's own date*, not by when it was fetched: the
        listing this is compared against was bounded that way, and comparing
        against anything else would treat a document the pull never looked for
        as one Zoho has deleted.
        """
        table = _MIRRORED.get(doc_type)
        if table is None:
            return []
        model, ref_col, date_col = table
        rows = self.s.scalars(
            select(getattr(model, ref_col)).where(
                model.organization_id == self.org,
                getattr(model, date_col) >= start,
                getattr(model, date_col) <= end,
                getattr(model, ref_col).in_(self._cursor_refs(doc_type)),
            ))
        return [str(r) for r in rows]

    def _cursor_refs(self, doc_type: str) -> Any:
        """This connection's document ids for one kind, as a subquery.

        The third guard `_mirror` names: *only this connection's documents*.
        The fact tables cannot answer it — they carry a reference and no book —
        so the cursor table does, and it can: `mark_ingested` writes one row per
        document per connection, immediately before each `_mirror` call.

        A connection of ``None`` compares ``IS NULL`` and so matches exactly the
        rows written by a caller that had no connection either, which is what
        keeps a scripted pull behaving as it always has.
        """
        return select(models.IngestedDocument.doc_id).where(
            models.IngestedDocument.organization_id == self.org,
            models.IngestedDocument.connection_id == self.connection_id,
            models.IngestedDocument.doc_type == doc_type,
        )

    def unattributable_in_window(self, doc_type: str, start: date,
                                 end: date) -> int:
        """How many documents in the window record no connection at all.

        Separate from `ingested_in_window` rather than returned beside it: that
        method has one caller and a contract stated in its docstring, and
        widening its return type for a diagnostic would make every reader of
        the sweep learn about a counter to understand the retirement.

        These are not swept. A row whose provenance nobody recorded may belong
        to this company or to one whose rows predate connections, and deleting
        it assumes the friendlier answer — §1's benign default, in the one
        operation here that cannot be undone.
        """
        table = _MIRRORED.get(doc_type)
        if table is None or self.connection_id is None:
            return 0
        model, ref_col, date_col = table
        orphans = select(models.IngestedDocument.doc_id).where(
            models.IngestedDocument.organization_id == self.org,
            models.IngestedDocument.connection_id.is_(None),
            models.IngestedDocument.doc_type == doc_type,
        )
        return int(self.s.scalar(
            select(func.count()).select_from(model).where(
                model.organization_id == self.org,
                getattr(model, date_col) >= start,
                getattr(model, date_col) <= end,
                getattr(model, ref_col).in_(orphans),
            )) or 0)

    def retire_document(self, doc_type: str, doc_id: str) -> int:
        """Remove a document's read-model rows. Returns how many.

        Its events are superseded separately by the caller — that is what makes
        every *derived* thing (states, decisions, metrics) forget it on the next
        build. This clears what the screens read directly.
        """
        removed = 0
        for model, ref_col, prefixed in _RETIRE_FROM.get(doc_type, ()):
            column = getattr(model, ref_col)
            # Line rows are keyed `{doc_id}:{line_id}`; header rows are the id.
            match = (column.startswith(f"{doc_id}:") if prefixed
                     else column == doc_id)
            # Scoped to this connection, which the fact tables can finally
            # express. Until they carried provenance this matched on
            # `(organization_id, external_ref)` alone, so retiring one company's
            # document deleted another's rows for the same reference — the case
            # `_mirror`'s docstring claimed to guard and could not.
            #
            # A row with no provenance is not swept here for the same reason the
            # sweep does not select one: it may belong to a company this pull
            # has never read, and of the two possible mistakes only one is
            # recoverable.
            rows = self.s.scalars(
                select(model).where(model.organization_id == self.org, match,
                                    *self._source(model))).all()
            for row in rows:
                self.s.delete(row)
                removed += 1
        # The cursor goes too, or the next pull believes it still holds it.
        cursor = self.s.scalars(
            select(models.IngestedDocument).where(
                models.IngestedDocument.organization_id == self.org,
                models.IngestedDocument.connection_id == self.connection_id,
                models.IngestedDocument.doc_type == doc_type,
                models.IngestedDocument.doc_id == doc_id)).all()
        for row in cursor:
            self.s.delete(row)
        return removed

    def clear_ingested(self) -> int:
        """Forget this connection's cursor, so its next pull re-fetches
        everything.

        This connection's, not the organization's. A full re-read of one Zoho
        company is a reasonable thing to ask for; making the other two companies
        re-read their entire history as a side effect is not, and that is what
        an organization-wide clear did.
        """
        rows = self.s.scalars(
            select(models.IngestedDocument).where(
                models.IngestedDocument.organization_id == self.org,
                models.IngestedDocument.connection_id == self.connection_id)).all()
        for r in rows:
            self.s.delete(r)
        return len(rows)


    # ── supply, stock and cash ───────────────────────────────────────────────
    #
    # Same upsert-by-(org, external ref) shape as everything above. Keyed on
    # Zoho's own ids so a re-pull of the same window updates rather than
    # duplicating — which is what makes a resumed or overlapping sync safe.

    def upsert_vendor(self, v: VendorIn) -> models.Vendor:
        row = self.s.scalar(
            select(models.Vendor).where(
                models.Vendor.organization_id == self.org,
                models.Vendor.external_id == v.external_id,
                *self._source(models.Vendor),
            )
        )
        if row is None:
            row = models.Vendor(organization_id=self.org, external_id=v.external_id,
                               connector=self.connector,
                               connection_id=self.connection_id)
            self.s.add(row)
        row.name = v.name
        row.gstin = v.gstin
        row.pan = v.pan
        row.payment_terms_days = v.payment_terms_days
        row.status = v.status.value
        row.source_ref = v.source_ref.model_dump()
        return row

    def get_vendor_by_external(self, external_id: str) -> Optional[models.Vendor]:
        """Resolve within this repository's own source.

        Scoped deliberately: an invoice from one connected company must resolve
        against that company's vendors and not against a same-numbered record
        in another. Falls back to an unsourced match so a pull against rows
        imported before provenance existed still resolves them.
        """
        row = self.s.scalar(
            select(models.Vendor).where(
                models.Vendor.organization_id == self.org,
                models.Vendor.external_id == external_id,
                *self._source(models.Vendor),
            )
        )
        if row is not None:
            return row
        # Fall back to a row whose source was never recorded. Rows written
        # before provenance existed are unattributed by definition and nothing
        # can attribute them after the fact, so a pull that now knows its
        # company must still find them — otherwise the first sync after this
        # change orphans every document those rows support.
        return self.s.scalar(
            select(models.Vendor).where(
                models.Vendor.organization_id == self.org,
                models.Vendor.external_id == external_id,
                models.Vendor.connection_id.is_(None),
            )
        )

    def upsert_stock_snapshot(self, product_id: str,
                              snap: StockSnapshotIn) -> models.StockSnapshot:
        """One row per item per day.

        Upserted rather than appended: a sync run twice in one afternoon is a
        correction, not two observations, and two rows for one day would make
        every later average silently weight that day double.
        """
        row = self.s.scalar(
            select(models.StockSnapshot).where(
                models.StockSnapshot.organization_id == self.org,
                models.StockSnapshot.product_id == product_id,
                models.StockSnapshot.as_of == snap.as_of,
            )
        )
        if row is None:
            row = models.StockSnapshot(organization_id=self.org, product_id=product_id,
                                       as_of=snap.as_of)
            self.s.add(row)
        row.on_hand = snap.on_hand
        row.available = snap.available
        row.actual_available = snap.actual_available
        row.reorder_level = snap.reorder_level
        row.purchase_rate = snap.purchase_rate
        row.tracked = snap.tracked
        row.source_ref = snap.source_ref.model_dump()
        return row

    def upsert_payment(self, customer_id: str,
                       p: PaymentReceiptIn) -> models.PaymentReceipt:
        row = self._for_upsert(models.PaymentReceipt, p.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.PaymentReceipt(organization_id=self.org,
                                        external_ref=p.external_ref,
                                        connector=self.connector,
                                        connection_id=self.connection_id)
            self.s.add(row)
        row.customer_id = customer_id
        row.date = p.date
        row.amount = p.amount
        row.mode = p.mode
        row.is_advance = p.is_advance
        row.unapplied_amount = p.unapplied_amount
        row.source_ref = p.source_ref.model_dump()
        self.s.flush()

        def assign(app_row: models.PaymentApplication,
                   a: DocumentApplicationIn) -> None:
            app_row.customer_id = customer_id
            app_row.invoice_external_ref = a.document_external_ref
            app_row.invoice_number = a.document_number
            app_row.invoice_date = a.document_date
            app_row.invoice_due_date = a.document_due_date

        self._replace_applications(
            models.PaymentApplication,
            models.PaymentApplication.payment_receipt_id, row.payment_receipt_id,
            p.applications, paid_on=p.date,
            source_ref=p.source_ref.model_dump(), assign=assign)
        return row

    def _replace_applications(self, model: Any, parent_column: Any,
                              parent_id: str, rows: list[DocumentApplicationIn],
                              *, paid_on: date, source_ref: dict[str, Any],
                              assign: Any) -> None:
        """Write one payment's applications, wholesale.

        Replaced rather than merged: a payment re-applied in Zoho can drop a
        document, and a merge would leave the old application behind as a
        settlement that no longer exists — which is a phantom observation in
        exactly the series that decides how fast a party is thought to pay.

        Generic over the two application tables because the lifecycle is the
        rule, not the columns: the columns differ (``invoice_*`` against
        ``bill_*``) and are written by ``assign``, but "replace, do not merge"
        must be one decision. Two copies of this loop would be two chances for
        one side to start merging quietly.
        """
        existing = {
            a.external_ref: a
            for a in self.s.scalars(
                select(model).where(model.organization_id == self.org,
                                    parent_column == parent_id)).all()
        }
        seen: set[str] = set()
        for a in rows:
            seen.add(a.external_ref)
            app_row = existing.get(a.external_ref)
            if app_row is None:
                app_row = model(organization_id=self.org,
                                external_ref=a.external_ref,
                                connector=self.connector,
                                connection_id=self.connection_id,
                                **{parent_column.key: parent_id})
                self.s.add(app_row)
            assign(app_row, a)
            app_row.paid_on = paid_on
            app_row.amount_applied = a.amount_applied
            app_row.source_ref = source_ref
        for ref, stale in existing.items():
            if ref not in seen:
                self.s.delete(stale)

    def upsert_sales_order(self, customer_id: Optional[str],
                           so: SalesOrderIn) -> models.SalesOrderDoc:
        """One customer order, keyed on the id its ERP gave it.

        Keyed on ``external_ref`` alone rather than on the source triple, like
        every other *document* here — documents already carry a globally unique
        id from their own system and are scoped by the connection that fetched
        them. The source triple matters for masters (customers, items, vendors),
        where two connected companies genuinely number from one.
        """
        row = self._for_upsert(models.SalesOrderDoc, so.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.SalesOrderDoc(organization_id=self.org,
                                       external_ref=so.external_ref,
                                       connector=self.connector,
                                       connection_id=self.connection_id)
            self.s.add(row)
        row.number = so.number
        row.customer_id = customer_id
        row.date = so.date
        row.expected_ship_date = so.expected_ship_date
        row.status = so.status
        row.invoiced_status = so.invoiced_status
        row.shipped_status = so.shipped_status
        row.total = so.total
        row.salesperson_external_id = so.salesperson_external_id
        row.source_ref = so.source_ref.model_dump()
        return row

    def upsert_quote_document(self, customer_id: Optional[str],
                              q: QuoteDocIn) -> models.QuoteDoc:
        """One quote as its ERP raised it, keyed on the id that ERP gave it.

        Keyed on ``external_ref`` alone through ``_for_upsert``, like every
        other *document* here and for the reason ``upsert_sales_order`` states:
        a document already carries a globally unique id from its own system.

        **Every column is assigned unconditionally**, including the ones that
        arrive as ``None``. Not an oversight and not tidiness — a partial write
        (``if q.expires_on is not None: row.expires_on = ...``) would make the
        table look like somewhere a value can be kept, and the first person to
        notice that would put a loss reason on it. Rewriting the whole row from
        the payload every pull is what makes it obviously derived, which is what
        keeps human facts on ``quote_outcomes`` where a re-sync cannot reach
        them.

        It also has to be a rewrite for a duller reason: ``source_status`` and
        ``outcome`` are exactly the columns that change after the quote is
        raised. A row written once and never revisited would report every
        accepted and every declined quote as still open, which is the same bug
        ``upsert_invoice`` describes for a settled invoice.

        ``customer_id`` may be ``None``, and the row is kept anyway: a quote to a
        customer the contact pull did not return is still a quote, and dropping
        it would silently shrink the denominator of every win rate. The ERP's own
        ``customer_ref`` is stored beside it so the row is still nameable.
        """
        row = self._for_upsert(models.QuoteDoc, q.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.QuoteDoc(organization_id=self.org,
                                  external_ref=q.external_ref,
                                  connector=self.connector,
                                  connection_id=self.connection_id)
            self.s.add(row)
        row.number = q.number
        row.source_reference = q.source_reference
        row.customer_id = customer_id
        row.customer_ref = q.customer_ref
        row.date = q.date
        row.expires_on = q.expires_on
        row.source_status = q.source_status
        row.outcome = q.outcome.value
        row.decided_on = q.decided_on
        row.total = q.total
        row.salesperson_external_id = q.salesperson_external_id
        row.client_viewed_at = q.client_viewed_at
        row.attributes = dict(q.attributes)
        row.source_ref = q.source_ref.model_dump()
        return row

    def upsert_bill(self, vendor_id: Optional[str], b: BillIn) -> models.BillDoc:
        """The payable header. Re-read on every pull that touches the bill,
        because ``status`` and ``balance`` change as it is paid — a bill row
        written once and never revisited would report every settled bill as
        still outstanding."""
        row = self._for_upsert(models.BillDoc, b.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.BillDoc(organization_id=self.org,
                                 external_ref=b.external_ref,
                                 connector=self.connector,
                                 connection_id=self.connection_id)
            self.s.add(row)
        row.number = b.number
        row.vendor_id = vendor_id
        row.date = b.date
        row.due_date = b.due_date
        row.status = b.status
        row.total = b.total
        row.balance = b.balance
        row.source_ref = b.source_ref.model_dump()
        return row

    def upsert_invoice(self, customer_id: Optional[str],
                       inv: InvoiceIn) -> models.InvoiceDoc:
        """The receivable header. Re-read on every pull that touches the
        invoice, because ``status`` and ``balance`` change as it is collected —
        an invoice row written once and never revisited would report every
        settled invoice as still outstanding, which is how a customer who paid
        on time ends up on a collections list."""
        row = self._for_upsert(models.InvoiceDoc, inv.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.InvoiceDoc(organization_id=self.org,
                                    external_ref=inv.external_ref,
                                    connector=self.connector,
                                    connection_id=self.connection_id)
            self.s.add(row)
        row.number = inv.number
        row.customer_id = customer_id
        row.date = inv.date
        row.due_date = inv.due_date
        row.status = inv.status
        row.total = inv.total
        row.balance = inv.balance
        row.source_ref = inv.source_ref.model_dump()
        self._replace_invoice_sales_orders(inv)
        return row

    def _replace_invoice_sales_orders(self, inv: InvoiceIn) -> None:
        """Write this invoice's order links, wholesale.

        Replaced rather than merged, for the same reason ``_replace_applications``
        gives about payments: an invoice re-linked in Zoho can drop an order, and
        a merge would leave the old link behind as an order this invoice no
        longer bills against — a phantom in exactly the series that decides how
        long an order takes to be invoiced.

        Not folded into ``_replace_applications`` despite the shared rule. That
        helper is generic over the two *application* tables, which share a parent
        id, an ``external_ref``, a ``paid_on`` and an ``amount_applied``; a link
        row has none of those four. Parameterising it far enough to cover both
        would leave a helper whose every column is a callback, which is the
        abstraction redundancy in ``CLAUDE.md`` §2 rather than a fix for it.
        """
        existing = {
            row.sales_order_external_ref: row
            for row in self.s.scalars(
                select(models.InvoiceSalesOrderLink).where(
                    models.InvoiceSalesOrderLink.organization_id == self.org,
                    models.InvoiceSalesOrderLink.invoice_external_ref
                    == inv.external_ref)).all()
        }
        source_ref = inv.source_ref.model_dump()
        seen: set[str] = set()
        for ref in inv.sales_orders:
            seen.add(ref.external_ref)
            link = existing.get(ref.external_ref)
            if link is None:
                link = models.InvoiceSalesOrderLink(
                    organization_id=self.org,
                    invoice_external_ref=inv.external_ref,
                    sales_order_external_ref=ref.external_ref,
                    connector=self.connector,
                    connection_id=self.connection_id)
                self.s.add(link)
            link.sales_order_number = ref.number
            link.is_primary = ref.is_primary
            link.source_ref = source_ref
        for ref_id, stale in existing.items():
            if ref_id not in seen:
                self.s.delete(stale)

    def upsert_location(self, loc: LocationIn) -> models.Location:
        """Where the business trades from. Re-read every pull: a branch can be
        deactivated, renamed or re-parented, and a row written once would keep
        a closed location in every branch total."""
        row = self._for_upsert(models.Location, loc.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.Location(organization_id=self.org,
                                  external_ref=loc.external_ref,
                                  connector=self.connector,
                                  connection_id=self.connection_id)
            self.s.add(row)
        row.name = loc.name
        row.kind = loc.kind
        row.parent_external_ref = loc.parent_external_ref
        row.is_active = loc.is_active
        row.is_primary = loc.is_primary
        row.tax_reg_no = loc.tax_reg_no
        row.source_ref = loc.source_ref.model_dump()
        return row

    def upsert_stock_location_snapshot(
        self, product_id: str, snap: StockLocationSnapshotIn,
    ) -> models.StockLocationSnapshot:
        """One item at one location on one day. Upserted on the day, so a second
        sync in the same day corrects the reading rather than adding a row that
        would double the location's holding."""
        row = self.s.scalar(
            select(models.StockLocationSnapshot).where(
                models.StockLocationSnapshot.organization_id == self.org,
                models.StockLocationSnapshot.product_id == product_id,
                models.StockLocationSnapshot.location_external_ref
                == snap.location_external_ref,
                models.StockLocationSnapshot.as_of == snap.as_of,
            )
        )
        if row is None:
            row = models.StockLocationSnapshot(
                organization_id=self.org, product_id=product_id,
                location_external_ref=snap.location_external_ref, as_of=snap.as_of,
                connector=self.connector, connection_id=self.connection_id)
            self.s.add(row)
        row.on_hand = snap.on_hand
        row.available = snap.available
        row.asset_value = snap.asset_value
        row.source_ref = snap.source_ref.model_dump()
        return row

    def upsert_credit_note(self, customer_id: Optional[str],
                           note: CreditNoteIn) -> models.CreditNoteDoc:
        """The credit-note header. Re-read on every pull that touches it, for
        the same reason as an invoice: ``status`` and ``balance`` move as the
        credit is applied or refunded, and a row written once would keep
        reporting credit as available long after it was spent."""
        row = self._for_upsert(models.CreditNoteDoc, note.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.CreditNoteDoc(organization_id=self.org,
                                       external_ref=note.external_ref,
                                       connector=self.connector,
                                       connection_id=self.connection_id)
            self.s.add(row)
        row.number = note.number
        row.customer_id = customer_id
        row.date = note.date
        row.status = note.status
        row.total = note.total
        row.balance = note.balance
        row.source_ref = note.source_ref.model_dump()
        return row

    def upsert_credit_note_application(
        self, credit_note_id: str, customer_id: Optional[str],
        app: CreditNoteApplicationIn,
    ) -> models.CreditNoteApplication:
        """One credit note set against one invoice."""
        row = self._for_upsert(models.CreditNoteApplication, app.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.CreditNoteApplication(organization_id=self.org,
                                               external_ref=app.external_ref,
                                               connector=self.connector,
                                               connection_id=self.connection_id)
            self.s.add(row)
        row.credit_note_id = credit_note_id
        row.customer_id = customer_id
        row.invoice_external_ref = app.invoice_external_ref
        row.invoice_number = app.invoice_number
        row.invoice_date = app.invoice_date
        row.applied_on = app.applied_on
        row.amount_applied = app.amount_applied
        row.source_ref = app.source_ref.model_dump()
        return row

    def upsert_vendor_credit(self, vendor_id: Optional[str],
                             vc: VendorCreditIn) -> models.VendorCreditDoc:
        """The vendor-credit header. Re-read on every pull that touches it, the
        same as a customer credit note: ``status`` and ``balance`` move as the
        credit is set against bills or refunded, and a row written once would
        keep reporting credit as available long after it was spent."""
        row = self._for_upsert(models.VendorCreditDoc, vc.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.VendorCreditDoc(organization_id=self.org,
                                         external_ref=vc.external_ref,
                                         connector=self.connector,
                                         connection_id=self.connection_id)
            self.s.add(row)
        row.number = vc.number
        row.vendor_id = vendor_id
        row.date = vc.date
        row.status = vc.status
        row.total = vc.total
        row.balance = vc.balance
        row.source_ref = vc.source_ref.model_dump()
        return row

    def upsert_vendor_credit_application(
        self, vendor_credit_id: str, vendor_id: Optional[str],
        app: VendorCreditApplicationIn,
    ) -> models.VendorCreditApplication:
        """One vendor credit set against one bill."""
        row = self._for_upsert(models.VendorCreditApplication, app.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.VendorCreditApplication(
                organization_id=self.org, external_ref=app.external_ref,
                connector=self.connector, connection_id=self.connection_id)
            self.s.add(row)
        row.vendor_credit_id = vendor_credit_id
        row.vendor_id = vendor_id
        row.bill_external_ref = app.bill_external_ref
        row.bill_number = app.bill_number
        row.amount_applied = app.amount_applied
        row.source_ref = app.source_ref.model_dump()
        return row

    def upsert_vendor_payment(self, vendor_id: Optional[str],
                              vp: VendorPaymentIn) -> models.VendorPaymentDoc:
        row = self._for_upsert(models.VendorPaymentDoc, vp.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.VendorPaymentDoc(organization_id=self.org,
                                          external_ref=vp.external_ref,
                                          connector=self.connector,
                                          connection_id=self.connection_id)
            self.s.add(row)
        row.vendor_id = vendor_id
        row.date = vp.date
        row.amount = vp.amount
        row.mode = vp.mode
        row.reference = vp.reference
        row.source_ref = vp.source_ref.model_dump()
        self.s.flush()

        def assign(app_row: models.BillPaymentApplication,
                   a: DocumentApplicationIn) -> None:
            # Nullable, unlike the receivable side: a payment we made is a fact
            # about our own bank account whether or not the supplier resolved.
            app_row.vendor_id = vendor_id
            app_row.bill_external_ref = a.document_external_ref
            app_row.bill_number = a.document_number
            app_row.bill_date = a.document_date
            app_row.bill_due_date = a.document_due_date

        self._replace_applications(
            models.BillPaymentApplication,
            models.BillPaymentApplication.vendor_payment_id, row.vendor_payment_id,
            vp.applications, paid_on=vp.date,
            source_ref=vp.source_ref.model_dump(), assign=assign)
        return row

    def upsert_purchase_order(self, vendor_id: Optional[str],
                              po: PurchaseOrderIn) -> models.PurchaseOrderDoc:
        row = self._for_upsert(models.PurchaseOrderDoc, po.external_ref,
                               ref_col="external_ref")
        if row is None:
            row = models.PurchaseOrderDoc(organization_id=self.org,
                                          external_ref=po.external_ref,
                                          connector=self.connector,
                                          connection_id=self.connection_id)
            self.s.add(row)
        row.number = po.number
        row.vendor_id = vendor_id
        row.date = po.date
        row.expected_date = po.expected_date
        row.status = po.status
        row.received_status = po.received_status
        row.ordered_qty = po.ordered_qty
        row.pending_qty = po.pending_qty
        row.total = po.total
        row.received_on = po.received_on
        row.source_ref = po.source_ref.model_dump()
        return row


class DecisionRepository:
    """Persistence + lifecycle for decisions, scoped to one org."""

    def __init__(self, session: Session, organization_id: str) -> None:
        self.s = session
        self.org = organization_id

    def get(self, decision_id: str) -> Optional[models.Decision]:
        return self.s.scalar(
            select(models.Decision).where(
                models.Decision.organization_id == self.org,
                models.Decision.decision_id == decision_id,
            )
        )

    def get_by_key(self, decision_key: str) -> Optional[models.Decision]:
        return self.s.scalar(
            select(models.Decision).where(
                models.Decision.organization_id == self.org,
                models.Decision.decision_key == decision_key,
            )
        )

    def add(self, decision: models.Decision) -> models.Decision:
        assert decision.organization_id == self.org, "cross-org write blocked"
        self.s.add(decision)
        return decision

    def since(self, cutoff: datetime) -> Sequence[models.Decision]:
        """Decisions opened on or after ``cutoff``.

        On ``created_at``, which is when this card was opened. ``detected_at`` is
        copied from the signal and rewritten every time the card is refreshed, so
        a window built on it would move a decision forward in time whenever the
        detectors ran again — and the outcome report would count the same card in
        two different windows.
        """
        return self.s.scalars(
            select(models.Decision).where(
                models.Decision.organization_id == self.org,
                models.Decision.created_at >= cutoff,
            )
        ).all()

    def list(
        self,
        *,
        assigned_user_id: Optional[str] = None,
        exclude_types: Sequence[str] = (),
        decision_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Sequence[models.Decision]:
        """Scoped list. ``assigned_user_id`` restricts to a salesperson's decisions;
        ``exclude_types`` removes restricted types from a salesperson's view."""
        stmt = select(models.Decision).where(models.Decision.organization_id == self.org)
        if assigned_user_id is not None:
            stmt = stmt.where(models.Decision.assigned_user_id == assigned_user_id)
        if exclude_types:
            stmt = stmt.where(models.Decision.decision_type.notin_(list(exclude_types)))
        if decision_type is not None:
            stmt = stmt.where(models.Decision.decision_type == decision_type)
        if status is not None:
            stmt = stmt.where(models.Decision.status == status)
        # Ordered on the *deterministic* base, not the final score.
        #
        # A signal decision's score carries an AI adjustment clamped to ±20; a
        # state decision's is its quantified money and carries none. Ranking
        # one against the other on ``priority_score`` would let a model's nudge
        # outrank ₹4 lakh of locked capital, and would mean "priority 72" said
        # two different things depending on which producer made the row. The
        # adjustment is still stored and still shown on the card — it just no
        # longer decides who is read first.
        stmt = stmt.order_by(models.Decision.priority_deterministic_base.desc(),
                             models.Decision.detected_at.desc())
        return self.s.scalars(stmt).all()

    def _actor_name(self, user_id: Optional[str]) -> Optional[str]:
        """This user's name, or None if there is nobody to name."""
        if not user_id:
            return None
        row = self.s.get(models.User, user_id)
        return row.name if row is not None else None

    def record_human_action(
        self, decision: models.Decision, action: HumanAction, actor_user_id: str,
        note: Optional[str] = None, reason: Optional[str] = None,
    ) -> models.Decision:
        """Apply a human action and advance the lifecycle (human-driven transitions only).

        Detector-driven transitions (RESOLVED / SUPERSEDED / EXPIRED) are out of
        Phase 1 scope and are not performed here.
        """
        now = datetime.now(timezone.utc)
        entry = {
            "action": action.value, "actor_user_id": actor_user_id,
            # The name as it was, recorded rather than resolved on read. An
            # audit entry should say who this was at the time: resolving the id
            # later would rename a past action if somebody's name changed, and
            # would show nothing at all once an account is removed. One query per
            # human click is a fair price for that.
            "actor_name": self._actor_name(actor_user_id),
            "acted_at": now.isoformat(), "note": note,
        }
        # Append, never replace. This used to assign, so only the last action
        # survived: an owner who accepted a decision with their reasoning on it
        # and then pressed Undo had that reasoning destroyed and replaced with
        # "Undone by the user". `api.ts` documents the opposite in as many words
        # — "the reopen is itself recorded, so the audit trail keeps both the
        # action and its reversal" — and `approval_requests.thread` is the
        # pattern that already does it correctly.
        #
        # The trail is nested inside the same JSON column rather than given a
        # column of its own, and the latest action stays denormalised at the top
        # level. That keeps every existing reader — the API, both renderers, the
        # schema — working on the field they already read, which is "what
        # happened last" and is what a queue row wants. The cost is that the
        # trail is not queryable in SQL; it is only ever read one decision at a
        # time, so that buys nothing we need.
        previous = decision.human_action or {}
        trail = list(previous.get("trail") or [])
        if previous and not trail:
            # A row written before the trail existed. Seed it from what it holds
            # so the first append does not silently lose the action already
            # there — a backfill on touch, rather than a data migration for a
            # column that is schemaless anyway.
            trail.append({k: v for k, v in previous.items() if k != "trail"})
        trail.append(entry)
        decision.human_action = {**entry, "trail": trail}
        if action is HumanAction.VIEW:
            if decision.status == DecisionStatus.OPEN.value:
                decision.status = DecisionStatus.VIEWED.value
        elif action is HumanAction.ACT:
            decision.status = DecisionStatus.ACTIONED.value
            # Snapshot-on-accept: freeze the signal's own evidence as the
            # baseline the Outcome Tracker will later measure against. Here
            # rather than in the router because this method *is* the decision
            # lifecycle — every acceptance path flows through it or through
            # `approvals._settle_escalated_decision`, and both call the one
            # capture function. Function-level import: this module is imported
            # before `commercial/` in several chains, and capture is only
            # needed on the one transition.
            from .commercial.outcome_tracker import capture_on_accept

            capture_on_accept(self.s, decision,
                              accepted_by_user_id=actor_user_id)
        elif action is HumanAction.DISMISS:
            decision.status = DecisionStatus.DISMISSED.value
            decision.override_reason = reason
        elif action is HumanAction.OVERRIDE:
            decision.status = DecisionStatus.OVERRIDDEN.value
            decision.override_reason = reason
        elif action is HumanAction.ESCALATE:
            # Parked, not closed. The approval request raised alongside this is
            # what actually routes it; settling that request moves the decision
            # on (see approvals._settle_escalated_decision).
            decision.status = DecisionStatus.ESCALATED.value
        elif action is HumanAction.SNOOZE:
            pass  # snooze keeps status; scheduling deferred to the outcome phase
        elif action is HumanAction.REOPEN:
            # Undo: return the decision to the queue and clear the reason that
            # closed it. The REOPEN joins the trail above, so what is recorded is
            # both the earlier call *and* its reversal — which is what this
            # comment claimed before the trail existed to make it true.
            decision.status = DecisionStatus.OPEN.value
            decision.override_reason = None
        return decision


class AiTelemetryRepository:
    """AI call telemetry, scoped to one org (WS3).

    Writing is best-effort by design: observability must never be able to fail a
    decision. Reads power the owner-scoped ops metrics endpoint.
    """

    def __init__(self, session: Session, organization_id: str) -> None:
        self.s = session
        self.org = organization_id

    def record(self, tel, *, cache_hit: bool = False) -> Optional[models.AiCallLog]:
        """Persist one telemetry record. Returns None when disabled."""
        from .config import settings

        if not settings.AI_TELEMETRY_ENABLED or tel is None:
            return None
        row = models.AiCallLog(
            organization_id=self.org,
            decision_type=tel.decision_type or "",
            subject_entity_id=tel.subject_entity_id,
            recipient_role=tel.recipient_role,
            provider=tel.provider or "", model=tel.model or "",
            prompt_version=tel.prompt_version or "",
            context_hash=tel.context_hash or "",
            ai_status=tel.ai_status or "",
            provider_called=bool(tel.provider_called),
            cache_hit=bool(cache_hit or tel.cache_hit),
            attempts=int(tel.attempts or 0),
            latency_ms=tel.latency_ms,
            input_tokens=tel.input_tokens, output_tokens=tel.output_tokens,
            estimated_cost_usd=tel.estimated_cost_usd,
            failure_reason=tel.failure_reason,
            corrections=list(tel.corrections or []),
        )
        self.s.add(row)
        return row

    def since(self, cutoff: datetime) -> Sequence[models.AiCallLog]:
        return self.s.scalars(
            select(models.AiCallLog).where(
                models.AiCallLog.organization_id == self.org,
                models.AiCallLog.created_at >= cutoff,
            )
        ).all()


class SignalRepository:
    """Write-once signal persistence, scoped to one org."""

    def __init__(self, session: Session, organization_id: str) -> None:
        self.s = session
        self.org = organization_id

    def add(self, signal: models.Signal) -> models.Signal:
        assert signal.organization_id == self.org, "cross-org write blocked"
        self.s.add(signal)
        return signal

    def since(self, cutoff: datetime) -> Sequence[models.Signal]:
        """Signals written on or after ``cutoff``.

        On ``created_at`` rather than ``detected_at``: this answers "what did the
        engine emit in this window", and ``detected_at`` is the reference date of
        the trade behind the signal, which can be older than the run that found it.
        """
        return self.s.scalars(
            select(models.Signal).where(
                models.Signal.organization_id == self.org,
                models.Signal.created_at >= cutoff,
            )
        ).all()

    def get(self, signal_id: str) -> Optional[models.Signal]:
        return self.s.scalar(
            select(models.Signal).where(
                models.Signal.organization_id == self.org,
                models.Signal.signal_id == signal_id,
            )
        )


#: Which table answers "what do we hold for this document kind, and when was
#: it dated". Only kinds listed here are ever reconciled against the source —
#: a kind with no entry is simply never retired, which is the safe default.
_MIRRORED: dict[str, tuple[Any, str, str]] = {
    "invoice": (models.InvoiceDoc, "external_ref", "date"),
    "bill": (models.BillDoc, "external_ref", "date"),
}

#: What to delete when a document is retired. The boolean says whether the
#: reference is a line key (``{doc_id}:{line_id}``) or the document id itself.
#:
#: Both the header and its lines, because the screens read the lines directly
#: and a header removed without them would leave revenue with nothing to
#: attribute it to.
_RETIRE_FROM: dict[str, tuple[tuple[Any, str, bool], ...]] = {
    "invoice": ((models.SalesTxn, "external_ref", True),
                (models.InvoiceDoc, "external_ref", False)),
    "bill": ((models.CostRecord, "external_ref", True),
             (models.BillDoc, "external_ref", False)),
}
