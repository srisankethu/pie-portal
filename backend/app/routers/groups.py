"""Group definitions over HTTP — drawing a set, and putting things in it.

Thin by rule: every decision here is ``groups.service``'s, and this module maps
it to a status code and enforces who may do what. The one thing it owns is the
role split, and the split is the same one ``item_category_overrides`` uses:

- **Reading is open to every principal in the workspace.** A group is how a
  salesperson narrows their own directory, so a picker only managers can see is
  a feature only managers can use. Restricted groups are the exception and they
  are withheld rather than refused — see ``service.find``.
- **Writing is manager and above.** Drawing a group is policy: it moves every
  figure computed under it, and it is the same class of decision as placing an
  item in a line of the business.

Nothing here computes a number, so nothing here needs a thresholds version. The
figures a group scopes are computed by the screens that accept ``?group=``, and
those carry both their own stamp and the group's.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import groups
from ..approvals import user_names
from ..authz import Principal, current_principal, require_manager_or_owner
from ..db import get_session
from ..domain import models

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


def _kind_or_404(entity_kind: str) -> str:
    """The kind as the service spells it, or a 404 on the path segment itself.

    404 rather than 422: ``/groups/widget/aerospace`` names a resource that does
    not exist, and the reader's next move is the same either way.
    """
    kind = entity_kind.upper()
    if kind not in groups.KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"No such kind of group: {entity_kind}")
    return kind


def _group_or_404(session: Session, principal: Principal, entity_kind: str,
                  slug: str) -> models.EntityGroup:
    group = groups.find(session, principal.organization_id,
                        _kind_or_404(entity_kind), slug,
                        include_restricted=principal.is_manager_or_owner)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such group")
    return group


def _to_read(group: models.EntityGroup, *, members: int,
             created_by: Optional[str]) -> dict:
    return {
        "slug": group.slug,
        "name": group.name,
        "description": group.description,
        "entity_kind": group.entity_kind,
        "entity_label": groups.KIND_LABELS[group.entity_kind],
        "membership": group.membership,
        "visibility": group.visibility,
        # The definition's stamp, on every representation of a group and not
        # only on a computed answer. A reader comparing two figures needs to be
        # able to see that the group moved between them, and the group screen is
        # where somebody looks when the figures disagree.
        "group_version": group.version,
        "members": members,
        "created_by": created_by,
        "archived": group.archived_at is not None,
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
    }


@router.get("")
def list_groups(entity_kind: Optional[str] = Query(None),
                include_archived: bool = Query(False),
                principal: Principal = Depends(current_principal),
                session: Session = Depends(get_session)) -> dict:
    """Every group this reader may see, with how many are in each.

    The counts come from one query over the whole page rather than a
    ``member_ids`` call per group: a workspace with forty groups would otherwise
    make forty round trips to render a picker.
    """
    org = principal.organization_id
    kind = _kind_or_404(entity_kind) if entity_kind else None
    rows = groups.list_groups(
        session, org, entity_kind=kind, include_archived=include_archived,
        include_restricted=principal.is_manager_or_owner)
    sizes = _sizes(session, org, [g.group_id for g in rows])
    people = user_names(session, org,
                        [g.created_by_user_id for g in rows if g.created_by_user_id])
    return {
        "groups": [_to_read(g, members=sizes.get(g.group_id, 0),
                            created_by=people.get(g.created_by_user_id or ""))
                   for g in rows],
        "kinds": [{"value": k, "label": groups.KIND_LABELS[k]}
                  for k in sorted(groups.KINDS)],
        "may_edit": principal.is_manager_or_owner,
        "empty_reason": (None if rows else
                         "No groups yet. A group is a set of customers, vendors "
                         "or items you want to ask questions about together."),
    }


def _sizes(session: Session, org: str, group_ids: list[str]) -> dict[str, int]:
    from sqlalchemy import func, select

    if not group_ids:
        return {}
    return dict(session.execute(
        select(models.EntityGroupMember.group_id, func.count())
        .where(models.EntityGroupMember.organization_id == org,
               models.EntityGroupMember.group_id.in_(group_ids))
        .group_by(models.EntityGroupMember.group_id)).all())


@router.get("/{entity_kind}/{slug}")
def get_group(entity_kind: str, slug: str,
              principal: Principal = Depends(current_principal),
              session: Session = Depends(get_session)) -> dict:
    """One group, and its members by name.

    Names resolved here rather than in the browser for the reason the account
    directory resolves its assignees: the client would need three entity
    directories to render one list, and this is one indexed read.
    """
    org = principal.organization_id
    group = _group_or_404(session, principal, entity_kind, slug)
    ids = groups.member_ids(session, group)
    model, id_column = groups.KINDS[group.entity_kind]
    names = _names(session, org, model, id_column, ids)
    people = user_names(session, org,
                        [group.created_by_user_id] if group.created_by_user_id else [])
    return {
        **_to_read(group, members=len(ids),
                   created_by=people.get(group.created_by_user_id or "")),
        # Sorted by name for a person to read, not by id. A member whose entity
        # has since been removed from the books is named honestly rather than
        # dropped — a roster that quietly shrinks is a definition that changed
        # without anybody deciding to change it.
        "roster": sorted(
            ({"entity_id": i, "name": names.get(i)} for i in ids),
            key=lambda r: (r["name"] is None, (r["name"] or "").lower())),
        "may_edit": principal.is_manager_or_owner,
    }


def _names(session: Session, org: str, model: type, id_column: str,
           ids: list[str]) -> dict[str, str]:
    from sqlalchemy import select

    if not ids:
        return {}
    column = getattr(model, id_column)
    return dict(session.execute(
        select(column, model.name).where(model.organization_id == org,
                                         column.in_(ids))).all())


class GroupIn(BaseModel):
    entity_kind: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=128)
    #: Optional: derived from the name when it is left out, which is what a
    #: person filling in one field expects. Supplied explicitly when the derived
    #: one is taken or unreadable.
    slug: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = Field(default=None, max_length=512)
    visibility: str = Field(default=groups.OPERATIONAL)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_group(body: GroupIn,
                 principal: Principal = Depends(require_manager_or_owner),
                 session: Session = Depends(get_session)) -> dict:
    """Draw a new, empty group."""
    kind = _kind_or_404(body.entity_kind)
    slug = (body.slug or groups.slugify(body.name)).strip().lower()
    if not slug:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That name has no addressable form — give the group a short id of "
            "lowercase letters, digits and hyphens")
    try:
        group = groups.create(
            session, principal.organization_id, entity_kind=kind,
            name=body.name, slug=slug, description=body.description,
            visibility=body.visibility, created_by_user_id=principal.user_id)
    except groups.GroupError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    session.flush()
    return _to_read(group, members=0, created_by=principal.name)


class GroupPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    visibility: Optional[str] = None
    archived: Optional[bool] = None


@router.patch("/{entity_kind}/{slug}")
def update_group(entity_kind: str, slug: str, body: GroupPatch,
                 principal: Principal = Depends(require_manager_or_owner),
                 session: Session = Depends(get_session)) -> dict:
    """Rename, re-describe, restrict or archive. Never a membership change.

    None of these four is part of the definition, so none of them restamps —
    see ``service.rename``. Membership is the only thing that moves a group's
    version, and it has its own two endpoints below so that a rename and a
    roster edit can never be one request that half-succeeds.
    """
    group = _group_or_404(session, principal, entity_kind, slug)
    try:
        if body.name is not None or body.description is not None:
            groups.rename(session, group,
                          name=body.name if body.name is not None else group.name,
                          description=(body.description
                                       if body.description is not None
                                       else group.description))
        if body.visibility is not None:
            groups.set_visibility(session, group, body.visibility)
        if body.archived is not None:
            groups.set_archived(session, group, body.archived)
    except groups.GroupError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    session.flush()
    ids = groups.member_ids(session, group)
    return _to_read(group, members=len(ids), created_by=None)


class MembersIn(BaseModel):
    entity_ids: list[str] = Field(min_length=1, max_length=2000)


@router.post("/{entity_kind}/{slug}/members")
def add_members(entity_kind: str, slug: str, body: MembersIn,
                principal: Principal = Depends(require_manager_or_owner),
                session: Session = Depends(get_session)) -> dict:
    """Put entities in a group, and restamp it.

    422 on an id that is not this workspace's entity of this kind, with nothing
    written — see ``service.add_members`` for why a partial success would be
    worse than a refusal.
    """
    group = _group_or_404(session, principal, entity_kind, slug)
    try:
        added = groups.add_members(session, group, body.entity_ids,
                                   added_by_user_id=principal.user_id)
    except groups.GroupError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    session.flush()
    return {"added": added, "members": len(groups.member_ids(session, group)),
            "group_version": group.version}


@router.delete("/{entity_kind}/{slug}/members/{entity_id}")
def remove_member(entity_kind: str, slug: str, entity_id: str,
                  principal: Principal = Depends(require_manager_or_owner),
                  session: Session = Depends(get_session)) -> dict:
    """Take one entity out of a group, and restamp it.

    The id is in the path rather than in a body, which is what every other
    DELETE in this API does — no client here sends a body on a DELETE, and the
    one that tried would be at the mercy of whatever proxy sits in front of it.
    One at a time is also the honest granularity: each removal is a definition
    change with its own version, and a bulk call would collapse several into one
    stamp that describes none of the intermediate states.
    """
    group = _group_or_404(session, principal, entity_kind, slug)
    removed = groups.remove_members(session, group, [entity_id])
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of that group")
    session.flush()
    return {"removed": removed, "members": len(groups.member_ids(session, group)),
            "group_version": group.version}
