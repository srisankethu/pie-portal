"""What share of a customer's spend comes here — and what that claim rests on.

``dependency.py`` states the problem this module answers to, and states it
correctly: the platform sees what a customer buys *here* and has no sight of
what they buy elsewhere, so "they are 8% of our revenue" is a fact and "we are
8% of their purchasing" is a guess. Nothing below overturns that. What it does
is separate the cases where the guess has *observable evidence* behind it from
the cases where it does not, and refuse the second kind out loud.

**A ladder of bases, not one number.** Each customer lands on the strongest rung
their evidence supports, and the rung travels with the answer, because "we hold
about a third, measured against published tender quantities" and "we hold about
a third, because somebody said so on a visit" are not the same sentence and a
screen that renders both as ``34%`` has destroyed the difference.

``MEASURED_TENDER``   Σ won ÷ Σ tendered over published tenders. Two documented
                      facts, so this is arithmetic rather than inference. It is
                      also *scoped*: it measures share of what was tendered, and
                      says nothing about the same customer's off-tender buying.
``BOUNDED_ASKS``      An **upper** bound. Every quote we are recorded as having
                      lost to a competitor is spend we watched go elsewhere, so
                      their tooling spend is *at least* our revenue plus those
                      losses, and our share is therefore *at most* the ratio of
                      the two. The band runs from that ceiling down to zero,
                      because nothing here establishes a floor.
``DECLARED``          Somebody's stated view, with their name and the date on
                      it, widened into a band and expiring.
``UNKNOWN``           No basis. The answer is a refusal naming what would change
                      it, which is the honest output and the common one.

**The bound is only as tight as the quoting is disciplined, and that failure
runs the dangerous way.** A customer whose orders mostly arrive without a
recorded quote has few recorded losses, so the ceiling sits near 100% and they
read as an account we own. The missing record *flatters* the answer. That is why
quote coverage is computed, returned beside every bound, and made a condition of
producing one at all: below ``wallet_min_quote_coverage`` this module refuses
rather than reporting a ceiling it knows is loose.

**No midpoint, anywhere.** The band's width is the honesty of the estimate, and
a ``(low + high) / 2`` field would be consumed as a measurement within a week —
by a chart axis, by a sort, by an export. There is no such field on the
dataclass and none in the dict it emits. A caller that wants one number has to
write the arithmetic itself, which is the point at which somebody asks whether
it is a good idea.

**A lost quote of unknown kind is excluded, not assumed.** ``QuoteLossReason``
is three-valued about whether the money went anywhere — a competitor took it,
the requirement died, or the record does not say — and the third case is dropped
from both sides rather than folded into either. Folding it into "nobody bought
it" would shrink the denominator and overstate our share, which is again the
flattering direction.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
Revenue and quoted values only — no cost and no margin are read here, so the
whole view is visible to a salesperson.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from ..config import CommercialThresholds
from . import absence

#: Which rung the answer stands on, strongest first. The order is the ranking:
#: a customer with both a tender history and recorded losses is reported on the
#: measured rung, because a measurement beats a bound.
MEASURED_TENDER = "MEASURED_TENDER"
BOUNDED_ASKS = "BOUNDED_ASKS"
DECLARED = "DECLARED"
UNKNOWN = "UNKNOWN"

LADDER: tuple[str, ...] = (MEASURED_TENDER, BOUNDED_ASKS, DECLARED, UNKNOWN)

BASIS_MEANING: dict[str, str] = {
    MEASURED_TENDER:
        "Measured against published tender quantities: what we won, over what "
        "was tendered. Both figures come from documents that can be looked up. "
        "It covers only what this customer buys through tenders — their "
        "off-tender buying is not in it, and may be much larger.",
    BOUNDED_ASKS:
        "An upper bound, not an estimate. Every enquiry we are recorded as "
        "having lost to another supplier is spend we watched go elsewhere, so "
        "their spend is at least our revenue plus those losses — which makes "
        "our share at most the figure shown. It cannot say how much lower the "
        "true share is, because enquiries that never reached us are invisible.",
    DECLARED:
        "Somebody's stated view, shown as a band because that is all a stated "
        "view supports. It carries who said it and when, and it expires.",
    UNKNOWN:
        "No basis on record. This is the honest answer for most customers: the "
        "platform sees what they buy here and nothing of what they buy "
        "elsewhere, and a number invented to fill the gap would be worse than "
        "the gap.",
}

_ZERO = Decimal("0")


# ── inputs ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LostAsk:
    """One quote this customer did not give us, and where it went.

    ``went_elsewhere`` is ``QuoteLossReason``'s own three-valued answer, passed
    through rather than re-derived: ``True`` a competitor supplied it, ``False``
    the requirement died, ``None`` the record does not say. The caller maps a
    ``QuoteOutcome`` row into this; the classification stays in the enum so
    there is one definition of what counts as a competitor's rupee.
    """

    quote_id: str
    value: Decimal
    decided_on: Optional[date]
    went_elsewhere: Optional[bool]


@dataclass(frozen=True)
class TenderObservation:
    """One published tender, and what we took of it."""

    tender_ref: str
    tendered: Decimal
    #: ``None`` while the award is unknown. Excluded from both sides rather
    #: than counted as a loss — an open bid treated as lost understates every
    #: share by however many are still out.
    won: Optional[Decimal]
    closed_on: date
    source: str


@dataclass(frozen=True)
class Declaration:
    """Somebody's stated view of the share, with their name and the date."""

    share: float
    declared_by: str
    declared_on: date


