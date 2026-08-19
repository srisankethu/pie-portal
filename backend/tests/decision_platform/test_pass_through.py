"""Pass-through: the ratio, and the five refusals that make it worth reading.

The division itself is one line. Everything worth pinning is the refusal around
it, because every one of these cases has an arithmetic answer that looks
plausible, reads as good news, and would be produced by the obvious
implementation:

* **a near-zero cost move is refused, not divided by** — ₹0.40 of drift under a
  ₹12 price rise is a "pass-through" of 30 that sorts above every honest row;
* **one cost point is refused, not scored zero** — the supplier has not been
  observed to move, so there was nothing for a price to follow;
* **no cost record is refused, not scored 1.0** — reporting perfect pricing
  power on the platform's own ignorance is the failure `CLAUDE.md` records three
  times;
* **offsetting cost moves refuse the roll-up** — the exploding denominator
  returns one level up when a rise and a fall cancel, and the items must survive
  it;
* **volume is beside the ratio, never inside it** — a composite score would rank
  an account leaving politely beside one that held both.

Two more, and they are the ones a later change is most likely to break: the
aggregate is Σ price move ÷ Σ cost move and never the mean of the item ratios,
and there is no cost or margin field anywhere in the response.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.commercial import categories as cat
from app.commercial import metrics
from app.commercial.compute import compute_for
from app.commercial.config import CommercialThresholds
from app.commercial.economics import line_economics
from app.commercial.insight import absence, passthrough
from app.db import Base, get_session
from app.domain import models
from app.routers import insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.signals.base import CostRow, SaleRow

TH = CommercialThresholds()
AS_OF = date(2026, 7, 1)
ORG = "org_sanketh"


def _d(days_ago: int) -> date:
    return AS_OF - timedelta(days=days_ago)


# ── hand-built relationships, so every number in an assertion is checkable ───
def _sale(customer: str, product: str, day: date, qty: str, price: str,
          ref: str) -> SaleRow:
    q, p = Decimal(qty), Decimal(price)
    return SaleRow(customer_id=customer, product_id=product, date=day, qty=q,
                   unit_price=p, line_revenue=q * p,
                   source_ref={"record_type": "invoice", "record_id": ref},
                   external_ref=f"{ref}:1")


def _cost(product: str, day: date, unit_cost: str) -> CostRow:
    return CostRow(product_id=product, date=day, qty=Decimal("100"),
                   unit_cost=Decimal(unit_cost),
                   source_ref={"record_type": "bill",
                               "record_id": f"B{product}{day}"},
                   external_ref=f"B{product}{day}:1")


def _relationship(*, costs: list[CostRow], sales: list[SaleRow],
                  th: CommercialThresholds = TH) -> metrics.RelationshipMetrics:
    """One customer × item, costed through the platform's own economics."""
    ordered = sorted(costs, key=lambda c: c.date)
    lines = [line_economics(s, ordered) for s in sales]
    return metrics.compute_relationship(sales[0].customer_id,
                                        sales[0].product_id, lines, AS_OF, th)


def _baseline_and_recent(product: str, *, customer: str = "c1",
                         baseline_price: str, recent_price: str,
                         baseline_cost: str, recent_cost: str,
                         qty: str = "10") -> metrics.RelationshipMetrics:
    """Two costs and four sales — two in the baseline window, two in the recent.

    ``recent_days`` is 90 and the baseline reaches back to 730, so days 400/300
    sit in the baseline and days 40/20 in the recent window.
    """
    costs = [_cost(product, _d(500), baseline_cost),
             _cost(product, _d(120), recent_cost)]
    sales = [
        _sale(customer, product, _d(400), qty, baseline_price, f"{product}h1"),
        _sale(customer, product, _d(300), qty, baseline_price, f"{product}h2"),
        _sale(customer, product, _d(40), qty, recent_price, f"{product}r1"),
        _sale(customer, product, _d(20), qty, recent_price, f"{product}r2"),
    ]
    return _relationship(costs=costs, sales=sales)


