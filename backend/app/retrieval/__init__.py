"""Nearest-neighbour retrieval over a company's decoded catalogue.

A **candidate generator, and nothing more.** The rule engine in pie-parser
answers an RFQ line by decoding it to a spec and scoring every catalogue record
against that spec — which is exact, explainable, and blind to a line the pack
cannot decode. "12mm carbide drill through coolant for stainless" has no ISO
code in it, so the engine's spec is nearly empty, every record passes its gates
at the same vacuous score, and the six it shows are the first six by sort order.

This package answers a different question — *which catalogue descriptions read
most like this text?* — and hands those records to the engine to be compared.
It never scores fit, never assigns a relationship, and never confirms an
identity:

* every retrieved record goes through the engine's own ``compare_geometry``
  against the decoded spec, and a record the engine gates out is dropped;
* a retrieved record is ``POSSIBLE`` and marked ``retrieved``, whatever its
  similarity — similarity is a statement about *text*, and CLAUDE.md §1 is
  explicit that an equivalence score is policy and never an identity, so a
  text score is further still from one;
* retrieved records are appended after the engine's ranked suggestions and
  the auto-selection decision is taken before they are, so they can never
  become the supply product.

The embedding is a hashed character-n-gram model: deterministic, offline, and
dependency-free, so a catalogue and a model id fix the neighbours exactly —
the same property pie-parser has for its own reruns, and the one that lets a
retrieved candidate be explained months later. A neural embedder plugs in
behind the same ``features`` protocol; the index is stamped with the model that
built it so two indexes are never read as one.

``aliases.py`` is the one place the index learns from people: a customer's
confirmed codes and quoted phrases, searched for that customer only, so a near
miss of something somebody already chose is offered back — as an option, never
as the answer. ``vocabulary.py`` is what those choices add up to: which of a
tenant's words go with which technical attributes, as counts, used to widen a
search and to explain a candidate — never to score one.

Deterministic by contract: listed in ``test_layer_boundaries.DETERMINISTIC`` and
imported by ``catalog`` and ``pie_service``, both of which the deterministic
packages import, so nothing here may reach ``ai/``.
"""
from __future__ import annotations

from .aliases import AliasHit, AliasIndex
from .embedder import HashedNgramEmbedder, tokens
from .vocabulary import Hint, Vocabulary
from .index import (
    MIN_SIMILARITY,
    Hit,
    RetrievalIndex,
    Stamp,
    describe,
    document_text,
    ensure_index,
    index_path_for,
)

__all__ = [
    "MIN_SIMILARITY", "AliasHit", "AliasIndex", "HashedNgramEmbedder", "Hint",
    "Hit", "RetrievalIndex", "Stamp", "Vocabulary",
    "describe", "document_text", "ensure_index", "index_path_for", "tokens",
]
