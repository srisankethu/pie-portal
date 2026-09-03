"""A dense, meaning-aware embedder and the re-ranking it plugs into.

The hashed n-gram index (``index.py``) matches on spelling. It cannot tell
that "stainless" and "SS" are one thing unless a tenant's quotes have taught
it. A dense embedder — a small language model run locally — maps text by
meaning, and this module is the seam it plugs into:

* :class:`DenseEmbedder` is the protocol: a ``model_id``, a ``dim`` and
  ``embed(texts) -> unit vectors``. Anything satisfying it can be used, which
  is how the tests use a fake and how a tenant-tuned model is used later.
* :class:`OnnxEmbedder` runs a sentence-embedding model exported to ONNX from
  a local directory: ``model.onnx`` and ``tokenizer.json``. No network at
  build or quote time; the weights are a file this deployment ships, and the
  model id on the stamp is that file's hash. The runtime and tokenizer are
  optional dependencies (``requirements-embed.txt``), imported only when a
  model directory is configured, so a deployment without them is unchanged.
* :class:`DenseReranker` re-orders the hashed index's candidates by cosine
  similarity of dense vectors. **Re-rank, not replace**: the hashed index
  stays the candidate source because on codes and designations it is the
  better instrument, and scoring only the handful of candidates keeps the
  cost to a few vector products rather than a pass over the catalogue. Record
  vectors are computed on first use and persisted beside the catalogue,
  stamped by model, so the next process reads them back.

Everything downstream is unchanged: a re-ranked candidate is still compared
by the engine, still POSSIBLE, still never selected. The dense similarity is
carried beside the candidate as provenance, never as its score.

Determinism: the same weights and the same runtime give the same vectors,
rounded before they are stored or compared; ties break by record id.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol, Sequence

from .index import Hit, document_text

log = logging.getLogger("pie_portal.retrieval.dense")

#: Vector components are rounded to this many places before storage and
#: comparison, so a vector read back from disk compares as it did in memory.
PLACES = 6


class DenseEmbedder(Protocol):
    model_id: str
    dim: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        """One unit-length vector per text, in order."""
        ...


def _unit(vector: Iterable[float]) -> List[float]:
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [round(v / norm, PLACES) for v in values]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class OnnxEmbedder:
    """A sentence-embedding model exported to ONNX, run on CPU.

    Expects ``model_dir/model.onnx`` and ``model_dir/tokenizer.json`` — the
    layout ``optimum`` and ``sentence-transformers`` both export. Mean-pools
    the last hidden state over the attention mask and normalises, which is
    what those models are trained for. ``model_id`` is ``onnx/<sha256 of the
    weights>[:16]`` so an index built by one file is never read as another's.
    """

    def __init__(self, model_dir: Path, max_length: int = 64) -> None:
        try:
            import numpy as np  # noqa: PLC0415 — optional dependency
            import onnxruntime as ort  # noqa: PLC0415
            from tokenizers import Tokenizer  # noqa: PLC0415
        except ImportError as e:  # pragma: no cover — exercised by deployment
            raise RuntimeError(
                "A dense embedder needs onnxruntime, tokenizers and numpy: "
                "pip install -r requirements-embed.txt") from e
        model_dir = Path(model_dir)
        weights = model_dir / "model.onnx"
        if not weights.exists() or not (model_dir / "tokenizer.json").exists():
            raise FileNotFoundError(
                f"{model_dir} must hold model.onnx and tokenizer.json")
        self._np = np
        self._session = ort.InferenceSession(str(weights),
                                             providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=max_length)
        self._tokenizer.enable_padding(length=None)
        self._inputs = {i.name for i in self._session.get_inputs()}
        digest = hashlib.sha256(weights.read_bytes()).hexdigest()[:16]
        self.model_id = f"onnx/{digest}"
        self.dim = int(self._session.get_outputs()[0].shape[-1])

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        np = self._np
        encoded = self._tokenizer.encode_batch(list(texts))
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0]           # (n, tokens, dim)
        weights = mask[..., None].astype(hidden.dtype)
        pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
        return [_unit(row) for row in pooled.tolist()]


@dataclass(frozen=True)
class RerankedHit:
    record_id: str
    similarity: float          # the hashed index's, kept as it was
    dense_similarity: float    # cosine of dense vectors, rounded
    row: int


class DenseReranker:
    """Re-orders candidates by dense similarity, remembering record vectors."""

    def __init__(self, catalogue_path: Path, embedder: DenseEmbedder,
                 records: Optional[Dict[str, dict]] = None) -> None:
        self.embedder = embedder
        self.model_id = embedder.model_id
        self._records = records or {}
        self._vectors: Dict[str, List[float]] = {}
        self._path = self.sidecar_for(catalogue_path, embedder.model_id)
        self._load()

    @staticmethod
    def sidecar_for(catalogue_path: Path, model_id: str) -> Path:
        slug = model_id.replace("/", "-")
        return Path(catalogue_path).with_name(f"dense.{slug}.jsonl")

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        row = json.loads(line)
                        self._vectors[str(row["record_id"])] = [float(v) for v in row["v"]]
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("dense vectors at %s are unreadable; recomputing on use",
                        self._path, exc_info=True)
            self._vectors = {}

    def _remember(self, record_ids: Sequence[str], vectors: Sequence[List[float]]) -> None:
        for record_id, vector in zip(record_ids, vectors):
            self._vectors[record_id] = vector
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                for record_id, vector in zip(record_ids, vectors):
                    fh.write(json.dumps({"record_id": record_id, "v": vector}) + "\n")
        except OSError:
            log.warning("could not persist dense vectors to %s", self._path, exc_info=True)

    def vectors_for(self, record_ids: Sequence[str],
                    lookup) -> Dict[str, List[float]]:
        """Vectors for these records, embedding the ones not yet known.
        ``lookup(record_id)`` returns the catalogue record, or None."""
        missing = []
        texts = []
        for record_id in record_ids:
            if record_id in self._vectors:
                continue
            rec = self._records.get(record_id) or lookup(record_id)
            if rec is None:
                continue
            missing.append(record_id)
            texts.append(document_text(rec))
        if missing:
            self._remember(missing, [_unit(v) for v in self.embedder.embed(texts)])
        return {r: self._vectors[r] for r in record_ids if r in self._vectors}

    def rerank(self, text: str, hits: Sequence[Hit], lookup) -> List[RerankedHit]:
        """``hits`` in dense-similarity order, best first, ties by record id.
        A hit whose record cannot be embedded keeps its place at the end, in
        the hashed order, with a dense similarity of 0."""
        if not hits or not text.strip():
            return []
        query = _unit(self.embedder.embed([text])[0])
        vectors = self.vectors_for([h.record_id for h in hits], lookup)
        scored = []
        for order, h in enumerate(hits):
            vector = vectors.get(h.record_id)
            dense = round(_dot(query, vector), 4) if vector is not None else 0.0
            scored.append((vector is None, -dense, h.record_id, order, h))
        scored.sort(key=lambda t: t[:4])
        return [RerankedHit(record_id=h.record_id, similarity=h.similarity,
                            dense_similarity=-neg if not absent else 0.0, row=h.row)
                for absent, neg, _, _, h in scored]


def embedder_from_settings(model_dir: Optional[Path]) -> Optional[DenseEmbedder]:
    """The configured dense embedder, or None when none is configured. A
    directory that is configured and unusable is reported and treated as
    none: the hashed index answers exactly as before."""
    if not model_dir:
        return None
    try:
        return OnnxEmbedder(Path(model_dir))
    except Exception:  # noqa: BLE001 — a missing optional model must not take resolution down
        log.warning("dense embedder at %s could not be loaded; resolving without it",
                    model_dir, exc_info=True)
        return None
