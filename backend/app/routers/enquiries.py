"""Inbound demand: the door the enquiry corpus was missing.

``enquiry/capture.py`` has been complete since ``a7inbound`` — capture, decide,
supersede, export, all of it tested. Nothing called it. ``inbound_lines`` held
zero rows, so every text technique in ``14-machine-learning.md`` §5.17–§5.21 was
blocked on a table with no way in, and the block was invisible because an empty
table looks the same as a table nobody is filling.

This module is HTTP mapping and nothing else. Every rule lives one layer down,
where it is already tested: the closed channel set, the refusal to store an
empty ask, the supersede-don't-mutate discipline, the tenant scoping. What is
*new* here, and the reason this file has a docstring rather than four
one-liners, is that **the boundary is where the no-normalisation rule dies.**

    raw_text: str

That is the whole defence and it is one token wide. ``constr(strip_whitespace=
True)``, a ``@field_validator`` that trims, ``.strip()`` before the call — each
one reads as hygiene in review and each one silently destroys the property the
corpus exists for. The parser under benchmark is the thing that is *supposed*
to cope with a trailing space and a CRLF and somebody's mojibake; a corpus that
has been tidied measures the tidier. ``InboundLine``'s own docstring says this
at length and ``test_inbound_line_capture`` pins it at the function; the tests
for this router pin it at the wire, because that is the layer a request now
passes through and the one the earlier tests could not see.

**Who may do what.** Capturing and deciding are ordinary work — a salesperson
takes the enquiry, so a salesperson records it. The bulk export is not: it is
every word this tenant's customers have written, in one response, and it is the
surface most likely to leave the building. Manager or owner.

**What this does not do.** It computes nothing, so there is no cost, no margin
and no economics anywhere in it (§1) — a disposition says what happened to a
line, never what it was worth.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Response,
                     UploadFile, status)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sqlalchemy import func, select

from .. import clock, enquiry
from ..enquiry import documents
from ..authz import Principal, current_principal, require_manager_or_owner
from ..db import get_session
from ..domain import enums, models

router = APIRouter(prefix="/api/v1/enquiries", tags=["enquiries"])

#: How many lines one export may return before it is refused rather than
#: truncated. A corpus export that silently stops is a benchmark run against a
#: prefix and a coverage denominator that is quietly too small — the failure
#: ``enquiry.export`` refuses to have by returning the whole set. If a tenant
#: ever passes this, the answer is a paged or streamed export, not a cap that
#: keeps the number plausible.
_EXPORT_CEILING = 50_000


class CaptureRequest(BaseModel):
    """One enquiry line, as it arrived.

    **``raw_text`` is a bare ``str`` on purpose.** No ``strip_whitespace``, no
    ``min_length``, no validator. Pydantic would happily tidy this and the
    tidying would be invisible in every test that asserts on parsed content
    rather than on bytes. Emptiness is refused one layer down, by
    ``enquiry.capture``, which says *why* — an empty ask inflates the coverage
    denominator with a question nobody asked.
    """

    raw_text: str
    #: An ``InboundChannel``. Required, and deliberately not defaulted: the enum
    #: has no UNKNOWN member because "an enquiry that arrived some other way has
    #: no honest value to store", and a default would be this router choosing a
    #: route on the sender's behalf. The refusal names the closed set.
    channel: str
    #: Whatever identified the sender, verbatim — a name, an address, a number.
    #: Not resolved to a ``Customer`` here: enquiries arrive from people who are
    #: not customers yet, and those are exactly the rows a coverage report is
    #: for.
    customer_ref: str = ""
    #: Where this was read from, and the handle an adapter uses to notice it is
    #: redelivering one message twice.
    source_ref: str = ""
    #: When the *customer* sent it. Defaults to now, which is right for a live
    #: webhook and wrong for a backlog import — an importer that lets it default
    #: files a year of demand on one afternoon.
    received_at: Optional[datetime] = None


class DispositionRequest(BaseModel):
    """How a line ended. There is no PENDING and there must not be — a line
    whose fate is unknown has no row at all, which is what keeps "not answered
    yet" distinguishable from "answered with nothing"."""

    disposition: str
    source_ref: str = ""
    decided_at: Optional[datetime] = None


def _line_to_dict(row: models.InboundLine) -> dict:
    return {
        "inbound_line_id": row.inbound_line_id,
        # Byte-intact, the same string the database returned. Anything that
        # wanted it tidy tidies it here, on the way out, per reader.
        "raw_text": row.raw_text,
        "channel": row.channel,
        "customer_ref": row.customer_ref,
        "source_ref": row.source_ref,
        "received_at": clock.iso(row.received_at),
        "captured_at": clock.iso(row.captured_at),
    }


