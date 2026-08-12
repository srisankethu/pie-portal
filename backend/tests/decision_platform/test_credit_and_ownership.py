"""The credit line, whose book the account is in, and the three states of "no".

The platform could already say that an account takes 69 days to pay. It could
not say that the account is ₹8 lakh past the limit somebody gave them, because
no limit existed anywhere — and it could not say who should call them, because
no account belonged to a person anybody had decided on.

The tests that matter here are the refusals and the distinctions, not the
subtraction:

1. A typed value **survives a re-sync**. That is the entire reason both of these
   are tables rather than columns on ``customers``, and the failure it prevents
   is silent — an afternoon of somebody's work erased by the next pull.
2. **Absent, zero and over are three different states.** No limit recorded is
   not a limit of zero and is certainly not unlimited, and a withdrawal is a
   delete rather than a zero.
3. A limit and a balance are **receivables, not cost**, so a salesperson reads
   them. Scoping this manager-only "to be safe" would take the collections list
   away from the person whose job collections are.
4. Ownership is resolved in **one place**. A handed-over account that still
   reads ``assigned_user_id`` directly stays in the wrong person's book on
   whichever screen forgot.

The fixture is the synced book from ``test_decision_intelligence`` — one
customer, ₹1,01,900 outstanding of which ₹16,700 is overdue. Importing it rather
than rebuilding it is deliberate: a second copy of "a book" drifts from the
first, and then two tests disagree about what this business looks like.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.commercial import ownership
from app.commercial.insight import credit
from app.db import get_session
from app.domain import models

from .test_decision_intelligence import ORG, _fold, _seed

#: What the fixture book owes: INV-2 (₹16,700, overdue) and INV-3 (₹85,200, not
#: yet due). INV-1 is settled and contributes nothing, which is the case a
#: "count every invoice" reading gets wrong.
OUTSTANDING = 101900.0
OVERDUE = 16700.0


# ── the arithmetic, with no database in it ──────────────────────────────────
def _balance(outstanding: str, overdue: str = "0") -> credit.Balance:
    return credit.Balance(customer_id="c", outstanding=Decimal(outstanding),
                          overdue=Decimal(overdue))


def test_no_limit_recorded_is_not_a_limit_of_zero():
    """The distinction the whole feature rests on. One means nobody has decided;
    the other means somebody decided this account ships against cash. They are
    different instructions to the person holding the order."""
    none = credit.exposure(_balance("100000"), None)
    zero = credit.exposure(_balance("100000"), Decimal("0"))

    assert none.has_limit is False
    assert none.status == credit.NO_LIMIT
    # Not "infinite headroom", not "over by everything" — unanswerable, and it
    # says so rather than picking one.
    assert none.headroom is None and none.over_by is None

    assert zero.has_limit is True
    assert zero.status == credit.OVER
    assert zero.over_by == Decimal("100000.00")


def test_over_the_limit_is_reported_as_the_amount_it_ran_past():
    """₹8 lakh over is the sentence somebody acts on. A percentage is not."""
    over = credit.exposure(_balance("1300000", "800000"), Decimal("500000"))

    assert over.status == credit.OVER
    assert over.over_by == Decimal("800000.00")
    assert over.headroom == Decimal("-800000.00")     # signed, once
    assert over.utilisation == Decimal("2.6000")


def test_most_of_the_line_used_is_its_own_state():
    """Between "fine" and "stop" there is a conversation to have before the next
    order rather than after it."""
    near = credit.exposure(_balance("950000"), Decimal("1000000"))
    fine = credit.exposure(_balance("100000"), Decimal("1000000"))

    assert (near.status, fine.status) == (credit.NEAR, credit.WITHIN)
    assert near.headroom == Decimal("50000.00")


def test_a_share_of_a_zero_limit_is_not_a_number():
    """Reporting it as 0 or as a large multiple would both be inventions. The
    status still answers the question."""
    assert credit.exposure(_balance("0"), Decimal("0")).utilisation is None
    assert credit.exposure(_balance("0"), Decimal("0")).status == credit.WITHIN


def test_an_account_with_no_limit_is_present_rather_than_missing():
    """Unlike an absent payment term, which means "leave the schedule alone".
    An account owing money against no recorded limit is exactly the row somebody
    needs to see, because it is the one nobody has decided about."""
    rows = credit.exposures([_balance("50000"), credit.Balance("d", Decimal("1"))],
                            {"d": Decimal("10")})

    assert set(rows) == {"c", "d"}
    assert rows["c"].status == credit.NO_LIMIT
    assert [e.customer_id for e in credit.over_limit(rows.values())] == []


def test_a_limit_that_cannot_mean_money_is_refused_in_one_place():
    """Validated in ``commercial/`` rather than at the call site, so the API and
    any future importer cannot disagree about what is storable."""
    # Quantised to the paisa on the way in, so two screens cannot disagree in
    # the second decimal place.
    assert credit.validate("250000.006") == Decimal("250000.01")
    assert credit.validate(0) == Decimal("0.00")

    with pytest.raises(credit.InvalidLimit):
        credit.validate(-1)
    with pytest.raises(credit.InvalidLimit):
        credit.validate(credit.MAX_LIMIT + 1)
    with pytest.raises(credit.InvalidLimit):
        credit.validate("a lakh or so")


# ── who owns the account ────────────────────────────────────────────────────
def test_an_assignment_wins_over_the_synced_salesperson():
    """Both are kept. Zoho's says what the last invoice implied; the typed one
    says what somebody decided, and only one of those is a decision."""
    assert ownership.effective("usr_a", "usr_b") == ownership.Owner("usr_a",
                                                                   ownership.TYPED)
    assert ownership.effective(None, "usr_b") == ownership.Owner("usr_b",
                                                                 ownership.SYNCED)
    # Unowned stays unowned rather than defaulting to anybody: an account filed
    # under the wrong person is invisible to whoever should be acting on it.
    assert ownership.effective(None, None) is None


# ── the API ─────────────────────────────────────────────────────────────────
def _api(session):
    from app.routers import accounts as accounts_router
    from app.routers import insight as insight_router
    from app.routers import platform_auth
    from app.seed import SEED_PASSWORD, ensure_org_and_users

    ensure_org_and_users(session)
    session.commit()

    app = FastAPI()
    app.include_router(platform_auth.router)
    app.include_router(insight_router.router)
    app.include_router(accounts_router.router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    def token(email: str) -> dict:
        r = client.post("/api/v1/auth/login",
                        json={"email": email, "password": SEED_PASSWORD})
        return {"Authorization": f"Bearer {r.json()['token']}"}

    return client, token


MANAGER = "m.rao@pie.example"
SALESPERSON = "r.nair@pie.example"
SALES_USER = "usr_sales"


def _customer_of(session, external_id: str = "c1") -> str:
    return session.scalars(
        select(models.Customer).where(
            models.Customer.organization_id == ORG,
            models.Customer.external_id == external_id)).one().customer_id


@pytest.fixture()
def book(session):
    _seed(session)
    _fold(session)
    return session


def _account(body: dict, customer_id: str) -> dict:
    return next(r for r in body["accounts"] if r["customer_id"] == customer_id)


def test_a_credit_limit_survives_a_resync(book):
    """The whole reason this is not a column on ``customers``.
    ``upsert_customer`` rewrites the synced fields from the payload on every
    pull, so a limit stored there would last until the next sync and no longer.
    """
    client, token = _api(book)
    customer = _customer_of(book)

    ok = client.put("/api/v1/insight/credit-limits",
                    json={"customer_id": customer, "amount": "500000",
                          "note": "agreed with PIE, Apr 2026"},
                    headers=token(MANAGER))
    assert ok.status_code == 200, ok.text
    assert ok.json()["limit"] == 500000.0

    # A second sync of the same book, which rewrites every customer row.
    _seed(book)

    row = book.query(models.CustomerCreditLimit).one()
    assert Decimal(str(row.amount)) == Decimal("500000")
    assert row.note == "agreed with PIE, Apr 2026"
    assert row.set_by_user_id == "usr_manager"


def test_an_account_owner_survives_a_resync(book):
    """Same claim, for the assignment. ``_sync_assignments`` writes Zoho's
    salesperson onto ``assigned_user_id`` on every pull; an assignment typed
    there would be taken back by the next one, and nothing would record that it
    had."""
    client, token = _api(book)
    customer_id = _customer_of(book)
    # What the sync would have written: Zoho's salesperson on the last invoice.
    # (`test_sync_persistence` covers the mapping itself; what is under test
    # here is that a person's decision outlives it.)
    book.get(models.Customer, customer_id).assigned_user_id = "usr_owner"
    book.commit()

    ok = client.put("/api/v1/insight/account-owners",
                    json={"customer_id": customer_id, "user_id": SALES_USER,
                          "note": "handed over from S. Menon"},
                    headers=token(MANAGER))
    assert ok.status_code == 200, ok.text

    _seed(book)

    customer = book.get(models.Customer, customer_id)
    assert book.query(models.CustomerAccountOwner).one().user_id == SALES_USER
    # And Zoho's own value is untouched, because the gap between the two is the
    # thing worth being able to see.
    assert customer.assigned_user_id == "usr_owner"
    assert ownership.owner_of(book, customer) == ownership.Owner(SALES_USER,
                                                                 ownership.TYPED)


def test_withdrawing_an_assignment_falls_back_to_zohos_salesperson(book):
    """Not "assign it to whoever Zoho currently names": typing today's synced
    answer freezes it against a book that keeps moving."""
    client, token = _api(book)
    customer_id = _customer_of(book)
    book.get(models.Customer, customer_id).assigned_user_id = "usr_owner"
    book.commit()
    client.put("/api/v1/insight/account-owners",
               json={"customer_id": customer_id, "user_id": SALES_USER},
               headers=token(MANAGER))

    gone = client.delete(f"/api/v1/insight/account-owners/{customer_id}",
                         headers=token(MANAGER))

    assert gone.status_code == 200
    assert gone.json()["owner_user_id"] == "usr_owner"
    assert gone.json()["owner_source"] == ownership.SYNCED
    assert book.query(models.CustomerAccountOwner).count() == 0
    assert client.delete(f"/api/v1/insight/account-owners/{customer_id}",
                         headers=token(MANAGER)).status_code == 404


def test_a_handed_over_account_moves_to_the_new_owners_book(book):
    """The failure this guards: four call sites read ``assigned_user_id``
    directly, so an account given to somebody by hand stayed in the previous
    person's book on every screen that forgot to ask ``ownership``."""
    client, token = _api(book)
    customer_id = _customer_of(book)
    book.get(models.Customer, customer_id).assigned_user_id = "usr_owner"
    book.commit()

    before = client.get("/api/v1/accounts", headers=token(SALESPERSON))
    assert [r["customer_id"] for r in before.json()] == []

    client.put("/api/v1/insight/account-owners",
               json={"customer_id": customer_id, "user_id": SALES_USER},
               headers=token(MANAGER))

    after = client.get("/api/v1/accounts", headers=token(SALESPERSON))
    assert [r["customer_id"] for r in after.json()] == [customer_id]
    assert after.json()[0]["owner_source"] == ownership.TYPED
    # And the per-customer route agrees with the list, which is the pairing
    # that used to be checkable only by reading two files.
    assert client.get(f"/api/v1/accounts/{customer_id}/items",
                      headers=token(SALESPERSON)).status_code == 200


