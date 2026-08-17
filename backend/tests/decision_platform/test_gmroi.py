"""GMROI: the arithmetic, and the four refusals that make it worth reading.

The ratio itself is a division. What is worth pinning is everything around it,
because each one makes the screen look emptier or less impressive and is
therefore the thing a later change will be tempted to soften:

* **the window is the one actually observed**, and gross profit is summed over
  exactly it — a year of invoices over eight weeks of stock readings is a figure
  roughly six times the truth, and nothing on the row would say so;
* **below a usable window there is no figure at all**, not a figure with a
  caveat beside it;
* **nothing is annualised**, because scaling a partial window to a year prints a
  projection in the same typeface as a measurement;
* **the three ways of having no answer are distinguished** — a measured zero, an
  unknown numerator and an absent denominator are not one state, and only one of
  them is news;
* **the unattributed residue is its own line**, never an "Other" bucket that
  reads like a small brand.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

import dbsupport
from app.commercial import principals
from app.commercial.config import CommercialThresholds
from app.commercial.insight import absence, gmroi
from app.commercial.policy import load_for_org
from app.config import settings
from app.domain import models
from app.signals.base import CostRow, SaleRow
from app import clock

TH = CommercialThresholds()
AS_OF = date(2026, 8, 10)
ORG = "org_gmroi"


# ── fixtures, hand-built so every number in an assertion is checkable ────────
def _obs(product_id: str, day: date, on_hand="10", rate="100",
         tracked: bool = True) -> gmroi.Observation:
    return gmroi.Observation(
        product_id=product_id, as_of=day,
        on_hand=(Decimal(on_hand) if on_hand is not None else None),
        purchase_rate=(Decimal(rate) if rate is not None else None),
        tracked=tracked)


def _shelf(product_id: str, *, days: int, end: date = AS_OF, on_hand="10",
           rate="100", tracked: bool = True) -> list[gmroi.Observation]:
    """One reading a day for ``days`` days, ending on ``end``."""
    return [_obs(product_id, end - timedelta(days=i), on_hand, rate, tracked)
            for i in range(days)]


def _sale(product_id: str, day: date, revenue: str, qty: str = "1",
          ref: str = "") -> SaleRow:
    return SaleRow(customer_id="c1", product_id=product_id, date=day,
                   qty=Decimal(qty), unit_price=Decimal(revenue),
                   line_revenue=Decimal(revenue),
                   source_ref={"record_id": ref or f"{product_id}{day}"},
                   external_ref=ref or f"{product_id}{day}:l1")


def _cost(product_id: str, day: date, unit_cost: str) -> CostRow:
    return CostRow(product_id=product_id, date=day, qty=Decimal("100"),
                   unit_cost=Decimal(unit_cost),
                   source_ref={"record_id": f"bill{product_id}{day}"},
                   external_ref=f"bill{product_id}{day}:l1")


def _build(*, sales=(), costs=(), observations=(), attribution=None,
           labels=None, requested_days=360) -> dict:
    return gmroi.build(
        sales=list(sales), costs=list(costs), observations=list(observations),
        labels=labels or {}, attribution=attribution or {},
        as_of=AS_OF, requested_days=requested_days, thresholds=TH)


def _sku(result: dict, product_id: str) -> dict:
    return next(s for s in result["skus"] if s["product_id"] == product_id)


def _brand(result: dict, key: str) -> dict:
    return next(b for b in result["brands"] if b["principal_id"] == key)


# ── the ratio ───────────────────────────────────────────────────────────────
def test_gmroi_is_gross_profit_over_average_inventory_at_cost():
    """30 days on the shelf at 10 x ₹100, one sale of ₹1,500 costed at ₹800.

    Average inventory is ₹1,000 every day, so ₹1,000. Gross profit is
    1500 − 800 = ₹700. GMROI is 0.7 — 70 paise back for every rupee held over
    that month, which is exactly the kind of line this view exists to name.
    """
    result = _build(
        observations=_shelf("p1", days=30),
        sales=[_sale("p1", AS_OF - timedelta(days=5), "1500")],
        costs=[_cost("p1", AS_OF - timedelta(days=40), "800")])
    row = _sku(result, "p1")
    assert row["avg_inventory_at_cost"] == 1000.0
    assert row["gross_profit"] == 700.0
    assert row["gmroi"] == 0.7
    assert row["confidence"] == gmroi.MEASURED
    assert result["totals"]["gmroi"] == 0.7


def test_the_average_moves_with_the_shelf_rather_than_taking_the_last_reading():
    """Ten days at ₹2,000 and twenty at ₹500 is not "₹500 now"."""
    observations = (_shelf("p1", days=10, end=AS_OF, on_hand="20")
                    + _shelf("p1", days=20, end=AS_OF - timedelta(days=10),
                             on_hand="5"))
    result = _build(observations=observations,
                    sales=[_sale("p1", AS_OF, "3000")],
                    costs=[_cost("p1", AS_OF - timedelta(days=60), "100")])
    # (10 x 2000 + 20 x 500) / 30 = 1000
    assert _sku(result, "p1")["avg_inventory_at_cost"] == 1000.0


def test_a_day_observed_twice_is_still_one_day_in_the_average():
    """The table is upserted per item per day; the average must not double-weight
    a day a caller happened to assemble twice."""
    doubled = _shelf("p1", days=30) + [_obs("p1", AS_OF, on_hand="10")]
    result = _build(observations=doubled,
                    sales=[_sale("p1", AS_OF, "1500")],
                    costs=[_cost("p1", AS_OF - timedelta(days=60), "800")])
    assert _sku(result, "p1")["inventory_days"] == 30


def test_the_average_is_over_the_days_this_item_was_seen_not_the_whole_window():
    """An item first stocked halfway through the window has a half-window
    average, not a halved one.

    Dividing by every observation day in the window would interpolate zeros the
    platform never observed, and the zeros land on exactly the newly-stocked
    lines — halving their denominator and printing them as spectacular earners.
    """
    observations = _shelf("old", days=60) + _shelf("new", days=30)
    result = _build(
        observations=observations,
        sales=[_sale("new", AS_OF, "1500")],
        costs=[_cost("new", AS_OF - timedelta(days=70), "800")])
    row = _sku(result, "new")
    assert row["inventory_days"] == 30
    assert row["avg_inventory_at_cost"] == 1000.0    # not 500
    assert result["window"]["days_observed"] == 60


# ── requirement 1: the window actually covered ──────────────────────────────
def test_the_window_is_the_span_observed_not_the_span_requested():
    """A year is asked for; sixty days of readings exist. The response says so
    and reports the sixty-day figure, never the year's heading."""
    result = _build(observations=_shelf("p1", days=60), requested_days=360)
    window = result["window"]
    assert window["requested_days"] == 360
    assert window["span_days"] == 60
    assert window["covers_request"] is False
    assert "60 are covered" in window["shortfall"]
    assert "not annualised" in window["basis"].lower()


