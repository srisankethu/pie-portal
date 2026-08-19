"""In what order the book takes its lines, and which gap that makes worth a call.

``mix`` builds the grid and stops there on purpose: it shows BUYS, LAPSED and
NEVER and reports, beside each gap, how many comparable customers take the line.
What it cannot say is *which* of a dozen blanks to ring about first, because
co-occurrence is symmetric in the only number it publishes — lift is literally
the same figure both ways round, so it says a pair is related and never which
way round the relationship runs.

This module adds the one thing that is not symmetric: **the order the base took
them in.** For every ordered pair of lines it counts the customers who hold both
and asks whether the first purchase of B fell after the first purchase of A. If
four in five of the customers who took cutting tools went on to take coolant,
and the ones who took coolant hardly ever came back for cutting tools, then a
cutting-tool customer with no coolant is a different call from a coolant
customer with no cutting tools — and the grid on its own ranks them identically.

**It is a count of rows, not a model.** Every figure here is a tally over
persisted first-trade dates and a median of day differences. There is no
training, no fitted parameter, no score. ``after_share`` is a whole number
divided by a whole number and both are published, so a reader who doubts it can
recount it from the grid on the same screen — which is the property that makes
this quotable in a meeting, and the reason a market-basket library was not the
answer even though it would have been fewer lines.

**A gap is still not money, and this ranks plausibility rather than value.**
``mix``'s central refusal is inherited whole: an empty cell means they do not
buy the line *from us*, and a shop with no measuring room has no metrology gap.
The obvious next feature — multiply the gap by what comparable customers spend
on that line and sort by rupees — is refused here in writing, because it is the
one change that would turn an honest ordering into a forecast. There is no
revenue, opportunity, potential or value field anywhere in this output, and
``test_no_money_field_appears_anywhere_in_the_ranking`` says so structurally
rather than by inspection.

**Order is not cause.** That customers usually took coolant after cutting tools
is a fact about the sequence in our own invoices. It is not evidence that the
first purchase caused the second, and it is emphatically not a prediction that
this customer will take the line next. The word the output uses is
``after_share``; nothing here is called probability, likelihood or propensity.

**Four decisions that could each have gone the easy way.**

1. *Same-day adoptions count in the denominator.* Where both lines first appear
   on one invoice there is no order to observe. Dropping those pairs would raise
   every ``after_share`` by exactly the share of customers who were sold the two
   lines together — which on a distributor's book is not a rounding error, it is
   the house selling a basket. They are counted as "no order observed" and
   published as ``same_day``, so the denominator is the whole dual-holding
   population and the numerator is a subset of it.
2. *The population is everyone who ever held both, not everyone who holds both
   now.* ``mix``'s affinity deliberately counts current holders, because it is
   answering "who takes this line". This is answering "what order were they
   taken in", and a first purchase is a historical fact that a later lapse does
   not unmake. Restricting to live holders would throw away exactly the
   customers whose whole story is a line taken and lost — which the grid calls
   its strongest cell.
3. *The pair statistic is all-history on both sides.* The window in ``mix``
   decides what BUYS means; it does not touch this, because a first purchase
   truncated by a window is not a first purchase. Numerator and denominator are
   drawn from the same set of customers over the same span by construction, so
   there is no way to compare a count over one period against a count over
   another.
4. *LAPSED still outranks NEVER, whatever the sequence says.* A line the
   customer already bought was approved by somebody, went through their goods
   inward, and stopped — a shorter conversation than one that has never
   happened. Sequence evidence orders *within* each of those two groups and is
   never allowed to promote a NEVER above a LAPSED, which would be this module
   quietly overruling the grid's own finding.

**The floor is an evidence floor, and it refuses rather than shrinks.** A pair
seen in three customers is a coincidence with a percentage sign on it.
``crosssell_min_base_customers`` lives in ``CommercialThresholds`` — inside the
version hash, so an ordering computed last quarter still says what rule produced
it — and below it a pair reports no share at all. When *no* pair clears the
floor the answer is an unranked grid with the floor and the largest base it
rejected attached, exactly as ``radar.below_floor`` does. The fix for that
screen is more trade on the book. It is never a lower floor, and the threshold
is deliberately not in ``policy.EDITABLE`` so it cannot be turned into one from
Settings.

**Uncategorised is excluded from both sides of every ratio.** ``mix`` already
declines to make it a column; this declines to let it be an anchor, a target or
a member of any denominator. An item nothing could place is not evidence that a
customer does or does not take a line, and counting it either way would put a
number on the catalogue's gaps rather than on the customer's.

Not to be confused with ``decisions/outcomes.adoption_report``, which measures
whether people act on the queue's recommendations. That is adoption of the
platform; this is adoption of a product line, and the two share nothing but the
word.

Layer rules, inherited: ``commercial/``, deterministic, never imports ``ai/``.
Counts and dates only — no cost, no margin, no revenue — which is why this rides
along on a screen every role can open.
"""
from __future__ import annotations

