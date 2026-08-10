"""Who owns an account, when two sources both have an opinion.

There are two of them and there always will be:

* **Synced.** ``Customer.assigned_user_id``, written by ``_sync_assignments``
  from the salesperson on the account's most recent invoice, and only where
  that person's Zoho email matches a platform user exactly. Derived, rewritten
  by the next pull, and silent about every account Zoho has no salesperson for.
* **Typed.** ``CustomerAccountOwner``, which is somebody deciding. It survives
  a complete re-sync, because it is the only copy of a decision nobody else
  holds.

``effective`` is the one place that says which wins, and it is the only place —
the same discipline as ``insight/terms.effective_days``. Four call sites ask
this question (the account directory, one customer's screen, decision routing
and quote routing), and four copies of "typed if there is one, else Zoho's" is
four places for it to drift. The failure that would cause is quiet: a manager
hands an account over, the typed row says Rahul, and one screen still shows it
in somebody else's book because it read the column directly.

**Typed wins, and the synced value is never overwritten.** The alternative —
writing the decision onto ``assigned_user_id`` — is what the whole
typed-not-synced pattern exists to prevent (see ``CustomerAccountOwner``): the
next sync would put Zoho's answer back, and nothing would record that it had.
Keeping both means a screen can say *why* an account is in this person's book,
which is the difference between an assignment somebody can query and one they
have to take on faith.

Session-passing rather than pure, like ``commercial/quote_service``: this reads
the one table that holds the answer. The rule itself is ``effective``, which
takes two ids and knows nothing about a database.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain import models

#: Somebody assigned this account. Survives a re-sync.
TYPED = "TYPED"
#: Zoho's salesperson on the most recent invoice, mapped by email. Derived.
SYNCED = "SYNCED"

#: What each source means, for a screen that has to explain why an account sits
#: in somebody's book.
SOURCE_LABELS: dict[str, str] = {
    TYPED: "assigned here",
    SYNCED: "from the salesperson on their latest invoice in Zoho",
}


@dataclass(frozen=True)
class Owner:
    """The person whose book this account is in, and where that came from."""

    user_id: str
    #: ``TYPED`` or ``SYNCED``.
    source: str


def effective(typed: Optional[str], synced: Optional[str]) -> Optional[Owner]:
    """The owner to act on: the assignment if there is one, else Zoho's.

    ``None`` means genuinely unowned — no assignment and no salesperson the
    sync could map. That is a real state and a common one, and it is left as
    ``None`` rather than defaulted to anybody: an account wrongly filed under a
    person is invisible to whoever should be acting on it, whereas an unowned
    one is visible to every manager. The sync makes the same choice for the
    same reason.
    """
    if typed:
        return Owner(user_id=typed, source=TYPED)
    if synced:
        return Owner(user_id=synced, source=SYNCED)
    return None


def _typed(session: Session, org: str,
           customer_ids: Optional[list[str]] = None) -> dict[str, str]:
    stmt = select(models.CustomerAccountOwner).where(
        models.CustomerAccountOwner.organization_id == org)
    if customer_ids is not None:
        stmt = stmt.where(models.CustomerAccountOwner.customer_id.in_(customer_ids))
    return {row.customer_id: row.user_id for row in session.scalars(stmt).all()}


def owners(session: Session, org: str,
           customers: Iterable[models.Customer]) -> dict[str, Owner]:
    """Every one of these accounts' owners, keyed by customer id.

    One query for the typed rows rather than one per customer: the account
    directory is the screen most likely to hold four hundred rows, and an N+1
    here is four hundred round trips before anybody has clicked anything —
    which is the shape ``accounts._trade_summary`` already avoids.

    Unowned accounts are **absent** from the result rather than present with a
    null owner, so a caller filtering somebody's book writes ``.get(...)`` and
    cannot accidentally match on nobody.
    """
    rows = list(customers)
    typed = _typed(session, org, [c.customer_id for c in rows])
    out: dict[str, Owner] = {}
    for customer in rows:
        owner = effective(typed.get(customer.customer_id),
                          customer.assigned_user_id)
        if owner is not None:
            out[customer.customer_id] = owner
    return out


def owner_of(session: Session, customer: models.Customer) -> Optional[Owner]:
    """One account's owner. The single-row form of ``owners``."""
    typed = session.scalar(
        select(models.CustomerAccountOwner.user_id).where(
            models.CustomerAccountOwner.organization_id == customer.organization_id,
            models.CustomerAccountOwner.customer_id == customer.customer_id))
    return effective(typed, customer.assigned_user_id)


def owned_by(session: Session, customer: models.Customer, user_id: str) -> bool:
    """Is this account in that person's book?

    The question every salesperson-scoped endpoint asks. Written once here
    because the four call sites that ask it used to read ``assigned_user_id``
    directly, and a typed assignment would have been invisible to all of them.
    """
    owner = owner_of(session, customer)
    return owner is not None and owner.user_id == user_id
