#!/usr/bin/env python3
"""Run the documented backup procedure end to end, and check what came back.

    ./scripts/restore_drill.py            # provisions its own disposable server
    PG_VERIFY_URL=... ./scripts/restore_drill.py

``docs/hosting.md`` tells an operator to ``pg_dump`` this database, restore the
dump into an empty one, and re-run the sync. That instruction had never been
executed by anything. An untested backup is a hypothesis about a file, and the
half of this database that a re-sync cannot rebuild is exactly the half nobody
would find out about until they needed it.

So: migrate an empty database, seed it with representative data **including
platform state**, run the runbook's own two commands against it, and compare the
restored database against the source.

**What it proves.** That the procedure in ``docs/hosting.md`` round-trips *this
schema* with its data intact: every table's row count, every ``Decimal`` money
column's exact Σ, every row compared column-for-column, every audit chain still
verifying under ``trust/audit.verify`` with the same head hash, and every erasure
receipt still verifying under ``trust/erasure.verify_receipt``.

**What it does not prove, and it is a short list worth reading.** Nothing about
any *particular* production backup — this drill dumps a database it made
seconds earlier, so a dump that is corrupt, truncated, or of the wrong database
is outside what it can see. Nothing about the dump being stored anywhere
durable, or off the host it backs up. Nothing about how long a real restore
takes at production volume: the seed here is a few hundred rows, chosen so the
drill costs the gate seconds rather than minutes, so it measures the procedure
and not the clock. And it says nothing about the read model being *current* —
the runbook's third step, re-running the sync, is not exercised here.

The audit chain is the assertion that matters most, and it is why this runs on
every gate rather than once. Entries are HMAC-linked and anchored in
``audit_chain_heads``; a restore that brings the chain back in a state which
fails ``verify`` is indistinguishable, from the operator's chair, from someone
having altered the log. The chain has one known way to fail on a round trip that
is not tampering at all — ``timestamptz`` renders in the session's TimeZone, so
a server set to Asia/Kolkata would hash every entry differently
(both signers normalise through ``trust/signing.utc_iso`` for exactly this
reason).

This drill **found** that defect and does not keep it fixed, and the difference
matters. It runs both databases on one server under one TimeZone, so a signer
that stopped normalising would still round-trip consistently here — and CI runs
under UTC, where the bug cannot appear at all. What it caught was the *already
broken* signer, on a developer machine that was not UTC:
``erasure.receipt_body`` still used ``clock.iso`` after ``audit.covered_body``
had been fixed, so an erasure receipt — the proof of deletion handed to a
departing customer — reported itself altered.

What keeps it fixed is a pair of unit tests that shift the offset explicitly and
assert the body is unchanged, in ``test_audit_chain.py`` and
``test_trust_controls.py``. They fail on any host, in any zone. A drill that
happens to be run somewhere non-UTC is a smoke alarm, not a guarantee.

Exit 0 pass, 1 a real failure, 3 skipped for want of a PostgreSQL server.

No money *value* is ever printed. Several of the columns summed here are cost,
and CLAUDE.md §1 keeps cost out of anything a gate writes to a terminal — a
mismatch names its column and says the Σs differ, which is all anyone needs to
start looking.
"""
from __future__ import annotations

import gzip
import logging
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

# One definition of "the data survived", shared with migrate_to_postgres.py.
from dbcompare import decimal_sum, money_columns, normalise  # noqa: E402

SOURCE_DB = "pie_drill_source"

#: Below this, the seed did not run and the comparison proves nothing. Set from
#: what the seed actually produces (79 rows) with room to shrink, not from a
#: round number: a floor above the real figure fails the gate for no fault, and
#: one at zero is the check not existing.
MIN_SEEDED_ROWS = 40
RESTORED_DB = "pie_drill_restored"

# A second and third tenant, so "every organization that has a chain" is a claim
# about more than one. The erased tenant is separate on purpose: issuing its
# receipt destroys its data key, which must not touch the seeded book.
SECOND_ORG = "org_drill_second"
ERASED_ORG = "org_drill_erased"


class Skip(RuntimeError):
    """No PostgreSQL to run against. Narrowed, never silently passed."""


