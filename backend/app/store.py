"""In-memory quote + line state, with role-gated serialization.

A ``Quote`` is a list of ``Line``s built from a pasted RFQ. Each line carries
the pie-parser resolution plus the Zoho-derived commercial facts, and derives
its own status (the design's READY / NEEDS ATTENTION / NOT IN BOOKS / NO PRICE
/ UNRESOLVED … states). Economics (cost, margin, below-floor) are computed here
but only serialized for a management principal — the sales client never receives
them.

State lives in process memory (single-node demo). A real deployment persists
quotes; the shape here is the persistence contract.
"""
from __future__ import annotations

import itertools
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from . import pricing
from .pie_service import Bands, Candidate, Resolution, pie_service
from .ingestion.errors import (SourceUnavailable, SourceWriteRefused,
                               SourceWriteUnknown)
from .zoho import (
    ZohoService,
)

_REL_LABELS = {
    "EXACT": "EXACT", "TECH": "TECH EQUIV", "COMPAT": "COMPATIBLE",
    "POSSIBLE": "POSSIBLE", "AMBIGUOUS": "AMBIGUOUS", "INCOMPATIBLE": "INCOMPATIBLE",
    "UNRESOLVED": "UNRESOLVED", "INSUFF": "INSUFF. INFO", "PIE_DOWN": "PIE OFFLINE",
    "NONE": "—",
}

_ids = itertools.count(1)

#: Distinguishes this process's ids from the last one's.
#:
#: Quotes live in memory and their ids restarted at ``q1`` on every boot, which
#: was harmless while nothing outside this module remembered them. It is not
#: harmless now: sending a quote writes ``QuoteDecision`` and ``QuoteOutcome``
#: rows keyed on the quote id, and the approval gate judges a quote by the
#: snapshots filed under its id. After a restart, a brand-new ``q1`` would
#: inherit the previous ``q1``'s snapshots and be refused — or worse, be
#: approved — on the strength of a quote nobody in the room had ever seen.
_RUN = f"{int(time.time()):x}"


def sales_tax_rate() -> float:
    """The headline sales-tax rate applied to a quote subtotal.

    A single blended rate is a simplification, and an honest one only while a
    deployment sells one tax treatment: Indian GST splits by HSN and by whether
    the buyer is in-state, EU VAT varies by member state, and US sales tax
    varies by county. What matters here is that the rate is *configuration* —
    the previous literal ``0.18`` made this quote screen an Indian screen in a
    way no amount of currency plumbing would have fixed.

    Line-level tax from the ERP supersedes this wherever it is available: a
    line resolved to an item whose books state a rate is taxed at that rate,
    and this is the fallback for a line that has none — a pasted RFQ before any
    ERP has seen it, or an item nobody has set a rate against.

    That sentence was false when it was first written, and is kept in the
    present tense now that it is true. There was no line-level tax anywhere in
    the backend: no ``tax_amount``, no ``tax_percentage``, no ``item_tax``. The
    whole tax system was this constant times the subtotal, while
    ``zoho_books_service`` sent no tax field on the estimate and let Zoho price
    each line from its own item settings — so the screen and the document the
    customer received disagreed on any item off the default rate, and the
    docstring claiming otherwise is what stopped anybody looking.

    ``Quote.to_dict`` reports how many lines this fallback was applied to. A
    docstring is not a control; the count is.
    """
    from .config import settings
    return settings.SALES_TAX_RATE


def sales_tax_label() -> str:
    """What the buyer's jurisdiction calls that tax — GST, VAT, Sales Tax."""
    from .config import settings
    return settings.SALES_TAX_LABEL


#: Words a buyer puts next to a number to mean "this many of them".
#:
#: Load-bearing twice over. They let a quantity be read out of "- 100 nos" and
#: "100 nos CNMG…", and — where one is present and *not* consumed — they are the
#: evidence that a quantity was stated and this parser failed to read it. That
#: second use is why the list is deliberately short: a word here that also occurs
#: inside a tool description would flag good lines, and a flag that is usually
#: wrong is one people learn to click through.
_UNIT_WORDS = r"(?:nos?|no\.|pcs?|pieces?|units?|ea|each|qty|quantity)"

#: A list marker a person typed to number their enquiry — "1." or "2)".
#:
#: Stripped before anything reads a quantity, and it must never be read *as* one:
#: the review's own example is `1. CNMG 120408-MP insert - 100 nos`, where taking
#: the marker as the quantity would quote one of a hundred and look deliberate.
#: The leading-quantity rule below cannot make that mistake because it requires a
#: unit word, which a list marker never has.
_LIST_MARKER = re.compile(r"^\(?\d{1,2}[.)]\s+")

