"""Decisions folded from Business State.

Three claims are worth testing hard; everything else follows from them.

1. **A decision is reproducible from events.** Replay the log into an empty
   read model, re-fold, re-detect, and the same queue comes back — the same
   rows, the same money, the same rank. If that fails, a decision is not
   derived from state and the layering is a story.
2. **Every number on a card is recomputable from the evidence beside it.** Not
   "looks plausible": the test does the arithmetic itself and compares.
3. **The queue's order is deterministic and explainable.** The score is
   re-derived from the published ranking working.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete

from app import clock
from app.commercial.config import CommercialThresholds
from app.commercial.policy import load_for_org
from app.decisions.opportunities import (STATE_TYPES, generate_from_state,
                                         policy_from)
from app.domain import models
from app.domain.enums import (DecisionOrigin, DecisionStatus, DecisionType,
                              RESTRICTED_DECISION_TYPES, STATE_DECISION_TYPES)
from app.ingestion.sync import SyncService
from app.repositories import DecisionRepository
from app.state import queue
from app.state.engine import build
from app.state.opportunities import ACTIONS, DETECTORS
from app.state.replay import replay

#: The seeded organization, so the API fixture below signs in as a real
#: user of the same book the detectors ran over.
ORG = "org_pie"

#: The day this book is read on — the *business* date, never the machine's.
#:
#: This was a hardcoded calendar date, which is a trap the whole suite fell into
#: at 18:30 UTC every day. The organization runs in Asia/Kolkata, so from that
#: moment the business date is tomorrow while ``date.today()`` is still today.
#: A stock observation is stamped with the business date, and a fold pinned to
#: the machine's date sits one day *behind* it — so the observation is in the
#: future, the fold silently excludes it, and every inventory decision
#: disappears. Twenty-six tests went red for five and a half hours a day and
#: green again overnight, which is the worst kind of failing test.
#:
#: The dated documents below are written relative to this, so they move with it.
TODAY = clock.today(CommercialThresholds().timezone)

#: INV-3's due date: Wednesday of the week AFTER the one TODAY falls in —
#: never overdue, never in the current-week bucket, always beyond a one-week
#: horizon, wherever the live TODAY lands. It used to be the literal
#: 2026-08-19, which was all of those things only until the calendar reached
#: that week; the cash-projection tests failed the day it did. A fixture date
#: whose *relationship* to today is the claim must be derived from today.
DUE_NEXT_WEEK = TODAY + timedelta(days=(7 - TODAY.isoweekday()) + 3)


class _Source:
    """A book with one of each situation the detectors can find."""

    def __init__(self, **over: Any) -> None:
        self._over = over

    def list_contacts(self):
        return self._over.get("contacts", [
            {"contact_id": "c1", "contact_name": "Pitti Engineering",
             "status": "active"}])

    def list_vendors(self):
        # Two, so a share is a real fraction rather than trivially 100%, and so
        # one item can have a second source and correctly NOT be sole-sourced.
        return [{"contact_id": "v1", "contact_name": "Kennametal India",
                 "status": "active"},
                {"contact_id": "v2", "contact_name": "Sandvik Asia",
                 "status": "active"}]

    def list_users(self):
        return []

    def list_items(self):
        return self._over.get("items", [
            # Dead: 1,000 on hand at 500, nothing sold for two years.
            {"item_id": "dead", "name": "Reamer 12H7", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 1000,
             "available_stock": 1000, "actual_available_stock": 1000,
             "purchase_rate": "500"},
            # Slow: last sold seven months ago.
            {"item_id": "slow", "name": "Face mill 63mm", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 200,
             "available_stock": 200, "actual_available_stock": 200,
             "purchase_rate": "400"},
            # Oversold: selling, and committed 60 beyond the shelf.
            {"item_id": "short", "name": "CNMG 120408", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 40,
             "available_stock": 40, "actual_available_stock": -60,
             "purchase_rate": "300"},
            # Below its reorder point, and selling.
            {"item_id": "low", "name": "Drill 8.5mm", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 5,
             "available_stock": 5, "actual_available_stock": 5,
             "reorder_level": 100, "purchase_rate": "200"},
            # Selling steadily, and hugely overstocked against that rate.
            {"item_id": "excess", "name": "Tap M10x1.5", "unit": "pcs",
             "status": "active", "track_inventory": True,
             "item_type": "inventory", "stock_on_hand": 800,
             "available_stock": 800, "actual_available_stock": 800,
             "purchase_rate": "50"},
        ])

    def list_invoices(self, skip=None):
        return self._over.get("invoices", [
            # Settled long ago: a zero balance must produce no receivable at
            # all, which is the case a "count every invoice" fold gets wrong.
            {"invoice_id": "inv1", "invoice_number": "INV-1", "customer_id": "c1",
             "date": "2024-06-01", "due_date": "2024-07-01", "status": "paid",
             "total": "9000", "balance": "0",
             "line_items": [{"line_item_id": "l1", "item_id": "dead",
                             "quantity": 10, "rate": "900", "item_total": "9000"}]},
            # Overdue: due 2026-02-04, still owed in full on TODAY.
            {"invoice_id": "inv2", "invoice_number": "INV-2", "customer_id": "c1",
             "date": "2026-01-05", "due_date": "2026-02-04", "status": "overdue",
             "total": "16700", "balance": "16700",
             "line_items": [{"line_item_id": "l1", "item_id": "slow",
                             "quantity": 20, "rate": "700", "item_total": "14000"},
                            {"line_item_id": "l2", "item_id": "excess",
                             "quantity": 30, "rate": "90", "item_total": "2700"}]},
            # Recent trade, so these lines are healthy rather than idle. Owed,
            # but not yet due — it belongs in the exposure and not in the
            # collection. The due date is derived from TODAY because "not yet
            # due" is a claim about the clock: a hardcoded date here was true
            # when written and started failing the week the calendar caught it.
            # Two weeks out keeps it off week one of the cash timeline and past
            # a one-week horizon, which is what the projection tests rely on.
            {"invoice_id": "inv3", "invoice_number": "INV-3", "customer_id": "c1",
             "date": "2026-07-20", "due_date": DUE_NEXT_WEEK.isoformat(),
             "status": "sent", "total": "85200", "balance": "85200",
             "line_items": [{"line_item_id": "l1", "item_id": "short",
                             "quantity": 100, "rate": "600", "item_total": "60000"},
                            {"line_item_id": "l2", "item_id": "low",
                             "quantity": 50, "rate": "450", "item_total": "22500"},
                            {"line_item_id": "l3", "item_id": "excess",
                             "quantity": 30, "rate": "90", "item_total": "2700"}]},
        ])

    def list_customer_payments(self, skip=None):
        return self._over.get("payments", [
            # Settles inv1 in full. The only thing the receivables fold takes
            # from a receipt is the date.
            {"payment_id": "pay1", "customer_id": "c1", "date": "2024-07-15",
             "amount": "9000", "payment_mode": "banktransfer",
             "invoices": [{"invoice_id": "inv1", "invoice_number": "INV-1",
                           "date": "2024-06-01", "due_date": "2024-07-01",
                           "amount_applied": "9000"}]},
        ])

    def list_bills(self, skip=None):
        return self._over.get("bills", [
            # Overdue payable: ₹3,00,000, due four months ago. v1 also becomes
            # the sole source of "dead" and of "slow".
            {"bill_id": "b1", "bill_number": "BILL-1", "vendor_id": "v1",
             "date": "2026-03-01", "due_date": "2026-04-01", "status": "open",
             "total": "300000", "balance": "300000",
             "line_items": [{"line_item_id": "l1", "item_id": "dead",
                             "quantity": 1000, "rate": "500"}]},
            {"bill_id": "b2", "bill_number": "BILL-2", "vendor_id": "v1",
             "date": "2026-03-05", "due_date": "2026-04-05", "status": "paid",
             "total": "80000", "balance": "0",
             "line_items": [{"line_item_id": "l1", "item_id": "slow",
                             "quantity": 200, "rate": "400"}]},
            # "excess" is bought from BOTH suppliers, so it is the control:
            # neither may report it as sole-sourced.
            {"bill_id": "b3", "bill_number": "BILL-3", "vendor_id": "v1",
             "date": "2026-04-01", "due_date": "2026-05-01", "status": "paid",
             "total": "20000", "balance": "0",
             "line_items": [{"line_item_id": "l1", "item_id": "excess",
                             "quantity": 400, "rate": "50"}]},
            {"bill_id": "b4", "bill_number": "BILL-4", "vendor_id": "v2",
             "date": "2026-04-10", "due_date": "2026-05-10", "status": "paid",
             "total": "20000", "balance": "0",
             "line_items": [{"line_item_id": "l1", "item_id": "excess",
                             "quantity": 400, "rate": "50"}]},
        ])

    def list_purchase_orders(self):
        return self._over.get("purchase_orders", [
            {"purchaseorder_id": "po1", "vendor_id": "v1", "date": "2026-05-01",
             "status": "issued", "received_status": "pending",
             "quantity_yet_to_receive": 500, "total": "250000"},
        ])


def _seed(session, **over) -> None:
    SyncService(session, _Source(**over), ORG).run()
    session.commit()


def _fold(session, on: date = TODAY):
    th = load_for_org(session, ORG)
    build(session, ORG, as_of=on, thresholds_version=th.version)
    session.commit()
    return th


def _generate(session, on: date = TODAY) -> dict:
    th = _fold(session, on)
    report = generate_from_state(session, ORG, thresholds=th, as_of=on)
    session.commit()
    return report


def _state_rows(session) -> list[models.Decision]:
    return session.query(models.Decision).filter_by(
        organization_id=ORG, origin=DecisionOrigin.STATE.value).all()


def _by_type(session) -> dict[str, models.Decision]:
    """One row per type. The fixture is built so each type fires once, which
    is what makes the arithmetic assertions below readable."""
    rows = _state_rows(session)
    by_type = {d.decision_type: d for d in rows}
    assert len(by_type) == len(rows), (
        "the fixture is meant to produce one decision per type; "
        f"got {[(d.decision_type, d.subject_entity_id) for d in rows]}")
    return by_type


# ── the claim that makes the layering real ──────────────────────────────────
def test_every_decision_is_reproducible_from_the_event_log(session):
    """Replay the events into an empty read model, re-fold, re-detect. The same
    queue must come back — same rows, same money, same rank.

    This is the honest test of "Business State is the only source of decision
    intelligence". If a detector were quietly reading a sales line, the wipe
    below would change its answer.
    """
    _seed(session)
    _generate(session)
    before = {
        d.decision_type: (d.priority_deterministic_base, d.impact["financial"],
                          d.subject_entity_id, tuple(d.actions))
        for d in _by_type(session).values()}
    assert before, "the fixture must produce decisions to compare"

    # Wipe everything derived, keeping only the masters and the event log.
    # Every derived table, including the ones a new event type adds. A table
    # left out of this list survives the wipe, and the replay then "reproduces"
    # rows it never touched — which is how a lossy log passes its own test.
    for model in (models.BusinessState, models.StateTransition, models.Decision,
                  models.SalesTxn, models.CostRecord, models.StockSnapshot,
                  models.BillDoc, models.PurchaseOrderDoc, models.InvoiceDoc,
                  models.SalesOrderDoc, models.VendorPaymentDoc):
        session.execute(delete(model).where(model.organization_id == ORG))
    session.commit()

    assert replay(session, ORG).unresolved == []
    session.commit()
    _generate(session)

    after = {
        d.decision_type: (d.priority_deterministic_base, d.impact["financial"],
                          d.subject_entity_id, tuple(d.actions))
        for d in _by_type(session).values()}
    assert after == before


def test_a_detector_never_reads_a_sales_line(session):
    """Structural, not incidental: Business State is the only input, so
    deleting the read model must not change what the detectors find."""
    _seed(session)
    _generate(session)
    before = {d.decision_type: d.impact["financial"] for d in _by_type(session).values()}

    session.execute(delete(models.SalesTxn).where(models.SalesTxn.organization_id == ORG))
    session.execute(delete(models.CostRecord).where(models.CostRecord.organization_id == ORG))
    session.commit()
    _generate(session)

    assert {d.decision_type: d.impact["financial"]
            for d in _by_type(session).values()} == before


# ── every number is recomputable from the evidence beside it ────────────────
def test_the_dead_stock_figure_is_the_arithmetic_of_its_own_evidence(session):
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.INV_DEAD_STOCK.value]
    ev = d.confidence["evidence"]

    on_hand = Decimal(ev["on_hand"])
    rate = Decimal(ev["purchase_rate"])
    assert Decimal(d.impact["financial"]) == on_hand * rate == Decimal("500000")
    # And the monthly drain is that at the annual carrying rate ÷ 12.
    annual = Decimal(ev["carrying_annual_pct"])
    assert Decimal(d.impact["monthly"]) == (
        (on_hand * rate * annual / 12).quantize(Decimal("0.01")))


def test_the_overdue_payable_figure_is_zoho_s_balance(session):
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.CASH_PAYABLE_OVERDUE.value]
    assert Decimal(d.impact["financial"]) == Decimal("300000")
    assert d.confidence["evidence"]["overdue_bills"] == 1
    assert d.impact["operational"]["days_past_due"] == (TODAY - date(2026, 4, 1)).days


# ── receivables ─────────────────────────────────────────────────────────────
def test_the_overdue_receivable_figure_is_zoho_s_balance(session):
    """The mirror of the payable test, and the same rule: what is owed is read,
    never derived from total minus the receipts we happen to have seen."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.CASH_RECEIVABLE_OVERDUE.value]
    # inv2 alone: inv1 is settled and inv3 is not due yet.
    assert Decimal(d.impact["financial"]) == Decimal("16700")
    assert d.confidence["evidence"]["overdue_invoices"] == 1
    assert d.impact["operational"]["days_past_due"] == (TODAY - date(2026, 2, 4)).days


