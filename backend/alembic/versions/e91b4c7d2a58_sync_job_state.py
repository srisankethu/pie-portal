"""sync runs carry live job state, not just an outcome

``sync_runs`` recorded what a pull had done once it was over. A pull takes
minutes, so the row now has to exist and be readable *while* it happens:

    QUEUED -> RUNNING -> OK | PARTIAL | FAILED

``phase`` is what the run is doing right now, in the words the screen shows.
``heartbeat_at`` is what separates a slow job from a dead one — a process
killed mid-pull cannot write its own failure, and without a heartbeat its row
would stay RUNNING forever and refuse every later sync as a duplicate.

Existing rows are historical and already finished. They get a null phase and,
for the heartbeat, their finish time — so nothing already in the table is ever
mistaken for a job still in flight.

Revision ID: e91b4c7d2a58
Revises: d7f2a51c8e34
Create Date: 2026-08-03
"""
import sqlalchemy as sa
from alembic import op


revision = 'e91b4c7d2a58'
down_revision = 'd7f2a51c8e34'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sync_runs", sa.Column("phase", sa.String(64)))
    op.add_column("sync_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    # What the finished run wants to report — cleared sample data, the metric
    # rebuild summary. These used to ride back on the POST response; the work
    # now happens after that response, so the row is the only place for them.
    op.add_column("sync_runs", sa.Column("notes", sa.JSON()))
    # A long pull is read in calendar slices; these are how far through the
    # requested window it has got. Zero on historical rows, which were never
    # sliced — the UI reads that as "no window breakdown", not "no progress".
    op.add_column("sync_runs", sa.Column("windows_total", sa.Integer(),
                                         server_default="0"))
    op.add_column("sync_runs", sa.Column("windows_done", sa.Integer(),
                                         server_default="0"))

    # Every existing row is a completed run. Giving the heartbeat their finish
    # time (falling back to their start) means the staleness check can never
    # read one of them as active, whatever their status column says.
    op.execute(sa.text(
        "UPDATE sync_runs SET heartbeat_at = COALESCE(finished_at, started_at)"))


def downgrade() -> None:
    op.drop_column("sync_runs", "windows_done")
    op.drop_column("sync_runs", "windows_total")
    op.drop_column("sync_runs", "notes")
    op.drop_column("sync_runs", "heartbeat_at")
    op.drop_column("sync_runs", "phase")
