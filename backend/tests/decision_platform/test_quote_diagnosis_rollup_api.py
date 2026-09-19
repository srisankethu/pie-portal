"""The quote-level roll-up and the per-line options, through the real routes.

Two questions this file exists for, neither of which a pure function can answer:

**A line that loses money survives the wire, on a quote whose total is healthy.**
``test_quote_diagnosis_rollup`` proves the object carries it; this proves the
response does, over HTTP, on a quote whose blended margin is comfortably positive
and whose loss-making line does not even render a card — the engine judged its
price consistent with what this customer has paid, which it is, and it still
loses money on every unit. That is the whole shape of the failure a total buries,
and it is asserted here rather than described.

**The desk is given coverage and no roll-up at all.** Not a filtered one —
``rollup`` is absent from a salesperson's payload, because ``_quote_level`` puts
it there on one branch and there is no key on the other for a future author to
forget to remove. The sweep runs over the **whole** response rather than one
line, which is the half these tests add: every existing sweep in this package
wraps a single line in ``{"lines": [...]}`` and therefore could never have seen a
quote-level key.

The leak definition is imported from ``cost_sweep`` rather than restated. Four
projections now serve a diagnosis and two definitions of "a leak" is one more
than this can afford. One test proves the check is live on the new keys rather
than asserting it: it splices the owner's roll-up into the desk's payload and
requires the sweep to object.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import cost_sweep
import dbsupport
from app.commercial.config import CommercialThresholds
from app.commercial.quote_diagnosis import (considerations, drivers,
                                            render, rollup)
from app.db import get_session
from app.domain import models
from app.routers import platform_auth, quote_diagnosis
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
AS_OF = date.today()

MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"

#: The healthy line. Quoted below what this customer has paid, so it renders a
#: card, and comfortably above what it was bought for.
EARNER_PRICE = 1000
EARNER_COST = 371
EARNER_QUOTE = 850
EARNER_QTY = 10

#: The buried line. Quoted at exactly what this customer has always paid — so
#: the price engine finds nothing to say about it and no card is drawn — and
#: bought for nine times that. This is the line the total must not hide.
SINKER_PRICE = 100
SINKER_COST = 907
SINKER_QTY = 4

QUOTE = "QT-ROLLUP-1"


def _stamp(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, tzinfo=timezone.utc)


def _seed(s) -> None:
    s.add(models.Customer(customer_id="c1", organization_id=ORG,
                          external_id="c1", name="Acme Engineering",
                          assigned_user_id="usr_sales"))
    s.add(models.Product(product_id="p1", organization_id=ORG,
                         external_id="ITEM-900", name="CNMG 120408-MP insert",
                         uom="pcs", source_item_category="Turning"))
    s.add(models.Product(product_id="p2", organization_id=ORG,
                         external_id="ITEM-410", name="20 mm boring bar",
                         uom="pcs", source_item_category="Boring"))

    for i in range(12):
        day = AS_OF - timedelta(days=300 - 7 * i)
        s.add(models.SalesTxn(
            sales_txn_id=f"stx_a{i}", organization_id=ORG,
            external_ref=f"INV-A{i}:1", customer_id="c1", product_id="p1",
            date=day, qty=Decimal(EARNER_QTY), unit_price=Decimal(EARNER_PRICE),
            line_revenue=Decimal(EARNER_PRICE * EARNER_QTY),
            source_recorded_at=_stamp(day),
            source_ref={"record_type": "invoice"}))
        s.add(models.SalesTxn(
            sales_txn_id=f"stx_b{i}", organization_id=ORG,
            external_ref=f"INV-B{i}:1", customer_id="c1", product_id="p2",
            date=day, qty=Decimal(SINKER_QTY), unit_price=Decimal(SINKER_PRICE),
            line_revenue=Decimal(SINKER_PRICE * SINKER_QTY),
            source_recorded_at=_stamp(day),
            source_ref={"record_type": "invoice"}))

    # Two rows the engine can locate and may not use: nothing records when they
    # became visible. That is what puts ``EVIDENCE_WITHHELD`` on the line's
    # context, which is what the desk's one firing option rests on.
    for i in range(2):
        day = AS_OF - timedelta(days=90 - 7 * i)
        s.add(models.SalesTxn(
            sales_txn_id=f"stx_dark{i}", organization_id=ORG,
            external_ref=f"INV-D{i}:1", customer_id="c1", product_id="p1",
            date=day, qty=Decimal(EARNER_QTY), unit_price=Decimal(EARNER_PRICE),
            line_revenue=Decimal(EARNER_PRICE * EARNER_QTY),
            source_recorded_at=None, source_ref={"record_type": "invoice"}))

    for i in range(3):
        day = AS_OF - timedelta(days=200 - 30 * i)
        s.add(models.CostRecord(
            cost_record_id=f"cst_a{i}", organization_id=ORG,
            external_ref=f"BILL-A{i}:1", product_id="p1", date=day,
            qty=Decimal("10"), unit_cost=Decimal(EARNER_COST),
            rate=Decimal(EARNER_COST), source_recorded_at=_stamp(day),
            source_ref={"record_type": "bill"}))
        s.add(models.CostRecord(
            cost_record_id=f"cst_b{i}", organization_id=ORG,
            external_ref=f"BILL-B{i}:1", product_id="p2", date=day,
            qty=Decimal("10"), unit_cost=Decimal(SINKER_COST),
            rate=Decimal(SINKER_COST), source_recorded_at=_stamp(day),
            source_ref={"record_type": "bill"}))
    s.flush()


@pytest.fixture()
def client() -> TestClient:
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(quote_diagnosis.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


EARNER = {"line_id": "L1", "product_id": "p1", "customer_id": "c1",
          "qty": EARNER_QTY, "quoted_unit_price": EARNER_QUOTE}
SINKER = {"line_id": "L2", "product_id": "p2", "customer_id": "c1",
          "qty": SINKER_QTY, "quoted_unit_price": SINKER_PRICE}


def _assess(c, email, *, lines=None, quote_id=QUOTE, record=True) -> dict:
    r = c.post("/api/v1/quote-diagnosis/assess", headers=_hdr(c, email), json={
        "quote_id": quote_id, "record": record,
        "lines": [EARNER, SINKER] if lines is None else lines})
    assert r.status_code == 200, r.text
    return r.json()


def _sweep_both_costs(payload) -> None:
    """The whole response, against both purchase costs on this quote."""
    for cost in (EARNER_COST, SINKER_COST):
        cost_sweep.assert_no_cost(payload, cost=cost)


# ── where the roll-up lands ──────────────────────────────────────────────────

def test_the_rollup_extends_the_quote_response_rather_than_adding_an_endpoint():
    """Named for what it is guarding: one response about one quote.

    A parallel endpoint would be a second place deciding what a quote comes to,
    and the two would disagree on the day somebody changed one of them — which is
    the argument ``_diagnose`` itself was extracted on.
    """
    assert [r.path for r in quote_diagnosis.router.routes
            if "rollup" in r.path or "coverage" in r.path] == []


def test_the_owner_gets_the_rollup_and_the_coverage_on_the_existing_payload(client):
    payload = _assess(client, MANAGER)

    assert set(payload) == {"quote_id", "as_of", "cutover_known", "coverage",
                            "rollup", "lines"}
    assert payload["quote_id"] == QUOTE
    assert payload["rollup"]["quote_id"] == QUOTE
    assert payload["coverage"]["quote_id"] == QUOTE


def test_both_roles_are_told_the_same_thing_about_what_was_checked(client):
    """One quote, one set of counts. ``QuoteRollup`` holds the coverage rather
    than restating it precisely so this cannot come apart."""
    owner = _assess(client, MANAGER)
    desk = _assess(client, SALES, record=False)

    assert desk["coverage"] == owner["coverage"]
    assert desk["coverage"]["renders"] is True
    assert desk["coverage"]["headline"]


# ── the property this whole engine exists for ────────────────────────────────

def test_a_loss_making_line_reaches_the_owner_when_the_quote_total_is_healthy(
        client):
    """End to end, on the shape that buries a loss.

    The quote's blended margin is positive and the loss-making line renders no
    card at all — its price is exactly what this customer has always paid, so the
    price engine has nothing to say about it. Reading either the total or the
    cards would miss it entirely. The roll-up names it, with what it loses.
    """
    payload = _assess(client, MANAGER)
    roll = payload["rollup"]

    # The total is healthy, and no card was drawn on the line that is not.
    figures = {f["label"]: f["value"] for f in roll["figures"]}
    assert not figures["Margin"].startswith("-"), figures
    by_line = {ln["line_id"]: ln for ln in payload["lines"]}
    assert by_line["L2"]["renders"] is False
    assert by_line["L1"]["renders"] is True

    # And the line is named anyway, with what it loses and why that is possible.
    assert roll["total_hides_a_loss"] is True
    assert [ln["line_id"] for ln in roll["loss_lines"]] == ["L2"]
    sentence = roll["loss_lines"][0]["sentence"]
    # Named by the line the grid above it draws, not by a platform id.
    assert sentence.startswith("Line L2 loses ")
    assert "at the price quoted" in sentence
    assert "p2" not in sentence
    # The id is still on the record for a caller that wants it.
    assert roll["loss_lines"][0]["product_id"] == "p2"
    assert roll["headline"].startswith("1 line loses money at the price quoted")
    assert "The quote's own total does not show it." in roll["headline"]
    # The note leads with the loss rather than with the arithmetic.
    assert roll["note"].startswith("1 line loses money at the price quoted")


def test_a_quote_with_no_loss_answers_with_an_empty_list_not_a_missing_key(
        client):
    """The other half, without which the test above proves only that a key can
    exist. An empty list is "checked, none found"; a missing key is read as the
    same thing and means something else."""
    payload = _assess(client, MANAGER, lines=[EARNER])
    roll = payload["rollup"]

    assert roll["loss_lines"] == []
    assert roll["total_hides_a_loss"] is False
    assert roll["headline"].startswith("This quote comes to ")
    assert roll["headline"].endswith(
        "No line on it loses money at the price quoted.")


# ── the line the desk's payload stops at ─────────────────────────────────────

def test_the_desk_is_given_coverage_and_has_no_rollup_key_at_all(client):
    payload = _assess(client, SALES, record=False)

    assert "rollup" not in payload
    assert set(payload) == {"quote_id", "as_of", "cutover_known", "coverage",
                            "lines"}
    # Over the WHOLE response, not one line: the quote-level keys are exactly
    # what every existing sweep in this package could not have seen.
    _sweep_both_costs(payload)


def test_the_coverage_served_to_the_desk_carries_no_money_field(client):
    """Structural, and it is the type that makes it so: ``QuoteCoverage``
    declares no cost, margin or value field, so there is nothing withheld here
    that somebody could restore by deleting a line."""
    labels = {f["label"] for f
              in _assess(client, SALES, record=False)["coverage"]["figures"]}
    assert labels == {"Lines diagnosed", "Compared against history",
                      "Not compared", "Carrying no quoted price",
                      "Raised something worth reading"}


def test_the_cost_sweep_would_catch_the_rollup_if_it_ever_reached_the_desk(
        client):
    """The negative control, without which the sweep above proves nothing about
    these keys.

    A sweep that passes is only evidence if the same sweep fails on the payload
    it is meant to reject. So: take the desk's response, which passes, splice the
    owner's roll-up into it, and require the sweep to object. That is the guard
    that would catch a future author publishing ``rollup`` on both branches, or
    growing a money field on ``QuoteCoverage``.
    """
    owner = _assess(client, MANAGER)
    desk = _assess(client, SALES, record=False)
    _sweep_both_costs(desk)

    leaked = {**desk, "rollup": owner["rollup"]}
    with pytest.raises(AssertionError) as caught:
        cost_sweep.assert_no_cost(leaked, cost=SINKER_COST)
    assert f"the cost {SINKER_COST} reached a recipient" in str(caught.value)

    # And by the vocabulary as well as by the figure. A sentinel cost that
    # appears nowhere takes the numeric half of the sweep out of the argument,
    # so what fails is the word: a roll-up whose figures happened to be
    # unprintable in this response would still be caught.
    with pytest.raises(AssertionError) as by_word:
        cost_sweep.assert_no_cost(leaked, cost="no-such-figure")
    assert "reached a salesperson" in str(by_word.value)

    # Each word this feature added to the sweep, on its own. Without this the
    # test above passes on `cost`, which was already there — and the four new
    # entries could be dead without anything noticing.
    for word in ("loss", "profit", "gross", "revenue"):
        with pytest.raises(AssertionError, match=f"{word!r} reached"):
            cost_sweep.assert_no_cost(leaked, cost="no-such-figure",
                                      words=(word,))
        # ...and none of them fires on the desk's own response, so the check is
        # discriminating rather than merely loud.
        cost_sweep.assert_no_cost(desk, cost="no-such-figure", words=(word,))


# ── absence is content ───────────────────────────────────────────────────────

def test_a_quote_where_nothing_is_unusual_still_says_what_was_checked(client):
    """The ordinary quote, and the one an empty panel would lie about.

    Nothing surfaces, so no card is drawn — and both roles are still told how
    much of the quote could be judged and how much could not. A response that
    said nothing here would read as "all clear", which is the failure CLAUDE.md
    §1 names three times.
    """
    for email in (SALES, MANAGER):
        payload = _assess(client, email, lines=[SINKER], record=False)
        assert [ln["renders"] for ln in payload["lines"]] == [False]

        coverage = payload["coverage"]
        assert coverage["renders"] is True
        assert "1 of 1 line had enough comparable history" in coverage["headline"]
        assert "Nothing on the lines that could be judged was unusual enough" \
            in coverage["headline"]


def test_a_quote_nothing_could_be_judged_on_never_reads_as_a_pass(client):
    """A line with no price is counted and never valued at zero, and the
    sentence says so in the engine's own words."""
    payload = _assess(client, MANAGER, record=False, lines=[
        {"line_id": "L9", "product_id": "p1", "customer_id": "c1",
         "qty": 1, "quoted_unit_price": None}])

    coverage = payload["coverage"]
    assert "carries no quoted price at all" in coverage["headline"]
    assert payload["rollup"]["figures"][0] == {"label": "Quote value",
                                               "value": "₹0"}


