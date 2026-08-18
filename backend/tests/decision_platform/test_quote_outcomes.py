"""Win rate, the loss mix, and where a losing price sat.

Two classes of test, and the second is the one that matters more.

The first is arithmetic: that quotes are counted per quote rather than per line,
that a quote spanning two principals lands in both rates, that an aggregate
margin is Σ gross profit ÷ Σ revenue rather than a mean of per-quote margins.

The second pins the **refusals**, because they are what a later change will
erode first: that a win rate over four quotes is absent rather than confident,
that a price comparison needs evidence on both sides of it, that a quote marked
lost without a reason is refused rather than filed under a null, and that a
salesperson's copy of this screen has no margin in it and no other
salesperson's accounts either.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.commercial.insight import outcomes
from app.db import get_session
from app.domain import models
from app.domain.enums import LOSS_REASON_NOT_RECORDED, QuoteLossReason
from app.routers import insight, platform_auth, quote_intelligence
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
AS_OF = date(2026, 7, 1)

OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"


def _quote(quote_id: str, *, won: bool, days_ago: int = 10,
           value: str = "1000", customer: str = "c1",
           reason: str = QuoteLossReason.PRICE.value,
           gross_profit: str | None = None,
           principals: tuple[str, ...] = ("v1",),
           product_lines: tuple[str, ...] = ("CUTTING_TOOLS",),
           lines: int = 1) -> outcomes.DecidedQuote:
    return outcomes.DecidedQuote(
        quote_id=quote_id, customer_id=customer, customer_label=customer.upper(),
        won=won, loss_reason="" if won else reason,
        decided_on=AS_OF - timedelta(days=days_ago),
        lines=lines, value=Decimal(value),
        gross_profit=Decimal(gross_profit) if gross_profit is not None else None,
        principals=principals, product_lines=product_lines)


def _won_and_lost(won: int, lost: int, **kw) -> list[outcomes.DecidedQuote]:
    return ([_quote(f"w{i}", won=True, **kw) for i in range(won)]
            + [_quote(f"l{i}", won=False, **kw) for i in range(lost)])


# ── the floor ───────────────────────────────────────────────────────────────
def test_a_win_rate_over_four_quotes_is_absent_rather_than_confident():
    """The refusal this feature is built around.

    Four quotes can only produce 0, 25, 50, 75 or 100 per cent, and every one of
    those reads as a finding. The count is still shown; the rate is not.
    """
    result = outcomes.build(_won_and_lost(1, 3), as_of=AS_OF, customer_names={},
                            principal_names={}, product_line_names={})
    assert result["decided"] == 4
    assert result["won"] == 1
    assert result["win_rate"] is None
    assert result["estimable"] is False
    assert result["min_decided_quotes"] == outcomes.MIN_DECIDED_QUOTES


def test_the_floor_is_applied_per_slice_not_only_to_the_book():
    """A book with a rate can still hold a customer who has no rate.

    The opposite — inheriting the book's rate into a two-quote customer — is how
    a screen ends up telling somebody a specific thing about an account the
    evidence says nothing about.
    """
    quotes = (_won_and_lost(6, 4, customer="big")
              + _won_and_lost(1, 1, customer="small"))
    result = outcomes.build(quotes, as_of=AS_OF, customer_names={},
                            principal_names={}, product_line_names={})
    by_key = {row["key"]: row for row in result["customers"]}
    assert result["win_rate"] is not None
    assert by_key["big"]["win_rate"] == 0.6
    assert by_key["small"]["win_rate"] is None
    assert by_key["small"]["decided"] == 2


# ── counted per quote ───────────────────────────────────────────────────────
def test_a_forty_line_tender_is_one_loss_not_forty():
    """A customer declines a quote, not a line. Counting lines would let one
    large RFQ outvote a quarter of trading."""
    quotes = _won_and_lost(4, 0) + [_quote("big", won=False, lines=40)]
    result = outcomes.build(quotes, as_of=AS_OF, customer_names={},
                            principal_names={}, product_line_names={})
    assert result["decided"] == 5
    assert result["lost"] == 1


def test_a_quote_spanning_two_principals_counts_in_both_rates():
    """And the slice counts therefore sum to more than the total, deliberately.

    The alternative is attributing the whole quote to whichever principal is on
    line one, which is wrong in a way nobody would notice.
    """
    quotes = _won_and_lost(5, 3, principals=("kennametal", "yg1"))
    result = outcomes.build(quotes, as_of=AS_OF, customer_names={},
                            principal_names={"kennametal": "Kennametal",
                                             "yg1": "YG1"},
                            product_line_names={})
    rows = {row["key"]: row for row in result["principals"]}
    assert rows["kennametal"]["decided"] == 8
    assert rows["yg1"]["decided"] == 8
    assert rows["kennametal"]["label"] == "Kennametal"
    assert sum(r["decided"] for r in result["principals"]) > result["decided"]


def test_the_month_a_quote_lands_in_is_the_month_it_was_decided():
    """Not the month it was sent. A quote sent in March and answered in May says
    nothing about March, and indexing it there moves a loss into a month whose
    numbers have already been read."""
    quotes = [_quote("q1", won=False, days_ago=1),      # June/July 2026
              _quote("q2", won=True, days_ago=95)]      # ~March 2026
    result = outcomes.build(quotes, as_of=AS_OF, customer_names={},
                            principal_names={}, product_line_names={}, months=6)
    with_activity = [m for m in result["months"] if m["decided"]]
    assert len(with_activity) == 2
    assert sum(m["decided"] for m in result["months"]) == 2


# ── the loss mix ────────────────────────────────────────────────────────────
def test_the_loss_mix_separates_a_pricing_problem_from_a_stock_one():
    """The owner's actual question, and the reason the vocabulary exists."""
    quotes = [_quote("l1", won=False, reason="PRICE", value="100"),
              _quote("l2", won=False, reason="PRICE", value="100"),
              _quote("l3", won=False, reason="DELIVERY", value="900"),
              _quote("w1", won=True)]
    rows = {r["reason"]: r for r in outcomes.reason_mix(quotes)}
    assert rows["PRICE"]["count"] == 2
    assert rows["PRICE"]["owner"] == "PRICING"
    assert rows["DELIVERY"]["owner"] == "SUPPLY"
    # Value as well as count: two small price losses and one large delivery one
    # are not the same book, and a count alone says they are.
    assert rows["DELIVERY"]["value"] > rows["PRICE"]["value"]