import math

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Optional

from ..categories import UNCATEGORISED
from ..config import CommercialThresholds
from . import absence, mix

#: What a pair's direction is called when it clears both floors. Kept as words
#: rather than a bare boolean on the wire so a client renders a label it was
#: given instead of inventing one from ``after_share > 0.5``.
FOLLOWS = "FOLLOWS"
#: Measured, and the base does not take the target after the anchor often
#: enough to say the pair has a direction. A finding, not a missing figure.
NO_ORDER = "NO_ORDER"
#: Too few customers hold both lines for any share to be computed.
THIN_BASE = "THIN_BASE"

DIRECTION_MEANING: dict[str, str] = {
    FOLLOWS: ("Customers who took the first line went on to take the second, "
              "in that order, often enough to clear the policy share. It says "
              "what usually happened next, not what will happen."),
    NO_ORDER: ("Enough customers hold both lines to measure, and no consistent "
               "order came out. The two are taken together or in either "
               "order — a measurement, not a gap in the data."),
    THIN_BASE: ("Too few customers hold both lines for a share to mean "
                "anything. Not a weak relationship — an unmeasured one."),
}


@dataclass(frozen=True)
class Pair:
    """One ordered pair of lines, and everything the share was counted from.

    Every count that goes into ``after_share`` travels with it. That is not
    generosity — it is the only way the sentence "four of your five" can be
    checked against the grid printed beside it, and an unauditable share is the
    thing this module exists to avoid producing.
    """

    anchor: str
    target: str
    #: Customers with a first-trade date for both lines. The denominator, and
    #: the number the evidence floor is applied to.
    both: int
    #: Of those, the ones who first took the target *after* the anchor.
    after: int
    #: The ones who took it the other way round.
    before: int
    #: The ones whose first purchase of the two fell on the same day, so no
    #: order was observed. Counted in ``both``; see the module docstring.
    same_day: int
    #: ``after ÷ both``. ``None`` below the floor — never 0.0, which is a
    #: measurement meaning "they never took it in that order".
    after_share: Optional[float]
    #: Median days between the two first purchases, over the ``after`` subset
    #: only. Median rather than mean because one customer who came back after
    #: four years would otherwise set the typical interval for the book.
    median_days: Optional[float]
    #: ``FOLLOWS`` / ``NO_ORDER`` / ``THIN_BASE``.
    direction: str

    @property
    def directional(self) -> bool:
        return self.direction == FOLLOWS

    def to_dict(self, label_of: dict[str, str]) -> dict:
        return {
            "from": self.anchor, "from_label": label_of.get(self.anchor, self.anchor),
            "to": self.target, "to_label": label_of.get(self.target, self.target),
            "both": self.both, "after": self.after, "before": self.before,
            "same_day": self.same_day,
            "after_share": self.after_share,
            "median_days": self.median_days,
            "direction": self.direction,
            "directional": self.directional,
        }


def _known(grid: dict) -> tuple[list[str], dict[str, str]]:
    """The columns worth counting, and what a person calls them.

    ``UNCATEGORISED`` is filtered here as well as in ``mix``, and the belt is
    deliberate: this module is fed a dict, and a dict is exactly the kind of
    input that arrives one day from a caller that built its columns some other
    way.
    """
    columns = [c for c in grid.get("categories") or []
               if c.get("category") and c["category"] != UNCATEGORISED]
    return ([c["category"] for c in columns],
            {c["category"]: c.get("label") or c["category"] for c in columns})


