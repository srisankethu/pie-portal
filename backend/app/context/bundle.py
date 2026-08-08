"""The ContextBundle — the curated structured AI input (spec §7).

Compact by construction (facts + a signal summary, not raw dumps), permission
-scoped (redacted facts are absent), and self-describing (evidence sufficiency +
explicit unknowns). It also exposes the *allowed number set* — the numeric values
the AI is permitted to reference — which the validation gate uses to reject any
fabricated figure.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

#: The two halves of the grounding contract live together deliberately: what a
#: bundle *permits* a model to say and how a number is *recognised* in prose have
#: to agree, and two regexes in two modules would eventually not. ``ai/contract``
#: imports this rather than owning a second copy.
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every number a reader would see in a piece of text."""
    out: list[float] = []
    for m in _NUMBER_RE.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


@dataclass
class FactView:
    label: str
    value: Any
    unit: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "value": self.value, "unit": self.unit}


@dataclass
class SignalView:
    signal_id: str
    signal_type: str
    subject_entity_type: str
    subject_entity_id: str
    severity_base: int

    def to_dict(self) -> dict[str, Any]:
        return {"signal_id": self.signal_id, "signal_type": self.signal_type,
                "subject_entity_type": self.subject_entity_type,
                "subject_entity_id": self.subject_entity_id,
                "severity_base": self.severity_base}


@dataclass
class ContextBundle:
    decision_type: str
    organization_id: str
    subject_ref: dict[str, Any]
    recipient_role: str
    permitted_data_classes: list[str]
    redactions_applied: list[str]
    signals: list[SignalView]
    facts: list[FactView]
    evidence_sufficiency: dict[str, Any]      # {level, reasons}
    unknowns: list[dict[str, str]]
    policies: list[str]
    evidence_refs: list[dict[str, Any]]       # source records (for audit; not prompted)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    #: pseudonym → display name, for re-hydrating the model's output at the
    #: ``decisions/`` seam. Never enters ``to_prompt_json`` and never enters
    #: ``context_hash``: a renamed customer must not invalidate a cached
    #: interpretation whose *facts* are unchanged, and the whole point of the
    #: pseudonym is that this mapping does not leave the building.
    display_names: dict[str, str] = field(default_factory=dict)

    # ── derived ──────────────────────────────────────────────────────────────
    def fact_labels(self) -> set[str]:
        return {f.label for f in self.facts}

    def signal_ids(self) -> set[str]:
        return {s.signal_id for s in self.signals}

    def has_numeric_facts(self) -> bool:
        """Whether this bundle gives the model anything to quote.

        Read by the gate: a narrative with no figure in it is only a defect when
        a figure was available. A bundle carrying nothing but a subject label
        cannot be blamed for a wordy reading.
        """
        return any(not isinstance(f.value, bool) and isinstance(f.value, (int, float))
                   for f in self.facts)

    def allowed_numbers(self) -> set[float]:
        """Numeric values the AI may cite (raw and 2-dp rounding, plus the ×100
        percent form ONLY for fractional ratios). The ×100 form is deliberately
        withheld for values with magnitude > 1 (money, counts, days): otherwise a
        ₹430 fact would also 'ground' a fabricated ₹43,000, inflating a monetary
        claim 100×.

        Two sources, and the split is the whole point:

        * **numeric fact values** — what the deterministic layer computed;
        * **the policy lines** — sentences this codebase *wrote*, from the
          configured thresholds, and put in front of the model. "Down 40%, past
          the 25% decline threshold" is a better sentence than "down 40%", and
          the 25 in it is as deterministic as the 40. Excluding them meant the
          gate rejected the model for quoting our own policy back at us, and a
          rejection degrades the decision to a template — so the rule as written
          made narratives worse without making any number less traceable.

        Free-text *fact values* are deliberately NOT harvested. "CNMG 120408-MP
        insert, box of 10" is data supplied by a customer or a catalogue, and
        letting its digits ground a financial claim is exactly the hole the live
        suite's adversarial fixtures exist to catch.
        """
        out: set[float] = set()
        for f in self.facts:
            v = f.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            fv = float(v)
            # include the value and its magnitude (a -0.4 ratio may be shown as "40%")
            for base in (fv, abs(fv)):
                out.update({base, round(base, 2), float(round(base))})
                if abs(base) <= 1:  # a ratio like 0.4 → "40%"
                    out.update({round(base * 100, 2), round(base * 100, 1)})
        for policy in self.policies:
            out.update(numbers_in(policy))
        return out

    def context_hash(self) -> str:
        """Stable hash for idempotency — same inputs ⇒ skip re-inference."""
        blob = json.dumps({
            "type": self.decision_type, "subject": self.subject_ref,
            "role": self.recipient_role,
            "facts": sorted((f.label, f.value) for f in self.facts),
            "signals": sorted(self.signal_ids()),
        }, sort_keys=True, default=str).encode()
        return "cx_" + hashlib.sha256(blob).hexdigest()[:12]

    def to_prompt_json(self) -> dict[str, Any]:
        """The compact structure sent to the model (no raw records, no evidence
        refs, no chain-of-thought)."""
        return {
            "decision_type": self.decision_type,
            "subject": self.subject_ref,
            "facts": [f.to_dict() for f in self.facts],
            "signals": [s.to_dict() for s in self.signals],
            "evidence_sufficiency": self.evidence_sufficiency,
            "unknowns": self.unknowns,
            "policies": self.policies,
        }

    def to_audit_dict(self) -> dict[str, Any]:
        d = self.to_prompt_json()
        d.update({
            "organization_id": self.organization_id,
            "recipient_role": self.recipient_role,
            "permitted_data_classes": self.permitted_data_classes,
            "redactions_applied": self.redactions_applied,
            "evidence_refs": self.evidence_refs,
            "context_hash": self.context_hash(),
            "generated_at": self.generated_at,
        })
        return d
