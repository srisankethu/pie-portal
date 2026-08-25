"""The Master Health Report itself: what is covered, what is missing, what to fix.

Everything here is a pure function of (the export's rows, the profile that read
them, the catalogue, the pack, the remediation policy). Nothing reads a
session, nothing reads a clock, and nothing reads a cost.

Three rules govern every number below, and they are the reason the shapes look
more careful than a count needs to be:

* **A missing column is UNKNOWN, never clean.** A source with no HSN column has
  no missing-HSN finding *and no clean bill of health either* — it has an
  unmeasured field, reported as such.
* **A missing catalogue is UNKNOWN, never zero.** "No pack covers this
  manufacturer" and "nobody asked the pack" are different facts and only the
  first is evidence about the master.
* **A missing value is not a zero.** Stock value sums over the rows that have
  both a rate and a quantity, and the report says how many rows that left out,
  rather than summing ``x or 0`` over rows holding ``None``.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from .geometry import GATED_SLOTS, PUBLISHED_SLOTS, DecodeRun
from .policy import RemediationPolicy
from .profile import ROLES, ColumnProfile
from .source import MasterRow

#: How many rows of an example list any section prints. A worklist is for
#: acting on, and a thousand example SKUs is a wall, not evidence.
EXAMPLE_LIMIT = 10

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def exact_key(value: Optional[str]) -> Optional[str]:
    """Trim and upper-case, leaving internal punctuation alone.

    Deliberately the same rule as pie-parser's ``identity.store.normalize_identifier``
    and as ``app/identity/service.normalize_code``, which restates it for the
    same reason: separators distinguish two genuinely different catalogue
    numbers, so stripping them would manufacture an identity.

    Restated rather than imported because importing either owner would drag a
    dependency this offline report does not otherwise need — pie-parser's copy
    is behind an optional submodule that may be absent, and the portal's is in a
    module that imports the ORM. ``test_master_health.py`` pins this function
    against pie-parser's whenever the engine is present, so the copy cannot
    drift silently.
    """
    if value is None:
        return None
    key = str(value).strip().upper()
    return key or None


def loose_key(value: Optional[str]) -> Optional[str]:
    """A deliberately over-eager key, used ONLY to nominate duplicates.

    This strips every separator, which :func:`exact_key` refuses to do — and
    that refusal is exactly why this must never be used for identity. Two codes
    that collapse to one key here are a **candidate pair for a human to look
    at**, never an assertion that they are the same item and never an input to
    a merge. ``KM-1234`` and ``KM1234`` usually are the same item; sometimes
    they are two, and the report has no way to tell.
    """
    if value is None:
        return None
    key = _NON_ALNUM.sub("", str(value).upper())
    return key or None


def _share(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def _pct_text(part: int, whole: int) -> str:
    share = _share(part, whole)
    return "UNKNOWN" if share is None else f"{share * 100:.1f}%"


def _money_share(part: Decimal, whole: Decimal) -> Optional[float]:
    """A ratio of two sums of money, computed in Decimal and rounded once.

    Σ covered ÷ Σ total, never the mean of per-row shares — the same rule
    ``CLAUDE.md`` §1 states for aggregated margin, and for the same reason.
    """
    if whole == 0:
        return None
    return float(round(part / whole, 6))


@dataclass(frozen=True)
class CoverageSlice:
    """One population's coverage, by row count and by stock value."""

    rows: int
    row_share: Optional[float]
    stock_value: Decimal
    value_share: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {"rows": self.rows, "row_share": self.row_share,
                "stock_value_at_selling_price": str(self.stock_value),
                "value_share": self.value_share}


@dataclass
class MasterHealthReport:
    """Everything the report says, as data. Rendering lives in :mod:`.render`."""

    source_file: str
    source_digest: str
    total_rows: int
    profile_id: str
    profile_label: str
    profile_version: str
    unmapped_roles: tuple[str, ...]
    policy_version: str
    catalogue: Dict[str, Any] = field(default_factory=dict)
    coverage: Dict[str, Any] = field(default_factory=dict)
    value: Dict[str, Any] = field(default_factory=dict)
    manufacturers: Dict[str, Any] = field(default_factory=dict)
    blanks: Dict[str, Any] = field(default_factory=dict)
    duplicates: Dict[str, Any] = field(default_factory=dict)
    worklist: List[Dict[str, Any]] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": {"file": self.source_file, "sha256": self.source_digest,
                       "rows": self.total_rows},
            "profile": {"id": self.profile_id, "label": self.profile_label,
                        "version": self.profile_version,
                        "roles_with_no_column": list(self.unmapped_roles)},
            "policy": {"version": self.policy_version},
            "catalogue": self.catalogue,
            "coverage": self.coverage,
            "value": self.value,
            "manufacturers": self.manufacturers,
            "blank_field_census": self.blanks,
            "duplicate_candidates": self.duplicates,
            "remediation_worklist": self.worklist,
            "caveats": self.caveats,
        }


