"""Read one uploaded item-master or price-list export; keep the nomenclature.

A company's decoded catalogue is built from files a person exports out of an
ERP or receives from a manufacturer — an item master, a price list, a range
extension. Those arrive as CSV or as an Excel workbook, with the part number in
whichever column that system happens to call it, and with commercial columns
beside it. This module is the one place that turns such a file into the three
columns pie-parser's ``CsvAdapter`` reads.

**Nomenclature only, structurally rather than by filtering.** The output
carries exactly the mapped record id, description and grade. Every other column
is absent from it because it was never written, not because something removed
it — so a price column cannot reach the catalogue through a path nobody thought
to filter. That is the same reason CLAUDE.md §1 gives for the server omitting
cost rather than the browser hiding it. :func:`ingest_report` still *names* the
money-shaped columns it saw, because "12 columns ignored" is not an answer a
person can check and "including New ZCNC Price, MRP" is.

**The pack says what the headers must be, and this module says what they were.**
``manifest.yaml`` declares ``columns: {record_id, description, grade}`` — an
organisation fact, the names *that* export uses. A file whose headers differ is
not a broken file; it is a file this company needs a mapping for. So the
mapping is stored per source and applied here, and the emitted CSV uses the
pack's own declared names. The pack keeps saying which headers it reads;
nothing downstream learns a second convention.

Reading a workbook needs no new dependency: ``openpyxl`` is already in
``requirements.txt`` — pie-parser pulls it in — and is read here in
``read_only`` mode with ``data_only=True``, so a price list full of formulas
yields the values a person sees rather than ``=B2*0.85``.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from typing import Any, Dict, Iterator, List, Optional, Sequence

log = logging.getLogger("pie_portal.item_master")

#: The three roles a pack declares columns for. ``grade`` is optional in a way
#: the other two are not: a row with no part number cannot be identified and a
#: row with no description cannot be decoded, but a master that keeps grade in
#: the description is common and decodes fine.
ROLES = ("record_id", "description", "grade")
REQUIRED_ROLES = ("record_id", "description")

#: How many leading rows are considered when looking for the headings. A price
#: list's preamble is a title, a blank line and a revision date; thirty is far
#: past any of that, and bounds the read of a file whose first data row is
#: hundreds of thousands of rows from the end.
_HEADER_SCAN = 30

#: Said the same way wherever a header row cannot be found, because the two
#: readers reach the same dead end and one of them phrasing it differently
#: would read as two different problems.
_NO_HEADER = (
    "No header row was found — no row in this file has two or more non-empty "
    "cells, so there are no column names to map."
)

#: How many rows a pack-fit trial reads. Enough for a parse rate to mean
#: something, small enough that trying every shipped pack stays inside one
#: request.
SAMPLE_ROWS = 500

#: What a header has to contain for this module to *guess* it fills a role.
#: Ordered: the first pattern that matches a header wins, so the specific
#: phrasings come before the generic ones. Guesses only — a wrong guess is
#: corrected by storing a mapping, and :func:`suggest_mapping` says which
#: headers it had to choose between.
_SUGGESTIONS: Dict[str, Sequence[str]] = {
    "record_id": (
        r"^mm\s*#?$", r"material\s*(number|no|code|#)", r"\bsku\b",
        r"part\s*(number|no|code|#)", r"item\s*(code|number|no|#)",
        r"catalog(ue)?\s*(number|no|code|#)", r"^code$", r"^item\s*id$",
        r"^article\b", r"^product\s*(code|number|no|id)",
        r"^ref(erence)?\s*(no|number|code|#)?$",
    ),
    "description": (
        r"material\s*description", r"item\s*(description|name)",
        r"product\s*(description|name)", r"^description$", r"short\s*text",
        r"^particulars$", r"^designation$", r"^name$", r"descr",
    ),
    "grade": (
        r"^grade$", r"(carbide|material|insert)\s*grade", r"grade\s*(code)?$",
    ),
}

#: Header words that mark a column as commercial. Used for **reporting only** —
#: nothing is dropped because it matched this, and nothing is kept because it
#: did not. Every unmapped column is dropped either way, which is why this list
#: being incomplete is a worse message rather than a leak.
_MONEY_WORDS = (
    "price", "cost", "mrp", "rate", "amount", "value", "discount", "margin",
    "landed", "moq", "stock", "qty", "quantity", "on hand", "onhand", "wsp",
    "net", "gross", "tax", "gst", "vat", "inr", "usd", "eur", "currency",
    "list", "nlc", "dp",
)


class ItemMasterError(ValueError):
    """This file cannot be read as an item-master export, with the reason.

    Carries a sentence written for the person who uploaded the file, because it
    is shown to them verbatim: a message naming the header it wanted and the
    headers the file actually has is one somebody can act on alone.
    """


class Table:
    """One uploaded file: its header row eagerly, its data rows on demand.

    Everything is a string by the time it leaves here. A workbook holds typed
    cells and a CSV does not, and a part number is exactly the value Excel
    likes to hand back as a float (``1234567.0``) — so the conversion happens
    once, in :func:`_text`, rather than in each caller.

    **The rows are a stream, not a list, and that is measured rather than
    tidiness.** Materialising a 33 MB CSV as lists of cells peaked at 394 MB of
    resident memory — for one file, on an upload an owner can repeat, on a
    container sized in hundreds of megabytes. Holding the bytes and re-reading
    them per pass costs a few seconds of CPU and bounds the memory at the file
    itself. :meth:`rows` may therefore be iterated more than once, and each
    call starts again from the first data row.
    """

    __slots__ = ("headers", "_raw", "_delimiter", "_workbook", "_skip")

    def __init__(self, headers: List[str], raw: bytes, *,
                 delimiter: str = ",", workbook: bool = False,
                 skip: int = 1) -> None:
        self.headers = headers
        self._raw = raw
        self._delimiter = delimiter
        self._workbook = workbook
        #: How many leading rows are preamble plus the header itself. A price
        #: list opens with a title and a blank line before its headings.
        self._skip = skip

    def rows(self) -> "Iterator[List[str]]":
        """Every data row, streamed, from the first row after the header."""
        if self._workbook:
            yield from _workbook_rows(self._raw, self._skip)
        else:
            yield from _csv_rows(self._raw, self._delimiter, self._skip)

    def column(self, name: str) -> Optional[int]:
        """The index of the column with this exact header, case-insensitively.

        Case- and space-insensitive because a person retyping ``MM#`` as
        ``mm #`` has not made a different file, and a mapping refused on that
        basis reads as the platform being pedantic rather than careful.
        """
        wanted = _key(name)
        for i, header in enumerate(self.headers):
            if _key(header) == wanted:
                return i
        return None


def _key(value: str) -> str:
    """A header reduced to what a comparison should care about."""
    return re.sub(r"[\s_\-]+", " ", str(value or "")).strip().lower()


def _text(value: Any) -> str:
    """One cell as the text a part number or a description actually is.

    ``1234567.0`` is the failure this exists for: openpyxl types a numeric part
    number as a float, ``str()`` gives it a decimal point, and the catalogue
    then holds a record id that matches nothing the customer will ever type. An
    integral float loses its ``.0``; a genuinely fractional number keeps it.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value).strip()


