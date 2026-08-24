"""Deciding what happened to a write whose answer was lost.

A non-idempotent call that fails without an answer leaves one question: did
the record land? Replaying to find out is how one quote becomes two orders in
a customer's inbox, so the only safe answer is to *read the record back* by a
key the caller chose before sending.

That read has exactly three outcomes and they are not interchangeable:

- the record is there — the write landed. Report it, do not send it again.
- the read found none — nothing landed. Refuse, and say retrying is safe.
- the read itself failed — genuinely unknown. Say so, name what to look up.

The control flow lives here rather than in each adapter because the *third*
case is the one that rots. Zoho's two write paths each grew their own copy and
they drifted: one distinguished a read that succeeded and found nothing from a
read that failed, the other collapsed both into UNKNOWN — so identical evidence
produced opposite verdicts depending on which write you happened to be holding,
and the collapsing one discarded a successful read that had already proved
nothing landed. Sharing the shape makes that disagreement unrepresentable
rather than merely absent today.

What is *not* shared is the wording. Each caller writes its own two sentences,
because "look for item CNMG120408 before adding it again" and "look for
reference Q-1042 before sending this quote again" are different instructions to
a different person, and one generic sentence covering both would help neither.
"""
from __future__ import annotations

from typing import Callable, Optional, TypeVar

from .errors import SourceWriteRefused, SourceWriteUnknown

Landed = TypeVar("Landed")
Result = TypeVar("Result")


def settle_by_read(read: Callable[[], Optional[Landed]],
                   present: Callable[[Landed], Result],
                   *,
                   unknown_message: Callable[[Exception], str],
                   refused_message: str,
                   reference: Optional[str] = None,
                   codes: Optional[list] = None,
                   unknown_error: type = SourceWriteUnknown,
                   refused_error: type = SourceWriteRefused) -> Result:
    """Settle an uncertain write by reading the record back.

    ``read`` returns the record if the source holds it and ``None`` if it does
    not — it must not swallow its own failure into ``None``, because "not
    there" and "could not look" are the two answers this exists to keep apart.
    ``present`` turns a found record into whatever the caller returns.

    ``reference`` and ``codes`` ride along on the raised outcome so a screen
    can point at the document or the lines responsible.

    This function owns which *category* each branch lands in; a caller may
    choose the concrete class within that category through ``unknown_error``
    and ``refused_error``, which is how an adapter whose own subclasses are
    already caught by name keeps raising them. The defaults are the neutral
    pair, so a new connector needs neither argument.
    """
    try:
        landed = read()
    except Exception as e:                           # noqa: BLE001
        # Deliberately every exception, not the source's own error type. The
        # fault that lost the write is usually a dropped connection, and it has
        # not healed by the time this read goes out — so a transport-level
        # fault here is the likely path, not the exotic one. Letting it escape
        # raw would strand the caller with no outcome at all, which is the
        # failure this whole function exists to prevent.
        raise unknown_error(unknown_message(e), reference=reference) from e
    if landed is not None:
        return present(landed)
    # The read succeeded and found nothing. That is evidence, not the absence
    # of it: reporting UNKNOWN here would discard what the read established and
    # send someone hunting the source by hand for a record it just disproved.
    raise refused_error(refused_message, codes=codes)
