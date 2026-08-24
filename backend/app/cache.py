"""A bounded, keyed, in-process cache — one implementation, with the rules
that make caching safe in this codebase written into it.

Caching is easy to add and easy to get wrong in ways nothing notices: the
failure is not a crash, it is a screen that quietly answers a question with
last week's facts. Two rules keep that from happening here, and both are
properties of the *key* rather than of this module:

**Every input that varies the answer is in the key, versions included.** A
resolution depends on the catalogue ruleset it ran against and on the confirmed
mappings the engine could read; a policy-shaped answer depends on the
thresholds version that judged it (§1 — "thresholds carry a version"). Leave
one out and the cache serves an answer from a world that no longer exists,
with no way to tell it apart from a fresh one. Callers build their key from
:func:`fingerprint`, which is content-addressed, so *adding* an input to the
key can never collide with an entry stored before it existed.

**Nothing here carries cost or margin.** This is an availability optimisation,
not a projection layer: the one caller today caches the nomenclature engine's
verdict about a product code, which has no money in it at all. A cached
role-projected response would be a new way for a below-floor fact to reach a
salesperson — the MFLOOR shape from §1, arriving through a key that forgot the
reader. If something money-shaped ever wants caching, the recipient's role is
part of the key or it does not go in.

Deliberately in-process rather than Redis, for the reason ``ingestion/jobs.py``
gives about threads: this deployment is one process, an external cache is a
dependency to run, secure and invalidate, and the honest version of what we
need is a dict with a bound on it. It is per-process, so N processes warm N
copies and a restart starts cold — both acceptable, because every entry is
re-derivable by construction. What it must never be is a *store*: nothing may
be true only inside this cache.

Eviction is LRU with an optional TTL. The TTL is a bound on staleness for
inputs the key cannot see (a rebuilt catalogue file under an unchanged
version), not the primary correctness mechanism — the key is.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

#: Returned by :meth:`Cache.get` when there is nothing stored, so that a cached
#: ``None`` is distinguishable from a miss. A caller that treats them the same
#: recomputes forever and reports a 0% hit rate it cannot explain.
MISS = object()

_registry: "OrderedDict[str, Cache]" = OrderedDict()
_registry_lock = threading.Lock()


def fingerprint(*parts: Any) -> str:
    """A short, stable key for a tuple of inputs.

    Stable across processes and runs: the parts are serialised with sorted keys
    and no dependence on dict insertion order, then hashed. That matters
    because the same key has to name the same entry in every worker, and
    because a key built from raw RFQ text would otherwise grow without bound
    and hold the customer's words in memory long after the quote.

    ``default=str`` so a Decimal, a date or a Path is keyed by its own text
    rather than raising. Anything whose ``str`` is not stable (an object with a
    default ``repr``, which embeds its address) must be reduced to a value by
    the caller — a key that changes per process is a cache that never hits.
    """
    blob = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class Cache:
    """One named, bounded LRU cache with per-entry expiry.

    Thread-safe: the app serves requests on a thread pool and background jobs
    run on threads of their own, so an unlocked dict here would be two callers
    resizing one mapping.
    """

    def __init__(self, name: str, *, maxsize: int = 1024,
                 ttl_seconds: float = 0.0) -> None:
        self.name = name
        #: 0 disables the cache entirely — every get misses and nothing is
        #: stored. That is the off switch a deployment gets when a cache is
        #: suspected of serving something stale, and it must not require a
        #: code change to reach.
        self.maxsize = max(0, int(maxsize))
        #: 0 means no expiry; entries live until evicted by size.
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._lock = threading.Lock()
        #: key -> (stored_at_monotonic, value). Monotonic, not wall clock: a
        #: clock adjustment must not resurrect an expired entry or expire a
        #: fresh one.
        self._entries: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.expirations = 0

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, key: str) -> Any:
        """The stored value, or :data:`MISS`."""
        with self._lock:
            if self.maxsize == 0:
                self.misses += 1
                return MISS
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return MISS
            stored_at, value = entry
            if self.ttl_seconds and (time.monotonic() - stored_at) > self.ttl_seconds:
                del self._entries[key]
                self.expirations += 1
                self.misses += 1
                return MISS
            self._entries.move_to_end(key)
            self.hits += 1
            return value

    # ── writes ───────────────────────────────────────────────────────────────
    def set(self, key: str, value: Any) -> None:
        if self.maxsize == 0:
            return
        with self._lock:
            self._entries[key] = (time.monotonic(), value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)
                self.evictions += 1

    def invalidate(self, key: str) -> bool:
        """Drop one entry. False when it was not there — not an error, because
        the caller invalidating on a write does not know whether anyone read it
        first."""
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # ── observability ────────────────────────────────────────────────────────
    def stats(self) -> Dict[str, Any]:
        """What this cache is doing, for the health endpoint and for tests.

        The hit *rate* is included rather than left to the reader: the raw
        counters are meaningless without each other, and a cache reporting
        1,000 hits is indistinguishable from a broken one until you know it
        also took 900,000 misses.
        """
        with self._lock:
            size = len(self._entries)
        looked_up = self.hits + self.misses
        return {
            "name": self.name,
            "size": size,
            "maxsize": self.maxsize,
            "ttl_seconds": self.ttl_seconds,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "expirations": self.expirations,
            "hit_rate": round(self.hits / looked_up, 4) if looked_up else None,
        }


def register(cache: Cache) -> Cache:
    """Make a cache visible to :func:`stats`. Idempotent by name so a module
    reloaded under test does not report two of the same cache."""
    with _registry_lock:
        _registry[cache.name] = cache
    return cache


def caches() -> List[Cache]:
    with _registry_lock:
        return list(_registry.values())


def stats() -> List[Dict[str, Any]]:
    """Every registered cache's counters, for ``/api/health``."""
    return [c.stats() for c in caches()]


def clear_all() -> None:
    """Drop every entry everywhere. For tests, and for an operator who has a
    reason to believe something is being served stale."""
    for cache in caches():
        cache.clear()


def get_cache(name: str) -> Optional[Cache]:
    with _registry_lock:
        return _registry.get(name)
