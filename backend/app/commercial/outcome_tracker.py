"""The Outcome Tracker: did an accepted recommendation change anything, measured.

Two halves, both deterministic and neither touching ``ai/``:

**Capture** (``capture_on_accept``). The moment a signal-derived decision is
accepted, the signal's own evidence — its metrics, its window, the threshold
version that judged it — is frozen into an append-only ``OutcomeSnapshot`` row
together with an evaluation horizon. Captured *at acceptance* because the read
model is rebuildable: a later re-sync may recompute every metric, and the
baseline a realised delta is measured against must be the one the human
actually said yes to.

**Evaluation** (``evaluate``). For a snapshot whose horizon has elapsed, the
same metrics are recomputed from persisted rows — ``SalesTxn`` and
``CostRecord``, through the same aggregate functions the detectors use — over
the post-decision window, and the delta against the frozen baseline is emitted
with an explicit status:

- ``PENDING``  — the horizon has not elapsed; nothing is asserted.
- ``REALISED`` — recomputed from evidence; the delta is a measurement.
- ``UNKNOWN``  — the evidence is missing, and ``missing`` names exactly what.
  On UNKNOWN no numbers are emitted at all: a partial figure beside a named gap
  still reads as a figure, and §1 forbids fabricating one from partial rows.

Evaluation is computed on read, never persisted, so the snapshot table stays
append-only by construction and a re-sync that adds late-arriving invoices
corrects the realised figure instead of contradicting a stored one. Persisting
evaluations (as superseded-not-mutated measurement rows, the ``state/`` idiom)
is the next increment, deliberately not this one.

Why ``commercial/``: this module computes margins, revenue deltas and realised
positions — numbers a screen will render — so it belongs with the other
deterministic computation, stamped and reproducible. The seam that *calls*
capture is the decision lifecycle (``DecisionRepository.record_human_action``
and ``approvals._settle_escalated_decision``), which is where acceptance is
already recorded.

The thresholds stamp: a snapshot copies ``Signal.threshold_config_version``
verbatim — ``th_…`` for engine signals, ``ci_…`` for Customer × Item signals.
Whichever hash judged the signal is the one recorded; the two are never read
as each other.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..domain import models
from ..domain.enums import OutcomeStatus, SignalType
from ..signals import aggregates as agg
from ..signals.base import CostRow, SaleRow
from ..signals.config import load_thresholds
from ..signals.quality import cost_anomalies, cost_is_reliable

_TWO_PLACES = Decimal("0.01")


def _money(value: Decimal) -> str:
    """Money leaves this module as a string, never a float (§1)."""
    return str(value.quantize(_TWO_PLACES))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# ── the horizon config ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class EvaluationHorizons:
    """How long after acceptance each category's outcome is measured — one
    deterministic config, not a magic number scattered around.

    Defaults are judgements, stated so they can be argued with:

    - Decline and the two cost/margin categories use 90 days — the detectors'
      own basis window — so the realised window is length-comparable to the
      window the baseline was measured over.
    - Dormancy uses 60 days: the question is "did they come back", cadence in
      this book is roughly monthly, and two typical cycles is enough to call
      it either way without parking the answer for a quarter.

    The *snapshot* stores the literal ``horizon_days`` in force at acceptance,
    which is stronger than a version pointer: editing this config never moves a
    window an acceptance already anchored.
    """

    customer_decline_days: int = 90
    customer_dormancy_days: int = 60
    margin_deterioration_days: int = 90
    cost_pass_through_days: int = 90
    #: Any category without a row of its own (CI_* signals, future detectors).
    default_days: int = 90

    @classmethod
    def from_env(cls) -> "EvaluationHorizons":
        return cls(
            customer_decline_days=_i("OUTCOME_HORIZON_DECLINE_DAYS", 90),
            customer_dormancy_days=_i("OUTCOME_HORIZON_DORMANCY_DAYS", 60),
            margin_deterioration_days=_i("OUTCOME_HORIZON_MARGIN_DAYS", 90),
            cost_pass_through_days=_i("OUTCOME_HORIZON_COST_PASS_DAYS", 90),
            default_days=_i("OUTCOME_HORIZON_DEFAULT_DAYS", 90),
        )

    def for_category(self, category: str) -> int:
        by_category = {
            SignalType.CUSTOMER_DECLINE.value: self.customer_decline_days,
            SignalType.CUSTOMER_DORMANCY.value: self.customer_dormancy_days,
            SignalType.MARGIN_DETERIORATION.value: self.margin_deterioration_days,
            SignalType.COST_PASS_THROUGH.value: self.cost_pass_through_days,
        }
        return by_category.get(category, self.default_days)


def load_horizons() -> EvaluationHorizons:
    return EvaluationHorizons.from_env()


# ── capture, at acceptance ───────────────────────────────────────────────────
def snapshot_for_decision(session: Session,
                          decision_id: str) -> Optional[models.OutcomeSnapshot]:
    return session.scalar(
        select(models.OutcomeSnapshot).where(
            models.OutcomeSnapshot.decision_id == decision_id))


def capture_on_accept(session: Session, decision: models.Decision, *,
                      accepted_by_user_id: Optional[str] = None,
                      horizons: Optional[EvaluationHorizons] = None,
                      ) -> Optional[models.OutcomeSnapshot]:
    """Freeze the accepted decision's baseline, once.

    Returns the snapshot, or ``None`` where there is honestly nothing to
    capture:

    - a decision with no ``signal_ids`` (STATE-derived, or a quote-context
      card) has no signal baseline to freeze — its realised impact is a later
      increment, not a row with an empty baseline pretending otherwise;
    - a decision whose signal row cannot be found (or belongs to another org)
      has no evidence to copy, and a snapshot without a baseline could only
      ever evaluate to a fabricated delta.

    Idempotent per decision: accept → reopen → accept again returns the first
    snapshot, so the horizon stays anchored at the first acceptance and one
    recommendation never carries two baselines.
    """
    if not decision.signal_ids:
        return None
    existing = snapshot_for_decision(session, decision.decision_id)
    if existing is not None:
        return existing
    signal = session.get(models.Signal, decision.signal_ids[0])
    if signal is None or signal.organization_id != decision.organization_id:
        return None
    h = horizons or load_horizons()
    row = models.OutcomeSnapshot(
        organization_id=decision.organization_id,
        decision_id=decision.decision_id,
        signal_id=signal.signal_id,
        category=signal.signal_type,
        subject_entity_type=signal.subject_entity_type,
        subject_entity_id=signal.subject_entity_id,
        baseline_metrics=dict(signal.metrics or {}),
        baseline_window=dict(signal.window or {}),
        # Verbatim: th_… or ci_…, whichever judged this signal.
        thresholds_version=signal.threshold_config_version or "",
        horizon_days=h.for_category(signal.signal_type),
        accepted_at=clock.now(),
        accepted_by_user_id=accepted_by_user_id,
    )
    session.add(row)
    session.flush()
    return row


# ── evaluation, on read ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class OutcomeEvaluation:
    """One snapshot's realised position, or the named reason there isn't one."""

    status: OutcomeStatus
    window_start: date
    window_end: date
    as_of: date
    realised: dict[str, Any]
    delta: dict[str, Any]
    missing: tuple[str, ...]
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "window": {"start": self.window_start.isoformat(),
                       "end": self.window_end.isoformat()},
            "as_of": self.as_of.isoformat(),
            "realised": dict(self.realised),
            "delta": dict(self.delta),
            "missing": list(self.missing),
            "note": self.note,
        }


def _unknown(start: date, end: date, ref: date, *missing: str) -> OutcomeEvaluation:
    """UNKNOWN emits no numbers at all — a partial figure beside a named gap
    still reads as a figure."""
    return OutcomeEvaluation(status=OutcomeStatus.UNKNOWN, window_start=start,
                             window_end=end, as_of=ref, realised={}, delta={},
                             missing=tuple(missing))


def _baseline_number(metrics: dict[str, Any], key: str) -> Optional[Decimal]:
    """A numeric baseline metric as ``Decimal``, or ``None`` when the snapshot
    cannot supply one. Through ``str`` so a float baseline does not smuggle
    binary noise into money arithmetic."""
    raw = metrics.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return Decimal(str(raw))


def _missing_baseline(key: str) -> str:
    return (f"Baseline metric {key!r} is absent from the snapshot, so the "
            "realised position cannot be compared against detection.")


def _sale_rows(session: Session, org: str, *, customer_id: Optional[str] = None,
               product_id: Optional[str] = None,
               through: Optional[date] = None) -> list[SaleRow]:
    """One entity's sale lines as the detectors' own row type.

    A targeted query rather than ``aggregates.load_snapshot``: the loader's
    bounds are named per consumer (customers for sales, products for costs) and
    cannot bound sales by product, which is the axis both product categories
    evaluate on. Loading the whole book per snapshot to reuse it would make the
    outcomes list O(book × snapshots). The row type and every aggregate over it
    are shared, so the two paths cannot disagree about what a line means.
    """
    stmt = select(
        models.SalesTxn.customer_id, models.SalesTxn.product_id,
        models.SalesTxn.date, models.SalesTxn.qty, models.SalesTxn.unit_price,
        models.SalesTxn.line_revenue, models.SalesTxn.source_ref,
        models.SalesTxn.external_ref,
    ).where(models.SalesTxn.organization_id == org)
    if customer_id is not None:
        stmt = stmt.where(models.SalesTxn.customer_id == customer_id)
    if product_id is not None:
        stmt = stmt.where(models.SalesTxn.product_id == product_id)
    if through is not None:
        stmt = stmt.where(models.SalesTxn.date <= through)
    return [
        SaleRow(customer_id=r.customer_id, product_id=r.product_id, date=r.date,
                qty=Decimal(r.qty), unit_price=Decimal(r.unit_price),
                line_revenue=Decimal(r.line_revenue),
                source_ref=r.source_ref or {}, external_ref=r.external_ref)
        for r in session.execute(stmt)
    ]


def _cost_rows(session: Session, org: str, product_id: str,
               through: date) -> list[CostRow]:
    stmt = select(
        models.CostRecord.product_id, models.CostRecord.date,
        models.CostRecord.qty, models.CostRecord.unit_cost,
        models.CostRecord.source_ref, models.CostRecord.external_ref,
    ).where(
        models.CostRecord.organization_id == org,
        models.CostRecord.product_id == product_id,
        models.CostRecord.date <= through,
    ).order_by(models.CostRecord.date)
    return [
        CostRow(product_id=r.product_id, date=r.date, qty=Decimal(r.qty),
                unit_cost=Decimal(r.unit_cost), source_ref=r.source_ref or {},
                external_ref=r.external_ref)
        for r in session.execute(stmt)
    ]


def _in_window(rows: list[SaleRow], start: date, end: date) -> list[SaleRow]:
    """The detectors' window convention: start < date ≤ end."""
    return [s for s in rows if start < s.date <= end]


