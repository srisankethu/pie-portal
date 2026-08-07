"""Product mix: placing an item in a line, and what a gap is allowed to claim.

The arithmetic is a grid and a co-occurrence share. The claims worth pinning are
the refusals, because every one of them makes the screen look emptier and each
is the thing a later change will be tempted to soften:

* an item nothing can place is **uncategorised**, never guessed into a column —
  a phantom column entry is a phantom gap, and a phantom gap sends somebody on
  a drive to sell a line the customer already buys;
* a gap is **not money**, and the module says so where it would be read;
* affinity below the support floor is **not reported**, because a share over
  four customers is an anecdote and an anecdote rendered as a percentage gets
  quoted in meetings as evidence;
* **lapsed is its own state**. Folding it into "no" throws away the strongest
  cell on the grid: the line was already approved and the buying stopped.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.commercial import categories as cat
from app.commercial.config import CommercialThresholds
from app.commercial.insight import mix
from app.domain import models

TH = CommercialThresholds()
AS_OF = date(2026, 8, 7)
LINES = list(cat.ORDER)


def _product(**kw) -> models.Product:
    return models.Product(product_id=kw.get("pid", "p1"), organization_id="o",
                          external_id="e1", name=kw.get("name", "Item"),
                          hsn=kw.get("hsn"), category=kw.get("category"))


# ── placing an item in a line ────────────────────────────────────────────────
def test_a_person_beats_the_catalogue_and_the_catalogue_beats_the_tax_code():
    """The order is the design: an override is somebody's answer, a Zoho
    category is the catalogue's, and an HSN prefix is an inference."""
    item = _product(category="Coolant", hsn="8207")
    assert cat.resolve(item, TH).category == cat.COOLANTS       # Zoho over HSN
    assert cat.resolve(item, TH).source == cat.BY_ZOHO
    forced = cat.resolve(item, TH, override=cat.METROLOGY)
    assert forced.category == cat.METROLOGY                     # person over all
    assert forced.source == cat.BY_OVERRIDE


def test_the_tax_code_places_an_item_the_catalogue_says_nothing_about():
    assert cat.resolve(_product(hsn="82071900"), TH).category == cat.CUTTING_TOOLS
    assert cat.resolve(_product(hsn="34031900"), TH).category == cat.COOLANTS
    assert cat.resolve(_product(hsn="90172000"), TH).category == cat.METROLOGY
    assert cat.resolve(_product(hsn="84571010"), TH).category == cat.MACHINES
    assert cat.resolve(_product(hsn="82071900"), TH).source == cat.BY_HSN


def test_the_heading_is_read_however_the_catalogue_writes_the_code():
    """4, 6 and 8 digits are the same heading, and catalogues carry dots."""
    assert cat.heading_of("8207") == 8207
    assert cat.heading_of("820730") == 8207
    assert cat.heading_of("82073010") == 8207
    assert cat.heading_of("8466.10") == 8466
    assert cat.heading_of("9017 00 00") == 9017
    # Fewer than four digits is not a heading, and inventing one would place an
    # item on two characters of evidence.
    assert cat.heading_of("82") is None
    assert cat.heading_of("") is None


def test_a_range_covers_a_run_of_headings_and_the_narrower_one_wins():
    """The tariff is organised in runs — 8456–8465 is machine tools as a block —
    and writing that as ten prefixes is ten places to leave a gap."""
    for heading in ("8456", "8460", "8465"):
        assert cat.from_hsn(heading, TH) == cat.MACHINES
    # 8466 sits immediately after that block and is deliberately *not* in it:
    # holders are tooling, not a machine.
    assert cat.from_hsn("8466", TH) == cat.CUTTING_TOOLS
    # A narrow range beats a wide one that contains it.
    overlapping = CommercialThresholds(
        hsn_category_ranges=TH.hsn_category_ranges
        + ((8200, 8299, cat.CONSUMABLES),))
    assert cat.from_hsn("8207", overlapping) == cat.CUTTING_TOOLS
    assert cat.from_hsn("8299", overlapping) == cat.CONSUMABLES


def test_a_heading_in_no_range_stays_unplaced():
    """Better an honest gap than an item in the wrong column."""
    assert cat.from_hsn("7318", TH) is None


def test_an_item_nothing_can_place_is_uncategorised_and_never_guessed():
    unplaceable = _product(name="Assorted", hsn=None, category=None)
    resolved = cat.resolve(unplaceable, TH)
    assert resolved.category == cat.UNCATEGORISED
    assert resolved.source == cat.BY_NOTHING
    assert resolved.known is False


