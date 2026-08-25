"""What a threshold stamp stood for — recorded when it is minted, resolved by re-hashing.

``CommercialThresholds.version`` and ``SignalThresholds.version`` are content
hashes: ``"ci_"``/``"th_"`` plus the first ten hex characters of a sha256 over
``json.dumps(asdict(th), sort_keys=True)``. Every computed row in the schema
carries one, which buys *distinguishability* — an approval stamped
``ci_9f3a…`` proves it was judged under a policy other than today's — and buys
nothing else, because a hash does not invert. ``docs/concepts/09-policy-replay.md``
names that as the real blocker under everything else it wants: "an experiment
whose arms are identified by a hash nobody can resolve to a policy is not
analysable."

This module closes it by keeping the **pre-image**: the exact bytes that were
hashed, stored against ``(organization_id, version)``, written in the same
transaction as the first row ever stamped with them.

Three recorders, deliberately overlapping
-----------------------------------------
1. **The flush handler** (``before_flush`` on the ``Session`` *class*). Any row
   carrying a column marked ``info={"policy_stamp": …}`` writes its version into
   the registry on the same connection, inside the same flush. Registry row and
   stamped row commit together or roll back together — there is no "recorded
   unless the recorder failed" state, which is the failure mode a second session
   would have: on SQLite a second connection opened inside ``execute_sync``'s
   SAVEPOINT blocks on ``busy_timeout`` and then raises, and swallowing that
   error still leaves tens of thousands of committed metric rows stamped with a
   policy nothing recorded. Riding inside the caller's transaction is also what
   makes ``_contained`` necessary rather than decorative: a bookkeeping
   statement that fails on that connection must be undone at a SAVEPOINT, not
   merely caught, or on PostgreSQL it takes the business write down with it.
   When the write genuinely cannot be made, nothing is recorded and the failure
   is *counted* — see ``_RECORDING_FAILURES``.
2. ``commercial.policy.save_for_org`` — the one site where a new version's
   pre-image is known *before* anything is stamped with it.
3. Boot, in ``bootstrap`` and ``main``'s lifespan — which makes today's policy
   resolvable from the moment this ships, and catches an environment-only change
   (``CI_RECENT_DAYS`` moving) at the deploy that made it, where no row may be
   written for days.

Because ``policy.load_for_org`` is the single read funnel for commercial
thresholds, "the pre-image is remembered whenever a version is *resolved*" is a
stronger guarantee than "whenever it is *stamped*" — and ``remember`` is
in-process only, so no read path gains a write. That was the one real risk the
design document flagged, and it is answered by construction rather than by a
``try/except``.

Import discipline
-----------------
Nothing from ``domain.models`` or ``db`` is imported at module scope, on purpose:
``commercial/config.py`` and ``signals/config.py`` import this module, and
pulling the engine into their import graph would make the two smallest, most
widely imported configuration modules in the codebase depend on a live database.
The model and session imports live inside the functions that need them.

Unresolvable stays unresolvable
-------------------------------
There is no ``resolve_or_default``, no ``Optional`` return and no fallback to
the current policy, and the omission is the feature. Today an unresolvable
``ci_9f3a…`` is visibly opaque. A fallback would replay an eighteen-month-old
approval under today's floor and hand back a number that is stamped, formatted
and confidently wrong — an unexplainable number that now *looks* explained,
attached to the exact artefact an audit reaches for. §1: absence of evidence is
not a pass.
"""
from __future__ import annotations

import hashlib
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

#: Prefix → kind. The kind is always read off the stamp itself rather than off
#: the column marker, because two columns legitimately hold both prefixes:
#: ``Signal.threshold_config_version`` (engine detectors stamp ``th_``,
#: Customer × Item detectors stamp ``ci_``) and ``OutcomeSnapshot.thresholds_version``,
#: which copies the signal's verbatim.
_PREFIXES: dict[str, str] = {"ci_": "commercial", "th_": "signal"}

#: The truncation the two ``version`` properties apply. Stated once here so the
#: verification in ``resolve`` cannot drift from the minting.
_SHORT = 10

# ── process-local memory ─────────────────────────────────────────────────────
#: (kind, version) → the bytes that hashed to it. Populated by ``remember``,
#: which every ``version`` property calls. This is what lets the flush handler
#: write a pre-image it never computed itself: by the time a row carrying a
#: stamp reaches a flush, something in this process had to have minted that
#: stamp, and minting is exactly when the bytes are in hand.
_PRE_IMAGES: dict[tuple[str, str], str] = {}

#: The key under which each ``Engine`` carries its own set of
#: ``(organization_id, kind, version)`` triples already known to be *committed*
#: — the skip list that keeps the flush handler from re-issuing the same INSERT
#: on every flush for the life of the process.
#:
#: On the engine rather than in a module-level set, because the set is a claim
#: about *a database* and one process legitimately talks to more than one: the
#: test suite builds a fresh database per test, several of them answering to the
#: identical URL ``sqlite://``. A process-global set would let a triple recorded
#: in one database suppress the write to the next, and the rows stamped there
#: would be unexplainable with nothing to show for it. ``Engine.info`` also ends
#: exactly when the engine does, so nothing accumulates.
#:
#: Populated only from ``after_commit`` — never optimistically. A triple marked
#: known on a flush that later rolls back would suppress every future attempt to
#: record it, and the retry's stamped rows would commit unexplained.
_PERSISTED_KEY = "_threshold_versions_persisted"

