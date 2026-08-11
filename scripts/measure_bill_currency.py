#!/usr/bin/env python3
"""Read-only. Does this book actually hold foreign-currency purchases?

M0 measurement A from ``docs/m0-connection-containment.md``. It answers one
question and refuses to answer any other: **do the raw Zoho bill payloads carry
a ``currency_code`` that is not the connected company's base, and do they carry
an ``exchange_rate``?**

Why it needs asking. ``economics.line_economics`` computes ``cogs = unit_cost *
qty`` and then ``gross_profit = revenue - cogs``, where the cost came from a bill
line and the revenue from an invoice line. Nothing in that subtraction knows
about currency, because nothing in the schema carries one: ``Organization`` has a
``currency`` column and no money row anywhere does. A EUR bill against an INR
invoice yields a margin near 100% and stamps a clean ``thresholds_version`` on it.

Whether that is a live defect or a hypothesis is not a design question. It is a
fact about this book, and one afternoon of reading raw payloads settles it —
which is the whole point of running this before committing to the 16-week FX
seam. ``grep -n "currency" backend/app/ingestion/zoho_client.py`` returns exactly
one hit, on the ``/organizations`` record, so the platform provably never fetches
either field on a document today.

**This reads the raw payload, deliberately.** The projections in
``zoho_client`` drop both fields, so reading anything downstream of them would
measure the projection rather than the book and always report "clean".

Requires ``ZOHO_SOURCE=api`` and a connected company. It only ever GETs.

Run:  cd backend && ZOHO_SOURCE=api python3 ../scripts/measure_bill_currency.py
      cd backend && ZOHO_SOURCE=api python3 ../scripts/measure_bill_currency.py --limit 50 --org org_sanketh
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select                            # noqa: E402

from app.config import settings                          # noqa: E402
from app.db import SessionLocal                          # noqa: E402
from app.domain import models                            # noqa: E402
from app.ingestion.connections import list_connections   # noqa: E402
from app.ingestion.sync import get_source                # noqa: E402

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--limit", type=int, default=20,
               help="how many bills to read per connection (default 20)")
p.add_argument("--org", default=None,
               help="organization id; default is every organization on the instance")
args = p.parse_args()

if settings.ZOHO_SOURCE != "api":
    sys.exit("ZOHO_SOURCE is %r. This measures the live book; the fixture source "
             "would report whatever the fixture happens to contain, which is not "
             "evidence about anything. Re-run with ZOHO_SOURCE=api."
             % settings.ZOHO_SOURCE)

s = SessionLocal()
orgs = ([s.get(models.Organization, args.org)] if args.org
        else list(s.scalars(select(models.Organization))))
orgs = [o for o in orgs if o is not None]
if not orgs:
    sys.exit("No organization found. Nothing to measure.")

total_read = 0
total_foreign = 0
total_rated = 0
grand: Counter[str] = Counter()

for org in orgs:
    conns = list_connections(s, org.organization_id, enabled_only=True) or [None]
    print(f"\n=== {org.name} ({org.organization_id}) · "
          f"organization currency {org.currency} ===")

    for conn in conns:
        conn_id = getattr(conn, "connection_id", None)
        label = getattr(conn, "label", None) or conn_id or "(no connection row)"

        try:
            source = get_source(s, org.organization_id, connection_id=conn_id)
        except Exception as exc:                          # noqa: BLE001
            print(f"  {label}: cannot open source — {exc}")
            continue

        # The company's own base currency, from the same ping the connection
        # screen shows and then discards.
        base = None
        try:
            base = (source.ping() or {}).get("currency")
        except Exception:                                 # noqa: BLE001
            pass
        print(f"  {label}: base currency {base or 'UNKNOWN'}")

        seen = 0
        kinds: Counter[str] = Counter()
        rated = 0
        examples: list[str] = []
        try:
            for raw in source.list_bills():
                if seen >= args.limit:
                    break
                seen += 1
                code = str(raw.get("currency_code") or "") or "ABSENT"
                rate = raw.get("exchange_rate")
                kinds[code] += 1
                if rate not in (None, "", 0):
                    rated += 1
                if code not in ("ABSENT", base) and len(examples) < 5:
                    examples.append(
                        f"{raw.get('bill_number') or raw.get('bill_id')} "
                        f"{code} rate={rate!r} total={raw.get('total')!r}")
        except Exception as exc:                          # noqa: BLE001
            print(f"    read failed after {seen} bills — {exc}")

        foreign = sum(n for c, n in kinds.items() if c not in ("ABSENT", base))
        total_read += seen
        total_foreign += foreign
        total_rated += rated
        grand.update(kinds)

        if not seen:
            print("    no bills in the window — this connection says nothing "
                  "either way, which is not the same as 'clean'.")
            continue
        print(f"    {seen} bills read · currency_code: "
              + ", ".join(f"{c}×{n}" for c, n in kinds.most_common()))
        print(f"    exchange_rate present on {rated}/{seen}")
        for ex in examples:
            print(f"      {ex}")

print("\n" + "=" * 68)
if not total_read:
    print("VERDICT: UNKNOWN — no bills were read. Absence of evidence is not a\n"
          "pass; widen the window or name a connection, and run it again.")
elif total_foreign:
    print(f"VERDICT: LIVE DEFECT. {total_foreign} of {total_read} bills are\n"
          f"denominated in a currency other than their company's base, and\n"
          f"economics.line_economics subtracts them from base-currency revenue\n"
          f"with nothing that could notice. FX moves ahead of the branch gate:\n"
          f"M1 stops being a guard and becomes the roadmap.")
else:
    print(f"VERDICT: single-currency, on the {total_read} bills read.\n"
          f"The FX seam is a hypothesis about a future customer rather than a\n"
          f"defect on this book — so it is priced as a feature and sequenced\n"
          f"behind the branch gate, not in front of it.\n"
          f"This does NOT clear the guard in M1: a second company with a\n"
          f"different base is still addable through the ordinary flow, and\n"
          f"nothing currently refuses it.")
print("currency_code across everything read: "
      + ", ".join(f"{c}×{n}" for c, n in grand.most_common()))
print(f"exchange_rate present on {total_rated}/{total_read} bills read.")