def is_workbook(filename: str, content_type: str = "") -> bool:
    """Whether these bytes should be read as an Excel workbook.

    By name and declared type rather than by sniffing: both are supplied by the
    browser for a file a person picked, and a mislabelled file fails in
    :func:`read_table` with a message that names the mismatch — which is more
    useful than a silent guess that reads a workbook as CSV and reports that
    every row is malformed.
    """
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xltx")):
        return True
    return "spreadsheetml" in (content_type or "").lower()


def read_table(raw: bytes, filename: str = "", content_type: str = "") -> Table:
    """The uploaded bytes as a header row and a re-readable row stream.

    Raises :class:`ItemMasterError` for anything that is not a table a person
    could have meant: an empty file, a workbook with no sheet, bytes that are
    not text, a file with no header row. Every one of those is refused at
    upload rather than stored and failed at build time — a corpus that is
    accepted and then cannot be used leaves somebody holding a file with no
    statement of what is wrong with it.

    Only the leading rows are read here, so this is cheap for a large file: the
    header is found, and the rows are left to :meth:`Table.rows`.
    """
    if not raw or not raw.strip():
        raise ItemMasterError("The file is empty.")
    if is_workbook(filename, content_type):
        return _read_workbook(raw, filename)
    if raw[:2] == b"PK":
        raise ItemMasterError(
            f"{filename or 'This file'} looks like an Excel workbook but is "
            "not named like one. Save it as .xlsx (or export it as CSV) and "
            "upload it again.")
    return _read_csv(raw)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ItemMasterError(
            "The file is not UTF-8 text. Export it as UTF-8 CSV — an item "
            "master in another encoding decodes to the wrong characters "
            "rather than failing outright.") from None


