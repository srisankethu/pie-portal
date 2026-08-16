"""The pseudonymisation promise, asserted where it can actually be broken.

The unit tests in ``test_trust_controls.py`` check that a bundle constructed by
hand keeps its names out of the prompt. That is necessary and not sufficient:
the bundle is built by two different code paths, and the way this guarantee
fails in practice is that someone adds a field to one of them.

So this file intercepts the provider — the real boundary, the last thing before
the payload leaves the process — and asserts on exactly what it was handed. A
name added anywhere upstream fails here regardless of which assembler put it
there or what it was called.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.context.assembler import assemble_from_signal
from app.domain import models
from app.domain.enums import Role
from app.trust import pseudonym, vault

ORG = "org_leak"
CUSTOMER_NAME = "Bharat Forge Precision Components"
PRODUCT_NAME = "Sandvik CoroMill 390 Insert"


class Recorder:
    """A provider that records what it was given and returns valid output."""

    name = "recorder"
    model = "recorder-1"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.seen.append(f"{system}\n{user}")
        return (
            '{"should_surface": true, "concise_title": "Margin slipped",'
            ' "explanation": "The relationship is earning less than it did.",'
            ' "recommended_action": "Review the price.",'
            ' "priority_adjustment": 0, "cannot_recommend_reliably": false,'
            ' "cited_fact_labels": [], "cited_signal_ids": []}')

    def everything_sent(self) -> str:
        return "\n".join(self.seen)


@pytest.fixture()
def seeded(session):
    session.add(models.Organization(organization_id=ORG, name="Leak Test",
                                    currency="INR", config={}))
    session.add(models.Customer(customer_id="c1", organization_id=ORG,
                                external_id="z1", name=CUSTOMER_NAME))
    session.add(models.Product(product_id="p1", organization_id=ORG,
                               external_id="zp1", name=PRODUCT_NAME))
    session.flush()
    vault.backfill(session, ORG)
    return session


def _signal(session, entity_type="CUSTOMER", entity_id="c1"):
    row = models.Signal(
        organization_id=ORG, signal_type="MARGIN_DETERIORATION",
        subject_entity_type=entity_type, subject_entity_id=entity_id,
        detector_version="v0", threshold_config_version="th_x", window={},
        severity_base=50,
        metrics={"margin_recent": 0.19, "margin_prior": 0.26,
                 "revenue_recent": 480000},
        sufficiency={"level": "SUFFICIENT", "missing_fields": [], "anomalies": []},
        evidence_refs=[], detected_at=date.today() - timedelta(days=1))
    session.add(row)
    session.flush()
    return row


# ── the bundle boundary ─────────────────────────────────────────────────────
def test_the_assembler_does_not_put_a_name_in_the_prompt(seeded):
    import json

    signal = _signal(seeded)
    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    payload = json.dumps(bundle.to_prompt_json(), default=str)

    assert CUSTOMER_NAME not in payload
    assert "Bharat" not in payload
    assert pseudonym.label_for(ORG, "CUSTOMER", "c1") in payload


def test_the_assembler_keeps_the_name_for_re_hydration(seeded):
    signal = _signal(seeded)
    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    label = pseudonym.label_for(ORG, "CUSTOMER", "c1")
    assert bundle.display_names.get(label) == CUSTOMER_NAME


def test_a_product_subject_is_pseudonymous_too(seeded):
    import json

    signal = _signal(seeded, entity_type="PRODUCT", entity_id="p1")
    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    payload = json.dumps(bundle.to_prompt_json(), default=str)

    assert PRODUCT_NAME not in payload
    assert "Sandvik" not in payload


# ── the provider boundary: the one that actually matters ────────────────────
def test_nothing_a_provider_receives_contains_a_name(seeded):
    from app.ai.interpret import interpret

    signal = _signal(seeded)
    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    recorder = Recorder()

    interpret(bundle, recorder, signal_type="MARGIN_DETERIORATION",
              metrics=signal.metrics, subject_label=bundle.subject_ref["label"])

    assert recorder.seen, "the provider was never called; this test proves nothing"
    sent = recorder.everything_sent()
    for fragment in (CUSTOMER_NAME, "Bharat", "Forge"):
        assert fragment not in sent, f"{fragment!r} reached the provider"


def test_the_user_still_sees_the_real_name(seeded):
    """Pseudonymisation the user pays for is not a feature."""
    from app.ai.interpret import interpret
    from app.trust import rehydrate

    signal = _signal(seeded)
    bundle = assemble_from_signal(seeded, signal, Role.OWNER)

    # A figure from the bundle's own facts, quoted verbatim: the gate rejects a
    # surfaced reading of numeric facts that names none, so a fake provider that
    # writes prose only would degrade and this test would be measuring the
    # fallback's wording instead of rehydration.
    figure = next(f.value for f in bundle.facts
                  if isinstance(f.value, (int, float)) and not isinstance(f.value, bool))

    class NamesTheSubject(Recorder):
        def complete(self, system: str, user: str) -> str:
            self.seen.append(f"{system}\n{user}")
            label = bundle.subject_ref["label"]
            return (
                f'{{"should_surface": true, "concise_title": "{label} margin slipped",'
                f' "explanation": "{label} is earning less than it did ({figure}).",'
                ' "recommended_action": "Review the price.",'
                ' "priority_adjustment": 0, "cannot_recommend_reliably": false,'
                ' "cited_fact_labels": [], "cited_signal_ids": []}')

    result = interpret(bundle, NamesTheSubject(), signal_type="MARGIN_DETERIORATION",
                       metrics=signal.metrics,
                       subject_label=bundle.subject_ref["label"])
    rehydrate.result(result, bundle.display_names)

    assert CUSTOMER_NAME in result.concise_title
    assert CUSTOMER_NAME in result.explanation
    assert "C-" not in result.concise_title, "a pseudonym leaked to the screen"


def test_the_payload_passes_its_own_disclosure_check(seeded):
    """The published statement and the real payload, measured against each other."""
    from app.ai.interpret import interpret
    from app.trust import disclosure

    signal = _signal(seeded)
    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    recorder = Recorder()
    interpret(bundle, recorder, signal_type="MARGIN_DETERIORATION",
              metrics=signal.metrics, subject_label=bundle.subject_ref["label"])

    assert disclosure.check(recorder.everything_sent()) == []


def test_the_payload_is_carried_out_for_logging(seeded):
    from app.ai.interpret import interpret

    signal = _signal(seeded)
    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    result = interpret(bundle, Recorder(), signal_type="MARGIN_DETERIORATION",
                       metrics=signal.metrics,
                       subject_label=bundle.subject_ref["label"])
    assert result.prompt_payload, (
        "The seam cannot log what it is not handed, and an unlogged call is an "
        "undisclosed one.")


def test_nothing_is_recorded_as_sent_when_nothing_was_sent(seeded):
    """Insufficient evidence suppresses the call up front. The payload must be
    None, not an empty string that would produce a misleading log row."""
    from app.ai.interpret import interpret

    signal = _signal(seeded)
    signal.sufficiency = {"level": "INSUFFICIENT", "reasons": ["thin"]}
    seeded.flush()

    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    recorder = Recorder()
    result = interpret(bundle, recorder, signal_type="MARGIN_DETERIORATION",
                       metrics=signal.metrics,
                       subject_label=bundle.subject_ref["label"])

    assert recorder.seen == []
    assert result.prompt_payload is None


# ── nested references: the shapes that actually leaked ──────────────────────
def test_an_entity_reference_nested_in_metrics_is_pseudonymised(seeded):
    """The decline detector reports ``top_declining_products`` as
    ``{"product_id": …, "label": "<real item name>"}``. Pseudonymising only the
    subject left that name in the fact list and sent it to the provider."""
    import json

    signal = _signal(seeded)
    signal.metrics = {
        **signal.metrics,
        "top_declining_products": [
            {"product_id": "p1", "label": PRODUCT_NAME, "recent_revenue": 0.0},
        ],
    }
    seeded.flush()

    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    payload = json.dumps(bundle.to_prompt_json(), default=str)

    assert PRODUCT_NAME not in payload
    assert "Sandvik" not in payload
    assert pseudonym.label_for(ORG, "PRODUCT", "p1") in payload
    assert bundle.display_names[pseudonym.label_for(ORG, "PRODUCT", "p1")] == PRODUCT_NAME


def test_a_name_list_beside_an_id_list_is_pseudonymised(seeded):
    """The cost detector emits ``affected_customers`` (names) alongside
    ``affected_customer_ids``. A bare list of strings has no id of its own, so
    the pairing is what makes it recoverable."""
    import json

    signal = _signal(seeded)
    signal.metrics = {
        **signal.metrics,
        "affected_customer_ids": ["c1"],
        "affected_customers": [CUSTOMER_NAME],
    }
    seeded.flush()

    bundle = assemble_from_signal(seeded, signal, Role.OWNER)
    payload = json.dumps(bundle.to_prompt_json(), default=str)

    assert CUSTOMER_NAME not in payload
    assert pseudonym.label_for(ORG, "CUSTOMER", "c1") in payload


def test_a_name_with_no_id_is_flagged_rather_than_silently_sent(seeded):
    """No rule can recover an id that is not there. The guarantee in that case
    is that it is *recorded as a finding*, not that it is caught."""
    from app.trust import disclosure

    payload = f'{{"facts": [{{"label": "mystery", "value": "{CUSTOMER_NAME}"}}]}}'
    findings = disclosure.check_names(seeded, ORG, payload)
    assert findings and CUSTOMER_NAME in findings[0]


def test_the_name_checker_does_not_cry_wolf(seeded):
    from app.trust import disclosure

    clean = ('{"subject": "Customer C-KQNVXB", "facts": '
             '[{"label": "margin_recent", "value": 0.19}]}')
    assert disclosure.check_names(seeded, ORG, clean) == []


def test_a_name_exactly_at_the_minimum_length_is_still_checked(seeded):
    """Names shorter than the floor are skipped because they appear inside
    ordinary words, but a name *of* the floor length is checked — and the two
    sides of that line are what decide whether a real customer name leaving the
    process is reported or passed over in silence.

    The floor is a false-positive control. Tightening it by one character costs a
    genuine finding, and a leak checker that misses is worse than one that is
    merely noisy.
    """
    from app.trust import disclosure

    at_floor = "A" * disclosure._MIN_NAME          # exactly the minimum
    below_floor = "B" * (disclosure._MIN_NAME - 1)  # one short
    seeded.add(models.Customer(customer_id="c_min", organization_id=ORG,
                               external_id="z_min", name=at_floor))
    seeded.add(models.Customer(customer_id="c_short", organization_id=ORG,
                               external_id="z_short", name=below_floor))
    seeded.flush()

    findings = disclosure.check_names(seeded, ORG, f'{{"value": "{at_floor}"}}')
    assert findings and at_floor in findings[0], (
        "a name of exactly the minimum length must still be reported")

    assert disclosure.check_names(
        seeded, ORG, f'{{"value": "{below_floor}"}}') == []
