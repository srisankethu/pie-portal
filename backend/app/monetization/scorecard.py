"""Scoring a pricing *metric* — and the incentives each one creates.

Two analyses that belong together because they answer the same question from
opposite ends. The scorecard asks what a metric is like to sell, measure and
live with; the game-theoretic register asks what a rational customer does once
the contract is signed. A metric that scores well on the first and badly on the
second is a metric that sells easily and then fails, which is the expensive
half of a pricing mistake.

**Scores are judgements, and they are labelled as such.** Nothing here is
measured — there is no closed-won cohort, no renewal, no billing dispute to
learn from — so every score is ``Evidence.ASSUMED`` and the weighted ranking is
a structured argument rather than a result. What makes it useful anyway is that
the weights are explicit and separable: a reader who disagrees changes a weight
and sees the ranking move, which is not something prose can offer.

**Direction is in the field name.** ``low_sales_friction`` and
``gaming_resistance`` are scored 1-10 with 10 as *good*, like every other
criterion, because a table where some columns are better high and some better
low is a table that gets summed wrongly exactly once and then trusted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import Enum
from typing import Any, Optional

from .config import Evidence

#: The objective function the brief states, as weights. Maximise PIE enterprise
#: value x adoption x customer ROI x revenue predictability; minimise sales
#: friction x measurement difficulty x gaming x churn. Each criterion below is
#: weighted by how directly it moves one of those eight terms — which is why
#: expansion potential and predictability carry more than auditability does.
DEFAULT_WEIGHTS: dict[str, float] = {
    "correlation_with_value": 1.5,
    "ease_of_understanding": 1.0,
    "ease_of_measurement": 1.25,
    "auditability": 1.0,
    "predictability": 1.5,
    "scalability": 1.25,
    "expansion_potential": 1.5,
    "gaming_resistance": 1.25,
    "low_sales_friction": 1.25,
    "customer_acceptance": 1.5,
    "incentive_alignment": 1.0,
}

CRITERIA = tuple(DEFAULT_WEIGHTS)


class Exposure(str, Enum):
    """How badly one failure mode bites a given metric."""

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    SEVERE = "SEVERE"


@dataclass(frozen=True)
class MetricScore:
    """One pricing metric, scored 1-10 on every criterion. 10 is always better."""

    key: str
    label: str
    correlation_with_value: int
    ease_of_understanding: int
    ease_of_measurement: int
    auditability: int
    predictability: int
    scalability: int
    expansion_potential: int
    gaming_resistance: int
    low_sales_friction: int
    customer_acceptance: int
    incentive_alignment: int
    note: str = ""

    def weighted(self, weights: Optional[dict[str, float]] = None) -> float:
        w = weights or DEFAULT_WEIGHTS
        total = sum(w.get(c, 0.0) for c in CRITERIA)
        if total <= 0:
            return 0.0
        return sum(getattr(self, c) * w.get(c, 0.0) for c in CRITERIA) / total

    def as_dict(self, weights: Optional[dict[str, float]] = None) -> dict[str, Any]:
        d = asdict(self)
        d["weighted_score"] = round(self.weighted(weights), 3)
        return d


@dataclass(frozen=True)
class GameTheory:
    """What a rational customer does to this metric once the contract is signed.

    The five named problems are the ones the brief calls out, and they are kept
    as separate fields rather than folded into one "gaming" paragraph because
    they have *different mitigations*: attribution is fixed with a baseline,
    bypass with contract language and product lock-in, opacity with a metric
    that does not need cost at all. A single risk score would hide which lever
    applies.
    """

    key: str
    attribution: tuple[Exposure, str]
    bypass: tuple[Exposure, str]
    margin_opacity: tuple[Exposure, str]
    under_reporting: tuple[Exposure, str]
    classification: tuple[Exposure, str]
    #: Does PIE earn more when the customer earns more? The brief asks whether
    #: it *should*; this field records whether, under this metric, it *does*.
    alignment: str
    mitigations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "alignment": self.alignment,
                               "mitigations": list(self.mitigations)}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, tuple) and len(value) == 2 \
                    and isinstance(value[0], Exposure):
                out[f.name] = {"exposure": value[0].value, "why": value[1]}
        return out


SCORES: tuple[MetricScore, ...] = (
    MetricScore(
        key="per_user", label="Per user (seat)",
        correlation_with_value=3, ease_of_understanding=10, ease_of_measurement=10,
        auditability=9, predictability=9, scalability=4, expansion_potential=3,
        gaming_resistance=4, low_sales_friction=8, customer_acceptance=8,
        incentive_alignment=2,
        note="The only metric here that is negatively correlated with the "
             "product's purpose: PIE exists so a desk of six can quote what "
             "twelve used to, and a seat meter bills the customer for not "
             "having adopted it. Scores well on everything that is easy and "
             "badly on everything that matters."),
    MetricScore(
        key="per_rfq", label="Per RFQ processed",
        correlation_with_value=5, ease_of_understanding=9, ease_of_measurement=9,
        auditability=8, predictability=5, scalability=7, expansion_potential=7,
        gaming_resistance=3, low_sales_friction=6, customer_acceptance=6,
        incentive_alignment=3,
        note="Taxes the one behaviour the platform needs most. A customer "
             "minimising the bill routes only the enquiries it already expects "
             "to win — exactly the set PIE adds least to — and the coverage "
             "that produces the value never happens."),
    MetricScore(
        key="per_quote", label="Per quote produced",
        correlation_with_value=6, ease_of_understanding=9, ease_of_measurement=9,
        auditability=8, predictability=5, scalability=7, expansion_potential=7,
        gaming_resistance=4, low_sales_friction=6, customer_acceptance=6,
        incentive_alignment=4,
        note="One step further down the funnel than per-RFQ and better for it, "
             "but it still bills work rather than outcome: a quote that loses "
             "costs the customer money twice."),
    MetricScore(
        key="per_match", label="Per successful match",
        correlation_with_value=6, ease_of_understanding=6, ease_of_measurement=7,
        auditability=5, predictability=5, scalability=7, expansion_potential=7,
        gaming_resistance=4, low_sales_friction=5, customer_acceptance=5,
        incentive_alignment=6,
        note="'Successful' is the whole problem. Every definitional edge — a "
             "match the customer did not use, an equivalent it rejected, a "
             "line it re-resolved by hand — is a monthly invoice dispute."),
    MetricScore(
        key="per_order", label="Per order",
        correlation_with_value=7, ease_of_understanding=8, ease_of_measurement=3,
        auditability=3, predictability=6, scalability=8, expansion_potential=8,
        gaming_resistance=3, low_sales_friction=6, customer_acceptance=7,
        incentive_alignment=8,
        note="Charges only on success, which sells well, and then cannot be "
             "computed. **There is no quote-to-order conversion.** The estimate "
             "is sent; the order arrives later as a customer PO and is entered "
             "independently; nothing joins them. Every invoice would have to be "
             "attributed by matching customer, product, quantity and date — an "
             "inference, made by the vendor, about the buyer's own book. Scored "
             "down from 6/6 on measurement and auditability once that was "
             "established rather than assumed."),
    MetricScore(
        key="gmv_pct", label="% of transaction value (GMV)",
        correlation_with_value=6, ease_of_understanding=9, ease_of_measurement=9,
        auditability=9, predictability=7, scalability=9, expansion_potential=9,
        gaming_resistance=6, low_sales_friction=5, customer_acceptance=5,
        incentive_alignment=7,
        note="The best-measured metric in the table, on one reading of it. "
             "Billed on the *whole connected book* the base is a synced ERP "
             "figure both sides can see, needing no cost disclosure and no "
             "attribution; billed on a PIE-touched subset it is not measurable "
             "at all, because no quote-to-order link exists. The scores here "
             "are for the whole-book reading. Its weakness is at the other end "
             "— a distributor on 21% gross margin hears '% of turnover' as a "
             "tax on a number that is mostly someone else's cost of goods."),
    MetricScore(
        key="gross_margin_pct", label="% of gross margin",
        correlation_with_value=8, ease_of_understanding=7, ease_of_measurement=7,
        auditability=6, predictability=6, scalability=9, expansion_potential=9,
        gaming_resistance=4, low_sales_friction=3, customer_acceptance=4,
        incentive_alignment=9,
        note="Aligned and defensible in theory. In practice it asks a "
             "distributor to expose landed cost to a vendor and then argue "
             "about rebates, freight loading and stock valuation once a "
             "quarter. This platform is unusually well placed to measure it — "
             "it computes margin from the customer's own ERP already — which "
             "improves measurement without improving willingness."),
    MetricScore(
        key="incremental_margin_pct", label="% of incremental gross margin",
        correlation_with_value=10, ease_of_understanding=5, ease_of_measurement=3,
        auditability=3, predictability=3, scalability=7, expansion_potential=8,
        gaming_resistance=2, low_sales_friction=3, customer_acceptance=4,
        incentive_alignment=10,
        note="Perfect correlation, unusable measurement. It requires a "
             "counterfactual — what the business would have earned without PIE "
             "— which nobody can observe and both sides have an interest in. "
             "attribution/ captures a pre-trial baseline, which makes this the "
             "one company that could argue it at all; it still cannot argue it "
             "in year three, when the baseline is three years stale and the "
             "market has moved."),
    MetricScore(
        key="savings_share", label="Savings share",
        correlation_with_value=8, ease_of_understanding=7, ease_of_measurement=5,
        auditability=6, predictability=3, scalability=5, expansion_potential=5,
        gaming_resistance=3, low_sales_friction=5, customer_acceptance=7,
        incentive_alignment=9,
        note="Easy to sign and hard to collect — the standard gainshare trade. "
             "The base is genuinely small here: procurement savings plus margin "
             "held is a fraction of the value PIE creates, most of which is won "
             "revenue that a savings definition excludes by construction."),
    MetricScore(
        key="flat_subscription", label="Value-derived subscription (banded)",
        correlation_with_value=6, ease_of_understanding=10, ease_of_measurement=10,
        auditability=10, predictability=10, scalability=7, expansion_potential=6,
        gaming_resistance=8, low_sales_friction=8, customer_acceptance=9,
        incentive_alignment=5,
        note="Priced from value at signing, then fixed. Everything about "
             "running it is easy and its two weaknesses are structural: it "
             "decouples from value between renewals, and PIE is paid the same "
             "whether the platform worked or not."),
    MetricScore(
        key="hybrid_platform_gmv", label="Platform fee + % GMV",
        correlation_with_value=8, ease_of_understanding=8, ease_of_measurement=9,
        auditability=9, predictability=8, scalability=9, expansion_potential=9,
        gaming_resistance=7, low_sales_friction=6, customer_acceptance=7,
        incentive_alignment=8,
        note="The platform fee sets a revenue floor and pays the cost to serve; "
             "the GMV component tracks the customer's growth on a base neither "
             "side can dispute. Nothing in it requires the customer to disclose "
             "cost, which is why it out-scores its margin-based twin — and "
             "nothing in it requires a quote to be linked to an order, which is "
             "why it is the only high-scoring structure that can actually be "
             "invoiced."),
    MetricScore(
        key="hybrid_platform_margin", label="Platform fee + % gross margin",
        correlation_with_value=9, ease_of_understanding=7, ease_of_measurement=7,
        auditability=7, predictability=8, scalability=9, expansion_potential=9,
        gaming_resistance=6, low_sales_friction=5, customer_acceptance=6,
        incentive_alignment=9,
        note="Better aligned than the GMV hybrid and harder to sell by exactly "
             "the same amount. The platform fee absorbs most of the "
             "predictability problem, leaving the disclosure problem intact."),
    MetricScore(
        key="hybrid_platform_performance", label="Low platform fee + performance fee",
        correlation_with_value=9, ease_of_understanding=6, ease_of_measurement=4,
        auditability=4, predictability=6, scalability=8, expansion_potential=8,
        gaming_resistance=4, low_sales_friction=6, customer_acceptance=8,
        incentive_alignment=10,
        note="The easiest first contract to sign and the hardest second one. "
             "Year one it is a demonstration; year two the customer argues the "
             "baseline, and PIE is negotiating its own evidence."),
    MetricScore(
        key="hybrid_commitment_usage", label="Minimum commitment + usage",
        correlation_with_value=6, ease_of_understanding=8, ease_of_measurement=9,
        auditability=8, predictability=9, scalability=8, expansion_potential=8,
        gaming_resistance=5, low_sales_friction=6, customer_acceptance=7,
        incentive_alignment=5,
        note="Predictable for PIE and safe for the customer, but the usage half "
             "carries the per-RFQ incentive defect in a smaller dose: above the "
             "commitment, adoption costs money."),
    MetricScore(
        key="enterprise_license", label="Enterprise licence, unlimited usage",
        correlation_with_value=5, ease_of_understanding=9, ease_of_measurement=10,
        auditability=10, predictability=10, scalability=5, expansion_potential=4,
        gaming_resistance=9, low_sales_friction=4, customer_acceptance=6,
        incentive_alignment=4,
        note="What a ₹1,000 Cr distributor's procurement function will "
             "eventually demand, and what PIE should resist until the account "
             "is large enough that the certainty is worth more than the "
             "expansion it forecloses. Removing the meter removes the "
             "expansion path with it."),
)

GAME_THEORY: tuple[GameTheory, ...] = (
    GameTheory(
        key="per_user",
        attribution=(Exposure.NONE, "Nothing to attribute; the meter is headcount."),
        bypass=(Exposure.MEDIUM, "Shared logins. Old, easy, and hard to police "
                                 "without surveillance nobody wants to sell."),
        margin_opacity=(Exposure.NONE, "No cost data required."),
        under_reporting=(Exposure.MEDIUM, "Seats are declared, then shared."),
        classification=(Exposure.LOW, "A user is a user."),
        alignment="Inverted. PIE earns more when the customer is less efficient, "
                  "and the product's own success shrinks the meter.",
        mitigations=("Do not use as the primary metric.",
                     "If used at all, price bands by seats rather than per seat, "
                     "so the customer is not billed for adoption.")),
    GameTheory(
        key="per_rfq",
        attribution=(Exposure.LOW, "PIE counts what it processed itself."),
        bypass=(Exposure.SEVERE, "The cheapest bypass in the table: the customer "
                                 "simply does not route an enquiry. It costs "
                                 "nothing, needs no negotiation, and is "
                                 "indistinguishable from low adoption."),
        margin_opacity=(Exposure.NONE, "No cost data required."),
        under_reporting=(Exposure.LOW, "The meter is PIE's own."),
        classification=(Exposure.LOW, "An RFQ is an RFQ."),
        alignment="Weak and slightly perverse: PIE is paid for effort, the "
                  "customer pays for trying, and neither is paid for winning.",
        mitigations=("Bundle a large block into the platform fee so ordinary "
                     "adoption is free at the margin.",
                     "Never meter the first RFQ of the month.")),
    GameTheory(
        key="per_quote",
        attribution=(Exposure.LOW, "PIE produced the quote."),
        bypass=(Exposure.HIGH, "Quote elsewhere for the lines that matter."),
        margin_opacity=(Exposure.NONE, "No cost data required."),
        under_reporting=(Exposure.LOW, "PIE's own meter."),
        classification=(Exposure.MEDIUM, "Is a revision a second quote?"),
        alignment="Better than per-RFQ, still effort-based.",
        mitigations=("Count a quote and its revisions as one billable event.",)),
    GameTheory(
        key="per_match",
        attribution=(Exposure.MEDIUM, "PIE proposed it; did the customer use it?"),
        bypass=(Exposure.SEVERE, "**The core bypass case.** PIE identifies an "
                                 "equivalent, the customer notes the part "
                                 "number and buys it through its existing "
                                 "channel. PIE sees the proposal and never sees "
                                 "the order. Without ERP sync it is invisible; "
                                 "with it, the order appears and can be linked "
                                 "— which is why the sync is a commercial asset "
                                 "and not only a feature."),
        margin_opacity=(Exposure.LOW, "Match success needs no cost."),
        under_reporting=(Exposure.HIGH, "The customer defines success."),
        classification=(Exposure.HIGH, "'We would have found that part anyway.'"),
        alignment="Good in principle, litigated in practice.",
        mitigations=("Define a match as billable on *proposal accepted in the "
                     "quote*, an event PIE records, not on outcome.",
                     "Require the ERP connection, so a subsequent purchase of a "
                     "proposed part is observable rather than asserted.")),
    GameTheory(
        key="per_order",
        attribution=(Exposure.SEVERE,
                     "There is no linking rule to turn on. A quote is never "
                     "converted into a sales order — the estimate is sent and "
                     "the order is entered separately from a customer PO — so "
                     "every attributed order is the vendor's inference about "
                     "the buyer's book, re-litigated monthly."),
        bypass=(Exposure.HIGH, "Quote in PIE, book the order outside it."),
        margin_opacity=(Exposure.NONE, "Order value is not cost."),
        under_reporting=(Exposure.MEDIUM, "Mitigated by ERP sync; PIE reads the "
                                          "sales orders itself."),
        classification=(Exposure.HIGH, "'That customer would have ordered "
                                       "anyway' is unfalsifiable per order."),
        alignment="Strong: PIE earns when the customer wins.",
        mitigations=("There is no quote lineage to attribute on: the link the "
                     "obvious mitigation assumes does not exist in the schema "
                     "or in the ERP. Building one means matching invoice lines "
                     "back to quoted lines by customer, product, quantity and "
                     "date — useful for a value report, not sound enough for "
                     "an invoice.",
                     "If a per-order fee is wanted anyway, bill on the whole "
                     "connected book's order count and drop the word 'PIE' "
                     "from the metric.")),
    GameTheory(
        key="gmv_pct",
        attribution=(Exposure.NONE,
                     "On the whole-book reading, none is required — which is "
                     "the reason to take that reading. The PIE-touched reading "
                     "is not merely disputable, it is unmeasurable: no "
                     "quote-to-order link exists to compute it from."),
        bypass=(Exposure.MEDIUM, "Route business around PIE — but at 0.3-0.5% "
                                 "the saving is smaller than the operational "
                                 "cost of running two quoting processes, which "
                                 "is what makes a low rate on a broad base more "
                                 "robust than a high rate on a narrow one."),
        margin_opacity=(Exposure.NONE, "**The decisive property.** No cost "
                                       "disclosure is required at all, which "
                                       "removes the single largest source of "
                                       "customer resistance in this table."),
        under_reporting=(Exposure.LOW, "The base is invoiced revenue that PIE "
                                       "syncs from the ERP and the customer "
                                       "files with the tax authority. Two "
                                       "independent records of one number."),
        classification=(Exposure.MEDIUM, "Contained by billing on the whole "
                                         "connected book rather than on a "
                                         "subset somebody has to classify."),
        alignment="PIE grows exactly as the customer grows. Not as tight as "
                  "margin, and it never asks a question the customer resents.",
        mitigations=("Bill on the whole connected book. This is not a "
                     "preference — the PIE-touched subset cannot be computed "
                     "without a quote-to-order link the ERP does not create. "
                     "It also removes the classification argument entirely and "
                     "lets the rate be lower for the same revenue: about 0.15% "
                     "of the book where 0.24% of touched GMV was.",
                     "Cap the variable component at a multiple of the platform "
                     "fee so a good year does not produce a renegotiation.")),
    GameTheory(
        key="gross_margin_pct",
        attribution=(Exposure.MEDIUM, "Same as GMV, on a harder base."),
        bypass=(Exposure.MEDIUM, "As GMV."),
        margin_opacity=(Exposure.SEVERE, "**The blocking problem.** A "
                                         "distributor's margin is its most "
                                         "closely held number — this codebase "
                                         "withholds it from its own "
                                         "salespeople — and a fee keyed on it "
                                         "makes the vendor a party to every "
                                         "cost decision. Expect landed cost to "
                                         "acquire freight, handling and "
                                         "financing the quarter after signing."),
        under_reporting=(Exposure.HIGH, "Cost is the customer's to state."),
        classification=(Exposure.MEDIUM, "Rebates and annual volume discounts "
                                         "land months later and change last "
                                         "quarter's margin retrospectively."),
        alignment="The tightest alignment available, at the highest cost in "
                  "trust.",
        mitigations=("Compute margin inside PIE from synced cost rows, and "
                     "agree the definition — landed cost, no allocations — in "
                     "the contract.",
                     "Prefer GMV unless the customer volunteers margin.")),
    GameTheory(
        key="incremental_margin_pct",
        attribution=(Exposure.SEVERE, "**The attribution problem in its purest "
                                      "form.** Every rupee billed rests on a "
                                      "counterfactual, and the party being "
                                      "billed is the party with the evidence."),
        bypass=(Exposure.HIGH, "Steer the profitable work away from PIE, then "
                               "point at the flat baseline."),
        margin_opacity=(Exposure.SEVERE, "Needs cost *and* a baseline."),
        under_reporting=(Exposure.SEVERE, "Both operands are contestable."),
        classification=(Exposure.SEVERE, "'That growth was the market.' In a "
                                         "year when tooling demand rises 12%, "
                                         "this is not even a bad argument."),
        alignment="Perfect on paper. The contract is a bet on a number that "
                  "cannot be settled, and unsettleable numbers end in churn.",
        mitigations=("Use it as a *renewal argument*, not a billing metric — "
                     "which is what attribution/ is already built to do.",
                     "If billed at all, cap the term at one year and convert to "
                     "a fixed fee sized on the observed result.")),
    GameTheory(
        key="savings_share",
        attribution=(Exposure.HIGH, "Was the cheaper equivalent PIE's find?"),
        bypass=(Exposure.HIGH, "Take the finding, buy it elsewhere."),
        margin_opacity=(Exposure.HIGH, "Savings are computed on cost."),
        under_reporting=(Exposure.HIGH, "The customer books the purchase."),
        classification=(Exposure.HIGH, "'We already knew that substitute.'"),
        alignment="Excellent, on a base too small to build a company on.",
        mitigations=("Bill on the proposal accepted into a quote, which PIE "
                     "records, rather than on the purchase, which it infers.",)),
    GameTheory(
        key="flat_subscription",
        attribution=(Exposure.NONE, "Nothing is attributed."),
        bypass=(Exposure.NONE, "There is nothing to bypass; the fee is owed."),
        margin_opacity=(Exposure.LOW, "Only if the *band* is set from margin — "
                                      "which is why the band should be set from "
                                      "revenue, a number the customer already "
                                      "publishes."),
        under_reporting=(Exposure.LOW, "Understating the band at signing, once, "
                                       "against a figure the ERP contradicts."),
        classification=(Exposure.NONE, "None."),
        alignment="Neutral. PIE is paid whether or not the platform worked, "
                  "which is a real weakness and the price of predictability.",
        mitigations=("Set the band from connected-book revenue, verified by the "
                     "sync, and re-band annually.",
                     "Publish the value report (attribution/) every quarter so "
                     "the neutrality is offset by evidence rather than trust.")),
    GameTheory(
        key="hybrid_platform_gmv",
        attribution=(Exposure.LOW, "Only the variable half, on a synced base."),
        bypass=(Exposure.LOW, "The platform fee is owed regardless, so bypass "
                              "saves only the marginal rate — too little to be "
                              "worth running a shadow process for."),
        margin_opacity=(Exposure.NONE, "No cost disclosure anywhere."),
        under_reporting=(Exposure.LOW, "Two independent records of revenue."),
        classification=(Exposure.LOW, "Removed by billing the whole book."),
        alignment="PIE's revenue floor covers cost to serve; its upside tracks "
                  "the customer's growth. This is the combination the objective "
                  "function in the brief actually asks for.",
        mitigations=("Cap the variable component; publish the meter monthly.",)),
    GameTheory(
        key="hybrid_platform_margin",
        attribution=(Exposure.LOW, "As above."),
        bypass=(Exposure.LOW, "As above."),
        margin_opacity=(Exposure.HIGH, "Reduced but not removed: the customer "
                                       "still has to let PIE compute margin."),
        under_reporting=(Exposure.MEDIUM, "Cost remains the customer's to state."),
        classification=(Exposure.LOW, "Whole-book billing."),
        alignment="Tighter than the GMV hybrid; sells harder by the same amount.",
        mitigations=("Offer it as the *alternative* to the GMV hybrid at a "
                     "lower headline rate, and let the customer choose which "
                     "number it would rather expose.",)),
    GameTheory(
        key="hybrid_platform_performance",
        attribution=(Exposure.HIGH, "The performance half needs a baseline."),
        bypass=(Exposure.MEDIUM, "Platform fee is owed."),
        margin_opacity=(Exposure.HIGH, "Performance is measured in margin."),
        under_reporting=(Exposure.HIGH, "As incremental margin."),
        classification=(Exposure.HIGH, "As incremental margin."),
        alignment="The strongest alignment of any signable structure.",
        mitigations=("Use for the first year only, with the performance half "
                     "capped and the baseline captured by attribution/ before "
                     "go-live.",
                     "Write the year-two conversion into the year-one contract, "
                     "so the renewal is an arithmetic step and not a "
                     "renegotiation.")),
    GameTheory(
        key="hybrid_commitment_usage",
        attribution=(Exposure.LOW, "PIE's own meter."),
        bypass=(Exposure.MEDIUM, "Suppress routing once the commitment is used."),
        margin_opacity=(Exposure.NONE, "No cost data."),
        under_reporting=(Exposure.LOW, "PIE's own meter."),
        classification=(Exposure.LOW, "Volume is volume."),
        alignment="Neutral below the commitment, mildly perverse above it.",
        mitigations=("Set the commitment above expected usage so the marginal "
                     "rate is rarely reached, which makes it a subscription "
                     "with an overage clause — and say so honestly.",)),
    GameTheory(
        key="enterprise_license",
        attribution=(Exposure.NONE, "None."),
        bypass=(Exposure.NONE, "None."),
        margin_opacity=(Exposure.NONE, "None."),
        under_reporting=(Exposure.NONE, "None."),
        classification=(Exposure.NONE, "None."),
        alignment="None, in either direction. The fee is a fact.",
        mitigations=("Attach a scope — entities, connected books, business "
                     "units — so growth by acquisition is a new negotiation "
                     "rather than free.",)),
)

_BY_KEY = {s.key: s for s in SCORES}
_GT_BY_KEY = {g.key: g for g in GAME_THEORY}


def score(key: str) -> Optional[MetricScore]:
    return _BY_KEY.get(key)


def ranking(weights: Optional[dict[str, float]] = None) -> list[dict[str, Any]]:
    """Every metric, best weighted score first.

    Ties are broken by ``key`` so two runs produce identical bytes — the
    determinism rule this repository applies to parses applies just as much to a
    ranking that is going to be pasted into a board deck.
    """
    rows = [{"key": s.key, "label": s.label,
             "weighted_score": round(s.weighted(weights), 3),
             "note": s.note,
             "scores": {c: getattr(s, c) for c in CRITERIA}}
            for s in SCORES]
    rows.sort(key=lambda r: (-r["weighted_score"], r["key"]))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def full(weights: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """The scorecard and the incentive register, joined on the metric key."""
    gt = {g.key: g.as_dict() for g in _GT_BY_KEY.values()}
    return {
        "evidence_grade": Evidence.ASSUMED.value,
        "evidence_note": ("Every score is a judgement. No closed-won cohort, "
                          "renewal or billing dispute exists to calibrate them "
                          "against; the weights are exposed so a reader can "
                          "disagree structurally rather than in prose."),
        "weights": dict(weights or DEFAULT_WEIGHTS),
        "criteria": list(CRITERIA),
        "ranking": [{**row, "game_theory": gt.get(row["key"])}
                    for row in ranking(weights)],
    }
