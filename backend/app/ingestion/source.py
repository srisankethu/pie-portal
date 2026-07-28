"""The Zoho read-source boundary.

A ``ZohoSource`` yields *raw* Zoho Books payloads for each object type. The sync
layer adapts them; the source itself does no normalization. Two implementations:
``FixtureZohoSource`` (deterministic, offline — dev/test) and ``ZohoApiSource``
(live HTTP — deferred). Both are read-only; the platform never writes to Zoho.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Protocol

# ``skip(doc_id, last_modified) -> bool``: the sync layer's answer to "do I
# already hold this document unchanged?". A source that can act on it saves the
# detail call; one that cannot may ignore it — the result is identical either way.
SkipPredicate = Callable[[str, str], bool]


class ZohoSource(Protocol):
    def list_contacts(self) -> Iterable[dict[str, Any]]: ...
    def list_items(self) -> Iterable[dict[str, Any]]: ...
    def list_invoices(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]: ...
    def list_bills(self, skip: Optional[SkipPredicate] = None) -> Iterable[dict[str, Any]]: ...
    def list_users(self) -> Iterable[dict[str, Any]]: ...
