"""The per-catalogue index: build, save, load, search.

One index per built catalogue, written beside its ``products.jsonl`` as
``retrieval.jsonl`` and stamped with the model that built it and the SHA-256
of the catalogue it was built from. ``ensure_index`` is the only entry point a
caller needs: it returns the index on disk when its stamp still describes the
catalogue and the model, and rebuilds otherwise — so a catalogue built before
this existed, or by a build that could not write its index, gets one on first
use rather than staying blind.

The vector space is IDF-weighted sparse features (``embedder.py``) with cosine
similarity, searched through an inverted index — posting lists per bucket —
because a query touches a few hundred buckets and a catalogue row touches the
same few hundred, so the walk is proportional to the overlap and not to the
catalogue. Pure Python on purpose: 6,700 records search in single-digit
milliseconds, and a numeric dependency for that would be a dependency for the
sake of a dependency.

**Deterministic by construction.** IDF, weights and similarities are rounded
before they are stored *and* before they are compared, ties are broken by
record id and then by row, and no timestamp is written into the file — so the
same catalogue through the same model yields the same bytes and the same
neighbours, in that process or the next one. ``test_retrieval`` pins both.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from .embedder import HashedNgramEmbedder

log = logging.getLogger("pie_portal.retrieval")

#: The file format. Bump when the layout below changes; an index in an older
#: format is rebuilt, never misread.
FORMAT = 1

#: Below this cosine similarity a neighbour is not offered at all. The floor is
#: what keeps "6205 2RS C3 bearing" from surfacing a carbide insert as its
#: nearest description: every text has *a* nearest neighbour, and a nearest
#: neighbour at 0.1 is noise with a record id attached. Chosen against the
#: shipped corpus — a paraphrase of a real description sits near 0.7, a
#: request written in words ("12mm carbide drill through coolant for
#: stainless") near 0.28, and the bearing at 0.13 — and pinned by
#: ``test_retrieval`` against that corpus.
MIN_SIMILARITY = 0.2

#: The record fields whose text a catalogue row is indexed by. The description
#: as it arrived plus the decoded slots that a customer would write in words —
#: family ("turning insert"), series, coating, and the dimensions as numbers.
#: Not the price, which is not in the record (``ingestion.item_master`` drops
#: it at the door), and not ``field_meta`` or provenance, which are about the
#: decode rather than the product.
TEXT_FIELDS = (
    "record_id", "description_raw", "description", "grade", "brand", "series",
    "product_family", "product_subfamily", "iso_shape", "coating",
    "coating_process", "chipbreaker", "cutting_dia_mm", "corner_radius_mm",
    "edge_length_mm", "thickness_mm", "flute_count", "shank_dia_mm", "loc_mm",
    "oal_mm",
)


def document_text(record: Mapping[str, Any]) -> str:
    """The text one catalogue record is indexed by."""
    parts: List[str] = []
    seen: set = set()
    for field_name in TEXT_FIELDS:
        value = record.get(field_name)
        if value in (None, ""):
            continue
        text = str(value)
        # ``description`` duplicates ``description_raw`` on most records;
        # indexing it twice would double-weight it against the decoded slots.
        if text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return " ".join(parts)


@dataclass(frozen=True)
class Stamp:
    """What built this index. Compared field by field by ``ensure_index``."""

    model_id: str
    dim: int
    records: int
    catalogue_sha256: str
    format: int = FORMAT

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Stamp":
        return cls(model_id=str(d["model_id"]), dim=int(d["dim"]),
                   records=int(d["records"]),
                   catalogue_sha256=str(d["catalogue_sha256"]),
                   format=int(d.get("format", 0)))

    def describes(self, model: HashedNgramEmbedder, catalogue_sha256: str) -> bool:
        return (self.format == FORMAT and self.model_id == model.model_id
                and self.dim == model.dim
                and self.catalogue_sha256 == catalogue_sha256)


@dataclass(frozen=True)
class Hit:
    """One neighbour. ``similarity`` is cosine in [0, 1], rounded to 4 places."""

    record_id: str
    similarity: float
    row: int


class RetrievalIndex:
    """An inverted index over one catalogue's records."""

    def __init__(self, stamp: Stamp, ids: List[str], idf: Dict[int, float],
                 postings: Dict[int, List[Tuple[int, float]]],
                 embedder: Optional[HashedNgramEmbedder] = None) -> None:
        self.stamp = stamp
        self.ids = ids
        self.idf = idf
        self.postings = postings
        self.embedder = embedder or HashedNgramEmbedder()

    # ── build ────────────────────────────────────────────────────────────────
    @classmethod
    def build(cls, records: Iterable[Mapping[str, Any]],
              embedder: Optional[HashedNgramEmbedder] = None,
              catalogue_sha256: str = "") -> "RetrievalIndex":
        embedder = embedder or HashedNgramEmbedder()
        ids: List[str] = []
        docs: List[Dict[int, float]] = []
        df: Dict[int, int] = defaultdict(int)
        for rec in records:
            record_id = rec.get("record_id")
            if record_id in (None, ""):
                continue
            feats = embedder.features(document_text(rec))
            ids.append(str(record_id))
            docs.append(feats)
            for bucket in feats:
                df[bucket] += 1
        n = len(ids)
        # Smoothed IDF, rounded once here so the value searched with is the
        # value saved — a query scored against an unrounded IDF in this process
        # and a rounded one in the next would rank ties differently.
        idf = {b: round(math.log((n + 1) / (c + 1)) + 1.0, 6) for b, c in df.items()}
        postings: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
        for row, feats in enumerate(docs):
            vec = {b: embedder.scale(tf) * idf[b] for b, tf in feats.items()}
            norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
            for b, w in vec.items():
                postings[b].append((row, round(w / norm, 4)))
        stamp = Stamp(model_id=embedder.model_id, dim=embedder.dim, records=n,
                      catalogue_sha256=catalogue_sha256)
        return cls(stamp, ids, idf, dict(postings), embedder)

    # ── search ───────────────────────────────────────────────────────────────
    def search(self, text: str, k: int = 5, exclude: Iterable[str] = (),
               min_similarity: float = MIN_SIMILARITY) -> List[Hit]:
        """The ``k`` nearest records to ``text`` at or above ``min_similarity``,
        best first, ties broken by record id. Never raises on empty text."""
        if k <= 0:
            return []
        feats = self.embedder.features(text)
        vec = {b: self.embedder.scale(tf) * self.idf[b]
               for b, tf in feats.items() if b in self.idf}
        if not vec:
            return []
        norm = math.sqrt(sum(w * w for w in vec.values())) or 1.0
        acc: Dict[int, float] = defaultdict(float)
        for b, w in vec.items():
            for row, dw in self.postings.get(b, ()):
                acc[row] += w * dw
        excluded = {str(e) for e in exclude}
        best: Dict[str, Tuple[float, int]] = {}
        for row, dot in acc.items():
            sim = round(dot / norm, 4)
            if sim < min_similarity:
                continue
            record_id = self.ids[row]
            if record_id in excluded:
                continue
            # A record id that occurs twice keeps its best row: the catalogue
            # build already collapses duplicates, and this keeps the index
            # honest about one that slipped past.
            held = best.get(record_id)
            if held is None or (sim, -row) > (held[0], -held[1]):
                best[record_id] = (sim, row)
        ordered = sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0], kv[1][1]))
        return [Hit(record_id=rid, similarity=sim, row=row)
                for rid, (sim, row) in ordered[:k]]

    # ── persistence ──────────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        """Write atomically: stamp line, ids line, idf line, postings line."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(self.stamp.to_dict(), sort_keys=True) + "\n")
            fh.write(json.dumps(self.ids) + "\n")
            fh.write(json.dumps({str(b): v for b, v in sorted(self.idf.items())}) + "\n")
            # Flat ``[row, weight, row, weight, ...]`` per bucket: a third the
            # bytes and the parse time of a list of pairs, and the parse is the
            # load time.
            fh.write(json.dumps({str(b): [x for r, w in rows for x in (r, w)]
                                 for b, rows in sorted(self.postings.items())}) + "\n")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path,
             embedder: Optional[HashedNgramEmbedder] = None) -> "RetrievalIndex":
        """Read an index written by :meth:`save`. Raises ``ValueError`` on a
        file this format does not describe; the caller rebuilds."""
        with Path(path).open("r", encoding="utf-8") as fh:
            lines = [fh.readline() for _ in range(4)]
        if not all(lines):
            raise ValueError(f"{path}: truncated retrieval index")
        stamp = Stamp.from_dict(json.loads(lines[0]))
        if stamp.format != FORMAT:
            raise ValueError(f"{path}: retrieval index format {stamp.format}, "
                             f"this code reads {FORMAT}")
        ids = [str(i) for i in json.loads(lines[1])]
        idf = {int(b): float(v) for b, v in json.loads(lines[2]).items()}
        postings = {int(b): list(zip(map(int, flat[0::2]), map(float, flat[1::2])))
                    for b, flat in json.loads(lines[3]).items()}
        return cls(stamp, ids, idf, postings, embedder)