#: Counters for ``coverage``. All three are *observations*, not estimates:
#: something happened in this process and was counted at the moment it happened.
_COLLISIONS: dict[tuple[str, str], int] = {}
#: Per organization, both of them, and that is the fix rather than a detail. A
#: review found ``CoverageReport`` labelled with one tenant's id while four of
#: its fields were process-global, so a gap raised while resolving org B's
#: stamps made ``coverage(session, org_a).healthy`` False and told org A it had
#: a recording-path defect. Both increment sites know which organization they
#: are about; the two counters below this comment do not, and are reported as
#: what they are.
_MISSING_PRE_IMAGE: dict[str, int] = {}
_POST_EPOCH_GAPS: dict[str, int] = {}

#: Bookkeeping writes this process could not make — a table that is not there
#: yet in a deploy-before-migrate window, a permission error, a dialect with no
#: ON CONFLICT wired up. Counted rather than only logged, because the health
#: check reads counters: an error that is swallowed and increments nothing
#: leaves a component asserting "recorded as stamped" while every recording it
#: attempted failed, which is precisely absence of evidence dressed as a pass
#: (§1). The rows written meanwhile commit stamped and unexplainable, so this
#: is the counter that says so.
_RECORDING_FAILURES = {"count": 0}


# ── failures, named ──────────────────────────────────────────────────────────
class UnresolvedStamp(LookupError):
    """A stamp this organization has no recorded pre-image for.

    ``reason`` is the part that matters, because the three are not the same
    news:

    ``PRE_EPOCH``       the stamp is older than this organization's earliest
                        sighting of its kind. Expected, finite, and shrinking —
                        history from before the registry existed. The message
                        names the epoch so a reader can see how far back the
                        answerable range goes.
    ``POST_EPOCH_GAP``  the stamp is *newer* than the epoch, so the registry was
                        running and should have caught it. This is an invariant
                        violation, not missing history: a stamp reached a
                        persisted row without being recorded. Logged at ERROR,
                        counted, and surfaced on ``/api/health``.
    ``UNRECORDED_ORG``  no rows at all for this tenant and kind, so there is no
                        epoch to compare against and the boot backfill has not
                        run for it.
    """

    def __init__(self, version: str, organization_id: str, reason: str,
                 epoch: Optional[datetime], message: str) -> None:
        super().__init__(message)
        self.version = version
        self.organization_id = organization_id
        self.reason = reason
        self.epoch = epoch


class CorruptThresholdRecord(RuntimeError):
    """A stored row is not what it claims to be — ``values_json`` does not hash
    to the ``version`` or ``content_digest`` it is filed under.

    Kept separate from ``UnresolvedStamp`` because a gap and a lie need
    different responses, and the lie is the more urgent of the two: a gap
    withholds an answer, while this would hand back the wrong policy under a
    stamp that looks right.
    """