# ── the stored projection ────────────────────────────────────────────────────

def test_a_stored_rollup_totals_money_and_says_why_it_names_no_factor(client):
    """The second producer. Every figure is a column; the split is not one.

    A blank line where the dominant factor goes would read as "the price and the
    cost both behaved", which is the one thing it does not mean — so the refusal
    is written out and names both ways it is reached.
    """
    _assess(client, MANAGER)
    stored = client.get(f"/api/v1/quote-diagnosis/quote/{QUOTE}",
                        headers=_hdr(client, MANAGER)).json()

    roll = stored["rollup"]
    # The money is real: the stored rows carry the price, the quantity and the
    # cost baseline, so the loss line survives being read back.
    assert [ln["line_id"] for ln in roll["loss_lines"]] == ["L2"]
    assert roll["total_hides_a_loss"] is True
    figures = {f["label"]: f["value"] for f in roll["figures"]}
    assert figures["Quote value"].startswith("₹")

    assert roll["dominant"].startswith(
        "No line's margin movement on this quote could be split")
    assert "read back from the store" in roll["dominant"]
    assert "No line's margin movement could be split" in roll["note"]


def test_a_stored_quote_gives_the_desk_coverage_and_no_rollup(client):
    _assess(client, MANAGER)
    stored = client.get(f"/api/v1/quote-diagnosis/quote/{QUOTE}",
                        headers=_hdr(client, SALES)).json()

    assert "rollup" not in stored
    assert stored["coverage"]["renders"] is True
    _sweep_both_costs(stored)


