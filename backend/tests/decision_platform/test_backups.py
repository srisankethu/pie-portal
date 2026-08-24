"""A backup that exists, and a check that will not pretend one does.

`scripts/restore_drill.py` proves on every `make verify` that the procedure in
`docs/hosting.md` round-trips this schema — it dumps a database it built seconds
earlier, restores it, and compares every row, every money Σ and every audit
chain. What it has never been able to say is whether a backup *exists*. Nothing
in the deployment took one, and nothing in the deployment noticed.

This file pins the two halves of that gap, and both are the same §1 rule read in
different places:

* `scripts/backup.sh` must never leave behind a file that *looks* like a backup
  and is not one. A failed dump, a truncated dump and an interrupted dump all
  end with an empty directory rather than a fresh-looking `.sql.gz`, because the
  health check downstream trusts an mtime and that trust has to be earned by the
  writer.

* the `backups` health component must never report healthy on missing evidence.
  A production deployment with nothing configured, a directory that holds no
  dump, and a zero-byte file are three different failures with three different
  fixes, and none of them is "fine".

The tests are written against behaviour a mutation would break, not against the
message strings, because the messages are the part most likely to be reworded.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import time

import pytest

from app.config import settings

BACKUP_SH = (pathlib.Path(__file__).resolve().parents[3]
             / "scripts" / "backup.sh")

#: Incompressible, and comfortably over `BACKUP_MIN_BYTES` once gzipped. Zeros
#: would compress to a few dozen bytes and be refused by the size floor, which
#: is the floor working rather than a fixture problem — but it makes for a
#: confusing test, so the bytes here are random.
GOOD_DUMP = "head -c 200000 /dev/urandom"


def _run(directory: pathlib.Path, dump: str = GOOD_DUMP,
         **env: str) -> subprocess.CompletedProcess:
    """`backup.sh`, with its dump command replaced.

    `BACKUP_DUMP_CMD` exists for a deployment whose database is not a compose
    service; it is what makes the interesting half of this script — atomicity,
    verification and pruning — exercisable without a Postgres server.
    """
    return subprocess.run(
        ["bash", str(BACKUP_SH)], capture_output=True, text=True,
        env={**os.environ, "BACKUP_DIR": str(directory),
             "BACKUP_DUMP_CMD": dump, **env})


def _dumps(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(directory.glob("*.sql.gz"))


def _everything(directory: pathlib.Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@pytest.fixture()
def backups(tmp_path) -> pathlib.Path:
    directory = tmp_path / "backups"
    directory.mkdir()
    return directory


# ── the writer: nothing that is not a backup is left looking like one ────────
def test_a_dump_that_completes_lands_as_one_file_and_no_debris(backups):
    result = _run(backups)
    assert result.returncode == 0, result.stderr
    assert len(_dumps(backups)) == 1
    # No `.part`. The rename is what lets the health check downstream trust an
    # mtime instead of guessing whether a write was finished.
    assert _everything(backups) == [_dumps(backups)[0].name]


def test_a_failing_dump_leaves_the_directory_empty(backups):
    """`pipefail`, and the reason `docs/hosting.md` has insisted on it since the
    restore drill was written: without it a `pg_dump` that dies halfway exits 0
    and leaves a perfectly valid gzip archive of nothing."""
    result = _run(backups, dump="false")
    assert result.returncode == 3
    assert _everything(backups) == []


def test_a_truncated_dump_is_refused_rather_than_stored(backups):
    """Freshness alone would let this pass. A file too small to hold this
    schema's DDL is not a small backup, it is a truncated write."""
    result = _run(backups, dump="printf 'not a database'")
    assert result.returncode == 3
    assert _everything(backups) == []


def test_a_second_dump_never_overwrites_the_first(backups):
    """The procedure in `docs/hosting.md` named files with `date +%F`, so a
    second run in a day silently replaced the first — and the day somebody takes
    an extra dump is the day they are about to do something risky.

    Run back to back with no delay, so the stamps collide to the second. A
    timestamp finer than `%F` is not a fix for that, only a smaller version of
    it: `mv` over an existing file destroys it without a word, whatever the
    resolution of the name. The script picks a free name instead.
    """
    assert _run(backups).returncode == 0
    assert _run(backups).returncode == 0
    assert len(_dumps(backups)) == 2, "the first dump was overwritten"