_Result = tuple[dict[str, Any], dict[str, Any], list[str]]


def _eval_customer_decline(session: Session, snap: models.OutcomeSnapshot,
                           start: date, end: date) -> _Result:
    """Did the revenue come back. Realised revenue over the post-decision
    window against the frozen recent/baseline figures."""
    m = snap.baseline_metrics or {}
    recent = _baseline_number(m, "recent_revenue")
    baseline = _baseline_number(m, "baseline_revenue")
    missing = [_missing_baseline(k) for k, v in
               (("recent_revenue", recent), ("baseline_revenue", baseline))
               if v is None]
    if missing:
        return {}, {}, missing

    rows = _in_window(
        _sale_rows(session, snap.organization_id,
                   customer_id=snap.subject_entity_id, through=end),
        start, end)
    # Zero here is a measurement, not a benign default: the coverage gate in
    # `evaluate` has already established the book is observed through `end`,
    # so an empty window is the customer genuinely not buying.
    window_revenue = sum((s.line_revenue for s in rows), Decimal("0"))
    orders = {str(s.source_ref.get("record_id") or s.external_ref) for s in rows}

    realised = {
        "window_days": int(snap.horizon_days),
        "window_revenue": _money(window_revenue),
        "window_orders": len(orders),
    }
    delta = {
        "revenue_vs_recent": _money(window_revenue - recent),
        "revenue_vs_baseline": _money(window_revenue - baseline),
        # Realised over the healthy baseline. 1.0 means fully recovered; only
        # asserted against a positive baseline (the detector guarantees one,
        # the snapshot re-checks rather than trusting).
        "revenue_recovery_ratio": (float(round(window_revenue / baseline, 4))
                                   if baseline > 0 else None),
    }
    return realised, delta, []


