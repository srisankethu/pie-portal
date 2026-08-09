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
    """Better an honest gap than an item in the wrong column.

    8471 is a computer and 8413 is a pump. Both appear exactly once in the live
    masters, and both are deliberately unmapped: sweeping a one-off into a line
    to flatter the coverage figure is how a mix grid starts lying.
    """
    assert cat.from_hsn("8471", TH) is None
    assert cat.from_hsn("8413", TH) is None
    assert cat.from_hsn("6109", TH) is None


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
        + ((6109, 6109, cat.CONSUMABLES),))
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


# ── the catalogue's last mile: placing an item by hand ───────────────────────
#
# The write path, tested through the API rather than the model: what matters is
# that an override outranks every automatic source, that it survives the re-sync
# that rebuilds products, and that only a manager can set one.
@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.routers import insight, platform_auth
    from app.seed import ensure_org_and_users

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    # One item the tariff code places, so an override has something to beat.
    s.add(models.Product(product_id="p-tool", organization_id="org_sanketh",
                         external_id="e-tool", name="CNMG 120408",
                         hsn="82071900", active=True, source_ref={}))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app), Maker


def _auth(client, email):
    from app.seed import SEED_PASSWORD
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_hand_placed_item_beats_its_tariff_code(client):
    c, Maker = client
    head = _auth(c, "m.rao@sanketh.in")
    r = c.put("/api/v1/insight/catalogue/p-tool",
              json={"category": cat.METROLOGY}, headers=head)
    assert r.status_code == 200, r.text
    assert r.json()["category"] == cat.METROLOGY
    assert r.json()["source"] == cat.BY_OVERRIDE

    listing = c.get("/api/v1/insight/catalogue?unplaced_only=false",
                    headers=head).json()
    row = next(i for i in listing["items"] if i["product_id"] == "p-tool")
    assert row["category"] == cat.METROLOGY      # not CUTTING_TOOLS from 8207
    assert row["source"] == cat.BY_OVERRIDE
    assert row["overridden"] is True


def test_clearing_an_override_lets_the_automatic_sources_speak_again(client):
    c, _ = client
    head = _auth(c, "m.rao@sanketh.in")
    c.put("/api/v1/insight/catalogue/p-tool",
          json={"category": cat.METROLOGY}, headers=head)
    assert c.delete("/api/v1/insight/catalogue/p-tool",
                    headers=head).status_code == 204

    listing = c.get("/api/v1/insight/catalogue?unplaced_only=false",
                    headers=head).json()
    row = next(i for i in listing["items"] if i["product_id"] == "p-tool")
    assert row["category"] == cat.CUTTING_TOOLS
    assert row["source"] == cat.BY_HSN


def test_an_override_survives_the_resync_that_rebuilds_the_product(client):
    """The whole reason it lives in its own table. Products are derived and a
    full re-sync rebuilds them; a mapping somebody typed is the only copy."""
    from app.repositories import ReadModelRepository
    from app.domain.schemas import ProductIn, SourceRef

    c, Maker = client
    head = _auth(c, "m.rao@sanketh.in")
    c.put("/api/v1/insight/catalogue/p-tool",
          json={"category": cat.METROLOGY}, headers=head)

    # Re-sync the same item: `upsert_product` rewrites every synced field.
    s = Maker()
    ReadModelRepository(s, "org_sanketh").upsert_product(ProductIn(
        external_id="e-tool", name="CNMG 120408", hsn="82071900",
        category="Cutting Tools", active=True,
        source_ref=SourceRef(system="zoho", record_type="item", record_id="e-tool")))
    s.commit()
    s.close()

    listing = c.get("/api/v1/insight/catalogue?unplaced_only=false",
                    headers=head).json()
    row = next(i for i in listing["items"] if i["product_id"] == "p-tool")
    assert row["category"] == cat.METROLOGY, (
        "a re-sync must not destroy a mapping somebody typed")


def test_a_salesperson_cannot_place_an_item(client):
    """Placing an item is policy — it moves every mix figure downstream."""
    c, _ = client
    head = _auth(c, "r.nair@sanketh.in")
    assert c.put("/api/v1/insight/catalogue/p-tool",
                 json={"category": cat.METROLOGY}, headers=head).status_code == 403
    assert c.get("/api/v1/insight/catalogue", headers=head).status_code == 403


