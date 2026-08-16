"""The MSME 45-day cliff, as a date and an amount — and nothing more than that.

Section 43B(h) of the Income-tax Act disallows, for the year, a deduction for
anything still owed to a registered micro or small supplier beyond the limit in
section 15 of the MSMED Act. It is a cliff on a date rather than a slope: a
bill settled on 30 March and the same bill settled on 2 April are treated
differently by a whole financial year.

Every input except one was already computed in this package — bill dates from
``BillDoc``, settlement from ``BillPaymentApplication``, agreed terms from
``VendorPaymentTerm``. The missing input is whether the supplier is protected,
which cannot be derived and is now captured on ``VendorMsmeStatus``.

**This module surfaces a date, an amount and the basis it used. It does not
give tax advice, and the distinction is not cosmetic.** A wrong margin costs a
deal; a wrong tax position is the operator's liability and nobody here is their
accountant. So there is no "you should pay this by", no "you will lose the
deduction", and no computed liability — only the arithmetic a person needs in
order to ask their accountant a precise question.

Three things this gets right that the obvious implementation gets wrong.

**Fifteen days is the default, not forty-five.** Section 15 allows fifteen days
where there is no written agreement, and caps a written one at forty-five.
Reading Zoho's ``payment_terms`` as an agreement would understate exposure on
exactly the suppliers who have no contract — the small ones, who are the ones
the section exists to protect. ``terms.py`` already establishes that Zoho's
terms are a fixed dropdown that real agreements get filed under; this module
takes that at its word and requires ``written_agreement is True`` before it
will use an agreed figure.

**A disallowance is a timing difference, not a loss.** The deduction returns in
the year the money is actually paid. Sizing the cost as ``balance × tax_rate``
overstates it by roughly an order of magnitude and is the first thing an
accountant will notice. What it actually costs is a year's carry on tax brought
forward, so the estimate is ``balance × tax_rate × carrying_rate`` — and it is
``None``, not zero, when the owner has not set a rate.

**Unknown is not safe.** A supplier nobody has classified produces a gap row
carrying the amount that would be at risk *if* they turn out to be protected.
That is CLAUDE.md §1: the benign default is the failure mode this codebase has
already found in three unrelated places.

Pure, and deterministic on ``(rows, thresholds, as_of)`` like every other
detector here — no session, no clock, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

from ...domain.enums import (
    MSME_PROTECTED_ACTIVITIES,
    MSME_PROTECTED_CLASSES,
    EnterpriseActivity,
    MsmeClassification,
    MsmeEvidence,
)
from ..jurisdiction import INDIA

# ── where the clock starts ──────────────────────────────────────────────────
#: The bill's own date. The ordinary case, and a proxy — see ``deadline_for``.
BILL_DATE = "BILL_DATE"
#: The date the goods were received against a matched purchase order. Closer to
#: the acceptance the Act actually counts from, so it wins where it exists.
PO_RECEIVED = "PO_RECEIVED_ON"

# ── which limit applied ─────────────────────────────────────────────────────
DEFAULT_LIMIT = "FIFTEEN_DAY_DEFAULT"
AGREED_LIMIT = "WRITTEN_AGREEMENT"
#: A written agreement longer than the statutory maximum, cut down to it. Named
#: separately from ``AGREED_LIMIT`` because the row should say that what was
#: agreed and what the law allows are different numbers.
CAPPED_LIMIT = "WRITTEN_AGREEMENT_CAPPED"

# ── whether the rule reaches this supplier at all ───────────────────────────
IN_SCOPE = "IN_SCOPE"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
UNKNOWN = "UNKNOWN"

#: How each basis reads, for a screen that has to explain itself. The same
#: pattern as ``terms.BASIS_LABELS``, and for the same reason: a date with no
#: stated derivation is a date nobody can argue with or check.
LIMIT_LABELS: dict[str, str] = {
    DEFAULT_LIMIT: "15 days — no written agreement on record",
    AGREED_LIMIT: "as agreed in writing",
    CAPPED_LIMIT: "capped at the statutory maximum",
}

START_LABELS: dict[str, str] = {
    BILL_DATE: "from the bill date",
    PO_RECEIVED: "from the date the goods were received",
}

SCOPE_LABELS: dict[str, str] = {
    IN_SCOPE: "Registered micro/small supplier",
    OUT_OF_SCOPE: "Not covered by the 45-day rule",
    UNKNOWN: "Status not established",
}


@dataclass(frozen=True)
class Status:
    """One supplier's MSME position, as somebody recorded it.

    A plain value object rather than the ORM row, so the arithmetic below can
    be tested without a database and so a caller cannot accidentally hand this
    module a lazily-loaded relationship.
    """

    classification: str = MsmeClassification.UNKNOWN.value
    activity: str = EnterpriseActivity.UNKNOWN.value
    written_agreement: Optional[bool] = None
    agreed_days: Optional[int] = None
    evidence: str = MsmeEvidence.NONE.value
    captured_at: Optional[date] = None

    @property
    def scope(self) -> str:
        """``IN_SCOPE`` / ``OUT_OF_SCOPE`` / ``UNKNOWN`` — three states, never two.

        A boolean would force the unknown case into one of the other two, and
        whichever way it fell would be wrong: as False it hides real exposure,
        as True it cries wolf on the whole supplier list.

        Order matters. A *known* non-protected classification settles the
        question whatever the activity says, because a medium enterprise is out
        of scope regardless of what it does. Only once size is known and
        protected does the trader carve-out get consulted — and an unknown
        activity at that point leaves the whole answer unknown rather than
        assuming the favourable reading.
        """
        if self.classification == MsmeClassification.UNKNOWN.value:
            return UNKNOWN
        if self.classification not in {c.value for c in MSME_PROTECTED_CLASSES}:
            return OUT_OF_SCOPE
        if self.activity == EnterpriseActivity.UNKNOWN.value:
            return UNKNOWN
        if self.activity not in {a.value for a in MSME_PROTECTED_ACTIVITIES}:
            return OUT_OF_SCOPE
        return IN_SCOPE


#: What a caller gets for a supplier nobody has recorded anything about. A
#: module-level constant rather than a default argument, so the "no row" case
#: is one object every caller shares and cannot mutate into something else.
NO_STATUS = Status()


class InvalidStatus(ValueError):
    """A status that cannot mean anything about a deadline."""


def validate_status(*, classification: str, activity: str,
                    written_agreement: Optional[bool],
                    agreed_days: Optional[int], evidence: str) -> Status:
    """The one place an incoming status is checked.

    Here rather than in the router for the reason ``terms.validate`` is: the
    API and any future importer must not be able to disagree about what is
    storable, and a second copy of these rules is a second copy that drifts.

    The combination check is the point. A written agreement with no day count
    cannot produce a date, and a day count with no agreement is the Zoho-term
    confusion this whole feature exists to avoid — both are refused rather than
    silently resolved to a default, because a status that quietly means
    something other than what was typed is worse than one that was rejected.
    """
    valid_classes = {c.value for c in MsmeClassification}
    if classification not in valid_classes:
        raise InvalidStatus(
            f"classification must be one of {', '.join(sorted(valid_classes))}")

    valid_activities = {a.value for a in EnterpriseActivity}
    if activity not in valid_activities:
        raise InvalidStatus(
            f"enterprise activity must be one of "
            f"{', '.join(sorted(valid_activities))}")

    valid_evidence = {e.value for e in MsmeEvidence}
    if evidence not in valid_evidence:
        raise InvalidStatus(
            f"evidence must be one of {', '.join(sorted(valid_evidence))}")

    if written_agreement is True and agreed_days is None:
        raise InvalidStatus(
            "A written agreement needs the number of days it agreed to — "
            "without it there is no date to compute, and the fifteen-day "
            "default would silently apply to a supplier you have just recorded "
            "an agreement for.")
    if written_agreement is not True and agreed_days is not None:
        raise InvalidStatus(
            "Days were given without a written agreement on record. Section 15 "
            "allows fifteen days unless an agreement in writing says otherwise, "
            "and a term copied from the ERP's dropdown is not that agreement — "
            "confirm the agreement exists, or leave the days blank.")
    if agreed_days is not None and agreed_days < 0:
        raise InvalidStatus("An agreed term cannot be negative")

    if (classification != MsmeClassification.UNKNOWN.value
            and evidence == MsmeEvidence.NONE.value):
        raise InvalidStatus(
            "A classification needs evidence behind it — a Udyam certificate, "
            "the declaration on their invoice, an email, or a portal lookup. A "
            "status nobody can source is one nobody can defend later.")

    return Status(classification=classification, activity=activity,
                  written_agreement=written_agreement, agreed_days=agreed_days,
                  evidence=evidence)


@dataclass(frozen=True)
class Deadline:
    """When the money has to be out, and why that date and not another."""

    on: date
    days: int
    limit_basis: str
    start: date
    start_basis: str

    @property
    def explanation(self) -> str:
        return (f"{self.days} days {START_LABELS[self.start_basis]} "
                f"({LIMIT_LABELS[self.limit_basis]})")


def deadline_for(bill_date: date, status: Status, *, th,
                 po_received_on: Optional[date] = None) -> Deadline:
    """The section 15 deadline for one bill, with its basis attached.

    **The start date is a proxy and the row says so.** Section 15 runs from
    acceptance or deemed acceptance, which this platform does not hold. The
    bill date is the ordinary stand-in and is usually conservative, but not
    always — a bill raised on dispatch for goods that arrive a fortnight later
    starts the real clock later than this does. Where a matched purchase order
    records an actual receipt, that is closer to acceptance and wins.

    The limit is fifteen days unless a written agreement has been *recorded* —
    not merely a Zoho term, which is a dropdown value and not an agreement.
    A written agreement above the statutory maximum is cut to it, and reported
    as ``CAPPED_LIMIT`` so the screen can show that what was signed and what
    the Act allows are two different numbers.
    """
    start, start_basis = ((po_received_on, PO_RECEIVED) if po_received_on
                          else (bill_date, BILL_DATE))

    if status.written_agreement is True and status.agreed_days is not None:
        days = int(status.agreed_days)
        limit_basis = AGREED_LIMIT
        if days > th.msme_max_agreed_days:
            days = int(th.msme_max_agreed_days)
            limit_basis = CAPPED_LIMIT
    else:
        days = int(th.msme_default_days)
        limit_basis = DEFAULT_LIMIT

    return Deadline(on=start + timedelta(days=days), days=days,
                    limit_basis=limit_basis, start=start, start_basis=start_basis)


# ── the financial year ──────────────────────────────────────────────────────
# The calendar belongs to the jurisdiction, not to this module — it lives in
# ``commercial/jurisdiction.py``, keyed by country. This module binds to INDIA
# by name rather than taking a parameter, because it *is* the Indian statute:
# a deployment that needed a different calendar would need a different statute
# module, not a different number here. The routers refuse to run these screens
# for a tenant whose country is not IN, which is what keeps the binding honest.


def fy_of(day: date) -> str:
    """``FY2026-27`` — the year a deduction would be disallowed *in*.

    The whole reason a watchlist beats an ageing report. A bill whose deadline
    falls on 28 March and one whose deadline falls on 2 April are the same
    number of days late and a year apart in consequence, and only this tells
    them apart.
    """
    return INDIA.fy_of(day)


def carry_cost(balance: float, tax_rate: Optional[float],
               carrying_rate: float) -> Optional[float]:
    """What a year's disallowance actually costs, in rupees.

    ``None`` when the owner has not set a tax rate — an unknown cost is
    reported as unknown, never as zero. The same rule ``ai/telemetry`` applies
    to an unreported token count.

    **Not ``balance × tax_rate``.** The disallowance moves a deduction from one
    year to the next; it does not delete it. What the business is out of pocket
    is the money it pays in tax a year early, which is one year's carry on that
    tax at the rate the organization already publishes for its own capital.
    """
    if tax_rate is None:
        return None
    return round(float(balance) * float(tax_rate) * float(carrying_rate), 2)


@dataclass(frozen=True)
class OpenBill:
    """An unpaid bill, at the grain the cliff applies to.

    Per bill and not per supplier: the deduction is disallowed bill by bill, so
    a supplier with one bill inside the limit and one outside is two different
    facts and a single aggregated row would state neither.
    """

    vendor_id: Optional[str]
    external_ref: str
    number: Optional[str]
    bill_date: date
    due_date: Optional[date]
    balance: float


@dataclass(frozen=True)
class WatchRow:
    vendor_id: Optional[str]
    vendor_name: str
    bill_ref: str
    bill_number: Optional[str]
    bill_date: date
    zoho_due_date: Optional[date]
    deadline: Deadline
    days_remaining: int
    balance: float
    fy: str
    scope: str
    status: Status
    #: The deduction that moves if this is still unpaid at the year end. The
    #: balance itself — a fact, not an estimate.
    amount_at_risk: float
    #: The estimated cost of that, or None when no tax rate is set.
    estimated_carry_cost: Optional[float]

    @property
    def already_past(self) -> bool:
        return self.days_remaining < 0

    def to_dict(self) -> dict:
        return {
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "bill_ref": self.bill_ref,
            "bill_number": self.bill_number,
            "bill_date": self.bill_date.isoformat(),
            "zoho_due_date": (self.zoho_due_date.isoformat()
                              if self.zoho_due_date else None),
            "deadline": self.deadline.on.isoformat(),
            "deadline_days": self.deadline.days,
            "deadline_basis": self.deadline.limit_basis,
            "deadline_start": self.deadline.start.isoformat(),
            "deadline_start_basis": self.deadline.start_basis,
            "deadline_explanation": self.deadline.explanation,
            "days_remaining": self.days_remaining,
            "already_past": self.already_past,
            "balance": self.balance,
            "financial_year": self.fy,
            "scope": self.scope,
            "scope_label": SCOPE_LABELS[self.scope],
            "classification": self.status.classification,
            "enterprise_activity": self.status.activity,
            "evidence": self.status.evidence,
            "status_captured_at": (self.status.captured_at.isoformat()
                                   if self.status.captured_at else None),
            "amount_at_risk": self.amount_at_risk,
            "estimated_carry_cost": self.estimated_carry_cost,
        }


def watchlist(bills: Iterable[OpenBill], statuses: dict[str, Status],
              names: dict[str, str], *, as_of: date, th,
              po_receipts: Optional[dict[str, date]] = None) -> dict:
    """Open bills approaching or past their section 15 deadline.

    Out-of-scope suppliers are dropped: this is a worklist, and a row nobody
    can act on is noise. Unknown-status suppliers are **kept**, in their own
    band, because "we cannot tell" is the finding rather than the absence of
    one.

    The horizon bounds how far ahead it looks. Anything already past its
    deadline is always included regardless of horizon — a passed cliff does not
    stop being one because it happened a while ago.
    """
    po_receipts = po_receipts or {}
    horizon = as_of + timedelta(days=int(th.msme_watch_horizon_days))
    current_fy = fy_of(as_of)

    rows: list[WatchRow] = []
    for bill in bills:
        if bill.balance <= 0:
            continue
        status = statuses.get(bill.vendor_id or "", NO_STATUS)
        scope = status.scope
        if scope == OUT_OF_SCOPE:
            continue

        deadline = deadline_for(bill.bill_date, status, th=th,
                                po_received_on=po_receipts.get(bill.external_ref))
        if deadline.on > horizon:
            continue

        rows.append(WatchRow(
            vendor_id=bill.vendor_id,
            vendor_name=names.get(bill.vendor_id or "", "Supplier not on record"),
            bill_ref=bill.external_ref,
            bill_number=bill.number,
            bill_date=bill.bill_date,
            zoho_due_date=bill.due_date,
            deadline=deadline,
            days_remaining=(deadline.on - as_of).days,
            balance=bill.balance,
            fy=fy_of(deadline.on),
            scope=scope,
            status=status,
            amount_at_risk=bill.balance,
            estimated_carry_cost=carry_cost(bill.balance, th.effective_tax_rate,
                                            th.carrying_cost_annual_pct),
        ))

    # This financial year first, then the nearest deadline, then the largest
    # balance. Absolute rather than relative to whatever else is on the list,
    # so a row does not move because an unrelated one appeared — the same rule
    # the decision queue follows.
    rows.sort(key=lambda r: (r.fy != current_fy, r.days_remaining, -r.balance))

    confirmed = [r for r in rows if r.scope == IN_SCOPE]
    gaps = [r for r in rows if r.scope == UNKNOWN]
    this_fy = [r for r in confirmed if r.fy == current_fy]

    return {
        "as_of": as_of.isoformat(),
        "financial_year": current_fy,
        "horizon_days": int(th.msme_watch_horizon_days),
        "rows": [r.to_dict() for r in rows],
        "confirmed": {
            "bills": len(confirmed),
            "amount_at_risk": round(sum(r.balance for r in confirmed), 2),
            "amount_at_risk_this_fy": round(sum(r.balance for r in this_fy), 2),
            "already_past": sum(1 for r in confirmed if r.already_past),
        },
        # Reported beside the confirmed total and never folded into it. Adding
        # them would assert an exposure nobody has established; omitting them
        # would report a reassuring number computed from ignorance.
        "gaps": {
            "bills": len(gaps),
            "suppliers": len({r.vendor_id for r in gaps}),
            "amount_if_protected": round(sum(r.balance for r in gaps), 2),
            "note": ("These suppliers have no MSME status on record, so whether "
                     "the 45-day rule reaches them is unknown. The amount shown "
                     "is what would be at risk if it does — it is not counted in "
                     "the confirmed total above."),
        },
        # Stated on the response rather than left to the reader, because every
        # date on this screen rests on it.
        "basis_note": (
            "Section 15 runs from acceptance or deemed acceptance of the goods, "
            "which this platform does not hold. Each row states the date it "
            "counted from: the recorded goods receipt where a purchase order "
            "carries one, and otherwise the bill date. Figures are a "
            "deterministic reading of your own records, not tax advice."),
        "tax_rate_set": th.effective_tax_rate is not None,
    }


@dataclass(frozen=True)
class VendorSpend:
    """What one supplier is worth knowing about, for the capture backlog."""

    vendor_id: str
    spend: float
    bills: int
    settled_past_limit: int
    settled_total: int


def capture_backlog(spends: Iterable[VendorSpend], statuses: dict[str, Status],
                    names: dict[str, str], *, limit: int = 25) -> dict:
    """Which suppliers are worth chasing an MSME status for, in order.

    Status has to be collected by a person, one supplier at a time, and a book
    has hundreds of them — so "go and find out for everybody" is advice nobody
    follows and the feature dies there. This ranks the question instead.

    The suppliers whose status is worth knowing are the ones already being paid
    slowly *and* being paid a lot: spend decides how much is at stake and the
    late share decides how likely it is to matter. Multiplying them puts a
    supplier who is always paid on time near the bottom whatever the spend,
    which is correct — their status changes nothing until behaviour does.
    """
    ranked = []
    for spend in spends:
        if statuses.get(spend.vendor_id, NO_STATUS).scope != UNKNOWN:
            continue
        late_share = (spend.settled_past_limit / spend.settled_total
                      if spend.settled_total else 0.0)
        ranked.append({
            "vendor_id": spend.vendor_id,
            "vendor_name": names.get(spend.vendor_id, "Supplier not on record"),
            "spend": round(spend.spend, 2),
            "bills": spend.bills,
            "settled_past_limit": spend.settled_past_limit,
            "settled_total": spend.settled_total,
            "late_share": round(late_share, 4),
            "priority": round(spend.spend * late_share, 2),
        })

    # Spend breaks a tie so the order is total rather than dependent on the
    # order rows arrived in — a backlog that reshuffles between two runs over
    # the same data is one nobody works through.
    ranked.sort(key=lambda r: (-r["priority"], -r["spend"], r["vendor_id"]))

    # The cap is reported, not applied silently. A truncated list that says
    # nothing about the truncation reads as "these are all of them", and this
    # one is a *coverage* list — the reader's next question is exactly how much
    # of the book is still unestablished.
    return {
        "suppliers": ranked[:limit],
        "shown": min(limit, len(ranked)),
        "unestablished": len(ranked),
        "unestablished_spend": round(sum(r["spend"] for r in ranked), 2),
    }
