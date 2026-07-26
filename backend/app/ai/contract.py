"""Structured AI output contract + deterministic validation gate (spec §15).

The AI must return strict JSON matching ``AIDecisionOutput``. The gate then
enforces, deterministically:
  1. schema validity,
  2. cited fact labels ⊆ context facts, cited signal ids ⊆ context signals,
  3. fact-grounding — every number in user-facing text must trace to a supplied
     fact value (no fabricated financials),
  4. a withheld recommendation carries no action text,
  5. priority_adjustment clamped to bounds.
Any failure raises :class:`AIValidationError`; the caller falls back.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from ..config import settings
from ..context.bundle import ContextBundle

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


class AIValidationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class AIDecisionOutput(BaseModel):
    should_surface: bool = True
    concise_title: str = Field(max_length=120)
    explanation: str = Field(max_length=1200)
    recommended_action: str = Field(default="", max_length=600)
    priority_adjustment: int = 0
    cannot_recommend_reliably: bool = False
    caveat: Optional[str] = Field(default=None, max_length=600)
    cited_fact_labels: list[str] = Field(default_factory=list)
    cited_signal_ids: list[str] = Field(default_factory=list)


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMBER_RE.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def _grounded(value: float, allowed: set[float]) -> bool:
    for a in allowed:
        tol = max(0.05, abs(a) * 0.01)   # absolute floor + 1% relative
        if abs(value - a) <= tol:
            return True
    return False


def validate_output(raw: str, bundle: ContextBundle) -> AIDecisionOutput:
    # 1) schema
    try:
        data = json.loads(raw)
        out = AIDecisionOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise AIValidationError("malformed_output", str(e)[:200])

    # 5) clamp priority (never trust the model's bound)
    bound = settings.AI_PRIORITY_ADJUST_BOUND
    out.priority_adjustment = max(-bound, min(bound, int(out.priority_adjustment)))

    # 2) subset checks
    bad_facts = [c for c in out.cited_fact_labels if c not in bundle.fact_labels()]
    if bad_facts:
        raise AIValidationError("cited_fact_not_in_context", f"{bad_facts}")
    bad_sigs = [c for c in out.cited_signal_ids if c not in bundle.signal_ids()]
    if bad_sigs:
        raise AIValidationError("cited_signal_not_in_context", f"{bad_sigs}")

    # 4) withheld ⇒ no asserted action/recommendation
    if out.cannot_recommend_reliably:
        out.recommended_action = ""

    # 3) fact-grounding — no fabricated numbers in user-facing text
    allowed = bundle.allowed_numbers()
    text = " ".join(filter(None, [out.concise_title, out.explanation,
                                  out.recommended_action, out.caveat]))
    for n in _numbers_in(text):
        if not _grounded(n, allowed):
            raise AIValidationError("ungrounded_number",
                                    f"{n} not traceable to a supplied fact")
    return out
