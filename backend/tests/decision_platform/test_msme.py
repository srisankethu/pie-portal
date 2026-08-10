"""The 43B(h) watchlist, tested against the ways it would be wrong.

Each test here is a mistake the obvious implementation makes: defaulting to 45
days because Zoho said 45, treating an unclassified supplier as safe, counting
a registered trader as protected, sizing the cost as the whole tax, and losing
the financial-year boundary that makes the whole thing a cliff instead of an
ageing report.
"""
from __future__ import annotations

from datetime import date

from app.commercial.config import CommercialThresholds
from app.commercial.insight import msme
from app.domain.enums import EnterpriseActivity, MsmeClassification

TH = CommercialThresholds()
#: A book whose owner has set their rate, for the cost arithmetic only.
TAXED = CommercialThresholds(effective_tax_rate=0.25)

MICRO_MFR = msme.Status(classification=MsmeClassification.MICRO.value,
                        activity=EnterpriseActivity.MANUFACTURER.value)


def _bill(ref="b1", *, vendor="v1", on=date(2026, 3, 1), balance=100_000.0,
          due=None, number="BILL-1"):
    return msme.OpenBill(vendor_id=vendor, external_ref=ref, number=number,
                         bill_date=on, due_date=due, balance=balance)


# ── the deadline ────────────────────────────────────────────────────────────
def test_deadline_defaults_to_fifteen_days_when_no_written_agreement():
    deadline = msme.deadline_for(date(2026, 3, 1), MICRO_MFR, th=TH)

    assert deadline.days == 15
    assert deadline.on == date(2026, 3, 16)
    assert deadline.limit_basis == msme.DEFAULT_LIMIT


def test_written_agreement_of_sixty_days_is_capped_at_forty_five():
    status = msme.Status(classification=MsmeClassification.SMALL.value,
                         activity=EnterpriseActivity.MANUFACTURER.value,
                         written_agreement=True, agreed_days=60)

    deadline = msme.deadline_for(date(2026, 3, 1), status, th=TH)

    assert deadline.days == 45
    assert deadline.limit_basis == msme.CAPPED_LIMIT, (
        "the row must be able to say that what was agreed and what the Act "
        "allows are different numbers")


def test_a_written_agreement_inside_the_cap_is_used_as_agreed():
    status = msme.Status(classification=MsmeClassification.MICRO.value,
                         activity=EnterpriseActivity.SERVICE.value,
                         written_agreement=True, agreed_days=30)

    deadline = msme.deadline_for(date(2026, 3, 1), status, th=TH)

    assert (deadline.days, deadline.limit_basis) == (30, msme.AGREED_LIMIT)


def test_zoho_payment_terms_alone_do_not_establish_a_written_agreement():
    """The single most expensive mistake available here.

    ``written_agreement`` is None — nobody recorded an agreement — while an
    agreed_days sits on the record from some other source. Fifteen days must
    still apply, because a dropdown value is not a contract.
    """
    status = msme.Status(classification=MsmeClassification.MICRO.value,
                         activity=EnterpriseActivity.MANUFACTURER.value,
                         written_agreement=None, agreed_days=45)

    deadline = msme.deadline_for(date(2026, 3, 1), status, th=TH)

    assert deadline.days == 15
    assert deadline.limit_basis == msme.DEFAULT_LIMIT


def test_an_explicitly_absent_agreement_also_gets_fifteen_days():
    status = msme.Status(classification=MsmeClassification.MICRO.value,
                         activity=EnterpriseActivity.MANUFACTURER.value,
                         written_agreement=False, agreed_days=45)

    assert msme.deadline_for(date(2026, 3, 1), status, th=TH).days == 15


def test_po_received_on_wins_over_bill_date_and_says_so():
    deadline = msme.deadline_for(date(2026, 3, 1), MICRO_MFR, th=TH,
                                 po_received_on=date(2026, 3, 11))

    assert deadline.start == date(2026, 3, 11)
    assert deadline.start_basis == msme.PO_RECEIVED
    assert deadline.on == date(2026, 3, 26)
    assert "goods were received" in deadline.explanation


# ── who the rule reaches ────────────────────────────────────────────────────
def test_unknown_status_produces_a_gap_row_not_a_pass():
    built = msme.watchlist([_bill(balance=250_000.0)], {}, {"v1": "Acme"},
                           as_of=date(2026, 3, 10), th=TH)

    assert built["confirmed"]["bills"] == 0
    assert built["gaps"]["bills"] == 1
    assert built["gaps"]["amount_if_protected"] == 250_000.0
    assert built["confirmed"]["amount_at_risk"] == 0, (
        "an unestablished status must never be added to a confirmed total")


