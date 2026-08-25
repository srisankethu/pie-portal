"""The quoted-line coverage measurement, over a synthetic book.

``scripts/measure_quoted_coverage.py`` changes one thing about the published
21.6% figure: the denominator. So what these tests pin is the arithmetic of the
three weightings and — much more importantly — the three places the script must
refuse rather than answer. A coverage script that reports 0% when it was never
told anything is worse than one that crashes: the zero is quotable.

There is no real database and no ERP credentials in this checkout, so the
fixture below is the *whole* population these tests see. It is deliberately
lopsided — the covered products carry few lines and large value, the uncovered
ones the reverse — because three weightings that agree with each other prove
nothing about whether the script is weighting anything at all.

The engine is stubbed for everything except the one ISO-gate test, for the
reason ``test_catalog_link`` gives: what is under test here is this script's
decision-making, and the absent-catalogue branch cannot be reproduced at all
when the catalogue is present.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.domain import models

# The script lives in scripts/, outside the app package — the same import shape
# `test_deploy_runbook` uses.
_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from measure_quoted_coverage import (  # noqa: E402
    ABSENT,
    LINKED,
    LINKED_STALE,
    MEASURED,
    NO_SKU,
    UNKNOWN,
    combine,
    read_geometry,
    read_identity,
    read_trade,
)

ORG = "org_test"


class _Catalog:
    """Stands in for pie_service. Same two-member surface the script uses."""

    def __init__(self, records=(), available=True):
        self._records = {str(r).strip().upper() for r in records}
        self.catalog_available = available

    def lookup_record(self, identifier):
        if not identifier or not self.catalog_available:
            return None
        key = str(identifier).strip().upper()
        return {"record_id": key} if key in self._records else None


def _product(session, pid, name, *, sku=None, linked=None, conn="c1"):
    session.add(models.Product(product_id=pid, organization_id=ORG, connector="zoho",
                               connection_id=conn, external_id=pid, name=name,
                               pie_record_id=linked,
                               pie_link_method="SKU_EXACT" if linked else None))
    if sku is not None:
        session.add(models.ItemIdentity(identity_id=f"id_{pid}", organization_id=ORG))
        session.add(models.ItemConnectorRecord(
            record_id=f"rec_{pid}", organization_id=ORG, identity_id=f"id_{pid}",
            connector="zoho", connection_id=conn, external_id=pid, sku=sku,
            product_id=pid))


def _line(session, ref, pid, revenue, *, conn="c1"):
    session.add(models.SalesTxn(
        sales_txn_id=f"t_{ref}", organization_id=ORG, connector="zoho",
        connection_id=conn, external_ref=ref, customer_id="cust1", product_id=pid,
        date=date(2026, 1, 1), qty=Decimal("1"),
        unit_price=Decimal(revenue), line_revenue=Decimal(revenue)))


@pytest.fixture
def session():
    """A synthetic book: two entities, four traded products, one untraded.

    ``p_linked``   — the sync already wrote its link. Two lines, ₹1,000.
    ``p_stale``    — no link on the row, but the catalogue knows its SKU.
    ``p_absent``   — a SKU the catalogue was asked about and does not hold.
    ``p_nosku``    — traded, but no connector record carries a SKU.
    ``p_untraded`` — in the master, never invoiced. Must not reach any figure.
    """
    s = sessionmaker(bind=dbsupport.fresh_engine(), future=True)()
    s.add(models.Organization(organization_id=ORG, name="Test Entity"))
    s.add(models.ZohoConnection(connection_id="c1", organization_id=ORG,
                                connector="zoho", label="Book One",
                                zoho_organization_id="z1"))
    s.add(models.ZohoConnection(connection_id="c2", organization_id=ORG,
                                connector="zoho", label="Book Two",
                                zoho_organization_id="z2"))
    _product(s, "p_linked", "CNMG 120408 MP KCP25", sku="2001174", linked="2001174")
    _product(s, "p_stale", "TCMT 110204 HP KC5010", sku="2002222")
    _product(s, "p_absent", "SDJCR 2020K11", sku="9999999")
    _product(s, "p_nosku", "M3X11 SCREW")
    _product(s, "p_untraded", "CNMG 090304 KCP10", sku="2003333", linked="2003333")
    # Lopsided on purpose: one high-value line against many small ones.
    _line(s, "i1:1", "p_linked", "900")
    _line(s, "i1:2", "p_linked", "100")
    for i, pid in enumerate(("p_absent", "p_absent", "p_absent", "p_nosku", "p_nosku")):
        _line(s, f"i2:{i}", pid, "10")
    _line(s, "i3:1", "p_stale", "50", conn="c2")
    s.commit()
    yield s
    s.close()


def _measure(session, catalog, geometry):
    traded = read_trade(session)
    ids = traded.product_ids()
    identity = read_identity(session, ids, service=catalog)
    return combine(traded, identity, geometry)


def _book(report, key):
    return next(b for b in report["books"] if b["book"] == key)


# ── the denominator ─────────────────────────────────────────────────────────
def test_only_traded_products_reach_the_denominator(session):
    """The whole point of the script. A master row nobody invoiced is not in it."""
    traded = read_trade(session)
    assert "p_untraded" not in traded.product_ids()
    assert traded.product_ids() == {"p_linked", "p_stale", "p_absent", "p_nosku"}


def test_each_connected_book_is_its_own_entity(session):
    """One organization holding several connections is several entities. Rolling
    them together is what `ZohoConnection` warns rolls revenue up across legal
    entities, and a per-entity coverage figure has to resist exactly that."""
    report = _measure(session, _Catalog(["2002222"]), {})
    assert {b["book"] for b in report["books"]} == {f"{ORG}/zoho/c1", f"{ORG}/zoho/c2"}
    assert _book(report, f"{ORG}/zoho/c2")["traded"]["products"] == 1


# ── the three weightings actually differ ────────────────────────────────────
def test_the_three_weightings_are_three_different_numbers(session):
    """Book One: 3 traded products, 7 lines, ₹1,050. Only `p_linked` is covered
    — 1 of 3 products, 2 of 7 lines, ₹1,000 of ₹1,050. A script that weighted
    nothing would report the same share three times."""
    report = _measure(session, _Catalog(["2002222"]), {})
    ident = _book(report, f"{ORG}/zoho/c1")["identity"]
    assert (ident["products"], ident["products_pct"]) == (1, 33.3)
    assert (ident["lines"], ident["lines_pct"]) == (2, 28.6)
    assert (ident["value"], ident["value_pct"]) == ("1000.0000", 95.2)


def test_value_is_weighted_at_selling_price_and_nothing_else(session):
    """`SalesTxn.line_revenue` is net of the line discount and pre-tax. No cost
    column is opened anywhere, so no figure below has a cost boundary in it."""
    report = _measure(session, _Catalog(["2002222"]), {})
    assert _book(report, f"{ORG}/zoho/c1")["traded"]["value"] == "1050.0000"
    blob = repr(report).lower()
    for forbidden in ("cost", "margin", "gross_profit", "purchase_price"):
        assert forbidden not in blob


# ── identity: the persisted link, and what NULL means ───────────────────────
def test_the_persisted_link_is_read_rather_than_re_resolved(session):
    """`_link_catalog` already answered this. A catalogue that knows nothing at
    all still leaves `p_linked` linked, because the column is the evidence."""
    verdicts = read_identity(session, {"p_linked"}, service=_Catalog([]))
    assert verdicts == {"p_linked": LINKED}


def test_a_null_link_is_separated_into_its_three_causes(session):
    """The column cannot distinguish these — `_link_catalog` clears all three
    fields on a miss — and reporting them as one number would fuse a pack gap
    with a master defect."""
    verdicts = read_identity(
        session, {"p_stale", "p_absent", "p_nosku"}, service=_Catalog(["2002222"]))
    assert verdicts == {"p_stale": LINKED_STALE, "p_absent": ABSENT,
                        "p_nosku": NO_SKU}


def test_the_sku_is_reached_through_the_connector_record(session):
    """`Product` has no SKU column and the sync never writes one. The only path
    is SalesTxn.product_id -> ItemConnectorRecord.product_id -> .sku, so a
    product whose connector record is missing its SKU is unmeasurable, not
    uncovered."""
    session.query(models.ItemConnectorRecord).filter_by(product_id="p_stale").delete()
    session.commit()
    verdicts = read_identity(session, {"p_stale"}, service=_Catalog(["2002222"]))
    assert verdicts == {"p_stale": NO_SKU}


# ── the three refusals ──────────────────────────────────────────────────────
def test_an_unloaded_catalogue_is_unknown_and_never_zero(session):
    """`catalog_available` exists precisely to separate "the pack does not cover
    this item" from "nobody asked the pack". A 0% identity figure produced by
    the second is the benign default CLAUDE.md §1 refuses — and it is quotable."""
    assert read_identity(session, {"p_linked"}, service=_Catalog([], available=False)) is None
    report = _measure(session, _Catalog([], available=False), {})
    assert report["identity_evidence"] == UNKNOWN
    ident = _book(report, f"{ORG}/zoho/c1")["identity"]
    assert ident["state"] == UNKNOWN
    assert ident["products"] is None and ident["products_pct"] is None


def test_an_unknown_half_makes_the_union_unknown(session):
    """A measured set added to an unmeasured one is a lower bound of unstated
    size wearing a coverage figure's clothes."""
    report = _measure(session, _Catalog(["2002222"]), None)
    assert report["geometry_evidence"] == UNKNOWN
    book = _book(report, f"{ORG}/zoho/c1")
    assert book["identity"]["state"] == MEASURED
    assert book["geometry"]["state"] == UNKNOWN
    assert book["union"]["state"] == UNKNOWN
    assert book["neither"]["state"] == UNKNOWN