def test_losses_recorded_before_the_vocabulary_are_named_not_dropped():
    """They are in the win rate; their silence should be visible beside it."""
    quotes = [_quote("l1", won=False, reason=LOSS_REASON_NOT_RECORDED)]
    rows = {r["reason"]: r for r in outcomes.reason_mix(quotes)}
    assert rows[LOSS_REASON_NOT_RECORDED]["label"] == "Not recorded"
    assert rows[LOSS_REASON_NOT_RECORDED]["owner"] == "UNKNOWN"


def test_every_reason_in_the_vocabulary_has_prose_and_an_owner():
    """A code with no meaning attached renders as a shouty enum on the screen."""
    for reason in QuoteLossReason:
        entry = outcomes.REASONS[reason.value]
        assert entry["label"] and entry["meaning"]
        assert entry["owner"] in outcomes.OWNERS


# ── the price question ──────────────────────────────────────────────────────
def _line(product: str, price: str, *, won: bool, band: str = "1-10",
          paid: str | None = None) -> outcomes.PricedLine:
    return outcomes.PricedLine(
        quote_id=f"q{price}{won}", product_id=product, product_label=product,
        quantity_band=band, unit_price=Decimal(price), won=won,
        customer_id="c1",
        customer_paid=Decimal(paid) if paid is not None else None)


def test_the_gap_is_the_losing_price_against_the_winning_one():
    """"We lose at 8% above where we win" — the sentence this exists to produce."""
    lines = ([_line("p1", "100", won=True) for _ in range(3)]
             + [_line("p1", "108", won=False) for _ in range(3)])
    [comparison] = outcomes.comparisons(lines)
    assert comparison.won_median == Decimal("100")
    assert comparison.lost_median == Decimal("108")
    assert comparison.gap_pct == pytest.approx(0.08)


