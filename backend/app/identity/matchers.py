"""Matching strategies: how two connector records are judged to be one entity.

A registry rather than a chain of ``if connector == ...``. Adding a strategy is
adding a function and registering it; nothing else changes, and no connector
ever appears by name in this file. That is the architectural requirement stated
plainly — a Tally-specific rule living here would mean the next ERP needs a
patch to the resolver rather than an importer.

Every strategy is **deterministic and evidence-bearing**. It returns the value
it matched on, not a score, because the value is what a human reviewer can
judge: "GSTIN 29ABCDE1234F1Z5" settles an argument; "confidence 0.94" starts
one. A future fuzzy or AI-assisted strategy fits the same shape — it just has to
say what it saw.

Order matters and is explicit: strategies run strongest-first and the first hit
wins. GSTIN and SKU are exact identifiers issued by somebody other than us,
which is why they lead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

#: A GSTIN is 15 characters: 2 state digits, a 10-character PAN, then entity,
#: 'Z', and a checksum character. Validated by shape only — a checksum test
#: would reject legitimately odd values from an ERP that stores them loosely,
#: and rejecting a real identifier is worse than accepting an unusual one.
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{3}$")


def normalize_gstin(raw: Optional[str]) -> Optional[str]:
    """Upper-cased, stripped of anything that is not alphanumeric.

    ERPs disagree about spacing and case for the same registration, so the
    stored form is canonical and the raw one is kept in ``source_ref``. Matching
    on the raw value would make a link depend on which system typed it.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9A-Za-z]", "", str(raw)).upper()
    if not cleaned:
        return None
    return cleaned


def is_valid_gstin(value: Optional[str]) -> bool:
    return bool(value) and bool(_GSTIN.match(value or ""))


def normalize_sku(raw: Optional[str]) -> Optional[str]:
    """Upper-cased, with separators removed.

    ``KCMT 090304 LF``, ``kcmt-090304-lf`` and ``KCMT090304LF`` are one part
    number written by three people. Punctuation is not identity.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[\s\-_./]", "", str(raw)).upper()
    return cleaned or None


@dataclass(frozen=True)
class Candidate:
    """A proposed link, with the reason attached."""

    identity_id: str
    strategy: str
    evidence: str


@dataclass(frozen=True)
class RecordFacts:
    """What a strategy is allowed to see.

    Deliberately narrow, and deliberately connector-agnostic: a strategy that
    could read the connector name could branch on it, and the architecture rule
    would erode one commit at a time.
    """

    organization_id: str
    record_id: str
    keys: dict[str, Optional[str]]      # e.g. {"gstin": "29..."} or {"sku": "KCMT..."}
    text: str = ""                      # name or description, for future fuzzy rules


class Lookup(Protocol):
    """How a strategy asks the store for records sharing a key.

    Injected so the strategies stay pure and testable, and so the resolver
    controls every query — a strategy cannot reach past its own key.
    """

    def identities_by_key(self, organization_id: str, key: str,
                          value: str, exclude_record_id: str) -> list[str]:
        ...


Strategy = Callable[[RecordFacts, Lookup], list[Candidate]]

_REGISTRY: dict[str, tuple[int, Strategy]] = {}


def register(name: str, order: int) -> Callable[[Strategy], Strategy]:
    """Add a strategy. Lower ``order`` runs first."""
    def wrap(fn: Strategy) -> Strategy:
        _REGISTRY[name] = (order, fn)
        return fn
    return wrap


def strategies() -> list[tuple[str, Strategy]]:
    """Registered strategies, strongest first."""
    return [(name, fn) for name, (_, fn) in
            sorted(_REGISTRY.items(), key=lambda kv: kv[1][0])]


def find_candidates(facts: RecordFacts, lookup: Lookup) -> list[Candidate]:
    """Every identity any strategy proposes, strongest strategy first.

    Returns all of them rather than only the best: two identities matching on
    one GSTIN is itself a finding — it means an earlier link was wrong, or the
    same registration is genuinely shared — and hiding the second one would
    hide the problem.
    """
    out: list[Candidate] = []
    seen: set[str] = set()
    for _, fn in strategies():
        for candidate in fn(facts, lookup):
            if candidate.identity_id in seen:
                continue
            seen.add(candidate.identity_id)
            out.append(candidate)
    return out


# ── the strategies themselves ───────────────────────────────────────────────
@register("GSTIN", order=10)
def by_gstin(facts: RecordFacts, lookup: Lookup) -> list[Candidate]:
    """Exact GSTIN. The strongest evidence available for an Indian business.

    A tax registration is issued by the government, not by us or by an ERP, so
    two records carrying the same one are the same legal entity — with the
    caveat that a group may trade under several names against one registration,
    which is exactly why this proposes rather than decides.
    """
    value = facts.keys.get("gstin")
    if not is_valid_gstin(value):
        return []
    return [Candidate(identity_id=i, strategy="GSTIN", evidence=f"GSTIN {value}")
            for i in lookup.identities_by_key(
                facts.organization_id, "gstin", value or "", facts.record_id)]


@register("SKU", order=10)
def by_sku(facts: RecordFacts, lookup: Lookup) -> list[Candidate]:
    """Exact SKU, after normalisation.

    Weaker than a GSTIN and honest about it: a SKU is issued by whoever set the
    catalogue up, and two ERPs can reuse a short code for different parts. It
    still proposes rather than decides, and the evidence names the code so a
    reviewer can see what was compared.
    """
    value = facts.keys.get("sku")
    if not value:
        return []
    return [Candidate(identity_id=i, strategy="SKU", evidence=f"SKU {value}")
            for i in lookup.identities_by_key(
                facts.organization_id, "sku", value, facts.record_id)]
