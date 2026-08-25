"""The Master Health Report: the profile mechanism, the gate, and the refusals.

The tests that matter most here are not the counting ones. They are:

* :func:`test_a_second_profile_reads_a_different_export_with_no_code_change` —
  the agnosticism test. Two files with entirely different headers, two
  profiles, one report body. If this needs a code edit, the profile mechanism
  does not exist.
* the UNKNOWN-not-zero pair, which pin ``CLAUDE.md`` §1's "absence of evidence
  is not a pass" for the two ways this report can be blind.
* :func:`test_the_gate_excludes_a_row_that_routed_but_did_not_fill_the_slots`,
  which is the 11.6% contamination finding turned into an assertion.
"""
from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.master_health import analysis, policy as policy_module
from app.master_health.cli import main
from app.master_health.geometry import GATED_SLOTS, DecodeOutcome, DecodeRun, decode_names
from app.master_health.profile import (
    ROLES,
    ProfileError,
    available_profiles,
    load_profile,
    profile_from_columns,
)
from app.master_health.source import MasterRow, SourceError, read_export

# ── fixtures ─────────────────────────────────────────────────────────────────
#
# One population, written twice under two vocabularies. Everything below that
# compares the two runs depends on these holding the same items in the same
# order and differing in nothing but the header row.

ITEMS = [
    # sku,      name,                      manufacturer,  rate,     stock, hsn,        uom
    ("2001174", "CNMG 120408-49 - TN2000", "KENNAMETAL",  "412.00", "24",  "82090010", "pcs"),
    ("KM-1234", "DNMG 150608 MP KCP25",    "KENNAMETAL",  "530.50", "10",  "82090010", "pcs"),
    ("KM1234",  "DNMG 150608 MP KCP25",    "Kennametal",  "530.50", "2",   "",         "pcs"),
    ("YG-9001", "EM-4FL-10MM CARBIDE ENDMILL", "YG1",     "990.00", "7",   "",         "pcs"),
    ("SCR-01",  "M3X11 SCREW",             "",            "12.00",  "400", "",         "pcs"),
    ("",        "WNMG 080408 FN KCK15",    "",            "",       "5",   "82090010", ""),
]

ZOHO_HEADERS = ["SKU", "Item Name", "Manufacturer", "Rate",
                "Stock On Hand", "HSN/SAC", "Usage unit"]
NETSUITE_HEADERS = ["Name", "Display Name", "Manufacturer", "Base Price",
                    "Quantity On Hand", "Tax Schedule", "Units Type"]


