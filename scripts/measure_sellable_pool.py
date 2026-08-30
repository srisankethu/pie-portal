#!/usr/bin/env python3
"""Read-only. Is putting the sellable master in the candidate pool worth it?

The measurement behind decision 003's accepted-in-part first slice, and the one
that has to run **before** the source it measures is trusted. The portal ranks a
customer's requirement against `products.jsonl` — the *manufacturer* catalogue —
and not against what the business sells: `resolve_rfq._build_sources` builds a
`ZohoCatalogSource` only when `--zoho-fixture` is passed and `pie_service.
_make_args` passes `zoho_fixture=None`, so a Zoho item reaches the ranking only
through `products.pie_record_id`. That column is set on ~9% of items. The other
~91% of the book cannot be offered however well it matches, because it is not in
the pool.

This script answers two questions and refuses to answer only the first:

1. **How much more of the book becomes offerable?** Products reachable as a
   candidate BEFORE (a catalogue link) and AFTER (a link *or* decoded attributes
   from Phase 1's `product_attribute_values`).
2. **Does anything get worse?** A bigger pool is not obviously better. Every new
   candidate competes for the same six result slots, and decision 017's defect —
   a candidate scoring high on a comparison where no dimension of the request was
   compared at all — is exactly the failure a bigger pool re-opens. So the harm
   arm is the longer half of the output: displaced resolutions, vacuous new
   leaders, shadow duplicates, and latency.

**It resolves through `pie_service.resolve`, the path the product uses.** Both
arms go through the same `_map` — the branch that refuses a vacuous leader, the
discrimination guard, the reference demotion — so a difference between them is a
difference in the *pool* and not in two readings of it. Calling
`equivalence.query.find_equivalents` directly would have been simpler and would
have measured a path no customer request takes; `measure_crossbrand.py` states
the same argument at length and this is the same instrument.

**The AFTER pool is the shipped one.** `app.sellable_catalog.build_pool` builds
it and `PieService.resolve(pool=…)` composes it in, so both arms are the product's
own code and the only difference between them is the argument. The first draft of
this script carried its own stand-in source, written while that module did not yet
exist, and switching to the real one changed a result: the stand-in keyed each
record on the item's `external_id`, which for a linked item *is* the catalogue
number, so 12 of 62 result lists showed one code twice. The shipped source keys on
`product_id` and cannot collide. A harness measuring its own reconstruction of a
feature reports that reconstruction's defects as the feature's — which is the
reason this note is longer than "it uses the real source".

**What the seeded master is, stated plainly because it caps the headline number.**
The master here is the real 6,717-row nomenclature corpus loaded as one
organization's items: every item name is a *catalogue-format designation*, so it
decodes at ~98%. A real book does not look like that — `app/attributes/__init__`
records DECODED_NAME reaching about **21%** of the live master, whose names are
things like "SC DRILL SLOT ENDMILL". So the AFTER coverage below is a **ceiling**,
not a forecast. It is still the right corpus to run on: it is the only real
6,717-row product text in the building, and the harm arm — which is what this
script is really for — does not depend on the decode rate at all.

Run:

    cd backend && PIE_PARSER_ROOT=/path/to/pie-parser \\
        python3 ../scripts/measure_sellable_pool.py
    cd backend && python3 ../scripts/measure_sellable_pool.py --json pool.json
    cd backend && python3 ../scripts/measure_sellable_pool.py --requirements lost.txt

The scratch database is created fresh, migrated by Alembic (never `create_all`,
CLAUDE.md §4), seeded, and deleted on the way out unless `--keep` is given. It is
never the development database: `DATABASE_URL` is set to a temporary file before
`app.config` is imported, which is why the imports below sit under the argument
parsing rather than at the top of the file.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]

p = argparse.ArgumentParser(
    description="Measure the sellable-master candidate pool, before and after.")
p.add_argument("--org", default="org_pool_measure",
               help="organization id to seed and measure (scratch database only)")
p.add_argument("--link-rate", type=int, default=11, metavar="N",
               help="give every Nth corpus row a pie_record_id. Default 11 "
                    "(9.1%%), which is the ~9%% the live master carries.")
p.add_argument("--requirements", type=Path, default=None,
               help="file of requirement lines, one per line (# comments "
                    "ignored). Replaces both default probe channels.")
p.add_argument("--catalogue-probes", type=int, default=40, metavar="N",
               help="how many real catalogue descriptions to probe with, drawn "
                    "deterministically from rows the seeded master did NOT link. "
                    "0 to use the benchmark channel alone.")
p.add_argument("--identity-probes", type=int, default=10, metavar="N",
               help="how many of those rows to probe again by bare MM#, so the "
                    "exact-identity path is exercised rather than assumed.")
p.add_argument("--build-repeats", type=int, default=15, metavar="N",
               help="how many times to time build_pool and pool_version "
                    "(default 15). The median of these is the number "
                    "sellable_catalog's docstring quotes.")
p.add_argument("--json", type=Path, default=None,
               help="write the per-requirement detail here for review")
p.add_argument("--keep", action="store_true",
               help="keep the scratch database instead of deleting it")
p.add_argument("--db", type=Path, default=None,
               help="scratch database file to build in (default: a temp dir)")
args = p.parse_args()

# The scratch database is chosen here and nowhere else, and it is chosen by
# setting the variable `app.config` reads — CLAUDE.md §4's "one URL" rule, which
# forbids a second source of truth and not a script picking a value for the one
# that exists. This must happen before `app.config` is imported, hence E402.
_scratch_dir = Path(tempfile.mkdtemp(prefix="pie-pool-measure-"))
_db_path = args.db or (_scratch_dir / "scratch.db")
_db_path.parent.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import distinct, func, select                # noqa: E402

from app import cache as cache_module                        # noqa: E402
from app.attributes import decorate_organization              # noqa: E402
from app.bootstrap import ensure_schema                      # noqa: E402
from app.config import settings                              # noqa: E402
from app.db import SessionLocal                              # noqa: E402
from app.domain import models                                # noqa: E402
from app.pie_service import Candidate, Resolution, pie_service  # noqa: E402
from app.sellable_catalog import (                                 # noqa: E402
    SELLABLE_LABEL, build_pool, pool_version)

# Alembic and the sync logger both talk at INFO. Ninety lines of migration
# output above a measurement is how the measurement stops being read.
logging.disable(logging.INFO)

#: The three fields a mismatch on which excludes a candidate outright
#: (`equivalence.distance.HARD_GATE_FIELDS`). Restated here to *count* how many
#: pool records can be gated at all, never to re-implement the gate.
_HARD_GATE_FIELDS: Tuple[str, ...] = ("product_family", "iso_shape",
                                      "insert_polarity")

#: The graded dimensional fields (`equivalence.distance.DIMENSIONAL_FIELDS`),
#: counted for the same reason: a pool record carrying none of them can never
#: contribute a compared dimension, so every comparison it wins is vacuous.
_DIMENSIONAL_FIELDS: Tuple[str, ...] = (
    "cutting_dia_mm", "edge_length_mm", "shank_dia_mm", "loc_mm", "oal_mm",
    "thickness_mm", "corner_radius_mm")


# ── seeding ──────────────────────────────────────────────────────────────────

def _seed(session, org: str, link_rate: int) -> Tuple[int, int]:
    """Load the real nomenclature corpus as one organization's item master.

    Returns (products, linked). Deterministic in every respect — row order, ids,
    and which rows link — because a measurement whose seed moves between runs
    cannot be compared with its own previous output.

    The link is modelled the way `ingestion.sync._link_catalog` actually makes
    one: an item links because its **SKU is exactly the catalogue number**, so a
    linked item's `external_id` is the MM# and an unlinked item's is an ordinary
    item id. Every `link_rate`-th row links, which at the default of 11 is 9.1%
    — the ~9% the live master carries.
    """
    if not settings.PIE_CORPUS.exists():
        sys.exit(f"PIE corpus not found at {settings.PIE_CORPUS}. Set "
                 "PIE_PARSER_ROOT (or PIE_CORPUS) to a pie-parser checkout.")

    session.add(models.Organization(organization_id=org,
                                    name="Pool measurement", currency="INR"))
    linked = 0
    with settings.PIE_CORPUS.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for index, row in enumerate(rows):
        material = (row.get("MM#") or "").strip()
        description = (row.get("Material Description") or "").strip()
        if not material or not description:
            continue
        is_linked = (index % link_rate == 0)
        linked += int(is_linked)
        session.add(models.Product(
            product_id=f"prod_{index:06d}",
            organization_id=org,
            connector="zoho",
            connection_id="conn_pool_measure",
            external_id=material if is_linked else f"ITEM-{index:06d}",
            name=description,
            manufacturer="KENNAMETAL INDIA LIMITED",
            active=True,
            pie_record_id=material if is_linked else None,
            pie_link_method="SKU_EXACT" if is_linked else None,
        ))
    session.commit()
    return len(rows), linked


# ── coverage: how much of the book is reachable at all ───────────────────────

def _coverage(session, org: str, pool) -> Dict[str, Any]:
    """Pool membership, before and after. An upper bound, and labelled as one.

    A product in the pool is a product the ranking *can* return. It is not a
    product the ranking *will* return, and the gap between those is the whole
    subject of the requirement arm below. Reporting membership as "offerable"
    without that sentence beside it would be the padded numerator
    `attributes/extract.ROUTE_FIELDS` refuses for the same reason.
    """
    products = session.scalar(select(func.count()).select_from(models.Product)
                              .where(models.Product.organization_id == org))
    linked = session.scalar(
        select(func.count()).select_from(models.Product).where(
            models.Product.organization_id == org,
            models.Product.pie_record_id.is_not(None)))
    with_rows = session.scalar(
        select(func.count(distinct(models.ProductAttributeValue.product_id)))
        .where(models.ProductAttributeValue.organization_id == org,
               models.ProductAttributeValue.superseded_at.is_(None)))
    linked_with_rows = session.scalar(
        select(func.count(distinct(models.ProductAttributeValue.product_id)))
        .where(models.ProductAttributeValue.organization_id == org,
               models.ProductAttributeValue.superseded_at.is_(None),
               models.ProductAttributeValue.product_id.in_(
                   select(models.Product.product_id).where(
                       models.Product.organization_id == org,
                       models.Product.pie_record_id.is_not(None)))))

    # What the pool records can actually be *judged* on. A record carrying no
    # hard-gate field cannot be excluded by one; a record carrying no
    # dimensional field cannot contribute a compared dimension, so every
    # comparison it survives is dimensionally vacuous by construction. Read off
    # the source's own records rather than recomputed from the table, so this
    # counts the pool the engine will see and not a second derivation of it.
    records = [r.record for r in pool.load()] if pool is not None else []
    gate_counts = {f: sum(1 for r in records if r.get(f) is not None)
                   for f in _HARD_GATE_FIELDS}
    any_gate = sum(1 for r in records
                   if any(r.get(f) is not None for f in _HARD_GATE_FIELDS))
    any_dimension = sum(1 for r in records if any(
        r.get(f) is not None for f in _DIMENSIONAL_FIELDS))
    return {
        "products": products,
        "before_reachable": linked,
        # Union, not a sum: a linked product that also decoded is one product.
        "after_reachable": linked + with_rows - linked_with_rows,
        "pool_records": len(records),
        # The source's own count of products it had to leave out. Carried rather
        # than inferred from `products - pool_records`, because the two can
        # differ for a second reason — `build_pool` reads active products only.
        "products_without_attributes": (
            getattr(pool, "without_attributes", None) if pool is not None else None),
        "products_with_live_attributes": with_rows,
        "linked_products_with_live_attributes": linked_with_rows,
        "pool_with_any_hard_gate_field": any_gate,
        "pool_with_any_dimensional_field": any_dimension,
        "pool_hard_gate_field_counts": gate_counts,
    }


# ── the two arms ─────────────────────────────────────────────────────────────

def _resolve_arm(texts: Sequence[str],
                 pool) -> Tuple[Dict[str, Resolution], Dict[str, float]]:
    """Resolve every probe through `pie_service`, with or without the book.

    `pool` is passed as an argument on every call — the shipped seam — rather
    than installed on the service. That is decision 003's first constraint
    honoured by the code under test, so the harness does not have to work around
    it: the BEFORE arm is `pool=None`, which is what a deployment with nothing
    decorated does today, and the AFTER arm passes the organization's source.

    The resolution cache is cleared between arms. `_cache_key` does fingerprint
    the pool, so a stale answer could not be served across arms anyway; the clear
    is here so that **every timed resolution is a miss**. Without it the AFTER
    arm's latency would be a measurement of the cache, and the cost this script
    is asked about is the cost of searching a bigger pool.
    """
    pie_service.warm()
    cache_module.clear_all()

    # One untimed pass before the timed one. The authoritative index is built
    # lazily inside `run` (222 ms for this corpus, per resolve_rfq's own note)
    # and the pack loads on first use, so the first probe of a cold arm measures
    # the warm-up rather than the pool. Cleared again after, so the pass itself
    # leaves nothing cached.
    if texts:
        pie_service.resolve(texts[0], pool=pool)
    cache_module.clear_all()

    results: Dict[str, Resolution] = {}
    elapsed_ms: Dict[str, float] = {}
    for text in texts:
        started = time.perf_counter()
        results[text] = pie_service.resolve(text, pool=pool)
        elapsed_ms[text] = (time.perf_counter() - started) * 1000.0
    cache_module.clear_all()
    return results, elapsed_ms


def _cache_keys_collide(texts: Sequence[str], pool, other) -> Optional[bool]:
    """Do two organizations' pools produce the same resolution-cache key?

    Asked of `PieService` itself rather than answered by reading `_cache_key` and
    reasoning about it, because the property it establishes is a *cross-tenant*
    one — `_resolution_cache` is process-wide, so a key that does not name the
    pool serves one organization's ranked candidates to another — and a finding
    of that class should rest on the running code.

    **Three keys, because two do not answer the question.** The first draft
    compared no-pool against this-org's-pool, which establishes only that the
    pool reaches the key at all; a fingerprint that named the pool but not the
    *organization* would pass that and still collide between two tenants, which
    is the failure the finding claims to have closed. So the second organization
    is seeded and its pool built for no other purpose than to be the key that
    has to differ. `tests/decision_platform/test_sellable_catalog_pool.py`
    asserts the same property on the answers rather than the keys; this is the
    version that runs against a full master.

    ``None`` when caching is disabled, when the key is refused, or when the
    second pool could not be built — each makes the question moot rather than
    answered. Absence of a key is not a passing key.
    """
    if not texts or settings.PIE_CACHE_SIZE == 0 or other is None:
        return None
    keys = [pie_service._cache_key(texts[0], None, None, p)
            for p in (None, pool, other)]
    if any(k is None for k in keys):
        return None
    return len(set(keys)) != len(keys)


def _seed_second_org(session, org: str) -> Optional[str]:
    """A second organization with its own small book, for the key check alone.

    Small on purpose — the question is whether two pools key differently, and a
    second full decode would add a quarter-minute to every run to answer it no
    better. It is a real book by the same route as the first: real product rows,
    decorated by `decorate_organization`, pooled by `build_pool`.
    """
    if not settings.PIE_CORPUS.exists():
        return None
    session.add(models.Organization(organization_id=org,
                                    name="Pool measurement, second tenant",
                                    currency="INR"))
    with settings.PIE_CORPUS.open("r", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh)][:200]
    for index, row in enumerate(rows):
        description = (row.get("Material Description") or "").strip()
        if not description:
            continue
        session.add(models.Product(
            product_id=f"other_{index:06d}", organization_id=org,
            connector="zoho", connection_id="conn_pool_measure_other",
            external_id=f"OTHER-{index:06d}", name=description,
            manufacturer="KENNAMETAL INDIA LIMITED", active=True))
    session.commit()
    decorate_organization(session, org, on_batch=lambda done, total: session.commit())
    session.commit()
    return org


# ── comparison ───────────────────────────────────────────────────────────────

def _candidate_row(cand: Candidate) -> Dict[str, Any]:
    return {"code": cand.code, "desc": cand.desc, "rel": cand.rel,
            "brand": cand.brand, "score": cand.score,
            "unverified": cand.unverified, "grade": cand.grade}


def _identity(cand: Candidate) -> Tuple[str, str]:
    """What makes two candidates the same *entry* in a result list.

    The code alone is not enough. The two pools key their records differently —
    the catalogue on the manufacturer's number, the book on the portal's own
    `product_id` — so a code is unique in practice today, and a comparison that
    relied on that would break the moment either side changed its key. `brand` is
    the catalog label the engine stamps ("pie" or "book"), so the pair says both
    which product and which pool it came from.
    """
    return (cand.code, str(cand.brand or ""))


#: Fields compared when asking "is this the same product, offered twice?".
#: `product_family` is deliberately absent. It was absent originally because a
#: master record never carried one; it stays absent now that every one does,
#: because a field both sides always agree on adds nothing to a same-product
#: test and a field only one side carries would make every cross-pool
#: comparison differ — either way the count comes back a confident zero, which
#: is the benign default this repository refuses.
_SHAPE_FIELDS: Tuple[str, ...] = (
    "iso_shape", "iso_clearance_letter", "iso_tolerance", "iso_fixing",
    "corner_radius_mm", "cutting_dia_mm", "edge_length_mm")

#: How many of those must actually be known before two shapes agreeing means
#: anything. Without a floor, two records that decoded to nothing would have
#: identical all-None shapes and be counted as the same product.
_SHAPE_MIN_KNOWN = 3


def _shape(cand: Candidate) -> Optional[Tuple[Any, ...]]:
    """The canonical spec a reader would call "the same insert", or None.

    Used as ONE of two routes to a shadow duplicate — one physical product
    offered twice, once from the catalogue and once from the master.
    `equivalence.query._dedup` cannot collapse those because its key includes
    `description_norm`, which a master record does not carry; that is a real
    property of the composed pool and counting it is not a re-implementation of
    the dedup.

    ``None`` when too little decoded to say, which is an UNKNOWN and not a
    mismatch: such a candidate is left out of the count in both directions —
    and it is why this route alone under-counts, see :func:`_desc_key`.
    """
    attrs = cand.attributes or {}
    values = tuple(attrs.get(f) for f in _SHAPE_FIELDS) + (cand.grade,)
    if sum(1 for v in values if v is not None) < _SHAPE_MIN_KNOWN:
        return None
    return values


def _desc_key(cand: Candidate) -> Optional[str]:
    """The second route to a shadow duplicate: the two entries read the same.

    :func:`_shape` needs `_SHAPE_MIN_KNOWN` decoded fields before it will call
    two candidates one product, and a record that decodes to fewer never
    qualifies however obviously it is the same thing. That floor made the count
    a **false zero**: an earlier run reported 0/62 while its own printed output
    showed a `[pie]` and a `[book]` entry with byte-identical descriptions in
    the same result list, three separate times. A count that reads zero beside
    the evidence against it is worse than no count, because it is the one a
    reader quotes.

    An identical description is not proof of an identical product — two items
    can share a short text and differ in the digits nobody typed — so this route
    is reported as its own number rather than folded into the shape one. It is
    the weaker claim of the two and the one that actually fires here.

    ``None`` for a description too short to mean anything, which is an UNKNOWN.
    """
    text = re.sub(r"[^a-z0-9]+", " ", str(cand.desc or "").lower()).strip()
    return text if len(text) >= 12 else None


_REL_ORDER = {"EXACT": 4, "TECH": 3, "COMPAT": 2, "POSSIBLE": 1,
              "AMBIGUOUS": 0, "UNRESOLVED": 0, "PIE_DOWN": -1}


def _compare(text: str, before: Resolution, after: Resolution) -> Dict[str, Any]:
    """One probe, both arms, and every way this could have gone wrong.

    The verdicts are deliberately not collapsed into "better"/"worse". A supply
    that changed is not automatically a regression and not automatically a win —
    it is the case a person has to look at, which is why the JSON carries both
    lists in full and the console prints the changed ones.
    """
    b_ids = [_identity(c) for c in before.candidates]
    a_ids = [_identity(c) for c in after.candidates]
    b_set, a_set = set(b_ids), set(a_ids)
    new = [c for c in after.candidates if _identity(c) not in b_set]
    dropped = [c for c in before.candidates if _identity(c) not in a_set]

    b_top = before.candidates[0] if before.candidates else None
    a_top = after.candidates[0] if after.candidates else None

    # The top slot changed hands to a record that was not in the old list at
    # all. Reported on its own rather than only in its worst form, because the
    # worst form turns out to be rare for a reason that is not reassuring: most
    # BEFORE leaders are already unverified, so "a vacuous candidate replaced a
    # verified one" cannot fire. Both flags are carried and both are printed.
    leader_changed = bool(
        a_top is not None and b_top is not None
        and _identity(a_top) != _identity(b_top)
        and _identity(a_top) not in b_set)
    # The harm decision 017 fixed, arriving through the pool: a candidate whose
    # comparison covered nothing the request specified now leads a list whose
    # previous leader had a real comparison behind it.
    vacuous_new_leader = bool(
        leader_changed and a_top.unverified and not b_top.unverified)
    # Total displacement: nothing the old list offered survives into the new
    # one. The engine may still abstain on both, so this is not automatically a
    # wrong answer — it is the visible list of options replaced wholesale, which
    # is what a person actually reads off the screen.
    displaced_all = bool(before.candidates and not (b_set & a_set))

    # The family question, which `unverified` cannot answer and which turns out
    # to be the sharper one. "solid carbide drill 8mm 3xD" replaces three real
    # drills with three end mills at `unverified=False`: the diameters *were*
    # compared, so nothing about the comparison was vacuous — what was never
    # compared is whether the two are the same kind of tool, because the master
    # record carries no `product_family` for the gate to catch (finding (a)).
    b_family = (b_top.attributes or {}).get("product_family") if b_top else None
    a_family = (a_top.attributes or {}).get("product_family") if a_top else None
    leader_family_unstated = bool(leader_changed and b_family and not a_family)
    leader_family_differs = bool(leader_changed and b_family and a_family
                                 and a_family != b_family)

    # A verified old candidate pushed down the list by a new unverified one.
    # Wider than the leader case and the one that erodes a result list quietly.
    outranked = []
    for old_pos, old in enumerate(before.candidates):
        if _identity(old) not in a_set:
            continue
        new_pos = a_ids.index(_identity(old))
        if new_pos <= old_pos or old.unverified:
            continue
        jumpers = [c for c in after.candidates[:new_pos]
                   if _identity(c) not in b_set and c.unverified]
        if jumpers:
            outranked.append({
                "displaced": _candidate_row(old),
                "from_rank": old_pos + 1, "to_rank": new_pos + 1,
                "by": [_candidate_row(j) for j in jumpers]})

    # One product offered twice in one list: the same catalogue number reached
    # from two pools (a linked item enters under the number it links to), or two
    # entries whose decoded specs agree on enough fields to be read as one part.
    codes_before = {c.code for c in before.candidates}
    shapes_before = {s for s in (_shape(c) for c in before.candidates)
                     if s is not None}
    descs_before = {d for d in (_desc_key(c) for c in before.candidates)
                    if d is not None}
    shadow = [c for c in new
              if c.code in codes_before
              or (_shape(c) is not None and _shape(c) in shapes_before)]
    shadow_desc = [c for c in new
                   if c not in shadow
                   and _desc_key(c) is not None
                   and _desc_key(c) in descs_before]
    duplicate_codes = sorted({c.code for c in after.candidates
                              if sum(1 for o in after.candidates
                                     if o.code == c.code) > 1})

    if before.supplyCode is None and after.supplyCode is not None:
        verdict = "new_resolution"
    elif before.supplyCode is not None and after.supplyCode is None:
        verdict = "lost_resolution"
    elif before.supplyCode != after.supplyCode:
        verdict = "supply_changed"
    elif _REL_ORDER.get(after.rel, 0) < _REL_ORDER.get(before.rel, 0):
        verdict = "rel_downgrade"
    elif _REL_ORDER.get(after.rel, 0) > _REL_ORDER.get(before.rel, 0):
        verdict = "rel_upgrade"
    elif b_ids == a_ids:
        verdict = "unchanged"
    elif displaced_all:
        verdict = "candidates_replaced"
    else:
        verdict = "candidates_reordered"

    return {
        "input": text,
        "verdict": verdict,
        "before": {"rel": before.rel, "supplyCode": before.supplyCode,
                   "outcome": before.outcome, "semantics": before.semantics,
                   "candidates": [_candidate_row(c) for c in before.candidates]},
        "after": {"rel": after.rel, "supplyCode": after.supplyCode,
                  "outcome": after.outcome, "semantics": after.semantics,
                  "candidates": [_candidate_row(c) for c in after.candidates]},
        "new_candidates": [_candidate_row(c) for c in new],
        "new_candidates_unverified": sum(1 for c in new if c.unverified),
        "dropped_candidates": [_candidate_row(c) for c in dropped],
        "leader_changed": leader_changed,
        "leader_family_before": b_family,
        "leader_family_after": a_family,
        "leader_family_unstated": leader_family_unstated,
        "leader_family_differs": leader_family_differs,
        "displaced_all": displaced_all,
        "vacuous_new_leader": vacuous_new_leader,
        "verified_outranked_by_vacuous_new": outranked,
        "shadow_duplicates": [_candidate_row(c) for c in shadow],
        "shadow_duplicates_by_description": [_candidate_row(c)
                                             for c in shadow_desc],
        "duplicate_codes_in_after": duplicate_codes,
        # Filled in by a person, not by this script: whether the new candidate
        # is one the business would actually have offered. No score answers it.
        "owner_would_have_offered": None,
    }


# ── probes ───────────────────────────────────────────────────────────────────

def _probe_lines() -> List[Tuple[str, str]]:
    """(channel, text) pairs, each channel labelled because they are not alike.

    * `file` — the reviewer's own lines, when `--requirements` is given. Real
      inbound text is what this arm is for; everything below is a stand-in.
    * `benchmark` — pie-parser's `eval/rfq_benchmark/cases.jsonl`. Its own README
      says these are a hand-written **seed set**, "shaped like real inbound text
      but not received from anyone", so a rate computed over them is a property
      of the seed. Reported per channel so nobody averages the two.
    * `catalogue_text` — real catalogue descriptions from the corpus, drawn from
      rows the seeded master did **not** link. These are the favourable case by
      construction: the request text is a master row's own description. Read
      them as an upper bound on what the new pool can do, never as a rate.
    * `catalogue_id` — the bare MM# of some of those same rows. Carried so the
      exact-identity path is actually exercised: without it, finding (c) below
      would be a claim about a branch no probe ever entered, which is the shape
      of evidence this repository refuses everywhere else.
    """
    if args.requirements is not None:
        if not args.requirements.exists():
            sys.exit(f"No such file: {args.requirements}")
        return [("file", line.strip())
                for line in args.requirements.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]

    probes: List[Tuple[str, str]] = []
    cases = settings.PIE_PARSER_ROOT / "eval" / "rfq_benchmark" / "cases.jsonl"
    if cases.exists():
        for line in cases.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            text = (json.loads(line).get("text") or "").strip()
            if text:
                probes.append(("benchmark", text))

    if args.catalogue_probes > 0 and settings.PIE_CORPUS.exists():
        with settings.PIE_CORPUS.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        unlinked = [r for i, r in enumerate(rows) if i % args.link_rate != 0]
        step = max(1, len(unlinked) // args.catalogue_probes)
        drawn = unlinked[::step][:args.catalogue_probes]
        for row in drawn:
            text = (row.get("Material Description") or "").strip()
            if text:
                probes.append(("catalogue_text", text))
        for row in drawn[:args.identity_probes]:
            material = (row.get("MM#") or "").strip()
            if material:
                probes.append(("catalogue_id", material))

    # De-duplicated, first channel wins, order preserved: the same string
    # resolved twice would be one cache hit and one miss and would corrupt the
    # latency arm.
    seen: set = set()
    out: List[Tuple[str, str]] = []
    for channel, text in probes:
        if text not in seen:
            seen.add(text)
            out.append((channel, text))
    return out


# ── report ───────────────────────────────────────────────────────────────────

def _pct(n: int, d: int) -> str:
    return "n/a" if not d else f"{100.0 * n / d:.1f}%"


def _latency_line(label: str, values: Sequence[float]) -> str:
    if not values:
        return f"  {label:<8}: no samples"
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return (f"  {label:<8}: median {statistics.median(ordered):7.1f} ms   "
            f"mean {statistics.fmean(ordered):7.1f} ms   p95 {p95:7.1f} ms   "
            f"max {ordered[-1]:7.1f} ms")




def _tally(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Every grouping the harm arm and the JSON both need, computed once.

    One tally rather than a set of comprehensions in the print block and the
    same set again in the JSON writer: two copies of "which rows count as a lost
    resolution" is the semantic duplication CLAUDE.md §2 names, and the copy that
    drifts is the one in the file nobody reads — which here is the JSON, the half
    a reviewer actually loads.
    """
    lines = list(rows)
    verdicts: Dict[str, int] = {}
    by_channel: Dict[str, Dict[str, int]] = {}
    for row in lines:
        verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
        channel = by_channel.setdefault(row["channel"], {})
        channel[row["verdict"]] = channel.get(row["verdict"], 0) + 1

    # The fuzzy path on its own. An exact identity resolves to a supply in both
    # arms by construction (finding (c)), so leaving those in would make the
    # requirement path look like it answers when it does not.
    requirement = [r for r in lines if r["before"]["rel"] != "EXACT"]
    return {
        "rows": lines,
        "verdicts": verdicts,
        "by_channel": by_channel,
        "lost": [r for r in lines if r["verdict"] == "lost_resolution"],
        "swapped": [r for r in lines if r["verdict"] == "supply_changed"],
        "leader_changed": [r for r in lines if r["leader_changed"]],
        "vacuous_leader": [r for r in lines if r["vacuous_new_leader"]],
        "family_unstated": [r for r in lines if r["leader_family_unstated"]],
        "family_differs": [r for r in lines if r["leader_family_differs"]],
        "outranked": [r for r in lines if r["verified_outranked_by_vacuous_new"]],
        "replaced": [r for r in lines if r["displaced_all"]],
        "shadow": [r for r in lines if r["shadow_duplicates"]],
        "shadow_desc": [r for r in lines
                        if r["shadow_duplicates_by_description"]],
        "dup_codes": [r for r in lines if r["duplicate_codes_in_after"]],
        "resolved_before": [r for r in lines
                            if r["before"]["supplyCode"] is not None],
        "requirement": requirement,
        "req_before": [r for r in requirement
                       if r["before"]["supplyCode"] is not None],
        "req_after": [r for r in requirement
                      if r["after"]["supplyCode"] is not None],
        "exact_before": [r for r in lines if r["before"]["rel"] == "EXACT"],
        "exact_after": [r for r in lines if r["after"]["rel"] == "EXACT"],
        "exact_same": [r for r in lines if r["before"]["rel"] == "EXACT"
                       and r["after"]["supplyCode"] == r["before"]["supplyCode"]],
        "new_total": sum(len(r["new_candidates"]) for r in lines),
        "new_vacuous": sum(r["new_candidates_unverified"] for r in lines),
        "after_slots": sum(len(r["after"]["candidates"]) for r in lines),
        "after_slots_from_master": sum(
            1 for r in lines for c in r["after"]["candidates"]
            if c["brand"] == SELLABLE_LABEL),
    }


