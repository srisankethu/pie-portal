"""Assembling the whole analysis, and turning it into an actual price.

Everything above this module computes one thing well. This one runs them in the
order the question needs — waterfall, then the three rate sweeps, then hybrids,
then PIE's own economics, then the recommendation — and it is the only place
that produces a *recommended structure*, so there is exactly one definition of
"what PIE should charge this customer" for the API, the calculator screen and
the CLI to share.

The recommendation is built by three constraints intersecting, in this order:

1. **A floor from cost.** A fee that does not clear the cost to serve at a
   sensible gross margin is a subsidy, not a price, and no ROI argument makes it
   one. This is the constraint that decides the 0.1% hypothesis.
2. **A ceiling from ROI.** The largest fee that still leaves the customer the
   return PIE requires of itself. Below the floor and above the ceiling is an
   empty band, and when it is empty the model says so instead of picking one.
3. **A target from value capture**, placed inside that band and clamped to it.

Then the structure: most of the target as a platform fee, the remainder as a
rate on a base the customer never has to disclose anything to compute.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from . import elasticity, experiments, plan, scorecard
from .config import (MonetizationParameters, PROVENANCE, load_parameters,
                     validation_list)
from .customer import CustomerProfile, PieImpact, Waterfall, build_waterfall, money
from .evaluate import evaluate, fee_for_roi
from .segments import ANSWERS_TO, ARCHETYPES, IMPACTS
from .strategies import (EnterpriseLicensePricing, FeeBase, HybridPricing,
                         IncrementalMarginPricing, MarginSharePricing,
                         MatchPricing, OrderPricing, PricingStrategy,
                         QuotePricing, RFQPricing, SavingsSharePricing,
                         SubscriptionPricing, TransactionPricing,
                         ValueDerivedSubscription)
from .unitecon import floor_price, unit_economics

_ZERO = Decimal("0")

#: What share of the recommended fee is the fixed platform component. Two
#: thirds: enough that PIE's revenue floor covers the cost to serve and the
#: forecast is a forecast rather than a hope, and little enough that the
#: variable half still grows with the customer. Stated here because it is a
#: structural choice, not a threshold anybody tunes.
PLATFORM_SHARE = Decimal("0.66")

#: The variable component is capped at this multiple of the platform fee. An
#: uncapped share of a growing book is how a vendor gets renegotiated out in
#: year three; a cap at 2x still allows the account to triple.
VARIABLE_CAP_MULTIPLE = Decimal("2.0")


def _rows(wf: Waterfall, strategies: list[PricingStrategy],
          params: MonetizationParameters) -> list[dict[str, Any]]:
    return [evaluate(wf, s.quote(wf, params), params).as_dict() for s in strategies]


# ── §5: the 0.1%-of-margin hypothesis ───────────────────────────────────────
def margin_hypothesis(wf: Waterfall,
                      params: Optional[MonetizationParameters] = None
                      ) -> dict[str, Any]:
    """The rate ladder, run against all three margin bases.

    A. total gross margin flowing through PIE, B. total gross margin of the
    whole book, C. incremental gross margin created by PIE. The brief calls A
    and C radically different; the numbers below put a multiple on it.
    """
    params = params or load_parameters()
    bases = (FeeBase.PIE_TOUCHED_GROSS_MARGIN, FeeBase.TOTAL_GROSS_MARGIN,
             FeeBase.INCREMENTAL_GROSS_MARGIN)
    out: dict[str, Any] = {"hypothesis_rate": params.hypothesis_margin_rate,
                           "ladder": {}}
    for base in bases:
        strategies = [
            (IncrementalMarginPricing(rate=rate)
             if base is FeeBase.INCREMENTAL_GROSS_MARGIN
             else MarginSharePricing(rate=rate, base=base))
            for rate in params.margin_rate_ladder]
        out["ladder"][base.value] = _rows(wf, strategies, params)

    cost_floor = floor_price(wf.profile, params)
    out["cost_floor"] = str(cost_floor)
    out["verdict"] = _hypothesis_verdict(wf, params, cost_floor)
    return out


def _hypothesis_verdict(wf: Waterfall, params: MonetizationParameters,
                        cost_floor: Decimal) -> dict[str, Any]:
    """Is 0.1% of margin viable for this customer? Answered, not hedged."""
    rate = params.hypothesis_margin_rate
    readings = {}
    for base in (FeeBase.PIE_TOUCHED_GROSS_MARGIN, FeeBase.TOTAL_GROSS_MARGIN,
                 FeeBase.INCREMENTAL_GROSS_MARGIN):
        strategy = (IncrementalMarginPricing(rate=rate)
                    if base is FeeBase.INCREMENTAL_GROSS_MARGIN
                    else MarginSharePricing(rate=rate, base=base))
        fee = strategy.quote(wf, params)
        ev = evaluate(wf, fee, params)
        multiple_to_floor = (float(cost_floor / fee.annual_fee)
                             if fee.annual_fee > 0 else None)
        readings[base.value] = {
            "annual_fee": str(fee.annual_fee),
            "covers_cost_to_serve": fee.annual_fee >= cost_floor,
            "shortfall_multiple": (None if multiple_to_floor is None
                                   else round(multiple_to_floor, 1)),
            "value_capture_pct": ev.value_capture_pct,
            "customer_roi": ev.customer_roi,
        }
    viable = any(r["covers_cost_to_serve"] for r in readings.values())
    return {
        "viable": viable,
        "readings": readings,
        "statement": (
            "0.1% of gross margin clears the cost to serve on at least one "
            "reading for this customer."
            if viable else
            "0.1% of gross margin does not clear the cost to serve on any "
            "reading of 'margin'. It is not a low price; it is not a price."),
    }


# ── §6: transaction pricing ─────────────────────────────────────────────────
def transaction_ladder(wf: Waterfall,
                       params: Optional[MonetizationParameters] = None
                       ) -> dict[str, Any]:
    params = params or load_parameters()
    return {
        "cost_floor": str(floor_price(wf.profile, params)),
        "ladder": {
            base.value: _rows(
                wf, [TransactionPricing(rate=r, base=base)
                     for r in params.gmv_rate_ladder], params)
            for base in (FeeBase.PIE_TOUCHED_GMV, FeeBase.TOTAL_GMV)},
    }


# ── §7: subscription derived from value ─────────────────────────────────────
def subscription_ladder(wf: Waterfall,
                        params: Optional[MonetizationParameters] = None
                        ) -> dict[str, Any]:
    params = params or load_parameters()
    rows = _rows(wf, [ValueDerivedSubscription(capture_rate=c)
                      for c in params.capture_ladder], params)
    defensible = [r for r in rows
                  if params.value_capture_floor
                  <= (r["value_capture_pct"] or 0.0)
                  <= params.value_capture_ceiling
                  and r["clears_min_roi"]]
    return {
        "rows": rows,
        "capture_band": [params.value_capture_floor, params.value_capture_ceiling],
        "defensible_range": {
            "low": (defensible[0]["fee"]["annual_fee"] if defensible else None),
            "high": (defensible[-1]["fee"]["annual_fee"] if defensible else None),
        },
    }


# ── §8: hybrids ─────────────────────────────────────────────────────────────
def hybrid_structures(wf: Waterfall,
                      params: Optional[MonetizationParameters] = None
                      ) -> dict[str, Any]:
    """Hybrids A-E, each sized so its expected total lands near the target.

    The platform fee is derived from the same target the recommendation uses, so
    the five structures are genuinely comparable: they differ in *shape*, not in
    how ambitious each one is, which is the only way a shape comparison means
    anything.
    """
    params = params or load_parameters()
    target = money(wf.total_economic_value
                   * Decimal(str(params.value_capture_target)))
    platform = money(target * PLATFORM_SHARE)
    remainder = money(target - platform)
    cap = money(platform * VARIABLE_CAP_MULTIPLE)

    gmv_base = wf.pie_touched_gmv
    margin_base = wf.pie_touched_gross_margin
    incremental = wf.incremental_gross_profit
    covered_rfqs = wf.covered_with_pie.rfqs

    gmv_rate = float(remainder / gmv_base) if gmv_base > 0 else 0.0
    margin_rate = float(remainder / margin_base) if margin_base > 0 else 0.0
    perf_platform = money(target * Decimal("0.35"))
    perf_rate = (float((target - perf_platform) / incremental)
                 if incremental > 0 else 0.0)
    rfq_rate = (money(remainder / covered_rfqs) if covered_rfqs > 0 else _ZERO)

    built: list[tuple[str, PricingStrategy]] = [
        ("A. Platform fee + transaction fee",
         HybridPricing(platform_fee=platform, maximum_fee=money(platform + cap),
                       component=TransactionPricing(
                           rate=gmv_rate, base=FeeBase.PIE_TOUCHED_GMV))),
        ("B. Platform fee + % gross margin",
         HybridPricing(platform_fee=platform, maximum_fee=money(platform + cap),
                       component=MarginSharePricing(
                           rate=margin_rate,
                           base=FeeBase.PIE_TOUCHED_GROSS_MARGIN))),
        ("C. Low platform fee + performance fee",
         HybridPricing(platform_fee=perf_platform,
                       maximum_fee=money(perf_platform * Decimal("4")),
                       component=IncrementalMarginPricing(rate=perf_rate))),
        ("D. Minimum annual commitment + usage",
         HybridPricing(platform_fee=money(platform * Decimal("0.25")),
                       minimum_fee=target,
                       maximum_fee=money(target * Decimal("2")),
                       component=RFQPricing(rate=rfq_rate))),
        ("E. Enterprise licence, unlimited usage",
         EnterpriseLicensePricing(capture_rate=params.value_capture_target)),
    ]

    rows = []
    for label, strategy in built:
        ev = evaluate(wf, strategy.quote(wf, params), params)
        key = {"A": "hybrid_platform_gmv", "B": "hybrid_platform_margin",
               "C": "hybrid_platform_performance",
               "D": "hybrid_commitment_usage",
               "E": "enterprise_license"}[label[0]]
        card = scorecard.score(key)
        rows.append({
            "structure": label,
            **ev.as_dict(),
            "scorecard_key": key,
            "weighted_score": (None if card is None else round(card.weighted(), 3)),
            "qualitative": (None if card is None else
                            {c: getattr(card, c) for c in scorecard.CRITERIA}),
        })
    return {"target_fee": str(target), "platform_fee": str(platform),
            "variable_cap": str(cap), "rows": rows}


# ── all the single metrics, side by side ────────────────────────────────────
def all_strategies(wf: Waterfall,
                   params: Optional[MonetizationParameters] = None
                   ) -> list[dict[str, Any]]:
    """One representative instance of every model, sized to the same target.

    Rates are *solved* so that each single-metric model would collect the same
    target as the others on this customer. That is what makes the comparison
    about shape rather than about which rate somebody happened to type: a
    per-RFQ model at ₹40 and a GMV model at 1% are not a comparison of metrics,
    they are a comparison of two arbitrary numbers.
    """
    params = params or load_parameters()
    target = money(wf.total_economic_value
                   * Decimal(str(params.value_capture_target)))

    def rate_on(base_amount: Decimal) -> float:
        return float(target / base_amount) if base_amount > 0 else 0.0

    def unit_on(volume: Decimal) -> Decimal:
        return money(target / volume) if volume > 0 else _ZERO

    covered = wf.covered_with_pie
    strategies: list[PricingStrategy] = [
        SubscriptionPricing(annual_fee=target),
        ValueDerivedSubscription(capture_rate=params.value_capture_target),
        RFQPricing(rate=unit_on(covered.rfqs)),
        QuotePricing(rate=unit_on(covered.quotes)),
        MatchPricing(rate=unit_on(covered.quotes)),
        OrderPricing(rate=unit_on(covered.orders)),
        TransactionPricing(rate=rate_on(wf.pie_touched_gmv),
                           base=FeeBase.PIE_TOUCHED_GMV),
        MarginSharePricing(rate=rate_on(wf.pie_touched_gross_margin),
                           base=FeeBase.PIE_TOUCHED_GROSS_MARGIN),
        IncrementalMarginPricing(rate=rate_on(wf.incremental_gross_profit)),
        SavingsSharePricing(
            rate=rate_on(money(wf.procurement_savings + wf.gp_from_margin))),
        EnterpriseLicensePricing(capture_rate=params.value_capture_target),
    ]
    return _rows(wf, strategies, params)


# ── the recommendation ──────────────────────────────────────────────────────
def recommend(wf: Waterfall,
              params: Optional[MonetizationParameters] = None) -> dict[str, Any]:
    """The price PIE should actually ask this customer for, and its structure."""
    params = params or load_parameters()
    value = wf.total_economic_value
    cost_floor = floor_price(wf.profile, params)
    roi_ceiling = fee_for_roi(value, params.min_customer_roi)
    target = money(value * Decimal(str(params.value_capture_target)))

    band_empty = roi_ceiling is None or roi_ceiling < cost_floor
    if band_empty:
        chosen = cost_floor
        note = ("EMPTY BAND: the lowest fee that covers the cost to serve is "
                "above the highest fee that leaves this customer the required "
                "ROI. This customer cannot be served profitably at the required "
                "return — either the value model is too conservative, the cost "
                "to serve is too high for this size, or this segment should not "
                "be sold to at all. The figure below is the cost floor, shown "
                "so the gap is visible; it is not a recommendation.")
    else:
        chosen = max(cost_floor, min(target, roi_ceiling))
        note = ("Target capture, clamped into the band between the cost floor "
                "and the ROI ceiling.")

    rounded = params.round_fee(chosen)
    platform = params.round_fee(money(rounded * PLATFORM_SHARE))
    remainder = money(rounded - platform)
    gmv_base = wf.pie_touched_gmv
    gmv_rate = float(remainder / gmv_base) if gmv_base > 0 else 0.0

    structure = HybridPricing(
        platform_fee=platform,
        maximum_fee=money(platform * (Decimal("1") + VARIABLE_CAP_MULTIPLE)),
        component=TransactionPricing(rate=gmv_rate,
                                     base=FeeBase.PIE_TOUCHED_GMV))
    ev = evaluate(wf, structure.quote(wf, params), params)
    econ = unit_economics(wf.profile, ev.fee.annual_fee, params,
                          orders=wf.covered_with_pie.orders)

    # A design-partner price for the first customers. Discounted deliberately
    # and against a stated exchange — reference, data, a case study — with the
    # step-up written into the same contract, because a discount whose expiry
    # is a future conversation is a permanent discount.
    dp_fee = params.round_fee(max(cost_floor, money(rounded * Decimal("0.45"))))
    dp_eval = evaluate(wf, SubscriptionPricing(annual_fee=dp_fee).quote(wf, params),
                       params)

    return {
        "band": {
            "cost_floor": str(cost_floor),
            "roi_ceiling": (None if roi_ceiling is None else str(roi_ceiling)),
            "target_at_capture": str(target),
            "is_empty": band_empty,
            "note": note,
        },
        "recommended_annual_fee": str(rounded),
        "structure": {
            "platform_fee": str(platform),
            "variable_metric": "% of PIE-touched GMV",
            "variable_rate": round(gmv_rate, 6),
            "variable_rate_pct": f"{gmv_rate * 100:.3f}%",
            "variable_cap": str(money(platform * VARIABLE_CAP_MULTIPLE)),
            "why": ("The platform fee covers the cost to serve and makes the "
                    "revenue forecastable; the GMV rate grows the account "
                    "without asking the customer to disclose cost, which is the "
                    "single largest source of resistance in the scorecard."),
        },
        "evaluation": ev.as_dict(),
        "pie_unit_economics": econ.as_dict(),
        "design_partner_offer": {
            "annual_fee": str(dp_fee),
            "discount_vs_list": round(1.0 - float(dp_fee / rounded), 3)
            if rounded > 0 else None,
            "conditions": [
                "Named reference and a written case study after two quarters.",
                "Baseline captured before go-live, so the value report is "
                "arguable at renewal rather than negotiated.",
                "Contractual step-up to list at the second renewal, written "
                "into this contract — not deferred to a future conversation.",
                "Full ERP connection, without which none of the above is "
                "measurable.",
            ],
            "evaluation": dp_eval.as_dict(),
        },
    }


# ── whole-segment and whole-model reports ───────────────────────────────────
def segment_report(profile: CustomerProfile, impact: PieImpact,
                   params: Optional[MonetizationParameters] = None
                   ) -> dict[str, Any]:
    params = params or load_parameters()
    wf = build_waterfall(profile, impact, params)
    return {
        "waterfall": wf.as_dict(),
        "margin_hypothesis": margin_hypothesis(wf, params),
        "transaction_ladder": transaction_ladder(wf, params),
        "subscription_ladder": subscription_ladder(wf, params),
        "hybrids": hybrid_structures(wf, params),
        "all_strategies": all_strategies(wf, params),
        "recommendation": recommend(wf, params),
    }


def strategic_test(acv: dict[str, Decimal],
                   params: Optional[MonetizationParameters] = None
                   ) -> dict[str, Any]:
    """§21: software, or economic participation in industrial commerce?

    Answered by computing the thing the question is really about — **the take
    rate PIE's recommended price already implies** — rather than by taking a
    position on it.

    The first draft of this function asserted that the transaction flow dwarfs
    the subscription business, and the numbers said otherwise: at a blended
    ₹203 Cr of GMV per customer, ten basis points is ₹20 lakh against a
    recommended fee of about ₹1 crore. The subscription is the *larger* number.
    That inversion is the finding, and it reframes the question: PIE is not
    choosing whether to participate in the transaction — a value-derived fee is
    already about half a percent of the GMV it touches — it is choosing whether
    to *call* it a take rate and let it float with the customer's book.
    """
    params = params or load_parameters()
    mix = plan.SCENARIOS[1].mix()
    milestones = plan.arr_at(acv, mix)
    blended_gmv = sum(
        (Decimal(str(share)) * ARCHETYPES[key].annual_gmv
         for key, share in mix.items()), _ZERO)
    blended_acv = sum((Decimal(str(share)) * acv.get(key, _ZERO)
                       for key, share in mix.items()), _ZERO)
    implied = float(blended_acv / blended_gmv) if blended_gmv > 0 else 0.0

    #: The rates the flow would have to be billed at to match the subscription.
    ladder = (0.001, 0.0025, 0.005, 0.01)
    flow = [{
        "customers": str(n),
        "gmv_under_management": str(money(blended_gmv * Decimal(n))),
        "subscription_arr": str(money(blended_acv * Decimal(n))),
        **{f"take_rate_{r * 100:g}pct": str(money(blended_gmv * Decimal(n)
                                                  * Decimal(str(r))))
           for r in ladder},
    } for n in plan.ARR_MILESTONES]

    return {
        "arr_from_subscription": milestones,
        "blended_gmv_per_customer": str(blended_gmv),
        "blended_acv": str(blended_acv),
        "implied_take_rate": round(implied, 6),
        "implied_take_rate_pct": f"{implied * 100:.3f}%",
        "take_rate_ladder": [f"{r * 100:g}%" for r in ladder],
        "transaction_flow": flow,
        "reading": (
            f"The recommended fee is already {implied * 100:.2f}% of the GMV a "
            "customer puts through the platform. So the choice is not whether "
            "to participate in the transaction — arithmetically PIE already "
            "does — but whether to bill it as a rate that floats with the "
            "customer's book or as a fee fixed until renewal. Ten basis points "
            "is not the alternative to the subscription; it is roughly a fifth "
            "of it, which is why a take rate is a way to price the same value "
            "rather than a larger business hiding behind a smaller one. What a "
            "take rate buys is that the number moves without a renegotiation, "
            "and what it costs is that it can only be collected where the "
            "platform is in the path of the order. Today it is in the path of "
            "the quote."),
    }


def full_report(params: Optional[MonetizationParameters] = None,
                impact_key: str = "base") -> dict[str, Any]:
    """Every segment, every model, plus PIE's own five-year shape."""
    params = params or load_parameters()
    impact = IMPACTS.get(impact_key, IMPACTS["base"])

    segments = {}
    acv: dict[str, Decimal] = {}
    for key, profile in ARCHETYPES.items():
        report = segment_report(profile, impact, params)
        report["answers"] = ANSWERS_TO.get(key, "")
        segments[key] = report
        acv[key] = Decimal(report["recommendation"]["recommended_annual_fee"])

    sensitivity = {
        name: {key: segment_report(ARCHETYPES[key], impact_set, params)
               ["recommendation"]["recommended_annual_fee"]
               for key in ("small", "mid", "large")}
        for name, impact_set in IMPACTS.items()}

    return {
        "parameters_version": params.version,
        "impact_set": impact_key,
        "segments": segments,
        "impact_sensitivity": sensitivity,
        "scorecard": scorecard.full(),
        "five_year": plan.five_year(acv, params),
        "elasticity": elasticity.scenarios(prospects=200, params=params),
        "experiments": {"designs": experiments.all_experiments(),
                        "feasibility": experiments.feasibility_note()},
        "strategic_test": strategic_test(acv, params),
        "evidence": {
            "needs_validation": validation_list(params),
            "provenance": {name: {"grade": grade.value, "why": note}
                           for name, (grade, note) in sorted(PROVENANCE.items())},
        },
    }
