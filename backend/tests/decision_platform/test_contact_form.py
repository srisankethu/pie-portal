"""The public site's enquiry form, and the queue it lands in.

The landing page states no price. It ends its plans section in a form instead,
and that makes this endpoint the last step of the only path a buyer has: a page
that describes three plans, says what each is for, and then loses the enquiry
would be worse than the price list it replaced.

So what is pinned here is mostly *refusal*, for the same reason the sign-up
suite next door is: this is an unauthenticated write, and the two things that
go wrong are a row nobody can answer and a row that is not a person.

**A queue nobody reads is a form that lies**, which is why the operator
commands are exercised rather than assumed. An enquiry recorded and never shown
to anybody is the same defect as a "Book a demo" button pointing at a
placeholder — invisible to whoever maintains the page, and the whole experience
for the visitor who used it.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app import contact
from app.config import settings
from app.db import get_session
from app.domain import models

GOOD = {"company": "Acme Distributors", "name": "A. Buyer",
        "email": "buyer@acme.example", "erp": "Prophet 21",
        "plan": "intelligence", "message": "Three companies, one book each."}


@pytest.fixture()
def client(monkeypatch):
    """The public router over HTTP, on an isolated database.

    The limiter counts in this process rather than in the database, so its
    bucket is cleared per client for the reason the sign-up suite gives: a test
    that filled it would otherwise leak into whichever test ran next.
    """
    from app import ratelimit
    from app.routers import onboarding as onboarding_router

    ratelimit.reset("contact")
    monkeypatch.setattr(settings, "CONTACT_RATE_LIMIT_PER_HOUR", 0)

    eng = dbsupport.fresh_engine()
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False,
                         future=True)

    app = FastAPI()
    app.include_router(onboarding_router.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    c.maker = maker
    return c


def _rows(client) -> list[models.ContactRequest]:
    with client.maker() as session:
        return contact.pending(session)


# ── the enquiry lands ────────────────────────────────────────────────────────
def test_an_enquiry_is_recorded_and_readable_by_an_operator(client):
    res = client.post("/api/v1/contact", json=GOOD)
    assert res.status_code == 202, res.text

    rows = _rows(client)
    assert len(rows) == 1
    row = rows[0]
    assert (row.company, row.name, row.email) == (
        "Acme Distributors", "A. Buyer", "buyer@acme.example")
    assert (row.erp, row.plan, row.status) == ("Prophet 21", "intelligence", "NEW")
    assert row.message == "Three companies, one book each."


def test_it_creates_nothing_and_grants_nothing(client):
    """The form is not a checkout and not a sign-up.

    The row it writes licenses no plan and opens no session, which is the same
    non-promise `PlanChangeRequest` makes from inside the product. A response
    that carried a token or an organization id would be this endpoint claiming
    to have done something it did not.
    """
    res = client.post("/api/v1/contact", json=GOOD)
    body = res.json()
    assert body == {"received": True,
                    "note": "Thanks — we will come back to you at that address."}

    with client.maker() as session:
        assert session.query(models.Organization).count() == 0
        assert session.query(models.User).count() == 0


def test_the_response_names_no_row(client):
    """No id reaches an unauthenticated caller. A visitor has nothing to do
    with one, and an id handed out is an argument somebody can then use."""
    body = client.post("/api/v1/contact", json=GOOD).json()
    stored = _rows(client)[0].contact_request_id
    assert stored not in str(body)


def test_a_plan_named_on_a_panel_is_recorded_but_licenses_nothing(client):
    """Pressing "Talk to us" on the Platform panel says which panel it was.

    It is the same field the sign-up form's radio writes and it is read by
    nothing that decides what may be used — `entitlements.licensed_plan` reads
    `plan` alone.
    """
    client.post("/api/v1/contact", json={**GOOD, "plan": "platform"})
    assert _rows(client)[0].plan == "platform"


def test_an_enquiry_without_a_plan_is_an_ordinary_enquiry(client):
    """Most of them will be. The form asks which plan a visitor is asking
    about; it does not require an answer, because somebody who does not yet
    know which plan they need is exactly who this form is for."""
    res = client.post("/api/v1/contact",
                      json={k: v for k, v in GOOD.items() if k != "plan"})
    assert res.status_code == 202
    assert _rows(client)[0].plan == ""


# ── what it refuses ──────────────────────────────────────────────────────────
def test_an_enquiry_with_no_way_to_answer_it_is_refused(client):
    """A row with an unusable address is a lead that cannot be followed, and it
    looks identical to one that can until somebody tries to reply."""
    res = client.post("/api/v1/contact", json={**GOOD, "email": "not-an-address"})
    assert res.status_code == 400
    assert "email" in res.json()["detail"].lower()
    assert _rows(client) == []


def test_an_enquiry_with_nobody_in_it_is_refused(client):
    """An address and nothing else is a scrape, not an enquiry."""
    res = client.post("/api/v1/contact",
                      json={"email": "someone@example.com", "company": "",
                            "name": "   "})
    assert res.status_code == 400
    assert _rows(client) == []


def test_either_a_person_or_a_company_is_enough(client):
    """Both refusals above are about being able to *act* on the row. A named
    company with no contact name still can be."""
    assert client.post("/api/v1/contact", json={
        "email": "desk@acme.example", "company": "Acme Distributors"},
    ).status_code == 202


def test_an_unknown_plan_is_refused_rather_than_recorded_wrong(client):
    """The client and the server disagreeing about what the plans are is worth
    a refusal: silently storing "enterprise" answers the operator's question
    with something nobody offered."""
    res = client.post("/api/v1/contact", json={**GOOD, "plan": "enterprise"})
    assert res.status_code == 400
    assert _rows(client) == []


def test_a_very_long_message_is_refused_at_the_edge(client):
    """The body cap is declared on the request model, so an oversized message
    never reaches the column. 422 rather than 400: it is malformed against the
    schema, which is FastAPI's answer and not one to override."""
    res = client.post("/api/v1/contact",
                      json={**GOOD, "message": "x" * (contact.MAX_MESSAGE + 1)})
    assert res.status_code == 422
    assert _rows(client) == []


