#!/usr/bin/env python3
"""Read-only. How much of what this business actually TRADED reaches the catalogue?

`docs/concepts/01-application-engineering.md` §1 answers a neighbouring question
and answers it over the wrong denominator for this one. Its 21.6% union is
computed across all 15,028 rows of the Zoho item master, and an item master is a
graveyard: dead stock, one-time buys, migration artifacts, 6,146 rows carrying no
manufacturer at all. Nobody quotes out of it. A coverage figure whose denominator
is mostly items no salesperson will ever type understates what the catalogue does
for the work, and it is the figure the strategic decision was taken against.

So this measures the same coverage over **invoice lines** — the products that
actually moved — and reports it three ways, because they answer different
questions and can differ by a lot:

* **by distinct product** — how much of the traded catalogue is reachable
* **frequency-weighted** — how much of the day's line-picking is reachable
* **value-weighted at SELLING price** — how much of the revenue is reachable

READ THIS BEFORE QUOTING THE NUMBER. It is a LOWER BOUND, not the answer, and
the bias is structural rather than a sampling accident: **a line became an
invoice line because somebody could already identify the product.** The
denominator therefore conditions on the very outcome the product exists to
change, and it systematically excludes the lines nobody could identify — the
enquiry that was never quoted, the part that could not be cross-referenced, the
customer who went elsewhere. Those are exactly the lines a cross-referencing
engine is for. Whatever this prints, the real reachable population is larger and
this cannot see how much larger. Closing that gap needs forward capture of ALL
inbound lines including the unquoted ones, which is a different instrument.

**Scope: invoice lines only, and that is a limitation rather than a choice.**
There is no estimate line-level history anywhere in this system to measure
instead. Estimates are write-only — ``routers/quote.create_estimate`` pushes one
to Zoho, and there is no estimates table, no ``_sync_estimates`` in
``ingestion/sync.py``, and no estimate pull in any ``ingestion/erp/`` connector.
Quoted-but-not-won lines, which are the closest available proxy for demand the
catalogue failed to reach, are not recorded at all. An invoice line is a WON
line, so this denominator is narrower still than "everything we quoted".

**It goes through ``pie_service``, not ``identity.AuthoritativeIndex``.** Same
reason ``measure_crossbrand.py`` states for going through ``pie_service.resolve``
rather than a pie-parser CLI: the product resolves through this surface, so a
measurement taken anywhere else measures a path nothing uses. Concretely,
``pie_service.catalog_available`` is the property that separates "the pack does
not cover this item" from "nobody asked the pack" — both return None from
``lookup_record``, only the first is evidence, and a run with the catalogue
absent must report UNAVAILABLE rather than 0%.

Identity coverage is read from ``Product.pie_record_id``, which
``sync._link_catalog`` already writes on every item pull through that same
``lookup_record``. Nothing in the codebase reads it back, so on a synced database
this half of the measurement is a JOIN rather than a re-resolution. ``--reresolve``
recomputes it live through ``SalesTxn.product_id -> ItemConnectorRecord.sku ->
pie_service.lookup_record`` — the real SKU path, because ``Product`` has no
``sku`` column and the sync never persists one — and reports where the two
disagree, which is how a link written under a superseded catalogue shows up.

Geometry coverage is gated on **full ISO slot fill** (shape + edge length +
corner radius), never on a family route: §3 of that document measures the family
route misrouting an EMUGE screwdriver and an ``M3X11`` screw into
``turning_insert``, and records that restricting to full slot fill drops definite
misroutes to 1 in 2,057. Row confidence is deliberately *not* a gate here — an
item name carries no grade column, so it scores 0.00 by construction, which
`docs/concepts/13-confidence-and-input-completeness.md` establishes is the engine
abstaining on a missing field rather than failing on the name.

**No cost and no margin appear in this output, by construction.** Value weighting
is ``SalesTxn.line_revenue``, the net selling figure. Nothing here reads
``CostRecord``, and the report carries no field from which cost could be derived.

This script only reads. It prints what it finds and changes nothing.

Run:  cd backend && python3 ../scripts/measure_quoted_line_coverage.py
      cd backend && python3 ../scripts/measure_quoted_line_coverage.py --json out.json
      cd backend && python3 ../scripts/measure_quoted_line_coverage.py --reresolve
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select                            # noqa: E402

from app.db import SessionLocal                          # noqa: E402
from app.domain import models                            # noqa: E402
from app.pie_service import pie_service                  # noqa: E402

#: The slots that must ALL be present for a decode to count as geometry.
#: Partial fill is what §3 measured contaminating the family route, so a
#: two-of-three row is not "mostly decoded", it is not decoded.
ISO_REQUIRED = ("iso_shape", "edge_length_mm", "corner_radius_mm")


# --------------------------------------------------------------------------- #
# Coverage accounting
# --------------------------------------------------------------------------- #


class Bucket:
    """One entity's three denominators and the numerators against them.

    Products are counted distinct; lines and value accumulate per invoice line.
    ``Decimal`` throughout for the money, per CLAUDE.md §1 — a float sum of
    thousands of line revenues is not the number on the invoice.
    """

    def __init__(self) -> None:
        self.products: set = set()
        self.identity_products: set = set()
        self.geometry_products: set = set()
        self.lines = 0
        self.identity_lines = 0
        self.geometry_lines = 0
        self.union_lines = 0
        self.value = Decimal("0")
        self.identity_value = Decimal("0")
        self.geometry_value = Decimal("0")
        self.union_value = Decimal("0")
        #: Lines whose product row is missing entirely — a referential gap, not
        #: a coverage miss. Counted apart so it can never be read as either.
        self.orphan_lines = 0

    def observe(self, product_id: str, revenue: Decimal,
                identity: bool, geometry: bool) -> None:
        self.products.add(product_id)
        self.lines += 1
        self.value += revenue
        if identity:
            self.identity_products.add(product_id)
            self.identity_lines += 1
            self.identity_value += revenue
        if geometry:
            self.geometry_products.add(product_id)
            self.geometry_lines += 1
            self.geometry_value += revenue
        if identity or geometry:
            self.union_lines += 1
            self.union_value += revenue

    def to_dict(self) -> Dict[str, Any]:
        def share(num: float, den: float) -> Optional[float]:
            return round(100.0 * num / den, 2) if den else None
        n_prod = len(self.products)
        union_products = self.identity_products | self.geometry_products
        return {
            "distinct_products": n_prod,
            "lines": self.lines,
            "orphan_lines": self.orphan_lines,
            # Money is reported as a string: it is a Decimal, and a JSON float
            # would silently re-introduce the binary rounding the model avoids.
            "selling_value": str(self.value),
            "by_product": {
                "identity": len(self.identity_products),
                "geometry": len(self.geometry_products),
                "union": len(union_products),
                "identity_pct": share(len(self.identity_products), n_prod),
                "geometry_pct": share(len(self.geometry_products), n_prod),
                "union_pct": share(len(union_products), n_prod),
            },
            "by_line": {
                "identity": self.identity_lines,
                "geometry": self.geometry_lines,
                "union": self.union_lines,
                "identity_pct": share(self.identity_lines, self.lines),
                "geometry_pct": share(self.geometry_lines, self.lines),
                "union_pct": share(self.union_lines, self.lines),
            },
            "by_value": {
                "identity": str(self.identity_value),
                "geometry": str(self.geometry_value),
                "union": str(self.union_value),
                "identity_pct": share(float(self.identity_value), float(self.value)),
                "geometry_pct": share(float(self.geometry_value), float(self.value)),
                "union_pct": share(float(self.union_value), float(self.value)),
            },
        }


# --------------------------------------------------------------------------- #
# The two coverage tests
# --------------------------------------------------------------------------- #


def sku_index(session) -> Dict[str, str]:
    """product_id -> SKU, via ``ItemConnectorRecord``.

    ``Product`` has no ``sku`` column and the sync never persists one, so this
    is the only path from a traded line to a catalogue number. Where one
    product carries several connector records the first non-empty SKU wins and
    the rest are ignored: they are the same item as several books hold it, and
    the lookup is exact either way.
    """
    out: Dict[str, str] = {}
    rows = session.execute(
        select(models.ItemConnectorRecord.product_id, models.ItemConnectorRecord.sku)
    ).all()
    for product_id, sku in rows:
        if product_id and sku and str(sku).strip() and product_id not in out:
            out[product_id] = str(sku).strip()
    return out


def decode_geometry(names: Iterable[Tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
    """Decode item NAMES and keep only rows with full ISO slot fill.

    Returns ``product_id -> {slot: value}`` for the rows that passed the gate;
    a product absent from the mapping did not decode, which is not the same as
    it having no geometry.

    The engine is driven exactly as ``app/catalog.build_catalog`` drives it —
    one pack, one ``ParserPipeline``, the same ``RunProfile`` — because a second
    way of invoking it would be a second thing to keep in step. Grade is None
    because an item name has no grade column; see the module docstring for why
    that costs nothing here.
    """
    from app.config import settings

    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from engine.model import RawRecord
    from engine.pack import load_pack
    from engine.pipeline import ParserPipeline, RunProfile

    records = [
        RawRecord(record_id=pid, description=name or "", grade=None, payload={},
                  source_file="item_master", source_sheet="-", source_row=i)
        for i, (pid, name) in enumerate(names, start=1)
    ]
    if not records:
        return {}
    pack = load_pack(settings.PIE_PACK)
    products, _report, quarantine = ParserPipeline(pack, RunProfile()).run(records)
    out: Dict[str, Dict[str, Any]] = {}
    for rec in list(products) + list(quarantine):
        if all(rec.get(slot) is not None for slot in ISO_REQUIRED):
            out[rec["record_id"]] = {slot: rec.get(slot) for slot in ISO_REQUIRED}
    return out


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, default=None,
                    help="write the full report here")
    ap.add_argument("--reresolve", action="store_true",
                    help="recompute the identity link live through the SKU path "
                         "instead of trusting Product.pie_record_id, and report "
                         "where the two disagree")
    ap.add_argument("--no-geometry", action="store_true",
                    help="skip the name decode (identity coverage only)")
    args = ap.parse_args()

    session = SessionLocal()
    try:
        return _run(session, args)
    finally:
        session.close()


def build_report(session, args) -> Dict[str, Any]:
    """The measurement. Pure: reads the session, returns the report, prints nothing."""
    available = pie_service.catalog_available

    lines = session.execute(
        select(models.SalesTxn.organization_id, models.SalesTxn.connection_id,
               models.SalesTxn.product_id, models.SalesTxn.line_revenue)
    ).all()

    products = {
        p.product_id: p for p in session.scalars(select(models.Product))
    }
    skus = sku_index(session)

    # ── identity ─────────────────────────────────────────────────────────────
    traded_ids = {pid for _, _, pid, _ in lines if pid}
    persisted = {pid for pid in traded_ids
                 if getattr(products.get(pid), "pie_record_id", None)}
    identity: set = set(persisted)
    disagreement: Dict[str, List[str]] = {"persisted_only": [], "live_only": []}
    if args.reresolve and available:
        live = {pid for pid in traded_ids
                if pie_service.lookup_record(skus.get(pid)) is not None}
        disagreement["persisted_only"] = sorted(persisted - live)
        disagreement["live_only"] = sorted(live - persisted)
        identity = live

    # ── geometry ─────────────────────────────────────────────────────────────
    geometry: set = set()
    geometry_ran = False
    if not args.no_geometry and available and traded_ids:
        named = [(pid, getattr(products.get(pid), "name", "") or "")
                 for pid in sorted(traded_ids)]
        geometry = set(decode_geometry(named))
        geometry_ran = True

    # ── accumulate, per entity ───────────────────────────────────────────────
    conns = {c.connection_id: c for c in session.scalars(select(models.ZohoConnection))}
    orgs = {o.organization_id: o for o in session.scalars(select(models.Organization))}
    buckets: Dict[Tuple[str, Optional[str]], Bucket] = defaultdict(Bucket)
    overall = Bucket()
    for org_id, conn_id, product_id, revenue in lines:
        key = (org_id, conn_id)
        bucket = buckets[key]
        if not product_id or product_id not in products:
            bucket.orphan_lines += 1
            overall.orphan_lines += 1
            continue
        rev = Decimal(str(revenue or 0))
        hit_id = product_id in identity
        hit_geo = product_id in geometry
        bucket.observe(product_id, rev, hit_id, hit_geo)
        overall.observe(product_id, rev, hit_id, hit_geo)

    report: Dict[str, Any] = {
        "catalog_available": available,
        "catalog_version": pie_service.catalog_version if available else None,
        "identity_source": ("live SKU re-resolution" if args.reresolve and available
                            else "persisted Product.pie_record_id"),
        "geometry_measured": geometry_ran,
        "geometry_gate": list(ISO_REQUIRED),
        "scope": "invoice lines (SalesTxn); no estimate line history exists to include",
        "entities": [
            {
                "organization_id": org_id,
                "organization": getattr(orgs.get(org_id), "name", None),
                "connection_id": conn_id,
                "connection_label": (getattr(conns.get(conn_id), "label", None)
                                     if conn_id else None),
                **bucket.to_dict(),
            }
            for (org_id, conn_id), bucket in sorted(
                buckets.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
        ],
        "overall": overall.to_dict(),
    }
    if args.reresolve:
        report["link_disagreement"] = disagreement
    return report


def _run(session, args) -> int:
    report = build_report(session, args)
    _print(report, report["catalog_available"], report["geometry_measured"])
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


def _print(report: Dict[str, Any], available: bool, geometry_ran: bool) -> None:
    print("\nCoverage over TRADED invoice lines — a LOWER BOUND.")
    print("A line is here because somebody could already identify the product,")
    print("so this denominator conditions on the outcome the product changes and")
    print("cannot see the enquiries nobody could identify. Read the docstring.")

    if not available:
        # The distinction pie_service.catalog_available exists to preserve. A 0%
        # printed here would be indistinguishable from a real 0% and is the
        # "absence of evidence read as a pass" CLAUDE.md §1 forbids, inverted.
        print("\n!! THE CATALOGUE IS NOT LOADED. Every lookup would return None,")
        print("   which is 'nobody asked the pack', NOT 'the pack does not cover")
        print("   this'. No coverage figure below is admissible. Build it with")
        print("   ./scripts/setup_pie_parser.sh && python3 scripts/build_catalog.py")
    else:
        print(f"\ncatalogue {report['catalog_version']}")
    print(f"identity from: {report['identity_source']}")
    print("geometry gate: " + (" + ".join(report["geometry_gate"]) if geometry_ran
                               else "NOT MEASURED"))

    overall = report["overall"]
    if not overall["lines"]:
        print("\nNo invoice lines in this database. Nothing to measure —")
        print("this is an empty denominator, not 0% coverage.")
        return

    for row in report["entities"] + [{"organization": "— ALL —",
                                      "connection_label": None, **overall}]:
        label = row.get("connection_label") or row.get("organization") or "(unattributed)"
        print(f"\n── {label} " + "─" * max(0, 60 - len(str(label))))
        print(f"   {row['distinct_products']} distinct products · {row['lines']} lines "
              f"· {row['selling_value']} selling value")
        if row["orphan_lines"]:
            print(f"   {row['orphan_lines']} line(s) reference a product row that is "
                  f"absent — a referential gap, counted as neither hit nor miss")
        print(f"   {'weighting':<22}{'identity':>12}{'geometry':>12}{'union':>12}")
        for name, key in (("by distinct product", "by_product"),
                          ("by line count", "by_line"),
                          ("by selling value", "by_value")):
            c = row[key]
            cells = []
            for half in ("identity_pct", "geometry_pct", "union_pct"):
                pct = c[half]
                if half == "geometry_pct" and not geometry_ran:
                    cells.append(f"{'n/m':>12}")
                else:
                    cells.append(f"{pct:>11.2f}%" if pct is not None else f"{'—':>12}")
            print(f"   {name:<22}" + "".join(cells))

    dis = report.get("link_disagreement")
    if dis is not None:
        n_p, n_l = len(dis["persisted_only"]), len(dis["live_only"])
        print(f"\nlink disagreement: {n_p} product(s) linked in the database but not "
              f"live, {n_l} live but not in the database")
        if n_p:
            print("   persisted-only links were written under a catalogue this "
                  "process no longer resolves against — re-sync to clear them")


if __name__ == "__main__":
    raise SystemExit(main())
