"""A company keeps a catalogue per manufacturer, not one catalogue.

``h1cat`` gave a connected company exactly one catalogue: one set of source
files, one pack, one ``products.jsonl``. A distributor sells several
manufacturers, and each manufacturer's price lists are decoded through that
manufacturer's own pack — Kennametal's through ``zcnc``, YG-1's through a YG-1
pack — so a company now keeps a **catalogue per manufacturer**, each with its
own files and its own pack, and resolves against the union of them.

Four changes, and the shape of each.

``company_corpora.catalogue_key`` — which of the company's catalogues a source
file feeds. Every existing row reads ``default``, the key this migration gives
the one catalogue a company already had.

``company_catalogues`` is re-keyed from ``(organization_id, connection_id)`` to
``(organization_id, connection_id, catalogue_key)``. On Postgres the primary
key is dropped and re-created **in place** — never through a batch recreate,
which would rebuild the table and drop the row-level security policy ``h2rls``
put on it. SQLite has no policies and cannot alter a primary key in place, so
there — and only there — the table is recreated.

``company_catalogues.name`` and ``.pack_choice`` — the definition half of a
row, which now exists *before* the first build: a catalogue is created with a
name and a pack, and built later. ``built_at`` becomes nullable for the same
reason. ``pack_choice`` is filled from ``zoho_connections.config["pie_pack"]``,
where the choice used to live while it was a fact about the company rather
than about one of its catalogues; and a company that holds source files but
had never built gets its ``default`` row inserted here, so its files keep a
catalogue to belong to.

Columns written out literally rather than imported from ``app.domain.models``
(§4): this runs against schemas from months ago, and models describe today.

Downgrade keeps every uploaded file: sources of a non-default catalogue are
superseded rather than deleted, because the older code merges every live
source of a company into one catalogue and a YG-1 price list in a Kennametal
catalogue is a wrong part with a real stamp. Their catalogue rows go, since the
older key cannot hold them.

Revision ID: m1cats
Revises: l1alias
Create Date: 2026-09-03
"""
import contextlib
import json
import warnings

import sqlalchemy as sa
from alembic import op

revision = "m1cats"
down_revision = "l1alias"
branch_labels = None
depends_on = None

DEFAULT_KEY = "default"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


@contextlib.contextmanager
def _quiet_batch_rekey():
    """Re-keying through a batch recreate makes SQLAlchemy warn that the
    reflected table's key columns disagree with the primary key being
    declared — which is the whole point of the operation, and the new key is
    the one it applies. Silenced here, for this block only, so the one
    warning that would say something new is not lost in a known one."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", sa.exc.SAWarning)
        yield


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("company_corpora", sa.Column(
        "catalogue_key", sa.String(64), nullable=False, server_default=DEFAULT_KEY))

    op.add_column("company_catalogues", sa.Column(
        "catalogue_key", sa.String(64), nullable=False, server_default=DEFAULT_KEY))
    op.add_column("company_catalogues", sa.Column(
        "name", sa.String(255), nullable=False, server_default=""))
    op.add_column("company_catalogues", sa.Column(
        "pack_choice", sa.String(255), nullable=True))

    if _is_postgres():
        # In place: a batch recreate would drop the tenant policy on this table.
        op.drop_constraint("company_catalogues_pkey", "company_catalogues",
                           type_="primary")
        op.create_primary_key("company_catalogues_pkey", "company_catalogues",
                              ["organization_id", "connection_id", "catalogue_key"])
        op.alter_column("company_catalogues", "built_at",
                        existing_type=sa.DateTime(timezone=True), nullable=True)
    else:
        with _quiet_batch_rekey(), op.batch_alter_table(
                "company_catalogues", recreate="always") as batch:
            batch.alter_column("built_at", existing_type=sa.DateTime(timezone=True),
                               nullable=True)
            batch.create_primary_key(
                "pk_company_catalogues",
                ["organization_id", "connection_id", "catalogue_key"])

    # The pack choice moves from the company to its (one) catalogue.
    packs = {}
    for connection_id, config in bind.execute(sa.text(
            "SELECT connection_id, config FROM zoho_connections")):
        if isinstance(config, (bytes, str)):
            try:
                config = json.loads(config)
            except ValueError:
                config = None
        chosen = ((config or {}).get("pie_pack") or "").strip() if isinstance(config, dict) else ""
        if chosen:
            packs[connection_id] = chosen[:255]
    for connection_id, chosen in packs.items():
        bind.execute(sa.text(
            "UPDATE company_catalogues SET pack_choice = :pack "
            "WHERE connection_id = :cid AND pack_choice IS NULL"),
            {"pack": chosen, "cid": connection_id})

    # A company with files on record but no catalogue row: its files need a
    # catalogue to belong to, so the default one is defined here.
    have_rows = {(org, cid) for org, cid in bind.execute(sa.text(
        "SELECT organization_id, connection_id FROM company_catalogues"))}
    for org, cid in bind.execute(sa.text(
            "SELECT DISTINCT organization_id, connection_id FROM company_corpora "
            "WHERE superseded_at IS NULL")):
        if (org, cid) in have_rows:
            continue
        bind.execute(sa.text(
            "INSERT INTO company_catalogues "
            "(organization_id, connection_id, catalogue_key, name, pack_choice, "
            " pack, records, rows_read, quarantined) "
            "VALUES (:org, :cid, :key, '', :pack, '', 0, 0, 0)"),
            {"org": org, "cid": cid, "key": DEFAULT_KEY, "pack": packs.get(cid)})


def downgrade() -> None:
    bind = op.get_bind()
    # Kept, hidden: the older code would merge these into the one catalogue.
    bind.execute(sa.text(
        "UPDATE company_corpora SET superseded_at = CURRENT_TIMESTAMP "
        "WHERE catalogue_key <> :key AND superseded_at IS NULL"),
        {"key": DEFAULT_KEY})
    bind.execute(sa.text(
        "DELETE FROM company_catalogues WHERE catalogue_key <> :key "
        "OR built_at IS NULL"), {"key": DEFAULT_KEY})

    if _is_postgres():
        op.drop_constraint("company_catalogues_pkey", "company_catalogues",
                           type_="primary")
        op.create_primary_key("company_catalogues_pkey", "company_catalogues",
                              ["organization_id", "connection_id"])
        op.alter_column("company_catalogues", "built_at",
                        existing_type=sa.DateTime(timezone=True), nullable=False)
        op.drop_column("company_catalogues", "pack_choice")
        op.drop_column("company_catalogues", "name")
        op.drop_column("company_catalogues", "catalogue_key")
    else:
        with _quiet_batch_rekey(), op.batch_alter_table(
                "company_catalogues", recreate="always") as batch:
            batch.alter_column("built_at", existing_type=sa.DateTime(timezone=True),
                               nullable=False)
            batch.drop_column("pack_choice")
            batch.drop_column("name")
            batch.drop_column("catalogue_key")
            batch.create_primary_key("pk_company_catalogues",
                                     ["organization_id", "connection_id"])

    op.drop_column("company_corpora", "catalogue_key")
