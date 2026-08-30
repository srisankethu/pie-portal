"""The Phase 1 exit criterion, over HTTP: who may read it and what it says.

``attributes/coverage.py`` is tested at the function in
``test_product_attributes``. What is pinned here is the surface — the part that
was missing, because a measurement nothing publishes is a measurement whoever
decides the phase cannot see.

Four claims, in the order the endpoint would break them:

* **Owner-only.** A salesperson and a manager are refused. The gate is
  ``require_owner`` rather than a hand-rolled role check, and this is what says
  the right dependency is on the route.
* **The published numbers are the computed ones.** The whole payload is
  compared against the report object, field for field, rather than spot-checked
  — a projection that drops ``by_key`` or reorders it passes every assertion
  that only reads a total.
* **No products is UNKNOWN, not 0%.** §1's "absence of evidence is not a pass",
  in the shape it takes here: the ratio keys are present and ``null``. A
  projection that flattened them to ``0.0`` would report a measured failure of
  a catalogue that does not exist, and would do it in the exact place somebody
  is deciding whether the phase is done.
* **A source kind with no rows is absent, not zero.** "This source wrote
  nothing" and "nobody asked this source" are different facts; only
  ``decorate``'s report can tell them apart, so this one must not answer as
  though it could.

The store is filled through ``write_claims`` — the package's own writer — with
claims spelled out rather than decoded. These tests are about the surface, and
driving them through the real pack would make them slow and make a failure
ambiguous between the endpoint and the decoder. ``test_product_attributes``
runs the real decode.
"""
from __future__ import annotations

from app.attributes import (CATALOGUE_LINK, DECODED_NAME, attribute_coverage,
                            write_claims)
from app.attributes.extract import AttributeClaim
from app.config import settings
from app.domain import models

from .test_api_authz import _hdr, _login

ORG = settings.DEFAULT_ORG_ID
PATH = "/api/v1/internal/attribute-coverage"


def _product(session, name: str, external_id: str) -> models.Product:
    row = models.Product(organization_id=ORG, external_id=external_id, name=name,
                         connector="zoho", connection_id="c1")
    session.add(row)
    session.flush()
    return row


def _seed_one_decorated_product(session) -> models.Product:
    """One product both sources claim, and one nothing says anything about.

    The undecorated row is not padding: it is the denominator, and a coverage
    rate computed over decorated products alone would be 100% forever.
    """
    decorated = _product(session, "CNMG 120408-49 - TN2000", "i1")
    _product(session, "MISC BRACKET ASSEMBLY 4 OFF", "i2")
    write_claims(session, ORG, decorated.product_id, DECODED_NAME,
                 [AttributeClaim("corner_radius_mm", "0.8", 0.8, unit="mm"),
                  AttributeClaim("iso_shape", "C")])
    write_claims(session, ORG, decorated.product_id, CATALOGUE_LINK,
                 [AttributeClaim("corner_radius_mm", "0.8", 0.8, unit="mm")])
    session.commit()
    return decorated


def test_the_coverage_report_is_owner_only(api_client):
    client = api_client
    assert client.get(PATH, headers=_hdr(_login(client, "r.nair@pie.example"))
                      ).status_code == 403
    assert client.get(PATH, headers=_hdr(_login(client, "m.rao@pie.example"))
                      ).status_code == 403
    assert client.get(PATH, headers=_hdr(_login(client, "s.menon@pie.example"))
                      ).status_code == 200


def test_the_published_numbers_are_the_ones_coverage_computed(api_client, session):
    """Every field, against the report object — including the per-key census.

    The per-key table is the one that answers what a retrieval layer could
    filter on, and it is also the one a projection is most likely to drop or
    re-sort. Comparing the whole list, in order, is what notices.
    """
    _seed_one_decorated_product(session)

    body = api_client.get(
        PATH, headers=_hdr(_login(api_client, "s.menon@pie.example"))).json()

    report = attribute_coverage(session, ORG)
    assert body == {
        "organization_id": ORG,
        "products_total": report.products_total,
        "products_with_any_attribute": report.products_with_any_attribute,
        "coverage_rate": report.coverage_rate,
        "live_values": report.live_values,
        "attributes_per_decorated_product": report.attributes_per_decorated_product,
        "by_key": [{"attribute_key": k.attribute_key, "products": k.products,
                    "values": k.values, "fill_rate": k.fill_rate}
                   for k in report.by_key],
        "by_source_kind": [{"source_kind": kind, "values": values}
                           for kind, values in report.by_source_kind],
    }
    # And the report it agrees with is the one the seeding implies, so the
    # comparison above cannot pass by both sides being empty.
    assert (body["products_total"], body["products_with_any_attribute"]) == (2, 1)
    assert body["coverage_rate"] == 0.5
    assert body["live_values"] == 3
    by_key = {k["attribute_key"]: k for k in body["by_key"]}
    assert by_key["corner_radius_mm"] == {"attribute_key": "corner_radius_mm",
                                          "products": 1, "values": 2,
                                          "fill_rate": 0.5}
    assert {k["source_kind"] for k in body["by_source_kind"]} == {DECODED_NAME,
                                                                 CATALOGUE_LINK}


def test_an_organization_with_no_products_reports_unknown_not_zero(api_client):
    """The ratios come back ``null`` and the key is still there.

    Present-and-null rather than omitted, because a client reading a missing
    key with ``get(..., 0)`` lands on the same 0% this refuses to report.
    """
    body = api_client.get(
        PATH, headers=_hdr(_login(api_client, "s.menon@pie.example"))).json()

    assert body["products_total"] == 0
    assert "coverage_rate" in body and body["coverage_rate"] is None
    assert ("attributes_per_decorated_product" in body
            and body["attributes_per_decorated_product"] is None)
    assert body["by_key"] == []
    assert body["by_source_kind"] == []


def test_a_source_that_wrote_nothing_is_absent_from_the_split_not_zero(
        api_client, session):
    """Silence is not a measured zero — the split lists only what holds rows.

    A ``CATALOGUE_LINK: 0`` here would read as "the catalogue was consulted and
    matched nothing", which is a claim this endpoint has no evidence for: it
    reads live rows, and the report that knows whether a source could even be
    asked is ``decorate``'s.
    """
    product = _product(session, "CNMG 120408-49 - TN2000", "i1")
    write_claims(session, ORG, product.product_id, DECODED_NAME,
                 [AttributeClaim("iso_shape", "C")])
    session.commit()

    body = api_client.get(
        PATH, headers=_hdr(_login(api_client, "s.menon@pie.example"))).json()

    assert body["by_source_kind"] == [{"source_kind": DECODED_NAME, "values": 1}]


def test_the_report_carries_no_commercial_value(api_client, session):
    """§1, asserted on the payload rather than assumed from the table's columns.

    The attribute store holds decoded technical facts and has no cost, price or
    margin column — so the way one arrives here is a future field on this
    projection, which is what the walk below would catch.
    """
    _seed_one_decorated_product(session)

    body = api_client.get(
        PATH, headers=_hdr(_login(api_client, "s.menon@pie.example"))).json()

    forbidden = ("cost", "margin", "price", "revenue", "discount")
    def _walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert not any(word in key.lower() for word in forbidden), (
                    f"{path}.{key} names something commercial")
                _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                _walk(value, f"{path}[{i}]")
        elif isinstance(node, str):
            assert not any(word in node.lower() for word in forbidden), (
                f"{path} carries {node!r}")

    _walk(body)
