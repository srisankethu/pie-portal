#!/usr/bin/env python3
"""Why does a row say "Zoho" instead of naming the connected company?

Because the company is looked up by the row's ``connection_id``, and a row that
has none cannot be attributed to a book. Rows written before per-connection
provenance existed have none, and a later sync does not adopt them: the upsert
matches on (connector, connection_id), so a connection with a real id never
finds a row whose id is NULL — it creates a fresh one beside it.

Run:  cd backend && python3 ../scripts/diagnose_attribution.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select                      # noqa: E402

from app.db import SessionLocal                          # noqa: E402
from app.domain import models                            # noqa: E402

ENTITIES = [("customers", models.Customer), ("products", models.Product)]
for name in ("SalesTxn", "CostRecord", "StockLevel"):
    if hasattr(models, name):
        ENTITIES.append((name.lower(), getattr(models, name)))

s = SessionLocal()
conns = list(s.scalars(select(models.ZohoConnection)))
print(f"connected companies: {len(conns)}")
for c in conns:
    print(f"   {c.connection_id}  label={c.label!r}  enabled={c.enabled}")
if not conns:
    print("   none — nothing can name a company until a connection exists")

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
s.close()
