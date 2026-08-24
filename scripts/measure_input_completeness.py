#!/usr/bin/env python3
"""Read-only. Is `row_confidence 0.00` the engine failing, or the engine abstaining?

``docs/concepts/01-application-engineering.md`` §3 records that running the
engine over the 15,028-name Zoho item master scores ``row_confidence`` **0.00**
on every row, carrying ``GRADE_MISSING`` and ``MANUFACTURER_UNKNOWN``. That
number has been read two ways — "the engine cannot decide on real data" and "the
engine correctly abstains on an input that lacks the evidence" — and the two have
opposite implications for whether this product works.

Separating them does not need the live master, and is *better* without it. What
makes the master a poorer input is precisely the fields it lacks: it has no grade
column at all. A master run confounds that with a second difference — the master
also contains item names the catalogue was never built to decode (screws,
another maker's screwdriver), so a drop could be missing evidence *or* a
different population, and one run cannot tell you which.

So this suppresses the variable instead of swapping the dataset: the SAME 6,717
corpus rows are run twice through the SAME pipeline, differing in exactly one
field — ``RawRecord.grade`` set to ``None``. Population, ordering, pack, engine
and every threshold are held identical by construction, so any difference is
caused by the absent column and by nothing else. The two runs are then compared
per row, by ``record_id``.

Nothing here changes a threshold, a confidence weight or a validator, and that
is the point rather than an omission — a scorer tuned to emit non-zero
confidence over ``GRADE_MISSING`` would be `CLAUDE.md` §1's "absence of evidence
read as a pass", and would destroy the measurement it appeared to improve.
``tests/decision_platform/test_confidence_config_unchanged.py`` pins that.

Reads the corpus and writes nothing but its report.

Run:  cd backend && python3 ../scripts/measure_input_completeness.py
      cd backend && python3 ../scripts/measure_input_completeness.py --json out.json
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings                          # noqa: E402

#: Slots whose value is read from the grade column, or decoded from something
#: that was. They are *expected* to disappear when the column does; counting
#: them as engine judgement would attribute the engine's own arithmetic to it.
#: ``coating``/``coating_process`` sit here because the grade registry supplies
#: most of them — the handful the description supplies survives, and the run
#: below reports the survivors rather than assuming either way.
GRADE_DERIVED = frozenset({
    "grade", "grade_system", "grade_segment", "grade_revision", "material_class",
    "toughness_index", "applications", "coating", "coating_process",
})

#: Row-level fields that record what the engine made of the *description*.
#: If suppressing the grade column changed the engine's reading rather than
#: merely removing evidence, it would show here.
READING_FIELDS = (
    "description_norm", "product_family", "product_subfamily",
    "grammar_id", "family_rule_id", "attributes_ext",
)


def _load_engine():
    root = str(settings.PIE_PARSER_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from engine import configio
    from engine.pack import load_pack
    from engine.pipeline import ColumnMapping, CsvAdapter, ParserPipeline, RunProfile
    return configio, load_pack, ColumnMapping, CsvAdapter, ParserPipeline, RunProfile


def _run(pipeline_cls, profile, pack, records: List[Any], configio) -> Tuple[Dict[str, dict], Any]:
    """One batch through the pipeline; returns (rows by record_id, run report).

    Products and quarantine are merged: a row the router could not place is
    still a row this experiment must compare, and dropping it would silently
    narrow the population on whichever side quarantined more.
    """
    fingerprint = configio.checksum_bytes(
        "|".join(f"{r.record_id}\x1f{r.description}\x1f{r.grade or ''}"
                 for r in records).encode("utf-8")
    )
    products, report, quarantine = pipeline_cls(pack, profile).run(
        records, input_fingerprint=fingerprint)
    return {r["record_id"]: r for r in list(products) + list(quarantine)}, report


def _dist(rows: Iterable[dict]) -> Dict[str, int]:
    c = collections.Counter(f"{r['row_confidence']:.4f}" for r in rows)
    return dict(sorted(c.items(), key=lambda kv: (-float(kv[0]), kv[0])))


def _mean(rows: List[dict]) -> float:
    return sum(r["row_confidence"] for r in rows) / len(rows) if rows else 0.0


def _nonnull(rows: Iterable[dict], slots: frozenset | None = None) -> int:
    return sum(1 for r in rows for k in r["field_meta"]
               if (slots is None or k in slots) and r.get(k) is not None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, help="also write the full report as JSON")
    args = ap.parse_args()

    if not (settings.PIE_PARSER_ROOT / "tools" / "run_parser.py").exists():
        print("error: pie-parser not found. Fetch it with:\n"
              "  ./scripts/setup_pie_parser.sh   (or set PIE_PARSER_ROOT)", file=sys.stderr)
        return 2

    configio, load_pack, ColumnMapping, CsvAdapter, ParserPipeline, RunProfile = _load_engine()
    pack = load_pack(settings.PIE_PACK)
    mapping = ColumnMapping(record_id="MM#", description="Material Description",
                            grade="Grade")
    full_records = list(CsvAdapter(settings.PIE_CORPUS, mapping).read())

    # The suppression. `payload` is emptied with it so the column cannot re-enter
    # through the opaque unmapped-columns bag — the engine never reads payload,
    # but a measurement that relies on that is weaker than one that does not.
    supp_records = [dataclasses.replace(r, grade=None, payload={}) for r in full_records]

    profile = RunProfile()
    full, full_report = _run(ParserPipeline, profile, pack, full_records, configio)
    supp, supp_report = _run(ParserPipeline, profile, pack, supp_records, configio)
    assert set(full) == set(supp), "paired runs must cover the same record_ids"
    n = len(full)

    out: Dict[str, Any] = {
        "corpus": settings.PIE_CORPUS.name,
        "rows": n,
        "pack": f"{pack.pack_id}@{pack.version}",
        "ruleset_checksum": pack.checksum,
    }

    # ── 1. the paired confidence distribution ────────────────────────────────
    fr, sr = list(full.values()), list(supp.values())
    out["confidence"] = {
        "full": {"mean": round(_mean(fr), 4), "distribution": _dist(fr),
                 "zero_rows": sum(1 for r in fr if r["row_confidence"] == 0.0)},
        "suppressed": {"mean": round(_mean(sr), 4), "distribution": _dist(sr),
                       "zero_rows": sum(1 for r in sr if r["row_confidence"] == 0.0)},
        "paired_delta": dict(sorted(collections.Counter(
            f"{full[k]['row_confidence'] - supp[k]['row_confidence']:.4f}"
            for k in full).items(), key=lambda kv: (-float(kv[0]), kv[0]))),
    }

    # ── 2. the flag census on each side ──────────────────────────────────────
    flags = sorted(set(full_report.by_flag) | set(supp_report.by_flag))
    out["flags"] = {f: {"full": full_report.by_flag.get(f, 0),
                        "suppressed": supp_report.by_flag.get(f, 0)} for f in flags}

    # ── 3. what set the score, on each side ──────────────────────────────────
    #
    # ``row_confidence`` is ``min(family_confidence, critical-slot confidences)``
    # and ``critical_slots`` is ``("grade",)``. So the grade column can only ever
    # do one of two things to a row: bind the minimum, or not. Which one it did
    # is the whole question, and it is readable straight off the emitted
    # ``field_meta`` without re-deriving anything.
    def _binding(rows: Mapping[str, dict]) -> Dict[str, int]:
        c = collections.Counter()
        for r in rows.values():
            g = r["field_meta"].get("grade", {}).get("confidence")
            c["grade" if g is not None and abs(g - r["row_confidence"]) < 1e-9
              else "family_route"] += 1
        return dict(c)
    out["binding_term"] = {"full": _binding(full), "suppressed": _binding(supp)}
    out["grade_provenance"] = {
        side: dict(collections.Counter(
            r["field_meta"].get("grade", {}).get("provenance") for r in rows.values()))
        for side, rows in (("full", full), ("suppressed", supp))
    }

    # ── 4. did the engine's reading of the description change? ───────────────
    #
    # This is the attribution. Every difference between the two runs is either
    # the suppressed column and what is decoded from it, or a change in what the
    # engine made of the text — and only the second would be the engine's
    # judgement moving.
    reading_changed = {k: sum(1 for rid in full if full[rid][k] != supp[rid][k])
                       for k in READING_FIELDS}
    all_slots: set = set()
    for r in fr:
        all_slots |= set(r["field_meta"])
    desc_slots = frozenset(all_slots - GRADE_DERIVED)
    value_diffs: collections.Counter = collections.Counter()
    meta_diffs: collections.Counter = collections.Counter()
    for rid, f in full.items():
        s = supp[rid]
        for k in (set(f["field_meta"]) | set(s["field_meta"])) - GRADE_DERIVED:
            if f.get(k) != s.get(k):
                value_diffs[k] += 1
            if f["field_meta"].get(k) != s["field_meta"].get(k):
                meta_diffs[k] += 1
    token_diffs: collections.Counter = collections.Counter()
    for rid, f in full.items():
        for t in set(f["unresolved_tokens"]) ^ set(supp[rid]["unresolved_tokens"]):
            token_diffs[t.split(":", 1)[0]] += 1
    # The residual, and its sign. Where the two runs disagree about a
    # description slot they disagree only about its *confidence*, so the mean
    # emitted per-slot confidence over those slots says which way the residual
    # points. A suppressed run that reads the description slightly *better*
    # cannot be the engine's judgement degrading.
    def _slot_conf_mean(rows: Iterable[dict]) -> float:
        vals = [m["confidence"] for r in rows for k, m in r["field_meta"].items()
                if k in desc_slots and m["confidence"] is not None]
        return sum(vals) / len(vals) if vals else 0.0
    out["reading"] = {
        "row_fields_changed": reading_changed,
        "description_slot_value_diffs": dict(value_diffs),
        "description_slot_confidence_diffs": dict(meta_diffs),
        "unresolved_token_diffs_by_prefix": dict(token_diffs),
        "mean_description_slot_confidence_full": round(_slot_conf_mean(fr), 6),
        "mean_description_slot_confidence_suppressed": round(_slot_conf_mean(sr), 6),
    }

    # ── 5. what survives, and what is actually lost ──────────────────────────
    #
    # Confidence is not the only casualty, and reporting it alone would flatter
    # the suppressed side: the score can be defended as an abstention while half
    # the emitted attributes quietly vanish. Both are counted.
    out["information"] = {
        "attribute_values_full": _nonnull(fr),
        "attribute_values_suppressed": _nonnull(sr),
        "description_derived_full": _nonnull(fr, desc_slots),
        "description_derived_suppressed": _nonnull(sr, desc_slots),
        "grade_derived_full": _nonnull(fr, GRADE_DERIVED),
        "grade_derived_suppressed": _nonnull(sr, GRADE_DERIVED),
        "manufacturer_named_full": sum(1 for r in fr if r["manufacturer"]),
        "manufacturer_named_suppressed": sum(1 for r in sr if r["manufacturer"]),
        "family_named_full": sum(1 for r in fr if r["product_family"]),
        "family_named_suppressed": sum(1 for r in sr if r["product_family"]),
        # §3's gate: full ISO slot fill is the read that validates itself.
        "iso_full_slot_fill_full": sum(
            1 for r in fr if r.get("iso_shape") and r.get("edge_length_mm")
            and r.get("corner_radius_mm") is not None),
        "iso_full_slot_fill_suppressed": sum(
            1 for r in sr if r.get("iso_shape") and r.get("edge_length_mm")
            and r.get("corner_radius_mm") is not None),
    }

    # ── 6. the natural control ───────────────────────────────────────────────
    #
    # Three corpus rows already carry no grade. They are master-shaped in the
    # full run, and they are the one place the suppression is not something this
    # script did.
    ctrl = [rid for rid, r in full.items()
            if r["field_meta"].get("grade", {}).get("provenance") == "ABSENT"]
    out["natural_control"] = {
        "record_ids": sorted(ctrl),
        "n": len(ctrl),
        "confidence_in_full_run": sorted({full[r]["row_confidence"] for r in ctrl}),
    }

    # ── 7. manufacturer resolution: is there any non-grade channel at all? ───
    out["claims"] = [
        {"claim_id": c.claim_id, "has_grade_pattern": c.grade_pattern is not None,
         "series_tokens": list(c.series_tokens)} for c in pack.claims
    ]

    _print(out)
    if args.json:
        args.json.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


def _print(out: Dict[str, Any]) -> None:
    n = out["rows"]
    print(f"corpus {out['corpus']}  rows {n}  pack {out['pack']}")
    print(f"ruleset checksum {out['ruleset_checksum']}")

    print("\n── 1. paired row_confidence ──────────────────────────────────────")
    for side in ("full", "suppressed"):
        c = out["confidence"][side]
        print(f"  {side:<11} mean {c['mean']:.4f}   zero rows {c['zero_rows']:>5}/{n}")
        for value, count in c["distribution"].items():
            print(f"        {value}  {count:>5}  ({count / n:6.2%})")
    print("  paired delta (full - suppressed), same row:")
    for value, count in out["confidence"]["paired_delta"].items():
        print(f"        {value}  {count:>5}  ({count / n:6.2%})")

    print("\n── 2. flag census ────────────────────────────────────────────────")
    print(f"  {'flag':<28}{'full':>8}{'suppressed':>13}")
    for flag, v in out["flags"].items():
        print(f"  {flag:<28}{v['full']:>8}{v['suppressed']:>13}")

    print("\n── 3. which term set row_confidence ──────────────────────────────")
    for side in ("full", "suppressed"):
        print(f"  {side:<11} {out['binding_term'][side]}")
        print(f"              grade provenance: {out['grade_provenance'][side]}")

    print("\n── 4. did the engine's reading of the description change? ────────")
    r = out["reading"]
    for k, v in r["row_fields_changed"].items():
        print(f"  rows differing in {k:<22} {v}")
    print(f"  description-derived slots differing in VALUE:      "
          f"{r['description_slot_value_diffs'] or 'none'}")
    print(f"  ... differing only in per-slot CONFIDENCE:         "
          f"{sum(r['description_slot_confidence_diffs'].values())} slot-instances")
    print(f"  unresolved-token differences, by prefix:           "
          f"{r['unresolved_token_diffs_by_prefix'] or 'none'}")
    print(f"  mean per-slot confidence on description slots:     "
          f"full {r['mean_description_slot_confidence_full']:.6f}  "
          f"suppressed {r['mean_description_slot_confidence_suppressed']:.6f}")

    print("\n── 5. what survives and what is lost ─────────────────────────────")
    i = out["information"]
    print(f"  {'measure':<34}{'full':>9}{'suppressed':>13}")
    for label, a, b in (
        ("non-null attribute values", "attribute_values_full", "attribute_values_suppressed"),
        ("  from the description", "description_derived_full", "description_derived_suppressed"),
        ("  from the grade column", "grade_derived_full", "grade_derived_suppressed"),
        ("rows with a named manufacturer", "manufacturer_named_full", "manufacturer_named_suppressed"),
        ("rows with a named family", "family_named_full", "family_named_suppressed"),
        ("rows with full ISO slot fill", "iso_full_slot_fill_full", "iso_full_slot_fill_suppressed"),
    ):
        print(f"  {label:<34}{i[a]:>9}{i[b]:>13}")

    print("\n── 6. natural control (grade already blank in the full input) ────")
    c = out["natural_control"]
    print(f"  {c['n']} row(s) {c['record_ids']}  "
          f"row_confidence in the FULL run: {c['confidence_in_full_run']}")

    print("\n── 7. manufacturer claim signatures in the pack ──────────────────")
    for c in out["claims"]:
        print(f"  {c['claim_id']:<14} grade_pattern={c['has_grade_pattern']}  "
              f"series_tokens={c['series_tokens'] or '()'}")


if __name__ == "__main__":
    raise SystemExit(main())
