"""Emailing a demo request to the person who has to answer it.

The webhook next door (`test_contact_alert.py`) tells somebody an enquiry
arrived. This tells them *and gives them something to reply to* — `Reply-To` is
the prospect, so answering is one keypress rather than a trip back to the
console to copy an address. Everything pinned here is a way that quietly fails.

  * **It sends twice.** A resubmitted form, a sweep racing the request, a
    retried worker. Pinned by calling the path twice and counting messages.
  * **It records a send that did not happen.** Marking SENT before the provider
    answered means an outage swallows an enquiry permanently and the retry
    never runs. Pinned by failing the send and asserting the row is retried.
  * **It loses the enquiry when the provider is down.** The single worst
    outcome available here, and the reason the send happens *after* the commit:
    pinned by failing every send and asserting the row is still there, the
    visitor still got 202, and the sweep still picks it up.
  * **It tells the visitor about our infrastructure.** A provider outage is not
    a stranger's problem and the response must not change shape because of one.
  * **It leaks the key.** Into a log line, into the message, into anything a
    test can read. Pinned by sweeping every string this path produces.
  * **It sends for real from a test run.** The default sender is a mock and
    reaching a provider requires a key, so there is no configuration under which
    this suite can send mail. `MockSender` is asserted to be that default.

The one deliberate residual: a crash between the provider accepting a message
and this committing `SENT` sends a second copy next sweep. Being told twice is
an annoyance; being told never is the defect — the same direction the webhook
chose, for the same reason.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import contact, mailer
from app.config import settings
from app.db import get_session
from app.domain import models

GOOD = {"company": "Acme Distributors", "name": "A. Buyer",
        "email": "buyer@acme.example", "phone": "+91 80 1234 5678",
        "erp": "Prophet 21", "plan": "intelligence",
        "message": "Three companies, one book each."}

#: Not a real key, and shaped like one on purpose: every leak assertion below
#: greps for this string, so it has to be distinctive enough that finding it
#: anywhere means something.
KEY = "re_test_dEadBeefNeverRealKey123"


@pytest.fixture()
def configured(monkeypatch):
    """A deployment that can send mail, sending it nowhere."""
    monkeypatch.setattr(settings, "RESEND_API_KEY", KEY)
    monkeypatch.setattr(settings, "RESEND_FROM", "PIE <hello@syncpie.com>")
    monkeypatch.setattr(settings, "RESEND_TO", "sales@syncpie.com")


class Boom:
    """A sender that always fails, the way a provider outage looks from here."""

    name = "boom"

    def __init__(self) -> None:
        self.attempts = 0

    def send(self, message):
        self.attempts += 1
        return mailer.SendResult(ok=False, error="ResendError")


@pytest.fixture()
def client(monkeypatch, configured):
    """The public router over HTTP, on an isolated database, with a mock sender.

    The sender is installed over `select_sender` rather than passed in, because
    the endpoint does not take one — what is under test is the wiring, and a
    test that injected past it would pass with the endpoint unwired.
    """
    from app import ratelimit
    from app.routers import onboarding as onboarding_router

    ratelimit.reset("contact")
    monkeypatch.setattr(settings, "CONTACT_RATE_LIMIT_PER_HOUR", 0)

    sender = mailer.MockSender()
    monkeypatch.setattr(mailer, "select_sender", lambda: sender)

    eng = dbsupport.fresh_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False,
                         future=True)

    app = FastAPI()
    app.include_router(onboarding_router.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    c.maker = maker
    c.sender = sender
    return c


def _only(client) -> models.ContactRequest:
    with client.maker() as session:
        rows = list(session.query(models.ContactRequest))
        assert len(rows) == 1, f"expected one enquiry, found {len(rows)}"
        return rows[0]


# ── the mailer on its own ────────────────────────────────────────────────────
def test_an_unconfigured_deployment_sends_nothing_and_says_so(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    assert mailer.configured() is False
    assert isinstance(mailer.select_sender(), mailer.MockSender)


def test_a_key_without_a_sender_address_is_not_configured(monkeypatch):
    """Halfway through configuring it is the state that must not look done."""
    monkeypatch.setattr(settings, "RESEND_API_KEY", KEY)
    monkeypatch.setattr(settings, "RESEND_FROM", "")
    monkeypatch.setattr(settings, "RESEND_TO", "sales@syncpie.com")
    assert mailer.configured() is False
    assert isinstance(mailer.select_sender(), mailer.MockSender)


def test_the_mock_is_the_default_so_a_test_run_cannot_send_mail(monkeypatch):
    monkeypatch.delattr(settings, "RESEND_API_KEY", raising=False)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    sender = mailer.select_sender()
    sender.send({"to": ["a@b.example"], "subject": "s", "html": "<p>h</p>",
                 "text": "t", "reply_to": ""})
    assert isinstance(sender, mailer.MockSender)
    assert len(sender.sent) == 1


def test_several_recipients_come_from_one_setting(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_TO", "a@x.example, b@x.example")
    assert mailer.recipients() == ["a@x.example", "b@x.example"]


def test_a_provider_that_raises_is_a_failed_send_not_an_exception(monkeypatch,
                                                                 configured):
    """The one rule the whole reliability story rests on: `send` never raises."""
    sender = mailer.ResendSender()

    def explode(payload):
        raise RuntimeError(f"connection to api.resend.com with {KEY} failed")

    import resend
    monkeypatch.setattr(resend.Emails, "send", staticmethod(explode))
    result = sender.send({"to": ["a@b.example"], "subject": "s",
                          "html": "<p>h</p>", "text": "t", "reply_to": ""})
    assert result.ok is False
    # The class name, never the message — that message carries the key.
    assert result.error == "RuntimeError"
    assert KEY not in (result.error or "")


def test_a_failed_send_never_puts_the_key_in_the_log(monkeypatch, configured,
                                                     caplog):
    sender = mailer.ResendSender()

    def explode(payload):
        raise RuntimeError(f"bad request, api_key={KEY}")

    import resend
    monkeypatch.setattr(resend.Emails, "send", staticmethod(explode))
    with caplog.at_level(logging.DEBUG):
        sender.send({"to": ["a@b.example"], "subject": "s", "html": "<p>h</p>",
                     "text": "t", "reply_to": ""})
    assert KEY not in caplog.text


def test_the_provider_is_given_the_reply_to_and_the_configured_sender(
        monkeypatch, configured):
    captured = {}

    import resend
    monkeypatch.setattr(resend.Emails, "send",
                        staticmethod(lambda p: captured.update(p) or {"id": "re_1"}))
    result = mailer.ResendSender().send(
        {"to": ["sales@syncpie.com"], "subject": "Demo request — Acme",
         "html": "<p>h</p>", "text": "t", "reply_to": "buyer@acme.example"})

    assert result.ok is True and result.message_id == "re_1"
    assert captured["from"] == "PIE <hello@syncpie.com>"
    assert captured["reply_to"] == "buyer@acme.example"
    assert captured["html"] and captured["text"]


def test_an_empty_reply_to_is_omitted_rather_than_sent_blank(monkeypatch,
                                                             configured):
    captured = {}
    import resend
    monkeypatch.setattr(resend.Emails, "send",
                        staticmethod(lambda p: captured.update(p) or {"id": "re_1"}))
    mailer.ResendSender().send({"to": ["a@b.example"], "subject": "s",
                                "html": "<p>h</p>", "text": "t", "reply_to": ""})
    assert "reply_to" not in captured


# ── what the message says ────────────────────────────────────────────────────
def test_the_message_carries_everything_a_reply_needs(session, configured):
    row = contact.capture(session, **{k: v for k, v in GOOD.items()})
    session.flush()
    message = contact.demo_email(row)

    assert message["reply_to"] == "buyer@acme.example"
    assert message["to"] == ["sales@syncpie.com"]
    assert "Acme Distributors" in message["subject"]
    for half in (message["html"], message["text"]):
        assert "A. Buyer" in half                       # name
        assert "buyer@acme.example" in half             # work email
        assert "Acme Distributors" in half              # company
        assert "Prophet 21" in half                     # what they run
        assert "Three companies, one book each." in half  # requirements
        assert str(row.created_at.year) in half         # submitted when


def test_both_halves_are_present_so_it_renders_somewhere(session, configured):
    row = contact.capture(session, company="Acme", name="A", email="a@b.example")
    session.flush()
    message = contact.demo_email(row)
    assert message["html"].strip() and message["text"].strip()
    assert "<" in message["html"] and "<" not in message["text"]


def test_a_visitor_cannot_put_markup_in_the_message(session, configured):
    """Free text from an unauthenticated form is the one input that becomes
    markup here, so it is the one that has to be escaped."""
    row = contact.capture(session, company="<script>alert(1)</script>",
                          name="A", email="a@b.example",
                          message='1 < 2 & "quoted"')
    session.flush()
    message = contact.demo_email(row)
    assert "<script>" not in message["html"]
    assert "&lt;script&gt;" in message["html"]
    assert "&amp;" in message["html"]


def test_a_field_the_visitor_left_blank_is_left_out(session, configured):
    row = contact.capture(session, company="Acme", name="", email="a@b.example")
    session.flush()
    text = contact.demo_email(row)["text"]
    assert "Phone" not in text and "Runs" not in text


# ── the form, end to end ─────────────────────────────────────────────────────
def test_a_submission_is_saved_and_emailed(client):
    res = client.post("/api/v1/contact", json=GOOD)
    assert res.status_code == 202, res.text

    row = _only(client)
    assert row.notification_status == contact.SENT
    assert row.notification_message_id           # the provider's own handle
    assert row.notification_error is None
    assert len(client.sender.sent) == 1
    assert client.sender.sent[0]["reply_to"] == "buyer@acme.example"


def test_invalid_input_is_refused_and_no_email_goes_out(client):
    res = client.post("/api/v1/contact", json={**GOOD, "email": "not-an-address"})
    assert res.status_code == 400
    with client.maker() as session:
        assert session.query(models.ContactRequest).count() == 0
    assert client.sender.sent == []


def test_an_enquiry_with_nobody_in_it_sends_nothing(client):
    res = client.post("/api/v1/contact",
                      json={**GOOD, "name": "", "company": ""})
    assert res.status_code == 400
    assert client.sender.sent == []


def test_a_database_failure_is_not_a_successful_submission(client, monkeypatch):
    """The visitor is told it worked only when the row is actually there."""
    def refuse(*a, **k):
        raise RuntimeError("neon is having a bad afternoon")

    monkeypatch.setattr(contact, "capture", refuse)
    with pytest.raises(RuntimeError):
        client.post("/api/v1/contact", json=GOOD)
    assert client.sender.sent == []


def test_an_email_failure_still_keeps_the_enquiry_and_still_answers_the_visitor(
        client, monkeypatch):
    """The whole point. A provider outage costs an announcement, never a lead."""
    boom = Boom()
    monkeypatch.setattr(mailer, "select_sender", lambda: boom)

    res = client.post("/api/v1/contact", json=GOOD)

    # The visitor is told the same thing they are always told.
    assert res.status_code == 202
    assert res.json() == {"received": True,
                          "note": "Thanks — we will come back to you at that address."}

    # And the enquiry is on the queue, flagged for the operator, awaiting retry.
    row = _only(client)
    assert row.status == contact.NEW
    assert row.notification_status == contact.FAILED
    assert row.notification_error == "ResendError"
    assert row.notification_message_id is None
    assert boom.attempts == 1


def test_the_visitor_is_told_nothing_about_the_database_or_the_provider(
        client, monkeypatch):
    monkeypatch.setattr(mailer, "select_sender", lambda: Boom())
    body = client.post("/api/v1/contact", json=GOOD).text
    for leak in ("resend", "Resend", "sql", "SQL", "neon", "Neon",
                 "notification_status", KEY):
        assert leak not in body


def test_the_response_is_the_same_whether_or_not_the_email_went(client,
                                                                monkeypatch):
    sent_ok = client.post("/api/v1/contact", json=GOOD)
    monkeypatch.setattr(mailer, "select_sender", lambda: Boom())
    failed = client.post("/api/v1/contact",
                         json={**GOOD, "email": "other@acme.example"})
    assert sent_ok.status_code == failed.status_code == 202
    assert sent_ok.json() == failed.json()


# ── once, and only once ──────────────────────────────────────────────────────
def test_the_same_row_is_never_emailed_twice(client):
    client.post("/api/v1/contact", json=GOOD)
    assert len(client.sender.sent) == 1

    with client.maker() as session:
        row = session.query(models.ContactRequest).one()
        # The sweep, a retried worker, a second call for any reason at all.
        assert contact.notify_one(session, row, sender=client.sender) is False
    assert len(client.sender.sent) == 1


def test_a_resubmitted_form_is_a_second_enquiry_and_a_second_message(client):
    """Two rows, two emails — a person who filled the form in twice asked twice,
    and neither is a duplicate of the other."""
    client.post("/api/v1/contact", json=GOOD)
    client.post("/api/v1/contact", json=GOOD)
    with client.maker() as session:
        assert session.query(models.ContactRequest).count() == 2
    assert len(client.sender.sent) == 2


def test_a_send_that_failed_is_retried_and_one_that_succeeded_is_not(
        session, configured):
    """What `unemailed` is for: never-attempted and failed are both outstanding,
    and SENT is the only state that is finished."""
    boom = Boom()
    row = contact.capture(session, **GOOD)
    session.flush()

    assert contact.notify_one(session, row, sender=boom) is False
    assert row.notification_status == contact.FAILED
    assert [r.contact_request_id for r in contact.unemailed(session)] \
        == [row.contact_request_id]

    good = mailer.MockSender()
    assert contact.notify_one(session, row, sender=good) is True
    assert row.notification_status == contact.SENT
    assert contact.unemailed(session) == []


def test_email_and_the_webhook_are_independent_channels(session, configured):
    """Turning mail on must not quietly switch the webhook off — a row emailed
    is still unannounced as far as the webhook's own stamp is concerned."""
    row = contact.capture(session, **GOOD)
    session.flush()
    contact.notify_one(session, row, sender=mailer.MockSender())

    assert row.notification_status == contact.SENT
    assert row.notified_at is None
    assert [r.contact_request_id for r in contact.unannounced(session)] \
        == [row.contact_request_id]


