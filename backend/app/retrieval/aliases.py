"""Confirmed customer codes as an index the retrieval pass can search.

A confirmed mapping (``identity.service.confirm_code_mapping``) is a person
asserting "this customer's code means that product". The engine already reads
those exactly: the same code, from the same customer, resolves authoritatively
and never reaches retrieval. What this adds is the *near miss* — the code with
a quantity after it, a hyphen dropped, a digit transposed, a label in front —
which the engine cannot read as the code and would otherwise answer with
nothing, or with whatever the catalogue's descriptions happen to resemble.

The rules are the ones ``app/retrieval`` already has, plus one:

* **A customer's aliases answer only that customer.** A code is meaningful
  inside the relationship that confirmed it; another customer's ``PART-0042``
  is a different part. An unscoped line searches no aliases at all.
* An alias hit is offered as ``POSSIBLE``, compared by the engine, flagged
  with the code it matched, and never selected. The confirmation gate is
  untouched: a person answered a question once, and this shows them their
  own answer beside a line that nearly repeats it — it does not answer for
  them.

Deterministic like the catalogue index — the same rows through the same model
give the same neighbours — and cheap: an organization's confirmed codes are
hundreds of short strings, so the index is rebuilt whenever the mapping
store's fingerprint changes and memoised by ``pie_service`` against it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .embedder import HashedNgramEmbedder
from .index import MIN_SIMILARITY, RetrievalIndex


@dataclass(frozen=True)
class AliasHit:
    """One confirmed code near the text: which record it names, how near, and
    the code itself as it was confirmed."""

    record_id: str
    similarity: float
    alias: str
    #: ``"code"`` — a confirmed customer code; ``"phrase"`` — words a person
    #: had quoted as this record for this customer. Same index, different
    #: sentence beside the candidate.
    kind: str = "code"


class AliasIndex:
    """Confirmed codes, indexed per customer scope."""

    def __init__(self, aliases: Iterable[Tuple[str, ...]],
                 embedder: Optional[HashedNgramEmbedder] = None) -> None:
        """``aliases`` are ``(scope, text, record_id[, kind])`` — the customer
        identity the text was confirmed or quoted under, the text, the
        catalogue record it names, and ``"code"`` (the default) or
        ``"phrase"``."""
        self.embedder = embedder or HashedNgramEmbedder()
        grouped: Dict[str, List[Tuple[str, str, str]]] = {}
        for scope, text, record_id, *rest in aliases:
            if not (scope and text and record_id):
                continue
            kind = str(rest[0]) if rest else "code"
            grouped.setdefault(str(scope), []).append((str(text), str(record_id), kind))
        self._by_scope: Dict[str, Tuple[RetrievalIndex, List[Tuple[str, str]]]] = {}
        for scope, rows in grouped.items():
            # Sorted so the same rows in any order build the same index. Each
            # alias is its own row; ``RetrievalIndex.search`` keeps the best row
            # per record, so two texts for one product yield one hit.
            rows.sort()
            records = [{"record_id": record_id, "description_raw": text}
                       for text, record_id, _ in rows]
            self._by_scope[scope] = (
                RetrievalIndex.build(records, self.embedder),
                [(text, kind) for text, _, kind in rows])
        self.aliases = sum(len(texts) for _, texts in self._by_scope.values())

    @property
    def model_id(self) -> str:
        return self.embedder.model_id

    def count(self, scope: Optional[str]) -> int:
        """How many confirmed codes ``scope`` has — what a search for that
        customer actually searches. Zero for no scope."""
        if not scope:
            return 0
        entry = self._by_scope.get(str(scope))
        return len(entry[1]) if entry is not None else 0

    def search(self, scope: Optional[str], text: str, k: int = 5,
               exclude: Iterable[str] = (),
               min_similarity: float = MIN_SIMILARITY) -> List[AliasHit]:
        """The confirmed codes of ``scope`` nearest to ``text``. Nothing for a
        line with no customer, and nothing for a customer with no codes."""
        if not scope or not text:
            return []
        entry = self._by_scope.get(str(scope))
        if entry is None:
            return []
        index, texts = entry
        return [AliasHit(record_id=h.record_id, similarity=h.similarity,
                         alias=texts[h.row][0], kind=texts[h.row][1])
                for h in index.search(text, k=k, exclude=exclude,
                                      min_similarity=min_similarity)]
