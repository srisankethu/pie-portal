"""Whose product an item is: a bill first, the item's manufacturer second.

Two facts answer the question and they fail in opposite directions. A purchase
bill is the stronger evidence but has a horizon — ``cost_records`` only reaches
back as far as the sync window, so anything sold out of older stock is
attributable to nobody. The item master's manufacturer has no horizon but is missing
wherever nobody tagged it. Chained, the residue is items that are both untagged
and bought outside the window.

The claims worth pinning are the refusals and the boundary, because both make
the output smaller and both are what a later change will be tempted to soften:

* the **bill always wins** where there is one, because it is what reconciles
  against a principal's own statement;
* an **ambiguous maker matches nothing** — a wrong match silently merges two
  principals, which is far worse than an extra column with the right name;
* an **unmatched manufacturer is still a principal**, not a dropped row, or the
  fallback would fail to do the one job it exists for;
* the **purchase side never sees a manufacturer**, so a target on what this book buys
  cannot drift from what the principal invoiced.
"""
from __future__ import annotations

import pytest

import dbsupport
from app.commercial import principals as pr

VENDORS = {
    "v_ken": "Kennametal India Limited",
    "v_noga": "Noga Engineering & Technology Ltd",
    "v_yg": "YG-1 Industries (India) Pvt Ltd",
}


def _purchase(product_id: str, vendor_id: str, amount: float) -> pr.Purchase:
    return pr.Purchase(product_id=product_id, vendor_id=vendor_id, amount=amount)


# ── the order, which is the whole design ─────────────────────────────────────
def test_a_purchase_bill_beats_the_item_masters_manufacturer():
    """The bill is a transaction; the maker is an attribute somebody typed.

    Where they disagree the bill is right about who was paid — which is the
    question — even though the maker may be right about whose logo is on the box.
    """
    resolved = pr.resolve_all({"p1": "YG1"}, [_purchase("p1", "v_ken", 100.0)],
                              VENDORS)
    assert resolved["p1"].principal_id == "v_ken"
    assert resolved["p1"].source == pr.BY_BILL
    # And no inference is claimed, because none was made.
    assert resolved["p1"].matched_on is None


def test_the_manufacturer_answers_where_no_bill_reaches():
    """The case the fallback exists for: stock older than the sync window.

    There is no bill because the purchase predates what was synced, and without
    the manufacturer this item would be attributed to nobody at all.
    """
    resolved = pr.resolve_all({"p1": "Kennametal India Limited"}, [], VENDORS)
    assert resolved["p1"].principal_id == "v_ken"
    assert resolved["p1"].source == pr.BY_MANUFACTURER
    assert resolved["p1"].matched_on == "kennametalindia"


def test_an_item_with_neither_is_an_explicit_refusal_not_a_missing_key():
    """A caller must be able to tell "not attributed" from "not a product"."""
    resolved = pr.resolve_all({"p1": None, "p2": "   "}, [], VENDORS)
    assert set(resolved) == {"p1", "p2"}
    assert all(r.source == pr.BY_NOTHING and not r.known for r in resolved.values())


def test_an_item_bought_from_two_suppliers_goes_wholly_to_the_larger():
    resolved = pr.resolve_all(
        {"p1": None},
        [_purchase("p1", "v_ken", 30.0), _purchase("p1", "v_noga", 70.0),
         _purchase("p1", "v_ken", 10.0)],
        VENDORS)
    assert resolved["p1"].principal_id == "v_noga"


# ── matching a manufacturer to a vendor, timidly ────────────────────────────────────
def test_legal_form_is_not_identity():
    """One principal written by two people is one principal."""
    assert pr.normalise_name("KENNAMETAL INDIA LIMITED") == "kennametalindia"
    assert pr.normalise_name("Kennametal India Pvt. Ltd.") == "kennametalindia"
    assert pr.normalise_name("Kennametal India Private Limited") == "kennametalindia"


def test_punctuation_is_not_identity_either():
    """``YG1`` and ``YG-1`` are one maker; the hyphen is typing, not fact."""
    assert pr.normalise_name("YG1") == pr.normalise_name("YG-1") == "yg1"


