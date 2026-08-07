"""FastAPI dependencies: the Zoho adapter.

Swap ``get_zoho`` to return a live Zoho Books adapter (implementing
``ZohoService``) to go from the mock to production without touching routers.

Authentication lives in ``app/authz.py`` — one principal for the whole product.
This module used to hold a second one: a demo login with two fixed accounts that
accepted any password, which the Quote Builder authenticated against while every
other endpoint used the database-backed platform user. Two identities meant
signing in twice and being somebody else on the second screen.
"""
from __future__ import annotations

from .zoho import ZohoService, mock_zoho


def get_zoho() -> ZohoService:
    return mock_zoho
