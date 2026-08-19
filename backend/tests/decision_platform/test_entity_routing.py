"""The entity-routing determinants: five facts side by side, and no answer.

The arithmetic here is trivial — counting rows and adding figures the module
was handed — so almost nothing below tests a sum. What is worth pinning is the
set of things this screen must never grow into, because every one of them is
what somebody asks for the first time they look at it:

1. **No recommendation, no preferred entity, no ranking, no score.** Placement
   between legal entities moves the GST place of supply and the income-tax
   position of two companies. ``insight/msme`` states the rule this inherits —
   a wrong margin costs a deal, a wrong tax position is the operator's
   liability — so the only key on this response that names a recommendation is
   the one refusing to make one.
2. **Nothing pooled.** ``insight/cycle`` refuses a group total and
   ``insight/capital`` inherits it. Here it bites hardest: the screen exists to
   choose *between* entities, so a combined row would be the one line on it
   that answers nothing.
3. **The 194Q section stays silent while the gate is unconfirmed**, and its
   counts come back ``None`` rather than ``0`` — "nobody has crossed" and "we
   are not allowed to say" are different answers and the zero is the
   reassuring one.
4. **A country nobody recorded reads as not set, never as India.** Three
   states, and the two refusals name different gaps because they need
   different things done about them.
5. **The capital refusal propagates with its own sentence.** A month before the
   receivable/payable reliability boundary has *unknown* capital employed, and
   a blank cell where that sentence should be would be read as nil.
6. **Absent, zero and over stay three states on the credit column.** The
   headroom figure covers the accounts that have a limit and says how many; a
   book where nobody has recorded one states no headroom rather than a total of
   nothing.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterator

import pytest

from app.commercial.config import CommercialThresholds
from app.commercial.insight import absence, credit, msme, routing
from app.config import settings
from app.domain import models
from app.seed import SEED_PASSWORD

# The two-connection book next door, imported rather than copied — the same
# reason ``test_capital`` borrows it. It is the only fixture that exercises the
# router's resolution of a customer, a vendor and a bill into the connected
# company whose balance sheet they belong to, and this endpoint leans on
# exactly that resolution.
from .test_cash_cycle import two_books  # noqa: F401
from .test_capital import FOURU, SLS
from .test_jurisdiction_gate import _api, _set_country

AS_OF = date(2026, 6, 30)
TH = CommercialThresholds()

MANAGER = "m.rao@pie.example"
OWNER = "s.menon@pie.example"
SALESPERSON = "r.nair@pie.example"


def _exposure(customer_id: str, outstanding: str, limit: str | None,
              overdue: str = "0") -> credit.Exposure:
    return credit.exposure(
        credit.Balance(customer_id=customer_id,
                       outstanding=Decimal(outstanding),
                       overdue=Decimal(overdue)),
        None if limit is None else Decimal(limit))


def _book(connection_id: str = "cx_sls", label: str = "SLS Engineers",
          **kw) -> routing.Book:
    return routing.Book(connection_id=connection_id, label=label, **kw)


def _capital_view(*entities: dict) -> dict:
    return {"entities": list(entities)}


def _capital_entity(connection_id: str, *, latest: dict | None,
                    months: list[dict] | None = None,
                    months_stated: int = 0) -> dict:
    return {"connection_id": connection_id, "label": connection_id,
            "months": months or [], "latest": latest,
            "months_stated": months_stated,
            "receivables_reliable_from": None, "payables_reliable_from": None,
            "stock_observed_from": None}


def _assemble(**kw) -> dict:
    base = dict(books=[_book()], country="IN", capital_view=None,
                withholding_views={}, msme_views={}, as_of=AS_OF,
                thresholds=TH)
    base.update(kw)
    return routing.assemble(**base)


def _keys(node: object) -> Iterator[str]:
    """Every key anywhere in the payload, however deeply nested."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