def test_a_country_word_is_left_alone_because_it_can_be_a_real_distinction():
    """"Sandvik" and "Sandvik India" may be two supply relationships on
    different terms. Collapsing them is exactly the wrong-match failure this
    module is built to avoid, so the normaliser does not touch them."""
    assert pr.normalise_name("Sandvik") != pr.normalise_name("Sandvik India")


def test_a_short_maker_matches_a_longer_vendor_name_by_prefix():
    """``NOGA`` is the maker; the vendor row is the trading entity."""
    assert pr.match_manufacturer("NOGA", VENDORS) == ("v_noga", "noga")
    assert pr.match_manufacturer("YG1", VENDORS) == ("v_yg", "yg1")


def test_an_ambiguous_maker_matches_nothing_rather_than_guessing():
    """The failure that matters is not a missed match — that costs a separate
    column with the right name on it — but a wrong one, which merges two
    suppliers into a number nobody can reconcile."""
    ambiguous = {"v_a": "Noga Engineering Ltd", "v_b": "Noga Precision Pvt Ltd"}
    assert pr.match_manufacturer("NOGA", ambiguous) is None


def test_two_vendor_rows_with_one_trading_name_resolve_to_neither():
    """A re-sync that duplicated a contact is not something to guess through."""
    duplicated = {"v_a": "Kennametal India Ltd", "v_b": "KENNAMETAL INDIA LIMITED"}
    assert pr.match_manufacturer("Kennametal India", duplicated) is None


def test_a_name_too_short_to_be_evidence_does_not_prefix_match():
    """Two characters would let a maker claim any vendor beginning with them."""
    assert pr.match_manufacturer("YG", {"v_yg": "YG-1 Industries"}) is None
    # An exact match on a short name is still fine — it is not an inference.
    assert pr.match_manufacturer("YG", {"v_yg": "YG"}) == ("v_yg", "yg")


@pytest.mark.parametrize("manufacturer", ["", "   ", None, "!!!", "---"])
def test_a_name_with_nothing_in_it_matches_nothing(manufacturer):
    assert pr.match_manufacturer(manufacturer, VENDORS) is None


def test_an_unmatched_manufacturer_is_still_a_principal():
    """Dropping it would understate the book against a principal we
    demonstrably sell — and those items are the whole reason for the fallback."""
    resolved = pr.resolve_all({"p1": "Emuge Franken"}, [], VENDORS)
    got = resolved["p1"]
    assert got.known and got.source == pr.BY_MANUFACTURER
    assert got.principal_id == "maker:emugefranken"
    assert got.name == "Emuge Franken"          # as typed, not as normalised
    assert got.is_maker_only


def test_a_maker_that_matched_a_vendor_is_not_flagged_as_maker_only():
    """``is_maker_only`` asks whether a real supplier row backs this principal,
    not how the item reached it — a matched maker names a vendor this book has
    a record for, and a screen counting unbacked principals must not count it."""
    resolved = pr.resolve_all({"p1": "NOGA"}, [], VENDORS)
    assert resolved["p1"].source == pr.BY_MANUFACTURER
    assert not resolved["p1"].is_maker_only


def test_one_maker_is_matched_once_however_many_items_carry_it(monkeypatch):
    """A catalogue has thousands of items and, on the live masters, six makers."""
    calls = {"n": 0}
    real = pr.match_manufacturer

    def counting(manufacturer, vendors):
        calls["n"] += 1
        return real(manufacturer, vendors)

    monkeypatch.setattr(pr, "match_manufacturer", counting)
    pr.resolve_all({f"p{i}": "NOGA" for i in range(50)}, [], VENDORS)
    assert calls["n"] == 1


# ── the boundary: the purchase side never sees a manufacturer ───────────────────────
def test_dominant_vendor_reads_only_bills():
    """The function anything reconciling against a principal's own statement
    calls. It takes purchases and nothing else — there is no argument through
    which a maker could reach it, which is why the separation survives the next
    screen that needs a vendor."""
    assert "manufacturers" not in pr.dominant_vendor.__code__.co_varnames
    assert pr.dominant_vendor([_purchase("p1", "v_ken", 5.0)]) == {"p1": "v_ken"}


def test_a_cost_line_with_no_vendor_attributes_nothing():
    assert pr.dominant_vendor([_purchase("p1", "", 5.0)]) == {}


