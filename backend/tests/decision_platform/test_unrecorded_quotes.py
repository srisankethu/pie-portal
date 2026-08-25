"""The worklist over quotes with no recorded outcome, and what it refuses.

Three quarters of this book's quotes end with nobody writing down what happened.
``ingestion`` already refuses to read that silence as a loss; this list is what
turns the resulting pile into a morning's work, and the tests below are mostly
about the ways a ranking can quietly lie about its own inputs.

The three that matter:

- **A quote with no expiry date has no age past expiry.** Not zero. Sorting it
  as zero would file the oldest half of the pile with the freshest quotes in the
  book, where nobody would ever see it again — the ``sum(... or 0)`` failure
  wearing a sort key. It gets its own group and its own count.
- **A quote with no total is kept, and never valued at zero.** It is real
  quoting activity; what is missing is only the ranking key, so it sorts on the
  date the ERP always supplies and is counted as unvalued rather than dropped or
  summed as nothing.
- **Nothing on this list may read as lost.** The sweep asserts over the whole
  serialized response rather than field by field, because every field-level
  assertion in the ``filterCounts.MFLOOR`` file passed while the endpoint gave
  up cost.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import dbsupport
from app.commercial.insight import unrecorded
from app.db import get_session
from app.domain import models
from app.domain.enums import QuoteDocOutcome
from app.routers import insight, platform_auth
from app.seed import SEED_PASSWORD, ensure_org_and_users

ORG = "org_pie"
AS_OF = date(2026, 8, 24)

OWNER = "s.menon@pie.example"
MANAGER = "m.rao@pie.example"
SALES = "r.nair@pie.example"


def _doc(s, ref: str, *, status: str = "sent",
         outcome: str = QuoteDocOutcome.UNRECORDED.value,
         customer: str | None = "c1", customer_ref: str = "Acme Engineering",
         raised: date = date(2026, 5, 1), expires: date | None = None,
         total: str | None = "10000", opened: datetime | None = None,
         decided: date | None = None) -> None:
    """One row of ``quote_documents``, as a sync would have written it.

    Written directly rather than through a sync because this file is about the
    read: ``test_quote_document_sync`` already pins that the classifier never
    turns a status into a loss, and re-running the pull here would test that
    twice and the ordering once.
    """
    s.add(models.QuoteDoc(
        organization_id=ORG, connector="zoho", connection_id="conn1",
        external_ref=ref, number=f"SLS/QTN-{ref}", customer_id=customer,
        customer_ref=customer_ref, date=raised, expires_on=expires,
        source_status=status, outcome=outcome, decided_on=decided,
        total=Decimal(total) if total is not None else None,
        client_viewed_at=opened))


def _seed(s) -> None:
    """Two accounts, one held by the demo salesperson and one not."""
    s.add(models.Customer(customer_id="c1", organization_id=ORG,
                          external_id="c1", name="Acme Engineering",
                          assigned_user_id="usr_sales"))
    s.add(models.Customer(customer_id="c2", organization_id=ORG,
                          external_id="c2", name="Beta Works",
                          assigned_user_id="usr_manager"))
    s.flush()


@pytest.fixture()
def maker():
    engine = dbsupport.fresh_engine()
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    s = Maker()
    ensure_org_and_users(s)
    _seed(s)
    s.commit()
    s.close()
    return Maker


def _build(Maker, **kw):
    s = Maker()
    try:
        return unrecorded.build(s, ORG, as_of=AS_OF,
                                customer_names={"c1": "Acme Engineering",
                                                "c2": "Beta Works"}, **kw)
    finally:
        s.close()


# ── the ranking ─────────────────────────────────────────────────────────────
def test_the_longest_lapsed_and_largest_quote_is_asked_about_first(maker):
    """Lapsed age first, then money — both descending, both on the row.

    The pairing is the point. Ranking on money alone puts a quote that expired
    yesterday above one that expired in March, and the March one is the one
    whose answer is about to be forgotten.
    """
    s = maker()
    _doc(s, "small-old", expires=date(2026, 3, 1), total="5000")
    _doc(s, "big-old", expires=date(2026, 3, 1), total="90000")
    _doc(s, "big-recent", expires=date(2026, 8, 20), total="400000")
    s.commit()
    s.close()

    rows = _build(maker)
    assert [r.quote_document_ref for r in rows] == [
        "big-old", "small-old", "big-recent"]
    assert [r.days_past_expiry for r in rows] == [176, 176, 4]
    assert all(r.group == unrecorded.PAST_EXPIRY for r in rows)


def test_a_live_quote_ranks_below_every_lapsed_one_however_large(maker):
    """The offer the customer still has time to answer is a different job."""
    s = maker()
    _doc(s, "live", expires=date(2026, 12, 1), total="1000000")
    _doc(s, "lapsed", expires=date(2026, 8, 23), total="100")
    s.commit()
    s.close()

    rows = _build(maker)
    assert [r.quote_document_ref for r in rows] == ["lapsed", "live"]
    assert rows[1].group == unrecorded.STILL_OPEN
    # Not "zero days past expiry" — an expiry still ahead is not a lapse.
    assert rows[1].days_past_expiry is None


def test_the_group_order_is_published_rather_than_implied(maker):
    """A reader recomputes the list from the response or the list is a score."""
    assert unrecorded.GROUP_ORDER == (unrecorded.PAST_EXPIRY,
                                      unrecorded.EXPIRY_NOT_RECORDED,
                                      unrecorded.STILL_OPEN)


# ── the missing expiry ──────────────────────────────────────────────────────
def test_a_quote_with_no_expiry_has_an_unanswerable_age_not_a_zero(maker):
    """The §1 case: absence of evidence is not a pass.

    Read as zero days past expiry it would sort against the freshest quotes in
    the book and disappear. It sits in its own group, between the ones known to
    have lapsed and the ones known to be live, because it is neither.
    """
    s = maker()
    _doc(s, "lapsed", expires=date(2026, 6, 1), total="100")
    _doc(s, "no-expiry", expires=None, total="100")
    _doc(s, "live", expires=date(2026, 12, 1), total="100")
    s.commit()
    s.close()

    rows = _build(maker)
    assert [r.quote_document_ref for r in rows] == ["lapsed", "no-expiry", "live"]
    [gap] = [r for r in rows if r.quote_document_ref == "no-expiry"]
    assert gap.group == unrecorded.EXPIRY_NOT_RECORDED
    assert gap.days_past_expiry is None
    assert gap.expires_on is None

    counts = unrecorded.totals(rows)
    assert counts["by_group"] == {unrecorded.PAST_EXPIRY: 1,
                                  unrecorded.EXPIRY_NOT_RECORDED: 1,
                                  unrecorded.STILL_OPEN: 1}
    # The oldest lapse on record, over the rows that have one. Not the oldest
    # quote — the one with no expiry cannot contribute a lapse it has no basis
    # for, and must not be read as the longest one either.
    assert counts["longest_lapse_days"] == 84


def test_the_longest_lapse_is_absent_rather_than_zero_when_nothing_has_expired(maker):
    s = maker()
    _doc(s, "no-expiry", expires=None)
    _doc(s, "live", expires=date(2026, 12, 1))
    s.commit()
    s.close()

    assert unrecorded.totals(_build(maker))["longest_lapse_days"] is None


# ── the missing total ───────────────────────────────────────────────────────
def test_a_quote_with_no_total_is_kept_counted_and_never_valued_at_zero(maker):
    s = maker()
    _doc(s, "priced", expires=date(2026, 6, 1), total="60000")
    _doc(s, "unpriced-old", expires=date(2026, 6, 1), total=None,
         raised=date(2026, 4, 1))
    _doc(s, "unpriced-new", expires=date(2026, 6, 1), total=None,
         raised=date(2026, 5, 20))
    s.commit()
    s.close()

    rows = _build(maker)
    # Priced rows first — they are the ones the money key can order. The
    # unvalued pair falls back to the day they were raised, oldest first.
    assert [r.quote_document_ref for r in rows] == [
        "priced", "unpriced-old", "unpriced-new"]
    assert rows[1].value is None

    counts = unrecorded.totals(rows)
    assert counts["count"] == 3
    assert counts["quotes_without_a_value"] == 2
    # 60000 and nothing else. A missing total contributed no rupees, and the
    # count beside it is what says the figure is over two rows of three.
    assert counts["value_at_stake"] == 60000.0


# ── nothing here is a loss ──────────────────────────────────────────────────
def test_a_decided_quote_never_appears_on_the_worklist(maker):
    """WON and LOST are answered. This list is the ones with neither."""
    s = maker()
    _doc(s, "won", status="invoiced", outcome=QuoteDocOutcome.WON.value,
         decided=date(2026, 6, 2), expires=date(2026, 6, 1))
    _doc(s, "lost", status="declined", outcome=QuoteDocOutcome.LOST.value,
         decided=date(2026, 6, 2), expires=date(2026, 6, 1))
    _doc(s, "silent", status="expired", expires=date(2026, 6, 1))
    s.commit()
    s.close()

    assert [r.quote_document_ref for r in _build(maker)] == ["silent"]


def test_no_unrecorded_quote_is_presented_as_lost(client):
    """Sweep-shaped, over the whole response rather than field by field.

    Every status Zoho can put on an unanswered quote goes in — including
    ``expired``, which is the one a reader is most tempted to call a loss — and
    the assertion is that the word never appears in what comes back, in any
    field, under any key. A field-level check would pass while a new key spelled
    it out; that is exactly how ``filterCounts.MFLOOR`` survived.
    """
    body = client.get("/api/v1/insight/unrecorded-quotes",
                      headers=_hdr(client, MANAGER))
    raw = body.text.lower()
    for forbidden in ("lost", "loss", "declined", "won", "win_rate"):
        assert forbidden not in raw, forbidden
    payload = body.json()
    assert payload["count"] == 7
    assert {q["source_status"] for q in payload["quotes"]} == {
        "draft", "pending_approval", "sent", "viewed", "expired", "approved"}


# ── opening is a positive fact; its absence is not the opposite ─────────────
def test_an_unrecorded_opening_is_not_a_claim_that_nobody_opened_it(maker):
    """``client_viewed_at IS NULL`` is silence about an open, not a no.

    A draft was never sent, so the ERP could not have seen it opened; calling
    that "the customer never looked at it" is a false statement about a customer
    dressed as a count. The response names the absence as an absence.
    """
    s = maker()
    _doc(s, "opened", opened=datetime(2026, 5, 3, 9, 30, tzinfo=timezone.utc))
    _doc(s, "draft", status="draft")
    s.commit()
    s.close()

    counts = unrecorded.totals(_build(maker))
    assert counts["opened"] == 1
    assert counts["opening_not_recorded"] == 1
    assert "never_opened" not in counts


# ── role scope ──────────────────────────────────────────────────────────────
@pytest.fixture()
def client(maker):
    s = maker()
    _doc(s, "q-draft", status="draft", expires=None, total="1000")
    _doc(s, "q-pending", status="pending_approval", expires=date(2026, 7, 1),
         total="2000")
    _doc(s, "q-approved", status="approved", expires=date(2026, 7, 1),
         total="3000")
    _doc(s, "q-sent", status="sent", expires=date(2026, 6, 1), total="4000")
    _doc(s, "q-viewed", status="viewed", expires=date(2026, 6, 1), total="5000",
         opened=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc))
    # Somebody else's account, and a quote whose customer never resolved.
    _doc(s, "q-other", status="expired", customer="c2",
         customer_ref="Beta Works", expires=date(2026, 4, 1), total="900000")
    _doc(s, "q-unattributed", status="sent", customer=None,
         customer_ref="Walk-in", expires=date(2026, 4, 1), total="700000")
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _hdr(c, email):
    r = c.post("/api/v1/auth/login",
               json={"email": email, "password": SEED_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_a_salesperson_sees_their_own_accounts_and_not_the_book(client):
    """Their worklist, not the book's — and not the size of the book either."""
    body = client.get("/api/v1/insight/unrecorded-quotes",
                      headers=_hdr(client, SALES)).json()
    assert {q["quote_document_ref"] for q in body["quotes"]} == {
        "q-draft", "q-pending", "q-approved", "q-sent", "q-viewed"}
    assert body["count"] == 5
    # The two they cannot see contribute nothing anywhere, including to the
    # money figure — an excluded count is itself a measure of a book they do
    # not hold.
    assert body["value_at_stake"] == 15000.0