# ── the pieces ───────────────────────────────────────────────────────────────

def _identity_hits(rows: Sequence[MasterRow], lookup) -> Dict[int, str]:
    """Row number to catalogue ``record_id``, for the rows that link exactly.

    Linking is delegated to ``pie_service.lookup_record`` — pie-parser's own
    ``AuthoritativeIndex`` behind it — rather than matched here. A second
    matcher is the responsibility duplication ``CLAUDE.md`` §2 warns about, and
    a looser one would put another manufacturer's part number on a quote.
    """
    hits: Dict[int, str] = {}
    for row in rows:
        if not row.sku:
            continue
        record = lookup(row.sku)
        if record:
            hits[row.row_number] = str(record.get("record_id") or row.sku)
    return hits


def _slice(rows: Sequence[MasterRow], members: set[int],
           total_value: Decimal, total_rows: int) -> CoverageSlice:
    value = sum((r.stock_value or Decimal("0")
                 for r in rows if r.row_number in members), Decimal("0"))
    return CoverageSlice(
        rows=len(members),
        row_share=_share(len(members), total_rows),
        stock_value=value,
        value_share=_money_share(value, total_value),
    )


def _manufacturer_census(rows: Sequence[MasterRow], identity: Dict[int, str],
                         gated: set[int], decode: DecodeRun,
                         has_column: bool) -> Dict[str, Any]:
    """Who this master says makes its items, and how each maker fares.

    The per-maker identity and geometry counts are the *evidence* for whether a
    pack covers a maker. The comparison against the pack's own declared brands
    is an inference and is labelled one: a pack's claim signatures are matched
    against a maker's name by token containment, which is a heuristic, whereas
    "1,721 rows name this maker and none of them linked" is a measurement.
    """
    if not has_column:
        return {"measured": False,
                "reason": "this export has no manufacturer column, so who makes "
                          "these items is unmeasured — not blank."}

    counts: Counter = Counter()
    display: Dict[str, str] = {}
    per_maker: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"rows": 0, "identity_linked": 0, "geometry_gated": 0})
    blank = 0
    for row in rows:
        if not row.manufacturer:
            blank += 1
            continue
        key = row.manufacturer.strip().upper()
        counts[key] += 1
        display.setdefault(key, row.manufacturer.strip())
        bucket = per_maker[key]
        bucket["rows"] += 1
        bucket["identity_linked"] += 1 if row.row_number in identity else 0
        bucket["geometry_gated"] += 1 if row.row_number in gated else 0

    claimed = {b.upper() for b in decode.covered_brands}
    named: List[Dict[str, Any]] = []
    unreached: List[Dict[str, Any]] = []
    for key, n in counts.most_common():
        bucket = per_maker[key]
        tokens = set(_NON_ALNUM.sub(" ", key).split())
        looks_claimed = bool(tokens & claimed) or any(c in key for c in claimed)
        entry = {
            "manufacturer": display[key],
            "rows": n,
            "identity_linked": bucket["identity_linked"],
            "geometry_gated": bucket["geometry_gated"],
            "pack_claims_this_name": (
                None if not decode.available else looks_claimed),
        }
        named.append(entry)
        # A maker the loaded pack does not claim, which linked nothing and
        # decoded nothing. All three together are what turns 0% into a
        # *finding*: the pack was loaded, it was asked, and it does not cover
        # this maker. Any one of them alone would not say that.
        if (decode.available and not looks_claimed
                and bucket["identity_linked"] == 0 and bucket["geometry_gated"] == 0):
            unreached.append(entry)
    return {
        "measured": True,
        "distinct_manufacturers": len(counts),
        "rows_with_no_manufacturer": blank,
        "share_with_no_manufacturer": _share(blank, len(rows)),
        "pack_declared_brands": (list(decode.covered_brands) if decode.available
                                 else None),
        "pack_declared_brands_note": (
            "Read from the loaded pack's own claim signatures. "
            "'pack_claims_this_name' is an INFERENCE by name matching; the "
            "identity_linked and geometry_gated counts beside it are the "
            "measurement." if decode.available else
            "No pack was loaded, so what a pack covers is unknown here."),
        "by_manufacturer": named,
        "unreached_by_the_loaded_pack": [
            {"manufacturer": e["manufacturer"], "rows": e["rows"]} for e in unreached],
        "rows_unreached_by_the_loaded_pack": sum(e["rows"] for e in unreached),
    }


