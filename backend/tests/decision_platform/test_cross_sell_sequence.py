"""Ordering the cross-sell gaps by the order the book actually took its lines.

The arithmetic is two counters and a median, and it is not what these tests are
for. What is pinned here is every place the feature could quietly become
something it must not be:

* a gap is **not money** — no revenue, opportunity or value field is allowed to
  appear anywhere in the ranking, and that is checked structurally over the
  whole payload rather than by reading the code;
* **LAPSED still outranks NEVER**, whatever the sequence evidence says. The line
  was already approved once, and no co-occurrence share is allowed to overrule
  the grid's strongest cell;
* below the base-size floor the answer is **no ranking with the reason
  attached**, never a share computed over three customers. An empty list is
  fixed by more trade, never by a lower floor;
* **uncategorised is on neither side of any ratio** — an item nothing could
  place is not evidence about a customer;
* a same-day adoption is **counted in the denominator**, because dropping it
  would inflate every share by the share of customers the house sold a basket
  to.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.commercial import categories as cat
from app.commercial.config import CommercialThresholds
from app.commercial.insight import adoption, mix
from app.domain import models

TH = CommercialThresholds()
AS_OF = date(2026, 8, 7)
LINES = list(cat.ORDER)
COLUMNS = [mix.Column(c, cat.LABELS[c]) for c in LINES]


def _line(customer: str, category: str, day: date, amount: float = 1000.0):
    return mix.MixLine(customer_id=customer, date=day, amount=amount,
                       key=category)


def _grid(lines, **kw) -> dict:
    kw.setdefault("columns", COLUMNS)
    return mix.build(lines, {}, AS_OF, thresholds=TH, **kw)


def _ranked(lines, **kw) -> tuple[dict, dict]:
    grid = _grid(lines, **kw)
    return grid, adoption.rank(grid, thresholds=TH)


def _row(grid: dict, customer: str) -> dict:
    return next(c for c in grid["customers"] if c["customer_id"] == customer)


def _pair(summary: dict, anchor: str, target: str) -> dict:
    return next(p for p in summary["pairs"]
                if p["from"] == anchor and p["to"] == target)


def _book(*, adopters: int, first: str, second: str,
          reversed_customers: int = 0, same_day: int = 0,
          live: date = date(2026, 6, 1)) -> list[mix.MixLine]:
    """A base that took ``first`` and then ``second``, plus the exceptions.

    Every customer is left holding both lines inside the window, so the grid
    calls both BUYS and nothing in these fixtures depends on the lapse rule
    except the tests that are about it.
    """
    lines: list[mix.MixLine] = []
    for i in range(adopters):
        lines += [_line(f"a{i}", first, date(2023, 1, 10)),
                  _line(f"a{i}", second, date(2023, 7, 10)),
                  _line(f"a{i}", first, live), _line(f"a{i}", second, live)]
    for i in range(reversed_customers):
        lines += [_line(f"r{i}", second, date(2023, 1, 10)),
                  _line(f"r{i}", first, date(2023, 7, 10)),
                  _line(f"r{i}", first, live), _line(f"r{i}", second, live)]
    for i in range(same_day):
        lines += [_line(f"s{i}", first, date(2023, 4, 1)),
                  _line(f"s{i}", second, date(2023, 4, 1)),
                  _line(f"s{i}", first, live), _line(f"s{i}", second, live)]
    return lines


# ── the statistic itself ─────────────────────────────────────────────────────
def test_the_order_the_base_took_two_lines_in_is_counted_both_ways():
    """The one thing lift cannot say. P(B|A)/P(B) is the same figure in both
    directions; which line came first is not."""
    _, seq = _ranked(_book(adopters=9, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS, reversed_customers=1))
    forward = _pair(seq, cat.CUTTING_TOOLS, cat.COOLANTS)
    back = _pair(seq, cat.COOLANTS, cat.CUTTING_TOOLS)

    assert forward["both"] == back["both"] == 10
    assert forward["after"] == 9 and forward["before"] == 1
    assert back["after"] == 1 and back["before"] == 9
    assert forward["after_share"] == pytest.approx(0.9)
    assert back["after_share"] == pytest.approx(0.1)
    assert forward["direction"] == adoption.FOLLOWS
    assert back["direction"] == adoption.NO_ORDER


def test_the_interval_is_the_median_of_the_customers_who_did_take_it():
    """Median, not mean: one customer who came back after four years would
    otherwise set the typical interval for the whole book."""
    _, seq = _ranked(_book(adopters=8, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS))
    pair = _pair(seq, cat.CUTTING_TOOLS, cat.COOLANTS)
    # 2023-01-10 → 2023-07-10, every adopter.
    assert pair["median_days"] == pytest.approx(181.0)


def test_a_same_day_adoption_counts_against_the_share_rather_than_vanishing():
    """Both lines on one invoice is *no order observed*, and it belongs in the
    denominator. Dropping it would raise every share by exactly the share of
    customers the house sold a basket to — which on this book is not noise."""
    _, seq = _ranked(_book(adopters=6, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS, same_day=6))
    pair = _pair(seq, cat.CUTTING_TOOLS, cat.COOLANTS)
    assert pair["both"] == 12
    assert pair["same_day"] == 6
    assert pair["after_share"] == pytest.approx(0.5)
    # Half in one direction and half in no direction at all is not an order.
    assert pair["direction"] == adoption.NO_ORDER


def test_a_measured_zero_is_a_finding_and_a_thin_base_is_not():
    """The distinction CLAUDE.md calls absence of evidence. 0.0 means the base
    never took them in that order; None means nobody counted."""
    _, seq = _ranked(_book(adopters=10, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS))
    back = _pair(seq, cat.COOLANTS, cat.CUTTING_TOOLS)
    assert back["after_share"] == 0.0
    assert back["direction"] == adoption.NO_ORDER

    thin_grid, thin = _ranked(_book(adopters=3, first=cat.CUTTING_TOOLS,
                                    second=cat.COOLANTS))
    assert thin["pairs"] == []
    assert thin["measurable"] is False
    gap = next(g for g in _row(thin_grid, "a0")["gaps"]
               if g["category"] == cat.METROLOGY)
    assert gap["sequence"] is None


def test_a_customer_holding_one_line_is_in_no_denominator():
    """They have held no pair, so they cannot be evidence about one. Counting
    them would depress every share by the size of the single-line tail."""
    lines = _book(adopters=8, first=cat.CUTTING_TOOLS, second=cat.COOLANTS)
    lines += [_line(f"single{i}", cat.CUTTING_TOOLS, date(2026, 6, 1))
              for i in range(20)]
    _, seq = _ranked(lines)
    assert seq["customers_compared"] == 8
    assert _pair(seq, cat.CUTTING_TOOLS, cat.COOLANTS)["both"] == 8


def test_a_line_taken_and_lost_still_counts_in_the_order_it_was_taken():
    """A first purchase is a historical fact a later lapse does not unmake, and
    the customers whose whole story is a line taken and lost are exactly the
    ones an adoption sequence is about."""
    lines = []
    for i in range(8):
        lines += [_line(f"c{i}", cat.CUTTING_TOOLS, date(2023, 1, 10)),
                  # Taken second, and stopped — LAPSED on the grid.
                  _line(f"c{i}", cat.COOLANTS, date(2023, 7, 10)),
                  _line(f"c{i}", cat.CUTTING_TOOLS, date(2026, 6, 1))]
    grid, seq = _ranked(lines)
    cell = next(c for c in _row(grid, "c0")["cells"]
                if c["category"] == cat.COOLANTS)
    assert cell["state"] == mix.LAPSED
    assert _pair(seq, cat.CUTTING_TOOLS, cat.COOLANTS)["after"] == 8


def test_the_window_does_not_truncate_a_first_purchase():
    """`months` decides what BUYS means and must not touch this: a first
    purchase clipped by a window is not a first purchase."""
    lines = _book(adopters=8, first=cat.CUTTING_TOOLS, second=cat.COOLANTS)
    for months in (3, 12, 36):
        _, seq = _ranked(lines, months=months)
        assert _pair(seq, cat.CUTTING_TOOLS, cat.COOLANTS)["after"] == 8, months


# ── the evidence floor ───────────────────────────────────────────────────────
def test_below_the_base_floor_it_refuses_rather_than_ranking():
    """A co-occurrence measured over three customers is a coincidence. The
    answer is no ranking with the floor and the best base attached."""
    _, seq = _ranked(_book(adopters=4, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS))
    assert seq["measurable"] is False
    assert seq["pairs"] == []
    assert seq["below_floor"]["min_base_customers"] == TH.crosssell_min_base_customers
    assert seq["below_floor"]["largest_base_excluded"] == 4
    # The reason names the gap between what there is and what is needed, so
    # nobody goes looking for a bug and finds the floor on the second try.
    assert str(TH.crosssell_min_base_customers) in seq["reason"]
    assert "lower floor does not" in seq["reason"]


def test_an_unheld_pair_is_told_apart_from_a_thinly_held_one():
    """A book where most pairs were never held by anybody is not a book with a
    floor set too high — it is a book whose customers each take one line, and
    the two want different responses."""
    _, seq = _ranked(_book(adopters=4, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS))
    floor = seq["below_floor"]
    assert floor["pairs_possible"] == len(LINES) * (len(LINES) - 1)
    # Only the one pair anybody holds both halves of, in both directions.
    assert floor["pairs_observed"] == 2
    assert floor["pairs_below_floor"] == 2


def test_the_floor_lives_in_the_thresholds_and_moves_the_version():
    """Policy inside the version hash, so a ranking says what rule produced it —
    and not in `policy.EDITABLE`, so an empty screen cannot be fixed from
    Settings by lowering the bar it failed."""
    from app.commercial import policy

    loosened = CommercialThresholds(crosssell_min_base_customers=3)
    assert loosened.version != TH.version
    assert "crosssell_min_base_customers" not in policy.EDITABLE
    assert "crosssell_min_sequence_share" not in policy.EDITABLE

    _, seq = _ranked(_book(adopters=4, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS))
    assert seq["measurable"] is False
    grid = _grid(_book(adopters=4, first=cat.CUTTING_TOOLS,
                       second=cat.COOLANTS))
    assert adoption.rank(grid, thresholds=loosened)["measurable"] is True


def test_an_empty_book_says_which_of_three_things_is_missing():
    """Three ways to rank nothing, and they send a reader to three different
    places — the `mix._empty_reason` incident, not repeated here."""
    one_line = adoption.rank(_grid([_line("c1", cat.CUTTING_TOOLS,
                                          date(2026, 6, 1))]), thresholds=TH)
    assert "No customer on this book" in one_line["reason"]

    no_columns = adoption.rank(
        mix.build([_line("c1", cat.UNCATEGORISED, date(2026, 6, 1))], {},
                  AS_OF, thresholds=TH, columns=[]), thresholds=TH)
    assert "Fewer than two lines" in no_columns["reason"]


# ── the ordering ─────────────────────────────────────────────────────────────
def test_a_lapsed_gap_still_outranks_a_never_gap_with_a_stronger_sequence():
    """The rule this feature was most likely to break. A line they already
    bought was approved, went through goods inward and stopped; no
    co-occurrence share is allowed to demote that below a line nobody has ever
    ordered."""
    lines = _book(adopters=10, first=cat.CUTTING_TOOLS, second=cat.METROLOGY)
    # The customer under test: holds cutting tools, lapsed out of coolants,
    # never took metrology — which is the line with the strong sequence.
    lines += [_line("target", cat.CUTTING_TOOLS, date(2026, 6, 1)),
              _line("target", cat.COOLANTS, date(2021, 3, 1))]
    grid, seq = _ranked(lines)

    gaps = _row(grid, "target")["gaps"]
    assert gaps[0]["category"] == cat.COOLANTS
    assert gaps[0]["state"] == mix.LAPSED
    # And the strong sequence did rank — it is simply ranked below the lapse.
    metrology = next(g for g in gaps if g["category"] == cat.METROLOGY)
    assert metrology["sequence"]["directional"] is True
    assert gaps.index(metrology) > 0


def test_a_gap_the_base_adopts_in_order_leads_one_it_says_nothing_about():
    lines = _book(adopters=10, first=cat.CUTTING_TOOLS, second=cat.COOLANTS)
    # A second line taken in no consistent order: half before, half after.
    for i in range(10):
        day = date(2022, 1, 1) if i % 2 else date(2024, 1, 1)
        lines += [_line(f"a{i}", cat.CONSUMABLES, day),
                  _line(f"a{i}", cat.CONSUMABLES, date(2026, 6, 1))]
    lines.append(_line("target", cat.CUTTING_TOOLS, date(2026, 6, 1)))
    grid, _ = _ranked(lines)

    gaps = [g["category"] for g in _row(grid, "target")["gaps"]]
    assert gaps.index(cat.COOLANTS) < gaps.index(cat.CONSUMABLES)


def test_a_gap_names_the_line_its_sequence_was_measured_from():
    """"Usually taken after" is meaningless without "after what". The anchor is
    a line this customer actually holds, so the sentence is about them."""
    lines = _book(adopters=10, first=cat.CUTTING_TOOLS, second=cat.COOLANTS)
    lines.append(_line("target", cat.CUTTING_TOOLS, date(2026, 6, 1)))
    grid, _ = _ranked(lines)

    gap = next(g for g in _row(grid, "target")["gaps"]
               if g["category"] == cat.COOLANTS)
    assert gap["sequence"]["from"] == cat.CUTTING_TOOLS
    assert gap["sequence"]["from_label"] == cat.LABELS[cat.CUTTING_TOOLS]
    assert gap["sequence"]["after"] == 10
    assert gap["sequence"]["both"] == 10


def test_a_gap_with_no_measurable_anchor_keeps_the_position_the_grid_gave_it():
    """A gap this module can say nothing about is not shuffled to the bottom —
    it keeps `mix`'s own lift-and-confidence ordering."""
    lines = _book(adopters=10, first=cat.CUTTING_TOOLS, second=cat.COOLANTS)
    lines.append(_line("target", cat.CUTTING_TOOLS, date(2026, 6, 1)))
    grid, _ = _ranked(lines)
    gaps = _row(grid, "target")["gaps"]
    unmeasured = [g for g in gaps if g["sequence"] is None]
    assert unmeasured, "expected the untraded lines to have no sequence"
    assert all(g["state"] == mix.NEVER for g in unmeasured)