def _harm_counts(tally: Dict[str, Any]) -> Dict[str, int]:
    """The tally as plain numbers, for the JSON. Same source as the print."""
    return {
        "lines": len(tally["rows"]),
        "resolved_to_a_supply_before": len(tally["resolved_before"]),
        "requirement_lines": len(tally["requirement"]),
        "requirement_supply_before": len(tally["req_before"]),
        "requirement_supply_after": len(tally["req_after"]),
        "lost_resolution": len(tally["lost"]),
        "supply_changed": len(tally["swapped"]),
        "leader_changed": len(tally["leader_changed"]),
        "vacuous_new_leader": len(tally["vacuous_leader"]),
        "leader_family_unstated": len(tally["family_unstated"]),
        "leader_family_differs": len(tally["family_differs"]),
        "displaced_all": len(tally["replaced"]),
        "verified_outranked_by_vacuous_new": len(tally["outranked"]),
        "lines_with_shadow_duplicate": len(tally["shadow"]),
        "lines_with_shadow_duplicate_by_description": len(tally["shadow_desc"]),
        "lines_with_repeated_code": len(tally["dup_codes"]),
        "new_candidates": tally["new_total"],
        "new_candidates_unverified": tally["new_vacuous"],
        "after_slots": tally["after_slots"],
        "after_slots_from_master": tally["after_slots_from_master"],
    }


