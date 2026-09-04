"""The quote being worked on, and the mutations the desk performs on it.

A ``Quote`` is a list of ``Line``s built from a pasted RFQ. Each line carries
the pie-parser resolution plus the Zoho-derived commercial facts, and derives
its own status (the design's READY / NEEDS ATTENTION / NOT IN BOOKS / NO PRICE
/ UNRESOLVED … states). Economics (cost, margin, below-floor) are computed here
but only serialized for a management principal — the sales client never receives
them.

**Where a quote lives.** In ``quote_drafts``, through ``quote_workspace`` —
loaded into these dataclasses for a request and written back at the end of
it. It used to live in a process-wide dict on this module, which gave every
restart a clean slate, every browser one draft of its own, and nobody a list
of what the desk was working on. This module is deliberately database-free —
it imports pricing, the engine and the books adapter, and nothing else — so
the row-to-dataclass half is ``quote_workspace``'s, and ``to_state`` /
``from_state`` below are the contract between them.
"""
from __future__ import annotations

import itertools
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

from . import clock, pricing
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

#: Line ids. Unique within a process, and made unique across restarts by the
#: random tail ``_line_id`` appends: a line is keyed by ``QuoteDecision`` and
#: ``ApprovalRequest`` rows for as long as the quote lives, which is now longer
#: than the process that minted it.
_ids = itertools.count(1)


def _line_id() -> str:
    return f"l{next(_ids)}-{uuid.uuid4().hex[:6]}"


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

#: The two unit words that are *only* ever quantity keywords.
#:
#: `pc`, `no` and `ea` all appear inside ordinary product prose — `WMT PC
#: 805M`, `INSERT NO WIPER` — so they are only evidence of a quantity when
#: they sit against a number or end the line. `qty` and `quantity` carry no
#: such risk: they occur in 0 of the 6,717 corpus rows, and a person who
#: types one is talking about a count and nothing else.
_QTY_KEYWORD = r"(?:qty|quantity)"

#: The unit words long enough that they are never a token in a description.
#:
#: This is `_UNIT_WORDS` minus the abbreviations that collide with product
#: prose: bare `pc` (`WMT PC 805M`), bare `no` and `no.` (`INSERT NO WIPER`),
#: bare `ea`, and the singulars. Only these may be read as a unit when they
#: come *before* the number — `- nos 100 required` — because that position is
#: exactly where `PC 805M` would otherwise be misread. Measured: 0 of the
#: 6,717 corpus rows match in that position.
_UNIT_STRONG = r"(?:nos|pcs|pieces|units|each|qty|quantity)"

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
#: A comma, an `x`, a dash or a `qty` keyword is explicit enough to lift the
#: bound; bare whitespace is not.
#:
#: A *unit word* is not, either, and that correction is the point of this note.
#: The bound used to be lifted by the mere presence of one, on the reasoning
#: that a unit word makes the number unambiguous — but the unit sits *after* the
#: number, so it says nothing about which digits were meant. `DNMG 150608 nos`
#: therefore came back as code `DNMG` at 150,608: exactly the defect this
#: constant was introduced to stop, re-entering through the door held open for
#: it. The bound now travels with the separator, and only an explicit one lifts
#: it — `DNMG 150608 - 250000 nos` is still a quarter-million pieces.
_BARE_QTY_DIGITS = 4

