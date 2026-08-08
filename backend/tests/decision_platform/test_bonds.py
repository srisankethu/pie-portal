"""Relationship bond strength: the arithmetic, and the refusals around it.

The arithmetic half is ordinary — a decaying recency, a saturating breadth, a
weighted mean. The half that matters is the second: a composite score is the
easiest thing in this codebase to quietly make dishonest, because every
dishonest version makes the screen look better. So the claims pinned here are
the ones a later change is most likely to erode:

* a relationship below the evidence floor gets **no score**, in the list *and*
  in every frame — a play that scores somebody the list beside it refuses to
  score is a screen disagreeing with itself;
* a facet that cannot be measured is **dropped and named**, never read as zero,
  because "we have no due dates for them" is not "they pay late";
* ``overdue`` comes from the **dormancy detector's own rule**, not from a score
  cut-off, so this screen and the decision queue can never disagree about who
  has gone quiet;
* a counterparty is **absent** from the frames before they first traded, rather
  than present at zero, which would draw a bond decaying before anybody met;
* the supplier half is **absent from a salesperson's response**, not hidden in
  it — spend is purchase cost by another name.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.commercial import categories
from app.commercial.config import CommercialThresholds
from app.commercial.insight import bonds, payments
from app.db import Base, get_session
from app.routers import insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.signals.config import SignalThresholds

AS_OF = date(2026, 8, 7)

#: The seeded accounts these role tests sign in as. Named rather than
#: repeated, so a change to the seed is one edit and not four.
SALESPERSON = "r.nair@sanketh.in"
MANAGER = "m.rao@sanketh.in"

#: The lines of the business, and the one most of these fixtures trade in.
FIVE_LINES = list(categories.ORDER)
CUTTING = categories.CUTTING_TOOLS
TH = CommercialThresholds()
SIG = SignalThresholds()


def _lines(party: str, *, count: int, every_days: int = 30, amount: float = 5000.0,
           items: int = 1, start: date = date(2024, 9, 1),
           category: str | None = CUTTING) -> list[bonds.TradeLine]:
    """A counterparty trading on a fixed rhythm, across ``items`` products.

    ``category`` is the line of the business those products belong to — one by
    default, because most of these tests are about rhythm rather than mix and a
    fixture that spread across five lines would make every bond look broad.
    """
    out: list[bonds.TradeLine] = []
    for i in range(count):
        day = start + timedelta(days=every_days * i)
        for k in range(items):
            out.append(bonds.TradeLine(party, day, amount, f"{category}-item{k}",
                                       f"{party}-{i}", category=category))
    return out


def _build(lines, names=None, **kw) -> dict:
    kw.setdefault("categories_sold", FIVE_LINES)
    return bonds.build(lines, names or {}, AS_OF, side=bonds.CUSTOMER,
                       thresholds=TH, signal_thresholds=SIG, **kw)


def _bond(result: dict, party: str) -> dict:
    return next(b for b in result["bonds"] if b["counterparty_id"] == party)


def _track(result: dict, party: str) -> list:
    """One counterparty's score in each frame, absent frames as ``None``."""
    return [next((e["score"] for e in f["bonds"]
                  if e["counterparty_id"] == party), None)
            for f in result["frames"]]


# ── the facets ───────────────────────────────────────────────────────────────
def test_recency_is_full_marks_anywhere_inside_their_own_rhythm():
    """A quarterly buyer eighty days in is not weakening, and a fixed
    days-since threshold is exactly what would say they were."""
    assert bonds.recency_of(0.4) == 1.0
    assert bonds.recency_of(1.0) == 1.0


def test_recency_decays_rather_than_falling_off_a_cliff():
    assert bonds.recency_of(2.0) == pytest.approx(0.5)
    assert bonds.recency_of(4.0) == pytest.approx(0.25)
    # Monotonic: further past their rhythm is never a stronger bond.
    ratios = [1.0, 1.5, 2.0, 3.0, 8.0]
    scores = [bonds.recency_of(r) for r in ratios]
    assert scores == sorted(scores, reverse=True)


