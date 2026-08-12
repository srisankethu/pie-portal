"""The cash conversion cycle, and the four things it must refuse to say.

The arithmetic is the easy half. ``DIO + DSO − DPO`` over positions that are all
present is a division and two additions, and a test that only checked it would
pass on a module that quietly filled every gap with the nearest plausible number
— which is the failure mode this measure invites, because a missing leg leaves a
hole in a chart and somebody always wants the chart filled.

So the claims under test are, in order of how expensive it would be to get them
wrong:

1. **A month with no stock observation has no DIO, and therefore no CCC.** Not
   the previous month's shelf, not the nearest one, not a shortened window.
2. **Applied credit is subtracted from a reconstructed receivable** — and only
   from the reconstruction. The receivables fold reads ``InvoiceDoc.balance``,
   which Zoho has already netted, so subtracting there too would count it twice.
3. **Below the cost-coverage floor there is no COGS**, so no DIO and no DPO.
   Summing cost over the costed lines alone is a smaller number that looks real.
4. **Each entity is its own balance sheet.** Two books are two series, never one
   pooled cycle, and a record whose master names no company is counted out.

Plus the boundary that decides how far back any of it may be believed: an
application against an invoice the platform does not hold proves a receivable
existed that cannot be reconstructed, and every month whose window opens before
that is UNKNOWN rather than optimistic.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.insight import cycle
from app.domain import models

#: A month end well inside a year, so ``months_back`` never straddles a boundary
#: that a reader has to check by hand.
AS_OF = date(2026, 6, 30)

SLS, FOURU = "cx_sls", "cx_4u"
BOOKS = {SLS: "SLS Engineers", FOURU: "4U Precision"}

TH = CommercialThresholds()


def _months(n: int = 4) -> list[date]:
    """The month ends the default series covers, oldest first."""
    from app.commercial.insight import periods
    return [p.end for p in periods.months_back(AS_OF, n)]


def _sold(book: str, when: date, revenue: str, cogs: str | None) -> cycle.Sold:
    return cycle.Sold(book=book, date=when, revenue=Decimal(revenue),
                      cogs=None if cogs is None else Decimal(cogs))


def _daily_sales(book: str, start: date, end: date, *, per_day: str = "1000",
                 cogs: str | None = "750") -> list[cycle.Sold]:
    """One costed line a day, so every window has a denominator.

    Cost of sales is the denominator of two of the three legs, so a fixture that
    only sold in one month would make most of the series UNKNOWN for a reason
    that has nothing to do with what a test is asking about.
    """
    out, day = [], start
    while day <= end:
        out.append(_sold(book, day, per_day, cogs))
        day += timedelta(days=1)
    return out


def _monthly_invoices(book: str, first: date, *, amount: str = "100000",
                      prefix: str = "inv") -> list[cycle.Document]:
    """One invoice on the 10th of every month from ``first`` to ``AS_OF``.

    A book whose only invoice predates the trailing window has nothing billed
    *in* it, and DSO is correctly withheld — which is the right behaviour and
    the wrong fixture for a test about anything else. Invoicing every month is
    also what a distributor does.
    """
    out: list[cycle.Document] = []
    when = date(first.year, first.month, 10)
    while when <= AS_OF:
        out.append(cycle.Document(book=book, date=when,
                                  ref=f"{prefix}-{when.isoformat()}",
                                  total=Decimal(amount)))
        when = date(when.year + when.month // 12, when.month % 12 + 1, 10)
    return out


def _settled(invoices: list[cycle.Document], *, after_days: int = 30,
             skip_last: int = 0, part: str | None = None) -> list[cycle.Applied]:
    """A full receipt ``after_days`` after each invoice, bar the last few.

    ``skip_last`` leaves the newest invoices open, which is what makes a
    receivable position non-zero; ``part`` settles the first skipped one only
    partly, so a test can name an exact balance.
    """
    out: list[cycle.Applied] = []
    settled = invoices[:len(invoices) - skip_last] if skip_last else invoices
    for doc in settled:
        out.append(cycle.Applied(book=doc.book,
                                 on=doc.date + timedelta(days=after_days),
                                 document_ref=doc.ref, amount=doc.total))
    if part is not None and skip_last:
        doc = invoices[len(invoices) - skip_last]
        out.append(cycle.Applied(book=doc.book,
                                 on=doc.date + timedelta(days=5),
                                 document_ref=doc.ref, amount=Decimal(part)))
    return out


def _held(book: str, on: date, units: str = "100",
          unit_cost: str | None = "500") -> cycle.Held:
    return cycle.Held(book=book, on=on, units=Decimal(units),
                      unit_cost=None if unit_cost is None else Decimal(unit_cost))


def _build(**kw) -> dict:
    base = dict(invoices=[], receipts=[], credits=[], bills=[],
                bill_payments=[], sold=[], held=[], books=BOOKS, as_of=AS_OF,
                thresholds=TH, months=4)
    base.update(kw)
    return cycle.build(**base)


def _entity(result: dict, connection_id: str) -> dict:
    return next(e for e in result["entities"]
                if e["connection_id"] == connection_id)


def _month(entity: dict, ends_on: date) -> dict:
    return next(m for m in entity["months"] if m["ends_on"] == ends_on.isoformat())


# ── a complete book, so the arithmetic has somewhere to stand ───────────────
@pytest.fixture()
def whole_book() -> dict:
    """One entity with every leg backed, over the full series.

    Invoices and bills from a year before the window opens, so the
    reliable-from boundary is never what a test about arithmetic trips over.
    Stock observed on the last day of every month.
    """
    start = date(2025, 1, 1)
    ends = _months(4)
    return _build(
        invoices=[cycle.Document(book=SLS, date=start + timedelta(days=30 * i),
                                 ref=f"inv{i}", total=Decimal("100000"))
                  for i in range(18)],
        receipts=[cycle.Applied(book=SLS, on=start + timedelta(days=30 * i + 45),
                                document_ref=f"inv{i}", amount=Decimal("100000"))
                  for i in range(15)],
        bills=[cycle.Document(book=SLS, date=start + timedelta(days=30 * i),
                              ref=f"bill{i}", total=Decimal("60000"))
               for i in range(18)],
        bill_payments=[cycle.Applied(book=SLS,
                                     on=start + timedelta(days=30 * i + 40),
                                     document_ref=f"bill{i}",
                                     amount=Decimal("60000"))
                       for i in range(15)],
        sold=_daily_sales(SLS, start, AS_OF),
        held=[_held(SLS, end) for end in ends],
    )


def test_the_cycle_is_the_three_legs_and_nothing_else(whole_book):
    """DIO + DSO − DPO, to the tenth of a day the response rounds to."""
    entity = _entity(whole_book, SLS)
    stated = [m for m in entity["months"] if m["ccc"] is not None]
    assert stated, entity["months"]
    for month in stated:
        assert month["ccc"] == pytest.approx(
            round(month["dio"] + month["dso"] - month["dpo"], 1), abs=0.05)


def test_each_leg_divides_its_position_by_its_own_window(whole_book):
    """The identity a reader has to be able to check by hand.

    Positions and denominators are both on the row, so a leg that used a
    different window — a month instead of the trailing quarter, say — shows up
    here as a factor of three rather than as a slightly odd-looking chart.
    """
    month = _month(_entity(whole_book, SLS), _months(4)[-1])
    days = month["window"]["days"]

    assert month["dso"] == pytest.approx(
        month["receivables"] / month["billed"] * days, abs=0.05)
    assert month["dpo"] == pytest.approx(
        month["payables"] / month["cogs"] * days, abs=0.05)
    assert month["dio"] == pytest.approx(
        month["inventory"] / month["cogs"] * days, abs=0.05)


# ── 1. inventory the platform never observed ────────────────────────────────
def test_a_month_with_no_stock_observation_has_no_dio_and_no_ccc(whole_book):
    """The claim the whole module is bounded by.

    Zoho holds no stock history, so a month nobody looked at cannot be
    recovered. The nearest observation is not a stand-in for it — an inventory
    leg drawn flat through unobserved months reads as stability, which is the
    one thing absence cannot support.
    """
    ends = _months(4)
    # The same book, with the middle month's observation removed and every
    # other input untouched.
    result = _build(
        invoices=[cycle.Document(book=SLS, date=date(2025, 1, 1), ref="inv0",
                                 total=Decimal("100000"))],
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in ends if end != ends[1]],
    )
    entity = _entity(result, SLS)

    missing = _month(entity, ends[1])
    assert missing["dio"] is None
    assert missing["ccc"] is None
    assert "dio" in missing["unknown"]
    assert "no stock" in missing["why"]["dio"].lower()

    # And the neighbours are unaffected, which is what makes the gap a gap
    # rather than a truncation.
    assert _month(entity, ends[2])["dio"] is not None


def test_an_unobserved_month_does_not_borrow_the_previous_shelf(whole_book):
    """A stronger form of the above: the position itself stays absent.

    A carried-forward value would show up as an `inventory` figure on a month
    with no `stock_observed_on`, which is the shape the mistake actually takes
    — the refusal is remembered and the number is filled in anyway.
    """
    entity = _entity(_build(
        invoices=[cycle.Document(book=SLS, date=date(2025, 1, 1), ref="inv0",
                                 total=Decimal("100000"))],
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, _months(4)[0])],
    ), SLS)

    for month in entity["months"][1:]:
        assert month["stock_observed_on"] is None
        assert month["inventory"] is None


def test_an_observation_of_items_with_no_purchase_rate_is_not_a_valuation():
    """Stock counted but not priced is not a shelf worth anything.

    Valuing it at zero would shorten the inventory leg, which flatters the
    cycle — the same direction every other absence in this module is careful
    about.
    """
    end = _months(4)[-1]
    entity = _entity(_build(
        invoices=[cycle.Document(book=SLS, date=date(2025, 1, 1), ref="inv0",
                                 total=Decimal("100000"))],
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end, unit_cost=None)],
    ), SLS)

    month = _month(entity, end)
    assert month["dio"] is None
    assert month["items_unvalued"] == 1
    assert month["items_valued"] == 0


def test_nothing_on_the_shelf_is_worth_nothing_rather_than_unknown():
    """A zero count and a blank rate are different answers.

    An item with no stock is genuinely worth nothing whatever its rate says;
    an item with stock and no rate cannot be valued. Collapsing the two would
    either lose real months or invent them.
    """
    assert _held(SLS, AS_OF, units="0", unit_cost=None).value == Decimal(0)
    assert _held(SLS, AS_OF, units="5", unit_cost=None).value is None
    assert _held(SLS, AS_OF, units="5", unit_cost="20").value == Decimal(100)


# ── 2. the credit-note term ─────────────────────────────────────────────────
def test_applied_credit_is_subtracted_from_a_reconstructed_receivable():
    """The term that has no source without ``CreditNoteApplication``.

    Left out, the receivable is overstated by exactly the credit issued, which
    lengthens DSO — in the direction that makes collection look worse, and the
    cycle look longer. Both are wrong, and both are avoidable from rows the
    platform already holds.
    """
    invoices = _monthly_invoices(SLS, date(2025, 1, 1))
    june = invoices[-1]
    common = dict(
        invoices=invoices,
        # Everything settled a month later, bar June's — which is 30% paid, so
        # ₹70,000 of it is still outstanding on the 30th.
        receipts=_settled(invoices, skip_last=1, part="30000"),
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in _months(4)],
    )
    without = _month(_entity(_build(**common), SLS), _months(4)[-1])
    with_credit = _month(_entity(_build(
        credits=[cycle.Applied(book=SLS, on=date(2026, 6, 15),
                               document_ref=june.ref, amount=Decimal("20000"))],
        **common), SLS), _months(4)[-1])

    assert without["receivables"] == 70000.0
    assert with_credit["receivables"] == 50000.0
    assert with_credit["dso"] < without["dso"]


def test_the_reconstruction_never_touches_the_receivables_fold(session):
    """The mistake this module could most easily introduce, pinned structurally.

    ``state/reducers/receivables`` reads ``InvoiceDoc.balance``, which Zoho has
    *already* netted applied credit into. This module subtracts credit again
    because it starts from ``total``, not from ``balance`` — correct there, and
    a double subtraction anywhere the fold can see it.

    So: no reducer handles a credit-note event, and the receivables state has no
    credit field. Asserted here as well as in ``test_credit_notes`` because the
    two would break for different reasons and only one of them is next door.
    """
    from app.state import events as ev
    from app.state import reducers  # noqa: F401  (register every reducer)
    from app.state.engine import REDUCERS

    handled = {kind for reducer in REDUCERS.values() for kind in reducer.handles}
    assert not any("CREDIT" in kind for kind in handled), sorted(handled)
    assert ev.RECEIVABLE_RECORDED in handled


# ── 3. cost coverage ────────────────────────────────────────────────────────
def test_thin_cost_coverage_withholds_cogs_and_both_legs_that_need_it():
    """Adding up the costed lines alone is a smaller number that looks real.

    It would stretch DIO and DPO — inventory and payables divided by a cost of
    sales that is missing part of itself — and the reader has no way to see it.
    The platform's own ``min_cost_coverage`` already decides where this line
    falls, so it is reused rather than a second floor being invented.
    """
    start = date(2025, 1, 1)
    # One costed line in five, well under the 60% floor.
    sold = []
    day = start
    i = 0
    while day <= AS_OF:
        sold.append(_sold(SLS, day, "1000", "750" if i % 5 == 0 else None))
        day += timedelta(days=1)
        i += 1

    entity = _entity(_build(
        invoices=_monthly_invoices(SLS, start),
        bills=_monthly_invoices(SLS, start, amount="60000", prefix="bill"),
        sold=sold, held=[_held(SLS, end) for end in _months(4)]), SLS)

    month = _month(entity, _months(4)[-1])
    assert month["cogs"] is None
    assert month["cost_coverage"] == pytest.approx(0.2, abs=0.02)
    assert month["dio"] is None and month["dpo"] is None
    assert month["ccc"] is None
    # DSO is unaffected: its denominator is what was billed, not what it cost.
    assert month["dso"] is not None


def test_a_window_that_sold_nothing_has_no_cycle_rather_than_an_infinite_one():
    """Dividing by an empty quarter produces a number, and it is not a cycle."""
    entity = _entity(_build(
        invoices=[cycle.Document(book=SLS, date=date(2025, 1, 1), ref="inv0",
                                 total=Decimal("100000"))],
        held=[_held(SLS, end) for end in _months(4)]), SLS)

    for month in entity["months"]:
        assert month["cogs"] is None
        assert month["ccc"] is None


# ── 4. one series per legal entity ──────────────────────────────────────────
def test_two_books_are_two_series_and_never_one_pooled_cycle():
    """A cycle read across entities merges balance sheets that never merged.

    The same customer in two books is two legal relationships, and a composite
    over both belongs to no company that files anything. There is deliberately
    no total.
    """
    start = date(2025, 1, 1)
    slow = _monthly_invoices(FOURU, start, amount="400000", prefix="f")
    result = _build(
        invoices=_monthly_invoices(SLS, start, prefix="s") + slow,
        # SLS collects; 4U does not. Two books, two cycles, and the difference
        # is the whole reason they are not pooled.
        receipts=_settled(_monthly_invoices(SLS, start, prefix="s")),
        sold=_daily_sales(SLS, start, AS_OF) + _daily_sales(FOURU, start, AS_OF),
        held=[_held(SLS, end) for end in _months(4)]
             + [_held(FOURU, end) for end in _months(4)])

    assert {e["connection_id"] for e in result["entities"]} == {SLS, FOURU}
    assert "total" not in result and "pooled" not in result
    sls = _month(_entity(result, SLS), _months(4)[-1])
    fouru = _month(_entity(result, FOURU), _months(4)[-1])
    # One month outstanding against eighteen: the two positions are nothing
    # like each other, and a pooled series would show neither.
    assert sls["receivables"] == 100000.0
    assert fouru["receivables"] == 400000.0 * len(slow)
    assert sls["dso"] < fouru["dso"]


def test_a_record_whose_master_names_no_company_is_counted_out_not_filed():
    """An unattributable row belongs to no balance sheet, so it enters none.

    Putting it in the first book, or in a fourth "unknown" entity, would make
    one company's cycle depend on which rows failed to resolve.
    """
    result = _build(
        invoices=[
            cycle.Document(book=SLS, date=date(2025, 1, 1), ref="s1",
                           total=Decimal("100000")),
            cycle.Document(book="", date=date(2025, 1, 1), ref="orphan",
                           total=Decimal("900000")),
        ],
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in _months(4)])

    assert result["unattributed"]["invoices"] == 1
    assert {e["connection_id"] for e in result["entities"]} == {SLS, FOURU}
    assert _month(_entity(result, SLS), _months(4)[-1])["receivables"] == 100000.0


# ── the trustworthiness boundary ────────────────────────────────────────────
def test_a_payment_against_an_unknown_invoice_moves_the_boundary_forward():
    """The evidence that a receivable existed which cannot be reconstructed.

    ``PaymentApplication`` stores the invoice's own date precisely so a payment
    can settle an invoice from before the sync window. Every one of those is
    proof of a balance this replay is blind to, so the months whose window opens
    before it are UNKNOWN rather than understated-and-drawn.
    """
    settled_late = date(2026, 3, 15)
    entity = _entity(_build(
        invoices=_monthly_invoices(SLS, date(2024, 1, 1)),
        receipts=[cycle.Applied(book=SLS, on=settled_late,
                                document_ref="an-invoice-we-never-read",
                                amount=Decimal("500000"))],
        sold=_daily_sales(SLS, date(2024, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in _months(4)]), SLS)

    assert entity["receivables_reliable_from"] == (
        settled_late + timedelta(days=1)).isoformat()
    assert entity["counts"]["receivable_applications_unmatched"] == 1

    # March's own window opens in January, before the boundary: no DSO.
    assert _month(entity, date(2026, 3, 31))["dso"] is None
    # June's opens in April, after it.
    assert _month(entity, date(2026, 6, 30))["dso"] is not None


def test_an_unmatched_application_is_never_subtracted():
    """Subtracting a payment for an invoice that was never added goes negative.

    A negative receivable is not a small error, it is a position that cannot
    exist — and once the boundary hides the affected months, it would be a
    position nobody sees until the boundary moves.
    """
    entity = _entity(_build(
        invoices=_monthly_invoices(SLS, date(2024, 1, 1)),
        receipts=[cycle.Applied(book=SLS, on=date(2024, 2, 1),
                                document_ref="not-ours", amount=Decimal("9000000"))],
        sold=_daily_sales(SLS, date(2024, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in _months(4)]), SLS)

    assert all(m["receivables"] >= 0 for m in entity["months"])


def test_a_book_with_no_invoices_states_no_dso_at_all():
    """Zero is not a receivable position, it is an absent one."""
    entity = _entity(_build(
        sold=_daily_sales(SLS, date(2025, 1, 1), AS_OF),
        held=[_held(SLS, end) for end in _months(4)]), SLS)

    assert entity["receivables_reliable_from"] is None
    assert all(m["dso"] is None for m in entity["months"])
    assert all("no invoice is on record" in m["why"]["dso"]
               for m in entity["months"])


# ── what the response has to say about itself ───────────────────────────────
def test_the_response_names_the_two_absences_that_bound_it():
    """Stock history that cannot exist, and supplier credit that is not read.

    Both shorten the cycle if forgotten, and both are the reader's business.
    """
    result = _build()
    series = {u["series"] for u in result["unavailable"]}
    assert "inventory_before_the_first_observation" in series
    assert "credit_notes_from_suppliers" in series


def test_the_basis_states_the_tax_mismatch_on_the_payable_leg():
    """Gross payables over net cost of sales reads longer than it is.

    Correcting it would mean inventing a tax-adjusted cost. Stating it is what
    stops the figure being compared against a published ratio computed on a
    different basis and the difference being read as performance.
    """
    assert "gross" in _build()["basis"]["tax"]
    assert "understated" in _build()["basis"]["tax"]


def test_every_month_that_withholds_a_leg_says_why():
    """A blank a reader cannot explain is a blank they will assume is a bug."""
    entity = _entity(_build(
        invoices=[cycle.Document(book=SLS, date=date(2025, 1, 1), ref="inv0",
                                 total=Decimal("100000"))]), SLS)

    for month in entity["months"]:
        for leg in month["unknown"]:
            assert month["why"].get(leg), (leg, month)


# ── over HTTP, at the role the payable side needs ───────────────────────────
def _token(api_client, email: str) -> str:
    return api_client.post("/api/v1/auth/login", json={
        "email": email, "password": "change-me-now"}).json()["token"]


def test_the_cycle_is_manager_and_owner_only(api_client):
    """Scoped like ``/supply`` and ``/cashflow``, for the same reason.

    Two of three legs are denominated in what stock cost. Stripping them would
    leave a composite that answers nothing, so the endpoint is refused rather
    than half-served.
    """
    path = "/api/v1/insight/cash-cycle"
    for email, expected in (("r.nair@pie.example", 403),
                            ("m.rao@pie.example", 200),
                            ("s.menon@pie.example", 200)):
        r = api_client.get(path, headers={
            "Authorization": f"Bearer {_token(api_client, email)}"})
        assert r.status_code == expected, (email, r.text)


def test_the_endpoint_stamps_a_thresholds_version(api_client):
    """`_envelope` exists to do this; a response that 500s stamps nothing.

    Shallow on purpose: the arithmetic is covered above through the builder's
    own signature, and what that cannot catch is a call site that never runs.
    """
    r = api_client.get("/api/v1/insight/cash-cycle", headers={
        "Authorization": f"Bearer {_token(api_client, 's.menon@pie.example')}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thresholds_version"]
    assert body["empty_reason"]           # nothing connected on a seeded book
    assert body["definition"]["ccc"]


@pytest.fixture()
def two_books(engine):
    """A client over a database holding two connected companies of real rows.

    ``api_client`` builds its own empty database, which is right for the scoping
    tests above and useless for this one: what needs covering here is the part
    the builder's own tests structurally *cannot* reach — resolving a customer,
    a vendor and an item into the connected company whose balance sheet the row
    belongs to. That resolution only exists in the router, and only runs against
    rows.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.config import settings
    from app.db import get_session
    from app.routers import insight, platform_auth
    from app.seed import ensure_org_and_users

    org = settings.DEFAULT_ORG_ID
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    with maker() as s:
        ensure_org_and_users(s)
        for connection_id, label, zoho in ((SLS, "SLS Engineers", "60001"),
                                           (FOURU, "4U Precision", "60002")):
            s.add(models.ZohoConnection(connection_id=connection_id,
                                        organization_id=org, label=label,
                                        zoho_organization_id=zoho))
            s.add(models.Customer(customer_id=f"cus_{connection_id}",
                                  organization_id=org, external_id=f"z{zoho}",
                                  name=f"{label} customer", source_ref={},
                                  connector="zoho", connection_id=connection_id))
            s.add(models.Product(product_id=f"prd_{connection_id}",
                                 organization_id=org, external_id=f"p{zoho}",
                                 name=f"{label} insert", source_ref={},
                                 connector="zoho", connection_id=connection_id))
            for i, when in enumerate(_monthly_invoices(SLS, date(2025, 1, 1))):
                s.add(models.InvoiceDoc(
                    invoice_id=f"inv_{connection_id}_{i}", organization_id=org,
                    external_ref=f"e{zoho}-{i}",
                    customer_id=f"cus_{connection_id}", date=when.date,
                    status="sent", total=Decimal("100000"),
                    balance=Decimal("100000"), source_ref={}))
                s.add(models.SalesTxn(
                    sales_txn_id=f"tx_{connection_id}_{i}", organization_id=org,
                    external_ref=f"t{zoho}-{i}",
                    customer_id=f"cus_{connection_id}",
                    product_id=f"prd_{connection_id}", date=when.date,
                    qty=Decimal("10"), unit_price=Decimal("1000"),
                    line_revenue=Decimal("10000"), source_ref={}))
                s.add(models.CostRecord(
                    cost_record_id=f"cr_{connection_id}_{i}",
                    organization_id=org, external_ref=f"c{zoho}-{i}",
                    product_id=f"prd_{connection_id}", date=when.date,
                    qty=Decimal("10"), unit_cost=Decimal("700"), source_ref={}))
            s.add(models.StockSnapshot(
                stock_snapshot_id=f"ss_{connection_id}", organization_id=org,
                product_id=f"prd_{connection_id}", as_of=date(2026, 6, 10),
                on_hand=Decimal("40"), purchase_rate=Decimal("700"),
                tracked=True, source_ref={}))
        s.commit()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def test_one_book_syncing_later_does_not_hide_another_books_observation(session):
    """A month one company was observed in must not be lost to the other's clock.

    ``_stock_days`` narrows a million-row snapshot table down to the handful of
    days a series needs. Narrowing it to the last day per month *across the
    organization* discards a book's own observation whenever the other book was
    observed later that month — and the symptom is a month reported as never
    observed that was observed, which is a refusal manufactured by the query.

    Found by running the screen rather than by reading the code: two books on
    schedules a day apart drew five months of stock for one of them and two for
    the other, from the same number of snapshots.
    """
    from app.routers.insight import _stock_days

    org = "org_stock_days"
    for connection_id, day in ((SLS, date(2026, 3, 30)), (FOURU, date(2026, 3, 2))):
        session.add(models.Product(product_id=f"p_{connection_id}",
                                   organization_id=org, external_id=connection_id,
                                   name="Insert", source_ref={},
                                   connector="zoho", connection_id=connection_id))
        session.add(models.StockSnapshot(
            stock_snapshot_id=f"s_{connection_id}", organization_id=org,
            product_id=f"p_{connection_id}", as_of=day, on_hand=Decimal("10"),
            purchase_rate=Decimal("100"), tracked=True, source_ref={}))
    session.flush()

    days = _stock_days(session, org, date(2026, 1, 1), date(2026, 6, 30))

    # Both, not just the later one. Each book then takes the latest of its own
    # rows inside the month, so a day chosen for one is invisible to the other.
    assert days == [date(2026, 3, 2), date(2026, 3, 30)]


def test_the_endpoint_files_each_row_under_its_own_connected_company(two_books):
    """Two connections of identical rows come back as two identical series.

    Identical on purpose: if the router filed everything under one book, one
    entity would carry double and the other nothing — a failure that a fixture
    with different amounts could hide behind a plausible-looking difference.
    """
    token = _token(two_books, "s.menon@pie.example")
    r = two_books.get("/api/v1/insight/cash-cycle",
                      headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["empty_reason"] is None
    assert body["sources_differ"] is True
    entities = {e["connection_id"]: e for e in body["entities"]}
    assert set(entities) == {SLS, FOURU}
    assert [e["label"] for e in body["entities"]] == ["4U Precision",
                                                      "SLS Engineers"]

    latest = {k: e["months"][-1] for k, e in entities.items()}
    assert latest[SLS]["receivables"] == latest[FOURU]["receivables"] > 0
    assert latest[SLS]["cogs"] == latest[FOURU]["cogs"] > 0
    # The stock leg exists for the month that was observed and for no other,
    # which is the module's central refusal reaching the wire.
    assert latest[SLS]["stock_observed_on"] == "2026-06-10"
    assert entities[SLS]["months"][-2]["dio"] is None
    assert body["unattributed"]["invoices"] == 0
