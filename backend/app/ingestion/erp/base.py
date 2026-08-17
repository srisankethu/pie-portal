"""What a connector *is*, declared once: its fields, its identity, its factory.

Every non-Zoho ERP the platform can read registers a :class:`ConnectorSpec`
here. The spec is the single description that drives everything generic about
a connector — the connect form the UI renders, the validation the router runs,
which entered values are secrets (encrypted as one JSON document) and which are
identifying config (stored readable), and how to build a live source from a
stored credential. Adding connector number seven is adding a module that
registers a spec; nothing branches on a connector key outside its own module.

Zoho deliberately does not register here. Its connection flow predates this
registry and is richer than a field list (browser OAuth, data-centre picker,
per-scope probing), and pretending it fits the generic form would either dumb
that flow down or bloat the spec with one connector's needs. The registry is
for connectors whose auth is "enter the integration credentials the ERP admin
minted" — which is all five US-market systems.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional

from ..errors import IngestionError


@dataclass(frozen=True)
class Field:
    """One value the connect form asks for.

    ``secret`` decides where the value lives at rest: secrets go into the
    credential's encrypted JSON document, everything else into readable
    ``config`` — so a support screen can show an account id but can never
    show a token.
    """

    name: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "secret": self.secret,
                "required": self.required, "placeholder": self.placeholder,
                "help": self.help}


@dataclass(frozen=True)
class ConnectorSpec:
    """One ERP the platform can read, and everything generic code needs to
    know about it.

    ``credential_fields`` belong to the sign-in and are shared by every company
    connected through it (a Business Central app registration serves every
    company in the tenant); ``connection_fields`` belong to one company.
    ``external_id_field`` names the connection field whose value *is* the
    company's identity in that system — it lands in the connection row's
    external-org-id column and is what the duplicate-connection and plan-gate
    checks key on.
    """

    key: str
    label: str
    #: What the source system calls one set of books — "subsidiary",
    #: "company", "tenant" — so screens can speak its language.
    company_term: str
    credential_fields: tuple[Field, ...]
    connection_fields: tuple[Field, ...]
    external_id_field: str
    #: One paragraph an owner reads before connecting: which API this uses and
    #: what to mint in the ERP's admin console.
    setup_note: str
    #: ``(material, since=None) -> source`` — a live source for one company.
    build_source: Callable[..., Any]
    #: Optionally ``(material) -> [{"id", "name"}]``: the companies a
    #: credential can see, so the connect flow offers a picker instead of
    #: asking somebody to find a GUID. None for systems whose sign-in is
    #: already scoped to one company.
    discover: Optional[Callable[..., list[dict[str, Any]]]] = None


@dataclass(frozen=True)
class CredentialMaterial:
    """Everything ``build_source`` needs, already decrypted.

    Assembled by ``ingestion/connections.credential_material`` from a
    connection row; never persisted, never logged.
    """

    connector: str
    #: Decrypted secret fields, by field name.
    secrets: dict[str, str] = dc_field(default_factory=dict)
    #: The credential's non-secret fields, by field name.
    config: dict[str, Any] = dc_field(default_factory=dict)
    #: The connection's own fields (company id, branch, endpoint …).
    connection_config: dict[str, Any] = dc_field(default_factory=dict)
    #: The connected company's id in the source system.
    external_org_id: str = ""

    def value(self, name: str, default: str = "") -> str:
        """One entered value, wherever it was stored — secret, credential
        config, or connection config, in that order of specificity."""
        for bag in (self.secrets, self.connection_config, self.config):
            v = bag.get(name)
            if v not in (None, ""):
                return str(v)
        return default


class UnknownConnectorError(IngestionError):
    """A connector key nothing has registered. Distinct from a bad credential:
    the remedy is a different key (or a platform upgrade), not a fixed secret."""


_REGISTRY: dict[str, ConnectorSpec] = {}


def register(spec: ConnectorSpec) -> ConnectorSpec:
    """Add one connector. Refuses a duplicate key — two modules claiming one
    key would make which spec wins an import-order accident."""
    if spec.key in _REGISTRY:
        raise ValueError(f"connector {spec.key!r} is already registered")
    _REGISTRY[spec.key] = spec
    return spec


def get_spec(key: str) -> ConnectorSpec:
    spec = _REGISTRY.get(key)
    if spec is None:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise UnknownConnectorError(
            f"No connector named {key!r} is available (available: {known}).")
    return spec


def catalog() -> list[ConnectorSpec]:
    """Every registered connector, stably ordered for the UI."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def split_inputs(spec: ConnectorSpec,
                 values: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any],
                                                  dict[str, Any], str]:
    """Entered form values → (secrets, credential config, connection config,
    external org id), validated against the spec.

    Refuses a missing required field by name rather than storing a credential
    that can never authenticate — a connection that fails at connect time is a
    ten-second fix; one that fails on the first nightly sync is a support
    ticket. Unknown keys are refused too: a value the spec does not declare
    would be stored nowhere visible and silently do nothing.
    """
    declared = {f.name: f for f in spec.credential_fields + spec.connection_fields}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ValueError(
            f"{spec.label} does not use: {', '.join(unknown)}")
    missing = [f.label for f in declared.values()
               if f.required and str(values.get(f.name) or "").strip() == ""]
    if missing:
        raise ValueError(
            f"{spec.label} needs: {', '.join(missing)}")

    secrets: dict[str, str] = {}
    cred_config: dict[str, Any] = {}
    conn_config: dict[str, Any] = {}
    for f in spec.credential_fields:
        v = str(values.get(f.name) or "").strip()
        if not v:
            continue
        (secrets if f.secret else cred_config)[f.name] = v
    for f in spec.connection_fields:
        v = str(values.get(f.name) or "").strip()
        if not v:
            continue
        # A per-company secret would end up on the shared credential row,
        # readable by every company connected through it — so the spec shape
        # forbids it and this refuses rather than quietly relocating it.
        if f.secret:
            raise ValueError(
                f"{spec.label}: per-company field {f.name!r} cannot be secret")
        conn_config[f.name] = v

    external_org_id = str(conn_config.get(spec.external_id_field) or "").strip()
    if not external_org_id:
        term = next((f.label for f in spec.connection_fields
                     if f.name == spec.external_id_field), spec.external_id_field)
        raise ValueError(f"{spec.label} needs: {term}")
    return secrets, cred_config, conn_config, external_org_id


