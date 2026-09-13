"""Pressing "New quote" creates nothing. Saving creates exactly one quote.

The Quote Builder used to write a ``quote_drafts`` row and mint ``QB-0042``
against it the moment somebody pressed the button. Open the builder, look at
it, navigate away — and that number was spent, and an empty quote sat on the
whole desk's shared list for good. The workspace filled up with quotes nobody
had meant to start.

These tests are written against the row count rather than against the screen,
because that is the claim: not "the list looks empty" but "the database has
nothing in it". Every assertion below counts ``quote_drafts`` directly, so
hiding a record would fail here exactly as creating one does.

Same fixture shape as ``test_quote_workspace``: the quote endpoints on their
own database, signed in through the product's only login.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import dbsupport
import piesupport
from app import clock, quote_workspace
from app.db import get_session
from app.domain import models  # noqa: F401  (populate metadata)
from app.routers import platform_auth, quote
from app.seed import SEED_PASSWORD, ensure_org_and_users
from app.store import Line

OWNER = "s.menon@pie.example"
SALES = "r.nair@pie.example"
ORG = "org_pie"
COMPANY = piesupport.company_id("cx_quote_form")


@pytest.fixture()
def client():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    s.add(models.ZohoConnection(connection_id=COMPANY, organization_id=ORG,
                                label="SLS Engineers", zoho_organization_id="z1"))
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


def _hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/v1/auth/login", json={"email": email, "password": SEED_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def owner(client):
    return _hdr(client, OWNER)


@pytest.fixture()
def sales(client):
    return _hdr(client, SALES)


def _quotes(client) -> int:
    """How many quotes exist. The whole point of this file."""
    with client.Maker() as s:
        return int(s.scalar(select(func.count()).select_from(models.QuoteDraft)) or 0)


def _forms(client) -> int:
    with client.Maker() as s:
        return int(s.scalar(select(func.count())
                            .select_from(models.QuoteFormDraft)) or 0)


def _line(id_: str, code: str = "2001174", price: float | None = 500.0) -> Line:
    return Line(id=id_, raw=f"{code} x10", reqCode=code, reqDesc=code, reqQty=10,
                rel="EXACT", supplyCode=code, candidates=[], outcome="OK",
                semantics="EXACT", inBooks=True, listPrice=520.0, quoted=price,
                priceSource="USER" if price is not None else "LIST")


def _age_form(client, form_id: str, *, hours: int) -> None:
    """Backdate a form so the staleness rule can be exercised without waiting.

    The rule is about elapsed time, so the test has to move one of the two —
    the clock or the row — and moving the row is the one that leaves every
    other timestamp in the request alone.
    """
    with client.Maker() as s:
        row = s.get(models.QuoteFormDraft, form_id)
        row.updated_at = clock.now() - timedelta(hours=hours)
        s.commit()


def _seed_lines(client, quote_id: str, *lines: Line) -> None:
    """Put lines on whatever is under this id — form or quote — without the
    engine, straight through the workspace. Exactly what the intake endpoint
    does to it, minus the resolution."""
    with client.Maker() as s:
        q = quote_workspace.load(s, ORG, quote_id)
        q.lines.extend(lines)
        quote_workspace.save(s, q, None)
        s.commit()


# ── opening a form ───────────────────────────────────────────────────────────
def test_opening_a_form_creates_no_quote(client, owner):
    """The headline claim, counted rather than looked at."""
    r = client.post("/api/v1/quotes/form", json={}, headers=owner)
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["saved"] is False
    # No number, rather than a provisional one. A number somebody can write
    # down and then fail to find is worse than none.
    assert body["number"] == ""
    assert _quotes(client) == 0


def test_opening_and_closing_a_form_creates_no_quote(client, owner):
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    client.delete(f"/api/v1/quotes/form/{form['id']}", headers=owner)

    assert _quotes(client) == 0
    assert _forms(client) == 0


def test_a_form_with_lines_abandoned_creates_no_quote(client, owner):
    """Typed into and then thrown away. This is the case a flag on the quote
    row could not answer: the work existed, and no quote ever did."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"), _line("l2", "2001175"))

    client.delete(f"/api/v1/quotes/form/{form['id']}", headers=owner)

    assert _quotes(client) == 0
    assert _forms(client) == 0


def test_an_unsaved_form_is_on_nobody_s_list(client, owner, sales):
    """Not filtered out of the listing — absent from it, because it is not a
    quote. The workspace reads ``quote_drafts`` and a form is not one."""
    client.post("/api/v1/quotes/form", json={}, headers=owner)

    assert client.get("/api/v1/quotes", headers=owner).json()["quotes"] == []
    assert client.get("/api/v1/quotes", headers=sales).json()["quotes"] == []


