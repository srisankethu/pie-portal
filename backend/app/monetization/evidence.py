"""Replacing assumed inputs with this platform's own rows, where there are any.

The rest of this package runs on archetypes — plausible distributors nobody
measured. This module is the one that touches a database, and its whole job is
to narrow that gap: for a connected organization, four of the model's inputs
are facts already sitting in the schema, and using the fact instead of the
guess is what turns a spreadsheet into a pricing model for *this* customer.

**What it derives, and deliberately what it does not.**

Derived: enquiry volume, quote volume, invoiced order count, invoiced revenue,
and invoiced gross margin. Each is a single aggregate over rows the sync
already writes.

Not derived: quote and order *conversion rates*. Enquiry lines and quotes are
different grains and dividing one by the other produces a ratio that looks like
a conversion and is not; and the defensible win-rate reading — with the guard
for quotes that could never have been won — is ``attribution/``'s, which owns
it. A second implementation here would be the semantic duplication CLAUDE.md §2
is about, and the two would disagree on exactly the organizations where the
answer matters. The gap is named in the output and points at the owner.

**Every unavailable input is UNKNOWN and says why.** Nothing here falls back to
a plausible number: an organization with no synced invoices produces
``annual_revenue=None`` and a gap, not a zero, because a zero would flow into a
value base and out the other side as a confident recommendation to charge
nothing.

Deterministic layer. Never imports ``ai/``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..domain import models
from .customer import CustomerProfile, money
from .segments import ARCHETYPES

#: One year, because everything the model states is annual. Not configurable:
#: a "year" that is 300 days on one deployment makes two organizations'
#: recommendations incomparable for a reason nobody would look for.
WINDOW_DAYS = 365


def _gap(field: str, why: str) -> dict[str, str]:
    return {"field": field, "why": why}


@dataclass(frozen=True)
class ObservedInputs:
    """What the rows say, with a gap for everything they do not say."""

    organization_id: str
    window_days: int
    annual_rfqs: Optional[int]
    quotes_produced: Optional[int]
    invoiced_orders: Optional[int]
    annual_revenue: Optional[Decimal]
    gross_margin: Optional[float]
    #: Share of 12-month revenue that carries a cost, and therefore the share
    #: the margin above actually speaks for. A margin over 8% of the book is a
    #: fact about 8% of the book.
    costed_revenue_share: Optional[float]
    sku_count: Optional[int]
    #: Issued in the window, tax-inclusive, and deliberately not netted off
    #: ``annual_revenue``. See the gap this raises.
    credit_notes_total: Optional[Decimal]
    #: How many connected companies contribute to ``annual_revenue``.
    contributing_connections: int
    #: The measured value the attribution ledger has attributed to PIE over the
    #: same window. This is the one number in the whole package that is neither
    #: assumed nor modelled, and it is the floor a value-based fee can be
    #: argued from without any forecast at all.
    attributed_value: Optional[Decimal]
    gaps: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "window_days": self.window_days,
            "annual_rfqs": self.annual_rfqs,
            "quotes_produced": self.quotes_produced,
            "invoiced_orders": self.invoiced_orders,
            "annual_revenue": None if self.annual_revenue is None else str(self.annual_revenue),
            "gross_margin": self.gross_margin,
            "costed_revenue_share": self.costed_revenue_share,
            "sku_count": self.sku_count,
            "credit_notes_total": (None if self.credit_notes_total is None
                                   else str(self.credit_notes_total)),
            "contributing_connections": self.contributing_connections,
            "attributed_value": (None if self.attributed_value is None
                                 else str(self.attributed_value)),
            "gaps": [dict(g) for g in self.gaps],
        }


def observe(session: Session, organization_id: str) -> ObservedInputs:
    """Six aggregates over one organization's last 365 days."""
    now = clock.now()
    since = now - timedelta(days=WINDOW_DAYS)
    since_date = since.date()
    gaps: list[dict[str, str]] = []

    rfqs = int(session.scalar(
        select(func.count()).select_from(models.InboundLine)
        .where(models.InboundLine.organization_id == organization_id,
               models.InboundLine.received_at >= since)) or 0)
    annual_rfqs: Optional[int] = rfqs or None
    if annual_rfqs is None:
        gaps.append(_gap("annual_rfqs",
                         "no inbound enquiry lines are recorded in the window. "
                         "That is not zero demand — it means enquiries are not "
                         "being captured in this platform yet"))

    quotes = int(session.scalar(
        select(func.count(func.distinct(models.QuoteDecision.quote_id)))
        .where(models.QuoteDecision.organization_id == organization_id,
               models.QuoteDecision.created_at >= since)) or 0)
    quotes_produced: Optional[int] = quotes or None
    if quotes_produced is None:
        gaps.append(_gap("quotes_produced",
                         "no priced quote lines in the window"))

    orders = int(session.scalar(
        select(func.count()).select_from(models.InvoiceDoc)
        .where(models.InvoiceDoc.organization_id == organization_id,
               models.InvoiceDoc.date >= since_date)) or 0)
    invoiced_orders: Optional[int] = orders or None

    revenue = session.scalar(
        select(func.sum(models.SalesTxn.line_revenue))
        .where(models.SalesTxn.organization_id == organization_id,
               models.SalesTxn.date >= since_date))
    annual_revenue = None if revenue is None else money(Decimal(str(revenue)))
    if not annual_revenue or annual_revenue <= 0:
        annual_revenue = None
        gaps.append(_gap("annual_revenue",
                         "no invoiced sales lines are synced for the window. "
                         "Connect the books before pricing from this profile — "
                         "an unconnected organization has no measurable GMV, "
                         "which is not the same as a small one"))
        invoiced_orders = None

    # Margin from the persisted Customer x Item projection rather than from a
    # fresh join: those rows are computed by commercial/ and carry a
    # thresholds_version, so this reads the platform's one answer instead of
    # producing a second one. Numerator and denominator are both restricted to
    # rows carrying gross profit, which is the whole lesson of the "absence of
    # evidence is not a pass" incidents — profit earned on costed revenue over
    # *all* revenue banded a healthy book as poor.
    costed_revenue, costed_gp = session.execute(
        select(func.sum(models.CustomerItemMetric.revenue_12m),
               func.sum(models.CustomerItemMetric.gross_profit_12m))
        .where(models.CustomerItemMetric.organization_id == organization_id,
               models.CustomerItemMetric.revenue_12m.is_not(None),
               models.CustomerItemMetric.gross_profit_12m.is_not(None))).one()
    all_revenue = session.scalar(
        select(func.sum(models.CustomerItemMetric.revenue_12m))
        .where(models.CustomerItemMetric.organization_id == organization_id,
               models.CustomerItemMetric.revenue_12m.is_not(None)))

    gross_margin: Optional[float] = None
    costed_share: Optional[float] = None
    if costed_revenue is not None and Decimal(str(costed_revenue)) > 0:
        gross_margin = round(
            float(Decimal(str(costed_gp)) / Decimal(str(costed_revenue))), 4)
        if all_revenue is not None and Decimal(str(all_revenue)) > 0:
            costed_share = round(
                float(Decimal(str(costed_revenue)) / Decimal(str(all_revenue))), 4)
    else:
        gaps.append(_gap("gross_margin",
                         "no customer-item row carries both revenue and gross "
                         "profit, so no margin can be computed. Run the "
                         "commercial recompute after a full sync"))

    if costed_share is not None and costed_share < 0.5:
        gaps.append(_gap("gross_margin",
                         f"the margin is measured over {costed_share:.0%} of "
                         "12-month revenue — the rest carries no cost. Treat it "
                         "as a reading of that share, not of the book"))

    # Credit notes, reported and deliberately NOT netted. The document total is
    # tax-inclusive and line revenue is pre-tax, so subtracting one from the
    # other over-deducts by exactly the GST — a wrong number in the direction
    # that flatters the customer, which is still a wrong number to invoice on.
    credits = session.scalar(
        select(func.sum(models.CreditNoteDoc.total))
        .where(models.CreditNoteDoc.organization_id == organization_id,
               models.CreditNoteDoc.date >= since_date))
    credit_notes = None if credits is None else money(Decimal(str(credits)))
    if credit_notes and annual_revenue:
        gaps.append(_gap("credit_notes",
                         f"{credit_notes} of credit notes was issued in the "
                         "window and is NOT deducted from the revenue above: "
                         "the credit-note total is tax-inclusive and line "
                         "revenue is pre-tax, so the two are not subtractable. "
                         "The contract must state the basis before this figure "
                         "is billed on"))

    # How many connected companies contribute. More than one means inter-entity
    # sales are counted once per book — the same goods billed twice — and
    # nothing in the schema marks a related party, so this can only be named.
    connections = int(session.scalar(
        select(func.count(func.distinct(models.SalesTxn.connection_id)))
        .where(models.SalesTxn.organization_id == organization_id,
               models.SalesTxn.date >= since_date,
               models.SalesTxn.connection_id.is_not(None))) or 0)
    if connections > 1:
        gaps.append(_gap("annual_revenue",
                         f"{connections} connected companies contribute to "
                         "this figure. Sales between them are counted once in "
                         "each book, so anything they invoice each other is "
                         "billed twice. No related-party marker exists to net "
                         "it — the contract must name the entities"))

    skus = int(session.scalar(
        select(func.count()).select_from(models.Product)
        .where(models.Product.organization_id == organization_id)) or 0)

    attributed: Optional[Decimal] = None
    try:
        from ..attribution import evaluator as attribution_evaluator

        summary = attribution_evaluator.value_summary(
            session, organization_id, days=WINDOW_DAYS)
        raw = summary.get("attributed_value")
        attributed = None if raw is None else money(Decimal(str(raw)))
    except Exception:  # noqa: BLE001 - a missing ledger must not break pricing
        attributed = None
    if attributed is None:
        gaps.append(_gap("attributed_value",
                         "the value ledger has attributed nothing in this "
                         "window. That is UNKNOWN, not ₹0 — it may mean no "
                         "detection run has been recorded"))

    # Quote and order conversion, named as owned elsewhere rather than guessed.
    gaps.append(_gap("quote_conversion",
                     "not derived here. Enquiry lines and quotes are different "
                     "grains, and the defensible win-rate reading belongs to "
                     "attribution/evaluator, which guards the case where no "
                     "decided quote could ever have been won"))
    gaps.append(_gap("order_conversion",
                     "not derivable at all from synced rows. A quote is not "
                     "converted into a sales order — the estimate is sent, the "
                     "order is entered from a customer PO, and nothing joins "
                     "them — so a quote's outcome exists only where a person "
                     "recorded one, which for most of a book nobody has. Read "
                     "it from recorded outcomes and treat the rest as UNKNOWN; "
                     "it is never a measured zero"))
    gaps.append(_gap("pie_touched_revenue",
                     "not computable for the same reason, which is why the "
                     "recommended structure bills the whole connected book "
                     "rather than a PIE-touched subset of it"))
    gaps.append(_gap("pie_rfq_share",
                     "not computable at all, for a reason distinct from the "
                     "grain mismatch above: InboundLine is written only for "
                     "enquiries fed into PIE's own capture flow, so a count of "
                     "it is a PIE-touched total, not the customer's whole "
                     "enquiry volume. There is no synced denominator to divide "
                     "it into, so adoption stays at the archetype's reference "
                     "value here. See monetization.report.adoption_sensitivity "
                     "for what that costs in pricing accuracy"))

    return ObservedInputs(
        organization_id=organization_id, window_days=WINDOW_DAYS,
        annual_rfqs=annual_rfqs, quotes_produced=quotes_produced,
        invoiced_orders=invoiced_orders, annual_revenue=annual_revenue,
        gross_margin=gross_margin, costed_revenue_share=costed_share,
        sku_count=skus or None, attributed_value=attributed,
        credit_notes_total=credit_notes, contributing_connections=connections,
        gaps=tuple(gaps))