def _write_csv(path: Path, headers: list[str], rows=ITEMS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


@pytest.fixture
def zoho_export(tmp_path: Path) -> Path:
    return _write_csv(tmp_path / "zoho.csv", ZOHO_HEADERS)


@pytest.fixture
def netsuite_export(tmp_path: Path) -> Path:
    return _write_csv(tmp_path / "netsuite.csv", NETSUITE_HEADERS)


def _blind_run(rows, profile):
    """A report built with no catalogue and no pack — the both-blind case."""
    return analysis.build_report(
        rows=rows, profile=profile,
        decode=DecodeRun({}, unavailable_reason="no engine in this test"),
        policy=policy_module.load_policy(),
        lookup=lambda _sku: None, catalogue_available=False,
        source_file="fixture.csv", source_digest="0" * 64)


# ── the profile mechanism ────────────────────────────────────────────────────

def test_every_shipped_profile_loads_and_fills_the_required_roles():
    names = available_profiles()
    assert {"zoho", "netsuite", "prophet21"} <= set(names), names
    for name in names:
        profile = load_profile(name)
        assert profile.has("sku") and profile.has("name")
        assert set(profile.columns) <= set(ROLES)
        assert profile.version.startswith("cp_")


def test_two_profiles_that_map_differently_have_different_versions():
    assert load_profile("zoho").version != load_profile("netsuite").version


def test_a_profile_cannot_invent_a_role(tmp_path: Path):
    """A role is a report feature. A profile that adds one is asking for code."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("columns:\n  sku: A\n  name: B\n  purchase_cost: C\n")
    with pytest.raises(ProfileError, match="unknown column role"):
        load_profile(str(bad))


def test_there_is_no_role_a_cost_column_could_be_mapped_to():
    """CLAUDE.md §1, enforced by the shape rather than by a filter downstream.

    The report cannot print a cost because it has nowhere to read one into.
    """
    assert not any("cost" in r or "margin" in r or "purchase" in r for r in ROLES)
    for name in available_profiles():
        headers = " ".join(load_profile(name).columns.values()).lower()
        assert "purchase rate" not in headers
        assert "average cost" not in headers


def test_a_mispointed_profile_is_refused_rather_than_read_as_all_blank(tmp_path: Path):
    """The silent failure this prevents is a census of 100% blank with no defect."""
    export = _write_csv(tmp_path / "x.csv", ZOHO_HEADERS)
    wrong = load_profile("netsuite")
    with pytest.raises(ProfileError, match="does not have"):
        read_export(export, wrong)


def test_col_flags_override_one_role_and_can_remove_one():
    profile = load_profile("zoho").with_overrides({"rate": "Selling Price", "hsn": ""})
    assert profile.header("rate") == "Selling Price"
    assert not profile.has("hsn")
    assert "hsn" in profile.missing_roles
    assert profile.version != load_profile("zoho").version


def test_a_profile_can_be_built_from_flags_alone():
    profile = profile_from_columns({"sku": "Code", "name": "Description"})
    assert profile.has("sku") and not profile.has("rate")


# ── THE AGNOSTICISM TEST ─────────────────────────────────────────────────────

def test_a_second_profile_reads_a_different_export_with_no_code_change(
        zoho_export: Path, netsuite_export: Path):
    """The same items under two vocabularies produce the same report.

    Nothing in this test names a header. Both files hold identical rows; only
    the header row and the profile differ. Every finding — coverage, census,
    blanks, duplicates, worklist — must come out identical, because the profile
    is the only thing that knows what a column is called.
    """
    zoho_rows, _ = read_export(zoho_export, load_profile("zoho"))
    ns_rows, _ = read_export(netsuite_export, load_profile("netsuite"))
    assert [(r.sku, r.name, r.rate, r.stock) for r in zoho_rows] \
        == [(r.sku, r.name, r.rate, r.stock) for r in ns_rows]

    left = _blind_run(zoho_rows, load_profile("zoho")).to_dict()
    right = _blind_run(ns_rows, load_profile("netsuite")).to_dict()
    for body in (left, right):
        # These four are *supposed* to differ: they identify the file and the
        # mapping that read it. Everything else is a finding about the items.
        body.pop("source")
        body.pop("profile")
        for entry in body["blank_field_census"].values():
            entry.pop("column", None)
    assert left == right


def test_an_unknown_erp_needs_a_yaml_file_and_no_code(tmp_path: Path):
    """Adding an ERP is adding a profile. This is that claim, executed."""
    headers = ["Artikelnummer", "Bezeichnung", "Hersteller", "VK-Preis",
               "Bestand", "Zolltarif", "Einheit"]
    export = _write_csv(tmp_path / "erp7.csv", headers)
    profile_yaml = tmp_path / "erp7.yaml"
    profile_yaml.write_text(
        "label: A system nobody here has seen\n"
        "columns:\n"
        "  sku: Artikelnummer\n  name: Bezeichnung\n  manufacturer: Hersteller\n"
        "  rate: VK-Preis\n  stock: Bestand\n  hsn: Zolltarif\n  uom: Einheit\n",
        encoding="utf-8")
    rows, _ = read_export(export, load_profile(str(profile_yaml)))
    report = _blind_run(rows, load_profile(str(profile_yaml)))
    assert report.total_rows == len(ITEMS)
    assert report.value["total_stock_value_at_selling_price"] == str(
        Decimal("412.00") * 24 + Decimal("530.50") * 10 + Decimal("530.50") * 2
        + Decimal("990.00") * 7 + Decimal("12.00") * 400)


# ── reading an export ────────────────────────────────────────────────────────

def test_a_row_with_no_sku_is_kept_because_it_is_the_finding(zoho_export: Path):
    """pie-parser's adapters drop it as a trailing blank. Here it is the point."""
    rows, _ = read_export(zoho_export, load_profile("zoho"))
    assert len(rows) == len(ITEMS)
    assert sum(1 for r in rows if r.sku is None) == 1


def test_numbers_survive_commas_symbols_and_accounting_negatives(tmp_path: Path):
    export = _write_csv(tmp_path / "n.csv", ZOHO_HEADERS, rows=[
        ("A", "N", "M", "1,234.50", "(3)", "H", "pcs"),
        ("B", "N", "M", "₹99", "abc", "H", "pcs"),
    ])
    rows, _ = read_export(export, load_profile("zoho"))
    assert rows[0].rate == Decimal("1234.50") and rows[0].stock == Decimal("-3")
    assert rows[1].rate == Decimal("99")
    assert rows[1].stock is None and rows[1].stock_raw == "abc"


def test_an_unreadable_number_is_a_third_state_not_a_blank(tmp_path: Path):
    export = _write_csv(tmp_path / "n.csv", ZOHO_HEADERS, rows=[
        ("A", "N", "M", "10", "abc", "H", "pcs"),
        ("B", "N", "M", "10", "", "H", "pcs"),
    ])
    rows, _ = read_export(export, load_profile("zoho"))
    census = _blind_run(rows, load_profile("zoho")).blanks["stock"]
    assert census["unreadable_rows"] == 1
    assert census["blank_rows"] == 1


def test_an_unsupported_format_is_refused_by_name(tmp_path: Path):
    bad = tmp_path / "items.txt"
    bad.write_text("nope")
    with pytest.raises(SourceError, match="unsupported export format"):
        read_export(bad, load_profile("zoho"))


def test_an_xlsx_export_reads_the_same_as_a_csv_one(tmp_path: Path, zoho_export: Path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(ZOHO_HEADERS)
    for item in ITEMS:
        sheet.append(list(item))
    path = tmp_path / "items.xlsx"
    book.save(path)
    from_xlsx, _ = read_export(path, load_profile("zoho"))
    from_csv, _ = read_export(zoho_export, load_profile("zoho"))
    assert [(r.sku, r.name, r.rate, r.stock) for r in from_xlsx] \
        == [(r.sku, r.name, r.rate, r.stock) for r in from_csv]


# ── absence of evidence is not a pass ────────────────────────────────────────

def test_no_catalogue_makes_identity_unknown_rather_than_zero(zoho_export: Path):
    rows, _ = read_export(zoho_export, load_profile("zoho"))
    report = _blind_run(rows, load_profile("zoho"))
    assert report.coverage["identity"] is None
    assert report.coverage["union"] is None
    assert any("nobody asked the pack" in c for c in report.caveats)


def test_no_pack_makes_geometry_unknown_rather_than_zero(zoho_export: Path):
    rows, _ = read_export(zoho_export, load_profile("zoho"))
    report = _blind_run(rows, load_profile("zoho"))
    assert report.coverage["geometry_gated"] is None
    assert any("UNKNOWN, not zero" in c for c in report.caveats)


def test_an_unmapped_column_is_unmeasured_rather_than_clean(tmp_path: Path):
    headers = ["SKU", "Item Name"]
    export = _write_csv(tmp_path / "thin.csv", headers,
                        rows=[(i[0], i[1]) for i in ITEMS])
    profile = profile_from_columns({"sku": "SKU", "name": "Item Name"})
    rows, _ = read_export(export, profile)
    report = _blind_run(rows, profile)
    assert report.blanks["hsn"]["measured"] is False
    assert report.manufacturers["measured"] is False
    assert report.value["measured"] is False
    # And the actions that would have needed those columns are absent from the
    # ranking, not ranked at zero.
    ranked = {item["action"] for item in report.worklist}
    assert "ADD_HSN" not in ranked and "RECORD_MANUFACTURER" not in ranked
    assert any("not ranked because" in c.lower() for c in report.caveats)


def test_a_row_with_no_rate_is_unvalued_rather_than_worth_zero():
    rows = [MasterRow(2, "A", "N", "M", Decimal("10"), Decimal("2"), "H", "pcs"),
            MasterRow(3, "B", "N", "M", None, Decimal("99"), "H", "pcs")]
    profile = load_profile("zoho")
    report = _blind_run(rows, profile)
    assert report.value["total_stock_value_at_selling_price"] == "20"
    assert report.value["unvalued_rows"] == 1
    assert any("never as worth zero" in c for c in report.caveats)


# ── the gate ─────────────────────────────────────────────────────────────────

def test_the_gate_requires_all_three_iso_slots():
    assert GATED_SLOTS == ("iso_shape", "edge_length_mm", "corner_radius_mm")
    partial = DecodeOutcome(2, "turning_insert", {"iso_shape": "C", "edge_length_mm": 12})
    full = DecodeOutcome(3, "turning_insert",
                         {"iso_shape": "C", "edge_length_mm": 12, "corner_radius_mm": 0.8})
    assert not partial.gated and full.gated


def test_the_gate_excludes_a_row_that_routed_but_did_not_fill_the_slots():
    """A family route is not a fact — the 11.6% contamination finding, asserted."""
    rows = [MasterRow(2, "A", "M3X11 SCREW", "EMUGE", Decimal("1"), Decimal("1"), "H", "pcs"),
            MasterRow(3, "B", "CNMG 120408", "KMT", Decimal("1"), Decimal("1"), "H", "pcs")]
    decode = DecodeRun(
        outcomes={
            2: DecodeOutcome(2, "turning_insert", {}),               # routed, no slots
            3: DecodeOutcome(3, "turning_insert",
                             {"iso_shape": "C", "edge_length_mm": 12,
                              "corner_radius_mm": 0.8}),
        },
        pack_id="p", pack_version="1", covered_brands=("Kennametal",))
    report = analysis.build_report(
        rows=rows, profile=load_profile("zoho"), decode=decode,
        policy=policy_module.load_policy(), lookup=lambda _s: None,
        catalogue_available=True, source_file="f", source_digest="d")
    assert report.coverage["geometry_gated"]["rows"] == 1
    assert report.coverage["gate"]["rows_routed_to_a_family"] == 2
    assert report.coverage["gate"]["rows_routed_but_not_gated"] == 1


def test_the_published_two_slot_definition_is_carried_but_is_not_the_gate():
    rows = [MasterRow(2, "A", "N", "M", Decimal("1"), Decimal("1"), "H", "pcs")]
    decode = DecodeRun(outcomes={2: DecodeOutcome(
        2, "turning_insert", {"iso_shape": "C", "edge_length_mm": 12})},
        pack_id="p", pack_version="1")
    report = analysis.build_report(
        rows=rows, profile=load_profile("zoho"), decode=decode,
        policy=policy_module.load_policy(), lookup=lambda _s: None,
        catalogue_available=True, source_file="f", source_digest="d")
    assert report.coverage["geometry_gated"]["rows"] == 0
    assert report.coverage["published_definition"]["geometry"]["rows"] == 1


# ── manufacturer census ──────────────────────────────────────────────────────

def test_a_maker_no_pack_covers_is_reported_with_the_reason_not_a_bare_zero():
    rows = [MasterRow(n, f"YG-{n}", "EM-4FL-10MM ENDMILL", "YG1",
                      Decimal("100"), Decimal("1"), "H", "pcs") for n in (2, 3, 4)]
    decode = DecodeRun(outcomes={n: DecodeOutcome(n, None, {}) for n in (2, 3, 4)},
                       pack_id="p", pack_version="1",
                       covered_brands=("Kennametal", "WIDIA"))
    report = analysis.build_report(
        rows=rows, profile=load_profile("zoho"), decode=decode,
        policy=policy_module.load_policy(), lookup=lambda _s: None,
        catalogue_available=True, source_file="f", source_digest="d")
    entry = report.manufacturers["by_manufacturer"][0]
    assert entry["manufacturer"] == "YG1"
    assert entry["rows"] == 3 and entry["identity_linked"] == 0
    assert entry["pack_claims_this_name"] is False
    # The pack's own claims are named, so "no pack covers this maker" is
    # readable off the report rather than assumed by the reader.
    assert report.manufacturers["pack_declared_brands"] == ["Kennametal", "WIDIA"]
    # …and stated outright, because a bare zero is not the finding.
    assert report.manufacturers["rows_unreached_by_the_loaded_pack"] == 3
    assert [e["manufacturer"] for e in
            report.manufacturers["unreached_by_the_loaded_pack"]] == ["YG1"]
    assert any("NO PACK COVERS THEM" in c for c in report.caveats)
    assert any("nobody asked" in c for c in report.caveats)


def test_a_zero_from_no_pack_loaded_is_never_reported_as_no_pack_covers_it():
    """The two zeroes must not be confusable, so the blind run claims neither."""
    rows = [MasterRow(2, "A", "N", "YG1", Decimal("1"), Decimal("1"), "H", "pcs")]
    report = _blind_run(rows, load_profile("zoho"))
    assert report.manufacturers["rows_unreached_by_the_loaded_pack"] == 0
    assert not any("NO PACK COVERS" in c for c in report.caveats)
    assert any("nobody asked the pack" in c for c in report.caveats)


def test_without_a_pack_the_census_says_unknown_rather_than_not_covered():
    rows = [MasterRow(2, "A", "N", "YG1", Decimal("1"), Decimal("1"), "H", "pcs")]
    report = _blind_run(rows, load_profile("zoho"))
    entry = report.manufacturers["by_manufacturer"][0]
    assert entry["pack_claims_this_name"] is None
    assert report.manufacturers["pack_declared_brands"] is None


# ── duplicates ───────────────────────────────────────────────────────────────

def test_a_repeated_identifier_and_a_lookalike_are_different_findings(zoho_export: Path):
    rows, _ = read_export(zoho_export, load_profile("zoho"))
    dup = _blind_run(rows, load_profile("zoho")).duplicates
    assert dup["lookalike_sku_groups"] == 1        # KM-1234 vs KM1234
    assert dup["repeated_identifier_groups"] == 0
    assert "Nothing is merged" in dup["note"]


def test_the_loose_key_never_decides_identity():
    """It nominates. The exact key is the one identity is allowed to use."""
    assert analysis.loose_key("KM-1234") == analysis.loose_key("KM1234")
    assert analysis.exact_key("KM-1234") != analysis.exact_key("KM1234")


# ── worklist ─────────────────────────────────────────────────────────────────

def test_the_worklist_is_ranked_by_rows_recovered_per_hour(zoho_export: Path):
    rows, _ = read_export(zoho_export, load_profile("zoho"))
    worklist = _blind_run(rows, load_profile("zoho")).worklist
    assert worklist, "the fixture has gaps, so something must be ranked"
    rates = [item["rows_recovered_per_hour"] for item in worklist]
    assert rates == sorted(rates, reverse=True)
    for item in worklist:
        assert item["rows"] > 0
        assert item["estimated_hours"] > 0


def test_setup_cost_is_why_a_bigger_batch_outranks_a_smaller_one():
    action = policy_module.load_policy().actions[0]
    assert action.rows_per_hour_for(1000) > action.rows_per_hour_for(10)
    assert action.rows_per_hour_for(0) is None


def test_the_policy_stamp_moves_when_a_figure_moves(tmp_path: Path):
    original = policy_module.load_policy()
    edited = tmp_path / "policy.yaml"
    edited.write_text(policy_module.DEFAULT_PATH.read_text(encoding="utf-8")
                      .replace('rows_per_hour: "150"', 'rows_per_hour: "300"'),
                      encoding="utf-8")
    assert policy_module.load_policy(edited).version != original.version
    assert original.version.startswith("mh_")


def test_a_float_in_the_policy_is_refused_rather_than_rounded(tmp_path: Path):
    bad = tmp_path / "p.yaml"
    bad.write_text('actions:\n  - id: A\n    trigger: t\n    setup_hours: 1\n'
                   '    rows_per_hour: 12.5\n')
    with pytest.raises(policy_module.PolicyError, match="is a float"):
        policy_module.load_policy(bad)


# ── the CLI ──────────────────────────────────────────────────────────────────

def test_the_cli_refuses_to_guess_a_profile(zoho_export: Path, capsys):
    assert main([str(zoho_export)]) == 2
    assert "no column profile" in capsys.readouterr().err


def test_the_cli_writes_a_report_and_its_json(tmp_path: Path, zoho_export: Path, capsys):
    out, blob = tmp_path / "r.txt", tmp_path / "r.json"
    assert main([str(zoho_export), "--profile", "zoho",
                 "--out", str(out), "--json", str(blob)]) == 0
    capsys.readouterr()
    text = out.read_text(encoding="utf-8")
    assert "MASTER HEALTH REPORT" in text
    assert "SELLING price" in text
    body = json.loads(blob.read_text(encoding="utf-8"))
    assert body["source"]["rows"] == len(ITEMS)
    assert body["profile"]["id"] == "zoho"


def test_no_report_ever_prints_a_cost_or_a_margin(tmp_path: Path, zoho_export: Path, capsys):
    out = tmp_path / "r.txt"
    assert main([str(zoho_export), "--profile", "zoho", "--out", str(out)]) == 0
    capsys.readouterr()
    body = out.read_text(encoding="utf-8").lower()
    # "selling price" and the standing note about cost are the only permitted
    # appearances, so strip them before looking.
    for permitted in ("no cost or margin value appears in this report",
                      "the column profile has no role a cost column could be "
                      "mapped to.", "selling price"):
        body = body.replace(permitted, "")
    assert "margin" not in body
    assert "purchase rate" not in body


def test_list_profiles_names_every_shipped_one(capsys):
    assert main(["--list-profiles"]) == 0
    printed = capsys.readouterr().out
    for name in available_profiles():
        assert name in printed


# ── the seam with pie-parser ─────────────────────────────────────────────────

@pytest.mark.requires_pie
def test_the_exact_key_still_agrees_with_pie_parsers_own_rule():
    """A restated rule that has drifted is worse than a duplicated one."""
    from identity.store import normalize_identifier
    for value in ("  km-1234 ", "CNMG120408", "a.b/c", "", "5290155"):
        assert analysis.exact_key(value) == (normalize_identifier(value) or None)


@pytest.mark.requires_pie
def test_the_real_engine_gates_an_insert_and_refuses_a_screw():
    rows = [
        MasterRow(2, "A", "CNMG 120408-49 - TN2000", "KMT", Decimal("1"), Decimal("1"), "H", "pcs"),
        MasterRow(3, "B", "M3X11 SCREW", "", Decimal("1"), Decimal("1"), "H", "pcs"),
    ]
    run = decode_names(rows)
    assert run.available, run.unavailable_reason
    assert run.covered_brands, "the pack must declare the brands it claims"
    assert run.outcomes[2].gated is True
    assert run.outcomes[3].gated is False


@pytest.mark.requires_pie
def test_a_real_catalogue_record_links_by_identity_and_an_invented_one_does_not(
        tmp_path: Path):
    from app.catalog import ensure_catalog
    from app.pie_service import pie_service

    with Path(ensure_catalog()).open(encoding="utf-8") as fh:
        record_id = json.loads(fh.readline())["record_id"]
    export = _write_csv(tmp_path / "z.csv", ZOHO_HEADERS, rows=[
        (record_id, "CNMG 120408", "KENNAMETAL", "100", "1", "82090010", "pcs"),
        ("NOT-A-REAL-MM-NUMBER", "CNMG 120408", "KENNAMETAL", "100", "1", "82090010", "pcs"),
    ])
    rows, _ = read_export(export, load_profile("zoho"))
    report = analysis.build_report(
        rows=rows, profile=load_profile("zoho"), decode=decode_names(rows),
        policy=policy_module.load_policy(), lookup=pie_service.lookup_record,
        catalogue_available=pie_service.catalog_available,
        source_file="f", source_digest="d")
    assert report.coverage["identity"]["rows"] == 1


# ── the three refusals, as a check rather than a paragraph ───────────────────

def test_the_report_reaches_no_database_no_http_and_no_network():
    """The offline claim, parsed rather than grepped.

    Each of these was a live option: an upload endpoint (fastapi), a connector
    (httpx/requests), a persisted diagnostic (sqlalchemy). A comment saying they
    were declined ages badly; this fails the build if one comes back.

    ``ai`` is in the list for ``CLAUDE.md`` §1's reason — every number here is
    deterministic, and a model must never be able to produce one.
    """
    import ast

    banned = {"sqlalchemy", "fastapi", "starlette", "requests", "httpx",
              "urllib", "socket", "sqlite3", "psycopg2"}
    package = Path(__file__).resolve().parents[1] / "app" / "master_health"
    found: dict[str, set[str]] = {}
    for module in sorted(package.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    modules.add(node.module.split(".")[0])
                elif node.level and node.module:
                    # `from ..ai import x` — the relative form the layer rule uses.
                    modules.add(node.module.split(".")[0])
        hit = (modules & banned) | (modules & {"ai"})
        if hit:
            found[module.name] = hit
    assert not found, f"master_health reached for something it must not: {found}"
