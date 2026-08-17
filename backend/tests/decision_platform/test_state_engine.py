"""The state engine and its two reducers.

Two claims are worth testing hard and everything else follows from them:

1. **A state equals the arithmetic of the events that made it.** Not "looks
   plausible" — the transitions are checked to sum to the value.
2. **A state is a series.** Building as of two dates gives two answers, and the
   earlier one does not know about the later events. That is the whole reason
   "what was inventory worth in March" is answerable.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.clock import today as clock_today
from app.domain import models
from app.ingestion.sync import SyncService
from app.state import events as ev
from app.state.engine import (ADD, MAX, MIN, SET, Delta, UnknownOp,
                              apply_change, build, load, why)
from app.state.engine import REDUCERS, register
from app.state.reducers.commitments import COMMITMENTS
from app.state.reducers.inventory import INVENTORY
from app.state.reducers.trade import CUSTOMER_ITEM_MONTH, CUSTOMER_MONTH

#: The build date. The real today, because a stock observation is dated on the
#: day the pull ran — a fixed past date would silently exclude it and make the
#: inventory assertions test nothing.
TODAY = clock_today()


class _Source:
    """Two items, one customer, one supplier, and documents across two months."""

    def __init__(self, **rows):
        self._rows = rows

    def list_contacts(self):
        return [{"contact_id": "c1", "contact_name": "Acme", "status": "active"}]

    def list_items(self):
        return [{"item_id": "i1", "name": "Insert", "unit": "pcs",
                 "status": "active", "stock_on_hand": 40, "available_stock": 35,
                 "actual_available_stock": 30, "purchase_rate": "401.25"},
                {"item_id": "i2", "name": "End mill", "unit": "pcs",
                 "status": "active", "stock_on_hand": 12, "available_stock": 12,
                 "purchase_rate": "880"}]

    def list_vendors(self):
        return [{"contact_id": "v1", "contact_name": "Kennametal India",
                 "status": "active"}]

    def list_users(self):
        return []

    def _docs(self, key, id_field, skip):
        for row in self._rows.get(key, []):
            doc_id = str(row.get(id_field))
            if skip is not None and skip(doc_id, str(row.get("last_modified_time") or "")):
                continue
            yield row

    def list_invoices(self, skip=None):
        return list(self._docs("invoices", "invoice_id", skip))

    def list_bills(self, skip=None):
        return list(self._docs("bills", "bill_id", skip))

    def list_sales_orders(self):
        return list(self._rows.get("sales_orders", []))

    def list_purchase_orders(self):
        return list(self._rows.get("purchase_orders", []))


def _line(item_id, qty, rate, total, line_id="l1"):
    return {"line_item_id": line_id, "item_id": item_id, "quantity": qty,
            "rate": rate, "item_total": total}


def _book() -> _Source:
    """Two sales of i1 in different months, one bill, one open order each way."""
    return _Source(
        invoices=[
            {"invoice_id": "inv1", "invoice_number": "INV-1", "customer_id": "c1",
             "date": "2026-05-10",
             "line_items": [_line("i1", 10, "500", "5000")]},
            {"invoice_id": "inv2", "invoice_number": "INV-2", "customer_id": "c1",
             "date": "2026-06-10",
             "line_items": [_line("i1", 4, "525", "2100"),
                            _line("i2", 2, "1500", "3000", line_id="l2")]},
        ],
        bills=[{"bill_id": "b1", "bill_number": "BILL-1", "vendor_id": "v1",
                "date": "2026-05-01", "due_date": "2026-05-31", "status": "open",
                "total": "48150", "balance": "48150",
                "line_items": [{"line_item_id": "l1", "item_id": "i1",
                                "quantity": 100, "rate": "401.25"}]}],
        sales_orders=[{"salesorder_id": "so1", "customer_id": "c1",
                       "date": "2026-06-15", "status": "open",
                       "invoiced_status": "not_invoiced", "total": "90000"}],
        purchase_orders=[{"purchaseorder_id": "po1", "vendor_id": "v1",
                          "date": "2026-06-02", "status": "issued",
                          "received_status": "pending", "quantity_yet_to_receive": 50,
                          "total": "20000"}],
    )


def _synced(session, source=None) -> None:
    SyncService(session, source or _book(), "org_a").run()
    session.commit()


def _product(session, external_id: str) -> str:
    return session.query(models.Product).filter_by(
        external_id=external_id).one().product_id


# ── the monthly series ───────────────────────────────────────────────────────
def _customer(session, external_id: str = "c1") -> str:
    return session.query(models.Customer).filter_by(
        external_id=external_id).one().customer_id


def test_a_month_is_a_key_not_a_second_build_call(session):
    """The architecture note for this work called for a new ``build_series()``.
    It is not needed, and this is the test that says why: ``build()`` folds in
    ``(occurred_on, seq)`` order into ``(state, key)`` accumulators and has no
    opinion about what a key means, so putting the month in the key gets the
    monthly series in the same single pass. A second build function beside this
    one would have been a second way to do one thing."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    rows = load(session, "org_a", CUSTOMER_MONTH, TODAY)
    cid = _customer(session)
    assert set(rows) == {f"{cid}:2026-05", f"{cid}:2026-06"}
    # May: one invoice, 10 x 500. June: one invoice, 2100 + 3000.
    assert Decimal(rows[f"{cid}:2026-05"]["revenue"]) == Decimal("5000")
    assert Decimal(rows[f"{cid}:2026-06"]["revenue"]) == Decimal("5100")


