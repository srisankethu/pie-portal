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


@dataclass
class ZohoEstimate:
    estimate_id: str
    number: str
    customer: str
    line_count: int
    # True when the estimate already existed under this reference and was read
    # back rather than created. Sending the same quote twice must not put two
    # estimates in front of the customer.
    already_existed: bool = False


# ── the three ways this boundary is allowed to fail ─────────────────────────
class ZohoUnavailable(RuntimeError):
    """The books could not be read.

    Every read-side adapter failure collapses to this so a single unreachable
    item cannot 500 the whole intake: the line reads BOOKS OFFLINE, which is
    what that state is for.
    """


class ZohoWriteRefused(RuntimeError):
    """The write was not attempted, and this is why.

    Carries the item codes responsible where there are any, so the screen can
    point at the lines rather than at the quote. Nothing was sent to Zoho, so
    fixing the named problem and sending again is safe.
    """

    def __init__(self, message: str, codes: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.codes = list(codes or [])


class ZohoWriteUnknown(RuntimeError):
    """The write was sent and its outcome could not be established.

    The one state that must never be reported as either success or failure. It
    carries the reference the estimate would have been written under, because
    the only way to resolve it is for a person to look that up in Zoho — and
    because sending again under the same reference is then safe.
    """

    def __init__(self, message: str, reference: Optional[str] = None) -> None:
        super().__init__(message)
        self.reference = reference


class ZohoService(Protocol):
    """The contract the portal depends on.

    Implemented by ``MockZoho``, ``UnavailableZoho`` and the live
    ``ZohoBooksService``.
    """

    def get_item(self, code: str) -> Optional[ZohoItem]: ...

    def create_item(self, code: str, name: str, list_price: Optional[float] = None) -> ZohoItem: ...

    def create_estimate(self, customer: str, lines: List[dict], *,
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

    @property
    def available(self) -> bool:
        """Whether the books can be read right now (design's BOOKS OFFLINE state)."""
        ...


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
        return ZohoItem(code=code, name=name, in_books=in_books,
                        list_price=float(list_price), stock=stock_val, cost=float(cost))

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
                stock=base.stock, cost=base.cost,
            )
            self._created[code] = item
            self._not_in_books.discard(code)
            return item

    def create_estimate(self, customer: str, lines: List[dict], *,
                        customer_ref: Optional[str] = None,
                        reference: Optional[str] = None) -> ZohoEstimate:
        """Invent an estimate number. ``customer_ref`` and ``reference`` are
        accepted and ignored: there is no ledger here for a contact id to point
        into and no second estimate for a reference to deduplicate against."""
        with self._lock:
            self._estimate_seq += 1
            num = f"EST-{self._estimate_seq:05d}"
            return ZohoEstimate(estimate_id=f"zoho-{int(time.time())}-{self._estimate_seq}",
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

    def create_estimate(self, customer: str, lines: List[dict], *,
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