def _print_coverage(coverage: Dict[str, Any], decoration) -> None:
    print("\n-- 1. HOW MUCH OF THE BOOK IS REACHABLE AS A CANDIDATE " + "-" * 19)
    print("   Pool membership: what the ranking CAN return, never what it will.")
    products = coverage["products"]
    print(f"  products in the master              : {products}")
    print(f"  BEFORE — catalogue link only        : {coverage['before_reachable']}"
          f"  ({_pct(coverage['before_reachable'], products)})")
    print(f"  AFTER  — link OR decoded attributes : {coverage['after_reachable']}"
          f"  ({_pct(coverage['after_reachable'], products)})")
    gained = coverage["after_reachable"] - coverage["before_reachable"]
    print(f"  gained                              : +{gained}"
          f"  ({_pct(gained, products)} of the book)")

    pool = coverage["pool_records"]
    print(f"\n  pool records built                  : {pool}")
    without = coverage["products_without_attributes"]
    if without is not None:
        print(f"  products left out (no live attribute): {without}"
              f"  ({_pct(without, products)})")
    print("  of those in the pool, carrying:")
    for field_name, count in coverage["pool_hard_gate_field_counts"].items():
        print(f"    {field_name:<18} (hard gate) : {count:>5}"
              f"  ({_pct(count, pool)})")
    print(f"    any hard-gate field            : "
          f"{coverage['pool_with_any_hard_gate_field']:>5}"
          f"  ({_pct(coverage['pool_with_any_hard_gate_field'], pool)})")
    print(f"    any dimensional field          : "
          f"{coverage['pool_with_any_dimensional_field']:>5}"
          f"  ({_pct(coverage['pool_with_any_dimensional_field'], pool)})")
    print("\n  CEILING, not a forecast. Every item name in this seeded master is a\n"
          "  catalogue-format designation, so it decodes at "
          f"{_pct(decoration.names_decoded, products)}. app/attributes/__init__\n"
          "  records DECODED_NAME reaching about 21% of the LIVE master, whose\n"
          "  names read 'SC DRILL SLOT ENDMILL'. Read the gain as an upper bound.")


