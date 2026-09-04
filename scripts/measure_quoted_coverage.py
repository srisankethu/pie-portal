#!/usr/bin/env python3
"""Read-only. Of what this business actually TRADED, how much does the pack reach?

``docs/concepts/01-application-engineering.md`` §1 publishes 21.6% union
coverage and takes the **item master** as its denominator — 15,028 rows, of
which 6,146 carry no manufacturer at all. A master is a graveyard: dead stock,
one-time buys, migration artifacts, rows nobody has quoted from in years. A
coverage number over it answers "what share of our filing cabinet does the pack
decode", and the decision it is used for is "can this change what we sell",
which is a question about *traded* lines. This script changes the denominator
and nothing else: same two coverage definitions, measured over invoice lines.

**Read the number as a LOWER BOUND on the reachable population, never as the
answer.** A line only became an invoice line because somebody could already
identify what the customer wanted — find the part, price it, put it on a
document. So this denominator conditions on the outcome the product exists to
change, and systematically excludes exactly the lines that were lost, refused
or never quoted because nobody could work out what was being asked for. Those
lines leave no trace in ``sales_txns``. If the pack reaches 40% of what was
traded, that is 40% of the demand that *already converted*; it says nothing
about the demand that did not, and the honest version of this measurement needs
forward capture of every inbound line including the unquoted ones
(``enquiry/`` is where that would land). Quoting this figure as "coverage of
demand" is the survivorship error, and it will be made unless the sentence
above travels with the number.

**Invoice lines only, and that is scope rather than silence.** There is no
estimate line-level history anywhere in this system to widen it with: estimates
are write-only (``routers/quote.py`` pushes them to the ERP), no connector
pulls them back, and no table holds their lines. So the population here is
``models.SalesTxn`` — invoice-line grain — and a quote that never became an
invoice is outside it in both directions: it is not in the numerator and it is
not in the denominator.

**Identity comes from the JOIN, not from a re-resolution.**
``ingestion/sync._link_catalog`` already writes ``Product.pie_record_id`` on
every item pull, and nothing in the codebase has ever read those columns back.
On a synced database the identity half of this measurement is a column, and
re-deriving it here would be a second answer to a question the sync has
already answered. The one thing that column cannot say is *why* it is NULL —
``_link_catalog`` clears all three fields on a miss, so "the pack does not
cover this item" and "nobody asked the pack" are stored identically. That is
the distinction ``pie_service.catalog_available`` exists for, and it is settled
per product by asking the live catalogue about the SKU, through
``pie_service.lookup_record``. The SKU is reached the only way it can be:
``SalesTxn.product_id`` → ``ItemConnectorRecord.product_id`` →
``ItemConnectorRecord.sku``. ``Product`` has no SKU column and the sync never
writes one.

**Geometry is admitted on full ISO slot fill, never on a family route.** §3 of
that document measured the difference: 30.3% of master names route to a named
``product_family`` and 11.6% of those routed rows carry a named non-Kennametal
manufacturer — an EMUGE screwdriver and an ``M3X11`` screw both route to
``turning_insert``. Restricted to shape + edge + radius all present, definite
misroutes fall to 0.05%. So a route is not evidence and does not count here.
The decode and that gate are ``app/master_health/geometry.py``'s, called rather
than reimplemented — see :func:`read_geometry`.

**How this differs from the Master Health Report, which is not a duplicate of
it.** ``app/master_health/`` answers the same coverage question over the *other*
denominator: an item-master export, read offline with no database, weighted by
stock value at selling price. This script is its complement — it needs the
database precisely because trade is the thing a file cannot show, and it weights
by revenue that was actually billed rather than by stock sitting on a shelf. The
two share the step where they should (the decode above) and part where the
question does: the master report says what the pack could reach if everything in
the catalogue were sold, this one says what it reached on what was.

**Counts, shares and SELLING revenue. No cost, no margin, anywhere in the
output.** Structurally, not by a filter at the end: nothing below opens
``cost_records`` or reads a cost column, and the only money that reaches the
report is ``SalesTxn.line_revenue`` — net of the line discount, pre-tax, the
figure the customer was actually billed. A share of selling revenue has no
boundary in it that a caller could walk (CLAUDE.md §1).

Run:  cd backend && python3 ../scripts/measure_quoted_coverage.py
      cd backend && python3 ../scripts/measure_quoted_coverage.py --org org_pie
      cd backend && python3 ../scripts/measure_quoted_coverage.py --json coverage.json

Running it live: the coverage figures come out of the database, so what this
needs is a completed sync rather than an API budget. Filling that database is
the expensive half — ``ingestion/zoho_client.list_invoices`` fetches
per-document detail because ``line_items`` are not on the list response, which
is one API call per invoice, against roughly 3,367 invoices for SLS alone.
Budget for that, or narrow the sync window and read the smaller answer.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select                       # noqa: E402

from app.config import settings                           # noqa: E402
from app.db import SessionLocal                           # noqa: E402
from app.domain import models                             # noqa: E402
from app.master_health.geometry import (                  # noqa: E402
    GATED_SLOTS, decode_names)
from app.master_health.source import MasterRow            # noqa: E402

# ── the two coverage definitions, and the gate on each ───────────────────────

#: The gate — shape AND edge AND radius — re-exported rather than restated.
#: ``app/master_health/geometry.py`` owns the one definition and the argument
#: behind it; a second tuple here would be the responsibility duplication
#: CLAUDE.md §2 names, and the copy that drifts is always the one nobody reads.
ISO_SLOTS = GATED_SLOTS

#: Per-product identity states. The last two are the pair `Product.pie_record_id`
#: cannot distinguish on its own, and separating them is the whole reason this
#: script asks the live catalogue at all.
LINKED = "LINKED"                    # pie_record_id is set — the sync's own answer
LINKED_STALE = "LINKED_ON_REREAD"    # NULL on the row, but the catalogue knows the SKU
ABSENT = "ABSENT_FROM_CATALOGUE"     # the pack was asked about this SKU and said no
NO_SKU = "NO_SKU_ON_RECORD"          # no connector record carries a SKU to ask about
UNASKED = "UNASKED"                  # no catalogue was loaded; nothing is known here

#: Identity states that count as covered. `LINKED_STALE` is covered and is also
#: a defect report: the row should have carried the link and does not, which
#: means the sync that wrote it ran without a catalogue or predates the column.
COVERED_IDENTITY = frozenset({LINKED, LINKED_STALE})

#: The states in which the pack was actually *asked* about this item. `NO_SKU`
#: is not among them, and that distinction gets its own reported figure: an
#: item the master holds no catalogue number for cannot be linked by any pack,
#: so counting it as a miss measures the master rather than the corpus. Both
#: readings are reported side by side — the full-population one, which is what
#: is comparable to the geometry figure and to the published 21.6%, and this
#: one, which is what the corpus is answerable for.
ASKABLE_IDENTITY = frozenset({LINKED, LINKED_STALE, ABSENT})

MEASURED = "MEASURED"
UNKNOWN = "UNKNOWN"

#: Money is `Decimal` (CLAUDE.md §1) and reaches JSON as a string, never a
#: float. Quantized so an empty sum and a summed column spell themselves the
#: same way — `line_revenue` is `Numeric(18, 4)`, and "0" beside "1050.0000" in
#: one report is the kind of inconsistency that gets "fixed" with a float.
_PAISE = Decimal("0.0001")


def _money(value: Decimal) -> str:
    return str(value.quantize(_PAISE))


def _pct(part: Any, whole: Any) -> Optional[float]:
    """Share, or None for an empty denominator.

    Never 0.0 when there is nothing to divide — that reads as a measured zero,
    and "no rows" is a different statement from "no coverage".
    """
    return round(100.0 * float(part) / float(whole), 1) if whole else None


# ── 1. what was traded ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Book:
    """One connected company's book — the entity a coverage figure is *about*.

    An organization deliberately holds several connections and rolls revenue up
    across them (``ZohoConnection``'s docstring), so the organization alone is
    the wrong grain: three legal entities under one tenant would be reported as
    one. The triple below is the one every imported row already carries.
    """

    organization_id: str
    connector: Optional[str]
    connection_id: Optional[str]
    label: str

    def key(self) -> str:
        return f"{self.organization_id}/{self.connector or '-'}/{self.connection_id or '-'}"


@dataclass
class Traded:
    """Invoice lines rolled to product grain, per book."""

    #: book → product_id → [line_count, revenue]
    by_book: dict[Book, dict[str, list]] = field(default_factory=dict)
    #: Lines whose ``line_revenue`` was NULL. Excluded from the value
    #: denominator and reported, rather than summed as zero — `sum(… or 0)`
    #: over rows that may hold None is the tell CLAUDE.md §1 names.
    lines_without_revenue: int = 0

    def product_ids(self) -> set[str]:
        return {pid for products in self.by_book.values() for pid in products}


def read_trade(session, org: Optional[str] = None) -> Traded:
    """Every invoice line, grouped to (book, product).

    Aggregated in SQL rather than in Python: at invoice-line grain over a real
    book this is hundreds of thousands of rows, and the three weightings need
    only a count and a sum per product.
    """
    labels = {
        c.connection_id: (c.label or c.connection_id)
        for c in session.scalars(select(models.ZohoConnection))
    }
    org_names = {
        o.organization_id: o.name
        for o in session.scalars(select(models.Organization))
    }

    stmt = (
        select(models.SalesTxn.organization_id, models.SalesTxn.connector,
               models.SalesTxn.connection_id, models.SalesTxn.product_id,
               func.count().label("lines"),
               func.sum(models.SalesTxn.line_revenue).label("revenue"))
        .group_by(models.SalesTxn.organization_id, models.SalesTxn.connector,
                  models.SalesTxn.connection_id, models.SalesTxn.product_id)
    )
    if org:
        stmt = stmt.where(models.SalesTxn.organization_id == org)

    out = Traded()
    books: dict[tuple, Book] = {}
    for org_id, connector, connection_id, product_id, lines, revenue in session.execute(stmt):
        ident = (org_id, connector, connection_id)
        book = books.get(ident)
        if book is None:
            org_name = org_names.get(org_id, org_id)
            company = labels.get(connection_id) if connection_id else None
            book = books[ident] = Book(
                org_id, connector, connection_id,
                f"{org_name} — {company}" if company else org_name)
            out.by_book[book] = {}
        out.by_book[book][product_id] = [int(lines), revenue]

    # A NULL sum means every line in the group had a NULL revenue; the count of
    # lines it stands for is what gets reported, not a fabricated zero.
    for products in out.by_book.values():
        for pid, cell in products.items():
            if cell[1] is None:
                out.lines_without_revenue += cell[0]   # left None, never zeroed
            else:
                cell[1] = Decimal(str(cell[1]))
    return out


# ── 2. identity: the persisted link, and what NULL actually means ────────────

def read_identity(session, product_ids: set[str],
                  service=None) -> Optional[dict[str, str]]:
    """Per-product identity state, or None when nothing can be known.

    Returns None — not an empty map, not a map of zeros — when no catalogue was
    loaded. Every product would read ABSENT in that state, and reporting 0%
    identity coverage because nobody asked the pack is precisely the benign
    default CLAUDE.md §1 refuses.
    """
    if not product_ids:
        return {}
    if service is None:
        from app.pie_service import pie_service as service   # noqa: PLC0415

    rows = list(session.scalars(
        select(models.Product).where(models.Product.product_id.in_(product_ids))))
    if not service.catalog_available:
        return None

    # One query for every SKU behind these products, rather than one per
    # product. A product can carry records from more than one connector; any
    # SKU that resolves is evidence that the pack covers the item.
    skus: dict[str, list[str]] = defaultdict(list)
    for pid, sku in session.execute(
            select(models.ItemConnectorRecord.product_id,
                   models.ItemConnectorRecord.sku)
            .where(models.ItemConnectorRecord.product_id.in_(product_ids))):
        if sku and str(sku).strip():
            skus[pid].append(str(sku))

    verdicts: dict[str, str] = {}
    for row in rows:
        if row.pie_record_id:
            verdicts[row.product_id] = LINKED
            continue
        candidates = skus.get(row.product_id, ())
        if not candidates:
            verdicts[row.product_id] = NO_SKU
            continue
        verdicts[row.product_id] = (
            LINKED_STALE if any(service.lookup_record(s) for s in candidates)
            else ABSENT)
    # A traded product with no master row left is not evidence about the pack.
    for pid in product_ids - set(verdicts):
        verdicts[pid] = NO_SKU
    return verdicts


# ── 3. geometry: the ISO slots, decoded from the item name ───────────────────

def read_geometry(names: Mapping[str, str],
                  rule_set: Optional[Path] = None) -> Optional[dict[str, bool]]:
    """Whether each product's NAME fills every slot in :data:`ISO_SLOTS`.

    The decode itself is ``master_health.geometry.decode_names`` and not a
    second implementation. That module landed for the *other* denominator — an
    item-master export read offline, with no database — and the step the two
    reports share is exactly this one: one batch through one ``ParserPipeline``,
    quarantined rows merged back in so the denominator does not narrow, and an
    unloadable engine reported as UNKNOWN rather than as a run of zeroes. Two
    decoders would eventually disagree about a row nobody looks at, and the
    disagreement would read as a real difference between the two censuses.

    So this function is an adapter and nothing more: traded products in, the
    gate's verdict out. ``decode_names`` keys on a row number because a master
    holds blank and duplicate SKUs; the products here are keyed by
    ``product_id``, so the mapping is positional over a sorted list — sorted
    for determinism, which is the property a rerun of this measurement rests on.

    ``rule_set`` is the decoder these names are read through, and it is named
    rather than defaulted: which rule set reads a file is a fact about that
    file, and ``decode_names`` has no fallback. Here the shipped rule set is
    the *subject* of the measurement — the question this script asks is how
    much of what the business traded that rule set reaches — so the caller
    passes ``settings.PIE_PACK`` and the report names it.

    Returns None when the engine or its rule set could not be loaded, which is
    ``DecodeRun.available`` restated in this script's vocabulary.

    The decode runs over ``Product.name`` because that is where this master
    keeps the product code (§1: ``Item.sku`` holds the MM#, ``Item.name`` holds
    the code).
    """
    if not names:
        return {}
    ordered = sorted(names)
    run = decode_names([
        MasterRow(row_number=i, sku=None, name=names[pid], manufacturer=None,
                  rate=None, stock=None, hsn=None, uom=None)
        for i, pid in enumerate(ordered)
    ], rule_set or settings.PIE_PACK)
    if not run.available:
        log_reason = run.unavailable_reason
        print(f"  note: {log_reason}", file=sys.stderr)
        return None
    return {pid: bool(run.outcomes[i].gated) if i in run.outcomes else False
            for i, pid in enumerate(ordered)}


# ── 4. the three weightings ──────────────────────────────────────────────────

def _weigh(products: Mapping[str, list], covered: Optional[Iterable[str]]) -> dict:
    """One coverage figure, three ways over the same set of products.

    ``products`` is the denominator, so a figure over a restricted population
    is taken by passing that population rather than by dividing differently
    afterwards. ``covered=None`` means the evidence for this definition is
    missing, and every figure is None rather than zero — as does an empty
    population, because a share of nothing is not zero coverage.
    """
    if not products:
        return {"state": UNKNOWN, "products": None, "lines": None, "value": None,
                "products_pct": None, "lines_pct": None, "value_pct": None}
    total_products = len(products)
    total_lines = sum(cell[0] for cell in products.values())
    total_value = sum((cell[1] for cell in products.values() if cell[1] is not None),
                      Decimal("0"))
    if covered is None:
        return {"state": UNKNOWN, "products": None, "lines": None, "value": None,
                "products_pct": None, "lines_pct": None, "value_pct": None}
    hit = [pid for pid in covered if pid in products]
    lines = sum(products[pid][0] for pid in hit)
    value = sum((products[pid][1] for pid in hit if products[pid][1] is not None),
                Decimal("0"))
    return {
        "state": MEASURED,
        "products": len(hit), "products_pct": _pct(len(hit), total_products),
        "lines": lines, "lines_pct": _pct(lines, total_lines),
        "value": _money(value), "value_pct": _pct(value, total_value),
    }


def combine(traded: Traded, identity: Optional[Mapping[str, str]],
            geometry: Optional[Mapping[str, bool]]) -> dict[str, Any]:
    """The report body. Pure — every input is already gathered.

    An UNKNOWN half makes the union UNKNOWN. Adding a measured set to an
    unmeasured one produces a number that looks like coverage and is a lower
    bound of unstated size, which is worse than saying nothing.
    """
    books = []
    for book in sorted(traded.by_book, key=lambda b: b.key()):
        products = traded.by_book[book]
        ident_hits = (None if identity is None
                      else {p for p in products if identity.get(p) in COVERED_IDENTITY})
        geom_hits = (None if geometry is None
                     else {p for p in products if geometry.get(p)})
        if ident_hits is None or geom_hits is None:
            union = overlap = neither = None
        else:
            union = ident_hits | geom_hits
            overlap = ident_hits & geom_hits
            neither = set(products) - union

        states: dict[str, int] = defaultdict(int)
        for pid in products:
            states[UNASKED if identity is None else identity.get(pid, NO_SKU)] += 1

        # The same identity hits over the population the pack was answerable
        # for. An item the master holds no catalogue number for is unmeasurable,
        # not uncovered, and an all-unmeasurable book reports UNKNOWN here
        # rather than a quotable 0%.
        askable = (None if identity is None else
                   {pid: cell for pid, cell in products.items()
                    if identity.get(pid) in ASKABLE_IDENTITY})

        books.append({
            "book": book.key(),
            "label": book.label,
            "organization_id": book.organization_id,
            "connector": book.connector,
            "connection_id": book.connection_id,
            "traded": {
                "products": len(products),
                "lines": sum(c[0] for c in products.values()),
                "value": _money(sum((c[1] for c in products.values()
                                     if c[1] is not None), Decimal("0"))),
            },
            "identity": _weigh(products, ident_hits),
            "identity_where_askable": (
                {"state": UNKNOWN, "products": None, "lines": None, "value": None,
                 "products_pct": None, "lines_pct": None, "value_pct": None}
                if askable is None else _weigh(askable, ident_hits)),
            "geometry": _weigh(products, geom_hits),
            "union": _weigh(products, union),
            "overlap": _weigh(products, overlap),
            "neither": _weigh(products, neither),
            "identity_states": dict(sorted(states.items())),
        })

    return {
        "denominator": "invoice lines (models.SalesTxn) — traded, not the item master",
        "iso_slots_required": list(ISO_SLOTS),
        "identity_evidence": UNKNOWN if identity is None else MEASURED,
        "geometry_evidence": UNKNOWN if geometry is None else MEASURED,
        "lines_without_revenue": traded.lines_without_revenue,
        "books": books,
    }


# ── 5. reporting ─────────────────────────────────────────────────────────────

_HEADLINE = """\
QUOTED-LINE CATALOGUE COVERAGE — over what was traded, not over the item master

  This is a LOWER BOUND on the reachable population, not the answer. A line
  became an invoice line because somebody could already identify what the
  customer wanted, so this denominator conditions on the very outcome the
  product exists to change and excludes every line that was lost, refused or
  never quoted because nobody could work out what was being asked for. Those
  lines leave no trace in sales_txns and are absent from BOTH sides of every
  ratio below. The answer needs forward capture of all inbound lines,
  including the unquoted ones. Do not quote these figures as coverage of
  demand.

  Scope: invoice lines only. Estimates are write-only in this system — nothing
  pulls their lines back and no table holds them — so a quote that never became
  an invoice is outside the population in both directions.
"""


def _row(name: str, cell: Mapping[str, Any]) -> str:
    if cell["state"] == UNKNOWN:
        return f"    {name:<12}{'UNKNOWN':>18}{'UNKNOWN':>18}{'UNKNOWN':>18}"

    def _one(count: Any, pct: Optional[float]) -> str:
        n = f"{int(count):,}" if not isinstance(count, str) else count
        return f"{n} ({pct:.1f}%)" if pct is not None else f"{n} (n/a)"

    return (f"    {name:<12}"
            f"{_one(cell['products'], cell['products_pct']):>18}"
            f"{_one(cell['lines'], cell['lines_pct']):>18}"
            f"{_one(Decimal(cell['value']).quantize(Decimal('1')), cell['value_pct']):>18}")


def render(report: Mapping[str, Any]) -> str:
    out = [_HEADLINE]
    out.append(f"  ISO gate: all of {', '.join(report['iso_slots_required'])} — "
               f"a family route is not evidence and does not count.")
    out.append(f"  identity evidence: {report['identity_evidence']}   "
               f"geometry evidence: {report['geometry_evidence']}")
    if report["identity_evidence"] == UNKNOWN:
        out.append("  !! No catalogue was loaded, so nothing is known about identity —\n"
                   "     that is UNKNOWN, not 0%. Fetch pie-parser and rebuild the\n"
                   "     catalogue (scripts/build_catalog.py) before reading identity.")
    if report["geometry_evidence"] == UNKNOWN:
        out.append("  !! The engine or its pack could not be loaded, so no name was\n"
                   "     decoded. That is UNKNOWN, not 0%.")
    if report["lines_without_revenue"]:
        out.append(f"  !! {report['lines_without_revenue']:,} invoice lines carry no "
                   f"line_revenue. They are counted in the line weighting and\n"
                   f"     excluded from the value weighting rather than summed as zero.")

    if not report["books"]:
        out.append("\n  NO TRADED LINES IN THIS DATABASE. Nothing is measured, and\n"
                   "  nothing below should be read as a coverage of zero.")
        return "\n".join(out)

    for book in report["books"]:
        out.append(f"\n  {book['label']}  [{book['book']}]")
        t = book["traded"]
        out.append(f"    traded: {t['products']:,} distinct products, "
                   f"{t['lines']:,} invoice lines, {Decimal(t['value']):,.0f} at selling price")
        out.append(f"    {'':<12}{'by product':>18}{'by line':>18}{'by value':>18}")
        for name in ("identity", "geometry", "union", "overlap", "neither"):
            out.append(_row(name, book[name]))
        out.append(_row("id/askable", book["identity_where_askable"]))
        states = ", ".join(f"{k}×{v:,}" for k, v in book["identity_states"].items())
        out.append(f"    identity states: {states}")
        no_sku = book["identity_states"].get(NO_SKU, 0)
        if no_sku:
            out.append(
                f"    note: {no_sku:,} of {book['traded']['products']:,} traded products "
                f"carry no SKU on any connector record, so no pack\n"
                f"          could link them. They are inside the `identity` denominator "
                f"and outside `id/askable`;\n"
                f"          the gap between those two rows is the master's, not the "
                f"corpus's.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", help="restrict to one organization_id")
    ap.add_argument("--json", type=Path, help="also write the report as JSON")
    args = ap.parse_args()

    with SessionLocal() as session:
        if args.org:
            known = list(session.scalars(
                select(models.Organization.organization_id)
                .order_by(models.Organization.organization_id)))
            if args.org not in known:
                # A typo must not read as an empty book. "No traded lines" is a
                # finding; "no such organization" is a mistake, and the two must
                # not print the same page.
                sys.exit(f"No such organization: {args.org}. "
                         f"Present: {', '.join(known) or 'none'}")
        traded = read_trade(session, args.org)
        product_ids = traded.product_ids()
        identity = read_identity(session, product_ids)
        names = {
            p.product_id: p.name for p in session.scalars(
                select(models.Product).where(models.Product.product_id.in_(product_ids)))
        } if product_ids else {}
        geometry = read_geometry(names, settings.PIE_PACK)
        report = combine(traded, identity, geometry)

    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
