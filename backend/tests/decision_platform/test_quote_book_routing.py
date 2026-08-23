"""Which set of books a quote belongs to — and when that question is refused.

The organization behind this platform trades through three legal entities under
one Zoho login. "Create the estimate" is therefore never a complete instruction
on its own: an estimate written into 4U Precision for a customer who buys from
SLS Engineers is a real document, in the wrong company's ledger, discovered at
the quarter's accounts.

The answer comes from the identity triple ``domain/origin.py`` defines and
``Customer`` stores — connector, connected company, that system's own id — so
this is a lookup rather than a name match. What these tests hold is the other
half: that anything short of one clear answer is refused rather than chosen.
"""
from __future__ import annotations

import pytest

from app.domain import models
from app.ingestion.connections import (
    ZOHO_CONNECTOR,
    ConnectionNotFound,
    book_for_customer,
    connect_erp,
    set_zoho_credentials,
)


def _org(session, org_id="org_a"):
    session.add(models.Organization(organization_id=org_id, name=org_id))
    session.flush()
    return org_id


def _connect(session, org, zoho_org_id, label):
    return set_zoho_credentials(
        session, org, zoho_organization_id=zoho_org_id, client_id="cid",
        client_secret="sec", refresh_token="rtok", label=label)


def _customer(session, org, *, connection_id, external_id="3300000009",
              name="Pitti Engineering Ltd", connector=ZOHO_CONNECTOR):
    row = models.Customer(organization_id=org, connector=connector,
                          connection_id=connection_id, external_id=external_id,
                          name=name)
    session.add(row)
    session.flush()
    return row


def test_the_customers_own_connection_decides_the_book(session):
    org = _org(session)
    sls = _connect(session, org, "60036630487", "SLS Engineers")
    _connect(session, org, "60036630626", "4U Precision")
    customer = _customer(session, org, connection_id=sls.connection_id)

    book = book_for_customer(session, org, customer)
    assert book.connection.connection_id == sls.connection_id
    assert book.contact_id == "3300000009"


def test_the_contact_id_is_that_books_own_id(session):
    """Two connected companies keep their own id spaces and they collide
    freely — Tally numbers ledgers from 1 in every company. The id written onto
    the estimate has to be the one the target book issued."""
    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    ups = _connect(session, org, "60036630911", "UPS")
    customer = _customer(session, org, connection_id=ups.connection_id,
                         external_id="999")

    book = book_for_customer(session, org, customer)
    assert book.connection.connection_id == ups.connection_id
    assert book.contact_id == "999"


def test_a_disabled_connection_is_refused_not_substituted(session):
    org = _org(session)
    sls = _connect(session, org, "60036630487", "SLS Engineers")
    _connect(session, org, "60036630626", "4U Precision")
    customer = _customer(session, org, connection_id=sls.connection_id)
    sls.enabled = False
    session.flush()

    with pytest.raises(ConnectionNotFound):
        book_for_customer(session, org, customer)


def test_a_customer_with_no_contact_id_is_refused_for_that_not_for_ambiguity(
        session):
    """A missing contact id blocks every book equally, so it is not a question
    about which book.

    With two connected Zoho companies this used to fall through to the
    which-one sentence, sending whoever read it to compare two ledgers when the
    blocker was that this customer has no contact in either. The remedy is the
    same re-sync either way, which is precisely why the wrong reason survived:
    following it happened to work, so nobody learned the message was wrong.
    """
    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    _connect(session, org, "60036630488", "4U Precision")
    customer = _customer(session, org, connection_id=None, connector=None,
                         external_id="")

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    msg = str(e.value)
    assert "no contact id" in msg
    assert "Which one" not in msg, (
        "the refusal blamed a choice between books for a blocker that applies "
        "to all of them")


def test_a_customer_from_a_connector_that_cannot_write_is_refused(session):
    """A Tally customer has no contact to write a quote against, and the id it
    carries belongs to a different system entirely.

    The refusal names the system and says what is actually true of it — that
    this platform reads it and cannot create a quote in it. It used to say "not
    Zoho Books", which is a fact about the wrong end: an owner reading it
    learns which system we happen to write today rather than what is missing.
    """
    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    customer = _customer(session, org, connection_id=None, connector="tally",
                         external_id="17")

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    assert "tally" in str(e.value)
    assert "cannot create a quote" in str(e.value)


def test_the_refusal_asks_what_a_connector_can_write_not_what_it_is_called(
        session, monkeypatch):
    """The point of the whole seam, and the only test that can prove it.

    Every connector but Zoho declares ``writes=()`` today, so a refusal keyed on
    capability and a refusal keyed on ``connector != "zoho"`` are indistinguishable
    on the real registry — both refuse everything. Registering a connector that
    *does* declare the write separates them: this one must not be refused for
    being unable to write, because it can.

    Without this the seam could be quietly name-based and no test would notice
    until the first real connector write, which is the point at which finding
    out is most expensive.
    """
    from app.ingestion import connections as conn
    from app.ingestion.erp import base as erp_base

    spec = erp_base.ConnectorSpec(
        key="writeable", label="Writeable Test ERP", company_term="company",
        credential_fields=(), connection_fields=(), external_id_field="company_id",
        setup_note="A test connector that declares it can create a quote.",
        build_source=lambda *a, **k: None,
        permissions=(erp_base.Permission("quotes.create", "Creating quotes",
                                         writes=("sales_quotes",)),))
    monkeypatch.setitem(erp_base._REGISTRY, "writeable", spec)

    assert conn.can_write_quotes("writeable") is True
    assert conn.can_write_quotes("tally") is False, "unknown connectors write nothing"

    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    customer = _customer(session, org, connection_id=None, connector="writeable",
                         external_id="17")

    # Still refused — nothing here knows how to write into it yet — but for
    # that reason and not the capability one. Both refusals matter: the wrong
    # one misinforms, and no refusal at all would resolve this customer into
    # whichever book happened to be connected and put its quote on another
    # system's ledger.
    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    assert "cannot create a quote" not in str(e.value), (
        "a connector that declares the write was refused for being unable to "
        "write — the dispatch is reading the connector's name, not its capability")
    assert "no writer for it yet" in str(e.value)