def _print_candidate(indent: str, cand: Dict[str, Any]) -> None:
    print(f"{indent}- {cand['code']:<12} [{cand['brand']}] "
          f"{cand['rel']:<8} score={cand['score']} "
          f"unverified={cand['unverified']}  {cand['desc'][:40]}")


def _print_arms(tally: Dict[str, Any], shown: int = 12) -> None:
    rows = tally["rows"]
    print("\n-- 2. WHAT THE RANKING RETURNS, BEFORE AND AFTER " + "-" * 25)
    print(f"  {len(rows)} requirement lines, resolved through pie_service.resolve")
    print("  (top 3 of each list shown below; --json carries every candidate)")
    for channel, counts in sorted(tally["by_channel"].items()):
        detail = ", ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
        print(f"    {channel:<15} n={sum(counts.values()):<4} {detail}")

    changed = [r for r in rows if r["verdict"] != "unchanged"]
    for row in changed[:shown]:
        print(f"\n  [{row['verdict']}] {row['input']!r}  ({row['channel']})")
        for arm in ("before", "after"):
            print(f"      {arm.upper():<6} rel={row[arm]['rel']:<10} "
                  f"supply={row[arm]['supplyCode']}")
            for cand in row[arm]["candidates"][:3]:
                _print_candidate("        ", cand)
    if len(changed) > shown:
        print(f"\n  ... {len(changed) - shown} more changed lines; "
              "--json has all of them.")