def _item(m: metrics.RelationshipMetrics, *,
          category: str = cat.CUTTING_TOOLS) -> passthrough.Item:
    return passthrough.from_metrics(
        m, label=f"Item {m.product_id}",
        resolution=cat.Resolution(category, cat.BY_HSN))


def _build(items, th: CommercialThresholds = TH) -> dict:
    return passthrough.build(items, customer_names={"c1": "Acme Engineering"},
                             as_of=AS_OF, thresholds=th)


def _first(result: dict) -> dict:
    return result["customers"][0]


# ── the ratio ───────────────────────────────────────────────────────────────
def test_full_pass_through_is_one():
    """Cost up 20, price up 20 — the whole increase reached the customer."""
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="120",
                             baseline_price="150", recent_price="170")
    assert m.pass_through.measured
    assert m.pass_through.ratio == pytest.approx(1.0)


def test_half_the_cost_move_reaching_the_price_is_a_half():
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="120",
                             baseline_price="150", recent_price="160")
    assert m.pass_through.ratio == pytest.approx(0.5)


def test_a_price_taker_absorbs_the_whole_move_and_scores_zero():
    """Zero here is a *measurement*, and the one finding this view exists for.

    It must stay distinguishable from every refusal below, all of which also
    render as "no number" to a careless reader.
    """
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="120",
                             baseline_price="150", recent_price="150")
    assert m.pass_through.measured
    assert m.pass_through.ratio == pytest.approx(0.0)
    assert m.pass_through.reason is None


def test_a_price_rise_larger_than_the_cost_rise_is_reported_not_clipped():
    """Above 1.0 is a real finding — the increase used as cover for more."""
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="110",
                             baseline_price="150", recent_price="175")
    assert m.pass_through.ratio == pytest.approx(2.5)


def test_a_falling_cost_kept_is_a_negative_ratio_not_a_refusal():
    """Cost fell and the price held: none of the fall was given away.

    Signed on purpose. Taking an absolute value here would report giving a
    discount away and keeping one as the same number.
    """
    m = _baseline_and_recent("p1", baseline_cost="120", recent_cost="100",
                             baseline_price="170", recent_price="170")
    assert m.pass_through.measured
    assert m.pass_through.ratio == pytest.approx(0.0)

    given_away = _baseline_and_recent("p2", baseline_cost="120",
                                      recent_cost="100", baseline_price="170",
                                      recent_price="150")
    assert given_away.pass_through.ratio == pytest.approx(1.0)


# ── the degenerate cases, each refused rather than smoothed ─────────────────
def test_a_tiny_cost_move_refuses_instead_of_producing_a_huge_ratio():
    """₹0.50 on ₹100 under a ₹12 price rise is a 24x ratio, or a refusal.

    The refusal is the whole point: capping it at some maximum would still put
    it above every honest row on a page sorted by pass-through.
    """
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="100.50",
                             baseline_price="150", recent_price="162")
    assert not m.pass_through.measured
    assert m.pass_through.reason == metrics.COST_MOVE_IMMATERIAL
    assert m.pass_through.ratio is None, "no ratio at all, not a capped one"

    # And the naive arithmetic really would have been enormous — otherwise this
    # test would pass against an implementation that had nothing to refuse.
    naive = Decimal("12") / Decimal("0.50")
    assert naive > 20


def test_a_cost_move_just_over_the_floor_is_measured():
    """The floor must let real moves through, or it is a way of showing nothing."""
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="104",
                             baseline_price="150", recent_price="152")
    assert TH.pass_through_min_cost_move_pct == 0.03
    assert m.pass_through.measured
    assert m.pass_through.ratio == pytest.approx(0.5)


