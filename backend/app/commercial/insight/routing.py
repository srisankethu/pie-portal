"""Which legal entity a customer or a principal should sit under — the inputs,
never the answer.

Three companies trade on one platform and the placement decision is made from
memory: somebody remembers that 4U is the one registered where this customer
takes delivery, that SLS is the one already past the withholding threshold with
that supplier, that UPS has no headroom left. Every one of those facts is
already computed here, and not one of them is on the same page as the others.
This puts them side by side, per connected company, and stops there.

**It does not recommend, and that boundary is the feature rather than a
limitation of it.** Routing an order between legal entities moves the GST
place of supply and the income-tax position of two companies. ``insight/msme``
sets the precedent in one sentence — *a wrong margin costs a deal; a wrong tax
position is the operator's liability and nobody here is their accountant* — and
the same reasoning applies with more force here, because a margin is at least a
number this platform computed. There is therefore no recommendation field, no
preferred entity, no ranking and no score. The entities come back in
alphabetical order, which is not a ranking and is sorted that way so that
nobody can read one into the order they arrived in. What ships is the set of
facts a person needs in order to ask their accountant a precise question, and
``no_recommendation`` says so on the response rather than leaving the omission
to be read as an oversight somebody should fill in.

The rejected design, stated because it is the obvious one and somebody will
propose it again: score each entity on the five determinants, weight them, and
print "route this to 4U Precision". It fails twice over. The weights would be
invented — nothing in this platform knows what a percentage point of return on
capital is worth against a place-of-supply mismatch — and the output would be
an advisory the operator would reasonably rely on, in the one area of this
product where being wrong is a liability rather than a lost deal. A screen that
lays five true facts out and says nothing is more useful than a confident
ordering nobody can defend.

## What each column is, and whose refusal it inherits

Nothing here is re-derived. Every figure arrives from the module that owns it,
and every refusal that module makes is carried through with its own wording —
the rule ``insight/daily`` states for the morning read and for the same reason:
a screen that worked out "over the limit" its own way would eventually disagree
with the credit screen and nobody could say which was right.

``jurisdiction``  Which country's statutes reach this tenant, and the statutory
                  calendar that follows from it. Three states and never two —
                  supported, unsupported, not set — exactly as
                  ``commercial/jurisdiction`` argues: a NULL country read as
                  India would put Indian deadlines on a book they may not
                  govern, and read as "nothing applies" would hide real
                  exposure from an Indian tenant whose column simply predates
                  the migration.
``withholding``   The s.194Q position. **Silent while the organization's own
                  turnover gate is unconfirmed**, which is ``insight/
                  withholding``'s own restraint inherited rather than
                  re-decided: the crossing list is empty and the counts beside
                  it are ``None`` rather than zero, because "no supplier has
                  crossed" and "we are not allowed to say" are different
                  answers and a zero would be the reassuring one.
``msme``          The 43B(h) exposure ``insight/msme`` already computes, with
                  its confirmed band and its gap band kept apart — the gap is
                  what *would* be at risk if suppliers nobody has classified
                  turn out to be protected, and it is never added into the
                  confirmed figure.
``credit``        Headroom per customer from ``insight/credit``, with absent,
                  zero and over kept as three distinct states. A limit nobody
                  recorded is not a limit of zero and not an unlimited one, so
                  the headroom total speaks for the accounts that have a limit
                  and says how many that is; folding the rest in as zero would
                  understate the room and as unlimited would erase the
                  question.
``capital``       Capital employed and its return from ``insight/capital``,
                  which refuses every month before the receivable/payable
                  reliability boundary. That refusal propagates here with the
                  sentence that module wrote, rather than arriving as a blank
                  cell a reader would take for nil capital.

## Never pooled

``insight/cycle`` refuses a group total on the ground that three balance sheets
added together belong to no legal entity and match no filing; ``insight/
capital`` inherits it and so does this. There is deliberately no group row, no
organization-wide summary and no "all entities" option, and the refusal is
listed in ``unavailable`` rather than left as an absence — a missing total with
nothing said reads as an omission somebody should fix, which is how it would
come back.

The same argument bites harder here than on either of those screens, because
the whole point of this one is choosing *between* the entities: a pooled row
would be the one line on the page that answers no question anybody came with.

## Two grains this platform does not have, said out loud

The country is recorded on the *organization* and the 194Q turnover gate is one
flag on the organization's thresholds. Both are properly per legal entity — a
group can hold an Indian company and a Gulf one, and the s.194Q gate turns on
each buyer's own prior-year turnover. Every entity therefore shows the same
jurisdiction and the same gate, and both are filed in ``unavailable`` as
BUILDABLE rather than quietly presented as an established per-entity fact.
BUILDABLE and not COLLECTABLE, and the distinction is the one ``insight/
absence`` exists for: there is no column to fill in, so putting this on a
worklist would put a clerical task on a blank nobody can complete.

Layer rules, inherited: this is ``commercial/``, deterministic, pure on its
inputs, and it never imports ``ai/``. Money stays ``Decimal`` until the
response rounds it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from .. import jurisdiction
from ..config import CommercialThresholds
from . import absence, credit, msme

_ZERO = Decimal(0)

#: The three states a tenant's jurisdiction can be in. Named here rather than
#: read as "country is None" at each call site, because two of them are refusals
#: with different remedies and a boolean would force one of them to be the
#: other. The same device ``msme.Status.scope`` is, for the same reason.
SUPPORTED = "SUPPORTED"
UNSUPPORTED = "UNSUPPORTED"
NOT_SET = "NOT_SET"

#: The five determinants, named once so the response, the legend and any table
#: built on them cannot disagree about the set — ``capital.READINGS`` and
#: ``cycle.LEGS`` are the same device.
DETERMINANTS = ("jurisdiction", "withholding", "msme", "credit", "capital")


@dataclass(frozen=True)
class Book:
    """One connected company, and the two things about it nothing else carries.

    The statutory and capital views arrive already built, keyed by
    ``connection_id``. These two do not, because neither has a builder of its
    own: a supplier's MSME scope is a property of ``msme.Status`` read per
    vendor, and a customer's standing is an ``insight/credit`` exposure read per
    account. Both are passed as the values themselves rather than as counts, so
    the counting happens here — a router that arrived at "three accounts over
    their limit" on its own would be the money arithmetic CLAUDE.md §3 keeps out
    of routers.
    """

    connection_id: str
    label: str
    #: ``msme.Status.scope`` for every supplier filed in this book.
    supplier_scopes: tuple[str, ...] = ()
    #: Every customer of this book, against their own credit line.
    exposures: tuple[credit.Exposure, ...] = field(default_factory=tuple)


# ── the five determinants, one function each ────────────────────────────────
def _jurisdiction(country: Optional[str]) -> dict:
    """Which statutes reach this book, and the calendar that follows.

    The refusal *texts* are not written here. They live in
    ``commercial/jurisdiction``, keyed by country and by statute, and are read
    through the same three functions the statutory endpoints read them through
    — a second wording of "your country is not set" is a second wording that
    drifts, and the reader would be looking at both on adjacent screens.
    """
    code = jurisdiction.normalize(country)
    known = jurisdiction.for_country(code)
    if known is not None:
        state = SUPPORTED
    elif code is None:
        state = NOT_SET
    else:
        state = UNSUPPORTED

    return {
        "country": code,
        # Three states, never two. A screen that collapsed NOT_SET into
        # UNSUPPORTED would send somebody to a jurisdiction review when what is
        # wanted is a two-character code typed into Settings.
        "state": state,
        "fy_start_month": known.fy_start_month if known else None,
        "statutes": {
            "msme": known.msme_applies if known else None,
            "withholding": known.withholding_applies if known else None,
        },
        "why": jurisdiction.calendar_refusal(code),
        # Where the fact is recorded, not where it belongs. See the module
        # docstring and the ``unavailable`` entry: one country for three
        # companies is a grain this platform does not hold yet, and saying so on
        # the row is cheaper than a reader discovering it from the fact that all
        # three rows agree.
        "recorded_on": "organization",
    }


def _financial_year(country: Optional[str], as_of: date) -> Optional[str]:
    """The statutory year ``as_of`` falls in, or ``None`` for a book whose
    calendar this platform cannot name.

    Gated on the jurisdiction table rather than on the country merely being
    set, because April-to-March is India's year and not a universal one — the
    argument ``/self-funding`` makes before folding anything into financial
    years.
    """
    known = jurisdiction.for_country(country)
    return known.fy_of(as_of) if known else None


def _withholding(view: Optional[dict], *, blocked_by: Optional[str],
                 thresholds: CommercialThresholds) -> dict:
    """The s.194Q position for one book, and nothing while the gate is open.

    Three shapes, and the differences between them are deliberate.

    Where the statute does not reach this tenant there is no ``gate_confirmed``
    key at all — the endpoint ``/withholding-crossings`` makes exactly this
    call, on the ground that the flag means "confirm your turnover in Settings",
    which is advice that cannot help somebody the statute does not govern.

    Where it reaches but the gate is unconfirmed, the counts are ``None`` rather
    than ``0``. A zero would say no supplier is near the threshold, which is not
    known and is the benign reading of missing evidence CLAUDE.md §1 names three
    times. The statutory threshold itself still travels: it is the policy in
    force, not a reading of this book, and a reader who cannot see the line
    cannot tell how far the silence extends.
    """
    if blocked_by is not None:
        return {"applies": False, "blocked_by": blocked_by, "crossings": []}
    # **A book with no bills is not a book with an unconfirmed gate.** The gate
    # is a fact about the tenant's own prior-year turnover — one setting for the
    # whole organization — while ``view`` is absent whenever *this* book has no
    # purchases to measure against it. Conflating them told an owner who had
    # already confirmed the gate to go and confirm it, on the one entity that
    # bought nothing, and contradicted the ``gate_confirmed: True`` sitting on
    # the book next to it.
    if view is None and thresholds.s194q_org_gate_met:
        return {
            "applies": True,
            "gate_confirmed": True,
            "party_threshold": float(thresholds.s194q_party_threshold),
            "crossings": [],
            # Zero, not ``None``, and the difference is the point: the gate is
            # confirmed and the bills were looked for, so "no supplier is near
            # the threshold" is a measurement here rather than a silence.
            "crossed": 0,
            "approaching": 0,
            "blocked_by": None,
            "note": ("No bill is on record for this company, so no supplier has "
                     "any purchase against this entity to measure. The gate is "
                     "confirmed; there is simply nothing under it."),
        }
    if view is None or not view.get("gate_confirmed"):
        return {
            "applies": True,
            "gate_confirmed": False,
            "party_threshold": float(thresholds.s194q_party_threshold),
            "crossings": [],
            "crossed": None,
            "approaching": None,
            "blocked_by": (view or {}).get("note") or (
                "Section 194Q applies only if this entity's own turnover "
                "exceeded the statutory limit in the preceding financial year. "
                "That figure is not in this platform, and nothing is asserted "
                "until it is confirmed in Settings."),
        }
    rows = view.get("crossings") or []
    return {
        "applies": True,
        "gate_confirmed": True,
        "party_threshold": view.get("threshold"),
        "financial_year": view.get("financial_year"),
        "crossings": rows,
        "crossed": sum(1 for r in rows if r.get("crossed")),
        "approaching": sum(1 for r in rows if r.get("approaching")),
        "basis_note": view.get("basis_note"),
        "blocked_by": None,
    }


def _msme(view: Optional[dict], scopes: tuple[str, ...], *,
          blocked_by: Optional[str]) -> dict:
    """The 43B(h) exposure for one book, with its two bands kept apart.

    ``confirmed`` and ``gaps`` are passed through as ``insight/msme`` built
    them and are never added together. That module's own reason holds here
    unchanged: adding the gap band would assert an exposure nobody has
    established, and dropping it would report a reassuring number computed from
    ignorance.

    The supplier counts are the coverage question this book's exposure figure
    depends on — a confirmed total of nil means little when nobody has
    classified a single supplier, and the two numbers only mean anything beside
    each other.
    """
    coverage = {
        "in_scope": sum(1 for s in scopes if s == msme.IN_SCOPE),
        "out_of_scope": sum(1 for s in scopes if s == msme.OUT_OF_SCOPE),
        "unknown": sum(1 for s in scopes if s == msme.UNKNOWN),
        "suppliers": len(scopes),
    }
    if blocked_by is not None:
        return {"applies": False, "blocked_by": blocked_by, "suppliers": coverage}
    if view is None:
        return {
            "applies": True,
            "suppliers": coverage,
            "blocked_by": ("No bill is on record for this book, so there is no "
                           "payment deadline to place against the section 15 "
                           "limit."),
        }
    return {
        "applies": True,
        "financial_year": view.get("financial_year"),
        "horizon_days": view.get("horizon_days"),
        "confirmed": view.get("confirmed"),
        "gaps": view.get("gaps"),
        "suppliers": coverage,
        "tax_rate_set": view.get("tax_rate_set"),
        "basis_note": view.get("basis_note"),
        "blocked_by": None,
    }


def _credit(exposures: tuple[credit.Exposure, ...]) -> dict:
    """Every account of this book against its own line.

    **Absent, zero and over are three states and none of them is folded into
    another.** ``insight/credit`` makes that argument at length and this is the
    one place it could quietly be lost, because a per-entity roll-up wants a
    single headroom figure and the only way to get one is to decide what an
    unassessed account contributes. Neither answer is available: as zero it
    understates the room, as unlimited it erases the question. So headroom is
    summed over the accounts that carry a limit, the count it speaks for
    travels beside it, and a book where nobody has recorded a limit at all
    reports ``None`` rather than a total of nothing.

    A recorded limit of zero is counted separately from both, because it is a
    decision somebody made — this account ships against cash — and it is
    invisible in the status alone: a zero line with nothing owed reads WITHIN
    and one with a rupee owed reads OVER.

    The figures are computed per account and reported per book; the accounts
    themselves are deliberately not repeated here. ``/credit`` is the screen
    that lists them, with the owner, the note and who set the limit, and a
    second list would be a second place for one account to appear with two
    different balances the day one of them is filtered differently.
    """
    with_limit = [e for e in exposures if e.limit is not None]
    over = [e for e in exposures if e.status == credit.OVER]
    headroom = (sum((e.headroom for e in with_limit if e.headroom is not None),
                    _ZERO)
                if with_limit else None)
    return {
        "accounts": len(exposures),
        # Not a limit of zero, and not an unlimited one. The count is the
        # worklist: it is how many accounts this entity is carrying with nobody
        # having decided what they may owe.
        "no_limit_recorded": sum(1 for e in exposures if e.limit is None),
        "limit_of_zero": sum(1 for e in exposures if e.limit == _ZERO),
        "within": sum(1 for e in exposures if e.status == credit.WITHIN),
        "near": sum(1 for e in exposures if e.status == credit.NEAR),
        "over": len(over),
        "outstanding": _money(sum((e.outstanding for e in exposures), _ZERO)),
        "overdue": _money(sum((e.overdue for e in exposures), _ZERO)),
        "headroom": _money(headroom),
        # What the headroom figure covers. Without it a reader cannot tell a
        # comfortable book from an unassessed one, and those are the two states
        # this whole module exists to keep apart.
        "headroom_speaks_for": len(with_limit),
        "over_by": _money(sum((e.over_by for e in over
                               if e.over_by is not None), _ZERO)),
        "note": ("Headroom is stated for the accounts that carry a recorded "
                 "limit. Accounts with none are counted separately and are not "
                 "read as unlimited or as zero — nobody has decided what they "
                 "may owe, which is a different fact from either."),
    }


def _capital(view: Optional[dict]) -> dict:
    """Capital employed and its return, as ``insight/capital`` last stated them.

    The latest month it could answer for, not the latest month there is a row
    for — that module already makes the distinction and hands back ``latest``
    taken from the stated months. Where there is no stated month the reason is
    carried through instead of a null: ``capital`` refuses every month end
    before the receivable and payable positions can be reconstructed, and that
    refusal arriving here as a blank cell would be read as nil capital, which
    is both a smaller number and a flattering one.
    """
    if view is None:
        return {"stated": False, "blocked_by": (
            "No cycle has been replayed for this book, so there is no capital "
            "position to state. A book with no invoice and no bill on record "
            "has nothing to reconstruct a balance from.")}

    latest = view.get("latest")
    months = view.get("months") or []
    if latest is None:
        why = dict((months[-1].get("why") or {}) if months else {})
        return {
            "stated": False,
            "months_stated": int(view.get("months_stated") or 0),
            "why": why,
            # The module's own sentence where it wrote one. A second sentence
            # about the same gap is how a reader ends up believing there are
            # two problems.
            "blocked_by": why.get("capital_employed") or (
                "No month end in the reported window carries a capital figure "
                "for this book."),
            "receivables_reliable_from": view.get("receivables_reliable_from"),
            "payables_reliable_from": view.get("payables_reliable_from"),
            "stock_observed_from": view.get("stock_observed_from"),
        }
    return {
        "stated": True,
        "month": latest.get("month"),
        "capital_employed": latest.get("capital_employed"),
        "avg_capital_employed": latest.get("avg_capital_employed"),
        "return_on_capital": latest.get("return_on_capital"),
        "working_capital_per_rupee": latest.get("working_capital_per_rupee"),
        "confidence": latest.get("confidence"),
        "months_stated": int(view.get("months_stated") or 0),
        # Which of the readings above are absent, and the module's reason for
        # each. Carried whole rather than filtered: a return that is missing
        # because the average was refused and one missing because suppliers
        # fund the whole cycle are different findings.
        "unknown": latest.get("unknown") or [],
        "why": dict(latest.get("why") or {}),
        "receivables_reliable_from": view.get("receivables_reliable_from"),
        "payables_reliable_from": view.get("payables_reliable_from"),
        "stock_observed_from": view.get("stock_observed_from"),
        "blocked_by": None,
    }


def _money(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(round(value, 2))


# ── the build ────────────────────────────────────────────────────────────────
def assemble(*, books: Iterable[Book], country: Optional[str],
             capital_view: Optional[dict],
             withholding_views: Optional[dict[str, dict]] = None,
             msme_views: Optional[dict[str, dict]] = None,
             as_of: date, thresholds: CommercialThresholds) -> dict:
    """The five determinants, per connected company, side by side.

    Takes what the other builders returned rather than a session, which is the
    shape ``insight/daily.assemble`` uses and for its reason: a view that worked
    out its own answer to a question another screen already answers would
    eventually disagree with it, and nobody could say which was right. The only
    arithmetic here is counting rows and adding figures it was handed.

    ``capital_view`` is what ``capital.build`` returned; the two statutory
    dictionaries are keyed by ``connection_id`` and carry what
    ``withholding.crossings`` and ``msme.watchlist`` returned for that book
    alone. A key missing from either is a book with nothing to say, not a book
    that was refused — the refusal travels as ``blocked_by`` instead.
    """
    withholding_views = withholding_views or {}
    msme_views = msme_views or {}
    capital_by_book = {e["connection_id"]: e
                       for e in ((capital_view or {}).get("entities") or [])}

    withholding_blocked = jurisdiction.withholding_refusal(country)
    msme_blocked = jurisdiction.msme_refusal(country)
    shared = _jurisdiction(country)
    financial_year = _financial_year(country, as_of)

    entities = [
        {
            "connection_id": book.connection_id,
            "label": book.label,
            "financial_year": financial_year,
            "jurisdiction": shared,
            "withholding": _withholding(
                withholding_views.get(book.connection_id),
                blocked_by=withholding_blocked, thresholds=thresholds),
            "msme": _msme(msme_views.get(book.connection_id),
                          book.supplier_scopes, blocked_by=msme_blocked),
            "credit": _credit(tuple(book.exposures)),
            "capital": _capital(capital_by_book.get(book.connection_id)),
        }
        for book in books
    ]
    # Alphabetical, and the comment is the point: this is not a rank. Sorting by
    # any of the determinants would be the recommendation this module refuses to
    # make, arriving through the back door as an order somebody reads top-down.
    entities.sort(key=lambda e: (str(e["label"]), str(e["connection_id"])))

    return {
        "as_of": as_of.isoformat(),
        "financial_year": financial_year,
        "determinants": list(DETERMINANTS),
        "entities": entities,
        "legend": _legend(),
        "no_recommendation": _no_recommendation(),
        "unavailable": _unavailable(),
    }


def _no_recommendation() -> str:
    """Why there is no preferred entity on a screen built to compare entities.

    On the response rather than in a comment, because the absence is the part a
    reader will otherwise take for an unfinished feature — and the next person
    to be asked for "just a suggested entity" needs the reason to hand.
    """
    return ("This screen surfaces the inputs to an entity-placement decision "
            "and deliberately does not make one. Moving a customer or a "
            "principal between legal entities changes the GST place of supply "
            "and the income-tax position of two companies, which is the "
            "operator's liability and not this platform's to answer. There is "
            "no preferred entity, no ranking and no score here; the entities "
            "are listed alphabetically so that no order can be read as one. "
            "Take these figures to your accountant as the question, not as the "
            "answer.")


def _legend() -> dict:
    """Each determinant in the reader's terms.

    Here rather than on the client for the reason ``cycle._definition`` gives: a
    number somebody cannot restate in a sentence is a number they will not act
    on, and two explanations of one figure is one explanation too many.
    """
    return {
        "jurisdiction": (
            "Which country's statutes reach this tenant, and the statutory "
            "financial year that follows. Supported, unsupported and not set "
            "are three different answers — an unrecorded country is not India."),
        "withholding": (
            "Section 194Q: suppliers this book has bought past the party "
            "threshold from this financial year. Nothing is listed until the "
            "entity's own prior-year turnover gate is confirmed in Settings, "
            "because the duty only arises if it was crossed."),
        "msme": (
            "Section 43B(h): what is still owed to micro and small suppliers "
            "past the section 15 limit. The confirmed band is suppliers whose "
            "status is on record; the gap band is what would be at risk if "
            "suppliers nobody has classified turn out to be protected, and the "
            "two are never added together."),
        "credit": (
            "What this book's customers owe against the limits they were "
            "given. Headroom covers the accounts that have a recorded limit; "
            "accounts with none are counted apart, because nobody deciding is "
            "not the same as deciding on nothing."),
        "capital": (
            "Operating capital standing in this book's trading cycle and the "
            "gross profit it earned over the same window — receivables plus "
            "stock less payables, per legal entity. Not a balance sheet and "
            "not ROCE; see the capital screen for what is left out."),
    }


def _unavailable() -> list[dict]:
    """What this view cannot answer, said plainly rather than left blank."""
    return [{
        "series": "recommended_entity",
        # PERMANENT, and not for a data reason — the precedent is
        # ``capital.annualised_return_on_capital``, which is PERMANENT because
        # the operation is wrong rather than because an input is missing.
        # Filing this as BUILDABLE would put "add a recommendation engine" on
        # somebody's backlog, which is the outcome the whole module is against.
        "kind": absence.PERMANENT,
        "reason": ("No entity is recommended, ranked or scored. Placement "
                   "moves the GST place of supply and the income-tax position "
                   "of two legal entities; a wrong margin costs a deal and a "
                   "wrong tax position is the operator's liability. More data "
                   "would not make this platform the right author of that "
                   "answer — the determinants are surfaced so a person can put "
                   "the question to their accountant precisely."),
    }, {
        "series": "gst_place_of_supply",
        "kind": absence.PERMANENT,
        "reason": ("Where a supply is treated as made, and therefore which tax "
                   "applies to it, is a determination about a transaction that "
                   "has not happened yet under registrations this platform "
                   "does not hold as a rule set. It is not computed, not "
                   "approximated, and not implied by anything on this screen."),
    }, {
        "series": "group_routing_summary",
        "kind": absence.PERMANENT,
        "reason": ("There is deliberately no total or combined row across the "
                   "connected companies. Three balance sheets added together "
                   "produce figures belonging to no legal entity and matching "
                   "no filing — the refusal ``insight/cycle`` makes and "
                   "``insight/capital`` inherits. It bites harder here: this "
                   "screen exists to choose between the entities, so a pooled "
                   "row would be the one line on it that answers nothing."),
    }, {
        "series": "jurisdiction_per_connected_company",
        # BUILDABLE rather than COLLECTABLE, and the difference matters: there
        # is no per-connection country column, so this is not a blank somebody
        # can fill in — filing it as COLLECTABLE would put a clerical task on a
        # field that does not exist.
        "kind": absence.BUILDABLE,
        "reason": ("The country is recorded on the organization, not on each "
                   "connected company, so every entity here shows the same "
                   "jurisdiction. A group holding companies in two countries "
                   "cannot be told apart until a connection carries its own "
                   "country — which is a column and a sync change, not a "
                   "value somebody can record today."),
    }, {
        "series": "s194q_gate_per_connected_company",
        "kind": absence.BUILDABLE,
        "reason": ("The section 194Q turnover gate is one flag on the "
                   "organization's thresholds, and the statute turns on each "
                   "buyer's own prior-year turnover. Every entity therefore "
                   "shows the same gate. Confirming it separately per company "
                   "needs a per-connection setting that does not exist yet, so "
                   "the gate is presented as the tenant-wide fact it currently "
                   "is."),
    }, {
        "series": "why_a_customer_or_principal_sits_where_it_does",
        # Not COLLECTABLE and not TRANSIENT: the placement itself is already
        # recorded, as the connection each master row was imported under. What
        # is missing is the *intent* behind it, which nobody types anywhere.
        "kind": absence.PERMANENT,
        "reason": ("Which entity a customer or supplier trades under today is "
                   "visible from the book their records were imported into, "
                   "but why they were placed there is not recorded anywhere "
                   "and cannot be recovered. A placement made for a reason "
                   "that has since changed looks identical to one made "
                   "deliberately last week."),
    }]