# ── what it will not say ─────────────────────────────────────────────────────
#: Every word that would turn an ordering into a valuation. Matched against
#: every key at every depth of the payload.
MONEY_WORDS = ("revenue", "opportunity", "value", "amount", "spend", "impact",
               "potential", "worth", "margin", "cost", "price", "profit")


def _keys(node, path="") -> list[str]:
    if isinstance(node, dict):
        out = []
        for k, v in node.items():
            out.append(f"{path}.{k}")
            out += _keys(v, f"{path}.{k}")
        return out
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _keys(v, f"{path}[{i}]")]
    return []


def test_no_money_field_appears_anywhere_in_the_ranking():
    """The one change that would turn this into a forecast: multiply a gap by
    what comparable customers spend and sort by rupees. There is no field here
    it could be smuggled into, and this walks the whole payload rather than the
    top level so a nested one cannot slip past."""
    _, seq = _ranked(_book(adopters=10, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS))
    offenders = [k for k in _keys(seq)
                 if any(w in k.rsplit(".", 1)[-1].lower() for w in MONEY_WORDS)]
    assert not offenders, offenders


def test_the_gap_annotation_carries_no_money_either():
    lines = _book(adopters=10, first=cat.CUTTING_TOOLS, second=cat.COOLANTS)
    lines.append(_line("target", cat.CUTTING_TOOLS, date(2026, 6, 1)))
    grid, _ = _ranked(lines)
    gap = next(g for g in _row(grid, "target")["gaps"]
               if g["category"] == cat.COOLANTS)
    offenders = [k for k in _keys(gap["sequence"])
                 if any(w in k.rsplit(".", 1)[-1].lower() for w in MONEY_WORDS)]
    assert not offenders, offenders