def test_a_settled_invoice_is_not_a_receivable(session):
    """A zero balance is collected. Counting every invoice, or deriving the
    balance from the total, would put a customer who paid two years ago at the
    top of a collections list."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.CASH_RECEIVABLE_OVERDUE.value]
    op = d.impact["operational"]
    # Three invoices exist; two are open, one of those is overdue.
    assert op["open_invoices"] == 2
    assert Decimal(op["outstanding"]) == Decimal("101900")   # 16,700 + 85,200
    assert Decimal(d.impact["financial"]) < Decimal(op["outstanding"]), (
        "the collection card must be sized on the overdue portion, not the "
        "whole balance — otherwise it is the exposure card wearing another name")


def test_an_invoice_not_yet_due_is_owed_but_not_overdue(session):
    """inv3 is unpaid and dated before today, but its terms have not expired.
    Ageing on the invoice date rather than the due date would make it late."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.CASH_RECEIVABLE_OVERDUE.value]
    assert Decimal(d.impact["financial"]) == Decimal("16700")
    exposure = _by_type(session)[DecisionType.CASH_CREDIT_EXPOSURE.value]
    # The same rupees appear in the exposure card, because that card is about
    # the whole balance. The two `basis` sentences are what stop a reader
    # adding them together.
    assert Decimal(exposure.impact["financial"]) == Decimal("101900")
    assert exposure.impact["basis"] != d.impact["basis"]