# ── the server, and the two databases on it ─────────────────────────────────
def _pg_bin() -> Path:
    """Where ``pg_dump`` and ``psql`` live — the versioned pair, not a wrapper.

    ``pg_sandbox.sh bin`` first, because on Debian ``/usr/bin/pg_dump`` is
    ``pg_wrapper``, which picks a cluster and can pick the wrong one. It looks
    for ``initdb``, so it finds nothing on a machine with only the *client*
    package installed — which is a perfectly good setup for a ``PG_VERIFY_URL``
    pointing at a server somewhere else, hence the fallback to PATH.
    """
    found = subprocess.run([str(REPO_ROOT / "scripts" / "pg_sandbox.sh"), "bin"],
                           capture_output=True, text=True).stdout.strip()
    if found:
        return Path(found)
    on_path = shutil.which("pg_dump")
    if on_path:
        return Path(on_path).parent
    raise Skip("no PostgreSQL client binaries (pg_dump) found")


def _client_args(url: URL) -> list[str]:
    """``-h/-p/-U`` for pg_dump and psql, from the SQLAlchemy URL."""
    host = url.query.get("host") or url.host or ""
    port = url.query.get("port") or (str(url.port) if url.port else "")
    args = ["-h", str(host)] if host else []
    if port:
        args += ["-p", str(port)]
    if url.username:
        args += ["-U", url.username]
    return args


def _recreate(admin_url: URL, name: str) -> None:
    """A genuinely empty database, however the last run ended.

    ``WITH (FORCE)`` (PostgreSQL 13+) so a connection left over from an
    interrupted run cannot wedge the next one — the drill's databases are its
    own and nobody else is ever legitimately in them.
    """
    engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def _drop(admin_url: URL, name: str) -> None:
    engine = create_engine(admin_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    except Exception:                                       # noqa: BLE001
        pass
    finally:
        engine.dispose()


# ── the data ────────────────────────────────────────────────────────────────
def _seed(url: URL) -> None:
    """Representative data, weighted towards what a re-sync cannot rebuild.

    The read model comes from ``app.demo.seed_demo`` — the repository's own
    representative book, so this drill has no second opinion about what a
    plausible row looks like — and it brings money columns (``sales_txns``,
    ``cost_records``), signals and decisions with it. Everything added on top is
    platform state: an audit chain per tenant, an approval a person raised, and
    an erasure receipt.

    Called directly rather than through ``bootstrap._seed_demo``, which catches
    every exception so that a demo-data problem cannot stop the app booting.
    Here a seeding failure must be loud — a drill that silently seeds nothing
    would compare two empty databases and pass.
    """
    from app.approvals import raise_request
    from app.authz import Principal
    from app.bootstrap import _alembic_config
    from app.demo import seed_demo
    from app.domain import models
    from app.domain.enums import ApprovalAuthority, ApprovalKind, Role
    from app.seed import ensure_org_and_users
    from app.trust import audit, erasure, keys

    from alembic import command
    command.upgrade(_alembic_config(str(url)), "head")

    engine = create_engine(url, future=True)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    session = maker()
    try:
        org = ensure_org_and_users(session)
        seed_demo(session)
        session.commit()

        owner = session.get(models.User, "usr_owner")
        principal = Principal(user_id=owner.user_id, organization_id=org,
                              role=Role.OWNER, name=owner.name, email=owner.email)

        raise_request(
            session, principal,
            kind=ApprovalKind.QUOTE_LINE_PRICE,
            subject_id="qt_drill_0001", subject_line_id="qln_drill_0001",
            subject={"quote_id": "qt_drill_0001", "product": "prd_dnmg"},
            title="Restore drill — a line held for approval",
            summary="Seeded so the drill covers a human action, not only derived rows.",
            required_authority=ApprovalAuthority.MANAGER,
            reason="drill", thresholds_version="ci_drill")
        session.commit()

        # Several entries, on more than one chain. `append` links and anchors
        # each one for real, so what is dumped is a genuine chain rather than
        # rows shaped like one.
        session.add(models.Organization(organization_id=SECOND_ORG,
                                        name="Restore drill, second tenant",
                                        currency="INR", country="IN", config={}))
        session.flush()
        for n in range(6):
            audit.append(session, organization_id=org, action=audit.POLICY_CHANGED,
                         actor=principal, subject_type="policy",
                         subject_id=f"pol_{n}", detail={"step": n},
                         thresholds_version="ci_drill")
        for n in range(3):
            audit.append(session, organization_id=SECOND_ORG,
                         action=audit.LOGIN_SUCCEEDED, detail={"step": n})
        session.commit()

        # An erasure receipt: the same class of claim as an audit entry — an
        # HMAC over a body read back from the row — and the other thing a
        # restore could quietly turn into "this was tampered with".
        session.add(models.Organization(organization_id=ERASED_ORG,
                                        name="Restore drill, erased tenant",
                                        currency="INR", country="IN", config={}))
        session.flush()
        keys.ensure_key(session, ERASED_ORG)
        erasure.erase(session, ERASED_ORG, reason="restore drill",
                      actor_user_id=owner.user_id)
        session.commit()
    finally:
        session.close()
        engine.dispose()


# ── what is true of a database ──────────────────────────────────────────────
def _facts(url: URL) -> dict[str, Any]:
    """Everything the comparison needs, read once from one database."""
    from app.db import Base
    from app.domain import models  # also populates Base.metadata
    from app.migration_state import inspect_database
    from app.trust import audit, erasure

    engine = create_engine(url, future=True)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                         future=True)
    session = maker()
    try:
        state = inspect_database(engine)
        rows: dict[str, list[dict[str, Any]]] = {}
        sums: dict[str, dict[str, Optional[Any]]] = {}
        for table in Base.metadata.sorted_tables:
            order = list(table.primary_key.columns) or list(table.columns)
            fetched = [normalise(dict(r)) for r in session.execute(
                select(table).order_by(*order)).mappings().all()]
            rows[table.name] = fetched
            sums[table.name] = decimal_sum(fetched, money_columns(table))

        orgs = [o for (o,) in session.execute(
            select(models.AuditChainHead.organization_id)
            .order_by(models.AuditChainHead.organization_id)).all()]
        chains = {org: audit.verify(session, org) for org in orgs}

        receipts = {r.organization_id: erasure.verify_receipt(r)
                    for r in session.scalars(
                        select(models.ErasureReceipt)
                        .order_by(models.ErasureReceipt.organization_id)).all()}

        return {"revision": state.current, "state": state.state, "rows": rows,
                "sums": sums, "chains": chains, "receipts": receipts}
    finally:
        session.close()
        engine.dispose()


