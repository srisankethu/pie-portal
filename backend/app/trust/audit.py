"""The audit chain: who did what, when, under which policy — and provably in order.

The platform's central claim is that AI never computes a number: ``commercial/``
computes deterministically and stamps a content-hashed ``thresholds_version`` on
every row. That claim was unreconstructable. Six append-only surfaces existed and
none of them recorded a *principal acting*: ``state/events`` logs the sync's
reading of ERP documents with no actor column, ``attribution/ledger`` records
rupee outcomes actorless, ``ai/telemetry`` records calls without who asked,
``trust/access`` records break-glass and had no production caller at all. Nothing
answered "who saw this customer's economics, when, under which policy, and what
did the model say?", and audit coverage cannot be backfilled — every day without
it is a permanently unauditable day.

Three properties, and each one is here because its absence is what makes an audit
log decorative:

**Linked, so a row cannot be quietly rewritten.** Each entry's hash covers the
previous entry's hash. Editing one entry invalidates it and everything after it,
and ``verify`` names the first break. The hash is an HMAC keyed by the
deployment secret (``trust/signing``), so re-signing a doctored history needs
more than the database.

**Ordered by the database, not by a lock.** This is the design, and it is the
thing ``app/lease.py`` refuses to provide. Read its closing paragraph: there is
no fence token, so a leader can hold an unexpired claim while frozen and resume
after another worker has taken it, acting in the belief that it still leads. A
chain built on that forks silently. So the order is enforced where the write
lands — ``uq_audit_entry_org_seq`` — and a lease is not involved at all. Two
appenders racing for position N both compute ``head.seq + 1``; one INSERT
succeeds and the other raises ``IntegrityError``, re-reads the head and retries.
The same idiom ``a7syncguard`` used for one active sync run per connection, and
for the same reason: a database constraint fails loudly where a process-local
lock forks quietly.

**Atomic with what it describes.** ``append`` writes on the caller's session
inside a SAVEPOINT and does *not* commit. A conflict rolls back only the
savepoint, so the caller's own pending work survives the retry, and the entry
and the change it records reach disk in the same transaction or neither does.
That rules out both halves of the failure: an audit entry for a change that was
rolled back, and a change with no audit entry. When the retries are exhausted
``append`` raises, which fails the request and takes the unaudited change with
it — the right way round.

**What this must not become.** §1: cost and margin never reach a salesperson, and
this log carries values a salesperson may never see (a margin floor, a policy
transition). So the read surface is OWNER-only — the tightest gate on anything it
records, and the same gate the rest of ``routers/trust.py`` already uses — and
``detail`` never carries a line's cost or price. An approval entry records that a
below-cost sign-off happened and who signed it, never the number that made it
one. An audit read that answered a margin question would be exactly the
``filterCounts.MFLOOR`` defect wearing a compliance badge.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..domain import models
from . import signing

log = logging.getLogger("pie_portal.audit")

#: The genesis link. A literal rather than NULL: ``uq_audit_entry_org_prev``
#: has to refuse a second genesis entry, and NULL is not equal to NULL, so a
#: nullable column would let two chains start in one organization.
GENESIS = "0" * 64

#: How many positions to lose before giving up. Each loss means another appender
#: won the position we wanted, so exhausting this needs eight simultaneous
#: writers for one tenant — far past anything this deployment produces, and
#: bounded rather than a spin so a genuine constraint bug fails a request in
#: milliseconds instead of hanging one forever.
MAX_ATTEMPTS = 8


class AuditAppendFailed(RuntimeError):
    """The entry could not be placed, so the change it describes must not stand.

    Raised out of ``append`` rather than swallowed. Logging and continuing would
    produce the one outcome this module exists to prevent — a change nobody can
    attribute — and it would produce it silently, which is worse than a 500 that
    names the problem.
    """


# ── the vocabulary ──────────────────────────────────────────────────────────
#
# Plain constants rather than an enum class: these are stored as strings and
# read back from rows written by older code, and a value that stops resolving is
# a chain that stops verifying. New actions are added here so the set is
# greppable in one place; nothing validates against it at write time on purpose.

POLICY_CHANGED = "POLICY_CHANGED"
LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
LOGIN_FAILED = "LOGIN_FAILED"
SESSION_ENDED = "SESSION_ENDED"
SESSIONS_ENDED_ALL = "SESSIONS_ENDED_ALL"
APPROVAL_DECIDED = "APPROVAL_DECIDED"
APPROVAL_WITHDRAWN = "APPROVAL_WITHDRAWN"
AI_CALL = "AI_CALL"

SYSTEM = "SYSTEM"


# ── writing ─────────────────────────────────────────────────────────────────
def append(session: Session, *, organization_id: str, action: str,
           actor: Any = None, actor_user_id: Optional[str] = None,
           actor_label: str = SYSTEM, actor_role: str = "",
           subject_type: str = "", subject_id: Optional[str] = None,
           detail: Optional[dict[str, Any]] = None,
           thresholds_version: Optional[str] = None) -> models.AuditEntry:
    """Link one entry onto this organization's chain. Does **not** commit.

    Call it as the last act of the operation it describes, on the same session:
    the entry and the change then commit together, which is the property that
    makes the log worth reading. Nothing here calls ``session.commit`` — that
    stays the caller's decision, because it is the caller's transaction.

    ``actor`` takes the request's ``Principal`` and is how a router should say
    who acted; the three ``actor_*`` fields are for callers that hold only a
    user id (``commercial/policy.save_for_org``) or that genuinely act for no
    person. Passing the principal rather than unpacking it at every call site is
    not tidiness — a duplicate scan found the same three-line unpacking in four
    routers, which is exactly how one of them ends up reading ``name`` where the
    others read ``email`` and the log grows two ways of naming one person.

    Duck-typed, so ``trust/`` keeps its §3 property of importing nothing above
    it. ``authz`` is not a forbidden layer, but a package that imports the
    request-identity module to read three attributes off it has acquired a
    dependency for a type annotation's sake.

    Raises ``AuditAppendFailed`` if the position cannot be claimed within
    ``MAX_ATTEMPTS``. Let it propagate: the caller's transaction then rolls back
    and the unaudited change goes with it.
    """
    detail = dict(detail or {})
    if actor is not None:
        actor_user_id = actor_user_id or getattr(actor, "user_id", None)
        actor_label = (actor_label if actor_label != SYSTEM else
                       (getattr(actor, "email", None)
                        or getattr(actor, "name", None) or SYSTEM))
        role = getattr(actor, "role", None)
        actor_role = actor_role or getattr(role, "value", None) or (role or "")
    actor_label, actor_role = _named(session, actor_user_id, actor_label, actor_role)
    at = clock.now()
    source = _source()
    last_error: Optional[Exception] = None

    _open_the_transaction(session)

    for _attempt in range(MAX_ATTEMPTS):
        # A SAVEPOINT, not a transaction. A losing INSERT has to be undone
        # without touching whatever the caller has pending — rolling the whole
        # session back would discard the very change this entry is about, and
        # the retry would then record something that never happened.
        savepoint = session.begin_nested()
        try:
            head = _head(session, organization_id)
            seq = (head.seq + 1) if head is not None else 1
            prev_hash = head.entry_hash if head is not None else GENESIS

            row = models.AuditEntry(
                organization_id=organization_id, seq=seq, prev_hash=prev_hash,
                at=at, action=action, actor_user_id=actor_user_id,
                actor_label=(actor_label or SYSTEM)[:255],
                actor_role=actor_role or "", subject_type=subject_type or "",
                subject_id=subject_id, detail=detail,
                thresholds_version=thresholds_version, source=source)
            row.entry_hash = signing.sign(covered_body(row))

            session.add(row)
            # The anchor moves with the entry, inside the same savepoint. If it
            # were updated separately, a crash between the two would leave the
            # anchor behind the chain and every later verify would cry
            # TRUNCATED at a chain that is intact.
            _move_head(session, organization_id, seq, row.entry_hash)
            session.flush()
            savepoint.commit()
            return row
        except IntegrityError as exc:
            # Somebody else took this position between our read and our write.
            # An ordinary outcome and the mechanism working, not an error: the
            # database refused the fork, which is what it is there for.
            last_error = exc
            savepoint.rollback()

    log.error("audit append lost %d races for %s/%s", MAX_ATTEMPTS,
              organization_id, action)
    raise AuditAppendFailed(
        f"Could not place an audit entry for {action!r} after {MAX_ATTEMPTS} "
        f"attempts — another writer holds every position we tried. The change "
        f"this entry describes is being rolled back with it rather than "
        f"committed unaudited.") from last_error


def _move_head(session: Session, organization_id: str,
               seq: int, entry_hash: str) -> None:
    """Record where this tenant's chain now ends.

    Upserted by hand rather than with a dialect-specific ``ON CONFLICT``: the
    row is already inside the append's savepoint, and a losing race rolls this
    back with the entry it belongs to.
    """
    head = session.get(models.AuditChainHead, organization_id)
    if head is None:
        session.add(models.AuditChainHead(
            organization_id=organization_id, seq=seq,
            entry_hash=entry_hash, updated_at=clock.now()))
        return
    head.seq = seq
    head.entry_hash = entry_hash
    head.updated_at = clock.now()


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    """A timestamp as an ISO-8601 string in UTC, whatever offset it arrives in.

    The signature covers this string, so it must depend only on the instant and
    never on which machine or session rendered it.
    """
    if value is None:
        return None
    aware = clock.aware(value)
    return None if aware is None else aware.astimezone(timezone.utc).isoformat()


def covered_body(row: models.AuditEntry) -> dict[str, Any]:
    """The exact structure ``entry_hash`` covers — read from the row, to re-verify.

    Read back from stored columns rather than rebuilt from the arguments that
    made it, for the reason ``erasure.receipt_body`` gives: what a signature
    covers is a fact about the moment it was made, and a body reassembled from
    today's code would either change its story or stop verifying.

    ``entry_id`` is not in it. The identity of the row is not part of what the
    row *says*, and including a value nothing else references would make the
    hash depend on a UUID rather than on the history.
    """
    return {
        "organization_id": row.organization_id,
        "seq": int(row.seq),
        "prev_hash": row.prev_hash,
        # Normalised to UTC here rather than through ``clock.iso``, which
        # preserves whatever offset it is handed. Signing happens over
        # ``clock.now()`` (always +00:00); verification happens over a value
        # read back from the database, and psycopg renders a ``timestamptz`` in
        # the *session* TimeZone — which follows the server's. On a Postgres
        # whose TimeZone is Asia/Kolkata every entry would read back as
        # '...+05:30', hash differently, and report SIGNATURE on a chain nobody
        # had touched: the whole log failing closed for a server setting. The
        # instant is identical; only the rendering differs, so the fix belongs
        # at the signing boundary and not in ``clock.iso``, whose callers render
        # for display.
        "at": _utc_iso(row.at),
        "action": row.action,
        "actor_user_id": row.actor_user_id,
        "actor_label": row.actor_label,
        "actor_role": row.actor_role,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "detail": row.detail or {},
        "thresholds_version": row.thresholds_version,
        "source": row.source,
    }


def _open_the_transaction(session: Session) -> None:
    """Make sure a real transaction is running before a SAVEPOINT is taken.

    Found by a test, and it is not a nicety. Python's ``sqlite3`` driver decides
    for itself when to emit ``BEGIN``, and it emits one before ``INSERT`` and
    friends but **not** before ``SAVEPOINT``. So a session whose first statement
    is this append takes a savepoint in autocommit mode, and ``RELEASE
    SAVEPOINT`` then writes the row for good: a later ``session.rollback()``
    leaves the entry behind, claiming something happened that did not. That is
    the exact failure this module's atomicity argument exists to rule out, and
    it was true on the backend every test runs against.

    ``sqlite3.Connection.in_transaction`` is how the driver reports it. Anything
    else — psycopg on the production dialect — has no such attribute, so the
    check reads ``None``, nothing is emitted, and the normal SQLAlchemy
    behaviour stands. Deliberately keyed on the *absence of a transaction*
    rather than on the dialect name: a driver that starts one properly needs
    nothing done to it, whatever it is called.

    **IMMEDIATE, not a plain BEGIN**, and eight concurrent appenders is what
    said so. A deferred ``BEGIN`` takes its read snapshot at the first SELECT —
    which here is the head read — and SQLite then refuses the INSERT with
    ``SQLITE_BUSY_SNAPSHOT`` ("database is locked") if anybody committed in
    between. That is not a wait ``busy_timeout`` can absorb and not a conflict a
    SAVEPOINT can undo: the whole transaction's view is stale. ``IMMEDIATE``
    takes the write lock up front, so the head this reads is the head this
    writes onto, and a competing appender queues on the lock — where
    ``busy_timeout`` does apply — instead of failing.

    The write lock this takes is held until the *caller* commits, not until the
    append finishes, so it is worth knowing when this branch fires: only where
    the session has written nothing yet. A caller that appends as its last act —
    which is the documented way to call this — has already written, reads
    ``in_transaction`` True, and runs none of it: that connection holds the
    write lock anyway, the same protection by a different route. What is left is
    the read-only-so-far caller, whose remaining work is the append itself.
    """
    connection = session.connection()
    raw = getattr(connection, "connection", None)
    raw = getattr(raw, "dbapi_connection", None)
    if getattr(raw, "in_transaction", None) is False:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _named(session: Session, user_id: Optional[str], label: str,
           role: str) -> tuple[str, str]:
    """Fill in who acted from the account row when the caller did not say.

    Resolved here rather than at each call site so a producer is one line and
    every entry names its actor the same way. A caller that already holds a
    ``Principal`` passes the label and role it has and this does nothing —
    which is also the path that keeps the *role at the time* honest, since the
    principal carries the role the request actually ran under.

    A user id that no longer resolves keeps the id as the label rather than
    falling back to ``SYSTEM``. A deleted account is still not the system, and
    an entry that says ``SYSTEM`` for an act a person took is a false record,
    not a missing one.
    """
    if not user_id or (label and label != SYSTEM and role):
        return (label or SYSTEM), role
    user = session.get(models.User, user_id)
    if user is None:
        return (label if label and label != SYSTEM else user_id), role
    return ((label if label and label != SYSTEM else (user.email or user.name or user_id)),
            role or (user.role or ""))


def _head(session: Session, organization_id: str) -> Optional[models.AuditEntry]:
    """The highest-numbered entry in this organization's chain, or None."""
    return session.scalars(
        select(models.AuditEntry)
        .where(models.AuditEntry.organization_id == organization_id)
        .order_by(models.AuditEntry.seq.desc()).limit(1)).first()