# ── the retention policy, and the one value that would make it destructive ───
def test_a_dump_past_the_window_is_pruned_once_a_newer_one_exists(backups):
    assert _run(backups).returncode == 0
    old = _dumps(backups)[0]
    os.utime(old, (time.time() - 30 * 86400,) * 2)

    assert _run(backups, BACKUP_RETAIN_DAYS="14").returncode == 0
    survivors = _dumps(backups)
    assert len(survivors) == 1 and survivors[0] != old


def test_keeping_only_the_latest_never_means_keeping_none(backups):
    """`BACKUP_RETAIN_DAYS=0` is the natural way to spell "keep only the
    latest" and must not come out as "keep none"."""
    assert _run(backups).returncode == 0
    previous = _dumps(backups)[0]
    # Backdated rather than slept past: the point is that the file this run is
    # about to write survives, and an older one has to exist for the prune to
    # have anything to do.
    os.utime(previous, (time.time() - 3600,) * 2)

    assert _run(backups, BACKUP_RETAIN_DAYS="0").returncode == 0

    survivors = _dumps(backups)
    assert survivors == [p for p in survivors if p != previous], (
        "the aged-out dump should have been pruned")
    assert len(survivors) == 1, "the newest dump must survive its own prune"
    assert survivors[0].stat().st_size > 0


def test_pruning_never_touches_a_file_it_did_not_write(backups):
    """Only `pie-portal-*.sql.gz` is ever removed. A backup directory is
    somewhere an operator also drops a note to themselves, and a retention
    sweep that eats it teaches people not to use the directory."""
    stranger = backups / "READ-ME-BEFORE-RESTORING.txt"
    stranger.write_text("the 04-11 dump is the one from before the migration")
    os.utime(stranger, (time.time() - 400 * 86400,) * 2)

    assert _run(backups, BACKUP_RETAIN_DAYS="1").returncode == 0
    assert stranger.exists()


# ── the reader: no green check over an empty set ─────────────────────────────
@pytest.fixture()
def backup_check(monkeypatch):
    """The registered `backups` check, isolated from the global registry the
    way `test_process_lease` isolates the scheduler one."""
    from app.observability.health import health, register_health_checks

    saved = dict(health._components)
    health._components.clear()
    register_health_checks(object(), object())
    try:
        yield health._components["backups"].check_fn
    finally:
        health._components.clear()
        health._components.update(saved)


def _status(check):
    from app.observability.health import HealthStatus  # noqa: F401
    return check()[0]


def test_a_production_deployment_with_nowhere_to_look_is_not_healthy(
        backup_check, monkeypatch):
    """An unset `BACKUP_DIR` is not a statement that backups are handled
    elsewhere. It is the absence of one, and reading it as good news is the
    exact failure §1 records three times over."""
    from app.observability.health import HealthStatus

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "BACKUP_DIR", None)
    assert _status(backup_check) == HealthStatus.UNHEALTHY


def test_a_development_deployment_with_nowhere_to_look_is_healthy_and_says_why(
        backup_check, monkeypatch):
    """The counterweight, and the reason this check will still be read in a
    year: a development database is derived — a full sync rebuilds it from Zoho
    and `app.bootstrap` builds it from nothing — so amber here would be a light
    that is always on. `check_scheduler` reports a fixture source the same way.
    """
    from app.observability.health import HealthStatus

    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "BACKUP_DIR", None)
    status, message = backup_check()
    assert status == HealthStatus.HEALTHY
    assert "development" in (message or "")


def test_a_configured_directory_holding_no_dump_is_a_failure(
        backup_check, monkeypatch, backups):
    """Somebody said dumps land here and none do: the job has never once
    completed. That is not an unknown and it is certainly not a pass."""
    from app.observability.health import HealthStatus

    monkeypatch.setattr(settings, "BACKUP_DIR", backups)
    assert _status(backup_check) == HealthStatus.UNHEALTHY


def test_a_missing_directory_is_a_failure_not_an_absent_check(
        backup_check, monkeypatch, tmp_path):
    """Usually a volume that did not mount, which is exactly when a deployment
    most needs to be told."""
    from app.observability.health import HealthStatus

    monkeypatch.setattr(settings, "BACKUP_DIR", tmp_path / "never-mounted")
    assert _status(backup_check) == HealthStatus.UNHEALTHY