#: How many digits a bare, space-separated trailing number may have and still be
#: read as a quantity.
#:
#: Four, because an insert code is six — `DNMG 150608`, `CNMG 120408`. The old
#: rule matched any trailing number after whitespace, so `DNMG 150608` came back
#: as code `DNMG` at a quantity of a hundred and fifty thousand: the code mangled
#: and the quantity invented, from a line a buyer would call perfectly ordinary.
#: A comma, an `x` or a unit word is explicit enough to lift the bound; bare
#: whitespace is not.
_BARE_QTY_DIGITS = 4

#: Tried in order, strongest evidence first, and the bare-number rule last
#: because it is the only one that guesses. Each names its quantity `qty` and the
#: rest of the line `code`.
_QTY_PATTERNS = (
    # "<code>, 100"  ·  "<code>, x100" — a comma is a deliberate separator.
    re.compile(r"^(?P<code>.*?)\s*,\s*x?\s*(?P<qty>\d+)\s*$", re.IGNORECASE),
    # "<code> x100"  ·  "<code>x100"
    re.compile(r"^(?P<code>.*?)\s*\bx\s*(?P<qty>\d+)\s*$", re.IGNORECASE),
    # "<code> - 100 nos"  ·  "<code> 100 pcs"  ·  "<code> qty 100 nos"
    # A unit word has to be present — that is what makes this safe on a code
    # whose own tail is numeric.
    re.compile(rf"^(?P<code>.*?)[\s,\t:\u2013\u2014-]+"
               rf"(?:(?:qty|quantity)[\s.:-]*)?(?P<qty>\d+)\s*{_UNIT_WORDS}\s*[.]?$",
               re.IGNORECASE),
    # "<code> qty 100" — the keyword standing in for the unit word.
    re.compile(r"^(?P<code>.*?)[\s,\t:\u2013\u2014-]+(?:qty|quantity)[\s.:-]*"
               r"(?P<qty>\d+)\s*[.]?$", re.IGNORECASE),
    # "100 nos <code>"  ·  "100 nos of <code>" — leading, and it needs a unit
    # word to be told apart from a code that starts with digits.
    re.compile(rf"^(?P<qty>\d+)\s*{_UNIT_WORDS}[\s.:]*(?:of\s+)?(?P<code>.+)$",
               re.IGNORECASE),
    # "qty 25 <code>"  ·  "qty: 25 nos <code>"
    re.compile(rf"^(?:qty|quantity)[\s.:-]*(?P<qty>\d+)\s*(?:{_UNIT_WORDS})?"
               rf"[\s.:]*(?:of\s+)?(?P<code>.+)$", re.IGNORECASE),
    # "<code>  100" — bare, and bounded. Last on purpose.
    re.compile(rf"^(?P<code>.*?)[\s\t]+(?P<qty>\d{{1,{_BARE_QTY_DIGITS}}})\s*$"),
)


def _split_rfq(text: str) -> List[Dict[str, Any]]:
    """Split pasted RFQ text into (raw, code, qty) rows.

    Accepts ``code, qty`` / ``code  qty`` / ``code xNN`` / ``code - NN nos`` /
    ``NN nos code`` / bare ``code``.

    **A quantity that was stated and not read is flagged, never defaulted.** The
    original rule matched only a trailing bare number, so every one of these came
    back as one unit, silently:

        CNMG 120408 TN2000 - 100 nos
        CNMG 120408-MP insert 100 nos
        100 nos CNMG 120408 TN2000

    Priced end to end that is a quotation out by a factor of a hundred, and it
    also picks the wrong quantity band, which is what decides the margin floor a
    manager signs against. Three of the five shapes a reviewer tried failed.

    A bare code still means one, because pasting a column of codes is a
    documented and common way to use this screen and flagging all of it would
    make the feature unusable. What is flagged is the middle case: a line
    carrying a unit word this parser did not turn into a quantity. The unit word
    is the evidence that a number was meant, so failing to find it is a gap to
    report rather than a default to take — `CLAUDE.md` §1, absence of evidence is
    not a pass.

    A flagged row travels as ``proposed`` with a ``reading``, which is the
    machinery a model-read line already uses: status ``CONFIRM READING``,
    technical, so ``blockers`` stops the estimate until a person clears it one
    line at a time. ``Line.reading``'s docstring named "a quantity implied" as an
    example from the beginning; this is the path that finally produces one.
    """
    rows: List[Dict[str, Any]] = []
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue

        body = _LIST_MARKER.sub("", raw).strip()
        code, qty, read = body, 1, ""

        for pattern in _QTY_PATTERNS:
            m = pattern.match(body)
            if m and any(ch.isalnum() for ch in m.group("code")):
                code = m.group("code").strip().rstrip(",").strip()
                code = re.sub(r"\s*x$", "", code, flags=re.IGNORECASE).strip()
                code = re.sub(rf"[\s,:–—-]*{_UNIT_WORDS}[\s.]*$", "", code,
                              flags=re.IGNORECASE).strip()
                qty = int(m.group("qty"))
                break
        else:
            code = body.rstrip(",").strip()
            # No quantity found. A unit word left in the line says one was meant.
            if re.search(rf"\b{_UNIT_WORDS}\b", code, re.IGNORECASE):
                read = ("quantity not read from this line — assumed 1. "
                        "Check it against what the customer wrote.")

        code = re.sub(r"\s{2,}", " ", code)
        row: Dict[str, Any] = {"raw": raw, "code": code, "qty": max(1, qty)}
        if read:
            row["proposed"] = True
            row["reading"] = read
        rows.append(row)
    return rows