# ── the answer ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class WalletEstimate:
    """A share as a band, the basis it rests on, and what it does not cover.

    There is deliberately no midpoint and no single ``share``. See the module
    docstring: the band's width is the estimate's honesty, and a midpoint field
    is consumed as a measurement.
    """

    customer_id: str
    basis: str
    #: ``None`` on the UNKNOWN rung. Both ends, or neither — a band with one
    #: end filled in is a number wearing a range's clothes.
    share_low: Optional[float]
    share_high: Optional[float]
    #: What the denominator was built from, in words a person can check.
    denominator_basis: str
    #: What this figure does not cover. Never empty on a rung that has limits,
    #: which is every rung except UNKNOWN.
    caveats: list[str]
    #: Documents and records behind it — tender refs, quote ids, a declarer.
    evidence_refs: list[str]
    #: Why there is no answer, when there is none, naming what would produce
    #: one. A refusal that does not say what is missing teaches nobody.
    refusal: Optional[str]
    as_of: date
    expires_on: Optional[date]
    #: Share of this customer's revenue that came through a recorded quote.
    #: Returned on every rung, because it is what says how much the bound can
    #: be trusted — and it is the number that reveals the flattering failure.
    quote_coverage: Optional[float]
    thresholds_version: str

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id,
            "basis": self.basis,
            "basis_meaning": BASIS_MEANING[self.basis],
            "share_low": self.share_low,
            "share_high": self.share_high,
            "denominator_basis": self.denominator_basis,
            "caveats": self.caveats,
            "evidence_refs": self.evidence_refs,
            "refusal": self.refusal,
            "as_of": self.as_of.isoformat(),
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "quote_coverage": self.quote_coverage,
            "thresholds_version": self.thresholds_version,
        }


def _ratio(part: Decimal, whole: Decimal) -> Optional[float]:
    """A share, or None where the denominator cannot carry one.

    Guards the denominator rather than the objection — a zero denominator here
    means there is nothing to be a share *of*, and returning 0.0 would say "we
    hold none of it", which is a claim about a customer nobody measured.
    """
    if whole <= _ZERO:
        return None
    return round(float(part / whole), 4)


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _add_months(day: date, months: int) -> date:
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    # Clamped to the 28th rather than doing calendar arithmetic: an expiry is a
    # policy horizon, not an anniversary, and month-end precision here would be
    # precision nobody asked for.
    return date(year, month + 1, min(day.day, 28))


