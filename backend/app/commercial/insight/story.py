"""The briefing: what changed, why, and what to do — assembled, not narrated.

This is the homepage's data. It is deliberately *composed* from the other
modules rather than computing anything of its own, so the number on the front
page and the number on the screen you click through to cannot disagree.

The shape is a short ordered list of **beats**. Each beat carries all three
answers or it is not emitted:

  ``change``  the movement, with both periods, so it can be checked
  ``cause``   the decomposition that explains it, from persisted rows
  ``action``  where to go and what is waiting there

A beat missing a cause is not padded with a plausible one. The platform either
has the decomposition or it says the movement is unexplained — which is itself
useful, and is the honest alternative to a generated sentence.

No interpretation happens here. This module is in ``commercial/`` and never
imports ``ai/``; phrasing a beat into prose, if that is ever wanted, belongs at
the ``decisions/`` seam where the boundary already exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Beats are ordered by what a person should deal with first, not by size.
# Money already lost outranks money at risk, which outranks money available.
LOST_REVENUE = "LOST_REVENUE"
MARGIN_EROSION = "MARGIN_EROSION"
CONCENTRATION = "CONCENTRATION"
OPPORTUNITY = "OPPORTUNITY"
GROWTH = "GROWTH"
QUIET = "QUIET"

_ORDER = [LOST_REVENUE, MARGIN_EROSION, CONCENTRATION, QUIET, OPPORTUNITY, GROWTH]


@dataclass
class Beat:
    kind: str
    headline: str
    change: dict[str, Any]
    cause: Optional[dict[str, Any]]
    action: dict[str, Any]
    severity: str = "INFO"
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "headline": self.headline,
                "change": self.change, "cause": self.cause,
                "action": self.action, "severity": self.severity,
                "evidence": self.evidence}


def _money(v: float) -> float:
    """Rounded, for the machine-readable ``change`` payload."""
    return round(v, 2)


def _spell(v: float, currency: str) -> str:
    """A headline amount, with its currency symbol.

    Headlines used to be built with a bare ``:,.0f``, which rendered
    "241,141 of revenue stopped" — a number with no unit, on the first screen
    of a commercial product. ``commercial.money`` already knows how to spell an
    amount in a tenant's currency, including the lakh grouping, so it is used
    here rather than a second format string.
    """
    from ..money import money as _fmt
    return _fmt(v, currency)


def build(*, flow: dict, lost: dict, radar: list[dict], radar_totals: dict,
          dormant: dict, concentration: dict, currency: str,
          restricted_ok: bool) -> dict:
    """Assemble the beats. Order is fixed; presence is earned.

    ``restricted_ok`` says whether this reader may open the manager-scoped
    screens. It is required rather than defaulted: the whole defect it fixes was
    a beat that assumed everyone could follow it, and a default would let the
    next caller inherit that assumption silently.

    The radar-derived beats never reach a salesperson anyway — the caller passes
    an empty ``radar``, so they are not built. The lost-revenue beat is different:
    it is computed from ``lost``, which is revenue rather than margin and is
    served to every role, so it *is* built for a salesperson and used to hand
    them a button to a screen that answers 403.
    """
    beats: list[Beat] = []

    # ── what was lost, and why ───────────────────────────────────────────────
    total_lost = lost.get("total_lost", 0.0)
    if total_lost > 0 and lost.get("causes"):
        top = lost["causes"][0]
        named = [c for c in lost["causes"] if c["cause"] != "UNEXPLAINED"]
        beats.append(Beat(
            kind=LOST_REVENUE, severity="HIGH",
            headline=f"{_spell(total_lost, currency)} of revenue stopped",
            change={"amount": _money(total_lost),
                    "comparison": lost.get("comparison"),
                    "direction": "down"},
            cause={
                "primary": top["cause"],
                "primary_amount": top["amount"],
                "breakdown": [{"cause": c["cause"], "amount": c["amount"],
                               "count": c["count"]} for c in lost["causes"]],
                "explained_share": (
                    round(sum(c["amount"] for c in named) / total_lost, 2)
                    if total_lost else None),
            },
            # The headline and the causes stay — a salesperson is entitled to
            # know their revenue stopped, and the breakdown is in this beat
            # already. What changes is where the button goes. `/lost-revenue` is
            # `require_manager_or_owner`, so for this role the primary
            # call-to-action on the first screen they see led to "This did not
            # load. Manager or owner role required" — which `PlatformApp` itself
            # calls out as the thing that teaches people the product is broken.
            # `journey` is the sibling beat's destination and opens for everyone.
            action=({"label": "Open the lost-revenue breakdown",
                     "route": "lost-revenue",
                     "count": sum(c["count"] for c in lost["causes"])}
                    if restricted_ok else
                    {"label": "See revenue by customer",
                     "route": "journey",
                     "count": sum(c["count"] for c in lost["causes"])}),
            evidence=top.get("customers", [])[:3]))

    # ── margin, from the radar's own classification ──────────────────────────
    eroding = [o for o in radar if o["kind"] in ("MARGIN_EROSION", "COST_NOT_PASSED")]
    confident_eroding = [o for o in eroding if o["confidence"] == "SUFFICIENT"]
    if confident_eroding:
        amount = sum(o["impact"] for o in confident_eroding)
        cost_driven = [o for o in confident_eroding if o["kind"] == "COST_NOT_PASSED"]
        beats.append(Beat(
            kind=MARGIN_EROSION, severity="HIGH" if amount > 0 else "INFO",
            headline=f"{_spell(amount, currency)} a year in eroding margin",
            change={"amount": _money(amount),
                    "relationships": len(confident_eroding),
                    "direction": "down"},
            cause={
                "primary": ("COST_NOT_PASSED" if len(cost_driven) > len(confident_eroding) / 2
                            else "MARGIN_EROSION"),
                "cost_driven": len(cost_driven),
                "price_driven": len(confident_eroding) - len(cost_driven),
                "note": ("Cost-driven erosion is a supplier conversation; the rest "
                         "is a pricing one. They are counted separately because "
                         "they are different jobs."),
            },
            action={"label": "Review the eroding relationships",
                    "route": "opportunities",
                    "count": len(confident_eroding)},
            evidence=confident_eroding[:3]))

    # ── concentration ────────────────────────────────────────────────────────
    top_share = concentration.get("top_customer_share")
    if top_share is not None and top_share >= 0.25:
        beats.append(Beat(
            kind=CONCENTRATION, severity="MEDIUM",
            headline=(f"{concentration['top_customer_label']} is "
                      f"{top_share:.0%} of revenue"),
            change={"share": top_share,
                    "top_five_share": concentration.get("top_five_share")},
            cause={"primary": "REVENUE_CONCENTRATION",
                   "note": "Not a change — a standing exposure. Shown because a "
                           "single customer at this share makes every other "
                           "number on this page conditional on one relationship."},
            action={"label": "See revenue by customer", "route": "journey",
                    "count": concentration.get("customer_count", 0)},
            evidence=concentration.get("top", [])[:3]))

    # ── customers gone quiet ─────────────────────────────────────────────────
    if dormant.get("count"):
        at_risk = sum(c["lifetime_revenue"] for c in dormant.get("customers", []))
        beats.append(Beat(
            kind=QUIET, severity="MEDIUM",
            headline=f"{dormant['count']} customers have gone quiet",
            change={"count": dormant["count"],
                    "months": dormant.get("threshold_months"),
                    # Lifetime revenue of the quiet ones, so the count carries a
                    # size. "Twelve customers" and "twelve customers worth ₹40L"
                    # are different priorities.
                    "lifetime_revenue_quiet": _money(at_risk)},
            cause={"primary": "NO_RECENT_ORDERS",
                   "note": "An observation about order dates, not a churn "
                           "prediction — the platform has no basis for one."},
            action={"label": "Review and plan win-backs", "route": "journey",
                    "count": dormant["count"],
                    "simulate": "CUSTOMER_RECOVERY"},
            evidence=dormant.get("customers", [])[:3]))

    # ── what is available ────────────────────────────────────────────────────
    confident = radar_totals.get("confident_impact", 0.0)
    if confident > 0:
        beats.append(Beat(
            kind=OPPORTUNITY, severity="INFO",
            headline=f"{_spell(confident, currency)} recoverable with solid evidence",
            change={"amount": _money(confident),
                    "count": radar_totals.get("count", 0)},
            cause={"primary": "PRICED_BELOW_REFERENCE",
                   "breakdown": radar_totals.get("by_kind", {}),
                   "note": "Only relationships with sufficient evidence are in "
                           "this figure; weaker ones are listed but not totalled."},
            action={"label": "Open the opportunity radar", "route": "opportunities",
                    "count": radar_totals.get("count", 0),
                    "simulate": "MARGIN_FLOOR"},
            evidence=radar[:3]))

    # ── growth, last, because good news is not an action ─────────────────────
    grown = next((b for b in flow.get("buckets", []) if b["kind"] == "GROWN"), None)
    new = next((b for b in flow.get("buckets", []) if b["kind"] == "NEW"), None)
    gained = (grown["amount"] if grown else 0.0) + (new["amount"] if new else 0.0)
    if gained > 0:
        beats.append(Beat(
            kind=GROWTH, severity="GOOD",
            headline=f"{_spell(gained, currency)} gained from growth and new customers",
            change={"amount": _money(gained),
                    "grown": grown["amount"] if grown else 0.0,
                    "new": new["amount"] if new else 0.0},
            cause={"primary": "CUSTOMER_GROWTH",
                   "note": "Shown against the losses above, not on its own — the "
                           "net is what moved."},
            action={"label": "See the revenue waterfall", "route": "revenue-flow",
                    "count": (grown["customers"] if grown else 0)
                             + (new["customers"] if new else 0)},
            evidence=((grown or {}).get("top", []) + (new or {}).get("top", []))[:3]))

    beats.sort(key=lambda b: _ORDER.index(b.kind))
    return {
        "currency": currency,
        "period": flow.get("comparison"),
        "net_change": flow.get("delta"),
        "net_change_pct": flow.get("pct"),
        "beats": [b.to_dict() for b in beats],
        "empty_reason": (None if beats else
                         "There is not enough comparable history yet to say what "
                         "changed. Run a sync covering at least two periods."),
    }


def concentration_of(flow_moves: list[dict], names: dict[str, str]) -> dict:
    """Revenue share held by the largest customers, from the current period."""
    current = [(m["customer_id"], m["current"]) for m in flow_moves if m["current"] > 0]
    total = sum(v for _, v in current)
    if not total:
        return {"top_customer_share": None, "customer_count": 0, "top": []}
    current.sort(key=lambda kv: kv[1], reverse=True)
    top = [{"customer_id": cid, "label": names.get(cid, cid),
            "revenue": round(v, 2), "share": round(v / total, 4)}
           for cid, v in current[:5]]
    return {
        "top_customer_share": top[0]["share"] if top else None,
        "top_customer_label": top[0]["label"] if top else None,
        "top_five_share": round(sum(t["share"] for t in top), 4),
        "customer_count": len(current),
        "total_revenue": round(total, 2),
        "top": top,
    }
