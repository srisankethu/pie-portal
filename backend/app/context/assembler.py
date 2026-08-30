"""Assemble a permission-scoped ContextBundle from a deterministic signal.

Only relevant, compact information is included (no raw DB dumps). RESTRICTED facts
(cost/margin) are removed for a salesperson recipient — absent, not masked — so
they never reach the AI. The set of visible facts also bounds the numbers the AI
may cite (grounding).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..domain import models
from ..domain.enums import (
    OPERATIONAL, RESTRICTED, RESTRICTED_FACT_FIELDS, EvidenceSufficiency, Role,
    SubjectEntityType)
from ..signals.config import SignalThresholds, load_thresholds
from ..trust import pseudonym, vault
from .bundle import ContextBundle, FactView, SignalView

_MAX_FACTS = 40


def _is_restricted(path: str) -> bool:
    leaf = path.split(".")[-1].split("[")[0]
    return leaf in RESTRICTED_FACT_FIELDS or any(tok in path for tok in ("cost", "margin"))


#: Entity-reference keys a detector may embed inside its metrics, and the kind
#: each one names. A nested dict carrying one of these *and* a display label is
#: an entity reference, and that label is a name.
#:
#: This exists because pseudonymising only the subject was not enough. The
#: decline detector reports ``top_declining_products`` as
#: ``{"product_id": …, "label": "25mm shank turning holder", …}``, which flattens
#: straight into the fact list and carried a real item name to the provider.
#: Handling it here rather than in that one detector means the next detector to
#: embed an entity reference is covered without anyone remembering to.
_ID_KEYS: dict[str, str] = {
    "product_id": SubjectEntityType.PRODUCT.value,
    "item_id": SubjectEntityType.PRODUCT.value,
    "customer_id": SubjectEntityType.CUSTOMER.value,
}
_LABEL_KEYS = ("label", "name")


def _kind_of(key: str) -> str | None:
    """Which entity a metrics key is about, from its name."""
    low = key.lower()
    if "customer" in low:
        return SubjectEntityType.CUSTOMER.value
    if "product" in low or "item" in low:
        return SubjectEntityType.PRODUCT.value
    return None


def _paired_ids(key: str, holder: dict[str, Any]) -> list[str] | None:
    """The id list that runs alongside a list of names, if there is one.

    The second shape detectors use: ``affected_customers`` (names) beside
    ``affected_customer_ids`` (ids), positionally aligned. Singular-plus-``_ids``
    is the convention in this codebase, so it is the convention matched here.
    """
    candidate = f"{key[:-1]}_ids" if key.endswith("s") else f"{key}_ids"
    ids = holder.get(candidate)
    return ids if isinstance(ids, list) else None


def _pseudonymise(org: str, value: Any, display_names: dict[str, str]) -> Any:
    """Replace embedded entity names with pseudonyms, recursively.

    Two shapes are handled, because detectors use two: an entity reference as a
    dict (``{"product_id": …, "label": …}``) and a list of names running
    alongside a list of ids (``affected_customers`` / ``affected_customer_ids``).

    A name a detector emits with no id anywhere near it cannot be pseudonymised
    by any rule here, and is deliberately not guessed at. That case is caught
    instead by ``trust.disclosure.check_names``, which knows the tenant's actual
    names and records a finding on the payload — visible on the trust screen and
    asserted by the test suite, rather than silently sent.
    """
    if isinstance(value, dict):
        out = {k: _pseudonymise(org, v, display_names) for k, v in value.items()}

        # Shape 1: this dict is itself an entity reference.
        for id_key, kind in _ID_KEYS.items():
            entity_id = value.get(id_key)
            if not entity_id:
                continue
            label = pseudonym.label_for(org, kind, str(entity_id))
            for label_key in _LABEL_KEYS:
                real = out.get(label_key)
                if isinstance(real, str) and real:
                    display_names[label] = real
                    out[label_key] = label
            break

        # Shape 2: a list of names beside a list of ids.
        for key, names in list(value.items()):
            if not (isinstance(names, list) and names
                    and all(isinstance(n, str) for n in names)):
                continue
            ids = _paired_ids(key, value)
            kind = _kind_of(key)
            if not ids or not kind or len(ids) != len(names):
                continue
            swapped = []
            for entity_id, real in zip(ids, names):
                label = pseudonym.label_for(org, kind, str(entity_id))
                if real:
                    display_names[label] = real
                swapped.append(label)
            out[key] = swapped
        return out

    if isinstance(value, list):
        return [_pseudonymise(org, v, display_names) for v in value]
    return value


def _flatten(prefix: str, value: Any, out: list[tuple[str, Any]]) -> None:
    if len(out) >= _MAX_FACTS:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else k, v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value[:5]):
            _flatten(f"{prefix}[{i}]", v, out)
    else:
        out.append((prefix, value))


def _policies(decision_type: str, th: SignalThresholds) -> list[str]:
    if decision_type == "CUSTOMER_DECLINE":
        return [f"Decline threshold: recent revenue down ≥ {int(th.decline_drop_pct*100)}% "
                f"vs prior {th.comparison_period_days}d (≥{th.decline_min_prior_orders} prior "
                f"orders, ≥{th.decline_min_history_months}mo history)."]
    if decision_type == "CUSTOMER_DORMANCY":
        return [f"Dormancy threshold: gap > {th.dormancy_interval_multiplier}× the customer's "
                f"typical interval (≥{th.dormancy_min_orders} orders to estimate cadence)."]
    if decision_type == "MARGIN_DETERIORATION":
        return [f"Margin threshold: margin down > {int(th.margin_drop_points*100)} points vs baseline."]
    if decision_type == "COST_PASS_THROUGH":
        return [f"Cost threshold: latest cost up > {int(th.cost_increase_pct*100)}% while price lagged."]
    return []


def assemble_from_signal(session: Session, signal: models.Signal, recipient_role: Role,
                         thresholds: SignalThresholds | None = None) -> ContextBundle:
    th = thresholds or load_thresholds()
    is_sales = recipient_role is Role.SALESPERSON

    # The subject is referred to by a pseudonym, not by its name. A margin
    # calculation never needed to know the customer is called Bharat Forge, and
    # the label is the one field in this bundle that would carry that name to a
    # model provider. The real name is looked up from the vault and kept beside
    # the bundle so the seam can put it back before a person reads the output.
    subject_label = pseudonym.label_for(
        signal.organization_id, signal.subject_entity_type, signal.subject_entity_id)
    display_names: dict[str, str] = {}
    if signal.subject_entity_type in (SubjectEntityType.CUSTOMER.value,
                                      SubjectEntityType.PRODUCT.value):
        display_names[subject_label] = vault.resolve(
            session, signal.organization_id, signal.subject_entity_type,
            signal.subject_entity_id)

    # flatten metrics → facts, redacting RESTRICTED for a salesperson.
    # Names nested inside the metrics are swapped for pseudonyms first — the
    # subject is not the only place a detector can put one.
    flat: list[tuple[str, Any]] = []
    _flatten("", _pseudonymise(signal.organization_id, signal.metrics or {},
                               display_names), flat)
    facts: list[FactView] = [FactView(label="subject", value=subject_label)]
    redactions: list[str] = []
    for label, value in flat:
        if value is None:
            continue
        if is_sales and _is_restricted(label):
            redactions.append(label)
            continue
        facts.append(FactView(label=label, value=value))

    suff = signal.sufficiency or {}
    unknowns = [{"field": f, "reason": "not available in source data"}
                for f in suff.get("missing_fields", [])]
    for a in suff.get("anomalies", []):
        unknowns.append({"field": "data_quality", "reason": f"{a.get('code')}: {a.get('detail')}"})

    return ContextBundle(
        decision_type=signal.signal_type,   # signal families map 1:1 to proactive decision types
        organization_id=signal.organization_id,
        subject_ref={"entity_type": signal.subject_entity_type,
                     "entity_id": signal.subject_entity_id, "label": subject_label},
        display_names=display_names,
        recipient_role=recipient_role.value,
        permitted_data_classes=([OPERATIONAL] if is_sales
                                else [OPERATIONAL, RESTRICTED]),
        redactions_applied=redactions,
        signals=[SignalView(signal_id=signal.signal_id, signal_type=signal.signal_type,
                            subject_entity_type=signal.subject_entity_type,
                            subject_entity_id=signal.subject_entity_id,
                            severity_base=signal.severity_base)],
        facts=facts[:_MAX_FACTS],
        # A missing (or empty) level defaults to INSUFFICIENT, not SUFFICIENT:
        # this value is read as the pass/fail gate downstream, and "we have no
        # record of whether the evidence was enough" must not read as "it was" —
        # the §1 benign-default trap. No live path persists a level-less signal
        # (every Signal is built via Sufficiency.to_dict / quote_bundle, which
        # always set one), so this changes no current behaviour; it closes the
        # fail-open direction the invariant forbids.
        evidence_sufficiency={"level": (suff.get("level")
                                        or EvidenceSufficiency.INSUFFICIENT.value),
                              "reasons": suff.get("reasons", [])},
        unknowns=unknowns,
        policies=_policies(signal.signal_type, th),
        evidence_refs=signal.evidence_refs or [],
    )