def test_medium_enterprise_is_out_of_scope():
    status = msme.Status(classification=MsmeClassification.MEDIUM.value,
                         activity=EnterpriseActivity.MANUFACTURER.value)

    assert status.scope == msme.OUT_OF_SCOPE
    built = msme.watchlist([_bill()], {"v1": status}, {"v1": "Acme"},
                           as_of=date(2026, 3, 10), th=TH)
    assert built["rows"] == []


def test_registered_trader_is_out_of_scope():
    """Udyam registration without the section 15 benefit.

    A large share of a cutting-tool distributor's suppliers are dealers, so
    getting this wrong would raise most of the list against parties the rule
    does not reach.
    """
    status = msme.Status(classification=MsmeClassification.SMALL.value,
                         activity=EnterpriseActivity.TRADER.value)

    assert status.scope == msme.OUT_OF_SCOPE


def test_a_protected_size_with_unknown_activity_stays_unknown():
    """The favourable reading is not the default one."""
    status = msme.Status(classification=MsmeClassification.MICRO.value,
                         activity=EnterpriseActivity.UNKNOWN.value)

    assert status.scope == msme.UNKNOWN


def test_a_checked_unregistered_supplier_is_out_of_scope_not_unknown():
    """"We looked and they are not registered" is an answer, and differs from
    "nobody looked" in the only way that matters."""
    status = msme.Status(classification=MsmeClassification.NOT_REGISTERED.value,
                         activity=EnterpriseActivity.UNKNOWN.value)

    assert status.scope == msme.OUT_OF_SCOPE


# ── the financial year ──────────────────────────────────────────────────────
def test_fy_banding_puts_31_march_and_1_april_in_different_years():
    assert msme.fy_of(date(2026, 3, 31)) == "FY2025-26"
    assert msme.fy_of(date(2026, 4, 1)) == "FY2026-27"


def test_amount_at_risk_this_fy_excludes_a_deadline_in_the_next_one():
    as_of = date(2026, 3, 20)
    bills = [
        # deadline 2026-03-26 — this FY
        _bill("b1", on=date(2026, 3, 11), balance=100_000.0),
        # deadline 2026-04-10 — next FY, still on the list, not in the total
        _bill("b2", on=date(2026, 3, 26), balance=900_000.0),
    ]

    built = msme.watchlist(bills, {"v1": MICRO_MFR}, {"v1": "Acme"},
                           as_of=as_of, th=TH)

    assert built["confirmed"]["amount_at_risk"] == 1_000_000.0
    assert built["confirmed"]["amount_at_risk_this_fy"] == 100_000.0


# ── the cost ────────────────────────────────────────────────────────────────
def test_carry_cost_is_one_year_on_the_tax_not_the_tax():
    """₹10L disallowed at 25% costs the carry on ₹2.5L, not ₹2.5L."""
    cost = msme.carry_cost(1_000_000.0, 0.25, 0.12)

    assert cost == 30_000.0
    assert cost != 250_000.0


def test_carry_cost_is_none_when_no_tax_rate_is_set():
    assert msme.carry_cost(1_000_000.0, None, 0.12) is None


def test_the_watchlist_reports_an_unset_tax_rate_rather_than_assuming_one():
    built = msme.watchlist([_bill()], {"v1": MICRO_MFR}, {"v1": "Acme"},
                           as_of=date(2026, 3, 10), th=TH)

    assert built["tax_rate_set"] is False
    assert built["rows"][0]["estimated_carry_cost"] is None
    assert built["rows"][0]["amount_at_risk"] == 100_000.0, (
        "the amount at risk is a fact and must survive an unset rate")


def test_the_cost_estimate_appears_once_a_rate_is_set():
    built = msme.watchlist([_bill()], {"v1": MICRO_MFR}, {"v1": "Acme"},
                           as_of=date(2026, 3, 10), th=TAXED)

    assert built["tax_rate_set"] is True
    assert built["rows"][0]["estimated_carry_cost"] == 3_000.0


# ── the list itself ─────────────────────────────────────────────────────────
def test_ranking_prefers_a_current_fy_deadline_over_a_larger_later_one():
    as_of = date(2026, 3, 20)
    bills = [
        _bill("big", on=date(2026, 3, 26), balance=5_000_000.0),   # next FY
        _bill("small", on=date(2026, 3, 11), balance=50_000.0),    # this FY
    ]

    built = msme.watchlist(bills, {"v1": MICRO_MFR}, {"v1": "Acme"},
                           as_of=as_of, th=TH)

    assert [r["bill_ref"] for r in built["rows"]] == ["small", "big"]


def test_a_passed_deadline_is_listed_however_old_it_is():
    """The horizon bounds how far *ahead* it looks. A cliff already gone over
    does not stop being one."""
    built = msme.watchlist([_bill(on=date(2025, 1, 1))], {"v1": MICRO_MFR},
                           {"v1": "Acme"}, as_of=date(2026, 3, 10), th=TH)

    assert built["rows"][0]["already_past"] is True
    assert built["rows"][0]["days_remaining"] < 0


