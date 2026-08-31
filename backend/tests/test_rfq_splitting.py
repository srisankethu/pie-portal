"""The offline RFQ splitter — what it reads, and what it refuses to guess.

`_split_rfq` is the path a fresh clone runs on: with no AI provider configured
the reader in `ai/reading.py` degrades to this, so these are the quantities a
quotation is actually built from most of the time.

Two defects are pinned here, and the second is the worse one.

A reviewer tried five ways of writing a hundred pieces and three came back as
one, silently — priced end to end that is a quotation out by a factor of a
hundred, and it also selects the wrong quantity band, which is what decides the
margin floor a manager signs against.

The other was already there and nobody had reported it: the rule matched *any*
trailing number after whitespace, so `DNMG 150608` — an ordinary insert code —
became code `DNMG` at a quantity of 150,608. The code mangled and the quantity
invented, from one line.
"""
from __future__ import annotations

import pytest

from app.store import _split_rfq


def _one(text: str) -> dict:
    rows = _split_rfq(text)
    assert len(rows) == 1, rows
    return rows[0]


# ── the formats this screen documents, which must not regress ────────────────
@pytest.mark.parametrize("line,code,qty", [
    ("2001174, 20", "2001174", 20),
    ("2045826 x30", "2045826", 30),
    ("CNMG 120408 TN2000  100", "CNMG 120408 TN2000", 100),
    ("CNMG 120408-MP", "CNMG 120408-MP", 1),
])
def test_the_formats_the_sample_text_uses_still_parse(line, code, qty):
    row = _one(line)
    assert (row["code"], row["qty"]) == (code, qty)
    assert not row.get("proposed"), "a documented format is not a guess"


# ── the three the review found silently wrong ───────────────────────────────
@pytest.mark.parametrize("line", [
    "CNMG 120408 TN2000 - 100 nos",
    "CNMG 120408-MP insert 100 nos",
    "100 nos CNMG 120408 TN2000",
    "CNMG 120408 TN2000 qty 100",
    "qty 100 CNMG 120408 TN2000",
    "100 pcs of CNMG 120408 TN2000",
    "TNMG 160404 - 100nos.",
])
def test_a_hundred_pieces_is_a_hundred_however_it_is_written(line):
    assert _one(line)["qty"] == 100, line


def test_the_reviews_numbered_prose_paste_reads_every_quantity():
    """The exact text from the salesperson review, which produced `reqQty: 1`
    on all four lines."""
    rows = _split_rfq(
        "1. CNMG 120408-MP insert - 100 nos\n"
        "2. DNMG 150608-MP insert - 50 nos\n"
        "3. 25mm shank turning holder - 5 nos\n"
        "4. 8.0mm HSS-Co machine reamer - 10 nos\n")
    assert [r["qty"] for r in rows] == [100, 50, 5, 10]
    assert [r["code"] for r in rows] == [
        "CNMG 120408-MP insert", "DNMG 150608-MP insert",
        "25mm shank turning holder", "8.0mm HSS-Co machine reamer"]


def test_a_list_marker_is_never_read_as_the_quantity():
    """`1.` is how a person numbers an enquiry. Reading it as the quantity would
    quote one of a hundred and look deliberate."""
    row = _one("1. CNMG 120408-MP insert - 100 nos")
    assert row["qty"] == 100
    assert not row["code"].startswith("1")


# ── the code-mangling hazard ─────────────────────────────────────────────────
@pytest.mark.parametrize("line", [
    "DNMG 150608",
    "CNMG 120408",
    "TNMG 160404",
])
def test_a_two_token_code_ending_in_digits_is_a_code_not_a_quantity(line):
    row = _one(line)
    assert row["code"] == line, "the code must survive intact"
    assert row["qty"] == 1