def test_a_single_cost_point_refuses_and_is_not_zero_pass_through():
    """One purchase price: nothing moved, so nothing failed to be passed on."""
    costs = [_cost("p1", _d(500), "100")]
    sales = [_sale("c1", "p1", _d(400), "10", "150", "h1"),
             _sale("c1", "p1", _d(300), "10", "150", "h2"),
             _sale("c1", "p1", _d(40), "10", "180", "r1"),
             _sale("c1", "p1", _d(20), "10", "180", "r2")]
    m = _relationship(costs=costs, sales=sales)
    assert m.pass_through.reason == metrics.SINGLE_COST_POINT
    assert m.pass_through.ratio is None
    assert m.pass_through.cost_points == 1


def test_two_bills_at_the_same_price_are_one_cost_point():
    """Cost *points*, not cost records. Two bills that agree moved nothing."""
    costs = [_cost("p1", _d(500), "100"), _cost("p1", _d(120), "100")]
    sales = [_sale("c1", "p1", _d(400), "10", "150", "h1"),
             _sale("c1", "p1", _d(40), "10", "180", "r1")]
    m = _relationship(costs=costs, sales=sales)
    assert m.pass_through.reason == metrics.SINGLE_COST_POINT


def test_no_cost_record_refuses_and_is_not_full_pass_through():
    """The absence-of-evidence case. 1.0 here would be the documented defect."""
    sales = [_sale("c1", "p1", _d(400), "10", "150", "h1"),
             _sale("c1", "p1", _d(40), "10", "180", "r1")]
    m = _relationship(costs=[], sales=sales)
    assert m.pass_through.reason == metrics.NO_COST_ON_RECORD
    assert m.pass_through.ratio is None
    assert m.pass_through.cost_move_value is None


def test_a_placeholder_zero_cost_is_not_a_cost():
    """``economics`` withholds a zero unit cost; the ratio must inherit that.

    A zero baseline would make every division infinite, and a zero treated as
    real would report a 100% margin on the same rows.
    """
    costs = [_cost("p1", _d(500), "0"), _cost("p1", _d(120), "120")]
    sales = [_sale("c1", "p1", _d(400), "10", "150", "h1"),
             _sale("c1", "p1", _d(40), "10", "180", "r1")]
    m = _relationship(costs=costs, sales=sales)
    assert m.pass_through.reason == metrics.NO_COST_ON_RECORD


def test_nothing_sold_recently_refuses_rather_than_reading_a_stale_price():
    """``current_sell_price`` falls back to the last line ever; this must not.

    That fallback exists so a dormant account can show a last-known position.
    Reading it here would compare a two-year-old price against a baseline window
    that contains it and call the difference pass-through.
    """
    costs = [_cost("p1", _d(500), "100"), _cost("p1", _d(400), "130")]
    sales = [_sale("c1", "p1", _d(380), "10", "150", "h1"),
             _sale("c1", "p1", _d(300), "10", "150", "h2")]
    m = _relationship(costs=costs, sales=sales)
    # NO_PRICE_IN_WINDOW, not NO_COST_ON_RECORD. Both cost records are on file;
    # what is absent is any recent sale to have passed a move through. Answering
    # "no cost on record" here sends somebody to look for a bill that is already
    # there — a COLLECTABLE worklist item with nothing collectable behind it.
    assert m.pass_through.reason == metrics.NO_PRICE_IN_WINDOW
    assert m.pass_through.ratio is None
    # The stale fallback really is populated — so the refusal above is a
    # decision this code made, not an accident of empty data.
    assert m.current_sell_price == Decimal("150")


def test_every_refusal_code_has_a_meaning_and_an_absence_kind():
    """A refusal a reader cannot classify is one they learn to scroll past."""
    for code in (metrics.NO_COST_ON_RECORD, metrics.SINGLE_COST_POINT,
                 metrics.COST_MOVE_IMMATERIAL, metrics.NO_PRICE_IN_WINDOW,
                 passthrough.NO_MEASURED_ITEM,
                 passthrough.OFFSETTING_COST_MOVES):
        assert code in passthrough.REASON_MEANING
        assert passthrough.REASON_KIND[code] in absence.KINDS


