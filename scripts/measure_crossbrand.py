#!/usr/bin/env python3
"""Read-only. Would a second pack have won quotes this business actually lost?

M0 measurement B from ``docs/m0-connection-containment.md``, and the signal that
decides the branch at M3. The Lean In path is an 11-week bet — 38 weeks
challenged — on cross-manufacturer cross-referencing being the moat. This is the
cheapest honest test of that, and it runs on data already in the building.

**It goes through ``pie_service.resolve``, not ``tools/find_equivalents.py``.**
That distinction is the entire reason this script exists rather than a one-line
CLI invocation. ``find_equivalents`` already takes ``--catalogs`` as a plural
argument, so it looks like the obvious tool — but it bypasses the identity-first
short-circuit at ``resolve_rfq.py:459``, where an authoritative same-product hit
returns immediately with ``suggestions: []``. The portal path hits that branch
and the CLI does not, so the CLI will look good on exactly the inputs where the
product is broken. Measuring the path the product does not use is worse than not
measuring: it produces a number that argues for the bet.

Feed it one requirement per line — the competitor codes and descriptions from
RFQs that were lost or never quoted. Blank lines and ``#`` comments ignored.

    cd backend && python3 ../scripts/measure_crossbrand.py ../lost_lines.txt
    cd backend && python3 ../scripts/measure_crossbrand.py lines.txt --json out.json

The threshold in the plan is **>=15% of lines returning a TECH or COMPAT
candidate the owner confirms he could have sold**. This script measures the
first half — what the engine returns. The confirmation is a person sitting with
the output, and it is not optional: a candidate the engine likes and the owner
would not have sold is not a win, and no amount of scoring detects that.

Reports the abstention rate separately. A high rate is not automatically a
verdict on the engine: with one pack loaded, geometrically vacuous comparisons
all score identically and tie, and ``_is_discriminating`` correctly abstains on a
tie. Read a high number as "this corpus is mostly bare ISO codes", not as "the
ranker failed" — check ``no_candidates`` against ``abstained`` before concluding.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.pie_service import pie_service                  # noqa: E402

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("lines", type=Path,
               help="file of lost/unquoted RFQ lines, one requirement per line")
p.add_argument("--customer", default=None,
               help="optional customer scope, as the quote screen would pass it")
p.add_argument("--json", type=Path, default=None,
               help="write the per-line detail here for the review sitting")
args = p.parse_args()

if not args.lines.exists():
    sys.exit(f"No such file: {args.lines}")

requests = [ln.strip() for ln in args.lines.read_text().splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
if not requests:
    sys.exit("No requirement lines found. Nothing to measure.")

SELLABLE = ("TECH", "COMPAT")

rels: Counter[str] = Counter()
rows: list[dict] = []
sellable = abstained = no_candidates = offline = 0

for text in requests:
    try:
        res = pie_service.resolve(text, customer_scope=args.customer)
    except Exception as exc:                              # noqa: BLE001
        rels["ERROR"] += 1
        rows.append({"input": text, "rel": "ERROR", "error": str(exc)})
        continue

    if res.pie_offline:
        offline += 1

    rels[res.rel] += 1
    top = res.candidates[0] if res.candidates else None
    hits = [c for c in res.candidates if c.rel in SELLABLE]
    if hits:
        sellable += 1
    if not res.candidates:
        no_candidates += 1
    # The engine declined to pick between candidates it could not separate.
    if res.candidates and res.supplyCode is None:
        abstained += 1

    rows.append({
        "input": text,
        "rel": res.rel,
        "semantics": res.semantics,
        "outcome": res.outcome,
        "supplyCode": res.supplyCode,
        "pie_offline": res.pie_offline,
        "sellable_candidates": [
            {"code": c.code, "desc": c.desc, "rel": c.rel,
             "brand": c.brand, "score": c.score, "reason": c.reason}
            for c in hits[:3]],
        "top": ({"code": top.code, "rel": top.rel, "brand": top.brand,
                 "score": top.score} if top else None),
        # Filled in by a person, not by this script.
        "owner_would_have_sold": None,
    })

n = len(requests)
pct = 100.0 * sellable / n

print(f"\n{n} lines resolved through pie_service.resolve "
      f"(the path the product uses)")
if offline:
    print(f"!! {offline} lines resolved with the engine OFFLINE (rel=PIE_DOWN).\n"
          f"   That is a measurement of the degradation path, not of the engine.\n"
          f"   Check the pack/catalogue is built before reading anything below.")
print("\nrel distribution: " + ", ".join(f"{r}×{c}" for r, c in rels.most_common()))
print(f"\nlines with a TECH or COMPAT candidate : {sellable}/{n}  ({pct:.1f}%)")
print(f"lines with no candidate at all       : {no_candidates}/{n}")
print(f"lines where the engine abstained     : {abstained}/{n}")

print("\n" + "=" * 68)
if offline:
    print("VERDICT: INVALID — the engine was offline for some or all lines.")
elif pct >= 15.0:
    print(f"VERDICT: {pct:.1f}% >= 15% threshold, ON THE ENGINE'S SIDE.\n"
          f"Now the half this script cannot do: sit with the owner and mark\n"
          f"`owner_would_have_sold` per line. The threshold is lines he would\n"
          f"ACTUALLY have sold, not lines the engine liked. If that survives,\n"
          f"cross-brand is the moat and Lean In wins the branch — with the\n"
          f"reference-vs-identity branch at resolve_rfq.py:459 as step zero,\n"
          f"because loading a second pack without it makes this query worse.")
else:
    print(f"VERDICT: {pct:.1f}% < 15% threshold.\n"
          f"On this corpus the cross-reference is not the moat, and the 11-week\n"
          f"pack investment should not start. Before concluding, check\n"
          f"no_candidates against abstained above: a corpus of bare ISO codes\n"
          f"ties on every vacuous comparison and abstains by construction, which\n"
          f"is a fact about the input rather than a verdict on the ranker.")

if args.json:
    args.json.write_text(json.dumps(
        {"n": n, "sellable": sellable, "pct": pct, "abstained": abstained,
         "no_candidates": no_candidates, "offline": offline,
         "rels": dict(rels), "lines": rows}, indent=1))
    print(f"\nPer-line detail → {args.json}")
    print("`owner_would_have_sold` is null on every row on purpose. It is the "
          "measurement that decides this, and only a person can fill it in.")