# ── the options on a line ────────────────────────────────────────────────────

def test_both_roles_are_offered_the_same_option_in_the_same_words(client):
    """The one consideration this quote's evidence supports on both sides.

    Two past transactions were located and could not be used, so the range may
    not be describing a population — the engine already prints that as a note,
    and the option is the step a person can take about it. It is worded once, on
    the server, and both readers get that wording.
    """
    owner = _assess(client, MANAGER)["lines"][0]
    desk = _assess(client, SALES, record=False)["lines"][0]

    assert owner["line_id"] == desk["line_id"] == "L1"
    codes = [c["code"] for c in desk["considerations"]["items"]]
    assert considerations.CHECK_THE_COMPARISON in codes

    mine = [c for c in owner["considerations"]["items"]
            if c["code"] == considerations.CHECK_THE_COMPARISON][0]
    theirs = [c for c in desk["considerations"]["items"]
              if c["code"] == considerations.CHECK_THE_COMPARISON][0]
    assert mine == theirs
    assert theirs["label"] == "Check whether these past transactions are comparable"
    assert "EVIDENCE_WITHHELD" in theirs["rests_on"]
    # Every allowlisted option publishes no magnitude — a margin grade beside
    # the price the caller sent is the cost in one step.
    assert theirs["severity"] is None


