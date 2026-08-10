"""Which floor family an item prices in.

`m_floor` is published per family and nothing mapped a product onto those
names, so every line in the catalogue priced at `default`. The claims worth
pinning are mostly refusals, because each one is what a later change will be
tempted to soften into a guess:

* a category that names a **line** and not a family — "Cutting Tools", which is
  what most catalogues actually say — resolves **nothing**, because it is true
  of three families and picking one would move a real floor on no evidence;
* an item nothing places falls to `default` and is **counted as unplaced**, so
  an owner can see how much of the book is priced on a fallback before they
  solve `r` from it;
* the family reaches the **owner** type and never the operations one, because
  two items known to share a family are two items known to share a multiplier.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.commercial import categories as cat
from app.commercial import floor_families as ff
from app.commercial.config import CommercialThresholds
from app.domain import models

TH = CommercialThresholds()
AS_OF = date(2026, 8, 7)


def _product(**kw) -> models.Product:
    return models.Product(product_id=kw.get("pid", "p1"), organization_id="o",
                          external_id=kw.get("pid", "e1"),
                          name=kw.get("name", "Item"),
                          hsn=kw.get("hsn"), category=kw.get("category"))


# ── the evidence order ───────────────────────────────────────────────────────
def test_a_person_beats_the_catalogue_and_the_catalogue_beats_the_tax_code():
    item = _product(category="Inserts", hsn="8466")
    assert ff.resolve(item, TH).family == ff.INSERTS          # catalogue over HSN
    assert ff.resolve(item, TH).source == ff.BY_ZOHO
    forced = ff.resolve(item, TH, override=ff.METROLOGY)
    assert forced.family == ff.METROLOGY                      # person over all
    assert forced.source == ff.BY_OVERRIDE


def test_an_override_that_is_not_a_family_is_ignored_rather_than_trusted():
    """`CUTTING_TOOLS` is a line, not a family. A caller passing one has made a
    category error, and honouring it would ask `m_floor_for_family` for a key
    that does not exist — which silently returns the default."""
    got = ff.resolve(_product(hsn="8209"), TH, override=cat.CUTTING_TOOLS)
    assert got.family == ff.INSERTS and got.source == ff.BY_HSN


# ── the tariff draws the distinction no catalogue category does ──────────────
def test_the_tariff_separates_the_insert_from_the_tool_from_the_holder():
    """8209 unmounted tips, 8207 the interchangeable tool, 8466 the holder. This
    is the whole reason HSN outranks the business line for this question."""
    assert ff.resolve(_product(hsn="82090010"), TH).family == ff.INSERTS
    assert ff.resolve(_product(hsn="82071900"), TH).family == ff.SOLID_CARBIDE
    assert ff.resolve(_product(hsn="84661010"), TH).family == ff.HOLDERS_TOOLSYSTEMS
    assert ff.resolve(_product(hsn="90172000"), TH).family == ff.METROLOGY
    assert ff.resolve(_product(hsn="34031900"), TH).family == ff.CHEMICALS
    assert ff.resolve(_product(hsn="84571010"), TH).family == ff.MACHINES


def test_every_mapped_family_name_is_one_the_parameter_block_knows():
    """A near-miss on a family name prices at default and says nothing. The
    strings here and the keys in `m_floor_by_family` are the same strings."""
    from incentive_engine.floor import m_floor_for_family

    cfg = _cfg()
    table = cfg.get("floor", "m_floor_by_family")
    for _lo, _hi, family in TH.hsn_floor_family_ranges:
        assert family in table, f"{family!r} is not a published family"
    for family in ff.FAMILIES:
        assert family in table
        # And each really is distinct from the default, or the mapping buys
        # nothing for that family.
        assert m_floor_for_family(cfg, family) == Decimal(str(table[family]))


# ── the refusals ─────────────────────────────────────────────────────────────
def test_the_line_that_spans_three_families_resolves_nothing():
    """"Cutting Tools" is what most catalogues say, and it is true of inserts,
    solid carbide and holders alike. Resolving it would be a coin toss that
    moves a floor."""
    got = ff.resolve(_product(category="Cutting Tools"), TH,
                     line=cat.CUTTING_TOOLS)
    assert got.family is None and got.source == ff.BY_NOTHING
    assert got.key == ff.DEFAULT


def test_consumables_are_none_of_the_six_and_stay_unplaced():
    """Abrasives and hardware are real lines for this trade and no family
    covers them. Unplaced is the honest answer; the nearest match is not."""
    for hsn in ("68042200", "73181500", "82041100"):
        assert ff.resolve(_product(hsn=hsn), TH).family is None


def test_the_business_line_only_fires_where_it_settles_the_family():
    """Metrology, machines and coolants map one-to-one. Nothing else does."""
    bare = _product()
    assert ff.resolve(bare, TH, line=cat.METROLOGY).family == ff.METROLOGY
    assert ff.resolve(bare, TH, line=cat.METROLOGY).source == ff.BY_LINE
    assert ff.resolve(bare, TH, line=cat.COOLANTS).family == ff.CHEMICALS
    assert ff.resolve(bare, TH, line=cat.MACHINES).family == ff.MACHINES
    assert ff.resolve(bare, TH, line=cat.CONSUMABLES).family is None
    assert ff.resolve(bare, TH, line=cat.UNCATEGORISED).family is None


def test_a_holder_word_wins_over_the_tool_word_inside_it():
    """"Drill chuck" is a workholder. First-match-wins over an ordered table
    means the order is load-bearing, so it is asserted rather than assumed."""
    assert ff.from_zoho("Drill Chuck") == ff.HOLDERS_TOOLSYSTEMS
    assert ff.from_zoho("Insert Holder") == ff.HOLDERS_TOOLSYSTEMS
    assert ff.from_zoho("Solid Carbide Endmill") == ff.SOLID_CARBIDE
    # "Carbide" alone is true of all three and must resolve nothing.
    assert ff.from_zoho("Carbide") is None
    assert ff.from_zoho("Tooling") is None


# ── coverage is the number that gates a shadow run ───────────────────────────
def test_coverage_counts_what_is_still_priced_on_the_fallback():
    resolved = ff.resolve_all(
        [_product(pid="a", hsn="8209"), _product(pid="b", hsn="8466"),
         _product(pid="c", hsn="6804"), _product(pid="d")],
        TH)
    report = ff.coverage(resolved)
    assert report["products"] == 4
    assert report["unplaced"] == 2
    assert report["placed_share"] == 0.5
    assert report["by_family"][ff.DEFAULT] == 2
    assert report["by_source"][ff.BY_HSN] == 2


def _cfg():
    from incentive_engine.config import load_config
    return load_config(as_of=AS_OF)
