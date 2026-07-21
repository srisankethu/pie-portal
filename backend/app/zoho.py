"""Zoho Books integration boundary.

The quote builder needs three things from Zoho Books that pie-parser cannot
provide (pie-parser is nomenclature-only, never commercial): whether an item
exists in the books, its list price and landed cost, and live stock — plus the
ability to create a missing item and to create the final estimate.

This module defines the ``ZohoService`` protocol and ships a deterministic
``MockZoho`` adapter so the whole flow works end to end offline. A real adapter
(live Zoho Books API) implements the same protocol and is wired in ``deps.py``;
see README "Wiring real Zoho".
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol


@dataclass
class ZohoItem:
    """A Zoho Books item view. ``cost`` is management-only commercial data."""

    code: str                 # maps to manufacturer MM#
    name: str
    in_books: bool
    list_price: Optional[float]   # standard selling price (₹)
    stock: Optional[int]          # on-hand quantity; None == availability unknown
    cost: Optional[float]         # landed cost (₹) — never sent to a sales client


@dataclass
class ZohoEstimate:
    estimate_id: str
    number: str
    customer: str
    line_count: int


class ZohoService(Protocol):
    """The contract the portal depends on. Implemented by MockZoho and (later)
    a live Zoho Books adapter."""

    def get_item(self, code: str) -> Optional[ZohoItem]: ...

    def create_item(self, code: str, name: str, list_price: Optional[float] = None) -> ZohoItem: ...

    def create_estimate(self, customer: str, lines: List[dict]) -> ZohoEstimate: ...

    @property
    def available(self) -> bool:
        """False simulates a Zoho outage (design's BOOKS OFFLINE state)."""
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

    def create_estimate(self, customer: str, lines: List[dict]) -> ZohoEstimate:
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


# Default process-wide instance (swapped for a real adapter in deps.py).
mock_zoho = MockZoho()