# ── the catalogue on disk ────────────────────────────────────────────────────
def index_path_for(catalogue_path: Path) -> Path:
    """Where a catalogue's index lives: beside it, so the two travel together."""
    return Path(catalogue_path).with_name("retrieval.jsonl")


def catalogue_sha256(catalogue_path: Path) -> str:
    digest = hashlib.sha256()
    with Path(catalogue_path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _records(catalogue_path: Path) -> Iterator[Dict[str, Any]]:
    with Path(catalogue_path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_stamp(index_path: Path) -> Optional[Stamp]:
    """The stamp on an index file, from its first line only, or None."""
    try:
        with Path(index_path).open("r", encoding="utf-8") as fh:
            return Stamp.from_dict(json.loads(fh.readline()))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ensure_index(catalogue_path: Path,
                 embedder: Optional[HashedNgramEmbedder] = None) -> RetrievalIndex:
    """The index for this catalogue, current for this model — built if it must be.

    Raises ``FileNotFoundError`` when there is no catalogue; an index over
    nothing is not a thing to return. Any *unreadable* index is rebuilt and
    the reason logged, because the catalogue is the source of truth and the
    index is derived from it — which is also why a build that could not write
    its index leaves a working catalogue behind rather than a failed one.
    """
    embedder = embedder or HashedNgramEmbedder()
    catalogue_path = Path(catalogue_path)
    if not catalogue_path.exists():
        raise FileNotFoundError(catalogue_path)
    sha = catalogue_sha256(catalogue_path)
    path = index_path_for(catalogue_path)
    if path.exists():
        try:
            index = RetrievalIndex.load(path, embedder)
            if index.stamp.describes(embedder, sha):
                return index
            log.info("retrieval index at %s is for another catalogue or model "
                     "(%s); rebuilding", path, index.stamp)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            log.warning("retrieval index at %s is unreadable; rebuilding",
                        path, exc_info=True)
    index = RetrievalIndex.build(_records(catalogue_path), embedder, sha)
    index.save(path)
    log.info("retrieval index built at %s: %d records, model %s",
             path, index.stamp.records, index.stamp.model_id)
    return index


def describe(catalogue_path: Path,
             embedder: Optional[HashedNgramEmbedder] = None) -> Optional[Dict[str, Any]]:
    """What a screen says about this catalogue's index, or None if it has none.

    ``current`` is whether the index on disk describes the catalogue on disk
    through the model this code runs. A stale one is still reported — with
    ``current: False`` — because the next resolution will rebuild it, and a
    reader should know the stamp they see is the one being replaced.
    """
    catalogue_path = Path(catalogue_path)
    stamp = read_stamp(index_path_for(catalogue_path))
    if stamp is None or not catalogue_path.exists():
        return None
    embedder = embedder or HashedNgramEmbedder()
    return {
        "model_id": stamp.model_id,
        "dim": stamp.dim,
        "records": stamp.records,
        "current": stamp.describes(embedder, catalogue_sha256(catalogue_path)),
        "min_similarity": MIN_SIMILARITY,
    }