def test_the_desk_is_never_offered_an_option_outside_the_allowlist(client):
    payload = _assess(client, SALES, record=False)
    for line in payload["lines"]:
        codes = {c["code"] for c in line["considerations"]["items"]}
        assert codes <= considerations.OPERATIONS_CONSIDERATIONS, codes
    _sweep_both_costs(payload)


def test_a_line_with_no_option_says_what_was_weighed_rather_than_nothing(client):
    """An empty list rendered alone reads as "nothing to do here", which is
    indistinguishable from "nothing was looked at"."""
    line = _assess(client, MANAGER, lines=[SINKER], record=False)["lines"][0]

    block = line["considerations"]
    assert block["items"] == []
    assert block["reason"] == considerations.NOTHING_TO_WEIGH
    assert block["note"].startswith(
        "Nothing on this line was found worth interrupting anybody about")


def test_a_stored_line_says_its_options_are_not_on_the_record(client):
    """The fourth instance of one pattern. An empty list here would read as
    "there is nothing you could do about this line"."""
    _assess(client, MANAGER)
    for email in (SALES, MANAGER):
        stored = client.get(f"/api/v1/quote-diagnosis/quote/{QUOTE}",
                            headers=_hdr(client, email)).json()
        block = [ln for ln in stored["lines"]
                 if ln["line_id"] == "L1"][0]["considerations"]
        assert block["items"] == []
        assert block["reason"] == render.NOT_ON_STORED_RECORD
        assert render.NOT_ON_STORED_RECORD not in block["note"]
        assert block["note"].startswith(
            "This is the diagnosis as it was stored")
        assert "Re-assess this line to see them." in block["note"]