def _disposition_to_dict(row: models.InboundLineDisposition) -> dict:
    return {
        "disposition": row.disposition,
        "source_ref": row.source_ref,
        "decided_by_user_id": row.decided_by_user_id,
        "decided_at": clock.iso(row.decided_at),
        "recorded_at": clock.iso(row.recorded_at),
        # Present and null on the live row rather than absent, so a reader can
        # tell a live disposition from a superseded one without knowing the
        # ordering convention.
        "superseded_at": clock.iso(row.superseded_at),
    }


@router.get("/channels")
def channels(_: Principal = Depends(current_principal)) -> dict:
    """The two closed sets, published so no client hardcodes a copy of them.

    A seventh channel or a seventh disposition is a schema decision made in
    ``domain/enums.py``; a client holding its own list would keep offering the
    old one and a capture would start failing for a reason nobody could see
    from the screen.
    """
    return {
        "channels": [c.value for c in enums.InboundChannel],
        "dispositions": [d.value for d in enums.LineDisposition],
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def capture_line(body: CaptureRequest,
                 principal: Principal = Depends(current_principal),
                 session: Session = Depends(get_session)) -> dict:
    """Record one enquiry line. Appends — always, and with no content key.

    Two identical asks a week apart are two enquiries. A router that deduplicated
    them would under-report exactly the repeat demand this table was added to
    measure, so the redelivery question belongs to the adapter, on ``source_ref``.
    """
    try:
        row = enquiry.capture(
            session, principal.organization_id,
            raw_text=body.raw_text,
            channel=body.channel,
            customer_ref=body.customer_ref,
            source_ref=body.source_ref,
            received_at=clock.aware(body.received_at))
    except enquiry.CaptureRefusal as e:
        # 400 rather than 422: the body parsed, and what is wrong with it is a
        # domain rule this router did not restate. The message from the refusal
        # is the useful part — it names the closed set, or says why an empty ask
        # is not storable.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    session.commit()
    return _line_to_dict(row)


# ── documents: what the customer SENT ───────────────────────────────────────
#
# **Registered above `/{inbound_line_id}`, and that is load-bearing.** FastAPI
# matches routes in definition order, so a literal path declared after a
# path-parameter route at the same level is unreachable: appended to the end of
# this file, `GET /documents` was matched as an enquiry line whose id is the
# string "documents", and the listing endpoint returned a 404 body that a
# client would read as "you have no documents". Caught by
# `test_only_a_manager_or_owner_may_withdraw`, which asked for the listing and
# got a shape with no `count` in it.
#
#
# Decision 012, Phase 3. These four routes are the first multipart handler in
# this codebase, and `app/master_health/__init__.py:11` records the absence they
# reverse. The reversal is deliberate and its cost is a new dependency
# (`python-multipart`, which FastAPI requires for `UploadFile`) plus the surface
# these routes defend: `enquiry/documents.py` holds every refusal and this file
# holds only the HTTP mapping, per §3's rule that a router is thin.


def _refusal(e: documents.DocumentRefused) -> HTTPException:
    """A refusal as its own status, never a bare 400 or a 500.

    413 for the two ceilings and 415 for the three "this is not a document we
    take" cases, because a caller that cannot tell "too big" from "wrong kind"
    retries the same file. The reason code travels beside the sentence so a
    client can branch without matching prose.
    """
    over = e.reason in ("TOO_LARGE", "ARCHIVE_TOO_LARGE")
    return HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if over
        else status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        {"reason": e.reason, "detail": e.detail})


