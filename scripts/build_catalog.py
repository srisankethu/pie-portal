#!/usr/bin/env python3
"""Build the decoded PIE product catalogue (backend/data/products.jsonl).

Runs pie-parser (the ./pie-parser submodule) over the Kennametal/WIDIA nomenclature
corpus. The output is deterministic and large (~13 MB), so it is gitignored and
rebuilt from source rather than committed.

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
    ap.add_argument("--force", action="store_true", help="rebuild even if it exists")
    args = ap.parse_args()

    from app.catalog import build_catalog
    from app.config import settings

    if not (settings.PIE_PARSER_ROOT / "tools" / "run_parser.py").exists():
        print("error: pie-parser not found. Fetch it with:\n"
              "  ./scripts/setup_pie_parser.sh", file=sys.stderr)
        return 2

    out = build_catalog(force=args.force)
    n = sum(1 for _ in out.open(encoding="utf-8"))
    print(f"Built catalogue: {out} ({n} products)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