def test_an_empty_file_is_never_reported_as_a_backup(
        backup_check, monkeypatch, backups):
    """Fresh, correctly named, and zero bytes. Every check that looked only at
    the newest mtime would call this healthy."""
    from app.observability.health import HealthStatus

    (backups / "pie-portal-20260823T000000Z.sql.gz").write_bytes(b"")
    monkeypatch.setattr(settings, "BACKUP_DIR", backups)
    assert _status(backup_check) == HealthStatus.UNHEALTHY


def test_a_stale_backup_is_amber_and_a_fresh_one_is_green(
        backup_check, monkeypatch, backups):
    """Amber rather than red, and the distinction matters at three in the
    morning: a stale backup is still a backup."""
    from app.observability.health import HealthStatus

    dump = backups / "pie-portal-20260823T000000Z.sql.gz"
    dump.write_bytes(b"x" * (settings.BACKUP_MIN_BYTES + 1))
    monkeypatch.setattr(settings, "BACKUP_DIR", backups)
    assert _status(backup_check) == HealthStatus.HEALTHY

    os.utime(dump, ((time.time() - (settings.BACKUP_MAX_AGE_HOURS + 2) * 3600,)
                    * 2))
    assert _status(backup_check) == HealthStatus.DEGRADED


def test_the_age_comes_from_the_file_not_from_its_name(
        backup_check, monkeypatch, backups):
    """A date in a filename is written by whoever wrote the file and proves only
    that somebody typed it. `backup.sh` renames into place only after the
    archive verifies, so an mtime is the moment a *complete* dump landed."""
    from app.observability.health import HealthStatus

    lying = backups / "pie-portal-20991231T235959Z.sql.gz"
    lying.write_bytes(b"x" * (settings.BACKUP_MIN_BYTES + 1))
    os.utime(lying, ((time.time() - 90 * 86400,) * 2))
    monkeypatch.setattr(settings, "BACKUP_DIR", backups)
    assert _status(backup_check) == HealthStatus.DEGRADED


# ── the aggregation, where an unknown used to vanish ─────────────────────────
def test_a_component_that_cannot_be_judged_is_not_hidden_by_a_healthy_one():
    """`get_overall_status` used to end `if HEALTHY in statuses: return
    HEALTHY`, so one healthy component made the whole registry healthy while
    another sat at UNKNOWN. Nothing reached it while every registered check
    returned a real status, and it is the same shape as every defect §1 lists.
    """
    from app.observability.health import (ComponentHealth, HealthRegistry,
                                          HealthStatus)

    registry = HealthRegistry()
    for name, status in (("fine", HealthStatus.HEALTHY),
                         ("unreadable", HealthStatus.UNKNOWN)):
        component = ComponentHealth(name, status)
        registry._components[name] = component

    assert registry.get_overall_status() == HealthStatus.UNKNOWN


def test_a_known_impairment_still_outranks_an_unknown():
    """The ordering is the worst thing said: red, then amber, then "we cannot
    tell", then fine. An unknown must not mask a component that is reporting a
    real fault."""
    from app.observability.health import (ComponentHealth, HealthRegistry,
                                          HealthStatus)

    registry = HealthRegistry()
    for name, status in (("unreadable", HealthStatus.UNKNOWN),
                         ("slow", HealthStatus.DEGRADED),
                         ("fine", HealthStatus.HEALTHY)):
        registry._components[name] = ComponentHealth(name, status)

    assert registry.get_overall_status() == HealthStatus.DEGRADED


def test_the_newest_dump_survives_a_cutoff_that_would_sweep_everything(backups):
    """The unconditional guard, probed directly.

    A negative retention is not a supported setting; it is the only way to
    *schedule* the condition the guard exists for. The reachable version was a
    race — the cutoff was taken after the rename, so a second ticking over in
    that gap put the file the run had just written outside its own window with
    `BACKUP_RETAIN_DAYS=0`. That race is closed by fixing the cutoff before the
    dump, which makes this guard redundant rather than unnecessary, and this
    test is what stops the redundancy being quietly deleted as dead code: with
    a cutoff past every file on disk, exactly one still survives.

    Written after the first version of this file passed with the guard removed.
    """
    assert _run(backups).returncode == 0
    assert _run(backups, BACKUP_RETAIN_DAYS="-1").returncode == 0

    survivors = _dumps(backups)
    assert len(survivors) == 1, (
        "a cutoff past every file must still leave the newest one")