def _eval_customer_dormancy(session: Session, snap: models.OutcomeSnapshot,
                            start: date, end: date) -> _Result:
    """Did the customer order again. Needs no baseline numbers: the realised
    fact is the presence or absence of orders in an observed window."""
    rows = _in_window(
        _sale_rows(session, snap.organization_id,
                   customer_id=snap.subject_entity_id, through=end),
        start, end)
    by_order: dict[str, date] = {}
    for s in rows:
        key = str(s.source_ref.get("record_id") or s.external_ref)
        if key not in by_order or s.date < by_order[key]:
            by_order[key] = s.date
    first = min(by_order.values()) if by_order else None

    realised = {
        "window_days": int(snap.horizon_days),
        "window_orders": len(by_order),
        "first_order_date": first.isoformat() if first else None,
        "reordered": bool(by_order),
    }
    delta = {
        "days_to_first_order": (first - start).days if first else None,
    }
    return realised, delta, []


def _product_price_and_cost(session: Session, snap: models.OutcomeSnapshot,
                            start: date, end: date,
                            ) -> tuple[Optional[Decimal], Optional[CostRow], list[str]]:
    """The window's quantity-weighted price and the applicable cost basis for a
    product subject — or the named gaps. Shared by both restricted categories
    so they cannot disagree about what a realised price or cost is."""
    missing: list[str] = []
    sales = _in_window(
        _sale_rows(session, snap.organization_id,
                   product_id=snap.subject_entity_id, through=end),
        start, end)
    price = agg.avg_unit_price_range(sales, start, end)
    if price is None or price <= 0:
        missing.append(
            f"No priced sales of the product between {start.isoformat()} and "
            f"{end.isoformat()} — a realised position cannot be computed "
            "without a selling price in the window.")

    costs = _cost_rows(session, snap.organization_id, snap.subject_entity_id, end)
    basis = agg.cost_basis_asof(costs, end)
    if basis is None:
        missing.append(
            f"No cost record on or before {end.isoformat()} for the product — "
            "a realised margin cannot be computed without a cost basis.")
    elif price is not None and price > 0:
        anomalies = cost_anomalies(basis.unit_cost, price, load_thresholds())
        if not cost_is_reliable(anomalies):
            codes = ", ".join(sorted({a["code"] for a in anomalies}))
            missing.append(
                f"The applicable cost basis is unreliable ({codes}) — a margin "
                "computed from it would be asserted on bad cost.")
            basis = None
    return price, basis, missing