class UnrebuildableThresholds(RuntimeError):
    """The bytes are sound but today's code cannot faithfully reconstruct them.

    A third exception rather than a reuse of the two above, because it points
    somewhere else entirely: neither the recording path nor the stored row is at
    fault, the *dataclass* has moved since. Filing it as ``CorruptThresholdRecord``
    would send an operator to inspect a database row that is perfectly fine.
    """

    def __init__(self, version: str, message: str, *, missing: tuple[str, ...] = (),
                 unexpected: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.version = version
        self.missing = missing
        self.unexpected = unexpected


# ── what a resolution answers with ───────────────────────────────────────────
@dataclass(frozen=True)
class ResolvedThresholds:
    """A verified pre-image: these bytes hash to this stamp, for this tenant.

    ``values`` is a plain dict and is the safe surface. "What was the approval
    floor on that quote?" is answered by ``values["min_margin"]`` and stays
    correct however the dataclass evolves, because it is a fact about the
    recorded policy rather than about today's type. Turning it back into a
    dataclass is a strictly stronger claim and goes through ``rebuild``.
    """

    organization_id: str
    version: str
    kind: str
    values: dict[str, Any]
    first_seen_at: datetime
    first_seen_via: str
    #: The verified bytes ``values`` was parsed from. Carried so ``rebuild`` can
    #: compare a reconstruction against the recorded serialisation exactly,
    #: rather than against ten hex characters of a hash of it.
    serialized: str = ""


@dataclass(frozen=True)
class CoverageReport:
    """How much of one tenant's stamp history can be dereferenced, plus defects.

    ``epochs`` is the load-bearing field: below a kind's epoch an unresolvable
    stamp is expected history, above it is a bug in the recording path. The
    counters are process-local observations since start-up — deliberately not
    persisted, because a defect counter that survives a restart invites reading
    it as a rate.

    **Two scopes, named apart.** ``post_epoch_gaps`` and
    ``stamps_without_a_pre_image`` are *this organization's*: both increment
    sites know whose stamp they were resolving. ``collisions`` and
    ``recording_failures`` are the process's and cannot be otherwise — a stamp
    is a content hash with no tenant in it, and a failed INSERT means the
    recording path is broken for everyone sharing this process. All four
    previously read as this tenant's, so one book's fault was reported as
    another's; the fields keep their scope in their names now.
    """

    organization_id: str
    epochs: dict[str, Optional[datetime]]
    recorded: dict[str, int]
    #: This organization's.
    post_epoch_gaps: int
    stamps_without_a_pre_image: int
    #: The process's. A collision is two policies sharing one stamp, which makes
    #: that stamp ambiguous on every tenant's rows, so it still counts against
    #: ``healthy`` below — it is genuinely everyone's defect, not a
    #: misattribution.
    process_collisions: int = 0
    #: The process's. Recording writes that could not be made — see
    #: ``_RECORDING_FAILURES``. Counted because ``healthy`` would otherwise
    #: answer True for a process whose every INSERT failed, which is the same
    #: benign default in a second place.
    process_recording_failures: int = 0

    @property
    def healthy(self) -> bool:
        """No observed defect. An empty registry is not a defect — it is a
        tenant nothing has stamped yet, and calling that unhealthy would be the
        benign default's mirror image: a loud failure with no fault behind it.
        """
        return (self.process_collisions == 0 and self.post_epoch_gaps == 0
                and self.stamps_without_a_pre_image == 0
                and self.process_recording_failures == 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "epochs": {k: (v.isoformat() if v else None)
                       for k, v in self.epochs.items()},
            "recorded": dict(self.recorded),
            # Keyed by scope, so a reader cannot take a process-wide count for
            # this tenant's — which is exactly what the flat shape invited.
            "post_epoch_gaps": self.post_epoch_gaps,
            "stamps_without_a_pre_image": self.stamps_without_a_pre_image,
            "process_collisions": self.process_collisions,
            "process_recording_failures": self.process_recording_failures,
        }


# ── minting side ─────────────────────────────────────────────────────────────
def serialized(th: Any) -> str:
    """The exact bytes a threshold dataclass hashes from, as a ``str``.

    A one-line accessor rather than a re-serialisation on purpose. Recomputing
    ``json.dumps(asdict(th), sort_keys=True)`` here would be a second answer to
    the question the ``version`` property already answers, and the two would be
    equal right up until somebody changed one of them — at which point the
    registry would store bytes that do not hash to the stamp they are filed
    under, and ``resolve`` would report every row as corrupt.
    """
    return th.serialized


def kind_of(version: str) -> str:
    """"commercial" | "signal", read off the stamp's own prefix.

    Read from the value rather than from the column marker because two columns
    hold both prefixes — see ``_PREFIXES``. An unrecognised prefix returns
    ``"unknown"`` rather than raising: this is called from a flush handler that
    must never be the reason a business write fails.
    """
    return _PREFIXES.get(version[:3], "unknown")


def remember(kind: str, version: str, serialized: str) -> None:
    """Hold this version's pre-image in process memory, and detect a collision.

    Called from the ``version`` property of both threshold dataclasses, which is
    the only place in the codebase where a stamp is minted. That placement is
    the point and is easy to mistake for a stray side effect worth cleaning up:
    ``commercial.config.load_commercial_thresholds().version`` reaches a stamp
    without ever touching ``policy.py``, and that path — the environment-derived
    half of the policy — is exactly the half nothing recorded. Recording at the
    property closes it; recording at the loader would not.

    **Collision detection, free and in-process.** The stamp keeps 40 bits of a
    sha256. If this memo already holds *different* bytes under the same
    ``(kind, version)``, that is a collision, observed at the moment it happens
    rather than inferred later from a puzzling number. The first pre-image wins
    — overwriting would quietly change what every already-stamped row means —
    and the event is logged at ERROR and counted into ``coverage``.
    """
    key = (kind, version)
    existing = _PRE_IMAGES.get(key)
    if existing is None:
        _PRE_IMAGES[key] = serialized
        return
    if existing == serialized:
        return
    _COLLISIONS[key] = _COLLISIONS.get(key, 0) + 1
    log.error(
        "threshold version collision: %s already stands for different values. "
        "The stamp keeps only %d hex characters of the digest, so two distinct "
        "policies can share one. Keeping the first pre-image; rows stamped "
        "%s in this process may mean either. held=%s incoming=%s",
        version, _SHORT, version, existing, serialized)


def pre_image(kind: str, version: str) -> Optional[str]:
    """The remembered bytes for a stamp, or ``None``. Process-local, never a read."""
    return _PRE_IMAGES.get((kind, version))


# ── writing side ─────────────────────────────────────────────────────────────
def _row(organization_id: str, kind: str, version: str, serialized: str,
         via: str) -> dict[str, Any]:
    from . import clock

    return {
        "organization_id": organization_id,
        "version": version,
        "kind": kind,
        "values_json": serialized,
        "content_digest": hashlib.sha256(serialized.encode()).hexdigest(),
        "first_seen_at": clock.now(),
        "first_seen_via": via,
    }


def _note_failure(what: str, *, exc_info: bool = True) -> None:
    """One place where "the registry could not write" is both logged and counted.

    Counted is the load-bearing half. ``log.exception`` alone was what the
    blanket handler below already did, and it left ``check_threshold_registry``
    returning HEALTHY with the affirmative message "Threshold versions recorded
    as stamped" while every INSERT it attempted had failed — the health check
    reads counters, so a failure path that increments none of them is invisible
    to it.
    """
    _RECORDING_FAILURES["count"] += 1
    log.error("threshold registry: %s. The write it rode inside is unaffected; "
              "the version(s) it carried are stamped on rows that cannot yet be "
              "dereferenced.", what, exc_info=exc_info)


@contextmanager
def _contained(session: Session, what: str) -> Any:
    """Run the registry's own database work so a failure cannot reach the caller.

    A ``try/except`` wrapped around a statement that has *already spoken to the
    database* is not containment on PostgreSQL. A failed statement there aborts
    the entire transaction, so swallowing the error hands the caller back a
    connection on which the very next statement — the business INSERT this
    recorder rode inside — dies with ``InFailedSqlTransaction``. That is the
    exact inversion the handler was written to prevent, and it stayed invisible
    because the test that claimed to prove it raised in pure Python and never
    touched the connection.

    So the work runs inside a SAVEPOINT, which rolls back the failed statement
    alone and leaves the caller's transaction usable.

    **Except on SQLite**, where the savepoint would cost more than it buys, both
    halves verified rather than assumed. A failed statement there leaves the
    transaction perfectly usable, so there is nothing to contain. And pysqlite
    does not emit its own BEGIN: a SAVEPOINT taken as the first statement of a
    transaction — which is exactly where ``before_flush`` sits — *starts* the
    transaction, and the matching RELEASE *commits* it. A registry row would
    then survive a rollback of the write it rode inside, breaking the "recorded
    together or rolled back together" guarantee this module is built on.

    Yields the connection, or ``None`` when obtaining one failed; a caller that
    gets ``None`` must do nothing. Never raises, and every failure is counted.
    """
    connection = None
    nested = None
    try:
        connection = session.connection()
        if connection.dialect.name != "sqlite":
            nested = connection.begin_nested()
    except Exception:  # noqa: BLE001 — bookkeeping never fails a business write
        _note_failure(f"{what}: no usable connection")
        try:
            # The body is expected to see ``None`` and do nothing; the guard is
            # here so that a caller which does not check still cannot raise out
            # of a flush handler.
            yield None
        except Exception:  # noqa: BLE001
            _note_failure(what)
        return

    try:
        yield connection
    except Exception:  # noqa: BLE001
        if nested is not None:
            try:
                nested.rollback()
            except Exception:  # noqa: BLE001
                log.exception("threshold registry: rolling the bookkeeping "
                              "SAVEPOINT back also failed.")
        _note_failure(what)
        return
    if nested is not None:
        try:
            nested.commit()
        except Exception:  # noqa: BLE001
            _note_failure(f"{what}: releasing the bookkeeping SAVEPOINT")


def _insert_ignore(connection: Any, rows: list[dict[str, Any]]) -> None:
    """One INSERT … ON CONFLICT DO NOTHING, on the caller's own connection.

    Not SELECT-then-add, for two reasons that both matter inside a flush. It
    removes a read from the flush path, which is where ``state.engine._persist``
    is writing tens of thousands of rows at once. And it removes the
    ``IntegrityError`` a race between two processes would otherwise raise: on
    PostgreSQL a failed statement aborts the whole transaction, so a concurrent
    metrics recompute recording the same version first would fail somebody
    else's quote save. Bookkeeping must not be able to do that.

    Both dialects this platform runs support the clause natively, which is why
    the branch is four lines rather than a portable emulation.
    """
    from .domain.models import ThresholdVersion

    table = ThresholdVersion.__table__
    name = connection.dialect.name
    if name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    elif name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        # No third dialect is supported here today. Falling back to a plain
        # INSERT would reintroduce exactly the IntegrityError this helper exists
        # to avoid, so the honest move is to record nothing and say so — loudly
        # enough that the health check sees it, because "recorded nothing" is
        # the same fact whether a statement failed or was never sent.
        _note_failure(f"dialect {name!r} has no ON CONFLICT support wired up, "
                      f"so {len(rows)} version(s) were not recorded",
                      exc_info=False)
        return
    connection.execute(_insert(table).on_conflict_do_nothing(), rows)


def _mark_pending(session: Session, keys: set[tuple[str, str, str]]) -> None:
    """Remember, on the session, what this transaction wrote — until it commits."""
    session.info.setdefault("_threshold_pending", set()).update(keys)


def _persisted(session: Session) -> set[tuple[str, str, str]]:
    """This database's own set of triples known to be committed."""
    bind = session.get_bind()
    info = getattr(bind, "info", None)
    if info is None:                      # a Connection-bound session in a test
        return set()
    return info.setdefault(_PERSISTED_KEY, set())


def record_current(session: Session, organization_id: str, *, kind: str,
                   version: str, serialized: str) -> None:
    """Record one version's pre-image, in the caller's transaction.

    Used where the pre-image is known *before* anything is stamped with it —
    ``save_for_org`` and the boot backfill — which is the only way an
    environment-only policy change is ever recorded at all: nothing may be
    stamped with it for days, and the deploy that made it is the moment worth
    tying it to.

    Idempotent by ``ON CONFLICT DO NOTHING``, so a second boot is a no-op and
    the ``first_seen_at`` of the first sighting stands.
    """
    if not organization_id or not version or not serialized:
        return
    remember(kind, version, serialized)
    key = (organization_id, kind, version)
    if key in _persisted(session):
        return
    # Contained, like the flush recorder, and for a reason this site made
    # visible on its own: with the raw call here, a policy edit 500'd outright
    # whenever ``threshold_versions`` could not be written — the deploy window
    # before ``d1thrv`` runs, a permission error — and an owner could not change
    # the margin floor at all. The bookkeeping that exists to explain a policy
    # must never be the reason the policy cannot be edited.
    with _contained(session, f"recording {version} for {organization_id}") as conn:
        if conn is None:
            return
        _insert_ignore(conn, [_row(organization_id, kind, version, serialized,
                                   "boot" if _boot_flag["on"] else "edit")])
        _mark_pending(session, {key})


#: Which of the two out-of-flush recorders is running. A module flag rather than
#: a parameter because ``first_seen_via`` is diagnostic only — it says how a
#: version came to be known, never how authoritative it is — and threading a
#: string through two call sites to carry a debugging aid is the kind of API
#: widening §9 warns about.
_boot_flag = {"on": False}


@contextmanager
def _via_boot() -> Any:
    previous = _boot_flag["on"]
    _boot_flag["on"] = True
    try:
        yield
    finally:
        _boot_flag["on"] = previous


# ── recorder 1: the flush handler ────────────────────────────────────────────
#: mapper → the attribute names on it that carry a policy stamp. Built once per
#: mapper and then a single dict lookup per instance, which is what makes this
#: affordable on a flush of tens of thousands of rows: mappers with no stamp
#: column cost exactly that lookup and nothing else.
_STAMP_ATTRS: dict[Any, tuple[str, ...]] = {}


def _stamp_attrs(mapper: Any) -> tuple[str, ...]:
    cached = _STAMP_ATTRS.get(mapper)
    if cached is not None:
        return cached
    found: list[str] = []
    has_org = "organization_id" in mapper.columns
    if has_org:
        for prop in mapper.column_attrs:
            for column in prop.columns:
                if column.info.get("policy_stamp"):
                    found.append(prop.key)
                    break
    result = tuple(found)
    _STAMP_ATTRS[mapper] = result
    return result


def _stamps_in_flight(session: Session) -> tuple[dict[tuple[str, str, str], str],
                                                 dict[tuple[str, str, str], tuple[str, str]]]:
    """The stamps this flush carries, split into recordable and unexplained.

    Pure: it reads the session's own units of work and touches no connection,
    which is what lets the caller put every statement that follows inside one
    contained block.

    Both halves are *keyed* rather than appended. One distinct stamp can be
    carried by every row in a flush and ``state.engine._persist`` flushes tens
    of thousands at once, so a warning per row would bury the one line that
    matters under twenty thousand identical ones — the shape of log that gets
    filtered out and then goes unread when it finally says something new.
    """
    triples: dict[tuple[str, str, str], str] = {}
    unknown: dict[tuple[str, str, str], tuple[str, str]] = {}
    known = _persisted(session)
    for obj in list(session.new) + list(session.dirty):
        mapper = getattr(type(obj), "__mapper__", None)
        if mapper is None:
            continue
        attrs = _stamp_attrs(mapper)
        if not attrs:
            continue
        org = getattr(obj, "organization_id", None)
        if not org:
            continue
        for attr in attrs:
            version = getattr(obj, attr, None)
            if not version:
                continue
            kind = kind_of(version)
            if kind == "unknown":
                continue
            key = (org, kind, version)
            if key in triples or key in known:
                continue
            bytes_ = _PRE_IMAGES.get((kind, version))
            if bytes_ is None:
                unknown.setdefault(key, (type(obj).__name__, attr))
                continue
            triples[key] = bytes_
    return triples, unknown


def _still_unrecorded(connection: Any, session: Session,
                      unknown: dict[tuple[str, str, str], tuple[str, str]],
                      ) -> dict[tuple[str, str, str], tuple[str, str]]:
    """Of the stamps with no pre-image *here*, the ones the database lacks too.

    Asked rather than assumed, and the distinction is the whole point. "This
    process never minted that version" is not evidence of a gap: an ordinary
    UPDATE to a row written weeks ago carries a stamp some earlier process
    minted and recorded perfectly well — approving an ``ApprovalRequest``
    created before the last policy edit, ``outcome_tracker`` copying a signal's
    ``threshold_config_version`` onto a new snapshot, a detector reusing the
    stamp on the row it flagged. Warning on those and degrading /api/health for
    the life of the process is how the one instrument that reports a genuine
    POST_EPOCH_GAP becomes a check nobody reads (§6).

    One SELECT per distinct unseen stamp per process, not per row: a hit is
    promoted into the engine's persisted set, so an update-heavy job asks once
    and then takes the skip-list branch for the rest of its life. A miss is not
    promoted — it is exactly the case a later write should try to record.
    """
    from sqlalchemy import and_, or_

    from .domain.models import ThresholdVersion

    table = ThresholdVersion.__table__
    found = set(connection.execute(
        select(table.c.organization_id, table.c.version).where(
            or_(*[and_(table.c.organization_id == org, table.c.version == version)
                  for org, _kind, version in unknown]))).all())
    if not found:
        return unknown
    recorded = {key for key in unknown if (key[0], key[2]) in found}
    _persisted(session).update(recorded)
    return {key: value for key, value in unknown.items() if key not in recorded}


def _record_stamped_rows(session: Session, _flush_context: Any = None,
                         _instances: Any = None) -> None:
    """Record the pre-image of every stamp about to be flushed. Never raises.

    Registered on the ``sqlalchemy.orm.Session`` *class* rather than on
    ``SessionLocal``, so the tests, ``bootstrap.py`` and everything under
    ``scripts/`` are covered without opting in. Action at a distance, and that
    is the honest cost of it: nothing at a write site says this happens. The
    alternative — a call at each write site — is the coverage chain this design
    rejected, because a site added later is a site that silently records
    nothing, and the rows it writes are unexplainable forever rather than
    noisily wrong.

    A stamp with no pre-image anywhere is a WARNING and writes nothing. It must
    never be filled in from today's policy: a plausible-looking current policy
    served as the historical one is the single worst outcome available here
    (§1).

    Every statement lives inside ``_contained`` — see there for why the
    ``except`` this used to rely on was not containment at all on the dialect
    production runs.
    """
    try:
        triples, unknown = _stamps_in_flight(session)
        if not triples and not unknown:
            return
        with _contained(session, "recording the stamps in this flush") as conn:
            if conn is None:
                return
            if unknown:
                unknown = _still_unrecorded(conn, session, unknown)
            for (_org, _kind, version), (model_name, attr) in unknown.items():
                _MISSING_PRE_IMAGE[_org] = _MISSING_PRE_IMAGE.get(_org, 0) + 1
                log.warning(
                    "threshold registry: %s.%s is being written with %s, whose "
                    "values this process never minted and which no row in "
                    "threshold_versions explains, so there is nothing to "
                    "record. The row still commits — it is stamped, just not "
                    "dereferenceable. Expected only for a stamp carried in from "
                    "outside (a restore, a fixture); if this row was computed "
                    "here, the minting path is not calling remember().",
                    model_name, attr, version)
            if triples:
                _insert_ignore(conn, [_row(org, kind, version, bytes_, "stamp")
                                      for (org, kind, version), bytes_
                                      in triples.items()])
                _mark_pending(session, set(triples))
    except Exception:  # noqa: BLE001 — bookkeeping never fails a business write
        _note_failure("recording the stamps in this flush")


def _confirm_persisted(session: Session) -> None:
    """Promote this transaction's writes to known-persisted, on commit only.

    The whole point of populating the engine's skip list here and nowhere else:
    a triple marked known on a flush that later rolls back would suppress every
    future attempt to record it, and the rows written by the retry would commit
    unexplained.
    """
    pending = session.info.pop("_threshold_pending", None)
    if pending:
        _persisted(session).update(pending)


def _discard_pending(session: Session, *_args: Any, **_kwargs: Any) -> None:
    session.info.pop("_threshold_pending", None)


_LISTENERS = (
    ("before_flush", _record_stamped_rows),
    ("after_commit", _confirm_persisted),
    ("after_rollback", _discard_pending),
    ("after_soft_rollback", _discard_pending),
)


def install() -> None:
    """Register the flush recorder on the ``Session`` class. Idempotent."""
    for name, fn in _LISTENERS:
        if not event.contains(Session, name, fn):
            event.listen(Session, name, fn)


def installed() -> bool:
    """Whether the flush recorder is live. Asserted at start-up in ``main``."""
    return all(event.contains(Session, name, fn) for name, fn in _LISTENERS)


# ── reading side ─────────────────────────────────────────────────────────────
def _epoch(session: Session, organization_id: str, kind: str) -> Optional[datetime]:
    from .domain.models import ThresholdVersion

    return session.scalar(
        select(func.min(ThresholdVersion.first_seen_at)).where(
            ThresholdVersion.organization_id == organization_id,
            ThresholdVersion.kind == kind))


def resolve(session: Session, organization_id: str, version: str, *,
            stamped_at: Optional[datetime] = None) -> ResolvedThresholds:
    """The values behind a stamp, verified — or a named refusal.

    Raises rather than returning ``None`` or a default, and see the module
    docstring for why that is the feature rather than an omission.

    Verification is not decoration: the stored bytes are re-hashed and must
    reproduce **both** ``content_digest`` (the full sha256) and the ten hex
    characters in ``version`` itself. Checking only the truncated form would
    accept a 40-bit collision as an answer; checking only the digest would
    accept a row filed under the wrong stamp.

    ``stamped_at`` is optional and is only used to classify a *miss*. It is what
    a caller holding the stamped row knows and this function cannot otherwise
    learn — the stamp carries no date — and it is now the **only** thing that
    separates PRE_EPOCH from POST_EPOCH_GAP. Pass the row's own timestamp where
    you have one; a miss with no date is reported as missing history, which is
    the honest reading when nothing places it.

    It used to fall back to "did this process mint that version itself", and
    ``_unresolved`` records why that had to go: a stamp is a content hash, so it
    is org-blind and shared by any policy holding the same numbers, and
    ``backtest`` mints hypothetical ones on demand.
    """
    from . import clock
    from .domain.models import ThresholdVersion

    kind = kind_of(version)
    row = session.get(ThresholdVersion, (organization_id, version))
    if row is None:
        raise _unresolved(session, organization_id, version, kind, stamped_at)

    digest = hashlib.sha256((row.values_json or "").encode()).hexdigest()
    if row.content_digest and digest != row.content_digest:
        raise CorruptThresholdRecord(
            f"threshold_versions row ({organization_id}, {version}) does not "
            f"hash to the content_digest it is filed under "
            f"(stored {row.content_digest}, recomputed {digest}). The stored "
            f"values have been altered since they were written; this is a row "
            f"that lies, not a gap, and no policy should be read out of it.")
    if digest[:_SHORT] != version[3:]:
        raise CorruptThresholdRecord(
            f"threshold_versions row ({organization_id}, {version}) holds "
            f"values that hash to {digest[:_SHORT]}, not to the stamp they are "
            f"filed under. Either the row was written against a different "
            f"serialisation or the two do not belong together.")

    try:
        values = json.loads(row.values_json)
    except ValueError as exc:
        raise CorruptThresholdRecord(
            f"threshold_versions row ({organization_id}, {version}) is not "
            f"readable JSON: {exc}") from exc

    return ResolvedThresholds(
        organization_id=organization_id, version=version,
        kind=row.kind or kind, values=values,
        first_seen_at=clock.aware(row.first_seen_at),
        first_seen_via=row.first_seen_via or "",
        serialized=row.values_json)


def _unresolved(session: Session, organization_id: str, version: str,
                kind: str, stamped_at: Optional[datetime]) -> UnresolvedStamp:
    """Classify a miss, which is the part that decides whether it is a defect.

    Three outcomes and they are not the same news, so the split is made on
    evidence rather than on a default. The order below is the order of strength:
    no rows at all is a fact about the tenant; a pre-image this process minted
    is proof the registry was live at minting; a caller-supplied timestamp is
    the direct comparison the epoch exists for; and only when none of those
    applies does the answer fall to PRE_EPOCH — which is the *benign* of the
    two, and therefore the one that has to be earned rather than assumed.
    """
    from . import clock
    from .domain.models import ThresholdVersion

    epoch = clock.aware(_epoch(session, organization_id, kind))
    if epoch is None:
        any_row = session.scalar(
            select(func.count()).select_from(ThresholdVersion).where(
                ThresholdVersion.organization_id == organization_id)) or 0
        return UnresolvedStamp(
            version, organization_id, "UNRECORDED_ORG", None,
            f"{version} cannot be resolved for {organization_id}: this "
            f"organization has no recorded {kind} threshold versions at all "
            f"({any_row} row(s) of any kind), so there is no epoch to judge the "
            f"stamp against. The boot backfill has not run for this tenant — "
            f"start the application against this database, or call "
            f"record_current_policies for it, and today's policy becomes "
            f"resolvable.")

    # ``(kind, version) in _PRE_IMAGES`` used to be a second reason to call this
    # a gap — "this process minted that version itself". It is not evidence and
    # a review caught why. A stamp is a content hash, so it is the same value
    # for any organization whose policy holds the same numbers, and
    # ``_PRE_IMAGES`` is deliberately org-blind because collision detection is.
    # Worse, ``CommercialThresholds.version`` mints on every property access, so
    # ``backtest.run`` computing a variant from a caller-supplied ``min_margin``
    # puts a hypothetical policy's hash into that memo — one the organization
    # may never have run. If the org genuinely *did* run at that floor before
    # the epoch, its true PRE_EPOCH rows then resolved as POST_EPOCH_GAP,
    # logging a recording-path bug that did not exist and flipping
    # ``/api/health`` UNHEALTHY for the life of the process, across tenants.
    #
    # What survives is the test that is per-organization and persisted: the
    # row's own stamp date against this organization's own recorded epoch. A row
    # with no date cannot be placed and is reported as PRE_EPOCH, which is the
    # true statement — history older than the registry is expected, finite and
    # shrinking.
    if stamped_at is not None and clock.aware(stamped_at) >= epoch:
        _POST_EPOCH_GAPS[organization_id] = _POST_EPOCH_GAPS.get(
            organization_id, 0) + 1
        why = (f"the row carrying it is dated {clock.iso(stamped_at)}, at or "
               f"after the epoch")
        message = (
            f"{version} cannot be resolved for {organization_id}, and it is not "
            f"missing history: {why} (epoch {epoch.isoformat()}). A stamp "
            f"reached a persisted row without being recorded. This is a bug in "
            f"the recording path, not missing history — check that the minting "
            f"site calls remember() and that the flush handler is installed "
            f"(threshold_registry.installed()).")
        log.error("%s", message)
        return UnresolvedStamp(version, organization_id, "POST_EPOCH_GAP",
                               epoch, message)

    return UnresolvedStamp(
        version, organization_id, "PRE_EPOCH", epoch,
        f"{version} was stamped before {organization_id} began recording "
        f"{kind} threshold versions (epoch {epoch.isoformat()}). The values "
        f"behind it were never captured and cannot be reconstructed — the "
        f"environment half of that hash came from a process that is gone. This "
        f"is expected for rows older than the registry, and the set of them is "
        f"finite and shrinking; it is not a defect, and it must never be "
        f"answered with today's policy.")


def rebuild(resolved: ResolvedThresholds) -> Any:
    """Turn a resolution back into the dataclass — or refuse.

    ``resolve`` answers "what was the approval floor" from ``values`` and is
    always safe, because a dict of recorded numbers is a fact about the past.
    ``rebuild`` is the strictly stronger claim a backtest needs: *this object is
    the policy that was in force*. It is separated out because the naive version
    of it is the forbidden substitution wearing a type annotation —
    ``CommercialThresholds(**old_values)`` after the dataclass grows a field
    fills that field with **today's** default and hands back an object that
    claims to be the historical policy while carrying a value nobody ever set.
    Nothing about the result looks wrong; that is precisely the problem.

    So: the stored key set must match today's field set exactly, and re-hashing
    the reconstructed object must reproduce the original stamp. Either check
    failing means today's code cannot faithfully represent that policy, and the
    honest answer is a refusal that names the drift.
    """
    from dataclasses import fields as _fields

    if resolved.kind == "commercial":
        from .commercial.config import CommercialThresholds as cls
    elif resolved.kind == "signal":
        from .signals.config import SignalThresholds as cls
    else:
        raise UnrebuildableThresholds(
            resolved.version,
            f"{resolved.version} has kind {resolved.kind!r}, which names no "
            f"threshold dataclass in this codebase.")

    field_types = {f.name: str(f.type) for f in _fields(cls)}
    expected = set(field_types)
    stored = set(resolved.values)
    missing = tuple(sorted(expected - stored))
    unexpected = tuple(sorted(stored - expected))
    if missing or unexpected:
        raise UnrebuildableThresholds(
            resolved.version,
            f"{resolved.version} cannot be rebuilt as {cls.__name__}: the "
            f"recorded policy has "
            f"{'fields this code does not define (' + ', '.join(unexpected) + ')' if unexpected else ''}"
            f"{' and ' if missing and unexpected else ''}"
            f"{'no value for fields this code requires (' + ', '.join(missing) + ')' if missing else ''}"
            f". Constructing it anyway would fill those from today's defaults "
            f"and return an object claiming to be the historical policy. Read "
            f"the individual values off ResolvedThresholds.values instead.",
            missing=missing, unexpected=unexpected)

    obj = cls(**{name: _retuple(value, field_types[name])
                 for name, value in resolved.values.items()})
    if obj.version != resolved.version:
        raise UnrebuildableThresholds(
            resolved.version,
            f"{resolved.version} rebuilt into a {cls.__name__} that stamps "
            f"{obj.version}. The field set matches, so what moved is how the "
            f"stamp is taken — a serialisation or ordering change. The object "
            f"would be a plausible impostor and is not returned.")
    if resolved.serialized and serialized(obj) != resolved.serialized:
        raise UnrebuildableThresholds(
            resolved.version,
            f"{resolved.version} rebuilt into a {cls.__name__} that does not "
            f"re-serialise to the recorded bytes. The stamp still matches, "
            f"which is exactly why this second check exists — it is only ten "
            f"hex characters of a hash, and it agrees on differences JSON "
            f"cannot express.")
    return obj


def _retuple(value: Any, declared: str) -> Any:
    """Put JSON's lists back into the tuples the dataclass declares.

    Not cosmetic, and the reason it is easy to miss is that the stamp cannot
    catch it: ``json.dumps`` renders a tuple and a list identically, so a
    rebuilt object holding lists where the frozen dataclass declares
    ``tuple[tuple[str, float], ...]`` re-hashes to exactly the right version
    while being a different object — unequal to the original, and unhashable,
    so ``hash(th)`` raises on a rebuilt policy and works on a loaded one. That
    is the same class of silent substitution ``rebuild`` exists to refuse,
    arriving through the container type instead of through a field default.

    Recursive, because two of these are tuples of tuples.
    """
    if "tuple" not in declared or not isinstance(value, list):
        return value
    return tuple(tuple(v) if isinstance(v, list) else v for v in value)


def coverage(session: Session, organization_id: str) -> CoverageReport:
    """What this tenant's registry can answer, and what has gone wrong in-process.

    The epochs are the useful half: they are the line below which an
    unresolvable stamp is expected history rather than a defect. The counters
    are the other half and are read by ``/api/health``.
    """
    from .domain.models import ThresholdVersion

    epochs: dict[str, Optional[datetime]] = {}
    recorded: dict[str, int] = {}
    for kind in sorted(set(_PREFIXES.values())):
        epochs[kind] = _epoch(session, organization_id, kind)
        recorded[kind] = session.scalar(
            select(func.count()).select_from(ThresholdVersion).where(
                ThresholdVersion.organization_id == organization_id,
                ThresholdVersion.kind == kind)) or 0
    return CoverageReport(
        organization_id=organization_id,
        epochs=epochs, recorded=recorded,
        process_collisions=sum(_COLLISIONS.values()),
        post_epoch_gaps=_POST_EPOCH_GAPS.get(organization_id, 0),
        stamps_without_a_pre_image=_MISSING_PRE_IMAGE.get(organization_id, 0),
        process_recording_failures=_RECORDING_FAILURES["count"])


def record_current_policies(session: Session, organization_id: str) -> None:
    """Record both of this organization's *current* threshold versions.

    The boot recorder, and the one that makes today's version resolvable from
    the moment this ships rather than from whenever a row happens to be written
    next. It is also the only recorder that catches an environment-only change:
    move ``CI_RECENT_DAYS`` and every stamp in the platform changes, while
    nothing may be *stamped* for days.

    Does not commit — the caller owns the transaction boundary, because §4 wants
    one committed transaction per organization rather than one long one across
    all of them.
    """
    from .commercial.policy import load_for_org
    from .signals.engine import thresholds_for_org

    with _via_boot():
        commercial = load_for_org(session, organization_id)
        record_current(session, organization_id, kind="commercial",
                       version=commercial.version,
                       serialized=serialized(commercial))
        signal = thresholds_for_org(session, organization_id)
        record_current(session, organization_id, kind="signal",
                       version=signal.version, serialized=serialized(signal))


def backfill_all_organizations(session_factory: Any) -> int:
    """Record today's policy for every organization, one transaction each.

    One committed transaction per organization, per §4's "commit at a natural
    boundary": a single transaction spanning every tenant would hold a write
    lock across the whole start-up on SQLite, which is the exact shape of the
    stall that section exists because of. Returns how many organizations were
    covered. Never raises — a registry that could stop the platform booting
    would be worse than one with a gap in it.
    """
    from .domain.models import Organization

    covered = 0
    session = session_factory()
    try:
        org_ids = list(session.scalars(select(Organization.organization_id)))
    except Exception:  # noqa: BLE001
        log.exception("threshold registry: could not list organizations; "
                      "today's policy stays unrecorded until the next boot.")
        org_ids = []
    finally:
        session.close()

    for org_id in org_ids:
        session = session_factory()
        try:
            record_current_policies(session, org_id)
            session.commit()
            covered += 1
        except Exception:  # noqa: BLE001
            session.rollback()
            log.exception("threshold registry: could not record the current "
                          "policy for %s.", org_id)
        finally:
            session.close()
    return covered


install()