def test_a_month_in_the_series_equals_a_point_build_at_that_month_end(session):
    """The equality the architecture note asked for. A series row for month M
    must agree with what a point build says about M — otherwise the series is a
    second, differently-wrong answer to a question the engine already answers.

    Point-built up to 31 May, the customer's *entire* trade is May's trade, so
    a plain revenue total is directly comparable to the series row for May.
    """
    _synced(session)
    may_end = date(2026, 5, 31)
    build(session, "org_a", as_of=may_end)
    session.commit()
    at_may = load(session, "org_a", CUSTOMER_MONTH, may_end)
    cid = _customer(session)

    # Only May exists in a fold that stops on 31 May — June has not happened.
    assert set(at_may) == {f"{cid}:2026-05"}

    build(session, "org_a", as_of=TODAY)
    session.commit()
    full = load(session, "org_a", CUSTOMER_MONTH, TODAY)
    # And May's row is identical in both, because a month that has closed
    # cannot change. This is what makes the series safe to read as history.
    assert full[f"{cid}:2026-05"] == at_may[f"{cid}:2026-05"]


def test_orders_count_documents_and_lines_count_lines(session):
    """A fold cannot count distinct documents — ADD over sale lines counts
    lines. Orders come from the invoice header event, which is emitted once per
    invoice, so a customer who buys three items at once ordered once."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()
    rows = load(session, "org_a", CUSTOMER_MONTH, TODAY)
    june = rows[f"{_customer(session)}:2026-06"]
    assert june["orders"] == 1, "one invoice"
    assert june["lines"] == 2, "two items on it"


def test_the_item_grain_splits_what_the_customer_grain_totals(session):
    """The finer state must reconcile to the coarser one, or two screens
    reading them will disagree about the same month."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()
    cid = _customer(session)
    coarse = load(session, "org_a", CUSTOMER_MONTH, TODAY)
    fine = load(session, "org_a", CUSTOMER_ITEM_MONTH, TODAY)

    for month in ("2026-05", "2026-06"):
        per_item = sum(
            (Decimal(v["revenue"]) for k, v in fine.items()
             if v["customer_id"] == cid and v["month"] == month),
            Decimal(0))
        assert per_item == Decimal(coarse[f"{cid}:{month}"]["revenue"]), month


