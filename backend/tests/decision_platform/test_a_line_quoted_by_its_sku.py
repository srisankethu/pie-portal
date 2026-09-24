"""A line quoted by the item master's own SKU names that item.

The case, from 4U PRECISION's books: the customer wrote ``22000865``, which is
the SKU of ``CNMG120408-UC-D2 YC0014`` — fifteen invoices, three bills. Two
separate things went wrong on the quote screen:

* **The line asked "which item is this?"** The engine resolves against the
  manufacturers' catalogues, which do not hold a distributor's SKUs, so it
  answered UNRESOLVED — and with the engine down, nothing at all. Nothing then
  asked this company's own master, which holds the code exactly.
* **The drawer said "not found in sales history"**, and the assessment said
  "first time for this customer and item" and "no purchase cost on record".
  Product lookup matched the portal id, the Zoho item id and the name, and
  never the SKU — although the sync writes it to ``item_connector_records``.

Engine-free on purpose: ``pie_service.resolve`` is stubbed, because the
behaviour under test is what happens *after* the engine answers, and a test
that needs pie-parser runs in neither a checkout without the submodule nor a
credential-less CI job — the two places a regression here has to be caught.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.commercial.quote_service import _resolve_products
from app.decisions.quote_support import _resolve_product
from app.domain import models
from app.identity import service as identity
from app.pie_service import Candidate, Resolution
from app.repositories import ReadModelRepository
from app.store import QuoteStore
from app.zoho import MockZoho

ORG = "org_sku"
FOUR_U, SLS = "conn_4u", "conn_sls"


def _item(s, pid, connection_id, name, sku, active=True):
    s.add(models.Product(product_id=pid, organization_id=ORG, connector="zoho",
                         connection_id=connection_id, external_id=f"z-{pid}",
                         name=name, active=active))
    s.flush()
    # The sync's own write path, so the SKU is stored exactly as a pull
    # stores it — normalised by ``identity.matchers.normalize_sku``.
    identity.ingest_item(s, ORG, connector="zoho", connection_id=connection_id,
                         external_id=f"z-{pid}", name=name, sku=sku,
                         description=name, local_id=pid)


@pytest.fixture()
def session():
    engine = dbsupport.fresh_engine()
    s = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                     future=True)()
    _item(s, "p_yc", FOUR_U, "CNMG120408-UC-D2 YC0014", "22000865")
    # Two items sharing one SKU: some masters put an HSN code there.
    _item(s, "p_h1", FOUR_U, "16X16X35X90 ALU POWER 3LF45", "82077090")
    _item(s, "p_h2", FOUR_U, "SPCL CHAMFER TOOL 16/15", "82077090")
    _item(s, "p_old", FOUR_U, "OLD DRILL 8.5", "30001234", active=False)
    # The same code in another company's master, meaning something else.
    _item(s, "p_sls", SLS, "SLS ITEM THAT SHARES A CODE", "55500055")
    s.commit()
    yield s
    s.close()


# ── the sales-history half ──────────────────────────────────────────────────
def test_a_sku_finds_the_product_its_history_is_filed_under(session):
    assert _resolve_products(session, ORG, ["22000865"])["22000865"].product_id == "p_yc"
    # Written the way people write it — separators are not identity.
    assert _resolve_products(session, ORG, [" 2200-0865 "])["2200-0865"].product_id == "p_yc"


def test_the_drawer_and_the_assessment_resolve_a_code_the_same_way(session):
    """``quote_support._resolve_product`` used to be a second copy of the
    matcher, and the copy is where the SKU was missing from both. One ref
    through either must name one product."""
    for ref in ("22000865", "CNMG120408-UC-D2 YC0014", "z-p_yc", "p_yc"):
        assert _resolve_product(session, ORG, ref).product_id == "p_yc", ref
        assert _resolve_products(session, ORG, [ref])[ref].product_id == "p_yc", ref


def test_a_sku_several_items_share_names_none_of_them(session):
    """Picking one would file another item's sales history under this line."""
    assert _resolve_products(session, ORG, ["82077090"])["82077090"] is None


# ── the "which item is this?" half ──────────────────────────────────────────
def test_the_master_names_the_one_active_item_a_code_is(session):
    repo = ReadModelRepository(session, ORG)
    hit = repo.exact_item("22000865", connection_id=FOUR_U)
    assert hit["name"] == "CNMG120408-UC-D2 YC0014"
    assert "_sku" not in hit
    # The SKU however the separators fall; the name as the master writes it.
    for code in ("2200-0865", "cnmg120408-uc-d2 yc0014"):
        assert repo.exact_item(code, connection_id=FOUR_U)["externalId"] == "z-p_yc"


@pytest.mark.parametrize("code, why", [
    ("82077090", "several items carry this SKU"),
    ("30001234", "the only item with it is inactive and cannot go on a document"),
    ("55500055", "it is another company's item"),
    ("2200086", "a prefix of a SKU is not that SKU"),
    ("   ", "there is no code"),
])
def test_the_master_answers_nothing_rather_than_a_guess(session, code, why):
    assert ReadModelRepository(session, ORG).exact_item(
        code, connection_id=FOUR_U) is None, why