def test_a_product_quoted_on_only_one_side_is_not_compared():
    """Three wins and no losses says nothing about why anything was lost."""
    lines = ([_line("p1", "100", won=True) for _ in range(6)]
             + [_line("p1", "108", won=False) for _ in range(2)])
    assert outcomes.comparisons(lines) == []


def test_quantity_bands_are_not_pooled():
    """The same insert at 10 pieces and at 1,000 is two prices for good reasons.

    The mix here is deliberately lopsided — the small-quantity band lost more
    often than it won, the bulk band the reverse — so pooling the two would
    report a large gap manufactured entirely out of which band each side is
    weighted towards. Within each band the price is identical on both sides, so
    the only honest answer is "no gap".
    """
    lines = ([_line("p1", "200", won=True, band="1-10")] * 3
             + [_line("p1", "200", won=False, band="1-10")] * 9
             + [_line("p1", "100", won=True, band="100+")] * 9
             + [_line("p1", "100", won=False, band="100+")] * 3)
    compared = outcomes.comparisons(lines)
    assert len(compared) == 2
    for comparison in compared:
        assert comparison.gap_pct == pytest.approx(0.0)


def test_one_mistyped_price_does_not_redefine_what_an_item_wins_at():
    """A median, not a mean — the extra zero is a story about one line."""
    lines = ([_line("p1", "100", won=True), _line("p1", "100", won=True),
              _line("p1", "1000", won=True)]
             + [_line("p1", "110", won=False) for _ in range(3)])
    [comparison] = outcomes.comparisons(lines)
    assert comparison.won_median == Decimal("100")


def test_the_comparison_against_what_this_customer_paid_needs_evidence_too():
    two = [_line("p1", "110", won=False, paid="100") for _ in range(2)]
    assert outcomes.versus_own_history(two) is None
    three = two + [_line("p1", "110", won=False, paid="100")]
    assert outcomes.versus_own_history(three)["median_gap_pct"] == pytest.approx(0.1)


# ── margin, aggregated the one legal way ────────────────────────────────────
def test_aggregate_margin_is_total_profit_over_total_revenue():
    """Never the mean of per-quote margins: a one-line screwdriver quote would
    otherwise weigh the same as a forty-line tender."""
    quotes = [_quote("q1", won=True, value="100", gross_profit="50"),   # 50%
              _quote("q2", won=True, value="900", gross_profit="90")]   # 10%
    # Σ140 ÷ Σ1000 = 14%. The mean of the two would be 30%.
    assert outcomes.margin_of(quotes) == pytest.approx(0.14)


def test_a_partially_costed_quote_is_excluded_from_the_margin_comparison():
    """Otherwise the comparison measures cost coverage rather than margin."""
    quotes = [_quote("q1", won=True, value="100", gross_profit="20"),
              _quote("q2", won=True, value="900", gross_profit=None)]
    assert outcomes.margin_of(quotes) == pytest.approx(0.2)
    result = outcomes.pricing(quotes, [], as_of=AS_OF)
    assert result["costed_quotes"] == 1
    assert result["decided_quotes"] == 2


def test_the_margin_gap_is_percentage_points():
    quotes = [_quote("w", won=True, value="100", gross_profit="25"),
              _quote("l", won=False, value="100", gross_profit="18")]
    result = outcomes.pricing(quotes, [], as_of=AS_OF)
    assert result["won_margin"] == pytest.approx(0.25)
    assert result["lost_margin"] == pytest.approx(0.18)
    assert result["margin_gap_pp"] == pytest.approx(-0.07)


# ── through the real endpoints ──────────────────────────────────────────────
def _seed(s) -> None:
    """Two customers, two makers, and enough decided quotes to clear the floor.

    ``c1`` is assigned to the demo salesperson and ``c2`` is not, which is what
    the scoping test turns on.
    """
    s.add(models.Customer(customer_id="c1", organization_id=ORG, external_id="c1",
                          name="Acme Engineering", assigned_user_id="usr_sales"))
    s.add(models.Customer(customer_id="c2", organization_id=ORG, external_id="c2",
                          name="Beta Works", assigned_user_id="usr_manager"))
    for pid, maker in (("p1", "KENNAMETAL INDIA LIMITED"), ("p2", "YG1")):
        s.add(models.Product(product_id=pid, organization_id=ORG,
                             external_id=f"ITEM-{pid}", name=f"Item {pid}",
                             uom="pcs", manufacturer=maker))
    s.flush()