def test_the_item_grain_carries_no_order_count(session):
    """An invoice is a document about a customer, not about one line of it.
    Counting it once per item would multiply one order by what was on it."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()
    for value in load(session, "org_a", CUSTOMER_ITEM_MONTH, TODAY).values():
        assert "orders" not in value


def test_a_back_dated_correction_lands_in_its_own_month(session):
    """The reason the month comes from ``occurred_on`` and not from the build
    date: a July re-read of a May invoice must rewrite May, not add to July."""
    source = _book()
    _synced(session, source)
    # The same invoice, re-read with a different total. Supersession retires
    # the old reading; the new one is still dated in May.
    source._rows["invoices"][0]["line_items"] = [_line("i1", 10, "600", "6000")]
    source._rows["invoices"][0]["last_modified_time"] = "2026-07-01T00:00:00+0530"
    _synced(session, source)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    rows = load(session, "org_a", CUSTOMER_MONTH, TODAY)
    cid = _customer(session)
    assert Decimal(rows[f"{cid}:2026-05"]["revenue"]) == Decimal("6000")
    assert f"{cid}:2026-07" not in rows, "the correction is May's, not July's"


def test_a_screen_state_records_no_working_and_a_decision_state_still_does(session):
    """Transitions answer exactly one question — ``why()``, the drill-down from
    a decision card to the events behind its number — so only a state some
    detector reads can ever be asked. The monthly trade states feed screens, and
    at this book's size their working came to about 90,000 rows per build that
    nothing would ever query; measured, it was most of what the fold cost.

    The flag is opt-out and defaults to on, so this asserts both halves: the
    states that carry an audit trail still do."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    def transitions(state):
        return session.query(models.StateTransition).filter_by(
            organization_id="org_a", state=state, as_of=TODAY).count()

    assert transitions(CUSTOMER_MONTH) == 0
    assert transitions(CUSTOMER_ITEM_MONTH) == 0
    # Untouched: INVENTORY is read by detectors, so its cards can be drilled.
    assert transitions(INVENTORY) > 0


def test_a_state_without_working_still_has_values(session):
    """The opt-out drops the explanation, never the answer."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()
    rows = load(session, "org_a", CUSTOMER_MONTH, TODAY)
    assert rows and all(r.get("revenue") for r in rows.values())


# ── the arithmetic ───────────────────────────────────────────────────────────
def test_inventory_state_is_the_sum_of_the_movements_that_made_it(session):
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    i1 = load(session, "org_a", INVENTORY, TODAY)[_product(session, "i1")]
    # 10 sold in May + 4 in June, 100 bought.
    assert Decimal(i1["units_sold"]) == Decimal("14")
    assert Decimal(i1["revenue"]) == Decimal("7100")
    assert Decimal(i1["units_purchased"]) == Decimal("100")
    assert i1["last_sold_on"] == "2026-06-10"
    assert i1["last_purchased_on"] == "2026-05-01"
    # Observed, not accumulated: 40 on hand is a count, not a running total.
    assert Decimal(i1["on_hand"]) == Decimal("40")
    assert Decimal(i1["last_unit_cost"]) == Decimal("401.25")


def test_every_state_value_is_reproducible_from_its_transitions(session):
    """The traceability claim, checked rather than asserted: the changes
    recorded against a key must add up to the value stored for it."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    product_id = _product(session, "i1")
    stored = load(session, "org_a", INVENTORY, TODAY)[product_id]
    rebuilt: dict = {}
    for step in why(session, "org_a", INVENTORY, product_id, TODAY):
        for op, field_name, raw in step["changes"]:
            value = Decimal(raw) if isinstance(raw, str) and _is_number(raw) else raw
            apply_change(rebuilt, op, field_name, value)

    assert {k: str(v) for k, v in rebuilt.items()} == {
        k: str(v) for k, v in stored.items()}


def _is_number(raw: str) -> bool:
    try:
        Decimal(raw)
    except Exception:       # noqa: BLE001 — a date or a word, not a number
        return False
    return True


