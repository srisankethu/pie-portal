"""Reading an enquiry written as prose.

Two things are worth pinning. That the model's reach is *segmentation* — a code
and a quantity, never a match or a price, because pie-parser decides what a code
means and the whole safety argument rests on that division. And that every path
through the reader ends with either usable rows or the regex the product already
had, because an enquiry must never be lost to a provider having a bad minute.
"""
from __future__ import annotations

import json

import pytest

from app.ai import reading


class _Provider:
    """A provider that returns whatever the test hands it."""

    name, model = "test", "test"

    def __init__(self, payload):
        self.payload = payload
        self.seen: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.seen.append(user)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


@pytest.fixture(autouse=True)
def _real_provider(monkeypatch):
    """`read` refuses to call the mock provider, so tests must look configured."""
    monkeypatch.setattr(reading.settings, "AI_PROVIDER", "anthropic")


def _read(payload, text="need 50 nos CNMG 120408-MP"):
    return reading.read(text, _Provider(payload))


# ── what it reads ────────────────────────────────────────────────────────────
def test_prose_becomes_lines_the_parser_can_resolve():
    r = _read({"lines": [
        {"code": "CNMG 120408-MP", "qty": 50,
         "verbatim": "need 50 nos CNMG 120408-MP", "reading": ""},
        {"code": "25mm boring bar", "qty": 20,
         "verbatim": "also 20 of the 25mm boring bar",
         "reading": "referred to a previous order"},
    ]})
    assert r.used_ai
    assert [(ln.code, ln.qty) for ln in r.lines] == [
        ("CNMG 120408-MP", 50), ("25mm boring bar", 20)]
    # The row shape is the one `build_lines` already consumes, so nothing
    # downstream needed a second path for read lines.
    row = r.lines[0].to_row()
    assert set(row) >= {"raw", "code", "qty"}
    assert row["proposed"] is True


def test_a_line_carries_the_customers_own_words():
    """The confirmation is against what the customer wrote, not against the
    model's tidied version of it — otherwise a person is checking the reading
    against the reading."""
    r = _read({"lines": [{"code": "CNMG 120408-MP", "qty": 50,
                          "verbatim": "50nos cnmg120408 mp gr", "reading": "spacing"}]})
    assert r.lines[0].verbatim == "50nos cnmg120408 mp gr"
    assert r.lines[0].reading == "spacing"


def test_a_missing_quantity_is_one_not_a_guess():
    r = _read({"lines": [{"code": "CNMG 120408-MP", "qty": None, "verbatim": "x"}]})
    assert r.lines[0].qty == 1


def test_the_prompt_forbids_matching_and_pricing():
    """The model's reach is the whole safety argument: it segments text, and
    pie-parser decides what a code means."""
    system = reading._SYSTEM.lower()
    assert "do not resolve the item against any catalogue" in system
    assert "do not price anything" in system
    # The grade suffix is the thing that must survive verbatim.
    assert "cnmg 120408-mp is not cnmg 120408-ms" in system


# ── every failure ends at the regex, never at a lost enquiry ─────────────────
def test_no_provider_configured_falls_back(monkeypatch):
    monkeypatch.setattr(reading.settings, "AI_PROVIDER", "mock")
    r = reading.read("CNMG 120408-MP, 50", None)
    assert not r.used_ai and r.lines == []


def test_a_provider_that_raises_falls_back():
    r = _read(RuntimeError("upstream 529"))
    assert not r.used_ai
    assert "529" in r.detail


def test_malformed_output_falls_back():
    r = _read("not json at all")
    assert not r.used_ai and r.lines == []


def test_output_missing_the_lines_key_falls_back():
    r = _read({"items": []})
    assert not r.used_ai


def test_reading_nothing_quotable_falls_back_rather_than_ending_the_enquiry():
    """The regex may still split a list of bare codes. A model that found
    nothing is not a reason to stop trying."""
    r = _read({"lines": []})
    assert not r.used_ai and r.lines == []


def test_an_empty_enquiry_never_calls_the_provider():
    p = _Provider({"lines": []})
    assert reading.read("   ", p).used_ai is False
    assert p.seen == []