def test_an_unknown_line_is_refused_rather_than_stored(client):
    c, _ = client
    head = _auth(c, "m.rao@sanketh.in")
    r = c.put("/api/v1/insight/catalogue/p-tool",
              json={"category": "NONSENSE"}, headers=head)
    assert r.status_code == 422
    # UNCATEGORISED is not a line either — it is the absence of one.
    assert c.put("/api/v1/insight/catalogue/p-tool",
                 json={"category": cat.UNCATEGORISED},
                 headers=head).status_code == 422


def test_the_list_leads_with_the_items_revenue_runs_through(client):
    """Placing the item nothing sells is busywork; the first rows have to be
    the ones worth the keystrokes."""
    from decimal import Decimal

    c, Maker = client
    head = _auth(c, "m.rao@sanketh.in")
    s = Maker()
    for pid, rev in (("p-big", 900), ("p-small", 10)):
        s.add(models.Product(product_id=pid, organization_id="org_sanketh",
                             external_id=pid, name=pid, hsn=None, active=True,
                             source_ref={}))
        s.add(models.SalesTxn(
            organization_id="org_sanketh", external_ref=f"inv-{pid}",
            customer_id="c1", product_id=pid, date=date(2026, 6, 1),
            qty=Decimal("1"), unit_price=Decimal(rev),
            line_revenue=Decimal(rev), source_ref={"record_id": f"inv-{pid}"}))
    s.commit()
    s.close()

    listing = c.get("/api/v1/insight/catalogue", headers=head).json()
    unplaced = [i["product_id"] for i in listing["items"]]
    assert unplaced[:2] == ["p-big", "p-small"]
    # And the headline is revenue, because a count can look alarming while the
    # unplaced items sell nothing.
    assert listing["unplaced_revenue"] == 910.0


def test_the_headings_the_live_masters_actually_use_are_all_mapped():
    """Measured against the real item masters rather than assumed.

    Sampling 800 items from SLS Engineers and 400 from 4U Precision, these are
    every heading that carries more than a one-off. 7318 was the largest
    unmapped one in both books until it was added. The one-offs a real
    catalogue always has — a computer, a pump, a project import — are
    deliberately absent and stay honestly unplaced.
    """
    live = {
        "8209": cat.CUTTING_TOOLS,   # 379 items — inserts and cermet tips
        "8466": cat.CUTTING_TOOLS,   # 257 — holders, arbors, work holders
        "8207": cat.CUTTING_TOOLS,   # 87  — interchangeable tools
        "8202": cat.CUTTING_TOOLS,   # 5   — saws and blades
        "7318": cat.CONSUMABLES,     # 22 across both books — fasteners
        "6804": cat.CONSUMABLES,     # 8   — abrasives
        "8204": cat.CONSUMABLES,     # 3   — spanners
        "8205": cat.CONSUMABLES,     # 2   — hand tools
        "8203": cat.CONSUMABLES,     # 2   — files and pliers
        "8506": cat.CONSUMABLES,     # 1   — the battery in a digital gauge
        "9031": cat.METROLOGY,       # 4   — measuring instruments
        "8458": cat.MACHINES,        # 1   — a lathe
    }
    for heading, expected in live.items():
        assert cat.from_hsn(heading, TH) == expected, heading


# ── scoping the grid to one connected company ───────────────────────────────
#
# Every other list uses `CompanyFilter`, which hides rows and deliberately never
# restates a total. On this screen the totals *are* the screen — "112 customers
# do not take cutting tools" is the output — so a filter that only hid rows
# would leave the headline describing a book the reader is no longer looking at.
# The bound therefore goes into the snapshot, on the server.


def _two_company_book(Maker):
    """One customer per company, buying a different line each."""
    from decimal import Decimal

    s = Maker()
    for cid, conn, name in (("c-sls", "conn_sls", "Amtek"),
                            ("c-4u", "conn_4u", "Pitti")):
        s.add(models.Customer(customer_id=cid, organization_id="org_sanketh",
                              external_id=cid, name=name, connection_id=conn))
    s.add_all([
        models.ZohoConnection(connection_id="conn_sls",
                              organization_id="org_sanketh",
                              label="SLS Engineers", zoho_organization_id="111"),
        models.ZohoConnection(connection_id="conn_4u",
                              organization_id="org_sanketh",
                              label="4U Precision", zoho_organization_id="222"),
    ])
    # Two items in different lines, so the grids genuinely differ per company.
    s.add(models.Product(product_id="p-cool", organization_id="org_sanketh",
                         external_id="e-cool", name="Cutting oil",
                         hsn="34031900", active=True, source_ref={}))
    for cid, pid in (("c-sls", "p-tool"), ("c-4u", "p-cool")):
        s.add(models.SalesTxn(
            organization_id="org_sanketh", external_ref=f"inv-{cid}",
            customer_id=cid, product_id=pid, date=date(2026, 6, 1),
            qty=Decimal("1"), unit_price=Decimal("100"),
            line_revenue=Decimal("100"), source_ref={"record_id": f"inv-{cid}"}))
    s.commit()
    s.close()


