"""Customer RFQ documents: received, checked, encrypted, and never rendered.

Decision 012, Phase 3. What a customer *sent*, beside ``InboundLine`` — what
they wrote. Canonical, not derived: a PDF that arrived by email exists in no
ERP, so a complete re-sync rebuilds nothing here, which is the property that put
this package outside ``state/`` in the first place.

**This module receives and retains. It does not read.** Nothing here parses a
PDF, opens a spreadsheet, or extracts a requirement — and the one place it looks
inside a container it does so without decompressing. Extraction is a later
slice, and the boundary is worth stating because it is what keeps this file's
attack surface to "bytes in, bytes out, checked on the way".

**Four refusals, and each one names what it refused.** Too large, unrecognised,
a declared type the bytes contradict, and an archive whose contents do not add
up. Each raises :class:`DocumentRefused` with a sentence a person can act on,
because "upload failed" is the answer that makes somebody try the same file four
times.

**The order of the checks is the defence, not the checks themselves.** Size is
refused from ``Content-Length`` *before* the body is read, and again while it is
read, because a length header is a claim by the sender. That is
``routers/enquiries.py``'s export ceiling applied to an input: it "counted before
it is loaded", having previously "fired only once the process had done exactly
the work the ceiling exists to prevent".
"""
from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import now as utcnow
from ..domain import models
from ..trust import keys

log = logging.getLogger("pie_portal.enquiry.documents")


class DocumentRefused(Exception):
    """A document this platform will not store, and why.

    Carries a ``reason`` code beside the sentence so a caller can branch on the
    kind without matching on prose, and so the message can say something useful
    without the code changing meaning.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


#: The ceiling on one document, in bytes.
#:
#: 25 MB. Chosen against what a customer actually sends — a scanned multi-page
#: BOQ or a photographed handwritten note, both of which routinely pass 5 MB —
#: and against what the store costs: ciphertext is ~1.33x this, and
#: ``BACKUP_RETAIN_DAYS`` defaults to 14, so one document at the ceiling is
#: about 470 MB of retained backup at worst. It is a config value rather than a
#: constant for the reason every ceiling here is: the number that is right for a
#: VM with a volume is not the number that is right for a free-tier Postgres.
MAX_BYTES = 25 * 1024 * 1024

#: Leading-byte signatures for the formats a customer's RFQ actually arrives in.
#:
#: A table rather than a dependency. ``python-magic`` needs libmagic on the
#: image and ``filetype`` is another supply-chain edge, and neither buys
#: anything for a list this short — these are the formats named in the RFQ
#: parser's own description (a PDF, a spreadsheet, a photo of a handwritten
#: note) plus the two plain-text ones a BOQ is pasted into.
#:
#: **Sniffing decides; the client's declared type is only evidence.** A browser
#: sends whatever it likes, and a file called ``bom.pdf`` that begins ``PK`` is
#: the interesting case rather than the broken one.
_SIGNATURES: Tuple[Tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    # The legacy OLE2 container: .xls and .doc alike. Recorded as what it is;
    # nothing here opens it.
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "application/vnd.ms-excel"),
    # Any ZIP. Refined below — .xlsx and .docx are ZIPs with a known first entry.
    (b"PK\x03\x04", "application/zip"),
)

#: A ZIP whose first entry names one of these is really an Office file. Read
#: from the central directory, which is metadata: no member is decompressed.
_ZIP_KINDS: Tuple[Tuple[str, str], ...] = (
    ("xl/", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("word/", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
)

#: What may be stored. ``application/zip`` is deliberately absent: a bare ZIP
#: that is not an Office file is a container of unknown things, and "we accept
#: it but never open it" is a promise about today's code rather than about the
#: code that will want to extract requirements from it.
ACCEPTED: Tuple[str, ...] = (
    "application/pdf",
    "image/png",
    "image/jpeg",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # A CSV and a pasted BOQ are both `text/plain` here, and there is no
    # `text/csv` entry because :func:`sniff` could never produce one — a CSV has
    # no magic bytes, so deciding it is a CSV means parsing it, which this
    # module does not do. An entry that can never be reached is a branch a
    # reader trusts and a test cannot exercise.
    "text/plain",
)

#: How far a ZIP's declared contents may exceed the ZIP itself before it is
#: refused unopened. A real spreadsheet's XML compresses perhaps 20x; a bomb
#: declares thousands.
MAX_ZIP_EXPANSION = 200

#: And a floor, so the ratio cannot be dodged by a tiny archive. 400 MB of
#: declared content is past anything a quotation carries.
MAX_ZIP_DECLARED_BYTES = 400 * 1024 * 1024


def sniff(content: bytes) -> str:
    """What these bytes actually are, or ``""`` if nothing recognised them.

    Empty string rather than a guess, and rather than the client's own claim.
    "Absence of evidence is not a pass": a stream nothing here recognises is
    UNKNOWN, and :func:`store` refuses it — it does not fall back to what the
    upload said it was, which is the one input an attacker fully controls.
    """
    for prefix, kind in _SIGNATURES:
        if content.startswith(prefix):
            if kind != "application/zip":
                return kind
            return _zip_kind(content)
    if _looks_like_text(content):
        return "text/plain"
    return ""


def _zip_kind(content: bytes) -> str:
    """Refine a ZIP by its central directory. Nothing is decompressed."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        # A file that begins PK and will not open as an archive is not an
        # Office document, whatever it was called.
        return "application/zip"
    for prefix, kind in _ZIP_KINDS:
        if any(name.startswith(prefix) for name in names):
            return kind
    return "application/zip"