def test_a_blank_or_meaningless_zoho_category_falls_through_rather_than_sticking():
    assert cat.from_zoho("") is None
    assert cat.from_zoho("   ") is None
    assert cat.from_zoho("Misc") is None
    # ...but it is matched loosely enough to survive how people actually type.
    assert cat.from_zoho("CUTTING TOOLS") == cat.CUTTING_TOOLS
    assert cat.from_zoho("Tooling - Cutting") == cat.CUTTING_TOOLS
    assert cat.from_zoho("Coolant & Lubricants") == cat.COOLANTS


def test_the_hsn_ranges_are_inside_the_thresholds_version():
    """Re-mapping a heading must make last quarter's mix distinguishable, which
    is the whole reason the map is policy rather than a module constant."""
    other = CommercialThresholds(
        hsn_category_ranges=TH.hsn_category_ranges
        + ((7318, 7318, cat.CONSUMABLES),))
    assert other.version != TH.version


def test_the_vendor_inference_floors_are_inside_the_version_too():
    """Loosening them changes how much of the grid is inference rather than
    fact, which must be visible in the version a figure was stamped with."""
    assert CommercialThresholds(vendor_category_min_items=2).version != TH.version
    assert CommercialThresholds(vendor_category_dominance=0.4).version != TH.version


# ── the fourth source: the principal who supplies it ─────────────────────────
def test_an_unplaced_item_takes_its_suppliers_line():
    """An authorised distributor's suppliers are mostly single-line, so where
    the tariff code is blank, who sold it to us is real evidence."""
    products = [_product(pid=f"p{i}", hsn="34031900") for i in range(5)]
    products.append(_product(pid="blank", hsn=None))
    vendor_of = {p.product_id: "v-coolant" for p in products}
    resolved = cat.resolve_all(products, TH, vendor_of=vendor_of)
    assert resolved["blank"].category == cat.COOLANTS
    assert resolved["blank"].source == cat.BY_VENDOR


def test_a_mixed_principal_infers_nothing():
    """A major tooling brand sells inserts, holders and gauges. Sweeping their
    unplaced items into whichever line happened to be commonest is exactly the
    guess this module exists to refuse."""
    products = [_product(pid="a", hsn="82071900"), _product(pid="b", hsn="82071900"),
                _product(pid="c", hsn="90172000"), _product(pid="d", hsn="34031900"),
                _product(pid="blank", hsn=None)]
    vendor_of = {p.product_id: "v-mixed" for p in products}
    resolved = cat.resolve_all(products, TH, vendor_of=vendor_of)
    assert resolved["blank"].category == cat.UNCATEGORISED


def test_too_few_placed_items_infer_nothing():
    """One coincidence is not a dominant line."""
    products = [_product(pid="a", hsn="34031900"), _product(pid="blank", hsn=None)]
    resolved = cat.resolve_all(products, TH,
                               vendor_of={"a": "v", "blank": "v"})
    assert resolved["blank"].category == cat.UNCATEGORISED


def test_a_vendor_inference_never_overwrites_better_evidence():
    """Weaker evidence does not get to win. The item's own tariff code stands."""
    products = [_product(pid=f"p{i}", hsn="34031900") for i in range(6)]
    products.append(_product(pid="tool", hsn="82071900"))
    vendor_of = {p.product_id: "v-coolant" for p in products}
    resolved = cat.resolve_all(products, TH, vendor_of=vendor_of)
    assert resolved["tool"].category == cat.CUTTING_TOOLS
    assert resolved["tool"].source == cat.BY_HSN


def test_dominant_line_needs_both_support_and_dominance():
    assert cat.dominant_line([cat.COOLANTS] * 3, TH) is None          # too few
    assert cat.dominant_line([cat.COOLANTS] * 5, TH) == cat.COOLANTS
    mixed = [cat.COOLANTS] * 3 + [cat.METROLOGY] * 2
    assert cat.dominant_line(mixed, TH) is None                       # 60% < 70%


def test_the_catalogue_report_says_how_much_it_could_not_place():
    """A grid built on a half-placed catalogue has half-phantom whitespace, and
    the reader has to know that before acting on a gap."""
    resolutions = cat.resolve_all(
        [_product(pid="a", hsn="8207"), _product(pid="b", hsn=None)], TH)
    report = cat.coverage_report(resolutions)
    assert report["products"] == 2
    assert report["uncategorised"] == 1
    assert report["resolved_share"] == pytest.approx(0.5)