#: Tried in order, strongest evidence first, and the bare-number rule last
#: because it is the only one that guesses. Each names its quantity `qty` and the
#: rest of the line `code`.
_QTY_PATTERNS = (
    # "<code>, 100"  ·  "<code>, x100" — a comma is a deliberate separator.
    # The comma is a separator when it is followed by whitespace, or when what
    # precedes it is not a digit. `digit,digit` with nothing between them is a
    # European decimal, and reading it as a quantity cost both halves of the
    # line: `ENDMILL HARL 5FL 8x8x40x87 R0,5` came back as code `...R0` at a
    # quantity of 5 — a 0.5 mm corner radius turned into an order for five of a
    # product that is not the one asked for, and one that collides with a
    # genuine `R0`. 790 catalogue rows carry a decimal comma; this rule was
    # truncating every one that ended in it.
    re.compile(r"^(?P<code>.*?)\s*,(?:\s+|(?<=\D,))\s*x?\s*(?P<qty>\d+)\s*$",
               re.IGNORECASE),
    # "<code> x100"  ·  "<code>x100"
    re.compile(r"^(?P<code>.*?)\s*\bx\s*(?P<qty>\d+)\s*$", re.IGNORECASE),
    # "<code> - 100 nos"  ·  "<code> qty 100 nos" — an explicit separator or the
    # keyword is somebody saying "a quantity starts here", so it is unbounded.
    re.compile(rf"^(?P<code>.*?)(?:[,\t:\u2013\u2014-]\s*|[\s,\t:\u2013\u2014-]+(?:qty|quantity)[\s.:-]*)"
               rf"(?P<qty>\d+)\s*{_UNIT_WORDS}(?![A-Za-z])\s*[.]?$",
               re.IGNORECASE),
    # "<code> 100 pcs" — the same shape with nothing but whitespace between the
    # code and the number, so `_BARE_QTY_DIGITS` still applies. The unit word
    # cannot lift that bound: it sits after the number and says nothing about
    # which digits were meant, which is how `DNMG 150608 nos` was read as
    # 150,608 of a code called `DNMG`.
    re.compile(rf"^(?P<code>.*?)[\s\t]+(?P<qty>\d{{1,{_BARE_QTY_DIGITS}}})"
               rf"\s*{_UNIT_WORDS}(?![A-Za-z])\s*[.]?$",
               re.IGNORECASE),
    # "<code> qty 100" — the keyword standing in for the unit word.
    re.compile(r"^(?P<code>.*?)[\s,\t:\u2013\u2014-]+(?:qty|quantity)[\s.:-]*"
               r"(?P<qty>\d+)\s*[.]?$", re.IGNORECASE),
    # "100 nos <code>"  ·  "100 nos of <code>" — leading, and it needs a unit
    # word to be told apart from a code that starts with digits.
    #
    # The lookahead is load-bearing: without it the alternation matches a
    # *prefix* of the next token, so `2001174 nos` read `no` as the unit and
    # left `s` as the product code — a quantity of two million of a code one
    # character long, out of a line naming one part.
    re.compile(rf"^(?P<qty>\d+)\s*{_UNIT_WORDS}(?![A-Za-z])[\s.:]*(?:of\s+)?(?P<code>.+)$",
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
                # Zero is not a quantity, and reading one costs the code.
                # `END MILL W4N1 12x12x26x83 R2,0` is a 2.0 mm corner radius
                # written with a European decimal comma; the comma rule read
                # `,0` as an order of zero, clamped it to one, and handed on
                # `…R2` — a *different product*, and one that collides with a
                # genuine `R2`. Twenty catalogue rows are written that way.
                #
                # It also silently discarded a real quantity: because a pattern
                # had matched, `250000 nos ENDMILL … Rad 1,0` never reached the
                # leading-quantity rule or the flag below, so a quarter-million
                # piece line travelled as one. Falling through instead lets the
                # right rule have it.
                if int(m.group("qty")) == 0:
                    continue
                code = m.group("code").strip().rstrip(",").strip()
                code = re.sub(r"\s*x$", "", code, flags=re.IGNORECASE).strip()
                code = re.sub(rf"[\s,:–—-]*{_UNIT_WORDS}[\s.]*$", "", code,
                              flags=re.IGNORECASE).strip()
                qty = int(m.group("qty"))
                break
        else:
            code = body.rstrip(",").strip()
            # No quantity found. A unit word left in the line says one was
            # meant — but only where it is *doing the work of a unit*, and the
            # test for that is adjacency to a number, not position in the line.
            #
            # `\d\s*{unit}\b` — the unit sits against a number, anywhere in the
            # line: `- 100 nos urgent`, `(100 nos)`, `100 nos TN2000`, and
            # `2001174nos` with no space at all. The last of those is why the
            # digit is inside the pattern rather than a `\b` in front of it: a
            # `\b` needs a boundary before the word and there is none between a
            # digit and a letter, so that shape used to default silently to 1.
            #
            # The separator between number and unit is `[\s.-]*`, not a space:
            # `100-nos` is how plenty of people write it, and a hyphen there
            # cost the flag entirely.
            #
            # `\b{strong}[\s.-]*\d` — the unit can also come *first*:
            # `- nos 100 required`. Only the long forms are allowed to, because
            # that position is precisely where `WMT PC 805M` sits, so bare `pc`,
            # `no` and `ea` are excluded from this arm and only this one.
            #
            # `\b{unit}\W*$` — or the unit stands at the end with no number at
            # all: `insert, nos`. A marker with nothing to attach to is exactly
            # the case a human has to read. The trailing run is `\W*` rather
            # than an enumerated punctuation class, because the enumeration kept
            # being wrong by one character — `(nos)`, `nos?`, `nos —` and
            # WhatsApp's `*nos*` each defeated a list that did not name them,
            # and `\W*` cannot swallow a digit, so it stays specific.
            #
            # `\bqty|quantity\b` — and the explicit keyword counts wherever it
            # appears, because unlike `nos` or `pc` it is never a grade token or
            # an English word in a description: it occurs in 0 of the 6,717
            # corpus rows. `CNMG 120408-MP - qty to be confirmed` is the
            # customer saying the number is not settled, which is the strongest
            # evidence there is that a person must supply it — and adjacency
            # alone would miss it, since there is no digit to be adjacent to.
            #
            # What neither arm matches is a unit word that is merely *present*,
            # which the previous rule (a bare search) treated as evidence. It
            # read the grade token in `WMT PC 805M MOULDED INSERTS` as `pcs` and
            # the English in `DOV-LOK PCD MINI TIP INSERT NO WIPER` as a count —
            # ten real catalogue rows blocked for a unit that was not one, and a
            # flag that fires on ordinary descriptions is one people learn to
            # click through.
            #
            # Measured, not reasoned about: over all 6,717 corpus rows in three
            # shapes this flags none where the bare search flagged ten, and it
            # holds every quantity-bearing shape an adversarial pass could
            # construct. An earlier attempt anchored to the end of the line
            # alone; it cleared the false flags and lost the flag on `- 100 nos
            # urgent`, which is a hundred pieces quoted as one, in silence.
            if re.search(rf"(?:\b{_QTY_KEYWORD}\b"
                         rf"|\d[\s.-]*{_UNIT_WORDS}\b"
                         rf"|\b{_UNIT_STRONG}[\s.-]*\d"
                         rf"|\b{_UNIT_WORDS}\W*$)", code, re.IGNORECASE):
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
    #: Where ``cost`` came from. ``BOOKS`` is the connected ledger's own landed
    #: cost; ``DEMO`` is the offline stand-in adapter's hashed figure, which is
    #: not a purchase price and must never be read as one. ``None`` when no
    #: cost was returned at all.
    #:
    #: Carried for the same reason ``priceSource`` is: without it the two are
    #: rendered identically, and a deployment left in mock mode showed a
    #: management pricing card reading "Cost ₹2,830" for an item nobody had
    #: ever bought — ``sha256(code)`` at 82% of a list price from the same
    #: hash, in the same weight as a real one.
    costSource: Optional[str] = None   # None | BOOKS | DEMO
    #: The cost a person put on this line by hand — the second cost basis.
    #:
    #: The books answer "what have we paid for this item", which is the wrong
    #: question for an item nobody has bought yet: a first-time part quoted
    #: against a supplier's fresh ADR has a real cost that no bill and no item
    #: master holds. Without somewhere to put it the desk either quotes blind
    #: or invents a price, and every downstream number — margin, the floors,
    #: the approval gate — rests on a cost that is missing.
    #:
    #: It takes precedence over ``cost`` for this line and only this line. It is
    #: never written back to the item master: this is what *this* deal costs,
    #: not a fact about the product. See ``effective_cost``.
    customCost: Optional[float] = None
    #: Why that number — the supplier, the offer, the date it holds until.
    customCostNote: str = ""
    #: Who recorded it and when, because a cost with no author is a cost
    #: nobody can question later.
    customCostBy: Optional[str] = None
    customCostAt: Optional[str] = None
    #: True when a manager or owner recorded it. A custom cost entered by the
    #: desk is the desk's own number — a supplier quote they obtained — and
    #: returning it to them discloses nothing the platform knows and they do
    #: not. One entered by management is management's number and stays
    #: management's: §1's rule is that our cost does not reach a salesperson,
    #: and "we typed it into the quote" is not an exemption from it.
    customCostRestricted: bool = False
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

    def effective_cost(self) -> Optional[float]:
        """The cost this line is actually priced against.

        A hand-entered cost wins over the books. It is the more specific claim
        — a number somebody sourced for this deal, against a general one the
        ledger holds for the item — and it is the only one available at all for
        an item with no purchase history, which is the case that made it
        necessary.
        """
        return self.customCost if self.customCost is not None else self.cost

    def cost_basis(self) -> Optional[str]:
        """Which of the two costs ``effective_cost`` returned, and from where.

        Safe for either role: it says where a number came from, not what it is
        — the same rule ``priceSource`` is serialized under.
        """
        if self.customCost is not None:
            return "CUSTOM"
        return self.costSource if self.cost is not None else None

    def economics(self) -> pricing.Economics:
        return pricing.compute_economics(self.effective_cost(), self.listPrice,
                                         self.quoted, self.family)

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
            # Which cost the numbers above rest on, and whether one exists at
            # all. Both roles: it names a source, never a value — the same line
            # `priceSource` sits on — and the desk cannot decide whether to
            # enter a cost for this line without being told there is none.
            "costBasis": self.cost_basis(),
            "customCostSet": self.customCost is not None,
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
        # The number itself, under the rule `customCostRestricted` states: the
        # desk reads back what the desk recorded, and never what management
        # recorded. `recommended` above still moves with either — that is the
        # residual §1 already accepts and documents, and it is not a licence to
        # hand over the figure as a field on top of it.
        if mgmt or not self.customCostRestricted:
            base["customCost"] = self.customCost
            base["customCostNote"] = self.customCostNote
            base["customCostBy"] = self.customCostBy
            base["customCostAt"] = self.customCostAt
        if mgmt:
            base["economics"] = econ.to_dict()
        return base

    # ── persistence ──────────────────────────────────────────────────────────
    def to_state(self) -> Dict[str, Any]:
        """Every field, cost included — the server's own copy.

        Not ``to_dict``: that is the role-gated view for a screen, and it
        derives status and flags that would only have to be dropped on the
        way back in. This is the row, and the row may hold cost because the
        gate is applied on the way out (``to_dict(mgmt)``), never on the way
        in.
        """
        return asdict(self)

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "Line":
        """The inverse of ``to_state``, tolerant of fields this version does
        not know — a row written by a newer build must still open."""
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in state.items() if k in known}
        data["candidates"] = [_candidate_from_state(c)
                              for c in (data.get("candidates") or [])]
        return cls(**data)


def _candidate_from_state(state: Dict[str, Any]) -> Candidate:
    known = {f.name for f in fields(Candidate)}
    return Candidate(**{k: v for k, v in state.items() if k in known})


@dataclass
class Quote:
    id: str
    customer: str
    number: str
    #: The tenant that owns this quote. Every read seam checks it — a
    #: signed-in user from any tenant could otherwise name another tenant's
    #: quote id on ``GET /api/v1/quotes/{id}``, ``/intake``, the approval gate
    #: or ``quote-intelligence/assess`` and read — or mutate — it, cost and
    #: margin included. The ids are UUIDs now rather than an enumerable
    #: counter, which narrows the guess and changes nothing about the rule:
    #: stamped at creation from ``principal.organization_id`` and checked in
    #: ``quote_workspace.load``, which every seam goes through.
    organizationId: str = ""
    #: Which connected company this quote is raised from, and therefore whose
    #: decoded catalogue its lines resolve against.
    #:
    #: A quote belongs to one legal entity — it is that entity that will
    #: invoice — and each entity decodes its own item master. Without this a
    #: line has no catalogue it may honestly search: an organization reading
    #: three books has three, and picking one on the quote's behalf would put a
    #: different company's product on a customer's quote under a real stamp.
    #: Stamped at ``create``; silent where the organization has exactly one
    #: company, because a picker with one option is a question with one answer.
    connectionId: Optional[str] = None
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
    #: Who owns this quote — whoever started it, until it is handed over. Only
    #: the owner changes a quote, plus whoever the organization's policy lets
    #: (``quote_workspace.may_edit``); the name is joined on by the router.
    ownerId: Optional[str] = None
    #: Quote-level details, keyed by the organization's field definitions —
    #: ``quote_fields``. Stored as given; which are mandatory is judged there.
    fields: Dict[str, Any] = field(default_factory=dict)

    @property
    def customer_ref(self) -> str:
        """What to resolve this quote's customer by. Id if we have it."""
        return self.customerId or self.customer

    @property
    def has_customer(self) -> bool:
        """Whether anybody has said who this quote is for.

        Empty is the default now — a quote opens with no customer and the desk
        chooses one — so every path that needs a customer (pricing history,
        the books to send into, the send itself) asks this rather than reading
        a placeholder name as if it were one.
        """
        return bool(self.customerId or self.customer.strip())

    def lines_state(self) -> List[Dict[str, Any]]:
        """The lines as the row stores them — see ``Line.to_state``."""
        return [ln.to_state() for ln in self.lines]

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
            # Which company's catalogue answered every line on this quote. On
            # the wire because a resolution is only interpretable against the
            # catalogue that produced it — the screen says which one, rather
            # than leaving the reader to assume there is only ever one.
            "connectionId": self.connectionId,
            # Shown so that when a send fails in a way nobody can resolve from
            # here, the person has the string to search for in Zoho.
            "reference": self.reference,
            "savedAt": self.savedAt,
            "ownerId": self.ownerId,
            "fields": dict(self.fields),
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
            # ``estimate`` is added by the router, from the persisted document
            # rather than from this object. It used to be three attributes
            # here, which a restart erased — so a quote that had been sent
            # looked unsent, and the send button offered to send it again.
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

    Exactly the fields that reach ``QuoteWriter.create_sales_quotes`` — product, quantity
    and rate, per line, in order. Everything else about a quote can move without
    changing what was sent, and a fingerprint that also covered, say, the filter
    counts would call an unchanged quote changed.
    """
    return "|".join(
        f"{ln.supplyCode}:{ln.reqQty}:{ln.quoted}"
        for ln in quote.lines if ln.supplyCode)


class QuoteStore:
    """The operations the desk performs on a quote.

    Stateless. It used to be the registry as well — one dict of every quote
    the process had seen — and ``get`` / ``create`` / ``line_cost`` lived
    here. They are ``quote_workspace``'s now, where a session can reach the
    row; what is left is the working object and what may be done to it.
    """

    # ── line construction ────────────────────────────────────────────────────
    def build_lines(self, rows: List[Dict[str, Any]], zoho: ZohoService,
                    customer_scope: Optional[str] = None,
                    bands: Optional[Bands] = None,
                    mapping_store: Any = None,
                    connection_id: Optional[str] = None) -> List[Line]:
        lines: List[Line] = []
        for row in rows:
            # This quote's company, and only its catalogue. A line with no
            # company resolves against nothing and comes back UNRESOLVED,
            # which is the honest answer — never against whichever catalogue
            # happened to be loaded.
            res: Resolution = pie_service.resolve(row["code"], customer_scope, bands,
                                                  mapping_store,
                                                  connection_id=connection_id)
            ln = Line(
                id=_line_id(),
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
        #
        # The company is read off the quote rather than taken as an argument:
        # it was decided once when the quote was created, and a caller able to
        # supply a different one per intake could put two companies' decodes in
        # one grid, where the lines are read as comparable.
        new = self.build_lines(rows or _split_rfq(text), zoho, customer_scope,
                               bands, mapping_store,
                               connection_id=quote.connectionId)
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
        # Said, not assumed. A stand-in adapter's figure and a ledger's figure
        # arrive through the same field and are worth different amounts.
        ln.costSource = None if item.cost is None else ("DEMO" if item.synthetic
                                                        else "BOOKS")
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

    def set_custom_cost(self, ln: Line, cost: Optional[float], *,
                        user_id: Optional[str], note: str = "",
                        restricted: bool = False) -> None:
        """Record — or clear — the cost a person sourced for this line.

        ``None`` clears it and the line falls back to the books, attribution
        and all, because a cleared entry is not a correction of the number to
        nothing: there is simply no hand-entered cost any more.

        A zero or negative cost is refused rather than stored. It is the same
        rule ``quote_intelligence`` applies to a cost record and
        ``line_economics`` to a bill line — a zero there is a placeholder
        somebody has not filled in, and letting one through here would make
        every price on the line read as pure profit.
        """
        if cost is None:
            ln.customCost = None
            ln.customCostNote = ""
            ln.customCostBy = None
            ln.customCostAt = None
            ln.customCostRestricted = False
            return
        if cost <= 0:
            raise ValueError(
                "A cost price must be greater than zero. Clear the field to go "
                "back to the cost on record.")
        ln.customCost = float(cost)
        ln.customCostNote = (note or "").strip()[:500]
        ln.customCostBy = user_id
        ln.customCostAt = clock.iso(clock.now())
        ln.customCostRestricted = restricted

    def delete_line(self, quote: Quote, line_id: str) -> Line:
        line = next((ln for ln in quote.lines if ln.id == line_id), None)
        if line is None:
            raise KeyError(line_id)
        quote.lines.remove(line)
        return line

    def set_customer(self, quote: Quote, customer: str,
                     customer_id: Optional[str], zoho: ZohoService,
                     customer_scope: Optional[str] = None,
                     bands: Optional[Bands] = None,
                     mapping_store: Any = None) -> int:
        """Point the quote at a customer, re-resolving what is already on it.

        The quote opens with no customer and the desk chooses one — often
        after the RFQ has been pasted, because the enquiry is what arrived and
        the customer is who it is from. So the lines already on the quote
        were resolved with no identity scope, and they are resolved again
        here under the customer's: a confirmed mapping filed for this
        customer, or an equivalence the engine only proposes within their
        scope, is exactly what choosing them is supposed to bring in.

        Re-resolved from ``reqCode`` — the request as the engine normalised it
        — not from the line's *previous* answer. Feeding a derived supply code
        back in as the next resolution's input is the composition CLAUDE.md
        §1 forbids, and the request is the one operand that never composes.

        What a person put on a line survives when it still applies: a price
        they typed stays where the same product came back, and a reading they
        confirmed stays confirmed. A substitution they chose does not — it
        was a choice among that scope's candidates, and this is a new scope.
        Returns how many typed prices were carried over, for the sentence the
        screen says.
        """
        quote.customer = customer.strip()
        quote.customerId = customer_id or None
        if not quote.lines:
            return 0
        rows = [{"raw": ln.raw, "code": ln.reqCode, "qty": ln.reqQty,
                 "proposed": ln.proposed, "reading": ln.reading}
                for ln in quote.lines]
        fresh = self.build_lines(rows, zoho, customer_scope, bands, mapping_store,
                                 connection_id=quote.connectionId)
        kept = 0
        for old, new in zip(quote.lines, fresh):
            # Same id: the snapshots and approval requests filed under this
            # line are about this request, and the request has not changed.
            new.id = old.id
            if (old.priceSource == "USER" and old.quoted is not None
                    and new.supplyCode == old.supplyCode):
                new.quoted, new.priceSource = old.quoted, "USER"
                kept += 1
        quote.lines[:] = fresh
        return kept

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
        ln.costSource = None if item.cost is None else ("DEMO" if item.synthetic
                                                        else "BOOKS")
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


store = QuoteStore()