# ── a state is a series, not a current value ─────────────────────────────────
def test_the_same_state_on_two_days_gives_two_answers(session):
    """The reason as_of is part of the key. Nothing else in this platform can
    answer 'what was this worth in March'."""
    _synced(session)
    build(session, "org_a", as_of=date(2026, 5, 31))
    build(session, "org_a", as_of=TODAY)
    session.commit()

    product_id = _product(session, "i1")
    may = load(session, "org_a", INVENTORY, date(2026, 5, 31))[product_id]
    july = load(session, "org_a", INVENTORY, TODAY)[product_id]

    assert Decimal(may["units_sold"]) == Decimal("10")      # June sale not yet
    assert Decimal(july["units_sold"]) == Decimal("14")
    assert may["last_sold_on"] == "2026-05-10"


def test_a_rebuild_replaces_the_day_rather_than_adding_to_it(session):
    """A state row is the whole answer for that key on that day. Merging a new
    fold into an old one would leave fields from a reading that no longer
    exists."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    product_id = _product(session, "i1")
    rows = session.query(models.BusinessState).filter_by(
        state=INVENTORY, key=product_id, as_of=TODAY).all()
    assert len(rows) == 1
    assert Decimal(rows[0].value["units_sold"]) == Decimal("14")   # not 28


# ── ordering ─────────────────────────────────────────────────────────────────
def test_a_correction_lands_on_the_date_it_happened_not_the_date_it_was_read(session):
    """The engine folds by (occurred_on, seq). A cost correction read today for
    a May bill must be the May cost, not today's."""
    _synced(session)

    later_bill = _book()
    later_bill._rows["bills"] = [
        {"bill_id": "b2", "bill_number": "BILL-2", "vendor_id": "v1",
         "date": "2026-06-20", "status": "open", "total": "1000",
         "balance": "1000",
         "line_items": [{"line_item_id": "l1", "item_id": "i1",
                         "quantity": 2, "rate": "450"}]}]
    SyncService(session, later_bill, "org_a").run()
    session.commit()

    # Now a *back-dated* correction, read last but dated before the June bill.
    corrected = _book()
    corrected._rows["bills"][0]["last_modified_time"] = "2026-07-01T09:00:00+0530"
    corrected._rows["bills"][0]["line_items"][0]["rate"] = "399.00"
    SyncService(session, corrected, "org_a").run()
    session.commit()

    build(session, "org_a", as_of=TODAY)
    session.commit()
    i1 = load(session, "org_a", INVENTORY, TODAY)[_product(session, "i1")]
    # The June bill is still the latest cost, even though the May one was read
    # most recently.
    assert Decimal(i1["last_unit_cost"]) == Decimal("450")


# ── commitments ──────────────────────────────────────────────────────────────
def test_an_uninvoiced_order_is_a_commitment_to_the_customer(session):
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    customer_id = session.query(models.Customer).one().customer_id
    row = load(session, "org_a", COMMITMENTS, TODAY)[customer_id]
    assert row["direction"] == "customer"
    assert row["open_sales_orders"] == 1
    assert Decimal(row["open_sales_value"]) == Decimal("90000")
    assert row["oldest_open_sale_on"] == "2026-06-15"


def test_an_invoiced_order_stops_being_a_commitment(session):
    """Read from Zoho's own field. Nothing decrements — supersession leaves one
    live event per order, and a closed one simply stops qualifying."""
    _synced(session)
    shipped = _book()
    shipped._rows["sales_orders"][0]["invoiced_status"] = "invoiced"
    SyncService(session, shipped, "org_a").run()
    session.commit()

    build(session, "org_a", as_of=TODAY)
    session.commit()
    customer_id = session.query(models.Customer).one().customer_id
    row = load(session, "org_a", COMMITMENTS, TODAY)[customer_id]
    assert row["sales_orders"] == 1
    assert "open_sales_orders" not in row


def test_a_supplier_carries_both_what_is_owed_and_what_is_coming(session):
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    vendor_id = session.query(models.Vendor).one().vendor_id
    row = load(session, "org_a", COMMITMENTS, TODAY)[vendor_id]
    assert row["direction"] == "supplier"
    assert row["open_purchase_orders"] == 1
    assert Decimal(row["pending_qty"]) == Decimal("50")
    assert row["unpaid_bills"] == 1
    assert Decimal(row["payables_balance"]) == Decimal("48150")
    # Due 31 May, built as of 1 July.
    assert row["overdue_bills"] == 1
    assert Decimal(row["overdue_balance"]) == Decimal("48150")