def _source() -> str:
    """Which process is writing. Best-effort and never fatal.

    ``lease.holder_id()`` is host:pid:random and is already this deployment's
    answer to "which worker". Imported here rather than at module scope only to
    keep ``trust/`` free of an import it does not otherwise need; a failure to
    identify the writer must never be the reason an audit entry is not written.
    """
    try:
        from ..lease import holder_id
        return holder_id()[:128]
    except Exception:                                   # noqa: BLE001
        return ""


# ── reading ─────────────────────────────────────────────────────────────────
def entries_for(session: Session, organization_id: str, *,
                limit: int = 200, action: Optional[str] = None,
                ) -> list[models.AuditEntry]:
    """This organization's chain, newest first.

    Scoped to one organization with no way to ask otherwise, the
    ``access.events_for`` rule: there is no supported call that reads another
    tenant's history, so there is no call site to get the scoping wrong at.
    """
    query = (select(models.AuditEntry)
             .where(models.AuditEntry.organization_id == organization_id))
    if action:
        query = query.where(models.AuditEntry.action == action)
    return list(session.scalars(
        query.order_by(models.AuditEntry.seq.desc()).limit(limit)).all())


def walk(session: Session, organization_id: str) -> Iterator[models.AuditEntry]:
    """The whole chain in order, streamed. What verification and export read.

    Its two callers are the two that cannot be paged: a verdict about a page is
    not a verdict about a history, and an export missing its tail is not an
    export. So both touch every row, and this is the one place that decides how.

    **What that costs, stated rather than discovered.** Verification is
    O(entries) in HMAC computations and in memory, and it runs on every
    ``GET /trust/audit`` — a tenant with a year of entries pays for all of them
    to render one page. Correct, and slow at a size no tenant here has yet.

    Two fixes exist and neither is built. Streaming with ``yield_per`` bounds the
    memory, and was written and then taken out again: it implies server-side
    cursors on psycopg, ``verify`` abandons the result mid-iteration at the first
    break, and there is no PostgreSQL server in this environment to run that
    combination against — shipping the production dialect's path untested is the
    drift CLAUDE.md §6 is about. The deeper fix is a periodically written, signed
    checkpoint ("the chain verified to seq N at this hash") that a walk starts
    from instead of from genesis; that is a second thing to keep honest, and
    building it before a chain is long enough to need it trades a real property
    for an imagined problem. Both land here when they land.
    """
    return iter(session.scalars(
        select(models.AuditEntry)
        .where(models.AuditEntry.organization_id == organization_id)
        .order_by(models.AuditEntry.seq.asc())).all())


