"""Where an imported record came from, in one shape, for every entity.

The hierarchy is connector → connected company → record, and it is the same
hierarchy for customers, items, vendors and everything a future connector
imports. So the projection is written once here rather than per entity and per
endpoint: a customer picker that names the company and an item picker that does
not is exactly how the ambiguity comes back after being fixed.

**Names are not identity.** "ABC Industries" can legitimately exist in three
connected companies and be three different customers. An external id is not
identity either — it is unique only inside the system that issued it, and Tally
numbers ledgers from 1 in every company. Identity is the triple:

    connector + connected company + that system's id

which is what ``Customer``/``Product``/``Vendor`` now store and what
``CustomerConnectorRecord`` has always stored.

**Nothing here is connector-specific.** There is no branch on "zoho", and a
rule that needed one would belong in that connector's own package. A connector
contributes a display name and an icon through ``CONNECTORS`` below; everything
else about it is data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models


class Sourced(Protocol):
    """Anything imported from a connected company.

    Structural rather than a base class: ``Customer``, ``Product`` and
    ``Vendor`` are unrelated tables that happen to share these three columns,
    and giving them a common base would put an inheritance relationship where
    there is only a shared property.
    """

    connector: Optional[str]
    connection_id: Optional[str]
    external_id: str


def index_of(session: Session, organization_id: str, model: Any) -> dict[str, Sourced]:
    """Every imported record of one kind, by its own local id.

    One query per entity kind per request, which is what keeps ``stamp`` from
    being an N+1. The primary key column differs per table and is read from the
    mapper rather than passed in, so a caller cannot pair the wrong one.
    """
    pk = list(model.__table__.primary_key.columns)[0].name
    return {getattr(r, pk): r for r in session.scalars(
        select(model).where(model.organization_id == organization_id))}


#: How each connector presents itself. A registry, not a chain of conditionals:
#: adding a connector is adding a row, and nothing branches on the key. The
#: short code is what a badge shows when there is no room for the full name.
CONNECTORS: dict[str, dict[str, str]] = {
    "zoho": {"label": "Zoho Books", "short": "Zoho", "icon": "◆"},
    "tally": {"label": "Tally Prime", "short": "Tally", "icon": "▲"},
    "sap": {"label": "SAP", "short": "SAP", "icon": "■"},
    "odoo": {"label": "Odoo", "short": "Odoo", "icon": "●"},
    "quickbooks": {"label": "QuickBooks", "short": "QB", "icon": "◗"},
    "erpnext": {"label": "ERPNext", "short": "ERPNext", "icon": "◆"},
}

#: What an unregistered connector renders as. A connector nobody has described
#: still has to be nameable — showing its key is better than showing nothing,
#: and better than pretending it is the one connector we happen to know.
_UNKNOWN = {"label": "", "short": "", "icon": "◇"}


@dataclass(frozen=True)
class Origin:
    """One record's source, as a screen needs it.

    Deliberately flat and free of ids nobody can read except ``external_id``,
    which is the one thing that makes a row findable in the source system.
    """

    connector: Optional[str]
    connector_label: str
    connector_short: str
    icon: str
    connection_id: Optional[str]
    company: str
    #: The id the source system gave this record. Shown small, and only where
    #: somebody would go looking for it — it is a lookup key, not a name.
    external_id: str
    #: True when nothing recorded where this came from. Rendered as "source not
    #: recorded" rather than guessed at.
    unknown: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "connector_label": self.connector_label,
            "connector_short": self.connector_short,
            "icon": self.icon,
            "connection_id": self.connection_id,
            "company": self.company,
            "external_id": self.external_id,
            "unknown": self.unknown,
        }


@dataclass(frozen=True)
class _ConnectionSource:
    """One connector's connection table, described rather than branched on."""

    connector: str
    model: Any
    fallback_label: Any