# ── 1. no recommendation, under any name ────────────────────────────────────
#: Every word a recommendation would plausibly be spelled with. Substring rather
#: than exact match, because the failure this guards against is somebody adding
#: ``suggested_entity`` or ``placement_score``, not somebody adding a field
#: called ``recommendation``.
ADVISORY_WORDS = ("recommend", "prefer", "suggest", "rank", "score", "best",
                  "advis", "should", "winner", "choice", "chosen", "verdict",
                  "optimal")


def test_no_recommendation_or_preferred_entity_field_exists():
    """The only key naming a recommendation is the one that refuses to make one.

    Asserted over the whole payload rather than the top level, because the
    tempting place to put it is on each entity — a ``suitability`` beside the
    capital block reads as a natural extension of the column next to it.
    """
    body = _assemble(books=[_book(), _book("cx_4u", "4U Precision")])
    named = [k for k in _keys(body)
             if any(word in k.lower() for word in ADVISORY_WORDS)]
    assert named == ["no_recommendation"], named
    assert "does not" in body["no_recommendation"]
    assert "accountant" in body["no_recommendation"]


def test_the_refusal_to_recommend_is_filed_as_permanent_rather_than_buildable():
    """Deliberately not a backlog item.

    ``BUILDABLE`` means engineering has not got to it yet, and filing this
    there would put "add a recommendation engine" on somebody's list — which is
    the outcome the module exists to prevent. The precedent is
    ``capital.annualised_return_on_capital``: PERMANENT because the operation is
    wrong, not because an input is missing.
    """
    entry = next(u for u in _assemble()["unavailable"]
                 if u["series"] == "recommended_entity")
    assert entry["kind"] == absence.PERMANENT


def test_entities_come_back_alphabetically_and_not_by_any_determinant():
    """Order is not a rank. Sorting by capital employed would be the
    recommendation this module refuses, arriving as a list somebody reads
    top-down."""
    rich = _capital_entity("cx_z", latest={"month": "2026-06",
                                           "capital_employed": 9_000_000.0},
                           months_stated=3)
    poor = _capital_entity("cx_a", latest={"month": "2026-06",
                                           "capital_employed": 11.0},
                           months_stated=3)
    body = _assemble(
        books=[_book("cx_z", "Zenith Tools"), _book("cx_a", "Apex Carbide")],
        capital_view=_capital_view(rich, poor))
    assert [e["label"] for e in body["entities"]] == ["Apex Carbide",
                                                      "Zenith Tools"]


# ── 2. nothing pooled ───────────────────────────────────────────────────────
#: The names a group row would be given. ``insight/capital`` refuses one and so
#: does this; the check is on key names because a pooled figure would arrive as
#: a key beside ``entities`` rather than as a row inside it.
POOLING_WORDS = ("group", "pool", "combined", "consolidat", "total",
                 "aggregate", "organisation", "organization", "all_entities")


def test_no_pooled_total_exists_under_any_name_one_would_be_given():
    body = _assemble(books=[_book(), _book("cx_4u", "4U Precision")])
    named = [k for k in _keys(body)
             if any(word in k.lower() for word in POOLING_WORDS)]
    assert named == [], named


def test_two_books_are_two_rows_and_the_top_level_carries_no_figure():
    """The only place a number appears is inside an entity.

    Pinned as an exact key set rather than as an absence, because the way a
    group total arrives is somebody adding one helpful summary field — and a
    test that only banned today's names would not catch tomorrow's.
    """
    body = _assemble(books=[_book(), _book("cx_4u", "4U Precision")])
    assert len(body["entities"]) == 2
    assert set(body) == {"as_of", "financial_year", "determinants", "entities",
                         "legend", "no_recommendation", "unavailable"}
    refusal = next(u for u in body["unavailable"]
                   if u["series"] == "group_routing_summary")
    assert refusal["kind"] == absence.PERMANENT


