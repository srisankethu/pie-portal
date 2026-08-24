"""The cache, and the rules that make caching safe here.

Two halves. The first is the mechanism — bounds, expiry, counters — which is
ordinary. The second is the part that actually goes wrong in production: a key
that forgot an input, so the answer comes back from a world that no longer
exists.
"""
from __future__ import annotations

import time

from app import cache as cache_module


def _cache(**kw) -> cache_module.Cache:
    return cache_module.Cache("test", **kw)


# ── mechanism ────────────────────────────────────────────────────────────────

def test_a_stored_value_comes_back():
    c = _cache()
    assert c.get("k") is cache_module.MISS
    c.set("k", {"answer": 1})
    assert c.get("k") == {"answer": 1}


def test_a_cached_none_is_not_a_miss():
    """A caller that cannot tell them apart recomputes forever and reports a
    hit rate it cannot explain."""
    c = _cache()
    c.set("k", None)
    assert c.get("k") is None
    assert c.get("nothing-here") is cache_module.MISS


def test_the_least_recently_used_entry_is_evicted_first():
    c = _cache(maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    c.get("a")                       # 'a' is now the recently used one
    c.set("c", 3)
    assert c.get("b") is cache_module.MISS
    assert c.get("a") == 1 and c.get("c") == 3
    assert c.stats()["evictions"] == 1


def test_an_expired_entry_is_a_miss():
    c = _cache(ttl_seconds=0.01)
    c.set("k", 1)
    time.sleep(0.02)
    assert c.get("k") is cache_module.MISS
    assert c.stats()["expirations"] == 1


def test_a_zero_sized_cache_stores_nothing():
    """The off switch a deployment gets when something is suspected of being
    served stale — it must not need a code change."""
    c = _cache(maxsize=0)
    c.set("k", 1)
    assert c.get("k") is cache_module.MISS
    assert c.stats()["size"] == 0


def test_stats_report_the_hit_rate_and_not_only_the_counters():
    c = _cache()
    c.set("k", 1)
    c.get("k")
    c.get("k")
    c.get("other")
    stats = c.stats()
    assert stats["hits"] == 2 and stats["misses"] == 1
    assert stats["hit_rate"] == round(2 / 3, 4)


def test_an_untouched_cache_reports_no_hit_rate():
    """None rather than 0.0: nobody asked, which is a different fact from
    everybody missing."""
    assert _cache().stats()["hit_rate"] is None


def test_invalidate_and_clear_remove_entries():
    c = _cache()
    c.set("a", 1)
    c.set("b", 2)
    assert c.invalidate("a") is True
    assert c.invalidate("a") is False       # not an error: nobody may have read it
    c.clear()
    assert c.get("b") is cache_module.MISS


# ── keys ─────────────────────────────────────────────────────────────────────

def test_the_same_inputs_make_the_same_key_whatever_the_dict_order():
    """The key has to be stable across processes, or every worker misses."""
    a = cache_module.fingerprint("ns", {"x": 1, "y": 2}, "text")
    b = cache_module.fingerprint("ns", {"y": 2, "x": 1}, "text")
    assert a == b


def test_a_different_version_is_a_different_key():
    """The whole reason versions go in the key: an answer computed under a
    ruleset that has since changed must not be served as if it still held."""
    before = cache_module.fingerprint("ns", "ruleset-1", "CNMG 120408")
    after = cache_module.fingerprint("ns", "ruleset-2", "CNMG 120408")
    assert before != after


def test_adding_an_input_to_a_key_cannot_collide_with_the_old_one():
    """Content-addressed, so a key that gains a component is a new key rather
    than the old one with a new meaning."""
    old = cache_module.fingerprint("ns", "text")
    new = cache_module.fingerprint("ns", "text", "customer-42")
    assert old != new


def test_registered_caches_are_reported_once_per_name():
    """A module reloaded under test must not produce two of the same cache in
    the health output."""
    first = cache_module.register(cache_module.Cache("dup-test"))
    second = cache_module.register(cache_module.Cache("dup-test"))
    names = [c["name"] for c in cache_module.stats()]
    assert names.count("dup-test") == 1
    assert cache_module.get_cache("dup-test") is second is not first