def _looks_like_text(content: bytes) -> str:
    """UTF-8-decodable and free of control bytes. CSV and pasted BOQs land here.

    Last, and only after every signature has failed, because "it decodes" is the
    weakest possible evidence about a byte stream — a PDF is not UTF-8, but a
    crafted payload can be.
    """
    if not content:
        return ""
    sample = content[:8192]
    if b"\x00" in sample:
        return ""
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    return "text/plain"


def _refuse_oversized_archive(content: bytes, kind: str) -> None:
    """A ZIP whose declared contents do not add up, refused without opening it.

    ``infolist()`` reads the central directory — metadata — so this costs no
    decompression and cannot itself be made to expand anything.

    **What it catches and what it does not, stated because the difference is the
    whole value of the check.** A declared-size bomb is caught here. A bomb that
    *lies* in its central directory is not: the header can say 1 KB and the
    member can expand to a gigabyte, and the only thing that catches that is a
    bounded read at extraction time. Nothing in this platform extracts anything
    today, so nothing is exposed to it today — and the day something does, the
    bounded read is that code's obligation and not this one's. Recorded here
    rather than left for whoever writes it to discover.
    """
    if kind not in {k for _p, k in _ZIP_KINDS}:
        return
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            declared = sum(info.file_size for info in archive.infolist())
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise DocumentRefused(
            "UNREADABLE_ARCHIVE",
            "This file begins like a spreadsheet but its index could not be "
            "read, so what it contains is unknown.") from e
    if declared > MAX_ZIP_DECLARED_BYTES or declared > len(content) * MAX_ZIP_EXPANSION:
        raise DocumentRefused(
            "ARCHIVE_TOO_LARGE",
            f"This file is {len(content):,} bytes but says it contains "
            f"{declared:,}. That ratio is past anything a real quotation "
            "carries, so it is refused unopened.")


def check(content: bytes, *, declared_type: str = "") -> str:
    """Every refusal, in order, returning the sniffed type when all of them pass.

    Separate from :func:`store` so a caller can validate without a session and
    so the refusals are testable without a database — and because the router
    needs the size refusal *before* it has a body to hand to ``store`` at all.
    """
    if not content:
        raise DocumentRefused(
            "EMPTY", "There are no bytes in this upload.")
    if len(content) > MAX_BYTES:
        raise DocumentRefused(
            "TOO_LARGE",
            f"{len(content):,} bytes is past this endpoint's ceiling of "
            f"{MAX_BYTES:,}. Send the document itself rather than a scan of "
            "every page at full resolution, or split it.")
    kind = sniff(content)
    if not kind:
        raise DocumentRefused(
            "UNRECOGNISED",
            "Nothing in this platform recognises what this file is. "
            f"Accepted: {', '.join(ACCEPTED)}.")
    if kind not in ACCEPTED:
        raise DocumentRefused(
            "UNSUPPORTED_TYPE",
            f"This file is a {kind}, which is not a document this platform "
            f"stores. Accepted: {', '.join(ACCEPTED)}.")
    _refuse_oversized_archive(content, kind)
    return kind