# ── 3. the 194Q gate ────────────────────────────────────────────────────────
def test_the_194q_section_stays_silent_while_the_gate_is_unconfirmed():
    """Inherited from ``insight/withholding``, not re-decided here.

    The counts are ``None`` and not ``0``: a zero would report that no supplier
    is near the threshold, which is the benign reading of an unanswered
    question — the failure CLAUDE.md §1 names three times.
    """
    unconfirmed = {"gate_confirmed": False, "crossings": [],
                   "note": "Confirm the turnover gate in Settings."}
    block = _assemble(withholding_views={"cx_sls": unconfirmed}
                      )["entities"][0]["withholding"]

    assert block["gate_confirmed"] is False
    assert block["crossings"] == []
    assert block["crossed"] is None
    assert block["approaching"] is None
    assert block["blocked_by"] == "Confirm the turnover gate in Settings."
    # The statutory line itself still travels: it is the policy in force rather
    # than a reading of this book, and without it a reader cannot tell how far
    # the silence extends.
    assert block["party_threshold"] == float(TH.s194q_party_threshold)


def test_a_book_with_no_withholding_view_is_silent_the_same_way():
    """A book with no purchases is not a book that has crossed nothing."""
    block = _assemble()["entities"][0]["withholding"]
    assert block["gate_confirmed"] is False
    assert block["crossed"] is None
    assert "not in this platform" in block["blocked_by"]


def test_a_confirmed_gate_reports_the_crossings_the_module_found():
    confirmed = {
        "gate_confirmed": True, "threshold": 5_000_000.0,
        "financial_year": "FY2026-27",
        "crossings": [{"vendor_id": "v1", "crossed": True, "approaching": False},
                      {"vendor_id": "v2", "crossed": False, "approaching": True}],
        "basis_note": "GST-inclusive until the tax split is ingested.",
    }
    block = _assemble(withholding_views={"cx_sls": confirmed}
                      )["entities"][0]["withholding"]
    assert block["gate_confirmed"] is True
    assert block["crossed"] == 1
    assert block["approaching"] == 1
    assert block["blocked_by"] is None


# ── 4. the jurisdiction, in three states ────────────────────────────────────
def test_a_country_nobody_recorded_reads_as_not_set_rather_than_india():
    body = _assemble(country=None)
    block = body["entities"][0]["jurisdiction"]

    assert block["state"] == routing.NOT_SET
    assert block["country"] is None
    # The calendar is India's, not a universal one, so nothing is dated into a
    # statutory year for a tenant whose calendar this platform cannot name.
    assert block["fy_start_month"] is None
    assert body["financial_year"] is None
    assert block["statutes"] == {"msme": None, "withholding": None}
    assert "not set" in block["why"]


def test_an_unsupported_country_names_a_different_gap_from_an_unset_one():
    """Two refusals, because they need different things done about them: one is
    a two-character code somebody types, the other is a statute review."""
    unset = _assemble(country=None)["entities"][0]["jurisdiction"]["why"]
    unsupported = _assemble(country="AE")["entities"][0]["jurisdiction"]["why"]

    assert unset != unsupported
    assert _assemble(country="AE")["entities"][0]["jurisdiction"]["state"] == \
        routing.UNSUPPORTED
    assert "AE" in unsupported


def test_an_unsupported_country_drops_the_gate_flag_rather_than_advising_on_it():
    """"Confirm your turnover in Settings" is advice that cannot help somebody
    the statute does not reach — the call ``/withholding-crossings`` already
    makes."""
    entity = _assemble(country="AE")["entities"][0]
    assert entity["withholding"]["applies"] is False
    assert "gate_confirmed" not in entity["withholding"]
    assert entity["msme"]["applies"] is False
    assert entity["msme"]["blocked_by"]


def test_a_supported_country_carries_its_calendar_and_its_statutes():
    body = _assemble(country="IN")
    block = body["entities"][0]["jurisdiction"]
    assert block["state"] == routing.SUPPORTED
    assert block["fy_start_month"] == 4
    assert block["statutes"] == {"msme": True, "withholding": True}
    assert block["why"] is None
    assert body["financial_year"] == "FY2026-27"


