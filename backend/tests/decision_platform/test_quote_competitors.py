"""Who is taking the business, aggregated off ``QuoteOutcome.lost_to``.

The arithmetic here is small — count the losses, group the names — so almost
every test below pins a **refusal or a non-merge**, which is where this feature
can go wrong without anybody noticing.

Three failures are worth naming, because each one would read as a better screen
than the truth:

*Folding the blanks in.* A loss nobody attributed is not a loss to the leading
competitor, and it is not absent from the book either. If it quietly left the
denominator, every named competitor's share would rise and the screen would say
the field is better filled in than it is.

*Merging two spellings that are two firms.* ``competitor_key`` groups on case
and spacing and nothing else. The moment it strips legal forms it has invented
an entity this platform does not hold, and the losses reported against that
invention look exactly like real ones.

*Lowering the floor to fill the screen.* Eight losses before a name is called a
competitive position, the same floor a win rate gets. Below it the names are
withheld and the count of what was withheld is shown — an empty list with a
reason, never a shorter list of guesses.

The last test is the endpoint one, and it is the shape ``CLAUDE.md`` asks for:
it greps the whole response body rather than asserting field by field, because
every field-level assertion in a sibling file passed while the endpoint gave up
cost.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.commercial.insight import absence, outcomes
from app.db import Base, get_session
from app.domain import models
from app.domain.enums import QuoteLossReason
from app.routers import insight, platform_auth, quote_intelligence
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_sanketh"
AS_OF = date(2026, 7, 1)

OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

FLOOR = outcomes.MIN_DECIDED_QUOTES


def _lost(quote_id: str, *, lost_to: str = "", customer: str = "c1",
          value: str = "1000",
          reason: str = QuoteLossReason.PRICE.value,
          product_lines: tuple[str, ...] = ("CUTTING_TOOLS",),
          ) -> outcomes.DecidedQuote:
    return outcomes.DecidedQuote(
        quote_id=quote_id, customer_id=customer, customer_label=customer.upper(),
        won=False, loss_reason=reason, decided_on=AS_OF - timedelta(days=10),
        lines=1, value=Decimal(value), product_lines=product_lines,
        lost_to=lost_to)


def _won(quote_id: str, *, customer: str = "c1",
         value: str = "1000") -> outcomes.DecidedQuote:
    return outcomes.DecidedQuote(
        quote_id=quote_id, customer_id=customer, customer_label=customer.upper(),
        won=True, loss_reason="", decided_on=AS_OF - timedelta(days=10),
        lines=1, value=Decimal(value), product_lines=("CUTTING_TOOLS",))


def _losses_to(name: str, count: int, *, start: int = 0, **kw) -> list:
    return [_lost(f"{name}-{i}", lost_to=name, **kw)
            for i in range(start, start + count)]


def _mix(quotes, **kw) -> dict:
    return outcomes.competitor_mix(quotes, product_line_names=kw.pop(
        "product_line_names", {"CUTTING_TOOLS": "Cutting tools"}), **kw)


def _named(result: dict) -> dict[str, dict]:
    return {row["key"]: row for row in result["competitors"]}


def _bucket(result: dict, key: str) -> dict:
    [row] = [r for r in result["unattributed"] if r["key"] == key]
    return row


# ── the blanks ──────────────────────────────────────────────────────────────
def test_a_loss_with_no_winner_recorded_is_its_own_bucket():
    """It is not the leading competitor's, and it is not absent either."""
    result = _mix(_losses_to("Bright Tools", FLOOR)
                  + [_lost("silent1"), _lost("silent2")])

    assert _named(result)["bright tools"]["losses"] == FLOOR
    not_recorded = _bucket(result, outcomes.COMPETITOR_NOT_RECORDED)
    assert not_recorded["losses"] == 2
    assert not_recorded["value"] == pytest.approx(2000.0)
    # And it is labelled and explained, not a bare count — the way REASONS
    # handles the same silence.
    assert not_recorded["label"] == "Winner not recorded"
    assert not_recorded["kind"] in absence.KINDS


def test_a_cancelled_requirement_is_out_of_the_attributed_share():
    """The number and the sentence beside it have to agree.

    ``UNATTRIBUTED[COMPETITOR_NO_WINNER]["meaning"]`` is rendered on the same
    screen and says these rows "are held out of the attributed share rather than
    counted as silence about a competitor who never existed". Dividing by every
    loss instead makes a fully attributed book read as partly blind, and sends
    somebody looking for records that were never missing.

    Pinned as an exact 1.0 rather than as a comparison, because the failure is a
    denominator swap and a denominator swap survives ``>= 0.5``.
    """
    result = _mix([_lost(f"p{i}", lost_to="Bright Tools") for i in range(8)]
                  + [_lost("dead1", reason=QuoteLossReason.CUSTOMER_CANCELLED.value),
                     _lost("dead2", reason=QuoteLossReason.CUSTOMER_CANCELLED.value)])

    assert result["losses"] == 10
    assert result["named_losses"] == 8
    # Eight of eight attributable, not eight of ten.
    assert result["attributable_losses"] == 8
    assert result["attributed_share"] == 1.0

    # And the cancelled pair is still visible rather than quietly dropped.
    no_winner = next(u for u in result["unattributed"]
                     if u["key"] == outcomes.COMPETITOR_NO_WINNER)
    assert no_winner["losses"] == 2


