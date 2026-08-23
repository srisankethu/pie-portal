"""One active sync run per connection, enforced by the database rather than a lock.

``jobs.start_sync`` reads the active runs and then inserts a new one, under a
``threading.Lock``. That lock is process-local and says so in its own comment;
the deployment runs two uvicorn workers (``deploy/backend.Dockerfile``,
``compose.yaml``). Two clicks landing on two workers both read "nothing
running" before either insert committed, both inserted, and ``run_job`` never
re-checks — so both pulls executed. Every invoice and bill fetched twice, two
jobs writing the same rows, and a progress counter that cannot say which job it
belongs to.

Only the database can exclude across processes. Two partial unique indexes do
it:

  * ``uq_sync_run_active_connection`` — at most one QUEUED/RUNNING run per
    (organization, connection), for the rows that name a connection.
  * ``uq_sync_run_active_umbrella`` — at most one QUEUED/RUNNING run per
    organization, for the rows that do not. ``connection_id`` is nullable and
    NULL is never equal to NULL, so a single index over the pair would let two
    all-companies runs both insert; this is the half that catches them.

Partial over the active rows only, so a finished run never blocks the next one
and the pull history stays in the table. Neither index constrains the other,
which is what keeps two connected companies pulling at once.

**The data fix, and why it comes first.** ``CREATE UNIQUE INDEX`` fails on a
database that already holds the duplicates this index exists to prevent — and
any deployment that has run two workers may. So the duplicates are closed
before the index is created: per group, the newest active row survives and the
older ones are closed the way ``jobs.active_run`` closes a run that stopped
reporting — ``FAILED``, ``phase`` cleared, ``finished_at`` set, and an ``error``
that says the rows it wrote were kept.

``FAILED`` rather than ``PARTIAL`` deliberately. A run reports ``PARTIAL`` when
it knows it wrote something (``report.wrote_anything``); a migration cannot know
that, and claiming partial success it has no evidence for is the benign default
CLAUDE.md §1 forbids. The reaper faces the same ignorance and answers the same
way.

``finished_at`` is ``COALESCE(heartbeat_at, started_at)`` rather than the
reaper's "now": the reaper closes a run seconds after it stopped reporting,
while this may run months later, and the last moment the run is known to have
been alive is the honest answer.

Revision ID: a7syncguard
Revises: z6subject
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = "a7syncguard"
down_revision = "z6subject"
branch_labels = None
depends_on = None

#: Written out literally, not imported from ``jobs.ACTIVE``: a migration runs
#: against schemas from months ago and must not depend on today's code (§4).
_ACTIVE = "status IN ('QUEUED', 'RUNNING')"

_CLOSED_ERROR = (
    "This sync overlapped another pull of the same books that was already in "
    "flight, and was closed when the database gained the index that now "
    "prevents the overlap. Anything it had already written was kept; running "
    "it again carries on from there.")

#: Close every active row in a group except the newest. The winner is the row
#: with the latest ``started_at`` (``sync_run_id`` breaks a tie, so the choice
#: is deterministic and a re-run picks the same row). The correlated subquery
#: reads the table it updates, which is safe here because the row it names is
#: the one row the UPDATE never touches.
_CLOSE = """
UPDATE sync_runs
   SET status = 'FAILED',
       phase = NULL,
       error = :error,
       finished_at = COALESCE(heartbeat_at, started_at)
 WHERE {active}
   AND {scope}
   AND sync_run_id <> (
        SELECT s2.sync_run_id
          FROM sync_runs AS s2
         WHERE s2.organization_id = sync_runs.organization_id
           AND {peer}
           AND s2.status IN ('QUEUED', 'RUNNING')
         ORDER BY s2.started_at DESC, s2.sync_run_id DESC
         LIMIT 1)
"""


def upgrade() -> None:
    bind = op.get_bind()
    # Per-connection duplicates first, then umbrella duplicates. Two statements
    # because the two groups are keyed differently — one on the pair, one on the
    # organization alone — and NULL cannot be compared with ``=``.
    bind.execute(
        sa.text(_CLOSE.format(
            active=_ACTIVE,
            scope="connection_id IS NOT NULL",
            peer="s2.connection_id = sync_runs.connection_id")),
        {"error": _CLOSED_ERROR})
    bind.execute(
        sa.text(_CLOSE.format(
            active=_ACTIVE,
            scope="connection_id IS NULL",
            peer="s2.connection_id IS NULL")),
        {"error": _CLOSED_ERROR})

    op.create_index(
        "uq_sync_run_active_connection",
        "sync_runs",
        ["organization_id", "connection_id"],
        unique=True,
        sqlite_where=sa.text(f"{_ACTIVE} AND connection_id IS NOT NULL"),
        postgresql_where=sa.text(f"{_ACTIVE} AND connection_id IS NOT NULL"),
    )
    op.create_index(
        "uq_sync_run_active_umbrella",
        "sync_runs",
        ["organization_id"],
        unique=True,
        sqlite_where=sa.text(f"{_ACTIVE} AND connection_id IS NULL"),
        postgresql_where=sa.text(f"{_ACTIVE} AND connection_id IS NULL"),
    )


def downgrade() -> None:
    # The indexes go; the rows the upgrade closed stay closed. Reopening them
    # would be worse than leaving them: nothing is executing those runs, so a
    # row put back to RUNNING is a dead job blocking every later sync until the
    # reaper's ten minutes elapse — exactly the state the reaper exists to
    # clear. A closed run is also re-runnable, which a wedged one is not.
    op.drop_index("uq_sync_run_active_umbrella", table_name="sync_runs")
    op.drop_index("uq_sync_run_active_connection", table_name="sync_runs")
