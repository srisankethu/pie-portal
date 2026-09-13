"""Group definitions, their members, and the version that makes both citable.

Reads and writes ``EntityGroup`` / ``EntityGroupMember`` and does nothing else.
In particular it does not decide what a group *means* for any screen: a caller
asks for the members and narrows its own computation with them, which is why
this module imports ``domain/`` and the registry and no other package.

**That isolation is deliberate and worth keeping.** Rule membership, when it
lands, will need facts that live in ``commercial/`` — an item's resolved line of
business, a vendor's principal — and importing them here would set up a cycle
the first time ``commercial/`` wants to scope a computation by group. So the
evaluator will take those facts as an argument, the way
``categories.resolve_all(products, th, overrides=…, vendor_of=…)`` already does.
Nothing here should ever grow an import of a package that might want to call it.

What a caller gets is ``resolve``: a group, its members, and its version, or
``None`` when the slug names nothing. The membership may legitimately be
**empty**, and every caller has to treat that as the answer rather than as a
missing filter — a group nobody has added anyone to computes an empty screen,
not the whole book. ``load_snapshot`` reads ``[]`` as a real bound for exactly
this reason, and ``narrow`` below is written so it cannot widen.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import threshold_registry
from ..domain import models
from ..domain.enums import SubjectEntityType

#: What may be grouped, and the model each kind lives in. ``SubjectEntityType``
#: rather than an enum of this module's own: "which kind of entity is this
#: about" already has one vocabulary here, and a second one saying ITEM where
#: the first says PRODUCT is the semantic duplication CLAUDE.md §2 is about.
#:
#: The id column is named because the writer validates membership against it.
#: A table is not enough — each of the three names its primary key differently.
KINDS: dict[str, tuple[type, str]] = {
    SubjectEntityType.CUSTOMER.value: (models.Customer, "customer_id"),
    SubjectEntityType.VENDOR.value: (models.Vendor, "vendor_id"),
    SubjectEntityType.PRODUCT.value: (models.Product, "product_id"),
}

#: What the screens call each kind. The stored value says PRODUCT and people say
#: "items"; that split already exists between ``Product`` and every screen that
#: renders it, and inventing a third word for it here would not help.
KIND_LABELS: dict[str, str] = {
    SubjectEntityType.CUSTOMER.value: "Customers",
    SubjectEntityType.VENDOR.value: "Vendors",
    SubjectEntityType.PRODUCT.value: "Items",
}

ROSTER = "ROSTER"

#: Who may see a group at all. OPERATIONAL is everyone in the workspace;
#: RESTRICTED is management only — see ``EntityGroup.visibility`` for why a
#: roster group's is a judgement rather than something derived.
OPERATIONAL = "OPERATIONAL"
RESTRICTED = "RESTRICTED"
VISIBILITIES = (OPERATIONAL, RESTRICTED)

#: A slug is what a URL and a saved link name, so it is restricted to what
#: survives both without escaping. Length-capped to the column.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """A name reduced to something addressable, or ``""`` if nothing survives.

    Returns the empty string rather than a generated fallback when a name is
    all punctuation or all non-Latin script: a slug invented here would be
    meaningless in the URL and the caller is better placed to ask for one. An
    empty return is a refusal, and ``routers.groups`` reports it as one.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return _SLUG_STRIP.sub("-", folded.lower()).strip("-")[:64]


def valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


# ── the version ──────────────────────────────────────────────────────────────
#
# A group's version has one job: to make a number computed over the group
# explainable after somebody has edited the group. So what goes into the hash is
# exactly what changes such a number, and nothing else.
#
# **The roster is in.** A membership edit is a definition change — that is the
# whole of what a roster group *is* — and a version that held still while two
# customers were added would be worse than no version at all, because a reader
# would take the matching stamp as evidence the figures were comparable.
#
# **The name and the description are out.** Renaming a group changes no number.
# Folding them in would mint a fresh version, and a fresh registry row, every
# time somebody fixed a capital letter — and then "the version moved" would stop
# meaning "the answer may have moved", which is the only thing it is for.
#
# **``archived_at`` is out** for the same reason: archiving stops a group being
# offered; it does not change who was in it.


def definition_of(group: models.EntityGroup, member_ids: Iterable[str]) -> str:
    """The exact bytes a group's version is taken over.

    Sorted at every level and serialized with ``sort_keys``: the registry
    re-hashes these bytes to verify a stamp, so the same definition has to
    produce the same byte sequence on every machine and every run. A set
    iterated in insertion order would hash differently after a re-sync that
    happened to add members in another order, and the two stamps would describe
    one group.
    """
    return json.dumps(
        {
            "entity_kind": group.entity_kind,
            "slug": group.slug,
            "membership": group.membership,
            "rule": group.rule,
            "members": sorted(set(member_ids)),
        },
        sort_keys=True,
    )


