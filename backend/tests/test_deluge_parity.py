"""The Deluge port must decode exactly what pie-parser decodes.

``tests/deluge_reference.py`` is the Deluge function written in Python — no
named capture groups, no lookaheads, positional character tests only, so it can
be transliterated line for line into ``deluge/zoho_books_item_pie_parse.dg``.
Verifying it here verifies the Deluge, which cannot be executed in CI.

The corpus is the real 6,717-row Kennametal/WIDIA nomenclature file. Parity on a
handful of hand-picked codes proves nothing; parity across every ISO designation
a real catalogue contains is the claim worth defending.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.config import settings
from app.pie_describe import describe
from tests.deluge_reference import describe as deluge_describe, parse_iso

CORPUS = settings.PIE_PARSER_ROOT / "corpora" / "kmt_zcnc_2026-07_nomenclature.csv"
SIZE_KEYS = ("edge_length_mm", "thickness_mm", "corner_radius_mm", "cutting_dia_mm")


def _corpus_names() -> list[str]:
    if not CORPUS.exists():
        pytest.skip(f"corpus not present at {CORPUS}")
    with CORPUS.open(encoding="utf-8") as fh:
        return [r["Material Description"] for r in csv.DictReader(fh)]


def _pie_sizes(name: str) -> dict:
    d = describe(name)
    if d.product_family not in ("turning_insert", "milling_insert"):
        return {}
    return {k: v for k, v in d.dimensions.items() if k in SIZE_KEYS}


def _port_sizes(name: str) -> dict:
    return {k: v for k, v in parse_iso(name).items() if k in SIZE_KEYS}


def test_the_port_agrees_with_pie_parser_on_every_full_iso_code():
    """The headline claim. Any divergence here means the Deluge function and
    the Quote Builder would describe the same insert differently."""
    names = _corpus_names()
    checked = disagreed = 0
    failures = []

    for name in names:
        pie = _pie_sizes(name)
        if "edge_length_mm" not in pie and "cutting_dia_mm" not in pie:
            continue                      # not a full designation
        checked += 1
        port = _port_sizes(name)
        if port != pie:
            disagreed += 1
            if len(failures) < 5:
                failures.append(f"{name}: pie={pie} port={port}")

    assert checked > 400, f"expected the corpus to hold hundreds of ISO codes, saw {checked}"
    assert disagreed == 0, f"{disagreed}/{checked} disagreed:\n" + "\n".join(failures)


def test_the_port_never_decodes_what_pie_parser_declines():
    """A port that decodes more is not "better" — it invents measurements the
    engine deliberately withholds, and writes them onto items."""
    invented = []
    for name in _corpus_names():
        port = _port_sizes(name)
        if port and not _pie_sizes(name):
            invented.append(f"{name} -> {port}")
    assert not invented, "decoded where pie-parser declined:\n" + "\n".join(invented[:5])


# ── the specific rules, so a regression names itself ────────────────────────
def test_a_round_insert_reports_a_diameter_and_no_corner_radius():
    """A round insert has no corner radius. The pack's round grammar captures
    those digits and binds no slot for them; emitting R0.0 would be a
    measurement ISO 1832 does not define."""
    d = parse_iso("RCMX 1204MO - THM")
    assert d["cutting_dia_mm"] == 12 and d["thickness_mm"] == 4.76
    assert "corner_radius_mm" not in d
    assert "edge_length_mm" not in d, "the first pair is a diameter, not an edge"


def test_corner_radius_is_tenths_notation():
    assert parse_iso("CNMG 120408")["corner_radius_mm"] == 0.8
    assert parse_iso("TNMG 220416")["corner_radius_mm"] == 1.6
    assert parse_iso("CCMT 060204")["corner_radius_mm"] == 0.4


def test_the_t_thickness_codes_decode():
    assert parse_iso("SCMT 09T308")["thickness_mm"] == 3.97
    assert parse_iso("RDEX12T3MOT")["thickness_mm"] == 3.97


def test_a_longer_digit_run_is_a_part_number_not_a_size():
    """The engine's lookahead guards. Without them a part number that happens
    to start with four letters decodes into fictional millimetres."""
    assert parse_iso("ABCD 1234567") == {}
    assert parse_iso("ABCD 12345") == {}


def test_a_code_behind_a_prefix_is_still_found():
    d = parse_iso("WSP,CCMT060204,THM")
    assert d["edge_length_mm"] == 6 and d["corner_radius_mm"] == 0.4


def test_narrative_prefixes_are_stripped():
    assert parse_iso("CARBIDE INSERT XPHT160404")["edge_length_mm"] == 16


def test_a_name_that_is_not_an_insert_decodes_to_nothing():
    """The 4U item that prompted this. Nothing is the honest answer, and the
    Deluge function writes nothing for it."""
    assert parse_iso("16X16X35X90/ ALU POWER 3LF45 LONG E/M E5E49160") == {}
    assert deluge_describe("16X16X35X90/ ALU POWER 3LF45 LONG E/M E5E49160") == ""


def test_shape_letter_i_is_not_a_shape():
    assert parse_iso("INMG 120408") == {}