def test_an_explicit_separator_still_lifts_the_digit_bound():
    """The bound is on *bare* whitespace only. A comma, an `x` or a dash is
    somebody saying "this is a quantity", and a large order is allowed."""
    assert _one("2001174, 250000")["qty"] == 250000
    assert _one("2001174 x250000")["qty"] == 250000
    assert _one("2001174 - 250000 nos")["qty"] == 250000
    assert _one("2001174 qty 250000 nos")["qty"] == 250000


# ── the unit word must not lift the bound, nor eat the code ──────────────────
@pytest.mark.parametrize("line,code", [
    ("DNMG 150608 nos", "DNMG 150608"),
    ("CNMG 120408 nos", "CNMG 120408"),
    ("TNMG 160404 pcs", "TNMG 160404"),
])
def test_a_unit_word_after_a_numeric_code_does_not_make_it_a_quantity(line, code):
    """`_BARE_QTY_DIGITS` exists because `DNMG 150608` was read as 150,608 of a
    code called `DNMG`. Lifting that bound whenever a unit word appeared let the
    same defect back in through the other door: the unit sits *after* the
    number, so it says nothing about which digits were meant.

    The line still carries a unit word nobody could attribute, so it travels
    flagged — the estimate is blocked until a person confirms the reading,
    rather than a quotation going out for a hundred and fifty thousand.
    """
    row = _one(line)
    assert row["qty"] == 1, f"{line!r} states no quantity"
    assert row["code"].startswith(code), "the code must survive intact"
    assert row.get("proposed") is True, "a stated-but-unread unit word is flagged"


@pytest.mark.parametrize("line", [
    "2001174 nos",
    "2001174 pcs",
    "6739214 units",
    "2001174 NOS",
])
def test_a_unit_word_is_never_matched_as_a_prefix_of_the_next_token(line):
    """The leading rule read `no` out of `nos` and kept the leftover `s` as the
    product code, so `2001174 nos` became two million of a code one character
    long. The number in these lines is the part, not the quantity."""
    row = _one(line)
    assert row["qty"] == 1, f"{line!r} names a part, not a quantity"
    assert row["code"].startswith(line.split()[0]), "the part number must survive"
    assert len(row["code"]) > 1, "the code is not the tail of a chopped unit word"


def test_the_shapes_that_do_state_a_quantity_still_read_it():
    """The guard above must not cost the readings it was put in to protect."""
    assert _one("100 nos 2001174")["qty"] == 100
    assert _one("100 nos of CNMG 120408")["qty"] == 100
    assert _one("50 pieces DNMG 150608")["qty"] == 50
    assert _one("100 no. CNMG 120408")["qty"] == 100
    assert _one("CNMG 120408 100 nos")["qty"] == 100


# ── the part that must not default silently ──────────────────────────────────
def test_a_unit_word_we_could_not_attribute_is_flagged_not_defaulted():
    """A unit word is evidence a quantity was meant. Failing to read it is a gap
    to report — §1, absence of evidence is not a pass — so the line travels as
    `proposed`, which is the machinery that blocks the estimate until a person
    clears it."""
    row = _one("CNMG 120408-MP insert, nos")
    assert row["qty"] == 1
    assert row.get("proposed") is True
    assert "quantity" in row["reading"].lower()


def test_a_bare_code_is_one_unit_and_is_not_flagged():
    """Pasting a column of codes is a documented way to use this screen, and
    flagging every line of it would make the flag meaningless."""
    rows = _split_rfq("CNMG 120408-MP\nDNMG 150608-MP\n")
    assert [r["qty"] for r in rows] == [1, 1]
    assert not any(r.get("proposed") for r in rows)


def test_blank_lines_are_dropped_and_nothing_else_is():
    """Prose the parser cannot read stays a line on purpose. It resolves
    UNRESOLVED, which is a technical blocker somebody sees and deletes — whereas
    a heuristic that dropped it could drop a real requirement instead."""
    rows = _split_rfq("Please quote for the following:\n\nCNMG 120408-MP, 10\n")
    assert len(rows) == 2
    assert rows[0]["code"] == "Please quote for the following:"
