"""A price list may carry its own frozen decoder instead of a shipped rule set.

``m1cats`` gave every price list a decoding config and removed the default
pack: a file is decoded through the columns and the *rule set* its config
names, and a file with no config is not decoded at all. The rule set is still
one of pie-parser's shipped org-layer packs, so "no default" was true and "no
shipped grammar" was not.

This adds the other decode path. A config may instead name a **decoder built
for that file and no other** — inferred from the file's own descriptions,
its captured groups named where the text settles them and where a person
confirmed a proposal, and frozen content-addressed so decoding is a pure
function of the file's bytes and the artifact.

``company_corpora.decoder`` — the frozen artifact as JSON: its schema version,
its decimal convention, and its segments with their patterns, bindings,
examples and counterexamples. Nullable, and null on every existing row: a file
decoded before this migration was decoded through a rule set, and inventing a
decoder for it would be a decoding decision taken by a migration.

``company_corpora.decoder_id`` — that artifact's content id, sixteen hex
characters, denormalised so a screen can name the decoder without loading and
re-hashing it.

Exactly one of ``decoder`` and ``rule_set`` is set on a confirmed config, and
that is enforced in ``catalog.confirm_decoding`` rather than by a check
constraint. Deliberately: the two are also both null on an *unconfirmed* row,
which is the state every upload starts in, so the invariant is "confirmed
implies exactly one" — a conditional a portable check constraint expresses
badly, and one whose violation must produce the sentence saying which file is
wrong rather than an integrity error.

No backfill and nothing to reconcile: two nullable columns, added.

Columns written out literally rather than imported from ``app.domain.models``
(§4): this runs against schemas from months ago, and models describe today.
"""
from alembic import op
import sqlalchemy as sa

revision = "n1dec"
down_revision = "m1cats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_corpora", sa.Column("decoder", sa.JSON(), nullable=True))
    op.add_column("company_corpora",
                  sa.Column("decoder_id", sa.String(length=16), nullable=True))


def downgrade() -> None:
    # Dropping these loses the artifacts, and a catalogue built through one can
    # then not be rebuilt — so the rows a downgrade would strand are the ones
    # whose config named a decoder. That is the accepted cost of a downgrade
    # here: the built JSONL is a cache and the *corpus* survives, so what is
    # lost is the decoding decision, which a person made once and can make
    # again. Nothing else refers to these columns.
    op.drop_column("company_corpora", "decoder_id")
    op.drop_column("company_corpora", "decoder")