@router.post("/documents", status_code=status.HTTP_201_CREATED)
async def upload_document(
        file: UploadFile = File(...),
        licence_note: str = Form(""),
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session)) -> dict:
    """Store one document a customer sent. Every role — whoever received it.

    **The size ceiling is checked twice, and the first check is the one that
    matters.** `content-length` is refused before the body is read, so an
    oversized upload costs a header rather than 25 MB of memory — the same
    correction `/export` needed, where the ceiling "fired only once the process
    had done exactly the work the ceiling exists to prevent". But a length
    header is a claim by the sender, so the body is read under a hard cap as
    well and refused again if it exceeds it. Neither check alone is enough:
    the first is fast and lies, the second is honest and expensive.

    The response never contains the bytes.
    """
    declared_length = 0
    try:
        declared_length = int(file.size or 0)
    except (TypeError, ValueError):
        declared_length = 0
    if declared_length > documents.MAX_BYTES:
        raise _refusal(documents.DocumentRefused(
            "TOO_LARGE",
            f"{declared_length:,} bytes is past this endpoint's ceiling of "
            f"{documents.MAX_BYTES:,}."))

    # Read one byte past the ceiling and no further. A sender whose header
    # understated the body is refused here having cost the ceiling, not the
    # file — and `read(n)` is what makes that bound real rather than advisory.
    content = await file.read(documents.MAX_BYTES + 1)
    try:
        row = documents.store(
            session, principal.organization_id,
            filename=file.filename or "",
            content=content,
            declared_type=file.content_type or "",
            uploaded_by_user_id=principal.user_id,
            licence_note=licence_note)
    except documents.DocumentRefused as e:
        raise _refusal(e) from e
    session.commit()
    return documents.summary(row)


@router.get("/documents")
def list_documents(principal: Principal = Depends(current_principal),
                   session: Session = Depends(get_session)) -> dict:
    """Every live document, metadata only. Never the bytes."""
    rows = documents.listing(session, principal.organization_id)
    return {"count": len(rows), "documents": [documents.summary(r) for r in rows]}


