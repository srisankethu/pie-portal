"""The Zoho read-source boundary.

A ``ZohoSource`` yields *raw* Zoho Books payloads for each object type. The sync
layer adapts them; the source itself does no normalization. Two implementations:
``FixtureZohoSource`` (deterministic, offline — dev/test) and ``ZohoApiSource``
(live HTTP — deferred). Both are read-only; the platform never writes to Zoho.
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol


class ZohoSource(Protocol):
    def list_contacts(self) -> Iterable[dict[str, Any]]: ...
    def list_items(self) -> Iterable[dict[str, Any]]: ...
    def list_invoices(self) -> Iterable[dict[str, Any]]: ...
    def list_bills(self) -> Iterable[dict[str, Any]]: ...