def version_of(definition: str) -> str:
    """``gr_`` plus ten hex characters, with the pre-image remembered.

    The ``remember`` call is the same arrangement ``CommercialThresholds.version``
    uses and must not be tidied away: minting is the one moment the bytes are in
    hand, so recording here is what lets the flush handler write a pre-image it
    never computed. See ``threshold_registry``.
    """
    version = "gr_" + hashlib.sha256(definition.encode()).hexdigest()[:10]
    threshold_registry.remember("group", version, definition)
    return version


def restamp(session: Session, group: models.EntityGroup) -> str:
    """Recompute a group's version from its current roster and record it.

    Called after **every** write that could move the definition, and the "every"
    is load-bearing in two directions. A definition change that did not restamp
    would leave numbers stamped with a version that no longer describes them.
    And a group row flushed carrying a version this process never minted is what
    ``threshold_registry`` counts as a stamp with no pre-image — so a writer that
    edits a group without coming through here degrades /api/health as well as
    lying about the definition.

    Records explicitly rather than leaning on the flush handler, for the reason
    ``policy.save_for_org`` does: this is a site where the pre-image is known
    *before* anything is stamped with it, and recording it here means every
    version a group has ever had stays resolvable even though the row holding it
    is superseded in place.
    """
    definition = definition_of(group, member_ids(session, group))
    group.version = version_of(definition)
    threshold_registry.record_current(
        session, group.organization_id, kind="group",
        version=group.version, serialized=definition)
    return group.version


# ── reads ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ResolvedGroup:
    """One group and who is in it, as a caller needs it to bound a computation.

    ``entity_ids`` is sorted and may be empty. Empty is a real answer — see the
    module docstring — and nothing here substitutes a benign default for it.
    """

    group_id: str
    entity_kind: str
    slug: str
    name: str
    version: str
    entity_ids: tuple[str, ...]
    archived: bool

    @property
    def size(self) -> int:
        return len(self.entity_ids)

    def to_ref(self) -> dict:
        """The group as a response carries it, beside ``thresholds_version``.

        ``version`` travels with every answer computed under this group for the
        reason a threshold version does: two readings of "what did the aerospace
        book do" taken either side of a membership edit are different questions,
        and the stamp is the only thing on screen that says so.
        """
        return {"slug": self.slug, "name": self.name,
                "entity_kind": self.entity_kind,
                "group_version": self.version, "members": self.size}


def list_groups(session: Session, org: str, *, include_restricted: bool,
                entity_kind: Optional[str] = None,
                include_archived: bool = False) -> list[models.EntityGroup]:
    """Every group this reader may see, ordered for a picker.

    ``include_restricted`` has no default, here and on ``find`` below, and that
    is the point of it: a caller has to say which reader it is answering for.
    A default would be a decision taken once by whoever wrote this line and then
    inherited silently by every call site added afterwards, which is the shape
    of the withholding bugs CLAUDE.md §1 records.
    """
    stmt = select(models.EntityGroup).where(
        models.EntityGroup.organization_id == org)
    if entity_kind is not None:
        stmt = stmt.where(models.EntityGroup.entity_kind == entity_kind)
    if not include_archived:
        stmt = stmt.where(models.EntityGroup.archived_at.is_(None))
    if not include_restricted:
        stmt = stmt.where(models.EntityGroup.visibility == OPERATIONAL)
    return list(session.scalars(stmt.order_by(models.EntityGroup.entity_kind,
                                              models.EntityGroup.name)).all())


def find(session: Session, org: str, entity_kind: str, slug: str, *,
         include_restricted: bool) -> Optional[models.EntityGroup]:
    """One group by the key a URL names it with. Archived groups included.

    Archived ones resolve on purpose: a link or a saved read that named a group
    before it was archived should say "this group is archived" rather than "no
    such group", which sends the reader looking for a typo.

    A RESTRICTED group returns ``None`` to a reader who may not see it, which is
    the same answer a nonexistent slug gets — so the router's 404 is honest
    rather than a 403 dressed up. A 403 would confirm the group exists, and its
    existence is part of what is being withheld.
    """
    stmt = select(models.EntityGroup).where(
        models.EntityGroup.organization_id == org,
        models.EntityGroup.entity_kind == entity_kind,
        models.EntityGroup.slug == slug)
    if not include_restricted:
        stmt = stmt.where(models.EntityGroup.visibility == OPERATIONAL)
    return session.scalar(stmt)


