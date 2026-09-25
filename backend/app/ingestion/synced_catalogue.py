"""A source catalogue read from the synced item master, for the connectors
that are pulled on a schedule rather than read live.

Business Central, Acumatica, NetSuite, Prophet 21 and Sage have no live
item-lookup call in this platform. Until now a quote for one of their customers
got ``UnavailableZoho`` — every line BOOKS OFFLINE, with a sentence saying the
book is synced rather than read live — and the send still worked because the
writer is a separate object. That was honest about *live* prices and useless
about everything else: the synced master already knows whether an item exists
in that book, what its id there is, and what stock the last pull saw, and
refusing to say so made every line on a Business Central quote look like a
line the platform had never heard of.

**What this answers, and what it refuses to.** ``get_item`` reads
``item_connector_records`` for the one connected company the quote belongs to
— the same table ``repositories.search_items`` searches — and answers:

* ``in_books`` and ``item_id`` — whether the code names an item in that
  company's master and the source system's own id for it, which is what the
  writer needs on the line to create the quote there.
* ``stock`` — ``available`` from the latest stock snapshot for the product, or
  ``None`` where no snapshot exists. Never ``on_hand`` in its place: what can be
  promised is the question a quote asks, and on-hand includes what is already
  promised to somebody else.
* ``cost`` — the latest snapshot's ``purchase_rate`` where the source carried
  one, else ``None``. It arrives through the same field the Zoho adapter fills
  and is withheld from a salesperson by the same projection.
* ``list_price`` — **always ``None``.** The synced master carries no selling
  price: neither ``products`` nor ``item_connector_records`` has a column for
  one, and no connector reads one. So no line auto-quotes at list on these
  books and every one reads NO PRICE until a person prices it. That is the
  truth about what was synced, and an invented list price — say, the last
  invoice rate — would be exactly the number a desk quotes without looking.
* ``as_of`` — when this answer was last true: the record's ``last_synced_at``
  as a stamp, else the snapshot's date. A price read live is current by
  construction; one read from a sync is as old as the sync, and the line says
  so. An item the master holds as inactive answers ``in_books=False`` with
  its id and stock beside it, exactly as the live adapter answers one.

``create_item`` refuses. There is no live write to a registry connector's item
master, and a refusal that names the book is the same answer the adapter it
replaces gave — nothing here pretends a sync is a ledger.

``available`` is whether this company has synced any items at all. A connection
whose master has never been pulled answers BOOKS OFFLINE with that reason, as
before; one that has answers from what it holds.

**Matching.** The code is matched the way ``search_items`` and the identity
layer match it — ``normalize_sku`` on both sides — so ``CNMG 120408-MP`` and
``CNMG120408MP`` are one item here as they are everywhere else, and the
product's *name* is tried second because that is the spelling the desk types
and the item screens show. One rule, imported, not a second spelling of it.

Lives beside ``zoho_books_service`` because it is the other implementation of
the same protocol: that one reads a ledger over HTTP, this one reads what a
sync left behind. It is not ``item_master.py`` — that module reads uploaded
spreadsheets and has nothing to do with connector records, whatever the plan
that named it believed.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..domain import models
from ..identity.matchers import normalize_sku
from ..zoho import ZohoItem, ZohoWriteRefused


class SyncedCatalogue:
    """``SourceCatalogue`` over one connected company's synced item master."""

    def __init__(self, session: Session, org: str, *, connection_id: str,
                 connector: str, label: str = "") -> None:
        self._s = session
        self._org = org
        self._connection_id = connection_id
        self._connector = connector
        self._label = label or connector
        self._available: Optional[bool] = None
        self._master_as_of: Optional[str] = ""   # "" = not yet read

    # ── SourceCatalogue ─────────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        """Whether this company's master has been synced at all.

        Cached per instance: a catalogue lives for one request, and the answer
        cannot change inside it.
        """
        if self._available is None:
            self._available = self._s.scalar(
                select(models.ItemConnectorRecord.record_id).where(
                    models.ItemConnectorRecord.organization_id == self._org,
                    models.ItemConnectorRecord.connection_id == self._connection_id,
                ).limit(1)) is not None
        return self._available

    def get_item(self, code: str) -> Optional[ZohoItem]:
        if not code:
            return None
        rec, product = self._find(code)
        if rec is None:
            # Known code, absent from this company's master: NOT IN BOOKS,
            # with no price attached. Deliberately not None — None reads as
            # "nothing to say about this code at all", which is the
            # ``UnavailableZoho`` answer this class exists to replace. Stamped
            # with the master's own date: absent *as of the last pull* is the
            # claim, and an item created in the book since is exactly the one
            # this answer is wrong about.
            return ZohoItem(code=code, name=code, in_books=False,
                            list_price=None, stock=None, cost=None,
                            as_of=self._master_stamp())
        snapshot = self._latest_snapshot(rec.product_id) if rec.product_id else None
        # The stamp itself, not a calendar day cut here: the screen renders it
        # in the business's zone like every other timestamp, so a pull at
        # 23:30 in Chennai is not Tuesday's here and Wednesday's there. A
        # snapshot carries a date already.
        as_of = None
        if rec.last_synced_at is not None:
            as_of = clock.iso(rec.last_synced_at)
        elif snapshot is not None:
            as_of = snapshot.as_of.isoformat()
        return ZohoItem(
            code=code,
            name=((product.name if product is not None else "")
                  or rec.description or code),
            # An inactive (BC: blocked) item exists in the master and cannot
            # be put on a document — the reading the live adapter takes, for
            # the same reason: reporting it available moves the failure to
            # the send. A record with no product row cannot say, and is held.
            in_books=(product.active if product is not None else True),
            list_price=None,
            stock=(int(snapshot.available) if snapshot is not None
                   and snapshot.available is not None else None),
            cost=(float(snapshot.purchase_rate) if snapshot is not None
                  and snapshot.purchase_rate is not None else None),
            item_id=rec.external_id,
            synthetic=False,
            as_of=as_of,
        )

    def create_item(self, code: str, name: str,
                    list_price: Optional[float] = None) -> ZohoItem:
        raise ZohoWriteRefused(
            f"{self._label} is synced on a schedule, not written live, so an "
            f"item cannot be created there from here. Create it in "
            f"{self._label} and sync again.",
            codes=[code] if code else None)

    # ── reads ───────────────────────────────────────────────────────────────
    def _find(self, code: str):
        """The record for this code in this company, and its product row."""
        wanted = normalize_sku(code)
        rec = models.ItemConnectorRecord
        prod = models.Product
        base = (select(rec, prod)
                .outerjoin(prod, prod.product_id == rec.product_id)
                .where(rec.organization_id == self._org,
                       rec.connection_id == self._connection_id))
        # ``sku`` holds the normalised key the identity layer writes, so an
        # exact comparison on it is the same match ``search_items`` re-ranks by.
        # Ordered, so two records that normalise to one SKU answer the same
        # way on every read and every backend — the first written wins, and
        # the master that holds two is what a sync report should be saying.
        base = base.order_by(rec.created_at, rec.record_id)
        if wanted:
            row = self._s.execute(base.where(rec.sku == wanted).limit(1)).first()
            if row is not None:
                return row[0], row[1]
        # Then the product's name, which is what a person types and what the
        # item screens show; ``search_items`` publishes that as the code for
        # exactly this reason. Case-insensitive, as the live adapter's name
        # leg is; punctuation is the SKU leg's business.
        row = self._s.execute(base.where(func.lower(prod.name) == code.lower())
                              .limit(1)).first()
        if row is not None:
            return row[0], row[1]
        return None, None

    def _master_stamp(self) -> Optional[str]:
        """When this company's master was last pulled — the newest
        ``last_synced_at`` across its records, as a stamp. Cached per instance."""
        if self._master_as_of == "":
            newest = self._s.scalar(
                select(func.max(models.ItemConnectorRecord.last_synced_at)).where(
                    models.ItemConnectorRecord.organization_id == self._org,
                    models.ItemConnectorRecord.connection_id == self._connection_id))
            self._master_as_of = clock.iso(newest) if newest else None
        return self._master_as_of

    def _latest_snapshot(self, product_id: str) -> Optional[models.StockSnapshot]:
        return self._s.scalar(
            select(models.StockSnapshot)
            .where(models.StockSnapshot.organization_id == self._org,
                   models.StockSnapshot.product_id == product_id)
            .order_by(models.StockSnapshot.as_of.desc(),
                      models.StockSnapshot.created_at.desc())
            .limit(1))