def test_it_refuses_to_say_what_a_gap_is_worth_or_that_order_is_cause():
    reasons = adoption.unavailable()
    assert any("worth" in r["what"].lower() for r in reasons)
    assert any("cause" in r["what"].lower() for r in reasons)
    assert any("forecast" in r["why"] for r in reasons)


def test_uncategorised_is_on_neither_side_of_any_ratio():
    """An item nothing could place is not evidence that a customer does or does
    not take a line. It is not a column, not an anchor and not a denominator."""
    lines = _book(adopters=10, first=cat.CUTTING_TOOLS, second=cat.COOLANTS)
    for i in range(10):
        lines.append(_line(f"a{i}", cat.UNCATEGORISED, date(2022, 1, 1)))
    grid, seq = _ranked(lines)

    keys = {p["from"] for p in seq["pairs"]} | {p["to"] for p in seq["pairs"]}
    assert cat.UNCATEGORISED not in keys
    assert all(g["category"] != cat.UNCATEGORISED
               for row in grid["customers"] for g in row["gaps"])
    # And it did not sneak into a denominator either: the pair's base is the
    # ten customers who hold both real lines, not eleven columns' worth.
    assert _pair(seq, cat.CUTTING_TOOLS, cat.COOLANTS)["both"] == 10


def test_the_ranking_carries_the_version_that_produced_it():
    _, seq = _ranked(_book(adopters=10, first=cat.CUTTING_TOOLS,
                           second=cat.COOLANTS))
    assert seq["thresholds_version"] == TH.version


