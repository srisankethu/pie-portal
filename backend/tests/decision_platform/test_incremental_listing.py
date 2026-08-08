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
    def __init__(self, marks):
        self.marks = marks

    def ingested_high_water(self, kind):
        return self.marks.get(kind)


def _svc(*, resume=True, incremental=True, marks=None):
    from app.ingestion.sync import SyncService

    svc = SyncService.__new__(SyncService)
    svc.resume = resume
    svc.incremental = incremental
    svc.repo = _Repo(marks or {})
    svc.source = None
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