def _first_dates(grid: dict, known: set[str]) -> dict[str, dict[str, date]]:
    """Each customer's first trade in each line, read back off the grid.

    Read from the published grid rather than from the raw sale lines on
    purpose. The alternative — handing this module the ``MixLine`` rows and
    letting it derive its own per-customer facts — is a second implementation of
    the thing ``mix`` exists to compute, and the two would agree until the first
    time one of them changed what counts as a trade. The cost is parsing an ISO
    date back into a date, which is three lines; the benefit is that every
    number this module publishes was counted from figures the reader can see on
    the same screen.
    """
    out: dict[str, dict[str, date]] = {}
    for row in grid.get("customers") or []:
        firsts: dict[str, date] = {}
        for cell in row.get("cells") or []:
            key, raw = cell.get("category"), cell.get("first_traded")
            if key in known and raw:
                firsts[key] = date.fromisoformat(raw)
        if len(firsts) >= 2:
            # A customer with one line has held no pair and belongs in no
            # denominator. Keeping them would not change a count; dropping them
            # lets ``customers_compared`` say what the pairs were counted over.
            out[row["customer_id"]] = firsts
    return out


def _pairs(firsts: dict[str, dict[str, date]],
           th: CommercialThresholds) -> dict[tuple[str, str], Pair]:
    """Every pair some customer actually holds both halves of, counted once.

    One pass over customers, walking only the lines that customer holds, rather
    than a pass over the customer base per pair of columns. The two forms agree
    on every number and not on how long they take. This grid pivots to
    principals as well as to the five lines of the business, and on that pivot
    the columns are every supplier the book has traded — a few hundred — so the
    per-pair form is tens of thousands of full walks of the customer base to
    answer a question about the handful of lines each customer actually holds.

    Pairs nobody holds both of are absent rather than present-and-empty, for the
    same reason: materialising every ordered pair of principals is a dictionary
    the size of the column count squared, to say "nobody" in each one.
    ``_best_anchor`` reads a missing pair as unmeasured, which it is.
    """
    tally: dict[tuple[str, str], list] = {}
    for lines in firsts.values():
        held = sorted(lines.items(), key=lambda kv: (kv[1], kv[0]))
        for i, (anchor, first_anchor) in enumerate(held):
            for target, first_target in held[i + 1:]:
                gap = (first_target - first_anchor).days
                forward = tally.setdefault((anchor, target), [0, 0, 0, []])
                backward = tally.setdefault((target, anchor), [0, 0, 0, []])
                if gap > 0:
                    forward[0] += 1
                    forward[3].append(gap)
                    backward[1] += 1
                else:
                    # Sorted by date, so ``gap`` is never negative here: the
                    # only remaining case is the same-day adoption, which is
                    # "no order observed" on both sides of the pair.
                    forward[2] += 1
                    backward[2] += 1

    out: dict[tuple[str, str], Pair] = {}
    for (anchor, target), (after, before, same, deltas) in tally.items():
        both = after + before + same
        if both < th.crosssell_min_base_customers:
            # Not a weak relationship — an unmeasured one. A 0.0 here would read
            # as "they never take it in that order", which is a finding this
            # book has not made.
            out[(anchor, target)] = Pair(anchor, target, both, after, before,
                                         same, None, None, THIN_BASE)
            continue
        share = round(after / both, 4)
        out[(anchor, target)] = Pair(
            anchor, target, both, after, before, same, share,
            round(float(median(deltas)), 1) if deltas else None,
            FOLLOWS if share >= th.crosssell_min_sequence_share else NO_ORDER)
    return out


def _best_anchor(pairs: dict[tuple[str, str], Pair], held: list[str],
                 target: str, label_of: dict[str, str]) -> Optional[dict]:
    """Which line the customer already holds says the most about this gap.

    Ranked on the direction flag first and the share second, the same shape
    ``mix._gaps`` uses for lift: a pair with an observed direction beats one
    without whatever their shares, and the base size breaks a tie so the better
    evidenced of two equal shares wins. Anchors below the floor are skipped
    rather than ranked last — an unmeasured pair has no place in an ordering.
    """
    best: Optional[Pair] = None
    for anchor in held:
        pair = pairs.get((anchor, target))
        if pair is None or pair.after_share is None:
            continue
        rank = (pair.directional, pair.after_share, pair.both)
        if best is None or rank > (best.directional, best.after_share, best.both):
            best = pair
    return best.to_dict(label_of) if best else None