def test_an_empty_book_reports_no_books_rather_than_zero_coverage(session):
    session.query(models.SalesTxn).delete()
    session.commit()
    report = _measure(session, _Catalog(["2002222"]), {})
    assert report["books"] == []


# ── union arithmetic ────────────────────────────────────────────────────────
def test_the_union_counts_a_product_once_and_records_the_overlap(session):
    """`p_linked` is covered both ways. Union must not double-count it, and the
    overlap has to stay visible — it is what says the two definitions are not
    measuring the same thing twice."""
    geometry = {"p_linked": True, "p_absent": True, "p_stale": False, "p_nosku": False}
    report = _measure(session, _Catalog(["2002222"]), geometry)
    book = _book(report, f"{ORG}/zoho/c1")
    assert book["identity"]["products"] == 1        # p_linked
    assert book["geometry"]["products"] == 2        # p_linked, p_absent
    assert book["union"]["products"] == 2
    assert book["overlap"]["products"] == 1
    assert book["neither"]["products"] == 1         # p_nosku


# ── the ISO gate, against the real engine ───────────────────────────────────
@pytest.mark.requires_pie
def test_geometry_is_gated_on_full_iso_slot_fill_not_on_a_family_route():
    """§3 of `01-application-engineering.md` is the whole reason for this gate:
    30.3% of master names route to a named family and 11.6% of those routed rows
    carry a non-Kennametal manufacturer — an `M3X11` screw routes to
    `turning_insert`. Restricted to shape + edge + radius, misroutes fall to
    0.05%. So the screw must be refused even though it routes."""
    decoded = read_geometry({
        "insert": "CNMG 120408 MP KCP25",
        "screw": "M3X11 SCREW",
        "holder": "SDJCR 2020K11",
    })
    assert decoded is not None, "the engine is present; this must not refuse"
    assert decoded["insert"] is True
    assert decoded["screw"] is False
    assert decoded["holder"] is False