def test_recency_is_unknown_rather_than_zero_without_a_rhythm():
    """``None`` and 0.0 are different claims. No rhythm is not 'never buys'."""
    assert bonds.recency_of(None) is None


def test_breadth_counts_lines_of_the_business_not_skus():
    """The correction that made this facet serve the mix goal.

    It counted distinct items first, which scored twelve cutting-tool inserts
    above four items spread across four lines — backwards for a distributor
    trying to grow mix, because the second customer is the one a competitor has
    to beat four times.
    """
    assert bonds.breadth_of(1, 5) == pytest.approx(0.2)
    assert bonds.breadth_of(4, 5) == pytest.approx(0.8)
    assert bonds.breadth_of(5, 5) == pytest.approx(1.0)


def test_many_skus_in_one_line_is_still_a_narrow_relationship():
    """The whole point, stated end to end rather than on the helper."""
    wide = _build(_lines("wide", count=8, items=1, category=CUTTING),
                  categories_sold=FIVE_LINES)
    deep = _build(_lines("deep", count=8, items=20, category=CUTTING),
                  categories_sold=FIVE_LINES)
    assert (_bond(wide, "wide")["facets"]["breadth"]
            == _bond(deep, "deep")["facets"]["breadth"]), (
        "twenty SKUs in one line is not broader than one SKU in that line")


def test_taking_more_lines_raises_breadth():
    spread: list[bonds.TradeLine] = []
    for i, line in enumerate(FIVE_LINES[:4]):
        spread += _lines("spread", count=2, category=line,
                         start=date(2025, 1, 1 + i))
    result = _build(spread, categories_sold=FIVE_LINES)
    assert _bond(result, "spread")["facets"]["breadth"] == pytest.approx(0.8)
    assert _bond(result, "spread")["categories_sold"] == 5


def test_breadth_is_unknown_rather_than_zero_when_nothing_is_categorised():
    """A gap in the catalogue is not a narrow customer, and scoring it as one
    would blame somebody for the platform's own missing data."""
    assert bonds.breadth_of(0, 0) is None
    result = _build(_lines("a", count=8, category=None), categories_sold=[])
    assert "breadth" in _bond(result, "a")["missing_facets"]


def test_an_uncategorised_item_is_excluded_rather_than_counted_against_anybody():
    known = _build(_lines("a", count=8, category=CUTTING),
                   categories_sold=FIVE_LINES)
    with_junk = _build(_lines("a", count=8, category=CUTTING)
                       + _lines("a", count=4, category=categories.UNCATEGORISED,
                                start=date(2025, 6, 1)),
                       categories_sold=FIVE_LINES)
    assert (_bond(known, "a")["facets"]["breadth"]
            == _bond(with_junk, "a")["facets"]["breadth"])


def test_the_denominator_is_what_the_business_sells_not_what_it_happened_to_sell():
    """Deriving it from the data would make the worst-mix book look perfect."""
    only_one_line = _lines("a", count=8, category=CUTTING)
    declared = _build(only_one_line, categories_sold=FIVE_LINES)
    assert _bond(declared, "a")["facets"]["breadth"] == pytest.approx(0.2)


def test_weight_saturates_at_a_tenth_of_the_book():
    assert bonds.weight_of(0.10) == pytest.approx(1.0)
    assert bonds.weight_of(0.20) == pytest.approx(1.0)
    assert bonds.weight_of(0.05) == pytest.approx(0.5)
    assert bonds.weight_of(None) is None


def test_consistency_counts_only_months_they_could_have_traded_in():
    """Measured from their first document, not from the start of the window —
    a customer who first bought two months ago has not missed the ten before."""
    assert bonds.consistency_of(2, 2) == pytest.approx(1.0)
    assert bonds.consistency_of(2, 24) == pytest.approx(2 / 24)
    assert bonds.consistency_of(0, 0) is None


