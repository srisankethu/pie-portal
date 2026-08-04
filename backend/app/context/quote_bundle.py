"""Assemble a permission-scoped ContextBundle for an on-demand QUOTE_CONTEXT.

Unlike ``assemble_from_signal`` (which flattens a persisted signal's metrics and
redacts by keyword), the quote-context assembler emits facts that are already
tagged with a ``data_class`` (OPERATIONAL / RESTRICTED). That tag is the
authority here: RESTRICTED cost/margin facts are dropped for a salesperson —
absent, not masked — while OPERATIONAL *direction-only* flags (e.g. "cost moving
up") are kept, which is exactly what the direction flag exists for.

This module states facts and reports what is unknown. It does not recommend and
does not calculate — the deterministic layer already computed every number, and
the AI layer interprets afterwards.
"""
from __future__ import annotations

from typing import Any, Optional

from ..domain.enums import EvidenceSufficiency, Role
from ..trust import pseudonym
from .bundle import ContextBundle, FactView

OPERATIONAL = "OPERATIONAL"
RESTRICTED = "RESTRICTED"

# Readable labels for the raw fact keys the quote-context assembler emits.
_LABELS = {
    "revenue_trend_pct": "Revenue trend",
    "revenue_trend_direction": "Revenue direction",
    "typical_interval_days": "Typical order interval (days)",
    "days_since_last_order": "Days since last order",
    "is_overdue": "Overdue to reorder",
    "last_price_paid": "Last price paid",
    "times_purchased": "Times purchased",
    "price_trend_pct": "Price trend",
    "price_trend_direction": "Price direction",
    "current_unit_cost": "Current unit cost",
    "standard_margin_pct": "Standard margin",
    "cost_delta_pct": "Cost change",
    "cost_movement_direction": "Cost direction",
}


def _label(raw: str) -> str:
    return _LABELS.get(raw, raw.replace("_", " ").capitalize())


def _suspicious_cost(item_facts: list[dict]) -> Optional[str]:
    """Detect a cost that is not below the last selling price (a data-quality
    red flag a human must weigh before trusting the margin read)."""
    by = {f["label"]: f["value"] for f in item_facts}
    cost = by.get("current_unit_cost")
    price = by.get("last_price_paid")
    if isinstance(cost, (int, float)) and isinstance(price, (int, float)) and price > 0:
        if cost >= price:
            return "recorded unit cost is at or above the last selling price"
    return None


def build_quote_bundle(
    assembled: dict[str, Any],
    recipient_role: Role,
    *,
    proposed_price: Optional[float] = None,
    proposed_product_id: Optional[str] = None,
) -> ContextBundle:
    """Turn ``quote_context.assemble(...)`` output into a role-scoped bundle.

    Customer and item names are replaced by pseudonyms here. This function needs
    no vault lookup to do it: it already holds the organization and every entity
    id, and the real names arrive alongside in ``assembled``, so the mapping is
    built from what is in hand and travels beside the bundle rather than inside
    it.
    """
    is_sales = recipient_role is Role.SALESPERSON
    subject = assembled.get("subject_ref", {})
    customer_id = subject.get("customer_id", "")
    org = assembled.get("organization_id", "")

    display_names: dict[str, str] = {}

    def _pseudo(entity_type: str, entity_id: str, real: Optional[str]) -> str:
        label = pseudonym.label_for(org, entity_type, entity_id)
        if real:
            display_names[label] = real
        return label

    customer_label = _pseudo("CUSTOMER", customer_id,
                             assembled.get("customer_label"))

    facts: list[FactView] = []
    redactions: list[str] = []
    unknowns: list[dict[str, str]] = list(assembled.get("unknowns", []))
    evidence_refs: list[dict[str, Any]] = []

    def _emit(fact: dict[str, Any], prefix: str = "") -> None:
        dc = fact.get("data_class", OPERATIONAL)
        raw = fact.get("label", "")
        label = (f"{prefix}{_label(raw)}" if prefix else _label(raw))
        if is_sales and dc == RESTRICTED:
            redactions.append(label)
            return
        facts.append(FactView(label=label, value=fact.get("value"), unit=fact.get("unit")))
        for ref in fact.get("source_refs", []) or []:
            evidence_refs.append(ref)

    # customer-level facts first, then per-item facts (prefixed with the item name)
    for f in assembled.get("customer_facts", []):
        _emit(f)

    items = assembled.get("items", [])
    has_item_history = False
    for item in items:
        item_label = _pseudo("PRODUCT", item.get("product_id", ""),
                             item.get("label"))
        item_facts = item.get("facts", [])
        if any(f.get("label") == "last_price_paid" for f in item_facts):
            has_item_history = True
        susp = _suspicious_cost(item_facts)
        if susp:
            unknowns.append({"field": f"cost_quality:{item.get('product_id')}", "reason": susp})
        prefix = f"{item_label} · " if len(items) > 1 else ""
        for f in item_facts:
            _emit(f, prefix)

    if proposed_price is not None:
        proposed_label = (_pseudo("PRODUCT", proposed_product_id, None)
                          if proposed_product_id else None)
        lbl = "Your proposed price" + (f" ({proposed_label})" if proposed_label else "")
        facts.append(FactView(label=lbl, value=round(float(proposed_price), 2), unit="currency"))

    # ── evidence sufficiency ────────────────────────────────────────────────
    # No usable item history AND no customer cadence/revenue signal ⇒ the AI
    # should not manufacture a recommendation.
    has_customer_context = bool(assembled.get("customer_facts"))
    if not has_item_history and not has_customer_context:
        level = EvidenceSufficiency.INSUFFICIENT.value
        reasons = ["No prior purchase history for this customer/item; commercial context is thin."]
    elif not has_item_history or unknowns:
        level = EvidenceSufficiency.PARTIAL.value
        reasons = ["Some commercial context is missing or flagged; weigh the recommendation carefully."]
    else:
        level = EvidenceSufficiency.SUFFICIENT.value
        reasons = []

    policies = [
        "This is decision support for pricing a quote. The salesperson chooses the "
        "final price and product; never instruct an automatic price change or product swap.",
    ]

    return ContextBundle(
        decision_type="QUOTE_CONTEXT",
        organization_id=org,
        subject_ref={"entity_type": "QUOTE", "customer_id": customer_id,
                     "label": customer_label},
        display_names=display_names,
        recipient_role=recipient_role.value,
        permitted_data_classes=(["OPERATIONAL"] if is_sales
                                else ["OPERATIONAL", "RESTRICTED"]),
        redactions_applied=redactions,
        signals=[],  # on-demand: no persisted detector signal
        facts=facts,
        evidence_sufficiency={"level": level, "reasons": reasons},
        unknowns=unknowns,
        policies=policies,
        evidence_refs=evidence_refs,
    )
