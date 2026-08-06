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
    "price_rounding_increment",
    "carrying_cost_annual_pct",
    "carrying_rate_is_published",
    "dead_stock_days",
    "slow_stock_days",
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
        "Erosion threshold",
        "How far margin must fall, in percentage points, before the platform "
        "calls it erosion rather than noise from mix and freight."),
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
}


#: Fields that are not ratios. Kept beside ``_coerce`` rather than inferred
#: from the dataclass, because inferring it would silently start coercing any
#: future field whose annotation happened to match.
#:
#: ``_kind`` reads these too, so what the screen renders and what the server
#: parses come from one list. They were separate for one commit and the screen
#: showed "36500 %" for a 365-day threshold.
_BOOLEAN = frozenset({"carrying_rate_is_published"})
_DAY_COUNTS = frozenset({"dead_stock_days", "slow_stock_days"})


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
        "updated_at": row.updated_at.isoformat() if row is not None and row.updated_at else None,
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
    return "ratio"


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        if value and isinstance(value[0], tuple):
            return {k: v for k, v in value}
        return list(value)
    return value
