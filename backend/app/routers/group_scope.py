"""Reading ``?group=`` off a request, once, for every screen that offers it.

A dependency rather than an ``if group:`` block per endpoint, and the reason is
the one CLAUDE.md §5 gives for a registry over a growing chain: the interesting
part of group scoping is not the filter, it is the three rules around it, and
three rules re-stated at each of a dozen call sites is three rules that will
disagree at one of them.

The rules, in one place:

**An unknown slug is a 404, and so is a group this reader may not see.** Not a
403 — a 403 confirms the group exists, and for a RESTRICTED group its existence
is part of what is withheld. ``groups.find`` returns ``None`` for both cases so
this module cannot tell them apart even by accident.

**An empty group is an empty answer, never an absent filter.** A group nobody
has been added to resolves to zero ids, and every caller passes those zero ids
through to its computation, which produces an empty screen. Substituting "no
filter" would answer a question about a set nobody is in with the whole book's
figures — the benign default §1 names — so ``ref`` and ``empty_note`` below
exist to make the empty case *say* what it is rather than merely look like a
quiet day.

**The group narrows and never widens.** The intersection lives in
``groups.narrow``; this module only resolves. An endpoint that took the group's
ids as its own scope rather than intersecting them with the principal's would
hand a salesperson another territory's accounts, and the shape of that mistake
is that it looks like it is doing the filtering correctly.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import groups
from ..authz import Principal, current_principal
from ..db import get_session
from ..domain.enums import SubjectEntityType


def for_kind(entity_kind: str, *, alias: str = "group") -> Callable:
    """A ``?<alias>=`` dependency resolving to a group of ``entity_kind``.

    **The alias is explicit because FastAPI names a query parameter after the
    function argument, and these are all one function.** Two of these on one
    endpoint would otherwise both bind to ``?group=`` and both read the same
    value — not an error, just an endpoint that resolves one slug as two kinds
    of group and keeps whichever it happened to find. ``/composition`` takes a
    customer group and an item group at once, which is what turned that from a
    hypothetical into a bug to avoid.
    """
    label = groups.KIND_LABELS[entity_kind].lower()

    def resolve_group(
        slug: Optional[str] = Query(
            None, alias=alias, max_length=64,
            description=f"Narrow this answer to a group of {label}, by slug."),
        principal: Principal = Depends(current_principal),
        session: Session = Depends(get_session),
    ) -> Optional[groups.ResolvedGroup]:
        if not slug:
            return None
        resolved = groups.resolve(
            session, principal.organization_id, entity_kind, slug,
            include_restricted=principal.is_manager_or_owner)
        if resolved is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                f"No such group of {label}: {slug}")
        return resolved

    return resolve_group


#: The three a screen about one kind of thing reaches for, all on ``?group=``.
#: Named for what a screen is about rather than for the enum member, because the
#: reader of an endpoint signature is asking "which group does this screen take"
#: and ``item_group`` answers it where ``product_group`` would send them to check.
customer_group = for_kind(SubjectEntityType.CUSTOMER.value)
vendor_group = for_kind(SubjectEntityType.VENDOR.value)
item_group = for_kind(SubjectEntityType.PRODUCT.value)

#: For a screen that crosses two kinds at once. ``?items=`` beside ``?group=``.
item_group_as_items = for_kind(SubjectEntityType.PRODUCT.value, alias="items")


def ref(group: Optional[groups.ResolvedGroup]) -> Optional[dict]:
    """The group as a response carries it, beside ``thresholds_version``.

    ``None`` when no group was asked for, so a screen can tell "the whole book"
    from "a group that happens to hold everything" — which are the same numbers
    and different claims.
    """
    return group.to_ref() if group is not None else None


def empty_note(group: Optional[groups.ResolvedGroup],
               what: str) -> Optional[str]:
    """Why a group-scoped screen has nothing on it, when the group is why.

    ``None`` when no group is in play, which leaves the screen's own
    ``empty_reason`` to stand. Otherwise one of two answers, and they are
    genuinely different: an **empty group** is somebody's unfinished setup, and
    a **populated group with no rows** is a real fact about the business. A
    screen that answered the first with "no sales history has been synced yet"
    would send somebody to re-run a sync that has already worked, and one that
    answered the second with "add members to the group" would send them to fix
    something that is not broken.

    Both branches live here rather than at the call sites so a caller cannot
    reach for the wrong one — the distinction is the sort that gets collapsed
    the third time somebody copies the pattern. ``what`` names the evidence the
    screen wanted, in the words that screen would use: "purchase order",
    "sales history".
    """
    if group is None:
        return None
    if group.size == 0:
        return (f"Nothing is in “{group.name}” yet, so there is nothing to "
                f"compute. Add members to the group in Setup.")
    return f"No {what} on record for anything in “{group.name}”."