def test_gross_profit_is_summed_over_the_observed_window_only():
    """The defect this guards against: a numerator from a year of invoices over
    a denominator from two months of readings, six times the truth."""
    result = _build(
        observations=_shelf("p1", days=60),
        sales=[
            _sale("p1", AS_OF - timedelta(days=10), "1500", ref="inside"),
            # Real revenue, ten months old — outside the observed shelf window,
            # so it is not this ratio's numerator.
            _sale("p1", AS_OF - timedelta(days=300), "90000", ref="outside"),
        ],
        costs=[_cost("p1", AS_OF - timedelta(days=400), "800")])
    row = _sku(result, "p1")
    assert row["gross_profit"] == 700.0
    assert row["txns"] == 1


def test_a_sale_on_the_first_observed_day_is_inside_the_window():
    """The effective window is inclusive of its first day. `in_window` is
    half-open on the left, so an off-by-one here silently drops that day."""
    start = AS_OF - timedelta(days=29)
    result = _build(
        observations=_shelf("p1", days=30),
        sales=[_sale("p1", start, "1500")],
        costs=[_cost("p1", start - timedelta(days=10), "800")])
    assert _sku(result, "p1")["txns"] == 1


def test_below_a_usable_window_there_is_no_figure_at_all():
    """Not a figure with a caveat: no rows, and the reason instead."""
    result = _build(observations=_shelf("p1", days=gmroi.MIN_OBSERVED_DAYS - 1),
                    sales=[_sale("p1", AS_OF, "1500")],
                    costs=[_cost("p1", AS_OF, "800")])
    assert result["measurable"] is False
    assert result["skus"] == []
    assert result["brands"] == []
    assert result["totals"] == {}
    assert str(gmroi.MIN_OBSERVED_DAYS) in result["window"]["shortfall"]


