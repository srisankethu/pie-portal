"""Stop listing at what changed — and never let that look like a deletion.

``skip()`` already saved the *detail* call for a document that had not moved.
The rest of a nightly pull's bill was the listing itself: two years of history
is one list call per 200 documents, every night, almost all of it spent
discovering that nothing changed.

So a nightly listing sorts by modification time and stops at the newest stamp
already held. The danger that buys is precise and severe: the deletion sweep in
``SyncService`` decides what Zoho no longer has by subtracting what the listing
saw from what is held. A listing that stopped early saw almost nothing. If it
were allowed to report itself complete, the first nightly pull would delete the
entire unchanged book.

That is what most of this file is about.
"""
from __future__ import annotations

from datetime import date

from app.ingestion.zoho_client import ZohoApiSource


def _source(rows: list[dict], detail: dict | None = None):
    """A source whose HTTP layer is replaced by a canned list page."""
    src = ZohoApiSource.__new__(ZohoApiSource)
    src._since = date(2024, 1, 1)
    src._until = None
    src.listed = {}
    src.listing_complete = set()
    src.documents_fetched = 0
    src.documents_resumed = 0
    src.modified_since = {}
    src._full_listing = False
    src.listings_short_circuited = 0
    src.calls = 0

    seen_params: list[dict] = []

    def fake_get(path, **params):
        seen_params.append({"path": path, **params})
        if "/" in path:                      # a detail call
            return {"invoice": detail or {"invoice_id": path.split("/")[-1]}}
        return {"invoices": rows, "page_context": {"has_more_page": False}}

    src._get = fake_get
    src._seen_params = seen_params
    return src


def _row(doc_id: str, modified: str, doc_date: str = "2025-06-01") -> dict:
    return {"invoice_id": doc_id, "date": doc_date, "status": "paid",
            "last_modified_time": modified}


def _documents(src):
    return list(src._documents("invoices", "invoices", "invoice", "invoice_id",
                               set(), skip=None))


# ── the saving ──────────────────────────────────────────────────────────────
def test_a_listing_stops_at_the_high_water_mark():
    src = _source([
        _row("new-2", "2026-08-07T10:00:00+0530"),
        _row("new-1", "2026-08-06T10:00:00+0530"),
        _row("old-9", "2026-01-01T10:00:00+0530"),   # at/below the mark
        _row("old-8", "2025-12-31T10:00:00+0530"),
    ])
    src.modified_since = {"invoice": "2026-01-01T10:00:00+0530"}

    got = _documents(src)

    assert [d["invoice_id"] for d in got] == ["new-2", "new-1"]
    assert src.listings_short_circuited == 1


def test_an_incremental_listing_sorts_by_modification_time():
    """Stopping early is only sound if the newest-modified came first."""
    src = _source([_row("a", "2026-08-07T10:00:00+0530")])
    src.modified_since = {"invoice": "2026-01-01T10:00:00+0530"}

    _documents(src)

    assert src._seen_params[0]["sort_column"] == "last_modified_time"
    assert src._seen_params[0]["sort_order"] == "D"


def test_without_a_mark_it_lists_the_whole_window_by_date():
    """The first ever pull has no floor, and must not invent one."""
    src = _source([_row("a", "2026-08-07T10:00:00+0530")])

    _documents(src)

    assert src._seen_params[0]["sort_column"] == "date"
    assert src.listings_short_circuited == 0


def test_each_kind_stops_at_its_own_mark():
    """One stamp for the whole pull would skip a kind that moved less recently."""
    src = _source([_row("a", "2026-05-01T10:00:00+0530")])
    src.modified_since = {"bill": "2026-08-01T10:00:00+0530"}   # not "invoice"

    got = _documents(src)

    # The bill mark must not apply to invoices, so nothing is short-circuited.
    assert [d["invoice_id"] for d in got] == ["a"]
    assert src.listings_short_circuited == 0


