#!/usr/bin/env python3
"""Seed and build every connected company's decoded PIE catalogue.

Each company keeps a catalogue per manufacturer it sells, and each decodes
its own price lists (`backend/data/catalogues/<connection_id>/<catalogue_key>/
products.jsonl`) through each file's own saved decoding config; the company
resolves against the union of them, written beside those under `_union/`. A
company that has uploaded nothing inherits the corpus shipping in the
./pie-parser submodule,
once — that seed is what keeps a deployment resolving across the move from one
shared catalogue to one per company.

The output is deterministic and large (~13 MB each), so it is gitignored and
rebuilt from the corpus rather than committed.

The running app does both of these on start-up. This is the same two functions
for a machine where the app is not running yet — a fresh clone straight after
`python -m app.bootstrap`, or a rebuild after clearing `backend/data/`.

Usage:
    python scripts/build_catalog.py [--force]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="rebuild every company's catalogue, not only the missing")
    args = ap.parse_args()

    from sqlalchemy import select

    from app import catalog
    from app.config import settings
    from app.db import SessionLocal
    from app.domain import models

    if not (settings.PIE_PARSER_ROOT / "tools" / "run_parser.py").exists():
        print("error: pie-parser not found. Fetch it with:\n"
              "  ./scripts/setup_pie_parser.sh", file=sys.stderr)
        return 2

    with SessionLocal() as session:
        seeded = catalog.seed_company_catalogues(session)
        for row in seeded:
            print(f"Seeded the shipped corpus to company {row['connection_id']}")
        if args.force:
            # `ensure_` skips a catalogue whose file is already there, so a
            # forced rebuild removes it first rather than being a second build
            # path. Per catalogue: a company keeps one per manufacturer.
            for cid, key in session.execute(
                    select(models.CompanyCorpus.connection_id,
                           models.CompanyCorpus.catalogue_key).distinct()):
                catalog.company_catalog_path(cid, key).unlink(missing_ok=True)
        built = catalog.ensure_company_catalogues(session, actor="build_catalog.py")
        session.commit()

    for row in built:
        out = catalog.company_catalog_path(row["connection_id"], row["catalogue_key"])
        n = sum(1 for _ in out.open(encoding="utf-8"))
        print(f"Built catalogue: {out} ({n} products)")
    if not built:
        print("Nothing to build: every connected company's catalogue is "
              "already on disk, or no company has a corpus yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