def test_an_account_can_only_be_given_to_a_real_active_user(book):
    """An account filed under somebody who cannot sign in is invisible to
    whoever should be acting on it — the failure the sync already refuses."""
    client, token = _api(book)
    customer_id = _customer_of(book)

    assert client.put("/api/v1/insight/account-owners",
                      json={"customer_id": customer_id, "user_id": "usr_ghost"},
                      headers=token(MANAGER)).status_code == 404
    assert client.put("/api/v1/insight/account-owners",
                      json={"customer_id": "nope", "user_id": SALES_USER},
                      headers=token(MANAGER)).status_code == 404


def test_assigning_an_account_is_manager_and_above(book):
    """A salesperson assigning accounts to themselves is not an assignment."""
    client, token = _api(book)
    customer_id = _customer_of(book)

    assert client.put("/api/v1/insight/account-owners",
                      json={"customer_id": customer_id, "user_id": SALES_USER},
                      headers=token(SALESPERSON)).status_code == 403


# ── exposure, end to end ────────────────────────────────────────────────────
def test_the_balance_is_read_from_the_receivables_fold_not_recomputed(book):
    """One outstanding balance in this platform. A second sum over invoices here
    would be a second answer to one question, and wrong the moment a credit note
    is applied — in the direction that gets a customer chased for money they do
    not owe."""
    client, token = _api(book)
    customer_id = _customer_of(book)

    body = client.get("/api/v1/insight/credit", headers=token(MANAGER)).json()
    row = _account(body, customer_id)

    assert (row["outstanding"], row["overdue"]) == (OUTSTANDING, OVERDUE)
    assert row["open_invoices"] == 2 and row["overdue_invoices"] == 1
    # No limit yet, so nothing derived from one is asserted.
    assert row["status"] == credit.NO_LIMIT
    assert row["has_limit"] is False
    assert (row["limit"], row["headroom"], row["over_by"]) == (None, None, None)