def test_credit_exposure_is_a_share_of_the_book_not_a_lateness(session):
    """The share is recomputed here rather than trusted: outstanding over the
    whole receivables book, against the policy threshold."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.CASH_CREDIT_EXPOSURE.value]
    ev = d.confidence["evidence"]
    share = Decimal(ev["outstanding"]) / Decimal(ev["receivables_book"])
    assert share == Decimal(ev["share_of_receivables"])
    assert share >= Decimal(ev["exposure_share_threshold"])
    # One customer in this book, so they are the whole of it.
    assert Decimal(ev["share_of_receivables"]) == Decimal("1")


def test_a_receipt_contributes_its_date_and_never_its_amount(session):
    """The balance already nets every applied receipt. Folding the amount too
    would count each payment twice — once as the reduction it caused and once
    as itself."""
    from app.state.engine import load
    from app.state.reducers.receivables import RECEIVABLES

    _seed(session)
    _generate(session)
    rows = load(session, ORG, RECEIVABLES, TODAY)
    (value,) = rows.values()
    assert value["last_paid_on"] == "2024-07-15"
    assert value["receipts"] == 1
    # The ₹9,000 receipt is nowhere in the money. Outstanding is the two open
    # balances and nothing else.
    assert Decimal(value["outstanding"]) == Decimal("101900")


def test_an_invoice_with_no_terms_is_counted_but_never_aged(session):
    """Defaulting a missing due date to the invoice date would report every
    untermed invoice as overdue from the day it was raised."""
    _seed(session, invoices=[
        {"invoice_id": "inv9", "invoice_number": "INV-9", "customer_id": "c1",
         "date": "2025-01-01", "status": "sent",
         "total": "500000", "balance": "500000",
         "line_items": [{"line_item_id": "l1", "item_id": "dead",
                         "quantity": 10, "rate": "50000",
                         "item_total": "500000"}]},
    ], payments=[])
    _generate(session)
    from app.state.engine import load
    from app.state.reducers.receivables import RECEIVABLES

    rows = load(session, ORG, RECEIVABLES, TODAY)
    (value,) = rows.values()
    assert Decimal(value["outstanding"]) == Decimal("500000")
    assert value["unageable_invoices"] == 1
    assert value.get("overdue_balance") in (None, "0")
    assert not [d for d in _state_rows(session)
                if d.decision_type == DecisionType.CASH_RECEIVABLE_OVERDUE.value], (
        "an invoice nobody gave terms for is not overdue")


def test_the_oversold_shortfall_is_priced_at_what_the_item_actually_sells_for(session):
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.INV_OVERSOLD.value]
    ev = d.confidence["evidence"]
    per_unit = Decimal(ev["revenue"]) / Decimal(ev["units_sold"])
    short = -Decimal(ev["actual_available"])
    assert Decimal(d.impact["financial"]) == (short * per_unit).quantize(Decimal("0.01"))


# ── suppliers ───────────────────────────────────────────────────────────────
def test_the_supplier_state_is_keyed_by_vendor_and_item(session):
    """The first composite-key state. Vendor and product are stored as fields
    as well as being in the key, so a detector groups rather than parsing."""
    from app.state.engine import load
    from app.state.reducers.supplier import SUPPLIER

    _seed(session)
    _generate(session)
    rows = load(session, ORG, SUPPLIER, TODAY)
    # v1 bought dead, slow and excess; v2 bought excess. Four pairs.
    assert len(rows) == 4
    for key, value in rows.items():
        assert key == f"{value['vendor_id']}:{value['product_id']}"


def test_spend_concentration_is_a_share_of_what_was_actually_bought(session):
    """Recomputed here rather than trusted: this vendor's year over the whole
    book's year, against the policy threshold."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.SUP_SPEND_CONCENTRATION.value]
    ev = d.confidence["evidence"]
    share = Decimal(ev["spend_recent"]) / Decimal(ev["purchase_book"])
    # Stored to four places, so the card and this test agree on the digits a
    # reader can actually see.
    assert share.quantize(Decimal("0.0001")) == Decimal(ev["share_of_spend"])
    assert share >= Decimal(ev["supplier_share_threshold"])
    # v1: dead 1000x500 + slow 200x400 + excess 400x50 = 600,000.
    # v2: excess 400x50 = 20,000. Book = 620,000.
    assert Decimal(ev["spend_recent"]) == Decimal("600000")
    assert Decimal(ev["purchase_book"]) == Decimal("620000")