def test_a_book_of_only_cancelled_losses_has_no_share_rather_than_nil():
    """Nothing could have carried a winner, so 0% attributed would be a lie
    about the recording rather than a fact about the book."""
    result = _mix([_lost("dead1", reason=QuoteLossReason.CUSTOMER_CANCELLED.value),
                   _lost("dead2", reason=QuoteLossReason.CUSTOMER_CANCELLED.value)])

    assert result["attributable_losses"] == 0
    assert result["attributed_share"] is None


def test_the_blanks_do_not_shrink_the_denominator():
    """The one failure that would flatter every share on the screen.

    Ten losses, eight of them attributed. The share of the book that is
    attributed is 80% and the competitor's share of the *named* losses is 100%;
    if the blanks had silently left the arithmetic, both would read 100%.
    """
    result = _mix(_losses_to("Bright Tools", FLOOR)
                  + [_lost("silent1"), _lost("silent2")])
    assert result["losses"] == FLOOR + 2
    assert result["named_losses"] == FLOOR
    assert result["attributed_share"] == pytest.approx(FLOOR / (FLOOR + 2))
    assert _named(result)["bright tools"]["share_of_named_losses"] == 1.0


def test_whitespace_only_is_not_a_name():
    """A field somebody typed a space into is blank, not a competitor."""
    result = _mix(_losses_to("Bright Tools", FLOOR)
                  + [_lost("blank", lost_to="   ")])
    assert set(_named(result)) == {"bright tools"}
    assert _bucket(result, outcomes.COMPETITOR_NOT_RECORDED)["losses"] == 1


def test_a_cancelled_requirement_is_not_filed_as_a_missing_winner():
    """There is no winner to record, so it is not a gap in the data.

    Counting it as one would put a worklist item on a blank nobody can ever
    fill in, and would understate how much of the book is really attributed.
    """
    result = _mix([_lost("dead", reason=QuoteLossReason.CUSTOMER_CANCELLED.value),
                   _lost("silent")])
    assert _bucket(result, outcomes.COMPETITOR_NO_WINNER)["losses"] == 1
    assert _bucket(result, outcomes.COMPETITOR_NOT_RECORDED)["losses"] == 1
    # Not a refusal — nothing is missing — so it carries no absence kind.
    assert "kind" not in _bucket(result, outcomes.COMPETITOR_NO_WINNER)


def test_a_loss_nobody_explained_is_unknown_rather_than_cancelled():
    """``NOT_RECORDED`` is outside the enum and must not read as "nobody
    bought it" — the three-valued answer the reason vocabulary exists for."""
    result = _mix([_lost("old", reason="NOT_RECORDED")])
    assert _bucket(result, outcomes.COMPETITOR_NOT_RECORDED)["losses"] == 1
    assert _bucket(result, outcomes.COMPETITOR_NO_WINNER)["losses"] == 0


def test_a_won_quote_never_names_a_competitor():
    """Nothing records who we beat, so a win contributes to no name here."""
    result = _mix(_losses_to("Bright Tools", FLOOR) + [_won("w1"), _won("w2")])
    assert result["losses"] == FLOOR
    assert _named(result)["bright tools"]["losses"] == FLOOR


def test_a_book_with_no_losses_refuses_the_share_rather_than_answering_zero():
    result = _mix([_won("w1"), _won("w2")])
    assert result["losses"] == 0
    assert result["attributed_share"] is None
    assert result["competitors"] == []


# ── the grouping, and what it will not merge ────────────────────────────────
def test_case_and_spacing_variants_are_one_competitor():
    quotes = (_losses_to("Bright Tools", 3)
              + _losses_to("bright tools", 3, start=10)
              + _losses_to("Bright  Tools ", 2, start=20))
    result = _mix(quotes)
    [row] = result["competitors"]
    assert row["key"] == "bright tools"
    assert row["losses"] == FLOOR
    # The display form is what people actually typed most, not the fold key.
    assert row["label"] == "Bright Tools"
    assert row["spellings"] == ["Bright Tools", "bright tools"]