# ── the composite ────────────────────────────────────────────────────────────
def test_a_missing_facet_is_dropped_and_renormalised_not_scored_as_zero():
    """A customer with no due dates on record has *unknown* payment behaviour.

    Reading that as zero would mark them down for a gap in what Zoho holds,
    which is the platform blaming somebody for its own missing data.
    """
    weights = {"recency": 0.5, "reliability": 0.5}
    full = bonds.Facets(recency=1.0, consistency=None, breadth=None,
                        weight=None, reliability=1.0)
    partial = bonds.Facets(recency=1.0, consistency=None, breadth=None,
                           weight=None, reliability=None)
    assert bonds.composite(full, weights) == 100.0
    assert bonds.composite(partial, weights) == 100.0
    assert partial.missing() == ["consistency", "breadth", "weight", "reliability"]


def test_a_composite_with_nothing_measurable_is_none_not_zero():
    empty = bonds.Facets(None, None, None, None, None)
    assert bonds.composite(empty, {"recency": 1.0}) is None


def test_the_bands_are_ordered_and_cover_the_whole_range():
    assert bonds.band_of(100.0) == "ANCHORED"
    assert bonds.band_of(70.0) == "ANCHORED"
    assert bonds.band_of(69.9) == "STEADY"
    assert bonds.band_of(0.0) == "THIN"
    # Every band carries a meaning; a legend with a blank entry is a band
    # nobody can act on.
    assert all(b["meaning"] for b in bonds.bands())


# ── the refusals ─────────────────────────────────────────────────────────────
def test_a_relationship_below_the_evidence_floor_gets_no_score():
    result = _build(_lines("thin", count=2))
    row = _bond(result, "thin")
    assert row["score"] is None
    assert row["band"] is None
    assert str(TH.min_transactions) in row["unscored_reason"]


def test_the_frames_apply_the_same_floor_as_the_list():
    """Otherwise the play scores a relationship the grid beside it will not."""
    result = _build(_lines("thin", count=2) + _lines("solid", count=20))
    assert _bond(result, "thin")["score"] is None
    assert set(_track(result, "thin")) == {None}


def test_evidence_accumulates_rather_than_being_granted_at_the_start():
    """The early frames of a real relationship are unscored, then it appears."""
    track = _track(_build(_lines("a", count=20)), "a")
    scored = [i for i, v in enumerate(track) if v is not None]
    assert scored, "a twenty-order relationship must be scored eventually"
    assert track[0] is None, "the first month cannot already clear the floor"
    # Once scored, never silently un-scored by a gap in the middle.
    assert scored == list(range(scored[0], scored[0] + len(scored)))


def test_a_counterparty_is_absent_before_they_first_traded():
    """Not present at zero — that would draw a bond decaying before they met."""
    result = _build(_lines("late", count=12, start=date(2026, 1, 5))
                    + _lines("old", count=20))
    first_frame = result["frames"][0]
    assert not [e for e in first_frame["bonds"] if e["counterparty_id"] == "late"]
    assert [e for e in first_frame["bonds"] if e["counterparty_id"] == "old"]


def test_overdue_comes_from_the_dormancy_rule_and_not_from_the_score():
    """A customer can be strongly bonded and overdue at the same time, and the
    two must be able to disagree — that is what makes them two facts."""
    quiet = _lines("quiet", count=12, every_days=15, start=date(2025, 1, 1))
    result = _build(quiet)
    row = _bond(result, "quiet")
    assert row["overdue"] is True
    assert row["typical_interval_days"] == pytest.approx(15.0)


def test_the_last_frame_agrees_with_the_current_score():
    """The play must land exactly where the list is.

    The final period is the month *containing* the reference date, so its end is
    normally still in the future. Evaluating the frame there charges the
    relationship for days that have not passed, and the map ended up showing a
    lower score than the ledger row beside it for the same counterparty — which
    reads as a bug in one of them and is really a claim about tomorrow.
    """
    result = _build(_lines("a", count=20))
    assert _track(result, "a")[-1] == _bond(result, "a")["score"]


