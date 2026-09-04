"""Telling somebody an enquiry arrived, and the three ways that quietly fails.

The alert exists because the queue was only ever read by somebody who decided
to read it. Adding a scheduled sender introduces its own silent failures, and
these are them:

  * **It says the same thing every run.** Twice a day, the same names, so it
    becomes noise and gets ignored — the original defect wearing a new costume.
    Fixed by `notified_at`, and pinned by asserting a second run is silent.
  * **It says a thing it did not send.** Stamping before delivering means a
    webhook outage swallows an enquiry permanently, and nothing would ever say
    so. Pinned by failing the delivery and asserting the row stays unannounced.
  * **It sends nothing and reports success.** A deployment with no destination
    running a silent job forever is the state this whole feature is meant to
    end. Pinned by asserting the unconfigured case stamps nothing.

The direction of the remaining failure is deliberate: a crash between sending
and committing announces something twice. Being told twice is an annoyance;
being told never is the defect.
"""
from __future__ import annotations

import pytest

from app import alerts, contact


@pytest.fixture()
def arrivals(session):
    """Two enquiries, neither announced."""
    contact.capture(session, company="Acme Tools", name="A Buyer",
                    email="buyer@acme.example", erp="Prophet 21",
                    plan="intelligence", message="Three companies, one book each.")
    contact.capture(session, company="Bharat Cutting", name="B Buyer",
                    email="b@bharat.example")
    session.flush()
    return contact.unannounced(session)


def test_everything_starts_unannounced(session, arrivals):
    assert len(arrivals) == 2
    assert all(r.notified_at is None for r in arrivals)


def test_stamping_makes_the_next_run_silent(session, arrivals):
    contact.mark_announced(session, arrivals)
    session.flush()
    assert contact.unannounced(session) == []


def test_an_enquiry_answered_before_the_alert_ran_is_still_announced(session):
    """The alert reports what *arrived*; the queue reports what is outstanding.

    Filtering the alert to NEW would make anything handled quickly vanish from
    the record of what came in — which is the number a week is judged by.
    """
    row = contact.capture(session, company="Fast", name="F",
                          email="f@fast.example")
    contact.mark_handled(session, row.contact_request_id, handled_by="sanketh")
    session.flush()
    assert [r.contact_request_id for r in contact.unannounced(session)] \
        == [row.contact_request_id]


def test_the_text_carries_what_arrived_and_what_is_outstanding(session, arrivals):
    text = contact.alert_text(arrivals, waiting=len(contact.pending(session)))
    assert "2 new enquiries" in text
    assert "Acme Tools" in text
    assert "buyer@acme.example" in text
    assert "runs Prophet 21" in text
    # The backlog, so a run that looks small does not hide a growing queue.
    assert "2 waiting for a reply in total." in text


def test_one_enquiry_is_not_called_one_enquiries(session):
    row = contact.capture(session, company="Solo", name="S", email="s@solo.example")
    session.flush()
    assert contact.alert_text([row], waiting=1).startswith("1 new enquiry\n")


def test_a_long_message_is_trimmed_rather_than_pasted_whole(session):
    row = contact.capture(session, company="Verbose", name="V",
                          email="v@verbose.example", message="x" * 1000)
    session.flush()
    text = contact.alert_text([row], waiting=1)
    assert "…" in text
    assert len(text) < 600


# ── delivery ────────────────────────────────────────────────────────────────
def test_no_webhook_configured_means_nothing_was_sent(monkeypatch):
    """`deliver` must answer False rather than raising or quietly returning
    True — the caller stamps on that answer."""
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK", "")
    assert alerts.configured() is False
    assert alerts.deliver("anything") is False


def test_a_webhook_that_refuses_is_not_a_delivery(monkeypatch):
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK", "https://example.invalid/hook")

    class Refused:
        status_code = 500

    monkeypatch.setattr(alerts.httpx, "post", lambda *a, **k: Refused())
    assert alerts.deliver("anything") is False


def test_a_webhook_that_cannot_be_reached_is_not_a_delivery(monkeypatch):
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK", "https://example.invalid/hook")

    def explode(*a, **k):
        raise alerts.httpx.ConnectError("no route")

    monkeypatch.setattr(alerts.httpx, "post", explode)
    # Never raises: the caller is a scheduled sweep whose real work is the
    # database, and an unreachable webhook must not fail the job.
    assert alerts.deliver("anything") is False


def test_a_delivery_sends_the_text_and_any_extra_field(monkeypatch):
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK", "https://example.invalid/hook")
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK_HEADERS",
                        '{"X-Token": "abc"}')
    sent = {}

    class Ok:
        status_code = 200

    def capture_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers)
        return Ok()

    monkeypatch.setattr(alerts.httpx, "post", capture_post)
    assert alerts.deliver("2 new enquiries", extra={"chat_id": "42"}) is True
    assert sent["json"] == {"text": "2 new enquiries", "chat_id": "42"}
    assert sent["headers"] == {"X-Token": "abc"}


def test_malformed_headers_do_not_stop_the_alert(monkeypatch):
    """A typo in one environment variable must not be the reason nobody was
    told. It is logged and the alert goes without them."""
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK", "https://example.invalid/hook")
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK_HEADERS", "not json")

    class Ok:
        status_code = 200

    monkeypatch.setattr(alerts.httpx, "post", lambda *a, **k: Ok())
    assert alerts.deliver("anything") is True


def test_extra_cannot_replace_the_text(monkeypatch):
    """A delivered alert that says nothing counts as success, and success is
    not retried — so it is worse than a failure. `extra` exists for a field a
    destination needs of its own; it must not be able to empty the message."""
    monkeypatch.setattr(alerts.settings, "ALERT_WEBHOOK", "https://example.invalid/hook")
    sent = {}

    class Ok:
        status_code = 200

    monkeypatch.setattr(alerts.httpx, "post",
                        lambda url, json=None, **k: (sent.update(json=json), Ok())[1])
    alerts.deliver("the news", extra={"text": "", "chat_id": "42"})
    assert sent["json"] == {"text": "the news", "chat_id": "42"}