def test_a_platform_that_started_yesterday_draws_a_point_and_says_so():
    result = _build(observations=[_obs("p1", AS_OF)])
    assert result["measurable"] is False
    assert result["window"]["days_observed"] == 1


def test_no_stock_has_ever_been_observed_is_its_own_sentence():
    result = _build(sales=[_sale("p1", AS_OF, "1500")])
    assert result["measurable"] is False
    assert "cannot be backfilled" not in result["window"]["shortfall"]
    assert "no stock history to backfill" in result["window"]["shortfall"]


def test_the_branch_dimension_is_still_named_as_missing():
    """Branch is the dimension a reader most expects here, and it is absent.

    This exists because the entry was once deleted rather than restated. Its
    original wording said Zoho reported location-level stock only on the
    Inventory plan's warehouse endpoints; that turned out to be untrue, and
    removing the claim took the notice with it — leaving the gap present and
    unexplained, which is the defect ``gmroi.withheld`` exists to prevent.

    The reason may change again as the split is built. The entry must not
    silently vanish while the figure is still company-wide.
    """
    result = _build(observations=_shelf("p1", days=400))
    entry = next(u for u in result["unavailable"] if u["series"] == "branch")
    # BUILDABLE, not PERMANENT: the per-branch holding *is* recorded now. What
    # is missing is splitting the sales side to match it.
    assert entry["kind"] == absence.BUILDABLE
    assert "stock_location_snapshots" in entry["reason"]


def test_an_annualised_figure_is_refused_where_it_would_be_read():
    result = _build(observations=_shelf("p1", days=60))
    entry = next(u for u in result["unavailable"]
                 if u["series"] == "annualised_gmroi")
    # PERMANENT rather than TRANSIENT: waiting does not make a projection a
    # measurement, and Zoho cannot supply the year that would.
    assert entry["kind"] == absence.PERMANENT


def test_the_short_window_is_transient_because_nobody_can_act_on_it():
    """A stock level for a day that has already passed cannot be recorded. Filing
    it as COLLECTABLE would put "wait a fortnight" on somebody's worklist."""
    result = _build(observations=_shelf("p1", days=60), requested_days=360)
    entry = next(u for u in result["unavailable"]
                 if u["series"] == "gmroi_over_the_requested_window")
    assert entry["kind"] == absence.TRANSIENT


def test_a_window_that_covers_the_request_reports_no_shortfall():
    result = _build(observations=_shelf("p1", days=60), requested_days=60)
    assert result["window"]["shortfall"] is None
    assert not [u for u in result["unavailable"]
                if u["series"] == "gmroi_over_the_requested_window"]


# ── absence of evidence is not a pass ───────────────────────────────────────
def test_an_item_that_sold_nothing_is_a_measured_zero():
    """Held all window, earned nothing. This is the finding, not a gap — and it
    is the one state among the three where a zero is the honest answer."""
    result = _build(observations=_shelf("p1", days=30))
    row = _sku(result, "p1")
    assert row["gross_profit"] == 0.0
    assert row["gmroi"] == 0.0
    assert row["reason"] is None


def test_a_sale_with_no_bill_behind_it_is_unknown_not_zero():
    """Reading it as zero would band an earner a freeloader on our own ignorance."""
    result = _build(observations=_shelf("p1", days=30),
                    sales=[_sale("p1", AS_OF, "1500")])
    row = _sku(result, "p1")
    assert row["gross_profit"] is None
    assert row["gmroi"] is None
    assert row["reason"] == gmroi.NO_COSTED_SALE
    assert "Not zero" in row["reason_meaning"]


def test_a_cost_record_dated_after_the_sale_does_not_cost_it():
    """`cost_basis_asof` takes the latest record on or before the sale; a bill
    that arrives afterwards leaves the line uncosted rather than costing it at a
    price that did not exist yet."""
    result = _build(observations=_shelf("p1", days=30),
                    sales=[_sale("p1", AS_OF - timedelta(days=20), "1500")],
                    costs=[_cost("p1", AS_OF, "800")])
    assert _sku(result, "p1")["gross_profit"] is None