def chain(session: Session, organization_id: str) -> list[models.AuditEntry]:
    """The whole chain in order, as a list. For callers that want it in hand."""
    return list(walk(session, organization_id))


# ── verification ────────────────────────────────────────────────────────────
def verify(session: Session, organization_id: str) -> dict[str, Any]:
    """Walk the chain and report the **first** break, or that there is none.

    First rather than all, deliberately. One altered row invalidates every
    signature after it, so a list of breaks would be one real finding followed
    by however many entries came later — which reads as a catastrophe and buries
    the row that actually changed. The entry named here is where to look.

    Four ways a chain can be wrong, and they are different findings:

    ``SEQUENCE``  positions are not 1, 2, 3… — an entry was deleted, or two
                  were written at once and something let both through. This is
                  the one ``uq_audit_entry_org_seq`` is supposed to make
                  impossible, so seeing it means the constraint is gone.
    ``LINK``      an entry does not name its predecessor's hash: the chain
                  forked, or a row in the middle was removed.
    ``SIGNATURE`` the stored hash is not this body's — the row was edited.
    ``FORK``      two entries name the same predecessor.

    An empty chain verifies. That is not the "absence of evidence is a pass"
    mistake §1 warns about: this function answers "has what is here been
    altered", and ``entries`` is reported beside the verdict so a caller can
    tell "nothing has happened" from "nothing is wrong". A caller that needs
    the stronger claim asks whether the chain covers the period it cares about,
    which no walk of the rows can answer for it.
    """
    expected_prev = GENESIS
    seen_prev: set[str] = set()
    position = 0

    # ``entries`` on a break is the count *reached*, not the table's total. The
    # walk stops at the first break by design, so reporting a total would mean a
    # second query whose only purpose is to make a failure message rounder.
    for position, row in enumerate(walk(session, organization_id), start=1):
        if row.seq != position:
            return _break(row, "SEQUENCE", position,
                          f"expected position {position}, found {row.seq} — an "
                          f"entry is missing or was renumbered")
        if row.prev_hash in seen_prev:
            return _break(row, "FORK", position,
                          "another entry already names this predecessor, so the "
                          "chain branches here")
        if row.prev_hash != expected_prev:
            return _break(row, "LINK", position,
                          "this entry does not name the hash of the entry "
                          "before it")
        if not signing.matches(covered_body(row), row.entry_hash):
            return _break(row, "SIGNATURE", position,
                          "the stored hash is not this entry's — the row has "
                          "been altered since it was written")
        seen_prev.add(row.prev_hash)
        expected_prev = row.entry_hash

    # Everything above proves the rows that are *here* are unaltered. It says
    # nothing about rows that are not, and tail deletion leaves a shorter chain
    # that is internally perfect — proved on a real database during review:
    # delete the newest entries and the verdict stayed "ok"; delete all of them
    # and it stayed "ok" over zero rows. The anchor is what closes that, and it
    # is checked last because a chain that is broken *and* short should be
    # reported at its break, which is the more specific finding.
    anchor = session.get(models.AuditChainHead, organization_id)
    if anchor is not None and (anchor.seq != position
                               or anchor.entry_hash != expected_prev):
        return {
            "organization_id": organization_id,
            "ok": False,
            "entries": position,
            "head_hash": expected_prev,
            "first_break": {
                "kind": "TRUNCATED",
                "at_position": position + 1,
                "entry_id": None,
                "seq": anchor.seq,
                "detail": (
                    f"the chain ends at position {position} but this tenant's "
                    f"recorded head is {anchor.seq} — "
                    + ("entries have been removed from the end"
                       if anchor.seq != position else
                       "the final entry is not the one that was recorded")),
            },
        }
    if anchor is None and position > 0:
        return {
            "organization_id": organization_id,
            "ok": False,
            "entries": position,
            "head_hash": expected_prev,
            "first_break": {
                "kind": "TRUNCATED", "at_position": 1, "entry_id": None,
                "seq": None,
                "detail": ("entries exist but no recorded head does — the "
                           "anchor was removed, which is the half of a "
                           "truncation that hides the other half"),
            },
        }

    return {
        "organization_id": organization_id,
        "ok": True,
        "entries": position,
        "head_hash": expected_prev,
        "first_break": None,
    }


