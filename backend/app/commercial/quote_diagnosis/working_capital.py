"""What the cash tied up in this quote line costs, at the grain of the line.

The platform already charges customer credit. ``insight/financing`` joins the
persisted item metrics to the settlement history and says what a *relationship*
earns after the cost of waiting to be paid; ``insight/cycle`` replays DIO, DSO
and DPO per legal entity at every month end; ``insight/capital`` restates that
cycle as money. All three answer at relationship, account or portfolio grain,
over rows that already exist.

None of them answers the question a desk has in front of it: **this line, this
quantity, this customer, this supplier — how long is the money out, and what
does that cost?** That is the only thing this module does. Every piece of it is
borrowed:

``insight/financing.financing_cost``  the arithmetic, and its floor at zero days
``CommercialThresholds.cost_of_capital_annual_pct``  the rate, owner-set, no default
``insight/payments.lag``              how long this customer actually takes to pay
``insight/payments.percentile``       the one nearest-rank definition
``insight/terms.effective_days``      the supplier's agreed term, or the ERP's
``insight/absence``                   the vocabulary for refusing
``drivers.severity`` / ``drivers.cost_strength``  how much it matters, how much
                                      to believe it
``rules``' four-grade ladder          believability, not a second scale

Nothing here recomputes any of them. A second financing rate, a second days-to-pay
floor or a third way of saying "cannot be assessed" would be the duplication
CLAUDE.md §2 is about, and the copy nobody reads is always the one that drifts.

── which days are counted, and from what to what ────────────────────────────

    funded_days = receivable_days - supplier_credit_days

**``receivable_days`` runs from the day this line is invoiced to the day the
customer's money arrives.** Measured wherever it can be: the
``expected_days_to_pay`` on ``payments.Lag``, the median over settled invoices
from the *document* date. That is the figure ``payments`` built for this exact
question — its
three percentile fields are days *late*, which answer "were the terms honoured",
and only the fourth answers "how long is the cash tied up". A customer on 90-day
terms who pays on day 90 is never late and is funded for ninety days all the
same.

Where ``lag`` refuses — below its evidence floor — the money comes back **on the
due date**, which is what that refusal means in its own docstring, and never
"assume they are prompt". The due date is taken as the median credit this
account was actually granted, ``due_date - document_date`` over the same settled
invoices. It is a fact about the agreement rather than about behaviour, and it
is graded on the one or two invoices that could carry it rather than on any
measurement — which is why a reading resting on it never interrupts anybody. See
``_days_strength``, which says so and explains why no cap enforces it.

**``supplier_credit_days`` runs from the day the supplier bills us to the day we
pay them** — ``terms.effective_days``, which prefers the term somebody recorded
over the term Zoho's dropdown could express, and which collapses an
``END_OF_MONTH`` basis to its day count because the basis changes the date of
one bill rather than the length of the credit.

**Day zero is the same day on both sides, and that is the approximation.** The
supplier's bill is taken to be dated the day this line is invoiced. True for a
line bought against the order; untrue, by exactly the time the goods sat on the
shelf, for a line sold from stock.

── stock holding is deliberately not in scope ───────────────────────────────

The full cycle is ``DIO + DSO - DPO`` and only two of the three legs are here.
``StockSnapshot`` exists and ``insight/stock`` and ``insight/gmroi`` read it, so
the omission is a decision rather than an oversight. Three reasons, and the
third is why the omission is safe to publish:

1. **Grain.** Nothing in these books records which purchase filled which sales
   line, and a quote line is not attached to stock at all. What ``insight/stock``
   produces is weeks of cover from a consumption *rate* — a fact about the item's
   velocity across the book, not about the units on this line. Charging a
   portfolio velocity to one line would be two windows in one product, which is
   the mistake ``gmroi`` exists to demonstrate.
2. **History.** Zoho reports stock as a level now and holds no history, so a
   stock leg exists only from the day this platform started observing.
   ``insight/cycle`` withholds a whole month's CCC rather than carry the previous
   observation forward, and a diagnosis replayed for a quote written in March
   would have nothing to read at all.
3. **Direction.** Leaving it out can only make the funded window *shorter*, so
   the charge is a **floor under what this line's cash costs, never a ceiling** —
   the same claim ``insight/financing`` makes about itself and for the same
   reason. It is published as a ``PERMANENT`` entry on every assessed reading, so
   the understatement is on the row rather than in this docstring only.

── what it refuses, and never defaults ──────────────────────────────────────

Five refusals, each naming the field that would close it. None of them is a
zero and none of them is a plausible-looking stand-in: a funding figure is
something somebody plans a payment run around.

``NO_RATE``            ``cost_of_capital_annual_pct`` is unset. Nothing at all is
                       computed — not the days, not the capital — because a
                       screen with a cycle and a blank charge invites somebody to
                       fill it with ``carrying_cost_annual_pct``, and that rate
                       also pays for the warehouse. ``COLLECTABLE``: one person
                       typing one number finishes it.
``NO_QUOTED_PRICE``    No price on the line, so the margin it earns is undefined
                       and the drag cannot be graded.
``NO_QUANTITY``        No positive quantity, so there is no capital to tie up.
``NO_COST_BASELINE``   No knowable purchase, so what leaves the bank is unknown.
                       Unknown, not zero — a zero outlay would price this line as
                       free to fund.
``NO_SUPPLIER_TERMS``  Neither an agreed term nor the ERP's. Assuming nought days
                       would charge the line from the day the goods arrive and
                       overstate every figure below it.
``NO_RECEIVABLE_DAYS`` Neither a measured lag nor a due date on record.

── point in time ────────────────────────────────────────────────────────────

Terms and behaviour are evidence like any other. Settlements are bounded to the
engine's own ``historical_lookback_days`` window and to what was visible when the
quote was written; an agreed supplier term recorded after the quote is dropped
through ``evidence.is_knowable`` and its reason reported. Two residuals survive
that and are published rather than buried — a settlement carries no source
creation stamp, and ``Vendor.payment_terms_days`` is rewritten by every sync.

── restricted ───────────────────────────────────────────────────────────────

**In its entirety.** The capital is purchase cost and the charge divides straight
back to it: ``expected_cost = charge_per_unit x 365 / (funded_days x rate)``, and
the rate is one organization-wide constant. This belongs on
``rules.OwnerDiagnosis`` and has no place on ``rules.OperationsDiagnosis``, whose
whole value is that it has no field to put it in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Optional, Sequence

from ...clock import aware
from ..config import CommercialThresholds
from ..insight import absence, financing, payments, terms
from . import drivers
from .baselines import CostBaseline
from .evidence import is_knowable
from .rules import INSUFFICIENT, MODERATE, STRONG, WEAK, _RANK

_ZERO = Decimal("0")


# ── where each leg's number came from ────────────────────────────────────────
#
# Published rather than left to a reader to infer from which fields are filled.
# The two are not the same claim and a screen that showed only the days would
# present a contractual term and a measured habit as one number.

#: ``payments.Lag.expected_days_to_pay`` — the median of what this account has
#: actually done, from invoice date to settlement.
MEASURED_LAG = "MEASURED_LAG"
#: The median credit this account was granted on its settled invoices — the due
#: date, which is what ``payments.lag`` returning ``None`` means.
GRANTED_TERM = "GRANTED_TERM"

#: ``VendorPaymentTerm`` — what was actually agreed with this supplier.
AGREED_TERM = "AGREED_TERM"
#: ``Vendor.payment_terms_days`` — what Zoho's dropdown could express.
ERP_TERM = "ERP_TERM"


# ── outcomes ─────────────────────────────────────────────────────────────────

#: A reading was produced. Everything else on this list is a refusal.
ASSESSED = "ASSESSED"

NO_RATE = "NO_RATE"
NO_QUOTED_PRICE = "NO_QUOTED_PRICE"
NO_QUANTITY = "NO_QUANTITY"
NO_COST_BASELINE = "NO_COST_BASELINE"
NO_SUPPLIER_TERMS = "NO_SUPPLIER_TERMS"
NO_RECEIVABLE_DAYS = "NO_RECEIVABLE_DAYS"
#: The line names no customer at all, so there is no account whose payment
#: behaviour could be read. Distinct from NO_RECEIVABLE_DAYS, which is about an
#: account that exists and has not settled enough: telling somebody pricing an
#: unassigned line that "this account has settled nothing" describes an account
#: that is not there. A refusal may be silent about what it cannot see; it may
#: not be wrong about it.
NO_CUSTOMER = "NO_CUSTOMER"


@dataclass(frozen=True)
class WorkingCapital:
    """What this line's cash cycle costs, or a refusal to say.

    **Two shapes, and ``assessed`` is which.** Either every money field is filled
    and ``reason`` is ``ASSESSED``, or all of them are ``None`` and ``reason``
    names what stopped it. A caller reads the flag rather than testing whether a
    figure happens to be present: a predicate re-derived from published fields is
    a guess about what the producer meant, which is the lesson CLAUDE.md §1 draws
    from ``_identity_candidate``.

    Frozen, like every other record in this package. A reading is written into an
    append-only diagnosis row and replayed; one that could be edited afterwards
    would make the citation a lie.

    **No ``thresholds_version`` of its own, deliberately.** It rides on
    ``rules.OwnerDiagnosis``, which is stamped, and a second stamp on a nested
    object would be two answers to "which policy judged this row" —
    ``drivers.Attribution`` sits in the same place for the same reason. The
    versioned numbers it reads are named on the row instead: ``rate`` is the
    cost of capital the charge was levied at, and the severity boundaries are
    ``drivers``'.

    **RESTRICTED in its entirety** — see the module docstring.
    """

    #: Whether a figure was produced at all.
    assessed: bool
    #: ``ASSESSED``, or the refusal.
    reason: str

    #: ``receivable_days - supplier_credit_days``, **signed**. Negative means the
    #: supplier funds this line outright and then some. Reported as measured; the
    #: charge below is floored at zero days by ``financing.financing_cost``,
    #: which says why — a negative charge would lift adjusted margin above gross
    #: margin, and pricing an advance is a different feature.
    funded_days: Optional[int]
    #: Invoice date to the customer's money arriving.
    receivable_days: Optional[int]
    #: Supplier's bill date to us paying it.
    supplier_credit_days: Optional[int]
    #: ``MEASURED_LAG`` or ``GRANTED_TERM``.
    days_source: Optional[str]
    #: ``AGREED_TERM`` or ``ERP_TERM``.
    terms_source: Optional[str]
    #: Settled invoices inside the window that the quoter could have seen. Zero
    #: is a real answer and is why a refusal can be ``COLLECTABLE`` rather than
    #: ``TRANSIENT``.
    settlements: int

    #: The annual cost of capital the charge was levied at.
    rate: Optional[Decimal]
    #: The cash that leaves the bank per unit — ``CostBaseline.expected_cost``,
    #: carried rather than recomputed.
    capital_per_unit: Optional[Decimal]
    #: That across the line.
    capital_at_risk: Optional[Decimal]
    #: What funding it for ``funded_days`` costs, per unit.
    charge_per_unit: Optional[Decimal]
    #: And across the line. Exactly ``charge_per_unit x qty``, so the two
    #: reconcile on screen.
    line_charge: Optional[Decimal]
    #: Margin points the wait costs, carried as a fraction — ``-0.032`` is 3.2
    #: points off. **Negative means this hurt margin**, which is ``drivers``'
    #: sign convention and not a second one.
    effect_pp: Optional[Decimal]

    #: ``MAJOR`` / ``MINOR`` / ``NEGLIGIBLE`` — how much it matters, from
    #: ``drivers.severity`` against the same versioned boundaries.
    severity: str
    #: ``STRONG`` / ``MODERATE`` / ``WEAK`` / ``INSUFFICIENT`` — how much to
    #: believe it. The weaker of the two halves below, never an average.
    strength: str
    #: The days half on its own.
    days_strength: str
    #: The capital half on its own — ``drivers.cost_strength``.
    cost_strength: str

    #: Whether this interrupts anybody. Calculation happens regardless; see
    #: ``_surfaces``.
    surfaces: bool
    #: What the number answers, in words, or the refusal and its reason.
    basis: str
    #: ``insight/absence`` entries: the refusal, or the residuals every assessed
    #: reading carries.
    unavailable: tuple[dict, ...]
    #: The purchase rows the capital figure rests on — ``CostBaseline.cited``,
    #: so a reading can be replayed against the same evidence.
    cited: tuple[str, ...]

    def to_dict(self) -> dict:
        """RESTRICTED. Only ever serialised into an owner-facing payload."""
        def m(v: Optional[Decimal]) -> Optional[float]:
            return float(round(v, 2)) if v is not None else None
        return {
            "assessed": self.assessed,
            "reason": self.reason,
            "funded_days": self.funded_days,
            "receivable_days": self.receivable_days,
            "supplier_credit_days": self.supplier_credit_days,
            "days_source": self.days_source,
            "terms_source": self.terms_source,
            "settlements": self.settlements,
            "rate": float(self.rate) if self.rate is not None else None,
            "capital_per_unit": m(self.capital_per_unit),
            "capital_at_risk": m(self.capital_at_risk),
            "charge_per_unit": m(self.charge_per_unit),
            "line_charge": m(self.line_charge),
            # Six decimals: exactly ``drivers.PP_QUANTUM``, the quantum the
            # figure is already carried at, so nothing is lost between the value
            # and its serialisation and two runs produce the same bytes.
            "effect_pp": (float(round(self.effect_pp, 6))
                          if self.effect_pp is not None else None),
            "severity": self.severity,
            "strength": self.strength,
            "days_strength": self.days_strength,
            "cost_strength": self.cost_strength,
            "surfaces": self.surfaces,
            "basis": self.basis,
            "unavailable": [dict(u) for u in self.unavailable],
            "cited": list(self.cited),
        }


def assess(*, quoted_unit_price: Optional[Decimal], qty: Decimal,
           cost: CostBaseline,
           settlements: Sequence[payments.Settlement],
           has_customer: bool = True,
           supplier_term: Optional[terms.Term],
           supplier_term_recorded_at: Optional[datetime],
           supplier_erp_days: Optional[int],
           as_of: date, knowable_by: datetime,
           th: CommercialThresholds) -> WorkingCapital:
    """What the cash tied up in this line costs. Pure, total and deterministic.

    Returns a ``WorkingCapital`` on every path — a refusal is one of its two
    shapes — so a caller is always told either the figure or why there is not
    one. A field that could simply be missing would read as "nothing to report",
    which is the failure CLAUDE.md §1 names.

    ``settlements`` are **this customer's**, unfiltered: the window and the
    visibility cut are applied here rather than by the caller, because a caller
    that bounded them differently from the engine's own evidence would put two
    windows in one product. Passing another party's rows is a programming error
    and raises — ``payments.lag`` reads the party from the first row, so a mixed
    list would silently label one customer's habit with another's name.

    ``as_of`` and ``knowable_by`` mean what they mean everywhere else in this
    package: the commercial date the reading speaks in, and the instant
    visibility is cut off at.
    """
    party_ids = {s.party_id for s in settlements}
    if len(party_ids) > 1:
        raise ValueError(
            "working_capital.assess takes one customer's settlements; it was "
            f"given {len(party_ids)} parties. `payments.lag` reads the party "
            "from the first row, so a mixed list measures one account and names "
            "another.")

    # ── the rate, first and on its own ──────────────────────────────────────
    # Nothing is computed without it, including the halves that would survive.
    # ``insight/financing`` takes the same position and says why: a cycle beside
    # a blank charge invites somebody to fill the blank with the stock carrying
    # rate, which also pays for the warehouse.
    configured = th.cost_of_capital_annual_pct
    if configured is None:
        return _refused(NO_RATE, absence.COLLECTABLE, (
            "No annual cost of capital is set, so there is no rate at which to "
            "charge the money this line ties up. Set 'Annual cost of capital' in "
            "Settings — what a rupee actually costs this business to fund for a "
            "year. It is deliberately left empty rather than defaulted, and the "
            "stock carrying rate is not it: a receivable occupies no shelf."))
    rate = Decimal(str(configured))

    if quoted_unit_price is None or quoted_unit_price <= _ZERO:
        return _refused(NO_QUOTED_PRICE, absence.COLLECTABLE, (
            "This line carries no quoted unit price, so the margin it earns is "
            "undefined and the points a funding charge would take off it cannot "
            "be graded."))

    if qty <= _ZERO:
        return _refused(NO_QUANTITY, absence.COLLECTABLE, (
            "This line carries no positive quantity, so there is no capital to "
            "be tied up in it."))

    unit_cost = cost.expected_cost
    if unit_cost is None or unit_cost <= _ZERO:
        missing = ("no purchase for this item was knowable when the quote was "
                   "written" if not cost.cited else
                   "the knowable purchases produced no usable cost level")
        return _refused(NO_COST_BASELINE, absence.COLLECTABLE, (
            f"The cash this line puts out is what was paid for the goods, and "
            f"{missing}. Unknown, not zero — a nought outlay would price this "
            f"line as free to fund. {cost.observations} usable purchase "
            f"observation{'' if cost.observations == 1 else 's'} on record."))

    # ── the supplier leg ────────────────────────────────────────────────────
    usable_term, term_excluded = _knowable_term(
        supplier_term, supplier_term_recorded_at, knowable_by=knowable_by)
    credit_days = terms.effective_days(usable_term, supplier_erp_days)
    if credit_days is None:
        return _refused(NO_SUPPLIER_TERMS, absence.COLLECTABLE, (
            "Neither an agreed payment term nor the ERP's own value is on record "
            "for this line's supplier, so how long our money stays in the bank "
            "before the bill is paid is not known. Nought days is not the "
            "answer — it would charge this line from the day the goods land and "
            "overstate every figure below."))
    terms_source = AGREED_TERM if usable_term is not None else ERP_TERM

    # ── the receivable leg ──────────────────────────────────────────────────
    visible, hidden = _visible_settlements(
        settlements, as_of=as_of, knowable_by=knowable_by,
        window_days=th.historical_lookback_days)
    measured = payments.lag(visible)
    granted = _granted_days(visible)

    if measured is not None:
        receivable_days = measured.expected_days_to_pay
        days_source = MEASURED_LAG
        days_evidence = measured.settlements
    elif granted is not None:
        receivable_days, days_evidence = granted
        days_source = GRANTED_TERM
    else:
        # Two kinds, because they call for two different responses. Rows
        # accumulating under the floor resolve themselves; nothing on record at
        # all is somebody's job.
        if visible:
            return _refused(NO_RECEIVABLE_DAYS, absence.TRANSIENT, (
                f"{len(visible)} settled invoice(s) for this account inside the "
                f"window and none of them carries a usable due date to read a "
                f"credit term off, so neither a measured days-to-pay — needing "
                f"{payments.MIN_SETTLEMENTS} — nor a due date is available. It "
                "resolves as the account trades and terms are recorded on its "
                "invoices; the book's median is deliberately not substituted, "
                "which would state a funding cost for the one account there is "
                "no evidence about."), settlements=len(visible))
        if not has_customer:
            # PERMANENT for this line as it stands, not COLLECTABLE: nothing can
            # be recorded that would complete the reading while the line names
            # nobody. Naming a customer is a different act from recording a
            # term, and offering "record the terms" here sends somebody to a
            # screen that cannot help them.
            return _refused(NO_CUSTOMER, absence.PERMANENT, (
                "This line names no customer, so there is no account whose "
                "payment behaviour could say when the money comes back. The "
                "book's median is deliberately not substituted: it would state "
                "a funding cost for an account this line does not have."))
        return _refused(NO_RECEIVABLE_DAYS, absence.COLLECTABLE, (
            "This account has settled nothing this quote could have seen and no "
            "credit term is recorded against its invoices, so there is no date "
            "the money comes back on. Record the terms this quote is written "
            "under and the reading completes."))

    # ── the money ───────────────────────────────────────────────────────────
    funded_days = receivable_days - credit_days
    # The one financing arithmetic in the platform, and its own floor at zero
    # days. Per unit first, so the line figure is exactly ``qty`` times what a
    # reader sees on the row rather than a separately rounded product.
    charge_per_unit = financing.financing_cost(unit_cost, funded_days, rate)
    line_charge = charge_per_unit * qty
    capital_at_risk = unit_cost * qty

    # Margin is ``(P - C) / P``; charging ``f`` per unit against it moves it by
    # exactly ``f / P``. One division, so the identity needs no reconciliation
    # check of the kind ``drivers`` builds — there is nothing here for two
    # counterfactual orders to disagree about.
    #
    # Negated because it costs margin, which is ``drivers``' sign convention —
    # except at zero, where negating would publish ``-0.000000`` and leave a
    # reader wondering which way a nothing went.
    drag = _drag_pp(charge_per_unit, quoted_unit_price)
    effect_pp = -drag if drag != _ZERO else drag

    cost_grade = drivers.cost_strength(cost, th)
    days_grade = _days_strength(days_evidence, th)
    grade = cost_grade if _RANK[cost_grade] < _RANK[days_grade] else days_grade
    harm = drivers.severity(effect_pp, th)

    return WorkingCapital(
        assessed=True, reason=ASSESSED,
        funded_days=funded_days, receivable_days=receivable_days,
        supplier_credit_days=credit_days, days_source=days_source,
        terms_source=terms_source, settlements=len(visible),
        rate=rate, capital_per_unit=unit_cost, capital_at_risk=capital_at_risk,
        charge_per_unit=charge_per_unit, line_charge=line_charge,
        effect_pp=effect_pp, severity=harm, strength=grade,
        days_strength=days_grade, cost_strength=cost_grade,
        surfaces=_surfaces(grade=grade, severity=harm),
        basis=_basis(funded_days=funded_days, receivable_days=receivable_days,
                     credit_days=credit_days, days_source=days_source,
                     terms_source=terms_source, effect_pp=effect_pp),
        unavailable=_residuals(days_source=days_source,
                               terms_source=terms_source,
                               term_excluded=term_excluded, hidden=hidden),
        cited=tuple(cost.cited),
    )


# ── the gate ─────────────────────────────────────────────────────────────────

def _surfaces(*, grade: str, severity: str) -> bool:
    """Whether this interrupts anybody.

    Computed and stored regardless: the gate governs *interruption*, not
    calculation, which is the rule ``rules._surfaces`` states and this one
    inherits rather than restates.

    Two conditions, and both are policy already written down elsewhere.

    **Below ``MODERATE`` nothing surfaces, however large the money.** A funding
    charge is a large number by construction — it is a cost multiplied by a
    fraction of a year — so a weak cost baseline or a days figure read off two
    invoices would produce an alarming figure with nothing behind it, and a card
    that is wrong at that size is the one that teaches a desk to stop reading.

    **And only ``MAJOR`` severity.** ``diagnosis_driver_minor_pp`` is where an
    effect stops being freight and rounding; ``diagnosis_driver_major_pp`` is
    where it is worth somebody's attention. At a twelve percent rate an ordinary
    sixty-day cycle is already a point or so of margin on every line in the book,
    so surfacing ``MINOR`` would surface everything and the package's own rule is
    that the default state is silent. A ``MINOR`` reading is still computed,
    still stored and still on the owner's card when they open it.
    """
    return _RANK[grade] >= _RANK[MODERATE] and severity == drivers.MAJOR


# ── the two legs ─────────────────────────────────────────────────────────────

def _visible_settlements(rows: Sequence[payments.Settlement], *, as_of: date,
                         knowable_by: datetime, window_days: int,
                         ) -> tuple[list[payments.Settlement], int]:
    """The settled invoices this quote could have been written against.

    Two bounds, and they are different questions.

    **The window** is ``historical_lookback_days`` on the invoice's own date —
    the same window ``service._load`` bounds the price evidence by and
    ``baselines.cost_baseline`` bounds the purchases by. Deliberately this
    engine's window rather than ``financing.WINDOW_DAYS``: that one is twelve
    months because it has to match a twelve-month revenue rollup, and there is no
    rollup here. What matters is that the price, the cost and the payment
    evidence behind one diagnosis span one period.

    **The visibility cut** is ``paid_on`` strictly before the quote's day. A
    receipt that had not happened is not evidence the quoter had, and strict
    rather than inclusive for the same reason ``evidence.is_knowable`` is: the
    boundary has to fall somewhere and it falls on the conservative side.

    ``paid_on`` is a weaker instrument than the ``recorded_at`` the rest of this
    engine filters on, and the residual is published rather than buried — see
    ``_residuals``. A ``Settlement`` carries no source creation stamp at all, so
    there is nothing stronger to ask.

    Returns the kept rows and how many were dropped. Sorted on the way out, so
    the median never depends on the order a database returned rows in.
    """
    cutoff = aware(knowable_by)
    start = as_of - timedelta(days=window_days)
    kept: list[payments.Settlement] = []
    dropped = 0
    for row in rows:
        in_window = start < row.document_date <= as_of
        seen = cutoff is not None and row.paid_on < cutoff.date()
        if in_window and seen:
            kept.append(row)
        else:
            dropped += 1
    kept.sort(key=lambda s: (s.document_date, s.document_ref))
    return kept, dropped


def _granted_days(rows: Sequence[payments.Settlement],
                  ) -> Optional[tuple[int, int]]:
    """The credit this account was actually granted, and how many invoices say so.

    ``due_date - document_date`` per settled invoice, at the median. This is the
    due date ``payments.lag`` means when it refuses: the money comes back on the
    day it was promised, which is a different claim from the account being
    prompt, and it is the only one the evidence supports.

    Taken through ``payments.percentile`` rather than a local median, so there is
    one nearest-rank definition in the platform — that function was made public
    for exactly this kind of second caller.

    An invoice due before it was raised is dropped rather than averaged in:
    ``terms.validate`` refuses a negative term in as many words, because a bill
    is not due before it is raised, and one transposed pair of dates would drag
    the median of a small account.

    ``None`` when no settled invoice carries a usable due date.
    """
    granted = [(s.due_date - s.document_date).days for s in rows
               if s.due_date is not None
               and (s.due_date - s.document_date).days >= 0]
    if not granted:
        return None
    return payments.percentile(granted, 0.50), len(granted)


def _knowable_term(term: Optional[terms.Term],
                   recorded_at: Optional[datetime], *, knowable_by: datetime,
                   ) -> tuple[Optional[terms.Term], Optional[str]]:
    """The agreed supplier term, if the quoter could have had it.

    A term typed last week is not evidence a quote written in March was priced
    against, and ``VendorPaymentTerm`` is upserted one row per vendor, so there
    is no earlier version to fall back to. The test is ``evidence.is_knowable``
    — the engine's one answer to "could this have been known" — rather than a
    local date comparison that would eventually disagree with it.

    ``backfill_before`` is passed as ``None`` on purpose. That rule exists for
    rows bulk-loaded out of a prior system, whose creation stamp is the load
    time; a payment term is typed into this platform by a person and its stamp
    means what it says. Applying a migration cut-over to it would discard terms
    recorded before a migration they have nothing to do with.

    A term with no stamp at all is dropped too — ``is_knowable`` answers
    ``NO_RECORDED_AT`` — because an unstamped agreement cannot be placed either
    side of the quote.

    Returns the term to use and, where one was dropped, ``is_knowable``'s reason.
    """
    if term is None:
        return None, None
    reason = is_knowable(recorded_at, knowable_by=knowable_by,
                         backfill_before=None)
    if reason is None:
        return term, None
    return None, reason


def _days_strength(observations: int, th: CommercialThresholds) -> str:
    """How much to believe the days half.

    The same ladder shape and the same threshold fields ``drivers.cost_strength``
    reads, and deliberately not that function: it grades purchases of an item and
    this grades settled invoices of an account. One ladder, two populations — a
    second set of boundaries here would be a second policy on "how many
    observations is enough", and the one that gets edited is never the one that
    gets read.

    **A granted term cannot reach ``MODERATE``, and no cap is applied to make
    that true.** The first version of this function capped it, and the cap could
    never bind: ``payments.lag`` refuses only below ``MIN_SETTLEMENTS`` invoices
    *carrying a due date*, and the due-date fallback reads the term off those
    same invoices — so the fallback runs on one or two rows by construction and
    the ladder already grades it ``WEAK`` or ``INSUFFICIENT``. A control that
    looks like a control and is a no-op is worse than not having one, which is
    the argument ``drivers.attribute`` makes about reaching for ``resistance``.

    The consequence is worth stating plainly rather than leaving to be
    discovered: a reading whose days come from the due date **never interrupts
    anybody**. It is computed, stored and on the owner's card, and ``_residuals``
    names what would change it — the account's *open* invoices carry due dates
    too, and reading them would let a contractual term be graded on more than the
    one or two that happen to have settled.
    """
    if observations >= th.diagnosis_strong_min_comparables:
        grade = STRONG
    elif observations >= th.diagnosis_moderate_min_comparables:
        grade = MODERATE
    elif observations >= th.diagnosis_weak_min_comparables:
        grade = WEAK
    else:
        grade = INSUFFICIENT
    return grade


# ── words ────────────────────────────────────────────────────────────────────

def _basis(*, funded_days: int, receivable_days: int, credit_days: int,
           days_source: str, terms_source: str, effect_pp: Decimal) -> str:
    """The sentence a reader quotes back: which days, from what to what."""
    measured = ("the median this account has actually taken to pay"
                if days_source == MEASURED_LAG else
                "the median credit granted on its invoices — the due date, "
                "because how long it actually takes is below the evidence floor")
    agreed = ("the term agreed with the supplier" if terms_source == AGREED_TERM
              else "the term the ERP holds for the supplier, no agreed term "
                   "being on record")
    if funded_days <= 0:
        return (
            f"The supplier's credit of {credit_days} days covers the "
            f"{receivable_days} days to being paid ({measured}) in full, so no "
            "funding charge is levied on this line. A negative cycle is worth "
            "something and pricing it is a different feature; this reading is a "
            f"floor under the cost of the line's cash, never a ceiling. Supplier "
            f"days are {agreed}.")
    return (
        f"Money is out for {funded_days} days on this line: {receivable_days} "
        f"days from invoice to the customer's money arriving ({measured}), less "
        f"{credit_days} days of supplier credit ({agreed}). Funding the purchase "
        f"cost over that window costs {_pp_text(-effect_pp)} of this line's "
        "margin. Day zero is the same day on both sides, so any time the goods "
        "sit on the shelf is not counted and the figure is a floor.")


def _pp_text(value: Decimal) -> str:
    """A percentage-point figure in points, the way ``drivers`` writes one."""
    return f"{value * 100:.4f} pp"


def _residuals(*, days_source: str, terms_source: str,
               term_excluded: Optional[str], hidden: int) -> tuple[dict, ...]:
    """What an assessed reading still cannot see.

    Every one of these is ``PERMANENT`` and none of them is a gap somebody can
    close: they are the honest edges of the figure above, and they push it one
    way — down. Published on the row rather than in a docstring, so a reader is
    told the number is a floor at the moment they read it.
    """
    out: list[dict] = [
        {
            "series": "working_capital_stock_holding",
            # Grain, not collection. Nothing records which purchase filled which
            # sales line, and a quote line is attached to no stock at all.
            "kind": absence.PERMANENT,
            "reason": (
                "The cycle counted here runs from the supplier's bill to the "
                "customer's payment and leaves out any time the goods sit on "
                "the shelf: nothing in these books records which purchase fills "
                "which line, and stock history exists only from the day this "
                "platform began observing. Including it could only lengthen the "
                "window, so what is shown is a floor under what this line's "
                "cash costs, not a ceiling."),
        },
        {
            "series": "working_capital_settlement_visibility",
            # The rest of the engine filters on `recorded_at`; a settlement has
            # none, so `paid_on` is the strongest instrument available.
            "kind": absence.PERMANENT,
            "reason": (
                f"A settlement carries no source creation stamp, so the "
                f"{days_source} figure is filtered on when the money moved "
                f"rather than on when this business recorded it — the weaker of "
                f"the two tests this engine uses. {hidden} settlement(s) were "
                "outside the window or not yet visible and were dropped."),
        },
    ]
    if days_source == GRANTED_TERM:
        out.append({
            "series": "working_capital_credit_term_evidence",
            # BUILDABLE rather than COLLECTABLE: the rows exist and are already
            # synced, so what is missing is a query rather than somebody's data
            # entry. Named because it is the one change that would let a
            # contractual cycle interrupt anybody — see ``_days_strength``.
            "kind": absence.BUILDABLE,
            "reason": (
                "The credit term is read off this account's settled invoices, "
                "and by construction there are at most two of them here — below "
                "that, how long it actually takes to pay is measured instead. "
                "Its open invoices carry due dates too and are not read, so the "
                "term is graded on less evidence than the book holds, and a "
                "reading resting on it is never strong enough to interrupt."),
        })
    if terms_source == ERP_TERM:
        # Only reachable with the ERP value: an agreed term that was excluded is
        # exactly what leaves this branch as the one that ran, so the two
        # sentences belong together rather than in two entries a reader has to
        # join up.
        out.append({
            "series": "working_capital_supplier_term_history",
            "kind": absence.PERMANENT,
            "reason": (
                "The supplier's payment days come from the ERP record, which "
                "every sync rewrites from the payload — so what it said on the "
                "day this quote was written is not recoverable, and a replay "
                "reads today's value."
                + (f" An agreed term is on record and was not usable here: "
                   f"{term_excluded}, and no earlier version of it is kept."
                   if term_excluded else "")),
        })
    return tuple(out)


# ── internals ────────────────────────────────────────────────────────────────

def _drag_pp(charge: Decimal, price: Decimal) -> Decimal:
    """``charge / price`` — the margin points a per-unit charge takes off.

    ``drivers.PP_QUANTUM`` and ``drivers.WORKING_PRECISION`` rather than local
    constants: those are the platform's answer to how finely a ``_pp`` figure is
    carried and at what precision a margin is divided, and a second pair here
    would agree exactly until somebody moved one of them. The context is pinned
    rather than inherited because a caller that had lowered ``getcontext().prec``
    would otherwise change what this module computes.
    """
    with localcontext() as ctx:
        ctx.prec = drivers.WORKING_PRECISION
        return (charge / price).quantize(drivers.PP_QUANTUM,
                                         rounding=ROUND_HALF_EVEN)


def _refused(reason: str, kind: str, why: str, *,
             settlements: int = 0) -> WorkingCapital:
    """The one shape a refusal takes.

    ``assessed`` is false and every money field is ``None`` on all of them, so a
    caller reading one figure is never handed a number the reading declined to
    assert. ``severity`` and ``strength`` say ``NEGLIGIBLE``/``INSUFFICIENT``
    rather than being left blank — a refusal has no effect to grade and nothing
    to believe, and a missing grade would be read as "not yet computed".
    """
    return WorkingCapital(
        assessed=False, reason=reason,
        funded_days=None, receivable_days=None, supplier_credit_days=None,
        days_source=None, terms_source=None, settlements=settlements,
        rate=None, capital_per_unit=None, capital_at_risk=None,
        charge_per_unit=None, line_charge=None, effect_pp=None,
        severity=drivers.NEGLIGIBLE, strength=INSUFFICIENT,
        days_strength=INSUFFICIENT, cost_strength=INSUFFICIENT,
        surfaces=False, basis=f"{reason}: {why}",
        unavailable=({"series": "working_capital", "kind": kind,
                      "reason": why},),
        cited=(),
    )