def test_a_shelf_with_no_purchase_rate_is_not_a_free_shelf():
    result = _build(observations=_shelf("p1", days=30, rate=None),
                    sales=[_sale("p1", AS_OF, "1500")],
                    costs=[_cost("p1", AS_OF - timedelta(days=60), "800")])
    row = _sku(result, "p1")
    assert row["avg_inventory_at_cost"] is None
    assert row["gmroi"] is None
    assert row["reason"] == gmroi.INVENTORY_NOT_COSTED
    assert result["counts"]["uncosted_shelf_skus"] == 1
    assert [u for u in result["unavailable"]
            if u["series"] == "average_inventory_for_unpriced_items"]


def test_a_zero_purchase_rate_is_a_placeholder_not_a_price():
    """The same reading `economics.line_economics` takes of one. Valuing a shelf
    at nothing makes every line on it an infinite earner."""
    result = _build(observations=_shelf("p1", days=30, rate="0"),
                    sales=[_sale("p1", AS_OF, "1500")],
                    costs=[_cost("p1", AS_OF - timedelta(days=60), "800")])
    assert _sku(result, "p1")["reason"] == gmroi.INVENTORY_NOT_COSTED


def test_an_item_with_no_reading_in_the_window_has_no_denominator():
    result = _build(observations=_shelf("other", days=30),
                    sales=[_sale("p1", AS_OF, "1500")],
                    costs=[_cost("p1", AS_OF - timedelta(days=60), "800")])
    row = _sku(result, "p1")
    assert row["gmroi"] is None
    assert row["reason"] == gmroi.NO_INVENTORY_OBSERVED


def test_an_item_the_window_has_nothing_to_say_about_gets_no_row():
    """Not on the shelf inside the window and not traded inside it either. A row
    reading "not measurable" about it is noise that pushes the rows carrying a
    finding off the first page — and on a three-thousand-item catalogue there are
    a great many of them."""
    result = _build(
        observations=_shelf("here", days=30),
        sales=[_sale("here", AS_OF, "1500", ref="s1"),
               # Sold ten months ago and never seen on a shelf since.
               _sale("gone", AS_OF - timedelta(days=300), "9000", ref="s2")],
        costs=[_cost("here", AS_OF - timedelta(days=60), "800")])
    assert [s["product_id"] for s in result["skus"]] == ["here"]


def test_an_item_that_sold_off_an_unobserved_shelf_keeps_its_row():
    """The other side of the same rule. This one earned money and the platform
    cannot say off what, which is a gap worth naming rather than hiding."""
    result = _build(
        observations=_shelf("here", days=30),
        sales=[_sale("here", AS_OF, "1500", ref="s1"),
               _sale("unseen", AS_OF, "9000", ref="s2")],
        costs=[_cost("here", AS_OF - timedelta(days=60), "800"),
               _cost("unseen", AS_OF - timedelta(days=60), "5000")])
    row = _sku(result, "unseen")
    assert row["reason"] == gmroi.NO_INVENTORY_OBSERVED
    assert row["gross_profit"] == 4000.0
    assert row["gmroi"] is None


def test_a_service_is_undefined_rather_than_missing():
    """It ties up no inventory, so GMROI does not apply. Named apart from a data
    gap so nobody is sent to fix a reorder level on a service."""
    result = _build(
        observations=_shelf("svc", days=30, tracked=False) + _shelf("p1", days=30),
        sales=[_sale("svc", AS_OF, "1500")],
        costs=[_cost("svc", AS_OF - timedelta(days=60), "800")])
    assert _sku(result, "svc")["reason"] == gmroi.NOT_STOCKED