def test_a_form_belongs_to_the_person_typing_it(client, owner, sales):
    """A quote is the desk's and a colleague may pick it up. A form is one
    person mid-sentence, and there is nothing to collaborate on yet."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()

    assert client.get(f"/api/v1/quotes/{form['id']}", headers=sales).status_code == 404
    assert client.get(f"/api/v1/quotes/{form['id']}", headers=owner).status_code == 200


def test_a_form_keeps_what_was_typed_into_it(client, owner):
    """The reason the scratch is on the server at all: a reload does not lose
    the work, and the lines keep their cost where the browser could not hold
    it."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"))

    again = client.get(f"/api/v1/quotes/{form['id']}", headers=owner).json()
    assert [ln["id"] for ln in again["lines"]] == ["l1"]
    assert again["saved"] is False
    assert _quotes(client) == 0


# ── saving ───────────────────────────────────────────────────────────────────
def test_saving_creates_exactly_one_quote_and_numbers_it(client, owner):
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"))

    saved = client.post(f"/api/v1/quotes/form/{form['id']}/save", headers=owner)
    assert saved.status_code == 200, saved.text

    body = saved.json()
    assert body["saved"] is True
    assert body["number"] == "QB-0001"
    # The number is minted here and nowhere earlier, so the first quote an
    # organization saves is its first number however many forms preceded it.
    assert _quotes(client) == 1
    # The form is gone: it was scratch, and there is no number on it to protect.
    assert _forms(client) == 0


def test_saving_carries_the_lines_over(client, owner):
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"), _line("l2", "2001175"))

    saved = client.post(f"/api/v1/quotes/form/{form['id']}/save",
                        headers=owner).json()
    assert [ln["id"] for ln in saved["lines"]] == ["l1", "l2"]
    assert saved["summary"]["subtotal"] == pytest.approx(10000.0)


def test_saving_twice_does_not_make_two_quotes(client, owner):
    """A double-click, a retried request, an impatient desk. The button is
    disabled while the first is in flight and this must hold without that."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"))

    first = client.post(f"/api/v1/quotes/form/{form['id']}/save", headers=owner)
    second = client.post(f"/api/v1/quotes/form/{form['id']}/save", headers=owner)

    assert first.status_code == 200
    assert second.status_code == 200
    # The same quote, not a second one wearing a second number.
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["number"] == first.json()["number"] == "QB-0001"
    assert _quotes(client) == 1


def test_saving_numbers_forms_in_the_order_they_are_saved(client, owner):
    """Numbers follow saving, not opening. Two forms opened and saved back to
    front get QB-0001 and QB-0002 in *that* order — which is the whole
    difference between a number that means something and one that was spent on
    a screen somebody closed."""
    first = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    second = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, first["id"], _line("l1"))
    _seed_lines(client, second["id"], _line("l2"))

    b = client.post(f"/api/v1/quotes/form/{second['id']}/save", headers=owner).json()
    a = client.post(f"/api/v1/quotes/form/{first['id']}/save", headers=owner).json()

    assert b["number"] == "QB-0001"
    assert a["number"] == "QB-0002"


def test_a_saved_quote_is_on_the_desk_s_list(client, owner, sales):
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    saved = client.post(f"/api/v1/quotes/form/{form['id']}/save",
                        headers=owner).json()

    shared = client.get("/api/v1/quotes", headers=sales).json()["quotes"]
    assert [q["id"] for q in shared] == [saved["id"]]


def test_saving_a_form_that_is_not_there_is_a_404(client, owner):
    assert client.post("/api/v1/quotes/form/nope/save",
                       headers=owner).status_code == 404


def test_discarding_a_form_twice_is_not_an_error(client, owner):
    """The only way to press Cancel twice is to have got what you wanted the
    first time."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    assert client.delete(f"/api/v1/quotes/form/{form['id']}",
                         headers=owner).status_code == 200
    assert client.delete(f"/api/v1/quotes/form/{form['id']}",
                         headers=owner).status_code == 200


