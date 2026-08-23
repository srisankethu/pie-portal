"""The traded-line coverage measurement, on a database whose answer is known.

``scripts/measure_quoted_line_coverage.py`` cannot be validated against anything
in this checkout. ``backend/data/platform.db`` is demo seed — one organization,
four fabricated products, none carrying a SKU — and ``ZOHO_SOURCE`` defaults to
``fixture``, three invoices deep. A coverage figure computed over either would be
a fabricated number wearing a percentage sign. So the script is exercised here
instead, against a synthetic database built so that **every axis has a different
right answer**, which is the only way a test can tell that four numbers were
computed rather than one computed four times.

The fixture is deliberately lopsided in two directions at once:

* Identity and geometry are made to *cross* rather than nest. One product is
  linked and decodes, one is linked and does not decode, one decodes and is not
  linked, one is neither. So the union strictly exceeds both halves, and a bug
  that computes the union as either half alone is visible.
* The covered products are cheap and sell often; the uncovered ones are dear and
  sell rarely. Coverage by line count is therefore high while coverage by value
  is low, and a bug that reuses the line-count numerator for the value row shows
  up as two equal percentages.

The catalogue numbers and item names are real rows of the pinned corpus, not
invented ones. An invented SKU would make every identity assertion pass against
a lookup that resolves nothing, which is the failure mode this file exists to
catch elsewhere.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import Base
from app.domain import models

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND.parent / "scripts" / "measure_quoted_line_coverage.py"

pytestmark = pytest.mark.requires_pie

# ── the four corners of the fixture ─────────────────────────────────────────
#: Linked AND decodes: a catalogue number in the corpus whose name is a full
#: ISO turning code.
BOTH = ("2001174", "CNMG 120408-49 - TN2000")
#: Linked and does NOT decode. It still *routes* — to ``milling_insert`` — which
#: is what makes it the load-bearing row for the slot-fill gate: a family route
#: would count it and the ISO gate must not.
ID_ONLY = ("6739214", "M760 WIPER INSERT")
#: Decodes and is NOT linked: a valid ISO code carrying a SKU the pack has never
#: heard of.
GEO_ONLY = ("ZZZ-NOT-A-CATALOGUE-NUMBER", "TNMG 160408-MP")
#: Neither. §3's own example of a name that is not a cutting geometry.
NEITHER = ("ZZZ-ALSO-NOT-ONE", "M3X11 SOCKET HEAD SCREW")


def _load_script():
    """Import the script as a module. It is a CLI, so it has no package."""
    spec = importlib.util.spec_from_file_location("measure_quoted_line_coverage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Args:
    def __init__(self, **kw):
        self.json = None
        self.reresolve = False
        self.no_geometry = False
        self.__dict__.update(kw)


@pytest.fixture()
def session():
    engine = dbsupport.fresh_engine()
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    s = maker()
    try:
        yield s
    finally:
        s.close()
        if not dbsupport.TEST_SERVER_URL:
            Base.metadata.drop_all(engine)


def _seed(session, *, persist_links: bool = True) -> None:
    """One organization, two connections, four products, eight invoice lines.

    SLS: p_both ×3 @100, p_geo ×1 @200, p_none ×1 @4000   (5 lines, 4,500)
    4U:  p_both ×1 @100, p_id  ×1 @500, p_none ×1 @4900   (3 lines, 5,500)

    Totals — 4 distinct products, 8 lines, 10,000 of selling value:
      by product  identity 2/4 = 50.00%   geometry 2/4 = 50.00%   union 3/4 = 75.00%
      by line     identity 5/8 = 62.50%   geometry 5/8 = 62.50%   union 6/8 = 75.00%
      by value    identity  900 = 9.00%   geometry  600 = 6.00%   union 1,100 = 11.00%

    The value row is the one that separates all three, which is why the money is
    shaped this way rather than round.
    """
    session.add(models.Organization(organization_id="org1", name="Acme Tools"))
    for cid, label in (("conn_a", "SLS Engineers"), ("conn_b", "4U Precision")):
        session.add(models.ZohoConnection(
            connection_id=cid, organization_id="org1", connector="zoho",
            label=label, zoho_organization_id=f"z_{cid}"))

    catalogue = {"p_both": BOTH, "p_id": ID_ONLY, "p_geo": GEO_ONLY, "p_none": NEITHER}
    linked = {"p_both", "p_id"}
    for i, (pid, (sku, name)) in enumerate(catalogue.items()):
        session.add(models.Product(
            product_id=pid, organization_id="org1", connector="zoho",
            connection_id="conn_a", external_id=f"i{i}", name=name,
            pie_record_id=(sku if (persist_links and pid in linked) else None),
            pie_link_method=("SKU_EXACT" if (persist_links and pid in linked) else None)))
        # SKU lives here and only here — Product has no sku column.
        ident = models.ItemIdentity(organization_id="org1", label=pid)
        session.add(ident)
        session.flush()
        session.add(models.ItemConnectorRecord(
            organization_id="org1", identity_id=ident.identity_id, connector="zoho",
            connection_id="conn_a", external_id=f"i{i}", sku=sku, product_id=pid))

    def line(ref, conn, pid, revenue):
        session.add(models.SalesTxn(
            organization_id="org1", connector="zoho", connection_id=conn,
            external_ref=ref, customer_id="c1", product_id=pid,
            date=date(2026, 1, 1), qty=Decimal("1"),
            unit_price=Decimal(revenue), line_revenue=Decimal(revenue)))

    for i in range(3):
        line(f"a_both_{i}", "conn_a", "p_both", "100")
    line("a_geo_0", "conn_a", "p_geo", "200")
    line("a_none_0", "conn_a", "p_none", "4000")
    line("b_both_0", "conn_b", "p_both", "100")
    line("b_id_0", "conn_b", "p_id", "500")
    line("b_none_0", "conn_b", "p_none", "4900")
    session.commit()


def _money(value: str) -> Decimal:
    """Compare money as a number. The scale a backend returns is its business."""
    return Decimal(value)


# ── the headline: three weightings, three answers ───────────────────────────


def test_the_three_weightings_are_computed_independently(session):
    _seed(session)
    module = _load_script()
    overall = module.build_report(session, _Args())["overall"]

    assert overall["distinct_products"] == 4
    assert overall["lines"] == 8
    assert _money(overall["selling_value"]) == Decimal("10000")

    assert overall["by_product"]["union_pct"] == pytest.approx(75.00)
    assert overall["by_line"]["union_pct"] == pytest.approx(75.00)
    assert overall["by_value"]["union_pct"] == pytest.approx(11.00)
    # The three must not coincide — that is the whole reason for reporting three.
    assert len({overall["by_product"]["union_pct"],
                overall["by_line"]["union_pct"],
                overall["by_value"]["union_pct"]}) == 2
    assert overall["by_value"]["identity_pct"] == pytest.approx(9.00)
    assert overall["by_value"]["geometry_pct"] == pytest.approx(6.00)


def test_the_union_exceeds_either_half_rather_than_nesting_inside_one(session):
    """Identity and geometry cross. A union computed as either half is visible."""
    _seed(session)
    module = _load_script()
    p = module.build_report(session, _Args())["overall"]["by_product"]
    assert p["identity"] == 2
    assert p["geometry"] == 2
    assert p["union"] == 3


def test_value_weighting_uses_selling_revenue_and_never_cost(session):
    _seed(session)
    module = _load_script()
    report = module.build_report(session, _Args())
    assert _money(report["overall"]["by_value"]["identity"]) == Decimal("900")
    # CLAUDE.md §1: cost and margin are absent from the output, not hidden in it.
    # Asserted over the whole serialized report rather than field by field,
    # because the failure this guards against is a field nobody thought to name.
    blob = str(report).lower()
    for banned in ("cost", "margin", "gross_profit", "unit_cost", "purchase"):
        assert banned not in blob, f"{banned!r} leaked into a coverage report"


def test_each_entity_is_reported_separately(session):
    _seed(session)
    module = _load_script()
    entities = module.build_report(session, _Args())["entities"]
    by_label = {e["connection_label"]: e for e in entities}
    assert set(by_label) == {"SLS Engineers", "4U Precision"}

    sls, fu = by_label["SLS Engineers"], by_label["4U Precision"]
    assert (sls["lines"], fu["lines"]) == (5, 3)
    assert _money(sls["selling_value"]) == Decimal("4500")
    assert _money(fu["selling_value"]) == Decimal("5500")
    # The entities disagree, which is the point of splitting them: SLS trades
    # the decodable insert, 4U trades the linked-but-undecodable one.
    assert sls["by_line"]["geometry"] == 4
    assert fu["by_line"]["geometry"] == 1
    assert sls["by_line"]["identity"] == 3
    assert fu["by_line"]["identity"] == 2


# ── the gates ───────────────────────────────────────────────────────────────


def test_geometry_is_gated_on_full_iso_slot_fill_not_a_family_route(session):
    """``M760 WIPER INSERT`` routes to a family and fills no ISO slots.

    It must not count as geometry. If it ever does, the gate has been loosened
    from slot fill to a family route — §3 measures that route misrouting an
    ``M3X11`` screw into ``turning_insert``, so a number computed behind it
    stops meaning anything.
    """
    _seed(session)
    module = _load_script()
    report = module.build_report(session, _Args())
    assert report["geometry_gate"] == ["iso_shape", "edge_length_mm", "corner_radius_mm"]

    decoded = module.decode_geometry([("p_id", ID_ONLY[1]), ("p_none", NEITHER[1]),
                                      ("p_geo", GEO_ONLY[1])])
    assert set(decoded) == {"p_geo"}


def test_the_screw_routes_to_a_family_but_still_fails_the_gate(session):
    """The premise of the test above, asserted rather than assumed."""
    module = _load_script()
    from app.config import settings
    sys.path.insert(0, str(settings.PIE_PARSER_ROOT))
    from engine.model import RawRecord
    from engine.pack import load_pack
    from engine.pipeline import ParserPipeline, RunProfile

    raw = RawRecord(record_id="x", description=ID_ONLY[1], grade=None, payload={},
                    source_file="t", source_sheet="-", source_row=1)
    products, _report, quarantine = ParserPipeline(
        load_pack(settings.PIE_PACK), RunProfile()).run([raw])
    rec = (list(products) + list(quarantine))[0]
    assert rec["product_family"] is not None      # it routes...
    assert not all(rec.get(s) is not None for s in module.ISO_REQUIRED)   # ...and fails


# ── identity: persisted vs live ─────────────────────────────────────────────


def test_identity_reads_the_link_the_sync_already_persisted(session):
    _seed(session)
    module = _load_script()
    report = module.build_report(session, _Args())
    assert report["identity_source"] == "persisted Product.pie_record_id"
    assert report["overall"]["by_product"]["identity"] == 2


def test_identity_can_be_recomputed_live_through_the_sku_path(session):
    """``Product`` has no ``sku``; the live path must go through the record."""
    _seed(session, persist_links=False)
    module = _load_script()
    assert module.build_report(session, _Args())["overall"]["by_product"]["identity"] == 0

    live = module.build_report(session, _Args(reresolve=True))
    assert live["identity_source"] == "live SKU re-resolution"
    assert live["overall"]["by_product"]["identity"] == 2
    assert live["link_disagreement"]["live_only"] == ["p_both", "p_id"]
    assert live["link_disagreement"]["persisted_only"] == []


def test_a_stale_persisted_link_is_reported_rather_than_trusted(session):
    """A link the live catalogue no longer resolves is the honest half."""
    _seed(session)
    record = session.query(models.ItemConnectorRecord).filter_by(product_id="p_id").one()
    record.sku = "ZZZ-WITHDRAWN"       # the catalogue no longer carries it
    session.commit()

    module = _load_script()
    live = module.build_report(session, _Args(reresolve=True))
    assert live["link_disagreement"]["persisted_only"] == ["p_id"]
    assert live["overall"]["by_product"]["identity"] == 1


def test_the_sku_index_goes_through_the_connector_record(session):
    """Correction 2, asserted: there is no Product.sku to shortcut through."""
    _seed(session)
    module = _load_script()
    assert not hasattr(models.Product, "sku")
    assert module.sku_index(session)["p_both"] == BOTH[0]


# ── the refusals ────────────────────────────────────────────────────────────


def test_an_empty_denominator_is_not_reported_as_zero_coverage(session, capsys):
    """No lines is 'nothing to measure', never '0% covered'."""
    session.add(models.Organization(organization_id="org1", name="Acme Tools"))
    session.commit()
    module = _load_script()
    assert module._run(session, _Args()) == 0
    out = capsys.readouterr().out
    assert "empty denominator, not 0% coverage" in out
    assert "0.00%" not in out


def test_an_absent_catalogue_is_refused_rather_than_scored_zero(session, capsys,
                                                                monkeypatch):
    """``catalog_available`` exists to keep these two apart. Keep them apart.

    With no catalogue every lookup returns None, which is "nobody asked the
    pack" — not "the pack does not cover this". Printing 0% would be the same
    absence-read-as-evidence CLAUDE.md §1 forbids, inverted.
    """
    _seed(session)
    module = _load_script()
    monkeypatch.setattr(type(module.pie_service), "catalog_available",
                        property(lambda self: False))
    report = module.build_report(session, _Args())
    assert report["catalog_available"] is False
    assert report["geometry_measured"] is False
    module._run(session, _Args())
    out = capsys.readouterr().out
    assert "THE CATALOGUE IS NOT LOADED" in out
    assert "No coverage figure below is admissible" in out


def test_an_orphan_line_is_neither_a_hit_nor_a_miss(session):
    """A line pointing at an absent product row is a referential gap.

    Counting it as uncovered would understate coverage with a data-quality
    problem, which is a different finding wearing this one's clothes.
    """
    _seed(session)
    session.add(models.SalesTxn(
        organization_id="org1", connector="zoho", connection_id="conn_a",
        external_ref="orphan", customer_id="c1", product_id="p_missing",
        date=date(2026, 1, 1), qty=Decimal("1"),
        unit_price=Decimal("1"), line_revenue=Decimal("1")))
    session.commit()
    module = _load_script()
    overall = module.build_report(session, _Args())["overall"]
    assert overall["orphan_lines"] == 1
    assert overall["lines"] == 8                              # not a denominator
    assert _money(overall["selling_value"]) == Decimal("10000")


def test_the_report_leads_with_its_own_limitation(session, capsys):
    """The lower-bound warning is in the output, not only in the docstring.

    Whoever reads a pasted terminal block will not have opened the source, and
    this number is misread by default — the conditioning is invisible unless the
    report says so where the number is.
    """
    _seed(session)
    module = _load_script()
    module._run(session, _Args())
    out = capsys.readouterr().out
    head = out[:out.index("catalogue")]
    assert "LOWER BOUND" in head
    assert "conditions on the outcome" in head


def test_the_scope_note_says_invoice_lines_and_why(session):
    """The estimate gap is scope, recorded, not silence."""
    _seed(session)
    module = _load_script()
    scope = module.build_report(session, _Args())["scope"]
    assert "invoice lines" in scope
    assert "estimate" in scope
