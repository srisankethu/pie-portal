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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


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

    # ── derived ──────────────────────────────────────────────────────────────
    def fact_labels(self) -> set[str]:
        return {f.label for f in self.facts}

    def signal_ids(self) -> set[str]:
        return {s.signal_id for s in self.signals}

    def allowed_numbers(self) -> set[float]:
        """Numeric values the AI may cite (from visible facts only, with common
        representations: raw, ×100 for ratios, and 2-dp rounding)."""
        out: set[float] = set()
        for f in self.facts:
            v = f.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            fv = float(v)
            # include the value and its magnitude (a -0.4 ratio may be shown as "40%")
            for base in (fv, abs(fv)):
                for cand in (base, round(base, 2), round(base * 100, 2),
                             round(base * 100, 1), float(round(base))):
                    out.add(cand)
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