def test_the_rate_limit_stops_a_loop(client, monkeypatch):
    """A speed bump, and documented as one — see the router. What it buys is
    the accidental case: a retried form or a script in a loop."""
    monkeypatch.setattr(settings, "CONTACT_RATE_LIMIT_PER_HOUR", 2)
    codes = [client.post("/api/v1/contact", json=GOOD).status_code
             for _ in range(3)]
    assert codes == [202, 202, 429]
    assert len(_rows(client)) == 2


def test_the_door_stays_open_where_sign_up_is_closed(client, monkeypatch):
    """A deployment that does not let strangers create tenants still wants to
    be told somebody is interested. The two doors answer different questions,
    and `SELF_SERVE_SIGNUP` closes only the first."""
    monkeypatch.setattr(settings, "SELF_SERVE_SIGNUP", False)
    assert client.post("/api/v1/contact", json=GOOD).status_code == 202


# ── the queue an operator works ──────────────────────────────────────────────
def test_answering_one_takes_it_off_the_list(client):
    client.post("/api/v1/contact", json=GOOD)
    with client.maker() as session:
        row = contact.pending(session)[0]
        contact.mark_handled(session, row.contact_request_id, handled_by="sanketh")
        session.commit()
    assert _rows(client) == []


def test_what_was_asked_survives_being_answered(client):
    """Answering stamps the row; it does not edit the enquiry into a different
    one. The question six months from now is what somebody asked for and when.
    """
    client.post("/api/v1/contact", json=GOOD)
    with client.maker() as session:
        row = contact.pending(session)[0]
        contact.mark_handled(session, row.contact_request_id, handled_by="sanketh")
        session.commit()
        again = session.get(models.ContactRequest, row.contact_request_id)
        assert again.message == GOOD["message"]
        assert again.plan == "intelligence"
        assert again.handled_by == "sanketh"
        assert again.handled_at is not None


def test_answering_twice_is_refused_rather_than_overwriting_the_first(client):
    """When this was answered, and by whom, is the only audit the row has."""
    client.post("/api/v1/contact", json=GOOD)
    with client.maker() as session:
        row_id = contact.pending(session)[0].contact_request_id
        contact.mark_handled(session, row_id, handled_by="first")
        session.commit()
        with pytest.raises(contact.ContactRefused):
            contact.mark_handled(session, row_id, handled_by="second")


def test_the_queue_is_oldest_first(client):
    """The top of the list is the enquiry that has been ignored longest."""
    for i in range(3):
        client.post("/api/v1/contact",
                    json={**GOOD, "email": f"buyer{i}@acme.example",
                          "message": str(i)})
    assert [r.message for r in _rows(client)] == ["0", "1", "2"]


def test_the_operator_line_carries_what_a_reply_needs(client):
    """`describe` is what both CLIs print. An operator reading it must not have
    to open the database to answer: the address, the company and what they
    asked about are the reply."""
    client.post("/api/v1/contact", json=GOOD)
    line = contact.describe(_rows(client)[0])
    for expected in ["Acme Distributors", "buyer@acme.example", "Prophet 21",
                     "intelligence", "Three companies"]:
        assert expected in line


def test_the_plan_queue_shows_enquiries_from_people_who_are_not_customers_yet(
        client, monkeypatch):
    """One command over three queues.

    `python -m app.entitlements requests` answers "who wants to buy something",
    and somebody who filled in the public form is the only one of the three who
    has no account to ask from. Left out, they would sit in a table no command
    reads — which is the failure this whole endpoint exists to avoid.

    The command is run rather than described: it reads `SessionLocal` itself, so
    the fixture's maker is put there for the call. Asserting on its output is
    the only way to know an operator would actually see the row.
    """
    import io
    import sys
    from contextlib import redirect_stdout

    from app import db, entitlements

    client.post("/api/v1/contact", json=GOOD)

    monkeypatch.setattr(db, "SessionLocal", client.maker)
    monkeypatch.setattr(sys, "argv", ["entitlements", "requests"])
    out = io.StringIO()
    with redirect_stdout(out):
        assert entitlements._main() == 0
    printed = out.getvalue()
    assert "buyer@acme.example" in printed
    assert "Acme Distributors" in printed
    # And it says what to do next, in the same shape the other two queues do.
    assert "app.contact handled" in printed


def test_the_plan_queue_says_nobody_is_asking_when_nobody_is(client, monkeypatch):
    """The empty case is the one that has to stay true: an operator who reads
    "nobody is asking" while an enquiry sits unanswered would stop looking."""
    import io
    import sys
    from contextlib import redirect_stdout

    from app import db, entitlements

    monkeypatch.setattr(db, "SessionLocal", client.maker)
    monkeypatch.setattr(sys, "argv", ["entitlements", "requests"])
    out = io.StringIO()
    with redirect_stdout(out):
        assert entitlements._main() == 0
    assert "Nobody is asking" in out.getvalue()
