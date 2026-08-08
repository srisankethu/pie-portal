"""What hitting a principal's number pays, and where the arithmetic must refuse.

A target on its own is half the sentence. The other half is the scheme, and the
claims worth pinning are the ones that decide whether an owner can act on this
in week eight rather than hearing about it at year end:

* **slabs are the shape**, not an extension of a flat rate — 2% at forty lakh and
  3% at sixty is the ordinary case here, and a flat percentage is the one-slab
  case running down the same arithmetic;
* the rate is paid on **what was bought**, and the slab is only the gate;
* what is **at stake is an uplift** — with 2% secured, reaching 3% is worth the
  difference, and a total there would count money already earned;
* a scheme where buying more earns less is a **transcription error**, refused on
  the way in rather than stored and rendered as a negative incentive;
* below the evidence floor there is **no projection at all** — a quarter three
  days old, or a fortnight whose whole total came off one bill, gets a named
  refusal instead of a confident close;
* the money is **Decimal**, because the one document this arithmetic has to
  agree with is the principal's own statement.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.commercial.insight import schemes
from app.commercial.insight.dependency import Target

#: Eight weeks into a thirteen-week quarter — the Tuesday in the brief.
AS_OF = date(2026, 5, 26)
QUARTER = Target(vendor_id="v1", period_start=date(2026, 4, 1),
                 period_end=date(2026, 6, 30), basis="PURCHASE",
                 amount=5_000_000.0, target_id="t1")


def _flat(rate: str = "0.025", at: str = "5000000") -> schemes.Scheme:
    return schemes.validate([(Decimal(at), Decimal(rate))])


def _slabbed() -> schemes.Scheme:
    """2% at forty lakh, 3% at sixty. The common case."""
    return schemes.validate([(Decimal("4000000"), Decimal("0.02")),
                             (Decimal("6000000"), Decimal("0.03"))])


def _outlook(scheme, actual: str, *, documents: int = 8, as_of: date = AS_OF,
             target: Target = QUARTER) -> dict:
    return schemes.outlook(target, scheme, actual=Decimal(actual),
                           documents=documents, as_of=as_of)


# ── the sentence the owner wants on a Tuesday ───────────────────────────────
def test_a_flat_scheme_puts_the_whole_rebate_at_stake_while_the_target_is_short():
    """₹42 lakh against ₹50 lakh, 2.5% on hitting it: ₹1.25 lakh at stake."""
    out = _outlook(_flat(), "4200000")

    assert out["secured"] is None          # nothing is earned below the number
    assert out["next_slab"]["threshold"] == 5_000_000.0
    assert out["next_slab"]["gap"] == 800_000.0
    assert out["at_stake"] == 125_000.0


def test_a_slab_scheme_puts_only_the_uplift_at_stake():
    """2% is already secured at ₹42 lakh; the 3% rung is worth the difference.

    A screen reporting the full ₹1.8 lakh here would be counting the ₹84,000
    this book has already earned as something still to play for.
    """
    out = _outlook(_slabbed(), "4200000")

    assert out["secured"] == {"threshold": 4_000_000.0, "rate": 0.02,
                              "rebate": 84_000.0}
    assert out["next_slab"]["rebate"] == 180_000.0     # 3% of the ₹60 lakh rung
    assert out["next_slab"]["gap"] == 1_800_000.0
    assert out["at_stake"] == 96_000.0                 # 1,80,000 − 84,000


def test_the_rate_is_paid_on_what_was_bought_not_on_the_slab_it_cleared():
    """₹52 lakh through the ₹40 lakh rung earns 2% of fifty-two, not of forty."""
    out = _outlook(_slabbed(), "5200000")
    assert out["secured"]["rebate"] == 104_000.0


def test_clearing_the_top_rung_leaves_nothing_at_stake_rather_than_no_answer():
    out = _outlook(_slabbed(), "7000000")
    assert out["next_slab"] is None
    assert out["at_stake"] == 0.0
    assert out["secured"]["rate"] == 0.03


def test_no_scheme_on_record_is_absent_rather_than_a_rebate_of_nothing():
    """Different facts. Nobody has said what the rebate is, which is not the
    same as a principal who pays none — and a zero would read as the second."""
    out = _outlook(None, "4200000")
    assert out["at_stake"] is None
    assert out["scheme"] is None
    assert out["secured"] is None
    # The projection is still made: where the period lands does not need a
    # scheme, and it is half of what the screen is for.
    assert out["projection"]["projected_close"] > 0


def test_a_flat_scheme_is_the_one_slab_case_of_the_same_arithmetic():
    """No second code path — the flat flag is presentation and nothing else."""
    flat = _flat(rate="0.02", at="4000000")
    assert flat.flat is True
    assert _slabbed().flat is False
    # The same purchase, the same rung, the same rebate.
    assert (_outlook(flat, "4200000")["secured"]["rebate"]
            == _outlook(_slabbed(), "4200000")["secured"]["rebate"])


# ── the refusal ─────────────────────────────────────────────────────────────
def test_a_quarter_three_days_old_gets_no_projection():
    """The house rule: below the evidence floor, say so rather than extrapolate."""
    out = _outlook(_flat(), "200000", as_of=date(2026, 4, 3))

    assert out["projection"] is None
    assert out["absent"]["reason"] == schemes.TOO_EARLY
    assert "run rate over a few days" in out["absent"]["why"]
    # What has actually been bought is still exact, and still says so.
    assert out["at_stake"] == 125_000.0


def test_a_fortnight_carried_by_a_single_bill_gets_no_projection_either():
    """A days floor alone would wave this through: one large order is not a rate."""
    out = _outlook(_flat(), "4000000", documents=2, as_of=date(2026, 4, 30))

    assert out["projection"] is None
    assert out["absent"]["reason"] == schemes.TOO_FEW_DOCUMENTS
    assert out["evidence"] == {"elapsed_days": 30, "documents": 2,
                               "min_elapsed_days": schemes.MIN_ELAPSED_DAYS,
                               "min_documents": schemes.MIN_DOCUMENTS}


def test_the_days_floor_is_reported_before_the_document_floor():
    """Three days in with one bill fails both. The reason a reader will
    understand without checking anything is the one that gets shown."""
    assert schemes.refusal(3, 1) == schemes.TOO_EARLY
    assert schemes.refusal(schemes.MIN_ELAPSED_DAYS, 1) == schemes.TOO_FEW_DOCUMENTS
    assert schemes.refusal(schemes.MIN_ELAPSED_DAYS,
                           schemes.MIN_DOCUMENTS) is None


# ── the projection, once it is earned ───────────────────────────────────────
def test_above_the_floor_the_close_is_the_rate_so_far_run_to_the_period_s_end():
    """₹42 lakh over 56 days of a 91-day quarter closes at ₹68.25 lakh."""
    out = _outlook(_slabbed(), "4200000")

    assert out["evidence"]["elapsed_days"] == 56
    assert out["projection"]["run_rate_per_day"] == 75_000.0
    assert out["projection"]["projected_close"] == 6_825_000.0
    assert out["projection"]["clears_target"] is True
    assert out["projection"]["shortfall"] == 0.0
    # Landing above the ₹60 lakh rung is worth 3% of the projected close.
    assert out["projection"]["slab"]["rate"] == 0.03
    assert out["projection"]["rebate"] == 204_750.0


def test_a_projection_that_misses_reports_the_shortfall_rather_than_a_verdict():
    out = _outlook(_flat(), "2000000")
    assert out["projection"]["clears_target"] is False
    assert out["projection"]["projected_close"] == 3_250_000.0
    assert out["projection"]["shortfall"] == 1_750_000.0
    assert out["projection"]["slab"] is None      # no rung reached at that close
    assert out["projection"]["rebate"] is None


def test_the_projection_never_moves_what_has_actually_been_bought():
    """Two books at the same purchase, one early and one late in the quarter,
    have the same secured rebate and the same amount at stake."""
    early = _outlook(_slabbed(), "4200000", as_of=date(2026, 4, 20))
    late = _outlook(_slabbed(), "4200000", as_of=date(2026, 6, 20))
    assert early["secured"] == late["secured"]
    assert early["at_stake"] == late["at_stake"]
    assert early["projection"]["projected_close"] != late["projection"]["projected_close"]


# ── what may be stored ──────────────────────────────────────────────────────
def test_a_scheme_where_buying_more_earns_less_is_refused():
    """Always a transcription error, and stored it would render as an incentive
    to stop buying — the uplift to the next rung would be negative."""
    with pytest.raises(schemes.InvalidScheme, match="higher rate"):
        schemes.validate([(Decimal("4000000"), Decimal("0.03")),
                          (Decimal("6000000"), Decimal("0.02"))])


def test_two_rungs_starting_at_the_same_amount_are_refused():
    with pytest.raises(schemes.InvalidScheme, match="same amount"):
        schemes.validate([(Decimal("4000000"), Decimal("0.02")),
                          (Decimal("4000000"), Decimal("0.03"))])


def test_a_percentage_typed_into_a_ratio_field_is_refused():
    """``2.5`` where the field wanted ``0.025``. The number it produces is large,
    plausible in shape, and wrong by a hundred."""
    with pytest.raises(schemes.InvalidScheme, match="not a rate"):
        schemes.validate([(Decimal("5000000"), Decimal("2.5"))])


def test_a_slab_that_pays_nothing_is_refused():
    with pytest.raises(schemes.InvalidScheme, match="pays nothing"):
        schemes.validate([(Decimal("5000000"), Decimal("0"))])


def test_an_empty_scheme_is_refused_rather_than_meaning_a_rebate_of_zero():
    with pytest.raises(schemes.InvalidScheme, match="at least one slab"):
        schemes.validate([])


def test_rungs_are_ordered_by_threshold_however_they_arrive():
    scheme = schemes.validate([(Decimal("6000000"), Decimal("0.03")),
                               (Decimal("4000000"), Decimal("0.02"))])
    assert [float(s.threshold) for s in scheme.slabs] == [4_000_000.0, 6_000_000.0]


def test_the_rebate_is_exact_to_the_paise():
    """The one document this has to agree with is the principal's statement.
    2.35% of ₹41,66,666.67 is ₹97,916.67 and no float about it."""
    scheme = schemes.validate([(Decimal("4000000"), Decimal("0.0235"))])
    assert scheme.slabs[0].rebate_on(Decimal("4166666.67")) == Decimal("97916.67")


# ── the API: the target and its scheme are written together ─────────────────
#
# One write path, because a rebate with no number to hit is not a scheme and two
# endpoints would let one be saved without the other. Manager and above
# throughout: purchase spend is cost by another name and a rebate is a
# percentage of it, so a salesperson's response has none of this in it rather
# than having it hidden in the browser.


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base, get_session
    from app.domain import models
    from app.routers import insight, platform_auth
    from app.seed import ensure_org_and_users

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    org = "org_sanketh"
    s.add(models.Vendor(vendor_id="v_ken", organization_id=org,
                        external_id="ev_ken", name="Kennametal India"))
    s.add(models.Customer(customer_id="c1", organization_id=org,
                          external_id="ec1", name="Buyer"))
    s.add(models.Product(product_id="p1", organization_id=org, external_id="ep1",
                         name="Insert", source_ref={}))
    # Eight bills through the quarter, ₹5.25 lakh each — ₹42 lakh by 26 May, on
    # a ₹50 lakh number. The sentence in the brief, as rows.
    for i in range(8):
        s.add(models.CostRecord(
            cost_record_id=f"cr{i}", organization_id=org,
            external_ref=f"bill{i}:1", product_id="p1", vendor_id="v_ken",
            date=date(2026, 4, 5) + timedelta(days=6 * i), qty=Decimal("1"),
            unit_cost=Decimal("525000")))
    # One sale, purely so the snapshot has a reference date to hang the wall on.
    s.add(models.SalesTxn(
        organization_id=org, external_ref="inv1:1", customer_id="c1",
        product_id="p1", date=AS_OF, qty=Decimal("1"),
        unit_price=Decimal("1000"), line_revenue=Decimal("1000"),
        source_ref={"record_id": "inv1"}))
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
    built = TestClient(app)
    # The one test that has to look past the API: whether a delete left rows
    # behind is invisible from outside, because a scheme is only ever read
    # through the target it hangs off.
    built.session_maker = Maker           # type: ignore[attr-defined]
    return built


def _head(client, email: str = "m.rao@sanketh.in"):
    from app.seed import SEED_PASSWORD
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _put(client, head, **over):
    body = {"vendor_id": "v_ken", "period_start": "2026-04-01",
            "period_end": "2026-06-30", "amount": "5000000",
            "basis": "PURCHASE",
            "slabs": [{"threshold": "4000000", "rate": "0.02"},
                      {"threshold": "6000000", "rate": "0.03"}]}
    body.update(over)
    return client.put("/api/v1/insight/targets", json=body, headers=head)


def test_the_scheme_is_saved_with_the_target_and_read_back_with_it(client):
    head = _head(client)
    assert _put(client, head).status_code == 200

    listed = client.get("/api/v1/insight/targets", headers=head).json()
    assert listed["targets"][0]["slabs"] == [
        {"threshold": 4_000_000.0, "rate": 0.02},
        {"threshold": 6_000_000.0, "rate": 0.03},
    ]


def test_a_write_replaces_the_scheme_rather_than_adding_to_it(client):
    """A PUT states the whole target. A rung somebody removed is gone, not
    surviving because the request did not mention it."""
    head = _head(client)
    _put(client, head)
    _put(client, head, slabs=[{"threshold": "5000000", "rate": "0.025"}])

    listed = client.get("/api/v1/insight/targets", headers=head).json()
    assert listed["targets"][0]["slabs"] == [{"threshold": 5_000_000.0,
                                              "rate": 0.025}]


def test_an_empty_slab_list_clears_the_scheme(client):
    head = _head(client)
    _put(client, head)
    _put(client, head, slabs=[])
    listed = client.get("/api/v1/insight/targets", headers=head).json()
    assert listed["targets"][0]["slabs"] == []


def test_a_scheme_that_cannot_mean_a_rebate_leaves_the_stored_one_alone(client):
    """Validated before anything is written. A refused write that had already
    deleted the old rungs would lose a scheme to a typo."""
    head = _head(client)
    _put(client, head)
    bad = _put(client, head, slabs=[{"threshold": "4000000", "rate": "0.03"},
                                    {"threshold": "6000000", "rate": "0.02"}])
    assert bad.status_code == 422
    assert "higher rate" in bad.text

    listed = client.get("/api/v1/insight/targets", headers=head).json()
    assert len(listed["targets"][0]["slabs"]) == 2
    assert listed["targets"][0]["slabs"][1]["rate"] == 0.03


def test_deleting_a_target_takes_its_scheme_with_it(client):
    """A rebate whose target is gone is unreachable, since every read of a
    scheme starts from the target it hangs off — so it is deleted, not orphaned.
    Checked against the rows, because from outside the API the two look the
    same."""
    from app.domain import models

    head = _head(client)
    target_id = _put(client, head).json()["target_id"]
    assert client.delete(f"/api/v1/insight/targets/{target_id}",
                         headers=head).status_code == 204

    body = client.get("/api/v1/insight/schemes", headers=head).json()
    assert body["rows"] == []
    assert "typed in" in body["empty_reason"]

    with client.session_maker() as check:
        assert check.query(models.VendorSchemeSlab).count() == 0


def test_the_wall_reports_the_rebate_beside_the_progress(client):
    """₹42 lakh bought against ₹50 lakh, eight bills, 2% secured and the 3%
    rung ₹96,000 away — computed from rows, not restated from the request."""
    head = _head(client)
    _put(client, head)

    body = client.get("/api/v1/insight/schemes", headers=head).json()
    row = body["rows"][0]

    assert row["label"] == "Kennametal India"
    assert row["progress"]["actual"] == 4_200_000.0
    assert row["progress"]["gap"] == 800_000.0
    assert row["rebate"]["secured"]["rebate"] == 84_000.0
    assert row["rebate"]["at_stake"] == 96_000.0
    assert body["at_stake_total"] == 96_000.0
    assert row["rebate"]["evidence"]["documents"] == 8


def test_a_target_whose_period_has_closed_is_not_on_the_wall(client):
    """Settled — the principal has paid or has not. A wall of finished quarters
    buries the number still winnable."""
    head = _head(client)
    _put(client, head, period_start="2025-01-01", period_end="2025-03-31")
    body = client.get("/api/v1/insight/schemes", headers=head).json()
    assert body["rows"] == []


def test_a_salesperson_cannot_read_any_of_it(client):
    """Purchase spend is cost by another name and a rebate is a percentage of
    it. 403, not a stripped payload — there is nothing left once the money goes."""
    head = _head(client, "r.nair@sanketh.in")
    assert client.get("/api/v1/insight/schemes", headers=head).status_code == 403
    assert client.get("/api/v1/insight/targets", headers=head).status_code == 403
    assert _put(client, head).status_code == 403