def _identity_candidate(res: Resolution) -> Optional[str]:
    """The record the engine proposed as this line's identity, if it did.

    Narrow on purpose. It is only the unconfirmed cross-namespace proposal —
    "this customer's code is probably MM# X, confirm it" — which pie-parser
    returns as NEEDS_REVIEW with exactly one candidate. Selecting that code is
    a person answering the question the engine asked, and worth remembering
    forever. Selecting anything else is a substitution on one quote, which is
    not a fact about what the customer's code means.
    """
    if res.outcome != "NEEDS_REVIEW" or len(res.candidates) != 1:
        return None
    return res.candidates[0].code


@dataclass
class Line:
    id: str
    raw: str
    reqCode: str
    reqDesc: str
    reqQty: int
    rel: str
    supplyCode: Optional[str]
    candidates: List[Candidate]
    outcome: str
    semantics: str
    notes: List[str] = field(default_factory=list)
    #: The identity this line was resolved under, if the customer is linked.
    #: Remembered rather than re-derived, so a confirmation is filed under the
    #: same scope the question was asked in even if the quote is re-pointed at
    #: another customer afterwards.
    customerScope: Optional[str] = None
    #: The record the engine proposed as this line's identity but declined to
    #: assert — "your code probably means this; confirm it". Selecting exactly
    #: this code is a confirmation of the proposal, which is a fact worth
    #: keeping; selecting anything else is a substitution on one quote, which
    #: is not. See store.select_supply.
    identityCandidate: Optional[str] = None
    #: True when a model read this line out of prose rather than a person
    #: typing it. Cleared by ``confirm_reading`` and by nothing else.
    proposed: bool = False
    #: What the reader had to interpret, where it did — an abbreviation
    #: expanded, a quantity implied. Empty for a line taken straight off the
    #: text. Shown next to the customer's own words so the confirmation is
    #: against what they wrote, not against the tidied version.
    reading: str = ""
    # supply selection
    sel: str = "AUTO"                 # AUTO | USER | MANUAL
    # zoho-derived
    supplyDesc: str = ""
    #: The supply product's id in the books this quote is bound to, when the
    #: adapter had one. Server-side only and never serialized: it is how the
    #: estimate names the item it already resolved rather than matching the
    #: code a second time at send time — a call per line against the rate
    #: limit, and a second chance to land on a different item.
    itemId: Optional[str] = None
    inBooks: Optional[bool] = None
    avail: Optional[int] = None
    listPrice: Optional[float] = None
    cost: Optional[float] = None
    family: Optional[str] = None
    #: The tax rate the books hold against this line's item, as a percentage.
    #: ``None`` means the books did not state one — which is why the summary
    #: counts assumed lines rather than quietly applying the default to them.
    taxPercent: Optional[float] = None
    # commercial
    quoted: Optional[float] = None
    #: Where ``quoted`` came from. ``LIST`` is the catalogue rate this line
    #: opened at; ``USER`` is a number a person put there.
    #:
    #: A resolved line is auto-quoted at list so a forty-line tender is not
    #: forty numbers to type, and that default is genuinely useful — but it is
    #: indistinguishable from a considered price unless something says which is
    #: which. It was not distinguished: the screen said "nothing is priced for
    #: you" in three places while every line arrived priced, and a quote nobody
    #: had looked at showed a Quotation total ready to send.
    priceSource: str = "LIST"          # LIST | USER
    createPhase: Optional[str] = None  # None | progress | failed
    service: Optional[str] = None      # None | BOOKS | AVAIL | PIE
    incompatReason: Optional[str] = None

    # ── derivation ───────────────────────────────────────────────────────────
    def substituted(self) -> bool:
        return bool(self.supplyCode) and self.supplyCode != self.reqCode

    def shortage(self) -> Optional[int]:
        if self.avail is None or not self.supplyCode:
            return None
        return max(0, self.reqQty - self.avail)

    def economics(self) -> pricing.Economics:
        return pricing.compute_economics(self.cost, self.listPrice, self.quoted, self.family)

    def status(self) -> Dict[str, str]:
        """(kind, label) — matches the design's status taxonomy."""
        # First, and `technical` on purpose. `blockers` is every technical line,
        # so this one classification is what stops a quote going out on a line
        # a model read and nobody checked — rather than a second gate beside
        # the one that already exists. CNMG 120408-MP and -MS are different
        # tools and the difference reaches a customer.
        if self.proposed:
            return {"kind": "technical", "label": "CONFIRM READING"}
        rel = self.rel
        if self.service == "PIE" or rel == "PIE_DOWN":
            return {"kind": "technical", "label": "PIE OFFLINE"}
        if rel == "UNRESOLVED":
            return {"kind": "technical", "label": "UNRESOLVED"}
        if rel == "AMBIGUOUS":
            return {"kind": "technical", "label": "AMBIGUOUS"}
        if rel == "INCOMPATIBLE":
            return {"kind": "technical", "label": "INCOMPATIBLE"}
        if self.createPhase == "failed":
            return {"kind": "operational", "label": "CREATE FAILED"}
        if self.createPhase == "progress":
            return {"kind": "operational", "label": "CREATING…"}
        if self.service == "BOOKS":
            return {"kind": "operational", "label": "BOOKS OFFLINE"}
        if self.inBooks is False:
            return {"kind": "operational", "label": "NOT IN BOOKS"}
        if self.quoted is None:
            return {"kind": "commercial", "label": "NO PRICE"}
        return {"kind": "ready", "label": "READY · SUBST" if self.substituted() else "READY"}

    def flags(self) -> Dict[str, bool]:
        st = self.status()
        kind = st["kind"]
        sh = self.shortage()
        return {
            "attention": kind in ("technical", "operational", "commercial"),
            "procurement": sh is not None and sh > 0,
            "missingBooks": (self.inBooks is False or self.service == "BOOKS"
                             or self.createPhase in ("failed", "progress")),
            "manualReview": self.sel == "MANUAL" or self.rel in ("INCOMPATIBLE", "AMBIGUOUS"),
            "unresolved": self.rel in ("UNRESOLVED", "AMBIGUOUS", "INSUFF") or self.service == "PIE",
            "substituted": self.substituted(),
        }

    # ── serialization (role-gated) ───────────────────────────────────────────
    def to_dict(self, mgmt: bool) -> Dict[str, Any]:
        st = self.status()
        econ = self.economics()
        sh = self.shortage()
        base: Dict[str, Any] = {
            "id": self.id, "raw": self.raw,
            "reqCode": self.reqCode, "reqDesc": self.reqDesc, "reqQty": self.reqQty,
            "rel": self.rel, "relLabel": _REL_LABELS.get(self.rel, self.rel),
            "proposed": self.proposed, "reading": self.reading,
            "supplyCode": self.supplyCode, "supplyDesc": self.supplyDesc,
            "sel": self.sel,
            "avail": self.avail, "availUnknown": self.avail is None and bool(self.supplyCode),
            "inBooks": self.inBooks,
            "shortage": sh,
            "quoted": self.quoted,
            # Whose number this is. Safe for both roles — it says where a rate
            # came from, not what it cost.
            "priceSource": self.priceSource if self.quoted is not None else None,
            "recommended": econ.recommended,   # decision support, safe for both roles
            "lineTotal": (self.quoted * self.reqQty) if self.quoted is not None else None,
            "createPhase": self.createPhase,
            "service": self.service,
            "incompatReason": self.incompatReason,
            "status": st,
            "flags": self.flags(),
            "candidates": [c.to_dict() for c in self.candidates],
            "notes": self.notes,
            "substituted": self.substituted(),
        }
        if mgmt:
            base["economics"] = econ.to_dict()
        return base