def _gap_key(gap: dict) -> tuple:
    """The order gaps are read in, most actionable first.

    Lapsed leads, unconditionally — see the module docstring. Then the sequence
    evidence, then ``mix``'s own lift-and-confidence ordering as the tiebreak,
    so a gap this module can say nothing about keeps exactly the position the
    grid already gave it instead of being shuffled to the bottom.

    **The share is read only where the pair is directional, and only to one
    decimal place.** Two defects sat here and both inverted the order the grid
    had already got right:

    *A share the module called NO_ORDER was still sorting the list.* If a pair's
    two directions are evenly split this module reports NO_ORDER — a
    measurement, not a gap in the evidence — and then let the same share it had
    just disowned outrank ``mix``'s lift. The tiebreak the paragraph above
    promises was unreachable for any gap carrying a measured share.

    *A raw share has no base behind it.* 0.90 over eight customers, which is the
    floor, outranked 0.85 over four hundred — so the ranking preferred the pair
    it knew least about. What sorts is therefore the **lower bound** of the
    share, not the share: ``_wilson_lower`` puts the first at about 0.60 and the
    second at about 0.81, which is the honest order. Banding the share to a
    decimal was tried first and does not work — 0.90 and 0.85 round into
    different bands, so the thin pair still won.

    The bound is arithmetic over two counts and nothing else; it introduces no
    model and no forecast. It is the same instinct ``radar`` applies by keeping
    evidence and money on separate axes, expressed as one number here because a
    sort key has to be one number.
    """
    seq = gap.get("sequence") or {}
    affinity = gap.get("affinity") or {}
    directional = bool(seq.get("directional", False))
    strength = (_wilson_lower(seq.get("after_share") or 0.0,
                              seq.get("both") or 0)
                if directional else 0.0)
    return (gap.get("state") != mix.LAPSED,
            not directional,
            -strength,
            -(affinity.get("lift") or 0.0),
            -(affinity.get("confidence") or 0.0))


def _wilson_lower(share: float, base: int, *, z: float = 1.96) -> float:
    """The lower end of a share measured over ``base`` customers.

    Standard Wilson score interval, taken at its lower bound so a share the book
    has seen eight times cannot outrank one it has seen four hundred times. Two
    counts in, one float out — no distributional assumption about future
    customers, and nothing here is a forecast.

    Zero base is zero rather than an error: the floor above should already have
    withheld such a pair, and a sort key is the wrong place to raise.
    """
    if base <= 0:
        return 0.0
    z2 = z * z
    denom = 1.0 + z2 / base
    centre = (share + z2 / (2 * base)) / denom
    margin = (z * math.sqrt((share * (1.0 - share) + z2 / (4 * base)) / base)
              / denom)
    return max(0.0, centre - margin)


def below_floor(order: list[str], pairs: dict[tuple[str, str], Pair],
                th: CommercialThresholds) -> dict:
    """What the evidence floor excluded, so an unranked grid explains itself.

    ``radar.below_floor``'s shape and its reasoning: a screen that ranks nothing
    because every pair is thin should say so with the largest base it rejected
    beside the floor it was measured against, so the reader can see how far off
    it is. Reporting only "no ranking available" is what makes somebody go
    looking for the bug, and the second thing they try is the floor.

    Two counts, because they mean different things and the difference is the
    finding. ``pairs_possible`` is every ordered pair of columns;
    ``pairs_observed`` is how many of those any customer has ever held both
    halves of, and ``pairs_below_floor`` how many of *those* were too thin. A
    book where
    those two are far apart is not a book with a thin floor — it is a book whose
    customers each take one line, and no floor was ever going to help.
    """
    thin = [p for p in pairs.values() if p.after_share is None]
    return {
        "min_base_customers": th.crosssell_min_base_customers,
        "pairs_possible": len(order) * (len(order) - 1),
        "pairs_observed": len(pairs),
        "pairs_below_floor": len(thin),
        "largest_base_excluded": max((p.both for p in thin), default=0),
    }


