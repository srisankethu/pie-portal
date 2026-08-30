"""The monetization console — PIE's own pricing model, over HTTP.

**Not a tenant surface.** Everything behind this router is PIE's commercial
position: what it costs to serve a customer, what share of created value it
intends to capture, and what each segment should be charged. A distributor who
could read it would be negotiating with the vendor's own reservation price on
screen, which is a worse disclosure than any of the cost-and-margin leaks
CLAUDE.md §1 catalogues — those leak one tenant's numbers to one tenant's own
staff; this would leak PIE's to a counterparty.

So the gate is a third question, orthogonal to the two this codebase already
answers. ``authz`` says who the person is. ``memberships`` says which
organizations they may open. Neither can say *is this person PIE staff*, because
that is not a fact about any tenant — there is no row for it inside a workspace
and there must not be one. The answer is a deployment-level allowlist,
``settings.PIE_OPERATOR_EMAILS``, and it composes with the ordinary session
rather than inventing a second credential path: an operator signs in normally
and is additionally recognised.

**Empty allowlist means closed.** Same reasoning as the metrics scrape token in
``internal.py``, and the refusal is byte-identical whether the list is empty or
the caller is simply absent from it — a 403 that distinguishes the two answers
"does this deployment have a pricing console" to anyone who asks.

Thin, per CLAUDE.md §3: every number here is computed in ``monetization/`` and
this module maps HTTP onto it. A router that added up a fee would be the second
definition of a price.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..authz import Principal, current_principal
from ..config import settings
from ..db import get_session
from ..monetization import (ARCHETYPES, IMPACTS, CustomerProfile, PieImpact,
                            build_waterfall, evidence, load_parameters)
from ..monetization import report as monetization_report
from ..monetization import scorecard as monetization_scorecard
from ..monetization.config import PROVENANCE, validation_list
from ..monetization.unitecon import floor_price, unit_economics

router = APIRouter(prefix="/api/v1/monetization", tags=["monetization"])


def _operators() -> frozenset[str]:
    return frozenset(
        part.strip().casefold()
        for part in (settings.PIE_OPERATOR_EMAILS or "").split(",")
        if part.strip())


def require_operator(principal: Principal = Depends(current_principal)) -> Principal:
    """This request is being made by PIE, not by a customer of PIE.

    Deliberately not a ``Role``: a role lives inside a tenant, and an owner of
    any workspace would then be able to grant it to themselves. The allowlist is
    outside every tenant, which is the only place a fact about PIE can live.
    """
    allowed = _operators()
    email = (principal.email or "").strip().casefold()
    if not allowed or not email or email not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "This surface is not available.")
    return principal


class ProfileIn(BaseModel):
    """A customer's economics as typed into the calculator.

    Money arrives as ``Decimal``. A JSON number is accepted and a JSON *string*
    is exact — the calculator sends strings for that reason, and the difference
    matters at the fourth decimal of a crore.
    """

    name: str = "Custom customer"
    annual_rfqs: int = Field(ge=0)
    pie_rfq_share: float = Field(ge=0.0, le=1.0)
    quote_conversion: float = Field(ge=0.0, le=1.0)
    order_conversion: float = Field(ge=0.0, le=1.0)
    average_order_value: Decimal = Field(ge=0)
    gross_margin: float = Field(ge=0.0, le=1.0)
    sales_engineers: int = Field(ge=0)
    cost_per_employee_year: Decimal = Field(ge=0)
    rfq_processing_minutes: float = Field(ge=0.0)
    quotation_minutes: float = Field(ge=0.0)
    sku_count: int = Field(default=25_000, ge=0)
    erp_rows_millions: float = Field(default=1.0, ge=0.0)
    #: When set, order value is solved so the funnel reconciles to this GMV.
    annual_gmv: Optional[Decimal] = None

    def to_profile(self) -> CustomerProfile:
        profile = CustomerProfile(
            name=self.name, annual_rfqs=self.annual_rfqs,
            pie_rfq_share=self.pie_rfq_share,
            quote_conversion=self.quote_conversion,
            order_conversion=self.order_conversion,
            average_order_value=self.average_order_value,
            gross_margin=self.gross_margin,
            sales_engineers=self.sales_engineers,
            cost_per_employee_year=self.cost_per_employee_year,
            rfq_processing_minutes=self.rfq_processing_minutes,
            quotation_minutes=self.quotation_minutes,
            sku_count=self.sku_count,
            erp_rows_millions=self.erp_rows_millions)
        if self.annual_gmv is not None and self.annual_gmv > 0:
            profile = profile.with_annual_gmv(self.annual_gmv)
        return profile


class ImpactIn(BaseModel):
    quote_conversion_uplift_pp: float = Field(default=0.0, ge=-1.0, le=1.0)
    order_conversion_uplift_pp: float = Field(default=0.0, ge=-1.0, le=1.0)
    aov_uplift: float = Field(default=0.0, ge=-1.0, le=10.0)
    gross_margin_uplift_pp: float = Field(default=0.0, ge=-1.0, le=1.0)
    procurement_saving_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    minutes_saved_per_rfq: float = Field(default=0.0, ge=0.0)
    minutes_saved_per_quote: float = Field(default=0.0, ge=0.0)
    response_time_improvement: float = Field(default=0.0, ge=0.0, le=1.0)
    substitution_opportunity_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    substitution_success_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    def to_impact(self) -> PieImpact:
        return PieImpact(**self.model_dump())


class CalculateIn(BaseModel):
    profile: ProfileIn
    impact: ImpactIn


@router.get("/access")
def access(principal: Principal = Depends(current_principal)) -> dict[str, bool]:
    """May this identity open the console? Always 200, never a refusal.

    A client has to know whether to draw the door, and the honest way to answer
    that is a question rather than a probe: without this the browser would have
    to call a real endpoint and read a 403, which fills error monitoring with
    refusals that are not failures and teaches everyone to ignore them.

    It leaks nothing the caller does not already have. ``false`` is what every
    tenant sees, including on a deployment where the allowlist is empty, so the
    response still does not answer "does this deployment have a console".
    """
    allowed = _operators()
    email = (principal.email or "").strip().casefold()
    return {"operator": bool(allowed and email and email in allowed)}


@router.get("/parameters")
def parameters(_: Principal = Depends(require_operator)) -> dict[str, Any]:
    """PIE's assumption set, its version, and what still needs validating."""
    params = load_parameters()
    return {
        "version": params.version,
        "currency": params.currency,
        "values": {name: (str(value) if isinstance(value, Decimal) else value)
                   for name, value in params.__dict__.items()
                   if not name.startswith("_")},
        "provenance": {name: {"grade": grade.value, "why": note}
                       for name, (grade, note) in sorted(PROVENANCE.items())},
        "needs_validation": validation_list(params),
    }