def _delimiter_of(text: str) -> str:
    """Which separator this file uses, counted over its first rows.

    Counted rather than sniffed with `csv.Sniffer`, and over several rows rather
    than the first: a price list opens with a title line that has no separators
    in it at all, and the Sniffer answers from statistics over a whole sample so
    it can change its mind when a description happens to contain punctuation.
    A European export is semicolon-separated, and reading it as comma-delimited
    gives one column whose header is the whole row — which then fails as "no
    such column" instead of "wrong delimiter".
    """
    head = [line for line in text.split("\n")[:20] if line.strip()]

    def separators(candidate: str) -> int:
        return max((line.count(candidate) for line in head), default=0)

    best = max(",;\t|", key=separators)
    return best if separators(best) > 0 else ","


def _header_row(rows: Iterator[List[str]]) -> "tuple[int, List[str]]":
    """Which of the leading rows is the headings, and how far in it sits.

    **The widest of the first rows, earliest wins.** Not simply the first row
    with two filled cells: a manufacturer's price list opens with a title, a
    blank line and then ``Effective | 01-04-2026`` — two filled cells, and
    taking it as the header made every real column unmappable and the file look
    broken. The headings are the widest row in the preamble, and taking the
    earliest of the widest keeps a plain CSV — where the header and every data
    row are the same width — answering with its first row.
    """
    best_index, best_row, best_width = -1, [], 1
    for index, row in enumerate(rows):
        if index >= _HEADER_SCAN:
            break
        width = len([c for c in row if str(c).strip()])
        if width > best_width:
            best_index, best_row, best_width = index, [_text(c) for c in row], width
    if best_index < 0:
        raise ItemMasterError(_NO_HEADER)
    return best_index, best_row