# ── the wiring, not the builder ──────────────────────────────────────────────
@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.config import settings
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
    org = settings.DEFAULT_ORG_ID
    # Two lines the tariff code places on its own, so the grid has two columns
    # without needing a category override.
    s.add(models.Product(product_id="p-tool", organization_id=org,
                         external_id="e-tool", name="CNMG 120408",
                         hsn="82071900", active=True, source_ref={}))
    s.add(models.Product(product_id="p-cool", organization_id=org,
                         external_id="e-cool", name="Cutting oil",
                         hsn="34031900", active=True, source_ref={}))
    s.flush()
    # Ten customers who took tools first and coolant six months later, then
    # bought both again inside the window.
    for i in range(10):
        for pid, when in (("p-tool", date(2023, 1, 10)),
                          ("p-cool", date(2023, 7, 10)),
                          ("p-tool", date(2026, 6, 1)),
                          ("p-cool", date(2026, 6, 1))):
            s.add(models.SalesTxn(
                organization_id=org, external_ref=f"inv-{i}-{pid}-{when}",
                customer_id=f"c{i}", product_id=pid, date=when,
                qty=Decimal("1"), unit_price=Decimal("100"),
                line_revenue=Decimal("100"),
                source_ref={"record_id": f"inv-{i}-{pid}-{when}"}))
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
    return TestClient(app)