# ── how a rejection gets back ────────────────────────────────────────────────

def test_an_option_is_rejected_through_the_dismissal_path_that_already_exists(
        client):
    """The whole of how a consideration reaches feedback, with no second path.

    A consideration names the line whose diagnosis produced it; that line's
    stored diagnosis is what ``POST /{id}/dismiss`` already points at, and the
    vocabulary is the one ``GET /reasons`` already serves. Rejecting the finding
    rejects the option resting on it, because no option here survives its finding
    being wrong.
    """
    line = _assess(client, SALES)["lines"][0]
    option = [c for c in line["considerations"]["items"]
              if c["code"] == considerations.CHECK_THE_COMPARISON][0]

    # The option points at the line the card is about, and at nothing else.
    assert option["line_id"] == line["line_id"]

    served = client.get("/api/v1/quote-diagnosis/reasons",
                        headers=_hdr(client, SALES)).json()["reasons"]
    codes = {r["code"] for r in served}
    # The refusal this option invites is already in that vocabulary.
    assert "COMPARISON_IS_WRONG" in codes

    posted = client.post(
        f"/api/v1/quote-diagnosis/{line['quote_diagnosis_id']}/dismiss",
        headers=_hdr(client, SALES),
        json={"reason_code": "COMPARISON_IS_WRONG",
              "note": "these were a different grade"})
    assert posted.status_code == 200, posted.text
    assert posted.json()["dismissed"] is True