# ── money is Decimal ────────────────────────────────────────────────────────
def test_the_money_halves_are_decimal_end_to_end():
    m = _baseline_and_recent("p1", baseline_cost="100.05", recent_cost="120.15",
                             baseline_price="150", recent_price="170",
                             qty="7")
    pt = m.pass_through
    assert isinstance(pt.cost_move_per_unit, Decimal)
    assert isinstance(pt.price_move_per_unit, Decimal)
    assert isinstance(pt.cost_move_value, Decimal)
    assert isinstance(pt.price_move_value, Decimal)
    # 20.10 exactly — a float would already have drifted by here.
    assert pt.cost_move_per_unit == Decimal("20.10")
    assert pt.cost_move_value == Decimal("20.10") * Decimal("14")


def test_the_ratio_itself_is_a_ratio_not_a_percentage():
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="120",
                             baseline_price="150", recent_price="165")
    assert m.pass_through.ratio == pytest.approx(0.75)
    assert _item(m).to_dict()["pass_through"] == pytest.approx(0.75)


# ── the aggregation rule ────────────────────────────────────────────────────
def test_a_group_is_weighted_by_money_and_is_never_the_mean_of_the_ratios():
    """A ₹9-lakh item at 0.25 and a ₹900 item at 1.0 is not 0.625.

    Worse than usual on this ratio: the small item is systematically the one
    carrying the near-degenerate denominator, so the mean is not merely
    unweighted, it is biased toward the rows that mean least.
    """
    big = _baseline_and_recent("big", baseline_cost="100", recent_cost="200",
                               baseline_price="150", recent_price="175",
                               qty="1000")          # 0.25, ₹100,000 of cost move
    small = _baseline_and_recent("small", baseline_cost="100", recent_cost="200",
                                 baseline_price="150", recent_price="250",
                                 qty="10")           # 1.00, ₹1,000 of cost move
    assert big.pass_through.ratio == pytest.approx(0.25)
    assert small.pass_through.ratio == pytest.approx(1.0)

    group = _first(_build([_item(big), _item(small)]))["overall"]
    # Σ price move ÷ Σ cost move over the recent quantities (2 lines × qty each):
    #   (25×2000 + 100×20) ÷ (100×2000 + 100×20) = 52,000 ÷ 202,000
    assert group["pass_through"] == pytest.approx(0.2574, abs=1e-4)
    mean_of_ratios = (0.25 + 1.0) / 2
    assert group["pass_through"] != pytest.approx(mean_of_ratios, abs=1e-3)


def test_offsetting_cost_moves_refuse_the_roll_up_and_keep_the_items():
    """The exploding denominator, one level up. The facts survive; the ratio does not."""
    up = _baseline_and_recent("up", baseline_cost="100", recent_cost="150",
                              baseline_price="150", recent_price="190",
                              qty="10")
    down = _baseline_and_recent("down", baseline_cost="150", recent_cost="100",
                                baseline_price="200", recent_price="170",
                                qty="10")
    customer = _first(_build([_item(up), _item(down)]))

    assert customer["overall"]["pass_through"] is None
    assert customer["overall"]["reason"] == passthrough.OFFSETTING_COST_MOVES
    assert customer["overall"]["reason_kind"] == absence.PERMANENT
    # Gross movement is visible, so the cancellation is a fact on the page
    # rather than something a reader has to infer from an odd-looking ratio.
    assert customer["overall"]["gross_cost_move_value"] == pytest.approx(2000.0)
    assert customer["overall"]["cost_move_value"] == pytest.approx(0.0)
    # Both items keep their own measured figures — and the order is the margin
    # consequence, not the ratio. This fixture is the case that separates them:
    # "up" absorbed a ₹50 cost rise with a ₹40 price rise, giving away ₹10 a
    # unit (₹100 over the line) at a ratio of 0.8; "down" met a ₹50 cost fall
    # with a ₹30 price cut, KEEPING ₹20 a unit (₹200) at a ratio of 0.6. Sorting
    # ascending by ratio would head a "worst first" list with the line that made
    # money. So 0.8 before 0.6.
    assert [i["pass_through"] for i in customer["items"]] == \
        pytest.approx([0.8, 0.6])


