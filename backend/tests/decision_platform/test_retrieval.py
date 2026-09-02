"""Nearest-neighbour retrieval over a decoded catalogue: deterministic, honest.

Two properties matter and both are about *refusing*, which is the shape every
test of a candidate source in this codebase takes:

* the same catalogue through the same model gives the same neighbours — in
  this process, in the next one, and on the bytes on disk. A retrieved option
  that cannot be reproduced cannot be explained, and an unexplainable option
  beside an explainable ranking would be read as the ranking;
* nothing is offered below a floor, so a text that resembles nothing in the
  catalogue gets no neighbour rather than its least-unlike record.

What is *not* here, and where it is: that a retrieved record never becomes the
answer is ``pie_service``'s promise, pinned in ``tests/test_pie_service.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import retrieval
from app.retrieval import (
    MIN_SIMILARITY,
    HashedNgramEmbedder,
    RetrievalIndex,
    describe,
    ensure_index,
    index_path_for,
    tokens,
)

RECORDS = [
    {"record_id": "2001174", "description_raw": "CNMG 120408-49 - TN2000",
     "grade": "TN2000", "brand": "WIDIA", "product_family": "turning_insert",
     "iso_shape": "C", "edge_length_mm": 12, "corner_radius_mm": 0.8},
    {"record_id": "5642232", "description_raw": "VSM11 MILLING INSERT R=1.2 MM",
     "grade": "WP40PM", "brand": "WIDIA", "product_family": "milling_insert",
     "series": "VSM11", "corner_radius_mm": 1.2},
    {"record_id": "4149315", "description_raw": "SC DRILL 12mm/.4724/ 5xD COOLANT",
     "grade": "KC7325", "product_family": "solid_carbide_drill",
     "cutting_dia_mm": 12},
    {"record_id": "3668915", "description_raw": "RMS REAMER Ø 10.00 H7 HELICAL FLUTE",
     "grade": "KC6305", "product_family": "reamer", "cutting_dia_mm": 10},
]


def _catalogue(directory: Path, records=RECORDS) -> Path:
    path = directory / "products.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return path


# ── the embedding ────────────────────────────────────────────────────────────
def test_tokens_split_letters_from_digits_and_read_a_decimal_comma():
    assert tokens("cnmg120408-49 R0,8 mm") == ["CNMG", "120408", "49", "R", "0.8", "MM"]
    assert tokens("CNMG 120408") == tokens("CNMG120408") == ["CNMG", "120408"]
    assert tokens("cnmg-12-04-08") == ["CNMG", "12", "04", "08"]
    assert tokens("") == []


def test_the_same_text_embeds_the_same_way_in_another_process():
    """The hash must not be Python's salted one. Run the embedder under two
    different ``PYTHONHASHSEED`` values and compare to this process."""
    text = "CNMG 120408 KCP25 turning insert"
    here = sorted(HashedNgramEmbedder().features(text).items())
    code = ("import json, sys; from app.retrieval import HashedNgramEmbedder; "
            f"print(json.dumps(sorted(HashedNgramEmbedder().features({text!r}).items())))")
    backend = Path(__file__).resolve().parents[2]
    for seed in ("1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(backend)}
        out = subprocess.run([sys.executable, "-c", code], cwd=backend, env=env,
                             capture_output=True, text=True, check=True).stdout
        assert [tuple(x) for x in json.loads(out)] == here, (
            f"features differ under PYTHONHASHSEED={seed}: an index built by one "
            "process would be unreadable by the next")


# ── search ───────────────────────────────────────────────────────────────────
def test_a_paraphrase_retrieves_the_record_it_paraphrases():
    index = RetrievalIndex.build(RECORDS)
    assert index.search("vsm11 milling insert r1,2")[0].record_id == "5642232"
    assert index.search("cnmg120408 tn2000")[0].record_id == "2001174"
    assert index.search("12 mm coolant drill")[0].record_id == "4149315"


def test_exclusion_k_and_empty_text():
    index = RetrievalIndex.build(RECORDS)
    hits = index.search("cnmg120408 tn2000", k=1)
    assert [h.record_id for h in hits] == ["2001174"]
    assert all(h.record_id != "2001174"
               for h in index.search("cnmg120408 tn2000", exclude={"2001174"}))
    assert index.search("") == []
    assert index.search("   ") == []
    assert index.search("cnmg", k=0) == []


def test_nothing_is_offered_below_the_floor():
    """Every text has a least-unlike record; below the floor it is not offered."""
    index = RetrievalIndex.build(RECORDS)
    assert index.search("stainless steel hex nut M8") == []
    # The floor is what refused it, not the absence of any overlap at all.
    assert index.search("stainless steel hex nut M8", min_similarity=0.0) != []


# ── determinism and provenance ───────────────────────────────────────────────
def test_the_same_catalogue_and_model_give_the_same_bytes_and_the_same_neighbours(tmp_path):
    a = _catalogue(tmp_path / "a")
    b = _catalogue(tmp_path / "b")
    first, second = ensure_index(a), ensure_index(b)
    assert index_path_for(a).read_bytes() == index_path_for(b).read_bytes()
    assert first.stamp == second.stamp
    for text in ("vsm11 r1.2", "cnmg 120408", "drill 12"):
        assert first.search(text) == second.search(text)
    # And what was loaded from disk searches as what was built in memory.
    loaded = RetrievalIndex.load(index_path_for(a))
    assert loaded.search("cnmg 120408 tn2000") == first.search("cnmg 120408 tn2000")


def test_the_stamp_names_the_model_and_the_catalogue(tmp_path):
    path = _catalogue(tmp_path)
    index = ensure_index(path)
    assert index.stamp.model_id == HashedNgramEmbedder.model_id
    assert index.stamp.dim == HashedNgramEmbedder.dim
    assert index.stamp.records == len(RECORDS)
    assert index.stamp.catalogue_sha256 == retrieval.index.catalogue_sha256(path)


def test_an_index_for_another_catalogue_is_rebuilt(tmp_path):
    path = _catalogue(tmp_path)
    before = ensure_index(path).stamp
    extra = {"record_id": "9999", "description_raw": "TAP M8 SPIRAL FLUTE",
             "product_family": "tap"}
    _catalogue(tmp_path, [*RECORDS, extra])
    after = ensure_index(path)
    assert after.stamp != before
    assert after.stamp.records == len(RECORDS) + 1
    assert after.search("tap m8 spiral")[0].record_id == "9999"


def test_an_index_built_by_another_model_is_rebuilt(tmp_path):
    path = _catalogue(tmp_path)
    ensure_index(path)
    index_file = index_path_for(path)
    lines = index_file.read_text(encoding="utf-8").splitlines(keepends=True)
    stamp = json.loads(lines[0])
    stamp["model_id"] = "someone-elses-embedder/7"
    index_file.write_text(json.dumps(stamp) + "\n" + "".join(lines[1:]), encoding="utf-8")
    assert retrieval.index.read_stamp(index_file).model_id == "someone-elses-embedder/7"

    rebuilt = ensure_index(path)
    assert rebuilt.stamp.model_id == HashedNgramEmbedder.model_id
    assert retrieval.index.read_stamp(index_file).model_id == HashedNgramEmbedder.model_id


def test_an_unreadable_index_is_rebuilt_rather_than_trusted(tmp_path):
    path = _catalogue(tmp_path)
    index_path_for(path).write_text("not an index\n", encoding="utf-8")
    index = ensure_index(path)
    assert index.search("vsm11")[0].record_id == "5642232"


def test_no_catalogue_is_an_error_not_an_empty_index(tmp_path):
    with pytest.raises(FileNotFoundError):
        ensure_index(tmp_path / "products.jsonl")


def test_describe_reports_the_stamp_and_whether_it_still_describes_the_file(tmp_path):
    path = _catalogue(tmp_path)
    assert describe(path) is None
    ensure_index(path)
    state = describe(path)
    assert state == {"model_id": HashedNgramEmbedder.model_id,
                     "dim": HashedNgramEmbedder.dim, "records": len(RECORDS),
                     "current": True, "min_similarity": MIN_SIMILARITY}
    _catalogue(tmp_path, RECORDS[:2])
    assert describe(path)["current"] is False, (
        "the catalogue changed under the index and the screen would say current")


# ── against the shipped corpus ───────────────────────────────────────────────
@pytest.mark.requires_pie
def test_the_floor_separates_a_bearing_from_a_request_written_in_words():
    """The two texts the floor was chosen against, so a change to the model or
    the floor that lets the bearing through, or shuts the drill out, fails here
    rather than on a quote."""
    import piesupport

    index = ensure_index(piesupport.shared_catalogue())
    assert index.search("6205 2RS C3 bearing") == []
    hits = index.search("12mm carbide drill through coolant for stainless")
    assert hits, "a request written in words retrieved nothing"
    records = {}
    with piesupport.shared_catalogue().open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            records[str(rec["record_id"])] = rec
    top = records[hits[0].record_id]
    assert top["product_family"] == "solid_carbide_drill"
    assert top["cutting_dia_mm"] == 12


# ── confirmed codes as aliases ───────────────────────────────────────────────
from app.retrieval import AliasIndex  # noqa: E402


ALIASES = [
    ("cust-a", "PITTI-7781", "2001174"),
    ("cust-a", "PITTI-7782", "5642232"),
    ("cust-b", "PART-0042", "4149315"),
]


def test_an_alias_answers_only_the_customer_who_confirmed_it():
    index = AliasIndex(ALIASES)
    assert index.aliases == 3
    hits = index.search("cust-a", "pitti 7781 x 10")
    assert hits and hits[0].record_id == "2001174" and hits[0].alias == "PITTI-7781"
    assert index.search("cust-b", "pitti 7781 x 10") == []
    assert index.search(None, "pitti 7781 x 10") == []
    assert index.search("cust-a", "") == []
    assert index.search("nobody", "part 0042") == []


def test_alias_hits_are_deterministic_and_order_independent():
    a = AliasIndex(ALIASES)
    b = AliasIndex(list(reversed(ALIASES)))
    for text in ("pitti 7781", "7782", "pitti"):
        assert a.search("cust-a", text) == b.search("cust-a", text)


def test_two_codes_for_one_record_yield_one_hit_through_the_nearer_code():
    index = AliasIndex([("c", "PITTI-7781", "2001174"), ("c", "OLD-REF-7781", "2001174")])
    hits = index.search("c", "old ref 7781")
    assert [h.record_id for h in hits] == ["2001174"]
    assert hits[0].alias == "OLD-REF-7781"


def test_exclusion_and_the_floor_apply_to_aliases_too():
    index = AliasIndex(ALIASES)
    assert index.search("cust-a", "pitti 7781", exclude={"2001174"})[:1] != [
        h for h in index.search("cust-a", "pitti 7781")[:1]]
    assert index.search("cust-a", "stainless steel hex nut M8") == []


def test_blank_rows_are_skipped_not_indexed():
    index = AliasIndex([("c", "", "1"), ("", "X", "1"), ("c", "X", ""), ("c", "X-1", "1")])
    assert index.aliases == 1


def test_a_phrase_alias_keeps_its_kind_and_a_code_keeps_the_default():
    index = AliasIndex([("c", "PITTI-7781", "2001174"),
                        ("c", "12mm drill for SS", "4149315", "phrase")])
    assert index.aliases == 2
    hit = index.search("c", "12 mm drill for stainless")[0]
    assert hit.record_id == "4149315" and hit.kind == "phrase"
    assert hit.alias == "12mm drill for SS"
    assert index.search("c", "pitti 7781")[0].kind == "code"
