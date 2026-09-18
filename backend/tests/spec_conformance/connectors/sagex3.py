"""Sage X3, driven over its own Syracuse SData rows.

X3 publishes a summary representation for listing and a details representation
per record, and the source uses both: it lists ``$query`` rows, windows on the
summary's date, then fetches ``$details`` for the lines. The stub answers both.

Properties wear the ``_0`` suffix Syracuse emits, because ``field_of`` exists to
read exactly that and a flat fixture would exercise a dress this ERP does not
send. Money is stated as strings, for the reason the other fixtures give.

``CREDAT``/``CRETIM`` are stated as X3 states them — a date column and a time
column, neither carrying a zone — because that split is the whole reason this
connector's declared source time does not survive normalisation. A fixture that
quietly supplied one zoned ISO datetime would report clean about it.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from app.ingestion.erp import sage

CUSTOMERS = [{"BPCNUM_0": "VAYA", "BPCNAM_0": "Vaya Precision LLC", "BPCSTA_0": "1"}]

VENDORS = [{"BPSNUM_0": "KMT", "BPSNAM_0": "Kennametal Inc"}]

ITEMS = [{"ITMREF_0": "CNMG120408MP", "ITMDES1_0": "CNMG 120408 MP",
          "STU_0": "PCS", "ITMSTA_0": "1"}]

#: ``NETPRI`` is the net unit price X3 states after the line discount and
#: ``GROPRI`` the gross one it came from; the translator reads ``NETPRI``
#: first. ``AMTNOTLIN`` is the line's own post-discount amount.
_SALES_LINE = {"SIDLIN_0": "1000", "ITMREF_0": "CNMG120408MP", "QTY_0": "10",
               "NETPRI_0": "225.00", "GROPRI_0": "250.00",
               "AMTNOTLIN_0": "2250.00", "ITMDES1_0": "CNMG 120408 MP"}

_PURCHASE_LINE = {"PIDLIN_0": "1000", "ITMREF_0": "CNMG120408MP", "QTY_0": "12",
                  "NETPRI_0": "142.50", "GROPRI_0": "158.00",
                  "AMTNOTLIN_0": "1710.00", "ITMDES1_0": "CNMG 120408 MP"}

SALES_INVOICE = {"NUM_0": "SIN0004412", "BPCINV_0": "VAYA",
                 "ACCDAT_0": "2026-06-04", "DUDDAT_0": "2026-07-04",
                 "AMTNOT_0": "2250.00", "CUR_0": "USD",
                 "UPDDAT_0": "2026-06-05", "CREDAT_0": "2026-06-04",
                 "CRETIM_0": "091422",
                 "SIH4": [_SALES_LINE]}

PURCHASE_INVOICE = {"NUM_0": "PIN0009981", "BPSINV_0": "KMT",
                    "ACCDAT_0": "2026-05-18", "DUDDAT_0": "2026-06-17",
                    "AMTNOT_0": "1710.00", "CUR_0": "USD",
                    "UPDDAT_0": "2026-05-19", "CREDAT_0": "2026-05-18",
                    "CRETIM_0": "075510",
                    "PIH4": [_PURCHASE_LINE]}

SALES_ORDERS = [{"NUM_0": "SON0005510", "BPCORD_0": "VAYA",
                 "ORDDAT_0": "2026-05-02", "SHIDAT_0": "2026-05-30",
                 "ORDSTA_0": "2", "ORDNOT_0": "2250.00"}]

PURCHASE_ORDERS = [{"NUM_0": "PON0003310", "BPSORD_0": "KMT",
                    "ORDDAT_0": "2026-04-28", "RCPDAT_0": "2026-05-15",
                    "ORDSTA_0": "2", "ORDNOT_0": "1710.00"}]

_QUERY = {"BPCUSTOMER": CUSTOMERS, "BPSUPPLIER": VENDORS, "ITMMASTER": ITEMS,
          "SINVOICE": [SALES_INVOICE], "PINVOICE": [PURCHASE_INVOICE],
          "SORDER": SALES_ORDERS, "PORDER": PURCHASE_ORDERS}

_DETAILS = {("SINVOICE", "SIN0004412"): SALES_INVOICE,
            ("PINVOICE", "PIN0009981"): PURCHASE_INVOICE}


class _Client:
    def query(self, entity: str) -> Iterator[dict[str, Any]]:
        rows = _QUERY.get(entity)
        if rows is None:
            raise AssertionError(f"the Sage X3 fixture has no rows for: {entity}")
        return iter(rows)

    def details(self, entity: str, key: str) -> Optional[dict[str, Any]]:
        return _DETAILS.get((entity, key))


def build_source() -> sage.SageX3Source:
    return sage.SageX3Source(_Client())