def quote_coverage(revenue: Decimal, quoted_revenue: Decimal) -> Optional[float]:
    """Share of this customer's revenue that came through a recorded quote.

    The honesty check on every bound below. A customer who buys steadily on
    repeat orders that never pass through the quote screen has almost no
    recorded losses, so a bound computed for them sits near 100% and reads as
    an account we own — when in truth nobody wrote down what we lost.
    """
    return _ratio(quoted_revenue, revenue)


def estimate(customer_id: str, as_of: date, *,
             thresholds: CommercialThresholds,
             revenue: Decimal,
             quoted_revenue: Decimal = _ZERO,
             lost_asks: Iterable[LostAsk] = (),
             tenders: Iterable[TenderObservation] = (),
             declaration: Optional[Declaration] = None) -> WalletEstimate:
    """The strongest honest statement available about this customer's share.

    Rungs are tried strongest first and the first one that holds is returned —
    a measurement is never demoted to a bound because a bound also exists.
    """
    coverage = quote_coverage(revenue, quoted_revenue)
    common = {
        "customer_id": customer_id,
        "as_of": as_of,
        "quote_coverage": coverage,
        "thresholds_version": thresholds.version,
    }

    measured = _from_tenders(list(tenders), **common)
    if measured is not None:
        return measured

    bounded = _from_asks(list(lost_asks), revenue, coverage,
                         thresholds=thresholds, **common)
    if bounded is not None:
        return bounded

    stated = _from_declaration(declaration, thresholds=thresholds, **common)
    if stated is not None:
        return stated

    return WalletEstimate(
        basis=UNKNOWN, share_low=None, share_high=None,
        denominator_basis="Nothing on record says what this customer spends "
                          "on tooling in total.",
        caveats=[],
        evidence_refs=[],
        refusal=_refusal(list(lost_asks), list(tenders), declaration, coverage,
                         thresholds),
        expires_on=None,
        **common)


# ── the rungs ────────────────────────────────────────────────────────────────
def _from_tenders(observations: list[TenderObservation], *, customer_id: str,
                  as_of: date, quote_coverage: Optional[float],
                  thresholds_version: str) -> Optional[WalletEstimate]:
    """Σ won ÷ Σ tendered, over tenders whose award is known.

    Aggregated as a ratio of sums, never the mean of per-tender shares — the
    same rule the margin arithmetic follows, and for the same reason: a ₹2L
    tender won outright and a ₹80L tender lost do not average to 50%.
    """
    decided = [t for t in observations if t.won is not None]
    if not decided:
        return None
    tendered = sum((t.tendered for t in decided), _ZERO)
    won = sum((t.won or _ZERO for t in decided), _ZERO)
    share = _ratio(won, tendered)
    if share is None:
        return None

    open_bids = len(observations) - len(decided)
    caveats = [
        "Covers only what this customer buys through tenders. Their "
        "off-tender buying — spares, consumables, repeat orders — is not in "
        "this figure and may be much larger.",
    ]
    if open_bids:
        caveats.append(
            f"{open_bids} bid(s) are still open and are excluded from both "
            "sides. Counting an undecided bid as lost would understate this "
            "share.")

    # A measurement, so both ends of the band are the same number. The band
    # shape is kept rather than collapsing to a scalar so every rung has one
    # shape and a caller never branches on which kind of answer it holds.
    return WalletEstimate(
        basis=MEASURED_TENDER, share_low=share, share_high=share,
        denominator_basis=(
            f"{len(decided)} awarded tender(s), published value "
            f"{tendered:.2f}."),
        caveats=caveats,
        evidence_refs=[t.tender_ref for t in decided],
        refusal=None, expires_on=None,
        customer_id=customer_id, as_of=as_of,
        quote_coverage=quote_coverage, thresholds_version=thresholds_version)


