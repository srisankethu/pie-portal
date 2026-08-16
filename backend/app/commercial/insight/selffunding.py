"""Retained profit against the revenue growth it had to pay for.

"Are we outgrowing our own cash." A distributor that grows revenue 40% on a
book that retained 3% of it is funding the difference from somewhere, and the
somewhere is a bank, a supplier's patience, or the owner's own money. The
question is worth a screen because the two halves of it live in different
places and nobody puts them side by side: revenue growth is in the trading
records, and retained profit is in an audited account nobody reads monthly.

**Half of this reading is not derivable here and is not derived.** PIE ingests
documents, not a ledger. There is no opex in it, no tax, no depreciation, and
no P&L ingestion — so the nearest available figure is gross profit, and gross
profit is not profit after tax. Substituting it would overstate retained profit
by the entire cost of running the business, and it would do so silently, in the
direction that reads as good news. That is precisely the benign default
CLAUDE.md §1 names three times. Zoho Books already reports Profit & Loss,
Balance Sheet, Cash Flow and Movement of Equity natively; re-deriving them here
would be a fourth, worse copy, and an accrual-vs-cash or inter-entity error in
it would surface as a confidently wrong headline rather than a visible gap.

So retained PAT is **owner-confirmed**, per entity per financial year, in
``CommercialThresholds.retained_pat`` — off by default and silent until
confirmed, exactly as ``s194q_org_gate_met`` is and for the same reason.

**Every trading entity, or nothing.** A partial sum is the trap this module
would otherwise walk into: three legal entities share one book, revenue is
whole-book, and totalling two entities' retained profit against three entities'
revenue understates retention and manufactures an alarm. Confirming the other
direction — one entity of three, against that entity's own revenue — is not
available either, because the trading rows carry a customer and a date and no
entity. So the reading waits until every entity that trades has a figure for
the year, and names the ones it is waiting on.

**The financial year, not a rolling window.** A PAT figure exists for a year
because that is the period a set of accounts closes over, and comparing it
against a three-month revenue movement would be comparing two different things
with the same units. The windows are built from ``periods.Period`` and read with
``periods.revenue_in_exact`` — the same bucketing every other comparative screen
uses, in ``Decimal`` because these two numbers are divided by each other.

**What this stops short of saying.** Growth does not consume a rupee of cash per
rupee of extra revenue; it consumes working capital, which is some fraction of
it — receivables plus stock, less what suppliers are carrying. This platform
holds no measured working-capital intensity, so the verdict is deliberately
one-sided:

``COVERED``       retained profit exceeds the *whole* revenue increase, so it
                  covers the growth under any intensity at all. Sufficient, and
                  safe to state.
``UNDETERMINED``  retained profit is smaller than the increase. This is **not**
                  a finding that growth was externally funded — it is the
                  honest limit of two numbers, and the missing third one is
                  named in the response rather than assumed.
``NOT_GROWING``   revenue did not grow, so there was nothing to fund.
``UNKNOWN``       cannot be computed; ``blocked_by`` says what is missing.

A four-state string rather than a boolean because ``False`` on a screen becomes
a red chip, and "we cannot tell" rendered as "you are in trouble" is the same
class of mistake as rendering it green.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Optional

from .msme import fy_of
from .periods import Period, label_for, revenue_in_exact
from .series import TradeRow
from .withholding import fy_bounds

COVERED = "COVERED"
UNDETERMINED = "UNDETERMINED"
NOT_GROWING = "NOT_GROWING"
UNKNOWN = "UNKNOWN"

_ZERO = Decimal(0)


def _out(value: Decimal) -> float:
    return float(round(value, 2))


def _ratio(top: Decimal, bottom: Decimal) -> Optional[float]:
    return float(round(top / bottom, 4)) if bottom > _ZERO else None


def fy_period(fy_label: str) -> Period:
    """One financial year as a closed period.

    ``withholding.fy_bounds`` — itself reading the calendar from
    ``commercial/jurisdiction`` — is the single definition of where a financial
    year starts and stops, and it returns a half-open range; ``Period`` is
    inclusive at both ends. Converting here rather than restating the months is
    what keeps one answer to "when does FY2025-26 end" in this package.
    """
    start, next_start = fy_bounds(fy_label)
    end = date.fromordinal(next_start.toordinal() - 1)
    return Period(start=start, end=end, label=label_for(start, end))


def previous_fy(fy_label: str) -> str:
    """``FY2025-26`` → ``FY2024-25``."""
    start, _ = fy_bounds(fy_label)
    return fy_of(date(start.year - 1, start.month, start.day))


def _blocked(reason: str, *, financial_year: Optional[str] = None,
             missing_entities: Optional[list[str]] = None,
             confirmed_years: tuple[str, ...] = ()) -> dict:
    """A refusal that names what is missing.

    Returned in the same envelope shape as an answer, with ``verdict`` UNKNOWN,
    so a caller can never mistake "nothing is wrong" for "we could not look".
    ``withholding.crossings`` makes the same choice for the same reason.
    """
    return {
        "verdict": UNKNOWN,
        "confirmed": False,
        "financial_year": financial_year,
        "blocked_by": reason,
        "missing_entities": missing_entities or [],
        "confirmed_years": list(confirmed_years),
    }


def _absent_from(entities: dict[str, str], figures: dict[str, str]) -> list[str]:
    """Which trading entities have no figure, by label rather than by id."""
    return sorted(label for eid, label in entities.items() if eid not in figures)


def reading(sales: Iterable[TradeRow], *, entities: dict[str, str],
            as_of: date, th) -> dict:
    """Retained profit against revenue growth, for the latest complete year.

    ``entities`` maps each trading entity's id to its label — every one of them
    has to have a confirmed figure before anything is asserted, and the labels
    are what makes the refusal readable rather than a list of ids.

    ``as_of`` bounds which years may be reported: a year that has not finished
    has no retained profit to confirm, and a figure entered against it is an
    estimate wearing an audited number's clothes. It is refused rather than
    trusted, and the refusal says so.
    """
    rows = list(sales)
    confirmed_years = th.retained_pat_years()

    if not entities:
        return _blocked(
            "No connected company has traded yet, so there is no entity whose "
            "accounts this figure would come from. Connect a Zoho company and "
            "run a sync.",
            confirmed_years=confirmed_years)
    if not confirmed_years:
        return _blocked(
            "No retained profit has been confirmed. This platform reads "
            "invoices and bills, not a ledger — it holds no opex, tax or "
            "depreciation — so it cannot work out profit after tax, and it will "
            "not stand in gross profit, which is a different and much larger "
            "number. Take each entity's figure from its Profit & Loss in Zoho "
            "Books and enter it in Settings.",
            missing_entities=sorted(entities.values()))

    current_fy = fy_of(as_of)
    # Newest first, and a year still running is never a candidate: its accounts
    # have not closed, so there is no confirmed figure to be had.
    complete = [fy for fy in reversed(confirmed_years) if fy != current_fy]
    if not complete:
        return _blocked(
            f"The only retained-profit figure on record is for {current_fy}, "
            f"which has not finished. A year's profit after tax is not settled "
            f"until its accounts close, so nothing is read from it.",
            financial_year=current_fy, confirmed_years=confirmed_years)

    # The newest complete year every entity has answered for. A year one entity
    # has not closed is skipped rather than reported short, and the refusal
    # below quotes the *newest* attempt, because that is the year somebody is
    # part-way through entering.
    chosen = next((fy for fy in complete
                   if not _absent_from(entities, th.retained_pat_for(fy))), None)
    if chosen is None:
        newest = complete[0]
        return _blocked(
            f"{newest} is partly confirmed. Every trading entity needs its own "
            f"figure before these can be added together — totalling some "
            f"entities' retained profit against the whole book's revenue would "
            f"understate what was kept and invent a shortfall.",
            financial_year=newest,
            missing_entities=_absent_from(entities, th.retained_pat_for(newest)),
            confirmed_years=confirmed_years)

    figures = th.retained_pat_for(chosen)
    retained = sum((Decimal(figures[eid]) for eid in entities), _ZERO)

    current = fy_period(chosen)
    prior_fy = previous_fy(chosen)
    prior = fy_period(prior_fy)
    revenue_now = revenue_in_exact(rows, current)
    revenue_before = revenue_in_exact(rows, prior)

    if revenue_before <= _ZERO:
        return _blocked(
            f"No revenue is on record for {prior.label}, so there is no growth "
            f"to measure {chosen} against. Growth needs a comparable prior "
            f"year; a percentage off a zero base is not one.",
            financial_year=chosen, confirmed_years=confirmed_years)

    growth = revenue_now - revenue_before
    if growth <= _ZERO:
        verdict = NOT_GROWING
    elif retained >= growth:
        verdict = COVERED
    else:
        verdict = UNDETERMINED

    return {
        "verdict": verdict,
        "confirmed": True,
        "financial_year": chosen,
        "previous_financial_year": prior_fy,
        "period": current.to_dict(),
        "previous_period": prior.to_dict(),
        "entities": [
            {"entity": eid, "label": entities[eid],
             "retained_pat": _out(Decimal(figures[eid]))}
            for eid in sorted(entities, key=lambda e: entities[e])
        ],
        "retained_pat": _out(retained),
        "revenue": _out(revenue_now),
        "previous_revenue": _out(revenue_before),
        "revenue_growth": _out(growth),
        # Two rates over the same base, which is what makes them comparable at
        # all: what the book grew by, and what it kept, each as a share of the
        # revenue it started the year with.
        "growth_ratio": _ratio(growth, revenue_before),
        "retention_ratio": _ratio(retained, revenue_before),
        # Their difference, in percentage points — a difference of two ratios,
        # carried as a fraction like every other `_pp` in this codebase.
        "funding_gap_pp": (
            float(round((retained - growth) / revenue_before, 4))),
        # How much retained profit stands behind each rupee of extra revenue.
        # Absent rather than infinite when the book did not grow: there is no
        # growth to divide into, and reporting a very large number would read as
        # a very good year rather than as a flat one.
        "retained_per_rupee_of_growth": (
            float(round(retained / growth, 4)) if growth > _ZERO else None),
        "basis_note": (
            "Retained profit is the figure confirmed for each entity in "
            "Settings, taken from its own accounts — this platform reads "
            "invoices and bills and cannot compute profit after tax. Revenue is "
            "this book's own trading records for the two financial years shown."),
        "limit_note": (
            "Growth does not consume a rupee of cash per rupee of extra "
            "revenue — it consumes working capital, which is a fraction of it. "
            "That fraction is not measured here, so retained profit exceeding "
            "the whole increase is reported as covered, and retained profit "
            "below it is reported as undetermined rather than as a shortfall."),
    }