def test_the_jurisdiction_says_which_grain_it_was_recorded_at():
    """One country for three companies is a grain this platform does not hold,
    and the row says so rather than letting a reader infer an established
    per-entity fact from three rows agreeing."""
    body = _assemble(books=[_book(), _book("cx_4u", "4U Precision")])
    assert all(e["jurisdiction"]["recorded_on"] == "organization"
               for e in body["entities"])
    entry = next(u for u in body["unavailable"]
                 if u["series"] == "jurisdiction_per_connected_company")
    # BUILDABLE, not COLLECTABLE: there is no per-connection country column, so
    # filing it as a worklist item would point somebody at a blank that does
    # not exist.
    assert entry["kind"] == absence.BUILDABLE


# ── 5. the capital refusal, propagated ──────────────────────────────────────
def test_the_capital_refusal_propagates_rather_than_showing_a_bare_null():
    reason = ("Receivables cannot be reconstructed this far back: the window "
              "opens on 2026-01-01, before 2026-03-04.")
    view = _capital_view(_capital_entity(
        "cx_sls", latest=None,
        months=[{"month": "2026-05", "why": {}},
                {"month": "2026-06", "why": {"capital_employed": reason}}]))
    block = _assemble(capital_view=view)["entities"][0]["capital"]

    assert block["stated"] is False
    # No figure at all, rather than a nil one — a blank cell where this
    # sentence should be is read as capital of nothing.
    assert "capital_employed" not in block
    # The module's own sentence, not a second one written about the same gap.
    assert block["blocked_by"] == reason
    assert block["why"]["capital_employed"] == reason


def test_a_book_with_no_replayed_cycle_says_so_instead_of_reading_as_nil():
    block = _assemble(capital_view=_capital_view())["entities"][0]["capital"]
    assert block["stated"] is False
    assert "no capital position to state" in block["blocked_by"]


def test_a_stated_month_carries_the_readings_and_the_reasons_beside_them():
    """A return withheld because the window average was refused and one withheld
    because suppliers fund the whole cycle are different findings, so the
    module's ``why`` travels whole rather than filtered."""
    latest = {"month": "2026-06", "capital_employed": 1_250_000.0,
              "avg_capital_employed": None, "return_on_capital": None,
              "working_capital_per_rupee": None, "confidence": "MEASURED",
              "unknown": ["return_on_capital", "working_capital_per_rupee"],
              "why": {"avg_capital_employed": "1 of the 3 month ends..."}}
    block = _assemble(capital_view=_capital_view(
        _capital_entity("cx_sls", latest=latest, months_stated=1)
    ))["entities"][0]["capital"]

    assert block["stated"] is True
    assert block["capital_employed"] == 1_250_000.0
    assert block["return_on_capital"] is None
    assert block["unknown"] == ["return_on_capital", "working_capital_per_rupee"]
    assert block["why"]["avg_capital_employed"].startswith("1 of the 3")
    assert block["blocked_by"] is None


# ── 6. credit: absent, zero and over are three states ───────────────────────
def test_absent_zero_and_over_are_counted_apart_rather_than_collapsed():
    book = _book(exposures=(
        _exposure("unassessed", outstanding="400000", limit=None),
        _exposure("cash_only", outstanding="0", limit="0"),
        _exposure("comfortable", outstanding="100000", limit="1000000"),
        _exposure("breached", outstanding="900000", limit="500000",
                  overdue="200000"),
    ))
    block = _assemble(books=[book])["entities"][0]["credit"]

    assert block["accounts"] == 4
    assert block["no_limit_recorded"] == 1
    assert block["limit_of_zero"] == 1
    assert block["over"] == 1
    assert block["over_by"] == 400_000.0
    assert block["outstanding"] == 1_400_000.0
    assert block["overdue"] == 200_000.0


