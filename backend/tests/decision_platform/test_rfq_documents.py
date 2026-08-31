"""What a customer sent: received, refused, encrypted, and never rendered.

Decision 012 adds the first upload endpoint this codebase has ever had, and
`master_health/__init__.py` recorded its absence as a deliberate refusal — "a
dependency decision and a new attack surface". This file is the price of the
reversal: every refusal exercised, the encryption proved rather than asserted,
and the two headers that stop a customer's file being rendered on this origin
checked on the response itself, because the reverse proxy that sets them exists
in only one of the two supported topologies.

**The store tests do not go through HTTP and the HTTP tests do not re-test the
store.** The refusals live in `enquiry/documents.py` and are cheap to exercise
directly with hostile bytes; the router's job is mapping them to statuses a
client can act on. Testing both through the endpoint would make every refusal
cost a login.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.config import settings
from app.domain import models
from app.enquiry import documents
from app.seed import SEED_PASSWORD
from app.trust import erasure, keys

ORG = settings.DEFAULT_ORG_ID
OTHER = "org_rival"
SALES = "r.nair@pie.example"
MANAGER = "m.rao@pie.example"

PDF = b"%PDF-1.7\n" + b"one customer's requirement, in a file. " * 40


def _org(session, organization_id: str) -> None:
    if session.get(models.Organization, organization_id) is None:
        session.add(models.Organization(organization_id=organization_id,
                                        name=organization_id, currency="INR"))
        session.flush()


def _zip(entries, compress=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def _xlsx() -> bytes:
    return _zip([("[Content_Types].xml", "<Types/>"),
                 ("xl/workbook.xml", "<workbook><sheets/></workbook>")])


def _hdr(client, email: str) -> dict:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ── the refusals, each one named ─────────────────────────────────────────────

@pytest.mark.parametrize("name,content,reason", [
    ("nothing at all", b"", "EMPTY"),
    ("bytes nothing recognises", b"\x01\x02\x03\x00\xff" * 50, "UNRECOGNISED"),
])
def test_a_document_this_platform_cannot_identify_is_refused(name, content, reason):
    """UNKNOWN, never the client's own claim about what it sent.

    `sniff` returns "" rather than falling back to the declared content type,
    which is the one input an attacker fully controls. "Absence of evidence is
    not a pass" applied to a byte stream.
    """
    with pytest.raises(documents.DocumentRefused) as raised:
        documents.check(content, declared_type="application/pdf")
    assert raised.value.reason == reason, name


def test_a_document_past_the_ceiling_is_refused_by_size_and_not_by_type():
    """The ceiling fires before anything looks at what the bytes are.

    Order matters: sniffing a 25 MB stream to then refuse it for its size does
    the work the ceiling exists to prevent.
    """
    with pytest.raises(documents.DocumentRefused) as raised:
        documents.check(b"%PDF-" + b"x" * documents.MAX_BYTES)
    assert raised.value.reason == "TOO_LARGE"
    assert f"{documents.MAX_BYTES:,}" in raised.value.detail


def test_a_bare_zip_is_refused_because_nothing_here_will_open_it():
    """An archive that is not an Office file is a container of unknown things.

    Accepting it would be a promise about today's code — "we never open it" —
    made on behalf of the code that will one day want to extract requirements.
    """
    with pytest.raises(documents.DocumentRefused) as raised:
        documents.check(_zip([("whatever.bin", "payload")]))
    assert raised.value.reason == "UNSUPPORTED_TYPE"


def test_an_archive_bomb_is_refused_without_being_opened():
    """A small archive declaring an enormous one, caught on its central directory.

    `infolist()` reads metadata, so this costs no decompression. The bomb below
    is ~58 KB on disk and declares 60 MB.
    """
    bomb = _zip([("xl/workbook.xml", "\0" * 60_000_000)])
    assert len(bomb) < 200_000, "the bomb must be small, or it is just a big file"
    with pytest.raises(documents.DocumentRefused) as raised:
        documents.check(bomb)
    assert raised.value.reason == "ARCHIVE_TOO_LARGE"
    assert "60,000,000" in raised.value.detail


def test_a_real_spreadsheet_is_not_mistaken_for_a_bomb():
    """The positive control. Without it the check above could pass by refusing
    every archive, which would be a defence and also a broken feature."""
    assert documents.check(_xlsx()).endswith("spreadsheetml.sheet")


def test_what_the_bytes_are_beats_what_the_upload_said_they_were(session):
    """A file called `.pdf` that is really a spreadsheet is stored as one.

    Not an error — it is the *interesting* case, and it is published so a reader
    can see it. Trusting the declared type here is how a renderer gets handed
    something it was not expecting.
    """
    _org(session, ORG)
    row = documents.store(session, ORG, filename="requirement.pdf",
                          content=_xlsx(), declared_type="application/pdf")
    assert row.content_type_sniffed.endswith("spreadsheetml.sheet")
    assert row.content_type_declared == "application/pdf"
    assert documents.summary(row)["type_matches_declaration"] is False


def test_a_document_with_no_declared_type_says_unknown_rather_than_agreeing(session):
    """No declaration is not a matching declaration."""
    _org(session, ORG)
    row = documents.store(session, ORG, filename="x.pdf", content=PDF)
    assert documents.summary(row)["type_matches_declaration"] is None


# ── what is stored, and in what state ────────────────────────────────────────

def test_the_document_comes_back_exactly_as_it_went_in(session):
    _org(session, ORG)
    row = documents.store(session, ORG, filename="rfq.pdf", content=PDF)

    found = documents.read(session, ORG, row.rfq_document_id)
    assert found is not None
    stored_row, content = found
    assert content == PDF
    assert stored_row.byte_size == len(PDF)


def test_the_bytes_on_disk_are_not_the_customers_bytes(session):
    """Encrypted at rest, proved by looking rather than by trusting the call.

    This is the assertion the whole storage decision rests on: erasure destroys
    the tenant key and deletes no rows, so a plaintext column here would sit
    outside a promise the signed receipt makes.
    """
    _org(session, ORG)
    row = documents.store(session, ORG, filename="rfq.pdf", content=PDF)
    session.flush()

    raw = session.execute(
        models.RfqDocument.__table__.select()
        .where(models.RfqDocument.__table__.c.rfq_document_id
               == row.rfq_document_id)).mappings().one()
    stored = bytes(raw["content_ciphertext"])
    assert stored != PDF
    assert b"%PDF" not in stored
    assert b"requirement" not in stored


def test_destroying_the_key_makes_the_document_unreadable(session):
    """Erasure reach, exercised end to end rather than asserted in a registry.

    `erase` deletes no rows — the row is still there afterwards, and that is the
    point: what is gone is the ability to read it, everywhere the ciphertext
    exists, backups included.
    """
    _org(session, ORG)
    row = documents.store(session, ORG, filename="rfq.pdf", content=PDF)
    session.flush()

    erasure.erase(session, ORG, reason="customer asked", actor_user_id="usr_owner")
    session.flush()

    assert session.get(models.RfqDocument, row.rfq_document_id) is not None
    # `KeyDestroyed`, not `KeyUnavailable`. The distinction is the whole point:
    # unavailable is "could not unwrap, try again", destroyed is "this is gone
    # on purpose and no amount of retrying changes that". A caller that caught
    # the wrong one would retry a permanent state forever.
    with pytest.raises(keys.KeyDestroyed):
        documents.read(session, ORG, row.rfq_document_id)


def test_the_erasure_receipt_says_the_documents_were_destroyed(session):
    """A receipt that did not name this column would under-report — which
    `erasure.py` calls worse than no receipt at all."""
    _org(session, ORG)
    documents.store(session, ORG, filename="rfq.pdf", content=PDF)
    session.flush()

    receipt = erasure.erase(session, ORG, reason="asked", actor_user_id=None)
    destroyed = {e["table"] for e in receipt.attestation["destroyed"]}
    assert "rfq_documents" in destroyed


def test_the_export_describes_the_bytes_rather_than_carrying_them(session):
    """A blob in a JSON export is undeliverable and useless; a repr of one is
    worse. The export says how many bytes and where to get them."""
    _org(session, ORG)
    documents.store(session, ORG, filename="rfq.pdf", content=PDF)
    session.flush()

    exported = erasure.export(session, ORG)["data"]["rfq_documents"]
    assert len(exported) == 1
    blob = exported[0]["content_ciphertext"]
    assert isinstance(blob, dict)
    assert blob["bytes"] > len(PDF)          # ciphertext is larger
    assert "omitted" in blob
    # And the metadata a reader actually wants is all there.
    assert exported[0]["filename"] == "rfq.pdf"
    assert exported[0]["byte_size"] == len(PDF)


def test_a_summary_never_carries_the_document(session):
    """The projection is named field by field so a new column cannot join a
    response by being added to the table."""
    _org(session, ORG)
    row = documents.store(session, ORG, filename="rfq.pdf", content=PDF)
    assert "content_ciphertext" not in documents.summary(row)
    assert PDF not in repr(documents.summary(row)).encode()


# ── whose document it is ─────────────────────────────────────────────────────

def test_one_organizations_document_is_invisible_to_another(session):
    """Absent, not refused — a foreign id must be indistinguishable from one
    that never existed, or the endpoint answers "does this exist elsewhere"."""
    _org(session, ORG)
    _org(session, OTHER)
    mine = documents.store(session, ORG, filename="mine.pdf", content=PDF)
    theirs = documents.store(session, OTHER, filename="theirs.pdf", content=PDF)
    session.flush()

    assert documents.read(session, OTHER, mine.rfq_document_id) is None
    assert documents.read(session, ORG, theirs.rfq_document_id) is None
    assert [r.rfq_document_id for r in documents.listing(session, ORG)] \
        == [mine.rfq_document_id]


def test_a_withdrawn_document_reads_as_absent_but_is_not_deleted(session):
    _org(session, ORG)
    row = documents.store(session, ORG, filename="rfq.pdf", content=PDF)
    session.flush()

    assert documents.withdraw(session, ORG, row.rfq_document_id) is not None
    assert documents.read(session, ORG, row.rfq_document_id) is None
    assert documents.listing(session, ORG) == []
    # Still on the table. What arrived is a fact about the enquiry.
    assert session.get(models.RfqDocument, row.rfq_document_id) is not None


def test_another_organization_cannot_withdraw_a_document(session):
    _org(session, ORG)
    _org(session, OTHER)
    row = documents.store(session, ORG, filename="rfq.pdf", content=PDF)
    session.flush()

    assert documents.withdraw(session, OTHER, row.rfq_document_id) is None
    assert session.get(models.RfqDocument, row.rfq_document_id).withdrawn_at is None


# ── over HTTP ────────────────────────────────────────────────────────────────

def test_a_salesperson_can_upload_the_pdf_a_customer_sent(api_client):
    client = api_client
    response = client.post(
        "/api/v1/enquiries/documents",
        files={"file": ("customer rfq.pdf", PDF, "application/pdf")},
        data={"licence_note": "Customer's own drawing; do not redistribute."},
        headers=_hdr(client, SALES))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["filename"] == "customer rfq.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["byte_size"] == len(PDF)
    assert body["licence_note"].startswith("Customer's own drawing")
    assert "content_ciphertext" not in body


def test_the_download_is_an_attachment_and_never_a_page(api_client):
    """The three headers that make inline rendering impossible, on the response.

    Not on the reverse proxy: `deploy/Caddyfile` sets `nosniff` and a CSP, and
    the free-tier topology (Vercel to Railway) has no Caddy — its proxy corrects
    two headers and adds no security ones. A defence present on one of two
    supported topologies is not a defence.
    """
    client = api_client
    hdr = _hdr(client, SALES)
    created = client.post("/api/v1/enquiries/documents",
                          files={"file": ("rfq.pdf", PDF, "application/pdf")},
                          headers=hdr).json()

    got = client.get(
        f"/api/v1/enquiries/documents/{created['rfq_document_id']}/content",
        headers=hdr)

    assert got.status_code == 200
    assert got.content == PDF
    # Never `application/pdf`, however confidently it was sniffed.
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"] == 'attachment; filename="rfq.pdf"'
    assert got.headers["x-content-type-options"] == "nosniff"


def test_a_hostile_filename_cannot_break_out_of_the_header(api_client):
    """The filename is the one field on this row an attacker fully controls.

    A quote, a newline or a semicolon in it would end the header value and
    begin something else. Reduced to a conservative set rather than escaped,
    because escaping is a thing to get subtly wrong.
    """
    client = api_client
    hdr = _hdr(client, SALES)
    nasty = 'a";\r\nSet-Cookie: x=1\r\n\r\n<script>.pdf'
    created = client.post("/api/v1/enquiries/documents",
                          files={"file": (nasty, PDF, "application/pdf")},
                          headers=hdr).json()

    got = client.get(
        f"/api/v1/enquiries/documents/{created['rfq_document_id']}/content",
        headers=hdr)

    disposition = got.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert "Set-Cookie" not in got.headers
    assert "<script>" not in disposition
    assert disposition.count('"') == 2


def test_an_oversized_upload_is_refused_with_a_status_that_says_so(api_client):
    """413, not 400 — a caller that cannot tell "too big" from "wrong kind"
    retries the same file."""
    client = api_client
    response = client.post(
        "/api/v1/enquiries/documents",
        files={"file": ("huge.pdf", b"%PDF-" + b"x" * documents.MAX_BYTES,
                        "application/pdf")},
        headers=_hdr(client, SALES))

    assert response.status_code == 413
    assert response.json()["detail"]["reason"] == "TOO_LARGE"


def test_an_unsupported_upload_is_refused_with_a_different_status(api_client):
    client = api_client
    response = client.post(
        "/api/v1/enquiries/documents",
        files={"file": ("x.bin", b"\x01\x02\x03\x00\xff" * 50,
                        "application/octet-stream")},
        headers=_hdr(client, SALES))

    assert response.status_code == 415
    assert response.json()["detail"]["reason"] == "UNRECOGNISED"


def test_a_document_that_does_not_exist_is_a_404_and_not_a_500(api_client):
    client = api_client
    got = client.get("/api/v1/enquiries/documents/nope/content",
                     headers=_hdr(client, SALES))
    assert got.status_code == 404


def test_only_a_manager_or_owner_may_withdraw(api_client):
    """Withdrawing changes what the record says arrived, so it is not every
    role — the same line `/export` draws."""
    client = api_client
    sales = _hdr(client, SALES)
    created = client.post("/api/v1/enquiries/documents",
                          files={"file": ("rfq.pdf", PDF, "application/pdf")},
                          headers=sales).json()
    doc_id = created["rfq_document_id"]

    assert client.post(f"/api/v1/enquiries/documents/{doc_id}/withdraw",
                       headers=sales).status_code == 403
    assert client.post(f"/api/v1/enquiries/documents/{doc_id}/withdraw",
                       headers=_hdr(client, MANAGER)).status_code == 200
    # And it is gone from the listing afterwards.
    listed = client.get("/api/v1/enquiries/documents", headers=sales).json()
    assert listed["count"] == 0


def test_the_listing_route_is_not_shadowed_by_the_line_route(api_client):
    """`GET /documents` must not be matched as an enquiry line called "documents".

    FastAPI matches in definition order, so a literal path declared after a
    path-parameter route at the same level is unreachable. This was a real
    defect for as long as the document routes sat at the end of the file: the
    listing returned a 404 body, which a client reads as "you have no
    documents" — a wrong answer that looks like a right one.

    Pinned separately from the tests that merely *use* the listing, because
    those would go on passing if the shadowing came back and the shadowing
    route happened to return an empty shape.
    """
    client = api_client
    hdr = _hdr(client, SALES)
    client.post("/api/v1/enquiries/documents",
                files={"file": ("rfq.pdf", PDF, "application/pdf")},
                headers=hdr)

    listed = client.get("/api/v1/enquiries/documents", headers=hdr)

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 1
    assert body["documents"][0]["filename"] == "rfq.pdf"


# ── the link back to the enquiry ─────────────────────────────────────────────

@pytest.fixture()
def quote_client(engine):
    """Both routers on one database, plus the session that wrote it.

    `conftest.api_client` mounts the enquiry routes and not the quote ones, and
    this pair of tests needs both in one request cycle: a document is uploaded
    through the first and named by the second. Local rather than widened in
    conftest, because every other suite that fixture serves would then pay for
    a router it does not use.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app.db import get_session
    from app.routers import enquiries, platform_auth, quote as quote_router
    from app.seed import ensure_org_and_users

    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    setup = maker()
    ensure_org_and_users(setup)
    setup.commit()
    org = setup.query(models.Organization).first().organization_id
    setup.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(enquiries.router)
    app.include_router(quote_router.router)

    def _session():
        s = maker()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_session] = _session
    reader = maker()
    yield TestClient(app), org, reader
    reader.close()




