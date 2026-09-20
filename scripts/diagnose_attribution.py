#!/usr/bin/env python3
"""Read-only. Three questions about the same root cause, answered in one run.

Why does a row say "Zoho" instead of naming the connected company? Because the
company is looked up by the row's ``connection_id``, and a row that has none
cannot be attributed to a book. Rows written before per-connection provenance
existed have none.

**A later sync does adopt them, and this paragraph said it did not.** The claim
was true when it was written and stopped being true in two steps.
``_for_upsert`` gained an adoption branch: on an organization with exactly one
connection, a pull claims a row that carries no connection rather than
inserting a twin beside it. ``upsert_vendor`` was then the one master upsert
still doing its own lookup, so vendors alone kept twinning — fixed since, with
the adoption path pinned. Both are why this script exists, and neither is a
reason to keep describing the old behaviour as current.

What that leaves is narrower and does not heal itself. A book that synced
*between* those two fixes has vendor twins already on disk: a good row carrying
the connection, and a NULL-connection orphan beside it. Adoption cannot reach
the orphan now, because the exact-source lookup finds the good row first and
returns before the claimable branch is consulted. ``--repair`` below is for
exactly those, and for nothing else.

That same miss is why a customer can appear twice, and why an item can show no
name: the row carrying the name is the one the screen is not reading. So this
reports all three — unattributed rows, duplicate groups, and unnamed records —
because a repair has to see them together to be safe. Two rows sharing an
external id across two *connections* are two companies' records of possibly
different customers, and must never be merged; two sharing one connection are
one customer written twice.

This script only reads, unless ``--repair`` is passed. Without it nothing is
written, which is the mode to run first and the mode to run again afterwards.

Run:  cd backend && python3 ../scripts/diagnose_attribution.py
      cd backend && python3 ../scripts/diagnose_attribution.py --repair
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select                      # noqa: E402

from app.db import Base, SessionLocal                    # noqa: E402
from app.domain import models                            # noqa: E402

_args = argparse.ArgumentParser(
    description="Diagnose connection attribution; optionally repair vendor twins.")
_args.add_argument(
    "--repair", action="store_true",
    help="delete NULL-connection vendor rows that have a surviving twin and "
         "nothing pointing at them. Everything else is reported and left alone.")
ARGS = _args.parse_args()

ENTITIES = [("customers", models.Customer), ("products", models.Product)]
for name in ("SalesTxn", "CostRecord", "StockLevel"):
    if hasattr(models, name):
        ENTITIES.append((name.lower(), getattr(models, name)))

s = SessionLocal()
conns = list(s.scalars(select(models.ZohoConnection)))
print(f"connected companies: {len(conns)}")
for c in conns:
    print(f"   {c.connection_id}  org={c.organization_id}  "
          f"label={c.label!r}  enabled={c.enabled}")
if not conns:
    print("   none — nothing can name a company until a connection exists")

# ── the organization each side thinks it is in ───────────────────────────────
#
# A row can carry a perfectly good connection_id and still render as "source not
# recorded", because both lookups that resolve it are scoped to the *requesting*
# organization: `Companies` loads only connections whose organization_id matches,
# and `index_of` loads only records whose organization_id matches. A row whose
# organization_id differs from its connection's is therefore attributable in the
# database and unattributable on the screen — which is precisely the state that
# a NULL-connection count of zero cannot detect.
conn_orgs = {c.organization_id for c in conns}
by_conn = {c.connection_id: c for c in conns}
print()
print(f"organizations owning a connection: {len(conn_orgs)}  {sorted(conn_orgs)}")
for label, model in (("customers", models.Customer), ("products", models.Product)):
    row_orgs = set(s.scalars(select(model.organization_id).distinct()))
    stray = row_orgs - conn_orgs
    print(f"{label:<10} span {len(row_orgs)} organization(s) {sorted(row_orgs)}"
          + (f"  <-- {sorted(stray)} own no connection" if stray else ""))
    # And the crossed case: the row and the connection it names disagree.
    crossed = 0
    for org_id, cid in s.execute(
            select(model.organization_id, model.connection_id)
            .where(model.connection_id.is_not(None)).distinct()).all():
        conn = by_conn.get(cid)
        if conn is not None and conn.organization_id != org_id:
            crossed += 1
            print(f"   MISMATCH {label}: rows in org {org_id} point at "
                  f"connection {cid} owned by org {conn.organization_id}")
        elif conn is None:
            print(f"   DANGLING {label}: rows point at connection {cid}, "
                  f"which no longer exists")
    if not crossed:
        print(f"   {label}: every connection_id resolves within its own org")

print()
print(f"{'table':<14}{'rows':>9}{'no connection':>15}{'no connector':>14}")
print("-" * 52)
for label, model in ENTITIES:
    if not hasattr(model, "connection_id"):
        continue
    total = s.scalar(select(func.count()).select_from(model)) or 0
    noconn = s.scalar(select(func.count()).select_from(model)
                      .where(model.connection_id.is_(None))) or 0
    nocx = (s.scalar(select(func.count()).select_from(model)
                     .where(model.connector.is_(None))) or 0
            if hasattr(model, "connector") else 0)
    print(f"{label:<14}{total:>9}{noconn:>15}{nocx:>14}")

print()
print("Any non-zero 'no connection' count is a row the Stock/Customers screens")
print("cannot attribute to a company. It is not lost data — the name, the")
print("quantity and the history are all intact; only the book it belongs to is")
print("unknown, and it will stay unknown until something claims it.")


# ── duplicates ───────────────────────────────────────────────────────────────
#
# Grouped by (organization, external_id) rather than by name. The external id is
# what the source system calls the record, so two rows sharing one are the same
# source record stored twice — a fact, not a guess. Grouping by name would put
# two companies' distinct customers who happen to both be "ABC Industries" into
# one group and invite exactly the merge that must never happen.
_LIMIT = 15

def _duplicates(model, label: str) -> None:
    dup = s.execute(
        select(model.organization_id, model.external_id, func.count().label("n"))
        .group_by(model.organization_id, model.external_id)
        .having(func.count() > 1)
        .order_by(func.count().desc())).all()
    print()
    print(f"{label} sharing one external id: {len(dup)} group(s), "
          f"{sum(n for _o, _e, n in dup)} rows")
    if not dup:
        print("   none — nothing is stored twice under one source id")
        return
    for org_id, ext, n in dup[:_LIMIT]:
        rows = list(s.scalars(
            select(model).where(model.organization_id == org_id,
                                model.external_id == ext)))
        conns_here = {r.connection_id for r in rows}
        # One connection, many rows: one record written repeatedly — safe to
        # collapse. Many connections: separate books, and collapsing them would
        # merge two companies' customers into one. The distinction decides
        # whether a repair is possible at all, so it is printed per group.
        kind = ("same connection — one record written repeatedly"
                if len(conns_here) == 1
                else f"{len(conns_here)} DIFFERENT connections — DO NOT MERGE")
        name = getattr(rows[0], "name", "")
        print(f"   {ext:<24} x{n}  {name[:32]:<32} {kind}")
        for r in rows:
            print(f"      connection={r.connection_id or 'NULL':<38} "
                  f"connector={r.connector or 'NULL'}")
    if len(dup) > _LIMIT:
        print(f"   … and {len(dup) - _LIMIT} more group(s)")

_duplicates(models.Customer, "customers")
_duplicates(models.Product, "products")
_duplicates(models.Vendor, "vendors")


# ── missing names ────────────────────────────────────────────────────────────
#
# An item with no name renders as "Unnamed product (id …)", which a narrow
# column truncates to an ellipsis — the screen is not broken, the master row
# genuinely has no name. Counting them separates the two explanations.
print()
for label, model in (("customers", models.Customer), ("products", models.Product)):
    if not hasattr(model, "name"):
        continue
    blank = list(s.scalars(
        select(model).where((model.name.is_(None)) | (model.name == ""))))
    print(f"{label} with no name: {len(blank)}")
    for r in blank[:_LIMIT]:
        ident = getattr(r, "customer_id", None) or getattr(r, "product_id", "")
        print(f"   id={ident}  external_id={r.external_id}  "
              f"connection={r.connection_id or 'NULL'}")
    if len(blank) > _LIMIT:
        print(f"   … and {len(blank) - _LIMIT} more")

# ── vendor twins, and the only repair this script will perform ───────────────
#
# Separate from the duplicate report above because this group has a cause, a
# shape and a safe remedy that the general case does not.
#
# ``upsert_vendor`` was for a while the one master upsert that did not go
# through ``_for_upsert``, so when a book first recorded a connection every
# other master was re-stamped in place and the vendor master was re-inserted
# beside itself: a good row carrying the connection, and a NULL-connection twin
# holding nothing but a name. The orphan is inert — ``get_vendor_by_external``
# finds the exact-source match first, so every bill written since hangs off the
# good row — and it is permanent, because that same lookup returning first is
# what stops adoption ever reaching it.
#
# Two conditions make deleting one safe, and BOTH are required per row:
#
#   a surviving twin   Same organization, connector and external id, with a
#                      real connection. Without one this is not a twin at all,
#                      it is the only copy of that vendor, and deleting it
#                      would destroy the record rather than de-duplicate it.
#                      This is the condition that makes the repair a repair.
#   nothing points at it
#                      Every foreign key into ``vendors.vendor_id``, counted.
#                      Derived from the metadata rather than listed here: nine
#                      tables reference a vendor today and a list written out
#                      in this file would be wrong the first time a tenth is
#                      added, silently, in the direction that deletes a row
#                      something still needs.
#
# A row failing the second condition is printed and left alone. Re-pointing its
# dependents onto the twin would be a guess about which vendor a historical
# document meant, and this script does not guess.
def _vendor_referrers() -> list[tuple[str, str]]:
    """Every (table, column) holding a foreign key into ``vendors.vendor_id``."""
    target = models.Vendor.__table__.c.vendor_id
    found = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            for fk in col.foreign_keys:
                if fk.column is target:
                    found.append((table.name, col.name))
    return sorted(found)


REFERRERS = _vendor_referrers()
print()
print(f"tables referencing a vendor: {len(REFERRERS)}")

orphans = list(s.scalars(
    select(models.Vendor).where(models.Vendor.connection_id.is_(None))))
twinned, lonely = [], []
for row in orphans:
    survivors = s.scalar(
        select(func.count()).select_from(models.Vendor).where(
            models.Vendor.organization_id == row.organization_id,
            models.Vendor.connector == row.connector,
            models.Vendor.external_id == row.external_id,
            models.Vendor.connection_id.is_not(None))) or 0
    (twinned if survivors else lonely).append((row, survivors))

print(f"vendors with no connection: {len(orphans)}  "
      f"({len(twinned)} have a surviving twin, {len(lonely)} are the only copy)")
if lonely:
    print("   the only-copy rows are NOT twins and are never deleted — they are")
    print("   unattributed legacy rows, and a sync on a single-connection book")
    print("   will adopt them. Listed so the two kinds are not confused:")
    for row, _ in lonely[:_LIMIT]:
        print(f"      {row.external_id:<24} {(row.name or '')[:40]}")
    if len(lonely) > _LIMIT:
        print(f"      … and {len(lonely) - _LIMIT} more")

deletable, blocked = [], []
for row, survivors in twinned:
    holds = []
    for table_name, col_name in REFERRERS:
        table = Base.metadata.tables[table_name]
        n = s.scalar(select(func.count()).select_from(table)
                     .where(table.c[col_name] == row.vendor_id)) or 0
        if n:
            holds.append(f"{table_name}.{col_name}={n}")
    (blocked if holds else deletable).append((row, survivors, holds))

print()
print(f"twinned orphans: {len(deletable)} deletable, {len(blocked)} blocked by a reference")
for row, survivors, _ in deletable[:_LIMIT]:
    print(f"   DELETABLE {row.external_id:<24} {(row.name or '')[:32]:<32} "
          f"twin(s)={survivors}")
if len(deletable) > _LIMIT:
    print(f"   … and {len(deletable) - _LIMIT} more")
for row, survivors, holds in blocked:
    print(f"   BLOCKED   {row.external_id:<24} {(row.name or '')[:32]:<32} "
          f"held by {', '.join(holds)}")
if blocked:
    print("   Blocked rows are left alone. Something still points at them, and")
    print("   moving those references onto the twin would be a guess about which")
    print("   vendor a historical document meant. Decide those by hand.")

if not ARGS.repair:
    print()
    print("Read-only run. Re-run with --repair to delete the DELETABLE rows above;")
    print("nothing else is touched, on either pass.")
elif not deletable:
    print()
    print("--repair: nothing to do.")
else:
    for row, _survivors, _ in deletable:
        s.delete(row)
    s.commit()
    print()
    print(f"--repair: deleted {len(deletable)} twinned vendor orphan(s).")
    print("Re-run without --repair to confirm the count is now zero.")


s.close()
