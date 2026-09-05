"""PIE's own commercial assumptions — one place, versioned, and labelled.

Everything in this repository so far prices the *tenant's* business: what a
distributor should charge for a cutting tool. This package answers the other
question, which had no home — **what should PIE charge the distributor**. The
two must never be confused, which is why this is a package of its own rather
than a second set of constants inside ``commercial/``: ``app/pricing.py``
recommends a price for a quote line, and nothing here touches that.

The rule this module exists to enforce is CLAUDE.md §8's, applied to a
commercial model rather than to code: **do not hard-code a commercial
assumption into business logic**. Every number a pricing conclusion depends on
is a field here, overridable from the environment, and carries a provenance
grade saying how much weight it can bear.

**Provenance is not decoration.** A model that mixes a measured conversion rate
with a guessed hourly labour rate and prints one ROI has produced a fabricated
number deterministically, which CLAUDE.md §1 forbids in the tenant's numbers
and which is no more acceptable in PIE's own. ``PROVENANCE`` below grades every
field, ``Evidence.NEEDS_VALIDATION`` is the default for anything nobody has
measured, and ``report`` renders the grades beside the figures so a reader can
see which conclusions rest on what.

Deliberately *not* registered with ``threshold_registry``: that registry
remembers the pre-image behind a stamp written onto a persisted row, and
nothing here is stamped onto anything. ``version`` exists so that two runs of
the model can be compared and so a recommendation can name the assumption set
that produced it — not because a row carries it.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal
from enum import Enum
from functools import cached_property
from typing import Optional


class Evidence(str, Enum):
    """How much weight a parameter can bear, and why.

    The four grades CLAUDE.md-style honesty requires of a model that will be
    used to set a price with a real customer. They are ordered by strength and
    the ordering is used: ``report`` refuses to call a conclusion *supported*
    when its dominant inputs are ``NEEDS_VALIDATION``.
    """

    #: Read from this platform's own rows, or from a signed contract.
    KNOWN = "KNOWN"
    #: A deliberate policy choice PIE is free to make (a target capture rate).
    #: Not a fact about the world, so it cannot be wrong — only unwise.
    ASSUMED = "ASSUMED"
    #: Derived from something measured, through arithmetic that is itself
    #: defensible (COGS per customer from token rates and observed call counts).
    ESTIMATED = "ESTIMATED"
    #: Nobody has measured this and the model is sensitive to it. Every one of
    #: these is a line item on the validation list in ``report``.
    NEEDS_VALIDATION = "NEEDS_VALIDATION"


#: Ordered weakest-last, so a set of grades can be reduced to its weakest link.
_STRENGTH = {Evidence.KNOWN: 0, Evidence.ESTIMATED: 1,
             Evidence.ASSUMED: 2, Evidence.NEEDS_VALIDATION: 3}


def weakest(grades) -> Evidence:
    """The weakest grade in a set — what a conclusion built on all of them is."""
    grades = list(grades)
    if not grades:
        return Evidence.NEEDS_VALIDATION
    return max(grades, key=lambda g: _STRENGTH[g])


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _d(name: str, default: Decimal) -> Decimal:
    """A money field. Parsed from the string as written — never through float.

    ``Decimal(str(...))`` rather than ``Decimal(float(...))`` because the whole
    reason money is ``Decimal`` in this codebase is that ``0.1`` is not 0.1 in
    binary, and routing an env value through ``float`` on the way in reproduces
    exactly the defect the type was chosen to avoid.
    """
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else Decimal(raw.strip())


def _od(name: str, default: Optional[Decimal]) -> Optional[Decimal]:
    """A money field that is allowed to stay unset.

    Unset means UNKNOWN and must not become a number. ``productivity_hourly_rate``
    is the field this exists for: this business holds no hourly rate — the
    attribution ledger says so at length in ``ValueEventType`` — so defaulting
    it to any figure would fabricate the labour-savings component of the value
    base rather than leave it honestly unmeasured.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return Decimal(raw.strip())


