"""Per-organization overrides for the commercial thresholds.

The margin policy was read-only because a threshold quietly editable from a
settings screen is one nobody can reproduce a past number against. That
objection is answered by the version hash rather than by refusing to edit:
``CommercialThresholds.version`` is a content hash of every field, so an edited
policy produces a different version, and every metric row, signal and quote
snapshot already records the version that produced it. Change the floor and old
rows still say which floor they were judged against.

What is editable is deliberately narrower than the dataclass. Business policy —
what margin we want, where the floors sit, how quantity bands are cut — belongs
to the owner. Analysis internals — window lengths, evidence floors, peer
recency — are not preferences; moving them silently changes what "eroding"
means, and they stay in environment configuration where a change is a
deployment with a record.

The ladder is validated on write, because a policy where the approval floor
sits above the target margin makes every quote simultaneously below floor and
above target, and the screens would argue with each other forever.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import clock
from ..domain import models
from .config import CommercialThresholds, load_commercial_thresholds

#: Fields an owner may set. Everything else is environment configuration.
EDITABLE: tuple[str, ...] = (
    "target_margin_default",
    "target_margin_by_family",
    "min_margin",
    "margin_floor",
    "sales_discretion_band",
    "quantity_band_edges",
    "min_quote_exception_impact",
    "min_material_gap",
    "min_margin_deterioration_pp",
    "queue_margin_drop_pp",
    "price_rounding_increment",
    "carrying_cost_annual_pct",
    "carrying_rate_is_published",
    "dead_stock_days",
    "slow_stock_days",
    "receivable_exposure_share",
    "supplier_spend_share",
    # Statutory timing. Editable because all four are facts about *this*
    # business rather than about the analysis: the limits vary with the statute,
    # the tax rate varies by entity and regime, and whether our own turnover
    # crossed the 194Q gate is something only the owner can confirm.
    "msme_default_days",
    "msme_max_agreed_days",
    "msme_watch_horizon_days",
    "effective_tax_rate",
    "s194q_party_threshold",
    "s194q_org_gate_met",
)
#: Incentive rates are deliberately absent. They are not org policy edited from
#: a settings screen — they are the published mechanism parameters in
#: ``incentive_engine/config/parameters.yaml``, changed at most annually and
#: before the year starts. A rate a salesperson can watch move mid-year is a
#: discretionary bonus wearing a formula costume, and ``load_for_org`` already
#: ignores a stored override whose field is not listed above, so an
#: organization that saved one under the old scheme degrades rather than breaks.

#: Human labels + why each one matters, surfaced as tooltips in Settings.
FIELD_HELP: dict[str, tuple[str, str]] = {
    "target_margin_default": (
        "Target margin",
        "What a line should earn when nothing else argues otherwise. Used to "
        "compute the target price a quote is compared against; it does not block "
        "anything on its own."),
    "target_margin_by_family": (
        "Target margin by tool family",
        "Overrides the default for a specific family. An insert and a holder do "
        "not carry the same margin, and one blended target flatters the cheap "
        "line while punishing the expensive one."),
    "min_margin": (
        "Approval floor",
        "Below this a quote line cannot be sent without a manager or owner "
        "approving it. This is the number with teeth."),
    "margin_floor": (
        "Review floor",
        "Below this a line is flagged for review but can still go out. Sits "
        "between the target and the approval floor; a warning nobody can act on "
        "is noise, so keep the gap meaningful."),
    "sales_discretion_band": (
        "Sales discretion",
        "How far off the recommended price a salesperson may move without "
        "asking. Widen it and fewer approvals are raised; narrow it and the "
        "queue fills."),
    "quantity_band_edges": (
        "Quantity bands",
        "Where one price bracket ends and the next begins. The same item at 5 "
        "pieces and 500 is a different commercial question, and comparing a bulk "
        "line against an all-quantities average makes every bulk line look "
        "under-priced."),
    "min_quote_exception_impact": (
        "Exception floor",
        "Advisory warnings worth less than this are suppressed. Policy breaches "
        "— below cost, below the approval floor — are never suppressed, whatever "
        "this is set to."),
    "min_material_gap": (
        "Material gap",
        "A margin gap smaller than this is real but not worth anyone's "
        "afternoon. Ranking by percentage instead of by money is how teams end "
        "up working trivial accounts first."),
    "min_margin_deterioration_pp": (
        "Erosion threshold — commercial screens",
        "How far margin must fall, in percentage points, before the platform "
        "calls it erosion rather than noise from mix and freight. This one "
        "governs what the commercial screens flag. It does not decide what "
        "reaches anybody's queue — that is the threshold below."),
    "queue_margin_drop_pp": (
        "Erosion threshold — decision queue",
        "How far margin must fall before a deterioration is routed to someone as "
        "a decision. Usually set wider than the screen threshold: a screen can "
        "afford to be sensitive, a queue cannot. Raise this to quieten the "
        "queue without desensitising the screens."),
    "carrying_cost_annual_pct": (
        "Annual carrying cost",
        "What a rupee of stock costs to hold for a year — interest, warehouse, "
        "insurance, obsolescence — as a fraction. Everything the Stock screen "
        "says about cash drain is this number times the value on the shelf, so "
        "it is worth arguing about once rather than guessing at monthly. It is "
        "never shown to a salesperson: with it, the drain figure they do see "
        "divides back into the purchase cost."),
    "carrying_rate_is_published": (
        "The carrying rate is public",
        "Has the rate above been written down anywhere a salesperson could "
        "read it — a policy document, a training deck, an email? Leave it off "
        "while it is confidential. Turn it on and the Monthly Cash Drain "
        "column comes off the salesperson's Stock screen the same moment, "
        "because the drain is quantity x cost x rate / 12 and the quantity is "
        "on every row: anyone holding the rate can divide back to the purchase "
        "cost of everything in the catalogue. An owner keeps the column either "
        "way."),
    "dead_stock_days": (
        "Dead after",
        "Days with no sale before a line is called dead rather than slow. A "
        "distributor's specialist tooling is not dead in month two, and a "
        "threshold set too low turns the whole catalogue red on day one."),
    "receivable_exposure_share": (
        "Credit exposure threshold",
        "What share of everything currently owed may sit with one customer "
        "before it is raised as a standing exposure. It is not about lateness — "
        "a customer at this share is flagged even when every invoice is "
        "current, because if that one relationship stops paying, that share of "
        "the book stops with it."),
    "supplier_spend_share": (
        "Supplier concentration threshold",
        "What share of everything you buy may sit with one supplier before it "
        "is raised. Usually higher than the customer credit threshold — buying "
        "most of your stock from one principal is normal in distribution, and "
        "a threshold set at the customer's level would flag your best "
        "relationship every week."),
    "slow_stock_days": (
        "Slow after",
        "Days with no sale before a line is marked slow-moving. Sits below the "
        "dead threshold; the gap between them is the window where a line is "
        "worth pushing rather than discounting."),
    "price_rounding_increment": (
        "Price rounding",
        "Recommended prices are rounded to a multiple of this, because quoting "
        "an exact 1,847.31 invites an argument about the 31. Scale it to the "
        "currency — a tick that is sensible on a ₹2,000 insert is a 5% "
        "distortion on a $100 one. Set 0 to quote the unrounded number."),
    "msme_default_days": (
        "MSME limit without a written agreement",
        "How long you have to pay a registered micro or small supplier when no "
        "written agreement is on record. Fifteen days under the MSMED Act, and "
        "this is the limit that applies to most small suppliers — a payment "
        "term picked from a dropdown in your books is not a written agreement."),
    "msme_max_agreed_days": (
        "MSME limit with a written agreement",
        "The longest a written agreement with a micro or small supplier can "
        "push the payment deadline. Forty-five days is the statutory ceiling; "
        "an agreement above it is capped to this when a deadline is computed, "
        "and the watchlist says it was capped."),
    "msme_watch_horizon_days": (
        "Watchlist horizon",
        "How far ahead the MSME watchlist looks. Bills whose deadline has "
        "already passed are always listed, however old — a deadline that has "
        "gone by does not stop mattering."),
    "effective_tax_rate": (
        "Effective tax rate",
        "Used only to estimate what a disallowed deduction costs. Leave it "
        "empty if it has not been decided: the watchlist still reports the "
        "deadline and the amount at risk, and simply shows no cost estimate "
        "rather than one computed from a guess. Note the estimate is a year's "
        "financing cost on tax paid early, not the tax itself — the deduction "
        "comes back in the year the supplier is actually paid."),
    "s194q_party_threshold": (
        "194Q threshold per supplier",
        "Purchases from one supplier in a financial year beyond which tax has "
        "to be deducted. Applies only if the turnover gate below is confirmed."),
    "s194q_org_gate_met": (
        "194Q applies to this entity",
        "Turn on only if this entity's own turnover exceeded the statutory "
        "limit in the previous financial year. That figure is not in this "
        "platform, so nothing is asserted until somebody confirms it here — "
        "the crossing list stays empty and says why."),
}


#: Fields that are not ratios. Kept beside ``_coerce`` rather than inferred
#: from the dataclass, because inferring it would silently start coercing any
#: future field whose annotation happened to match.
#:
#: ``_kind`` reads these too, so what the screen renders and what the server
#: parses come from one list. They were separate for one commit and the screen
#: showed "36500 %" for a 365-day threshold.
_BOOLEAN = frozenset({"carrying_rate_is_published", "s194q_org_gate_met"})
_DAY_COUNTS = frozenset({"dead_stock_days", "slow_stock_days",
                         "msme_default_days", "msme_max_agreed_days",
                         "msme_watch_horizon_days"})
#: Fields whose *absence* is a meaningful answer, so clearing one has to be
#: possible. Everything else coerces a blank to a number, which for these would
#: silently invent the value the field exists to withhold.
_NULLABLE_RATES = frozenset({"effective_tax_rate"})


class PolicyError(ValueError):
    """A policy that would make the screens contradict each other."""


def _coerce(field: str, value: Any) -> Any:
    """Normalize an incoming value into the shape the dataclass expects."""
    if field == "target_margin_by_family":
        if isinstance(value, dict):
            pairs = [(str(k), float(v)) for k, v in value.items()]
        else:
            pairs = [(str(a), float(b)) for a, b in (value or [])]
        return tuple(sorted(pairs))
    if field == "quantity_band_edges":
        edges = sorted({int(v) for v in (value or []) if int(v) > 0})
        return tuple(edges)
    if field in _BOOLEAN:
        # A checkbox arrives as a bool from the client and as a string from a
        # form post or a seeded fixture. ``float("true")`` raises and
        # ``bool("false")`` is True, so both wrong answers are available here.
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if field in _DAY_COUNTS:
        return int(value)
    if field in _NULLABLE_RATES:
        # An owner who has not set their tax rate, or who clears it again, must
        # end up with None rather than 0.0. A zero rate would report the cost
        # of a disallowance as nothing at all — the benign default this
        # codebase keeps finding in its own past.
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return float(value)
    return float(value)


def validate(th: CommercialThresholds) -> None:
    """Refuse a policy that cannot be satisfied.

    The three floors form a ladder: approval ≤ review ≤ target. Out of order,
    a price can be under the review floor while clearing the approval floor it
    is supposed to sit above, and the quote screen contradicts itself.
    """
    for name in ("target_margin_default", "min_margin", "margin_floor",
                 "sales_discretion_band"):
        value = getattr(th, name)
        if not (0 <= value < 1):
            raise PolicyError(
                f"{name.replace('_', ' ')} must be a fraction between 0 and 1 "
                f"(0.15 is 15%), got {value}")

    if th.min_margin > th.margin_floor:
        raise PolicyError(
            f"The approval floor ({th.min_margin:.0%}) cannot sit above the "
            f"review floor ({th.margin_floor:.0%}) — a line would be flagged for "
            f"review and cleared for sending at the same time.")
    if th.margin_floor > th.target_margin_default:
        raise PolicyError(
            f"The review floor ({th.margin_floor:.0%}) cannot sit above the "
            f"target margin ({th.target_margin_default:.0%}) — every quote at "
            f"target would be flagged.")

    for family, margin in th.target_margin_by_family:
        if not (0 <= margin < 1):
            raise PolicyError(f"Target margin for {family} must be between 0 and 1")
        if margin < th.min_margin:
            raise PolicyError(
                f"Target margin for {family} ({margin:.0%}) is below the approval "
                f"floor ({th.min_margin:.0%}) — every line in that family would "
                f"need approval.")

    if not th.quantity_band_edges:
        raise PolicyError("At least one quantity band edge is required")
    if len(th.quantity_band_edges) > 8:
        raise PolicyError("More than eight quantity bands is more than anyone reads")

    for name in ("min_quote_exception_impact", "min_material_gap",
                 "price_rounding_increment"):
        if getattr(th, name) < 0:
            raise PolicyError(f"{name.replace('_', ' ')} cannot be negative")
    if not (0 <= th.min_margin_deterioration_pp < 1):
        raise PolicyError("Erosion threshold must be between 0 and 1 (0.03 is 3 points)")

    # ── statutory timing ────────────────────────────────────────────────────
    for name in ("msme_default_days", "msme_max_agreed_days",
                 "msme_watch_horizon_days"):
        if getattr(th, name) < 0:
            raise PolicyError(f"{name.replace('_', ' ')} cannot be negative — a "
                              f"bill is not overdue before it is raised")
    if th.msme_default_days > th.msme_max_agreed_days:
        raise PolicyError(
            f"The default limit ({th.msme_default_days} days) cannot exceed the "
            f"agreed maximum ({th.msme_max_agreed_days} days) — a supplier with "
            f"no written agreement would get longer than one with an agreement, "
            f"which inverts the rule.")
    # None is the honest state and stays allowed; a rate that is set has to be
    # a rate. Zero is refused rather than accepted as "no tax": an owner who
    # means "I have not decided" clears the field.
    if th.effective_tax_rate is not None and not (0 < th.effective_tax_rate < 1):
        raise PolicyError(
            f"The effective tax rate must be a fraction between 0 and 1 "
            f"(0.25 is 25%), or left unset if it has not been decided — got "
            f"{th.effective_tax_rate}")
    if th.s194q_party_threshold < 0:
        raise PolicyError("The 194Q party threshold cannot be negative")


# ── loading ─────────────────────────────────────────────────────────────────
def _in_org_locale(session: Session, organization_id: str,
                   th: CommercialThresholds) -> CommercialThresholds:
    """Stamp the organization's own currency and timezone onto the thresholds.

    Both belong to the tenant, not to the deployment: one instance can hold an
    Indian distributor and a Gulf one, and the environment default is only a
    fallback for an organization row that has not said. Because both are inside
    the version hash, the same numeric policy in two currencies — or two zones,
    which put a month boundary in two different places — produces two versions,
    which is the point, since the rows stamped with them are not comparable.
    """
    org = session.get(models.Organization, organization_id)
    changes: dict = {}
    code = (getattr(org, "currency", None) or "").strip().upper()
    if code and code != th.currency:
        changes["currency"] = code
    tz = (getattr(org, "timezone", None) or "").strip()
    if tz and tz != th.timezone:
        changes["timezone"] = tz
    return replace(th, **changes) if changes else th


def load_for_org(session: Session, organization_id: str) -> CommercialThresholds:
    """The thresholds in force for this organization.

    Environment defaults with the organization's saved overrides applied. An
    org that has never edited its policy gets exactly what it got before, so
    nothing about existing behaviour depends on a row existing.
    """
    base = _in_org_locale(session, organization_id, load_commercial_thresholds())
    row = session.get(models.CommercialPolicy, organization_id)
    if row is None or not row.overrides:
        return base
    changes = {}
    for field, value in (row.overrides or {}).items():
        if field in EDITABLE and value is not None:
            try:
                changes[field] = _coerce(field, value)
            except (TypeError, ValueError):
                # A stored value that no longer coerces must not take the whole
                # organization's analysis down; the default stands and the row
                # can be corrected from Settings.
                continue
    return replace(base, **changes) if changes else base


def save_for_org(session: Session, organization_id: str, updates: dict,
                 user_id: Optional[str] = None) -> CommercialThresholds:
    """Apply and persist overrides. Validates the resulting policy, not the diff.

    Validating the result rather than the change is the point: a single edit
    that is fine alone can invert the ladder when combined with what is already
    saved, and only the combination is what quotes are judged against.
    """
    base = _in_org_locale(session, organization_id, load_commercial_thresholds())
    row = session.get(models.CommercialPolicy, organization_id)
    current = dict(row.overrides or {}) if row is not None else {}

    for field, value in updates.items():
        if field not in EDITABLE:
            raise PolicyError(
                f"{field!r} is not an editable policy field. Window lengths and "
                f"evidence floors are environment configuration — changing them "
                f"silently changes what the analysis means.")
        if value is None:
            current.pop(field, None)          # None clears an override
        else:
            current[field] = _coerce(field, value)

    candidate = replace(base, **{k: _coerce(k, v) for k, v in current.items()})
    validate(candidate)

    if row is None:
        row = models.CommercialPolicy(organization_id=organization_id)
        session.add(row)
    row.overrides = current
    row.updated_by_user_id = user_id
    session.flush()
    return candidate


def describe(session: Session, organization_id: str) -> dict:
    """The policy for the Settings screen: value, default, whether overridden."""
    base = _in_org_locale(session, organization_id, load_commercial_thresholds())
    effective = load_for_org(session, organization_id)
    row = session.get(models.CommercialPolicy, organization_id)
    overrides = (row.overrides or {}) if row is not None else {}

    fields = []
    for name in EDITABLE:
        label, help_text = FIELD_HELP[name]
        value = getattr(effective, name)
        default = getattr(base, name)
        fields.append({
            "field": name,
            "label": label,
            "help": help_text,
            "value": _jsonable(value),
            "default": _jsonable(default),
            "overridden": name in overrides,
            "kind": _kind(name),
        })
    return {
        "version": effective.version,
        "default_version": base.version,
        # The screen renders money fields with a symbol and has no other way to
        # know which one; without this it would have to assume, which is how the
        # rupee sign ended up hardcoded in four components.
        "currency": effective.currency,
        "fields": fields,
        "updated_at": clock.iso(row.updated_at) if row is not None and row.updated_at else None,
    }


#: Fields denominated in the organization's currency rather than in a ratio.
#: An explicit set, not a name suffix: the field names are currency-neutral now,
#: so there is nothing in ``min_material_gap`` for a suffix test to catch, and a
#: money field silently rendered as a ratio shows "1000000%" on the settings
#: screen.
MONEY_FIELDS: frozenset[str] = frozenset({
    "min_quote_exception_impact",
    "min_material_gap",
    "price_rounding_increment",
    "s194q_party_threshold",
})


def _kind(field: str) -> str:
    """What the settings screen should render this field as.

    ``ratio`` is the fall-through, and that is the trap: the screen multiplies a
    ratio by 100 and appends a percent sign, so any field that lands here by
    accident is displayed wrong rather than displayed plainly. ``MONEY_FIELDS``
    exists because that already happened once. The two sets below are the same
    guard for the two non-float shapes — a day count rendered as a ratio reads
    "36500 %", and a boolean reads "0 %" beside a percent sign with no way to
    turn it on.

    Deliberately keyed off the same sets ``_coerce`` uses. Classification and
    coercion disagreeing is precisely the bug: a field the screen sends as a
    checkbox and the server parses with ``float()`` fails on submit.
    """
    if field == "target_margin_by_family":
        return "family_margins"
    if field == "quantity_band_edges":
        return "band_edges"
    if field in _BOOLEAN:
        return "flag"
    if field in _DAY_COUNTS:
        return "days"
    if field in MONEY_FIELDS:
        return "money"
    if field in _NULLABLE_RATES:
        # A ratio the screen must be able to leave *empty*. Rendered as a plain
        # ratio it would show "0 %" for an unset rate, which is the one reading
        # this field must never have.
        return "optional_ratio"
    return "ratio"


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        if value and isinstance(value[0], tuple):
            return {k: v for k, v in value}
        return list(value)
    return value