#: Every table that holds connected companies. A list of one today. A second
#: connector appends a row here (or, better, replaces the list with one shared
#: table) and nothing downstream changes.
_CONNECTION_SOURCES: tuple[_ConnectionSource, ...] = (
    _ConnectionSource(
        connector="zoho",
        model=models.ZohoConnection,
        fallback_label=lambda row: f"Zoho company {row.zoho_organization_id}",
    ),
)


class Companies:
    """Connected-company labels, read once per request rather than per row.

    A picker renders hundreds of rows and every one of them needs its company's
    name. Resolving that per row is the N+1 this class exists to prevent, and
    the reason origins are built through a resolver instead of a free function.
    """

    def __init__(self, session: Session, organization_id: str) -> None:
        self._labels: dict[str, str] = {}
        self._connectors: dict[str, str] = {}
        # ── the one connector-specific line in this module, and where the
        #    second connector starts ────────────────────────────────────────
        #
        # There is exactly one connection table today and it is Zoho's, so this
        # reads it and stamps its connector name. That is a real coupling and
        # it is stated rather than hidden: when the second connector lands, the
        # fix is a ``connections`` table with a ``connector`` column that every
        # connector writes to, and this loop becomes one query with no literal
        # in it. Nothing *else* in this module or in the callers needs to
        # change when that happens — which is the property the split is for.
        for source in _CONNECTION_SOURCES:
            for row in session.scalars(
                    select(source.model).where(
                        source.model.organization_id == organization_id)):
                self._labels[row.connection_id] = (
                    row.label or source.fallback_label(row))
                self._connectors[row.connection_id] = source.connector

    def of(self, record: Sourced) -> Origin:
        connection_id = getattr(record, "connection_id", None)
        connector = (getattr(record, "connector", None)
                     or self._connectors.get(connection_id or ""))
        meta = CONNECTORS.get(connector or "", _UNKNOWN)
        company = self._labels.get(connection_id or "", "")
        return Origin(
            connector=connector,
            connector_label=meta["label"] or (connector or ""),
            connector_short=meta["short"] or (connector or ""),
            icon=meta["icon"],
            connection_id=connection_id,
            company=company,
            external_id=getattr(record, "external_id", "") or "",
            # Unknown when neither half of the source is recorded. A row that
            # names its connector but not its company is partially attributed,
            # which is worth showing rather than discarding.
            unknown=not connector and not company,
        )

    def stamp(self, rows: Iterable[dict[str, Any]],
              index: dict[str, Sourced], *, by: str,
              key: str = "origin") -> list[dict[str, Any]]:
        """Attach each row's source, in place, and hand the rows back.

        The alternative — every endpoint reaching for the master table it needs
        and formatting the source itself — is how the customer picker ended up
        naming the company while the item grid did not. One projection, applied
        the same way everywhere, is the whole point of this module; this is that
        projection applied to a list rather than to one record.

        A row whose entity has no master gets ``None`` rather than a fabricated
        origin. That is a real state — it is the gap the sync reports as
        UNKNOWN_CUSTOMER — and the screen says "source not recorded" for it.
        """
        out = []
        for row in rows:
            record = index.get(str(row.get(by) or ""))
            row[key] = self.of(record).to_dict() if record is not None else None
            out.append(row)
        return out

    def label_for(self, connection_id: Optional[str]) -> str:
        """A connected company's name, from its id alone.

        ``of`` answers the same question for a *record*; this answers it for a
        grouped query, which has the id and no row to hand. One dictionary
        either way, so the two cannot name the same company differently.
        """
        if not connection_id:
            return "Source not recorded"
        return self._labels.get(connection_id) or "Source not recorded"

    @property
    def count(self) -> int:
        """How many connected companies this organization has.

        The one number a screen needs to decide whether source badges are
        *information* or *noise*: with a single connected company every badge
        says the same thing, and a column of identical badges is decoration
        that costs width. With two or more it is the answer to "which ABC
        Industries is this?".
        """
        return len(self._labels)
