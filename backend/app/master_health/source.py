"""Read an item-master export into rows, under a column profile.

Why this does not reuse pie-parser's ``CsvAdapter``/``XlsxAdapter``, which read
the same two file formats under the same kind of run-profile mapping and were
the first thing checked (``CLAUDE.md`` §2): those adapters skip any row whose
mapped ``record_id`` is blank, because in a nomenclature corpus a row with no
material number is a trailing blank line. Here it is a **finding** — an item
with no SKU is precisely the kind of row a master-health report exists to
count, and an adapter that drops it would shrink the denominator by exactly the
rows the report is about, silently, in the direction that flatters the result.

Two different contracts, then, not two implementations of one. What *is* reused
is the engine's ``RawRecord`` contract itself (:mod:`.geometry` builds them
directly from these rows), so nothing re-states what the parser takes as input.

Money is ``Decimal`` from the moment it leaves the file, never float.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .profile import ColumnProfile

#: Values a person types into a spreadsheet to mean "nothing". Compared
#: case-folded and stripped. ``0`` is deliberately absent: a zero rate is a real
#: number that happens to be wrong, and the report distinguishes "no rate
#: recorded" from "a rate of zero recorded" because they are different fixes.
BLANK_TOKENS: frozenset[str] = frozenset({"", "-", "--", "n/a", "na", "null", "none", "nil"})


def is_blank(value: Any) -> bool:
    return value is None or str(value).strip().casefold() in BLANK_TOKENS


@dataclass(frozen=True)
class MasterRow:
    """One item as the export words it, with every role read or absent.

    ``None`` means the cell was blank *or* the profile has no column for that
    role; the two are distinguished by the profile, which the report carries,
    rather than by a second sentinel per field.
    """

    row_number: int
    sku: Optional[str]
    name: Optional[str]
    manufacturer: Optional[str]
    rate: Optional[Decimal]
    stock: Optional[Decimal]
    hsn: Optional[str]
    uom: Optional[str]
    #: The rate cell exactly as written, kept only so a number that failed to
    #: parse can be reported as unreadable rather than as absent.
    rate_raw: Optional[str] = None
    stock_raw: Optional[str] = None

    @property
    def stock_value(self) -> Optional[Decimal]:
        """Quantity on hand times SELLING price. Never a cost.

        ``None`` when either side is unknown — the report counts those rows as
        unvalued rather than as worth zero, which is the same refusal
        ``CLAUDE.md`` §1 makes about ``sum(... or 0)`` over rows holding None.
        """
        if self.rate is None or self.stock is None:
            return None
        return self.rate * self.stock


class SourceError(ValueError):
    """An export that could not be read as a table."""


def _text(value: Any) -> Optional[str]:
    if is_blank(value):
        return None
    return str(value).strip()


def _number(value: Any) -> tuple[Optional[Decimal], Optional[str]]:
    """A quantity or a price, as ``(Decimal, unreadable_raw)``.

    Exactly one of the two is set. A cell that is present but not a number
    comes back as ``(None, "1,2,3")`` so the report can say "unreadable" — a
    third state that is neither a value nor an absence, and the one a
    spreadsheet actually produces.
    """
    if is_blank(value):
        return None, None
    raw = str(value).strip()
    cleaned = raw.replace(",", "").replace("₹", "").replace("$", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):   # accounting negatives
        cleaned = "-" + cleaned[1:-1].strip()
    try:
        return Decimal(cleaned), None
    except (InvalidOperation, ValueError):
        return None, raw


def _rows_from_csv(path: Path) -> tuple[List[str], List[Dict[str, Any]]]:
    # utf-8-sig: a Zoho export opened and re-saved by Excel carries a BOM, and
    # the BOM lands inside the first header name, so the first mapped column
    # goes missing with an error blaming the profile.
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        return headers, [dict(r) for r in reader]


def _rows_from_xlsx(path: Path, sheet: Optional[str]) -> tuple[List[str], List[Dict[str, Any]]]:
    from openpyxl import load_workbook  # local import: optional dependency

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        try:
            header_row = next(it)
        except StopIteration as exc:
            raise SourceError(f"{path}: the worksheet is empty") from exc
        headers = [str(h).strip() if h is not None else "" for h in header_row]
        rows = []
        for values in it:
            if all(v is None or str(v).strip() == "" for v in values):
                continue  # a wholly empty spreadsheet row is not an item
            rows.append({headers[i]: values[i] for i in range(len(headers))
                         if i < len(values) and headers[i]})
        return [h for h in headers if h], rows
    finally:
        wb.close()


def read_export(path: Path, profile: ColumnProfile,
                sheet: Optional[str] = None) -> tuple[List[MasterRow], Sequence[str]]:
    """Every data row of an export, under ``profile``. Returns (rows, headers).

    Every row is returned, including one with no SKU and one with no name.
    Nothing is filtered here — filtering is a judgement, and the judgements
    this report makes are all in :mod:`.analysis`, where they are visible.
    """
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"no such export file: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        headers, raw_rows = _rows_from_csv(path)
    elif suffix in (".xlsx", ".xlsm"):
        headers, raw_rows = _rows_from_xlsx(path, sheet)
    else:
        raise SourceError(
            f"{path}: unsupported export format '{suffix}'. This reads .csv and "
            f".xlsx — export the item list as one of those."
        )
    if not headers:
        raise SourceError(f"{path}: no header row, so no column can be mapped")

    profile.validate_against(headers)

    def col(role: str, row: Dict[str, Any]) -> Any:
        header = profile.header(role)
        return None if header is None else row.get(header)

    rows: List[MasterRow] = []
    for offset, raw in enumerate(raw_rows, start=2):   # row 1 is the header
        rate, rate_raw = _number(col("rate", raw))
        stock, stock_raw = _number(col("stock", raw))
        rows.append(MasterRow(
            row_number=offset,
            sku=_text(col("sku", raw)),
            name=_text(col("name", raw)),
            manufacturer=_text(col("manufacturer", raw)),
            rate=rate, stock=stock,
            hsn=_text(col("hsn", raw)),
            uom=_text(col("uom", raw)),
            rate_raw=rate_raw, stock_raw=stock_raw,
        ))
    return rows, headers