def test_partial_cost_coverage_is_flagged_rather_than_smoothed():
    """Half the sales costed means the numerator is half-known, so the ratio is
    an understatement of known size — reported, not averaged away.

    The earlier sale predates the only bill on record, so ``cost_basis_asof``
    finds nothing applicable to it and refuses to cost it at a price that did not
    exist yet. The later one is covered.
    """
    result = _build(
        observations=_shelf("p1", days=30),
        sales=[_sale("p1", AS_OF - timedelta(days=25), "1500", ref="before"),
               _sale("p1", AS_OF - timedelta(days=5), "1500", ref="after")],
        costs=[_cost("p1", AS_OF - timedelta(days=15), "800")])
    row = _sku(result, "p1")
    assert row["txns"] == 2 and row["costed_txns"] == 1
    assert row["cost_coverage"] == 0.5
    assert row["confidence"] == gmroi.PARTIAL_COST
    # The costed line only: ₹1,500 − ₹800. The uncosted line's revenue is *not*
    # in the numerator, which is the `weather` defect turned around.
    assert row["gross_profit"] == 700.0
    assert result["counts"]["partial_cost_skus"] == 1
    entry = next(u for u in result["unavailable"]
                 if u["series"] == "gross_profit_on_uncosted_sales")
    assert entry["kind"] == absence.COLLECTABLE


# ── divide by zero ──────────────────────────────────────────────────────────
def test_an_item_with_no_average_inventory_has_no_gmroi_rather_than_infinity():
    result = _build(observations=_shelf("p1", days=30, on_hand="0"),
                    sales=[_sale("p1", AS_OF, "1500")],
                    costs=[_cost("p1", AS_OF - timedelta(days=60), "800")])
    row = _sku(result, "p1")
    assert row["avg_inventory_at_cost"] == 0.0
    assert row["gmroi"] is None
    assert row["reason"] == gmroi.NO_INVENTORY_HELD


def test_an_oversold_shelf_reads_negative_and_still_has_no_gmroi():
    result = _build(observations=_shelf("p1", days=30, on_hand="-4"),
                    sales=[_sale("p1", AS_OF, "1500")],
                    costs=[_cost("p1", AS_OF - timedelta(days=60), "800")])
    assert _sku(result, "p1")["gmroi"] is None


def test_a_book_with_no_measurable_item_has_no_total_rather_than_zero():
    result = _build(observations=_shelf("p1", days=30, rate=None))
    assert result["totals"]["gmroi"] is None


# ── aggregation: brands ─────────────────────────────────────────────────────
def _principal(vendor_id: str, name: str,
               source: str = principals.BY_BILL) -> principals.Principal:
    return principals.Principal(principal_id=vendor_id, name=name, source=source)


def test_a_brand_is_total_profit_over_total_shelf_never_a_mean_of_ratios():
    """A ₹900 line must not weigh as much as a ₹9 lakh one.

    Item A: shelf ₹1,00,000, profit ₹10,000 — GMROI 0.10.
    Item B: shelf ₹100, profit ₹100 — GMROI 1.00.
    The brand is 10,100 / 100,100 = 0.1009, not the 0.55 a mean would give.
    """
    attribution = {"a": _principal("v1", "Kennametal"),
                   "b": _principal("v1", "Kennametal")}
    result = _build(
        observations=(_shelf("a", days=30, on_hand="1000", rate="100")
                      + _shelf("b", days=30, on_hand="1", rate="100")),
        sales=[_sale("a", AS_OF, "110000", qty="100", ref="sa"),
               _sale("b", AS_OF, "200", qty="1", ref="sb")],
        costs=[_cost("a", AS_OF - timedelta(days=60), "1000"),
               _cost("b", AS_OF - timedelta(days=60), "100")],
        attribution=attribution)
    brand = _brand(result, "v1")
    assert brand["gross_profit"] == 10100.0
    assert brand["avg_inventory_at_cost"] == 100100.0
    assert brand["gmroi"] == pytest.approx(0.1009, abs=1e-4)
    assert brand["skus"] == 2 and brand["skus_measured"] == 2


def test_profit_the_brand_ratio_could_not_include_is_reported_not_dropped():
    """An item earning money off a shelf nothing valued cannot enter the ratio.
    Silently omitting it would depress the brand's figure with no trace."""
    attribution = {"a": _principal("v1", "Kennametal"),
                   "b": _principal("v1", "Kennametal")}
    result = _build(
        observations=(_shelf("a", days=30) + _shelf("b", days=30, rate=None)),
        sales=[_sale("a", AS_OF, "1500", ref="sa"),
               _sale("b", AS_OF, "5000", ref="sb")],
        costs=[_cost("a", AS_OF - timedelta(days=60), "800"),
               _cost("b", AS_OF - timedelta(days=60), "1000")],
        attribution=attribution)
    brand = _brand(result, "v1")
    assert brand["skus"] == 2 and brand["skus_measured"] == 1
    assert brand["gross_profit_excluded"] == 4000.0