def _from_asks(asks: list[LostAsk], revenue: Decimal,
               coverage: Optional[float], *, thresholds: CommercialThresholds,
               customer_id: str, as_of: date, quote_coverage: Optional[float],
               thresholds_version: str) -> Optional[WalletEstimate]:
    """An upper bound: share ≤ revenue ÷ (revenue + what went to competitors).

    Three conditions, and each one is a refusal rather than a fudge. Enough
    recorded losses to be more than an anecdote; enough quote coverage that the
    absence of losses means something; and revenue to be a share of.
    """
    if coverage is None or coverage < thresholds.wallet_min_quote_coverage:
        return None
    elsewhere = [a for a in asks if a.went_elsewhere is True]
    if len(elsewhere) < thresholds.wallet_min_lost_quotes:
        return None
    if revenue <= _ZERO:
        return None

    lost_value = sum((a.value for a in elsewhere), _ZERO)
    ceiling = _ratio(revenue, revenue + lost_value)
    if ceiling is None:
        return None

    unknown_kind = sum(1 for a in asks if a.went_elsewhere is None)
    caveats = [
        "An upper bound. Enquiries this customer never sent us are invisible, "
        "so their real spend can only be larger and the real share only "
        "smaller.",
    ]
    if unknown_kind:
        caveats.append(
            f"{unknown_kind} lost quote(s) do not say whether another supplier "
            "took them, and are excluded from both sides rather than assumed "
            "either way.")
    if coverage < 1.0:
        caveats.append(
            f"{coverage:.0%} of this customer's revenue came through a "
            "recorded quote. The rest was never quoted here, so any loss "
            "inside it was never written down — which makes this ceiling "
            "looser than it looks, in the direction that flatters us.")

    return WalletEstimate(
        basis=BOUNDED_ASKS,
        # Zero, not a fitted lower end. Nothing in the evidence establishes a
        # floor, and inventing one would turn a bound into an estimate.
        share_low=0.0, share_high=ceiling,
        denominator_basis=(
            f"Our revenue plus {len(elsewhere)} quote(s) recorded as lost to "
            f"another supplier, worth {lost_value:.2f}."),
        caveats=caveats,
        evidence_refs=[a.quote_id for a in elsewhere],
        refusal=None, expires_on=None,
        customer_id=customer_id, as_of=as_of,
        quote_coverage=quote_coverage, thresholds_version=thresholds_version)


def _from_declaration(declaration: Optional[Declaration], *,
                      thresholds: CommercialThresholds, customer_id: str,
                      as_of: date, quote_coverage: Optional[float],
                      thresholds_version: str) -> Optional[WalletEstimate]:
    """A stated view, widened into a band and given an expiry.

    Widened because a declaration cannot support a decimal point: somebody who
    says "we get about a third of their tooling" is not claiming 33.3%, and
    recording it as though they were is how an offhand answer becomes a figure
    in a review. Expiring because a view of a customer's spend a year ago
    describes a different shop floor.
    """
    if declaration is None:
        return None
    expires = _add_months(declaration.declared_on,
                          thresholds.wallet_declaration_max_age_months)
    if as_of > expires:
        return None

    band = thresholds.wallet_declared_band
    low = round(max(0.0, declaration.share - band), 4)
    high = round(min(1.0, declaration.share + band), 4)
    age = _months_between(declaration.declared_on, as_of)
    return WalletEstimate(
        basis=DECLARED, share_low=low, share_high=high,
        denominator_basis=(
            f"Stated by {declaration.declared_by} on "
            f"{declaration.declared_on.isoformat()}."),
        caveats=[
            "Nobody measured this. It is one person's view, widened to a band "
            "because that is what a view supports, and it expires.",
            f"Stated {age} month(s) ago." if age else "Stated this month.",
        ],
        evidence_refs=[f"declared_by:{declaration.declared_by}"],
        refusal=None, expires_on=expires,
        customer_id=customer_id, as_of=as_of,
        quote_coverage=quote_coverage, thresholds_version=thresholds_version)