def test_a_bill_becomes_overdue_only_once_the_as_of_passes_its_due_date(session):
    _synced(session)
    build(session, "org_a", as_of=date(2026, 5, 15))
    session.commit()

    vendor_id = session.query(models.Vendor).one().vendor_id
    row = load(session, "org_a", COMMITMENTS, date(2026, 5, 15))[vendor_id]
    assert row["unpaid_bills"] == 1
    assert "overdue_bills" not in row


def test_a_bill_with_no_terms_is_counted_but_named_unageable(session):
    """Assuming a due date would report it overdue from the day it was
    raised."""
    untermed = _book()
    untermed._rows["bills"][0].pop("due_date")
    _synced(session, untermed)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    vendor_id = session.query(models.Vendor).one().vendor_id
    row = load(session, "org_a", COMMITMENTS, TODAY)[vendor_id]
    assert row["unpaid_bills"] == 1
    assert row["unageable_bills"] == 1
    assert "overdue_bills" not in row


def test_a_settled_bill_is_not_a_payable(session):
    paid = _book()
    paid._rows["bills"][0]["balance"] = "0"
    paid._rows["bills"][0]["status"] = "paid"
    _synced(session, paid)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    vendor_id = session.query(models.Vendor).one().vendor_id
    row = load(session, "org_a", COMMITMENTS, TODAY)[vendor_id]
    assert row["bills"] == 1
    assert "unpaid_bills" not in row


# ── what the engine refuses to do ────────────────────────────────────────────
def test_the_registry_is_how_a_state_is_added_not_an_edit_to_the_engine(session):
    """OCP, checked: registering a third reducer takes no change to
    ``engine.py``, and it starts producing rows immediately."""
    class Trivial:
        state = "TEST_ONLY"
        handles = frozenset({ev.SALE_LINE_RECORDED})

        def apply(self, event, ctx, as_of):
            return (Delta(self.state, "all", ((ADD, "lines", 1),)),)

    register(Trivial())
    try:
        _synced(session)
        build(session, "org_a", as_of=TODAY)
        session.commit()
        assert load(session, "org_a", "TEST_ONLY", TODAY)["all"]["lines"] == 3
    finally:
        REDUCERS.pop("TEST_ONLY", None)


def test_two_reducers_cannot_claim_the_same_state():
    class Clash:
        state = INVENTORY
        handles = frozenset({ev.SALE_LINE_RECORDED})

        def apply(self, event, ctx, as_of):
            return ()

    with pytest.raises(ValueError, match="two reducers"):
        register(Clash())


def test_a_reducer_naming_an_unregistered_event_type_is_refused():
    """It would never fire, and nothing would say so."""
    class Typo:
        state = "TYPO_ONLY"
        handles = frozenset({"SALE_LINE_RECORDEDD"})

        def apply(self, event, ctx, as_of):
            return ()

    with pytest.raises(ValueError, match="unregistered event types"):
        register(Typo())
    assert "TYPO_ONLY" not in REDUCERS


def test_an_unknown_op_is_refused_rather_than_ignored():
    with pytest.raises(UnknownOp):
        apply_change({}, "MULTIPLY", "units", 2)


def test_every_op_the_vocabulary_claims_actually_folds():
    acc: dict = {}
    apply_change(acc, SET, "a", 1)
    apply_change(acc, SET, "a", 2)
    apply_change(acc, ADD, "b", Decimal("1.5"))
    apply_change(acc, ADD, "b", Decimal("2.25"))
    apply_change(acc, MAX, "c", date(2026, 1, 1))
    apply_change(acc, MAX, "c", date(2025, 1, 1))
    apply_change(acc, MIN, "d", date(2026, 1, 1))
    apply_change(acc, MIN, "d", date(2025, 1, 1))
    assert acc == {"a": 2, "b": Decimal("3.75"),
                   "c": date(2026, 1, 1), "d": date(2025, 1, 1)}


