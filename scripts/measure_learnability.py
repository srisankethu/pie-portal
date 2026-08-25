#!/usr/bin/env python3
"""Read-only. How much of this book can be *learned from*, and by which method.

``docs/concepts/14-machine-learning.md`` decides every technique in that category
on one quantity: **how many observations the book contains per thing a model
would have to learn about.** That number is not a matter of opinion and it moves
as the business trades, so it belongs in a script rather than in a paragraph —
this is the run behind that document, and re-running it is how its verdicts are
re-checked a year from now rather than re-argued.

It answers, per candidate learning target: how many examples exist, how many
carry a *label*, how those labels are distributed, and whether that clears the
floor the method needs. Where it cannot clear the floor it says so — a target
with no rows reports NOT_ESTIMABLE, never a benign zero.

**It reuses the detectors' own definitions rather than restating them.** An
"order" here is ``aggregates.order_dates`` — one per source invoice, the same
function ``dormancy.py`` measures cadence with — so a census that says a customer
has eight orders cannot disagree with the screen that says the same. Restating
that key in this file is the responsibility duplication CLAUDE.md §2 names, and
the copy that drifts is always the one nobody reads.

**Counts and dates only: no cost, no margin, no price, anywhere in the output.**
That is a structural property, not a filter applied at the end — nothing below
selects a money column. The one figure that comes close is
``sale_lines_with_cost_basis``, and the distinction is worth stating because it
is exactly the one ``filterCounts.MFLOOR`` got wrong: MFLOOR was a count of rows
*below a margin boundary*, so walking the boundary recovered cost. A count of
rows that have a cost record *at all* has no boundary in it — it is a statement
about whether a row exists, and no sweep of any input moves it.

Run:  cd backend && python3 ../scripts/measure_learnability.py
      cd backend && python3 ../scripts/measure_learnability.py --org org_pie
      cd backend && python3 ../scripts/measure_learnability.py --json census.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select                       # noqa: E402

from app.db import SessionLocal                           # noqa: E402
from app.domain import models                             # noqa: E402
from app.signals import aggregates as agg                 # noqa: E402

# ── the floors, declared once and with their reasons ─────────────────────────
#
# A verdict is only worth reading if the bar it was judged against is written
# down beside it. These are the bars.

#: Events per parameter. The long-standing floor for a logistic fit (Peduzzi
#: et al.): below ~10 events of the *minority* class per estimated coefficient,
#: the fit is unstable and its confidence intervals are not to be believed. It
#: is a rule of thumb and recent work argues it is optimistic, which is a reason
#: to treat clearing it as necessary rather than sufficient.
EPV = 10

#: The size of model this census is judged against — deliberately modest. Ten
#: features is a small model for this kind of problem, so a target that cannot
#: clear the bar at ten cannot clear it at any size worth building.
NOMINAL_FEATURES = 10

#: Minority-class events a classifier needs before it is worth fitting.
MIN_MINORITY_EVENTS = EPV * NOMINAL_FEATURES

#: Covariates for a survival fit — fewer, because the method spends its data on
#: the baseline hazard rather than on interactions.
NOMINAL_COVARIATES = 5
MIN_SURVIVAL_EVENTS = EPV * NOMINAL_COVARIATES

#: Subjects, separately from events. Two hundred gaps drawn from four customers
#: is four customers' habits, however many rows it is.
MIN_SUBJECTS = 25

#: Inter-arrival observations one SKU needs before an intermittent-demand method
#: (Croston, SBA) can estimate its interval. Mirrors ``min_transactions_strong``
#: in ``commercial/config.py``: a bar the platform already refuses to display
#: below is not one a model should quietly train under.
CROSTON_MIN_INTERVALS = 6

#: And the share of the traded catalogue those SKUs have to be. An absolute
#: count alone was the first version of this check and it was wrong: twenty-five
#: forecastable SKUs out of three thousand reads ESTIMABLE while 99% of the book
#: remains unforecastable, which is the benign default in a verdict field. A
#: method that can only speak for a fifth of the catalogue is a special case,
#: not a stocking policy.
CROSTON_MIN_SKU_COVERAGE_PCT = 20.0

#: What "a few thousand rows with live dispositions" is being read as. §8 claims
#: its conditions are "checkable rather than arguable", and a phrase is
#: arguable — so it gets a number here, where changing it is a visible act. Two
#: thousand is the low end of the doc's own phrase, chosen so the trigger fires
#: at the point the doc would call it fired rather than later.
TEXT_MODEL_MIN_LABELLED = 2_000

#: Quotes a month at which a two-armed policy trial resolves a 10 pp win-rate
#: lift inside two quarters. §5.25 derives it: ~350 per arm at a 30% baseline,
#: so ~700 quotes, so ~250 a month over the better part of three months.
RANDOMISED_POLICY_MIN_QUOTES_PER_MONTH = 250

#: How the §8 triggers report. ``UNKNOWN`` is the one that earns its place: a
#: trigger whose *measurement* has no evidence behind it is not "not yet". An
#: empty corpus on a book where nothing was ever captured says nothing about
#: how much demand arrives, and reporting it as 0 of 2,000 would be a
#: measurement where there is an absence — §1, in the field that decides
#: whether work starts.
FIRED = "FIRED"
NOT_YET = "NOT_YET"
UNKNOWN = "UNKNOWN"
#: The countable half is met and what remains is a person's call. Distinct from
#: FIRED because the doc asks for a judgement and this script must not make it.
JUDGEMENT = "JUDGEMENT"
#: Closed by argument rather than by data, and it does not expire.
NEVER = "NEVER"

ESTIMABLE = "ESTIMABLE"
MARGINAL = "MARGINAL"
NOT_ESTIMABLE = "NOT_ESTIMABLE"


def verdict(actual: int, floor: int) -> str:
    """Three bands, and the middle one is not a courtesy.

    Half a floor is where a fit stops erroring and starts producing numbers that
    look fine and are not — the range where a method is most dangerous, so it
    gets its own name rather than being rounded to whichever neighbour.
    """
    if actual >= floor:
        return ESTIMABLE
    if actual * 2 >= floor:
        return MARGINAL
    return NOT_ESTIMABLE


def _weaker(a: str, b: str) -> str:
    """The more pessimistic of two verdicts. Two bars mean both must be cleared."""
    order = (NOT_ESTIMABLE, MARGINAL, ESTIMABLE)
    return a if order.index(a) <= order.index(b) else b


def _pct(part: int, whole: int) -> Optional[float]:
    """Share, or None. Never 0.0 for an empty denominator — that reads as a
    measured zero, and "nothing to divide" is a different statement."""
    return round(100.0 * part / whole, 1) if whole else None


def _resolve_org(session, requested: Optional[str]) -> str:
    ids = list(session.scalars(select(models.Organization.organization_id)
                               .order_by(models.Organization.organization_id)))
    if requested:
        if requested not in ids:
            sys.exit(f"No such organization: {requested}. Present: {', '.join(ids) or 'none'}")
        return requested
    if not ids:
        sys.exit("No organizations in this database. Nothing to measure.")
    if len(ids) > 1:
        sys.exit(f"{len(ids)} organizations present; pass --org. Present: {', '.join(ids)}")
    return ids[0]


# ── 1. the shape of the book ─────────────────────────────────────────────────
def book_shape(snapshot, session, org: str) -> dict[str, Any]:
    """Observations per subject — the arithmetic every verdict below rests on.

    ``docs/concepts/02-process-control.md`` §2 rejected an entire category on
    these three numbers. They are recomputed here rather than quoted, because a
    document quoting a measurement taken once is a document that expires.
    """
    sales = snapshot.sales
    customers = {s.customer_id for s in sales}
    products = {s.product_id for s in sales}
    months = {(s.date.year, s.date.month) for s in sales}
    customer_months = {(s.customer_id, s.date.year, s.date.month) for s in sales}
    customer_item_months = {(s.customer_id, s.product_id, s.date.year, s.date.month)
                            for s in sales}
    pairs = {(s.customer_id, s.product_id) for s in sales}

    first = min((s.date for s in sales), default=None)
    last = max((s.date for s in sales), default=None)
    span_months = round((last - first).days / 30.44, 1) if first and last else 0.0

    # Cost coverage: does a line have a cost basis on record at all. See the
    # module docstring for why this count is not the MFLOOR mistake.
    costed_products = {c.product_id for c in snapshot.costs}
    with_cost = sum(1 for s in sales if s.product_id in costed_products)

    return {
        "organization_id": org,
        "sale_lines": len(sales),
        "cost_lines": len(snapshot.costs),
        "customers_with_trade": len(customers),
        "customers_on_master": session.scalar(
            select(func.count()).select_from(models.Customer)
            .where(models.Customer.organization_id == org)) or 0,
        "products_traded": len(products),
        "products_on_master": session.scalar(
            select(func.count()).select_from(models.Product)
            .where(models.Product.organization_id == org)) or 0,
        "first_sale": first.isoformat() if first else None,
        "last_sale": last.isoformat() if last else None,
        "history_span_months": span_months,
        "calendar_months_traded": len(months),
        "customer_months": len(customer_months),
        "customer_item_months": len(customer_item_months),
        "customer_item_pairs": len(pairs),
        "sale_lines_with_cost_basis": with_cost,
        "cost_coverage_pct": _pct(with_cost, len(sales)),
        # The three ratios doc 02 turns on.
        "lines_per_product": round(len(sales) / len(products), 1) if products else None,
        "months_per_customer": (round(len(customer_months) / len(customers), 1)
                                if customers else None),
        "observations_per_customer_item": (round(len(customer_item_months) / len(pairs), 1)
                                           if pairs else None),
    }


# ── 2. inter-order gaps — the survival-analysis census ───────────────────────
def inter_order_gaps(snapshot) -> dict[str, Any]:
    """Completed inter-order intervals, per customer and in total.

    A gap is an *event* in the survival sense: an order arrived and the wait
    ended. The wait since a customer's last order is the *censored* observation,
    and it is counted separately because a method that treats it as an ended
    wait understates every interval in the book.
    """
    gaps: list[int] = []
    per_customer: dict[str, int] = {}
    censored = 0
    for cid in snapshot.customer_ids():
        dates = agg.order_dates(snapshot.sales_for_customer(cid))
        completed = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        # A same-day repeat is not an interval — it is one order the source
        # system split. ``cadence_of`` refuses a degenerate cadence for the same
        # reason; counting zeros here would drag every quantile toward nothing.
        completed = [g for g in completed if g > 0]
        per_customer[cid] = len(completed)
        gaps.extend(completed)
        if dates:
            censored += 1

    subjects = sum(1 for n in per_customer.values() if n >= 1)
    quantiles: dict[str, Optional[float]] = {"p25": None, "median": None, "p75": None}
    if len(gaps) >= 4:
        ordered = sorted(gaps)
        quantiles = {
            "p25": float(statistics.quantiles(ordered, n=4)[0]),
            "median": float(statistics.median(ordered)),
            "p75": float(statistics.quantiles(ordered, n=4)[2]),
        }

    return {
        "completed_gaps": len(gaps),
        "censored_observations": censored,
        "customers_with_at_least_one_gap": subjects,
        "customers_with_four_or_more_orders": sum(1 for n in per_customer.values() if n >= 3),
        "gap_days": quantiles,
        "floor_events": MIN_SURVIVAL_EVENTS,
        "floor_subjects": MIN_SUBJECTS,
        "verdict": (verdict(len(gaps), MIN_SURVIVAL_EVENTS)
                    if subjects >= MIN_SUBJECTS
                    else verdict(subjects, MIN_SUBJECTS)),
    }


# ── 3. demand intervals — doc 08's census, re-runnable ───────────────────────
def demand_intervals(snapshot) -> dict[str, Any]:
    """Distinct sale days per SKU: whether Croston or SBA is estimable at all.

    ``docs/concepts/08-intermittent-demand.md`` §2 measured this once and
    rejected the method. The point of recomputing it is that the rejection came
    with a claim — that growth does not fix it — and this is the number that
    would falsify that claim if it were wrong.
    """
    per_sku = Counter()
    for s in snapshot.sales:
        per_sku[s.product_id] += 0  # ensure the key exists even for one-day SKUs
    days: dict[str, set[date]] = defaultdict(set)
    for s in snapshot.sales:
        days[s.product_id].add(s.date)
    for pid, ds in days.items():
        per_sku[pid] = len(ds)

    histogram = Counter(per_sku.values())
    # Intervals, not occasions: n sale days give n-1 intervals.
    estimable = sum(1 for n in per_sku.values() if n - 1 >= CROSTON_MIN_INTERVALS)
    sold_once = histogram.get(1, 0)

    return {
        "skus_sold": len(per_sku),
        "sale_days_histogram": {str(k): v for k, v in sorted(histogram.items())},
        "skus_sold_on_one_day_only": sold_once,
        "skus_sold_on_one_day_only_pct": _pct(sold_once, len(per_sku)),
        "skus_with_enough_intervals": estimable,
        "skus_with_enough_intervals_pct": _pct(estimable, len(per_sku)),
        "floor_intervals_per_sku": CROSTON_MIN_INTERVALS,
        "floor_sku_coverage_pct": CROSTON_MIN_SKU_COVERAGE_PCT,
        # Both bars, and the weaker verdict wins. Enough SKUs in absolute terms
        # *and* enough of the catalogue — either alone passes a book the method
        # cannot actually serve.
        "verdict": _weaker(
            verdict(estimable, MIN_SUBJECTS),
            verdict(int(estimable * 100), int(CROSTON_MIN_SKU_COVERAGE_PCT * len(per_sku)))
            if per_sku else NOT_ESTIMABLE),
    }


# ── 4. quote win/loss — the one target whose label a human must supply ───────
def quote_outcomes(session, org: str) -> dict[str, Any]:
    rows = list(session.execute(
        select(models.QuoteOutcome.status, models.QuoteOutcome.loss_reason,
               models.QuoteOutcome.decided_at)
        .where(models.QuoteOutcome.organization_id == org)))
    by_status = Counter(r[0] for r in rows)
    decided = [r for r in rows if r[2] is not None]
    won = by_status.get("WON", 0)
    lost = by_status.get("LOST", 0)
    with_reason = sum(1 for r in rows if r[0] == "LOST" and r[1])
    minority = min(won, lost)

    lines = session.scalar(
        select(func.count()).select_from(models.QuoteDecision)
        .where(models.QuoteDecision.organization_id == org)) or 0
    overrides = session.scalar(
        select(func.count()).select_from(models.QuoteDecision)
        .where(models.QuoteDecision.organization_id == org,
               models.QuoteDecision.overridden.is_(True))) or 0

    return {
        "quotes_with_an_outcome_row": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "decided": len(decided),
        "won": won,
        "lost": lost,
        "minority_class_events": minority,
        "losses_with_a_reason": with_reason,
        "losses_with_a_reason_pct": _pct(with_reason, lost),
        "loss_reasons": dict(sorted(Counter(
            r[1] for r in rows if r[0] == "LOST" and r[1]).items())),
        "priced_lines_on_record": lines,
        "priced_lines_overridden": overrides,
        "floor_minority_events": MIN_MINORITY_EVENTS,
        "verdict": verdict(minority, MIN_MINORITY_EVENTS),
    }


# ── 5. payment lateness — the one label that arrives without being asked for ─
def payment_lateness(session, org: str) -> dict[str, Any]:
    rows = list(session.execute(
        select(models.PaymentApplication.customer_id,
               models.PaymentApplication.invoice_due_date,
               models.PaymentApplication.paid_on)
        .where(models.PaymentApplication.organization_id == org)))
    labelled = [r for r in rows if r[1] is not None]
    per_customer = Counter(r[0] for r in labelled)
    late = sum(1 for r in labelled if r[2] > r[1])

    # Both classes matter: a set with no on-time payments teaches nothing about
    # what distinguishes one, however many rows it holds.
    minority = min(late, len(labelled) - late)
    return {
        "payment_applications": len(rows),
        "with_a_due_date": len(labelled),
        "with_a_due_date_pct": _pct(len(labelled), len(rows)),
        "late": late,
        "on_time": len(labelled) - late,
        "minority_class_events": minority,
        "customers_with_labels": len(per_customer),
        "customers_with_six_or_more": sum(1 for n in per_customer.values() if n >= 6),
        "floor_minority_events": MIN_MINORITY_EVENTS,
        "verdict": verdict(minority, MIN_MINORITY_EVENTS),
    }


# ── 6. queue labels — whether the ranking has anything to learn from ─────────
def decision_labels(session, org: str) -> dict[str, Any]:
    rows = list(session.execute(
        select(models.Decision.status, models.Decision.decision_type)
        .where(models.Decision.organization_id == org)))
    by_status = Counter(r[0] for r in rows)
    # A judged card is one somebody took a verdict on. VIEWED is not a verdict —
    # doc 02 §5 makes the same call for the dismissal rate, and counting it here
    # would let an unworked queue pass as labelled training data.
    judged = sum(v for k, v in by_status.items()
                 if k in {"ACTIONED", "DISMISSED", "OVERRIDDEN", "RESOLVED"})
    dismissed = by_status.get("DISMISSED", 0)
    return {
        "decisions": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "by_decision_type": dict(sorted(Counter(r[1] for r in rows).items())),
        "judged": judged,
        "dismissed": dismissed,
        "minority_class_events": min(dismissed, judged - dismissed),
        "floor_minority_events": MIN_MINORITY_EVENTS,
        "verdict": verdict(min(dismissed, judged - dismissed), MIN_MINORITY_EVENTS),
    }


# ── 7. the text corpus ───────────────────────────────────────────────────────
def enquiry_corpus(session, org: str) -> dict[str, Any]:
    lines = session.scalar(
        select(func.count()).select_from(models.InboundLine)
        .where(models.InboundLine.organization_id == org)) or 0
    by_channel = Counter(session.scalars(
        select(models.InboundLine.channel)
        .where(models.InboundLine.organization_id == org)))
    disp = Counter(session.scalars(
        select(models.InboundLineDisposition.disposition)
        .where(models.InboundLineDisposition.organization_id == org,
               models.InboundLineDisposition.superseded_at.is_(None))))
    labelled = sum(disp.values())
    return {
        "inbound_lines": lines,
        "by_channel": dict(sorted(by_channel.items())),
        "with_a_live_disposition": labelled,
        "with_a_live_disposition_pct": _pct(labelled, lines),
        "dispositions": dict(sorted(disp.items())),
        "floor_minority_events": MIN_MINORITY_EVENTS,
        "verdict": verdict(labelled, MIN_MINORITY_EVENTS),
    }


# ── 8. feature completeness on the item master ───────────────────────────────
def feature_completeness(session, org: str) -> dict[str, Any]:
    total = session.scalar(
        select(func.count()).select_from(models.Product)
        .where(models.Product.organization_id == org)) or 0

    def _set(column) -> int:
        return session.scalar(
            select(func.count()).select_from(models.Product)
            .where(models.Product.organization_id == org, column.is_not(None))) or 0

    fields = {
        "manufacturer": _set(models.Product.manufacturer),
        "category": _set(models.Product.category),
        "hsn": _set(models.Product.hsn),
        "pie_record_id": _set(models.Product.pie_record_id),
    }
    return {
        "products": total,
        "populated": fields,
        "populated_pct": {k: _pct(v, total) for k, v in fields.items()},
    }


# ── 9. the §8 triggers, evaluated rather than remembered ─────────────────────
#
# `14-machine-learning.md` §8 says of its own conditions that they are
# "checkable rather than arguable". They were arguable in one respect nobody
# noticed: **nothing checked them.** Each gated technique waits for a condition,
# and the condition is evaluated by a person holding the doc in one hand and
# this census in the other, if they remember to. A trigger nobody evaluates is a
# backlog item that stays blocked whatever the data says.
#
# So this section answers, per technique: has the condition fired, how far off
# is it, and — the part that matters most — is the distance even a measurement.
#
# **UNKNOWN is not a softer NOT_YET.** Where the evidence behind a count is
# itself missing, the honest answer is that nothing is known. An empty enquiry
# corpus on a book that has never captured one is not "0 of 2,000 and climbing";
# a dismissal rate over an unworked queue is not "the detector is fine". Both
# would be §1's benign default in the field that decides whether work starts,
# and this file's whole purpose is to make verdicts falsifiable.


def _trigger(technique: str, section: str, status: str, why: str,
             measured: Any = None, needs: Any = None) -> dict[str, Any]:
    return {"technique": technique, "section": section, "status": status,
            "measured": measured, "needs": needs, "why": why}


def triggers(session, org: str, census: dict[str, Any]) -> list[dict[str, Any]]:
    """Every §8 condition, evaluated against this book.

    Reads the sections already computed above rather than re-querying for them:
    two counts of the same thing in one report is how a census starts
    disagreeing with itself.
    """
    quotes = census["quote_outcomes"]
    corpus = census["enquiry_corpus"]

    out: list[dict[str, Any]] = []

    # ── §5.1 quote win/loss ranker ──────────────────────────────────────────
    erp_quotes = session.scalar(
        select(func.count()).select_from(models.QuoteDoc)
        .where(models.QuoteDoc.organization_id == org)) or 0
    losses = quotes["losses_with_a_reason"]
    if erp_quotes == 0 and quotes["quotes_with_an_outcome_row"] == 0:
        out.append(_trigger(
            "Quote win/loss ranker", "§5.1", UNKNOWN,
            "No quote has been read from an ERP and none has been recorded by "
            "hand, so there is no denominator and no numerator. A book whose "
            "ZohoBooks.estimates.READ scope was never granted looks exactly "
            "like a book that raises no quotes.",
            measured=0, needs=MIN_MINORITY_EVENTS))
    elif losses >= MIN_MINORITY_EVENTS:
        out.append(_trigger(
            "Quote win/loss ranker", "§5.1", JUDGEMENT,
            f"{losses} losses carry a reason, past the {MIN_MINORITY_EVENTS} "
            "floor. The second half of this trigger is a judgement this script "
            "must not make: whether the loss-reason table has stopped being "
            "surprising. Read `loss_reasons` above and decide.",
            measured=losses, needs=MIN_MINORITY_EVENTS))
    else:
        out.append(_trigger(
            "Quote win/loss ranker", "§5.1", NOT_YET,
            f"{losses} of {MIN_MINORITY_EVENTS} losses carry a reason. The "
            "quotes are being read; what is missing is somebody saying why each "
            "one went. That is the capture screen's whole job.",
            measured=losses, needs=MIN_MINORITY_EVENTS))

    # ── §5.6 inter-order survival, and §5.22 behind it ──────────────────────
    #
    # Delegated to `dismissal_band`, not restated. That function already orders
    # its answers so the two "we do not know" cases come first, which is exactly
    # the distinction this section exists to preserve — a second copy here would
    # be the one that forgets.
    from app.decisions.outcomes import REJECTED_STATUS, JUDGED_STATUSES, dismissal_band

    dormancy = [d for d in session.execute(
        select(models.Decision.status)
        .where(models.Decision.organization_id == org,
               models.Decision.decision_type == "CUSTOMER_DORMANCY"))]
    judged = sum(1 for (s,) in dormancy if s in JUDGED_STATUSES)
    dismissed = sum(1 for (s,) in dormancy if s == REJECTED_STATUS)
    rate = (dismissed / judged) if judged else None
    band, note = dismissal_band(rate, judged)

    if band in ("NOT_REVIEWED", "INSUFFICIENT_DATA"):
        status, why = UNKNOWN, note
    elif band == "HIGH":
        status, why = FIRED, (
            note + " That is this trigger's condition: the ordering is wrong "
            "often enough to be worth modelling rather than re-thresholding.")
    else:
        status, why = NOT_YET, (
            note + " The trigger asks for HIGH specifically — a detector inside "
            "its band is one a survival model would not improve.")
    out.append(_trigger("Inter-order survival (Kaplan–Meier)", "§5.6", status,
                        why, measured=band, needs="HIGH"))
    out.append(_trigger(
        "Queue learning-to-rank", "§5.22",
        status if status is not FIRED else NOT_YET,
        (why + " And a second condition behind it, which is an act rather than "
         "a measurement: moving `queue_margin_drop_pp` first, and finding it "
         "did not fix the ordering. Nothing here can observe that having been "
         "tried.") if status is not UNKNOWN else why,
        measured=band, needs="HIGH, then a threshold change that did not help"))

    # ── §5.18 / §5.21 enquiry text models ───────────────────────────────────
    labelled = corpus["with_a_live_disposition"]
    if corpus["inbound_lines"] == 0:
        out.append(_trigger(
            "Enquiry routing and coverage (text models)", "§5.18, §5.21",
            UNKNOWN,
            "The corpus is empty. Since the capture door shipped that means "
            "nothing has come through it — which is a fact about capture, not "
            "about how much demand arrives. 0 of "
            f"{TEXT_MODEL_MIN_LABELLED} would read as progress toward a bar; "
            "there is no measurement here at all.",
            measured=0, needs=TEXT_MODEL_MIN_LABELLED))
    else:
        out.append(_trigger(
            "Enquiry routing and coverage (text models)", "§5.18, §5.21",
            FIRED if labelled >= TEXT_MODEL_MIN_LABELLED else NOT_YET,
            f"{labelled} of {corpus['inbound_lines']} captured lines carry a "
            f"live disposition, against a bar of {TEXT_MODEL_MIN_LABELLED}. The "
            "label is the scarce half: text arrives on its own, a disposition "
            "is somebody saying what became of the line.",
            measured=labelled, needs=TEXT_MODEL_MIN_LABELLED))

    # ── §5.17 embedding shortlist ───────────────────────────────────────────
    #
    # The one trigger with a subtlety the row count hides. `source_ref` carries
    # `quote:<id>` for a line captured at the Quote Builder — an ask somebody
    # chose to work. That subset is the right corpus for a *benchmark* and the
    # wrong one for coverage, and §7a.4 says so; reporting the split is what
    # keeps a later reader from taking one for the other.
    worked = session.scalar(
        select(func.count()).select_from(models.InboundLine)
        .where(models.InboundLine.organization_id == org,
               models.InboundLine.source_ref.like("quote:%"))) or 0
    lines = corpus["inbound_lines"]
    if lines == 0:
        out.append(_trigger(
            "Embedding shortlist for RFQ resolution", "§5.17", UNKNOWN,
            "No captured wording at all, so recall cannot be measured and "
            "cannot be said to be unmeasurable either.",
            measured=0, needs=TEXT_MODEL_MIN_LABELLED))
    else:
        out.append(_trigger(
            "Embedding shortlist for RFQ resolution", "§5.17",
            FIRED if lines >= TEXT_MODEL_MIN_LABELLED else NOT_YET,
            f"{lines} captured lines, {worked} of them from a worked quote "
            f"({_pct(worked, lines)}%). The worked subset is the benchmark "
            "corpus and is fine for measuring recall; it is *not* a coverage "
            "denominator, because every line in it is an ask somebody chose to "
            "work (§7a.4).",
            measured=lines, needs=TEXT_MODEL_MIN_LABELLED))

    # ── §5.25 randomised policy evaluation ──────────────────────────────────
    span = session.execute(
        select(func.min(models.QuoteDoc.date), func.max(models.QuoteDoc.date))
        .where(models.QuoteDoc.organization_id == org)).one()
    first, last = span
    if erp_quotes == 0 or first is None or last is None:
        out.append(_trigger(
            "Randomised policy evaluation", "§5.25", UNKNOWN,
            "No quotes read, so the arrival rate this trigger is about cannot "
            "be computed. Withdrawn as work in §7a.7 on the rate measured when "
            "the doc was written; this is the number that would reopen it.",
            measured=None,
            needs=RANDOMISED_POLICY_MIN_QUOTES_PER_MONTH))
    else:
        months = max(1.0, ((last - first).days or 1) / 30.44)
        per_month = round(erp_quotes / months, 1)
        out.append(_trigger(
            "Randomised policy evaluation", "§5.25",
            FIRED if per_month >= RANDOMISED_POLICY_MIN_QUOTES_PER_MONTH
            else NOT_YET,
            f"{per_month} quotes a month over {round(months, 1)} months, "
            f"against {RANDOMISED_POLICY_MIN_QUOTES_PER_MONTH}. §5.25 also "
            "requires a unit where the treatment does not leak between arms, "
            "and this book has three legal entities and three Books users — "
            "volume alone does not reopen it.",
            measured=per_month,
            needs=RANDOMISED_POLICY_MIN_QUOTES_PER_MONTH))

    # ── the two that are not waiting on anything ────────────────────────────
    out.append(_trigger(
        "Anything at item grain", "§5.7–§5.10", NEVER,
        "Closed by argument rather than by data: scaling this book multiplies "
        "subjects, not observations per subject. Only selling fewer, deeper "
        "lines would change it, and that is a strategy decision.",
        measured=None, needs=None))
    out.append(_trigger(
        "Any model exposed to a salesperson", "§4.3", NEVER,
        "A gate rather than a trigger, and it does not expire: §4.3 has to be "
        "satisfied by construction, never by a projection. Listed so this "
        "report is the whole of §8 rather than the countable part of it.",
        measured=None, needs=None))
    return out


def _print(census: dict[str, Any]) -> None:
    shape = census["book_shape"]
    print(f"\nLearnability census — {shape['organization_id']}")
    print("=" * 72)
    print(f"{shape['sale_lines']:>8} sale lines   "
          f"{shape['customers_with_trade']} customers with trade "
          f"(of {shape['customers_on_master']} on the master)   "
          f"{shape['products_traded']} items traded "
          f"(of {shape['products_on_master']})")
    print(f"{'':>8} {shape['first_sale']} to {shape['last_sale']}  "
          f"({shape['history_span_months']} months, "
          f"{shape['calendar_months_traded']} with trade in them)")
    print("\n  observations per subject — what every verdict below rests on")
    print(f"    lines per item traded          {shape['lines_per_product']}")
    print(f"    months traded per customer     {shape['months_per_customer']}")
    print(f"    observations per customer-item {shape['observations_per_customer_item']}")
    print(f"    lines with a cost basis        {shape['sale_lines_with_cost_basis']} "
          f"({shape['cost_coverage_pct']}%)")

    print("\n  per candidate target")
    for key, label, actual_key, floor_key in (
        ("inter_order_gaps", "time-to-next-order (survival)", "completed_gaps",
         "floor_events"),
        ("demand_intervals", "per-SKU demand interval (Croston)",
         "skus_with_enough_intervals", None),
        ("quote_outcomes", "quote win/loss (classifier)", "minority_class_events",
         "floor_minority_events"),
        ("payment_lateness", "payment lateness (classifier)", "minority_class_events",
         "floor_minority_events"),
        ("decision_labels", "queue ranking (learning to rank)", "minority_class_events",
         "floor_minority_events"),
        ("enquiry_corpus", "enquiry routing (text classifier)",
         "with_a_live_disposition", "floor_minority_events"),
    ):
        block = census[key]
        actual = block[actual_key]
        floor = block[floor_key] if floor_key else block["floor_intervals_per_sku"]
        print(f"    {label:<34} {actual:>6} usable  "
              f"(floor {floor})  {block['verdict']}")

    print("\n  item-master feature completeness")
    for field, pct in census["feature_completeness"]["populated_pct"].items():
        print(f"    {field:<34} {'—' if pct is None else f'{pct}%'}")

    print("\n" + "=" * 72)
    estimable = [k for k in ("inter_order_gaps", "demand_intervals", "quote_outcomes",
                             "payment_lateness", "decision_labels", "enquiry_corpus")
                 if census[k]["verdict"] == ESTIMABLE]
    if estimable:
        print(f"VERDICT: {len(estimable)} of 6 targets clear their floor: "
              f"{', '.join(estimable)}.")
        print("Clearing a floor is necessary, not sufficient — read "
              "docs/concepts/14-machine-learning.md")
        print("for what each one would have to survive before it could ship.")
    else:
        print("VERDICT: no target clears its floor on this book today.")
        print("That is a statement about the volume of evidence, not about the "
              "methods. The")
        print("floors and their reasoning are at the top of this file; "
              "docs/concepts/14-machine-learning.md")
        print("says which of them the business can change and which it cannot.")


#: Ordered so the two answers a reader must not conflate sit apart, and the
#: one that needs a person comes first — it is the only line that is a
#: prompt rather than a status.
_TRIGGER_ORDER = (JUDGEMENT, FIRED, NOT_YET, UNKNOWN, NEVER)


def _print_triggers(rows: list[dict[str, Any]]) -> None:
    print("\n\nThe §8 triggers — what each gated technique is waiting for")
    print("=" * 72)
    print("`14-machine-learning.md` §8 says these conditions are checkable "
          "rather than")
    print("arguable. Until this section existed nothing checked them.\n")
    for status in _TRIGGER_ORDER:
        for row in [r for r in rows if r["status"] == status]:
            print(f"  {status:<10} {row['technique']}  ({row['section']})")
            if row["measured"] is not None:
                print(f"{'':>13}measured {row['measured']}   "
                      f"needs {row['needs']}")
            for line in _wrap(row["why"], 56):
                print(f"{'':>13}{line}")
            print()
    unknown = sum(1 for r in rows if r["status"] == UNKNOWN)
    if unknown:
        print(f"  {unknown} trigger(s) report UNKNOWN rather than NOT_YET. That "
              "is not a softer")
        print("  no: the evidence behind the count is itself missing, so the "
              "distance to")
        print("  the bar is not a measurement. §1 — absence of evidence is not "
              "a pass.")


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", help="organization_id; required when more than one exists")
    ap.add_argument("--json", type=Path, help="also write the census as JSON")
    args = ap.parse_args()

    with SessionLocal() as session:
        org = _resolve_org(session, args.org)
        snapshot = agg.load_snapshot(session, org)
        census = {
            "book_shape": book_shape(snapshot, session, org),
            "inter_order_gaps": inter_order_gaps(snapshot),
            "demand_intervals": demand_intervals(snapshot),
            "quote_outcomes": quote_outcomes(session, org),
            "payment_lateness": payment_lateness(session, org),
            "decision_labels": decision_labels(session, org),
            "enquiry_corpus": enquiry_corpus(session, org),
            "feature_completeness": feature_completeness(session, org),
            "floors": {
                "events_per_parameter": EPV,
                "nominal_features": NOMINAL_FEATURES,
                "min_minority_events": MIN_MINORITY_EVENTS,
                "min_survival_events": MIN_SURVIVAL_EVENTS,
                "min_subjects": MIN_SUBJECTS,
                "croston_min_intervals": CROSTON_MIN_INTERVALS,
                "text_model_min_labelled": TEXT_MODEL_MIN_LABELLED,
                "randomised_policy_min_quotes_per_month":
                    RANDOMISED_POLICY_MIN_QUOTES_PER_MONTH,
            },
        }
        # After the sections, because it reads them rather than re-querying.
        census["triggers"] = triggers(session, org, census)

    _print(census)
    _print_triggers(census["triggers"])
    if args.json:
        args.json.write_text(json.dumps(census, indent=2, sort_keys=True))
        print(f"\nJSON written to {args.json}")


if __name__ == "__main__":
    main()