def _resolution(text, *, rel="UNRESOLVED", supply=None, offline=False,
                candidates=()):
    return Resolution(input_text=text, reqCode=text,
                      reqDesc="Awaiting PIE" if offline else "No PIE match",
                      rel="PIE_DOWN" if offline else rel, supplyCode=supply,
                      candidates=list(candidates),
                      outcome="ERROR" if offline else "UNRESOLVED",
                      semantics="UNKNOWN", pie_offline=offline,
                      notes=(["The resolution engine is unavailable for this line."]
                             if offline else []))


@pytest.fixture()
def book(session):
    repo = ReadModelRepository(session, ORG)
    return lambda code: repo.exact_item(code, connection_id=FOUR_U)


@pytest.mark.parametrize("offline", [False, True],
                         ids=["engine answered UNRESOLVED", "engine down"])
def test_a_line_the_engine_could_not_place_is_placed_by_the_books(
        monkeypatch, book, offline):
    from app import store as store_module
    monkeypatch.setattr(store_module.pie_service, "resolve",
                        lambda text, *a, **k: _resolution(text, offline=offline))

    [ln] = QuoteStore().build_lines(
        [{"raw": "22000865 142", "code": "22000865", "qty": 142}], MockZoho(),
        connection_id=FOUR_U, book=book)

    assert ln.supplyCode == "22000865"
    assert ln.rel == "EXACT" and ln.sel == "AUTO"
    assert ln.supplyDesc == "CNMG120408-UC-D2 YC0014"
    # Answered, so not flagged offline — the books did the answering.
    assert ln.service is None
    # One item written two ways is not a substitution.
    assert not ln.substituted()
    # And nothing is proposed for confirmation: a book hit asserts no mapping
    # the engine will later read as this customer's code.
    assert ln.identityCandidate is None


def test_an_engine_answer_is_never_overridden_by_the_books(monkeypatch, session):
    from app import store as store_module
    engine_pick = Candidate(code="2218150", desc="CNMG120408 MS KCP25", rel="EXACT")
    monkeypatch.setattr(
        store_module.pie_service, "resolve",
        lambda text, *a, **k: _resolution(text, rel="EXACT", supply="2218150",
                                          candidates=[engine_pick]))
    asked = []

    [ln] = QuoteStore().build_lines(
        [{"raw": "22000865", "code": "22000865", "qty": 1}], MockZoho(),
        connection_id=FOUR_U, book=lambda code: asked.append(code))

    assert ln.supplyCode == "2218150"
    assert asked == [], "the books are asked only when the engine chose nothing"


def test_a_code_the_books_do_not_hold_stays_unresolved(monkeypatch, book):
    from app import store as store_module
    monkeypatch.setattr(store_module.pie_service, "resolve",
                        lambda text, *a, **k: _resolution(text, offline=True))

    [ln] = QuoteStore().build_lines(
        [{"raw": "fsdf 5", "code": "fsdf", "qty": 5}], MockZoho(),
        connection_id=FOUR_U, book=book)

    assert ln.supplyCode is None and ln.rel == "PIE_DOWN"


def test_sales_history_filed_under_the_item_is_read_for_its_sku(session):
    """The drawer's own path end to end: history recorded against the
    product is found when the line is named by SKU."""
    from app.signals.aggregates import load_snapshot

    cust = models.Customer(customer_id="c1", organization_id=ORG, connector="zoho",
                           connection_id=FOUR_U, external_id="z-c1",
                           name="PITTI ENGINEERING LIMITED- I")
    session.add(cust)
    session.add(models.SalesTxn(organization_id=ORG, external_ref="inv-45:l1",
                                connector="zoho", connection_id=FOUR_U,
                                customer_id="c1", product_id="p_yc",
                                date=date(2026, 9, 12), qty=Decimal(80),
                                unit_price=Decimal(490), line_revenue=Decimal(39200)))
    session.commit()

    product = _resolve_product(session, ORG, "22000865")
    snap = load_snapshot(session, ORG, sales_for_customers=["c1"],
                         costs_for_products=[product.product_id])
    assert [s.product_id for s in snap.sales] == ["p_yc"]


class _Books:
    """One live-looking item whose name is also the code it was picked by."""

    available = True

    def get_item(self, code):
        from app.zoho import ZohoItem
        return ZohoItem(code="22000865", name="CNMG120408-UC-D2 YC0014",
                        in_books=True, list_price=218.0, stock=20, cost=None,
                        item_id="2263307000000277078")


def test_an_item_picked_by_name_is_not_described_by_the_engines_status(monkeypatch):
    """Picked by hand from the books search, whose code is the item's name, on
    a line the engine never answered. The description used to fall back to the
    request's, which on that line is the engine's status message — so the grid
    read "CNMG120408-UC-D2 YC0014 / Awaiting PIE" under a chosen item."""
    from app import store as store_module
    monkeypatch.setattr(store_module.pie_service, "resolve",
                        lambda text, *a, **k: _resolution(text, offline=True))
    st = QuoteStore()
    [ln] = st.build_lines([{"raw": "22000865 142", "code": "22000865", "qty": 142}],
                          _Books(), connection_id=FOUR_U)
    assert ln.reqDesc == "Awaiting PIE"

    st.select_supply(ln, "CNMG120408-UC-D2 YC0014", _Books(), manual=True)

    assert ln.supplyDesc != "Awaiting PIE"
    assert ln.inBooks is True
