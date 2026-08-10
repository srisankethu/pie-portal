"""Locations, and what each one holds.

Two claims are worth pinning separately here.

The first is that the branch dimension exists at all. Head Office and the
Bangalore branch on this book carry *different GST registrations*, so "which
branch earned this" is a question about a trading entity rather than a reporting
preference, and a godown nested under head office is not a branch at all.

The second is the one that could quietly break something already working:
``stock_snapshots`` is organization-grain, one row per item per day, and several
readers sum it without filtering. Per-location holdings therefore go in their
own table. ``test_the_organization_grain_stock_row_count_is_unchanged`` is the
guard — if location rows ever land in ``stock_snapshots``, every existing shelf
total silently multiplies by the number of branches.
"""
from __future__ import annotations

from decimal import Decimal

from app.domain import models
from app.ingestion.sync import SyncService

from .test_sync_persistence import _Source

_LOCATIONS = [
    {"location_id": "L1", "location_name": "Head Office", "type": "general",
     "parent_location_id": None, "is_location_active": True,
     "is_primary_location": True, "tax_reg_no": "36AAFPU6689Q1ZF"},
    {"location_id": "L2", "location_name": "Bangalore Branch", "type": "general",
     "parent_location_id": None, "is_location_active": True,
     "is_primary_location": False, "tax_reg_no": "29AAFPU6689Q1ZA"},
    # Inactive, and a child of head office. Both facts matter: a roll-up that
    # ignored the nesting would count this shelf twice.
    {"location_id": "L3", "location_name": "Godown", "type": "line_item_only",
     "parent_location_id": "L1", "is_location_active": False,
     "is_primary_location": False, "tax_reg_no": None},
]

_ITEM_LOCATIONS = [
    {"item_id": "i1", "location_id": "L1", "on_hand": 40,
     "available": 40, "asset_value": 4000},
    {"item_id": "i1", "location_id": "L2", "on_hand": 10,
     "available": 10, "asset_value": 1000},
]


class _WithLocations(_Source):
    """The configurable source, plus the two location pulls.

    Subclassed rather than folded into ``_Source`` because both pulls are
    *probed* for with ``hasattr`` — a source that predates locations must stay
    representable, since that is exactly the older connection whose grant never
    included the scope.
    """

    def __init__(self, locations=None, item_locations=None, **kw):
        super().__init__(**kw)
        self._locs = locations or []
        self._item_locs = item_locations or []

    def list_locations(self):
        return list(self._locs)

    def list_item_locations(self, item_ids):
        wanted = set(item_ids)
        return [r for r in self._item_locs if r["item_id"] in wanted]


def _source(**kw) -> _WithLocations:
    return _WithLocations(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs",
                "status": "active", "stock_on_hand": 50, "purchase_rate": 100,
                "track_inventory": True, "item_type": "inventory"}],
        **kw)


def test_a_branch_is_recorded_with_what_makes_it_a_branch(session):
    report = SyncService(session, _source(locations=_LOCATIONS), "org_a").run()
    session.commit()

    assert report.locations == 3
    rows = {r.external_ref: r for r in session.query(models.Location).all()}

    head = rows["L1"]
    assert (head.name, head.is_primary, head.is_active) == ("Head Office", True, True)
    # The registration is the evidence that this is a trading entity rather
    # than a shelf, and the two branches genuinely differ.
    assert head.tax_reg_no == "36AAFPU6689Q1ZF"
    assert rows["L2"].tax_reg_no == "29AAFPU6689Q1ZA"

    godown = rows["L3"]
    assert godown.is_active is False
    assert godown.kind == "line_item_only"
    # Stored, not flattened: a total that added this to head office would count
    # the same stock twice.
    assert godown.parent_external_ref == "L1"


def test_stock_is_recorded_per_location_with_its_valuation(session):
    SyncService(session, _source(locations=_LOCATIONS,
                                 item_locations=_ITEM_LOCATIONS), "org_a").run()
    session.commit()

    rows = {r.location_external_ref: r
            for r in session.query(models.StockLocationSnapshot).all()}
    assert set(rows) == {"L1", "L2"}
    assert rows["L1"].on_hand == Decimal("40")
    # The valuation is what makes a return-on-inventory figure possible per
    # branch at all. It is cost, and scoped as such wherever it is read.
    assert rows["L1"].asset_value == Decimal("4000")
    assert rows["L2"].on_hand == Decimal("10")


def test_the_organization_grain_stock_row_count_is_unchanged(session):
    """The regression this whole table shape exists to prevent.

    ``stock_snapshots`` is one row per item per day and readers sum it without
    filtering. If per-location rows ever land there, every shelf total silently
    multiplies by the number of branches — a wrong number that looks entirely
    plausible.
    """
    SyncService(session, _source(locations=_LOCATIONS,
                                 item_locations=_ITEM_LOCATIONS), "org_a").run()
    session.commit()

    # One item, one day — regardless of how many locations reported stock.
    assert session.query(models.StockSnapshot).count() == 1
    assert session.query(models.StockLocationSnapshot).count() == 2


def test_a_blank_holding_stays_unknown_rather_than_becoming_zero(session):
    """A location that reports no figure is not a location holding nothing."""
    blank = [{"item_id": "i1", "location_id": "L1", "on_hand": None,
              "available": None, "asset_value": None}]
    SyncService(session, _source(locations=_LOCATIONS,
                                 item_locations=blank), "org_a").run()
    session.commit()

    row = session.query(models.StockLocationSnapshot).one()
    assert row.on_hand is None
    assert row.asset_value is None


def test_stock_against_an_unknown_item_is_skipped_not_invented(session):
    stray = _ITEM_LOCATIONS + [
        {"item_id": "ghost", "location_id": "L1", "on_hand": 5,
         "available": 5, "asset_value": 500},
    ]
    SyncService(session, _source(locations=_LOCATIONS,
                                 item_locations=stray), "org_a").run()
    session.commit()

    # The two real rows survive; nothing was conjured for the ghost.
    assert session.query(models.StockLocationSnapshot).count() == 2
    assert session.query(models.Product).count() == 1


def test_resyncing_the_same_day_corrects_rather_than_duplicates(session):
    org = "org_a"
    SyncService(session, _source(locations=_LOCATIONS,
                                 item_locations=_ITEM_LOCATIONS), org).run()
    session.commit()
    moved = [{"item_id": "i1", "location_id": "L1", "on_hand": 25,
              "available": 25, "asset_value": 2500},
             {"item_id": "i1", "location_id": "L2", "on_hand": 10,
              "available": 10, "asset_value": 1000}]
    SyncService(session, _source(locations=_LOCATIONS,
                                 item_locations=moved), org).run()
    session.commit()

    assert session.query(models.StockLocationSnapshot).count() == 2
    assert session.query(models.Location).count() == 3
    head = session.query(models.StockLocationSnapshot).filter_by(
        location_external_ref="L1").one()
    assert head.on_hand == Decimal("25")


def test_a_source_that_cannot_read_locations_still_syncs(session):
    """The connection whose grant predates the location pull."""
    report = SyncService(session, _Source(
        contacts=[{"contact_id": "c1", "contact_name": "Acme", "status": "active"}],
        items=[{"item_id": "i1", "name": "Insert", "unit": "pcs", "status": "active"}],
    ), "org_a").run()
    session.commit()

    assert (report.locations, report.stock_locations) == (0, 0)
    assert report.skipped == []
    assert session.query(models.Location).count() == 0
