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


def test_a_customer_from_another_connector_is_refused(session):
    """A Tally customer has no Zoho contact to write an estimate against, and
    the id it carries belongs to a different system entirely."""
    org = _org(session)
    _connect(session, org, "60036630487", "SLS Engineers")
    customer = _customer(session, org, connection_id=None, connector="tally",
                         external_id="17")

    with pytest.raises(ConnectionNotFound) as e:
        book_for_customer(session, org, customer)
    assert "tally" in str(e.value)


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