def _break(row: models.AuditEntry, kind: str, reached: int,
           explanation: str) -> dict[str, Any]:
    return {
        "organization_id": row.organization_id,
        "ok": False,
        "entries": reached,
        "head_hash": None,
        "first_break": {
            "kind": kind,
            "entry_id": row.entry_id,
            "seq": int(row.seq),
            # Normalised to UTC here rather than through ``clock.iso``, which
        # preserves whatever offset it is handed. Signing happens over
        # ``clock.now()`` (always +00:00); verification happens over a value
        # read back from the database, and psycopg renders a ``timestamptz`` in
        # the *session* TimeZone — which follows the server's. On a Postgres
        # whose TimeZone is Asia/Kolkata every entry would read back as
        # '...+05:30', hash differently, and report SIGNATURE on a chain nobody
        # had touched: the whole log failing closed for a server setting. The
        # instant is identical; only the rendering differs, so the fix belongs
        # at the signing boundary and not in ``clock.iso``, whose callers render
        # for display.
        "at": _utc_iso(row.at),
            "action": row.action,
            "explanation": explanation,
        },
    }


# ── export ──────────────────────────────────────────────────────────────────
#
# The point of an audit log is that a third party can accept it, and a third
# party accepts what they can re-check. Both forms below carry every field the
# signature covers plus the signature, so the recipient recomputes the chain
# themselves — given the key — rather than trusting the ``ok`` we hand them.