def _eval_margin_deterioration(session: Session, snap: models.OutcomeSnapshot,
                               start: date, end: date) -> _Result:
    """Did the margin recover. The detector's own method over the post-decision
    window: quantity-weighted price, cost basis as of the window end,
    margin = (price − cost) ÷ price."""
    m = snap.baseline_metrics or {}
    at_detection = _baseline_number(m, "current_margin_pct")
    if at_detection is None:
        return {}, {}, [_missing_baseline("current_margin_pct")]

    price, basis, missing = _product_price_and_cost(session, snap, start, end)
    if missing:
        return {}, {}, missing
    assert price is not None and basis is not None  # missing is empty

    realised_margin = float((price - basis.unit_cost) / price)
    realised = {
        "window_days": int(snap.horizon_days),
        "realised_margin_pct": round(realised_margin, 4),
        "window_avg_unit_price": float(round(price, 2)),
        "window_unit_cost_basis": float(round(basis.unit_cost, 2)),
    }
    delta = {
        # Percentage points of margin (§1: movement is _pp), positive = recovered.
        "margin_change_pp_vs_detection": round(
            realised_margin - float(at_detection), 4),
    }
    healthy = _baseline_number(m, "baseline_margin_pct")
    if healthy is not None:
        delta["margin_gap_pp_vs_baseline"] = round(
            float(healthy) - realised_margin, 4)
    return realised, delta, []


