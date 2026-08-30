"""Reference customers and reference impacts — the shapes the model is run on.

Two axes, and separating them is the point. ``ARCHETYPES`` are businesses of
different sizes; ``IMPACTS`` are how much the platform moves one. Crossing the
two is what produces a defensible *range* instead of a single number that hides
which half of it is the guess.

**The size bands are stated in gross profit**, per the brief — ₹1–10 Cr,
₹10–100 Cr, ₹100–1,000 Cr+ — which is not the same axis as "a ₹100 Cr
distributor", a phrase that almost always means revenue. Both readings are
built here (``mid`` and ``rev_100cr`` are different businesses) so a
recommendation can name which one it is answering, rather than resolving the
ambiguity silently in whichever direction flatters the price.

Every number in this module is ``NEEDS_VALIDATION``. They are shaped from what
industrial cutting-tool distribution looks like — high enquiry frequency,
five-figure order values, low-twenties gross margin — and not one of them is
measured. ``evidence.py`` is the module that replaces them with real rows where
a connected organization has any.
"""
from __future__ import annotations

from decimal import Decimal

from .customer import CustomerProfile, PieImpact

#: The three size bands the brief names, plus two revenue-anchored references.
#: Ordered small to large; ``report`` iterates in this order.
ARCHETYPES: dict[str, CustomerProfile] = {
    "small": CustomerProfile(
        name="Small distributor (₹5 Cr gross profit)",
        annual_rfqs=10_800, pie_rfq_share=0.60,
        quote_conversion=0.55, order_conversion=0.28,
        average_order_value=Decimal("120000"), gross_margin=0.25,
        sales_engineers=4, cost_per_employee_year=Decimal("900000"),
        rfq_processing_minutes=14.0, quotation_minutes=22.0,
        sku_count=12_000, erp_rows_millions=0.4),
    "mid": CustomerProfile(
        name="Mid-market distributor (₹35 Cr gross profit)",
        annual_rfqs=63_400, pie_rfq_share=0.60,
        quote_conversion=0.50, order_conversion=0.30,
        average_order_value=Decimal("160000"), gross_margin=0.23,
        sales_engineers=18, cost_per_employee_year=Decimal("1100000"),
        rfq_processing_minutes=13.0, quotation_minutes=20.0,
        sku_count=45_000, erp_rows_millions=2.5),
    "large": CustomerProfile(
        name="Large distributor (₹250 Cr gross profit)",
        annual_rfqs=367_000, pie_rfq_share=0.55,
        quote_conversion=0.45, order_conversion=0.32,
        average_order_value=Decimal("225000"), gross_margin=0.21,
        sales_engineers=70, cost_per_employee_year=Decimal("1300000"),
        rfq_processing_minutes=12.0, quotation_minutes=18.0,
        sku_count=140_000, erp_rows_millions=12.0),
    # Revenue-anchored, for the two questions phrased that way. Same funnel
    # shape as the band above them, scaled to hit the stated turnover.
    "rev_100cr": CustomerProfile(
        name="₹100 Cr revenue distributor",
        annual_rfqs=42_000, pie_rfq_share=0.60,
        quote_conversion=0.50, order_conversion=0.30,
        average_order_value=Decimal("158730"), gross_margin=0.23,
        sales_engineers=12, cost_per_employee_year=Decimal("1100000"),
        rfq_processing_minutes=13.0, quotation_minutes=20.0,
        sku_count=35_000, erp_rows_millions=1.8),
    "rev_1000cr": CustomerProfile(
        name="₹1,000 Cr revenue distributor",
        annual_rfqs=310_000, pie_rfq_share=0.55,
        quote_conversion=0.45, order_conversion=0.32,
        average_order_value=Decimal("224215"), gross_margin=0.21,
        sales_engineers=60, cost_per_employee_year=Decimal("1300000"),
        rfq_processing_minutes=12.0, quotation_minutes=18.0,
        sku_count=120_000, erp_rows_millions=10.0),
}

#: How much PIE moves a business. Three sets, swept rather than chosen, because
#: the honest state of knowledge is a range: no cohort has renewed, so nothing
#: here is measured and the spread between conservative and aggressive is a
#: 4-5x spread in the fee any value-based metric produces.
IMPACTS: dict[str, PieImpact] = {
    "conservative": PieImpact(
        quote_conversion_uplift_pp=0.02, order_conversion_uplift_pp=0.010,
        aov_uplift=0.010, gross_margin_uplift_pp=0.003,
        procurement_saving_rate=0.002,
        minutes_saved_per_rfq=4.0, minutes_saved_per_quote=8.0,
        response_time_improvement=0.35,
        substitution_opportunity_rate=0.10, substitution_success_rate=0.25),
    "base": PieImpact(
        quote_conversion_uplift_pp=0.05, order_conversion_uplift_pp=0.020,
        aov_uplift=0.030, gross_margin_uplift_pp=0.006,
        procurement_saving_rate=0.004,
        minutes_saved_per_rfq=6.0, minutes_saved_per_quote=12.0,
        response_time_improvement=0.60,
        substitution_opportunity_rate=0.18, substitution_success_rate=0.35),
    "aggressive": PieImpact(
        quote_conversion_uplift_pp=0.10, order_conversion_uplift_pp=0.040,
        aov_uplift=0.050, gross_margin_uplift_pp=0.010,
        procurement_saving_rate=0.007,
        minutes_saved_per_rfq=10.0, minutes_saved_per_quote=18.0,
        response_time_improvement=0.75,
        substitution_opportunity_rate=0.25, substitution_success_rate=0.45),
}

#: Which archetype answers which question in the final recommendation. Written
#: down so the answer to "what should a ₹100 Cr distributor pay" cites the
#: profile it was computed from instead of a number in prose.
ANSWERS_TO: dict[str, str] = {
    "small": "What should a small distributor pay?",
    "mid": "The ₹10-100 Cr gross-profit band.",
    "large": "The ₹100 Cr+ gross-profit band.",
    "rev_100cr": "What should a ₹100 Cr-scale (revenue) distributor pay?",
    "rev_1000cr": "What should a ₹1,000 Cr-scale (revenue) distributor pay?",
}
