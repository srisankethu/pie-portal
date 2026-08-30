"""Run both sources over a product set and make their claims current.

The orchestration, and nothing else: it decides *which* products to ask about
and *which* source can answer, then hands the answer to
:func:`.writer.write_claims`. The decoding is ``master_health.geometry``'s, the
catalogue lookup is ``pie_service``'s, and turning a decoded field into a
storable value is ``extract``'s.

**One decoder, not a second one.** ``master_health`` is offline by design — no
session, no connector, no upload — and pinned that way by a test, so it decodes
and this persists. ``decode_names`` runs the whole batch through one
``ParserPipeline`` rather than one per row, which is why this reads every
product first instead of streaming.

**Nobody-asked is not nothing-found.** Both sources can be *absent*: pie-parser
may not be checked out, and the catalogue may not have been built. Neither is
evidence about any product, so when a source cannot be asked this writes nothing
for it and retracts nothing — the same refusal ``ingestion.sync._link_catalog``
makes about ``pie_record_id``, and for the same reason. Silently retracting
every attribute in the table because one deployment shipped without the engine
would read, on the coverage report, as a Phase 1 regression.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models
from ..master_health.geometry import decode_names
from ..master_health.source import MasterRow
from .extract import (CATALOGUE_LINK, DECODED_NAME, claims_from_catalogue_record,
                      claims_from_decode)
from .writer import WriteResult, write_claims

log = logging.getLogger("pie_portal.attributes")


@dataclass
class DecorationReport:
    """What one run did, and what it could not do. Counts, never a verdict.

    ``decoded_name_unavailable`` and ``catalogue_unavailable`` are the two
    UNKNOWNs. A run with either set has not measured anything about that source,
    and a reader must not band its zero as a result — which is why they are
    sentences rather than booleans where the engine gave one.
    """

    organization_id: str
    products_considered: int = 0
    #: Products the decoder said at least one fact about, and products whose
    #: catalogue link resolved to a record. Both are populations, not scores.
    names_decoded: int = 0
    catalogue_records_read: int = 0
    #: Products carrying a ``pie_record_id`` that this catalogue does not hold.
    #: Not a failure of this run — a link written under a superseded catalogue.
    catalogue_links_unresolved: int = 0
    #: Rows the decode batch returned no outcome for at all. Zero in every
    #: observed run; carried because the alternative to counting it is assuming
    #: it, and an unanswered row is not a row that decoded to nothing.
    rows_without_outcome: int = 0
    written: WriteResult = field(default_factory=WriteResult)
    decoded_name_unavailable: str = ""
    catalogue_unavailable: str = ""
    #: ``(attribute_key, reason) -> count`` over every field extraction refused.
    #: Visible rather than dropped: a coverage number that came out low should
    #: be explainable without re-running the decoder.
    refusals: Dict[Tuple[str, str], int] = field(default_factory=dict)

    @property
    def decoders_available(self) -> bool:
        return not (self.decoded_name_unavailable or self.catalogue_unavailable)


def _master_row(row_number: int, product: models.Product) -> MasterRow:
    """A ``Product`` in the shape the decoder reads.

    ``MasterRow`` is "one item as the export words it", and a synced product row
    is the same item read from the ERP instead of from a file — the same fields,
    a different door. Only ``name`` is decoded; the rest are carried because the
    dataclass has them, and ``rate``/``stock`` are deliberately left ``None``:
    this package has no business holding a price, and the decoder never looks.
    """
    return MasterRow(
        row_number=row_number,
        sku=None,
        name=product.name,
        manufacturer=product.manufacturer,
        rate=None,
        stock=None,
        hsn=product.hsn,
        uom=product.uom,
    )


def _tally(report: DecorationReport, refused: Iterable[Tuple[str, str]]) -> None:
    for entry in refused:
        report.refusals[entry] = report.refusals.get(entry, 0) + 1


def decorate_products(session: Session, organization_id: str, *,
                      product_ids: Optional[Iterable[str]] = None
                      ) -> DecorationReport:
    """Decode this organization's products and store what came back.

    Idempotent by construction: a second run over unchanged products and an
    unchanged pack writes nothing at all, because every claim compares equal to
    the live row and :func:`.writer.write_claims` leaves it alone. That is the
    property to test rather than to assume — a supersede-on-every-run writer
    looks identical from the outside until the table is a hundred times the size
    it should be and no row is legible.

    Ordered by ``product_id`` so a run is reproducible, including the order rows
    are inserted in.
    """
    stmt = select(models.Product).where(
        models.Product.organization_id == organization_id)
    if product_ids is not None:
        wanted = list(product_ids)
        if not wanted:
            return DecorationReport(organization_id=organization_id)
        stmt = stmt.where(models.Product.product_id.in_(wanted))
    products: List[models.Product] = list(
        session.scalars(stmt.order_by(models.Product.product_id)))

    report = DecorationReport(organization_id=organization_id,
                              products_considered=len(products))
    if not products:
        return report

    run = decode_names([_master_row(n, p) for n, p in enumerate(products, start=1)])
    if not run.available:
        report.decoded_name_unavailable = run.unavailable_reason

    catalogue = _catalogue()
    if catalogue is None:
        report.catalogue_unavailable = (
            "the decoded catalogue is not loaded, so no item's manufacturer "
            "record was read. Catalogue coverage is UNKNOWN, not zero.")

    # One query for the whole batch rather than two per product: a source with
    # nothing to say about a product that has no rows from it either has nothing
    # to do, and finding that out cost a SELECT per product per source — 12,000
    # of them on a 6,000-item master.
    has_rows = _products_with_live_rows(session, organization_id, products)

    for number, product in enumerate(products, start=1):
        if run.available:
            outcome = run.outcomes.get(number)
            if outcome is None:
                report.rows_without_outcome += 1
            else:
                extraction = claims_from_decode(outcome)
                _tally(report, extraction.refused)
                if extraction.claims:
                    report.names_decoded += 1
                if extraction.claims or (product.product_id, DECODED_NAME) in has_rows:
                    report.written += write_claims(
                        session, organization_id, product.product_id, DECODED_NAME,
                        extraction.claims,
                        source_ref=product.name,
                        decoder_version=run.ruleset_checksum or None)

        if catalogue is not None:
            report.written += _decorate_from_catalogue(
                session, organization_id, product, catalogue, has_rows, report)

    return report


def _decorate_from_catalogue(session: Session, organization_id: str,
                             product: models.Product, catalogue,
                             has_rows: set, report: DecorationReport) -> WriteResult:
    """The CATALOGUE_LINK half for one product.

    Three states, and they are not the same:

    * no ``pie_record_id`` — this item is not a known catalogue record, so the
      source says nothing about it. Anything it said before is retracted, the
      way ``_link_catalog`` clears a link that no longer resolves;
    * a ``pie_record_id`` the catalogue does not hold — a link written under a
      superseded corpus. Counted separately, and retracted for the same reason:
      the catalogue is loaded and does not have it;
    * a record — its facts, with the ``field_meta`` the decode path never sees,
      stamped with the record's own ``ruleset_checksum`` rather than the running
      catalogue's, so a row says which catalogue actually produced it.
    """
    record = None
    if product.pie_record_id:
        record = catalogue.lookup_record(product.pie_record_id)
        if record is None:
            report.catalogue_links_unresolved += 1

    if record is None:
        if (product.product_id, CATALOGUE_LINK) not in has_rows:
            return WriteResult()
        return write_claims(session, organization_id, product.product_id,
                            CATALOGUE_LINK, ())

    extraction = claims_from_catalogue_record(record)
    _tally(report, extraction.refused)
    if extraction.claims:
        report.catalogue_records_read += 1
    elif (product.product_id, CATALOGUE_LINK) not in has_rows:
        return WriteResult()
    return write_claims(
        session, organization_id, product.product_id, CATALOGUE_LINK,
        extraction.claims,
        source_ref=str(record.get("record_id") or product.pie_record_id),
        decoder_version=str(record.get("ruleset_checksum") or "") or None)


def _products_with_live_rows(session: Session, organization_id: str,
                             products: List[models.Product]) -> set:
    """``{(product_id, source_kind)}`` that already hold a live row."""
    stmt = select(models.ProductAttributeValue.product_id,
                  models.ProductAttributeValue.source_kind).where(
        models.ProductAttributeValue.organization_id == organization_id,
        models.ProductAttributeValue.superseded_at.is_(None)).distinct()
    # Bounded to the batch when the caller named one; unbounded when it is the
    # whole organization, where an IN list of every product id is worse than no
    # filter at all.
    if len(products) < 500:
        stmt = stmt.where(models.ProductAttributeValue.product_id.in_(
            [p.product_id for p in products]))
    return {(pid, kind) for pid, kind in session.execute(stmt)}


def _catalogue():
    """The loaded PIE catalogue, or ``None`` when there is not one.

    Imported inside the call, the way ``ingestion.sync`` imports it: the module
    loads a 13 MB index on first use, and a package that only ever writes rows
    should not pay for it at import time.
    """
    from ..pie_service import pie_service

    try:
        return pie_service if pie_service.catalog_available else None
    except Exception:  # noqa: BLE001 — an absent engine must not fail the run
        log.warning("attributes: the catalogue could not be loaded", exc_info=True)
        return None