def test_an_item_bought_from_two_suppliers_is_not_sole_sourced(session):
    """The control in the fixture. "excess" comes from both vendors, so neither
    may claim it — a sole-source rule that counted per bill rather than per
    distinct supplier would get exactly this case wrong."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.SUP_SOLE_SOURCE.value]
    # v1 solely supplies dead (500,000) and slow (80,000) — not excess.
    assert d.confidence["evidence"]["sole_sourced_items"] == 2
    assert Decimal(d.impact["financial"]) == Decimal("580000")
    assert d.subject_entity_type == "VENDOR"


def test_the_sole_source_card_never_claims_no_alternative_exists(session):
    """We read a purchase ledger, not a market. The card must say "no
    alternative in your own purchase history" — stated as a finding rather than
    a prompt, it asserts something the platform cannot know."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.SUP_SOLE_SOURCE.value]
    text = d.rationale.lower()
    assert "purchase history" in text
    assert "does not mean no alternative exists" in text


def test_supplier_spend_is_ranked_on_the_year_not_on_all_time(session):
    """Every other impact in the queue is a stock. Ranking a lifetime flow
    against those would put a long-standing supplier on top forever, for no
    reason but longevity."""
    from app.state.engine import load
    from app.state.reducers.supplier import SUPPLIER

    # The same purchases, but dated four years ago — outside the window.
    old_bills = [
        {"bill_id": "old1", "bill_number": "OLD-1", "vendor_id": "v1",
         "date": "2022-03-01", "status": "paid", "total": "500000", "balance": "0",
         "line_items": [{"line_item_id": "l1", "item_id": "dead",
                         "quantity": 1000, "rate": "500"}]},
    ]
    _seed(session, bills=old_bills)
    _generate(session)
    rows = load(session, ORG, SUPPLIER, TODAY)
    (value,) = rows.values()
    assert Decimal(value["spend"]) == Decimal("500000"), "lifetime still counts it"
    assert "spend_recent" not in value, "but the ranked window does not"
    assert not [d for d in _state_rows(session)
                if d.decision_type == DecisionType.SUP_SPEND_CONCENTRATION.value], (
        "a supplier nobody has bought from in a year is not a live concentration")


def test_a_cost_line_whose_bill_named_no_supplier_is_skipped_not_bucketed(session):
    """Attributing it to an "unknown" vendor would create a phantom supplier
    that accumulates spend and eventually wins the concentration card."""
    from app.state.engine import load
    from app.state.reducers.supplier import SUPPLIER

    _seed(session, bills=[
        {"bill_id": "nov", "bill_number": "NO-VENDOR", "date": "2026-03-01",
         "status": "open", "total": "900000", "balance": "900000",
         "line_items": [{"line_item_id": "l1", "item_id": "dead",
                         "quantity": 1000, "rate": "900"}]},
    ])
    _generate(session)
    assert load(session, ORG, SUPPLIER, TODAY) == {}
    assert not [d for d in _state_rows(session)
                if d.decision_type.startswith("SUP_SPEND")]


# ── the queue ───────────────────────────────────────────────────────────────
def test_the_queue_is_ranked_by_money_and_says_so(session):
    _seed(session)
    _generate(session)
    rows = DecisionRepository(session, ORG).list()
    state_rows = [d for d in rows if d.origin == DecisionOrigin.STATE.value]
    assert len(state_rows) >= 3

    scores = [d.priority_deterministic_base for d in state_rows]
    assert scores == sorted(scores, reverse=True)

    # Money decides the order — but only among rows whose urgency is equal,
    # because the score is money points plus lateness points and both are
    # capped. This used to pin a decision type, which made it a hostage to the
    # fixture: adding a supplier changed which situation was largest, and two
    # situations above the money cap are then separated by urgency alone, which
    # is the ranking working rather than breaking.
    unhurried = [d for d in state_rows
                 if d.confidence["ranking"]["urgency_points"] == 0]
    assert len(unhurried) >= 2, "need two comparable rows to compare"
    for earlier, later in zip(unhurried, unhurried[1:]):
        assert (earlier.priority_deterministic_base
                >= later.priority_deterministic_base)
        # Equal scores mean both hit the money cap; below it, more money must
        # mean a higher score.
        if earlier.priority_deterministic_base > later.priority_deterministic_base:
            assert (Decimal(earlier.impact["financial"])
                    > Decimal(later.impact["financial"]))