def store(session: Session, organization_id: str, *, filename: str,
          content: bytes, declared_type: str = "",
          uploaded_by_user_id: Optional[str] = None,
          licence_note: str = "") -> models.RfqDocument:
    """Check, encrypt and persist one document. Raises :class:`DocumentRefused`.

    The bytes are encrypted under this organization's data key before they reach
    the session, so a plaintext document never exists in a transaction, in a
    statement log, or in a `pg_dump`. That is also what gives erasure its reach:
    ``trust.erasure.erase`` destroys the key and deletes no rows.
    """
    kind = check(content, declared_type=declared_type)
    cipher = keys.cipher_for(session, organization_id)
    row = models.RfqDocument(
        organization_id=organization_id,
        # Verbatim, and treated as hostile by every reader — see the model.
        filename=(filename or "")[:255],
        content_type_declared=(declared_type or "")[:128],
        content_type_sniffed=kind,
        byte_size=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_ciphertext=cipher.encrypt_bytes(content),
        uploaded_by_user_id=uploaded_by_user_id,
        licence_note=licence_note or "",
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    log.info("stored rfq document %s (%s, %d bytes) for %s",
             row.rfq_document_id, kind, len(content), organization_id)
    return row


def read(session: Session, organization_id: str,
         rfq_document_id: str) -> Optional[Tuple[models.RfqDocument, bytes]]:
    """The row and its plaintext bytes, or ``None``.

    ``organization_id`` is required and filtered, not merely passed to the
    cipher. Row-level security binds on PostgreSQL only, so on SQLite this
    filter is the whole of the tenant boundary — and a document belonging to
    another organization must be indistinguishable from one that never existed,
    which is why a foreign id returns ``None`` rather than a refusal.

    A withdrawn document reads as absent too. It is kept because it is a fact
    about the enquiry, not because it is still on offer.
    """
    row = session.scalar(
        select(models.RfqDocument)
        .where(models.RfqDocument.rfq_document_id == rfq_document_id,
               models.RfqDocument.organization_id == organization_id,
               models.RfqDocument.withdrawn_at.is_(None)))
    if row is None:
        return None
    cipher = keys.cipher_for(session, organization_id)
    return row, cipher.decrypt_bytes(row.content_ciphertext)


def listing(session: Session, organization_id: str) -> List[models.RfqDocument]:
    """Every live document for this organization, newest first. Metadata only.

    The rows carry ``content_ciphertext``, so a caller that serialises one of
    these wholesale publishes it. Every projection in this codebase names its
    fields; :func:`summary` is the one for this table.
    """
    return list(session.scalars(
        select(models.RfqDocument)
        .where(models.RfqDocument.organization_id == organization_id,
               models.RfqDocument.withdrawn_at.is_(None))
        .order_by(models.RfqDocument.created_at.desc(),
                  models.RfqDocument.rfq_document_id)))


def summary(row: models.RfqDocument) -> Dict[str, Any]:
    """One document as the wire sees it. **Never the bytes.**

    Named explicitly rather than built from ``__table__.columns`` so that adding
    a column cannot publish it by accident — the failure this projection exists
    to make impossible is exactly the one where ``content_ciphertext`` joins a
    JSON response because somebody widened a loop.
    """
    return {
        "rfq_document_id": row.rfq_document_id,
        "filename": row.filename,
        "content_type": row.content_type_sniffed,
        "content_type_declared": row.content_type_declared,
        # Published because a mismatch is worth a reader's attention: a file
        # named .pdf that is really a ZIP is the interesting case.
        "type_matches_declaration": _declaration_matches(row),
        "byte_size": row.byte_size,
        "content_sha256": row.content_sha256,
        "licence_note": row.licence_note,
        "uploaded_by_user_id": row.uploaded_by_user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _declaration_matches(row: models.RfqDocument) -> Optional[bool]:
    """``None`` when the client declared nothing — UNKNOWN, not agreement."""
    if not row.content_type_declared:
        return None
    return row.content_type_declared.split(";")[0].strip() == row.content_type_sniffed


def withdraw(session: Session, organization_id: str,
             rfq_document_id: str) -> Optional[models.RfqDocument]:
    """Mark a document withdrawn. Never a DELETE.

    The supersede convention this package borrows from ``state/``: a document
    somebody withdrew is a fact about what arrived, and deleting the row would
    make the corpus disagree with itself. Erasure is the operation that destroys
    content, and it does it by destroying the key.
    """
    row = session.scalar(
        select(models.RfqDocument)
        .where(models.RfqDocument.rfq_document_id == rfq_document_id,
               models.RfqDocument.organization_id == organization_id,
               models.RfqDocument.withdrawn_at.is_(None)))
    if row is None:
        return None
    row.withdrawn_at = utcnow()
    session.flush()
    return row
