"""A company's catalogue is built from several files, not one.

``h1cat`` gave a company one item-master export at a time: an upload superseded
whatever was there. That is the wrong shape for how the exports actually arrive
— an ERP item master, a manufacturer's range extension, a price list covering
products the master has not caught up with — so a company now keeps a *set* of
sources and the build merges them.

Five columns, and each of them exists because something could not be answered
without it.

``company_corpora.source_key`` — which source a row *is*, so a re-upload
replaces that one file and leaves the others alone. Nullable: every row written
before this reads as the unnamed source and behaves exactly as it did.

``company_corpora.mapping`` — which of that file's columns hold the record id,
the description and the grade. Before this, a file had to use the column names
the chosen pack declares or it was refused at upload, which made every export
other than the one the platform was written against unusable.

``company_corpora.ingest`` — what reading the file kept and what it dropped,
including which dropped columns were commercial. The catalogue is nomenclature
only; this is the evidence a person can check against their own spreadsheet
rather than a count they have to trust.

``company_catalogues.corpus_digest`` — a hash over the *set* of sources a build
read. With several files, "out of date" cannot be answered by comparing one
``corpus_id``: a source added or removed changes what a build would read while
leaving the newest id untouched, and the catalogue would have gone on reporting
itself current. Null on rows built before the column existed, where the
``corpus_id`` comparison is still honest.

``company_catalogues.sources`` and ``.ingest`` — which files this catalogue was
built from, and what merging them did, including the record ids that appeared in
more than one file. Those collisions are resolved in favour of the newest source
and counted: pie-parser's ``AuthoritativeIndex`` treats a duplicate identifier
inside one namespace as a collision that never resolves, so an unmerged
duplicate would silently stop a part number resolving at all.

Additive only. Every column is nullable or defaulted, no stored value changes,
and a database that has run this still serves the previous code — which is what
lets it deploy ahead of the application rather than in lockstep with it.

Revision ID: i1src
Revises: h2rls
Create Date: 2026-09-01
"""
import sqlalchemy as sa
from alembic import op

revision = "i1src"
down_revision = "h2rls"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.domain.models`` (§4):
#: this runs against schemas from months ago, and models describe today.
CORPUS_COLUMNS = (
    ("source_key", sa.String(128)),
    ("mapping", sa.JSON()),
    ("ingest", sa.JSON()),
)

CATALOGUE_COLUMNS = (
    ("corpus_digest", sa.String(64)),
    ("sources", sa.JSON()),
    ("ingest", sa.JSON()),
)


def upgrade() -> None:
    for name, type_ in CORPUS_COLUMNS:
        op.add_column("company_corpora", sa.Column(name, type_, nullable=True))
    for name, type_ in CATALOGUE_COLUMNS:
        op.add_column("company_catalogues", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in CATALOGUE_COLUMNS:
        op.drop_column("company_catalogues", name)
    for name, _ in CORPUS_COLUMNS:
        op.drop_column("company_corpora", name)