def ground(observed: ObservedInputs,
           archetype: Optional[CustomerProfile] = None,
           ) -> tuple[CustomerProfile, dict[str, str]]:
    """An archetype overlaid with whatever the rows actually say.

    Returns the profile *and* a per-field provenance map, because a profile that
    is half measured and half assumed is only useful if a reader can tell which
    half is which. The caller renders the map beside the number; it is the same
    discipline the quote screen applies to a cost that came from a bill versus
    one that came from a default.

    The archetype defaults to the size band the observed GMV falls in, not to a
    fixed one — pricing a ₹900 Cr distributor against the small profile's
    conversion rates would be worse than not grounding at all.
    """
    base = archetype or _closest_archetype(observed.annual_revenue)
    source = {name: "archetype" for name in
              ("annual_rfqs", "pie_rfq_share", "quote_conversion",
               "order_conversion", "average_order_value", "gross_margin",
               "sales_engineers", "cost_per_employee_year",
               "rfq_processing_minutes", "quotation_minutes", "sku_count",
               "erp_rows_millions")}

    changes: dict[str, Any] = {}
    if observed.annual_rfqs:
        changes["annual_rfqs"] = observed.annual_rfqs
        source["annual_rfqs"] = "measured"
    if observed.gross_margin is not None:
        changes["gross_margin"] = observed.gross_margin
        source["gross_margin"] = "measured"
    if observed.sku_count:
        changes["sku_count"] = observed.sku_count
        source["sku_count"] = "measured"

    profile = replace(base, **changes) if changes else base

    # Order value last, and only from GMV: it is the input that reconciles the
    # funnel to the invoiced book, so it has to be solved *after* every other
    # measured field has been applied or it would reconcile to the wrong funnel.
    if observed.annual_revenue is not None and observed.annual_revenue > 0:
        profile = profile.with_annual_revenue(observed.annual_revenue)
        source["average_order_value"] = "solved from measured GMV"

    return profile, source


def _closest_archetype(gmv: Optional[Decimal]) -> CustomerProfile:
    """The size band an observed book falls in. Mid when nothing is observed.

    Mid rather than small: an unknown organization defaulting to the smallest
    profile would produce the lowest fee, and a default that always errs toward
    charging less is not neutrality, it is a discount nobody decided to give.
    """
    if gmv is None or gmv <= 0:
        return ARCHETYPES["mid"]
    ordered = [ARCHETYPES[k] for k in ("small", "mid", "large")]
    return min(ordered, key=lambda p: abs(p.annual_revenue - gmv))
