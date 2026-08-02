"""How much of a catalogue would pie-parser actually describe?

Run this before pointing a Zoho workflow at the describe endpoint. The parser
abstains far more often than people expect — with the ``kennametal_widia`` pack
it decodes ISO turning inserts richly and most other names not at all — and a
workflow switched on blind will write blank descriptions across a catalogue and
look, from Zoho's side, like it worked.

    python -m app.pie_coverage --from-db
    python -m app.pie_coverage --file item_names.txt
    python -m app.pie_coverage --from-db --show-misses 20

Reports counts and a sample, never a price or a cost.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Iterable

from .pie_describe import describe


def coverage(names: Iterable[str]) -> dict:
    names = [n.strip() for n in names if n and n.strip()]
    described = [describe(n) for n in names]

    confident = [d for d in described if d.confident]
    families = Counter(d.product_family or "unrouted" for d in described)
    misses = [d for d in described if not d.confident]

    return {
        "total": len(described),
        "confident": len(confident),
        "abstained": len(misses),
        "families": families.most_common(),
        "errors": sum(1 for d in described if d.error),
        "samples": [{"name": d.name, "description": d.description}
                    for d in confident[:5]],
        "misses": [d.name for d in misses],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-db", action="store_true",
                     help="Use the product names already synced into this platform")
    src.add_argument("--file", type=Path, help="One item name per line")
    parser.add_argument("--org", default=None)
    parser.add_argument("--show-misses", type=int, default=0,
                        help="Print this many names the parser did not recognise")
    args = parser.parse_args()

    if args.file:
        names = args.file.read_text(encoding="utf-8").splitlines()
    else:
        from sqlalchemy import select

        from .config import settings
        from .db import SessionLocal
        from .domain import models

        session = SessionLocal()
        try:
            org = args.org or settings.DEFAULT_ORG_ID
            names = [p.name for p in session.scalars(
                select(models.Product).where(models.Product.organization_id == org))]
        finally:
            session.close()

    if not names:
        print("No item names found. Sync items first, or pass --file.")
        return

    r = coverage(names)
    pct = (r["confident"] / r["total"] * 100) if r["total"] else 0.0

    print(f"item names            {r['total']:>7}")
    print(f"parser recognised     {r['confident']:>7}   ({pct:.0f}%)")
    print(f"parser abstained      {r['abstained']:>7}")
    if r["errors"]:
        print(f"engine errors         {r['errors']:>7}")
    print()
    print("routed to:")
    for family, n in r["families"]:
        print(f"  {n:>6}  {family}")

    if r["samples"]:
        print()
        print("what a good one looks like:")
        for s in r["samples"]:
            print(f"  {s['name']}")
            print(f"    → {s['description']}")

    if args.show_misses and r["misses"]:
        print()
        print(f"not recognised (first {args.show_misses}):")
        for name in r["misses"][:args.show_misses]:
            print(f"  {name}")

    print()
    if pct >= 60:
        print("Most of the catalogue decodes. A describe workflow is worth running.")
    elif pct > 0:
        print(f"Only {pct:.0f}% decodes. A workflow would leave most items untouched — "
              "correct behaviour, but check that is what you expect before enabling it.")
    else:
        print("Nothing in this catalogue decodes with the installed pack. A workflow "
              "would write nothing at all. These item names are probably not "
              "Kennametal/WIDIA shapes; the parser needs a pack for whatever "
              "manufacturer these come from.")


if __name__ == "__main__":
    main()