# ── the danger ──────────────────────────────────────────────────────────────
def test_a_listing_that_stopped_early_is_not_reported_complete():
    """The whole safety property. `listing_complete` is what authorises the
    deletion sweep, and an incremental listing has not seen the book."""
    src = _source([
        _row("new-1", "2026-08-07T10:00:00+0530"),
        _row("old-9", "2026-01-01T10:00:00+0530"),
    ])
    src.modified_since = {"invoice": "2026-01-01T10:00:00+0530"}

    _documents(src)

    assert "invoice" not in src.listing_complete


def test_a_listing_that_ran_to_the_end_is_reported_complete():
    """An incremental pull where everything genuinely changed saw everything,
    so the sweep may run — the flag tracks what happened, not the mode."""
    src = _source([
        _row("a", "2026-08-07T10:00:00+0530"),
        _row("b", "2026-08-06T10:00:00+0530"),
    ])
    src.modified_since = {"invoice": "2020-01-01T10:00:00+0530"}   # nothing is below

    _documents(src)

    assert "invoice" in src.listing_complete
    assert src.listings_short_circuited == 0


def test_a_reconciliation_pass_ignores_the_mark_entirely():
    """`--reconcile`. The only mode that can notice a deleted document."""
    src = _source([
        _row("new-1", "2026-08-07T10:00:00+0530"),
        _row("old-9", "2026-01-01T10:00:00+0530"),
    ])
    src.modified_since = {"invoice": "2026-01-01T10:00:00+0530"}
    src._full_listing = True

    got = _documents(src)

    assert [d["invoice_id"] for d in got] == ["new-1", "old-9"]
    assert "invoice" in src.listing_complete
    assert src._seen_params[0]["sort_column"] == "date"


# ── which mode the sync asks for ────────────────────────────────────────────
class _Repo:
    def __init__(self, marks, covered):
        self.marks = marks
        self.covered = covered

    def ingested_high_water(self, kind):
        return self.marks.get(kind)

    def covered_since(self):
        return self.covered


#: The floor these tests run under. ``_source`` builds a window starting
#: 2024-01-01, so a connection covered from 2023 has already listed it — which
#: is the state every "nightly pull" test below is describing.
COVERED = date(2023, 1, 1)


def _svc(*, resume=True, incremental=True, marks=None, covered=COVERED):
    from app.ingestion.sync import SyncReport, SyncService

    svc = SyncService.__new__(SyncService)
    svc.resume = resume
    svc.incremental = incremental
    svc.repo = _Repo(marks or {}, covered)
    svc.source = None
    svc.report = SyncReport(organization_id="o")
    return svc


def test_a_nightly_pull_arms_every_kind_that_has_been_seen():
    svc = _svc(marks={"invoice": "2026-08-01", "bill": "2026-07-01"})
    src = _source([])

    marks = svc.arm_incremental_listing(src)

    assert marks == {"invoice": "2026-08-01", "bill": "2026-07-01"}
    assert src.modified_since == marks


def test_a_reconciliation_arms_nothing():
    """`incremental=False` is the weekly pass — it must list the whole book."""
    svc = _svc(incremental=False, marks={"invoice": "2026-08-01"})
    src = _source([])

    assert svc.arm_incremental_listing(src) == {}
    assert src.modified_since == {}


def test_a_rebuild_arms_nothing():
    """`resume=False` re-fetches everything; a floor would defeat it."""
    svc = _svc(resume=False, marks={"invoice": "2026-08-01"})
    src = _source([])

    assert svc.arm_incremental_listing(src) == {}


def test_a_kind_never_pulled_gets_no_mark():
    """No floor means "list everything", which is right for a first pull."""
    svc = _svc(marks={"invoice": "2026-08-01"})
    src = _source([])

    assert svc.arm_incremental_listing(src) == {"invoice": "2026-08-01"}
    assert "bill" not in src.modified_since


def test_a_row_with_no_stamp_never_stops_the_listing():
    """An unstamped row cannot be compared, and guessing would truncate the
    listing at the first oddity Zoho returned."""
    src = _source([
        _row("new-1", "2026-08-07T10:00:00+0530"),
        _row("odd", ""),
        _row("new-2", "2026-08-05T10:00:00+0530"),
    ])
    src.modified_since = {"invoice": "2026-01-01T10:00:00+0530"}

    got = _documents(src)

    assert [d["invoice_id"] for d in got] == ["new-1", "odd", "new-2"]


