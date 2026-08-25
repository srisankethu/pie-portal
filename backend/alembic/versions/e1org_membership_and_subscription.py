"""The organization becomes the tenant in its own right: memberships and subscriptions.

Two tables, and everything else in this revision is moving what already exists
into them without losing a row.

**``organization_memberships``.** Access used to be two columns on ``users``:
an ``organization_id`` and a ``role``. Every existing user therefore *is* a
membership already — this creates the row that says so, with the role they
hold and a status taken from whether their account is active. Nobody gains
access and nobody loses it; the same grants are simply written where they can
have a second one, a history and an end.

**``organization_subscriptions``.** The plan lived on ``organizations.plan``
and the trial lived in ``intelligence_trials``, keyed on the *connected books*
rather than on the customer. The backfill puts both on one row per
organization:

- An organization with a plan above free is ACTIVE on it, dated from when the
  organization was created — the closest honest answer available, and stated
  rather than left null so "how long have they been a customer" has a value.
- An organization with a live books-keyed trial keeps it: TRIALING, with that
  trial's own ``started_at`` and ``ends_at`` carried over, so nobody's month is
  shortened or restarted by this migration.
- An organization with a spent trial is EXPIRED, with the dates kept. It could
  have been left null, and that would have made "your trial ended on the 3rd"
  unsayable for every existing tenant — which is exactly the silence the new
  trial notice exists to end.
- Everything else is EXPIRED with no dates: the old always-free tenants, who
  never had the decision layer and do not acquire one here. **They lose
  nothing**, because free never included it.

``organizations.plan`` and ``users.role`` are deliberately **not dropped**.
They are written alongside the new tables by one function each and read by
nothing that authorizes, so a rollback to the previous deployment finds the
rows it expects. Dropping them is a later revision, once this one has run.

**Row-level security.** Both new tables go under the tenant policy. ``users``
keeps its existing policy untouched and gains a **second, SELECT-only one**, so
that a tenant can read the identity row of somebody it holds a membership for —
a person in two workspaces has that row filed under one of them, and the
other's members screen could otherwise not print their name.

Splitting the read from the write is the whole of the care here, and it was
learned the hard way: widening the single existing policy also widened which
rows an UPDATE could select, and its ``WITH CHECK`` could not object to
re-homing the row to the announced tenant, because that is precisely the value
it tests for. One tenant could take another's account. Two policies leave
writes consulting only the narrow one.

``app_user_memberships`` joins ``app_login_lookup`` as the second SECURITY
DEFINER function, and is narrower than it: one user id in, that user's own
active memberships out. The caller has already been authenticated as that user.

Revision ID: e1org
Revises: d4rls
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision = "e1org"
down_revision = "d4rls"
branch_labels = None
depends_on = None

#: Written out literally rather than imported from ``app.tenancy`` (§4): this
#: runs against schemas from months ago, and the module describes today.
GUC = "app.current_org"
POLICY = "tenant_isolation"
NEW_TABLES = ("organization_memberships", "organization_subscriptions")

#: One identity's own workspaces, across tenants. The switcher's lookup.
_USER_MEMBERSHIPS = """
CREATE OR REPLACE FUNCTION app_user_memberships(p_user_id text)
RETURNS TABLE (organization_id text, organization_name text, role text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT m.organization_id, o.name, m.role
    FROM public.organization_memberships AS m
    JOIN public.organizations AS o
      ON o.organization_id = m.organization_id
    WHERE m.user_id = p_user_id AND m.status = 'ACTIVE'
    ORDER BY m.created_at;
$$;
"""

#: A **second, SELECT-only** policy on ``users``, added beside the existing
#: ``tenant_isolation`` rather than replacing it.
#:
#: What it solves: a person can belong to two organizations while their identity
#: row is filed under one of them, and under the original policy the *other*
#: organization's members list could not print their name.
#:
#: Why it is a second policy rather than a widened first one — which is what
#: this revision originally did, and which was wrong. A single policy's
#: ``USING`` governs reads *and* which rows an UPDATE may select, so widening it
#: to admit a foreign member made that member's row updatable by the tenant that
#: could see them; and the ``WITH CHECK`` could not object, because re-homing
#: the row to the announced tenant is exactly the value it tests for. Any
#: organization could take an account belonging to another.
#: ``test_a_foreign_member_is_visible_but_not_writable`` is that hole, pinned.
#:
#: Permissive policies of the same command are OR'ed, so reads see
#: ``own OR member`` while writes still consult only ``tenant_isolation``, which
#: is left exactly as ``d3rls`` wrote it — narrow on both halves. The widening
#: is therefore exactly as wide as the sentence describing it.
POLICY_MEMBER_READ = "tenant_member_readable"

_USERS_MEMBER_READ = f"""
CREATE POLICY {POLICY_MEMBER_READ} ON users
FOR SELECT
USING (
    EXISTS (
        SELECT 1 FROM public.organization_memberships AS m
        WHERE m.user_id = users.user_id
          AND m.organization_id = current_setting('{GUC}', true)
    )
)
"""


def upgrade() -> None:
    op.create_table(
        "organization_memberships",
        sa.Column("membership_id", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(64),
                  sa.ForeignKey("organizations.organization_id"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.user_id"),
                  nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="ACTIVE"),
        sa.Column("invited_by_user_id", sa.String(64)),
        sa.Column("role_changed_by_user_id", sa.String(64)),
        sa.Column("role_changed_at", sa.DateTime(timezone=True)),
        sa.Column("removed_by_user_id", sa.String(64)),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id",
                            name="uq_membership_org_user"),
    )
    op.create_index("ix_organization_memberships_organization_id",
                    "organization_memberships", ["organization_id"])
    op.create_index("ix_organization_memberships_user_id",
                    "organization_memberships", ["user_id"])
    op.create_index("ix_organization_memberships_status",
                    "organization_memberships", ["status"])
    op.create_index("ix_memberships_org_status", "organization_memberships",
                    ["organization_id", "status"])
    op.create_index("ix_memberships_user_status", "organization_memberships",
                    ["user_id", "status"])

    op.create_table(
        "organization_subscriptions",
        sa.Column("organization_id", sa.String(64),
                  sa.ForeignKey("organizations.organization_id"),
                  primary_key=True),
        sa.Column("plan", sa.String(32)),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="TRIALING"),
        sa.Column("trial_started_at", sa.DateTime(timezone=True)),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True)),
        sa.Column("subscription_started_at", sa.DateTime(timezone=True)),
        sa.Column("trial_ended_reason", sa.Text(), nullable=False,
                  server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_organization_subscriptions_status",
                    "organization_subscriptions", ["status"])

    _backfill()

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has no policies. A no-op is the honest form of that; see
        # ``d1rls`` for what it costs and why it is stated rather than emulated.
        return

    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table} "
            f"USING (organization_id = current_setting('{GUC}', true)) "
            f"WITH CHECK (organization_id = current_setting('{GUC}', true))")

    op.execute(f"DROP POLICY IF EXISTS {POLICY_MEMBER_READ} ON users")
    op.execute(_USERS_MEMBER_READ)

    op.execute(_USER_MEMBERSHIPS)
    # EXECUTE is granted to PUBLIC by default for a new function, which for a
    # SECURITY DEFINER one is worth closing and reopening deliberately.
    op.execute("REVOKE ALL ON FUNCTION app_user_memberships(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app_user_memberships(text) TO PUBLIC")


def _backfill() -> None:
    """Every existing user becomes a membership; every organization a subscription.

    Written as literal SQL against literal column names, never through the
    models: this runs on schemas from months ago and the models describe today
    (§4). ``uuid`` ids are generated in Python for the same reason — the
    function that makes them differs by dialect, and one of the two dialects
    here has none.
    """
    import uuid
    from datetime import datetime, timezone

    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    users = bind.execute(sa.text(
        "SELECT user_id, organization_id, role, active, created_at FROM users"
    )).all()
    for user_id, organization_id, role, active, created_at in users:
        if not organization_id:
            # A user with no organization has no grant to carry across. Left
            # alone rather than attached to a guess: an identity nobody can
            # place is exactly the row a migration must not invent access for.
            continue
        bind.execute(sa.text(
            "INSERT INTO organization_memberships "
            "(membership_id, organization_id, user_id, role, status, "
            " created_at, updated_at) "
            "VALUES (:mid, :oid, :uid, :role, :status, :created, :updated)"),
            {"mid": str(uuid.uuid4()), "oid": organization_id, "uid": user_id,
             "role": role or "SALESPERSON",
             # A deactivated account keeps an ACTIVE membership. The two answer
             # different questions — ``users.active`` is "can this person sign
             # in at all", the membership is "does this workspace admit them" —
             # and collapsing them here would silently end the grant of every
             # dormant account, which is not what anybody did.
             "status": "ACTIVE",
             "created": created_at or now, "updated": now})

    orgs = bind.execute(sa.text(
        "SELECT organization_id, plan, created_at FROM organizations")).all()
    for organization_id, plan, created_at in orgs:
        trial = bind.execute(sa.text(
            "SELECT started_at, ends_at FROM intelligence_trials "
            "WHERE organization_id = :oid ORDER BY started_at DESC"),
            {"oid": organization_id}).first()
        trial_started, trial_ends = (trial if trial is not None else (None, None))

        paid = (plan or "").strip().lower() in ("intelligence", "platform")
        if paid:
            status, started = "ACTIVE", (created_at or now)
        elif trial_ends is not None and _aware(trial_ends) > now:
            status, started = "TRIALING", None
        else:
            status, started = "EXPIRED", None

        bind.execute(sa.text(
            "INSERT INTO organization_subscriptions "
            "(organization_id, plan, status, trial_started_at, trial_ends_at, "
            " subscription_started_at, trial_ended_reason, created_at, updated_at) "
            "VALUES (:oid, :plan, :status, :tstart, :tend, :sstart, '', "
            "        :created, :updated)"),
            {"oid": organization_id, "plan": plan if paid else None,
             "status": status, "tstart": trial_started, "tend": trial_ends,
             "sstart": started, "created": created_at or now, "updated": now})


def _aware(value):
    """A stored timestamp, comparable against a tz-aware now.

    SQLite hands datetimes back naive, and comparing one against an aware
    ``now`` raises rather than returning a wrong answer — which would take the
    migration down mid-backfill. ``app.clock`` does this for the application;
    a migration may not import it (§4), so it is four lines here.
    """
    from datetime import timezone

    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def downgrade() -> None:
    """Drops the two tables and the read-widening on ``users``.

    The grants they hold are still in ``users.organization_id`` /
    ``users.role`` and ``organizations.plan``, which this revision deliberately
    did not drop — so stepping back loses the *history* (who invited whom, who
    was removed and when) and no live access.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS app_user_memberships(text)")
        # Only the read-widening goes: `tenant_isolation` was never touched, so
        # there is nothing to put back.
        op.execute(f"DROP POLICY IF EXISTS {POLICY_MEMBER_READ} ON users")
        for table in NEW_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")

    op.drop_index("ix_organization_subscriptions_status",
                  table_name="organization_subscriptions")
    op.drop_table("organization_subscriptions")
    for index in ("ix_memberships_user_status", "ix_memberships_org_status",
                  "ix_organization_memberships_status",
                  "ix_organization_memberships_user_id",
                  "ix_organization_memberships_organization_id"):
        op.drop_index(index, table_name="organization_memberships")
    op.drop_table("organization_memberships")