def test_an_unattributed_customer_resolves_when_there_is_one_book(session):
    """Not a guess: with a single connected company there is nothing to choose
    between, and refusing here would break every single-entity deployment."""
    org = _org(session)
    only = _connect(session, org, "60036630487", "SLS Engineers")
    customer = _customer(session, org, connection_id=None)

    book = book_for_customer(session, org, customer)
    assert book.connection.connection_id == only.connection_id
    assert book.contact_id == "3300000009"


def test_an_unattributed_customer_is_refused_once_a_second_book_exists(session):
    """``connection_id`` is nullable because provenance genuinely was not
    recorded, and the model says so. Choosing one would invent it."""
    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    customer = _customer(session, org, connection_id=None)
    assert book_for_customer(session, org, customer) is not None

    _connect(session, org, "60036630626", "4U Precision")
    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    assert "re-sync" in str(e.value)


def test_no_connected_company_is_refused_with_that_reason(session):
    org = _org(session)
    customer = _customer(session, org, connection_id=None)

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    assert "no connected Zoho company" in str(e.value)


def test_another_organizations_connection_does_not_answer(session):
    """The connection is looked up among *this* organization's enabled ones, so
    a customer row pointing at another tenant's connection resolves to nothing
    rather than to that tenant's books."""
    org_a = _org(session, "org_a")
    org_b = _org(session, "org_b")
    _connect(session, org_a, "60036630487", "SLS Engineers")
    _connect(session, org_a, "60036630626", "4U Precision")
    b_conn = _connect(session, org_b, "70000000001", "Someone else")
    customer = _customer(session, org_a, connection_id=b_conn.connection_id)

    with pytest.raises(ConnectionNotFound):
        book_for_customer(session, org_a, customer)


def _connect_netsuite(session, org, label="US Books", company_id="1234567"):
    """A connected company in another system, through the generic ERP path so
    the row is shaped exactly as a real one is."""
    return connect_erp(session, org, connector="netsuite", label=label, values={
        "consumer_key": "ck", "consumer_secret": "cs", "token_id": "ti",
        "token_secret": "ts", "company_id": company_id})


def test_a_non_zoho_company_never_stands_in_for_a_missing_zoho_one(session):
    """The only connected book reads NetSuite, so there is no Zoho ledger here
    at all. Counting it as "the one connected company" placed the quote in it,
    and the failure surfaced one call later out of ``credentials_for`` as a
    bare ValueError — which ``books_for_quote`` does not catch, so an HTTP 500
    stood where a refusal belongs."""
    org = _org(session)
    _connect_netsuite(session, org)
    customer = _customer(session, org, connection_id=None, connector=None)

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    assert "netsuite" in str(e.value)


def test_the_customers_own_non_zoho_connection_is_refused_by_name(session):
    """``connector`` and ``connection_id`` are nullable independently, so a row
    can carry the company without carrying the system. It still resolves to a
    book whose credentials are not Zoho's, and the reason given has to be that
    rather than "disabled" — a refusal naming the wrong cause sends whoever
    reads it to the connections screen to re-enable something already on."""
    org = _org(session)
    ns = _connect_netsuite(session, org)
    customer = _customer(session, org, connection_id=ns.connection_id,
                         connector=None)

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    assert "netsuite" in str(e.value)


def test_one_zoho_book_beside_another_system_is_refused_on_provenance(session):
    """One Zoho book beside one NetSuite book is still two companies an
    unattributed customer could have come from, so it is still refused — but
    the reason is not the one the multi-Zoho case gives. There is exactly one
    Zoho company here, so "which one" is not a question anyone can answer, and
    a refusal that poses it sends the reader looking for a second set of Zoho
    books that does not exist. The real reason is that this customer's
    provenance was never recorded and may be the NetSuite book, so writing the
    Zoho estimate would invent it."""
    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    _connect_netsuite(session, org)
    customer = _customer(session, org, connection_id=None, connector=None)

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    msg = str(e.value)
    # Not a count of Zoho companies, and not a choice between them.
    assert "connected Zoho companies" not in msg
    assert "Which one" not in msg
    # The reason actually given: unrecorded provenance that may be the other
    # system, which is why the estimate cannot be written.
    assert "netsuite" in msg
    assert "invent the provenance" in msg
    assert "re-sync" in msg.lower()


def test_several_zoho_books_still_ask_which_one(session):
    """The other half of the pair above: with two Zoho companies the question
    genuinely is which of them, and adding a NetSuite book must not turn that
    sentence into the provenance one — the count it names is still true."""
    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    _connect(session, org, "60036630626", "4U Precision")
    _connect_netsuite(session, org)
    customer = _customer(session, org, connection_id=None, connector=None)

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    msg = str(e.value)
    assert "2 connected Zoho companies" in msg
    assert "Which one" in msg
    assert "netsuite" in msg
