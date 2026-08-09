"""Thresholds for Customer × Item commercial intelligence.

One dataclass, env overrides, a content-hashed ``version`` stamped onto every
metric row and every signal — the same pattern as ``signals/config.py``, for the
same reason: any number the platform reports must be reproducible against the
exact thresholds that produced it.

Nothing here is a magic number scattered through a detector or a component.
Changing a threshold is a config change, not an analytical rewrite.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, fields
from typing import Optional


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _default(name: str):
    return next(f.default for f in fields(CommercialThresholds) if f.name == name)


def _edges() -> tuple[int, ...]:
    """``CI_QUANTITY_BAND_EDGES=1,10,50,200`` — ascending, positive, deduped."""
    raw = os.environ.get("CI_QUANTITY_BAND_EDGES")
    if not raw:
        return _default("quantity_band_edges")
    edges = sorted({int(p) for p in raw.split(",") if p.strip()})
    return tuple(e for e in edges if e > 0) or _default("quantity_band_edges")


def _families() -> tuple[tuple[str, float], ...]:
    """``CI_TARGET_MARGIN_BY_FAMILY=milling_insert:0.30,reamer:0.27``."""
    raw = os.environ.get("CI_TARGET_MARGIN_BY_FAMILY")
    if not raw:
        return _default("target_margin_by_family")
    out = []
    for part in raw.split(","):
        if ":" in part:
            name, _, value = part.partition(":")
            out.append((name.strip(), float(value)))
    return tuple(sorted(out)) or _default("target_margin_by_family")


@dataclass(frozen=True)
class CommercialThresholds:
    # ── currency ─────────────────────────────────────────────────────────────
    # The unit every money threshold below is denominated in, and the unit the
    # numbers computed against them are reported in. It sits inside the version
    # hash on purpose: a 10,000 floor in rupees and a 10,000 floor in dollars
    # are different policies, and without this they would stamp the same
    # version onto rows that are not comparable. ``policy.load_for_org``
    # replaces this with the organization's own currency.
    currency: str = "INR"
    # The zone every period boundary and "as of" date is measured in. Inside
    # the version hash with everything else: the same numeric policy applied in
    # two zones puts a month boundary in two different places, so rows stamped
    # with one are not comparable to rows stamped with the other.
    timezone: str = "Asia/Kolkata"

    # ── periods ──────────────────────────────────────────────────────────────
    # "Recent" is the window a current position is read from; "previous" is the
    # equal-length window immediately before it, so the two are comparable.
    recent_days: int = 90
    previous_days: int = 90
    # How far back a relationship's own "historical" baseline reaches. Longer
    # than the comparison window on purpose: the baseline should be what the
    # relationship normally earned, not merely last quarter.
    historical_lookback_days: int = 730

    # ── what counts as a real move ───────────────────────────────────────────
    # Percentage POINTS of margin. 3 pp is roughly where a distributor's margin
    # move stops being noise from mix and freight.
    min_margin_deterioration_pp: float = 0.03
    # The same question at a coarser setting: how far margin must fall before a
    # deterioration is worth *a person's queue*, as opposed to worth showing on a
    # screen. Deliberately a separate number from the one above — a distributor
    # wants the screens sensitive and the queue quiet, and one knob cannot do
    # both — but it belongs to the same object so there is one place an owner
    # edits margin sensitivity and one version stamped on what it produced.
    #
    # It used to be `SignalThresholds.margin_drop_points`, settable only by
    # redeploying with SIG_MARGIN_DROP_POINTS. So an owner who raised the
    # "Erosion threshold" in Settings to quieten the noise watched the commercial
    # screens go silent while the decision queue carried on exactly as before.
    # Default 0.05 rather than 0.03: it is the value that was in force, and the
    # fold is not the place to change what the platform detects.
    queue_margin_drop_pp: float = 0.05
    meaningful_cost_increase_pct: float = 0.05
    meaningful_price_change_pct: float = 0.02
    meaningful_volume_change_pct: float = 0.15

    # ── economic materiality ─────────────────────────────────────────────────
    # Rupees. A gap below this is real but not worth anyone's afternoon, and
    # prioritising by percentage instead of dsize is how teams end up working
    # trivial accounts first.
    min_material_gap: float = 10_000.0

    # ── evidence floors ──────────────────────────────────────────────────────
    min_transactions: int = 3          # below this: nothing is asserted
    min_transactions_strong: int = 6   # at/above this (with span): SUFFICIENT
    min_history_months: float = 3.0
    min_history_months_strong: float = 6.0
    # A peer benchmark drawn from one or two other customers is an anecdote.
    min_peer_customers: int = 3
    # Cost coverage: the share of a relationship's transactions that have an
    # applicable cost record. Below this, margin is not asserted at all.
    min_cost_coverage: float = 0.6

    # ── annualization ────────────────────────────────────────────────────────
    # A yearly figure extrapolated from six weeks of trading is a guess wearing
    # a suit. Require real span and real transaction count before annualizing.
    annualize_min_history_months: float = 6.0
    annualize_min_transactions: int = 4

    # ── peer benchmark staleness ─────────────────────────────────────────────
    # A peer whose last purchase predates this is not evidence about today.
    peer_recency_days: int = 365

    # ── pricing policy (the authority; ``app.pricing`` reads it from here) ────
    # These used to live as module constants in ``app/pricing.py``, where the
    # Quote Builder read one set of numbers and the commercial analysis another.
    # One definition, one version hash, one place to change them.
    target_margin_default: float = 0.24
    # Tuple-of-pairs rather than a dict so the dataclass stays frozen, hashable
    # and JSON-stable for the version hash.
    target_margin_by_family: tuple[tuple[str, float], ...] = (
        ("solid_carbide_drill", 0.28),
        ("solid_carbide_endmill", 0.28),
        ("milling_insert", 0.30),
        ("drill_tip", 0.30),
        ("reamer", 0.27),
    )
    min_margin: float = 0.12            # hard floor — below this needs approval
    margin_floor: float = 0.15          # soft floor — below this is flagged
    sales_discretion_band: float = 0.03  # ±band off recommended without approval
    # A recommended price is rounded to a multiple of this before it is shown,
    # because quoting ₹1,847.31 invites a conversation about the 31 paise. The
    # increment is currency-scaled, not universal: ₹5 is a sensible tick on a
    # ₹2,000 insert and $5 is a 5% distortion on a $100 one. 0 disables
    # rounding. Denominated in ``currency`` above.
    price_rounding_increment: float = 5.0

    # ── supply resolution bands ──────────────────────────────────────────────
    # pie-parser returns an equivalence score in [0, 1] — geometry plus grade
    # agreement — and deliberately does not say what counts as "equivalent".
    # That call is commercial policy, not nomenclature: it decides whether a
    # candidate is auto-selected onto a customer quote as a TECHNICAL
    # EQUIVALENT, presented as merely COMPATIBLE, or left as a POSSIBLE option
    # for someone to choose.
    #
    # They lived as module constants in ``app/pie_service.py`` — unversioned,
    # untested, and invisible to the policy screen — which is the same mistake
    # the pricing block above records having already made once. A quote line
    # says "technical equivalent" because of these two numbers, so a decision
    # row that cannot name them cannot explain itself.
    equivalence_tech_band: float = 0.85
    equivalence_compat_band: float = 0.60

    # ── quote-time quantity bands ────────────────────────────────────────────
    # Upper edges, inclusive. (1, 10, 50, 200) gives 1 / 2–10 / 11–50 / 51–200 /
    # 201+. Quantity is part of the identity of a price: the same item at 5
    # pieces and at 500 is not the same commercial question, and comparing a
    # quote against an all-quantities average silently mixes the two.
    quantity_band_edges: tuple[int, ...] = (1, 10, 50, 200)
    # A band reference drawn from a single past line is a coincidence.
    min_band_transactions: int = 2

    # ── quote exceptions ─────────────────────────────────────────────────────
    # A gap smaller than this is inside the noise of freight and rounding; a
    # quote screen that flags every ₹40 becomes a screen nobody reads.
    min_quote_exception_impact: float = 500.0
    # How far below a reference price counts as materially below.
    quote_price_tolerance_pct: float = 0.02

    # ── inventory carrying cost ──────────────────────────────────────────────
    # What a rupee of stock costs to hold for a year, as a fraction: interest on
    # the money, warehousing, insurance, obsolescence. Owner-set, because it is
    # a real number about this business and a plausible-looking default would be
    # a made-up number driving a "write this off" recommendation.
    #
    # **This rate is RESTRICTED and must never reach a salesperson.** Monthly
    # holding cost is inventory value x rate / 12, and inventory value is
    # quantity x purchase cost — so a salesperson holding the drain, the
    # quantity and this rate recovers the cost by division. Unlike the floor
    # markup, which varies by family and takes many observations to unpick, this
    # is a single organization-wide constant: disclosing it once makes every
    # cost in the catalogue computable, permanently. See
    # ``commercial/insight/stock.py`` for how the two zones are kept apart.
    carrying_cost_annual_pct: float = 0.12
    # Nothing sold in this many days and the line is *dead* rather than merely
    # slow. Separate from the slow-moving mark below it so a screen can say
    # which of the two it means.
    dead_stock_days: int = 365
    slow_stock_days: int = 180
    #: Has the carrying rate above been published anywhere a salesperson could
    #: read it — a policy document, a training deck, an email?
    #:
    #: Off by default, because it is not. Turn it on and the Monthly Cash Drain
    #: column and its two KPI cards come off the salesperson's stock screen
    #: automatically: the drain is quantity x cost x rate / 12 and the quantity
    #: is on the row, so a reader holding the rate recovers every purchase cost
    #: in the catalogue. Unlike the floor markup, which varies by family and
    #: takes many observations to unpick, this is one organization-wide
    #: constant — disclosed once, disclosed permanently.
    #:
    #: A setting rather than a paragraph asking somebody to remember on the day
    #: it happens. It is part of the thresholds version, so a screen rendered
    #: before and after the change is distinguishable.
    carrying_rate_is_published: bool = False

    # ── statutory payment timing (MSMED s.15 / income-tax s.43B(h)) ──────────
    #
    # Money owed to a registered micro or small supplier past the section 15
    # limit is disallowed as a deduction for that year. It is a cliff on a
    # date, not a slope, and every input to it except the supplier's status is
    # already computed here — which is why these are thresholds rather than
    # constants in a detector.
    #
    # **15 is the default and 45 is the ceiling, not the other way round.**
    # Absent a written agreement the Act allows fifteen days; a written
    # agreement may extend that to a maximum of forty-five. Defaulting to 45
    # because a Zoho dropdown says 45 would understate exposure on exactly the
    # suppliers with no contract, who are the ones the rule protects.
    msme_default_days: int = 15
    msme_max_agreed_days: int = 45
    # How far ahead the watchlist looks. A bill whose deadline is three months
    # out is not yet a decision; one inside this window is.
    msme_watch_horizon_days: int = 60

    # What a disallowed rupee of deduction actually costs, expressed as the
    # rate that would apply to it. **RESTRICTED, and owner-set with no default
    # — this is deliberately ``None``.**
    #
    # Three entities with possibly three constitutions and two regimes sit
    # behind this platform, and a plausible-looking 0.25 would be a made-up
    # number driving a figure somebody plans a payment run around. Left unset,
    # the watchlist still reports the date and the amount at risk — the two
    # facts that matter — and simply omits the cost estimate rather than
    # inventing one. Same discipline as ``carrying_cost_annual_pct`` above,
    # taken one step further because the consequence of being wrong is a tax
    # position rather than a stock decision.
    #
    # Note what this is *not*: the cost of a disallowance is not the tax on it.
    # The deduction returns in the year the money is actually paid, so the
    # economic cost is one year's carry on tax brought forward — which is why
    # ``commercial/insight/msme.py`` multiplies by the carrying rate as well,
    # and why sizing it as the full tax would overstate it roughly tenfold.
    effective_tax_rate: Optional[float] = None

    # ── withholding on purchases (income-tax s.194Q) ─────────────────────────
    #
    # A buyer above the turnover gate deducts on purchases from one resident
    # supplier beyond the party threshold, in a financial year. The seller-side
    # mirror, s.206C(1H), was omitted with effect from 1 April 2025, so there is
    # no longer an interaction to model — only this one obligation.
    #
    # ``s194q_org_gate_met`` is a fact about *us* that this platform cannot
    # derive: our own prior-year turnover spans three legal entities and lives
    # in Tally, not here. Off by default, and the detector emits nothing at all
    # while it is off — an alert derived from an unverified gate is a confident
    # statement about a statutory duty nobody confirmed applies.
    s194q_party_threshold: float = 5_000_000.0
    s194q_org_gate_met: bool = False

    # ── the decision queue ───────────────────────────────────────────────────
    #
    # How money becomes rank. A decision derived from Business State is scored
    # from what it is worth, and this is the exchange rate: one priority point
    # per this many rupees at stake. Absolute rather than relative to the other
    # rows, so a decision's score does not move when an unrelated one appears —
    # a queue whose ordering shifts for reasons nobody can point at is a queue
    # nobody trusts twice.
    #
    # Versioned like every other threshold, so re-tuning the queue does not make
    # last quarter's rankings unexplainable.
    decision_rupees_per_point: float = 5_000.0
    # Stock covering more than this many months of its own measured offtake is
    # excess. Measured, not forecast: it is "you hold N months of what you have
    # historically sold", which is a ratio of two facts and not a prediction of
    # what will sell next.
    excess_cover_months: float = 12.0
    # What share of everything currently owed may sit with one customer before
    # that concentration is worth naming. A ratio (0.25 = a quarter of the
    # book), never a percentage — the same convention as every other share here.
    #
    # A judgement call rather than a derived number, which is exactly why it is
    # a setting: what counts as too much exposure depends on who the customer is
    # and how long the relationship has run, and the platform cannot know either.
    receivable_exposure_share: float = 0.25
    # What share of total purchase spend may sit with one supplier before the
    # concentration is worth naming. Higher than the receivable threshold on
    # purpose: buying most of your stock from one principal is ordinary in
    # distribution, while being owed most of your money by one customer is not.
    supplier_spend_share: float = 0.40

    # ── relationship bond strength ───────────────────────────────────────────
    #
    # How the five measured facets in ``insight/bonds.py`` combine into one
    # score. They are settings rather than constants for the same reason the
    # margin floor is: what makes a relationship strong is a judgement about
    # this business, and burying it in a module would make the score a black
    # box that nobody can argue with.
    #
    # Being here also means they are inside ``version`` — so re-weighting the
    # bond does not make last quarter's bonds unexplainable. A screen rendered
    # before and after a change is distinguishable, which is the whole reason
    # the version hash exists.
    #
    # They need not sum to 1: the composite renormalises over whichever facets
    # are measurable for a given counterparty, because a customer whose
    # invoices carry no due dates has *unknown* payment behaviour rather than
    # bad payment behaviour.
    #
    # Recency leads on purpose. Everything else describes what a relationship
    # has been; only recency says whether it still is.
    bond_weight_recency: float = 0.30
    bond_weight_consistency: float = 0.25
    bond_weight_breadth: float = 0.15
    #: Their share of this company's book. Named ``share`` rather than
    #: ``weight`` because ``weight`` already means "the weight of a facet" two
    #: lines up, and one word meaning two things in one block is how a
    #: mis-tuning happens.
    bond_weight_share: float = 0.15
    bond_weight_reliability: float = 0.15

    # ── which line of the business an item belongs to ────────────────────────
    #
    # HSN prefix → category, the last resort in ``commercial/categories.py``
    # after the item's own Zoho category and any manual override. Here rather
    # than in that module for the same reason ``target_margin_by_family`` is
    # here: it is a policy that moves every mix figure downstream of it, so it
    # belongs inside ``version``. Re-map a prefix and last quarter's coverage
    # stays explainable, because the version says what the map was.
    #
    # Matched as inclusive ranges over the four-digit HSN *heading*, numerically.
    # Ranges rather than string prefixes because the tariff is organised as
    # runs — 8456 through 8465 is "machine tools" as one block — and eleven
    # prefix entries to say one range is eleven places for somebody to leave a
    # gap. A single heading is written as a range whose ends are equal.
    #
    # Tuple-of-triples rather than a dict so the dataclass stays frozen,
    # hashable and JSON-stable for the version hash.
    #
    # Deliberately conservative: a heading is here only where it really does
    # mean one line. Chapter 82 as a whole covers spanners and files as well as
    # cutting tools, so the chapter is not mapped — its headings are, and they
    # split across two lines. An item this map cannot place is reported as
    # uncategorised, which is a much smaller problem than an item placed in the
    # wrong column.
    hsn_category_ranges: tuple[tuple[int, int, str], ...] = (
        # 8202 saws and saw blades; 8207–8209 interchangeable tools, knives and
        # blades for machines, and cermet tips — the carbide/HSS core.
        (8202, 8202, "CUTTING_TOOLS"),
        (8207, 8209, "CUTTING_TOOLS"),
        # Tool holders, arbors and work holders.
        (8466, 8466, "CUTTING_TOOLS"),
        # 8203–8206 hand tools, files, spanners and sets: real lines for this
        # trade, but not cutting tools.
        (8203, 8206, "CONSUMABLES"),
        # Abrasives, abrasive cloth and paper; self-adhesive tapes.
        (6804, 6805, "CONSUMABLES"),
        (3919, 3919, "CONSUMABLES"),
        # Screws, bolts, nuts and washers, and primary cells. Added after
        # measuring the live masters: 7318 was the single largest unmapped
        # heading in both books (22 items across SLS and 4U) and is
        # unambiguously hardware, and 8506 is the battery in a digital gauge.
        # Everything else left unmapped there is a one-off — a computer, a
        # pump, a project import — and stays honestly unplaced rather than
        # being swept into a line to flatter the coverage figure.
        (7318, 7318, "CONSUMABLES"),
        (8506, 8506, "CONSUMABLES"),
        # Petroleum oils. Broader than coolant, but in this book's purchase
        # pattern it is neat cutting oil far more often than anything else.
        (2710, 2710, "COOLANTS"),
        # Lubricating preparations — cutting fluids, way lubes, rust preventives.
        (3403, 3403, "COOLANTS"),
        # Drawing and measuring instruments; measuring, checking and regulating
        # instruments.
        (9017, 9017, "METROLOGY"),
        (9031, 9032, "METROLOGY"),
        # Machine tools as one block: 8456 laser/EDM, 8457 machining centres,
        # 8458 lathes, 8459 drilling/boring/milling, 8460 grinding, 8461
        # planing/shaping, 8462 forging/pressing, 8463 other working, 8464
        # stone/glass, 8465 wood.
        (8456, 8465, "MACHINES"),
    )

    # ── inferring a line from the principal who supplies it ──────────────────
    #
    # An authorised distributor's suppliers are mostly single-line: everything
    # from a coolant principal is coolant. So where the tariff code is blank,
    # the vendor's own catalogue is real evidence — but only where there is
    # enough of it, and only where that vendor is actually concentrated.
    #
    # Both floors are policy, so both are in the version hash. Loosening them
    # is loosening how much of the mix grid is inference rather than fact, and
    # that must be visible in the version a figure was stamped with.
    #
    # Fewer placed items than this from one vendor and their "dominant line" is
    # a coincidence.
    vendor_category_min_items: int = 4
    # And that dominant line must actually dominate. Kennametal sells inserts,
    # holders and gauges; at 0.7 a genuinely mixed principal infers nothing and
    # their unplaced items stay honestly uncategorised.
    vendor_category_dominance: float = 0.7

    @classmethod
    def from_env(cls) -> "CommercialThresholds":
        return cls(
            currency=os.environ.get("DEFAULT_CURRENCY", "INR").strip().upper() or "INR",
            timezone=(os.environ.get("BUSINESS_TIMEZONE", "").strip()
                      or "Asia/Kolkata"),
            carrying_cost_annual_pct=_f("CI_CARRYING_COST_ANNUAL_PCT",
                                        _default("carrying_cost_annual_pct")),
            dead_stock_days=_i("CI_DEAD_STOCK_DAYS", _default("dead_stock_days")),
            slow_stock_days=_i("CI_SLOW_STOCK_DAYS", _default("slow_stock_days")),
            carrying_rate_is_published=(
                os.environ.get("CI_CARRYING_RATE_IS_PUBLISHED", "").strip().lower()
                in ("1", "true", "yes")),
            msme_default_days=_i("CI_MSME_DEFAULT_DAYS",
                                 _default("msme_default_days")),
            msme_max_agreed_days=_i("CI_MSME_MAX_AGREED_DAYS",
                                    _default("msme_max_agreed_days")),
            msme_watch_horizon_days=_i("CI_MSME_WATCH_HORIZON_DAYS",
                                       _default("msme_watch_horizon_days")),
            # Unset stays unset. ``_f`` would turn a missing variable into a
            # number, and the whole point of this one being ``None`` is that
            # there is no defensible default for it.
            effective_tax_rate=(float(os.environ["CI_EFFECTIVE_TAX_RATE"])
                                if os.environ.get("CI_EFFECTIVE_TAX_RATE", "").strip()
                                else _default("effective_tax_rate")),
            s194q_party_threshold=_f("CI_S194Q_PARTY_THRESHOLD",
                                     _default("s194q_party_threshold")),
            s194q_org_gate_met=(
                os.environ.get("CI_S194Q_ORG_GATE_MET", "").strip().lower()
                in ("1", "true", "yes")),
            decision_rupees_per_point=_f("CI_DECISION_RUPEES_PER_POINT",
                                         _default("decision_rupees_per_point")),
            excess_cover_months=_f("CI_EXCESS_COVER_MONTHS",
                                   _default("excess_cover_months")),
            receivable_exposure_share=_f("CI_RECEIVABLE_EXPOSURE_SHARE",
                                         _default("receivable_exposure_share")),
            supplier_spend_share=_f("CI_SUPPLIER_SPEND_SHARE",
                                    _default("supplier_spend_share")),
            bond_weight_recency=_f("CI_BOND_WEIGHT_RECENCY",
                                   _default("bond_weight_recency")),
            bond_weight_consistency=_f("CI_BOND_WEIGHT_CONSISTENCY",
                                       _default("bond_weight_consistency")),
            bond_weight_breadth=_f("CI_BOND_WEIGHT_BREADTH",
                                   _default("bond_weight_breadth")),
            bond_weight_share=_f("CI_BOND_WEIGHT_SHARE",
                                 _default("bond_weight_share")),
            bond_weight_reliability=_f("CI_BOND_WEIGHT_RELIABILITY",
                                       _default("bond_weight_reliability")),
            recent_days=_i("CI_RECENT_DAYS", 90),
            previous_days=_i("CI_PREVIOUS_DAYS", 90),
            historical_lookback_days=_i("CI_HISTORICAL_LOOKBACK_DAYS", 730),
            min_margin_deterioration_pp=_f("CI_MIN_MARGIN_DETERIORATION_PP", 0.03),
            # `SIG_MARGIN_DROP_POINTS` is still read, and still means what it
            # meant, so a deployment that set it keeps the value it chose.
            queue_margin_drop_pp=_f("CI_QUEUE_MARGIN_DROP_PP",
                                    _f("SIG_MARGIN_DROP_POINTS", 0.05)),
            meaningful_cost_increase_pct=_f("CI_MEANINGFUL_COST_INCREASE_PCT", 0.05),
            meaningful_price_change_pct=_f("CI_MEANINGFUL_PRICE_CHANGE_PCT", 0.02),
            meaningful_volume_change_pct=_f("CI_MEANINGFUL_VOLUME_CHANGE_PCT", 0.15),
            min_material_gap=_f("CI_MIN_MATERIAL_GAP", 10_000.0),
            min_transactions=_i("CI_MIN_TRANSACTIONS", 3),
            min_transactions_strong=_i("CI_MIN_TRANSACTIONS_STRONG", 6),
            min_history_months=_f("CI_MIN_HISTORY_MONTHS", 3.0),
            min_history_months_strong=_f("CI_MIN_HISTORY_MONTHS_STRONG", 6.0),
            min_peer_customers=_i("CI_MIN_PEER_CUSTOMERS", 3),
            min_cost_coverage=_f("CI_MIN_COST_COVERAGE", 0.6),
            annualize_min_history_months=_f("CI_ANNUALIZE_MIN_HISTORY_MONTHS", 6.0),
            annualize_min_transactions=_i("CI_ANNUALIZE_MIN_TRANSACTIONS", 4),
            peer_recency_days=_i("CI_PEER_RECENCY_DAYS", 365),
            target_margin_default=_f("CI_TARGET_MARGIN_DEFAULT", 0.24),
            target_margin_by_family=_families(),
            min_margin=_f("CI_MIN_MARGIN", 0.12),
            margin_floor=_f("CI_MARGIN_FLOOR", 0.15),
            sales_discretion_band=_f("CI_SALES_DISCRETION_BAND", 0.03),
            price_rounding_increment=_f("CI_PRICE_ROUNDING_INCREMENT", 5.0),
            quantity_band_edges=_edges(),
            min_band_transactions=_i("CI_MIN_BAND_TRANSACTIONS", 2),
            min_quote_exception_impact=_f("CI_MIN_QUOTE_EXCEPTION_IMPACT", 500.0),
            quote_price_tolerance_pct=_f("CI_QUOTE_PRICE_TOLERANCE_PCT", 0.02),
        )

    # ── pricing-policy lookups ───────────────────────────────────────────────
    def target_margin(self, family: Optional[str]) -> float:
        """The target margin for a tool family, or the default."""
        if family:
            for name, value in self.target_margin_by_family:
                if name == family:
                    return value
        return self.target_margin_default

    def money(self, amount, unknown: str = "unknown") -> str:
        """An amount spelled in this policy's currency — ``₹4,00,000``.

        Lives here for the same reason ``target_margin`` does: the currency is
        part of the policy, so the thing that knows the policy is the thing
        that can spell an amount without being told twice.
        """
        from .money import money as _fmt
        return _fmt(amount, self.currency, unknown=unknown)

    # ── incentive scheme ─────────────────────────────────────────────────────
    # There are deliberately no incentive rates here.
    #
    # Four of them used to live in this dataclass, for a scheme paid on price
    # realisation against what a customer last paid. That scheme has been
    # replaced by contribution above floor, and its rates now live in exactly
    # one place: ``incentive_engine/config/parameters.yaml``, which is
    # versioned, effective-dated, and published before the year it applies to.
    #
    # Compensation policy in two homes is the failure this codebase warns about
    # in CLAUDE.md §2 under "responsibility duplication": the copy that gets
    # edited is never the copy that pays. A response that reports both a
    # thresholds version and an incentive-config version is telling the truth
    # about which parameters produced which numbers.

    @property
    def version(self) -> str:
        """Stable short hash of the threshold values (reproducibility)."""
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return "ci_" + hashlib.sha256(blob).hexdigest()[:10]


def load_commercial_thresholds() -> CommercialThresholds:
    return CommercialThresholds.from_env()