# ── the grid ─────────────────────────────────────────────────────────────────
def _line(customer: str, category: str, day: date, amount: float = 1000.0):
    return mix.MixLine(customer_id=customer, date=day, amount=amount,
                       key=category)


#: The five lines of the business, as the columns the grid is given.
COLUMNS = [mix.Column(c, cat.LABELS[c]) for c in LINES]


def _grid(lines, **kw) -> dict:
    kw.setdefault("columns", COLUMNS)
    return mix.build(lines, {}, AS_OF, thresholds=TH, **kw)


def _row(grid: dict, customer: str) -> dict:
    return next(c for c in grid["customers"] if c["customer_id"] == customer)


def _cell(grid: dict, customer: str, category: str) -> dict:
    return next(c for c in _row(grid, customer)["cells"] if c["category"] == category)


def test_a_line_they_bought_recently_reads_as_bought():
    grid = _grid([_line("c1", cat.CUTTING_TOOLS, date(2026, 6, 1))])
    assert _cell(grid, "c1", cat.CUTTING_TOOLS)["state"] == mix.BUYS


def test_a_line_they_stopped_buying_is_lapsed_and_not_folded_into_never():
    """The strongest cell on the grid. A two-state grid throws it away: you had
    the line, it was approved, and the buying stopped — a different call."""
    grid = _grid([_line("c1", cat.CUTTING_TOOLS, date(2026, 6, 1)),
                  _line("c1", cat.COOLANTS, date(2022, 3, 1))])
    assert _cell(grid, "c1", cat.COOLANTS)["state"] == mix.LAPSED
    assert _cell(grid, "c1", cat.METROLOGY)["state"] == mix.NEVER
    assert _cell(grid, "c1", cat.COOLANTS)["last_traded"] == "2022-03-01"


def test_a_lapsed_gap_is_ranked_above_a_never_gap():
    grid = _grid([_line("c1", cat.CUTTING_TOOLS, date(2026, 6, 1)),
                  _line("c1", cat.COOLANTS, date(2022, 3, 1))])
    gaps = _row(grid, "c1")["gaps"]
    assert gaps[0]["category"] == cat.COOLANTS
    assert gaps[0]["state"] == mix.LAPSED


def test_every_column_appears_for_every_customer_even_when_empty():
    """A grid with ragged rows cannot be scanned down a column, which is the
    only way anybody reads a whitespace matrix."""
    grid = _grid([_line("c1", cat.CUTTING_TOOLS, date(2026, 6, 1)),
                  _line("c2", cat.MACHINES, date(2026, 5, 1))])
    for customer in ("c1", "c2"):
        assert [c["category"] for c in _row(grid, customer)["cells"]] == LINES


def test_the_columns_are_in_the_catalogue_order_not_the_data_order():
    """A matrix whose columns move between runs cannot be compared with last
    month's screenshot."""
    grid = _grid([_line("c1", cat.MACHINES, date(2026, 6, 1)),
                  _line("c1", cat.CUTTING_TOOLS, date(2026, 6, 1))])
    assert [c["category"] for c in grid["categories"]] == LINES


def test_an_uncategorised_line_never_becomes_a_column():
    grid = _grid([_line("c1", cat.CUTTING_TOOLS, date(2026, 6, 1)),
                  _line("c1", cat.UNCATEGORISED, date(2026, 6, 1))])
    assert cat.UNCATEGORISED not in [c["category"] for c in grid["categories"]]


# ── affinity ─────────────────────────────────────────────────────────────────
def test_affinity_is_computed_from_who_actually_buys_both():
    lines = []
    # Six cutting-tool customers; three of them also take coolant.
    for i in range(6):
        lines.append(_line(f"c{i}", cat.CUTTING_TOOLS, date(2026, 6, 1)))
        if i < 3:
            lines.append(_line(f"c{i}", cat.COOLANTS, date(2026, 6, 1)))
    grid = _grid(lines)
    pair = next(a for a in grid["affinity"]
                if a["from"] == cat.CUTTING_TOOLS and a["to"] == cat.COOLANTS)
    assert pair["share"] == pytest.approx(0.5)
    assert pair["peers"] == 6


def test_affinity_below_the_support_floor_is_not_reported_at_all():
    """A share over four customers is an anecdote; rendered as a percentage it
    gets quoted as evidence."""
    lines = [_line(f"c{i}", cat.CUTTING_TOOLS, date(2026, 6, 1)) for i in range(3)]
    lines.append(_line("c0", cat.COOLANTS, date(2026, 6, 1)))
    grid = _grid(lines)
    pair = next(a for a in grid["affinity"]
                if a["from"] == cat.CUTTING_TOOLS and a["to"] == cat.COOLANTS)
    assert pair["share"] is None
    assert pair["estimable"] is False


