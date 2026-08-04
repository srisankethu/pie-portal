"""UTC now, and making a stored timestamp safe to compare.

Two functions, extracted because the second one is subtle enough that a second
copy would eventually drift from the first. ``ingestion/jobs`` and
``trust/access`` both reap expired rows by comparing a stored ``expires_at``
against the present, and both had their own pair.

The subtlety is ``aware``. SQLite has no timezone type, so a ``DateTime(timezone=True)``
column round-trips as a *naive* datetime, while Postgres returns an aware one.
Comparing a naive value to ``datetime.now(timezone.utc)`` raises ``TypeError``,
so the same code that works in production fails in tests — or, worse, the
reverse. Values are stored as UTC throughout, so attaching UTC to a naive one
is a correction, not an assumption.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def now() -> datetime:
    """The present, in UTC and timezone-aware."""
    return datetime.now(timezone.utc)


def aware(value: Optional[datetime]) -> Optional[datetime]:
    """A stored timestamp made comparable to ``now()``. ``None`` passes through."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
