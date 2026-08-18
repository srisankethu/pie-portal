"""Where the log goes, and how a sync's own log survives the sync.

Two problems, one module, because they are the same problem seen from two ends.

**The process log.** ``logging.basicConfig(level=INFO)`` was the whole of the
configuration: one handler on stdout, no level control, and no file anywhere.
On a platform that captures stdout that is thin but workable; on anything else
the log exists for as long as the terminal scrollback does. ``configure`` makes
the level settable, the format say which thread wrote a line — a sync runs in
its own thread while requests are served from others, and interleaved lines with
no thread name cannot be told apart — and adds a rotating file when
``LOG_FILE`` names one.

**A job's log.** A sync runs for an hour in a background thread and its process
log is the only account of what it did. A failed run then said, in the words the
screen showed, *"The sync job stopped unexpectedly. See the server log."* — to
somebody with a browser and no shell. That is advice that cannot be taken, and
it is worse than no advice, because it reads as though the information exists
somewhere.

So the lines a run emits are captured and stored with the run, which makes them
readable by whoever started it, from the same screen that reports the failure.
The capture is a handler on the root logger keyed by **thread**: each pull runs
in its own thread and binds itself, so three companies syncing at once produce
three logs rather than one interleaved one, and a request being served on
another thread contributes nothing to either.

What is not here, deliberately: nothing computes from these lines. They are an
account of what happened, kept for a person to read.

**The cap, and why warnings are exempt.** A five-year pull at INFO is unbounded,
so the buffer stops accepting ordinary lines at ``SYNC_LOG_MAX_LINES``. Warnings
and errors keep going in past it: they are rare, they are the reason anybody
opens a log, and dropping them to stay under a limit set by chatty progress
lines would throw away the evidence to preserve the noise. When the cap bites,
it says so *in the log* — a truncated log that does not admit it is truncated is
the same defect as the missing log this module exists to fix.
"""
from __future__ import annotations

import logging
import logging.handlers
import threading
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from ..config import settings

#: One line, and enough of it. The thread name is not decoration — a sync runs
#: on ``sync-1f5e846e`` while requests are served on the main thread, and
#: without it two interleaved stories read as one incoherent one.
FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False
_lock = threading.Lock()


def is_configured() -> bool:
    """Has this process's logging been set up by the application?

    Read by ``alembic/env.py``, which must not apply its own logging config on
    top of a running app — see the comment there. It is a question about *this
    process*, which is why it is a flag rather than an inspection of the root
    logger: the whole point is to know who owns the configuration.
    """
    return _configured


def configure(*, force: bool = False) -> None:
    """Set up process logging. Idempotent — safe to call from any entry point.

    Every entry point calls this rather than ``basicConfig``: the API process,
    the sync CLI, and bootstrap alike, so a log line looks the same whichever
    of them wrote it and the level is settable everywhere.

    Handlers are replaced rather than added to, because the alternative is a
    second call quietly doubling every line — the failure mode that makes
    people distrust the log they do have.
    """
    global _configured
    with _lock:
        if _configured and not force:
            return

        level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper().strip(),
                        logging.INFO)
        root = logging.getLogger()
        root.setLevel(level)
        for handler in list(root.handlers):
            root.removeHandler(handler)

        formatter = logging.Formatter(FORMAT, DATE_FORMAT)

        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)

        if settings.LOG_FILE:
            try:
                rotating = logging.handlers.RotatingFileHandler(
                    settings.LOG_FILE,
                    maxBytes=settings.LOG_FILE_MAX_BYTES,
                    backupCount=settings.LOG_FILE_KEEP,
                    encoding="utf-8")
                rotating.setFormatter(formatter)
                root.addHandler(rotating)
            except OSError:
                # An unwritable path must not stop the app booting — but it must
                # not be silent either, or the operator believes there is a file.
                root.exception("could not open LOG_FILE %r; logging to stdout "
                               "only", settings.LOG_FILE)

        root.addHandler(_CAPTURE)
        _configured = True


# ── a job's own log ─────────────────────────────────────────────────────────
@dataclass
class LogLine:
    """One captured line, before it is anybody's database row."""

    at: datetime
    level: str
    logger: str
    message: str