def _b(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return default if not raw else raw in ("1", "true", "yes", "on")


def _tuple_f(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    parsed = tuple(float(p) for p in raw.split(",") if p.strip())
    return parsed or default


@dataclass(frozen=True)
class MonetizationParameters:
    """PIE's own pricing policy and unit-economics assumptions.

    Frozen, hashable-by-content, and every field env-overridable under the
    ``PIE_MON_`` prefix. Nothing here is a fact about any one customer — that is
    ``CustomerProfile``. This is what PIE believes about itself.
    """

    currency: str = "INR"

    # ── What share of created value PIE intends to capture ──────────────────
    #: The target capture rate against *incremental* value created. 0.15 is a
    #: policy choice, not a measurement: it puts customer ROI at ~5.7x, which
    #: clears every threshold in ``roi_thresholds`` except 10x and 20x.
    value_capture_target: float = 0.15
    #: The band the model treats as defensible. Below the floor PIE is leaving
    #: money on the table for no adoption gain; above the ceiling the ROI story
    #: stops carrying a deal on its own.
    value_capture_floor: float = 0.05
    value_capture_ceiling: float = 0.25

    # ── What ROI a customer must see ────────────────────────────────────────
    #: The thresholds §12 asks to be tested. Configurable precisely because the
    #: right one is a judgement about the sales motion, not a fact.
    roi_thresholds: tuple[float, ...] = (3.0, 5.0, 10.0, 20.0)
    #: The one the model *enforces* when it sizes a price. A fee that puts a
    #: segment below this is reported as a refusal, not silently charged.
    min_customer_roi: float = 5.0
    #: Longest payback PIE will design for, in months. Beyond a budget year the
    #: buyer is being asked to believe a forecast rather than to check a bill.
    max_payback_months: float = 6.0

    # ── What counts as value ────────────────────────────────────────────────
    #: Whether hours saved are converted to rupees and added to the value base.
    #: **Default off.** The attribution ledger refuses to value time for a
    #: stated reason — this business holds no hourly rate — and a pricing model
    #: that quietly does so would inflate every ROI on this surface by the one
    #: component with no evidence behind it.
    include_productivity_in_value: bool = False
    #: Fully-loaded cost of a sales engineer hour. ``None`` is the honest state
    #: and the default; setting it is what switches the flag above on safely.
    productivity_hourly_rate: Optional[Decimal] = None
    #: Whether procurement (buy-side) savings count toward the value base. They
    #: are real and measured (``EQUIVALENT_SAVING``), but they accrue to the
    #: customer's cost line rather than to margin on a quote, so a margin-share
    #: fee never reaches them. Kept separable for exactly that reason.
    include_procurement_in_value: bool = True

    # ── PIE cost to serve (per customer, per year unless stated) ────────────
    #: Model inference. Rated per RFQ routed through interpretation rather than
    #: per customer, because that is the driver that actually moves.
    inference_cost_per_rfq: Decimal = Decimal("6.00")
    #: One-off embedding/normalisation of a catalogue, per SKU, at onboarding.
    embedding_cost_per_sku: Decimal = Decimal("0.12")
    #: Re-embedding as a catalogue churns, per SKU per year.
    embedding_refresh_cost_per_sku_year: Decimal = Decimal("0.03")
    #: Application + database + object storage, per customer per month. A
    #: multi-tenant slice, not a dedicated deployment.
    infra_cost_per_customer_month: Decimal = Decimal("3500")
    #: Storage of synced ERP rows, per million rows per year.
    storage_cost_per_million_rows_year: Decimal = Decimal("900")
    #: Third-party API calls that are not the model (ERP polling, e-invoice).
    api_cost_per_customer_year: Decimal = Decimal("18000")
    #: Human cost to serve, per customer per year: support + customer success.
    support_cost_per_customer_year: Decimal = Decimal("120000")
    customer_success_cost_per_customer_year: Decimal = Decimal("180000")
    #: One-time implementation and onboarding, amortised over the expected life.
    onboarding_cost_per_customer: Decimal = Decimal("250000")
    #: Sales and marketing cost to win one customer — the **floor**. Even the
    #: smallest deal costs this much in demos, travel and a pilot.
    cac_per_customer: Decimal = Decimal("600000")
    #: And the part that scales. A flat CAC applied to every deal size produced
    #: an LTV/CAC of 569 on the largest segment, which is not a finding about
    #: the business — it is the input saying a ₹5.5 Cr contract would be won for
    #: the same ₹6L as an ₹11L one. It would not: it needs a named account team,
    #: a procurement cycle and a security review. Effective CAC is therefore
    #: ``max(cac_per_customer, share x first-year ACV)``, and 1.0 is the middle
    #: of the range B2B software actually runs at.
    cac_share_of_first_year_acv: float = 1.0

    # ── Contract terms: what turns a fee into an agreement ──────────────────
    #: One-time implementation, as a share of the first-year fee. Charged, not
    #: absorbed: ``onboarding_cost_per_customer`` was modelled as a cost and
    #: amortised into COGS while nothing ever billed for it, which is the one
    #: place this model departed from the enterprise-HRMS shape it otherwise
    #: follows — Workday-class implementation is a separate line, often 1-2x
    #: first-year subscription. It is also the largest available lever on a
    #: 12-20 month CAC payback, because it lands as cash at signature.
    implementation_fee_share_of_annual: float = 0.25
    #: The floor under it: onboarding must at least be recovered with a margin,
    #: whatever the band. A small customer's catalogue does not get cheaper to
    #: embed because its turnover is low.
    implementation_cost_markup: float = 1.5
    #: And the ceiling. At the top bands a quarter of the annual fee is a very
    #: large number for work whose real driver is SKU count and connector
    #: complexity, not turnover.
    implementation_fee_cap_multiple: float = 0.6

    #: Contract length in years, and what a longer commitment buys the customer.
    #: Multi-year matters more here than in most models: the flat banded fee has
    #: no meter, so a 3-year term is the only structural defence against the
    #: renegotiation a flat fee otherwise invites annually.
    default_term_years: int = 1
    max_term_years: int = 3
    multi_year_discount_per_extra_year: float = 0.05

    #: Paid annually in advance by default. The discount is the price of that
    #: cash, and it is deliberately generous for an early-stage company: a year
    #: collected at signature is the difference between funding the next
    #: customer's acquisition and borrowing to.
    upfront_payment_discount: float = 0.05
    quarterly_payment_premium: float = 0.03

    #: Applied at each renewal *within* a band. Without it the price is flat in
    #: nominal terms for the 3.6 years between band crossings, which is a real
    #: -terms price cut every year that nobody decided to give.
    annual_escalation: float = 0.05

    #: The most a deal may be discounted, and the rule that overrides it. Below
    #: the cost floor a discount is not a concession, it is a subsidy — the same
    #: shape as the tenant-side margin floor this platform already enforces on a
    #: salesperson, one level up and pointed at PIE's own desk.
    max_discount_share: float = 0.30

    #: How far a band may fall at one renewal when a customer's turnover drops.
    #: Bands re-rate up automatically; nothing said what happens when a customer
    #: has a bad year, and the answer cannot be "nothing" — a fee that only ever
    #: rises is a fee a shrinking customer cancels rather than renegotiates.
    downgrade_bands_per_renewal: int = 1

    #: A group of legal entities is banded on combined turnover, then charged an
    #: uplift for each additional connected company. Summing alone hands the
    #: group a ~26% implicit discount (the bands are steep at the bottom), while
    #: the cost to serve genuinely rises with each entity — a second sync, a
    #: second catalogue, a second support surface. The uplift is what reconciles
    #: those two facts.
    group_entity_uplift: float = 0.15

    # ── Retention ───────────────────────────────────────────────────────────
    #: Gross logo retention per year. Drives expected life and therefore LTV.
    annual_gross_retention: float = 0.85
    #: Net revenue retention, including expansion within retained accounts.
    net_revenue_retention: float = 1.12
    #: Years the model amortises onboarding over and computes LTV across. Not
    #: derived from retention because 1/(1-r) runs to eight years, which is a
    #: longer claim than a two-year-old product can make.
    ltv_horizon_years: int = 5

    # ── Elasticity ──────────────────────────────────────────────────────────
    #: Reference annual price at which ``reference_win_rate`` was observed (or,
    #: today, assumed). The curve is anchored here rather than at zero.
    elasticity_reference_price: Decimal = Decimal("1200000")
    reference_win_rate: float = 0.30
    #: Constant-elasticity exponents to sweep. -0.6 is inelastic (the product is
    #: differentiated), -2.5 is elastic (it reads as commodity software).
    elasticity_scenarios: tuple[float, ...] = (-0.6, -1.2, -2.5)
    #: Sales-cycle lengthening per doubling of price, in days. A price rise is
    #: not only a win-rate event; it moves the deal up a signature ladder.
    sales_cycle_days_at_reference: float = 75.0
    sales_cycle_days_per_price_doubling: float = 45.0

    # ── Hypothesis under test ───────────────────────────────────────────────
    #: The rates §5 and §6 ask to be swept, as ratios.
    margin_rate_ladder: tuple[float, ...] = (
        0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.05)
    gmv_rate_ladder: tuple[float, ...] = (
        0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01)
    #: The capture rates §7 asks a subscription to be derived from.
    capture_ladder: tuple[float, ...] = (0.01, 0.02, 0.05, 0.10, 0.15, 0.20)
    #: The hypothesis itself, named, so nothing in the codebase has to spell
    #: 0.001 as a literal to test it.
    hypothesis_margin_rate: float = 0.001

    # ── Presentation ────────────────────────────────────────────────────────
    #: Annual fees are rounded to this increment before being quoted. Same
    #: reasoning as ``pricing.recommend_price``: an unrounded ₹18,73,412 reads
    #: as a formula output, and a buyer negotiates against a formula.
    fee_rounding_increment: Decimal = Decimal("50000")

    _fingerprint: str = field(default="", repr=False, compare=False)

    @classmethod
    def from_env(cls) -> "MonetizationParameters":
        return cls(
            currency=os.environ.get("DEFAULT_CURRENCY", "INR").strip().upper() or "INR",
            value_capture_target=_f("PIE_MON_VALUE_CAPTURE_TARGET",
                                    _default("value_capture_target")),
            value_capture_floor=_f("PIE_MON_VALUE_CAPTURE_FLOOR",
                                   _default("value_capture_floor")),
            value_capture_ceiling=_f("PIE_MON_VALUE_CAPTURE_CEILING",
                                     _default("value_capture_ceiling")),
            roi_thresholds=_tuple_f("PIE_MON_ROI_THRESHOLDS",
                                    _default("roi_thresholds")),
            min_customer_roi=_f("PIE_MON_MIN_CUSTOMER_ROI",
                                _default("min_customer_roi")),
            max_payback_months=_f("PIE_MON_MAX_PAYBACK_MONTHS",
                                  _default("max_payback_months")),
            include_productivity_in_value=_b(
                "PIE_MON_INCLUDE_PRODUCTIVITY",
                _default("include_productivity_in_value")),
            productivity_hourly_rate=_od("PIE_MON_PRODUCTIVITY_HOURLY_RATE",
                                         _default("productivity_hourly_rate")),
            include_procurement_in_value=_b(
                "PIE_MON_INCLUDE_PROCUREMENT",
                _default("include_procurement_in_value")),
            inference_cost_per_rfq=_d("PIE_MON_INFERENCE_COST_PER_RFQ",
                                      _default("inference_cost_per_rfq")),
            embedding_cost_per_sku=_d("PIE_MON_EMBEDDING_COST_PER_SKU",
                                      _default("embedding_cost_per_sku")),
            embedding_refresh_cost_per_sku_year=_d(
                "PIE_MON_EMBEDDING_REFRESH_PER_SKU_YEAR",
                _default("embedding_refresh_cost_per_sku_year")),
            infra_cost_per_customer_month=_d(
                "PIE_MON_INFRA_PER_CUSTOMER_MONTH",
                _default("infra_cost_per_customer_month")),
            storage_cost_per_million_rows_year=_d(
                "PIE_MON_STORAGE_PER_MILLION_ROWS_YEAR",
                _default("storage_cost_per_million_rows_year")),
            api_cost_per_customer_year=_d("PIE_MON_API_PER_CUSTOMER_YEAR",
                                          _default("api_cost_per_customer_year")),
            support_cost_per_customer_year=_d(
                "PIE_MON_SUPPORT_PER_CUSTOMER_YEAR",
                _default("support_cost_per_customer_year")),
            customer_success_cost_per_customer_year=_d(
                "PIE_MON_CS_PER_CUSTOMER_YEAR",
                _default("customer_success_cost_per_customer_year")),
            onboarding_cost_per_customer=_d("PIE_MON_ONBOARDING_PER_CUSTOMER",
                                            _default("onboarding_cost_per_customer")),
            cac_per_customer=_d("PIE_MON_CAC_PER_CUSTOMER",
                                _default("cac_per_customer")),
            cac_share_of_first_year_acv=_f(
                "PIE_MON_CAC_SHARE_OF_ACV",
                _default("cac_share_of_first_year_acv")),
            implementation_fee_share_of_annual=_f(
                "PIE_MON_IMPLEMENTATION_SHARE",
                _default("implementation_fee_share_of_annual")),
            implementation_cost_markup=_f("PIE_MON_IMPLEMENTATION_MARKUP",
                                          _default("implementation_cost_markup")),
            implementation_fee_cap_multiple=_f(
                "PIE_MON_IMPLEMENTATION_CAP",
                _default("implementation_fee_cap_multiple")),
            default_term_years=_i("PIE_MON_DEFAULT_TERM_YEARS",
                                  _default("default_term_years")),
            max_term_years=_i("PIE_MON_MAX_TERM_YEARS",
                              _default("max_term_years")),
            multi_year_discount_per_extra_year=_f(
                "PIE_MON_MULTI_YEAR_DISCOUNT",
                _default("multi_year_discount_per_extra_year")),
            upfront_payment_discount=_f("PIE_MON_UPFRONT_DISCOUNT",
                                        _default("upfront_payment_discount")),
            quarterly_payment_premium=_f("PIE_MON_QUARTERLY_PREMIUM",
                                         _default("quarterly_payment_premium")),
            annual_escalation=_f("PIE_MON_ANNUAL_ESCALATION",
                                 _default("annual_escalation")),
            max_discount_share=_f("PIE_MON_MAX_DISCOUNT",
                                  _default("max_discount_share")),
            downgrade_bands_per_renewal=_i(
                "PIE_MON_DOWNGRADE_BANDS", _default("downgrade_bands_per_renewal")),
            group_entity_uplift=_f("PIE_MON_GROUP_ENTITY_UPLIFT",
                                   _default("group_entity_uplift")),
            annual_gross_retention=_f("PIE_MON_GROSS_RETENTION",
                                      _default("annual_gross_retention")),
            net_revenue_retention=_f("PIE_MON_NET_REVENUE_RETENTION",
                                     _default("net_revenue_retention")),
            ltv_horizon_years=_i("PIE_MON_LTV_HORIZON_YEARS",
                                 _default("ltv_horizon_years")),
            elasticity_reference_price=_d("PIE_MON_ELASTICITY_REF_PRICE",
                                          _default("elasticity_reference_price")),
            reference_win_rate=_f("PIE_MON_REFERENCE_WIN_RATE",
                                  _default("reference_win_rate")),
            elasticity_scenarios=_tuple_f("PIE_MON_ELASTICITY_SCENARIOS",
                                          _default("elasticity_scenarios")),
            sales_cycle_days_at_reference=_f(
                "PIE_MON_SALES_CYCLE_DAYS", _default("sales_cycle_days_at_reference")),
            sales_cycle_days_per_price_doubling=_f(
                "PIE_MON_SALES_CYCLE_DAYS_PER_DOUBLING",
                _default("sales_cycle_days_per_price_doubling")),
            margin_rate_ladder=_tuple_f("PIE_MON_MARGIN_RATE_LADDER",
                                        _default("margin_rate_ladder")),
            gmv_rate_ladder=_tuple_f("PIE_MON_GMV_RATE_LADDER",
                                     _default("gmv_rate_ladder")),
            capture_ladder=_tuple_f("PIE_MON_CAPTURE_LADDER",
                                    _default("capture_ladder")),
            hypothesis_margin_rate=_f("PIE_MON_HYPOTHESIS_MARGIN_RATE",
                                      _default("hypothesis_margin_rate")),
            fee_rounding_increment=_d("PIE_MON_FEE_ROUNDING_INCREMENT",
                                      _default("fee_rounding_increment")),
        )

    @cached_property
    def serialized(self) -> str:
        payload = {k: (str(v) if isinstance(v, Decimal) else v)
                   for k, v in asdict(self).items() if k != "_fingerprint"}
        return json.dumps(payload, sort_keys=True, default=str)

    @cached_property
    def version(self) -> str:
        """Short content hash of the assumption set. ``mon_`` prefixed.

        Distinct from ``ci_`` (commercial thresholds) and ``th_`` (signal
        thresholds) on purpose — CLAUDE.md §1 warns against reading one stamp
        as another, and a third family of numbers needs a third prefix rather
        than a shared one that invites exactly that.
        """
        return "mon_" + hashlib.sha256(self.serialized.encode()).hexdigest()[:12]

    def round_fee(self, amount: Decimal) -> Decimal:
        """Round an annual fee to the quoting increment. 0 means do not round."""
        step = self.fee_rounding_increment
        if step <= 0:
            return amount
        return (amount / step).quantize(Decimal("1"), rounding="ROUND_HALF_UP") * step


def _default(name: str):
    return next(f.default for f in fields(MonetizationParameters) if f.name == name)


#: Every field, graded. A field absent from this map is a defect the test suite
#: fails on: an ungraded assumption is one a reader will take for a measurement.
PROVENANCE: dict[str, tuple[Evidence, str]] = {
    "currency": (Evidence.KNOWN, "The tenant currency this platform runs in."),
    "value_capture_target": (
        Evidence.ASSUMED,
        "PIE's choice. Benchmarked against nothing in this repo; the model's "
        "job is to show what each choice implies, not to justify one."),
    "value_capture_floor": (Evidence.ASSUMED, "Policy band, low end."),
    "value_capture_ceiling": (Evidence.ASSUMED, "Policy band, high end."),
    "roi_thresholds": (Evidence.ASSUMED, "The ladder to be tested, per brief."),
    "min_customer_roi": (
        Evidence.ASSUMED,
        "The threshold PIE enforces. 5x is the usual B2B software bar; nothing "
        "here measures it."),
    "max_payback_months": (Evidence.ASSUMED, "Sales-motion judgement."),
    "include_productivity_in_value": (
        Evidence.ASSUMED, "Off by default; see the field."),
    "productivity_hourly_rate": (
        Evidence.NEEDS_VALIDATION,
        "UNKNOWN until a customer states a fully-loaded engineer cost. The "
        "attribution ledger refuses to value time for this reason."),
    "include_procurement_in_value": (Evidence.ASSUMED, "Policy."),
    "inference_cost_per_rfq": (
        Evidence.ESTIMATED,
        "Derived from AI_COST_PER_MTOK_INPUT/OUTPUT and observed interpretation "
        "call shapes. Re-derive against ai/telemetry rather than trusting it."),
    "embedding_cost_per_sku": (Evidence.ESTIMATED, "Catalogue embedding, one-off."),
    "embedding_refresh_cost_per_sku_year": (
        Evidence.ESTIMATED, "Catalogue churn re-embedding."),
    "infra_cost_per_customer_month": (
        Evidence.ESTIMATED, "Multi-tenant slice of the hosting bill."),
    "storage_cost_per_million_rows_year": (Evidence.ESTIMATED, "Object + row storage."),
    "api_cost_per_customer_year": (Evidence.ESTIMATED, "ERP polling and e-invoice."),
    "support_cost_per_customer_year": (
        Evidence.NEEDS_VALIDATION,
        "No support-hours telemetry exists. This is the single largest COGS "
        "line and the least evidenced."),
    "customer_success_cost_per_customer_year": (
        Evidence.NEEDS_VALIDATION, "Same: no time tracking behind it."),
    "onboarding_cost_per_customer": (
        Evidence.NEEDS_VALIDATION,
        "One connector build plus catalogue work. Varies by ERP by more than "
        "this single figure admits."),
    "cac_per_customer": (
        Evidence.NEEDS_VALIDATION,
        "No closed-won cohort exists yet. Everything downstream of LTV/CAC "
        "inherits this uncertainty."),
    "cac_share_of_first_year_acv": (
        Evidence.ASSUMED,
        "A benchmark rather than a measurement: B2B software typically spends "
        "about one year of contract value to win one. Nothing in this business "
        "has tested it."),
    "implementation_fee_share_of_annual": (
        Evidence.ASSUMED,
        "PIE's choice. 25% of first-year is at the low end of what enterprise "
        "software charges for implementation; nothing here measures what the "
        "market will bear."),
    "implementation_cost_markup": (
        Evidence.ASSUMED, "Recovery floor. Policy, not a measurement."),
    "implementation_fee_cap_multiple": (
        Evidence.ASSUMED, "Ceiling at the top bands, where a share of turnover "
                          "stops tracking the work."),
    "default_term_years": (Evidence.ASSUMED, "Opening position, not a finding."),
    "max_term_years": (Evidence.ASSUMED, "Longest term PIE will write."),
    "multi_year_discount_per_extra_year": (
        Evidence.NEEDS_VALIDATION,
        "What a customer will actually pay for a longer commitment is "
        "unmeasured — no multi-year deal has been signed or refused."),
    "upfront_payment_discount": (
        Evidence.ASSUMED,
        "The price PIE puts on cash at signature. A judgement about PIE's own "
        "funding position, not about the customer."),
    "quarterly_payment_premium": (Evidence.ASSUMED, "The other side of it."),
    "annual_escalation": (
        Evidence.NEEDS_VALIDATION,
        "Whether a customer accepts a 5% uplift between band crossings without "
        "reopening the contract is exactly the sort of thing only a renewal "
        "tells you, and no renewal has happened."),
    "max_discount_share": (
        Evidence.ASSUMED, "Governance, set by PIE. The cost floor overrides it."),
    "downgrade_bands_per_renewal": (
        Evidence.ASSUMED,
        "A policy choice about shrinking customers. Untested — no customer has "
        "yet had a bad year on this platform."),
    "group_entity_uplift": (
        Evidence.NEEDS_VALIDATION,
        "Sized to offset the ~26% implicit discount that summing entity "
        "turnover into one band creates, against a cost to serve that rises "
        "per connection. Both halves are modelled, neither is measured."),
    "annual_gross_retention": (
        Evidence.NEEDS_VALIDATION, "No renewal has happened yet."),
    "net_revenue_retention": (
        Evidence.NEEDS_VALIDATION, "No expansion cohort exists yet."),
    "ltv_horizon_years": (Evidence.ASSUMED, "Deliberately shorter than 1/churn."),
    "elasticity_reference_price": (Evidence.ASSUMED, "Anchor for the curve."),
    "reference_win_rate": (
        Evidence.NEEDS_VALIDATION,
        "The elasticity curve's anchor. Until an experiment from §16 runs, "
        "every revenue-maximising price is a shape, not a number."),
    "elasticity_scenarios": (Evidence.ASSUMED, "A sweep, not a belief."),
    "sales_cycle_days_at_reference": (Evidence.NEEDS_VALIDATION, "No closed deals."),
    "sales_cycle_days_per_price_doubling": (
        Evidence.NEEDS_VALIDATION, "Signature-ladder effect, unmeasured."),
    "margin_rate_ladder": (Evidence.ASSUMED, "The sweep the brief specifies."),
    "gmv_rate_ladder": (Evidence.ASSUMED, "The sweep the brief specifies."),
    "capture_ladder": (Evidence.ASSUMED, "The sweep the brief specifies."),
    "hypothesis_margin_rate": (
        Evidence.ASSUMED, "The hypothesis under test: 0.1% of margin."),
    "fee_rounding_increment": (Evidence.ASSUMED, "Quoting convention."),
    "_fingerprint": (Evidence.KNOWN, "Internal."),
}


def load_parameters() -> MonetizationParameters:
    """The assumption set for this process.

    Not cached: an env override applied in a test or a what-if run must take
    effect on the next call, exactly as ``pricing.__getattr__`` resolves the
    commercial thresholds on access for the same reason.
    """
    return MonetizationParameters.from_env()


def validation_list(params: Optional[MonetizationParameters] = None) -> list[dict]:
    """Every parameter the recommendation rests on that nobody has measured.

    This is the deliverable §20 asks for under "needs validation", generated
    from the grades rather than written out by hand — a hand-written list is
    one that stops matching the model the first time a field is added.
    """
    params = params or load_parameters()
    values = asdict(params)
    return [
        {"parameter": name, "value": str(values.get(name)), "grade": grade.value,
         "why": note}
        for name, (grade, note) in sorted(PROVENANCE.items())
        if grade is Evidence.NEEDS_VALIDATION
    ]
