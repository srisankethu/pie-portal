"""A quote names the system its books are in — whatever that system is.

The Quote Builder printed the word "Zoho" at every customer: the filter chip
counting lines the ledger does not hold said "Missing Zoho item", the button
that creates one said "+ Zoho", and the primary action said "Create Zoho
estimate". A reader running Business Central or Prophet 21 was being told to
look for a record type their system does not have — and the platform already
knew better, because ``connections.system_label_for`` and ``quote_term_for``
have named connectors correctly since the connector registry landed. Those
words simply never reached the quote: they were attached to the *document a
send produced*, which is the one moment the screen no longer needs them.

So these tests pin the naming where the screen actually reads it — on the
quote, on every read, before anything is sent — and pin that it follows the
connector rather than a literal. The assertions are deliberately about
*absence of the word "Zoho"* as much as presence of the right one: a screen
that says "Zoho" to a Business Central user is the defect, and only a test
that looks for it fails when somebody hardcodes it again.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.db import get_session
from app.domain import models  # noqa: F401  (populate metadata)
from app.routers import platform_auth, quote
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.store import Line

OWNER = "s.menon@pie.example"
ORG = "org_pie"

#: The naming fields every quote and every send answer carries.
WORDS = ("system", "systemLabel", "systemShort", "documentTerm")


def _client(connector: str | None, *, connect: bool = True) -> TestClient:
    """The quote endpoints against an organization on one named ERP.

    ``connect=False`` is the deployment with nothing connected at all — a real
    state, and the one where a screen has no system to name.
    """
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    if connect:
        row = models.ZohoConnection(connection_id="cx_naming", organization_id=ORG,
                                    label="SLS Engineers", zoho_organization_id="z1")
        if connector is not None:
            row.connector = connector
        s.add(row)
    s.commit()
    s.close()

    api = FastAPI()
    api.include_router(platform_auth.router)
    api.include_router(quote.router)

    def _override():
        sess = Maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    api.dependency_overrides[get_session] = _override
    tc = TestClient(api)
    tc.Maker = Maker
    return tc


def _hdr(c: TestClient) -> dict:
    r = c.post("/api/v1/auth/login", json={"email": OWNER, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _quote(c: TestClient, hdr: dict) -> dict:
    r = c.post("/api/v1/quotes", json={}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    "connector,label,short,term",
    [
        ("zoho", "Zoho Books", "Zoho", "estimate"),
        ("dynamics365", "Dynamics 365 Business Central", "D365 BC", "sales quote"),
        ("prophet21", "Epicor Prophet 21", "P21", "sales quote"),
        ("netsuite", "Oracle NetSuite", "NetSuite", "sales quote"),
    ],
)
def test_a_quote_names_the_system_its_books_are_in(connector, label, short, term):
    """The words follow the connector, on the quote itself.

    On the quote rather than only on the document a send produces: the screen
    names the ledger long before anything is sent — the filter chip, the chip
    on a line, the create-item button and the send button all do — and with
    nothing here it had no choice but to print a literal.
    """
    c = _client(connector)
    hdr = _hdr(c)
    q = _quote(c, hdr)
    assert q["system"] == connector
    assert q["systemLabel"] == label
    assert q["systemShort"] == short
    assert q["documentTerm"] == term
    # Read back, not merely echoed by the create.
    got = c.get(f"/api/v1/quotes/{q['id']}", headers=hdr).json()
    assert [got[k] for k in WORDS] == [connector, label, short, term]


def test_a_business_central_quote_says_nothing_about_zoho():
    """The negative half, and the one that fails when somebody hardcodes it.

    Every naming field on a quote whose books are Business Central must be
    free of the word — a screen that reads these and still says "Zoho" is
    reading a literal of its own.
    """
    c = _client("dynamics365")
    q = _quote(c, _hdr(c))
    for key in WORDS:
        assert "zoho" not in str(q[key]).lower(), (key, q[key])


def test_with_nothing_connected_the_quote_claims_no_system():
    """A deployment can build a quote with no ERP connected at all — lines
    resolve against a company's decoded catalogue, which is not a ledger. The
    words then name no system rather than the wrong one, and are still
    present, so a screen never has to decide what to print for itself."""
    c = _client(None, connect=False)
    q = _quote(c, _hdr(c))
    assert q["system"] == ""
    assert q["systemLabel"] == "your books"
    assert q["systemShort"] == "books"
    assert q["documentTerm"] == "quote"


def test_every_send_answer_carries_the_naming_including_a_refusal():
    """A screen saying what it *failed* to create still has to name it.

    The words used to be filled in only where a document was actually
    written, so all four refusal paths answered with three empty strings.
    """
    c = _client("dynamics365")
    hdr = _hdr(c)
    q = _quote(c, hdr)

    # A refusal before anything reaches the source: no customer yet.
    with c.Maker() as s:
        from app import quote_workspace
        draft = quote_workspace.load(s, ORG, q["id"])
        draft.lines.append(Line(
            id="L1", raw="X x1", reqCode="X", reqDesc="X", reqQty=1, rel="EXACT",
            supplyCode="X", candidates=[], outcome="OK", semantics="EXACT",
            inBooks=True, quoted=100.0, priceSource="USER"))
        quote_workspace.save(s, draft, None)
        s.commit()

    r = c.post(f"/api/v1/quotes/{q['id']}/estimate", headers=hdr).json()
    assert r["ok"] is False, r
    assert r["system"] == "dynamics365"
    assert r["systemLabel"] == "Dynamics 365 Business Central"
    assert r["documentTerm"] == "sales quote"


def test_a_connection_this_organization_does_not_own_names_nothing():
    """The tenant rule, on the naming seam too: a quote must not name another
    organization's system. A stamped connection id that resolves nowhere in
    this org reads exactly as no connection does."""
    c = _client("zoho")
    hdr = _hdr(c)
    q = _quote(c, hdr)
    with c.Maker() as s:
        row = s.get(models.QuoteDraft, q["id"])
        row.connection_id = "cx_somebody_elses"
        s.commit()
    got = c.get(f"/api/v1/quotes/{q['id']}", headers=hdr).json()
    assert got["system"] == "" and got["systemLabel"] == "your books"
