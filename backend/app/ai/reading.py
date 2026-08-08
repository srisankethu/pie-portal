"""Read an enquiry written as prose into the rows the parser can resolve.

The Quote Builder's intake has always been a regular expression::

    ^(.*?)[\\s,\\t]+x?\\s*(\\d+)\\s*$

which reads ``CNMG 120408-MP, 50`` and gives up on how customers actually
write. "need 50 nos CNMG 120408 MP grade for SS work — also 20 of the 25mm
boring bar we took last time" is one WhatsApp message and zero usable lines.
Everything downstream is excellent at resolving a clean code; nothing was ever
going to read a sentence.

**What this is allowed to produce, and what it is not.** It returns a *code* and
a *quantity* per line, and nothing else. It does not match an item, choose a
supply option, or price anything — pie-parser resolves the code exactly as it
does for typed input, against the same catalogue and the same score bands. So
the model does segmentation and normalisation of language, which is the thing it
is genuinely good at, and the identity of the tool stays with the engine that
was built to decide it.

That split is also what keeps ``ai/`` free of ``commercial/``: this module
cannot price a line because it has no way to reach the code that could.

**Every line it proposes is unconfirmed.** ``CNMG 120408-MP`` and
``CNMG 120408-MS`` are different tools, and a model that reads one as the other
is wrong in a way that reaches a customer. A person confirms each line before
the quote can be sent; ``needsConfirmation`` is what the estimate gate reads.

**It degrades to the regex.** No provider, a refused call, malformed output or
a line the model declined all fall back to ``_split_rfq``, so the worst case is
the product exactly as it was before this existed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import settings
from .provider import AIProvider

log = logging.getLogger("pie_portal.ai.reading")

#: A single enquiry should not become a hundred lines. A model that loops, or a
#: pasted email thread that quotes itself three times, both end here rather than
#: in a quote nobody can review.
MAX_LINES = 60

#: Longer than a long WhatsApp enquiry and far shorter than a pasted thread.
#: Past this the text is truncated rather than refused — the first part of a
#: message is where the request is, and the signature is not.
MAX_CHARS = 6000

PROMPT_VERSION = "rfq-read-1"

_SYSTEM = """You read purchase enquiries for an industrial cutting-tool \
distributor and turn them into structured lines.

Return JSON only: {"lines": [{"code": str, "qty": int, "verbatim": str, \
"reading": str}]}

  code      the item as the customer identified it, cleaned of surrounding
            prose. Keep the manufacturer's nomenclature exactly as written -
            grade letters, dashes and suffixes are part of the identity and
            CNMG 120408-MP is not CNMG 120408-MS. Never invent, expand or
            correct a code you are unsure of; copy what is there.
  qty       the quantity asked for. Use 1 only when the text gives no number.
  verbatim  the span of the original message this line came from.
  reading   a short note ONLY where you had to interpret something - an
            abbreviation you expanded, a quantity implied rather than stated,
            a reference to a previous order you could not resolve. Empty
            otherwise.

Rules:
- One line per item requested. Do not merge two items or split one.
- Do not resolve the item against any catalogue. You are reading, not matching.
- Do not price anything, and do not comment on price or availability.
- If a fragment names no item, leave it out rather than guessing at one.
- If you cannot read the message at all, return {"lines": []}."""


@dataclass
class ProposedLine:
    code: str
    qty: int
    #: The span of the customer's message this came from, shown beside the line
    #: so a person confirms against what was actually written rather than
    #: against the model's tidied version of it.
    verbatim: str
    #: Why this line is not simply what the message said, where that applies.
    #: Empty for a line read straight off the text.
    reading: str = ""

    def to_row(self) -> dict[str, Any]:
        """The shape ``store.build_lines`` already consumes."""
        return {"raw": self.verbatim or self.code, "code": self.code,
                "qty": max(1, int(self.qty or 1)),
                "proposed": True, "reading": self.reading}


@dataclass
class ReadResult:
    lines: list[ProposedLine] = field(default_factory=list)
    #: "ok" when the model read it, "fallback" when the regex did. Carried so
    #: the screen can say which, and so telemetry can tell a quiet failure from
    #: an enquiry that genuinely had one clean line per row.
    status: str = "fallback"
    detail: str = ""

    @property
    def used_ai(self) -> bool:
        return self.status == "ok"


def _clean(raw: str) -> str:
    return " ".join(str(raw or "").split())[:200]


def read(text: str, provider: Optional[AIProvider]) -> ReadResult:
    """Propose lines for one enquiry. Never raises."""
    body = (text or "").strip()
    if not body:
        return ReadResult(status="fallback", detail="empty enquiry")
    if provider is None or settings.AI_PROVIDER == "mock":
        # Not an error. The regex is the product's behaviour without a provider
        # configured, and saying so beats a screen that looks broken.
        return ReadResult(status="fallback", detail="no AI provider configured")

    try:
        raw = provider.complete(system=_SYSTEM, user=body[:MAX_CHARS])
    except Exception as e:  # noqa: BLE001 — a reading failure must not lose the enquiry
        log.warning("rfq reading failed: %s", e)
        return ReadResult(status="fallback", detail=f"provider error: {e}"[:200])

    try:
        parsed = json.loads(_strip_fence(raw))
        rows = parsed["lines"]
        if not isinstance(rows, list):
            raise TypeError("lines is not a list")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("rfq reading returned unusable output: %s", e)
        return ReadResult(status="fallback", detail=f"unusable output: {e}"[:200])

    out: list[ProposedLine] = []
    for row in rows[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        code = _clean(row.get("code"))
        if not code:
            continue
        try:
            qty = int(row.get("qty") or 1)
        except (TypeError, ValueError):
            qty = 1
        out.append(ProposedLine(code=code, qty=max(1, qty),
                                verbatim=_clean(row.get("verbatim")) or code,
                                reading=_clean(row.get("reading"))))

    if not out:
        # The model read it and found nothing quotable. The regex may still
        # split a list of bare codes, so it gets its turn rather than the
        # enquiry ending here.
        return ReadResult(status="fallback", detail="no lines read")
    return ReadResult(lines=out, status="ok")


def _strip_fence(raw: str) -> str:
    """Tolerate a ```json fence. Cheap, and the alternative is discarding an
    otherwise perfect reading over three backticks."""
    s = str(raw or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()
