"""Zoho Books integration boundary.

The quote builder needs three things from Zoho Books that pie-parser cannot
provide (pie-parser is nomenclature-only, never commercial): whether an item
exists in the books, its list price and landed cost, and live stock — plus the
ability to create a missing item and to create the final estimate.

This module defines the ``ZohoService`` protocol, the failure vocabulary the
quote flow acts on, and two adapters that need no network: ``MockZoho``
(deterministic, offline — the dev/test/demo default) and ``UnavailableZoho``
(the honest answer when no book can be identified). The live adapter is
``ingestion.zoho_books_service.ZohoBooksService``; ``select_zoho_service`` picks
between them the way ``ai.provider.select_provider`` picks a model provider.

**The failure vocabulary is the point of this module as much as the protocol
is.** A quote that reports an estimate nobody can find in Zoho is worse than one
that reports a failure, so each adapter translates its own transport errors into
exactly one of three states before they leave it: the books could not be read
(``ZohoUnavailable``), the write was refused and nothing was sent
(``ZohoWriteRefused``), or the write was sent and its outcome cannot be
established (``ZohoWriteUnknown``). There is deliberately no fourth state
meaning "probably fine".
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol

from .ingestion.erp.base import WrittenDocument
from .ingestion.errors import (SourceUnavailable, SourceWriteRefused,
                               SourceWriteUnknown)

if TYPE_CHECKING:                     # pragma: no cover - typing only
    from .ingestion.zoho_client import ZohoCredentials


@dataclass
class ZohoItem:
    """A Zoho Books item view. ``cost`` is management-only commercial data."""

    code: str                 # maps to manufacturer MM#
    name: str
    in_books: bool
    list_price: Optional[float]   # standard selling price (₹)
    stock: Optional[int]          # on-hand quantity; None == availability unknown
    cost: Optional[float]         # landed cost (₹) — never sent to a sales client
    # The item's id in the books it came from, when the adapter has one. Carried
    # so a later write can name the item it already resolved instead of matching
    # the code a second time: re-matching at send time is both a call per line
    # against the rate limit and a second chance to pick a different item.
    item_id: Optional[str] = None
    #: True when this record was invented rather than read — the offline
    #: stand-in adapter's hashed figures. It travels because the alternative is
    #: what shipped: a list price and a landed cost derived from
    #: ``sha256(code)`` rendered in the same weight as Zoho's own numbers, with
    #: nothing on screen able to tell them apart. ``select_zoho_service`` already
    #: states the rule for the live-without-credentials case — "the mock, whose
    #: hashed prices would be indistinguishable on screen from real ones" — and
    #: this is that rule applied to the case the deployment is actually in.
    synthetic: bool = False
    # The tax rate the books hold against this item, as a percentage (18.0, not
    # 0.18 — it is Zoho's number in Zoho's units, converted where it is used).
    #
    # Carried because the alternative is the quote screen asserting one blended
    # rate over every line while the estimate Zoho creates prices each line from
    # *this* value. Two tax authorities, disagreeing on any item that is not on
    # the default rate, with the customer-facing document being the one the
    # screen did not compute.
    #
    # ``None`` means the books did not state one, which is not the same as zero
    # and not the same as the default — see ``store.Quote.to_dict``.
    tax_percentage: Optional[float] = None


#: The Zoho name for the neutral record every connector's write returns. Kept
#: as an alias rather than a second dataclass: one concept, one shape, and the
#: name callers already use goes on meaning what it meant. ``estimate_id`` is
#: ``document_id`` — see ``WrittenDocument``.
ZohoEstimate = WrittenDocument


# ── the three ways this boundary is allowed to fail ─────────────────────────
# The meanings are the connector-neutral ones in ``ingestion.errors`` — nothing
# in "unreadable", "nothing stored" or "sent, outcome unestablished" is about
# Zoho. The Zoho names survive as subclasses because callers and tests already
# catch them and a rename would be churn without a reader; new callers should
# catch the neutral base, which a second connector's adapter will also raise.
class ZohoUnavailable(SourceUnavailable):
    """The books could not be read."""


class ZohoWriteRefused(SourceWriteRefused):
    """The write was not attempted, and this is why. Carries the item codes."""


class ZohoWriteUnknown(SourceWriteUnknown):
    """The write was sent and its outcome could not be established."""


class SourceCatalogue(Protocol):
    """Reading and extending a source system's item master.

    Split from :class:`QuoteWriter` below because the two have different
    callers with different needs: ``store`` resolves and creates items and
    asks whether the books are readable at all, while the quote router only
    ever writes a document. Forcing one contract on both would mean a
    connector that can create a quote but has no item master to offer — which
    is the ordinary case outside Zoho — could not satisfy the port without
    stubbing methods nobody calls on it.
    """

    def get_item(self, code: str) -> Optional[ZohoItem]: ...

    def create_item(self, code: str, name: str, list_price: Optional[float] = None) -> ZohoItem: ...

    @property
    def available(self) -> bool:
        """Whether the books can be read right now (design's BOOKS OFFLINE state)."""
        ...


class QuoteWriter(Protocol):
    """Writing a quote into a source system, whichever system that is.

    The one member, because writing the document is the whole job. Nothing in
    the signature is Zoho-shaped: a customer's id *in the target system*, the
    lines, and a caller-stable reference that makes the write idempotent.

    Named for the write stage rather than for anybody's ledger. It was
    ``create_estimate`` — Zoho's word — which meant the capability pin looked
    for ``create_sales_quotes`` on a source while this looked for something
    else on a writer, and Business Central carried both names for one method to
    satisfy the two. One name now, and the pin and the port agree by
    construction rather than by an alias somebody has to keep.

    The record types it speaks are still named ``Zoho…`` — the shapes are
    generic (an id, a number, a customer, a line count) but the names have not
    caught up. Renaming them touches every caller and belongs with the change
    that reshapes the response anyway, not here.
    """

    def create_sales_quotes(self, customer: str, lines: List[dict], *,
                        customer_ref: Optional[str] = None,
                        reference: Optional[str] = None) -> ZohoEstimate:
        """Create the estimate for ``customer``.

        ``customer_ref`` is the customer's id *in the target system* — the Zoho
        contact id — which the caller resolves from the connection the quote
        belongs to. A live adapter requires it and refuses without it: matching
        a free-text customer name against a real ledger is how an estimate ends
        up on the wrong account.

        ``reference`` is a caller-stable key for this quote. It is what makes
        the write idempotent: an adapter that finds an estimate already carrying
        it returns that one rather than creating a second.
        """
        ...


class ZohoService(SourceCatalogue, QuoteWriter, Protocol):
    """Both halves at once — the contract the Zoho adapters actually provide.

    Kept under its own name because every annotation in the portal already
    says ``ZohoService`` and means exactly this. A connector that implements
    only one half satisfies that half's protocol directly.
    """


class MockZoho:
    """Deterministic in-memory Zoho stand-in.

    Commercial values are derived from a stable hash of the item code, so the
    same code always yields the same list price / cost / stock across runs — the
    determinism the design relies on for its seeded scenarios. A handful of code
    suffixes are steered into specific states (zero stock, not-in-books, unknown
    availability) so every line state in the design is reachable with real MM#s.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._created: Dict[str, ZohoItem] = {}
        self._not_in_books: set[str] = set()
        self._estimate_seq = 4200
        self._available = True

    # ── deterministic derivation ─────────────────────────────────────────────
    @staticmethod
    def _hash(code: str) -> int:
        return int(hashlib.sha256(code.encode("utf-8")).hexdigest()[:8], 16)

    def _derive(self, code: str) -> ZohoItem:
        h = self._hash(code)
        # List price ₹250–₹8,600, rounded to ₹5; cost ~76–82% of list.
        list_price = 250 + (h % 8350)
        list_price = round(list_price / 5) * 5
        cost_ratio = 0.76 + ((h >> 8) % 7) / 100.0     # 0.76..0.82
        cost = round(list_price * cost_ratio / 5) * 5
        # Stock 0–120; steer ~1/9 of codes to zero stock.
        stock = (h >> 4) % 121
        if h % 9 == 0:
            stock = 0
        # ~1/11 of codes: availability service can't answer (unknown != zero).
        if h % 11 == 3:
            stock_val: Optional[int] = None
        else:
            stock_val = stock
        # ~1/8 of codes are not yet in the books (must be created).
        in_books = (h % 8 != 5) and code not in self._not_in_books
        if code in self._created:
            in_books = True
        name = self._created[code].name if code in self._created else code
        if not in_books:
            # An item the books do not hold has no list price, no landed cost
            # and no stock, because there is no item record for any of them to
            # be on. ``ZohoBooksService.get_item`` has always answered exactly
            # this way for a code it cannot find; the mock did not, and handed
            # back all three anyway — so a line reading NOT IN BOOKS still
            # showed a cost, and "the item is not in our item list, where is
            # this cost coming from" had a real answer: ``sha256(code)``.
            #
            # A stand-in that contradicts the adapter it stands in for is worse
            # than no stand-in, since every screen and every test written
            # against it is written against behaviour production does not have.
            return ZohoItem(code=code, name=name, in_books=False,
                            list_price=None, stock=None, cost=None,
                            synthetic=True)
        return ZohoItem(code=code, name=name, in_books=in_books,
                        list_price=float(list_price), stock=stock_val,
                        cost=float(cost), synthetic=True)

    # ── protocol ─────────────────────────────────────────────────────────────
    def get_item(self, code: str) -> Optional[ZohoItem]:
        if not code:
            return None
        with self._lock:
            if code in self._created:
                return self._created[code]
            return self._derive(code)

    def create_item(self, code: str, name: str, list_price: Optional[float] = None) -> ZohoItem:
        with self._lock:
            base = self._derive(code)
            item = ZohoItem(
                code=code, name=name or code, in_books=True,
                list_price=list_price if list_price is not None else base.list_price,
                stock=base.stock, cost=base.cost, synthetic=True,
            )
            self._created[code] = item
            self._not_in_books.discard(code)
            return item

    def create_sales_quotes(self, customer: str, lines: List[dict], *,
                        customer_ref: Optional[str] = None,
                        reference: Optional[str] = None) -> ZohoEstimate:
        """Invent an estimate number. ``customer_ref`` and ``reference`` are
        accepted and ignored: there is no ledger here for a contact id to point
        into and no second estimate for a reference to deduplicate against."""
        with self._lock:
            self._estimate_seq += 1
            num = f"EST-{self._estimate_seq:05d}"
            return ZohoEstimate(document_id=f"zoho-{int(time.time())}-{self._estimate_seq}",
                                number=num, customer=customer, line_count=len(lines))

    @property
    def available(self) -> bool:
        return self._available

    # ── test / demo controls ─────────────────────────────────────────────────
    def set_available(self, value: bool) -> None:
        self._available = value

    def mark_not_in_books(self, code: str) -> None:
        self._not_in_books.add(code)


class UnavailableZoho:
    """The adapter for "we cannot tell which set of books this quote belongs to".

    Returned instead of a live adapter when the quote's customer cannot be tied
    to exactly one Zoho connection. The alternative — pricing from whichever
    company's book happened to be first — is the failure this class exists to
    prevent: with three legal entities sharing one platform, a list price read
    from SLS and an estimate written into 4U look identical on screen and are
    wrong in a way nobody can reproduce later.

    Reads report BOOKS OFFLINE (``available`` is False, which the line status
    already understands) and writes refuse with the reason, rather than falling
    back to anything.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def get_item(self, code: str) -> Optional[ZohoItem]:
        return None

    def create_item(self, code: str, name: str,
                    list_price: Optional[float] = None) -> ZohoItem:
        raise ZohoWriteRefused(self.reason, codes=[code] if code else None)

    def create_sales_quotes(self, customer: str, lines: List[dict], *,
                        customer_ref: Optional[str] = None,
                        reference: Optional[str] = None) -> ZohoEstimate:
        raise ZohoWriteRefused(self.reason)

    @property
    def available(self) -> bool:
        return False


def select_zoho_service(credentials: Optional["ZohoCredentials"] = None,
                        reason: str = "") -> ZohoService:
    """The configured quote-side adapter, mirroring ``ai.provider.select_provider``.

    Mock is the default and stays the default: the test suite, the offline demo
    and a fresh clone all depend on it, and a deployment that has not
    deliberately said ``ZOHO_QUOTE_SERVICE=live`` must never write to a real
    ledger by accident.

    In live mode, credentials are not optional. Without them there is no book to
    talk to, and the honest answer is ``UnavailableZoho`` carrying why — not the
    mock, whose hashed prices would be indistinguishable on screen from real
    ones.
    """
    from .config import settings

    if settings.ZOHO_QUOTE_SERVICE != "live":
        return mock_zoho
    if credentials is None:
        return UnavailableZoho(
            reason or "No Zoho connection could be resolved for this quote.")
    from .ingestion.zoho_books_service import ZohoBooksService

    return ZohoBooksService(credentials=credentials)


# Default process-wide instance (the mock adapter; see ``select_zoho_service``).
mock_zoho = MockZoho()