def _blank_census(rows: Sequence[MasterRow], profile: ColumnProfile) -> Dict[str, Any]:
    """How much of each mapped column is actually filled in.

    A role with no column reports ``measured: false`` rather than 0 blanks. The
    failure this prevents is the comfortable one: a source with no manufacturer
    column looking, in a table of counts, like a master where every item has a
    manufacturer.
    """
    getters = {
        "sku": lambda r: r.sku, "name": lambda r: r.name,
        "manufacturer": lambda r: r.manufacturer, "hsn": lambda r: r.hsn,
        "uom": lambda r: r.uom,
        "rate": lambda r: r.rate, "stock": lambda r: r.stock,
    }
    total = len(rows)
    out: Dict[str, Any] = {}
    for role in ROLES:
        if not profile.has(role):
            out[role] = {"measured": False,
                         "reason": "no column for this role in the profile"}
            continue
        get = getters[role]
        blank = sum(1 for r in rows if get(r) is None)
        entry: Dict[str, Any] = {
            "measured": True, "column": profile.header(role),
            "blank_rows": blank, "blank_share": _share(blank, total),
        }
        if role in ("rate", "stock"):
            # A cell that held something unreadable is neither filled nor
            # blank, and it is a different fix from an empty one.
            raw = "rate_raw" if role == "rate" else "stock_raw"
            unreadable = sum(1 for r in rows if getattr(r, raw) is not None)
            entry["unreadable_rows"] = unreadable
            entry["blank_rows"] = blank - unreadable
            entry["blank_share"] = _share(blank - unreadable, total)
            if role == "rate":
                entry["zero_rows"] = sum(1 for r in rows if r.rate == 0)
            else:
                entry["negative_rows"] = sum(
                    1 for r in rows if r.stock is not None and r.stock < 0)
        out[role] = entry
    return out


def _duplicate_candidates(rows: Sequence[MasterRow]) -> Dict[str, Any]:
    """Rows that may be the same item twice. Nominated, never merged.

    Two kinds, kept apart because they are different findings:

    * **repeated identifier** — two rows whose SKUs are equal under the exact
      identity rule. That is a defect in the master by definition; one item
      cannot have one identifier twice.
    * **look-alike** — two rows whose SKUs, or whose names, collapse to the
      same key once every separator is removed. A judgement call for a person,
      offered as a queue.

    Nothing here proposes which row survives. Auto-picking corrupts history,
    and the report has no basis for the choice.
    """
    by_exact: Dict[str, List[MasterRow]] = defaultdict(list)
    by_loose_sku: Dict[str, List[MasterRow]] = defaultdict(list)
    by_loose_name: Dict[str, List[MasterRow]] = defaultdict(list)
    for row in rows:
        key = exact_key(row.sku)
        if key:
            by_exact[key].append(row)
        lk = loose_key(row.sku)
        if lk:
            by_loose_sku[lk].append(row)
        ln = loose_key(row.name)
        if ln:
            by_loose_name[ln].append(row)

    def groups(index: Dict[str, List[MasterRow]], exclude_exact: bool) -> List[Dict[str, Any]]:
        out = []
        for key, members in sorted(index.items()):
            if len(members) < 2:
                continue
            if exclude_exact and len({exact_key(m.sku) for m in members}) < 2:
                continue   # already reported as a repeated identifier
            out.append({
                "key": key,
                "rows": [{"row": m.row_number, "sku": m.sku, "name": m.name}
                         for m in members],
            })
        return out

    repeated = groups(by_exact, exclude_exact=False)
    lookalike_sku = groups(by_loose_sku, exclude_exact=True)
    lookalike_name = groups(by_loose_name, exclude_exact=True)
    flagged = {m["row"] for g in repeated + lookalike_sku + lookalike_name
               for m in g["rows"]}
    return {
        "repeated_identifier_groups": len(repeated),
        "lookalike_sku_groups": len(lookalike_sku),
        "lookalike_name_groups": len(lookalike_name),
        "rows_in_any_group": len(flagged),
        "note": "Candidates for a person to review. Nothing is merged, and no "
                "surviving row is proposed — that choice has no undo.",
        "examples": {
            "repeated_identifier": repeated[:EXAMPLE_LIMIT],
            "lookalike_sku": lookalike_sku[:EXAMPLE_LIMIT],
            "lookalike_name": lookalike_name[:EXAMPLE_LIMIT],
        },
    }


