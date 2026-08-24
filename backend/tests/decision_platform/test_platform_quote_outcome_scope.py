"""Who may move a *platform* quote's outcome — the ``quote_id`` half of the seam.

``_may_record_erp_quote`` scoped the ERP-raised half of
``POST /quote-intelligence/outcome``: ``quote_document_ref`` identifies a row by
itself, so naming one you do not hold used to put a terminal WON or LOST on
somebody else's estimate. The ``quote_id`` half was left with **no role scope at
all**, on the reasoning that "a quote this platform priced is reached through a
store the caller already holds". That reasoning does not hold, and
``store.py``'s own comment on ``Quote.organizationId`` says why: the store is one
process-wide dict and the ids are enumerable — ``q{run}-{counter}`` — so a
salesperson can type an id they never held. Nothing on this path read the store
anyway; ``set_outcome`` keys straight off ``quote_id``.

Cross-*tenant* was never the hole and is not what these pin: ``set_outcome``
filters on the ``organization_id`` taken from the principal and never from the
body. The hole is *within* one organization, across desks — and because WON and
LOST are terminal by design, the account's rightful owner is refused from then
on. Same defect as ``filterCounts.MFLOOR`` (CLAUDE.md §1) in a third costume:
the refusal itself is the oracle, so "not yours" and "no such quote" have to be
one answer.

**The fixture gives the two quotes different owners on purpose.** The previous
round's review found a scope fixture whose two quotes shared one ``customer_id``
— it could not have detected the hole it was written to cover, because the
refused call and the permitted one asked the same question of the same account.
``q-theirs`` is Beta Works' and ``q-mine`` is Acme Engineering's, and only the
demo salesperson holds Acme.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.commercial import quote_service
from app.config import settings
from app.domain import models
from app.domain.enums import QuoteOutcomeStatus
from app.seed import SEED_PASSWORD

ORG = settings.DEFAULT_ORG_ID

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
    """Two accounts on two desks, and one platform quote each.

    ``c1`` is the demo salesperson's; ``c2`` is the manager's. The two quotes
    are attributed to *different* customers — the whole point of the fixture,
    and the thing a shared ``customer_id`` would quietly remove.

    ``q-theirs`` is SENT rather than DRAFT so that the terminal move the
    exploit makes is a legal transition: a refusal that came from the lifecycle
    instead of from the scope would prove nothing.
    """
    session.add(models.Customer(customer_id="c1", organization_id=ORG,
                                external_id="c1", name="Acme Engineering",
                                assigned_user_id="usr_sales"))
    session.add(models.Customer(customer_id="c2", organization_id=ORG,
                                external_id="c2", name="Beta Works",
                                assigned_user_id="usr_manager"))
    session.add(models.QuoteOutcome(organization_id=ORG, quote_id="q-theirs",
                                    customer_id="c2", customer_ref="Beta Works",
                                    status="SENT"))
    session.add(models.QuoteOutcome(organization_id=ORG, quote_id="q-mine",
                                    customer_id="c1",
                                    customer_ref="Acme Engineering",
                                    status="SENT"))
    session.commit()
    return session


def _status_of(book, quote_id: str) -> str:
    return book.query(models.QuoteOutcome).filter_by(
        organization_id=ORG, quote_id=quote_id).one().status


def test_a_salesperson_cannot_move_another_desks_platform_quote(api_client, book):
    """The gap itself: any enumerable id, moved to a terminal state.

    ``customer`` is left empty because it costs the exploit nothing — an
    out-of-scope name is blanked rather than refused by ``_visible_customer_ref``,
    so naming Beta Works and naming nobody both land the same row. What the
    write must not do is land at all.
    """
    r = _post(api_client, SALES, quote_id="q-theirs", status="LOST",
              loss_reason="PRICE", lost_to="Sandvik", customer="")
    assert r.status_code == 404, r.text

    # Refused before a field was written, so the desk that actually knows what
    # happened to this quote is not locked out of the one true answer.
    book.expire_all()
    assert _status_of(book, "q-theirs") == "SENT"
    mine = _post(api_client, MANAGER, quote_id="q-theirs", status="LOST",
                 loss_reason="DELIVERY", customer="Beta Works")
    assert mine.status_code == 200, mine.text
    assert mine.json()["loss_reason"] == "DELIVERY"


def test_a_salesperson_still_records_their_own_platform_quote(api_client, book):
    """The other half of the same fixture, and the reason the two quotes must
    not share an owner: this call and the one above differ only in whose
    account the quote is filed against."""
    r = _post(api_client, SALES, quote_id="q-mine", status="WON",
              customer="Acme Engineering")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "WON"


def test_a_manager_is_not_narrowed_on_the_platform_quote_path(api_client, book):
    """A manager keeps today's behaviour exactly, including opening a row for a
    quote id nothing has recorded yet — the same carve-out
    ``_may_record_erp_quote`` makes, and for the same reason: a manager is not
    scoped to a subset of the book, so there is no subset to check against."""
    assert _post(api_client, MANAGER, quote_id="q-theirs", status="WON",
                 customer="Beta Works").status_code == 200
    fresh = _post(api_client, MANAGER, quote_id="q-nobody-has-seen",
                  status="SENT", customer="Acme Engineering")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["status"] == "SENT"


def test_an_unattributable_platform_quote_fails_closed_for_a_salesperson(
        api_client, book):
    """Absence of evidence is not a pass (§1).

    Nothing in the book says whose quote ``q-nobody-has-seen`` is: no outcome
    row, no snapshot, and the in-process store cannot answer either — its ids
    carry a run marker, so a restart leaves every earlier id unknown to it. The
    benign default would be to let the write through and let ``set_outcome``
    open a fresh row, which is exactly how a terminal status gets parked on an
    id a colleague's builder has not minted yet.
    """
    r = _post(api_client, SALES, quote_id="q-nobody-has-seen", status="SENT",
              customer="Acme Engineering")
    assert r.status_code == 404, r.text
    assert book.query(models.QuoteOutcome).filter_by(
        quote_id="q-nobody-has-seen").count() == 0


def test_a_quote_the_snapshot_trail_attributes_is_scoped_by_that_trail(
        api_client, book):
    """``quote_decisions`` decides where no outcome row exists at all.

    A quote pushed to the ERP by another desk has snapshots long before anybody
    records an outcome for it, so the append-only trail is the attribution that
    exists when the mutable row does not. It is consulted *last*, after both
    facts about the outcome row itself: the trail names the account a quote was
    priced for, and the row being moved is the more specific fact about this
    outcome. Reading it ahead of the recorder is what refused a salesperson the
    quote they had sent themselves — see
    ``test_the_sender_can_finish_a_pushed_quote_after_the_account_moves_desks``.
    """
    book.add(models.QuoteDecision(
        organization_id=ORG, quote_id="q-snapshotted", quote_line_id="L1",
        customer_id="c2", customer_ref="Beta Works", quantity=1,
        as_of=date(2026, 5, 4), created_by_user_id="usr_manager"))
    book.commit()

    r = _post(api_client, SALES, quote_id="q-snapshotted", status="SENT",
              customer="")
    assert r.status_code == 404, r.text
    assert book.query(models.QuoteOutcome).filter_by(
        quote_id="q-snapshotted").count() == 0


def test_out_of_scope_and_never_existed_are_one_answer(api_client, book):
    """The refusal is the oracle, so both must be one sentence.

    ``q-theirs`` is real, SENT, and on another desk; ``q-never`` names nothing
    at all. A 403 on the first and a 409 "a quote that is DRAFT cannot become
    WON" on the second would answer "does this id name a live quote" for every
    id in the run — and the ids are ``q{run}-{counter}``, so the whole book is
    two nested loops. Byte-identical, and neither writes.
    """
    real = _post(api_client, SALES, quote_id="q-theirs", status="WON",
                 customer="")
    never = _post(api_client, SALES, quote_id="q-never", status="WON",
                  customer="")
    assert real.status_code == never.status_code == 404
    assert real.json()["detail"] == never.json()["detail"]
    book.expire_all()
    assert _status_of(book, "q-theirs") == "SENT"
    assert book.query(models.QuoteOutcome).filter_by(quote_id="q-never").count() == 0


def test_the_estimate_push_still_records_and_its_own_desk_can_finish_it(
        api_client, book, session):
    """``routers.quote`` calls the service, not this router — and must stay able to.

    The push writes the ERP link at the one moment the platform learns which
    estimate its own quote became, with exactly these arguments: both keys, no
    ``customer_id`` (the builder passes ``customer_ref``, which is the id when
    it has one), and the pusher's user id. It is a direct ``set_outcome`` call,
    so no guard in this router sits in front of it — asserted here rather than
    assumed, because a guard moved down into the service would break the push
    silently and the estimate would already exist in Zoho by then.

    The second half is the one the guard *does* touch: the salesperson who
    pushed it then presses Won. No customer ever resolved onto that row, so the
    attribution is the recorder — the same fallback ``_scoped_outcomes`` uses to
    keep an unresolved walk-in in its own salesperson's numbers.
    """
    row = quote_service.set_outcome(
        session, ORG, quote_id="q-pushed", status=QuoteOutcomeStatus.SENT,
        quote_document_ref="est-77", customer_ref="Walk-in, Mysore Road",
        user_id="usr_sales")
    session.commit()
    assert (row.quote_id, row.quote_document_ref) == ("q-pushed", "est-77")
    assert row.status == "SENT" and row.customer_id is None

    r = _post(api_client, SALES, quote_id="q-pushed", status="WON",
              customer="Walk-in, Mysore Road")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "WON"


def test_snapshotting_cannot_launder_another_desks_quote_onto_your_own_account(
        api_client, book):
    """The guard on ``/outcome`` was decorative while ``/snapshot`` had none.

    ``/snapshot`` ends in a ``set_outcome`` call that writes ``customer_id``,
    ``customer_ref`` and ``updated_by_user_id`` onto the row keyed by whatever
    ``quote_id`` the body carried — and DRAFT over DRAFT is not a transition,
    so the lifecycle never objected. A salesperson refused on ``q-theirs`` at
    the front door could relabel it onto their own account through this one and
    walk straight back in: the check reads exactly the two columns this path
    let the caller overwrite. DRAFT is the state every unpushed platform quote
    sits in, which is precisely the population that has a ``quote_id``.

    The append-only half matters as much as the row: ``insight._latest_lines``
    reads the newest snapshot per quote as the price the customer answered, so
    a line appended here is a price nobody quoted counted as one that was.
    """
    session = book
    session.add(models.QuoteOutcome(
        organization_id=ORG, quote_id="q-draft", customer_id="c2",
        customer_ref="Beta Works", status="DRAFT",
        note="Beta buyer signs only on Fridays",
        updated_by_user_id="usr_manager"))
    session.commit()

    r = api_client.post(
        "/api/v1/quote-intelligence/snapshot",
        json={"quote_id": "q-draft", "customer": "Acme Engineering",
              "lines": [{"line_id": "L1", "product": "X", "qty": 1,
                         "proposed_price": "100"}]},
        headers=_hdr(api_client, SALES))
    assert r.status_code == 404, r.text
    assert "Beta" not in r.text and "c2" not in r.text

    # Nothing written: not the attribution, not the recorder, not the trail.
    book.expire_all()
    row = book.query(models.QuoteOutcome).filter_by(quote_id="q-draft").one()
    assert (row.customer_id, row.updated_by_user_id) == ("c2", "usr_manager")
    assert row.note == "Beta buyer signs only on Fridays"
    assert book.query(models.QuoteDecision).filter_by(quote_id="q-draft").count() == 0


def test_a_salesperson_still_snapshots_a_quote_nothing_has_attributed_yet(
        api_client, book):
    """The other side of that guard, and why it is a parameter.

    A quote being priced for the first time has no outcome row and no trail —
    that is what "first save" means — so failing closed here would refuse the
    Quote Builder every new quote it opens. ``/snapshot`` is the one caller
    that lets an unattributed id through, and the snapshot it writes is what
    attributes the quote from then on.
    """
    r = api_client.post(
        "/api/v1/quote-intelligence/snapshot",
        json={"quote_id": "q-brand-new", "customer": "Acme Engineering",
              "lines": [{"line_id": "L1", "product": "X", "qty": 1,
                         "proposed_price": "100"}]},
        headers=_hdr(api_client, SALES))
    assert r.status_code == 201, r.text
    book.expire_all()
    assert book.query(models.QuoteOutcome).filter_by(
        quote_id="q-brand-new").one().customer_id == "c1"


def test_the_audit_read_answers_the_same_404_the_write_does(api_client, book):
    """A read rule that is the same rule must be the same function.

    ``GET /quotes/{quote_id}`` was scoped by organization alone, so the refusal
    next door was undone in one request: the outcome row with the account's id,
    its real name and a colleague's note, plus every snapshot with its
    quantities and quoted prices — while a missing id answered
    ``{"outcome": null, "decisions": []}``. That pair is both halves of what the
    404 exists to withhold, the live id list to aim at and the identity behind
    each one.
    """
    book.add(models.QuoteDecision(
        organization_id=ORG, quote_id="q-theirs", quote_line_id="L9",
        customer_id="c2", customer_ref="Beta Works", quantity=7,
        quoted_unit_price=1234, as_of=date(2026, 5, 4),
        created_by_user_id="usr_manager"))
    book.commit()

    real = api_client.get("/api/v1/quote-intelligence/quotes/q-theirs",
                          headers=_hdr(api_client, SALES))
    never = api_client.get("/api/v1/quote-intelligence/quotes/q-never",
                           headers=_hdr(api_client, SALES))
    assert real.status_code == never.status_code == 404
    assert real.json()["detail"] == never.json()["detail"]
    assert "Beta Works" not in real.text and "1234" not in real.text

    # Their own quote still reads back, and a manager is not narrowed at all.
    assert api_client.get("/api/v1/quote-intelligence/quotes/q-mine",
                          headers=_hdr(api_client, SALES)).status_code == 200
    assert api_client.get("/api/v1/quote-intelligence/quotes/q-theirs",
                          headers=_hdr(api_client, MANAGER)).status_code == 200


def test_assess_does_not_hand_back_a_stranger_quotes_outcome(api_client, book):
    """``/assess`` echoes ``outcome_to_dict`` for the ``quote_id`` in the body.

    ``_visible_customer_ref`` blanks the customer *name* the caller typed on
    this endpoint, and then this echo handed back that account's real name, its
    platform id and a colleague's note anyway — keyed on an id the caller chose
    rather than on a name they knew. Withheld as ``null`` rather than refused,
    so a live quote on another desk and an id that names nothing stay one
    answer here too.
    """
    body = {"customer": "Nobody At All",
            "lines": [{"line_id": "L1", "product": "X", "qty": 1}]}
    real = api_client.post("/api/v1/quote-intelligence/assess",
                           json=dict(body, quote_id="q-theirs"),
                           headers=_hdr(api_client, SALES))
    never = api_client.post("/api/v1/quote-intelligence/assess",
                            json=dict(body, quote_id="q-never"),
                            headers=_hdr(api_client, SALES))
    assert real.status_code == never.status_code == 200
    assert real.json()["outcome"] is None and never.json()["outcome"] is None
    # The account's own name, which is the half ``_visible_customer_ref``
    # already withholds on the field the caller typed. Not asserted on the raw
    # id: ``thresholds_version`` is a content hash and may spell anything.
    assert "Beta Works" not in real.text

    # Their own quote's outcome still travels with the assessment — the outcome
    # bar renders from this response and nothing else.
    mine = api_client.post("/api/v1/quote-intelligence/assess",
                           json=dict(body, quote_id="q-mine"),
                           headers=_hdr(api_client, SALES))
    assert mine.json()["outcome"]["status"] == "SENT"


def test_the_sender_can_finish_a_pushed_quote_after_the_account_moves_desks(
        api_client, book):
    """What ``routers.quote`` leaves behind, and who may close it.

    The push writes the trail with a resolved ``customer_id`` (the builder
    always starts from the picker) and then opens the outcome row with
    ``customer_ref`` and the pusher's user id and *no* ``customer_id``. So the
    row's own customer is always NULL and the trail always names an account —
    and while the trail was read first, attribution rested on who holds that
    account *now*. ``_sync_assignments`` rewrites ``assigned_user_id`` from the
    salesperson on the latest invoice on every pull, so the account moves and
    the person who priced and sent the quote met a 404 on the one screen still
    offering to close it.

    ``c2`` here is the account that moved to the other desk; ``usr_sales`` is
    the sender. The recorder on the row decides, which is ``_scoped_outcomes``'
    own rule for the same table — so what they may record is what their
    worklist already shows them.
    """
    book.add(models.QuoteDecision(
        organization_id=ORG, quote_id="q-pushed-then-moved", quote_line_id="L1",
        customer_id="c2", customer_ref="Beta Works", quantity=1,
        as_of=date(2026, 5, 4), created_by_user_id="usr_sales"))
    book.add(models.QuoteOutcome(
        organization_id=ORG, quote_id="q-pushed-then-moved",
        quote_document_ref="est-99", customer_ref="Beta Works", status="SENT",
        updated_by_user_id="usr_sales"))
    book.commit()

    r = _post(api_client, SALES, quote_id="q-pushed-then-moved", status="WON",
              customer="")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "WON"


def test_a_held_reference_does_not_tell_a_live_quote_id_from_an_empty_one(
        api_client, book):
    """The ERP half's answer used to be the platform half's fallback.

    An unattributed ``quote_id`` riding in beside a reference the caller was
    found to hold used to record — so with one worklist reference in hand, a
    404 meant "that id names a live quote and it is not yours" and a 200 meant
    "nothing has minted it", one request per id and a stray outcome row left
    behind each time. That is the sweep ``_NO_SUCH_PLATFORM_QUOTE``'s own
    comment rules out, so the fallback is gone: an unattributed id fails closed
    whatever sits beside it.

    ``est-mine`` is filed against ``c1`` — an ordinary reference this
    salesperson legitimately holds and could pick off their own worklist.
    """
    book.add(models.QuoteDoc(
        organization_id=ORG, connector="zoho", connection_id="conn-a",
        external_ref="est-mine", number="SLS/QTN-1", customer_id="c1",
        customer_ref="Acme Engineering", date=date(2026, 5, 4),
        source_status="sent", outcome="UNRECORDED"))
    book.commit()

    live = _post(api_client, SALES, quote_id="q-theirs", status="SENT",
                 quote_document_ref="est-mine", customer="")
    empty = _post(api_client, SALES, quote_id="q-never-minted", status="SENT",
                  quote_document_ref="est-mine", customer="")
    assert live.status_code == empty.status_code == 404
    assert live.json()["detail"] == empty.json()["detail"]

    # And neither left a row pointing at the reference they spent.
    book.expire_all()
    assert _status_of(book, "q-theirs") == "SENT"
    assert book.query(models.QuoteOutcome).filter_by(
        quote_id="q-never-minted").count() == 0
