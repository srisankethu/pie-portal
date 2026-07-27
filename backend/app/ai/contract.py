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
from ..domain.enums import AiFailureReason

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Scale factors a model plausibly confuses (ratio<->percent, and the ×100 money
# inflation the grounding set deliberately refuses to admit).
_SCALE_FACTORS = (100.0, 0.01, 1000.0, 0.001)


class AIValidationError(ValueError):
    """A gate rejection.

    ``code`` is the stable legacy string (kept for backward compatibility);
    ``reason`` is the WS3 taxonomy member used for telemetry and ops metrics.
    """

    def __init__(self, code: str, detail: str,
                 reason: AiFailureReason = AiFailureReason.SCHEMA_INVALID) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.reason = reason


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


def _is_scale_error(value: float, allowed: set[float]) -> bool:
    """True when an ungrounded number is a supplied fact off by a scale factor.

    Distinguishing this from an arbitrary fabrication matters operationally: a
    scale error points at the prompt (units/percent framing), a fabrication
    points at the model or a too-thin fact set. Either way it is still rejected.
    """
    for f in _SCALE_FACTORS:
        if _grounded(value * f, allowed):
            return True
    return False


def validate_output(raw: str, bundle: ContextBundle,
                    corrections: list[AiFailureReason] | None = None) -> AIDecisionOutput:
    """Validate a raw model response against the supplied context.

    ``corrections`` (optional, mutated in place) collects the deterministic
    repairs applied to an otherwise-usable output, so telemetry can report them.
    """
    noted = corrections if corrections is not None else []

    # 1) schema
    try:
        data = json.loads(raw)
        out = AIDecisionOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise AIValidationError("malformed_output", str(e)[:200],
                                AiFailureReason.SCHEMA_INVALID)

    # 5) clamp priority (never trust the model's bound) — repaired, and recorded
    bound = settings.AI_PRIORITY_ADJUST_BOUND
    raw_adj = int(out.priority_adjustment)
    out.priority_adjustment = max(-bound, min(bound, raw_adj))
    if out.priority_adjustment != raw_adj:
        noted.append(AiFailureReason.PRIORITY_OUT_OF_RANGE)

    # 2) subset checks
    bad_facts = [c for c in out.cited_fact_labels if c not in bundle.fact_labels()]
    if bad_facts:
        raise AIValidationError("cited_fact_not_in_context", f"{bad_facts}",
                                AiFailureReason.UNKNOWN_FACT_LABEL)
    bad_sigs = [c for c in out.cited_signal_ids if c not in bundle.signal_ids()]
    if bad_sigs:
        raise AIValidationError("cited_signal_not_in_context", f"{bad_sigs}",
                                AiFailureReason.UNKNOWN_SIGNAL_ID)

    # 4) withheld ⇒ no asserted action/recommendation — repaired, and recorded
    if out.cannot_recommend_reliably:
        if out.recommended_action:
            noted.append(AiFailureReason.ACTION_TEXT_ON_WITHHELD)
        out.recommended_action = ""

    # 3) fact-grounding — no fabricated numbers in user-facing text
    allowed = bundle.allowed_numbers()
    text = " ".join(filter(None, [out.concise_title, out.explanation,
                                  out.recommended_action, out.caveat]))
    for n in _numbers_in(text):
        if not _grounded(n, allowed):
            scale = _is_scale_error(n, allowed)
            raise AIValidationError(
                "ungrounded_number",
                f"{n} {'is a supplied fact at the wrong scale' if scale else 'not traceable to a supplied fact'}",
                AiFailureReason.SCALE_VIOLATION if scale else AiFailureReason.UNGROUNDED_NUMBER)
    return out