# ── requirement 2: the unattributed residue is its own line ─────────────────
def test_unattributed_items_are_their_own_line_and_keep_their_own_name():
    result = _build(
        observations=(_shelf("a", days=30) + _shelf("b", days=30)),
        sales=[_sale("a", AS_OF, "1500", ref="sa"),
               _sale("b", AS_OF, "1500", ref="sb")],
        costs=[_cost("a", AS_OF - timedelta(days=60), "800"),
               _cost("b", AS_OF - timedelta(days=60), "800")],
        attribution={"a": _principal("v1", "Kennametal")})
    residue = _brand(result, gmroi.UNATTRIBUTED)
    assert residue["label"] == gmroi.UNATTRIBUTED_LABEL
    assert residue["attributed"] is False
    assert residue["skus"] == 1
    assert result["counts"]["unattributed_skus"] == 1
    # Counted as a principal it is not: `brands` counts the real ones only.
    assert result["counts"]["brands"] == 1


def test_the_unattributed_line_sorts_last_whatever_its_figure():
    """Ordered ahead of a real brand it reads as the worst performer on the
    page, which is a claim about a supplier that does not exist."""
    result = _build(
        observations=(_shelf("a", days=30, on_hand="10")
                      + _shelf("b", days=30, on_hand="10")),
        # The unattributed item is the *better* earner, so a plain sort by
        # GMROI would put it first.
        sales=[_sale("a", AS_OF, "1100", ref="sa"),
               _sale("b", AS_OF, "5000", ref="sb")],
        costs=[_cost("a", AS_OF - timedelta(days=60), "1000"),
               _cost("b", AS_OF - timedelta(days=60), "1000")],
        attribution={"a": _principal("v1", "Kennametal")})
    assert [b["principal_id"] for b in result["brands"]] == \
        ["v1", gmroi.UNATTRIBUTED]


def test_a_brand_claims_only_the_evidence_its_thinnest_item_has():
    """One principal can be reached both ways: a bill names the vendor for an item
    bought inside the sync window, and a matched manufacturer names the same
    vendor for one bought outside it. Labelling the brand "from a purchase bill"
    because its first item happened to be overstates the other one.
    """
    result = _build(
        observations=(_shelf("a", days=30) + _shelf("b", days=30)),
        sales=[_sale("a", AS_OF, "1500", ref="sa"),
               _sale("b", AS_OF, "1500", ref="sb")],
        costs=[_cost("a", AS_OF - timedelta(days=60), "800"),
               _cost("b", AS_OF - timedelta(days=60), "800")],
        attribution={"a": _principal("v1", "Kennametal", principals.BY_BILL),
                     "b": _principal("v1", "Kennametal",
                                     principals.BY_MANUFACTURER)})
    brand = _brand(result, "v1")
    assert brand["source"] == principals.BY_MANUFACTURER
    # And the mix, so the reader can size how much of it the stronger fact covers
    # rather than inferring that from one label.
    assert brand["skus"] == 2 and brand["skus_from_bill"] == 1


def test_a_brand_placed_entirely_by_bills_says_so():
    result = _build(
        observations=_shelf("a", days=30),
        sales=[_sale("a", AS_OF, "1500")],
        costs=[_cost("a", AS_OF - timedelta(days=60), "800")],
        attribution={"a": _principal("v1", "Kennametal", principals.BY_BILL)})
    brand = _brand(result, "v1")
    assert brand["source"] == principals.BY_BILL
    assert brand["skus_from_bill"] == 1


def test_a_manufacturer_only_principal_carries_the_source_that_placed_it():
    """`principals.py`'s second rung. Which fact attributed a brand is the
    number to read before trusting the brand's figure."""
    result = _build(
        observations=_shelf("a", days=30),
        sales=[_sale("a", AS_OF, "1500")],
        costs=[_cost("a", AS_OF - timedelta(days=60), "800")],
        attribution={"a": principals.Principal(
            principal_id=principals.maker_key("YG-1"), name="YG-1",
            source=principals.BY_MANUFACTURER)})
    brand = result["brands"][0]
    assert brand["source"] == principals.BY_MANUFACTURER
    assert brand["source_label"] == "From the item's manufacturer"
    assert _sku(result, "a")["principal_source"] == principals.BY_MANUFACTURER