def test_affinity_is_directional_because_the_two_directions_differ():
    """'90% of coolant buyers take cutting tools' and '20% of cutting-tool
    buyers take coolant' are both true and describe different opportunities."""
    lines = []
    for i in range(10):
        lines.append(_line(f"c{i}", cat.CUTTING_TOOLS, date(2026, 6, 1)))
    for i in range(5):
        lines.append(_line(f"c{i}", cat.COOLANTS, date(2026, 6, 1)))
    grid = _grid(lines)
    forward = next(a for a in grid["affinity"]
                   if a["from"] == cat.CUTTING_TOOLS and a["to"] == cat.COOLANTS)
    back = next(a for a in grid["affinity"]
                if a["from"] == cat.COOLANTS and a["to"] == cat.CUTTING_TOOLS)
    assert forward["share"] == pytest.approx(0.5)
    assert back["share"] == pytest.approx(1.0)


def test_a_gap_carries_the_line_its_affinity_was_measured_from():
    """'34%' is meaningless without 'of your cutting-tool customers'."""
    lines = []
    for i in range(6):
        lines.append(_line(f"c{i}", cat.CUTTING_TOOLS, date(2026, 6, 1)))
        if i < 4:
            lines.append(_line(f"c{i}", cat.COOLANTS, date(2026, 6, 1)))
    grid = _grid(lines)
    gap = next(g for g in _row(grid, "c5")["gaps"] if g["category"] == cat.COOLANTS)
    assert gap["affinity"]["from"] == cat.CUTTING_TOOLS
    # Rounded to four places by the module, which is the precision a percentage
    # on a screen can carry.
    assert gap["affinity"]["share"] == pytest.approx(4 / 6, abs=1e-4)


def test_the_grid_refuses_to_call_a_gap_an_opportunity():
    """The one claim this view must never make: it cannot see whether they need
    the line, only whether they buy it here."""
    reasons = mix.unavailable()
    assert any("opportunity" in r["what"].lower() for r in reasons)
    assert "do not buy that line from us" in reasons[0]["why"]


def test_an_uncategorised_catalogue_explains_itself_rather_than_rendering_blank():
    grid = mix.build([_line("c1", cat.UNCATEGORISED, date(2026, 6, 1))], {},
                     AS_OF, thresholds=TH, columns=[])
    assert grid["customers"] == []
    # The empty state names the fix, not just the emptiness.
    assert "HSN ranges" in grid["empty_reason"]
    assert "Settings" in grid["empty_reason"]


def test_the_same_grid_pivots_to_principals_without_a_second_implementation():
    """"Who has never bought coolant" and "who has never bought a Sandvik item"
    are one conversation with different people. Two modules would agree until
    the first tuning."""
    columns = [mix.Column("v1", "Kennametal"), mix.Column("v2", "Sandvik")]
    lines = []
    for i in range(6):
        lines.append(mix.MixLine(f"c{i}", date(2026, 6, 1), 100.0, "v1"))
        if i < 3:
            lines.append(mix.MixLine(f"c{i}", date(2026, 6, 1), 100.0, "v2"))
    grid = mix.build(lines, {}, AS_OF, thresholds=TH, columns=columns,
                     dimension=mix.BY_VENDOR)
    assert grid["dimension"] == mix.BY_VENDOR
    assert [c["label"] for c in grid["categories"]] == ["Kennametal", "Sandvik"]
    pair = next(a for a in grid["affinity"]
                if a["from"] == "v1" and a["to"] == "v2")
    assert pair["share"] == pytest.approx(0.5)
    # And the gap on a customer who takes only the first names the second.
    gap = next(g for g in _row(grid, "c5")["gaps"])
    assert gap["label"] == "Sandvik"


def test_a_key_outside_the_given_columns_is_dropped_not_invented():
    """A phantom column entry is a phantom gap."""
    grid = mix.build(
        [mix.MixLine("c1", date(2026, 6, 1), 100.0, "v-unknown")], {}, AS_OF,
        thresholds=TH, columns=[mix.Column("v1", "Kennametal")],
        dimension=mix.BY_VENDOR)
    assert grid["customers"] == []


def test_the_grid_carries_the_version_that_produced_it():
    assert _grid([_line("c1", cat.CUTTING_TOOLS, date(2026, 6, 1))]
                 )["thresholds_version"] == TH.version
