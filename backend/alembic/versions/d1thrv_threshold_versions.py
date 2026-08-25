"""Make a threshold stamp dereferenceable: threshold_versions.

Every computed row in this schema already carries a ``thresholds_version`` — a
truncated sha256 of the whole threshold dataclass — and until now none of them
could be turned back into a policy. An approval stamped ``ci_9f3a…`` proved it
had been judged under something other than today's policy and could not say
what. ``commercial_policies`` cannot answer it either: that row is mutable, an
edit overwrites it, it holds only the owner-editable half, and the
environment-derived half of the hash (window lengths, evidence floors,
``CI_RECENT_DAYS``) never passed through it at all.

This table holds the exact bytes that were hashed, so an answer is *verified*
by re-hashing rather than trusted. ``content_digest`` carries the untruncated
sha256 alongside the ten-hex ``version``, which is what makes a within-tenant
collision detectable instead of silently served.

The primary key is ``(organization_id, version)`` rather than ``version``
alone. ``first_seen_at`` only means something per organization: the earliest
sighting per tenant and kind is the epoch that separates "stamped before the
registry existed" from "a stamp reached a row without being recorded, which is
a defect in the recording path". A shared key would let a tenant onboarded last
week inherit another tenant's six-month-old epoch and report its own genuine
gaps as expected history. The stamp is already per-org in any case —
``policy._in_org_locale`` folds the organization's currency and timezone into
the hash. No ``ForeignKey`` on ``organization_id``, matching
``commercial_policies``: this is bookkeeping that rides inside somebody else's
business transaction and must never be able to fail one.

**This revision inserts nothing.** Backfilling "the current policy" from here
would be wrong twice over: a migration runs in a different process whose
``os.environ`` is not the application's, so the environment half of the hash it
computed could be a policy that was never in force anywhere; and a migration
must not import application models to compute it with. Today's version becomes
resolvable at the first boot of the code that ships with this revision, through
``threshold_registry`` — which is a sighting, and is recorded as one.

Columns are written out literally rather than imported from the models, per the
rule that a migration runs against a schema from months ago.

Revision ID: d1thrv
Revises: c9qdoc
Create Date: 2026-08-24
"""
from alembic import op
import sqlalchemy as sa

revision = "d1thrv"
down_revision = "c9qdoc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "threshold_versions",
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        # "commercial" | "signal". Derivable from the ci_/th_ prefix and stored
        # anyway: both real queries filter on it, and `_` is a LIKE wildcard.
        sa.Column("kind", sa.String(length=16), nullable=False),
        # json.dumps(asdict(th), sort_keys=True). Text, not JSON: re-hashing
        # must see the same byte sequence the stamp was taken over, and a
        # round-trip through a JSON encoder is free to reorder or respell.
        sa.Column("values_json", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        # A sighting, not an effective date.
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_via", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "version"),
    )
    # The primary key serves resolution; this serves the epoch and the history
    # listing.
    op.create_index("ix_threshold_version_org_kind_seen", "threshold_versions",
                    ["organization_id", "kind", "first_seen_at"])


def downgrade() -> None:
    # Dropping this loses the only record of what past stamps meant, and no
    # re-sync rebuilds it — the bytes came from a process environment that is
    # gone. Reversible in schema only, which is the honest thing to say here.
    op.drop_index("ix_threshold_version_org_kind_seen",
                  table_name="threshold_versions")
    op.drop_table("threshold_versions")
