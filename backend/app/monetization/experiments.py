"""Pricing experiments PIE can actually run — and the sample sizes that say why.

The most useful output of this module is a refusal. A conventional price A/B
test needs a sample this business does not have and will not have for years:
detecting a 10-point change in win rate at 30% baseline needs roughly 350
qualified prospects *per arm*, and PIE will speak to a few dozen. Computing that
number rather than asserting it is the point — it turns "we should A/B test our
pricing" into "we cannot, and here is what to do instead".

What replaces it is sequential and qualitative: run one price at a time against
a homogeneous segment, read willingness-to-pay from the *shape* of the
objection, and treat a signed contract at a price as the strongest evidence
available even at n=1. Each experiment below therefore states what it can
conclude at realistic n, which is usually less than its authors would like.

``sample_size_per_arm`` uses normal-approximation constants rather than a stats
library: this repository's dependency list is short on purpose, and two z-values
do not justify lengthening it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

#: Two-sided alpha = 0.05, power = 0.80. The pair almost every design uses, so
#: they are named here once rather than passed at every call site.
Z_ALPHA_TWO_SIDED = 1.959964
Z_POWER = 0.841621


def sample_size_per_arm(baseline_rate: float, absolute_mde: float,
                        z_alpha: float = Z_ALPHA_TWO_SIDED,
                        z_power: float = Z_POWER) -> Optional[int]:
    """Prospects needed per arm to detect ``absolute_mde`` on a conversion rate.

    ``None`` — not a large number, and not zero — when the design is impossible
    as stated: a non-positive effect to detect, or a baseline outside (0, 1).
    A sample size of ``0`` would read as "no evidence needed", which is the
    exact inversion of what an impossible design means.
    """
    if not (0.0 < baseline_rate < 1.0) or absolute_mde <= 0.0:
        return None
    p2 = baseline_rate + absolute_mde
    if not (0.0 < p2 < 1.0):
        return None
    pooled = (baseline_rate + p2) / 2.0
    numerator = (z_alpha * math.sqrt(2 * pooled * (1 - pooled))
                 + z_power * math.sqrt(baseline_rate * (1 - baseline_rate)
                                       + p2 * (1 - p2))) ** 2
    return int(math.ceil(numerator / (absolute_mde ** 2)))


@dataclass(frozen=True)
class Arm:
    label: str
    structure: str
    annual_value_note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"label": self.label, "structure": self.structure,
                "annual_value_note": self.annual_value_note}


@dataclass(frozen=True)
class Experiment:
    """One runnable pricing test, with what would make it uninterpretable."""

    key: str
    title: str
    hypothesis: str
    arms: tuple[Arm, ...]
    target_customer: str
    #: The conversion the test moves, and how much movement matters.
    baseline_rate: float
    minimum_detectable_effect: float
    success_metric: str
    failure_metric: str
    collect: tuple[str, ...]
    bias: tuple[str, ...]
    interpretation: str
    #: What can honestly be concluded at the sample PIE will really get.
    at_realistic_n: str

    def required_sample_per_arm(self) -> Optional[int]:
        return sample_size_per_arm(self.baseline_rate,
                                   self.minimum_detectable_effect)

    def as_dict(self) -> dict[str, Any]:
        n = self.required_sample_per_arm()
        return {
            "key": self.key, "title": self.title, "hypothesis": self.hypothesis,
            "arms": [a.as_dict() for a in self.arms],
            "target_customer": self.target_customer,
            "baseline_rate": self.baseline_rate,
            "minimum_detectable_effect": self.minimum_detectable_effect,
            "required_sample_per_arm": n,
            "required_sample_total": (None if n is None else n * len(self.arms)),
            "feasible_as_randomised_test": bool(n is not None and n <= 40),
            "success_metric": self.success_metric,
            "failure_metric": self.failure_metric,
            "collect": list(self.collect), "bias": list(self.bias),
            "interpretation": self.interpretation,
            "at_realistic_n": self.at_realistic_n,
        }


EXPERIMENTS: tuple[Experiment, ...] = (
    Experiment(
        key="e1_price_level",
        title="Flat subscription at three levels",
        hypothesis=("Willingness to pay for the mid-market segment is above "
                    "₹5L/year and the win rate does not fall materially "
                    "between ₹2L and ₹5L — i.e. the low anchor is leaving "
                    "money on the table rather than buying adoption."),
        arms=(Arm("A", "₹2,00,000 / year flat", "Below cost to serve at mid scale."),
              Arm("B", "₹5,00,000 / year flat"),
              Arm("C", "₹10,00,000 / year flat")),
        target_customer="Mid-market distributor, ₹10-100 Cr gross profit, one ERP.",
        baseline_rate=0.30, minimum_detectable_effect=0.10,
        success_metric=("Signed contracts per qualified prospect, and — more "
                        "informative at this n — the price at which the first "
                        "objection changes from 'too expensive' to 'what is the "
                        "business case'."),
        failure_metric=("Win rate at B below A by more than the arm difference "
                        "in qualified-prospect quality, or any arm where "
                        "procurement escalates the decision above the sponsor."),
        collect=("Sponsor's title and signing authority",
                 "Whether the price triggered a procurement process at all",
                 "Time from first meeting to signature",
                 "The counter-offer, when there is one — a counter is a "
                 "willingness-to-pay reading and is worth more than a decline",
                 "The customer's own stated ROI arithmetic, in their words"),
        bias=("Founder-led selling: the founder sells arm C harder because it "
              "matters more, and the price effect is confounded with effort.",
              "Segment leakage: the first ten prospects are warm introductions "
              "and are not the population any of this generalises to.",
              "Anchoring across arms once prospects talk to each other, which "
              "in a single industrial vertical in one city they will."),
        interpretation=("Read the counter-offers, not the win rate. Three "
                        "counters clustering at ₹6-8L is a stronger signal "
                        "about willingness to pay than a win-rate difference "
                        "this sample can never make significant."),
        at_realistic_n=("At n=8-12 per arm, nothing about win rate is "
                        "significant. What *is* readable: the objection type, "
                        "the counter-offer distribution, and whether the price "
                        "moved the decision to a different person.")),
    Experiment(
        key="e2_hybrid_vs_flat",
        title="Hybrid platform fee + GMV component versus a larger flat fee",
        hypothesis=("A ₹2L platform fee plus 0.1% of connected-book GMV wins "
                    "more deals than a ₹5L flat fee of similar expected value, "
                    "because the entry price is lower and the meter is a number "
                    "the customer already trusts."),
        arms=(Arm("A", "₹2,00,000 / year + 0.10% of connected-book GMV",
                  "At ₹100 Cr book: ₹2L + ₹10L = ₹12L."),
              Arm("B", "₹5,00,000 / year flat")),
        target_customer="₹50-200 Cr revenue distributor with a connected ERP.",
        baseline_rate=0.30, minimum_detectable_effect=0.12,
        success_metric=("Contracts signed, and separately: whether the customer "
                        "accepted the GMV meter without asking for a cap. An "
                        "accepted uncapped meter is the finding that decides "
                        "whether the transaction model is available at all."),
        failure_metric=("Customers demanding a cap at or below the flat-fee "
                        "equivalent — that converts arm A into arm B with extra "
                        "administration and no expansion path."),
        collect=("Whether GMV was accepted as the meter, or contested",
                 "Which GMV definition the customer proposed instead",
                 "Requested cap level, as a multiple of the platform fee",
                 "Whether finance or the sponsor owned the objection"),
        bias=("Arm A looks cheaper at signature and more expensive at renewal; "
              "a same-year comparison measures entry resistance only.",
              "Customers with a small book self-select into A."),
        interpretation=("If the cap requested clusters at 2-3x the platform "
                        "fee, the hybrid is real and the cap belongs in the "
                        "standard contract. If it clusters at 1x, the market is "
                        "buying a subscription and the meter is theatre."),
        at_realistic_n=("Two arms, n=10 each, is enough to see whether the "
                        "meter is *acceptable* — a yes/no most prospects answer "
                        "in the first meeting — and not enough to price it.")),
    Experiment(
        key="e3_performance_fee",
        title="Performance fee on measured incremental gross profit",
        hypothesis=("A low platform fee plus 5% of attributed incremental gross "
                    "profit closes faster than any fixed price, and the "
                    "resulting invoice is disputed."),
        arms=(Arm("A", "₹1,50,000 / year + 5% of attributed incremental gross "
                        "profit, capped at ₹15,00,000",
                  "The cap is the experiment's safety rail in both directions."),),
        target_customer=("A design partner willing to have a pre-go-live "
                         "baseline captured — which is the binding constraint, "
                         "not the price."),
        baseline_rate=0.30, minimum_detectable_effect=0.15,
        success_metric=("An invoice raised on attributed value and paid without "
                        "adjustment. That single event is the whole experiment: "
                        "it is the only way to learn whether attribution "
                        "survives contact with a finance department."),
        failure_metric=("Any dispute about the baseline. One dispute is not "
                        "noise here — it is the model failing, because the "
                        "dispute recurs every quarter for the life of the "
                        "contract."),
        collect=("Baseline captured before go-live, with the customer's sign-off "
                 "on the window and the figures",
                 "Every line of the first invoice the customer questioned",
                 "Time spent by both sides reconciling it — the real cost of "
                 "this model is measured in finance hours, not in rate",
                 "Whether the customer's own analysts reproduced the number"),
        bias=("Selection: only a customer already convinced signs a "
              "performance deal, so win-rate evidence from it is worthless.",
              "The first year's baseline is the cleanest one this model will "
              "ever have; year three is the test and the experiment cannot "
              "reach it."),
        interpretation=("Treat as a *measurement* experiment, not a pricing "
                        "one. It answers 'can attribution be billed on', and "
                        "the answer is binary."),
        at_realistic_n=("n=1 is sufficient. A single disputed invoice rules "
                        "the model out; a single clean one does not rule it "
                        "in, but it is the prerequisite for everything else.")),
    Experiment(
        key="e4_per_rfq",
        title="Per-RFQ pricing, and what it does to adoption",
        hypothesis=("Metering RFQs suppresses the routing volume that produces "
                    "the value — the adoption share falls relative to a flat "
                    "fee at comparable annual cost."),
        arms=(Arm("A", "₹40 per RFQ processed, ₹1,50,000 annual minimum",
                  "At 40,000 RFQs: ₹16,00,000."),
              Arm("B", "₹8,00,000 / year flat, unlimited")),
        target_customer="Two comparable mid-market distributors.",
        baseline_rate=0.55, minimum_detectable_effect=0.10,
        success_metric=("PIE-routed share of total enquiries after six months. "
                        "This is measurable from the platform's own rows and "
                        "does not depend on the customer reporting anything."),
        failure_metric=("Routed share under the meter materially below the flat "
                        "arm. That is the incentive defect showing up as "
                        "behaviour, and it invalidates per-RFQ pricing "
                        "regardless of what the revenue looks like."),
        collect=("Routed enquiries per week per arm",
                 "Which enquiry types were withheld — value, customer, urgency",
                 "Whether anyone at the customer was told about the meter; a "
                 "meter nobody mentions to the desk cannot change behaviour"),
        bias=("Two customers are not a sample; they are two anecdotes with a "
              "shared instrument.",
              "Hawthorne: an arm that knows it is being measured on routing "
              "will route."),
        interpretation=("A large behavioural gap is decisive even at n=2, "
                        "because the mechanism is not statistical — it is a "
                        "person deciding whether to paste an email into a "
                        "system that charges for it."),
        at_realistic_n=("n=2 answers the behavioural question and nothing "
                        "about revenue.")),
)


def all_experiments() -> list[dict[str, Any]]:
    return [e.as_dict() for e in EXPERIMENTS]


def feasibility_note() -> dict[str, Any]:
    """Why the randomised reading of these designs is unavailable, with numbers."""
    rows = [{"experiment": e.key,
             "required_per_arm": e.required_sample_per_arm(),
             "arms": len(e.arms)} for e in EXPERIMENTS]
    return {
        "rows": rows,
        "conclusion": (
            "Every design here needs one to three hundred qualified prospects "
            "per arm to read as a randomised test. PIE will have tens. So none "
            "of these is run for statistical significance: they are run to "
            "observe an objection, a counter-offer, a behaviour or a paid "
            "invoice — each of which is informative at n=1 to n=10 and none of "
            "which is a p-value. Designing them as A/B tests and then reading "
            "the win rates anyway is how a pricing decision gets made on noise."),
    }