def test_an_unconfigured_deployment_stamps_nothing_at_all(session, monkeypatch):
    """Not even FAILED. "We do not send mail" is a different fact from "we tried
    and it did not work", and a row that recorded the second would be lying."""
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    row = contact.capture(session, **GOOD)
    session.flush()

    assert contact.notify_one(session, row) is False
    assert row.notification_status is None
    assert row.notification_error is None


def test_a_backlog_marked_as_history_is_not_emailed_as_news(session, configured):
    """`alert --mark-only` on a deployment turning mail on for the first time.

    SKIPPED rather than SENT, because nothing was sent — and still off the
    outstanding list, because announcing months of history as news is the
    failure the flag exists to prevent.
    """
    row = contact.capture(session, **GOOD)
    session.flush()
    row.notification_status = contact.SKIPPED
    session.flush()   # the fixture's session does not autoflush

    assert contact.unemailed(session) == []
    assert contact.notify_one(session, row, sender=mailer.MockSender()) is True, \
        "SKIPPED is a default, not a refusal — an operator asking for one send "
    assert row.notification_status == contact.SENT


def test_rows_nobody_has_tried_are_outstanding(session, configured):
    """The NULL case, which a plain `NOT IN` would silently drop — and it is
    most of the table."""
    first = contact.capture(session, **GOOD)
    second = contact.capture(session, **{**GOOD, "email": "b@acme.example"})
    session.flush()
    assert first.notification_status is None
    assert {r.contact_request_id for r in contact.unemailed(session)} == \
        {first.contact_request_id, second.contact_request_id}
