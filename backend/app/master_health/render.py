"""The report as a person reads it.

Separate from :mod:`.analysis` because rendering and deciding are two reasons
to change: a column width is not a finding, and a finding is not a column
width. The JSON form is ``MasterHealthReport.to_dict()`` and carries everything;
this is the narrower, ordered view somebody actually acts on.

Every number that is a share prints as a percentage; every number that is money
prints with its basis attached, because a stock value with no basis beside it is
the exact ambiguity ``CLAUDE.md`` §1 is about.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .analysis import MasterHealthReport

RULE = "─" * 78


def _pct(value: Optional[float]) -> str:
    return "UNKNOWN" if value is None else f"{value * 100:.1f}%"


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _slice_line(label: str, sl: Optional[Dict[str, Any]], width: int = 22) -> str:
    if sl is None:
        return f"  {label:<{width}} UNKNOWN — see the caveats below"
    return (f"  {label:<{width}} {sl['rows']:>7,} rows  {_pct(sl['row_share']):>8}"
            f"   value {_pct(sl['value_share']):>8}")


def render_text(report: MasterHealthReport) -> str:
    out: List[str] = []
    add = out.append

    add(RULE)
    add("MASTER HEALTH REPORT")
    add(RULE)
    add(f"  source            {report.source_file}")
    add(f"  sha256            {report.source_digest}")
    add(f"  rows              {report.total_rows:,}")
    add(f"  column profile    {report.profile_label} [{report.profile_id} "
        f"{report.profile_version}]")
    if report.unmapped_roles:
        add(f"  no column for     {', '.join(report.unmapped_roles)}  (unmeasured, not clean)")
    add(f"  remediation policy {report.policy_version}")
    cat = report.catalogue
    add(f"  catalogue         identity index "
        f"{'loaded' if cat.get('identity_index_loaded') else 'NOT LOADED'}; pack "
        f"{cat.get('pack_id') or 'NOT LOADED'}"
        + (f"@{cat.get('pack_version')}" if cat.get("pack_version") else ""))

    # ── coverage ─────────────────────────────────────────────────────────────
    add("")
    add("COVERAGE — what the product intelligence can reach")
    cov = report.coverage
    add(_slice_line("identity-linked", cov.get("identity")))
    add(_slice_line("geometry (gated)", cov.get("geometry_gated")))
    add(_slice_line("union", cov.get("union")))
    if cov.get("overlap_rows") is not None:
        add(f"  {'both':<22} {cov['overlap_rows']:>7,} rows")
    gate = cov.get("gate", {})
    add(f"  gate: all of {', '.join(gate.get('slots_required', []))} must decode.")
    if gate.get("rows_routed_to_a_family") is not None:
        add(f"        {gate['rows_routed_to_a_family']:,} rows routed to a product "
            f"family; {gate['rows_routed_but_not_gated']:,} of those did NOT fill "
            f"the ISO slots and are excluded.")
    add(f"        {gate.get('why', '')}")

    pub = cov.get("published_definition", {})
    if pub.get("geometry") is not None:
        add("")
        add("  For comparison with the 2026-08-09 published census, which called a")
        add("  row decodable on shape + edge alone (this report does not):")
        add(_slice_line("geometry (published)", pub.get("geometry")))
        add(_slice_line("union (published)", pub.get("union")))

    # ── value ────────────────────────────────────────────────────────────────
    add("")
    add("STOCK VALUE — at SELLING price")
    val = report.value
    if val.get("measured"):
        add(f"  total             {_money(val['total_stock_value_at_selling_price'])}"
            f"  (selling price x quantity on hand)")
        add(f"  valued rows       {val['valued_rows']:,}   unvalued {val['unvalued_rows']:,}")
    else:
        add("  UNKNOWN — this export has no rate and/or no stock column.")
    add(f"  {val.get('basis', '')}")

    # ── manufacturers ────────────────────────────────────────────────────────
    add("")
    add("MANUFACTURER CENSUS")
    man = report.manufacturers
    if not man.get("measured"):
        add(f"  UNKNOWN — {man.get('reason')}")
    else:
        add(f"  distinct makers   {man['distinct_manufacturers']:,}")
        add(f"  no maker recorded {man['rows_with_no_manufacturer']:,} rows "
            f"({_pct(man['share_with_no_manufacturer'])})")
        brands = man.get("pack_declared_brands")
        add(f"  pack claims       {', '.join(brands) if brands else 'UNKNOWN — no pack loaded'}")
        add(f"  {man.get('pack_declared_brands_note', '')}")
        add("")
        add(f"  {'maker':<34}{'rows':>8}{'identity':>10}{'geometry':>10}  pack claims name?")
        for entry in man["by_manufacturer"][:15]:
            claims = entry["pack_claims_this_name"]
            claims_text = "UNKNOWN" if claims is None else ("yes" if claims else "no")
            add(f"  {entry['manufacturer'][:33]:<34}{entry['rows']:>8,}"
                f"{entry['identity_linked']:>10,}{entry['geometry_gated']:>10,}  {claims_text}")
        if len(man["by_manufacturer"]) > 15:
            add(f"  … and {len(man['by_manufacturer']) - 15:,} more makers")
        unreached = man.get("unreached_by_the_loaded_pack") or []
        if unreached:
            add("")
            add(f"  NO LOADED PACK COVERS these makers — {man['rows_unreached_by_the_loaded_pack']:,} "
                f"rows. The pack was loaded and asked; it does not")
            add("  cover them. Their coverage is zero as EVIDENCE, not as silence:")
            for entry in unreached[:10]:
                add(f"    · {entry['manufacturer']}  ({entry['rows']:,} rows)")

    # ── blanks ───────────────────────────────────────────────────────────────
    add("")
    add("BLANK-FIELD CENSUS")
    add(f"  {'field':<16}{'column':<26}{'blank':>9}{'share':>9}   other")
    for role, entry in report.blanks.items():
        if not entry.get("measured"):
            add(f"  {role:<16}{'—':<26}{'UNKNOWN':>9}{'':>9}   {entry.get('reason', '')}")
            continue
        extra = []
        if "unreadable_rows" in entry and entry["unreadable_rows"]:
            extra.append(f"{entry['unreadable_rows']:,} unreadable")
        if entry.get("zero_rows"):
            extra.append(f"{entry['zero_rows']:,} zero")
        if entry.get("negative_rows"):
            extra.append(f"{entry['negative_rows']:,} negative")
        add(f"  {role:<16}{str(entry['column'])[:25]:<26}{entry['blank_rows']:>9,}"
            f"{_pct(entry['blank_share']):>9}   {', '.join(extra)}")

    # ── duplicates ───────────────────────────────────────────────────────────
    add("")
    add("DUPLICATE CANDIDATES")
    dup = report.duplicates
    add(f"  repeated identifier groups  {dup['repeated_identifier_groups']:,}")
    add(f"  look-alike SKU groups       {dup['lookalike_sku_groups']:,}")
    add(f"  look-alike name groups      {dup['lookalike_name_groups']:,}")
    add(f"  rows in any group           {dup['rows_in_any_group']:,}")
    add(f"  {dup['note']}")
    for kind, groups in dup.get("examples", {}).items():
        if not groups:
            continue
        add(f"  {kind}:")
        for group in groups[:5]:
            members = "; ".join(f"row {m['row']} {m['sku'] or '(no sku)'}"
                                for m in group["rows"][:4])
            add(f"    {group['key'][:28]:<30} {members}")

    # ── worklist ─────────────────────────────────────────────────────────────
    add("")
    add("REMEDIATION WORKLIST — ranked by rows recovered per hour")
    if not report.worklist:
        add("  Nothing to rank. Either the master is clean on every measured "
            "field, or nothing was measured — the caveats below say which.")
    else:
        add(f"  {'#':<3}{'action':<50}{'rows':>9}{'hours':>9}{'rows/hr':>10}")
        for i, item in enumerate(report.worklist, start=1):
            add(f"  {i:<3}{item['label'][:49]:<50}{item['rows']:>9,}"
                f"{item['estimated_hours']:>9.1f}{item['rows_recovered_per_hour']:>10.1f}")
        add("")
        for i, item in enumerate(report.worklist, start=1):
            add(f"  {i}. {item['label']} — {item['recovers']}")

    # ── caveats ──────────────────────────────────────────────────────────────
    add("")
    add("WHAT THIS REPORT DOES NOT SAY")
    if not report.caveats:
        add("  Every field this profile maps was measured against a loaded "
            "catalogue and pack.")
    for caveat in report.caveats:
        add(f"  · {caveat}")
    add(RULE)
    return "\n".join(out)