def _read_csv(raw: bytes) -> Table:
    text = _decode(raw)
    delimiter = _delimiter_of(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    index, headers = _header_row(reader)
    return Table(headers, raw, delimiter=delimiter, skip=index + 1)


def _csv_rows(raw: bytes, delimiter: str, skip: int) -> Iterator[List[str]]:
    reader = csv.reader(io.StringIO(_decode(raw)), delimiter=delimiter)
    for index, row in enumerate(reader):
        if index < skip:
            continue
        if any(str(c).strip() for c in row):
            yield [_text(c) for c in row]


def _open_workbook(raw: bytes, filename: str = ""):
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - openpyxl is in requirements.txt
        raise ItemMasterError(
            "This deployment cannot read Excel workbooks (openpyxl is not "
            "installed). Export the file as CSV and upload that.") from None
    try:
        # read_only keeps a large price list from being materialised as cell
        # objects; data_only gives the cached value of a formula, which is what
        # a person reading the sheet sees.
        return load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 — openpyxl raises several unrelated types
        raise ItemMasterError(
            f"{filename or 'That file'} could not be opened as an Excel "
            f"workbook ({type(e).__name__}). If it is an older .xls file, "
            "save it as .xlsx or CSV first.") from e


def _read_workbook(raw: bytes, filename: str) -> Table:
    book = _open_workbook(raw, filename)
    try:
        sheet = book.worksheets[0] if book.worksheets else None
        if sheet is None:
            raise ItemMasterError("The workbook has no sheets.")
        if len(book.sheetnames) > 1:
            # Named in the log rather than merged: which sheet of a workbook is
            # the item master is a question this module cannot answer, and
            # concatenating a "Discontinued" tab into the catalogue is worse
            # than reading only the first.
            log.info("read the first of %d sheets (%s) from %s",
                     len(book.sheetnames), book.sheetnames[0], filename)
        index, headers = _header_row(
            [_text(c) for c in row]
            for row in sheet.iter_rows(values_only=True))
        return Table(headers, raw, workbook=True, skip=index + 1)
    finally:
        book.close()


def _workbook_rows(raw: bytes, skip: int) -> Iterator[List[str]]:
    book = _open_workbook(raw)
    try:
        sheet = book.worksheets[0]
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index < skip:
                continue
            cells = [_text(c) for c in row]
            if any(cells):
                yield cells
    finally:
        book.close()


def suggest_mapping(table: Table) -> Dict[str, Optional[str]]:
    """Which of this file's headers look like the three roles a pack reads.

    A guess, and named as one everywhere it surfaces. It exists because the
    common case is a file whose headers a person would recognise instantly, and
    making them map three columns by hand to express that is friction with no
    safety behind it. Where it guesses wrong, the mapping is corrected and
    stored; where it cannot guess at all, the upload is refused with the file's
    headers listed, which is the state a person can act on.
    """
    out: Dict[str, Optional[str]] = {role: None for role in ROLES}
    taken: set = set()
    for role, patterns in _SUGGESTIONS.items():
        for pattern in patterns:
            for header in table.headers:
                if not header or header in taken:
                    continue
                if re.search(pattern, _key(header)):
                    out[role] = header
                    taken.add(header)
                    break
            if out[role]:
                break
    return out


def money_columns(headers: Sequence[str]) -> List[str]:
    """The headers that look commercial, for the report — never for a filter."""
    out = []
    for header in headers:
        key = _key(header)
        if key and any(word in key for word in _MONEY_WORDS):
            out.append(header)
    return out


def check_mapping(table: Table, mapping: Dict[str, Optional[str]]) -> None:
    """Raise unless this mapping names columns the file has.

    Both required roles, by name, with the file's own headers in the message.
    The pack's declared column is not what is checked here — the *mapping* is,
    because the mapping is what says which of this file's columns fills the
    role the pack will read.
    """
    for role in REQUIRED_ROLES:
        name = (mapping or {}).get(role)
        if not name:
            raise ItemMasterError(
                f"No column in this file was recognised as the "
                f"{role.replace('_', ' ')}. Its columns are: "
                f"{', '.join(h for h in table.headers if h) or '(none)'}. "
                f"Choose which one it is.")
        if table.column(name) is None:
            raise ItemMasterError(
                f"This file has no column called {name!r} to read the "
                f"{role.replace('_', ' ')} from. Its columns are: "
                f"{', '.join(h for h in table.headers if h) or '(none)'}.")


def ingest_report(table: Table, mapping: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """What reading this file with this mapping keeps, and what it leaves out.

    Stored with the source row and shown on the catalogue screen. The dropped
    columns are named rather than counted for the reason at the top of this
    module: a count is not something a person can check against their own file.
    """
    mapped = {name for name in (mapping or {}).values() if name}
    mapped_keys = {_key(n) for n in mapped}
    dropped = [h for h in table.headers if h and _key(h) not in mapped_keys]
    return {
        "columns": [h for h in table.headers if h],
        "mapped": {role: (mapping or {}).get(role) for role in ROLES},
        "dropped_columns": dropped,
        "commercial_columns_dropped": money_columns(dropped),
    }


def emit_rows(table: Table, mapping: Dict[str, Optional[str]],
              report: Dict[str, Any],
              limit: Optional[int] = None) -> Iterator[List[str]]:
    """Yield each row as ``[record_id, description, grade]``, and count as it goes.

    A generator rather than a returned CSV, and the counts land in ``report``
    as it is exhausted. That shape is what keeps a 33 MB file from becoming
    hundreds of megabytes of resident lists (see :class:`Table`), and it lets
    the caller write straight to the file the parser will read.

    ``report`` gains ``rows_read``, ``rows_kept``, ``rows_skipped_blank_key``
    and ``sampled``. The skipped count is reported rather than silently
    tolerated: a file that loses half its rows for having no part number or no
    description is a file with the wrong column mapped, and the count is the
    only thing that says so.

    ``limit`` reads only the first N rows, for the pack-fit trial, and sets
    ``sampled`` so a count measured on a sample is never read as one measured
    on the file. Callers must exhaust the generator before trusting the counts.
    """
    check_mapping(table, mapping)
    index = {role: table.column(mapping.get(role) or "") for role in ROLES}

    read = kept = skipped = 0
    for row in table.rows():
        if limit is not None and read >= limit:
            report["sampled"] = True
            break
        read += 1

        def cell(role: str, row: List[str] = row) -> str:
            i = index[role]
            return row[i].strip() if (i is not None and i < len(row)) else ""

        record_id = cell("record_id")
        description = cell("description")
        if not record_id or not description:
            skipped += 1
            continue
        kept += 1
        yield [record_id, description, cell("grade")]

    report.setdefault("sampled", False)
    report.update({"rows_read": read, "rows_kept": kept,
                   "rows_skipped_blank_key": skipped})


def describe(table: Table, mapping: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """The full ingest report for one file: its columns, and its row counts.

    Runs a complete pass and throws the rows away, which is deliberate. The
    counts are what tell a person their mapping is right — "6,717 rows, none
    skipped" against "3 rows, 6,714 skipped" — and getting them at upload means
    a wrong mapping is visible before a build rather than after one. The pass is
    streamed, so it costs seconds of CPU and no memory.
    """
    report = ingest_report(table, mapping)
    for _ in emit_rows(table, mapping, report):
        pass
    return report
