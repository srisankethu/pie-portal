"""The one read behind every settlement figure in the platform.

``payments.py`` next door owns the *shape* — ``Settlement`` — and every piece of
arithmetic over it: the lag, the percentiles, the evidence floor. It is pure and
holds no ``Session``, which is why it can be exercised without a database.

This module owns the other half: turning ``PaymentApplication`` rows into that
shape. It was a private helper in ``routers/insight.py`` until a second caller
appeared — ``quote_diagnosis`` asks how long one customer's money is out on the
line a desk is pricing — and a router-private loader has only two ways to grow a
second caller, both of them wrong. Copying it gives two reads that agree until
somebody changes one; importing it from the router would make a deterministic
package depend on the HTTP layer, which is the dependency §3 runs the other way.

So it moved down rather than sideways. ``routers/insight`` calls it, the quote
diagnosis calls it, and there is one answer to "which rows are this book's
settled invoices".

**Unbounded by date, deliberately**, exactly as it was in the router: the window
belongs to whoever is reasoning, not to the read. ``financing`` bounds by its own
twelve months and ``working_capital`` by the diagnosis engine's lookback, and a
query that pre-filtered would silently impose a third.

Receivables, not economics. A settlement is an invoice, its due date and the day
the money arrived — no cost and no margin — which is why the payments screen
behind it is visible to every role.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain import models
from . import payments


def load(session: Session, org: str,
         customer_id: Optional[str] = None) -> list[payments.Settlement]:
    """Payment applications as the grain the payment view computes on.

    ``customer_id`` narrows to one account. That is not only a convenience:
    ``payments.lag`` reads the party from its first row, so a caller wanting one
    account's habit must ask for one account's rows — ``working_capital.assess``
    refuses a mixed list in as many words rather than mislabelling one customer's
    behaviour with another's name.
    """
    stmt = select(models.PaymentApplication).where(
        models.PaymentApplication.organization_id == org)
    if customer_id:
        stmt = stmt.where(models.PaymentApplication.customer_id == customer_id)
    return [
        payments.Settlement(
            party_id=row.customer_id,
            document_ref=row.invoice_external_ref,
            document_number=row.invoice_number,
            document_date=row.invoice_date,
            due_date=row.invoice_due_date,
            paid_on=row.paid_on,
            amount=float(row.amount_applied or 0),
        )
        for row in session.scalars(stmt).all()
    ]
