"""Caching the engine's verdict about a product code.

The engine itself is stubbed here on purpose. What needs pinning is not that
pie-parser resolves correctly — its own corpus does that — but that the cache
around it asks the engine again exactly when the answer could have changed, and
never hands two quotes the same mutable object.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app import pie_service as pie_module


class _StubEngine:
    """Stands in for ``tools/resolve_rfq``: counts calls, returns a result."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, args, _sources):
        self.calls.append(args.text)
        return ({
            "resolution": {"outcome": "AUTO_MATCH", "input_semantics": "IDENTITY",
                           "matches": []},
            "suggestions": [{"record_id": "MM1", "description": "An insert",
                             "scores": {"combined": 0.9},
                             "attributes": {"corner_radius_mm": 0.8}},
                            {"record_id": "MM2", "description": "Another",
                             "scores": {"combined": 0.4}, "attributes": {}}],
            "notes": [],
        }, "human text")


class _Mappings:
    """A mapping store whose content — and so whose fingerprint — can change."""

    def __init__(self, value: str) -> None:
        self.value = value

    def fingerprint(self) -> str:
        return self.value


@pytest.fixture()
def service(monkeypatch):
    svc = pie_module.PieService()
    engine = _StubEngine()
    monkeypatch.setattr(svc, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(svc, "_ensure_index", lambda: None)
    svc._mod = engine
    svc._sources = []
    svc._catalog_version = "ruleset-1"
    pie_module._resolution_cache.clear()
    yield svc, engine
    pie_module._resolution_cache.clear()


def _bands() -> Any:
    return pie_module.Bands(tech=0.85, compat=0.5)


# ── the win ──────────────────────────────────────────────────────────────────

def test_the_same_line_resolved_twice_asks_the_engine_once(service):
    svc, engine = service
    first = svc.resolve("CNMG 120408", bands=_bands())
    second = svc.resolve("CNMG 120408", bands=_bands())
    assert engine.calls == ["CNMG 120408"]
    assert first.supplyCode == second.supplyCode == "MM1"


# ── the things that must invalidate ──────────────────────────────────────────

def test_a_rebuilt_catalogue_is_not_answered_from_the_old_one(service):
    """The ruleset version is what explains why the same text resolved to a
    different product last March. An answer from the previous one is a wrong
    answer with a confident provenance attached."""
    svc, engine = service
    svc.resolve("CNMG 120408", bands=_bands())
    svc._catalog_version = "ruleset-2"
    svc.resolve("CNMG 120408", bands=_bands())
    assert len(engine.calls) == 2


def test_confirming_a_mapping_changes_the_answer_immediately(service):
    """A person has just taught the system that this customer's code means that
    product. A cache that outlived the confirmation would keep telling them it
    had not been recorded."""
    svc, engine = service
    store = _Mappings("no-mappings")
    svc.resolve("THEIR-77", customer_scope="cust-1", bands=_bands(),
                mapping_store=store)
    store.value = "one-mapping"
    svc.resolve("THEIR-77", customer_scope="cust-1", bands=_bands(),
                mapping_store=store)
    assert len(engine.calls) == 2


def test_a_different_customer_is_a_different_question(service):
    svc, engine = service
    svc.resolve("THEIR-77", customer_scope="cust-1", bands=_bands())
    svc.resolve("THEIR-77", customer_scope="cust-2", bands=_bands())
    assert len(engine.calls) == 2


def test_a_store_that_cannot_be_fingerprinted_is_never_cached(service):
    """Refusing to cache costs a scan; caching against an input nothing can see
    costs the customer a stale answer that looks authoritative."""
    svc, engine = service
    opaque = object()
    svc.resolve("CNMG 120408", bands=_bands(), mapping_store=opaque)
    svc.resolve("CNMG 120408", bands=_bands(), mapping_store=opaque)
    assert len(engine.calls) == 2


# ── the things that must *not* invalidate ────────────────────────────────────

def test_the_bands_are_policy_applied_after_the_engine_not_a_cache_key(service):
    """Two organizations with different equivalence bands read one engine
    result differently. That is the correct relationship between a fact and the
    policy judging it (§1) — and it means the bands do not belong in the key."""
    svc, engine = service
    strict = svc.resolve("CNMG 120408", bands=pie_module.Bands(tech=0.95, compat=0.5))
    loose = svc.resolve("CNMG 120408", bands=pie_module.Bands(tech=0.85, compat=0.5))
    assert len(engine.calls) == 1
    assert strict.rel == "COMPAT"          # 0.9 is below a 0.95 tech band
    assert loose.rel == "TECH"             # and above a 0.85 one


# ── isolation ────────────────────────────────────────────────────────────────

def test_two_quotes_do_not_share_one_mutable_result(service):
    """A Line keeps a reference into the engine's result. If the cache handed
    out the object it stored, one quote's edit would change another's."""
    svc, _engine = service
    first = svc.resolve("CNMG 120408", bands=_bands())
    first.candidates[0].attributes["corner_radius_mm"] = 999
    second = svc.resolve("CNMG 120408", bands=_bands())
    assert second.candidates[0].attributes["corner_radius_mm"] == 0.8


def test_an_engine_failure_is_not_remembered(service):
    """PIE_DOWN is a transient state, and pinning it would keep a recovered
    engine offline for everyone until the entry expired."""
    svc, engine = service

    def explode(_args, _sources):
        raise RuntimeError("engine gone")

    engine_calls: Dict[str, int] = {"n": 0}

    def counted(args, sources):
        engine_calls["n"] += 1
        if engine_calls["n"] == 1:
            return explode(args, sources)
        return _StubEngine().run(args, sources)

    svc._mod.run = counted
    down = svc.resolve("CNMG 120408", bands=_bands())
    assert down.rel == "PIE_DOWN" and down.pie_offline

    recovered = svc.resolve("CNMG 120408", bands=_bands())
    assert recovered.rel != "PIE_DOWN"
    assert engine_calls["n"] == 2


def test_the_cache_can_be_switched_off_without_a_code_change(service, monkeypatch):
    svc, engine = service
    monkeypatch.setattr(pie_module._resolution_cache, "maxsize", 0)
    svc.resolve("CNMG 120408", bands=_bands())
    svc.resolve("CNMG 120408", bands=_bands())
    assert len(engine.calls) == 2
