"""Named sets of customers, vendors and items, drawn by somebody in the business.

The platform could answer a question about one account and a question about the
whole book, and nothing in between — while every question a distributor actually
asks lives in that gap: the PSU accounts, the aerospace book, one principal's
items, the vendors on ninety-day terms.

A group is a **label with a scope**, not an entity and not a permission. Three
consequences, and they are the whole design:

- **It is not derived.** A full re-sync rebuilds every customer, vendor and
  product row from the source system, so a grouping somebody typed cannot be a
  column on one of them — it is the only copy. Same argument
  ``ItemCategoryOverride`` makes, one level up.
- **It carries a version, and the roster is inside it.** Editing a group moves
  every number computed under it, so the definition hashes to a ``gr_`` stamp
  that travels with the answer. ``service.definition_of`` says exactly what is
  in that hash and what is deliberately left out.
- **It narrows, never widens.** ``service.narrow`` intersects a group with what
  the principal may already see. A group that could widen visibility would be a
  permission decision taken by a labelling feature, and naming one would read
  another territory's accounts.

Only ROSTER membership exists today — a list somebody typed. ``RULE`` (a
predicate over facts the platform already resolves) and ``INHERITED`` (the
ERP's own grouping) are named in the model because the three fail differently
and a reader has to be able to tell which one a number rests on.

**No rule will ever key on a restricted fact.** A rule is a predicate, and a
predicate a caller can walk is the number it tests against (§1) — a group
defined as "margin below 10%" hands its boundary to anyone who can see who is
in it, and a handful of such groups is the bisection oracle MFLOOR was. The
groupable-field registry that lands with rule membership is an allowlist for
that reason, and ``EntityGroup.visibility`` / ``boundary_refs`` are the
withholding path for the day somebody wants one anyway.
"""
from .service import (  # noqa: F401
    KIND_LABELS,
    KINDS,
    OPERATIONAL,
    RESTRICTED,
    VISIBILITIES,
    GroupError,
    ResolvedGroup,
    add_members,
    create,
    find,
    groups_of,
    list_groups,
    member_ids,
    narrow,
    remove_members,
    rename,
    resolve,
    restamp,
    set_archived,
    set_visibility,
    slugify,
    valid_slug,
)