# ── what an unsaved quote may not do ─────────────────────────────────────────
def test_an_unsaved_form_cannot_be_sent(client, owner):
    """The send writes a document into somebody's ledger keyed on a quote id.
    A form's id is discarded the moment Save mints the quote's own, so the
    refusal names the button rather than leaving a document pointing at
    nothing."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"))

    r = client.post(f"/api/v1/quotes/{form['id']}/estimate", headers=owner)
    assert r.status_code == 409
    assert "Save quote" in r.text


def test_an_unsaved_form_cannot_be_handed_over(client, owner):
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()

    r = client.put(f"/api/v1/quotes/{form['id']}/owner",
                   json={"user_id": "whoever"}, headers=owner)
    assert r.status_code == 409
    assert "Save quote" in r.text


# ── editing an existing quote ────────────────────────────────────────────────
def test_editing_a_saved_quote_updates_it_rather_than_making_another(client, owner):
    """The other half of the requirement, and the one a change like this is
    most likely to break: a saved quote still writes through on every change,
    keeping its id and its number."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"))
    saved = client.post(f"/api/v1/quotes/form/{form['id']}/save",
                        headers=owner).json()

    r = client.post(f"/api/v1/quotes/{saved['id']}/lines/l1/price",
                    json={"price": 725.0}, headers=owner)
    assert r.status_code == 200, r.text

    after = r.json()
    assert after["id"] == saved["id"]
    assert after["number"] == saved["number"]
    assert after["lines"][0]["quoted"] == pytest.approx(725.0)
    assert _quotes(client) == 1


def test_pricing_a_line_on_a_form_still_creates_no_quote(client, owner):
    """Editing details is not saving. The mutation endpoints work on a form
    exactly as they do on a quote — that is what makes the builder usable
    before Save — and none of them mints a number."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, form["id"], _line("l1"))

    r = client.post(f"/api/v1/quotes/{form['id']}/lines/l1/price",
                    json={"price": 640.0}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is False
    assert r.json()["lines"][0]["quoted"] == pytest.approx(640.0)
    assert _quotes(client) == 0


def test_the_customer_can_be_chosen_on_a_form(client, owner):
    r = client.put(
        f"/api/v1/quotes/"
        f"{client.post('/api/v1/quotes/form', json={}, headers=owner).json()['id']}"
        f"/customer",
        json={"customer": "Bharat Forge", "customer_id": None}, headers=owner)
    assert r.status_code == 200, r.text
    assert r.json()["customer"] == "Bharat Forge"
    assert r.json()["saved"] is False
    assert _quotes(client) == 0


# ── housekeeping ─────────────────────────────────────────────────────────────
def test_a_blank_form_left_for_a_day_is_collected(client, owner):
    """A tab closed on a blank form leaves a row nothing will ever discard.
    One holding no customer, no lines and no fields is carrying nothing
    anybody could want back, so the next New Quote sweeps it up."""
    stale = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _age_form(client, stale["id"], hours=48)

    client.post("/api/v1/quotes/form", json={}, headers=owner)

    assert _forms(client) == 1
    assert _quotes(client) == 0


def test_a_blank_form_opened_moments_ago_is_left_alone(client, owner):
    """Two tabs. Collecting the first would break it on the next keystroke —
    its id would stop resolving — and "I opened two and one died" is a far
    worse bug than a spare empty row."""
    first = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    client.post("/api/v1/quotes/form", json={}, headers=owner)

    assert _forms(client) == 2
    assert client.get(f"/api/v1/quotes/{first['id']}",
                      headers=owner).status_code == 200


def test_a_form_with_work_in_it_is_never_collected(client, owner):
    """The other side of that rule, and the important one: losing typed work
    to a housekeeping sweep would be far worse than the row it saves. Old
    enough to be swept on age alone — what spares it is having something in
    it."""
    kept = client.post("/api/v1/quotes/form", json={}, headers=owner).json()
    _seed_lines(client, kept["id"], _line("l1"))
    _age_form(client, kept["id"], hours=48)

    client.post("/api/v1/quotes/form", json={}, headers=owner)

    assert _forms(client) == 2
    still = client.get(f"/api/v1/quotes/{kept['id']}", headers=owner)
    assert still.status_code == 200
    assert [ln["id"] for ln in still.json()["lines"]] == ["l1"]


def test_one_person_s_abandoned_form_is_not_another_s_to_collect(client, owner, sales):
    """The sweep is scoped to the person opening the new form. A colleague
    opening theirs must not tidy away somebody else's blank one — it is not
    theirs to decide about, and the next thing that happens to it may be a
    paste."""
    theirs = client.post("/api/v1/quotes/form", json={}, headers=sales).json()
    _age_form(client, theirs["id"], hours=48)

    client.post("/api/v1/quotes/form", json={}, headers=owner)

    assert client.get(f"/api/v1/quotes/{theirs['id']}",
                      headers=sales).status_code == 200


def test_removing_an_unsaved_form_through_the_quote_route_really_removes_it(
        client, owner):
    """`DELETE /quotes/{id}` is the workspace's Remove, and a form can wear the
    same id. It must delete rather than answer `ok` having done nothing — the
    archive stamp a quote gets does not apply to a table it cannot see."""
    form = client.post("/api/v1/quotes/form", json={}, headers=owner).json()

    r = client.delete(f"/api/v1/quotes/{form['id']}", headers=owner)

    assert r.status_code == 200, r.text
    assert _forms(client) == 0
    assert _quotes(client) == 0