def test_a_hand_assigned_account_lands_in_the_right_persons_worklist(maker, client):
    """The account somebody was *given* is theirs; the one taken away is not.

    ``Customer.assigned_user_id`` is Zoho's derived answer — rewritten by
    ``_sync_assignments`` from the salesperson on the latest invoice — and a
    manager handing an account over writes a ``CustomerAccountOwner`` row that
    beats it. Scoping this list on the column instead of through
    ``commercial/ownership`` fails in both directions at once: it hides a
    reassigned account from the person it was given to and leaves it visible to
    the person it was taken from, which is the failure ``authz.can_view_customer``
    names as the reason the ownership rule exists.

    Both directions are asserted here rather than one, because a scope that
    reads the wrong table passes a one-sided test the moment the two sources
    happen to agree.
    """
    s = maker()
    # Beta Works handed to the salesperson; Acme taken off them. Neither
    # ``assigned_user_id`` is touched — the point of the typed row is that the
    # next sync cannot undo the decision.
    s.add(models.CustomerAccountOwner(organization_id=ORG, customer_id="c2",
                                      user_id="usr_sales",
                                      set_by_user_id="usr_manager"))
    s.add(models.CustomerAccountOwner(organization_id=ORG, customer_id="c1",
                                      user_id="usr_manager",
                                      set_by_user_id="usr_manager"))
    s.commit()
    s.close()

    body = client.get("/api/v1/insight/unrecorded-quotes",
                      headers=_hdr(client, SALES)).json()
    refs = {q["quote_document_ref"] for q in body["quotes"]}
    assert refs == {"q-other"}
    assert body["count"] == 1
    assert body["value_at_stake"] == 900000.0