def split_credential_inputs(spec: ConnectorSpec,
                            values: dict[str, Any]) -> tuple[dict[str, str],
                                                             dict[str, Any]]:
    """The credential half of :func:`split_inputs`, for flows that touch the
    sign-in without touching a company — rotation, and company discovery
    before any connection exists."""
    declared = {f.name: f for f in spec.credential_fields}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise ValueError(f"{spec.label} does not use: {', '.join(unknown)}")
    missing = [f.label for f in declared.values()
               if f.required and str(values.get(f.name) or "").strip() == ""]
    if missing:
        raise ValueError(f"{spec.label} needs: {', '.join(missing)}")
    secrets: dict[str, str] = {}
    config: dict[str, Any] = {}
    for f in spec.credential_fields:
        v = str(values.get(f.name) or "").strip()
        if v:
            (secrets if f.secret else config)[f.name] = v
    return secrets, config


# ── canonical-payload helpers shared by every translator ─────────────────────
#
# Translators map native records onto the canonical wire shape ``normalize``
# validates. These helpers cover the two conversions every ERP needs — dates
# arrive in that system's dress, and the canonical shape wants ISO dates —
# without ever inventing a value: an unreadable date comes back as None, and
# ``normalize`` then refuses the row by name instead of this layer guessing.

def iso_date(value: Any) -> Optional[str]:
    """A date in whatever dress the source wears → ``YYYY-MM-DD``, or None.

    Handles the shapes the five connectors actually emit: ISO dates, ISO
    datetimes with or without zone, ``MM/DD/YYYY`` (NetSuite's default), and
    ``/Date(ms)/`` (older OData). None for anything else — never a guess.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    # ISO date or datetime: the date is the first ten characters.
    head = text[:10]
    try:
        return date.fromisoformat(head).isoformat()
    except ValueError:
        pass
    # NetSuite SuiteQL default dress.
    for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    # Legacy OData /Date(1700000000000)/ — milliseconds since the epoch, UTC.
    if text.startswith("/Date(") and text.endswith(")/"):
        ms = text[6:-2].split("+")[0].split("-")[0]
        try:
            return datetime.utcfromtimestamp(int(ms) / 1000).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    return None


def in_window(iso: Optional[str], since: Optional[date],
              until: Optional[date]) -> bool:
    """Whether a document date falls inside the window a pull asked for.

    Client-side, for the sources whose APIs cannot be trusted to filter
    server-side; the source still sends the server filter where it can, and
    this keeps the window honest either way. An undated document passes — the
    normalizer is the layer that refuses those by name.
    """
    if not iso:
        return True
    try:
        when = date.fromisoformat(iso)
    except ValueError:
        return True
    if since is not None and when < since:
        return False
    return not (until is not None and when > until)


def first(record: dict[str, Any], *keys: str) -> Any:
    """The first present, non-empty value under any of these keys."""
    for key in keys:
        v = record.get(key)
        if v not in (None, ""):
            return v
    return None


class DocumentTally:
    """The listing bookkeeping the sync layer reads off a source.

    ``listed``/``listing_complete`` feed the deletion sweep (``sync._mirror``):
    only a listing that ran to its end may say what is absent. The two counters
    land on the sync report so a run can say what it cost. One small object so
    five sources cannot each rename an attribute the sync probes with getattr.
    """

    def __init__(self) -> None:
        self.listed: dict[str, set[str]] = {}
        self.listing_complete: set[str] = set()
        self.documents_fetched = 0
        self.documents_resumed = 0

    def saw(self, kind: str, doc_id: str) -> None:
        self.listed.setdefault(kind, set()).add(str(doc_id))

    def complete(self, kind: str) -> None:
        self.listing_complete.add(kind)


def group_lines(rows: Iterable[dict[str, Any]], header_key: str,
                ) -> dict[str, list[dict[str, Any]]]:
    """Flat joined rows → lines grouped per document, insertion-ordered.

    For the connectors whose efficient read is one flat header×line query
    (NetSuite SuiteQL, P21's OData views) rather than a nested document.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = str(row.get(header_key) or "")
        if key:
            out.setdefault(key, []).append(row)
    return out
