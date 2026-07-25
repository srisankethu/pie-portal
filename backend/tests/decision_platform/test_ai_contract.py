"""AI output contract + validation gate."""
from __future__ import annotations

import json

import pytest

from app.ai.contract import AIValidationError, validate_output

from .ai_helpers import make_bundle


def _out(**over):
    base = {"should_surface": True, "concise_title": "Revenue decline: Acme",
            "explanation": "Revenue is materially down versus the prior period.",
            "recommended_action": "Review the account.", "priority_adjustment": 5,
            "cannot_recommend_reliably": False, "cited_fact_labels": ["pct_change"],
            "cited_signal_ids": ["sig1"]}
    base.update(over)
    return json.dumps(base)


def test_valid_output_passes():
    out = validate_output(_out(), make_bundle())
    assert out.should_surface and out.recommended_action


def test_malformed_json_rejected():
    with pytest.raises(AIValidationError) as e:
        validate_output("not json {", make_bundle())
    assert e.value.code == "malformed_output"


def test_cited_fact_not_in_context_rejected():
    with pytest.raises(AIValidationError) as e:
        validate_output(_out(cited_fact_labels=["nope"]), make_bundle())
    assert e.value.code == "cited_fact_not_in_context"


def test_cited_signal_not_in_context_rejected():
    with pytest.raises(AIValidationError) as e:
        validate_output(_out(cited_signal_ids=["ghost"]), make_bundle())
    assert e.value.code == "cited_signal_not_in_context"


def test_ungrounded_number_rejected():
    # 999 is not in the supplied facts
    with pytest.raises(AIValidationError) as e:
        validate_output(_out(explanation="Increase price by 999 now."), make_bundle())
    assert e.value.code == "ungrounded_number"


def test_grounded_number_from_fact_passes():
    # 40% == pct_change -0.4 ×100 ; 30000 and 12000 are facts
    out = validate_output(_out(explanation="Down 40% (30000 → 12000)."), make_bundle())
    assert out.recommended_action


def test_priority_adjustment_clamped():
    out = validate_output(_out(priority_adjustment=999), make_bundle())
    assert out.priority_adjustment == 20
    out2 = validate_output(_out(priority_adjustment=-999), make_bundle())
    assert out2.priority_adjustment == -20


def test_withheld_clears_action():
    out = validate_output(_out(cannot_recommend_reliably=True,
                               recommended_action="Do X"), make_bundle())
    assert out.recommended_action == ""