@dataclass
class RunLog:
    """The lines one run has emitted and not yet handed over.

    ``dropped`` counts what the cap refused. It is reported rather than
    inferred: a reader comparing a log against a pull that clearly did more than
    it says must be told the difference is truncation and not silence.
    """

    sync_run_id: str
    max_lines: int
    lines: list[LogLine] = field(default_factory=list)
    #: Total accepted over the run's life, not the length of the pending
    #: buffer — the cap is on the whole log, and the buffer is drained often.
    accepted: int = 0
    dropped: int = 0
    #: How many lines have been handed to a store, and so the sequence number
    #: the next one gets. Held here rather than counted from the database: the
    #: flush happens at every phase boundary of a long pull, and a
    #: ``SELECT max(seq)`` per flush is a query per boundary for a number this
    #: object already knows.
    written: int = 0
    _capped_announced: bool = False

    def append(self, line: LogLine) -> None:
        important = line.level in ("WARNING", "ERROR", "CRITICAL")
        if self.accepted >= self.max_lines and not important:
            self.dropped += 1
            if not self._capped_announced:
                self._capped_announced = True
                self.lines.append(LogLine(
                    at=line.at, level="WARNING", logger=__name__,
                    message=(f"Log capped at {self.max_lines} lines for this "
                             f"run. Progress lines past this point are not "
                             f"kept; warnings and errors still are. Raise "
                             f"SYNC_LOG_MAX_LINES to keep more.")))
                self.accepted += 1
            return
        self.lines.append(line)
        self.accepted += 1

    def note(self, message: str, *, level: str = "INFO") -> None:
        """Put a line in this run's log directly, without going through logging.

        For the things worth recording in the run's own account that nobody
        would want on the process log twice — the resolved traceback, the
        parameters the run started with.
        """
        self.append(LogLine(at=datetime.now(timezone.utc), level=level,
                            logger="pie_portal.sync", message=message))

    def note_exception(self, exc: BaseException, *, context: str) -> None:
        """The whole traceback, in the run's log where the reader is.

        ``SyncRun.error`` holds one truncated line, which names what went wrong
        and never where. For a pull that ran an hour before failing, the frames
        are the difference between a report and a diagnosis.
        """
        self.note(f"{context}: {type(exc).__name__}: {exc}", level="ERROR")
        for chunk in "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)).rstrip().splitlines():
            self.note(chunk, level="ERROR")

    def drain(self) -> list[LogLine]:
        """Hand over what has accumulated, and start collecting again."""
        pending, self.lines = self.lines, []
        return pending


class _RunLogHandler(logging.Handler):
    """Routes each record to the run bound to the thread that emitted it.

    A handler on the root logger, so it sees everything the app logs — the
    Zoho client's throttling notices, SQLAlchemy's complaints, a library nobody
    here wrote — rather than only the lines somebody remembered to route. That
    is the point: the log is useful precisely for the thing nobody anticipated.

    Never raises. A logging handler that can fail takes down whatever was being
    logged about, which in this codebase would mean a failing sync failing
    differently on its way out.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self._local = threading.local()

    # -- binding, per thread --------------------------------------------------
    def bind(self, run_log: RunLog) -> Optional[RunLog]:
        previous = getattr(self._local, "run_log", None)
        self._local.run_log = run_log
        return previous

    def unbind(self, previous: Optional[RunLog]) -> None:
        self._local.run_log = previous

    def current(self) -> Optional[RunLog]:
        return getattr(self._local, "run_log", None)

    # -- the handler itself ---------------------------------------------------
    def emit(self, record: logging.LogRecord) -> None:
        run_log = self.current()
        if run_log is None:
            return
        try:
            message = record.getMessage()
            if record.exc_info:
                message = f"{message}\n{self.format_exc(record)}"
            run_log.append(LogLine(
                at=datetime.fromtimestamp(record.created, tz=timezone.utc),
                level=record.levelname,
                logger=record.name,
                message=message))
        except Exception:  # noqa: BLE001 — a broken log line must not raise
            self.handleError(record)

    @staticmethod
    def format_exc(record: logging.LogRecord) -> str:
        if not record.exc_info:
            return ""
        return "".join(traceback.format_exception(*record.exc_info)).rstrip()


#: Module-level, because a handler installed twice logs everything twice.
_CAPTURE = _RunLogHandler()


@contextmanager
def capture(sync_run_id: str, *, max_lines: Optional[int] = None) -> Iterator[RunLog]:
    """Collect this thread's log lines under one run, for as long as the block.

    The previous binding is restored rather than cleared, so nesting behaves —
    a job that itself runs a job does not silently redirect its parent's log
    into its own.

    ``configure`` is called on the way in. A sync started from a CLI that never
    configured logging would otherwise capture nothing and report an empty log,
    which is the failure this module exists to remove rather than reproduce.
    """
    reassert()
    run_log = RunLog(sync_run_id=sync_run_id,
                     max_lines=max_lines or settings.SYNC_LOG_MAX_LINES)
    previous = _CAPTURE.bind(run_log)
    try:
        yield run_log
    finally:
        _CAPTURE.unbind(previous)


def reassert() -> None:
    """Configure, and put it back if something has since taken logging over.

    Anything calling ``logging.config.fileConfig`` or ``basicConfig(force=True)``
    replaces the root handlers wholesale, and a capture handler that is no
    longer attached collects nothing while looking perfectly healthy — an empty
    log that reads as "the run did nothing". Alembic's ``env.py`` was doing
    exactly this, in-process, at every startup; it no longer does, and this is
    the belt to that braces. A library this process does not control may do it
    tomorrow.

    Cheap: an identity check against the root handler list, and a full
    reconfigure only when the handler has actually gone.
    """
    configure()
    if _CAPTURE not in logging.getLogger().handlers:
        configure(force=True)


def current_run_log() -> Optional[RunLog]:
    """The run this thread is logging into, if any."""
    return _CAPTURE.current()