# ── the window the mark is allowed to speak for ─────────────────────────────
#
# The mark is `max(modified_at)` over everything held, with no notion of which
# window earned it. That is fine for a window already listed and catastrophic
# for one that has not been: an incremental listing sorts newest-modified first
# and stops at the mark, so every document in a newly-added *older* window is
# below it — older-modified precisely because it is older — and the listing
# stops on its first row. The run then reports success having fetched nothing,
# and asking again never helps, because the mark only moves forward.


def test_widening_the_window_backwards_lists_the_new_months_in_full():
    """The reported bug. Synced 2025 in January, want 2024 in August.

    Without this the backfill is a silent no-op: the operator picks an earlier
    date, the run goes green, and not one document arrives.
    """
    svc = _svc(marks={"invoice": "2026-08-01"}, covered=date(2025, 1, 1))
    src = _source([])
    src._since = date(2024, 1, 1)          # the window being added

    assert svc.arm_incremental_listing(src) == {}
    assert src.modified_since == {}
    # And it says so, rather than backfilling invisibly.
    assert svc.report.windows_listed_in_full == 1


def test_a_window_already_covered_keeps_the_short_circuit():
    """The saving this must not destroy. A widened pull re-lists only the new
    months; every month covered before still stops at the mark."""
    svc = _svc(marks={"invoice": "2026-08-01"}, covered=date(2025, 1, 1))
    src = _source([])
    src._since = date(2025, 6, 1)          # inside what has been covered

    assert svc.arm_incremental_listing(src) == {"invoice": "2026-08-01"}
    assert svc.report.windows_listed_in_full == 0


def test_the_window_that_starts_exactly_at_the_floor_is_covered():
    """The floor is the earliest date *listed*, not the first one not listed."""
    svc = _svc(marks={"invoice": "2026-08-01"}, covered=date(2025, 1, 1))
    src = _source([])
    src._since = date(2025, 1, 1)

    assert svc.arm_incremental_listing(src) == {"invoice": "2026-08-01"}


def test_a_connection_that_has_never_finished_a_run_covers_nothing():
    """Rows can say what was found; only a finished run can say where we
    looked. Until one has, every window is new."""
    svc = _svc(marks={"invoice": "2026-08-01"}, covered=None)
    src = _source([])

    assert svc.arm_incremental_listing(src) == {}
    assert svc.report.windows_listed_in_full == 1


def test_a_window_with_no_lower_bound_is_treated_as_uncovered():
    """Listing too much is a cost; listing too little is a hole. An
    uncomparable window takes the cost."""
    svc = _svc(marks={"invoice": "2026-08-01"}, covered=date(2020, 1, 1))
    src = _source([])
    src._since = None
    src._cutoff = lambda: None

    assert svc.arm_incremental_listing(src) == {}


# ── what history is actually held ───────────────────────────────────────────
def test_coverage_reads_finished_runs_only(tmp_path):
    """"Last synced 2 hours ago" does not answer "what history do I have".

    A nightly pull can run for a year and still cover only the window the first
    run asked for, so coverage is reported separately — and only a run that
    finished may contribute it. A run that died in window three of twenty
    covered three months, and reconstructing which three from ``windows_done``
    would be a second thing to keep in step with the loop that increments it.
    Forgetting a partial run costs a re-listing and never loses a document.
    """
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.domain import models
    from app.repositories import ReadModelRepository

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, future=True)()

    def _run(since, status):
        s.add(models.SyncRun(organization_id="o", connection_id="c1",
                             source="api", status=status, since=since,
                             started_at=datetime.now(timezone.utc)))

    _run(date(2025, 1, 1), "OK")
    _run(date(2023, 1, 1), "FAILED")     # asked for more, never delivered it
    _run(date(2024, 1, 1), "OK")
    s.commit()

    repo = ReadModelRepository(s, "o", connection_id="c1")
    # The earliest window a run actually finished — not the earliest requested.
    assert repo.covered_since() == date(2024, 1, 1)

    # And a connection of its own has its own floor: one company's history
    # says nothing about another's.
    assert ReadModelRepository(s, "o", connection_id="c2").covered_since() is None
    s.close()