def _eval_cost_pass_through(session: Session, snap: models.OutcomeSnapshot,
                            start: date, end: date) -> _Result:
    """Was the cost rise passed through. Realised price movement against the
    pre-increase price, and the realised margin on the current cost basis."""
    m = snap.baseline_metrics or {}
    price_before = _baseline_number(m, "price_before")
    cost_delta = _baseline_number(m, "cost_delta_pct")
    missing = [_missing_baseline(k) for k, v in
               (("price_before", price_before), ("cost_delta_pct", cost_delta))
               if v is None]
    if not missing and price_before is not None and price_before <= 0:
        missing.append(
            "Baseline 'price_before' is non-positive, so a realised price "
            "change cannot be expressed against it.")
    if missing:
        return {}, {}, missing
    assert price_before is not None and cost_delta is not None

    price, basis, gaps = _product_price_and_cost(session, snap, start, end)
    if gaps:
        return {}, {}, gaps
    assert price is not None and basis is not None

    price_change = float((price - price_before) / price_before)
    realised_margin = float((price - basis.unit_cost) / price)
    realised = {
        "window_days": int(snap.horizon_days),
        "window_avg_unit_price": float(round(price, 2)),
        "price_change_pct_vs_before": round(price_change, 4),
        "window_unit_cost_basis": float(round(basis.unit_cost, 2)),
        "realised_margin_pct": round(realised_margin, 4),
    }
    delta = {
        # The detector's own gap, re-measured: how much of the cost rise the
        # price has still not covered. ≤ 0 means fully passed through.
        "pass_through_gap_pct": round(float(cost_delta) - price_change, 4),
    }
    at_detection = _baseline_number(m, "resulting_margin_pct")
    if at_detection is not None:
        delta["margin_change_pp_vs_detection"] = round(
            realised_margin - float(at_detection), 4)
    return realised, delta, []


_EVALUATORS: dict[str, Callable[[Session, models.OutcomeSnapshot, date, date],
                                _Result]] = {
    SignalType.CUSTOMER_DECLINE.value: _eval_customer_decline,
    SignalType.CUSTOMER_DORMANCY.value: _eval_customer_dormancy,
    SignalType.MARGIN_DETERIORATION.value: _eval_margin_deterioration,
    SignalType.COST_PASS_THROUGH.value: _eval_cost_pass_through,
}


def evaluate(session: Session, snapshot: models.OutcomeSnapshot, *,
             as_of: Optional[date] = None,
             tz: Optional[str] = None) -> OutcomeEvaluation:
    """One snapshot's realised outcome, computed from persisted rows.

    Deterministic given (rows, ``as_of``): the router passes the business day,
    tests pass a fixed date. Order of the gates matters and is deliberate —
    horizon first (PENDING dominates: before the window closes nothing may be
    asserted, favourable or not), then observation coverage, then the
    category's own evidence.

    The coverage gate is the §1 discipline applied to the window itself: an
    empty window in a book last observed *before* the window closed is not
    "no sales", it is "not looked". Only when the organization's latest sale
    date reaches the window end does absence of rows become evidence of
    absence.
    """
    # The tenant's zone, not the deployment's: which *day* a decision was
    # accepted on, and whether the horizon has closed, are facts about the
    # business's calendar — the same argument every insight surface makes by
    # passing ``th.timezone``. A bare default here flips PENDING a day early
    # or late for any tenant whose books are not kept in the server's zone.
    ref = as_of or clock.today(tz)
    accepted = clock.to_local(clock.aware(snapshot.accepted_at), tz)
    start = accepted.date()
    end = start + timedelta(days=int(snapshot.horizon_days))

    if ref < end:
        return OutcomeEvaluation(
            status=OutcomeStatus.PENDING, window_start=start, window_end=end,
            as_of=ref, realised={}, delta={}, missing=(),
            note=(f"Horizon of {snapshot.horizon_days} days ends "
                  f"{end.isoformat()}; {(end - ref).days} day(s) remain. "
                  "Nothing is asserted yet."))

    last_sale = agg.last_sale_date(session, snapshot.organization_id)
    if last_sale is None or last_sale < end:
        observed = last_sale.isoformat() if last_sale else "no sale on record"
        return _unknown(
            start, end, ref,
            f"The book's sales are observed through {observed}, but the "
            f"evaluation window ends {end.isoformat()}. Until data covers the "
            "window, an empty window cannot be told apart from an unsynced "
            "one, and no realised outcome is asserted.")

    fn = _EVALUATORS.get(snapshot.category)
    if fn is None:
        return _unknown(
            start, end, ref,
            f"No deterministic evaluator exists for category "
            f"{snapshot.category!r} in this version; its realised outcome is "
            "not measurable yet. The baseline is captured and a later "
            "increment can evaluate it.")

    realised, delta, missing = fn(session, snapshot, start, end)
    if missing:
        return _unknown(start, end, ref, *missing)
    return OutcomeEvaluation(
        status=OutcomeStatus.REALISED, window_start=start, window_end=end,
        as_of=ref, realised=realised, delta=delta, missing=())