def test_an_over_limit_account_is_named_with_the_amount_it_is_over(book):
    client, token = _api(book)
    customer_id = _customer_of(book)
    client.put("/api/v1/insight/credit-limits",
               json={"customer_id": customer_id, "amount": "50000"},
               headers=token(MANAGER))

    body = client.get("/api/v1/insight/credit", headers=token(MANAGER)).json()
    row = _account(body, customer_id)

    assert row["status"] == credit.OVER
    assert row["over_by"] == OUTSTANDING - 50000
    assert row["headroom"] == 50000 - OUTSTANDING
    assert body["over_limit_count"] == 1
    assert body["over_limit_total"] == OUTSTANDING - 50000
    # Who set it, so a limit nobody can source is not one nobody can defend.
    assert row["set_by"] == "M. Rao"


def test_withdrawing_a_limit_is_not_the_same_as_setting_it_to_zero(book):
    """A withdrawn limit means nobody has decided. A zero limit is a standing
    hold on an account somebody meant to keep supplying."""
    client, token = _api(book)
    customer_id = _customer_of(book)
    headers = token(MANAGER)
    client.put("/api/v1/insight/credit-limits",
               json={"customer_id": customer_id, "amount": "0"}, headers=headers)

    zero = _account(client.get("/api/v1/insight/credit", headers=headers).json(),
                    customer_id)
    assert (zero["has_limit"], zero["status"]) == (True, credit.OVER)

    gone = client.delete(f"/api/v1/insight/credit-limits/{customer_id}",
                         headers=headers)
    assert gone.status_code == 200
    assert book.query(models.CustomerCreditLimit).count() == 0

    withdrawn = _account(
        client.get("/api/v1/insight/credit", headers=headers).json(), customer_id)
    assert (withdrawn["has_limit"], withdrawn["status"]) == (False, credit.NO_LIMIT)
    assert client.delete(f"/api/v1/insight/credit-limits/{customer_id}",
                         headers=headers).status_code == 404