def test_the_options_carry_no_reason_vocabulary_of_their_own(client):
    """A second vocabulary would split the one labelled dataset this engine has
    into two nobody can join. The response is checked as well as the module: a
    front end reads the payload, not the import graph."""
    line = _assess(client, MANAGER, record=False)["lines"][0]
    block = line["considerations"]

    assert set(block) == {"line_id", "renders", "reason", "items", "note"}
    # The block's own reason is its offered/nothing-to-weigh code and never a
    # dismissal reason — one vocabulary, and this is not a second one.
    assert block["reason"] == considerations.OFFERED
    assert block["reason"] not in render.DISMISS_REASONS
    for option in block["items"]:
        assert set(option) == {"code", "label", "detail", "line_id",
                               "rests_on", "strength_word", "severity",
                               "surfaces"}
        assert option["code"] not in render.DISMISS_REASONS


# ── the half no HTTP fixture on this book reaches ────────────────────────────
#
# The seed above produces no attribution — the band collapses to one price and
# the cost is flat, so ``drivers`` declines to split anything, which is the
# correct answer and leaves ``render_rollup``'s *other* branch unexercised. A
# renderer branch nothing runs is a branch that is wrong the first time somebody
# needs it, so it is exercised directly here, on a ``LineFacts`` built by hand.
# The roll-up itself is real: ``roll_up`` does the arithmetic.

def _attributed(effect_per_unit: str, code: str) -> drivers.Attribution:
    return drivers.Attribution(
        movement_pp=Decimal("-0.04"),
        drivers=(drivers.Driver(
            code=code, severity="MAJOR", strength="STRONG",
            effect_pp=Decimal("-0.04"), effect_per_unit=Decimal(effect_per_unit),
            cited=("ev_1",), basis="against the band median and the historical "
                                   "purchase cost"),),
        reconciles=True, residual_pp=Decimal("0"),
        reason=drivers.PRICE_THEN_COST,
        basis="the same line at the band median price and the historical cost")


def _facts(line_id: str, *, attribution: drivers.Attribution) -> rollup.LineFacts:
    return rollup.LineFacts(
        line_id=line_id, product_id="p1", qty=Decimal("10"),
        quoted_unit_price=Decimal("1000"), unit_cost=Decimal("600"),
        codes=("BELOW_HISTORICAL_RANGE",), strength="STRONG", surfaces=True,
        attribution=attribution, thresholds_version="ci_1")


def test_a_named_dominant_factor_is_spelled_in_money_and_scoped_to_its_lines():
    th = CommercialThresholds()
    quote = rollup.roll_up(
        [_facts("L1", attribution=_attributed("-50", drivers.COST_LEVEL_EFFECT)),
         _facts("L2", attribution=_attributed("-30", drivers.COST_LEVEL_EFFECT))],
        quote_id="q1")

    view = render.render_rollup(quote, th=th)

    # Money, not percentage points — the only thing that is summable — and the
    # sign convention stated rather than assumed.
    assert view.dominant == (
        "The largest factor across this quote is the cost level, at -₹800 over "
        "all 2 lines whose movement could be split. Negative means that factor "
        "cost margin.")


def test_a_factor_measured_on_some_of_the_lines_says_which():
    """A dominant factor drawn from one line of forty is not a statement about
    the quote, which is why ``DriverTotal`` carries the count at all."""
    th = CommercialThresholds()
    quote = rollup.roll_up(
        [_facts("L1", attribution=_attributed("-50", drivers.COST_LEVEL_EFFECT)),
         _facts("L2", attribution=_attributed("-5", drivers.PRICE_POSITION_EFFECT))],
        quote_id="q1")

    view = render.render_rollup(quote, th=th)

    assert "1 of the 2 lines whose movement could be split" in view.dominant
    assert view.dominant.startswith(
        "The largest factor across this quote is the cost level, at -₹500 ")