def test_every_score_is_reproducible_from_its_published_working(session):
    _seed(session)
    _generate(session)
    for d in _by_type(session).values():
        r = d.confidence["ranking"]
        assert r["money_points"] + r["urgency_points"] == d.priority_deterministic_base
        assert r["money_points"] == min(
            queue.MONEY_CAP,
            int(Decimal(r["financial"]) / Decimal(r["rupees_per_point"])))


def test_an_ai_nudge_can_no_longer_outrank_quantified_money(session):
    """The queue orders on the deterministic base. A signal decision carrying a
    +20 adjustment used to jump the list; now the adjustment is stored, shown,
    and does not decide who is read first."""
    _seed(session)
    _generate(session)
    session.add(models.Decision(
        organization_id=ORG, decision_key="dk_nudged",
        decision_type=DecisionType.CUSTOMER_DECLINE.value,
        subject_entity_type="CUSTOMER", subject_entity_id="c1",
        assigned_role="SALES_MANAGER", origin=DecisionOrigin.SIGNAL.value,
        priority_deterministic_base=50, priority_ai_adjustment=20,
        priority_score=70, priority_band="HIGH",
        status=DecisionStatus.OPEN.value))
    session.commit()

    rows = DecisionRepository(session, ORG).list()
    dead = next(d for d in rows if d.decision_type == DecisionType.INV_DEAD_STOCK.value)
    nudged = next(d for d in rows if d.decision_key == "dk_nudged")
    assert dead.priority_deterministic_base > nudged.priority_deterministic_base
    assert rows.index(dead) < rows.index(nudged)
    assert nudged.priority_score > nudged.priority_deterministic_base   # still stored


def test_urgency_moves_a_decision_but_never_dominates_money(session):
    """A ₹2,000 bill 200 days overdue is an administrative annoyance; a ₹20 lakh
    commitment is not, however fresh."""
    assert queue.urgency_points(2000) == queue.URGENCY_CAP
    assert queue.URGENCY_CAP < queue.MONEY_CAP
    assert queue.urgency_points(None) == 0
    assert queue.urgency_points(-5) == 0


# ── what the layer refuses to do ────────────────────────────────────────────
def test_no_state_decision_ever_reaches_a_salesperson(session):
    """Every impact here is derived from a purchase rate or a payable balance,
    which is cost information. Absent by type, not by field masking."""
    assert STATE_DECISION_TYPES <= RESTRICTED_DECISION_TYPES
    _seed(session)
    _generate(session)
    for d in _by_type(session).values():
        assert d.assigned_user_id is None
        assert d.assigned_role == "SALES_MANAGER"


def test_nothing_in_this_layer_is_interpreted(session):
    _seed(session)
    _generate(session)
    for d in _by_type(session).values():
        assert d.priority_ai_adjustment == 0
        assert d.ai["status"] == "NOT_APPLICABLE"
        assert d.confidence["evidence_sufficiency"] == "DETERMINISTIC"


def test_the_producer_never_imports_the_interpreted_layer():
    """By construction rather than by convention."""
    import ast
    import inspect

    from app.decisions import opportunities
    from app.state import opportunities as detectors
    from app.state import queue as q

    for module in (opportunities, detectors.base, detectors.inventory,
                   detectors.supply, q):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "")] + [a.name for a in node.names]
            assert not any(n == "ai" or n.startswith("ai.") for n in names), module


def test_a_situation_below_materiality_is_not_surfaced(session):
    """A queue that lists every ₹300 line is a queue nobody opens."""
    _seed(session, items=[
        {"item_id": "tiny", "name": "Washer", "unit": "pcs", "status": "active",
         "track_inventory": True, "item_type": "inventory",
         "stock_on_hand": 2, "available_stock": 2, "actual_available_stock": 2,
         "purchase_rate": "10"}],
        invoices=[], bills=[], purchase_orders=[])
    _generate(session)
    assert DecisionType.INV_DEAD_STOCK.value not in _by_type(session)


def test_an_uncosted_line_is_left_out_rather_than_ranked_as_free(session):
    """A line nobody has priced is unknown, not worthless. Ranking it as zero
    would bury real problems under items we simply have not costed."""
    _seed(session, items=[
        {"item_id": "unpriced", "name": "Mystery tool", "unit": "pcs",
         "status": "active", "track_inventory": True, "item_type": "inventory",
         "stock_on_hand": 5000, "available_stock": 5000,
         "actual_available_stock": 5000}],
        invoices=[], bills=[], purchase_orders=[])
    _generate(session)
    assert DecisionType.INV_DEAD_STOCK.value not in _by_type(session)


def test_stock_that_has_never_had_time_to_sell_raises_no_dead_stock_card(session):
    """Never sold is not the same claim as dead, and the queue used to conflate
    them.

    ``_Idle._drafts`` read ``days = idle if idle is not None else at_least`` —
    so a missing sale date satisfied *every* idleness bound automatically, and
    an item first seen this week raised an INV_DEAD_STOCK decision with
    PROPOSE_WRITE_OFF among its actions. Measured on the live SLS book, 81% of
    the value in that band was stock bought in the previous ten weeks.

    The line below is expensive enough to clear materiality and has never sold.
    The only thing keeping it out of the queue is that there has not yet been
    time to sell it.
    """
    _seed(session, items=[
        {"item_id": "justarrived", "name": "Shell mill arbor", "unit": "pcs",
         "status": "active", "track_inventory": True, "item_type": "inventory",
         "stock_on_hand": 40, "available_stock": 40, "actual_available_stock": 40,
         "purchase_rate": "21356"}],
        invoices=[], bills=[], purchase_orders=[])
    _generate(session)
    assert DecisionType.INV_DEAD_STOCK.value not in _by_type(session)
    assert DecisionType.INV_SLOW_MOVING.value not in _by_type(session)


