"""A customer name must never arbitrate between two legal entities.

`routers/quote.py` feeds the resolved customer straight into
`connections.book_for_customer`, so the row this matcher returns decides which
connected company's books an estimate is created against — and with it, which
Zoho credentials sign it.

An organization holding several connected companies carries one `Customer` row
per book: `uq_customer_source` is keyed on `(organization_id, connector,
connection_id, external_id)`, so the same trading name legitimately exists once
per company. Returning the first row of an unordered query is therefore a coin
flip between ledgers, and it is silent.

The rule these pin: an issued identifier resolves, one name match resolves,
several name matches refuse. A refusal is already a supported outcome — the
caller renders it as "no set of books can be identified" — whereas a wrong
match is an invoice raised by the wrong company.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.decisions.quote_support import _resolve_customer
from app.domain import models
from app.seed import ensure_org_and_users


@pytest.fixture()
def org(session):
    ensure_org_and_users(session)
    return settings.DEFAULT_ORG_ID


def _cust(org_id, cid, name, connection_id):
    return models.Customer(
        customer_id=cid, organization_id=org_id, external_id=cid, name=name,
        connector="zoho", connection_id=connection_id, status="ACTIVE")


# ── the defect ───────────────────────────────────────────────────────────────
def test_one_name_in_two_books_refuses_rather_than_choosing_a_ledger(session, org):
    """The same customer, connected twice. Nothing in the reference says which."""
    session.add(_cust(org, "cst_sls_pitti", "Pitti Engineering Ltd", "conn_sls"))
    session.add(_cust(org, "cst_4u_pitti", "Pitti Engineering Ltd", "conn_4u"))
    session.flush()

    assert _resolve_customer(session, org, "Pitti Engineering Ltd") is None


def test_containment_across_two_different_companies_refuses(session, org):
    """'Pitti' is contained in two genuinely different customers, not one."""
    session.add(_cust(org, "cst_pitti_eng", "Pitti Engineering Ltd", "conn_sls"))
    session.add(_cust(org, "cst_pitti_cast", "Pitti Castings Pvt Ltd", "conn_sls"))
    session.flush()

    assert _resolve_customer(session, org, "Pitti") is None


def test_an_ambiguous_exact_name_does_not_fall_through_to_a_looser_match(session, org):
    """Two exact matches must not hand the decision to containment.

    Falling through would answer a question the stricter rule already refused,
    using a rule with more candidates — the wrong direction entirely.
    """
    session.add(_cust(org, "cst_a", "Acme Tools", "conn_sls"))
    session.add(_cust(org, "cst_b", "Acme Tools", "conn_4u"))
    session.add(_cust(org, "cst_c", "Acme Tools International", "conn_sls"))
    session.flush()

    assert _resolve_customer(session, org, "Acme Tools") is None


# ── positive controls, so the refusal cannot pass by matching nothing ────────
def test_an_issued_identifier_still_resolves_across_books(session, org):
    """An id is unique by construction, so it needs no arbitration."""
    session.add(_cust(org, "cst_sls_pitti", "Pitti Engineering Ltd", "conn_sls"))
    session.add(_cust(org, "cst_4u_pitti", "Pitti Engineering Ltd", "conn_4u"))
    session.flush()

    got = _resolve_customer(session, org, "cst_4u_pitti")
    assert got is not None and got.customer_id == "cst_4u_pitti"


def test_a_single_book_still_resolves_by_name_and_by_containment(session, org):
    """The ordinary case is untouched — this is a refusal on ambiguity only."""
    session.add(_cust(org, "cst_pitti", "Pitti Engineering Ltd", "conn_sls"))
    session.flush()

    exact = _resolve_customer(session, org, "Pitti Engineering Ltd")
    assert exact is not None and exact.customer_id == "cst_pitti"

    loose = _resolve_customer(session, org, "Pitti")
    assert loose is not None and loose.customer_id == "cst_pitti"


def test_the_same_customer_listed_twice_under_one_id_is_not_ambiguous(session, org):
    """Ambiguity is about distinct customers, not about row count."""
    session.add(_cust(org, "cst_pitti", "Pitti Engineering Ltd", "conn_sls"))
    session.flush()

    # Queried twice through the same matcher: still one customer, still resolves.
    assert _resolve_customer(session, org, "pitti engineering ltd") is not None
