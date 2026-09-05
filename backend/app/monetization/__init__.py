"""How PIE charges for what PIE creates — the pricing model, as code.

A deterministic package, and one of the layers CLAUDE.md §1 names: it must
never import ``ai/``, and ``ai/`` must never import it. A model that priced the
platform could be asked to justify a price, and a justification produced by a
model is not an audit trail.

Read it in this order:

    config       PIE's own assumptions, versioned, every one graded KNOWN /
                 ASSUMED / ESTIMATED / NEEDS_VALIDATION.
    customer     one distributor's economics: baseline, with PIE, and the
                 decomposition of the gap. Incremental revenue is never value.
    segments     the reference distributors and the three impact sets.
    strategies   every way PIE could charge, behind one interface.
    evaluate     what a fee means — ROI, payback, capture, retained value.
    unitecon     what a customer costs PIE to serve, and what it is worth.
    elasticity   price against adoption, on two named curves.
    plan         five years, three scenarios, ARR at 10 to 1,000 customers.
    scorecard    metrics scored and ranked, plus the incentive each creates.
    experiments  what can actually be run, and the sample sizes that say why
                 most of it cannot be run as a randomised test.
    evidence     the one module that reads rows, replacing assumptions with
                 this platform's own measurements where there are any.
    report       the orchestrator, and the single definition of "recommended".

The distinction that matters most on first reading: ``app/pricing.py`` prices a
*quote line* for a distributor's customer. Nothing in this package touches that,
and nothing there knows this exists.
"""
from .config import (Evidence, MonetizationParameters, PROVENANCE,
                     load_parameters, validation_list, weakest)
from .customer import (CustomerProfile, Funnel, PieImpact, Waterfall,
                       build_waterfall, money)
from .evaluate import Evaluation, compare, evaluate, fee_for_roi
from .report import (all_strategies, full_report, hybrid_structures,
                     margin_hypothesis, recommend, segment_report,
                     strategic_test, subscription_ladder, transaction_ladder)
from .segments import ARCHETYPES, IMPACTS
from .strategies import (EnterpriseLicensePricing, Fee, FeeBase, HybridPricing,
                         IncrementalMarginPricing, MarginSharePricing,
                         MatchPricing, OrderPricing, PricingStrategy,
                         QuotePricing, RFQPricing, SavingsSharePricing,
                         SubscriptionPricing, TransactionPricing,
                         ValueDerivedSubscription, base_amount)
from .unitecon import CostToServe, UnitEconomics, cost_to_serve, floor_price, \
    unit_economics

__all__ = [
    "ARCHETYPES", "CostToServe", "CustomerProfile", "EnterpriseLicensePricing",
    "Evaluation", "Evidence", "Fee", "FeeBase", "Funnel", "HybridPricing",
    "IMPACTS", "IncrementalMarginPricing", "MarginSharePricing",
    "MatchPricing", "MonetizationParameters", "OrderPricing", "PROVENANCE",
    "PieImpact", "PricingStrategy", "QuotePricing", "RFQPricing",
    "SavingsSharePricing", "SubscriptionPricing", "TransactionPricing",
    "UnitEconomics", "ValueDerivedSubscription", "Waterfall", "all_strategies",
    "base_amount", "build_waterfall", "compare", "cost_to_serve", "evaluate",
    "fee_for_roi", "floor_price", "full_report", "hybrid_structures",
    "load_parameters", "margin_hypothesis", "money", "recommend",
    "segment_report", "strategic_test", "subscription_ladder",
    "transaction_ladder", "unit_economics", "validation_list", "weakest",
]
