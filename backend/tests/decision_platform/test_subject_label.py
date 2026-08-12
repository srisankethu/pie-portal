"""What a decision card calls the thing it is about.

`_SUBJECT_MASTERS` maps a subject kind to the master that holds its name, and its
own comment says "a kind with no row falls through to its id rather than silently
borrowing another's name". `CUSTOMER_ITEM` had no row, so the fallback fired for
every commercial-intelligence decision in the product: the card read
`cst_pitti::prd_dnmg` where a name belongs, and carried no company badge either,
because `_subject_origin` looked the subject up through the same table.

The pair id is `customer_id::product_id`. These tests pin both halves — that it
resolves to two names, and that it still degrades to the id rather than to a
blank when a master row is missing.
"""
from __future__ import annotations

import pytest

from app.commercial.subject import encode
from app.domain import models
from app.domain.enums import SubjectEntityType
from app.routers.decisions import _subject_label, _subject_origin

ORG = "org_subject"


@pytest.fixture()
def org(session):
    session.add(models.Organization(organization_id=ORG, name="PIE",
                                    erp="zoho", currency="INR", config={}))
    session.add(models.Customer(customer_id="cst_pitti", organization_id=ORG,
                                external_id="z-1", name="Pitti Engineering Ltd"))
    session.add(models.Product(product_id="prd_dnmg", organization_id=ORG,
                               external_id="z-9", name="DNMG 150608-MP insert"))
    session.flush()
    return ORG


def _decision(subject_entity_type: str, subject_entity_id: str) -> models.Decision:
    return models.Decision(
        organization_id=ORG, decision_type="CI_MARGIN_EROSION",
        decision_key=f"{ORG}:CI_MARGIN_EROSION:{subject_entity_id}",
        subject_entity_type=subject_entity_type,
        subject_entity_id=subject_entity_id,
        assigned_role="SALES_MANAGER", priority_band="MEDIUM",
        priority_score=50, status="OPEN", ai={"status": "PENDING"})


def test_a_customer_item_pair_reads_as_two_names(session, org):
    d = _decision(SubjectEntityType.CUSTOMER_ITEM.value,
                  encode("cst_pitti", "prd_dnmg"))
    label = _subject_label(session, d)
    assert label == "Pitti Engineering Ltd · DNMG 150608-MP insert"
    assert "::" not in label, "the composite id must never reach a screen"


def test_a_pair_carries_the_customers_company(session, org):
    """The badge comes from the customer, not the product: the relationship sits
    in the book that holds the account."""
    d = _decision(SubjectEntityType.CUSTOMER_ITEM.value,
                  encode("cst_pitti", "prd_dnmg"))
    got = _subject_origin(session, d)
    assert got["subject_origin"] is not None, (
        "a pair subject used to resolve to no row at all, so it got no badge")


def test_a_single_subject_is_unchanged(session, org):
    d = _decision(SubjectEntityType.CUSTOMER.value, "cst_pitti")
    assert _subject_label(session, d) == "Pitti Engineering Ltd"


def test_a_pair_whose_product_is_missing_still_names_the_customer(session, org):
    d = _decision(SubjectEntityType.CUSTOMER_ITEM.value,
                  encode("cst_pitti", "prd_gone"))
    assert _subject_label(session, d) == "Pitti Engineering Ltd"


def test_a_pair_that_resolves_to_nothing_falls_back_to_its_id(session, org):
    """Never a blank. A card that cannot name its subject must still say which
    one it is, and the id is at least traceable."""
    raw = encode("cst_gone", "prd_gone")
    d = _decision(SubjectEntityType.CUSTOMER_ITEM.value, raw)
    assert _subject_label(session, d) == raw


def test_an_id_that_is_not_a_pair_does_not_pretend_to_be_one(session, org):
    """`decode` returns None for anything without the separator, and the single
    lookup has to take over rather than the row disappearing."""
    d = _decision(SubjectEntityType.CUSTOMER_ITEM.value, "cst_pitti")
    assert _subject_label(session, d) == "cst_pitti", (
        "a CUSTOMER_ITEM subject holding a bare id is malformed data, and the "
        "honest answer is the id rather than a name guessed from one half")
