"""Export a tenant's training pairs for fine-tuning a dense embedder.

One JSON line per active phrase alias: the customer's words as the anchor,
the chosen record's indexed text as the positive, and the scope, so a
training script can also draw in-tenant negatives. One organization per
file, always — a model tuned on one tenant's pairs is that tenant's, and a
file mixing two would be the leak the row policies exist to prevent.

The training itself is not in this codebase. It needs ``torch`` and
``sentence-transformers``, which are far heavier than anything the backend
runs, and it runs once, offline, on a machine with the base model's weights.
The recipe is in ``docs/per-company-catalogues.md`` §12. The output of that
recipe — ``model.onnx`` and ``tokenizer.json`` in one directory — is what
``PIE_EMBEDDER_MODEL_DIR`` points at.

    python -m app.retrieval.export_pairs --org X --catalogue <products.jsonl> --out pairs.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from .index import document_text


def _records(catalogue_path: Path) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    with Path(catalogue_path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                out[str(rec.get("record_id"))] = rec
    return out


def pairs_for(session: Session, organization_id: str,
              records: Dict[str, dict]) -> Iterable[dict]:
    rows = session.scalars(select(models.CustomerPhraseAlias).where(
        models.CustomerPhraseAlias.organization_id == organization_id,
        models.CustomerPhraseAlias.active.is_(True)).order_by(
        models.CustomerPhraseAlias.identity_id, models.CustomerPhraseAlias.phrase_key))
    for row in rows:
        rec = records.get(row.target_record_id)
        if rec is None:
            continue                       # not in this catalogue: not a pair here
        yield {"anchor": row.phrase, "positive": document_text(rec),
               "record_id": row.target_record_id, "scope": row.identity_id}


def export(session: Session, organization_id: str, catalogue_path: Path,
           out: Path) -> int:
    records = _records(catalogue_path)
    n = 0
    with Path(out).open("w", encoding="utf-8") as fh:
        for pair in pairs_for(session, organization_id, records):
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
            n += 1
    return n


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", required=True)
    parser.add_argument("--catalogue", required=True, type=Path,
                        help="the company's products.jsonl the records are read from")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    from ..db import SessionLocal  # noqa: PLC0415 — the CLI's own session

    session = SessionLocal()
    try:
        n = export(session, args.org, args.catalogue, args.out)
    finally:
        session.close()
    print(f"wrote {n} pairs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