def test_headroom_covers_only_the_accounts_that_carry_a_limit_and_says_so():
    """The account nobody assessed contributes neither zero nor infinity.

    Its ₹4 lakh outstanding is in the outstanding figure — that is a fact — and
    it is absent from headroom, which is the number that would otherwise imply
    somebody had decided what this account may owe.
    """
    book = _book(exposures=(
        _exposure("unassessed", outstanding="400000", limit=None),
        _exposure("cash_only", outstanding="0", limit="0"),
        _exposure("comfortable", outstanding="100000", limit="1000000"),
    ))
    block = _assemble(books=[book])["entities"][0]["credit"]

    # 0 − 0 on the cash-only line, 1000000 − 100000 on the other. The
    # unassessed account's line is not in it at any value.
    assert block["headroom"] == 900_000.0
    assert block["headroom_speaks_for"] == 2
    assert "not read as unlimited or as zero" in block["note"]


def test_a_book_where_nobody_recorded_a_limit_states_no_headroom_at_all():
    """``None``, not ``0``. A zero would read as "no room left" on a book whose
    room nobody has decided."""
    book = _book(exposures=(
        _exposure("a", outstanding="400000", limit=None),
        _exposure("b", outstanding="10000", limit=None),
    ))
    block = _assemble(books=[book])["entities"][0]["credit"]
    assert block["headroom"] is None
    assert block["headroom_speaks_for"] == 0
    assert block["no_limit_recorded"] == 2


def test_a_book_with_no_customers_at_all_still_reports_its_zero_accounts():
    block = _assemble()["entities"][0]["credit"]
    assert block["accounts"] == 0
    assert block["headroom"] is None


# ── the MSME bands, and the coverage they rest on ───────────────────────────
def test_the_confirmed_and_gap_bands_are_passed_through_and_never_added():
    view = {"financial_year": "FY2026-27", "horizon_days": 60,
            "confirmed": {"bills": 2, "amount_at_risk": 300000.0,
                          "amount_at_risk_this_fy": 300000.0, "already_past": 1},
            "gaps": {"bills": 9, "suppliers": 4,
                     "amount_if_protected": 1_100_000.0, "note": "…"},
            "tax_rate_set": False, "basis_note": "…"}
    block = _assemble(msme_views={"cx_sls": view})["entities"][0]["msme"]

    assert block["confirmed"] == view["confirmed"]
    assert block["gaps"] == view["gaps"]
    # No third figure anywhere that could only have come from adding them.
    assert 1_400_000.0 not in [v for v in block.values()
                               if isinstance(v, (int, float))]


def test_supplier_coverage_travels_beside_the_exposure_it_qualifies():
    """A confirmed exposure of nil means little when nobody has classified a
    single supplier, so the two numbers only mean anything beside each other."""
    book = _book(supplier_scopes=(msme.IN_SCOPE, msme.UNKNOWN, msme.UNKNOWN,
                                  msme.OUT_OF_SCOPE))
    block = _assemble(books=[book])["entities"][0]["msme"]
    assert block["suppliers"] == {"in_scope": 1, "unknown": 2,
                                  "out_of_scope": 1, "suppliers": 4}


def test_a_book_with_no_bills_says_that_rather_than_reporting_nil_exposure():
    block = _assemble()["entities"][0]["msme"]
    assert block["applies"] is True
    assert "no payment deadline" in block["blocked_by"]
    assert "confirmed" not in block


# ── the taxonomy ────────────────────────────────────────────────────────────
def test_every_refusal_carries_a_kind_from_the_closed_set():
    for entry in _assemble()["unavailable"]:
        assert entry["kind"] in absence.KINDS, entry["series"]
        assert entry["reason"]


def test_the_legend_explains_every_determinant_it_names():
    body = _assemble()
    assert set(body["legend"]) == set(body["determinants"])
    assert set(body["determinants"]) == set(routing.DETERMINANTS)


# ── the endpoint ────────────────────────────────────────────────────────────
def _token(client, email: str) -> str:
    return client.post("/api/v1/auth/login", json={
        "email": email, "password": SEED_PASSWORD}).json()["token"]