@router.get("/segments")
def segments(_: Principal = Depends(require_operator)) -> dict[str, Any]:
    """The reference customers and impact sets, for seeding the calculator."""
    return {
        "archetypes": {key: profile.as_dict()
                       for key, profile in ARCHETYPES.items()},
        "impacts": {key: impact.as_dict() for key, impact in IMPACTS.items()},
    }


@router.get("/scorecard")
def metric_scorecard(_: Principal = Depends(require_operator)) -> dict[str, Any]:
    """Every pricing metric, scored and ranked, with its incentive register."""
    return monetization_scorecard.full()


@router.post("/calculate")
def calculate(body: CalculateIn,
              _: Principal = Depends(require_operator)) -> dict[str, Any]:
    """One customer, every pricing model, side by side. The calculator's engine.

    Returns the waterfall, the three rate ladders, the five hybrids, every
    single-metric model sized to a common target, the recommendation and PIE's
    own unit economics at that recommendation — one round trip, because a
    calculator that fetched each panel separately would show a reader six
    answers computed from six slightly different states.
    """
    params = load_parameters()
    profile = body.profile.to_profile()
    wf = build_waterfall(profile, body.impact.to_impact(), params)
    recommendation = monetization_report.recommend(wf, params)
    fee = Decimal(recommendation["evaluation"]["fee"]["annual_fee"])
    return {
        "parameters_version": params.version,
        "waterfall": wf.as_dict(),
        "margin_hypothesis": monetization_report.margin_hypothesis(wf, params),
        "transaction_ladder": monetization_report.transaction_ladder(wf, params),
        "subscription_ladder": monetization_report.subscription_ladder(wf, params),
        "hybrids": monetization_report.hybrid_structures(wf, params),
        "all_strategies": monetization_report.all_strategies(wf, params),
        "recommendation": recommendation,
        "cost_floor": str(floor_price(profile, params)),
        "pie_unit_economics": unit_economics(
            profile, fee, params, orders=wf.covered_with_pie.orders).as_dict(),
    }


@router.get("/report")
def full_report(impact: str = Query(default="base"),
                _: Principal = Depends(require_operator)) -> dict[str, Any]:
    """Every segment, the scorecard, five years, elasticity and the experiments.

    Large by design — it is the whole analysis in one document, and the thing a
    pricing decision is actually made from. The calculator uses ``/calculate``
    for the interactive path; this is the one you read.
    """
    if impact not in IMPACTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"unknown impact set: {impact}")
    return monetization_report.full_report(load_parameters(), impact_key=impact)


@router.get("/observed/{organization_id}")
def observed(organization_id: str,
             _: Principal = Depends(require_operator),
             session: Session = Depends(get_session)) -> dict[str, Any]:
    """What one organization's own rows say, and the profile they ground.

    The gaps are the point: an organization with no connected books produces a
    profile that is entirely archetype, and the response says so field by field
    rather than presenting a guess as a measurement.
    """
    inputs = evidence.observe(session, organization_id)
    profile, source = evidence.ground(inputs)
    return {"observed": inputs.as_dict(),
            "grounded_profile": profile.as_dict(),
            "field_source": source}
