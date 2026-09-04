"""The vendor's own credential, so the console is not a flag on a tenant login.

PIE operates this platform through five command-line tools — the enquiry queue,
plan grants, provisioning, the demo workspace, the syncs. They work, and they
are unusable by anyone who is not holding an SSH session, which is the whole
reason a console is being built.

A console needs an identity, and the cheap version of that identity is a
boolean on ``users``. This table exists because that version is wrong here.
Every control in this codebase is downstream of ``Principal.organization_id``
being set: the cost and margin withholding, the scope checks, and row-level
security itself. A tenant-bypassing capability on the same table and the same
verification path as customer logins means one code path that sometimes returns
a tenant-bound caller and sometimes does not, told apart by a nullable column —
which is the shape of every "the check was there, on the other branch" defect
recorded in CLAUDE.md §1.

So the credential is separate at every level: its own table, its own prefix
(``pieop_``), its own verifier, and no path from a customer credential to a row
here.

**This table grants no reading inside a tenant, and that is not a promise but a
mechanism.** The 58 policied tables are fail-closed under ``d1rls``–``d5rls``:
a connection that has not announced a tenant sees nothing, by SQL's
three-valued logic rather than by a check somebody wrote. An operator therefore
cannot read a customer's rows without ``tenancy.set_tenant``, and the console
calls that only after ``trust/access.record_use`` has asserted a live
break-glass grant — whose justification the customer can read on their own
endpoint. ``organizations`` and ``contact_requests`` carry no policy and are
readable without one, which is correct: an organization's name and plan are the
vendor's own billing metadata, and an enquiry arrived before there was a tenant
at all.

``trust/access`` was written for exactly this and had no production caller.
This is the revision that gives it one.

One table, no columns changed elsewhere, nothing backfilled. A database that
has run this serves the previous code unchanged.

Revision ID: o1op
Revises: n1dec
Create Date: 2026-09-04
"""
import sqlalchemy as sa
from alembic import op

revision = "o1op"
down_revision = "n1dec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns written out literally rather than imported from
    # ``app.domain.models`` (§4): this runs against schemas from months ago and
    # the models describe today.
    op.create_table(
        "operator_keys",
        sa.Column("key_id", sa.String(64), primary_key=True),
        sa.Column("operator_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("secret_hash", sa.String(256), nullable=False),
        sa.Column("secret_hint", sa.String(8), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_operator_keys_operator_id", "operator_keys", ["operator_id"])
    op.create_index("ix_operator_keys_created_at", "operator_keys", ["created_at"])
    # No row-level security policy, and the omission is the decision rather
    # than an oversight: a policy keys on ``organization_id``, this table has
    # none, and it is not a tenant's data. It sits beside ``zoho_credentials``,
    # ``process_leases`` and ``contact_requests`` as the fourth unscoped table —
    # which `test_the_unscoped_tables_are_the_ones_we_named` asserts by name, so
    # a fifth has to be somebody's decision rather than a discovery.


def downgrade() -> None:
    op.drop_index("ix_operator_keys_created_at", table_name="operator_keys")
    op.drop_index("ix_operator_keys_operator_id", table_name="operator_keys")
    op.drop_table("operator_keys")