def _print_harm(tally: Dict[str, Any]) -> None:
    print("\n-- 3. THE HARM ARM " + "-" * 54)
    n = len(tally["rows"])
    resolved = len(tally["resolved_before"])
    leaders = tally["leader_changed"]

    # The denominators first, because without them the zeros below read as good
    # news and they are not. A supply cannot be lost on a line that never had
    # one, so "0 lost" measures the abstention rate of the BEFORE arm at least
    # as much as it measures the safety of the new pool.
    print(f"  lines that resolved to a supply BEFORE        : {resolved}/{n}"
          f"   <- denominator for the two below")
    print(f"    of those, on the REQUIREMENT path           : "
          f"{len(tally['req_before'])}/{len(tally['requirement'])}"
          f"   (after: {len(tally['req_after'])})")
    print(f"  resolutions LOST (had a supply, now abstains) : "
          f"{len(tally['lost'])}/{resolved}")
    print(f"  supply CHANGED to a different product         : "
          f"{len(tally['swapped'])}/{resolved}")
    print(f"  top candidate REPLACED by a new one           : {len(leaders)}/{n}")
    print(f"    of those, the new leader is itself unverified: "
          f"{sum(1 for r in leaders if r['after']['candidates'][0]['unverified'])}")
    print(f"    of those, an unverified replacing a verified: "
          f"{len(tally['vacuous_leader'])}   <- decision 017's defect, exactly")
    print(f"    of those, the new leader states NO family   : "
          f"{len(tally['family_unstated'])}   <- and this is the sharper one")
    print(f"    of those, the new leader states a DIFFERENT : "
          f"{len(tally['family_differs'])}")
    print(f"  every old candidate GONE from the new list    : "
          f"{len(tally['replaced'])}/{n}")
    # "vacuous" is retained in the JSON keys and the variable names below: they
    # are a released shape and renaming them would break a reader's saved query
    # for a wording fix. The printed labels are what a person reads, and they
    # are the half that has to be exact.
    print(f"  verified candidate outranked by an unverified  : "
          f"{len(tally['outranked'])}/{n}")
    print(f"  lines showing one product twice (shadow dup)  : "
          f"{len(tally['shadow'])}/{n}   <- matched on decoded shape")
    print(f"    same again, matched on identical description: "
          f"{len(tally['shadow_desc'])}/{n}   <- the weaker, louder one")
    print(f"  lines with a repeated candidate CODE          : "
          f"{len(tally['dup_codes'])}/{n}")
    print(f"  new candidates surfaced                       : {tally['new_total']}")
    print(f"    of which UNVERIFIED                         : {tally['new_vacuous']}"
          f"  ({_pct(tally['new_vacuous'], tally['new_total'])})")
    print("      `unverified` is TWO conditions, not one: nothing about the\n"
          "      request was comparable (dimensionally vacuous), OR the request\n"
          "      named a dimension this record does not carry. This count does\n"
          "      not separate them, and an earlier draft labelled the whole of it\n"
          "      'dimensionally vacuous', which overstates the weaker half. Both\n"
          "      cap the candidate at POSSIBLE, which is why one number is enough\n"
          "      for the safety question and not enough for the wording.")
    print(f"  share of visible result slots now from the    :"
          f" {tally['after_slots_from_master']}/{tally['after_slots']}"
          f"  ({_pct(tally['after_slots_from_master'], tally['after_slots'])})")
    print("    master rather than the catalogue")

    # Sharpest class first, and each line printed once: a row is usually in
    # several of these lists, and the same case printed four times reads as four
    # cases.
    cases: List[Dict[str, Any]] = []
    for group in (tally["vacuous_leader"], tally["outranked"],
                  tally["family_unstated"], tally["family_differs"],
                  tally["replaced"]):
        for row in group:
            if not any(row is seen for seen in cases):
                cases.append(row)
    for row in cases[:4]:
        print(f"\n  CASE  {row['input']!r}  ({row['channel']})")
        if row["before"]["candidates"] and row["after"]["candidates"]:
            for label, arm in (("led by", "before"), ("now by", "after")):
                cand = row[arm]["candidates"][0]
                print(f"    {label}  {cand['code']:<12} [{cand['brand']}] "
                      f"unverified={cand['unverified']} score={cand['score']} "
                      f"family={row['leader_family_' + arm]}"
                      f"  {cand['desc'][:38]}")
        for hit in row["verified_outranked_by_vacuous_new"][:2]:
            displaced = hit["displaced"]
            print(f"    {displaced['code']} [{displaced['brand']}] fell "
                  f"{hit['from_rank']} -> {hit['to_rank']}, passed by "
                  + ", ".join(f"{j['code']} [{j['brand']}]" for j in hit["by"]))


