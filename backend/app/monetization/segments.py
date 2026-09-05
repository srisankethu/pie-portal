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
from typing import Optional

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


#: Annual turnover bands, in rupees. The published price list's rows.
#:
#: **Width is the design decision, not the edges.** Band crossings are the only
#: expansion a flat fee has, and `report.expansion_levers` puts numbers on the
#: trade: at 12% customer growth, 2x bands re-rate an account every 6.1 years
#: and 1.6x bands every 3.6. These are ~1.6x for that reason — and narrower
#: bands are better on *both* sides of the table, which is unusual enough to say
#: out loud. PIE gets smoother, earlier revenue; the customer gets a 60% step at
#: renewal instead of a doubling, which is a far easier conversation.
#:
#: Ending open is deliberate: above the top band the deal is negotiated, and a
#: list price on a conversation that will not have one is theatre.
TURNOVER_BANDS: tuple[tuple[str, Decimal, Optional[Decimal]], ...] = (
    ("Up to ₹25 Cr",       Decimal("0"),            Decimal("250000000")),
    ("₹25 – 50 Cr",        Decimal("250000000"),    Decimal("500000000")),
    ("₹50 – 90 Cr",        Decimal("500000000"),    Decimal("900000000")),
    ("₹90 – 150 Cr",       Decimal("900000000"),    Decimal("1500000000")),
    ("₹150 – 250 Cr",      Decimal("1500000000"),   Decimal("2500000000")),
    ("₹250 – 400 Cr",      Decimal("2500000000"),   Decimal("4000000000")),
    ("₹400 – 650 Cr",      Decimal("4000000000"),   Decimal("6500000000")),
    ("₹650 – 1,000 Cr",    Decimal("6500000000"),   Decimal("10000000000")),
    ("₹1,000 – 1,600 Cr",  Decimal("10000000000"),  Decimal("16000000000")),
    ("Above ₹1,600 Cr",    Decimal("16000000000"),  None),
)


def profile_for_turnover(turnover: Decimal) -> CustomerProfile:
    """The archetype nearest this turnover, scaled to hit it exactly.

    Nearest rather than smallest: a ₹900 Cr distributor priced against the small
    profile's conversion rates and headcount would be given the small profile's
    economics at ten times the size, which is a worse error than not scaling at
    all. The funnel shape comes from the closest band; only the order value
    moves.
    """
    ordered = [ARCHETYPES[k] for k in ("small", "mid", "large")]
    nearest = min(ordered, key=lambda p: abs(p.annual_revenue - turnover))
    return nearest.with_annual_revenue(turnover)
