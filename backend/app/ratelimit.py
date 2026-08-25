"""One fixed-window rate limiter, shared by every door that needs one.

There are two of those now — the sign-up/demo speed bump in
``routers/onboarding.py`` and the per-key allowance on the public resolution
API — and they want different windows, different keys and different limits.
That is a *parameter* difference, not a second problem, so this is one
implementation rather than a second deque and a second trim loop (CLAUDE.md
§2: "does something *almost* do this?").

**In-process, and documented as such.** The counters live in this process's
memory, so with two replicas behind a load balancer the effective limit is the
configured one times the number of replicas. That is the conservative direction
for the sign-up bump and the honest one to state for the API: the limit exists
to stop one caller monopolising a deployment and to make a bisection sweep
visible in the logs, not to meter a paid quota. A limiter that looks like a
control while being a speed bump is worse than one that says which it is —
``app/config.py``'s note on ``CACHE_BACKEND`` describes the same trade for the
same reason, and both move together the day this needs Redis.
"""
from __future__ import annotations

import threading
import time
from collections import deque

#: ``bucket:key`` -> the monotonic times it was let through, newest last.
#: Trimmed on every read, so an idle key's deque empties rather than growing.
_SEEN: dict[str, deque[float]] = {}

#: Guards ``_SEEN``. FastAPI serves requests on a thread pool, so two requests
#: for the same key genuinely run at once; without this, both read a
#: not-yet-full deque and both are admitted. The window is a speed bump either
#: way, but a limiter that is only *usually* a limit is not one to reason from.
_LOCK = threading.Lock()


def too_many(bucket: str, key: str, *, limit: int, window_seconds: float) -> bool:
    """Has ``key`` already used its allowance of ``bucket`` in this window?

    Returns True when the request should be refused, and records the request
    when it should not — so calling this *is* spending the allowance, and it
    must be called once per request rather than consulted twice.

    ``limit <= 0`` means unlimited: a deployment that sets the limit to zero is
    turning the bump off, which is the reading every caller wants. A limit of
    zero meaning "refuse everything" would make an unset environment variable
    close the door it configures.
    """
    if limit <= 0:
        return False
    slot = f"{bucket}:{key}"
    now = time.monotonic()
    with _LOCK:
        seen = _SEEN.setdefault(slot, deque())
        while seen and now - seen[0] > window_seconds:
            seen.popleft()
        if len(seen) >= limit:
            return True
        seen.append(now)
        return False


def remaining(bucket: str, key: str, *, limit: int, window_seconds: float) -> int:
    """How much of the allowance is left, without spending any of it.

    For the ``X-RateLimit-Remaining`` header, which is the difference between a
    caller that can pace itself and one that discovers the limit by hitting it.
    """
    if limit <= 0:
        return limit
    slot = f"{bucket}:{key}"
    now = time.monotonic()
    with _LOCK:
        seen = _SEEN.get(slot)
        if not seen:
            return limit
        while seen and now - seen[0] > window_seconds:
            seen.popleft()
        return max(0, limit - len(seen))


def reset_key(bucket: str, key: str) -> None:
    """Forget one key's counter in one bucket.

    For a limiter whose counter records *failures*: a success is the event that
    clears it, so a caller in ordinary use never accumulates one. Separate from
    :func:`reset` because that one is for tests and clears whole buckets, which
    is not something a request path should ever do.
    """
    with _LOCK:
        _SEEN.pop(f"{bucket}:{key}", None)


def reset(bucket: str | None = None) -> None:
    """Forget the counters — for tests, which must not inherit each other's.

    Named rather than reaching into ``_SEEN`` from a test: a test that pokes a
    private dict is a test that breaks when the storage changes, and this
    module is the one that will grow a Redis backend.
    """
    with _LOCK:
        if bucket is None:
            _SEEN.clear()
            return
        for slot in [s for s in _SEEN if s.startswith(f"{bucket}:")]:
            del _SEEN[slot]