def test_the_grid_offers_every_connected_company_even_with_unstamped_rows(client):
    """Built from the connections, not from row provenance.

    A filter derived from the rows on screen vanishes exactly when it is most
    needed — a book synced before connections were stamped leaves every origin
    null and the control silently never renders, which is what happened on the
    live book.
    """
    c, Maker = client
    _two_company_book(Maker)
    head = _auth(c, "m.rao@sanketh.in")

    body = c.get("/api/v1/insight/mix", headers=head).json()
    assert [x["label"] for x in body["companies"]] == ["4U Precision",
                                                       "SLS Engineers"]
    assert body["scoped_to"] is None


def test_scoping_to_a_company_restates_the_numbers_rather_than_hiding_rows(client):
    c, Maker = client
    _two_company_book(Maker)
    head = _auth(c, "m.rao@sanketh.in")

    both = c.get("/api/v1/insight/mix", headers=head).json()
    assert both["counts"]["customers"] == 2

    one = c.get("/api/v1/insight/mix?connection_id=conn_sls", headers=head).json()
    assert one["scoped_to"] == "conn_sls"
    assert [x["label"] for x in one["customers"]] == ["Amtek"]
    # The headline is recomputed, not merely filtered — this is the whole point.
    assert one["counts"]["customers"] == 1


def test_an_unknown_company_scopes_to_nothing_rather_than_to_everything(client):
    """A bound that silently widened would show the whole book under one
    company's name, which is worse than an empty screen."""
    c, Maker = client
    _two_company_book(Maker)
    head = _auth(c, "m.rao@sanketh.in")

    body = c.get("/api/v1/insight/mix?connection_id=nope", headers=head).json()
    assert body.get("empty_reason")


def test_the_window_accepts_a_quarter(client):
    """3m/6m/1y are the ranges this trade actually plans in."""
    c, Maker = client
    _two_company_book(Maker)
    head = _auth(c, "m.rao@sanketh.in")

    for months in (3, 6, 12):
        r = c.get(f"/api/v1/insight/mix?months={months}", headers=head)
        assert r.status_code == 200, months
        assert r.json()["months"] == months


def test_the_empty_grid_says_which_of_three_things_is_missing():
    """A wrong reason is worse than none — it sends somebody to the wrong place.

    These were served as two states keyed on whether any column existed, and the
    interesting one fell on the wrong side: with trade on the book and every
    item's category unset, the screen said "Nothing has been traded yet" while the
    Customers screen in the same session showed the revenue.
    """
    from datetime import date

    from app.commercial.config import CommercialThresholds
    from app.commercial.insight.mix import BY_CATEGORY, Column, MixLine, build

    as_of = date(2026, 7, 22)
    th = CommercialThresholds()
    cols = [Column(key="inserts", label="Inserts")]
    traded = [MixLine(customer_id="c1", date=as_of, amount=1000.0, key="inserts")]

    def reason(lines, columns):
        return build(lines, {"c1": "Acme"}, as_of, thresholds=th,
                     columns=columns, dimension=BY_CATEGORY)["empty_reason"]

    # 1. Nothing traded at all.
    assert "Nothing has been traded yet" in reason([], cols)

    # 2. Trade exists, but no line of business has been defined to group it into.
    only_uncategorised = reason(
        [MixLine(customer_id="c1", date=as_of, amount=1000.0, key="inserts")], [])
    assert "No line of the business has been defined" in only_uncategorised
    assert "Nothing has been traded" not in only_uncategorised, (
        "there is trade — saying otherwise is the bug this test exists for")

    # 3. Trade and columns both exist, and none of the trade landed in one.
    unattributed = reason(
        [MixLine(customer_id="c1", date=as_of, amount=1000.0, key="something-else")],
        cols)
    assert "There is trade on the book" in unattributed
    assert "Nothing has been traded" not in unattributed

    # And the populated case still has no reason at all.
    assert build(traded, {"c1": "Acme"}, as_of, thresholds=th,
                 columns=cols, dimension=BY_CATEGORY)["empty_reason"] is None