def test_a_group_with_no_measurable_item_refuses_rather_than_reading_zero():
    single = _relationship(
        costs=[_cost("p1", _d(500), "100")],
        sales=[_sale("c1", "p1", _d(400), "10", "150", "h1"),
               _sale("c1", "p1", _d(40), "10", "180", "r1")])
    customer = _first(_build([_item(single)]))
    assert customer["overall"]["pass_through"] is None
    assert customer["overall"]["reason"] == passthrough.NO_MEASURED_ITEM
    assert customer["overall"]["reasons"] == {metrics.SINGLE_COST_POINT: 1}


def test_categories_come_from_the_catalogue_module_and_uncategorised_is_its_own_line():
    tools = _baseline_and_recent("t1", baseline_cost="100", recent_cost="120",
                                 baseline_price="150", recent_price="170")
    coolant = _baseline_and_recent("k1", baseline_cost="100", recent_cost="120",
                                   baseline_price="150", recent_price="150")
    unplaced = _baseline_and_recent("u1", baseline_cost="100", recent_cost="120",
                                    baseline_price="150", recent_price="160")

    customer = _first(_build([
        _item(tools, category=cat.CUTTING_TOOLS),
        _item(coolant, category=cat.COOLANTS),
        passthrough.from_metrics(unplaced, label="Unplaced",
                                 resolution=None),
    ]))
    by_key = {g["key"]: g for g in customer["categories"]}
    assert by_key[cat.CUTTING_TOOLS]["pass_through"] == pytest.approx(1.0)
    assert by_key[cat.COOLANTS]["pass_through"] == pytest.approx(0.0)
    # Not dropped and not swept into a line to make the grid look complete.
    assert by_key[cat.UNCATEGORISED]["pass_through"] == pytest.approx(0.5)
    assert by_key[cat.UNCATEGORISED]["items"] == 1
    # Reading order, with the unplaced line last.
    assert list(by_key)[-1] == cat.UNCATEGORISED


# ── volume beside the ratio, never inside it ────────────────────────────────
def test_volume_is_reported_beside_the_ratio_and_does_not_move_it():
    """Two accounts, identical pass-through, opposite volume stories.

    Full pass-through with the volume collapsing is not pricing power. The
    ratio must not know that, and the response must say it anyway.
    """
    holding = _baseline_and_recent("p1", baseline_cost="100", recent_cost="120",
                                   baseline_price="150", recent_price="170",
                                   qty="10")
    collapsing = _relationship(
        costs=[_cost("p2", _d(500), "100"), _cost("p2", _d(120), "120")],
        sales=[_sale("c1", "p2", _d(400), "100", "150", "h1"),
               _sale("c1", "p2", _d(300), "100", "150", "h2"),
               # previous window is the 90 days before the recent one
               _sale("c1", "p2", _d(150), "100", "150", "q1"),
               _sale("c1", "p2", _d(20), "5", "170", "r1")])

    assert holding.pass_through.ratio == pytest.approx(1.0)
    assert collapsing.pass_through.ratio == pytest.approx(1.0)

    rows = {i["product_id"]: i
            for i in _first(_build([_item(holding), _item(collapsing)]))["items"]}
    assert rows["p2"]["volume"]["change_pct"] == pytest.approx(-0.95)
    assert rows["p2"]["pass_through"] == pytest.approx(1.0), \
        "volume must not have been folded into the ratio"
    assert rows["p1"]["volume"]["change_pct"] is None, \
        "no previous-window quantity is unknown, not zero"