def test_a_deadline_beyond_the_horizon_is_not_yet_a_decision():
    built = msme.watchlist([_bill(on=date(2026, 12, 1))], {"v1": MICRO_MFR},
                           {"v1": "Acme"}, as_of=date(2026, 3, 10), th=TH)

    assert built["rows"] == []


def test_a_settled_bill_is_not_on_the_list():
    built = msme.watchlist([_bill(balance=0.0)], {"v1": MICRO_MFR},
                           {"v1": "Acme"}, as_of=date(2026, 3, 10), th=TH)

    assert built["rows"] == []


def test_every_row_states_the_date_it_counted_from():
    built = msme.watchlist([_bill()], {"v1": MICRO_MFR}, {"v1": "Acme"},
                           as_of=date(2026, 3, 10), th=TH)
    row = built["rows"][0]

    assert row["deadline_start_basis"] == msme.BILL_DATE
    assert row["deadline_basis"] == msme.DEFAULT_LIMIT
    assert row["deadline_explanation"]
    assert "not tax advice" in built["basis_note"]


def test_watchlist_is_deterministic_for_a_fixed_as_of():
    bills = [_bill(f"b{i}", on=date(2026, 3, 1 + i), balance=1000.0 * i)
             for i in range(1, 6)]
    args = (bills, {"v1": MICRO_MFR}, {"v1": "Acme"})

    first = msme.watchlist(*args, as_of=date(2026, 3, 10), th=TH)
    second = msme.watchlist(*args, as_of=date(2026, 3, 10), th=TH)

    assert first == second


def test_a_bill_from_an_unresolved_supplier_is_kept_and_named_honestly():
    """A bill whose vendor the contact pull never returned is still money owed;
    it simply cannot be grouped. The same choice ingestion already makes."""
    orphan = msme.OpenBill(vendor_id=None, external_ref="b9", number="BILL-9",
                           bill_date=date(2026, 3, 1), due_date=None,
                           balance=10_000.0)

    built = msme.watchlist([orphan], {}, {}, as_of=date(2026, 3, 10), th=TH)

    assert built["gaps"]["bills"] == 1
    assert built["rows"][0]["vendor_name"] == "Supplier not on record"


# ── the capture backlog ─────────────────────────────────────────────────────
def test_capture_backlog_ranks_by_spend_times_slowness():
    spends = [
        # Big spend, always paid on time — status changes nothing today.
        msme.VendorSpend("prompt", spend=10_000_000.0, bills=40,
                         settled_past_limit=0, settled_total=40),
        # Smaller spend, chronically past the limit — this is the one to chase.
        msme.VendorSpend("slow", spend=2_000_000.0, bills=12,
                         settled_past_limit=9, settled_total=12),
    ]

    built = msme.capture_backlog(spends, {}, {"prompt": "P", "slow": "S"})

    assert [r["vendor_id"] for r in built["suppliers"]] == ["slow", "prompt"]


def test_capture_backlog_skips_suppliers_whose_status_is_already_known():
    spends = [msme.VendorSpend("known", spend=5_000_000.0, bills=20,
                               settled_past_limit=15, settled_total=20)]

    built = msme.capture_backlog(spends, {"known": MICRO_MFR}, {})

    assert built["suppliers"] == []
    assert built["unestablished"] == 0


def test_capture_backlog_order_is_total_rather_than_input_dependent():
    """Two suppliers with identical behaviour must not swap places between
    runs — a backlog that reshuffles is one nobody works through."""
    spends = [
        msme.VendorSpend("b", spend=1_000.0, bills=1, settled_past_limit=1,
                         settled_total=1),
        msme.VendorSpend("a", spend=1_000.0, bills=1, settled_past_limit=1,
                         settled_total=1),
    ]

    assert ([r["vendor_id"] for r in msme.capture_backlog(spends, {}, {})["suppliers"]]
            == [r["vendor_id"]
                for r in msme.capture_backlog(spends[::-1], {}, {})["suppliers"]])


def test_capture_backlog_reports_the_cap_rather_than_truncating_silently():
    """A coverage list that shows 25 of 112 and says nothing reads as "these
    are all of them" — which is the opposite of what a coverage list is for."""
    spends = [msme.VendorSpend(f"v{i}", spend=1_000.0 * i, bills=1,
                               settled_past_limit=1, settled_total=1)
              for i in range(1, 31)]

    built = msme.capture_backlog(spends, {}, {}, limit=5)

    assert built["shown"] == 5
    assert built["unestablished"] == 30
    assert built["unestablished_spend"] == sum(1_000.0 * i for i in range(1, 31))