def test_the_managers_copy_carries_the_unattributed_quote(client):
    """Nobody recorded an account for it, so it belongs to whoever can fix it."""
    body = client.get("/api/v1/insight/unrecorded-quotes",
                      headers=_hdr(client, OWNER)).json()
    refs = {q["quote_document_ref"] for q in body["quotes"]}
    assert "q-unattributed" in refs and "q-other" in refs
    [walk_in] = [q for q in body["quotes"]
                 if q["quote_document_ref"] == "q-unattributed"]
    assert walk_in["customer_id"] is None
    assert walk_in["customer_label"] == "Walk-in"


def test_the_response_carries_no_cost_or_margin_anywhere(client):
    """Absent, not masked. ``value`` is a selling total and stays."""
    raw = client.get("/api/v1/insight/unrecorded-quotes",
                     headers=_hdr(client, SALES)).text
    for forbidden in ("margin", "unit_cost", "gross_profit", "cogs", "_pp"):
        assert forbidden not in raw


def test_the_headline_counts_the_pile_and_the_page_says_what_it_shows(client):
    """A truncated screen must not make the pile look the size of the page."""
    body = client.get("/api/v1/insight/unrecorded-quotes?limit=2",
                      headers=_hdr(client, MANAGER)).json()
    assert body["listed"] == 2
    assert len(body["quotes"]) == 2
    assert body["count"] == 7
    assert body["value_at_stake"] == 1615000.0


