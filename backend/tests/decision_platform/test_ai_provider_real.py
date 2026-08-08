"""Turning the real provider on: selection, the prompt, and the two new gates.

The branch this file arrived on had one question behind it — *is the AI doing
anything, and can it be trusted when it is?* These tests answer the second half
without a key, which is the half that matters: the gate, the fallback and the
prompt construction are all exercisable against a fake provider, and a fake
provider is a better adversary than a real one because it can be made to fail on
purpose.
"""
from __future__ import annotations

import json

import pytest

from app.ai import prompt
from app.ai.contract import AIValidationError, validate_output
from app.ai.interpret import interpret
from app.ai.mock_provider import MockProvider
from app.ai.provider import ProviderUnavailable, provider_status, select_provider
from app.domain.enums import AiFailureReason, AiStatus

from .ai_helpers import make_bundle, valid_output


# ── selection never leaves the caller with nothing ───────────────────────────
def test_anthropic_without_a_key_falls_back_to_the_mock(monkeypatch):
    """The likeliest misconfiguration there is, and it used to be a 500.

    ``AnthropicProvider.__init__`` raises without a key; that exception used to
    travel through ``select_provider`` and out of ``DecisionService.__init__``,
    so setting the provider and forgetting the key took the whole decisions
    screen down rather than degrading one narrative.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    provider = select_provider()
    assert provider.name == "mock"

    status = provider_status()
    assert status["configured"] == "anthropic"
    assert status["effective"] == "mock"
    assert status["live"] is False
    assert "ANTHROPIC_API_KEY" in status["detail"], "the reason must be readable"


def test_anthropic_with_a_key_is_selected(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-not-a-real-key")

    provider = select_provider()
    assert provider.name == "anthropic"
    assert provider.model == settings.AI_MODEL

    status = provider_status()
    assert status["live"] is True and status["detail"] is None


def test_the_shipped_default_says_plainly_that_it_is_not_a_model(monkeypatch):
    """``AI_PROVIDER`` defaults to the mock, and the readiness answer says so
    in words — the product should not be sold on an AI that is switched off."""
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    status = provider_status()
    assert status["effective"] == "mock" and status["live"] is False
    assert "not a model" in status["detail"]


def test_a_provider_that_cannot_be_built_still_yields_a_decision(monkeypatch):
    """End to end: misconfigured provider ⇒ a card, never a blank screen."""
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")

    result = interpret(make_bundle(), select_provider(),
                       signal_type="CUSTOMER_DECLINE",
                       metrics={"pct_change": -0.4}, subject_label="Acme")
    assert result.should_surface and result.explanation


def test_a_provider_raising_at_construction_is_contained(monkeypatch):
    """Not only the missing key: any ProviderError at construction degrades."""
    import app.ai.anthropic_provider as ap
    from app.config import settings

    monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")

    class Exploding:
        def __init__(self) -> None:
            raise ProviderUnavailable("simulated construction failure")

    monkeypatch.setattr(ap, "AnthropicProvider", Exploding)
    assert select_provider().name == "mock"


# ── the specificity gate: the actual complaint, as a test ────────────────────
def test_a_narrative_with_no_figure_in_it_is_rejected():
    """The sentence five identical decision cards were all showing.

    It cites a real fact label, invents nothing, and passes every other check.
    It is still not a reading of anything, and the deterministic template it
    degrades to carries more information than it does.
    """
    generic = valid_output(
        explanation="The deterministic signal indicates a material change worth a "
                    "look; consider a relationship or pricing review as appropriate.",
        recommended_action="Review the account and confirm the context.")
    with pytest.raises(AIValidationError) as e:
        validate_output(generic, make_bundle())
    assert e.value.code == "unspecific_narrative"
    assert e.value.reason is AiFailureReason.NO_GROUNDED_FIGURE


def test_the_generic_narrative_degrades_to_the_deterministic_sentence():
    """And the decision still surfaces, with the signal's own numbers in it."""
    result = interpret(make_bundle(), MockProvider(mode="generic"),
                       signal_type="CUSTOMER_DECLINE",
                       metrics={"pct_change": -0.4}, subject_label="Acme")
    assert result.status is AiStatus.DEGRADED
    assert result.should_surface
    assert "40%" in result.explanation, "the fallback must still carry the figure"
    assert result.telemetry.failure_reason == AiFailureReason.NO_GROUNDED_FIGURE.value


def test_a_bundle_with_no_figures_is_not_asked_for_one():
    """The gate blames a wordy reading only when there was something to quote."""
    wordless = make_bundle(facts=[("subject", "Acme"), ("segment", "automotive")])
    out = validate_output(
        valid_output(explanation="A change worth a look.", cited_fact_labels=["segment"]),
        wordless)
    assert out.should_surface


def test_a_withheld_reading_is_not_asked_for_a_figure():
    out = validate_output(
        valid_output(explanation="The evidence does not support a confident read.",
                     cannot_recommend_reliably=True, recommended_action=""),
        make_bundle())
    assert out.cannot_recommend_reliably


