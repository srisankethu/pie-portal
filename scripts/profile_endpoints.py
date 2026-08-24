"""Where does the read path actually spend itself?

Boots the real app against a database you point it at, hits every GET route it
can call with no path parameters, and records per request: wall time, SQL
statements executed, and the most-repeated statement — which is the N+1 tell.

    cd backend
    DATABASE_URL="sqlite:///$PWD/data/platform.db" PIE_WARM=0 \
        python3 ../scripts/profile_endpoints.py

Read the **query counts** before the milliseconds. A seeded development
database is small, so a count that is already per-row is what will be slow with
a real book — and on Postgres each one is a network round trip, not a memory
read. A first hit also pays one-off import cost, so re-run a suspicious
endpoint before believing its time: `/api/v1/connections/catalog` measured
262 ms cold and 7 ms warm, and there was nothing to fix.

This is the harness the caching work in `docs/caching-and-queue.md` was chosen
by, kept so the numbers in that document can be reproduced and re-checked
rather than believed. It is deliberately not in `scripts/verify.sh`: it needs a
seeded database with a signed-in user, and it reports rather than judges.

It signs in as the seeded owner, so it needs `ensure_org_and_users` to have run
(`python -m app.bootstrap`) and that account not to be pending a password
change.
"""
from __future__ import annotations

import collections
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import SEED_PASSWORD  # noqa: E402

STATEMENTS: list[str] = []


@event.listens_for(engine, "before_cursor_execute")
def _count(conn, cursor, statement, parameters, context, executemany):
    STATEMENTS.append(" ".join(statement.split())[:160])


client = TestClient(app)


def login(email: str) -> dict:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    r.raise_for_status()
    body = r.json()
    if body.get("must_change_password"):
        print("note: this account is pending a password change, so every "
              "role-gated endpoint will answer 403.\n", file=sys.stderr)
    return {"Authorization": f"Bearer {body['token']}"}


owner = login("s.menon@pie.example")

# From the OpenAPI spec, not app.routes: the intelligence routers are included
# lazily, so the route table is nearly empty until something has asked.
spec = client.get("/openapi.json").json()
routes = sorted(p for p, v in spec["paths"].items()
                if "get" in v and "{" not in p)

rows = []
for path in routes:
    STATEMENTS.clear()
    start = time.perf_counter()
    try:
        status = client.get(path, headers=owner).status_code
    except Exception as e:  # noqa: BLE001 — a broken endpoint is a result too
        status = f"EXC {type(e).__name__}"
    elapsed_ms = (time.perf_counter() - start) * 1000
    counts = collections.Counter(STATEMENTS)
    top, repeats = counts.most_common(1)[0] if counts else ("", 0)
    rows.append((elapsed_ms, len(STATEMENTS), repeats, status, path, top))

rows.sort(reverse=True)
print(f"{'ms':>8} {'sql':>5} {'rpt':>4} {'code':>6}  path")
print("-" * 100)
for ms, n, repeats, status, path, top in rows:
    if n == 0 and ms < 5:
        continue
    print(f"{ms:8.1f} {n:5d} {repeats:4d} {str(status):>6}  {path}")
    if repeats >= 5:
        # The same statement five times in one request is the shape of an N+1,
        # and the row count that produced it is a development database's, not a
        # customer's.
        print(f"{'':>26}repeated: {top[:110]}")