def test_an_enquiry_records_the_document_it_arrived_as(quote_client):
    """`source_ref` carries both handles, and neither replaces the other.

    The quote handle is what marks a row as part of the worked subset — the
    thing that keeps a coverage report from dividing by enquiries somebody
    chose to work. A document link that overwrote it would buy a join and lose
    the property the field was added for.
    """
    client, org, session = quote_client
    hdr = _hdr(client, SALES)
    doc = client.post("/api/v1/enquiries/documents",
                      files={"file": ("boq.pdf", PDF, "application/pdf")},
                      headers=hdr).json()

    quote = client.post("/api/v1/quotes", json={"customer": "Pitti"},
                        headers=hdr).json()
    client.post(f"/api/v1/quotes/{quote['id']}/intake",
                json={"text": "2001174, 10", "channel": "PDF",
                      "rfq_document_id": doc["rfq_document_id"]},
                headers=hdr)

    line = session.query(models.InboundLine).one()
    assert f"quote:{quote['id']}" in line.source_ref
    assert f"doc:{doc['rfq_document_id']}" in line.source_ref


def test_an_intake_naming_another_tenants_document_captures_without_it(quote_client):
    """A foreign id is dropped, not stored and not fatal.

    Storing it would put a cross-tenant reference permanently into a corpus
    row, which is worse than a failed lookup because nothing later would
    question it. Failing the intake would lose somebody's RFQ over a
    by-product — the trade `_capture_enquiry` already refuses to make.
    """
    client, org, session = quote_client
    hdr = _hdr(client, SALES)

    quote = client.post("/api/v1/quotes", json={"customer": "Pitti"},
                        headers=hdr).json()
    response = client.post(
        f"/api/v1/quotes/{quote['id']}/intake",
        json={"text": "2001174, 10", "channel": "PDF",
              "rfq_document_id": "doc_belonging_to_someone_else"},
        headers=hdr)

    assert response.status_code == 200, response.text
    line = session.query(models.InboundLine).one()
    assert line.source_ref == f"quote:{quote['id']}"
    assert "doc_belonging" not in line.source_ref
