"""Telling a person something happened, when nobody is looking at a screen.

One function and one setting, deliberately. This exists because of a gap rather
than an ambition: the demo-request form wrote its rows, `app/contact.py` listed
them, and nothing in between ever said an enquiry had *arrived* — so the only
thing standing between a buyer and silence was somebody remembering to run a
command. The operator console made that queue visible; it did not make it
noticed, and those are different problems.

**A webhook, not a mail server.** There is no SMTP anywhere in this application
and this is not the change that should add one: mail means a dependency, a
credential, a sender domain and a deliverability problem, in exchange for
reaching one person who already has a phone. A URL reaches Slack, Telegram,
ntfy, a WhatsApp provider or a two-line relay, and which one is a deployment's
choice rather than this module's.

**The body is Slack-shaped**, `{"text": …}`, because that shape is accepted
unchanged by Slack, Mattermost and most relays, and because a body somebody has
to template is a body somebody gets wrong once and never notices. Anything that
wants a different shape goes behind a relay — `docs/hosting.md` has the two
lines for Telegram, which is the one worth naming because it needs a `chat_id`.

**It reports failure rather than raising.** Every caller here is a scheduled
job whose real work is a database sweep; an unreachable webhook must not make
that job fail, and it must not make the job *pretend to have succeeded* either.
So `deliver` returns whether it worked, and the caller stamps only what was
actually announced. This is §1's rule about absent evidence at the smallest
possible scale: a send that did not happen is not a send.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from .config import settings

log = logging.getLogger("pie_portal.alerts")


def configured() -> bool:
    """Whether this deployment has anywhere to send an alert.

    Read before composing one, so a deployment with no webhook says so once at
    the top of a command rather than silently doing nothing on a schedule
    forever — which is the failure this whole module is about.
    """
    return bool((settings.ALERT_WEBHOOK or "").strip())


def _headers() -> dict:
    """Extra headers, for a destination that needs a token.

    Parsed here rather than at import so a malformed value is a logged warning
    on the run that needed it, not an application that will not start. The
    values are secrets: they are never logged, and `observability/logs.redact`
    would catch them in an httpx line even if they were.
    """
    raw = (settings.ALERT_WEBHOOK_HEADERS or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.warning("ALERT_WEBHOOK_HEADERS is not valid JSON; sending without it")
        return {}
    if not isinstance(parsed, dict):
        log.warning("ALERT_WEBHOOK_HEADERS is not a JSON object; sending without it")
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def deliver(text: str, *, extra: Optional[dict] = None) -> bool:
    """Send one alert. Returns whether it arrived, and never raises.

    ``extra`` is merged into the body for a destination that needs a field of
    its own — Telegram's ``chat_id`` is the one this was written for. It is
    merged rather than replacing ``text``, so a caller cannot accidentally send
    a body with nothing to read in it.
    """
    if not configured():
        return False
    # `text` last, so `extra` genuinely cannot replace it. Spread the other way
    # round this reads the same and is not: a caller passing `{"text": ""}`
    # would send a body with nothing in it, the webhook would answer 200, and
    # this would report a successful alert that said nothing — which is worse
    # than a failure, because a failure gets retried.
    body = {**(extra or {}), "text": text}
    try:
        response = httpx.post(
            settings.ALERT_WEBHOOK.strip(), json=body, headers=_headers(),
            timeout=settings.ALERT_TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        # The class, not the string: an httpx error message can carry the URL,
        # and the URL is the credential for most webhook destinations.
        log.warning("alert webhook failed: %s", type(e).__name__)
        return False
    if response.status_code >= 400:
        log.warning("alert webhook answered %s", response.status_code)
        return False
    return True