def test_the_screen_is_manager_and_owner_only(api_client):
    """Scoped like ``/capital``, whose figures it carries. There is no version
    of this screen with the economics removed that still answers the question,
    so an honest 403 beats a page stripped to nothing."""
    for email, expected in ((SALESPERSON, 403), (MANAGER, 200), (OWNER, 200)):
        r = api_client.get("/api/v1/insight/entity-routing", headers={
            "Authorization": f"Bearer {_token(api_client, email)}"})
        assert r.status_code == expected, (email, r.text)
        if expected == 403:
            assert r.json()["detail"] == "Manager or owner role required"


def test_the_endpoint_files_each_book_separately_over_real_rows(two_books):
    """The router half, which the builder's own tests structurally cannot reach.

    Two connections of identical rows must come back as two rows carrying the
    same figures. If a bill or a customer were filed under one book, one entity
    would carry everything and the other nothing — which a fixture with
    different amounts could hide behind a plausible-looking difference.
    """
    r = two_books.get("/api/v1/insight/entity-routing", headers={
        "Authorization": f"Bearer {_token(two_books, OWNER)}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["empty_reason"] is None
    assert body["thresholds_version"]
    entities = {e["connection_id"]: e for e in body["entities"]}
    assert set(entities) == {SLS, FOURU}

    left, right = entities[SLS], entities[FOURU]
    assert left["credit"]["accounts"] == right["credit"]["accounts"] == 1
    assert left["capital"]["capital_employed"] == \
        right["capital"]["capital_employed"]
    # Every supplier of both books is unclassified, which is the finding rather
    # than an absence — the exposure figure beside it rests on it.
    assert left["msme"]["suppliers"] == {"in_scope": 0, "out_of_scope": 0,
                                         "unknown": 1, "suppliers": 1}
    assert left["msme"]["gaps"]["bills"] > 0
    assert left["msme"]["confirmed"]["amount_at_risk"] == 0
    # Nothing on this book is unattributed, so the caveat is present and nil
    # rather than absent — a screen that only mentioned coverage when it was
    # broken would be a screen nobody trusts when it says nothing.
    assert body["unattributed"] == {"bills": 0, "customers": 0, "suppliers": 0}


def test_the_gate_being_unconfirmed_leaves_every_books_194q_section_silent(
        two_books):
    """The default is unconfirmed, and this book has bills past the party
    threshold's neighbourhood — so the silence is a decision, not an accident
    of there being nothing to report."""
    body = two_books.get("/api/v1/insight/entity-routing", headers={
        "Authorization": f"Bearer {_token(two_books, OWNER)}"}).json()
    for entity in body["entities"]:
        assert entity["withholding"]["gate_confirmed"] is False
        assert entity["withholding"]["crossings"] == []
        assert entity["withholding"]["crossed"] is None


@pytest.mark.parametrize("country,expected", [(None, routing.NOT_SET),
                                              ("AE", routing.UNSUPPORTED),
                                              ("IN", routing.SUPPORTED)])
def test_the_endpoint_reads_the_country_off_the_organization(session, country,
                                                             expected):
    """End to end, because the three states only differ in what they refuse and
    a builder test cannot catch an endpoint that never asked.

    One connected company and nothing else on it: the jurisdiction column does
    not depend on a single synced document, and a fixture that needed one would
    be testing the sync rather than the gate.
    """
    client, token = _api(session)
    org = settings.DEFAULT_ORG_ID
    session.add(models.ZohoConnection(connection_id="cx_only",
                                      organization_id=org,
                                      label="SLS Engineers",
                                      zoho_organization_id="60001"))
    _set_country(session, country)
    session.flush()

    r = client.get("/api/v1/insight/entity-routing", headers=token(OWNER))
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["empty_reason"] is None
    entity = body["entities"][0]
    assert entity["jurisdiction"]["state"] == expected
    assert entity["jurisdiction"]["country"] == country
    # An Indian book gets its calendar; the other two get neither a calendar
    # nor a statutory year, because April to March is India's year and not a
    # universal one.
    assert body["financial_year"] == (None if expected != routing.SUPPORTED
                                      else entity["financial_year"])
    assert (entity["msme"]["applies"] is True) == (expected
                                                   == routing.SUPPORTED)
