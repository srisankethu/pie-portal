"""Live Zoho Books read client — interface present, transport deferred.

The adaptation layer (``normalize.py``) and sync are complete and tested against
the fixture source. Wiring the live HTTP transport (OAuth refresh, pagination
over the Books API) is deliberately deferred: it needs org credentials and a
live tenant to verify, and shipping unverified network code would be dishonest
about what works. The seam is here so the live client drops in without touching
callers — construct it from ``settings`` and implement the four ``list_*`` pulls.
"""
from __future__ import annotations

from typing import Any, Iterable

from ..config import settings


class ZohoApiSource:
    """Read-only Zoho Books client. Not enabled in the Phase 1 foundation."""

    def __init__(self) -> None:
        self._base = settings.ZOHO_API_BASE
        self._org = settings.ZOHO_ORGANIZATION_ID

    def _not_enabled(self, what: str) -> Iterable[dict[str, Any]]:
        raise NotImplementedError(
            f"live Zoho {what} pull is not enabled in this build. Set ZOHO_SOURCE=fixture "
            "for offline data, or implement the Books API transport (OAuth refresh + "
            "pagination) here. Credentials come from ZOHO_* settings."
        )

    def list_contacts(self) -> Iterable[dict[str, Any]]:
        return self._not_enabled("contacts")

    def list_items(self) -> Iterable[dict[str, Any]]:
        return self._not_enabled("items")

    def list_invoices(self) -> Iterable[dict[str, Any]]:
        return self._not_enabled("invoices")

    def list_bills(self) -> Iterable[dict[str, Any]]:
        return self._not_enabled("bills")