def _worklist(counts: Dict[str, int], policy: RemediationPolicy) -> List[Dict[str, Any]]:
    """The remediation queue, ranked by rows recovered per hour.

    An action whose trigger count is unknown for this export (the column is not
    mapped, so nobody measured it) is omitted from the ranking and reported
    separately by the caller — ranking it at zero would put "nothing to do"
    and "nothing was looked at" in the same row.
    """
    items: List[Dict[str, Any]] = []
    for action in policy.actions:
        rows = counts.get(action.trigger)
        if rows is None or rows <= 0:
            continue
        rate = action.rows_per_hour_for(rows)
        if rate is None:
            continue
        items.append({
            "action": action.action_id,
            "label": action.label,
            "rows": rows,
            "estimated_hours": float(round(action.hours_for(rows), 2)),
            "rows_recovered_per_hour": float(round(rate, 2)),
            "recovers": action.recovers,
        })
    # Rank descending by rate; ties broken by the larger population, then by id,
    # so the order is total and a rerun cannot shuffle it.
    items.sort(key=lambda i: (-i["rows_recovered_per_hour"], -i["rows"], i["action"]))
    return items


# ── the whole thing ──────────────────────────────────────────────────────────

def build_report(rows: Sequence[MasterRow], profile: ColumnProfile,
                 decode: DecodeRun, policy: RemediationPolicy,
                 lookup, catalogue_available: bool,
                 source_file: str, source_digest: str) -> MasterHealthReport:
    """Assemble the report. ``lookup`` is ``pie_service.lookup_record``."""
    total = len(rows)
    caveats: List[str] = []

    # ── value denominator ────────────────────────────────────────────────────
    valued = [r for r in rows if r.stock_value is not None]
    total_value = sum((r.stock_value or Decimal("0") for r in valued), Decimal("0"))
    unvalued = total - len(valued)
    if not (profile.has("rate") and profile.has("stock")):
        caveats.append(
            "Value-weighted coverage is UNKNOWN: this export has no "
            + ("rate" if not profile.has("rate") else "stock")
            + " column, so no stock value could be computed.")
    elif unvalued:
        caveats.append(
            f"{unvalued} of {total} rows have no rate or no quantity, so they "
            f"carry no stock value. Value shares are over the "
            f"{len(valued)} valued rows; the missing rows are counted as "
            f"unvalued, never as worth zero.")

    # ── identity ─────────────────────────────────────────────────────────────
    if catalogue_available:
        identity = _identity_hits(rows, lookup)
    else:
        identity = {}
        caveats.append(
            "Identity coverage is UNKNOWN, not zero: no catalogue was loaded, "
            "so nobody asked the pack. 'The pack does not cover this item' and "
            "'nobody asked the pack' are different facts and only the first is "
            "evidence about this master.")

    # ── geometry, gated ──────────────────────────────────────────────────────
    gated = {n for n, o in decode.outcomes.items() if o.gated}
    published = {n for n, o in decode.outcomes.items() if o.fills(PUBLISHED_SLOTS)}
    routed = {n for n, o in decode.outcomes.items() if o.routed_family}
    if not decode.available:
        caveats.append("Geometry coverage is UNKNOWN, not zero: " + decode.unavailable_reason)

    identity_rows = set(identity)
    coverage: Dict[str, Any] = {
        "identity": (None if not catalogue_available
                     else _slice(rows, identity_rows, total_value, total).to_dict()),
        "geometry_gated": (None if not decode.available
                           else _slice(rows, gated, total_value, total).to_dict()),
        "union": (None if not (catalogue_available and decode.available)
                  else _slice(rows, identity_rows | gated, total_value, total).to_dict()),
        "overlap_rows": (None if not (catalogue_available and decode.available)
                         else len(identity_rows & gated)),
        "gate": {
            "slots_required": list(GATED_SLOTS),
            "why": "A family route is not a fact. Measured over an item master, "
                   "30.3% of rows route to a named product_family while 11.6% of "
                   "those routed rows name a manufacturer this pack does not "
                   "cover; restricted to a full ISO slot fill, definite misroutes "
                   "fall to 1 row in 2,057. ISO slot fill validates itself.",
            "rows_routed_to_a_family": (None if not decode.available else len(routed)),
            "rows_routed_but_not_gated": (None if not decode.available
                                          else len(routed - gated)),
        },
        "published_definition": {
            "slots_required": list(PUBLISHED_SLOTS),
            "note": "The 2026-08-09 census in docs/concepts/01-application-"
                    "engineering.md §1 called a row geometry-decodable on shape "
                    "and edge alone. Carried here for comparison with that "
                    "document only. This report's own figure is geometry_gated.",
            "geometry": (None if not decode.available
                         else _slice(rows, published, total_value, total).to_dict()),
            "union": (None if not (catalogue_available and decode.available)
                      else _slice(rows, identity_rows | published,
                                  total_value, total).to_dict()),
        },
    }

    value = {
        "basis": "SELLING price. Quantity on hand x selling rate. No cost or "
                 "margin value appears in this report, and the column profile "
                 "has no role a cost column could be mapped to.",
        "measured": bool(profile.has("rate") and profile.has("stock")),
        "total_stock_value_at_selling_price": str(total_value),
        "valued_rows": len(valued),
        "unvalued_rows": unvalued,
    }

    blanks = _blank_census(rows, profile)
    manufacturers = _manufacturer_census(
        rows, identity, gated, decode, profile.has("manufacturer"))
    unreached_rows = manufacturers.get("rows_unreached_by_the_loaded_pack", 0)
    if unreached_rows and total:
        makers = ", ".join(
            f"{e['manufacturer']} ({e['rows']:,} rows)"
            for e in manufacturers["unreached_by_the_loaded_pack"][:5])
        caveats.append(
            f"{unreached_rows:,} of {total:,} rows ({_pct_text(unreached_rows, total)}) "
            f"name a manufacturer the loaded pack "
            f"({', '.join(decode.covered_brands) or 'unnamed'}) does not cover, and "
            f"which linked and decoded nothing: {makers}. Their coverage is zero "
            f"because NO PACK COVERS THEM — the pack was loaded and it was asked. "
            f"That is a different fact from a coverage figure of zero because "
            f"nobody asked, and only this one is evidence about the master.")
    duplicates = _duplicate_candidates(rows)

    # ── worklist triggers ────────────────────────────────────────────────────
    #
    # A trigger absent from this mapping is an unmeasured one: its action drops
    # out of the ranking entirely rather than ranking at zero.
    def measured(role: str, key: str, default: int = 0) -> Optional[int]:
        entry = blanks.get(role, {})
        return entry.get(key, default) if entry.get("measured") else None

    counts: Dict[str, Optional[int]] = {
        "sku_blank": measured("sku", "blank_rows"),
        "name_blank": measured("name", "blank_rows"),
        "manufacturer_blank": measured("manufacturer", "blank_rows"),
        "hsn_missing": measured("hsn", "blank_rows"),
        "uom_missing": measured("uom", "blank_rows"),
        "duplicate_candidate_rows": duplicates["rows_in_any_group"],
    }
    rate_entry = blanks.get("rate", {})
    if rate_entry.get("measured"):
        # A rate of zero is as unquotable as no rate at all, and it is the same
        # fix, so the two are one trigger.
        counts["rate_missing"] = rate_entry["blank_rows"] + rate_entry.get("zero_rows", 0)
    else:
        counts["rate_missing"] = None
    unreadable = 0
    unreadable_measured = False
    for role in ("rate", "stock"):
        entry = blanks.get(role, {})
        if entry.get("measured"):
            unreadable_measured = True
            unreadable += entry.get("unreadable_rows", 0)
    counts["number_unreadable"] = unreadable if unreadable_measured else None

    unmeasured = sorted(k for k, v in counts.items() if v is None)
    worklist = _worklist({k: v for k, v in counts.items() if v is not None}, policy)
    if unmeasured:
        caveats.append(
            "Not ranked because this export has no column to measure them: "
            + ", ".join(unmeasured)
            + ". They are absent from the worklist, not finished.")

    return MasterHealthReport(
        source_file=source_file,
        source_digest=source_digest,
        total_rows=total,
        profile_id=profile.profile_id,
        profile_label=profile.label,
        profile_version=profile.version,
        unmapped_roles=profile.missing_roles,
        policy_version=policy.version,
        catalogue={
            "identity_index_loaded": catalogue_available,
            "pack_loaded": decode.available,
            "pack_id": decode.pack_id or None,
            "pack_version": decode.pack_version or None,
            "ruleset_checksum": decode.ruleset_checksum or None,
            "unavailable_reason": decode.unavailable_reason or None,
        },
        coverage=coverage,
        value=value,
        manufacturers=manufacturers,
        blanks=blanks,
        duplicates=duplicates,
        worklist=worklist,
        caveats=caveats,
    )