def _auth(client, email):
    from app.seed import SEED_PASSWORD
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_ordering_reaches_the_grid_over_http(client):
    """The builder is exercised above; this is the call site.

    Deliberately the shallowest possible assertion on the wiring, for the reason
    `test_daily` gives: a builder tested through its own signature cannot fail
    the way a call site fails."""
    head = _auth(client, "m.rao@pie.example")
    body = client.get("/api/v1/insight/mix", headers=head).json()

    assert body["cross_sell"]["measurable"] is True
    pair = _pair(body["cross_sell"], cat.CUTTING_TOOLS, cat.COOLANTS)
    assert pair["after"] == 10 and pair["direction"] == adoption.FOLLOWS
    # Both modules' refusals ride along, because the screen now makes both
    # kinds of claim.
    assert any("worth" in r["what"].lower() for r in body["unavailable"])


def test_a_salesperson_gets_the_ordering_and_still_no_money(client):
    """A line-of-business adoption pattern is not cost or margin, and the whole
    point of the grid is a conversation a salesperson has. What must hold is
    that nothing in the block answers a margin question — checked over the
    payload the salesperson actually receives."""
    head = _auth(client, "r.nair@pie.example")
    r = client.get("/api/v1/insight/mix", headers=head)
    assert r.status_code == 200, r.text
    block = r.json()["cross_sell"]

    assert block["measurable"] is True
    offenders = [k for k in _keys(block)
                 if any(w in k.rsplit(".", 1)[-1].lower() for w in MONEY_WORDS)]
    assert not offenders, offenders