@router.get("/documents/{rfq_document_id}/content")
def download_document(rfq_document_id: str,
                      principal: Principal = Depends(current_principal),
                      session: Session = Depends(get_session)) -> Response:
    """The document itself — as a download, never as a page.

    **Three headers, all set here rather than at the reverse proxy.**
    `deploy/Caddyfile` sets `X-Content-Type-Options` and a CSP, and the
    free-tier topology has no Caddy at all — `frontend/api/proxy.ts` corrects
    two headers and adds no security ones, and `vercel.json` has no headers
    block. A defence that holds on one of two supported topologies is not a
    defence, so the response carries its own:

    * `application/octet-stream`, never the sniffed type. The sniffed type is
      recorded as evidence and deliberately not echoed: serving a customer's
      PDF as `application/pdf` invites the browser to render it, and an
      attacker-supplied file rendered on this origin is the vector this route
      exists not to open.
    * `Content-Disposition: attachment`, with the filename quoted and stripped
      of anything that could break out of the header. The filename is the one
      field on this row that an attacker fully controls.
    * `X-Content-Type-Options: nosniff`, so a browser does not overrule the
      first bullet by looking at the bytes.

    404 for another organization's document, for a withdrawn one, and for one
    that never existed — three states a caller must not be able to tell apart.
    """
    found = documents.read(session, principal.organization_id, rfq_document_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document.")
    row, content = found
    # Built once and handed to the Response that is actually returned. Setting
    # them on the injected `response` as well would be dead: FastAPI sends the
    # returned object, so a header written to the other one is a header nobody
    # ever receives — and a security header that silently does not ship is
    # worse than one that was never claimed.
    return Response(content=content, media_type="application/octet-stream",
                    headers=_download_headers(row.filename))


def _download_headers(filename: str) -> dict:
    """`Content-Disposition` built from a filename treated as hostile.

    RFC 6266's `filename*` is deliberately NOT used. It would let the original
    name through faithfully, and faithfulness is not what is wanted from a
    string somebody else chose: what is wanted is a name that cannot carry a
    quote, a newline, a semicolon or a path separator into a response header.
    So the name is reduced to a conservative set and anything else becomes an
    underscore, and a name that reduces to nothing gets a neutral default
    rather than an empty `filename=""` for a browser to interpret.
    """
    safe = "".join(c if (c.isalnum() or c in "._- ") else "_"
                   for c in (filename or ""))[:120].strip() or "document"
    return {
        "Content-Disposition": f'attachment; filename="{safe}"',
        "X-Content-Type-Options": "nosniff",
    }


@router.post("/documents/{rfq_document_id}/withdraw")
def withdraw_document(rfq_document_id: str,
                      principal: Principal = Depends(require_manager_or_owner),
                      session: Session = Depends(get_session)) -> dict:
    """Withdraw a document. Manager or owner — it changes what the record says.

    Never a delete. The row stays and stops being live, which is this package's
    supersede convention: what arrived is a fact about the enquiry, and erasure
    is the operation that destroys content — by destroying the key, which
    reaches the backups as well.
    """
    row = documents.withdraw(session, principal.organization_id, rfq_document_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such document.")
    session.commit()
    return {"rfq_document_id": row.rfq_document_id,
            "withdrawn_at": clock.iso(row.withdrawn_at)}


@router.get("/{inbound_line_id}")
def read_line(inbound_line_id: str,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)) -> dict:
    """One line, its live verdict, and every verdict it has ever had.

    The history is not an audit extra: a report run last week was run against a
    disposition that may since have been corrected, and without the superseded
    rows nobody can explain the difference.
    """
    row = session.get(models.InboundLine, inbound_line_id)
    if row is None or row.organization_id != principal.organization_id:
        # One answer for two different failures — no such line, and a line
        # belonging to another tenant — because a distinct refusal for the
        # second confirms the row exists, which is most of what enumeration is
        # after. The same reasoning `_NO_SUCH_PLATFORM_QUOTE` gives.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such enquiry line")
    live = enquiry.live_disposition(session, principal.organization_id,
                                    inbound_line_id)
    history = enquiry.disposition_history(session, principal.organization_id,
                                          inbound_line_id)
    return {
        **_line_to_dict(row),
        # None means *undecided*, which is not "answered with nothing" — that
        # would be a row saying ABSTAINED or NO_STOCK (§1).
        "disposition": live.disposition if live is not None else None,
        "disposition_history": [_disposition_to_dict(d) for d in history],
    }


@router.post("/{inbound_line_id}/disposition")
def set_disposition(inbound_line_id: str, body: DispositionRequest,
                    principal: Principal = Depends(current_principal),
                    session: Session = Depends(get_session)) -> dict:
    """Decide a line, or correct an earlier decision.

    ``written`` comes back so a re-runnable caller can tell "I recorded this"
    from "this was already recorded, identically" — the second is the guard
    working, not a failure, and a nightly job that could not tell them apart
    would log a write every night for ever.
    """
    try:
        row, written = enquiry.set_disposition(
            session, principal.organization_id, inbound_line_id,
            body.disposition,
            source_ref=body.source_ref,
            decided_by_user_id=principal.user_id,
            decided_at=clock.aware(body.decided_at))
    except enquiry.CaptureRefusal as e:
        # A disposition against a line this tenant does not hold is refused one
        # layer down and arrives here as a refusal, not a 404 — the message
        # names which of the two it was, and it is not a cross-tenant probe
        # worth protecting against separately: the caller had to name a line id
        # to get here, and a wrong one answers the same way a missing one does.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    session.commit()
    return {"written": written, **_disposition_to_dict(row)}


@router.get("")
def export_corpus(principal: Principal = Depends(require_manager_or_owner),
                  session: Session = Depends(get_session)) -> dict:
    """The whole corpus for one tenant, raw text byte-intact.

    Not a page. This is what a benchmark reads and what a coverage report
    divides by, and both are wrong if the export silently stops — so a book past
    ``_EXPORT_CEILING`` is refused loudly rather than truncated quietly.

    Manager or owner. These rows are the tenant's customers' own words, routinely
    naming their project, their end customer, their volumes and their urgency.
    """
    # Counted before it is loaded. The ceiling used to be checked after
    # ``enquiry.export`` had already read every line and every disposition row
    # and built a dataclass per line — so the refusal fired only once the
    # process had done exactly the work the ceiling exists to prevent, on rows
    # that are raw customer text and are not small.
    held = session.scalar(
        select(func.count()).select_from(models.InboundLine)
        .where(models.InboundLine.organization_id
               == principal.organization_id)) or 0
    if held > _EXPORT_CEILING:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"{held} lines is past this endpoint's ceiling of "
            f"{_EXPORT_CEILING}. A truncated corpus is a benchmark run against "
            "a prefix and a coverage denominator that is quietly too small, so "
            "this refuses rather than trims — the fix is a paged export.")
    lines = enquiry.export(session, principal.organization_id)
    return {
        "count": len(lines),
        "lines": [
            {
                "inbound_line_id": ln.inbound_line_id,
                "raw_text": ln.raw_text,
                "channel": ln.channel,
                "customer_ref": ln.customer_ref,
                "source_ref": ln.source_ref,
                "received_at": clock.iso(ln.received_at),
                "captured_at": clock.iso(ln.captured_at),
                "disposition": ln.disposition,
                "disposition_decided_at": clock.iso(ln.disposition_decided_at),
                "disposition_history": [
                    _disposition_to_dict(d) for d in ln.disposition_history],
            }
            for ln in lines
        ],
    }