def rank(grid: dict, *, thresholds: CommercialThresholds) -> dict:
    """Annotate every gap on the grid with its sequence evidence, and reorder.

    Mutates ``grid["customers"][…]["gaps"]`` in place and returns the summary
    that goes beside it. In place because the alternative is a parallel index
    keyed by customer and line that the client has to join against the grid —
    and a join on the client is where two structures describing one screen start
    disagreeing about which gap they meant.
    """
    th = thresholds
    order, label_of = _known(grid)
    firsts = _first_dates(grid, set(order))
    pairs = _pairs(firsts, th)

    measured = [p for p in pairs.values() if p.after_share is not None]
    for row in grid.get("customers") or []:
        held = [c["category"] for c in row.get("cells") or []
                if c.get("state") == mix.BUYS and c.get("category") in label_of]
        for gap in row.get("gaps") or []:
            gap["sequence"] = _best_anchor(pairs, held, gap.get("category"),
                                           label_of)
        row["gaps"] = sorted(row.get("gaps") or [], key=_gap_key)

    return {
        "measurable": bool(measured),
        "reason": _reason(order, pairs, measured, th),
        # Strongest first, and the base size breaks ties so the better
        # evidenced of two equal shares leads.
        "pairs": [p.to_dict(label_of) for p in
                  sorted(measured, key=lambda p: (-(p.after_share or 0.0),
                                                  -p.both, p.anchor, p.target))],
        "below_floor": below_floor(order, pairs, th),
        "min_base_customers": th.crosssell_min_base_customers,
        "min_sequence_share": th.crosssell_min_sequence_share,
        # Customers holding at least two lines — the only ones any pair could
        # have been counted over. Published so a small ``both`` can be read
        # against the book it came from.
        "customers_compared": len(firsts),
        "direction_meanings": DIRECTION_MEANING,
        "unavailable": unavailable(),
        "thresholds_version": th.version,
    }


def _reason(order: list[str], pairs: dict[tuple[str, str], Pair],
            measured: list[Pair], th: CommercialThresholds) -> Optional[str]:
    """Why nothing is ranked, distinguishing the three ways that happens.

    ``mix._empty_reason`` is the precedent and so is its incident: two states
    served as one sent somebody to look at the sync when the answer was a field
    on the item master. Three states here — no columns, no customer holding two
    lines, and a book too thin for any pair — and they send a reader to three
    different places.
    """
    if measured:
        return None
    if len(order) < 2:
        return ("Fewer than two lines are on this grid, so there is no pair of "
                "them to observe an order between. On the supplier pivot only "
                "principals this book has actually sold become columns; on the "
                "line-of-business pivot, check the catalogue coverage.")
    if not pairs:
        return ("No customer on this book has ever bought from two different "
                "lines, so nothing has been taken in any order yet.")
    largest = max(p.both for p in pairs.values())
    return (f"No pair of lines has been taken by at least "
            f"{th.crosssell_min_base_customers} customers — the best-evidenced "
            f"pair has {largest}. The gaps on the grid are shown unranked "
            f"rather than ordered on a coincidence. More trade on the book "
            f"fixes this; a lower floor does not.")


def unavailable() -> list[dict]:
    """What the ordering will not tell you, said where it would be read."""
    return [
        {
            "what": "What a gap is worth",
            "kind": absence.PERMANENT,
            "why": ("This ranks how usually one line follows another, never "
                    "how much money is behind it. Multiplying a gap by what "
                    "comparable customers spend would produce a rupee figure "
                    "with a forecast hidden inside it, on a grid that cannot "
                    "see whether the customer needs the line at all."),
        },
        {
            "what": "Whether taking one line causes the next",
            "kind": absence.PERMANENT,
            "why": ("The order is a fact about our own invoices. Customers who "
                    "took both may simply have grown, or been sold both by the "
                    "same person on the same visit. Nothing here is a "
                    "probability that this customer takes the line next."),
        },
        {
            "what": "When the second line would land",
            "kind": absence.PERMANENT,
            "why": ("The median interval is how long it took the customers who "
                    "did take it, counted after the fact. It is not a due date "
                    "for anybody who has not."),
        },
    ]