# ── ordering and provenance ─────────────────────────────────────────────────
def test_earners_lead_and_unmeasurable_rows_sort_last_not_as_zero():
    result = _build(
        observations=(_shelf("earner", days=30) + _shelf("quiet", days=30)
                      + _shelf("unknown", days=30, rate=None)),
        sales=[_sale("earner", AS_OF, "3000", ref="s1"),
               _sale("unknown", AS_OF, "3000", ref="s2")],
        costs=[_cost("earner", AS_OF - timedelta(days=60), "800"),
               _cost("unknown", AS_OF - timedelta(days=60), "800")])
    assert [s["product_id"] for s in result["skus"]] == \
        ["earner", "quiet", "unknown"]


def test_every_response_carries_the_version_of_the_policy_behind_it():
    for observations in ([], _shelf("p1", days=30)):
        assert _build(observations=observations)["thresholds_version"] == TH.version


def test_an_item_with_no_name_is_named_honestly_rather_than_by_its_id_alone():
    result = _build(observations=_shelf("p1", days=30))
    assert _sku(result, "p1")["label"] == "Unnamed item (id p1)"
    named = _build(observations=_shelf("p1", days=30), labels={"p1": "TNMG 160408"})
    assert _sku(named, "p1")["label"] == "TNMG 160408"


# ── requirement 3: owner/manager scope, permanently ─────────────────────────
def test_a_salesperson_is_told_why_the_column_is_missing_from_their_shelf():
    """A column that vanishes with no explanation reads as a bug, and the next
    person to notice files one — or puts it back."""
    entry = gmroi.withheld()
    assert entry["series"] == "gmroi"
    # WITHHELD, not PERMANENT: the figure is computed and a manager sees it.
    # Filing a working permission rule under "not answerable" invites a "fix".
    assert entry["kind"] == absence.WITHHELD


# ── over HTTP: the gate, and the wiring behind it ───────────────────────────
#
# A builder tested through its own signature cannot fail the way a call site
# fails — the reason `/insight/daily` shipped a 500 for both roles that could
# open it while every unit test passed. So the endpoint is exercised end to end,
# with a book seeded into the same database it reads.

SEED_ORG = settings.DEFAULT_ORG_ID
LOGINS = {"SALESPERSON": "r.nair@pie.example",
          "SALES_MANAGER": "m.rao@pie.example",
          "OWNER": "s.menon@pie.example"}


def _seed_book(s, *, org: str = SEED_ORG, today: date) -> None:
    """One item on the shelf for 40 days, sold once, bought once.

    Forty days clears ``MIN_OBSERVED_DAYS``, so the endpoint returns figures
    rather than the refusal — the role assertions need a payload with something
    in it to withhold. The arithmetic is the same as the first unit test above:
    shelf ₹1,000 a day, profit ₹700, GMROI 0.7.
    """
    s.add(models.Customer(customer_id="c1", organization_id=org,
                          external_id="zc1", name="Pitti", source_ref={}))
    s.add(models.Product(product_id="p1", organization_id=org,
                         external_id="zp1", name="TNMG 160408",
                         manufacturer="Kennametal", source_ref={}))
    for i in range(40):
        s.add(models.StockSnapshot(
            stock_snapshot_id=f"s{i}", organization_id=org, product_id="p1",
            as_of=today - timedelta(days=i), on_hand=Decimal("10"),
            purchase_rate=Decimal("100"), tracked=True, source_ref={}))
    s.add(models.SalesTxn(
        sales_txn_id="t1", organization_id=org, external_ref="inv1:l1",
        customer_id="c1", product_id="p1", date=today - timedelta(days=5),
        qty=Decimal("1"), unit_price=Decimal("1500"),
        line_revenue=Decimal("1500"),
        source_ref={"system": "zoho", "record_type": "invoice",
                    "record_id": "inv1", "line_id": "l1"}))
    s.add(models.CostRecord(
        cost_record_id="k1", organization_id=org, external_ref="bill1:l1",
        product_id="p1", date=today - timedelta(days=50), qty=Decimal("50"),
        unit_cost=Decimal("800"), rate=Decimal("800"),
        source_ref={"system": "zoho", "record_type": "bill",
                    "record_id": "bill1"}))
    s.commit()