def _build_latency(session, organization_id: str,
                   repeats: int) -> Dict[str, List[float]]:
    """How long the pool costs to build, and to check for staleness.

    `sellable_pool_for`'s docstring quotes both of these to justify caching, and
    a number quoted in a docstring that no committed script produces is a number
    nobody can challenge. The first draft of that table came out of a scratch
    session and could not be reproduced from this file — which is the reason
    this function exists rather than a reader being asked to take it on trust.

    Every repeat is a real round trip: SQLAlchemy's identity map would serve the
    second read of a `Product` from memory and time a dictionary lookup instead
    of a query, so the session is expired between repeats.
    """
    build: List[float] = []
    version: List[float] = []
    for _ in range(repeats):
        session.expire_all()
        started = time.perf_counter()
        pool_version(session, organization_id)
        version.append((time.perf_counter() - started) * 1000.0)
        session.expire_all()
        started = time.perf_counter()
        build_pool(session, organization_id)
        build.append((time.perf_counter() - started) * 1000.0)
    return {"build_pool": build, "pool_version": version}


def _print_latency(before_ms: Dict[str, float], after_ms: Dict[str, float],
                   coverage: Dict[str, Any],
                   build: Dict[str, List[float]]) -> None:
    print("\n-- 4. LATENCY " + "-" * 59)
    print(_latency_line("BEFORE", list(before_ms.values())))
    print(_latency_line("AFTER", list(after_ms.values())))
    b_med = statistics.median(before_ms.values())
    a_med = statistics.median(after_ms.values())
    print(f"  median moves {b_med:.1f} -> {a_med:.1f} ms "
          f"({a_med - b_med:+.1f} ms, {(a_med / b_med - 1) * 100:+.0f}%) "
          f"on {coverage['pool_records']} extra records")
    if coverage["pool_records"]:
        per_new = (a_med - b_med) * 1000.0 / coverage["pool_records"]
        per_old = b_med * 1000.0 / max(1, coverage["products"])
        print(f"  {per_new:.1f} us per extra record, against {per_old:.1f} us per "
              f"catalogue record —")
        print("  and the second still includes the parse and the identity lookup, so")
        print("  it is an over-estimate. A master record is not merely one more row to")
        print("  score, it is a dearer one: a record stating few fields is gated")
        print("  out by none of them and is scored in full. Read it beside finding")
        print("  (a), which counts how many records the family gate can act on.")
    print("  Every timed resolution is a cache MISS (the cache is cleared per\n"
          "  arm, see below), so this is the cold cost, not the served cost.")
    if build:
        print(f"\n  the pool itself, over {len(build['build_pool'])} repeats:")
        print(_latency_line("build", build["build_pool"]))
        print(_latency_line("version", build["pool_version"]))
        b_med = statistics.median(build["build_pool"])
        v_med = statistics.median(build["pool_version"])
        print(f"  a cache hit checks the version instead of building: "
              f"{v_med:.1f} ms against {b_med:.1f} ms, "
              f"{(1 - v_med / b_med) * 100:.0f}% of the build avoided.")