@dataclass
class Quote:
    id: str
    customer: str
    number: str
    #: The tenant that owns this quote. The store is one process-wide dict keyed
    #: only by ``quote_id``, and the ids are enumerable (``q{run}-{counter}``),
    #: so without this every read seam authorized on the caller's *session* but
    #: never on the quote's *tenant*: another org's owner could pass a guessed id
    #: to ``GET /api/quotes/{id}``, ``/intake``, the approval gate or
    #: ``quote-intelligence/assess`` and read — or mutate — it, cost and margin
    #: included. Stamped at ``create`` from ``principal.organization_id`` and
    #: checked at every seam (``_get_quote``, ``line_cost``, ``_below_floor_lines``).
    organizationId: str = ""
    #: The platform's own id for the customer, when one was picked rather than
    #: typed.
    #:
    #: The name alone is not identity: "ABC Industries" can exist in all three
    #: connected books and be three different customers, which is the whole
    #: reason ``domain/origin.py`` exists. Every downstream reader resolves
    #: through ``_resolve_customer``, which tries the id *before* any name
    #: match — so carrying the id turns a tolerant guess into an exact lookup,
    #: and the name stays for the header to print.
    customerId: Optional[str] = None
    #: The key any Zoho estimate for this quote is written under, and the whole
    #: of this quote's idempotency. ``number`` is not enough on its own — it is
    #: derived from a truncated clock and repeats about once a day — and an
    #: estimate keyed on a value that repeats would let one quote silently
    #: return another's.
    reference: str = ""
    lines: List[Line] = field(default_factory=list)
    savedAt: Optional[str] = None
    #: The Zoho estimate this quote has already produced, and the priced content
    #: it was produced from. Nothing remembered either, so pressing the send
    #: button three times created three estimates and the screen looked
    #: identical after the first as before it.
    estimateNumber: Optional[str] = None
    estimateLineCount: Optional[int] = None
    estimateFingerprint: Optional[str] = None

    @property
    def customer_ref(self) -> str:
        """What to resolve this quote's customer by. Id if we have it."""
        return self.customerId or self.customer

    def to_dict(self, mgmt: bool) -> Dict[str, Any]:
        line_dicts = [ln.to_dict(mgmt) for ln in self.lines]
        subtotal = sum(
            (ln.quoted * ln.reqQty) for ln in self.lines if ln.quoted is not None
        )
        # Tax per line, from the rate the books hold against that line's item,
        # and the configured rate only where they hold none.
        #
        # This used to be `subtotal * sales_tax_rate()` — one blended rate over
        # the whole quote — while `zoho_books_service` sends no tax field at
        # all, so Zoho priced each line from its own item settings. Two tax
        # authorities, disagreeing on any item off the default rate, and the one
        # the customer receives was the one this screen did not compute.
        #
        # The default is still applied where nothing is on record, because a
        # quote desk needs a number and a pasted RFQ line has no item behind it
        # to ask. What changed is that the assumption is now *counted* and
        # reported rather than folded invisibly into one figure — a total
        # assembled from four known rates reads differently from the same total
        # assembled from four guesses, and only one of them is worth sending.
        default_rate = sales_tax_rate()
        tax = 0.0
        taxed_known = taxed_assumed = 0
        applied: set[float] = set()
        for ln in self.lines:
            if ln.quoted is None:
                continue
            line_total = ln.quoted * ln.reqQty
            if ln.taxPercent is None:
                line_rate = default_rate
                taxed_assumed += 1
            else:
                line_rate = ln.taxPercent / 100.0
                taxed_known += 1
            tax += line_total * line_rate
            applied.add(line_rate)

        # One rate to print only when every priced line was actually taxed at
        # it. Naming a single rate over a mixed quote is the same misstatement
        # in a smaller font.
        #
        # Keyed on the rates *applied* rather than on the ones the books stated,
        # so a quote where the known rate and the configured default coincide
        # still prints the number they agree on — and an empty quote, which has
        # no line to disagree, reports the rate that would be used rather than
        # refusing to name one. `taxBasis` below is what says how much of this
        # rested on the default; the rate itself is not the place to carry that.
        if not applied:
            rate: Optional[float] = default_rate
        elif len(applied) == 1:
            rate = next(iter(applied))
        else:
            rate = None
        counts = self._filter_counts(mgmt)
        return {
            "id": self.id, "customer": self.customer,
            "customerId": self.customerId, "number": self.number,
            # Shown so that when a send fails in a way nobody can resolve from
            # here, the person has the string to search for in Zoho.
            "reference": self.reference,
            "savedAt": self.savedAt,
            "lines": line_dicts,
            "summary": {
                "subtotal": round(subtotal, 2),
                # The rate and its name travel with the amount. The screen used
                # to print the literal "GST 18%" beside a number computed from a
                # literal 0.18 in this file — two hardcoded copies of one fact,
                # in different languages, either of which could be edited alone.
                "tax": round(tax, 2),
                "taxLabel": sales_tax_label(),
                # null where the priced lines do not share one rate. A screen
                # that prints "GST 18%" over a quote holding 18% and 12% lines
                # is stating something false about a document a customer will
                # receive, so there is deliberately no single number to print.
                "taxRate": rate,
                # How the tax above was arrived at. `assumed` is the count of
                # priced lines the books held no rate for, which took the
                # configured default — the honest version of what used to be
                # applied to every line without saying so.
                "taxBasis": {"known": taxed_known, "assumed": taxed_assumed,
                             "defaultRate": default_rate},
                "grand": round(subtotal + tax, 2),
                "total": len(self.lines),
                # What the total does and does not yet contain. A quote of four
                # lines where two are unresolved still printed a Quotation
                # total, in the same weight as a finished one, with nothing
                # saying it was the total of half a quote.
                "unpriced": sum(1 for ln in self.lines if ln.quoted is None),
                # Priced, but at the rate the catalogue opened the line with —
                # nobody has agreed to it. See Line.priceSource.
                "atListPrice": sum(1 for ln in self.lines
                                   if ln.quoted is not None and ln.priceSource == "LIST"),
            },
            "filterCounts": counts,
            # Both margin keys are *absent* for a salesperson rather than null or
            # zero. §1 asks for absent, and here the difference is not cosmetic:
            # see `_filter_counts`.
            **({"marginFloor": self._margin_floor()} if mgmt else {}),
            # What has already gone to Zoho from this quote, so the screen can
            # say so rather than leaving an unchanged primary button as the only
            # evidence that anything happened.
            "estimate": ({"number": self.estimateNumber,
                          "lineCount": self.estimateLineCount,
                          "current": self.estimateFingerprint == _priced_fingerprint(self)}
                         if self.estimateNumber else None),
        }

    def _filter_counts(self, mgmt: bool) -> Dict[str, int]:
        fl = [ln.flags() for ln in self.lines]
        counts = {
            "ALL": len(self.lines),
            "NEEDS": sum(f["attention"] for f in fl),
            "PROC": sum(f["procurement"] for f in fl),
            "BOOKS": sum(f["missingBooks"] for f in fl),
            "MANUAL": sum(f["manualReview"] for f in fl),
            "UNRES": sum(f["unresolved"] for f in fl),
            "SUBST": sum(f["substituted"] for f in fl),
        }
        # MFLOOR is a margin fact and it used to be sent to everyone, two lines
        # below the guard that correctly withheld `marginFloor`. On its own it
        # looks harmless — a count. It is a yes/no oracle on the floor price:
        # re-price one line and read the count back, and about twenty probes
        # bisect the floor to the rupee. The floor is cost x (1 + margin floor),
        # so that recovers the cost. The Quote Builder already hid the chip
        # behind `{mgmt && ...}`, which is exactly the failure §1 names —
        # "absent from the response, not hidden in the browser".
        #
        # Omitted, not zeroed. A zero still answers "is any line below the
        # floor?" with "no", and that is the same oracle at lower resolution.
        if mgmt:
            counts["MFLOOR"] = sum(
                1 for ln in self.lines if ln.economics().below_floor)
        return counts

    def _margin_floor(self) -> Optional[Dict[str, Any]]:
        below = [ln for ln in self.lines if ln.economics().below_floor]
        if not below:
            return None
        worst = min(ln.economics().margin for ln in below
                    if ln.economics().margin is not None)
        return {
            "count": len(below),
            "worst": round(worst, 4),
            "floor": pricing.MARGIN_FLOOR,
        }