def member_ids(session: Session, group: models.EntityGroup) -> list[str]:
    """Every entity in one group, sorted.

    Sorted rather than in insertion order because this feeds the version hash
    and a bounded query, and both have to be identical across runs.
    """
    return sorted(session.scalars(
        select(models.EntityGroupMember.entity_id).where(
            models.EntityGroupMember.organization_id == group.organization_id,
            models.EntityGroupMember.group_id == group.group_id)).all())


def resolve(session: Session, org: str, entity_kind: str, slug: str, *,
            include_restricted: bool) -> Optional[ResolvedGroup]:
    """A group and its members, or ``None`` when the slug names nothing this
    reader may see. See ``find`` for why those two are one answer."""
    group = find(session, org, entity_kind, slug,
                 include_restricted=include_restricted)
    if group is None:
        return None
    return ResolvedGroup(
        group_id=group.group_id, entity_kind=group.entity_kind, slug=group.slug,
        name=group.name, version=group.version,
        entity_ids=tuple(member_ids(session, group)),
        archived=group.archived_at is not None)


def groups_of(session: Session, org: str, entity_kind: str,
              entity_ids: Iterable[str], *,
              include_restricted: bool) -> dict[str, list[dict]]:
    """Which groups each of these entities is in — one query for a whole page.

    Keyed by entity id and holding only what a row's chips render, so a
    directory does not need the group rows themselves. An entity in no group is
    absent from the mapping rather than present with an empty list: the caller
    renders nothing either way, and an absent key cannot be mistaken for a group
    that was resolved and came back empty.

    Restricted groups are dropped for a reader who may not see them — the chip
    is the same disclosure the list is, and withholding a group from the picker
    while printing its name down the rows would be the ``filterCounts.MFLOOR``
    mistake again: the guard on one surface and the value on the next.
    """
    ids = list(entity_ids)
    if not ids:
        return {}
    stmt = (
        select(models.EntityGroupMember.entity_id, models.EntityGroup.slug,
               models.EntityGroup.name)
        .join(models.EntityGroup,
              models.EntityGroup.group_id == models.EntityGroupMember.group_id)
        .where(models.EntityGroupMember.organization_id == org,
               models.EntityGroup.entity_kind == entity_kind,
               models.EntityGroup.archived_at.is_(None),
               models.EntityGroupMember.entity_id.in_(ids)))
    if not include_restricted:
        stmt = stmt.where(models.EntityGroup.visibility == OPERATIONAL)
    rows = session.execute(stmt.order_by(models.EntityGroup.name)).all()
    out: dict[str, list[dict]] = {}
    for entity_id, slug, name in rows:
        out.setdefault(entity_id, []).append({"slug": slug, "name": name})
    return out


def narrow(visible: Iterable[str], group: Optional[ResolvedGroup]) -> list[str]:
    """The ids this principal may see, restricted to the group. Sorted.

    **This function cannot widen, and that is the point of it existing.** The
    rule the group feature rests on is that a group narrows what somebody is
    looking at and never what they may see — otherwise naming a group would read
    another territory's accounts, which is a permission decision made by a
    labelling feature. Written as an intersection with the caller's already-
    scoped list, so the only way to break the rule is to stop calling it.

    ``None`` means no group was asked for, and returns the visible set unchanged.
    """
    ids = sorted(set(visible))
    if group is None:
        return ids
    return sorted(set(ids) & set(group.entity_ids))


