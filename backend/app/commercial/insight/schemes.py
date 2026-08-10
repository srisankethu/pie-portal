"""What a principal's scheme pays, and whether this period is going to earn it.

A target says "buy fifty lakh this quarter". It is only half of what the
principal actually put in writing. The other half is the scheme: *and if you do,
you earn two and a half percent* — two to three points on this book, which is
the difference between a good year and an ordinary one, and at the bottom of the
range it is the distributorship itself. Today the owner learns how he did at
year end, from the principal, which is the one moment at which nothing can be
done about it.

``dependency.progress_of`` already answers "where does the number stand" — spend
against target, achievement against pace, and the run rate that would close the
gap. Nothing here restates any of that. This module answers the three questions
it deliberately does not:

    "What does hitting it pay?"      -> ``outlook(...)["secured"]`` / ``["next_slab"]``
    "Am I going to get there?"       -> ``outlook(...)["projection"]``
    "What does getting there cost?"  -> ``outlook(...)["marginal"]``

**Slabs are the shape, not an extension of it.** The common scheme here is not
one number: it is 2% at forty lakh and 3% at sixty. A model that held a single
rate and a single threshold would express the rarer case and force the ordinary
one to be entered wrong, so a scheme *is* an ordered set of slabs and a flat
percentage is the one-slab case — the same table, the same arithmetic, no second
code path that could disagree with the first. The screen offers "flat" as a
quick fill; the storage does not know the difference.

**The rate is paid on what was bought, and the slab is only the gate.** A book
that clears the forty-lakh slab at forty-two lakh earns 2% of forty-two, not of
forty. That is how these schemes settle, and it is why ``rebate_on`` takes an
amount rather than reading the threshold.

**What is at stake is an uplift, not a total.** With 2% already secured, moving
to the 3% slab is worth the *difference* between the two — anything else
double-counts money the book has already earned and turns an incentive into a
headline. Where no slab is cleared yet the uplift is the whole rebate, which is
exactly the sentence in the brief: forty-two lakh against fifty, eight short,
₹1.25 lakh at stake.

**What the increment costs is a different question from what the rung pays, and
it is the one that decides anything.** ``at_stake`` says ₹1.25 lakh is on the
table. It does not say whether chasing it is sensible, because the same ₹1.25
lakh is worth 12.5% off ₹10 lakh of buying and 125% off ₹1 lakh of it. Since the
rate is paid on the whole amount, the rung's bonus arrives as a **lump**, so its
value per rupee is set by how far there still is to go — and below a gap of
``uplift`` the increment costs *less than nothing*: ₹1 lakh of stock against
₹1.25 lakh of rebate is a negative acquisition cost. That is the number an owner
needs in week ten and it appeared on no screen, which is why ``marginal`` exists.

**The increment is measured from the projected close, never from what has been
bought.** A book already running past the rung will clear it without doing
anything, and pricing that as an opportunity would sell somebody stock they were
going to buy anyway. So the discretionary gap is ``threshold − projected_close``,
which makes ``marginal`` inherit the projection's evidence floors exactly: below
them there is no projection, so there is no marginal number either, and the
reason travels rather than a zero. A confident cost-of-increment computed off
three days of buying is the same defect as a confident close computed off it.

**None of this may reach a line's margin, and that is a decision rather than an
omission.** A rebate is period-level, principal-level and contingent on a total;
putting it on a line needs an allocation, every allocation is a choice, and once
it is in the line it moves a negotiation floor on the strength of an accrual
nobody has earned yet. The figures here stay denominated in purchase spend and
stay beside the principal. If a rebate is ever genuinely *accrued*, it belongs in
cost at the point the accrual is booked — through ``CostRecord``, where the rest
of cost lives — and never as an allocation inside ``economics.py``.

**Nothing here extrapolates from a week.** A run-rate projection is a claim
about the rest of the period, and a quarter three days old cannot support one —
neither can a quarter three weeks old whose only evidence is a single bill. Below
either floor the projection is **absent with a named reason** rather than
present and confident, the same refusal ``payments.lag`` makes and for the same
reason: a thin-evidence number that reads like a firm one is worse than a gap,
because somebody plans against it.

**A marginal scheme is not modelled, and that is a decision.** Some principals
pay the higher rate only on the excess above a slab rather than on the whole
purchase. None of the four here do, and an unused mode on a rebate calculation
is a second arithmetic nobody can check against a statement. When one appears it
becomes a column on the scheme with both readings named; it does not become a
flag somebody has to remember to set.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
Money is ``Decimal`` throughout — a rebate is a rate times a large number and a
float would settle a paise or two away from the principal's own statement, which
is the one document this arithmetic has to agree with. Rates are ratios
(``0.025``), never percentages, matching every other rate in this package. The
figures are denominated in purchase spend, so the router — not this module —
decides who may read them.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from datetime import date
from typing import Iterable, Optional

from . import absence
from .dependency import Target

_ZERO = Decimal("0")
_PAISE = Decimal("0.01")
#: Six places, matching the column. A scheme rate is quantised on the way in so
#: what is computed from is exactly what was stored — a rate that rounded at
#: read time would make two runs of the same quarter disagree by rupees.
_RATE_DP = Decimal("0.000001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_PAISE, rounding=ROUND_HALF_UP)


# ── the scheme ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Slab:
    """One rung: buy this much, earn this rate.

    ``threshold`` is the purchase (or sell-through, per the target's basis) that
    unlocks the rung. ``rate`` is a ratio — ``0.025`` is two and a half percent —
    and it is paid on the whole amount rather than on the excess. See the module
    docstring for why the marginal reading is not modelled.
    """

    threshold: Decimal
    rate: Decimal

    def rebate_on(self, amount: Decimal) -> Decimal:
        return _money(self.rate * amount)

    def to_dict(self) -> dict:
        return {"threshold": float(self.threshold), "rate": float(self.rate)}


@dataclass(frozen=True)
class Scheme:
    """A principal's rebate, as the rungs of it. Ascending by threshold.

    A flat percentage is a scheme with one slab. There is no ``kind`` column and
    no branch on one: the day a "flat" scheme and a "slab" scheme are two code
    paths is the day a flat one starts paying a different number from the
    single-slab scheme that says the same thing.
    """

    slabs: tuple[Slab, ...]

    @property
    def flat(self) -> bool:
        """Whether this reads as "hit the number, earn the rate" on a screen.

        Presentation only — nothing computes differently. It exists so the
        editor can show one rate where there is one rung instead of a slab table
        with a single row in it.
        """
        return len(self.slabs) == 1

    def cleared(self, amount: Decimal) -> Optional[Slab]:
        """The highest rung this amount has reached, or ``None`` below the first."""
        reached = [s for s in self.slabs if amount >= s.threshold]
        return reached[-1] if reached else None

    def next_above(self, amount: Decimal) -> Optional[Slab]:
        """The next rung up, or ``None`` once the top one is cleared."""
        above = [s for s in self.slabs if amount < s.threshold]
        return above[0] if above else None

    def to_dict(self) -> dict:
        return {"slabs": [s.to_dict() for s in self.slabs], "flat": self.flat}


class InvalidScheme(ValueError):
    """A scheme that cannot mean a rebate."""


#: More rungs than any real scheme has. A guard against a paste, not a rule —
#: the four principals here run two or three.
MAX_SLABS = 12

#: A quarter of purchases is not a rebate, it is somebody who typed ``2.5``
#: where the field wanted ``0.025``. Rejected rather than stored, because the
#: number it produces is large, plausible in shape, and wrong by a hundred.
MAX_RATE = Decimal("0.25")


def validate(rungs: Iterable[tuple[Decimal, Decimal]]) -> Scheme:
    """The one place a scheme is checked, so the API and any future importer
    cannot disagree about what is storable.

    Rates must **rise** with the threshold. A scheme that paid less for buying
    more is a transcription error every time — and left in, it would make the
    uplift to the next rung negative, which reads on a screen as an incentive to
    stop buying.
    """
    slabs: list[Slab] = []
    for threshold, rate in rungs:
        threshold = Decimal(threshold)
        rate = Decimal(rate).quantize(_RATE_DP, rounding=ROUND_HALF_UP)
        if threshold < _ZERO:
            raise InvalidScheme("a slab cannot start below zero")
        if rate <= _ZERO:
            raise InvalidScheme("a slab that pays nothing is not a slab — remove "
                                "it rather than storing a zero rate")
        if rate > MAX_RATE:
            raise InvalidScheme(
                f"a rebate of {rate} is not a rate, it is a percentage typed "
                f"into a ratio field — {MAX_RATE} is the ceiling")
        slabs.append(Slab(threshold=threshold, rate=rate))

    if not slabs:
        raise InvalidScheme("a scheme needs at least one slab")
    if len(slabs) > MAX_SLABS:
        raise InvalidScheme(f"{len(slabs)} slabs is not a scheme — {MAX_SLABS} "
                            f"is the ceiling")

    slabs.sort(key=lambda s: s.threshold)
    for lower, upper in zip(slabs, slabs[1:]):
        if lower.threshold == upper.threshold:
            raise InvalidScheme("two slabs start at the same amount, so which "
                                "rate applies is unanswerable")
        if upper.rate <= lower.rate:
            raise InvalidScheme("a higher slab must pay a higher rate — as "
                                "written, buying more earns less")
    return Scheme(slabs=tuple(slabs))


# ── the evidence floor ──────────────────────────────────────────────────────

#: A fortnight. Purchasing is lumpy — one bill can be a week's worth — so a run
#: rate over less than this is a projection of whichever way the first order
#: happened to fall.
MIN_ELAPSED_DAYS = 14

#: Three bills, the same floor ``payments.MIN_SETTLEMENTS`` applies for the same
#: reason: below it, "how this book buys from them" is one transaction wearing a
#: suit. A fortnight with a single large bill in it is exactly the case a days
#: floor alone would wave through.
MIN_DOCUMENTS = 3

TOO_EARLY = "TOO_EARLY"
TOO_FEW_DOCUMENTS = "TOO_FEW_DOCUMENTS"

#: Why no projection was made, in the words a reader needs. A refusal with no
#: reason attached is indistinguishable from a bug, and this one is a decision.
REFUSALS: dict[str, dict[str, str]] = {
    TOO_EARLY: {
        # Both refusals here clear themselves as the period runs. Nobody should
        # be asked to act on them, which is the whole reason TRANSIENT is a
        # separate kind from COLLECTABLE.
        "kind": absence.TRANSIENT,
        "label": "Too early to project",
        "why": (f"Less than {MIN_ELAPSED_DAYS} days of the period have gone. A "
                f"run rate over a few days projects whichever way the first "
                f"order happened to fall, so no close is estimated — what has "
                f"been bought so far is still exact."),
    },
    TOO_FEW_DOCUMENTS: {
        "kind": absence.TRANSIENT,
        "label": "Not enough purchases yet",
        "why": (f"Fewer than {MIN_DOCUMENTS} bills in the period. One large "
                f"order is not a rate, and projecting the quarter from it would "
                f"put a confident number on a single transaction."),
    },
}


#: Why no marginal cost was computed. Distinct from ``REFUSALS`` above because
#: these are not evidence failures — ``LANDS_ANYWAY`` in particular is a *good*
#: answer, and folding it in with "too early to project" would make a book that
#: is comfortably clearing its top rung read as one the platform cannot see.
NO_SCHEME = "NO_SCHEME"
NO_PROJECTION = "NO_PROJECTION"
LANDS_ANYWAY = "LANDS_ANYWAY"

MARGINAL_REFUSALS: dict[str, dict[str, str]] = {
    NO_SCHEME: {
        "label": "No scheme on record",
        "why": ("Nobody has said what this principal pays, so there is no rung "
                "to buy towards and no cost to put on reaching one. That is a "
                "gap in what has been typed in, not a principal who pays "
                "nothing."),
    },
    NO_PROJECTION: {
        "label": "Not enough of the period to price the next rung",
        "why": ("What the next rung costs depends on how much still has to be "
                "bought above where the period is heading, and the period is "
                "not far enough along to say where that is. The rung and what "
                "it pays are still exact."),
    },
    LANDS_ANYWAY: {
        "label": "On course to clear it without buying more",
        "why": ("At the rate this book is already buying, the period closes "
                "past every rung of this scheme. There is nothing discretionary "
                "left to price — buying earlier would only carry the stock "
                "sooner."),
    },
}


def refusal(elapsed_days: int, documents: int) -> Optional[str]:
    """Which floor stops a projection, or ``None`` when both are cleared.

    Days first: a period that has barely started cannot be rescued by the number
    of bills in it, and it is the reason a reader will understand without
    checking anything.
    """
    if elapsed_days < MIN_ELAPSED_DAYS:
        return TOO_EARLY
    if documents < MIN_DOCUMENTS:
        return TOO_FEW_DOCUMENTS
    return None


# ── the forecast ────────────────────────────────────────────────────────────

def outlook(target: Target, scheme: Optional[Scheme], *, actual: Decimal,
            documents: int, as_of: date) -> dict:
    """What this principal's period is worth, and where it is heading.

    Deliberately **not** a superset of ``dependency.progress_of``. Target,
    actual, gap, pace and the run rate that closes it are computed there and
    travel beside this; restating them here would be two answers to one question
    with nothing to say which is current. What this adds is the money — the
    scheme, what is secured, what is at stake — and the projection, which
    ``progress_of`` states in as many words that it does not make.

    ``actual`` is whichever figure the target's basis calls for: purchases from
    this principal on a ``PURCHASE`` target, sales of their product on a
    ``SALES`` one. The caller picks it, exactly as it does for ``progress_of``,
    because the two figures come from different tables and this module should
    not be able to reach for the wrong one.

    ``documents`` is how many bills (or invoices) that figure was summed from.
    It is evidence, not money: it decides whether a projection is made at all.
    """
    # ``dependency.Target`` predates Decimal money in this package and carries a
    # float. Converted once, here, so everything downstream of this line is
    # exact — via ``str`` because ``Decimal(0.1)`` is not one tenth.
    amount = Decimal(str(target.amount))
    elapsed = target.elapsed(as_of)

    secured = scheme.cleared(actual) if scheme else None
    ahead = scheme.next_above(actual) if scheme else None
    secured_rebate = secured.rebate_on(actual) if secured else _ZERO
    # The next rung is worth what it pays *at its own threshold* — the least
    # that has to be bought to earn it. Computing it on today's amount would
    # report a rebate for a slab this book has not reached.
    ahead_rebate = ahead.rebate_on(ahead.threshold) if ahead else None

    stalled = refusal(elapsed, documents)
    projection = (None if (stalled or elapsed <= 0)
                  else _projection(scheme, actual, amount, elapsed, target.days))
    marginal, unpriced = _marginal(scheme, projection)

    # The headline number, and the one an owner acts on. ``None`` — not zero —
    # where no scheme is on record: nothing is at stake because nobody has said
    # what the rebate is, which is a different fact from a rebate of nothing.
    if scheme is None:
        at_stake = None
    elif ahead is None:
        at_stake = 0.0          # the top rung is already cleared
    else:
        at_stake = float(_money(ahead_rebate - secured_rebate))

    return {
        "scheme": scheme.to_dict() if scheme else None,
        "secured": None if secured is None else {
            **secured.to_dict(), "rebate": float(secured_rebate),
        },
        "next_slab": None if ahead is None else {
            **ahead.to_dict(),
            "rebate": float(ahead_rebate),
            "gap": float(_money(ahead.threshold - actual)),
            # What moving up is actually worth. See the module docstring: a
            # total here would count money the book has already earned.
            "uplift": float(_money(ahead_rebate - secured_rebate)),
        },
        "at_stake": at_stake,
        "projection": projection,
        # What the next rung costs per rupee above the projected close, and — in
        # the same shape as ``projection``/``absent`` — the named reason when
        # there is no such number. ``LANDS_ANYWAY`` is not a failure; see
        # ``MARGINAL_REFUSALS``.
        "marginal": marginal,
        "marginal_absent": (None if unpriced is None
                            else {"reason": unpriced,
                                  **MARGINAL_REFUSALS[unpriced]}),
        "absent": None if stalled is None else {"reason": stalled,
                                                **REFUSALS[stalled]},
        "evidence": {
            "elapsed_days": elapsed,
            "documents": documents,
            "min_elapsed_days": MIN_ELAPSED_DAYS,
            "min_documents": MIN_DOCUMENTS,
        },
    }


def _marginal(scheme: Optional[Scheme],
              projection: Optional[dict]) -> tuple[Optional[dict], Optional[str]]:
    """What the next rung costs per rupee, measured above the projected close.

    Returns the figures and ``None``, or ``None`` and the reason there are none —
    the same shape ``outlook`` uses for ``projection``/``absent``, so a caller
    reads one pattern rather than two.

    The rung priced here is the next one above the **projected close**, not above
    what has been bought. Those are different rungs whenever the book is running
    ahead, and the one above today's actual is the wrong answer: it is often
    already paid for by the rest of the period. Where the projection clears every
    rung there is nothing discretionary and that is reported as its own state.

    Both figures come out of the same subtraction:

        gain = (what the rung pays at its own threshold)
             − (what the projected close earns without it)

    ``gain`` is always positive, which is a property of ``validate`` rather than
    an assumption — rungs ascend in both threshold and rate, so a higher rung on
    a larger amount cannot pay less. ``gap`` is strictly positive for the same
    structural reason: ``next_above`` is a strict comparison, so the projected
    close sits below the rung it returns and the division is safe.
    """
    if scheme is None:
        return None, NO_SCHEME
    if projection is None:
        return None, NO_PROJECTION

    close = Decimal(str(projection["projected_close"]))
    rung = scheme.next_above(close)
    if rung is None:
        return None, LANDS_ANYWAY

    gap = rung.threshold - close
    landed = scheme.cleared(close)
    gain = rung.rebate_on(rung.threshold) - (landed.rebate_on(close)
                                             if landed else _ZERO)

    # Quantised at the same six places a rate is stored, so what a screen shows
    # is what the arithmetic did rather than a wider float rounded on the way
    # out — the effective cost is a rate like any other in this package.
    earned = (gain / gap).quantize(_RATE_DP, rounding=ROUND_HALF_UP)
    cost = Decimal("1") - earned
    return {
        **rung.to_dict(),
        # What must be bought *above where the period is already heading*. Not
        # ``next_slab.gap``, which is measured from today and is the larger,
        # more alarming and less actionable of the two.
        "gap": float(_money(gap)),
        "gain": float(_money(gain)),
        "earned_per_rupee": float(earned),
        # 1.00 is full price, 0.75 is a quarter off, below zero is paid to take
        # the stock. Named for what it is rather than "discount", which reads as
        # something the principal offered on the invoice.
        "effective_cost": float(cost),
        # The whole point of the card, as a boolean the screen can lead with:
        # the gap has fallen below what the rung pays.
        "free": cost <= _ZERO,
    }, None


def _projection(scheme: Optional[Scheme], actual: Decimal, amount: Decimal,
                elapsed: int, period_days: int) -> dict:
    """Where the period closes if the book keeps buying at the rate it has.

    Stated as what it is — a straight-line extension of the evidence, above the
    floor that makes one defensible — and never as what will happen. A principal
    who ships in month three is behind all quarter and lands every time; the
    projection says so by being a projection, and the screen puts it beside the
    exact figure rather than instead of it.
    """
    run_rate = actual / Decimal(elapsed)
    close = _money(run_rate * Decimal(period_days))
    landing = scheme.cleared(close) if scheme else None
    return {
        "run_rate_per_day": float(_money(run_rate)),
        "projected_close": float(close),
        "clears_target": close >= amount,
        "shortfall": float(_money(amount - close)) if close < amount else 0.0,
        "slab": None if landing is None else landing.to_dict(),
        "rebate": None if landing is None else float(landing.rebate_on(close)),
    }