def test_money_never_becomes_a_float_anywhere_in_the_fold(session):
    """A float accumulator is exactly the noise this platform exists not to
    have. Values are stored as strings and folded as Decimals."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    for row in session.query(models.BusinessState).all():
        for name, value in row.value.items():
            assert not isinstance(value, float), f"{row.state}.{name} is a float"


def test_an_event_whose_master_is_missing_is_reported_not_keyed(session):
    """A state built from 90% of the evidence that says so is usable; one that
    says nothing is not."""
    _synced(session)
    # Removing the master while its events remain is the scenario; the sales
    # rows that reference the product must go with it (enforced foreign keys),
    # and the event log — deliberately not FK-bound — still holds the
    # reference the build must report as unresolved.
    gone = session.query(models.Product).filter_by(external_id="i2").one()
    session.query(models.SalesTxn).filter_by(product_id=gone.product_id).delete()
    session.query(models.CostRecord).filter_by(product_id=gone.product_id).delete()
    session.query(models.StockSnapshot).filter_by(product_id=gone.product_id).delete()
    session.query(models.Product).filter_by(external_id="i2").delete()
    session.flush()

    report = build(session, "org_a", as_of=TODAY)
    session.commit()

    assert report.unresolved
    assert {u["missing"] for u in report.unresolved} == {"product"}
    assert all(u["reference"] == "i2" for u in report.unresolved)


def test_two_organizations_never_fold_into_each_other(session):
    _synced(session)
    SyncService(session, _book(), "org_b").run()
    session.commit()

    build(session, "org_a", as_of=TODAY)
    build(session, "org_b", as_of=TODAY)
    session.commit()

    a = session.query(models.BusinessState).filter_by(
        organization_id="org_a", state=INVENTORY).all()
    b = session.query(models.BusinessState).filter_by(
        organization_id="org_b", state=INVENTORY).all()
    assert a and b
    assert {r.key for r in a}.isdisjoint({r.key for r in b})


def test_the_policy_version_is_stamped_on_every_computed_row(session):
    """Without it a state computed under one margin policy is
    indistinguishable from one computed under another."""
    _synced(session)
    build(session, "org_a", as_of=TODAY, thresholds_version="abc123")
    session.commit()

    rows = session.query(models.BusinessState).all()
    assert rows
    assert {r.thresholds_version for r in rows} == {"abc123"}


def test_building_one_state_leaves_the_other_alone(session):
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()
    before = load(session, "org_a", COMMITMENTS, TODAY)

    build(session, "org_a", as_of=TODAY, states=[INVENTORY])
    session.commit()

    assert load(session, "org_a", COMMITMENTS, TODAY) == before


def test_no_reducer_invents_a_threshold_or_a_band(session):
    """The facts a judgement is made from live here; the judgement does not.
    A health band or a reorder point in a state row would be policy baked into
    a projection, where no version could reach it."""
    _synced(session)
    build(session, "org_a", as_of=TODAY)
    session.commit()

    forbidden = {"health", "band", "status_band", "dead", "slow", "at_risk",
                 "reorder_point", "weeks_of_cover", "priority", "score",
                 "recommended_action", "risk"}
    for row in session.query(models.BusinessState).all():
        assert forbidden.isdisjoint(row.value.keys()), row.value


def test_the_working_belongs_to_one_fold_not_to_the_last_one_run(session):
    """Two builds at different dates are two arithmetics over the same events.
    A transition that did not say which would explain the wrong number."""
    _synced(session)
    build(session, "org_a", as_of=date(2026, 5, 31))
    build(session, "org_a", as_of=TODAY)
    session.commit()

    product_id = _product(session, "i1")
    may = why(session, "org_a", INVENTORY, product_id, date(2026, 5, 31))
    now = why(session, "org_a", INVENTORY, product_id, TODAY)

    assert may and now
    assert len(may) < len(now)
    assert all(step["occurred_on"] <= "2026-05-31" for step in may)