def _decide(s, quote_id: str, *, customer: str, won: bool, product: str = "p1",
            price: str = "100", cost: str = "70", qty: str = "10",
            reason: str = QuoteLossReason.PRICE.value, days_ago: int = 5) -> None:
    """One decided quote with one priced snapshot behind it."""
    decided = datetime.now(timezone.utc) - timedelta(days=days_ago)
    unit, unit_cost, quantity = Decimal(price), Decimal(cost), Decimal(qty)
    s.add(models.QuoteDecision(
        organization_id=ORG, quote_id=quote_id, quote_line_id="L1",
        customer_id=customer, product_id=product, quantity=quantity,
        quantity_band="1-10", quoted_unit_price=unit, unit_cost=unit_cost,
        line_revenue=unit * quantity, cogs=unit_cost * quantity,
        gross_profit=(unit - unit_cost) * quantity,
        margin=float((unit - unit_cost) / unit), as_of=AS_OF,
        thresholds_version="v1"))
    s.add(models.QuoteOutcome(
        organization_id=ORG, quote_id=quote_id, customer_id=customer,
        customer_ref=customer, status="WON" if won else "LOST",
        loss_reason=None if won else reason,
        decided_at=decided, sent_at=decided - timedelta(days=1)))


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)

    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    # Six won at 100, four lost at 112 — over the floor, and a gap wide enough
    # that the comparison has something to find.
    for i in range(6):
        _decide(s, f"w{i}", customer="c1", won=True, price="100")
    for i in range(4):
        _decide(s, f"l{i}", customer="c1", won=False, price="112")
    # One decided quote on an account the salesperson does not hold.
    _decide(s, "other", customer="c2", won=False, price="120",
            reason=QuoteLossReason.DELIVERY.value)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)
    app.include_router(quote_intelligence.router)

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
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_win_rate_endpoint_answers_for_every_role(client):
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, MANAGER)).json()
    assert body["decided"] == 11
    assert body["won"] == 6
    assert body["win_rate"] == pytest.approx(6 / 11, abs=1e-4)
    assert {r["reason"] for r in body["reasons"]} == {"PRICE", "DELIVERY"}


def test_a_salespersons_copy_carries_no_cost_or_margin_anywhere(client):
    """Absent, not masked — there is no field to read out of a network tab."""
    raw = client.get("/api/v1/insight/quote-outcomes",
                     headers=_hdr(client, SALES)).text
    for forbidden in ("margin", "unit_cost", "gross_profit", "cogs"):
        assert forbidden not in raw


def test_a_salesperson_sees_their_own_accounts_and_not_the_book(client):
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, SALES)).json()
    assert body["decided"] == 10                    # the c2 quote is not theirs
    assert {r["reason"] for r in body["reasons"]} == {"PRICE"}
    assert {row["key"] for row in body["customers"]} == {"c1"}


def test_the_pricing_analysis_is_manager_territory(client):
    assert client.get("/api/v1/insight/quote-pricing",
                      headers=_hdr(client, SALES)).status_code == 403
    assert client.get("/api/v1/insight/quote-pricing",
                      headers=_hdr(client, MANAGER)).status_code == 200
    assert client.get("/api/v1/insight/quote-pricing",
                      headers=_hdr(client, OWNER)).status_code == 200


def test_the_pricing_analysis_finds_the_gap_between_winning_and_losing(client):
    body = client.get("/api/v1/insight/quote-pricing",
                      headers=_hdr(client, MANAGER)).json()
    assert body["compared_products"] == 1
    [comparison] = body["comparisons"]
    assert comparison["won_median_price"] == 100
    assert comparison["lost_median_price"] == 112
    assert comparison["gap_pct"] == pytest.approx(0.12)
    # 30% on what wins, 37.5% on what lost — priced above the winning line.
    assert body["won_margin"] == pytest.approx(0.30)
    assert body["margin_gap_pp"] > 0


