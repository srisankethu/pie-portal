"""Recording why an ERP-raised quote was lost, over HTTP.

``set_outcome`` has accepted ``quote_document_ref`` since the quote pull
landed, but nothing could reach that argument: ``OutcomeRequest.quote_id`` was
a required ``str``, so the only quotes nameable over HTTP were the handful this
platform priced itself. Roughly three quarters of this book's estimates were
raised in Zoho and ended in no recorded way at all, and there was no request a
person could send that said why one of them was lost. These tests are the seam
between that service and the screen that will finally ask.

Most of what is pinned here is which **refusal** comes back, because the four
things ``set_outcome`` raises are all ``ValueError`` subclasses and a router
that caught the base class would flatten them into one status. The person at
the form can act on exactly one of them — the missing loss reason — and the
status is what tells them whether to look at the field they left empty, at the
lifecycle, or at nothing they can change.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.domain import models
from app.domain.enums import SELECTABLE_LOSS_REASONS
from app.seed import SEED_PASSWORD

ORG = settings.DEFAULT_ORG_ID

OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"


def _hdr(client, email: str) -> dict:
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _post(client, email: str, **body):
    return client.post("/api/v1/quote-intelligence/outcome", json=body,
                       headers=_hdr(client, email))


@pytest.fixture()
def book(session):
    """Two customers on different desks, and two Zoho estimates.

    ``c1`` is the demo salesperson's account and ``c2`` is not, which is what
    the scoping test turns on. The estimates are written directly rather than
    synced: what these tests exercise is the human row and the router above it,
    and a sync here would only re-prove ``test_quote_document_sync``.
    """
    session.add(models.Customer(customer_id="c1", organization_id=ORG,
                                external_id="c1", name="Acme Engineering",
                                assigned_user_id="usr_sales"))
    session.add(models.Customer(customer_id="c2", organization_id=ORG,
                                external_id="c2", name="Beta Works",
                                assigned_user_id="usr_manager"))
    for ref in ("est-1", "est-9"):
        session.add(models.QuoteDoc(
            organization_id=ORG, connector="zoho", connection_id="conn-a",
            external_ref=ref, number=f"SLS/QTN-{ref}", customer_id="c1",
            customer_ref="Acme Engineering", date=date(2026, 5, 4),
            expires_on=date(2026, 6, 3), source_status="sent",
            outcome="UNRECORDED"))
    # The other desk's estimate. Same shape, same book, different account —
    # which is the only difference the scope tests below are allowed to turn
    # on, so a refusal cannot come from the row being malformed instead.
    session.add(models.QuoteDoc(
        organization_id=ORG, connector="zoho", connection_id="conn-a",
        external_ref="est-7", number="SLS/QTN-est-7", customer_id="c2",
        customer_ref="Beta Works", date=date(2026, 5, 4),
        expires_on=date(2026, 6, 3), source_status="sent",
        outcome="UNRECORDED"))
    session.commit()
    return session


def test_an_erp_raised_quote_can_be_recorded_as_lost_over_http(api_client, book):
    """The path this change exists for, and it was unreachable before it.

    The estimate's own status is ``sent``, which ``reached_the_customer``
    accepts as evidence of a send, so the row opens SENT and LOST is a legal
    move in one call — a person recording a loss they already know about should
    not have to first assert a send the ERP already recorded.
    """
    r = _post(api_client, MANAGER, quote_document_ref="est-1", status="LOST",
              customer="Acme Engineering", loss_reason="PRICE",
              lost_to="Sandvik", note="beaten on price by about 8 per cent")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["quote_document_ref"] == "est-1"
    assert body["status"] == "LOST"
    assert body["loss_reason"] == "PRICE"
    assert body["lost_to"] == "Sandvik"

    row = book.query(models.QuoteOutcome).filter_by(
        organization_id=ORG, quote_document_ref="est-1").one()
    # The human fact lives on its own table and points at the document by
    # value; nothing was written back onto the derived erp_quotes row.
    assert row.quote_id is None
    assert row.customer_id == "c1"
    assert book.query(models.QuoteDoc).filter_by(
        external_ref="est-1").one().outcome == "UNRECORDED"


def test_an_erp_loss_without_a_reason_is_refused_and_the_choices_named(
        api_client, book):
    """422, because one required field is absent and the caller can supply it.

    The neighbouring 409s are refusals no body fixes. This one is the single
    case where the person at the form holds the answer, so the status has to
    keep the dialog open and the message has to list what may go in the field.
    """
    r = _post(api_client, MANAGER, quote_document_ref="est-1", status="LOST",
              customer="Acme Engineering")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    for reason in SELECTABLE_LOSS_REASONS:
        assert reason.value in detail
    # And nothing was filed under a benign default while the reason was missing.
    assert book.query(models.QuoteOutcome).filter_by(
        organization_id=ORG, status="LOST").count() == 0


def test_an_outcome_about_no_document_at_all_is_refused(api_client, book):
    """Neither key. 422 — the message names both fields that would satisfy it.

    Both fields default to ``None`` so that a body carrying only the other one
    validates; the cost of that is a body carrying neither also validating, and
    it is ``set_outcome`` that refuses it rather than a Pydantic rule here. Two
    writers of "a quote outcome needs a document to be about" is how the CLI
    and a future importer end up disagreeing with the screen.
    """
    r = _post(api_client, MANAGER, status="SENT", customer="Acme Engineering")
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "quote_id" in detail and "quote_document_ref" in detail
    assert book.query(models.QuoteOutcome).count() == 0


def test_a_platform_quote_pushed_to_the_erp_names_both_keys_and_is_one_row(
        api_client, book):
    """Both keys together is the normal shape, not a contradiction to reject.

    A quote this platform priced and pushed to Zoho is one quote: ``quote_id``
    is what the builder holds and ``quote_document_ref`` is what it became. The
    link is the only thing stopping it being counted twice — once from the row
    a person wrote and once from the document the pull read — so the endpoint
    must accept both on one request, and a stricter "exactly one" rule here
    would refuse what ``routers.quote`` already writes on every estimate push.
    """
    first = _post(api_client, MANAGER, quote_id="q1-1",
                  quote_document_ref="est-1", status="SENT",
                  customer="Acme Engineering")
    assert first.status_code == 200, first.text
    second = _post(api_client, MANAGER, quote_id="q1-1",
                   quote_document_ref="est-1", status="WON",
                   customer="Acme Engineering")
    assert second.status_code == 200, second.text
    assert second.json()["quote_id"] == "q1-1"
    assert second.json()["quote_document_ref"] == "est-1"
    assert book.query(models.QuoteOutcome).filter_by(
        organization_id=ORG).count() == 1


def test_moving_an_outcome_onto_a_different_erp_quote_is_a_conflict(
        api_client, book):
    """409, not 422, and the distinction is what the new exception type buys.

    The reason, the winner and the decision on that row were recorded about one
    quote. Repointing them would produce a loss nobody entered against a
    customer nobody spoke to, and no field in the body makes it acceptable — so
    it is a conflict with what is stored, which is what 409 says. It reached
    the router as a bare ``ValueError`` until ``QuoteOutcomeRepointed`` existed,
    where the only clause that could catch it was the 422 that means "one field
    is missing".
    """
    assert _post(api_client, MANAGER, quote_id="q1-1",
                 quote_document_ref="est-1", status="SENT",
                 customer="Acme Engineering").status_code == 200

    r = _post(api_client, MANAGER, quote_id="q1-1", quote_document_ref="est-9",
              status="WON", customer="Acme Engineering")
    assert r.status_code == 409, r.text
    assert "est-1" in r.json()["detail"] and "est-9" in r.json()["detail"]
    # Refused before a single field was written.
    row = book.query(models.QuoteOutcome).filter_by(quote_id="q1-1").one()
    assert (row.quote_document_ref, row.status) == ("est-1", "SENT")


def test_a_salesperson_records_against_their_own_accounts_only(api_client, book):
    """The scope rule this endpoint already applies, now on the ERP path too.

    ``_visible_customer_ref`` degrades an out-of-scope name to no name rather
    than answering 403 — a 403 would confirm the account exists, which is most
    of what an enumeration is after. The consequence worth pinning is that the
    outcome still records: what is lost is the attribution, so an account this
    person may not see cannot be reached by typing its name, and the loss is
    not silently filed against it either.
    """
    mine = _post(api_client, SALES, quote_document_ref="est-1", status="LOST",
                 customer="Acme Engineering", loss_reason="PRICE")
    assert mine.status_code == 200, mine.text
    assert mine.json()["customer_id"] == "c1"

    theirs = _post(api_client, SALES, quote_document_ref="est-9", status="LOST",
                   customer="Beta Works", loss_reason="PRICE")
    assert theirs.status_code == 200, theirs.text
    row = book.query(models.QuoteOutcome).filter_by(
        quote_document_ref="est-9").one()
    assert row.customer_id is None
    assert row.customer_ref == ""


def test_a_salesperson_cannot_record_an_outcome_on_another_desks_erp_quote(
        api_client, book):
    """The reference is the identity here, so scoping the typed name was not
    scoping the write.

    ``/insight/unrecorded-quotes`` already hides ``est-7`` from this
    salesperson — it is Beta Works' quote and Beta Works is not her account —
    and the worklist is where a reference comes from. The write applied no such
    rule, so naming the reference directly put a *terminal* LOST on somebody
    else's quote: won and lost cannot be reopened, so the person who actually
    knows what happened to it is refused from then on, and with the typed name
    blanked the row carries no ``customer_id`` and therefore escapes
    ``_scoped_outcomes`` into every manager's loss analysis. An empty
    ``customer`` is what the exploit sends because it costs nothing — an
    out-of-scope name is blanked rather than refused, so the fabricated loss
    lands either way.
    """
    hidden = api_client.get("/api/v1/insight/unrecorded-quotes",
                            headers=_hdr(api_client, SALES)).json()
    assert "est-7" not in [q["quote_document_ref"] for q in hidden["quotes"]]

    r = _post(api_client, SALES, quote_document_ref="est-7", status="LOST",
              loss_reason="PRICE", lost_to="Sandvik", customer="")
    assert r.status_code == 404, r.text
    # Nothing was written, so the account's owner is not locked out of the one
    # answer that is true.
    assert book.query(models.QuoteOutcome).filter_by(
        quote_document_ref="est-7").count() == 0

    mine = _post(api_client, MANAGER, quote_document_ref="est-7", status="LOST",
                 loss_reason="DELIVERY", customer="Beta Works")
    assert mine.status_code == 200, mine.text
    assert mine.json()["loss_reason"] == "DELIVERY"


def test_an_erp_quote_a_salesperson_may_not_see_reads_like_one_that_is_not_there(
        api_client, book):
    """Out of scope and does-not-exist are one answer, or the difference is the
    oracle.

    This endpoint writes, which is what makes the distinction worth having: the
    lifecycle refusals are themselves the tell. ``est-7`` is real and was sent,
    so a WON on it used to answer 200 while a reference naming nothing answered
    409 "a quote that is DRAFT cannot become WON" — two round trips that
    confirm an estimate exists and that the ERP says it reached a customer,
    about an account this reader may not see. Both are now the same 404 with
    the same sentence, and neither writes.
    """
    real = _post(api_client, SALES, quote_document_ref="est-7", status="WON",
                 customer="")
    nothing = _post(api_client, SALES, quote_document_ref="est-nope",
                    status="WON", customer="")
    assert real.status_code == nothing.status_code == 404
    assert real.json()["detail"] == nothing.json()["detail"]
    assert book.query(models.QuoteOutcome).count() == 0


def test_a_salesperson_is_not_handed_back_the_row_a_manager_wrote(
        api_client, book):
    """The read half of the same defect, and it needed no write to work.

    ``outcome_to_dict`` echoes the whole stored row. Posting a SENT — a legal
    no-op on a quote already SENT — returned ``customer_id``, the customer's
    real name and the note a manager had typed about that account. That is the
    lookup ``_visible_customer_ref`` exists to prevent, running backwards from
    a reference to an identity, plus a colleague's contact note.
    """
    wrote = _post(api_client, MANAGER, quote_document_ref="est-7",
                  status="SENT", customer="Beta Works",
                  note="PO promised Tuesday, spoke to Vinod")
    assert wrote.status_code == 200, wrote.text
    assert wrote.json()["customer_id"] == "c2"

    r = _post(api_client, SALES, quote_document_ref="est-7", status="SENT",
              customer="")
    assert r.status_code == 404, r.text
    assert "Vinod" not in r.text and "Beta Works" not in r.text and "c2" not in r.text


def test_a_salesperson_may_not_borrow_another_desks_reference_for_their_own_quote(
        api_client, book):
    """Both keys together is the normal shape, and it is also the second
    costume.

    ``set_outcome`` resolves the document only where the reference is the
    identity, so a ``quote_id`` beside it used to wave the reference straight
    through onto the row. The row then *holds* the other desk's reference, and
    the owner recording ``est-7`` is keyed onto this row and meets a terminal
    status somebody else set — the same lock-out by another route, with the
    fabricated loss now attributed to the wrong account rather than to none.
    """
    r = _post(api_client, SALES, quote_id="q-mine", quote_document_ref="est-7",
              status="LOST", loss_reason="PRICE", customer="Acme Engineering")
    assert r.status_code == 404, r.text
    assert book.query(models.QuoteOutcome).count() == 0

    # And it does not run the other way either. ``est-1`` *is* theirs, but
    # ``q-mine`` is a platform id nothing in this book attributes, and for one
    # round a held reference waved exactly that through — which made this 404
    # mean "that id names a live quote on another desk" while an unminted id
    # answered 200, one request per id. Both keys together stays the normal
    # shape; what it no longer does is supply the missing half of an id nobody
    # has priced. ``_holds_platform_quote`` has the walk.
    both = _post(api_client, SALES, quote_id="q-mine", quote_document_ref="est-1",
                 status="LOST", loss_reason="PRICE", customer="Acme Engineering")
    assert both.status_code == 404, both.text
    # Refused on the platform key, not the ERP one — the reference was fine and
    # did not unlock the id beside it. Which key failed is the one thing these
    # two messages may differ about, because each is uniform across every way
    # its own key can fail; the id being live or empty is not, and
    # ``test_a_held_reference_does_not_tell_a_live_quote_id_from_an_empty_one``
    # is where that half is pinned.
    assert "answers to that id" in both.json()["detail"]
    assert book.query(models.QuoteOutcome).count() == 0

    # And the ordinary case still records: their own account's ERP quote, named
    # the way the worklist names it — by the reference alone, which is what
    # ``intelligence.ts`` sends and the only shape any screen produces.
    ok = _post(api_client, SALES, quote_document_ref="est-1",
               status="LOST", loss_reason="PRICE", customer="Acme Engineering")
    assert ok.status_code == 200, ok.text
    assert ok.json()["customer_id"] == "c1"


def test_a_reference_another_row_already_holds_is_refused_not_500ed(
        api_client, book):
    """The half the repoint guard above did not cover, found by a review.

    That guard checks *this* row's claim to a reference. It says nothing about
    another row already holding it — which is the ordinary sequence on this
    branch: somebody records the ERP quote from the Unanswered worklist, and the
    platform quote is pushed to Zoho afterwards. ``quote_outcomes`` is unique on
    ``(organization_id, quote_document_ref)``, so the write raised an
    IntegrityError at the *outer* commit, outside every ``except`` on the way —
    a 500, after the Zoho estimate had already been created, rolling back a
    transaction the ERP knew nothing about.

    Caught in the service, it is the refusal the router already maps to 409.
    """
    # The ERP quote, recorded on its own reference from the worklist.
    assert _post(api_client, MANAGER, quote_document_ref="est-9",
                 status="LOST", loss_reason="PRICE",
                 customer="Acme Engineering").status_code == 200
    # A different platform quote, now trying to claim the same document.
    assert _post(api_client, MANAGER, quote_id="q1-1", status="SENT",
                 customer="Acme Engineering").status_code == 200

    r = _post(api_client, MANAGER, quote_id="q1-1", quote_document_ref="est-9",
              status="WON", customer="Acme Engineering")

    assert r.status_code == 409, r.text
    assert "est-9" in r.json()["detail"]
    # Both rows intact, and neither one silently repointed.
    rows = book.query(models.QuoteOutcome).filter_by(organization_id=ORG).all()
    assert {r.quote_document_ref for r in rows} == {"est-9", None}