def _last_line(stderr: str) -> str:
    """psql's final error line, with any COPY CONTEXT dropped.

    A failing COPY prints `CONTEXT:  COPY sales_txns, line 12: "..."` carrying
    the entire row, and several of these tables hold cost. The final `ERROR:`
    line says what broke without reproducing the data that broke it; the full
    text stays in the operator's own terminal, not in gate output.
    """
    errors = [ln for ln in (stderr or "").splitlines()
              if ln.startswith(("psql:", "ERROR:"))]
    return (errors[-1] if errors else "no ERROR line").strip()[:300]


def _compare(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Every way the restored database could differ, as a list of findings.

    Opens with a floor, and that is not defensive padding. Every assertion below
    is a comparison, and a comparison over two empty databases finds nothing
    wrong — so a seed that quietly wrote no rows would print a green gate line
    claiming the audit chain, the money sums and every row round-tripped.

    That is not hypothetical: ``demo.seed_demo`` returns early with
    ``{"note": "demo already seeded"}`` when it finds its first customer
    present, without raising. A drill that cannot tell "nothing was wrong" from
    "nothing was there" is the benign default §1 forbids, and it is the failure
    this whole exercise exists to catch one layer up.
    """
    problems: list[str] = []

    seeded = sum(len(r) for r in before["rows"].values())
    if seeded < MIN_SEEDED_ROWS:
        problems.append(
            f"the source database held {seeded} rows before the backup, below "
            f"the floor of {MIN_SEEDED_ROWS} — the seed did not run, so a "
            f"comparison over it proves nothing")
        return problems
    if not before["chains"]:
        problems.append("no audit chain was seeded — the assertion this drill "
                        "exists for would not have been made")
        return problems
    if not before["receipts"]:
        problems.append("no erasure receipt was seeded — its signature would "
                        "not have been checked across the restore")
        return problems

    if after["state"] != "CURRENT" or after["revision"] != before["revision"]:
        problems.append(
            f"the restored database is {after['state']} at "
            f"{after['revision']!r}; the source was CURRENT at "
            f"{before['revision']!r}")

    for name, src_rows in before["rows"].items():
        dst_rows = after["rows"].get(name)
        if dst_rows is None:
            problems.append(f"{name}: the table is not in the restored database")
            continue
        if len(src_rows) != len(dst_rows):
            problems.append(
                f"{name}: {len(src_rows)} rows before, {len(dst_rows)} after")
            continue
        for i, (a, b) in enumerate(zip(src_rows, dst_rows)):
            if a != b:
                differing = sorted(k for k in a if a[k] != b.get(k))
                problems.append(f"{name}: row {i} differs in {differing}")
                break
        # Named, never printed: a money Σ is cost on several of these tables.
        for column, total in before["sums"][name].items():
            if after["sums"][name].get(column) != total:
                problems.append(f"{name}.{column}: the Σ does not match")

    for org, verdict in before["chains"].items():
        if not verdict.get("ok"):
            problems.append(
                f"the audit chain for {org} did not verify BEFORE the backup — "
                f"the drill's own seed is wrong, and a restore cannot be blamed "
                f"for it")

    # The chain. A restored chain that fails to verify reads as tampering.
    for org, verdict in before["chains"].items():
        restored = after["chains"].get(org)
        if restored is None:
            problems.append(f"audit chain for {org} has no anchor after restore")
            continue
        if not restored["ok"]:
            # The finding this whole drill exists for: an operator restoring
            # from backup being told their audit log has been altered.
            broke = restored["first_break"]
            # ``verify`` spells the prose key two ways — ``explanation`` from
            # ``_break``, ``detail`` on the TRUNCATED paths. Nothing else reads
            # either, so this reads both rather than dictating to a signed
            # module which one it should have been.
            why = broke.get("explanation") or broke.get("detail") or ""
            problems.append(
                f"audit chain for {org} does NOT verify after restore — "
                f"{broke['kind']} at seq {broke['seq']}: {why}")
        elif restored["head_hash"] != verdict["head_hash"]:
            problems.append(f"audit chain for {org} verifies but its head hash moved")
        elif restored["entries"] != verdict["entries"]:
            problems.append(
                f"audit chain for {org}: {verdict['entries']} entries before, "
                f"{restored['entries']} after")
    for org in set(after["chains"]) - set(before["chains"]):
        problems.append(f"audit chain for {org} appeared during the restore")

    for org, ok in before["receipts"].items():
        if not ok:
            problems.append(f"erasure receipt for {org} did not verify BEFORE "
                            f"the backup — the drill's own seed is wrong")
        elif not after["receipts"].get(org):
            problems.append(f"erasure receipt for {org} does not verify after restore")

    return problems


# ── the runbook's own two commands ──────────────────────────────────────────
def _run_documented_procedure(bin_dir: Path, source: URL, restored: URL,
                              archive: Path) -> dict[str, Any]:
    """``pg_dump | gzip``, then ``gunzip -c | psql`` — as ``docs/hosting.md`` says.

    Run through ``bash -c`` so what executes is the runbook's shell pipeline and
    not a Python paraphrase of it. The two additions over what the doc used to
    say are in the doc now as well, because both were ways for a broken backup
    to report success:

    ``set -o pipefail``  a pipeline's status is its *last* command's, so
                         ``pg_dump | gzip`` exits 0 when pg_dump dies and leaves
                         a perfectly valid archive of nothing.
    ``ON_ERROR_STOP=1``  psql's default is to log a failed statement and carry
                         on, so a restore that dropped half the tables exits 0.
    """
    env = dict(os.environ)
    if source.password:
        env["PGPASSWORD"] = source.password

    def sh(script: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", "set -o pipefail; " + script],
                              capture_output=True, text=True, env=env)

    dump_cmd = (f"{shlex.quote(str(bin_dir / 'pg_dump'))} "
                f"{' '.join(shlex.quote(a) for a in _client_args(source))} "
                f"{shlex.quote(source.database)} | gzip > {shlex.quote(str(archive))}")
    t0 = time.perf_counter()
    dumped = sh(dump_cmd)
    dump_seconds = time.perf_counter() - t0
    if dumped.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {dumped.stderr.strip()[:2000]}")

    restore_cmd = (
        f"gunzip -c {shlex.quote(str(archive))} | "
        f"{shlex.quote(str(bin_dir / 'psql'))} -v ON_ERROR_STOP=1 --quiet "
        f"{' '.join(shlex.quote(a) for a in _client_args(restored))} "
        f"-d {shlex.quote(restored.database)}")
    t0 = time.perf_counter()
    restored_run = sh(restore_cmd)
    restore_seconds = time.perf_counter() - t0
    if restored_run.returncode != 0:
        raise RuntimeError(
            "psql restore failed. Its stderr is deliberately NOT reproduced "
            "here: on a COPY failure psql prints a CONTEXT line carrying the "
            "whole offending row, and several of these tables hold cost. "
            f"Last line only: {_last_line(restored_run.stderr)}")
    # ON_ERROR_STOP covers a failed statement; this covers a *warning* that
    # means data did not arrive. Absence of a non-zero exit is not a pass.
    noise = [ln for ln in restored_run.stderr.splitlines()
             if "ERROR" in ln or "FATAL" in ln]
    if noise:
        raise RuntimeError("psql restored with errors:\n  " + "\n  ".join(noise[:20]))

    return {"dump_seconds": dump_seconds, "restore_seconds": restore_seconds,
            "archive_bytes": archive.stat().st_size,
            "sql_bytes": len(gzip.decompress(archive.read_bytes()))}


def drill(server_url: str, workdir: Path) -> tuple[list[str], dict[str, Any]]:
    bin_dir = _pg_bin()
    admin = make_url(server_url)
    source = admin.set(database=SOURCE_DB)
    restored = admin.set(database=RESTORED_DB)

    dump_dir: Optional[Path] = None
    _recreate(admin, SOURCE_DB)
    _recreate(admin, RESTORED_DB)
    try:
        t0 = time.perf_counter()
        _seed(source)
        seed_seconds = time.perf_counter() - t0

        before = _facts(source)
        # A private directory, removed on the way out whatever happens. The
        # archive is a complete dump of a tenant's database — names, documents,
        # the audit chain — and the first version of this wrote it to a fixed
        # world-readable path under /tmp and left it there after every run.
        # verify.sh's own step 5 uses mktemp for exactly this reason.
        dump_dir = Path(tempfile.mkdtemp(prefix="restore-drill-", dir=workdir))
        os.chmod(dump_dir, 0o700)
        timings = _run_documented_procedure(bin_dir, source, restored,
                                            dump_dir / "dump.sql.gz")
        after = _facts(restored)

        report = {**timings, "seed_seconds": seed_seconds,
                  "revision": before["revision"],
                  "tables": len(before["rows"]),
                  "non_empty": sum(1 for r in before["rows"].values() if r),
                  "rows": sum(len(r) for r in before["rows"].values()),
                  "money_columns": sum(len(c) for c in before["sums"].values()),
                  "chains": before["chains"], "receipts": before["receipts"]}
        return _compare(before, after), report
    finally:
        if dump_dir is not None:
            shutil.rmtree(dump_dir, ignore_errors=True)
        _drop(admin, SOURCE_DB)
        _drop(admin, RESTORED_DB)


def main() -> int:
    # Every entry point in this codebase calls ``logs.configure`` rather than
    # ``basicConfig``; alembic/env.py checks ``logs.is_configured()`` and leaves
    # logging alone when it is, which is the only reason the next line survives
    # the migration. Alembic narrating all 79 revisions at INFO is useful in a
    # bootstrap and noise here — on a failure it is this script's own report
    # that has to be readable.
    from app.observability import logs
    logs.configure()
    logging.getLogger("alembic").setLevel(logging.WARNING)
    started = time.perf_counter()
    workdir = Path(os.environ.get("TMPDIR", "/tmp"))
    sandbox = REPO_ROOT / "scripts" / "pg_sandbox.sh"
    server = os.environ.get("PG_VERIFY_URL", "")
    started_sandbox = False
    if not server:
        got = subprocess.run([str(sandbox), "start"], capture_output=True, text=True)
        server = got.stdout.strip()
        started_sandbox = bool(server)
    if not server:
        print("restore drill SKIPPED: no PostgreSQL server binaries and no "
              "PG_VERIFY_URL.", file=sys.stderr)
        return 3

    try:
        problems, report = drill(server, workdir)
    except Skip as exc:
        print(f"restore drill SKIPPED: {exc}", file=sys.stderr)
        return 3
    finally:
        if started_sandbox:
            subprocess.run([str(sandbox), "stop"], capture_output=True)

    print("── restore drill — docs/hosting.md, executed ────────────────────")
    print(f"{'revision':>18}: {report['revision']}")
    print(f"{'seeded':>18}: {report['rows']} rows across {report['non_empty']} "
          f"non-empty tables ({report['tables']} total) in "
          f"{report['seed_seconds']:.1f}s")
    print(f"{'dumped':>18}: {report['sql_bytes']} bytes of SQL, "
          f"{report['archive_bytes']} gzipped, in {report['dump_seconds']:.1f}s")
    print(f"{'restored':>18}: into an empty database in "
          f"{report['restore_seconds']:.1f}s")
    print(f"{'compared':>18}: every row column-for-column; "
          f"{report['money_columns']} Decimal money Σ (values never printed)")
    for org, verdict in sorted(report["chains"].items()):
        print(f"{'audit chain':>18}: {org} — {verdict['entries']} entries, "
              f"head {(verdict['head_hash'] or '')[:12]}…")
    for org in sorted(report["receipts"]):
        print(f"{'erasure receipt':>18}: {org}")
    if problems:
        print(f"{'FINDINGS':>18}:")
        for problem in problems:
            print(f"{'':>20}{problem}")
        print(f"{'result':>18}: FAILED — the documented procedure did not "
              f"round-trip this database")
        return 1
    print(f"{'result':>18}: PASS in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