def _print_findings(coverage: Dict[str, Any], collide: Optional[bool],
                    tally: Dict[str, Any]) -> None:
    print("\n-- 5. STRUCTURAL FINDINGS, MEASURED " + "-" * 37)
    family = coverage["pool_hard_gate_field_counts"].get("product_family", 0)
    print(f"  a) product_family on pool records: {family}/{coverage['pool_records']}.")
    # This finding is printed FROM the count, not beside it. Its first draft was
    # a fixed paragraph saying the field was always absent, written when it was;
    # `Product.decoded_family` then made it always present and the paragraph went
    # on asserting the opposite two lines under the number that refuted it. A
    # finding that cannot be wrong is not a finding.
    missing = coverage["pool_records"] - family
    print("     It is a HARD GATE in equivalence.distance, and a gate constrains\n"
          "     only what BOTH sides specify — so a record without it is\n"
          "     transparent to it: a drill in the master would be a live candidate\n"
          "     for a turning-insert request.")
    if missing:
        print(f"     {missing} record(s) here state no family and are exactly that\n"
              "     case. The family cannot come from the attribute store —\n"
              "     NON_FACT_FIELDS drops it and ROUTE_FIELDS refuses\n"
              "     product_subfamily beside it, both deliberately — so a pool\n"
              "     that is short here is short at its source.")
    else:
        print("     Every record here states one, and it does NOT come from the\n"
              "     attribute store: NON_FACT_FIELDS drops the field and\n"
              "     ROUTE_FIELDS refuses product_subfamily beside it, both\n"
              "     deliberately. It comes from `products.decoded_family`, which\n"
              "     `decorate_products` stamps from the same decode. The count\n"
              "     above is the check that the column was actually populated —\n"
              "     a pool built before that migration reads 0 here and every\n"
              "     cross-family candidate it offers is scored as if in range.")
    if collide is True:
        print("  b) TWO OF THE THREE cache keys COLLIDE — no pool, this\n"
              "     organization's pool, and a second organization's pool do not\n"
              "     give three distinct keys.")
        print("     PieService._cache_key fingerprints catalogue version, text,\n"
              "     customer scope and mapping store. If the pool is missing from\n"
              "     it, or is fingerprinted without naming the organization, then\n"
              "     org B is served org A's ranked candidates from a process-wide\n"
              "     cache. That is decision 003's constraint 1 arriving through a\n"
              "     second door: _sources is the singleton everyone looks at,\n"
              "     _resolution_cache is the one that also has to change.")
    elif collide is False:
        print("  b) no pool, this organization's pool and a SECOND organization's\n"
              "     pool give three DISTINCT resolution-cache keys, so the key\n"
              "     names both the pool and whose it is.\n"
              "     `_resolution_cache` is process-wide: a key carrying the text and\n"
              "     the catalogue version but not the pool would have served one\n"
              "     organization's ranked candidates — records that exist only in\n"
              "     that tenant's book — to another. The second organization is\n"
              "     seeded for this check alone, because comparing only no-pool\n"
              "     against one pool proves the pool reaches the key and NOT that\n"
              "     two tenants differ in it. It is checked against the running\n"
              "     _cache_key rather than read off the source because it is the\n"
              "     property that would fail silently, with a plausible-looking\n"
              "     answer, if the argument were ever dropped from the key.")
    else:
        print("  b) resolution caching is disabled, or a second organization could\n"
              "     not be built, so the cache-key question is UNKNOWN rather than\n"
              "     answered.")
    print(f"  c) identity resolution: {len(tally['exact_before'])} probes resolved "
          f"EXACT before, {len(tally['exact_after'])} after,\n"
          f"     {len(tally['exact_same'])} of them to the same supply. "
          "_identity_resolver indexes\n"
          "     pie_data alone — a master record is not a source of exact\n"
          "     manufacturer identity — so the new pool reaches suggestions and\n"
          "     not identity. Stated because it is the one thing that could not\n"
          "     be allowed to change, and it is measured rather than assumed.")
    n_desc = len(tally["shadow_desc"])
    n_shape = len(tally["shadow"])
    print(f"  d) one product shown TWICE: {n_shape} line(s) by decoded shape, "
          f"{n_desc} by identical description.")
    if n_desc > n_shape:
        print("     The two routes disagree, and the description one is right about\n"
              "     what a reader sees. `query._dedup` keys on `description_norm`,\n"
              "     which a book record does not carry, so it cannot collapse a\n"
              "     catalogue entry against the book entry for the same physical\n"
              "     product — the list shows both, at the same score, and spends two\n"
              "     of six slots saying one thing. This is a REAL cost of composing\n"
              "     the pool, it is not a safety failure (neither entry is wrong),\n"
              "     and it is unfixed: de-duplicating across pools means an exact\n"
              "     key both sides can compute, which is the identity problem and\n"
              "     not a display one. Recorded rather than quietly tolerated.")
    if not tally["exact_before"]:
        print("     !! no probe resolved EXACT in either arm, so this line is\n"
              "        UNKNOWN rather than confirmed. Raise --identity-probes.")


