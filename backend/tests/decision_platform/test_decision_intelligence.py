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

ORG = "org_a"
TODAY = date(2026, 8, 6)


class _Source:
    """A book with one of each situation the detectors can find."""

    def __init__(self, **over: Any) -> None:
        self._over = over

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Pitti Engineering",
                 "status": "active"}]

    def list_vendors(self):
        return [{"contact_id": "v1", "contact_name": "Kennametal India",
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
            {"invoice_id": "inv1", "invoice_number": "INV-1", "customer_id": "c1",
             "date": "2024-06-01",
             "line_items": [{"line_item_id": "l1", "item_id": "dead",
                             "quantity": 10, "rate": "900", "item_total": "9000"}]},
            {"invoice_id": "inv2", "invoice_number": "INV-2", "customer_id": "c1",
             "date": "2026-01-05",
             "line_items": [{"line_item_id": "l1", "item_id": "slow",
                             "quantity": 20, "rate": "700", "item_total": "14000"},
                            {"line_item_id": "l2", "item_id": "excess",
                             "quantity": 30, "rate": "90", "item_total": "2700"}]},
            # Recent trade, so these lines are healthy rather than idle.
            {"invoice_id": "inv3", "invoice_number": "INV-3", "customer_id": "c1",
             "date": "2026-07-20",
             "line_items": [{"line_item_id": "l1", "item_id": "short",
                             "quantity": 100, "rate": "600", "item_total": "60000"},
                            {"line_item_id": "l2", "item_id": "low",
                             "quantity": 50, "rate": "450", "item_total": "22500"},
                            {"line_item_id": "l3", "item_id": "excess",
                             "quantity": 30, "rate": "90", "item_total": "2700"}]},
        ])

    def list_bills(self, skip=None):
        return self._over.get("bills", [
            # Overdue payable: ₹3,00,000, due four months ago.
            {"bill_id": "b1", "bill_number": "BILL-1", "vendor_id": "v1",
             "date": "2026-03-01", "due_date": "2026-04-01", "status": "open",
             "total": "300000", "balance": "300000",
             "line_items": [{"line_item_id": "l1", "item_id": "dead",
                             "quantity": 1000, "rate": "500"}]},
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
    for model in (models.BusinessState, models.StateTransition, models.Decision,
                  models.SalesTxn, models.CostRecord, models.StockSnapshot,
                  models.BillDoc, models.PurchaseOrderDoc):
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


def test_the_oversold_shortfall_is_priced_at_what_the_item_actually_sells_for(session):
    _seed(session)
    _generate(session)
    d = _by_type(session)[DecisionType.INV_OVERSOLD.value]
    ev = d.confidence["evidence"]
    per_unit = Decimal(ev["revenue"]) / Decimal(ev["units_sold"])
    short = -Decimal(ev["actual_available"])
    assert Decimal(d.impact["financial"]) == (short * per_unit).quantize(Decimal("0.01"))


# ── the queue ───────────────────────────────────────────────────────────────
def test_the_queue_is_ranked_by_money_and_says_so(session):
    _seed(session)
    _generate(session)
    rows = DecisionRepository(session, ORG).list()
    state_rows = [d for d in rows if d.origin == DecisionOrigin.STATE.value]
    assert len(state_rows) >= 3

    scores = [d.priority_deterministic_base for d in state_rows]
    assert scores == sorted(scores, reverse=True)
    # Dead stock at ₹5,00,000 outranks the overdue payable at ₹3,00,000.
    assert (state_rows[0].decision_type == DecisionType.INV_DEAD_STOCK.value)


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
