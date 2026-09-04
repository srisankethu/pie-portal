"""A company keeps a catalogue per manufacturer, and every price list carries
its own decoding config.

``h1cat`` gave a connected company exactly one catalogue: one set of source
files, one pack, one ``products.jsonl`` — and the pack, the decoder, was a
fact about the *company* (``zoho_connections.config["pie_pack"]``). Two things
are wrong with that. A distributor sells several manufacturers, so a company
now keeps a **catalogue per manufacturer**. And a decoder is a fact about one
*file* — how that export phrases its descriptions — so there is no default
decoder any more: every uploaded price list carries its own **decoding
config**, proposed by analysing the file and saved by a person, and a file
without one is not decoded.

The columns, and the shape of each.

``company_corpora.catalogue_key`` — which of the company's catalogues a file
feeds. Every existing row reads ``default``, the key this migration gives the
one catalogue a company already had.

``company_corpora.rule_set``, ``.analysis``, ``.decoding_confirmed_at``,
``.decoding_confirmed_by`` — the decoding config's decoder half (the columns
half is ``mapping``, from ``i1src``). A file that was decoded before this
migration was decoded through the company's recorded choice, so that choice
is carried onto each of its live files here and marked confirmed by the
migration: a recorded decision, not a default applied to an unknown file.

``company_catalogues`` is re-keyed from ``(organization_id, connection_id)``
to ``(organization_id, connection_id, catalogue_key)``. On Postgres the
primary key is dropped and re-created **in place** — never through a batch
recreate, which would rebuild the table and drop the row-level security policy
``h2rls`` put on it. SQLite has no policies and cannot alter a primary key in
place, so there — and only there — the table is recreated. ``name`` is the
definition half of a row, which now exists *before* the first build; ``built_at``
becomes nullable for the same reason; ``pack`` (the path a build decoded
through) goes, since a catalogue no longer has one decoder. A company that
holds source files but had never built gets its ``default`` row inserted
here, so its files keep a catalogue to belong to.

Columns written out literally rather than imported from ``app.domain.models``
(§4): this runs against schemas from months ago, and models describe today.

Downgrade keeps every uploaded file: sources of a non-default catalogue are
superseded rather than deleted, because the older code merges every live
source of a company into one catalogue and a YG-1 price list in a Kennametal
catalogue is a wrong part with a real stamp. Their catalogue rows go, since
the older key cannot hold them.

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

#: The decoding-config columns on a source file, written out literally.
CORPUS_COLUMNS = (
    ("rule_set", sa.String(128)),
    ("analysis", sa.JSON()),
    ("decoding_confirmed_at", sa.DateTime(timezone=True)),
    ("decoding_confirmed_by", sa.String(64)),
)


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


def _recorded_choices(bind) -> dict:
    """The decoder each company had chosen, off the connection's config."""
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
            packs[connection_id] = chosen[:128]
    return packs


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("company_corpora", sa.Column(
        "catalogue_key", sa.String(64), nullable=False, server_default=DEFAULT_KEY))
    for name, type_ in CORPUS_COLUMNS:
        op.add_column("company_corpora", sa.Column(name, type_, nullable=True))

    op.add_column("company_catalogues", sa.Column(
        "catalogue_key", sa.String(64), nullable=False, server_default=DEFAULT_KEY))
    op.add_column("company_catalogues", sa.Column(
        "name", sa.String(255), nullable=False, server_default=""))

    if _is_postgres():
        # In place: a batch recreate would drop the tenant policy on this table.
        op.drop_constraint("company_catalogues_pkey", "company_catalogues",
                           type_="primary")
        op.create_primary_key("company_catalogues_pkey", "company_catalogues",
                              ["organization_id", "connection_id", "catalogue_key"])
        op.alter_column("company_catalogues", "built_at",
                        existing_type=sa.DateTime(timezone=True), nullable=True)
        op.drop_column("company_catalogues", "pack")
    else:
        with _quiet_batch_rekey(), op.batch_alter_table(
                "company_catalogues", recreate="always") as batch:
            batch.alter_column("built_at", existing_type=sa.DateTime(timezone=True),
                               nullable=True)
            batch.drop_column("pack")
            batch.create_primary_key(
                "pk_company_catalogues",
                ["organization_id", "connection_id", "catalogue_key"])

    # A file decoded before this migration was decoded through the company's
    # recorded choice: carried onto each live file as its own decoding config,
    # confirmed by the migration — a recorded decision, never a default.
    for connection_id, chosen in _recorded_choices(bind).items():
        bind.execute(sa.text(
            "UPDATE company_corpora SET rule_set = :rule_set, "
            "decoding_confirmed_at = CURRENT_TIMESTAMP, "
            "decoding_confirmed_by = 'migration' "
            "WHERE connection_id = :cid AND superseded_at IS NULL"),
            {"rule_set": chosen, "cid": connection_id})

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
            "(organization_id, connection_id, catalogue_key, name, "
            " records, rows_read, quarantined) "
            "VALUES (:org, :cid, :key, '', 0, 0, 0)"),
            {"org": org, "cid": cid, "key": DEFAULT_KEY})


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
        op.add_column("company_catalogues", sa.Column(
            "pack", sa.String(255), nullable=False, server_default=""))
        op.drop_column("company_catalogues", "name")
        op.drop_column("company_catalogues", "catalogue_key")
    else:
        with _quiet_batch_rekey(), op.batch_alter_table(
                "company_catalogues", recreate="always") as batch:
            batch.alter_column("built_at", existing_type=sa.DateTime(timezone=True),
                               nullable=False)
            batch.add_column(sa.Column("pack", sa.String(255), nullable=False,
                                       server_default=""))
            batch.drop_column("name")
            batch.drop_column("catalogue_key")
            batch.create_primary_key("pk_company_catalogues",
                                     ["organization_id", "connection_id"])

    for name, _ in reversed(CORPUS_COLUMNS):
        op.drop_column("company_corpora", name)
    op.drop_column("company_corpora", "catalogue_key")