def _print_epilogue(tally: Dict[str, Any]) -> None:
    print("\n" + "=" * 74)
    print("WHAT THIS DOES AND DOES NOT ESTABLISH")
    print("=" * 74)
    print("Established: the size of the pool gain, on a master whose text decodes\n"
          "far better than a real one; the direction and size of the latency cost;\n"
          "and whether the harm classes above occur at all, with the cases named.")
    if not tally["req_before"] and not tally["req_after"]:
        print("\nAnd one thing the zeros above do NOT say. On this probe set the\n"
              "requirement path resolved to a supply on ZERO lines in BOTH arms —\n"
              "every one abstained. So 'no resolution lost' and 'no supply changed'\n"
              "are properties of an engine that was already abstaining, not evidence\n"
              "that the new pool is safe to auto-select from. The abstention guards\n"
              "(_is_discriminating, and the vacuity refusal) are carrying the whole\n"
              "load, and they are what keeps the vacuous new candidates off a quote.\n"
              "Weaken either and this measurement stops applying.\n"
              "\n"
              "And they never covered the family case: a master end mill matched\n"
              "to a drill request on diameter alone is `unverified=False` — a\n"
              "dimension really was compared — so neither guard objects, and what\n"
              "was never compared is whether the two are the same kind of tool.\n"
              "That case is closed by the hard gate rather than by a guard, which\n"
              "is why finding (a) counts the records the gate can act on and the\n"
              "harm arm counts leaders stating no family and a different one. If\n"
              "finding (a) ever reads short of the pool size, this paragraph is\n"
              "live again for exactly those records.")
    print("\nNOT established: whether a new candidate is one this business would\n"
          "have offered. No score answers that and this script does not pretend to\n"
          "— `owner_would_have_offered` is null on every row in the JSON, and the\n"
          "measurement that decides the slice is a person filling it in.")


def main() -> int:
    session = SessionLocal()
    try:
        ensure_schema()
        products, linked = _seed(session, args.org, args.link_rate)

        started = time.perf_counter()
        # The caller commits at each batch boundary, which is where this package
        # says the boundary belongs — and on SQLite it is the difference between
        # seventeen short write windows and one long lock (CLAUDE.md §4).
        decoration = decorate_organization(
            session, args.org, on_batch=lambda done, total: session.commit())
        session.commit()
        decoration_seconds = time.perf_counter() - started

        # `build_pool`, not `sellable_pool_for`: the cached entrypoint would
        # serve a pool built before decoration on a second run in one process,
        # and a measurement must read the book it just wrote.
        pool = build_pool(session, args.org)
        coverage = _coverage(session, args.org, pool)
        if pool is None:
            sys.exit("build_pool returned nothing: no product in this "
                     "organization holds a live attribute, so there is no AFTER "
                     "arm to measure. Decoration wrote "
                     f"{decoration.written.created} rows.")

        probes = _probe_lines()
        if not probes:
            sys.exit("No requirement lines to probe with. Nothing to measure.")
        texts = [text for _channel, text in probes]
        channel_of = {text: channel for channel, text in probes}

        other_org = _seed_second_org(session, f"{args.org}_other")
        other_pool = build_pool(session, other_org) if other_org else None
        collide = _cache_keys_collide(texts, pool, other_pool)
        before, before_ms = _resolve_arm(texts, None)
        after, after_ms = _resolve_arm(texts, pool)
        build_ms = _build_latency(session, args.org, args.build_repeats)

        rows = [_compare(text, before[text], after[text]) for text in texts]
        for row in rows:
            row["channel"] = channel_of[row["input"]]
            row["before_ms"] = round(before_ms[row["input"]], 1)
            row["after_ms"] = round(after_ms[row["input"]], 1)
        tally = _tally(rows)

        print("\n" + "=" * 74)
        print("SELLABLE MASTER AS A CANDIDATE POOL — decision 003, first slice")
        print("=" * 74)
        print(f"corpus            : {settings.PIE_CORPUS.name} ({products} rows)")
        print(f"catalogue         : {settings.PIE_CATALOG.name} "
              f"(ruleset {pie_service.catalog_version or 'unknown'})")
        print(f"organization      : {args.org}   scratch db: {_db_path}")
        print(f"decoration        : {decoration_seconds:.1f}s, "
              f"{decoration.written.created} attribute rows written")
        if not decoration.decoders_available:
            # A source that could not be *asked* has not been measured. Refusing
            # to print the rest is the same refusal `DecorationReport` makes by
            # carrying these as sentences instead of zeros.
            print("!! a decoder was UNAVAILABLE for this run, so its coverage is\n"
                  "   UNKNOWN rather than zero. Everything below would be invalid:")
            for reason in (decoration.decoded_name_unavailable,
                           decoration.catalogue_unavailable):
                if reason:
                    print(f"   - {reason}")
            return 2

        _print_coverage(coverage, decoration)
        _print_arms(tally)
        _print_harm(tally)
        _print_latency(before_ms, after_ms, coverage, build_ms)
        _print_findings(coverage, collide, tally)
        _print_epilogue(tally)

        if args.json:
            args.json.write_text(json.dumps({
                "corpus": settings.PIE_CORPUS.name,
                "catalog_version": pie_service.catalog_version,
                "organization": args.org,
                "link_rate": args.link_rate,
                "seeded_products": products,
                "seeded_links": linked,
                "decoration_seconds": round(decoration_seconds, 2),
                "attribute_rows": decoration.written.created,
                "names_decoded": decoration.names_decoded,
                "catalogue_records_read": decoration.catalogue_records_read,
                "coverage": coverage,
                "verdicts": tally["verdicts"],
                "verdicts_by_channel": tally["by_channel"],
                "harm": _harm_counts(tally),
                "cache_key_collides": collide,
                "latency_ms": {
                    "before": {k: round(v, 1) for k, v in before_ms.items()},
                    "after": {k: round(v, 1) for k, v in after_ms.items()},
                    "build_pool": [round(v, 1) for v in build_ms["build_pool"]],
                    "pool_version": [round(v, 1)
                                     for v in build_ms["pool_version"]]},
                "lines": rows,
            }, indent=1, default=str))
            print(f"\nPer-line detail -> {args.json}")
        return 0
    finally:
        session.close()
        # Only the temporary directory this script made is ever removed. A
        # `--db` path the caller typed is theirs; a script that deletes one is
        # the kind of helpfulness nobody wants twice.
        keep_db = args.keep or args.db is not None
        if keep_db:
            print(f"\nScratch database kept at {_db_path}")
        if args.db is not None or not keep_db:
            shutil.rmtree(_scratch_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