def test_a_quote_awaiting_an_answer_is_listed_but_counted_nowhere(client):
    """It is the thing an outcome gets recorded *on*, and it is not a loss.

    Counting it as one would turn the win rate into a measure of how fast
    customers reply.
    """
    _outcome(client, MANAGER, "pending", "SENT", customer="Acme Engineering")
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, MANAGER)).json()
    assert body["decided"] == 11
    assert body["open"] == 1
    [row] = body["awaiting"]
    assert row["quote_id"] == "pending"
    assert "WON" in row["allowed_next"] and "LOST" in row["allowed_next"]


def test_an_open_quote_on_somebody_elses_account_is_not_listed(client):
    """The scope on the open list is the scope on the decided one. A count
    taken outside it leaks the size of a book this person cannot see."""
    s = client.Maker()
    s.add(models.QuoteOutcome(organization_id=ORG, quote_id="theirs",
                              customer_id="c2", customer_ref="c2", status="SENT"))
    s.commit()
    s.close()
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, SALES)).json()
    assert body["open"] == 0
    assert body["awaiting"] == []


def test_a_quote_lands_under_its_principal_and_its_product_line(client):
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, MANAGER)).json()
    assert body["principals"], "the maker on the item master should attribute these"
    assert sum(r["decided"] for r in body["principals"]) >= body["decided"] - 1


# ── recording the reason ────────────────────────────────────────────────────
def _outcome(c, email, quote_id, status, **body):
    return c.post("/api/v1/quote-intelligence/outcome",
                  json={"quote_id": quote_id, "status": status, **body},
                  headers=_hdr(c, email))


def test_a_quote_cannot_be_marked_lost_without_saying_why(client):
    _outcome(client, MANAGER, "new1", "SENT")
    r = _outcome(client, MANAGER, "new1", "LOST")
    assert r.status_code == 422
    assert "reason" in r.json()["detail"].lower()
    # And the quote is still SENT — a refused transition must not half-apply.
    s = client.Maker()
    row = s.query(models.QuoteOutcome).filter_by(quote_id="new1").one()
    assert row.status == "SENT" and row.loss_reason is None
    s.close()


def test_the_recorded_reason_reaches_the_analysis(client):
    _outcome(client, MANAGER, "new2", "SENT", customer="Acme Engineering")
    r = _outcome(client, MANAGER, "new2", "LOST", customer="Acme Engineering",
                 loss_reason="DELIVERY", note="Six weeks, they needed two")
    assert r.status_code == 200
    assert r.json()["loss_reason"] == "DELIVERY"
    assert r.json()["note"] == "Six weeks, they needed two"


def test_a_won_quote_cannot_carry_a_loss_reason(client):
    """It would be a fact about nothing, and it would be counted.

    The column is written only on the LOST edge, so a reason arriving with a
    WON is dropped rather than refused. This branch originally refused it with
    a 422; main had already landed the drop, and its
    ``test_a_won_quote_carries_no_loss_reason`` pins that. The two mechanisms
    protect the same invariant — nothing but a real loss reaches the loss mix —
    and refusing would break the case main wrote it for: a form that still
    holds a reason from an earlier attempt, switched to WON before sending.
    """
    _outcome(client, MANAGER, "new3", "SENT")
    r = _outcome(client, MANAGER, "new3", "WON", loss_reason="PRICE")
    assert r.status_code == 200
    assert r.json()["loss_reason"] is None


def test_a_mistyped_reason_can_be_corrected_without_touching_the_snapshot(client):
    """The outcome is mutable because it is learned late; the priced snapshot
    is not, and correcting one must not rewrite the other."""
    _outcome(client, MANAGER, "new4", "SENT")
    _outcome(client, MANAGER, "new4", "LOST", loss_reason="PRICE")
    r = _outcome(client, MANAGER, "new4", "LOST", loss_reason="COMPETITOR")
    assert r.status_code == 200 and r.json()["loss_reason"] == "COMPETITOR"


def test_an_empty_screen_says_why_rather_than_showing_nothing(client):
    """A book with no decided quotes is a legitimate state and should read as
    one — the demo database is exactly this."""
    s = client.Maker()
    s.query(models.QuoteOutcome).delete()
    s.commit()
    s.close()
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, MANAGER)).json()
    assert body["decided"] == 0
    assert body["win_rate"] is None
    assert "won or lost" in body["empty_reason"]
