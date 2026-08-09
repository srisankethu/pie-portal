"""Every tenant-scoped table is a deliberate decision, and this is what checks it.

``erasure.EXPORTED`` is written out by hand rather than derived from the
metadata, and the comment above it says why: a new table must be a decision to
include or to leave out, because "everything with an ``organization_id``" would
sweep in credential ciphertext the moment somebody adds a column.

That reasoning is right and it had no teeth. The list was written when the
platform held customers, products and the analysis over them; the whole supply
and payables layer — vendors, bills, invoices, both sides of payments, orders,
stock — arrived afterwards and *none of it was decided about*. The export
promised "everything this organization owns" and omitted about half of it, and
because ``_manifest`` reads the same list, the signed erasure receipt attested
to an inventory that was equally short.

So the decision stays manual and the *coverage* becomes automatic. A model with
an ``organization_id`` must appear in one of the two lists. Which one is a
judgement nobody can make for you; that you made it is checkable.

Compared on ``__tablename__`` rather than on the label ``EXPORTED`` happens to
use, because those two are equal today by convention and nothing enforces it —
a renamed label would otherwise make this pass while the export lost a table.
"""
from __future__ import annotations

from app.db import Base
from app.domain import models  # noqa: F401  (populate the metadata)
from app.trust import erasure


def _org_scoped_tables() -> set[str]:
    """Every mapped table carrying an ``organization_id``.

    Read from the metadata rather than from a hand-kept list, which is the
    entire point: the thing being checked is that a human list keeps up with
    the schema, so the other side of the comparison must not be a human list.
    """
    return {
        table.name for table in Base.metadata.tables.values()
        if "organization_id" in table.columns
    }


def test_every_org_scoped_model_is_exported_or_explicitly_excluded():
    exported = {model.__tablename__ for _label, model in erasure.EXPORTED}
    decided = exported | set(erasure.EXCLUDED)

    undecided = sorted(_org_scoped_tables() - decided)

    assert not undecided, (
        "These tenant-scoped tables are in neither erasure.EXPORTED nor "
        "erasure.EXCLUDED, so nobody has decided whether a customer takes them "
        "when they leave: " + ", ".join(undecided) + ". Add each one to the "
        "list it belongs in — EXCLUDED entries carry their reason in "
        "``excluded_note``.")


def test_the_manifest_covers_every_org_scoped_table():
    """The receipt says what was destroyed, so it counts more than the export.

    An excluded table is still the tenant's data and still becomes unreadable
    (or does not) when the key goes. A manifest built from the *export* list
    would silently under-report exactly the tables somebody decided not to
    hand over.
    """
    manifested = {model.__tablename__ for _label, model in erasure.MANIFESTED}
    missing = sorted(_org_scoped_tables() - manifested)

    assert not missing, (
        "These tenant-scoped tables are absent from erasure.MANIFESTED, so the "
        "signed erasure receipt does not say they existed: "
        + ", ".join(missing))


def test_exported_is_a_subset_of_manifested():
    """Nothing may travel that the receipt does not also account for."""
    exported = {model.__tablename__ for _label, model in erasure.EXPORTED}
    manifested = {model.__tablename__ for _label, model in erasure.MANIFESTED}

    assert exported <= manifested, sorted(exported - manifested)


def test_nothing_is_both_exported_and_excluded():
    exported = {model.__tablename__ for _label, model in erasure.EXPORTED}
    assert not exported & set(erasure.EXCLUDED)


def test_credentials_and_staff_accounts_stay_out_of_the_export():
    """The one thing the hand-written list exists to prevent.

    Named explicitly rather than left to the coverage check above, because
    coverage only asserts that a decision was *made* — this asserts which
    decision these three are allowed to have.
    """
    exported = {model.__tablename__ for _label, model in erasure.EXPORTED}
    for secret in ("zoho_credentials", "zoho_connections", "users"):
        assert secret not in exported
        assert secret in erasure.EXCLUDED