# ── grounding: still hard, and now not hard on the wrong things ──────────────
def test_a_fabricated_figure_is_still_rejected():
    """The check this branch must never weaken, restated against the new gate:
    a number nobody computed cannot reach a person."""
    with pytest.raises(AIValidationError) as e:
        validate_output(
            valid_output(explanation="Revenue is down 40%; recover it by billing 87500."),
            make_bundle())
    assert e.value.reason is AiFailureReason.UNGROUNDED_NUMBER


def test_a_derived_figure_is_rejected_even_though_the_arithmetic_is_right():
    """30000 − 12000 = 18000 is correct and still forbidden. The model may read
    a computed number; it may not produce one, and 'it did the sum properly' is
    not a defence — nothing downstream can tell that sum from a guess."""
    with pytest.raises(AIValidationError) as e:
        validate_output(valid_output(explanation="Revenue fell by 18000."), make_bundle())
    assert e.value.reason is AiFailureReason.UNGROUNDED_NUMBER


def test_a_threshold_from_the_supplied_policy_is_grounded():
    """The policy lines are sent to the model, and are as deterministic as the
    facts. Refusing them meant rejecting the model for quoting our own rule."""
    bundle = make_bundle()
    bundle.policies = ["Decline threshold: recent revenue down ≥ 25% vs prior 90d."]
    out = validate_output(
        valid_output(explanation="Revenue is down 40%, past the 25% decline threshold."),
        bundle)
    assert "25%" in out.explanation


def test_a_number_that_is_in_no_policy_is_still_rejected():
    bundle = make_bundle()
    bundle.policies = ["Decline threshold: recent revenue down ≥ 25% vs prior 90d."]
    with pytest.raises(AIValidationError):
        validate_output(valid_output(explanation="Down 40%, past the 33% threshold."),
                        bundle)


def test_digits_inside_a_text_fact_do_not_ground_a_financial_claim():
    """A catalogue description is data, not a fact set. ``box of 10`` must not
    license the model to talk about 10 of anything."""
    bundle = make_bundle(facts=[("subject", "CNMG 120408-MP insert, box of 10"),
                                ("pct_change", -0.4)])
    with pytest.raises(AIValidationError):
        validate_output(valid_output(explanation="Down 40%; 120408 units are affected."),
                        bundle)


# ── the prompt is specific to this decision ──────────────────────────────────
@pytest.mark.parametrize("decision_type", sorted(prompt.GUIDANCE))
def test_each_decision_type_gets_its_own_guidance(decision_type):
    system = prompt.build_system(make_bundle(decision_type=decision_type))
    assert prompt.GUIDANCE[decision_type] in system
    for other, text in prompt.GUIDANCE.items():
        if other != decision_type:
            assert text not in system, "guidance for another signal family leaked in"


def test_an_unknown_decision_type_still_gets_the_rules_and_the_schema():
    """A new signal family must degrade to a plainer prompt, never to a prompt
    missing its guard rails."""
    system = prompt.build_system(make_bundle(decision_type="SOMETHING_NEW"))
    assert "Never output a number that is not present in the provided facts" in system
    assert "cited_fact_labels" in system


def test_the_prompt_carries_no_figures_of_its_own():
    """The guidance says what to look at, never what the numbers usually are —
    a rule of thumb baked into the prompt is a number the model could reason
    from, and the gate could not tell it from a fact."""
    from app.context.bundle import numbers_in

    for text in prompt.GUIDANCE.values():
        assert not numbers_in(text), f"a figure appears in the guidance: {text[:60]}"


def test_the_user_message_is_still_only_the_bundle():
    """Injection resistance: no free text is concatenated into the user turn."""
    bundle = make_bundle()
    assert json.loads(prompt.build_user(bundle)) == json.loads(
        json.dumps(bundle.to_prompt_json(), sort_keys=True, default=str))


# ── the offline default stopped saying the same thing five times ─────────────
def test_the_mock_quotes_this_subjects_own_figures():
    a = json.loads(MockProvider().complete(
        *_call(make_bundle(facts=[("subject", "Acme"), ("pct_change", -0.4)]))))
    b = json.loads(MockProvider().complete(
        *_call(make_bundle(facts=[("subject", "Beta"), ("pct_change", -0.15)]))))
    assert a["explanation"] != b["explanation"], "two decisions, one sentence"
    assert "40%" in a["explanation"] and "15%" in b["explanation"]


def test_a_margin_movement_is_quoted_in_points_not_per_cent():
    """Percentage points and per cent are different quantities (CLAUDE.md §1),
    and 'margin fell 17.5%' says something else entirely about a 30% margin."""
    bundle = make_bundle(decision_type="MARGIN_DETERIORATION",
                         facts=[("subject", "Acme"), ("margin_drop_points", 0.175)])
    out = json.loads(MockProvider().complete(*_call(bundle)))
    assert "17.5 points" in out["explanation"]
    assert "17.5%" not in out["explanation"]


def test_the_mock_says_on_the_card_that_it_is_not_a_model():
    out = json.loads(MockProvider().complete(*_call(make_bundle())))
    assert "stand-in" in out["caveat"].lower()


def test_the_mock_output_passes_the_gate_it_is_a_stand_in_for():
    bundle = make_bundle()
    out = validate_output(MockProvider().complete(*_call(bundle)), bundle)
    assert out.should_surface and out.cited_fact_labels


def _call(bundle):
    return prompt.build_system(bundle), prompt.build_user(bundle)