def test_a_limit_that_cannot_mean_money_is_refused_by_the_api(book):
    client, token = _api(book)
    customer_id = _customer_of(book)
    headers = token(MANAGER)

    assert client.put("/api/v1/insight/credit-limits",
                      json={"customer_id": customer_id, "amount": "-1"},
                      headers=headers).status_code == 422
    assert client.put("/api/v1/insight/credit-limits",
                      json={"customer_id": "nope", "amount": "1000"},
                      headers=headers).status_code == 404


# ── who may see it ──────────────────────────────────────────────────────────
def test_a_salesperson_reads_the_credit_screen_and_cannot_set_a_limit(book):
    """A limit and a balance are money already billed — not cost, not margin —
    and chasing your own overdue accounts is the salesperson's job. Deciding how
    much credit an account gets is a commercial position, like a payment term.
    """
    client, token = _api(book)
    customer_id = _customer_of(book)
    client.put("/api/v1/insight/account-owners",
               json={"customer_id": customer_id, "user_id": SALES_USER},
               headers=token(MANAGER))
    client.put("/api/v1/insight/credit-limits",
               json={"customer_id": customer_id, "amount": "50000"},
               headers=token(MANAGER))

    read = client.get("/api/v1/insight/credit", headers=token(SALESPERSON))
    assert read.status_code == 200
    assert _account(read.json(), customer_id)["over_by"] == OUTSTANDING - 50000
    # The server answers about this request rather than the browser rebuilding
    # the rule, and it offers no colleague list to somebody who cannot assign.
    assert read.json()["may_set"] is False
    assert read.json()["people"] == []

    assert client.put("/api/v1/insight/credit-limits",
                      json={"customer_id": customer_id, "amount": "999"},
                      headers=token(SALESPERSON)).status_code == 403
    assert client.delete(f"/api/v1/insight/credit-limits/{customer_id}",
                         headers=token(SALESPERSON)).status_code == 403