def test_there_is_no_single_pricing_power_score_anywhere_in_the_response():
    """The hidden-weight composite `weather.py` argues against, refused here.

    Asserted structurally rather than by reading the keys of one dict: a score
    added to a nested row would slip past a top-level check.
    """
    m = _baseline_and_recent("p1", baseline_cost="100", recent_cost="120",
                             baseline_price="150", recent_price="170")
    result = _build([_item(m)])

    banned = ("score", "index", "rating", "grade")
    found: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if any(word in key.lower() for word in banned):
                    found.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(result, "$")
    assert found == [], f"a composite score appeared at: {found}"


def test_the_volume_counts_use_the_platforms_own_materiality_floor():
    """A 3% quantity wobble is not a volume story, and this must not say it is."""
    wobble = _relationship(
        costs=[_cost("p1", _d(500), "100"), _cost("p1", _d(120), "120")],
        sales=[_sale("c1", "p1", _d(400), "100", "150", "h1"),
               _sale("c1", "p1", _d(150), "100", "150", "q1"),
               _sale("c1", "p1", _d(20), "103", "170", "r1")])
    volume = _first(_build([_item(wobble)]))["overall"]["volume"]
    assert TH.meaningful_volume_change_pct == 0.15
    assert (volume["items_flat"], volume["items_up"], volume["items_down"]) == (1, 0, 0)


# ── refusals the module states up front ─────────────────────────────────────
def test_elasticity_is_refused_permanently_and_says_why():
    entry = next(u for u in passthrough.unavailable()
                 if u["series"] == "price_elasticity_of_demand")
    assert entry["kind"] == absence.PERMANENT
    assert "negotiat" in entry["reason"]


def test_every_stated_refusal_carries_a_kind_from_the_closed_set():
    entries = passthrough.unavailable()
    assert len(entries) >= 3
    for entry in entries:
        assert entry["kind"] in absence.KINDS


def test_the_thresholds_that_place_the_floors_are_inside_the_version_hash():
    """Move a floor and the stamp moves, or last quarter's rows are unexplainable."""
    loosened = CommercialThresholds(pass_through_min_cost_move_pct=0.10)
    assert loosened.version != TH.version
    assert _build([], loosened)["thresholds_version"] == loosened.version


# ── the endpoint ────────────────────────────────────────────────────────────
def _seed(s) -> None:
    """One account that passes cost on, one that cannot, and one it refuses on."""
    s.add(models.Customer(customer_id="c1", organization_id=ORG,
                          external_id="c1", name="Acme Engineering"))
    s.add(models.Customer(customer_id="c2", organization_id=ORG,
                          external_id="c2", name="Beta Works"))
    for pid, hsn in (("p1", "82090010"), ("p2", "34031900")):
        s.add(models.Product(product_id=pid, organization_id=ORG,
                             external_id=f"ITEM-{pid}", name=f"Item {pid}",
                             uom="pcs", hsn=hsn))
    for pid, early, late in (("p1", "100", "120"), ("p2", "100", "120")):
        for i, (day, unit) in enumerate(((_d(500), early), (_d(120), late))):
            s.add(models.CostRecord(
                organization_id=ORG, external_ref=f"B{pid}{i}:1", product_id=pid,
                date=day, qty=Decimal("100"), unit_cost=Decimal(unit),
                source_ref={"record_type": "bill", "record_id": f"B{pid}{i}"}))

    def sale(cust, pid, days, qty, price, ref):
        q, p = Decimal(str(qty)), Decimal(str(price))
        s.add(models.SalesTxn(
            organization_id=ORG, external_ref=f"{ref}:1", customer_id=cust,
            product_id=pid, date=_d(days), qty=q, unit_price=p,
            line_revenue=q * p, rate=p, discount_percent=Decimal("0"),
            source_ref={"record_type": "invoice", "record_id": ref}))

    # c1 passes the whole increase on; c2 absorbs it.
    for i, days in enumerate((400, 300)):
        sale("c1", "p1", days, 10, 150, f"A-H{i}")
        sale("c2", "p2", days, 10, 150, f"B-H{i}")
    for i, days in enumerate((40, 20)):
        sale("c1", "p1", days, 10, 170, f"A-R{i}")
        sale("c2", "p2", days, 10, 150, f"B-R{i}")
    s.flush()


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
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
    tc = TestClient(app)
    tc.Maker = Maker
    return tc