# ── bounds ───────────────────────────────────────────────────────────────────
def test_a_runaway_reading_is_capped():
    r = _read({"lines": [{"code": f"C{n}", "qty": 1, "verbatim": "x"}
                         for n in range(reading.MAX_LINES + 40)]})
    assert len(r.lines) == reading.MAX_LINES


def test_a_pasted_thread_is_truncated_not_refused():
    """The request is at the top of a message; the quoted thread below it is
    not, and refusing the whole thing would lose the request."""
    p = _Provider({"lines": [{"code": "X", "qty": 1, "verbatim": "x"}]})
    reading.read("A" * (reading.MAX_CHARS * 3), p)
    assert len(p.seen[0]) == reading.MAX_CHARS


def test_a_fenced_json_block_is_still_read():
    r = _read('```json\n{"lines": [{"code": "X", "qty": 2, "verbatim": "x"}]}\n```')
    assert r.used_ai and r.lines[0].qty == 2


def test_rows_without_a_code_are_dropped_not_defaulted():
    r = _read({"lines": [{"code": "", "qty": 5, "verbatim": "y"},
                         {"code": "REAL", "qty": 1, "verbatim": "z"}]})
    assert [ln.code for ln in r.lines] == ["REAL"]


# ── the gate ─────────────────────────────────────────────────────────────────
#
# The reading is only safe because a person checks it. These pin that the check
# is not optional — a quote cannot leave with a line nobody confirmed.

def _quote_with_a_read_line():
    from app.store import QuoteStore

    st = QuoteStore()
    q = st.create("ACME")
    rows = [reading.ProposedLine(code="CNMG 120408-MP", qty=50,
                                 verbatim="50 nos cnmg 120408 mp").to_row()]
    st.add_rfq(q, "", zoho=None, rows=rows)
    return st, q


def test_an_unconfirmed_reading_blocks_the_estimate():
    st, q = _quote_with_a_read_line()
    assert [ln.id for ln in st.blockers(q)] == [q.lines[0].id]
    assert q.lines[0].status()["label"] == "CONFIRM READING"


def test_confirming_clears_the_reading_hold_and_nothing_else():
    """Confirming says "I have checked this against what the customer wrote".
    It does not say the code resolved — this line is still UNRESOLVED and still
    blocks, for its own reason. A confirmation that cleared every hold would be
    a button that waves a quote through."""
    st, q = _quote_with_a_read_line()
    st.confirm_reading(q.lines[0])
    assert q.lines[0].proposed is False
    assert q.lines[0].status()["label"] != "CONFIRM READING"


def test_a_confirmed_line_that_resolves_is_released():
    st, q = _quote_with_a_read_line()
    ln = q.lines[0]
    st.confirm_reading(ln)
    # What a resolved, in-books, priced line looks like — the state the rest of
    # the quote path produces once Zoho has answered.
    #
    # `service` is part of that state and was the one field this did not set.
    # With the engine present it is already None and the assertion held; without
    # it, `add_rfq` above had recorded `service="PIE"` — "the resolution engine
    # is unavailable for this line" — which `status()` reports as a technical
    # blocker for its own good reason. So the line being described was one that
    # had simultaneously resolved and failed to resolve, and the test failed on
    # every checkout that cannot fetch the private submodule, which includes CI.
    # It has been red on `main` since this file landed.
    #
    # Setting it here rather than marking the test `requires_pie`: the subject
    # is the reading-confirmation hold, not the engine, and a test that runs
    # everywhere is worth more than one that skips where it would have failed.
    ln.rel, ln.supplyCode, ln.inBooks, ln.quoted = "EXACT", "CNMG120408MP", True, 400.0
    ln.service = None
    assert st.blockers(q) == []


def test_a_typed_line_is_never_held_for_confirmation():
    """The gate exists for what a model read. Somebody who typed the code has
    already done the checking, and stopping them would be friction with no
    risk behind it."""
    from app.store import QuoteStore

    st = QuoteStore()
    q = st.create("ACME")
    st.add_rfq(q, "CNMG 120408-MP, 50", zoho=None)
    assert all(not ln.proposed for ln in q.lines)