def _priced_fingerprint(quote: "Quote") -> str:
    """What a Zoho estimate is built from, as one comparable string.

    Exactly the fields that reach ``zoho.create_estimate`` — product, quantity
    and rate, per line, in order. Everything else about a quote can move without
    changing what was sent, and a fingerprint that also covered, say, the filter
    counts would call an unchanged quote changed.
    """
    return "|".join(
        f"{ln.supplyCode}:{ln.reqQty}:{ln.quoted}"
        for ln in quote.lines if ln.supplyCode)


class QuoteStore:
    """Process-wide quote registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: Dict[str, Quote] = {}

    def get(self, quote_id: str) -> Optional[Quote]:
        return self._quotes.get(quote_id)

    def line_cost(self, quote_id: str, line_id: str,
                  organization_id: str) -> Optional[Decimal]:
        """The landed cost this server already holds against one quote line.

        Read by the assessment path so the gate is judging the same cost the
        grid is showing. One accessor rather than the same three-line lookup in
        the approvals router and the quote-intelligence router, because "where
        does a quote line's cost come from" is exactly the question that had two
        answers in the first place.

        Server-held, never requester-supplied: the cost arrived from the books at
        intake and lives here, so passing it into an assessment is not the same
        thing as trusting a number in a request body.

        ``organization_id`` is required and enforced: this returns a *cost*, and
        the store is not tenant-scoped, so a caller passing another org's
        ``quote_id`` would otherwise read that tenant's landed cost. A mismatch
        is treated exactly like an unknown quote — ``None`` — so a foreign id is
        indistinguishable from one that never existed.
        """
        quote = self._quotes.get(quote_id)
        if quote is None or quote.organizationId != organization_id:
            return None
        line = next((row for row in quote.lines if row.id == line_id), None)
        if line is None or line.cost is None:
            return None
        # `Decimal(str(...))` rather than `Decimal(float)`: money is Decimal (§1),
        # and the binary-float detour is how 420.0 becomes 419.99999999999994.
        return Decimal(str(line.cost))

    def create(self, customer: str, customer_id: Optional[str] = None,
               organization_id: str = "") -> Quote:
        with self._lock:
            qid = f"q{_RUN}-{next(_ids)}"
            num = f"QB-{int(time.time()) % 100000:05d}"
            q = Quote(id=qid, customer=customer or "New customer", number=num,
                      organizationId=organization_id,
                      customerId=customer_id or None,
                      reference=f"{num}-{uuid.uuid4().hex[:8]}")
            self._quotes[qid] = q
            return q

    # ── line construction ────────────────────────────────────────────────────
    def build_lines(self, rows: List[Dict[str, Any]], zoho: ZohoService,
                    customer_scope: Optional[str] = None,
                    bands: Optional[Bands] = None,
                    mapping_store: Any = None) -> List[Line]:
        lines: List[Line] = []
        for row in rows:
            res: Resolution = pie_service.resolve(row["code"], customer_scope, bands,
                                                  mapping_store)
            ln = Line(
                id=f"l{next(_ids)}",
                proposed=bool(row.get("proposed")),
                reading=str(row.get("reading") or ""),
                raw=row["raw"],
                reqCode=res.reqCode or row["code"],
                reqDesc=res.reqDesc or row["code"],
                reqQty=row["qty"],
                rel=res.rel,
                supplyCode=res.supplyCode,
                candidates=res.candidates,
                outcome=res.outcome,
                semantics=res.semantics,
                notes=res.notes,
                customerScope=customer_scope,
                identityCandidate=_identity_candidate(res),
                service="PIE" if res.pie_offline else None,
            )
            self._enrich_from_zoho(ln, zoho)
            lines.append(ln)
        return lines

    def add_rfq(self, quote: Quote, text: str, zoho: ZohoService,
                customer_scope: Optional[str] = None,
                bands: Optional[Bands] = None,
                mapping_store: Any = None,
                rows: Optional[List[Dict[str, Any]]] = None) -> List[Line]:
        """``customer_scope`` is the customer's cross-connector identity.

        It arrives as an opaque string rather than being looked up here: this
        store is deliberately database-free — it imports pricing, the engine and
        Zoho, and nothing else — and giving it a session to resolve an identity
        would be the first crack in that.
        """
        # `rows` when something upstream already read the enquiry — see
        # ai/reading.py. Falling back to the regex here rather than at the call
        # site keeps the store's behaviour identical with no reader present,
        # which is what makes the whole feature removable.
        new = self.build_lines(rows or _split_rfq(text), zoho, customer_scope,
                               bands, mapping_store)
        with self._lock:
            quote.lines.extend(new)
        return new

    def _enrich_from_zoho(self, ln: Line, zoho: ZohoService,
                          code_changed: bool = True) -> None:
        """Attach commercial facts + auto-price a resolved, in-books line.

        ``code_changed`` says whether this call follows a move to a *different*
        supply product. It does on a fresh line and when somebody picks another
        candidate; it does not when the same product is re-read.
        """
        if not ln.supplyCode:
            return
        if not zoho.available:
            ln.service = "BOOKS"
            return
        try:
            item = zoho.get_item(ln.supplyCode)
        except SourceUnavailable:
            # A live adapter can fail mid-intake — a revoked token, a throttle,
            # a price Zoho sent in a shape that is not a number. That is the
            # BOOKS OFFLINE state on this line, not a 500 for the whole RFQ.
            ln.service = "BOOKS"
            return
        if item is None:
            ln.inBooks = False
            return
        ln.supplyDesc = item.name if item.name != ln.supplyCode else ln.reqDesc
        ln.inBooks = item.in_books
        ln.itemId = item.item_id
        ln.avail = item.stock
        ln.listPrice = item.list_price
        ln.cost = item.cost
        ln.taxPercent = item.tax_percentage
        ln.family = self._family_of(ln)
        if item.in_books and item.list_price is not None:
            # Auto-quote at list so a long tender is not a column of typing —
            # but marked as the catalogue's number rather than a person's, so
            # the screen can show which rates nobody has looked at yet. A price
            # somebody set survives a re-resolution of the same product; moving
            # the line to a *different* product is a different catalogue rate
            # and the default comes back.
            if ln.quoted is None or ln.priceSource != "USER" or code_changed:
                ln.quoted = item.list_price
                ln.priceSource = "LIST"

    @staticmethod
    def _family_of(ln: Line) -> Optional[str]:
        for c in ln.candidates:
            fam = (c.attributes or {}).get("product_family")
            if fam:
                return fam
        return None

    # ── mutations ────────────────────────────────────────────────────────────
    def select_supply(self, ln: Line, code: str, zoho: ZohoService, manual: bool = False) -> None:
        cand = next((c for c in ln.candidates if c.code == code), None)
        was_exact = code == ln.reqCode
        code_changed = ln.supplyCode != code
        ln.supplyCode = code
        if was_exact:
            ln.rel, ln.sel = "EXACT", "AUTO"
        elif manual:
            ln.rel = cand.rel if cand else "COMPAT"
            ln.sel = "MANUAL"
        else:
            ln.rel = cand.rel if cand else "COMPAT"
            ln.sel = "USER"
        if cand:
            ln.reqDesc = ln.reqDesc  # requested stays; supplyDesc updated below
        ln.service = None
        ln.incompatReason = None
        self._enrich_from_zoho(ln, zoho, code_changed=code_changed)
        if cand:
            ln.supplyDesc = cand.desc

    def set_price(self, ln: Line, price: Optional[float]) -> None:
        ln.quoted = price
        # A number a person typed, including one that happens to equal list.
        # Clearing the field puts the line back to having no price at all, so
        # there is nothing left to attribute.
        ln.priceSource = "USER" if price is not None else "LIST"

    def delete_line(self, quote: Quote, line_id: str) -> Line:
        with self._lock:
            line = next((ln for ln in quote.lines if ln.id == line_id), None)
            if line is None:
                raise KeyError(line_id)
            quote.lines.remove(line)
            return line

    def apply_discount(self, lines: List[Line], pct: float) -> int:
        """Take ``pct`` off the rate each line is currently quoting.

        Off the *quoted* rate, not off the catalogue rate. It used to recompute
        from ``listPrice``, which made a control labelled "apply 10% discount"
        raise prices: a line negotiated down from ₹530 to ₹300 and then included
        in a 10% discount came back at ₹477 — up 59% — and pressing the button
        again changed nothing, because the answer never depended on where the
        line actually was. Discounting what is on the line compounds the way the
        label says and can only ever move a price down.

        A line with no rate yet has nothing to take a percentage of, so it falls
        back to list; a line with neither is skipped and not counted, which is
        what the caller reports back as "N line(s) discounted".
        """
        n = 0
        for ln in lines:
            base = ln.quoted if ln.quoted is not None else ln.listPrice
            if base is None:
                continue
            ln.quoted = round(base * (1 - pct / 100.0))
            ln.priceSource = "USER"
            n += 1
        return n

    def create_item(self, ln: Line, zoho: ZohoService) -> str:
        """Create the supply product in the books. Returns "" or why it failed.

        A failed write leaves the line in CREATE FAILED, which the status
        taxonomy already has and nothing ever set: the mock could not fail, so
        ``createPhase`` moved to "progress" and back with no path between.
        Against a real ledger the write does fail — a rejected token, a name
        Zoho will not accept — and a line stuck on "CREATING…" forever while the
        request 500s is the version of that a salesperson cannot act on.
        """
        if not ln.supplyCode:
            return ""
        ln.createPhase = "progress"
        try:
            item = zoho.create_item(ln.supplyCode, ln.supplyDesc or ln.reqDesc, ln.listPrice)
        except (SourceWriteRefused, SourceWriteUnknown, SourceUnavailable) as e:
            ln.createPhase = "failed"
            return str(e)
        ln.inBooks = True
        ln.createPhase = None
        ln.itemId = item.item_id or ln.itemId
        ln.cost = item.cost
        if ln.quoted is None and item.list_price is not None:
            ln.quoted = item.list_price
            ln.priceSource = "LIST"
        return ""

    def confirm_reading(self, ln: Line) -> None:
        """A person has checked this line against what the customer wrote.

        One line at a time, and there is deliberately no "confirm all": the
        whole risk this guards against is a grade suffix nobody looked at, and
        a button that clears forty lines at once is a button that gets pressed
        without reading forty lines.
        """
        ln.proposed = False

    def blockers(self, quote: Quote) -> List[Line]:
        """Technical-status lines that must be resolved before an estimate."""
        return [ln for ln in quote.lines if ln.status()["kind"] == "technical"]

    @staticmethod
    def priced_fingerprint(quote: Quote) -> str:
        """The quote's sendable content, for deciding whether a re-send is one."""
        return _priced_fingerprint(quote)

    @staticmethod
    def record_estimate(quote: Quote, *, number: str, line_count: int,
                        fingerprint: str) -> None:
        quote.estimateNumber = number
        quote.estimateLineCount = line_count
        quote.estimateFingerprint = fingerprint


store = QuoteStore()