def test_the_credit_screen_is_one_salespersons_book_and_a_managers_whole_org(session):
    """The per-person collections list, which is what ownership was missing.
    A manager sees the organization and filters it to a person; a salesperson
    sees their own accounts and no further, exactly as the account directory
    already scopes them."""
    _seed(session, contacts=[
        {"contact_id": "c1", "contact_name": "Pitti Engineering", "status": "active"},
        {"contact_id": "c2", "contact_name": "Rane Madras", "status": "active"},
    ])
    _fold(session)
    client, token = _api(session)
    mine, theirs = _customer_of(session, "c1"), _customer_of(session, "c2")
    client.put("/api/v1/insight/account-owners",
               json={"customer_id": mine, "user_id": SALES_USER},
               headers=token(MANAGER))

    manager_sees = client.get("/api/v1/insight/credit", headers=token(MANAGER))
    assert {r["customer_id"] for r in manager_sees.json()["accounts"]} == {mine,
                                                                          theirs}
    assert manager_sees.json()["may_set"] is True

    salesperson_sees = client.get("/api/v1/insight/credit",
                                  headers=token(SALESPERSON))
    assert [r["customer_id"] for r in salesperson_sees.json()["accounts"]] == [mine]
    assert _account(salesperson_sees.json(), mine)["owner_name"] == "R. Nair"


def test_an_unowned_account_belongs_to_no_ones_book_rather_than_everyones(session):
    """An account nobody owns is visible to managers and to no salesperson —
    the same choice the sync makes when it cannot map a salesperson."""
    _seed(session)
    _fold(session)
    client, token = _api(session)

    assert client.get("/api/v1/insight/credit",
                      headers=token(SALESPERSON)).json()["accounts"] == []
    assert client.get("/api/v1/insight/credit",
                      headers=token(SALESPERSON)).json()["empty_reason"]


def test_the_collections_screen_carries_the_limit_beside_the_behaviour(book):
    """"69 days typical" is an observation; "₹51,900 over the limit we gave
    them" is what makes it a decision. Joined onto the payment row rather than
    listed a second time — ``/credit`` is the list."""
    client, token = _api(book)
    customer_id = _customer_of(book)
    client.put("/api/v1/insight/credit-limits",
               json={"customer_id": customer_id, "amount": "50000"},
               headers=token(MANAGER))

    body = client.get("/api/v1/insight/payments",
                      headers=token(SALESPERSON)).json()
    row = next(r for r in body["customers"] if r["customer_id"] == customer_id)

    assert row["credit_status"] == credit.OVER
    assert row["over_by"] == OUTSTANDING - 50000
    assert row["outstanding"] == OUTSTANDING
    assert body["credit_statuses"][credit.OVER]["label"] == "Over the limit"


# ── the same settlements, read per salesperson ──────────────────────────────
def _book_of(body: dict, user_id) -> dict:
    return next(b for b in body["by_owner"] if b["owner_user_id"] == user_id)


