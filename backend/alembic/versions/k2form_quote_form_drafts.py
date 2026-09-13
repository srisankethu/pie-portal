"""A quote form somebody has open, and which quote it became.

Pressing "New quote" wrote a `quote_drafts` row and minted `QB-0042` against it
before anybody had typed anything. Open the builder, look at it, navigate away,
and that number was spent and an empty quote sat on the whole desk's shared list
for good — the workspace filled up with quotes nobody had meant to start.

Two changes, and they are one lifecycle:

`quote_form_drafts` is where an unsaved form lives. It is not a quote: no
number, no sequence, no reference, in no listing, and nothing downstream keys on
it. The scratch is on the server rather than in the browser because the
builder's work is server-side — an RFQ is resolved against the decoded
catalogue, lines are priced from the connected book — and `store.Line.to_state`
carries cost while the projection withholds it from a salesperson. A
browser-held draft would either hand over every line's cost or hold none at all,
and CLAUDE.md §1 refuses both. See `models.QuoteFormDraft`.

`quote_drafts.form_draft_id` is the other half, and it is what makes Save
idempotent. A quote records which form it came from, uniquely, so a second click
or a retried request finds the quote already made instead of minting a second
number for the same work. Two racing saves cannot both win the constraint; the
loser re-reads and answers with the same quote.

Org-scoped, so the new table joins the `tenant_isolation` policy list here in
the migration that creates it rather than in a later sweep — the standing rule
that a new table outside the policy list is isolated only by Python.

Revision ID: k2form
Revises: j1doc
Create Date: 2026-09-12
"""
import sqlalchemy as sa
from alembic import op

revision = "k2form"
down_revision = "j1doc"
branch_labels = None
depends_on = None

TABLE = "quote_form_drafts"
QUOTES = "quote_drafts"
POLICY = "tenant_isolation"
GUC = "app.current_org"


def upgrade() -> None:
    # The columns are written out literally rather than imported from the
    # model: this runs against schemas from months ago and models describe
    # today (CLAUDE.md §4).
    op.create_table(
        TABLE,
        sa.Column("form_draft_id", sa.String(length=64), primary_key=True),
        sa.Column("organization_id", sa.String(length=64), nullable=False),
        # Whose form it is. A quote is the desk's and a colleague may pick it
        # up; a form is one person still typing, and there is nothing to
        # collaborate on until it has been saved.
        sa.Column("owner_user_id", sa.String(length=64), nullable=True),
        sa.Column("customer_id", sa.String(length=64), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=False,
                  server_default=""),
        sa.Column("connection_id", sa.String(length=64), nullable=True),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quote_form_drafts_organization_id", TABLE,
                    ["organization_id"])
    op.create_index("ix_quote_form_drafts_owner_user_id", TABLE,
                    ["owner_user_id"])

    # Nullable, and null for every quote that already exists as well as for
    # every quote made any way other than through the builder's form. Unique
    # where present, which is the constraint Save's idempotency rests on.
    op.add_column(QUOTES,
                  sa.Column("form_draft_id", sa.String(length=64), nullable=True))
    op.create_index("ix_quote_drafts_form_draft_id", QUOTES, ["form_draft_id"],
                    unique=True)

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Policies exist on PostgreSQL only. A no-op is the honest form of that
        # on SQLite rather than an emulation that would give the suite a false
        # sense of coverage — the same reasoning d1rls states at length.
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY} ON {TABLE} "
        f"USING (organization_id = current_setting('{GUC}', true)) "
        f"WITH CHECK (organization_id = current_setting('{GUC}', true))")


def downgrade() -> None:
    """Drops the form table and the column linking a quote to its form.

    Destroys unsaved forms, which is what they are: work nobody had saved.
    Saved quotes are untouched — losing `form_draft_id` costs the idempotency
    key for a save that has already happened, and nothing else.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
    op.drop_index("ix_quote_drafts_form_draft_id", table_name=QUOTES)
    op.drop_column(QUOTES, "form_draft_id")
    op.drop_index("ix_quote_form_drafts_owner_user_id", table_name=TABLE)
    op.drop_index("ix_quote_form_drafts_organization_id", table_name=TABLE)
    op.drop_table(TABLE)