# ── the refusal ──────────────────────────────────────────────────────────────
def _refusal(asks: list[LostAsk], tenders: list[TenderObservation],
             declaration: Optional[Declaration], coverage: Optional[float],
             th: CommercialThresholds) -> str:
    """Why there is no answer, and which specific thing would produce one.

    Written per customer rather than as one sentence, because "record the next
    declined enquiry" and "this customer's declaration expired in March" are
    different pieces of work and a screen that says the same thing for both is
    a screen nobody acts on.
    """
    reasons: list[str] = []

    if tenders and not any(t.won is not None for t in tenders):
        reasons.append(
            f"{len(tenders)} tender(s) are recorded but none has an award yet. "
            "Filling in what was awarded turns this into a measured share.")

    elsewhere = sum(1 for a in asks if a.went_elsewhere is True)
    unknown_kind = sum(1 for a in asks if a.went_elsewhere is None)
    if coverage is None:
        reasons.append(
            "No revenue is on record for this customer, so there is nothing "
            "for a share to be a share of.")
    elif coverage < th.wallet_min_quote_coverage:
        reasons.append(
            f"Only {coverage:.0%} of this customer's revenue came through a "
            f"recorded quote, below the {th.wallet_min_quote_coverage:.0%} "
            "this platform needs before a bound means anything — with most "
            "orders unquoted, the losses were never written down, and a "
            "ceiling computed from what little there is would read far too "
            "high. Quoting through the platform is what closes this.")
    elif elsewhere < th.wallet_min_lost_quotes:
        reasons.append(
            f"{elsewhere} quote(s) are recorded as lost to another supplier, "
            f"fewer than the {th.wallet_min_lost_quotes} this platform treats "
            "as more than an anecdote. Recording the next declined enquiry, "
            "with who won it, is what closes this.")

    if unknown_kind:
        reasons.append(
            f"{unknown_kind} lost quote(s) do not say whether somebody else "
            "supplied them. Those cannot be counted either way.")

    if declaration is not None:
        reasons.append(
            f"The declaration by {declaration.declared_by} on "
            f"{declaration.declared_on.isoformat()} is older than "
            f"{th.wallet_declaration_max_age_months} months and has expired. "
            "Asking again on the next visit is what closes this.")
    else:
        reasons.append(
            "Nobody has recorded a view of this customer's total tooling "
            "spend.")

    return " ".join(reasons)


def unavailable() -> list[dict]:
    """What this module will not tell you, said where it would be read.

    Two entries, and they are deliberately different *kinds* of not-knowing —
    the distinction ``absence.py`` exists to draw. Lumping them together would
    be the more damaging error in both directions: it would invite somebody to
    go and "fix" the first, and it would let the second sit unfixed as though
    it were a law of nature.
    """
    return [
        {
            "what": "A single share-of-wallet percentage",
            "why": ("The platform sees what a customer buys here and has no "
                    "sight of what they buy elsewhere. No amount of recording "
                    "inside this book closes that — a competitor's invoices "
                    "are not ours to read. Where evidence does exist a band is "
                    "reported with its basis named; where it does not, the "
                    "answer is UNKNOWN. A midpoint is never offered: the width "
                    "of the band is the point of it."),
            # PERMANENT, not COLLECTABLE: this is the epistemic limit
            # `dependency.py` states, and it is why the ladder exists at all.
            "kind": absence.PERMANENT,
        },
        {
            "what": "A bound for a customer whose orders are not quoted here",
            "why": ("The upper bound is built from enquiries recorded as lost. "
                    "A customer whose orders arrive without a recorded quote "
                    "has few recorded losses, so a bound computed for them "
                    "would sit near 100% and read as an account we own — the "
                    "missing record flatters us. Quoting through the platform, "
                    "and recording who won the ones we lose, is what turns "
                    "this into an answer."),
            # COLLECTABLE, and the difference from the entry above is the whole
            # value of saying so: this one is somebody's afternoon, not a law.
            "kind": absence.COLLECTABLE,
        },
    ]