def test_two_spellings_that_are_two_names_stay_two_rows():
    """The merge this module refuses to make.

    ``Sandvik`` and ``Sandvik India`` may well be one firm, and folding them
    would invent an entity this platform does not hold. Two rows understate a
    competitor visibly; one row overstates a competitor invisibly.
    """
    result = _mix(_losses_to("Sandvik", FLOOR)
                  + _losses_to("Sandvik India", FLOOR, start=50))
    assert set(_named(result)) == {"sandvik", "sandvik india"}


def test_a_legal_form_is_not_stripped_the_way_a_principal_name_would_be():
    """``principals.normalise_name`` is the near-match, and it is not used
    here: it would close ``Bright Tools Pvt Ltd`` up onto ``Bright Tools``."""
    assert (outcomes.competitor_key("Bright Tools Pvt Ltd")
            != outcomes.competitor_key("Bright Tools"))
    assert outcomes.competitor_key("  BRIGHT   tools ") == "bright tools"
    assert outcomes.competitor_key(None) == ""


# ── the floor ───────────────────────────────────────────────────────────────
def test_a_name_below_the_floor_is_withheld_rather_than_reported():
    """One loss to a name is a coincidence, not a competitive position."""
    result = _mix(_losses_to("Bright Tools", FLOOR - 1)
                  + _losses_to("Nova Cutting", 1, start=90))
    assert result["competitors"] == []
    assert result["below_floor"]["names"] == 2
    assert result["below_floor"]["losses"] == FLOOR
    assert result["below_floor"]["largest"] == FLOOR - 1
    assert result["below_floor"]["floor"] == FLOOR
    assert "never a lower floor" in result["below_floor"]["why"]
    # The names themselves are the claim the floor refuses, so they are absent.
    assert "Bright Tools" not in str(result["below_floor"])


def test_the_withheld_names_still_count_in_the_share_denominator():
    """Otherwise the one competitor over the floor would read as the whole of
    a book it is barely half of."""
    result = _mix(_losses_to("Bright Tools", FLOOR)
                  + _losses_to("Nova Cutting", 6, start=90))
    assert result["names_recorded"] == 2
    assert result["named_losses"] == FLOOR + 6
    [row] = result["competitors"]
    assert row["share_of_named_losses"] == pytest.approx(FLOOR / (FLOOR + 6),
                                                         abs=1e-4)


def test_the_floor_is_the_one_the_win_rate_uses():
    """A second, lower number here would be a floor chosen to fill a screen."""
    result = _mix(_losses_to("Bright Tools", FLOOR))
    assert result["min_losses"] == outcomes.MIN_DECIDED_QUOTES
    assert len(result["competitors"]) == 1


# ── the win rate that cannot be computed ────────────────────────────────────
def test_there_is_no_win_rate_against_a_competitor_and_it_says_why():
    """The denominator is not observable: nothing records who else was bidding
    on the quotes we won. A refusal that names what is missing, not a null."""
    result = _mix(_losses_to("Bright Tools", FLOOR))
    refusal = result["win_rate_against"]
    assert refusal["kind"] in absence.KINDS
    assert "denominator" in refusal["why"]
    assert "win rate" in refusal["what"].lower()
    # And no row quietly offers one anyway.
    assert all("win_rate" not in row for row in result["competitors"])


# ── where they are taking it ────────────────────────────────────────────────
def test_a_competitor_carries_the_customers_and_lines_it_took():
    quotes = (_losses_to("Bright Tools", 6, customer="c1")
              + _losses_to("Bright Tools", 2, start=60, customer="c2",
                           product_lines=("INSERTS",)))
    result = _mix(quotes, product_line_names={"CUTTING_TOOLS": "Cutting tools",
                                              "INSERTS": "Inserts"})
    [row] = result["competitors"]
    assert row["losses"] == FLOOR
    assert row["lost_value"] == pytest.approx(8000.0)
    assert [c["key"] for c in row["customers"]] == ["c1", "c2"]
    assert row["customers"][0]["losses"] == 6
    assert {ln["key"]: ln["losses"] for ln in row["product_lines"]} == {
        "CUTTING_TOOLS": 6, "INSERTS": 2}
    assert row["product_lines"][0]["label"] == "Cutting tools"


def test_a_quote_spanning_two_lines_counts_under_both_and_says_so():
    result = _mix(_losses_to("Bright Tools", FLOOR,
                             product_lines=("CUTTING_TOOLS", "INSERTS")),
                  product_line_names={})
    [row] = result["competitors"]
    assert row["losses"] == FLOOR
    assert sum(ln["losses"] for ln in row["product_lines"]) == 2 * FLOOR
    assert "appears under both" in result["note"]