# ── writes ───────────────────────────────────────────────────────────────────
class GroupError(ValueError):
    """A refusal with a reason a router can hand back verbatim."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(session: Session, org: str, *, entity_kind: str, name: str,
           slug: str, description: Optional[str] = None,
           visibility: str = OPERATIONAL,
           created_by_user_id: Optional[str] = None) -> models.EntityGroup:
    """A new, empty roster group.

    Empty on creation, and its version is minted over the empty roster rather
    than left blank. A group with no members is a real state with a real
    definition — it computes an empty screen — and a blank stamp would be the
    one thing the registry treats as "nothing to record".
    """
    if entity_kind not in KINDS:
        raise GroupError(f"entity_kind must be one of {', '.join(sorted(KINDS))}")
    if not valid_slug(slug):
        raise GroupError(
            "slug must be lowercase letters, digits and hyphens, starting with "
            "a letter or digit")
    if not name.strip():
        raise GroupError("A group needs a name")
    if visibility not in VISIBILITIES:
        raise GroupError(f"visibility must be one of {', '.join(VISIBILITIES)}")
    # Asked with ``include_restricted=True`` deliberately, and this is the one
    # place it is right: the slug is UNIQUE across the kind whatever a reader
    # may see, so a check that skipped restricted groups would report the name
    # free and then fail on the constraint.
    if find(session, org, entity_kind, slug, include_restricted=True) is not None:
        raise GroupError(f"A {KIND_LABELS[entity_kind].lower()} group already "
                         f"answers to '{slug}'")

    group = models.EntityGroup(
        organization_id=org, entity_kind=entity_kind, slug=slug,
        name=name.strip(), description=description, membership=ROSTER,
        visibility=visibility, created_by_user_id=created_by_user_id)
    session.add(group)
    session.flush()
    restamp(session, group)
    return group


def rename(session: Session, group: models.EntityGroup, *, name: str,
           description: Optional[str]) -> models.EntityGroup:
    """Change what a group is called. Deliberately does not restamp.

    The version is over the definition, and a name is not part of one — see the
    note above ``definition_of``. Renaming a group leaves every number computed
    under it exactly as valid as it was, and moving the stamp would say
    otherwise.
    """
    if not name.strip():
        raise GroupError("A group needs a name")
    group.name = name.strip()
    group.description = description
    return group


def set_visibility(session: Session, group: models.EntityGroup,
                   visibility: str) -> models.EntityGroup:
    """Who may see this group. Not part of the definition, so no restamp.

    Changing who may *read* a group changes no number computed over it, and
    moving the version would say the figures were no longer comparable when the
    membership had not moved at all.
    """
    if visibility not in VISIBILITIES:
        raise GroupError(f"visibility must be one of {', '.join(VISIBILITIES)}")
    group.visibility = visibility
    return group


def set_archived(session: Session, group: models.EntityGroup,
                 archived: bool) -> models.EntityGroup:
    """Archive or restore. Not a delete, and not a restamp.

    Archiving hides a group from the pickers and leaves every past answer
    explainable; deleting it would destroy the definition a quoted number was
    computed under to save one row.
    """
    group.archived_at = _now() if archived else None
    return group


def _existing_ids(session: Session, org: str, entity_kind: str,
                  entity_ids: Iterable[str]) -> set[str]:
    """Which of these ids are really this org's entities of this kind.

    The check ``EntityGroupMember`` has no foreign key to make — see its
    docstring for why the column is polymorphic. A writer's promise rather than
    the database's, so it is made in one place and every write path goes through
    it.
    """
    model, id_column = KINDS[entity_kind]
    column = getattr(model, id_column)
    ids = list(entity_ids)
    if not ids:
        return set()
    return set(session.scalars(
        select(column).where(model.organization_id == org, column.in_(ids))).all())


def add_members(session: Session, group: models.EntityGroup,
                entity_ids: Iterable[str], *,
                added_by_user_id: Optional[str] = None) -> list[str]:
    """Put entities in a group. Returns the ids actually added.

    Refuses the whole call on an id that is not this organization's entity of
    this kind, rather than adding the ones it recognises and going quiet about
    the rest. A partial success here is a roster that differs from what somebody
    asked for, with nothing on screen saying so — and the roster is the
    definition, so the difference is silently in every number computed after.

    Ids already in the group are not an error and are not re-added; the call is
    idempotent, which is what a "select all" on a filtered list needs.
    """
    wanted = sorted(set(entity_ids))
    if not wanted:
        return []
    known = _existing_ids(session, group.organization_id, group.entity_kind, wanted)
    missing = [i for i in wanted if i not in known]
    if missing:
        label = KIND_LABELS[group.entity_kind].lower().rstrip("s")
        raise GroupError(
            f"{len(missing)} id(s) are not a {label} in this workspace: "
            f"{', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}")

    already = set(member_ids(session, group))
    added = [i for i in wanted if i not in already]
    for entity_id in added:
        session.add(models.EntityGroupMember(
            organization_id=group.organization_id, group_id=group.group_id,
            entity_id=entity_id, added_by_user_id=added_by_user_id))
    if added:
        session.flush()
        restamp(session, group)
    return added


def remove_members(session: Session, group: models.EntityGroup,
                   entity_ids: Iterable[str]) -> list[str]:
    """Take entities out of a group. Returns the ids actually removed."""
    wanted = set(entity_ids)
    if not wanted:
        return []
    rows = list(session.scalars(
        select(models.EntityGroupMember).where(
            models.EntityGroupMember.organization_id == group.organization_id,
            models.EntityGroupMember.group_id == group.group_id,
            models.EntityGroupMember.entity_id.in_(sorted(wanted)))).all())
    for row in rows:
        session.delete(row)
    if rows:
        session.flush()
        restamp(session, group)
    return sorted(row.entity_id for row in rows)