def test_a_decaying_relationship_reads_as_decaying_across_the_frames():
    """The whole point of the play: the trajectory has to be visible."""
    stopped = _lines("stopped", count=12, start=date(2024, 9, 1))
    track = [v for v in _track(_build(stopped), "stopped") if v is not None]
    assert track[-1] < track[len(track) // 2] < max(track)


def test_the_result_carries_the_version_that_produced_it():
    """A score with no version behind it cannot be explained six months on."""
    result = _build(_lines("a", count=8))
    assert result["thresholds_version"] == TH.version
    assert result["weights"]["recency"] == TH.bond_weight_recency


def test_the_frame_cap_is_reported_rather_than_applied_silently():
    lines: list[bonds.TradeLine] = []
    for i in range(12):
        lines += _lines(f"c{i}", count=8, amount=1000.0 * (i + 1))
    result = _build(lines, frame_cover=5)
    assert result["counts"]["counterparties"] == 12
    assert result["counts"]["frames_cover"] == 5
    assert all(len(f["bonds"]) <= 5 for f in result["frames"])


def test_an_empty_book_returns_the_shape_rather_than_nothing():
    result = _build([])
    assert result["bonds"] == [] and result["frames"] == []
    assert result["thresholds_version"] == TH.version
    assert result["bands"], "the legend exists even when the book does not"


# ── reliability, and its reconstructibility ──────────────────────────────────
def _settlement(customer: str, invoice_day: date, paid_day: date,
                due_day: date | None) -> payments.Settlement:
    return payments.Settlement(
        party_id=customer, document_ref=f"{customer}{invoice_day}",
        document_number=None, document_date=invoice_day, due_date=due_day,
        paid_on=paid_day, amount=1000.0)


def test_customer_reliability_is_replayable_month_by_month():
    """The facet has to mean the same thing in the first frame as in the last,
    which it only can if it is rebuilt from the days money actually moved."""
    rows = [
        _settlement("c", date(2025, 1, 1), date(2025, 1, 20), date(2025, 1, 31)),
        _settlement("c", date(2025, 2, 1), date(2025, 2, 20), date(2025, 2, 28)),
        _settlement("c", date(2025, 3, 1), date(2025, 5, 20), date(2025, 3, 31)),
    ]
    rel = bonds.reliability_from_payments(rows)["c"]
    assert rel.as_of(date(2025, 2, 25)) == pytest.approx(1.0)   # both on time
    assert rel.as_of(date(2025, 6, 1)) == pytest.approx(2 / 3)  # one late
    assert rel.as_of(date(2024, 1, 1)) is None                  # before any


def test_a_customer_without_due_dates_is_unknown_not_unreliable():
    rows = [_settlement("c", date(2025, 1, 1), date(2025, 3, 1), None)
            for _ in range(6)]
    rel = bonds.reliability_from_payments(rows)["c"]
    assert rel.score is None
    assert "due date" in rel.detail["payment"]


def test_reliability_below_the_settlement_floor_is_not_asserted():
    rows = [_settlement("c", date(2025, 1, 1), date(2025, 1, 5), date(2025, 1, 31))]
    assert bonds.reliability_from_payments(rows)["c"].score is None


def test_supplier_reliability_counts_orders_left_hanging():
    """The answerable version of 'they did not deliver'. ``supply.py`` records
    why it cannot be measured against a promised date in this book."""
    from app.commercial.insight import supply

    def _order(number: str, ordered: date, received: date | None, pending: float):
        return supply.SupplierOrder(
            vendor_id="v1", vendor_label="Kennametal", number=number,
            ordered_on=ordered, expected_on=None, received_on=received,
            pending_qty=pending, ordered_qty=10.0, total=1000.0, status="")

    orders = [
        _order("a", date(2025, 1, 1), date(2025, 1, 20), 0.0),
        _order("b", date(2025, 2, 1), date(2025, 2, 20), 0.0),
        _order("c", date(2025, 3, 1), None, 10.0),   # still open, long stale
    ]
    rel = bonds.reliability_from_supply(
        orders, AS_OF, stale_after_days=supply.STALE_ORDER_DAYS)["v1"]
    assert rel.score == pytest.approx(2 / 3)
    assert rel.detail["stale_open_orders"] == 1


def test_the_view_names_what_it_cannot_answer():
    """A blank is indistinguishable from a zero; a stated gap is not."""
    reasons = bonds.unavailable(bonds.VENDOR, has_reliability=False)
    assert any("Reliability" in r["what"] for r in reasons)
    assert any("pay them on time" in r["what"] for r in reasons)
    # The customer side has no such payable caveat to make.
    assert not any("pay them" in r["what"]
                   for r in bonds.unavailable(bonds.CUSTOMER,
                                              has_reliability=True))


# ── the endpoint, and the role split through the middle of it ────────────────
@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
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


def _as(client: TestClient, email: str) -> dict:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    out = client.get("/api/v1/insight/bonds",
                     headers={"Authorization": f"Bearer {token}"})
    assert out.status_code == 200, out.text
    return out.json()


def test_a_salespersons_response_has_no_supplier_half_at_all(client):
    """Omitted by the server, not hidden by the browser: supplier spend is
    purchase cost by another name, and there must be nothing in the payload to
    read out of a network tab."""
    body = _as(client, SALESPERSON)
    assert body["supplier_side_visible"] is False
    assert body["vendors"] is None
    assert "vendor" not in str(body["customers"]).lower()


def test_a_manager_sees_both_sides(client):
    body = _as(client, MANAGER)
    assert body["supplier_side_visible"] is True
    assert body["vendors"] is not None
    assert body["vendors"]["side"] == bonds.VENDOR
    assert body["customers"]["side"] == bonds.CUSTOMER


def test_an_unsynced_book_explains_itself_rather_than_rendering_blank(client):
    body = _as(client, MANAGER)
    assert body["customers"]["bonds"] == []
    assert body["empty_reason"], "an empty screen must say why it is empty"


# ── the column the supplier half rests on ────────────────────────────────────
def test_the_sync_attributes_every_cost_line_to_its_supplier(session):
    """``cost_records.vendor_id`` is what makes supplier breadth answerable.

    ``CostRecordIn`` has carried ``vendor_external_id`` since the supply pull
    landed; the read model dropped it. Checked against the shared book fixture
    rather than a hand-built row, because the claim is about the *sync*, and a
    row inserted by the test would pass whatever the sync does.
    """
    from sqlalchemy import select

    from app.domain import models

    from .test_decision_intelligence import ORG, _seed

    _seed(session)
    rows = session.scalars(
        select(models.CostRecord)
        .where(models.CostRecord.organization_id == ORG)).all()
    assert rows, "the fixture must produce cost lines to attribute"
    assert all(r.vendor_id for r in rows), (
        "every bill line in this fixture names its supplier, so every cost "
        "record must carry a vendor")


def test_a_replay_from_the_event_log_keeps_the_supplier(session):
    """The read model is derived, so a rebuild must not quietly lose a column —
    the platform's whole claim is that a full re-sync reconstructs it."""
    from sqlalchemy import delete, select

    from app.domain import models
    from app.state.replay import replay

    from .test_decision_intelligence import ORG, _seed

    _seed(session)
    before = {r.external_ref: r.vendor_id for r in session.scalars(
        select(models.CostRecord).where(
            models.CostRecord.organization_id == ORG)).all()}

    session.execute(delete(models.CostRecord).where(
        models.CostRecord.organization_id == ORG))
    session.commit()
    replay(session, ORG)
    session.commit()

    after = {r.external_ref: r.vendor_id for r in session.scalars(
        select(models.CostRecord).where(
            models.CostRecord.organization_id == ORG)).all()}
    assert after == before


def _field_names(value, out: set[str]) -> set[str]:
    """Every key name anywhere in a nested response."""
    if isinstance(value, dict):
        for key, inner in value.items():
            out.add(str(key).lower())
            _field_names(inner, out)
    elif isinstance(value, list):
        for inner in value:
            _field_names(inner, out)
    return out


def test_no_customer_bond_carries_a_cost_or_a_margin_field(client):
    """The invariant, checked on the wire rather than trusted from the code.

    Checked against *field names* rather than against the response as one
    string: the band descriptions contain the word "costs" in an ordinary
    English sentence, and a substring scan that failed on prose would be a test
    people learn to weaken rather than one that catches a leak.
    """
    body = _as(client, SALESPERSON)
    names = _field_names(body["customers"], set())
    leaked = [n for n in names
              if "cost" in n or "margin" in n or "gross_profit" in n]
    assert not leaked, f"restricted field(s) reached a salesperson: {leaked}"