# ── through the real endpoint ───────────────────────────────────────────────
def _seed(s) -> None:
    s.add(models.Customer(customer_id="c1", organization_id=ORG, external_id="c1",
                          name="Acme Engineering", assigned_user_id="usr_sales"))
    s.add(models.Customer(customer_id="c2", organization_id=ORG, external_id="c2",
                          name="Beta Works", assigned_user_id="usr_manager"))
    s.add(models.Product(product_id="p1", organization_id=ORG,
                         external_id="ITEM-p1", name="Item p1", uom="pcs",
                         manufacturer="KENNAMETAL INDIA LIMITED"))
    s.flush()


def _decide(s, quote_id: str, *, customer: str, won: bool,
            lost_to: str | None = None,
            reason: str = QuoteLossReason.PRICE.value) -> None:
    decided = datetime.now(timezone.utc) - timedelta(days=5)
    unit, unit_cost, qty = Decimal("100"), Decimal("70"), Decimal("10")
    s.add(models.QuoteDecision(
        organization_id=ORG, quote_id=quote_id, quote_line_id="L1",
        customer_id=customer, product_id="p1", quantity=qty,
        quantity_band="1-10", quoted_unit_price=unit, unit_cost=unit_cost,
        line_revenue=unit * qty, cogs=unit_cost * qty,
        gross_profit=(unit - unit_cost) * qty,
        margin=float((unit - unit_cost) / unit), as_of=AS_OF,
        thresholds_version="v1"))
    s.add(models.QuoteOutcome(
        organization_id=ORG, quote_id=quote_id, customer_id=customer,
        customer_ref=customer, status="WON" if won else "LOST",
        loss_reason=None if won else reason, lost_to=lost_to,
        decided_at=decided, sent_at=decided - timedelta(days=1)))


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
    # Eight losses to one name — spelled three ways, because two people typed
    # it — one loss nobody attributed, and two wins.
    for i in range(6):
        _decide(s, f"bt{i}", customer="c1", won=False, lost_to="Bright Tools")
    _decide(s, "bt6", customer="c1", won=False, lost_to="bright  tools")
    _decide(s, "bt7", customer="c1", won=False, lost_to="BRIGHT TOOLS")
    _decide(s, "quiet", customer="c1", won=False, lost_to=None)
    for i in range(2):
        _decide(s, f"w{i}", customer="c1", won=True)
    # One loss on an account the salesperson does not hold.
    _decide(s, "theirs", customer="c2", won=False, lost_to="Nova Cutting")
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


def test_the_rollup_reaches_the_endpoint_for_every_role(client):
    for email in (OWNER, MANAGER, SALES):
        body = client.get("/api/v1/insight/quote-outcomes",
                          headers=_hdr(client, email)).json()
        assert body["competitors"]["min_losses"] == FLOOR
        [row] = body["competitors"]["competitors"]
        assert row["label"] == "Bright Tools"
        assert row["losses"] == FLOOR
        assert row["customers"][0]["key"] == "c1"


def test_the_endpoint_keeps_the_blank_visible_and_out_of_the_name(client):
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, MANAGER)).json()
    mix = body["competitors"]
    assert mix["losses"] == 10                       # nine on c1, one on c2
    assert mix["named_losses"] == 9
    [not_recorded] = [r for r in mix["unattributed"]
                      if r["key"] == outcomes.COMPETITOR_NOT_RECORDED]
    assert not_recorded["losses"] == 1
    # The one name below the floor is counted and not printed.
    assert mix["below_floor"]["names"] == 1
    assert "Nova" not in str(mix["competitors"]) + str(mix["below_floor"])


def test_a_salespersons_rollup_stops_at_their_own_accounts(client):
    """The scope on the win rate is the scope on this. A competitor counted
    outside it leaks the size of a book this person cannot see."""
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, SALES)).json()
    assert body["competitors"]["losses"] == 9
    assert body["competitors"]["names_recorded"] == 1


def test_the_rollup_carries_no_cost_or_margin_anywhere(client):
    """Greps the whole body rather than asserting field by field.

    A competitor row is a name, a count and a quoted value. If any of it were
    ever derived from what we paid, this endpoint would have to move behind
    ``/quote-pricing`` — and a field-level check would not notice.
    """
    raw = client.get("/api/v1/insight/quote-outcomes",
                     headers=_hdr(client, SALES)).text
    for forbidden in ("margin", "unit_cost", "gross_profit", "cogs", "cost"):
        assert forbidden not in raw


def test_the_evidence_behind_a_row_is_readable_as_what_was_typed(client):
    """The per-quote list carries the raw name, not the fold key — a reader
    checking a merge has to be able to see the spellings that made it."""
    body = client.get("/api/v1/insight/quote-outcomes",
                      headers=_hdr(client, MANAGER)).json()
    typed = {q["lost_to"] for q in body["quotes"] if q["status"] == "LOST"}
    assert "bright  tools" in typed and "BRIGHT TOOLS" in typed
    assert None in typed                             # the unattributed loss
    assert all(q["lost_to"] is None for q in body["quotes"]
               if q["status"] == "WON")