def test_the_collection_book_follows_the_typed_assignment_not_the_column(book):
    """The join that had to go through ``ownership``. A handed-over account
    lives in the typed table; reading ``assigned_user_id`` would file its
    settlements under whoever Zoho last had on an invoice while the account
    directory showed the account under the person who now owns it."""
    client, token = _api(book)
    customer_id = _customer_of(book)

    # Before anybody assigns it, this book has no owner the platform can name.
    before = client.get("/api/v1/insight/payments",
                        headers=token(MANAGER)).json()
    assert _book_of(before, None)["label"] == "Unassigned"

    client.put("/api/v1/insight/account-owners",
               json={"customer_id": customer_id, "user_id": SALES_USER},
               headers=token(MANAGER))

    after = client.get("/api/v1/insight/payments",
                       headers=token(MANAGER)).json()
    mine = _book_of(after, SALES_USER)
    assert mine["label"] == "R. Nair"
    assert mine["accounts"] == 1
    # Every settlement moved with the account rather than being counted twice.
    assert [b["owner_user_id"] for b in after["by_owner"]] == [SALES_USER]
    assert (sum(b["settlements"] for b in after["by_owner"])
            == sum(c["settlements"] for c in after["customers"]))


def test_a_salespersons_book_is_their_own_and_a_managers_is_the_org(session):
    """The scope ``/credit`` already applies to anything keyed by who owns an
    account. A salesperson's own figure is computed from their own accounts —
    not from a book they cannot see, and with no "unassigned" bucket standing
    in for everybody else's."""
    _seed(session, contacts=[
        {"contact_id": "c1", "contact_name": "Pitti Engineering", "status": "active"},
        {"contact_id": "c2", "contact_name": "Rane Madras", "status": "active"},
    ])
    _fold(session)
    client, token = _api(session)
    # The account with a settlement against it is c1, and it stays unowned.
    # c2 is the one handed to the salesperson, and nothing has settled on it.
    client.put("/api/v1/insight/account-owners",
               json={"customer_id": _customer_of(session, "c2"),
                     "user_id": SALES_USER},
               headers=token(MANAGER))

    manager = client.get("/api/v1/insight/payments",
                         headers=token(MANAGER)).json()
    assert [b["owner_user_id"] for b in manager["by_owner"]] == [None]

    salesperson = client.get("/api/v1/insight/payments",
                             headers=token(SALESPERSON)).json()
    # Nothing has settled in their book, so there is no book figure — and the
    # unassigned bucket holds an account outside it, which is not theirs to
    # read. A salesperson seeing it would be reading the org's collection
    # behaviour through the one bucket nobody is named on.
    assert salesperson["by_owner"] == []
    # The per-account list is unchanged for either role — it is receivables,
    # and this endpoint has always shown the whole book.
    assert len(salesperson["customers"]) == len(manager["customers"])


def test_the_book_figure_is_days_to_pay_and_never_reaches_for_cost(book):
    """`/payments` is salesperson-visible, so what a row may carry is the rule
    in CLAUDE.md §1: no cost field, no margin field, and no count or flag that
    answers a margin question.

    The fixture book has settled exactly one invoice, which is below the floor
    — so this also pins the refusal end to end: the count is reported and the
    figure is not."""
    client, token = _api(book)
    client.put("/api/v1/insight/account-owners",
               json={"customer_id": _customer_of(book), "user_id": SALES_USER},
               headers=token(MANAGER))

    row = _book_of(client.get("/api/v1/insight/payments",
                              headers=token(SALESPERSON)).json(), SALES_USER)

    assert row["settlements"] == 1
    assert row["estimable"] is False
    assert row["weighted_days_to_pay"] is None and row["late_share"] is None
    assert not [k for k in row
                if any(word in k for word in
                       ("cost", "margin", "profit", "purchase", "floor"))]
    # Named "days to pay" and not "dso": the ratio needs a revenue window this
    # platform does not hold — see ``state/reducers/receivables``.
    assert not [k for k in row if "dso" in k.lower()]