def test_no_maker_key_can_ever_appear_in_the_purchase_side_answer():
    """``dominant_vendor`` returns vendor ids off cost rows, and a cost row
    cannot carry a synthetic key — so a target on what this book *buys* is
    provably free of maker inference."""
    got = pr.dominant_vendor([_purchase("p1", "v_ken", 5.0),
                              _purchase("p2", "v_noga", 5.0)])
    assert not any(v.startswith(pr.MAKER_PREFIX) for v in got.values())


# ── naming, and reporting what the attribution is worth ──────────────────────
def test_maker_principals_are_named_where_the_vendor_table_cannot_name_them():
    resolved = pr.resolve_all({"p1": "Emuge Franken", "p2": "NOGA"}, [], VENDORS)
    names = pr.names_of(resolved, VENDORS)
    assert names["maker:emugefranken"] == "Emuge Franken"
    # And the real vendors survive untouched.
    assert names["v_ken"] == "Kennametal India Limited"


def test_coverage_is_reported_in_money_as_well_as_items():
    """A count says "one of two unattributed" whether that item sells nothing or
    carries most of the book. The revenue split is the number to read."""
    resolved = pr.resolve_all(
        {"p1": None, "p2": "NOGA", "p3": None},
        [_purchase("p1", "v_ken", 10.0)], VENDORS)
    report = pr.coverage_report(resolved, {"p1": 100.0, "p2": 300.0, "p3": 600.0})

    assert report["from_bill"] == 1 and report["from_manufacturer"] == 1
    assert report["unattributed"] == 1
    # Two of three items are attributed, but only 40% of the money.
    assert report["resolved_share"] == pytest.approx(2 / 3)
    assert report["attributed_share"] == pytest.approx(0.4)


def test_the_maker_only_share_counts_principals_no_bill_corroborates():
    """The number to read before trusting anything built on this."""
    resolved = pr.resolve_all(
        {"p1": "Emuge Franken", "p2": "NOGA"}, [], VENDORS)
    report = pr.coverage_report(resolved, {"p1": 250.0, "p2": 750.0})
    # Both came from the item master; only Emuge has no vendor row behind it.
    assert report["revenue_from_manufacturer"] == pytest.approx(1000.0)
    assert report["maker_only_share"] == pytest.approx(0.25)


def test_a_book_that_has_not_traded_has_an_unknown_share_not_a_perfect_one():
    """Zero over zero is not 100%, and a screen that renders it as one is
    claiming complete attribution of nothing."""
    resolved = pr.resolve_all({"p1": None}, [], VENDORS)
    report = pr.coverage_report(resolved, {})
    assert report["attributed_share"] is None
    assert report["maker_only_share"] is None


def test_an_empty_catalogue_reports_a_shape_rather_than_failing():
    report = pr.coverage_report({}, {})
    assert report["products"] == 0
    assert report["resolved_share"] is None


# ── the wiring, end to end ───────────────────────────────────────────────────
#
# The unit tests above pin the resolver. These pin that the screens actually
# call it — a reverted call site is a silent regression, because a book whose
# bills all resolve looks identical either way and only older stock exposes it.