@pytest.mark.requires_pie
def test_the_decode_is_deterministic_over_the_same_names():
    names = {"a": "CNMG 120408 MP KCP25", "b": "TCMT 110204 HP KC5010"}
    assert read_geometry(names) == read_geometry(dict(reversed(list(names.items()))))


# ── the master's gap versus the corpus's gap ────────────────────────────────
def test_an_item_with_no_sku_is_unmeasurable_not_uncovered(session):
    """`p_nosku` carries no catalogue number anywhere, so no pack could ever
    link it. Counting it as a miss measures the item master, not the corpus —
    so both readings are reported and neither is allowed to stand alone."""
    report = _measure(session, _Catalog(["2002222"]), {})
    book = _book(report, f"{ORG}/zoho/c1")
    assert book["identity"]["products_pct"] == 33.3          # 1 of 3 traded
    assert book["identity_where_askable"]["products_pct"] == 50.0   # 1 of 2 asked
    assert book["identity_states"][NO_SKU] == 1


def test_a_book_with_nothing_askable_reports_unknown_rather_than_zero(session):
    """The demo-seed shape, and the one that would otherwise publish a 0%. Every
    traded product lacking a SKU means the pack was never asked anything; the
    full-population figure may honestly read 0, the askable one must not."""
    session.query(models.ItemConnectorRecord).delete()
    for row in session.query(models.Product):
        row.pie_record_id = None
    session.commit()
    report = _measure(session, _Catalog(["2002222"]), {})
    book = _book(report, f"{ORG}/zoho/c1")
    assert book["identity"]["state"] == MEASURED and book["identity"]["products"] == 0
    assert book["identity_where_askable"]["state"] == UNKNOWN
    assert book["identity_where_askable"]["products_pct"] is None
