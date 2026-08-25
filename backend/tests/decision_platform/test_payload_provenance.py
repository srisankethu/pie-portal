"""A logged prompt names the call it was sent on.

Two tables split one fact by design. ``AiCallLog`` records how a call went —
status, latency, tokens, estimated cost — and deliberately records no content.
``ModelPayload`` records the content and none of the operational facts. The
column that joins them, ``model_payloads.ai_call_log_id``, existed with its
foreign key from the start and was passed a hard-coded ``None`` at both call
sites, so every row was orphaned and "what did we send on the call that failed"
meant matching two tables on organization, decision type and a timestamp — and
hoping the run had been quiet.

The interesting test here is the second one. Asserting that ``log_result``
stores an id it was handed proves the parameter works and nothing about whether
anyone passes it; the defect was never in the function. So the call sites are
*parsed*, in the style of the ``sum(… or 0)`` guard next door, because that is
the shape the regression takes: somebody re-adds a caller and leaves the id out.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.config import settings
from app.domain import models
from app.trust import disclosure

ORG = "org_payload"
_APP = pathlib.Path(__file__).resolve().parents[2] / "app"


class _Result:
    """The shape `log_result` reads off an `AIResult` — nothing more."""

    prompt_payload = "system+user text that actually went to the provider"
    provider = "mock"
    model = "mock-1"


@pytest.fixture()
def org(session, monkeypatch):
    monkeypatch.setattr(settings, "AI_LOG_PAYLOADS", True)
    session.add(models.Organization(organization_id=ORG, name="Payload"))
    session.flush()
    return ORG


def _call_log(session) -> models.AiCallLog:
    row = models.AiCallLog(
        organization_id=ORG, decision_type="CUSTOMER_DECLINE",
        provider="mock", model="mock-1", prompt_version="v1",
        context_hash="ctx", ai_status="OK", provider_called=True)
    session.add(row)
    session.flush()
    return row


def test_a_logged_payload_points_at_a_real_call_row(session, org):
    call = _call_log(session)
    row = disclosure.log_result(
        session, organization_id=ORG, decision_type="CUSTOMER_DECLINE",
        result=_Result(), ai_call_log_id=call.ai_call_log_id)
    session.flush()

    assert row is not None
    assert row.ai_call_log_id == call.ai_call_log_id
    # And the join actually resolves, rather than the id merely matching a
    # string — the foreign key is the thing being relied on.
    assert session.get(models.AiCallLog, row.ai_call_log_id) is not None


def test_no_call_site_hard_codes_the_id_away():
    """The defect, in the shape it will come back.

    ``ai_call_log_id=None`` written literally at a call site is how the column
    stayed empty for the whole life of the feature — the parameter was there and
    correct, and both callers filled it with nothing. ``record`` is exempt: it
    is the writer, and ``None`` is a legitimate default on its own signature.
    """
    offenders: list[str] = []
    for path in sorted(_APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — not our files
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if callee == "record":
                continue
            for kw in node.keywords:
                if (kw.arg == "ai_call_log_id"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is None):
                    offenders.append(f"{path.name}:{kw.value.lineno}")
    assert offenders == [], (
        f"a caller passes ai_call_log_id=None literally in {offenders}. The "
        "payload is then unjoinable to the call it was sent on, which is the "
        "whole reason the column exists.")


def test_the_guard_above_catches_the_shape_it_names():
    """A check that cannot fail is one nobody should trust."""
    tree = ast.parse("log_result(session, ai_call_log_id=None)\n"
                     "record(session, ai_call_log_id=None)\n")
    found = [kw for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "attr", getattr(node.func, "id", "")) != "record"
             for kw in node.keywords
             if kw.arg == "ai_call_log_id"
             and isinstance(kw.value, ast.Constant) and kw.value.value is None]
    assert len(found) == 1, "the exemption for `record` must not swallow the rest"


def test_a_deployment_without_telemetry_still_keeps_the_payload(session, org):
    """``None`` stays a real state rather than a defect. With
    ``AI_TELEMETRY_ENABLED`` off there is no call row to point at, and the
    content is still worth keeping — the disclosure promise is about what was
    sent, not about whether the operational half was recorded."""
    row = disclosure.log_result(
        session, organization_id=ORG, decision_type="CUSTOMER_DECLINE",
        result=_Result(), ai_call_log_id=None)
    session.flush()

    assert row is not None
    assert row.ai_call_log_id is None
    # `record` stores system and user joined by a rule of its own, and
    # `log_result` supplies an empty system — so the payload is contained in
    # the revelation rather than equal to it. Asserted as containment on
    # purpose: pinning the separator here would make this test fail for a
    # change to formatting that has nothing to do with what it is about.
    assert _Result.prompt_payload in disclosure.reveal(session, row)
