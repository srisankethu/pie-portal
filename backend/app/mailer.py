"""Sending one email, to one person, about something that just happened.

A narrow seam over a mail provider, in the shape ``ai/provider.py`` already
uses here: a ``Protocol`` with one method, a live implementation, a mock that is
the offline default, and a ``select_sender()`` that **never raises**. The
reasons are the same ones that module gives, and one more that is specific to
this caller.

**It never raises, because the only caller is a public form.** ``ai/provider``
falls back to the mock so a misconfiguration cannot turn the decisions screen
into a 500. Here the stake is higher: the one caller is
``POST /api/v1/contact``, which is unauthenticated, is the last step of the only
path a buyer has, and must answer 202 whether or not a mail provider is
reachable. An enquiry is durable in ``contact_requests`` before this module is
ever called; nothing it does may put that at risk. So ``send`` returns a
:class:`SendResult` and lets no exception out — not a provider error, not a
timeout, not a ``TypeError`` from a payload shape the SDK did not like.

**Mock unless a key is set.** ``RESEND_API_KEY`` empty means
:class:`MockSender`, which records in memory and sends nothing. That is the
whole of the "safe test mode": a suite, a developer's laptop and a deployment
that has not chosen a sender all take the same branch, and there is no
configuration under which the tests can reach the network.

**The key is never logged, and neither is a provider's error message.**
``alerts.py`` learned this one first: an exception's *string* can carry the
credential or the URL that is the credential, so what goes in the log is the
exception's class name. ``observability/logs.redact`` would catch an ``api_key``
in a line that got past this, but that is the second line of defence and this is
the first.

**Both bodies, always.** ``html`` and ``text`` are required by
:class:`EmailMessage` rather than optional, because a message with only one is a
message that renders as nothing in somebody's client, and the one who finds out
is the person who was supposed to be told an enquiry arrived.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Protocol, TypedDict

from .config import settings

log = logging.getLogger("pie_portal.mailer")


class EmailMessage(TypedDict):
    """One message, fully composed. Typed so a caller cannot forget a half.

    ``to`` is a list because every provider takes one and a single-recipient
    string is the shape that quietly stops working the day somebody wants two.
    ``reply_to`` is what makes the demo notification answerable: the operator
    presses reply and reaches the prospect rather than the sending domain.
    """

    to: list[str]
    subject: str
    html: str
    text: str
    reply_to: str


@dataclass(frozen=True)
class SendResult:
    """What happened. Never an exception — see the module docstring.

    ``message_id`` is the provider's own id for the message, kept because it is
    the only handle that ties a row in this database to a delivery in Resend's
    dashboard when somebody asks "did that actually go out". ``error`` holds an
    exception *class name* or a short reason, never a provider message and
    never a key.
    """

    ok: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class EmailSender(Protocol):
    """The entire contract. One method, because there is one thing to do.

    Segregated on purpose (CLAUDE.md §5, I): both implementations below use all
    of it, which is the test that says this is one protocol rather than two.
    """

    name: str

    def send(self, message: EmailMessage) -> SendResult: ...


class MockSender:
    """Records what it was asked to send, and sends nothing.

    The dev and test default, and the reason no test in this repository can
    reach a mail provider by accident: reaching one requires a key, and a key
    is what this class's existence is conditioned on being absent.

    ``sent`` is a list rather than a last-message slot so a test can assert on
    *how many* messages went — which is the assertion the duplicate-prevention
    rule actually needs.
    """

    name = "mock"

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> SendResult:
        self.sent.append(message)
        # A stable synthetic id, shaped like a real one so the column that
        # stores it is exercised offline rather than only in production.
        return SendResult(ok=True, message_id=f"mock-{len(self.sent)}")


class ResendSender:
    """Resend, through its official SDK.

    The SDK keeps its configuration in module-level globals, which is not a
    shape this codebase uses anywhere else and is the one awkward thing about
    it. Both are set per call rather than once at import:

    - ``resend.api_key`` defaults to reading ``RESEND_API_KEY`` from the
      environment *itself*, at import. That is one URL-shaped second source of
      truth away from the rule §4 is built on, and it would quietly ignore a
      test that monkeypatched ``settings``. Assigning it on every send makes
      ``config.py`` the only thing that decides, which is the rule.
    - ``resend.default_http_client`` is a ``RequestsClient`` whose timeout
      defaults to 30 seconds. Thirty seconds inside a public form is a form
      that appears to hang, so it is rebuilt at the configured timeout.

    The import sits inside ``send``, below the ``try``, which is lazier than
    ``ai/provider``'s lazy import and deliberately so. Two things fall out of
    it: importing this module — which ``app.contact`` does on every request —
    never pulls the SDK into the graph of a deployment that has no key, and a
    missing package becomes an ordinary failed send rather than a second kind
    of failure that ``select_sender`` would have to know about. Python caches
    modules, so the cost after the first call is a dict lookup.
    """

    name = "resend"

    def send(self, message: EmailMessage) -> SendResult:
        try:
            return self._send(message)
        except Exception as e:  # noqa: BLE001 — the caller must never see this
            # The class, not the string. An SDK error message can quote the
            # request it failed on, and that request carries the key. This also
            # catches ImportError, which is the deployment that set a key and
            # never installed the package.
            log.warning("resend send failed: %s", type(e).__name__)
            return SendResult(ok=False, error=type(e).__name__)

    def _send(self, message: EmailMessage) -> SendResult:
        import resend  # noqa: PLC0415 — lazy on purpose; see the docstring

        resend.api_key = settings.RESEND_API_KEY
        # int(): the SDK passes this straight to `requests`, which takes a
        # float fine, but the setting is read as one and the SDK's own type is
        # `int` — narrowing here keeps a mismatch out of the provider call.
        resend.default_http_client = resend.RequestsClient(
            timeout=int(settings.RESEND_TIMEOUT_SECONDS))
        payload = {
            "from": settings.RESEND_FROM,
            "to": message["to"],
            "subject": message["subject"],
            "html": message["html"],
            "text": message["text"],
        }
        # Omitted rather than sent empty: a provider given `reply_to: ""` may
        # take it literally, and a reply addressed to nowhere is worse than a
        # reply addressed to the sending domain.
        if message["reply_to"]:
            payload["reply_to"] = message["reply_to"]
        sent = resend.Emails.send(payload)
        # The SDK returns a mapping; `.get` rather than `[...]` because a
        # provider that answered without an id is a send that happened and
        # cannot be pointed at, which is not the same failure as not sending.
        message_id = (sent or {}).get("id") if hasattr(sent, "get") else None
        if not message_id:
            log.warning("resend accepted a message but named no id")
        return SendResult(ok=True, message_id=message_id)


def configured() -> bool:
    """Whether this deployment can actually send mail.

    All three are required and none has a sensible default. A key with no
    ``RESEND_FROM`` is a deployment that would fail on every send with a
    provider error; saying so here makes it a startup-shaped problem instead of
    a per-enquiry one.
    """
    return bool((settings.RESEND_API_KEY or "").strip()
                and (settings.RESEND_FROM or "").strip()
                and recipients())


def recipients() -> list[str]:
    """Who gets told. Comma-separated in one variable, because a deployment
    that wants two addresses should not need a second setting to say so."""
    return [a.strip() for a in (settings.RESEND_TO or "").split(",") if a.strip()]


def select_sender() -> EmailSender:
    """The sender this deployment should use. Never raises.

    A configuration that cannot send mail gets :class:`MockSender` and a log
    line saying so, rather than an exception on the path of a public form. The
    line is at WARNING for a deployment that set *some* of the settings — that
    is somebody halfway through configuring it, and silence there is how a
    demo request goes unannounced for a week.
    """
    if not configured():
        if (settings.RESEND_API_KEY or "").strip():
            log.warning("RESEND_API_KEY is set but RESEND_FROM/RESEND_TO are "
                        "not; no email will be sent")
        return MockSender()
    return ResendSender()