@pytest.fixture()
def api(request):
    """The insight router over a seeded org, with a book in it.

    Not `conftest.api_client`: that fixture owns its engine privately, so there
    is no way to put rows in the database the endpoint reads. Parameterised with
    `indirect` so one test can ask for the same wiring over an *empty* book —
    which is the case that has to answer "no stock reading" rather than 500.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.routers import insight, platform_auth
    from app.seed import ensure_org_and_users

    eng = dbsupport.fresh_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False,
                         future=True)
    s = maker()
    ensure_org_and_users(s)
    s.commit()
    # The business date, not the machine's — with the organization in
    # Asia/Kolkata the two disagree for five and a half hours of every day, and
    # a suite that goes red every evening in India is a suite people stop
    # reading. `clock.today` is what the endpoints under test ask for.
    th = load_for_org(s, SEED_ORG)
    today = clock.today(th.timezone)
    if getattr(request, "param", "seeded") == "seeded":
        _seed_book(s, today=today)
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _headers(api, role: str) -> dict[str, str]:
    token = api.post("/api/v1/auth/login", json={
        "email": LOGINS[role], "password": "change-me-now"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_the_endpoint_refuses_a_salesperson_outright(api):
    """Entirely margin ÷ cost: there is no version of this screen with the
    economics removed, so an honest 403 beats an empty page."""
    r = api.get("/api/v1/insight/gmroi", headers=_headers(api, "SALESPERSON"))
    assert r.status_code == 403, r.text
    # The *role* gate, asserted by name. `current_principal` also 403s an account
    # still holding the password it was issued, so a bare status check here would
    # pass just as happily against a seeded book where the role rule was gone.
    assert r.json()["detail"] == "Manager or owner role required"


@pytest.mark.parametrize("role", ["SALES_MANAGER", "OWNER"])
def test_a_manager_and_an_owner_both_get_the_figures(api, role):
    r = api.get("/api/v1/insight/gmroi", headers=_headers(api, role))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["measurable"] is True
    assert body["skus"][0]["gmroi"] == 0.7
    assert body["window"]["days_observed"] == 40
    # `_envelope` exists to stamp this on every manager-facing payload, so a
    # figure on screen can be traced to the policy that produced it.
    assert body["thresholds_version"]


def test_the_endpoint_reports_the_window_it_covered_not_the_one_asked_for(api):
    r = api.get("/api/v1/insight/gmroi?months=12",
                headers=_headers(api, "OWNER"))
    window = r.json()["window"]
    assert window["requested_days"] == 360
    assert window["span_days"] == 40
    assert window["covers_request"] is False
    assert window["shortfall"]


def test_the_brand_comes_through_the_cascade_rather_than_the_item_master(api):
    """`Kennametal` is on the item master and matches no vendor row here, so the
    cascade keeps it as a principal of its own under a `maker:` key — never a
    vendor id, and never dropped."""
    r = api.get("/api/v1/insight/gmroi", headers=_headers(api, "OWNER"))
    brand = r.json()["brands"][0]
    assert brand["principal_id"] == principals.maker_key("Kennametal")
    assert brand["label"] == "Kennametal"
    assert brand["source"] == principals.BY_MANUFACTURER


@pytest.mark.parametrize("api", ["empty"], indirect=True)
def test_a_book_with_no_stock_readings_says_that_is_what_is_missing(api):
    """Not "no sales history" — the advice that sent somebody to re-run a sync
    that had already worked."""
    r = api.get("/api/v1/insight/gmroi", headers=_headers(api, "OWNER"))
    assert r.status_code == 200, r.text
    assert "stock reading" in r.json()["empty_reason"]


# The other half of requirement 3 — that `/stock` carries `gmroi.withheld()` for
# a reader who cannot see cost — lives in `test_stock_on_state.py`. That screen
# renders from folded INVENTORY state, which is derived from the event log, so
# asserting on it needs the sync harness that file already owns. A second harness
# here would be a second answer to "what does a folded book look like".