def test_no_detector_reports_an_order_as_late(session):
    """This book records no promised delivery dates. Age is a fact; lateness
    would be an invention."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.SUP_OPEN_COMMITMENT.value]
    assert "late" not in d.rationale.lower()
    assert "overdue" not in d.rationale.lower()
    assert d.impact["operational"]["oldest_age_days"] is not None
    # And it earns no urgency points, because nothing was promised.
    assert d.confidence["ranking"]["urgency_points"] == 0


# ── lifecycle ───────────────────────────────────────────────────────────────
def test_a_second_fold_refreshes_rather_than_duplicating(session):
    _seed(session)
    first = _generate(session)
    second = _generate(session)
    assert first["created"] > 0
    assert second["created"] == 0
    assert second["refreshed"] == first["created"]
    assert len(_state_rows(session)) == first["created"]


def test_a_situation_that_has_gone_away_is_resolved_not_left_open(session):
    """An item that sells is no longer dead stock. A queue that still shows it
    is a queue somebody has to hand-clean."""
    _seed(session)
    _generate(session)
    assert DecisionType.INV_DEAD_STOCK.value in _by_type(session)

    # It sells. Same book, one more invoice line, dated yesterday.
    revived = _Source()
    invoices = list(revived.list_invoices())
    invoices.append({"invoice_id": "inv9", "invoice_number": "INV-9",
                     "customer_id": "c1",
                     "date": (TODAY - timedelta(days=1)).isoformat(),
                     "line_items": [{"line_item_id": "l1", "item_id": "dead",
                                     "quantity": 1, "rate": "900",
                                     "item_total": "900"}]})
    SyncService(session, _Source(invoices=invoices), ORG).run()
    session.commit()
    _generate(session)

    rows = {(d.decision_type, d.subject_entity_id): d for d in _state_rows(session)}
    dead = next(d for (t, _s), d in rows.items()
                if t == DecisionType.INV_DEAD_STOCK.value)
    assert dead.status == DecisionStatus.RESOLVED.value

    # And the situation it has become is surfaced in its place: one sale does
    # not make 1,000 units the right amount to hold, so the same line now
    # carries an excess-cover card with different actions on it.
    excess = next(d for (t, sid), d in rows.items()
                  if t == DecisionType.INV_EXCESS_COVER.value
                  and sid == dead.subject_entity_id)
    assert excess.status == DecisionStatus.OPEN.value
    assert set(excess.actions) != set(dead.actions)


def test_a_dismissed_decision_does_not_come_back_on_the_next_fold(session):
    """Dismissing something and having it reappear tomorrow is how a queue
    teaches people to ignore it."""
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.INV_DEAD_STOCK.value]
    d.status = DecisionStatus.DISMISSED.value
    session.commit()

    _generate(session)
    assert _by_type(session)[DecisionType.INV_DEAD_STOCK.value].status == (
        DecisionStatus.DISMISSED.value)


def test_no_fold_means_no_decisions_rather_than_empty_ones(session):
    _seed(session)
    th = load_for_org(session, ORG)
    report = generate_from_state(session, ORG, thresholds=th)
    session.commit()
    assert report["created"] == 0
    assert report["as_of"] is None
    assert _by_type(session) == {}
    # Every state is named, so "nothing to report" is distinguishable from
    # "nothing is wrong" by a reader who only has the report.
    assert report["states_missing"] == sorted(
        set().union(*(d.states for d in DETECTORS.values())))


def test_one_state_with_no_fold_does_not_silence_the_detectors_that_can_see(session):
    """A book whose bills have not been pulled folds no SUPPLIER state at all —
    there is nothing to fold — so the two supplier detectors have nothing to
    say. That must not silence the nine reading a state that folded perfectly
    well: a shelf full of dead stock is still dead.

    And the gap is named rather than swallowed. No supplier fold on record is
    not evidence that nothing is concentrated; it is a missing input, and §1
    says a missing input is UNKNOWN and never a clean run.
    """
    from app.state.reducers.supplier import SUPPLIER

    _seed(session, bills=[])
    th = _fold(session)
    report = generate_from_state(session, ORG, thresholds=th)
    session.commit()

    assert report["as_of"] == TODAY.isoformat()
    assert report["created"] > 0
    assert DecisionType.INV_DEAD_STOCK.value in {
        d.decision_type for d in _state_rows(session)}
    assert report["states_missing"] == [SUPPLIER]


def test_a_state_that_did_not_fold_does_not_resolve_the_cards_it_would_have_made(session):
    """The other half of proceeding without a state: a detector that could not
    run produced nothing, and producing nothing is exactly what a detector does
    when the situation has gone away. Closing its cards on that basis would be
    the platform telling a person a supplier concentration cleared itself while
    it was not looking."""
    from app.state.reducers.supplier import SUPPLIER

    _seed(session)
    _generate(session)
    sole = _by_type(session)[DecisionType.SUP_SOLE_SOURCE.value]
    assert sole.status == DecisionStatus.OPEN.value

    session.execute(delete(models.BusinessState).where(
        models.BusinessState.organization_id == ORG,
        models.BusinessState.state == SUPPLIER))
    session.commit()

    th = load_for_org(session, ORG)
    report = generate_from_state(session, ORG, thresholds=th)
    session.commit()

    assert report["states_missing"] == [SUPPLIER]
    assert _by_type(session)[DecisionType.SUP_SOLE_SOURCE.value].status == (
        DecisionStatus.OPEN.value)


# ── the registry ────────────────────────────────────────────────────────────
def test_every_detector_declares_a_state_and_a_registered_type():
    assert DETECTORS
    for decision_type, detector in DETECTORS.items():
        assert decision_type in STATE_TYPES
        assert detector.states, decision_type
        assert DecisionType(decision_type)


def test_every_action_a_detector_offers_has_words_a_person_reads(session):
    _seed(session)
    _generate(session)
    for d in _by_type(session).values():
        assert d.actions
        for action in d.actions:
            assert ACTIONS[action]


def test_an_unrenderable_action_is_refused_at_construction():
    from app.state.opportunities import Impact, OpportunityDraft, UnknownAction

    with pytest.raises(UnknownAction):
        OpportunityDraft(
            decision_type="X", subject_entity_type="PRODUCT",
            subject_entity_id="p1",
            impact=Impact(financial=Decimal(1), basis="b"),
            rationale="r", evidence={}, actions=("SUMMON_A_WIZARD",))


def test_a_detector_reading_no_state_is_refused():
    """Business State is the only source. A detector reading none would be
    producing decisions from nothing."""
    from app.state.opportunities import register

    class FromNothing:
        decision_type = "FROM_NOTHING"
        states = frozenset()

        def detect(self, states, policy, as_of):
            return ()

    with pytest.raises(ValueError, match="declares no states"):
        register(FromNothing())


def test_the_policy_carries_the_version_that_produced_it(session):
    _seed(session)
    th = _fold(session)
    assert policy_from(th).version == th.version
    generate_from_state(session, ORG, thresholds=th, as_of=TODAY)
    session.commit()
    for d in _by_type(session).values():
        assert d.confidence["thresholds_version"] == th.version


# ── traceability ────────────────────────────────────────────────────────────
def test_a_decision_names_the_state_it_came_from(session):
    """The join that closes the chain: decision → state → transition → event."""
    from app.state.engine import why
    from app.state.reducers.inventory import INVENTORY

    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.INV_DEAD_STOCK.value]

    assert d.state_as_of == TODAY
    assert d.state_keys == [d.subject_entity_id]
    steps = why(session, ORG, INVENTORY, d.state_keys[0], d.state_as_of)
    assert steps, "the state key must lead to the events that moved it"
    seqs = [s["event_seq"] for s in steps]
    events = session.query(models.BusinessEvent).filter(
        models.BusinessEvent.seq.in_(seqs)).all()
    assert events
    # …and each of those events names the ERP document it was read from.
    assert all(e.source_doc_id for e in events)


# ── the card, and the drill-down ────────────────────────────────────────────
@pytest.fixture()
def api(session):
    """The decisions API over a book that has been synced, folded and detected."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.routers import decisions as decisions_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD, ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()
    _seed(session)
    _generate(session)

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(decisions_router.router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    def _hdr(email: str) -> dict:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": SEED_PASSWORD})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    client.hdr = _hdr
    return client


def _card(api, decision_type: str) -> dict:
    owner = api.hdr("s.menon@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows if r["decision_type"] == decision_type)
    return api.get(f"/api/v1/decisions/{row['decision_id']}/detail",
                   headers=owner).json()


def test_the_card_carries_everything_needed_to_act_without_asking(api):
    card = _card(api, DecisionType.INV_DEAD_STOCK.value)

    assert card["origin"] == DecisionOrigin.STATE.value
    assert card["subject_label"] == "Reamer 12H7"          # a name, not an id
    assert Decimal(card["impact"]["financial"]) == Decimal("500000")
    assert card["impact"]["basis"]                          # what the number IS
    assert card["impact"]["monthly"]
    assert "1000" in card["rationale"] and "500" in card["rationale"]
    assert card["state"]["as_of"] == TODAY.isoformat()
    assert card["state"]["thresholds_version"]


def test_every_action_on_a_card_arrives_with_the_words_a_person_reads(api):
    card = _card(api, DecisionType.INV_DEAD_STOCK.value)
    assert card["actions"]
    for action in card["actions"]:
        assert action["label"] and action["label"] != action["key"]
    # Presented, never chosen: the card offers several and picks none.
    assert len(card["actions"]) > 1


def test_the_card_shows_its_own_ranking_working(api):
    card = _card(api, DecisionType.CASH_PAYABLE_OVERDUE.value)
    r = card["ranking"]
    assert r["money_points"] + r["urgency_points"] == card["priority"]["deterministic_base"]
    assert Decimal(r["financial"]) == Decimal(card["impact"]["financial"])
    assert r["urgency_points"] > 0                          # it is genuinely late


def test_a_state_card_says_no_model_was_involved(api):
    card = _card(api, DecisionType.INV_OVERSOLD.value)
    assert card["interpretation"]["status"] == "NOT_APPLICABLE"
    assert card["priority"]["ai_adjustment"] == 0
    assert card["confidence"]["evidence_sufficiency"] == "DETERMINISTIC"


def test_a_signal_card_is_unchanged_by_any_of_this(api, session):
    """The existing producer's cards must render exactly as they did."""
    session.add(models.Decision(
        organization_id=ORG, decision_key="dk_sig",
        decision_type=DecisionType.CUSTOMER_DECLINE.value,
        subject_entity_type="CUSTOMER",
        subject_entity_id=session.query(models.Customer).first().customer_id,
        assigned_role="SALES_MANAGER", priority_deterministic_base=40,
        priority_score=45, priority_band="MEDIUM",
        status=DecisionStatus.OPEN.value,
        ai={"status": "OK", "title": "T", "explanation": "E"}))
    session.commit()

    owner = api.hdr("s.menon@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows
               if r["decision_type"] == DecisionType.CUSTOMER_DECLINE.value)
    card = api.get(f"/api/v1/decisions/{row['decision_id']}/detail",
                   headers=owner).json()
    assert card["origin"] == DecisionOrigin.SIGNAL.value
    assert card["interpretation"]["explanation"] == "E"
    assert card["impact"] == {}
    assert card["actions"] == []


# ── decision → impact → state → transition → event → ERP ────────────────────
def test_the_drill_down_walks_all_the_way_to_an_erp_record(api):
    owner = api.hdr("s.menon@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows
               if r["decision_type"] == DecisionType.INV_DEAD_STOCK.value)
    trace = api.get(f"/api/v1/decisions/{row['decision_id']}/trace",
                    headers=owner).json()

    assert trace["unavailable"] is None
    assert trace["impact"] and trace["rationale"]

    level = trace["states"][0]
    assert level["state"] == "INVENTORY"
    assert level["label"] == "Reamer 12H7"
    assert level["as_of"] == TODAY.isoformat()
    assert level["value"]["on_hand"] == "1000"
    assert level["thresholds_version"]

    steps = level["transitions"]
    assert steps, "a state key must lead to the events that moved it"
    assert level["transitions_total"] >= len(steps)
    # Newest first: what moved this most recently is what can still be acted on.
    assert [s["occurred_on"] for s in steps] == sorted(
        (s["occurred_on"] for s in steps), reverse=True)

    # …and the bottom of the chain is a document somebody can open in Zoho.
    erp = [s["erp"] for s in steps if s["erp"]]
    assert erp
    assert {e["record_type"] for e in erp} <= {"invoice", "bill", "stock"}
    assert all(e["record_id"] for e in erp)


def test_the_drill_down_reaches_the_bill_the_purchase_rate_came_from(api):
    """The claim the whole chain exists to support: the ₹5,00,000 on the card
    is 1,000 × 500, and the 500 came from a bill with a number on it."""
    owner = api.hdr("s.menon@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows
               if r["decision_type"] == DecisionType.INV_DEAD_STOCK.value)
    trace = api.get(f"/api/v1/decisions/{row['decision_id']}/trace",
                    headers=owner).json()

    steps = trace["states"][0]["transitions"]
    cost = next(s for s in steps if s["event_type"] == "COST_LINE_RECORDED")
    assert cost["erp"]["record_type"] == "bill"
    assert cost["erp"]["record_id"] == "b1"
    assert any(field == "last_unit_cost" and value == "500"
               for _op, field, value in cost["changes"])


def test_a_signal_decision_says_it_has_no_state_rather_than_showing_a_gap(api, session):
    session.add(models.Decision(
        organization_id=ORG, decision_key="dk_sig2",
        decision_type=DecisionType.CUSTOMER_DORMANCY.value,
        subject_entity_type="CUSTOMER",
        subject_entity_id=session.query(models.Customer).first().customer_id,
        assigned_role="SALES_MANAGER", status=DecisionStatus.OPEN.value))
    session.commit()

    owner = api.hdr("s.menon@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows
               if r["decision_type"] == DecisionType.CUSTOMER_DORMANCY.value)
    trace = api.get(f"/api/v1/decisions/{row['decision_id']}/trace",
                    headers=owner).json()
    assert trace["states"] == []
    assert "signal" in trace["unavailable"].lower()


def test_a_salesperson_cannot_reach_a_state_card_or_its_trace(api):
    """404 rather than 403, so scope is not probeable."""
    owner = api.hdr("s.menon@pie.example")
    sales = api.hdr("r.nair@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows
               if r["decision_type"] == DecisionType.INV_DEAD_STOCK.value)

    assert api.get("/api/v1/decisions", headers=sales).json() == []
    for path in ("detail", "trace"):
        r = api.get(f"/api/v1/decisions/{row['decision_id']}/{path}", headers=sales)
        assert r.status_code == 404


def test_a_supplier_card_is_named_after_the_supplier(api):
    """A vendor is its own kind of subject; borrowing CUSTOMER would resolve to
    the wrong party's name or to a bare uuid."""
    card = _card(api, DecisionType.SUP_OPEN_COMMITMENT.value)
    assert card["subject_entity_type"] == "VENDOR"
    assert card["subject_label"] == "Kennametal India"


def test_the_chain_pages_rather_than_stopping_at_forty(api, session):
    """A chain that stops with no way forward cannot settle an argument about
    the forty-first row."""
    owner = api.hdr("s.menon@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows
               if r["decision_type"] == DecisionType.INV_DEAD_STOCK.value)
    url = f"/api/v1/decisions/{row['decision_id']}/trace"

    first = api.get(f"{url}?limit=2", headers=owner).json()["states"][0]
    assert first["transitions_offset"] == 0
    assert len(first["transitions"]) == 2
    assert first["has_more"] is (first["transitions_total"] > 2)

    second = api.get(f"{url}?limit=2&offset=2", headers=owner).json()["states"][0]
    assert second["transitions_offset"] == 2
    assert second["transitions_total"] == first["transitions_total"]
    # Disjoint pages, still newest-first across the boundary.
    assert ({t["event_seq"] for t in first["transitions"]}
            .isdisjoint({t["event_seq"] for t in second["transitions"]}))
    if second["transitions"]:
        assert (second["transitions"][0]["occurred_on"]
                <= first["transitions"][-1]["occurred_on"])

    past_the_end = api.get(f"{url}?offset=9999", headers=owner).json()["states"][0]
    assert past_the_end["transitions"] == []
    assert past_the_end["has_more"] is False


def test_a_request_for_ten_thousand_rows_is_refused(api):
    """Somebody reconciling a year is a real reader; this is not."""
    owner = api.hdr("s.menon@pie.example")
    rows = api.get("/api/v1/decisions", headers=owner).json()
    row = next(r for r in rows
               if r["decision_type"] == DecisionType.INV_DEAD_STOCK.value)
    r = api.get(f"/api/v1/decisions/{row['decision_id']}/trace?limit=10000",
                headers=owner)
    assert r.status_code == 422
