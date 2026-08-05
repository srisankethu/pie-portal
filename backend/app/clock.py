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

import os
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def now() -> datetime:
    """The present, in UTC and timezone-aware."""
    return datetime.now(timezone.utc)


def aware(value: Optional[datetime]) -> Optional[datetime]:
    """A stored timestamp made comparable to ``now()``. ``None`` passes through."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── the business day ─────────────────────────────────────────────────────────
#
# Storage is UTC and stays UTC. What follows is about the *other* question —
# "which day is it for this business?" — and the two are not the same question
# for five and a half hours out of every twenty-four.
#
# ``date.today()`` reads the server's local zone, which in a container is UTC,
# and ``now().date()`` is UTC by construction. Both put an order placed at
# 09:00 IST on the 5th onto the 4th, so a day's trade lands in the wrong month
# twice a year at a month boundary, a floor is resolved against yesterday's
# cost record, and the sync screen says a pull that just ran happened
# "yesterday". None of it is visible in a UTC-hosted test.

DEFAULT_ZONE = "Asia/Kolkata"


def zone(name: Optional[str] = None) -> ZoneInfo:
    """A timezone by name, falling back to the configured business zone.

    An unknown name falls back rather than raising: a bad timezone string in a
    tenant row must not take the whole organization's screens down, and the
    fallback is the one the deployment already runs on.
    """
    for candidate in (name, os.environ.get("BUSINESS_TIMEZONE"), DEFAULT_ZONE):
        if not candidate:
            continue
        try:
            return ZoneInfo(str(candidate).strip())
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def local_now(tz: Optional[str] = None) -> datetime:
    """The present, as a wall clock in the business's own timezone."""
    return now().astimezone(zone(tz))


def today(tz: Optional[str] = None) -> date:
    """The business's current date. **Use this, never ``date.today()``.**

    ``date.today()`` is the server's idea of the day and is UTC in every
    container this runs in; between 05:30 and 11:00 IST that is the wrong day.
    """
    return local_now(tz).date()


def to_local(value: Optional[datetime], tz: Optional[str] = None) -> Optional[datetime]:
    """A stored UTC timestamp, as a wall clock in the business's timezone.

    For display and for deciding which day something happened on. Comparisons
    should keep using ``aware`` and stay in UTC — converting both sides to a
    local zone to compare them is how a DST boundary becomes an ordering bug.
    """
    value = aware(value)
    return value.astimezone(zone(tz)) if value is not None else None