def test_an_empty_worklist_explains_itself_rather_than_showing_nothing(maker):
    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight.router)

    def _override():
        sess = maker()
        try:
            yield sess
            sess.commit()
        finally:
            sess.close()

    app.dependency_overrides[get_session] = _override
    c = TestClient(app)
    body = c.get("/api/v1/insight/unrecorded-quotes",
                 headers=_hdr(c, MANAGER)).json()
    assert body["count"] == 0
    assert "silence" in body["empty_reason"]
    assert body["thresholds_version"]


# ── the list has to shrink when it is worked ────────────────────────────────
def test_recording_an_outcome_takes_the_quote_off_the_list(maker):
    """The defect a review caught, and the one that made this screen useless.

    ``QuoteDoc.outcome`` is the ERP's word and only the sync writes it;
    ``QuoteOutcome`` is the row a person writes, and ``quote_service``
    deliberately never touches ``quote_documents``. Both halves are right. The
    read filtered on the first alone, so recording a loss through the capture
    dialog changed nothing this query could see: the quote came back on the next
    reload, the headline never moved, and recording it again as WON answered
    409. A worklist whose whole purpose is to grow the six-loss sample §5.1
    needs could not be worked down at all.
    """
    from app.commercial import quote_service
    from app.domain.enums import QuoteLossReason, QuoteOutcomeStatus

    s = maker()
    _doc(s, "EST-8003", status="expired", expires=date(2026, 6, 1))
    s.commit()
    assert [q.quote_document_ref for q in _build(maker)] == ["EST-8003"]

    quote_service.set_outcome(
        s, ORG, quote_document_ref="EST-8003",
        status=QuoteOutcomeStatus.LOST, loss_reason=QuoteLossReason.PRICE,
        customer_id="c1")
    s.commit()

    assert _build(maker) == []


def test_a_quote_only_priced_is_still_unanswered(maker):
    """The edge the fix must not overshoot. DRAFT and SENT are not endings —
    the platform put a price on it and nobody has said how it went, which is
    exactly this list's population. Excluding them would empty the pile by
    redefining it."""
    from app.commercial import quote_service
    from app.domain.enums import QuoteOutcomeStatus

    s = maker()
    _doc(s, "EST-8004", status="sent", expires=date(2026, 6, 1))
    s.commit()
    quote_service.set_outcome(
        s, ORG, quote_document_ref="EST-8004",
        status=QuoteOutcomeStatus.SENT, customer_id="c1")
    s.commit()

    assert [q.quote_document_ref for q in _build(maker)] == ["EST-8004"]