@pytest.fixture()
def client():
    from datetime import date
    from decimal import Decimal

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.domain import models
    from app.routers import insight, platform_auth
    from app.seed import ensure_org_and_users

    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    org = "org_pie"
    s.add(models.Vendor(vendor_id="v_ken", organization_id=org,
                        external_id="ev1", name="Kennametal India Limited"))
    # Three items, one per state the resolver distinguishes.
    s.add_all([
        models.Product(product_id="p-billed", organization_id=org,
                       external_id="e1", name="CNMG 120408", hsn="82071900",
                       manufacturer="YG1", active=True, source_ref={}),
        models.Product(product_id="p-maker", organization_id=org,
                       external_id="e2", name="Old stock holder", hsn="84662000",
                       manufacturer="Kennametal India Pvt Ltd", active=True,
                       source_ref={}),
        models.Product(product_id="p-neither", organization_id=org,
                       external_id="e3", name="Unknown part", hsn="82071900",
                       active=True, source_ref={}),
    ])
    # Vendor and items on disk before the bill references them — the unit of
    # work does not order inserts across unrelated mappers.
    s.flush()
    # Only the first has a bill. The second is the case that matters: stock
    # bought before the sync window, which the manufacturer is the only witness to.
    s.add(models.CostRecord(cost_record_id="c1", organization_id=org,
                            external_ref="b1:1", product_id="p-billed",
                            vendor_id="v_ken", date=date(2026, 1, 5),
                            qty=10, unit_cost=100))
    # All three have sold, so the grids compute rather than returning the
    # empty-book refusal — and so the coverage figures have money behind them.
    s.add(models.Customer(customer_id="c1", organization_id=org,
                          external_id="ec1", name="Precision Motors"))
    s.flush()
    for pid, revenue in (("p-billed", "500"), ("p-maker", "300"),
                         ("p-neither", "200")):
        s.add(models.SalesTxn(
            organization_id=org, external_ref=f"inv-{pid}", customer_id="c1",
            product_id=pid, date=date(2026, 6, 1), qty=Decimal("1"),
            unit_price=Decimal(revenue), line_revenue=Decimal(revenue),
            source_ref={"record_id": f"inv-{pid}"}))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _auth(client, email="m.rao@pie.example"):
    from app.seed import SEED_PASSWORD
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_the_catalogue_says_which_evidence_placed_each_supplier(client):
    """Both read as a supplier name and they are not equally strong. Somebody
    correcting a catalogue has to be able to tell a bill from an attribute."""
    head = _auth(client)
    listing = client.get("/api/v1/insight/catalogue?unplaced_only=false",
                         headers=head).json()
    by_id = {i["product_id"]: i for i in listing["items"]}

    assert by_id["p-billed"]["supplier"] == "Kennametal India Limited"
    assert by_id["p-billed"]["supplier_source"] == pr.BY_BILL

    # The manufacturer named a vendor this book has a record for, so it resolves to
    # that vendor's name rather than to the string on the item.
    assert by_id["p-maker"]["supplier"] == "Kennametal India Limited"
    assert by_id["p-maker"]["supplier_source"] == pr.BY_MANUFACTURER

    assert by_id["p-neither"]["supplier"] is None
    assert by_id["p-neither"]["supplier_source"] == pr.BY_NOTHING


def test_the_manufacturer_is_carried_through_to_the_screen_that_corrects_it(client):
    head = _auth(client)
    listing = client.get("/api/v1/insight/catalogue?unplaced_only=false",
                         headers=head).json()
    row = next(i for i in listing["items"] if i["product_id"] == "p-maker")
    assert row["manufacturer"] == "Kennametal India Pvt Ltd"


def test_every_screen_built_on_this_reports_what_it_could_attribute(client):
    """Not optional decoration. A grid of principals drawn over a third of the
    book looks exactly like one drawn over all of it."""
    head = _auth(client)
    for path in ("/api/v1/insight/catalogue?unplaced_only=false",
                 "/api/v1/insight/mix?by=vendor",
                 "/api/v1/insight/dependency"):
        body = client.get(path, headers=head).json()
        assert "principals" in body, path
        assert body["principals"]["from_bill"] == 1, path
        assert body["principals"]["from_manufacturer"] == 1, path
        assert body["principals"]["unattributed"] == 1, path


def test_a_purchase_target_counts_bills_only_and_no_maker_reaches_it(client):
    """The number that has to agree with the principal's own statement.

    ``p-maker`` is attributed to Kennametal by the item master and has sold
    ₹300, but no bill bought it. A purchase-basis target measures what this
    book *paid* Kennametal — ₹1,000 — and that sale must not appear in it, or
    the figure drifts from the invoices Kennametal actually raised and the one
    that is wrong at year end is ours.
    """
    head = _auth(client)
    r = client.put("/api/v1/insight/targets", headers=head, json={
        "vendor_id": "v_ken", "period_start": "2026-01-01",
        "period_end": "2026-12-31", "basis": "PURCHASE", "amount": "5000"})
    assert r.status_code in (200, 201), r.text

    body = client.get("/api/v1/insight/dependency", headers=head).json()
    ken = next(v for v in body["vendors"]["rows"] if v["entity_id"] == "v_ken")
    assert ken["target"]["basis"] == "PURCHASE"
    # 10 × 100 off the one bill. Not 1,300, and not 1,800.
    assert ken["target"]["actual"] == pytest.approx(1000.0)
    # The sales side did see the maker, which is the whole point of the split.
    assert ken["downstream_revenue"] == pytest.approx(800.0)
