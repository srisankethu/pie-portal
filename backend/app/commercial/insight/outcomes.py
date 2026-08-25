"""Which quotes were won, which were lost, and whether the price is the reason.

The platform has recorded quote outcomes since the outcome table was added and
has never once read them. Beside them sits ``quote_decisions``: an append-only
snapshot of every line at the moment it was priced — the cost basis used, the
price quoted, the margin that produced, and the thresholds version that judged
it. That pairing is unusually good evidence, because it means a lost quote can
be examined **at the numbers the salesperson could actually see on the day**
rather than re-judged against today's cost.

The owner's question is the shape of this module: *losing at 8% below my quote
is a pricing problem; losing on delivery is a stock problem — I cannot
currently tell which*. So there are two halves here, and they answer that
sentence in order.

**Counted per quote, never per line.** A customer accepts or declines a quote;
they do not accept four of its lines. A forty-line tender that lost is one
loss, and counting lines would let one big RFQ outvote a quarter of trading.
The line grain still exists — the price comparison below needs it — but no win
rate is ever computed from it.

**A quote appears in every slice it belongs to.** A quote carrying three
principals' products counts once in each of those three principals' win rates,
so the slice counts deliberately sum to more than the total. The alternative is
attributing the whole quote to whichever principal happens to be on line one,
which is worse in a way nobody would ever notice.

**The evidence floor, and why it is higher here than for payments.**
``payments.MIN_SETTLEMENTS`` is three, because three settled invoices really do
describe a rhythm. Three quotes describe nothing: a win rate over three quotes
can only be 0%, 33%, 67% or 100%, and every one of those reads as a finding.
So the floor is higher, and below it the rate is ``None`` with the count
visible beside it — an absent figure with a reason, rather than a confident
percentage over four quotes.

**Who won it is free text, and stays free text.** ``QuoteOutcome.lost_to`` is
a name somebody typed, and the column says why in its own docstring: a
competitor is not an entity this platform holds, and a lookup table of them
would be a second customer master maintained by nobody. So the rollup below
groups on capitals and spacing and nothing else. It will not fold ``Sandvik``
into ``Sandvik India``, because doing so would invent the entity the column
refuses to hold and then report losses against it — with no mark on the screen
saying a merge had happened. Two rows for one firm is the error a reader can
see and correct; one row for two firms is not.

**Where the price comparison refuses.** The comparison that matters is *this
lost quote against the price that wins*, and it is only meaningful within one
product and one quantity band: the same insert at 10 pieces and at 1,000 is two
different prices for good reasons, and pooling them would manufacture a gap out
of the mix. So a comparison exists only where one product-and-band has enough
won observations *and* enough lost ones. Most products will not qualify, and a
book with a thin quote history produces no comparisons at all. That is the
correct answer to "are we losing on price" when the evidence cannot say.

**What is RESTRICTED, and what is not.** A win rate carries no cost: a
salesperson may see their own, and the whole first half of this module is
computed without a single cost figure. The second half — margin on what we win
against margin on what we lose, and how far above the winning price a lost
quote sat — is commercial position, and the router keeps it behind a
manager-or-owner endpoint the way ``/payables`` is kept behind one. Nothing
here decides that; ``build`` and ``pricing`` are separate functions precisely so
the router can serve one and not the other, rather than serving one dictionary
with fields stripped out of it.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Iterable, Optional

from ...domain.enums import LOSS_REASON_NOT_RECORDED, QuoteLossReason
from ..benchmark import median_decimal
from . import absence, periods

_ZERO = Decimal("0")

#: Fewer decided quotes than this and a win rate is arithmetic rather than
#: evidence — see the module docstring. Applied to every slice independently,
#: so the book can have a rate while a single customer does not.
#:
#: A sample-size floor answers "is there enough evidence", never "could this
#: evidence have gone the other way". Both have to be Yes: a slice of eight
#: quotes none of which was ever sent clears this floor and reports 0%, which
#: is a fabrication about a book whose wins were unrecordable. ``Slice`` checks
#: the second question separately.
MIN_DECIDED_QUOTES = 8

#: Won *and* lost observations needed on one product-and-quantity-band before
#: the two medians are compared. Three each, matching
#: ``payments.MIN_SETTLEMENTS``: the question here is "what does this item
#: normally go out at", which two prices genuinely cannot answer and three
#: begin to.
MIN_PRICE_OBSERVATIONS = 3


#: What each reason means, and — the part that answers the owner — whose
#: problem it is. A reason with no owner attached is a label; these are the
#: four different places the fix lives.
REASONS: dict[str, dict[str, str]] = {
    QuoteLossReason.PRICE.value: {
        "label": "Price",
        "owner": "PRICING",
        "meaning": "They said the number was too high. The one reason that can "
                   "be checked against evidence rather than believed: the "
                   "pricing half of this screen compares what was quoted "
                   "against what wins.",
    },
    QuoteLossReason.DELIVERY.value: {
        "label": "Delivery or availability",
        "owner": "SUPPLY",
        "meaning": "We could not supply it when they needed it. A stock and "
                   "lead-time problem, and repricing will not touch it.",
    },
    QuoteLossReason.COMPETITOR.value: {
        "label": "Went to a competitor",
        "owner": "POSITION",
        "meaning": "Somebody else took it, for a reason other than the number "
                   "alone — an approval, a relationship, a make we do not "
                   "carry. Worth separating from price precisely because it is "
                   "so often recorded as price.",
    },
    QuoteLossReason.CUSTOMER_CANCELLED.value: {
        "label": "Customer cancelled",
        "owner": "NOT_OURS",
        "meaning": "The requirement went away — the job was cancelled, the "
                   "budget went. Counted, and excluded from nothing, but it is "
                   "not a signal about how this business quotes.",
    },
    QuoteLossReason.NO_DECISION.value: {
        "label": "No decision",
        "owner": "NOT_OURS",
        "meaning": "They never came back. Honest, common, and the reason this "
                   "list has five entries rather than four — without it, every "
                   "silent enquiry would be filed under something it was not.",
    },
    LOSS_REASON_NOT_RECORDED: {
        "label": "Not recorded",
        "owner": "UNKNOWN",
        "meaning": "Lost before a reason was asked for. Shown rather than "
                   "hidden, because these quotes are in the win rate and their "
                   "silence should be visible beside it. The API refuses new "
                   "losses without a reason, so this bucket cannot grow.",
    },
}

#: What each ``owner`` above means as a sentence. Kept beside ``REASONS``
#: rather than repeated inside every entry of it: five reasons share four
#: owners today, and a sixth reason should name an existing owner rather than
#: restate its prose.
OWNERS: dict[str, str] = {
    "PRICING": "A pricing problem",
    "SUPPLY": "A stock and lead-time problem",
    "POSITION": "A position problem — not the number alone",
    "NOT_OURS": "Not a signal about how we quote",
    "UNKNOWN": "Lost before we asked why",
}


@dataclass(frozen=True)
class DecidedQuote:
    """One quote the customer has answered, and everything counted about it.

    The grain of every win rate in this module. ``value`` is what was quoted —
    the sum of the priced lines' revenue at the price that went out — which is
    a customer-facing number and carries no cost.

    ``gross_profit`` is ``None`` unless *every* line on the quote had a cost
    basis. A partially-costed quote would understate its own profit, and a
    won-against-lost margin comparison built from a mixture of complete and
    incomplete quotes is a comparison of coverage rather than of margin.
    """

    quote_id: str
    customer_id: str
    customer_label: str
    won: bool
    #: Whether this quote ever reached SENT. A quote that did not could never
    #: have come back WON — the transition table only reaches WON through SENT
    #: — so it sits in a win-rate denominator as a row the numerator could not
    #: have reached. Carried per quote rather than inferred from ``won``,
    #: because the whole point is to count the ones that are not wins.
    ever_sent: bool
    #: A ``QuoteLossReason`` value, or ``LOSS_REASON_NOT_RECORDED``. Empty on a
    #: won quote — there is nothing to explain.
    loss_reason: str
    decided_on: date
    lines: int
    value: Decimal
    gross_profit: Optional[Decimal] = None
    #: Facet keys the quote belongs to. A quote spanning two principals carries
    #: both and is counted in both — see the module docstring.
    principals: tuple[str, ...] = ()
    product_lines: tuple[str, ...] = ()
    #: Who won it, exactly as typed at the desk. Empty on a won quote and on a
    #: loss nobody attributed — and those two emptinesses are told apart by
    #: ``won``, never by the string. Carried raw rather than pre-normalised so
    #: the grouping rule lives in one place and the display form stays
    #: recoverable; see ``competitor_key``.
    lost_to: str = ""

    @property
    def costed(self) -> bool:
        return self.gross_profit is not None


@dataclass(frozen=True)
class PricedLine:
    """One priced line on a decided quote, as the price comparison needs it.

    Taken from the *latest* snapshot of that line — a re-priced line has
    several, and the negotiation's last word is the price the customer answered.
    Earlier rows stay in the audit trail; they are simply not what was accepted
    or refused.
    """

    quote_id: str
    product_id: str
    product_label: str
    quantity_band: str
    unit_price: Decimal
    won: bool
    customer_id: str
    #: What this customer has historically paid for this item, where the
    #: metrics layer knows. ``None`` is common and is left as ``None``.
    customer_paid: Optional[Decimal] = None


@dataclass(frozen=True)
class Slice:
    """One cut of the book — a customer, a principal, a product line, a month.

    ``win_rate`` is ``None`` below the floor and the counts are shown anyway,
    so a thin slice reads as "3 decided, no rate yet" instead of "0%".
    """

    key: str
    label: str
    decided: int
    won: int
    won_value: Decimal
    lost_value: Decimal
    #: How many of ``decided`` ever reached SENT — the ones a win could have
    #: come from. Zero means this slice's wins were unrecordable, not absent.
    ever_sent: int = 0

    @property
    def lost(self) -> int:
        return self.decided - self.won

    @property
    def estimable(self) -> bool:
        """Enough decided quotes, *and* a numerator any of them could reach.

        The second half is not a refinement of the first. A slice where nothing
        was ever sent has no path to a win at all, so its 0% is not a low win
        rate — it is a measurement of a question the evidence cannot answer, and
        it looked identical to a genuinely bad quarter on the screen.

        A recorded win settles it whatever the sent stamps say: rows written
        before that column meant anything carry none, and reading them alone
        would turn a slice that demonstrably won something into an UNKNOWN.
        """
        return (self.decided >= MIN_DECIDED_QUOTES
                and bool(self.won or self.ever_sent))

    @property
    def win_rate(self) -> Optional[float]:
        if not self.estimable:
            return None
        return round(self.won / self.decided, 4)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label,
            "decided": self.decided, "won": self.won, "lost": self.lost,
            "ever_sent": self.ever_sent,
            "win_rate": self.win_rate, "estimable": self.estimable,
            "won_value": float(self.won_value),
            "lost_value": float(self.lost_value),
            "quoted_value": float(self.won_value + self.lost_value),
        }


def _slice(key: str, label: str, quotes: list[DecidedQuote]) -> Slice:
    return Slice(
        key=key, label=label,
        decided=len(quotes),
        won=sum(1 for q in quotes if q.won),
        ever_sent=sum(1 for q in quotes if q.ever_sent),
        won_value=sum((q.value for q in quotes if q.won), _ZERO),
        lost_value=sum((q.value for q in quotes if not q.won), _ZERO),
    )


def by_facet(quotes: Iterable[DecidedQuote],
             facets: Callable[[DecidedQuote], Iterable[str]],
             labels: dict[str, str]) -> list[Slice]:
    """Slice the decided quotes by whatever ``facets`` names them.

    One function rather than four near-identical ones, because customer,
    principal and product line differ only in which keys a quote carries — and
    the moment the floor or the value arithmetic is fixed in one of four copies,
    the four stop agreeing. Sorted by decided count then by lost value, so the
    thing worth looking at is first whether or not it clears the floor.
    """
    grouped: dict[str, list[DecidedQuote]] = {}
    for quote in quotes:
        for key in facets(quote):
            if key:
                grouped.setdefault(key, []).append(quote)
    rows = [_slice(key, labels.get(key, key), group)
            for key, group in grouped.items()]
    rows.sort(key=lambda s: (s.decided, s.lost_value), reverse=True)
    return rows


def over_time(quotes: Iterable[DecidedQuote],
              months: list[periods.Period]) -> list[Slice]:
    """The same slice, per calendar month, by the date the quote was decided.

    Decided rather than sent, because the month in question is the one the
    business found out. A quote sent in March and answered in May says nothing
    about March, and indexing it there would move a loss into a month whose
    numbers have already been read.
    """
    rows = list(quotes)
    return [_slice(period.label, period.label,
                   [q for q in rows if period.contains(q.decided_on)])
            for period in months]


def reason_mix(quotes: Iterable[DecidedQuote]) -> list[dict]:
    """How the losses divide, by reason and by whose problem it is.

    Value as well as count, because five small losses on price and one large
    one on delivery are not the same book, and a count alone says they are.
    """
    lost = [q for q in quotes if not q.won]
    grouped: dict[str, list[DecidedQuote]] = {}
    for quote in lost:
        grouped.setdefault(quote.loss_reason or LOSS_REASON_NOT_RECORDED,
                           []).append(quote)
    rows = [
        {
            "reason": code,
            "label": REASONS.get(code, {}).get("label", code),
            "owner": REASONS.get(code, {}).get("owner", "UNKNOWN"),
            "count": len(group),
            "share": round(len(group) / len(lost), 4) if lost else None,
            "value": float(sum((q.value for q in group), _ZERO)),
        }
        for code, group in grouped.items()
    ]
    rows.sort(key=lambda r: (r["count"], r["value"]), reverse=True)
    return rows


# ── who is taking the business ──────────────────────────────────────────────
#
# ``QuoteOutcome.lost_to`` has been captured at the desk since the loss-reason
# vocabulary landed and read by nothing: no screen in this platform has ever
# sliced a loss by who won it. What follows is that rollup, and it is a sibling
# of ``reason_mix`` deliberately — same grain (one lost quote, one vote), same
# floor, same habit of showing the bucket it cannot name beside the ones it can.
#
# Nothing here reads ``gross_profit``, for the reason ``build`` does not: a
# name, a count and a quoted value are facts a salesperson may see, and the
# moment one row carried anything derived from what we paid, this whole
# response would have to move behind ``/quote-pricing``.

#: Losses with no winner typed on them. Deliberately the same WORD the reason
#: vocabulary uses for the same idea — one screen should not print "Not
#: recorded" in one panel and "Unknown" in the next for two identical silences.
#: A literal rather than an alias of ``LOSS_REASON_NOT_RECORDED``, though: that
#: sentinel stands for a missing *loss reason* and is compared against values
#: persisted in ``quote_outcomes.loss_reason``. The shared wording is the goal;
#: sharing the identifier would mean a rename or migration of one concept
#: silently changing a response key belonging to the other.
COMPETITOR_NOT_RECORDED = "NOT_RECORDED"

#: Losses where there is no winner to record, because the requirement died.
#: Held apart from the bucket above, and that separation is the point: filing a
#: cancelled job under "not recorded" would put a worklist item on a blank
#: nobody can ever fill in, and would understate how much of the book really is
#: attributed.
COMPETITOR_NO_WINNER = "NO_WINNER"

#: The two ways a loss carries no competitor, as sentences. Shaped like
#: ``REASONS`` so a client renders both catalogues the same way, and present in
#: the response for the reason ``REASONS`` is: a bucket that appears in the
#: arithmetic and not in the legend is a number nobody can interpret.
UNATTRIBUTED: dict[str, dict[str, str]] = {
    COMPETITOR_NOT_RECORDED: {
        "label": "Winner not recorded",
        "meaning": ("Lost, and nobody typed who to. Shown beside the named "
                    "competitors rather than dropped out of the arithmetic: "
                    "these are the losses the shares are silent about, and the "
                    "size of this bucket is what decides how much of the "
                    "picture those shares are."),
        # COLLECTABLE, and the honest version of that: the field exists and is
        # often left empty. Asking who won on the way out is somebody's habit,
        # not an engineering project.
        "kind": absence.COLLECTABLE,
    },
    COMPETITOR_NO_WINNER: {
        "label": "No winner to name",
        "meaning": ("The requirement went away — the customer cancelled the "
                    "job. Nothing is missing from these rows, so they are held "
                    "out of the attributed share rather than counted as "
                    "silence about a competitor who never existed."),
        # No ``kind`` on purpose. An absence reason would say something is
        # wanting here, and nothing is: this is an answer.
    },
}


def _went_elsewhere(loss_reason: str) -> Optional[bool]:
    """``QuoteLossReason``'s own three-valued answer, for a reason held as text.

    Not a second classification of what counts as a competitor's rupee — the
    enum owns that question so that a sixth reason has to answer it once, in one
    place. The not-recorded sentinel is outside the enum by design and lands on
    ``None`` here, which is *not* "no": a loss nobody explained may perfectly
    well have gone to somebody.
    """
    try:
        return QuoteLossReason(loss_reason).went_elsewhere
    except ValueError:
        return None


def competitor_key(raw: Optional[str]) -> str:
    """The form two typed spellings of one name are grouped on.

    Trim, collapse internal whitespace, case-fold. That is the whole of it, and
    the restraint is the design rather than an unfinished job.
    ``principals.normalise_name`` sits one module away and does more — it drops
    punctuation, strips legal forms and closes the string up, so ``Sandvik`` and
    ``Sandvik India Pvt. Ltd.`` become one key. That is right where it is used,
    matching a manufacturer against a vendor row this book actually holds, with
    a record there to be wrong against. It is wrong here, because there is no
    record to match against: a competitor is not an entity this platform holds,
    ``QuoteOutcome.lost_to`` says so in its own docstring, and a lookup table of
    them would be a second customer master maintained by nobody.

    So the only difference merged here is one nobody typed on purpose — the case
    and the spacing. Everything else stays two rows, which is the conservative
    error in the pair: two rows for one firm understate that firm and look odd
    to the person who typed both, while one row for two firms invents a
    competitor and reports losses against it with nothing on the screen ever
    showing that it happened.

    The accepted edge, stated because it is real: two genuinely different
    competitors whose names differ only in capitals and spacing would be merged.
    Far less likely than one firm typed twice by two people.
    """
    return " ".join((raw or "").split()).casefold()


def _display_name(group: list[DecidedQuote]) -> str:
    """The spelling to print: the one typed most often, ties alphabetically.

    A display form rather than the grouping key, because a screen showing
    ``bright tools`` where every rep typed ``Bright Tools`` looks like the
    platform mangled the name — and a reader who distrusts the label stops
    trusting the count beside it. Ties break alphabetically so the same book
    renders the same name twice running.
    """
    spellings = Counter(" ".join((q.lost_to or "").split()) for q in group)
    return min(spellings.items(), key=lambda kv: (-kv[1], kv[0]))[0]


#: The bucket a loss lands in when the facet being counted is blank — an
#: unresolved customer, or a quote whose lines resolved to no product line.
#: Named so a breakdown always sums to the losses on the row above it.
UNNAMED_FACET = "UNATTRIBUTED"

_FACET_LABELS = {UNNAMED_FACET: "Unattributed"}


def _within(quotes: list[DecidedQuote],
            facets: Callable[[DecidedQuote], Iterable[str]],
            labels: dict[str, str]) -> list[dict]:
    """Count one competitor's losses by customer, or by product line.

    Deliberately *not* ``by_facet``, which is the near-match a capability search
    finds first and the one that would be wrong. That function slices *decided*
    quotes and computes a win rate; every group reaching this one is losses
    only. Put through it, a competitor holding ten losses in a single account
    would come back with ``win_rate: 0.0`` — arithmetically true of the rows
    handed to it and flatly false about the account, which holds our wins there
    too. Six lines that count what is actually being counted beat a reuse that
    needs a paragraph of warning stapled to it.
    """
    # A loss whose facet is blank is counted under a named bucket rather than
    # dropped. ``_scoped_outcomes`` deliberately KEEPS quotes whose customer did
    # not resolve — "dropping them would quietly remove a salesperson's own
    # losses from their own denominator" — so skipping them here would make a
    # competitor's breakdown sum to less than the ``losses`` printed on the same
    # row, with nothing on screen accounting for the gap. The module's habit is
    # to show the bucket it cannot name beside the ones it can; this is that.
    grouped: dict[str, list[DecidedQuote]] = {}
    for quote in quotes:
        keys = [key for key in facets(quote) if key]
        for key in (keys or [UNNAMED_FACET]):
            grouped.setdefault(key, []).append(quote)
    rows = [{"key": key, "label": labels.get(key, _FACET_LABELS.get(key, key)),
             "losses": len(group),
             "value": float(sum((q.value for q in group), _ZERO))}
            for key, group in grouped.items()]
    rows.sort(key=lambda r: (r["losses"], r["value"]), reverse=True)
    return rows


def win_rate_against_unavailable() -> dict:
    """Why there is no win rate against a competitor, said where it is read.

    The figure everybody asks for next, and this platform cannot produce it. A
    winner is recorded on losses only: nothing anywhere says who else was
    bidding on a quote we *won*, so the denominator — the quotes this name
    contested — is not observable, and no arithmetic over the rows that do exist
    recovers it.

    The tempting substitute is the dangerous one, which is why it is named here
    rather than left for somebody to rediscover as a good idea. Dividing losses
    to a name by the quotes we won in the same customer and product line answers
    a different question and reads as this one: those wins include every quote
    the competitor never bid on, so a firm taking one deal out of a busy account
    scores 95% against us while a firm contesting everything in a quiet account
    scores 50%. The ranking would be of how busy the account is.
    """
    return {
        "what": "A win rate against a named competitor",
        "why": ("Who won is recorded on losses only. Nothing records who else "
                "was bidding on the quotes we won, so the denominator — the "
                "quotes this name actually contested — is not observable, and "
                "there is no field holding it waiting to be filled in either. "
                "Wins in the same customer and product line are not that "
                "denominator: they include every quote this competitor never "
                "bid on, so the quietest account would produce the "
                "strongest-looking rival. What is reported instead is the "
                "share of losses whose winner was named, labelled as that "
                "rather than dressed up as a rate."),
        # BUILDABLE, not COLLECTABLE, and the distinction is the one
        # ``absence`` says pays for the whole module. COLLECTABLE is the
        # worklist: a field exists and somebody has to fill it in. Here there is
        # no field — recording who else bid on a quote we won is a schema and a
        # desk change before anybody can type anything. Filing it under the
        # worklist would put an item on it that nobody can ever complete, which
        # is the same mistake ``COMPETITOR_NO_WINNER`` was split out to avoid.
        "kind": absence.BUILDABLE,
    }


def competitor_mix(quotes: Iterable[DecidedQuote], *,
                   product_line_names: dict[str, str]) -> dict:
    """Who took the losses, how much went with them, and where they took it.

    Counted per lost quote, like every other count in this module. A quote
    spanning two product lines appears under both, so the breakdown inside a
    competitor sums to more than that competitor's losses — the same deliberate
    over-sum ``by_facet`` carries, for the same reason.

    **The floor is ``MIN_DECIDED_QUOTES``, not a new number.** A name recorded
    against two losses is a coincidence; "this firm is taking business off us"
    is the same class of claim as a win rate and has earned the same eight
    observations. Inventing a lower floor here would be picking a number to make
    the list non-empty, which is the one thing a floor exists to prevent. What
    sits below it is reported in aggregate — how many names, how many losses,
    how much value — and without those names, because naming them *is* the claim
    the floor refuses.

    **Blanks are their own bucket and never shrink a denominator.**
    ``attributed_share`` is over every loss. A competitor's
    ``share_of_named_losses`` is over every *named* loss, including the ones the
    floor withheld. Neither denominator is quietly the set of rows that happened
    to survive a filter.
    """
    lost = [q for q in quotes if not q.won]

    named: dict[str, list[DecidedQuote]] = {}
    unattributed: dict[str, list[DecidedQuote]] = {
        COMPETITOR_NOT_RECORDED: [], COMPETITOR_NO_WINNER: []}
    for quote in lost:
        key = competitor_key(quote.lost_to)
        if key:
            # A name typed against a reason that says the requirement died is a
            # contradictory record. The name wins: somebody had a reason to type
            # it, and a stale reason is the commoner of the two mistakes.
            named.setdefault(key, []).append(quote)
        elif _went_elsewhere(quote.loss_reason) is False:
            unattributed[COMPETITOR_NO_WINNER].append(quote)
        else:
            unattributed[COMPETITOR_NOT_RECORDED].append(quote)

    named_losses = sum(len(group) for group in named.values())
    rows: list[dict] = []
    withheld: list[list[DecidedQuote]] = []
    for key, group in named.items():
        if len(group) < MIN_DECIDED_QUOTES:
            withheld.append(group)
            continue
        rows.append({
            "key": key,
            "label": _display_name(group),
            # Every distinct spelling folded into this row, as typed. Shown so
            # that a merge this module made is visible to the one person who
            # can tell whether it was right.
            "spellings": sorted({" ".join((q.lost_to or "").split())
                                 for q in group}),
            "losses": len(group),
            "lost_value": float(sum((q.value for q in group), _ZERO)),
            "share_of_named_losses": round(len(group) / named_losses, 4),
            "customers": _within(group, lambda q: (q.customer_id,),
                                 {q.customer_id: q.customer_label
                                  for q in group}),
            "product_lines": _within(group, lambda q: q.product_lines,
                                     product_line_names),
        })
    rows.sort(key=lambda r: (r["losses"], r["lost_value"]), reverse=True)

    # The denominator is the losses that COULD carry a winner, which is every
    # loss less the ones whose requirement died. Dividing by all of them instead
    # contradicts the sentence this same response ships in
    # ``UNATTRIBUTED[COMPETITOR_NO_WINNER]["meaning"]`` — and it understates
    # coverage, so a book where every real loss is attributed still reads as
    # partly blind and the reader goes looking for records that do not exist.
    attributable = len(lost) - len(unattributed.get(COMPETITOR_NO_WINNER, ()))

    return {
        "losses": len(lost),
        "named_losses": named_losses,
        # Distinct names recorded, the withheld ones included. A count is not a
        # claim about any one of them, so the floor does not apply to it.
        "names_recorded": len(named),
        # What the share is *of*, stated rather than left to be inferred from
        # the difference between two other counts.
        "attributable_losses": attributable,
        # None rather than zero where nothing could carry a winner: a share of
        # nothing is not 0% attributed, it is a question with no rows under it.
        # That covers both a book with no losses and one whose every loss was a
        # cancelled requirement.
        "attributed_share": (round(named_losses / attributable, 4)
                             if attributable else None),
        "competitors": rows,
        "unattributed": [
            {"key": key, **UNATTRIBUTED[key], "losses": len(group),
             "value": float(sum((q.value for q in group), _ZERO))}
            for key, group in unattributed.items()
        ],
        "below_floor": {
            "floor": MIN_DECIDED_QUOTES,
            "names": len(withheld),
            "losses": sum(len(group) for group in withheld),
            "value": float(sum((q.value for group in withheld for q in group),
                               _ZERO)),
            "largest": max((len(group) for group in withheld), default=0),
            "why": (f"A name recorded against fewer than {MIN_DECIDED_QUOTES} "
                    "losses is a coincidence rather than a competitive "
                    "position, so it is counted here and not named above. The "
                    "fix for an empty list is more recorded outcomes, never a "
                    "lower floor — a floor moved to fill a screen makes every "
                    "figure on that screen worth less."),
        },
        "min_losses": MIN_DECIDED_QUOTES,
        "win_rate_against": win_rate_against_unavailable(),
        "note": ("Who won is free text, grouped on capitals and spacing only. "
                 "Two spellings of one firm stay two rows unless nothing but "
                 "case and whitespace separates them — merging further would "
                 "invent a competitor this platform does not hold and then "
                 "report losses against it. Counted per lost quote; a quote "
                 "touching two product lines appears under both."),
    }


def build(quotes: Iterable[DecidedQuote], *, as_of: date,
          customer_names: dict[str, str], principal_names: dict[str, str],
          product_line_names: dict[str, str], months: int = 12,
          open_quotes: int = 0) -> dict:
    """The win-rate view: counts, rates, slices and the loss mix.

    Cost-free by construction — nothing in this function reads
    ``gross_profit`` — which is what makes it servable to a salesperson without
    a field ever being stripped out of the result.

    ``open_quotes`` is the number still in flight. Reported beside the decided
    count rather than folded into it: a quote nobody has answered is not a loss,
    and counting it as one is how a win rate quietly becomes a measure of how
    fast customers reply.
    """
    rows = list(quotes)
    decided = len(rows)
    won = sum(1 for q in rows if q.won)
    overall = _slice("ALL", "The whole book", rows)

    return {
        "as_of": as_of.isoformat(),
        "decided": decided,
        "won": won,
        "lost": decided - won,
        "open": open_quotes,
        "win_rate": overall.win_rate,
        "estimable": overall.estimable,
        "won_value": float(overall.won_value),
        "lost_value": float(overall.lost_value),
        "customers": [s.to_dict() for s in
                      by_facet(rows, lambda q: (q.customer_id,), customer_names)],
        "principals": [s.to_dict() for s in
                       by_facet(rows, lambda q: q.principals, principal_names)],
        "product_lines": [s.to_dict() for s in
                          by_facet(rows, lambda q: q.product_lines,
                                   product_line_names)],
        "months": [s.to_dict() for s in
                   over_time(rows, periods.months_back(as_of, months))],
        "reasons": reason_mix(rows),
        # Who took them, beside why they went. The two answer different halves
        # of one question and a screen that had the reason mix without this one
        # could say the business loses on price and not who to.
        "competitors": competitor_mix(rows,
                                      product_line_names=product_line_names),
        "reason_catalogue": REASONS,
        "owners": OWNERS,
        "min_decided_quotes": MIN_DECIDED_QUOTES,
        "quotes": [
            {
                "quote_id": q.quote_id,
                "customer_id": q.customer_id,
                "customer_label": q.customer_label,
                "status": "WON" if q.won else "LOST",
                "loss_reason": q.loss_reason or None,
                "loss_reason_label": (REASONS.get(q.loss_reason, {}).get("label")
                                      if not q.won else None),
                # As typed, not the grouping key — this row is the evidence
                # behind the rollup and has to be readable as what was written.
                "lost_to": (q.lost_to or None) if not q.won else None,
                "decided_on": q.decided_on.isoformat(),
                "lines": q.lines,
                "value": float(q.value),
            }
            for q in sorted(rows, key=lambda q: q.decided_on, reverse=True)
        ],
        "note": ("Counted per quote, not per line — a customer accepts or "
                 "declines a whole quote. A quote spanning several principals "
                 "counts once in each of their rates, so the slices sum to "
                 "more than the total."),
    }


# ── the price question ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class PriceComparison:
    """One product at one quantity band: what wins, and what lost.

    ``gap_pct`` is the lost median against the won median — positive means the
    losses were quoted *above* where this item wins, which is the finding the
    owner is asking for. It is a ratio like every other proportion in this
    codebase, and becomes a percentage only at the presentation edge.
    """

    product_id: str
    product_label: str
    quantity_band: str
    won_observations: int
    lost_observations: int
    won_median: Decimal
    lost_median: Decimal

    @property
    def gap_pct(self) -> Optional[float]:
        if self.won_median <= _ZERO:
            return None
        return float((self.lost_median - self.won_median) / self.won_median)

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_label": self.product_label,
            "quantity_band": self.quantity_band,
            "won_observations": self.won_observations,
            "lost_observations": self.lost_observations,
            "won_median_price": float(self.won_median),
            "lost_median_price": float(self.lost_median),
            "gap_pct": self.gap_pct,
        }


def comparisons(lines: Iterable[PricedLine]) -> list[PriceComparison]:
    """Every product-and-band where both sides clear the floor.

    Medians rather than means throughout: one line quoted at ten times the
    normal price because somebody typed an extra zero, or one deliberate
    loss-leader, would otherwise redefine what the item "wins at" — and the
    whole comparison is then driven by the row that least deserves to drive it.
    """
    grouped: dict[tuple[str, str], list[PricedLine]] = {}
    labels: dict[str, str] = {}
    for line in lines:
        if not line.product_id or line.unit_price is None:
            continue
        grouped.setdefault((line.product_id, line.quantity_band), []).append(line)
        labels[line.product_id] = line.product_label

    out: list[PriceComparison] = []
    for (product_id, band), group in grouped.items():
        won = [ln.unit_price for ln in group if ln.won]
        lost = [ln.unit_price for ln in group if not ln.won]
        if len(won) < MIN_PRICE_OBSERVATIONS or len(lost) < MIN_PRICE_OBSERVATIONS:
            continue
        out.append(PriceComparison(
            product_id=product_id, product_label=labels.get(product_id, product_id),
            quantity_band=band,
            won_observations=len(won), lost_observations=len(lost),
            won_median=median_decimal(won), lost_median=median_decimal(lost)))
    out.sort(key=lambda c: (c.gap_pct or 0.0), reverse=True)
    return out


def versus_own_history(lines: Iterable[PricedLine]) -> Optional[dict]:
    """How far a lost quote sat from what that customer has actually paid.

    A different question from the one ``comparisons`` answers, and worth both:
    the winning price says where the market is, this says whether we moved
    against *this* relationship. ``None`` when too few lost lines have a price
    history behind them, rather than a figure computed from one.
    """
    observed = [
        float((ln.unit_price - ln.customer_paid) / ln.customer_paid)
        for ln in lines
        if not ln.won and ln.customer_paid is not None and ln.customer_paid > _ZERO
    ]
    if len(observed) < MIN_PRICE_OBSERVATIONS:
        return None
    return {
        "observations": len(observed),
        "median_gap_pct": round(statistics.median(observed), 4),
    }


def margin_of(quotes: Iterable[DecidedQuote]) -> Optional[float]:
    """Σ gross profit ÷ Σ revenue over the fully-costed quotes.

    Never the mean of per-quote margins: a one-line quote for a screwdriver
    would otherwise weigh the same as a forty-line tender, which is how an
    aggregate margin ends up describing a book nobody trades.
    """
    costed = [q for q in quotes if q.costed]
    revenue = sum((q.value for q in costed), _ZERO)
    if revenue <= _ZERO:
        return None
    profit = sum((q.gross_profit or _ZERO for q in costed), _ZERO)
    return round(float(profit / revenue), 4)


def pricing(quotes: Iterable[DecidedQuote], lines: Iterable[PricedLine], *,
            as_of: date) -> dict:
    """The RESTRICTED half: margin on what we win, and where the losses sat.

    Everything here compares one customer's price against another's or against
    a margin, which is commercial position rather than a call list — so the
    router serves it to a manager or owner only, exactly as it does
    ``/payables``. Kept as its own function rather than extra keys on ``build``
    so that "who may see this" is a routing decision about a whole response
    instead of a redaction loop somebody has to remember to run.
    """
    rows = list(quotes)
    line_rows = list(lines)
    won_quotes = [q for q in rows if q.won]
    lost_quotes = [q for q in rows if not q.won]

    won_margin = margin_of(won_quotes)
    lost_margin = margin_of(lost_quotes)
    compared = comparisons(line_rows)
    gaps = [c.gap_pct for c in compared if c.gap_pct is not None]

    return {
        "as_of": as_of.isoformat(),
        "won_margin": won_margin,
        "lost_margin": lost_margin,
        # Percentage POINTS, per the house convention: the difference between
        # two ratios is never expressed as a percentage change of a percentage.
        "margin_gap_pp": (round(lost_margin - won_margin, 4)
                          if won_margin is not None and lost_margin is not None
                          else None),
        "costed_quotes": sum(1 for q in rows if q.costed),
        "decided_quotes": len(rows),
        "comparisons": [c.to_dict() for c in compared],
        # The headline the owner asked for, when it can be said at all: the
        # typical distance between a losing price and a winning one, across
        # every product that qualified. Median of the per-product gaps rather
        # than a gap of pooled prices — pooling would compare a book of cheap
        # items against a book of expensive ones.
        "median_gap_pct": (round(statistics.median(gaps), 4) if gaps else None),
        "compared_products": len(compared),
        "versus_own_history": versus_own_history(line_rows),
        "min_price_observations": MIN_PRICE_OBSERVATIONS,
        "priced_lines": len(line_rows),
        "note": ("Compared within one product and one quantity band only. The "
                 "same item at 10 pieces and at 1,000 is two prices for good "
                 "reasons, and pooling them would manufacture a gap out of the "
                 "mix. A product with fewer than "
                 f"{MIN_PRICE_OBSERVATIONS} won or {MIN_PRICE_OBSERVATIONS} "
                 "lost observations is absent rather than estimated."),
    }