#: Column order for the CSV. Fixed and written out rather than read from the
#: model, so a column added to the table cannot silently shift the meaning of
#: an existing position in files somebody has already archived.
CSV_COLUMNS = ("seq", "at", "action", "actor_user_id", "actor_label",
               "actor_role", "subject_type", "subject_id", "detail",
               "thresholds_version", "source", "prev_hash", "entry_hash")


def export_json(session: Session, organization_id: str) -> dict[str, Any]:
    """The chain plus its verdict, as JSON a recipient can re-verify."""
    return {
        "organization_id": organization_id,
        "exported_at": clock.iso(clock.now()),
        "verification": verify(session, organization_id),
        "method": (
            "Each entry's entry_hash is HMAC-SHA256, keyed by the deployment's "
            "credential encryption key, over a canonical JSON rendering "
            "(sorted keys, no whitespace) of every field below except "
            "entry_hash itself. prev_hash is the preceding entry's entry_hash; "
            f"the first entry's is {GENESIS[:8]}… (sixty-four zeros)."),
        "entries": [dict(covered_body(row), entry_hash=row.entry_hash)
                    for row in walk(session, organization_id)],
    }


def export_csv(session: Session, organization_id: str) -> str:
    """The same rows as CSV, for a recipient whose tooling is a spreadsheet.

    ``detail`` is rendered with the same canonical JSON the signature covers, so
    a cell copied out of the file is the exact substring that was signed. A
    prettier rendering here would produce a file that cannot be checked against
    itself.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS,
                            lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in walk(session, organization_id):
        body = covered_body(row)
        body["detail"] = signing.canonical(body["detail"]).decode()
        writer.writerow(dict(body, entry_hash=row.entry_hash))
    return buffer.getvalue()


def actions_seen(rows: Iterable[models.AuditEntry]) -> dict[str, int]:
    """How many entries of each action, for the summary above a long list."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.action] = counts.get(row.action, 0) + 1
    return dict(sorted(counts.items()))