def _hdr(c, email):
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_salesperson_cannot_reach_the_endpoint_at_all(client):
    """No projection with the middle taken out — the ratio *is* the economics."""
    r = client.get("/api/v1/insight/pass-through",
                   headers=_hdr(client, "r.nair@pie.example"))
    assert r.status_code == 403, r.text


def test_a_manager_gets_the_ratio_and_the_response_carries_no_cost_or_margin(client):
    r = client.get("/api/v1/insight/pass-through",
                   headers=_hdr(client, "m.rao@pie.example"))
    assert r.status_code == 200, r.text
    body = r.json()

    by_customer = {c["customer_id"]: c for c in body["customers"]}
    assert by_customer["c1"]["overall"]["pass_through"] == pytest.approx(1.0)
    assert by_customer["c2"]["overall"]["pass_through"] == pytest.approx(0.0)
    # Worst first: the price-taker is the conversation that is owed.
    assert body["customers"][0]["customer_id"] == "c2"
    assert body["thresholds_version"] == body["thresholds_version"]
    assert body["thresholds_version"].startswith("ci_")

    # No cost or margin field anywhere, at any depth. A movement is not a level:
    # `cost_move_value` is a difference over a window and does not give the
    # purchase price, which is what the rule is about.
    banned = ("unit_cost", "cogs", "margin", "gross_profit", "purchase_rate",
              "effective_cost", "baseline_cost")
    offenders: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if any(word in key.lower() for word in banned):
                    offenders.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(body, "$")
    assert offenders == [], f"cost/margin reached the response at: {offenders}"


def test_the_endpoint_can_be_bounded_to_one_customer(client):
    r = client.get("/api/v1/insight/pass-through?customer_id=c1",
                   headers=_hdr(client, "m.rao@pie.example"))
    assert r.status_code == 200, r.text
    assert [c["customer_id"] for c in r.json()["customers"]] == ["c1"]


def test_the_bounded_answer_matches_the_unbounded_one_for_that_customer(client):
    """A bound is a claim that the excluded rows could not change the answer."""
    hdr = _hdr(client, "m.rao@pie.example")
    whole = client.get("/api/v1/insight/pass-through", headers=hdr).json()
    one = client.get("/api/v1/insight/pass-through?customer_id=c1",
                     headers=hdr).json()
    mine = next(c for c in whole["customers"] if c["customer_id"] == "c1")
    assert one["customers"][0] == mine


def test_the_view_reads_the_same_relationships_the_recompute_does(client):
    """One computation of a relationship, not two that could drift apart."""
    s = client.Maker()
    computed, _ = compute_for(s, ORG, as_of=AS_OF, with_benchmarks=False)
    ratios = {c.metrics.customer_id: c.metrics.pass_through.ratio
              for c in computed}
    s.close()
    assert ratios == {"c1": pytest.approx(1.0), "c2": pytest.approx(0.0)}


def test_switching_benchmarks_off_does_not_change_the_relationship_metrics(client):
    """The parameter must be a cost saving, never a different answer."""
    s = client.Maker()
    with_bm, _ = compute_for(s, ORG, as_of=AS_OF, with_benchmarks=True)
    without, _ = compute_for(s, ORG, as_of=AS_OF, with_benchmarks=False)
    s.close()
    assert [c.metrics for c in with_bm] == [c.metrics for c in without]
    assert all(c.benchmark is None for c in without)
