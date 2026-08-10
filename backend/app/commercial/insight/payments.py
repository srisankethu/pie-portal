"""How long settlement actually takes, on either side of the ledger.

This is the series the health timeline used to declare it could not draw. The
platform held invoice lines and no receipts, so days-to-pay had to be invented
or omitted, and it was omitted. Now that payments are ingested it is computed,
and the refusal in ``cohorts.health_timeline`` comes off — a screen that keeps
apologising for a gap it no longer has is a screen nobody reads carefully.

**One module, both directions.** How long a customer takes to pay us and how
long we take to pay a supplier are the same measurement with the parties
swapped: a document raised on one date, due on another, settled on a third. The
arithmetic — median, spread, trend, percentiles, the evidence floor — therefore
has one copy, and what differs between the sides is carried as data on ``Side``.
A sibling ``payables.py`` would have been four hundred lines of the same
statistics with ``customer`` renamed to ``vendor``, and the first bug fixed in
one of them would have been the moment the two stopped agreeing.

What genuinely differs is what a pattern *means*. A customer who is predictably
late is a conversation to have with them; *we* who are predictably late are a
terms problem our own suppliers are already planning around, and the sentence a
reader needs is not the same sentence. That is prose, so it lives on ``Side``.

**Days-to-pay is per application, not per payment.** One bank transfer settling
ten documents is ten observations, each with its own document date, and
averaging the payment instead would give a party who batches their remittances
a flattering single data point. The row grain is the document.

**An advance is not a fast payment.** Money moved against no document has no
document date to subtract, so it produces no observation at all. Counting it as
zero days would make every party who pays up front look like the fastest payer
in the book, which is the opposite of what a prepayment means for risk.

**Late is measured against the due date; slow is measured against the document
date.** They answer different questions — "were the terms honoured" and "how
long is the cash tied up" — and a document with no due date on record can answer
the second but not the first.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

#: Fewer settled documents than this and an "average days to pay" is one
#: transaction wearing a suit. Matches the spirit of the cadence floor.
MIN_SETTLEMENTS = 3

@dataclass(frozen=True)
class Settlement:
    """One document, settled. The grain everything here is computed at.

    ``party_id`` is a customer on the receivable side and a vendor on the
    payable one. Named for the role rather than either party because the
    measurement does not care which it is, and a field called ``customer_id``
    holding a vendor id is how a reader stops trusting the names in a module.
    """
    party_id: str
    document_ref: str
    document_number: Optional[str]
    document_date: date
    due_date: Optional[date]
    paid_on: date
    amount: float

    @property
    def days_to_pay(self) -> int:
        return (self.paid_on - self.document_date).days

    @property
    def days_late(self) -> Optional[int]:
        """None when no due date is on record — not zero, which would read as
        "paid exactly on time" for a document whose terms nobody recorded."""
        if self.due_date is None:
            return None
        return (self.paid_on - self.due_date).days

    @property
    def late(self) -> bool:
        return (self.days_late or 0) > 0


#: A party whose days-to-pay swing by more than this around their own median
#: cannot be planned around, and that is a different problem from being slow.
#: Expressed in days rather than as a coefficient because a distributor thinks
#: in days: "give or take a fortnight" is a sentence somebody can act on.
ERRATIC_SPREAD_DAYS = 21

#: How much the second half of a party's history has to move against the first
#: before it is called a trend rather than noise.
TREND_DAYS = 10

#: The named patterns, and what each one means as a job of work. A pattern with
#: no consequence attached is a label; these are the four different things a
#: person actually does about a payer.
PATTERNS: dict[str, dict[str, str]] = {
    "PROMPT": {
        "label": "Pays to terms",
        "meaning": "Settles on or before the due date, consistently. Nothing to "
                   "do; worth knowing before anyone tightens their terms.",
    },
    "PREDICTABLY_LATE": {
        "label": "Late, but predictable",
        "meaning": "Consistently over the due date by a similar margin. This is "
                   "a terms problem, not a collections problem — the terms on "
                   "record do not describe how this account actually pays.",
    },
    "ERRATIC": {
        "label": "Erratic",
        "meaning": "The spread is wide enough that no single number describes "
                   "them. The riskiest of the four: cash from this account "
                   "cannot be planned, whatever the average says.",
    },
    "TOO_FEW": {
        "label": "Not enough history",
        "meaning": "Fewer settled invoices than the floor. No pattern is "
                   "asserted rather than one being read out of two payments.",
    },
}

#: The same four patterns, read from the other end. We are the payer here, so
#: every one of them is a statement about this business rather than about an
#: account — and the job of work attached to it is ours.
PAYABLE_PATTERNS: dict[str, dict[str, str]] = {
    "PROMPT": {
        "label": "We pay to terms",
        "meaning": "Settled on or before the due date, consistently. Worth "
                   "knowing before anyone negotiates on the strength of it — "
                   "it is a position, and it is currently being given away.",
    },
    "PREDICTABLY_LATE": {
        "label": "We are late, predictably",
        "meaning": "Consistently over the due date by a similar margin. The "
                   "terms on record do not describe how this supplier is "
                   "actually paid, and they are already planning around the "
                   "real number rather than the agreed one.",
    },
    "ERRATIC": {
        "label": "We are erratic",
        "meaning": "The spread is wide enough that no single number describes "
                   "how this supplier gets paid. The costly one: a supplier who "
                   "cannot plan around us prices that in, and prices it into "
                   "the quote before anyone here sees it.",
    },
    "TOO_FEW": {
        "label": "Not enough history",
        "meaning": "Fewer settled bills than the floor. No pattern is asserted "
                   "rather than one being read out of two payments.",
    },
}

#: Direction of travel, kept separate from the pattern. A party can be erratic
#: *and* improving, and collapsing the two would lose one of them.
TRENDS: dict[str, str] = {
    "IMPROVING": "Paying faster than they used to",
    "STEADY": "No material change",
    "DETERIORATING": "Paying slower than they used to",
    "UNKNOWN": "Too few settlements to compare two halves",
}

PAYABLE_TRENDS: dict[str, str] = {
    "IMPROVING": "We are paying faster than we used to",
    "STEADY": "No material change",
    "DETERIORATING": "We are paying slower than we used to",
    "UNKNOWN": "Too few settlements to compare two halves",
}


@dataclass(frozen=True)
class Side:
    """One side of the ledger, as the words that change with it.

    Everything measured in this module is the same arithmetic whichever
    direction the money runs, so the computation takes one of these rather than
    being written twice. Only what a reader is told differs: which noun names
    the party, which names the document, and what each pattern means as a job
    of work.

    ``party`` and ``document`` are pluralised by appending an ``s``. Both words
    are regular on both sides — customer/vendor, invoice/bill — and a
    pluralisation table for four known nouns would be ceremony.
    """

    key: str
    #: "customer" or "vendor". Becomes the response's list key (``customers``)
    #: and each row's id field (``customer_id``), so the two endpoints keep the
    #: shapes their own screens already read.
    party: str
    #: "invoice" or "bill".
    document: str
    patterns: dict[str, dict[str, str]]
    trends: dict[str, str]
    note: str

    @property
    def parties(self) -> str:
        return f"{self.party}s"

    @property
    def documents(self) -> str:
        return f"{self.document}s"

    @property
    def party_id_field(self) -> str:
        return f"{self.party}_id"


RECEIVABLE = Side(
    key="receivable", party="customer", document="invoice",
    patterns=PATTERNS, trends=TRENDS,
    note=("Measured per invoice settled, not per payment: one transfer "
          "clearing ten invoices is ten observations. Advances carry no "
          "invoice date and are counted separately rather than as same-day "
          "payments."),
)

PAYABLE = Side(
    key="payable", party="vendor", document="bill",
    patterns=PAYABLE_PATTERNS, trends=PAYABLE_TRENDS,
    note=("Measured per bill settled, not per payment: one transfer clearing "
          "ten bills is ten observations. A payment applied to no bill carries "
          "no bill date and is counted separately rather than as a same-day "
          "payment."),
)


def _spread(days: list[int]) -> Optional[float]:
    """Median absolute deviation, not standard deviation.

    One invoice settled nine months late is a story about that invoice. A
    standard deviation would let it redefine the customer; the MAD does not,
    which is the same reason the headline figure is a median.
    """
    if len(days) < 2:
        return None
    mid = statistics.median(days)
    return round(statistics.median([abs(d - mid) for d in days]), 1)


def _trend(settled: list[Settlement]) -> str:
    """Compare the older half of their history against the newer half.

    By document date, not payment date: the question is whether the documents
    being raised now get settled faster than the ones raised before.
    """
    if len(settled) < MIN_SETTLEMENTS * 2:
        return "UNKNOWN"
    ordered = sorted(settled, key=lambda s: s.document_date)
    mid = len(ordered) // 2
    before = statistics.median(s.days_to_pay for s in ordered[:mid])
    after = statistics.median(s.days_to_pay for s in ordered[mid:])
    if after - before >= TREND_DAYS:
        return "DETERIORATING"
    if before - after >= TREND_DAYS:
        return "IMPROVING"
    return "STEADY"


@dataclass(frozen=True)
class Lag:
    """How late one party settles, as three days-late figures.

    The spread of what they have actually done, not a forecast: ``early`` and
    ``late`` are the tenth and ninetieth percentiles of their own settled
    documents, and ``expected`` is their median. A party that has always paid
    on the day has ``0, 0, 0``, which is correct and says so.

    Percentiles rather than best-and-worst, because a single freak payment —
    one document settled six months late after a dispute — would otherwise
    become the whole worst case for that party for ever. The tails are where
    that lands, and clipping them is the difference between a planning range
    and a chart driven by two outliers.

    Read on both sides of the ledger by ``insight/cashflow``, and it is the
    *same* three numbers there: what changes is which direction each one is
    good news in. A customer at ``late_days`` is money arriving later; this
    business at ``late_days`` is money leaving later, which is the opposite for
    the week that has to fund it.

    ``expected_days_to_pay`` is the fourth number and it answers a different
    question from the other three. They are measured against the **due date** —
    "were the terms honoured". It is measured against the **document date** —
    "how long is the cash tied up" — which is the distinction this module's
    docstring draws, and it is what a concentration reader needs: how long the
    money sitting with the largest accounts has been out, not whether those
    accounts were punctual about it. Carried on ``Lag`` rather than as a sibling
    function so the evidence floor stays in one place; the cost of that is
    stated in ``lag``.
    """
    party_id: str
    early_days: int
    expected_days: int
    late_days: int
    settlements: int
    #: Median days from document date to settlement. Nearest-rank like the
    #: three above, for the same reason: an interpolated 17.4 days is not an
    #: observation anybody made.
    expected_days_to_pay: int


def _percentile(values: list[int], fraction: float) -> int:
    """The nearest-rank percentile, rounded to a whole day.

    Nearest-rank rather than interpolated: these are days, an interpolated
    17.4 days is not an observation anybody made, and the projection is going
    to floor it to a week anyway.
    """
    ordered = sorted(values)
    if not ordered:
        return 0
    rank = max(1, min(len(ordered), math.ceil(fraction * len(ordered))))
    return int(ordered[rank - 1])


def lag(settled: list[Settlement]) -> Optional[Lag]:
    """This party's days-late distribution, or ``None`` when it is not known.

    ``None`` in three cases, and every one of them means "leave their money on
    its due date" rather than "assume they are prompt":

    * fewer than ``MIN_SETTLEMENTS`` settled documents — the same floor the rest
      of this module applies, because three transactions is where "how they pay"
      stops being one transaction wearing a suit;
    * no settlement with a due date on record, so lateness is unanswerable;
    * a party id that is empty.

    Returning ``None`` rather than a zero-lag default is the whole point. A
    thin-evidence party silently treated as punctual would tighten a cash band
    that the evidence does not tighten, which is the one outcome worse than a
    wide one.

    ``expected_days_to_pay`` rides on the same three refusals, and that is
    stricter than it strictly needs to be. Days-to-pay is measured from the
    document date and needs no due date at all, so a party with six settled
    invoices and terms recorded on none of them has a perfectly measurable
    days-to-pay and gets ``None`` here anyway. That is deliberate: the error it
    makes is *withholding a figure that exists*, which a reader can see, rather
    than *stating one the evidence does not carry*, which they cannot. Splitting
    the two floors would mean a ``Lag`` whose days-late fields are absent, and
    every consumer of the cash projection would have to learn that shape to gain
    a number none of them reads.
    """
    datable = [s.days_late for s in settled if s.days_late is not None]
    if len(datable) < MIN_SETTLEMENTS:
        return None
    party_id = settled[0].party_id if settled else ""
    if not party_id:
        return None
    return Lag(
        party_id=party_id,
        early_days=_percentile(datable, 0.10),
        expected_days=_percentile(datable, 0.50),
        late_days=_percentile(datable, 0.90),
        settlements=len(datable),
        # Over every settlement, not only the datable ones — an invoice with no
        # terms on record still has a date it was raised on and a date it was
        # paid on. len(settled) >= len(datable) >= MIN_SETTLEMENTS, so the floor
        # above already covers this figure.
        expected_days_to_pay=_percentile([s.days_to_pay for s in settled], 0.50),
    )


def lags(settlements: Iterable[Settlement]) -> dict[str, Lag]:
    """Every party's lag, keyed by party id. Parties without enough history are
    absent rather than present with zeros — see ``lag``."""
    by_party: dict[str, list[Settlement]] = {}
    for s in settlements:
        by_party.setdefault(s.party_id, []).append(s)
    out: dict[str, Lag] = {}
    for party_id, rows in by_party.items():
        measured = lag(rows)
        if measured is not None:
            out[party_id] = measured
    return out


def classify(settled: list[Settlement]) -> dict:
    """How this party pays — a pattern, a direction, and the evidence.

    Deterministic and derived only from settled documents. Nothing here
    predicts whether they will pay next time; it describes what they have done,
    which is the only thing the data supports.
    """
    if len(settled) < MIN_SETTLEMENTS:
        return {"pattern": "TOO_FEW", "trend": "UNKNOWN", "spread_days": None,
                "median_days_late": None, "part_paid_documents": 0,
                "largest_batch": 0}

    days = [s.days_to_pay for s in settled]
    spread = _spread(days)
    datable = [s for s in settled if s.days_late is not None]
    median_late = (statistics.median(s.days_late for s in datable   # type: ignore[misc]
                                     ) if datable else None)

    if spread is not None and spread > ERRATIC_SPREAD_DAYS:
        pattern = "ERRATIC"
    elif median_late is None:
        # No due date anywhere on record: their punctuality is unanswerable, so
        # it is not answered. Spread still says whether they are plannable.
        pattern = "PROMPT" if spread is not None and spread <= ERRATIC_SPREAD_DAYS else "ERRATIC"
    elif median_late > 0:
        pattern = "PREDICTABLY_LATE"
    else:
        pattern = "PROMPT"

    # Two habits worth naming because they change what a collections call is
    # about: a document settled in several instalments, and one payment
    # clearing a batch.
    by_document: dict[str, int] = {}
    by_payment_day: dict[date, int] = {}
    for s in settled:
        by_document[s.document_ref] = by_document.get(s.document_ref, 0) + 1
        by_payment_day[s.paid_on] = by_payment_day.get(s.paid_on, 0) + 1

    return {
        "pattern": pattern,
        "trend": _trend(settled),
        "spread_days": spread,
        "median_days_late": (round(median_late, 1) if median_late is not None else None),
        "part_paid_documents": sum(1 for n in by_document.values() if n > 1),
        "largest_batch": max(by_payment_day.values()),
    }


@dataclass
class PartyPayment:
    """One party's settlement behaviour, in figures.

    The party is identified by ``party_id`` here and rendered as
    ``customer_id`` or ``vendor_id`` by ``to_dict``, so each endpoint keeps the
    field its own screen already reads.
    """

    party_id: str
    label: str
    settlements: int
    median_days_to_pay: Optional[float]
    worst_days_to_pay: Optional[int]
    late_count: int
    #: Of the settlements that *have* a due date. A share computed over
    #: documents that could never be late would understate the problem.
    datable_count: int
    total_settled: float
    #: What the terms on record say, when anything does. The measured median
    #: beside it is what turns "we agreed 30 days" into "we take 44" — which is
    #: the promised-against-actual comparison, at the party grain.
    agreed_terms_days: Optional[int]

    @property
    def late_share(self) -> Optional[float]:
        return (self.late_count / self.datable_count) if self.datable_count else None

    @property
    def terms_gap_days(self) -> Optional[float]:
        """Measured days-to-pay minus the agreed terms, or ``None``.

        ``None`` when either side of the subtraction is missing, and that is
        not the same as zero: a supplier with no terms on record is not a
        supplier being paid exactly to terms.
        """
        if self.agreed_terms_days is None or self.median_days_to_pay is None:
            return None
        return round(self.median_days_to_pay - self.agreed_terms_days, 1)

    def to_dict(self, side: "Side") -> dict:
        return {
            side.party_id_field: self.party_id, "label": self.label,
            "settlements": self.settlements,
            "median_days_to_pay": self.median_days_to_pay,
            "worst_days_to_pay": self.worst_days_to_pay,
            "late_count": self.late_count,
            "datable_count": self.datable_count,
            "late_share": (round(self.late_share, 4)
                           if self.late_share is not None else None),
            "total_settled": round(self.total_settled, 2),
            "agreed_terms_days": self.agreed_terms_days,
            "terms_gap_days": self.terms_gap_days,
            # Below the floor there is no rhythm to report. Named rather than
            # shown as a number derived from one or two documents.
            "estimable": self.settlements >= MIN_SETTLEMENTS,
        }


def build(settlements: Iterable[Settlement], names: dict[str, str],
          as_of: date, *, side: Side = RECEIVABLE, advances: int = 0,
          unapplied_total: float = 0.0,
          terms: Optional[dict[str, Optional[int]]] = None,
          unattributed: int = 0) -> dict:
    """The book's settlement behaviour, and every party's place in it.

    ``side`` selects the vocabulary and the prose; every figure below is
    computed identically either way. ``terms`` maps a party id to the payment
    terms on record for them, which is what lets a row state the agreed number
    beside the measured one instead of only the measured one.
    """
    rows = list(settlements)
    terms = terms or {}
    if not rows:
        return {"as_of": as_of.isoformat(), side.parties: [], "distribution": [],
                "median_days_to_pay": None, "late_share": None,
                "advances": advances, "unapplied_total": round(unapplied_total, 2),
                "unattributed": unattributed, "side": side.key,
                "party": side.party, "document": side.document,
                "min_settlements": MIN_SETTLEMENTS}

    by_party: dict[str, list[Settlement]] = {}
    for s in rows:
        by_party.setdefault(s.party_id, []).append(s)

    patterns: dict[str, dict] = {}
    parties: list[PartyPayment] = []
    for party_id, group in by_party.items():
        patterns[party_id] = classify(group)
        datable = [s for s in group if s.days_late is not None]
        parties.append(PartyPayment(
            party_id=party_id,
            label=names.get(party_id, party_id),
            settlements=len(group),
            # Median, not mean: one document paid nine months late is a story
            # on its own, not evidence that the party is a nine-month payer.
            median_days_to_pay=round(statistics.median(s.days_to_pay for s in group), 1),
            worst_days_to_pay=max(s.days_to_pay for s in group),
            late_count=sum(1 for s in datable if s.late),
            datable_count=len(datable),
            total_settled=float(sum(s.amount for s in group)),
            agreed_terms_days=terms.get(party_id),
        ))

    # Slowest first, then by money at stake. The question is "whose cash is
    # tied up longest", and that is a rank.
    parties.sort(key=lambda c: (c.median_days_to_pay or 0, c.total_settled),
                 reverse=True)

    all_days = [s.days_to_pay for s in rows]
    datable_all = [s for s in rows if s.days_late is not None]

    return {
        "as_of": as_of.isoformat(),
        side.parties: [{**c.to_dict(side), **patterns[c.party_id]} for c in parties],
        "patterns": side.patterns,
        "trends": side.trends,
        "pattern_counts": _counts(patterns, side),
        "erratic_spread_days": ERRATIC_SPREAD_DAYS,
        "distribution": _distribution(all_days),
        "median_days_to_pay": round(statistics.median(all_days), 1),
        "settlements": len(rows),
        "late_share": (round(sum(1 for s in datable_all if s.late) / len(datable_all), 4)
                       if datable_all else None),
        "undatable_count": len(rows) - len(datable_all),
        #: Money moved with no document behind it. Reported beside the averages
        #: rather than folded into them.
        "advances": advances,
        "unapplied_total": round(unapplied_total, 2),
        #: Settlements real enough to have happened and belonging to nobody the
        #: platform can name. Counted rather than dropped silently, so a thin
        #: list reads as "we cannot attribute these" instead of "there are few".
        "unattributed": unattributed,
        #: Which side this is, so a screen does not have to infer it from which
        #: list key came back.
        "side": side.key,
        "party": side.party,
        "document": side.document,
        "min_settlements": MIN_SETTLEMENTS,
        "note": side.note,
    }


#: Buckets a distributor actually talks in. Fixed rather than quantile because
#: the question here is "are we being paid to terms", and terms are absolute —
#: a quantile band would move every time the book did and 30 days would stop
#: meaning 30 days.
_BUCKETS: tuple[tuple[str, int, Optional[int]], ...] = (
    ("0–15 days", 0, 15),
    ("16–30 days", 16, 30),
    ("31–45 days", 31, 45),
    ("46–60 days", 46, 60),
    ("61–90 days", 61, 90),
    ("over 90 days", 91, None),
)


def _distribution(days: list[int]) -> list[dict]:
    out = []
    for label, lo, hi in _BUCKETS:
        n = sum(1 for d in days if d >= lo and (hi is None or d <= hi))
        out.append({"label": label, "from": lo, "to": hi, "count": n})
    # Paid before the invoice was raised: real, and it is a prepayment against
    # a known invoice rather than a negative duration to be hidden.
    early = sum(1 for d in days if d < 0)
    if early:
        out.insert(0, {"label": "paid in advance of the invoice",
                       "from": None, "to": -1, "count": early})
    return out


def _counts(patterns: dict[str, dict], side: Side = RECEIVABLE) -> dict[str, int]:
    out = {key: 0 for key in side.patterns}
    for p in patterns.values():
        out[p["pattern"]] = out.get(p["pattern"], 0) + 1
    return out


def monthly_series(settlements: Iterable[Settlement], periods) -> list[dict]:
    """Median days-to-pay per month, for the customer health timeline.

    Keyed on the month the document was *raised*, not the month it was paid, so
    the point sits beside that month's revenue and orders. A payment series
    indexed by payment date would put January's cash on the March row and make
    the three rows describe different things.
    """
    rows = list(settlements)
    out = []
    for period in periods:
        in_period = [s for s in rows if period.contains(s.document_date)]
        out.append({
            **period.to_dict(),
            "settled": len(in_period),
            "median_days_to_pay": (
                round(statistics.median(s.days_to_pay for s in in_period), 1)
                if in_period else None),
        })
    return out
