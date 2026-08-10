"""The morning read.

Two things are worth pinning here and the rest is assembly. First, that the
page reports its own age honestly — the whole view is a claim about *now*, made
from a mirror that is only as current as the last sync, and a stale one has to
say so rather than presenting Friday as Tuesday. Second, that it does not
invent numbers: every figure comes from the builder that owns it, so a tile
must equal its source and not merely resemble it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.commercial.insight import daily


def _sync(hours_ago: float, status: str = "SUCCESS") -> dict:
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"status": status, "started_at": when.isoformat(),
            "finished_at": when.isoformat()}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── freshness ────────────────────────────────────────────────────────────────
def test_a_recent_sync_reads_as_current():
    f = daily.freshness(now=_now(), last_sync=_sync(2), state_on=date(2026, 8, 1))
    assert f["stale"] is False
    assert "current" in f["headline"].lower()


def test_a_sync_older_than_the_threshold_says_which_days_picture_this_is():
    f = daily.freshness(now=_now(), last_sync=_sync(daily.STALE_AFTER_HOURS + 5),
                        state_on=None)
    assert f["stale"] is True
    # The point is not that a flag is set — it is that the sentence tells an
    # operator the numbers below belong to another day.
    assert "not today" in f["headline"].lower()


def test_never_synced_says_so_rather_than_reporting_an_age():
    f = daily.freshness(now=_now(), last_sync=None, state_on=None)
    assert f["stale"] is True
    assert f["age_hours"] is None
    assert "nothing has been synced" in f["headline"].lower()


def test_a_failed_sync_is_not_reported_as_a_fresh_one():
    """A run that failed two minutes ago is recent and worthless.

    Age alone would call this the most current page in the product.
    """
    f = daily.freshness(now=_now(), last_sync=_sync(0.2, status="FAILED"),
                        state_on=None)
    assert "failed" in f["headline"].lower()


def test_a_naive_timestamp_is_still_comparable():
    """SQLite keeps no offset, so half the timestamps in this database are
    naive. Subtracting one from an aware ``now()`` raises rather than lying —
    good, but only if something exercises it."""
    naive = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(tzinfo=None)
    f = daily.freshness(now=_now(),
                        last_sync={"status": "SUCCESS",
                                   "finished_at": naive.isoformat()},
                        state_on=None)
    assert f["age_hours"] is not None
    assert 2 < f["age_hours"] < 4


# ── the tiles come from their sources ────────────────────────────────────────
def test_every_tile_reports_its_source_number_exactly():
    out = daily.assemble(
        now=_now(), as_of=date(2026, 8, 1), state_on=date(2026, 8, 1),
        last_sync=_sync(1),
        approvals_pending=3,
        decisions_by_band={"HIGH": 2, "MEDIUM": 5},
        stock={"counts": {"oversold": 4}},
        supply={"counts": {"open_orders": 10, "stale_open_orders": 6}},
        cadence={"overdue_count": 11},
        cash={"overdue": {"inflow": 1200.0, "outflow": 800.0},
              "buckets": [{"inflow": 500.0, "outflow": 250.0}]},
        moved={"invoices": {"count": 7, "amount": 900.0, "by_company": []}})

    tiles = {t["key"]: t for b in out["bands"] for t in b["tiles"]}
    assert tiles["approvals"]["count"] == 3
    assert tiles["decisions"]["count"] == 7          # the bands summed
    assert tiles["past_cycle"]["count"] == 11
    assert tiles["oversold"]["count"] == 4
    assert tiles["ageing_orders"]["count"] == 6
    assert tiles["open_orders"]["count"] == 10
    assert tiles["overdue_cash"]["amount"] == 1200.0
    assert tiles["overdue_cash"]["amount_out"] == 800.0
    assert tiles["cash_this_week"]["amount"] == 500.0
    assert tiles["invoices"]["count"] == 7


def test_a_quiet_book_marks_its_tiles_settled_rather_than_hiding_them():
    """Nothing wrong is an answer, and the tile still has to be there to say
    it. A page that drops its clean tiles cannot be distinguished from one
    that failed to load them."""
    out = daily.assemble(now=_now(), as_of=None, state_on=None, last_sync=_sync(1))
    tiles = {t["key"]: t for b in out["bands"] for t in b["tiles"]}
    assert tiles["approvals"]["settled"] is True
    assert tiles["past_cycle"]["settled"] is True
    assert out["wants_attention"] == 0
    # Every band still renders.
    assert {b["key"] for b in out["bands"]} == {
        daily.NEEDS_YOU, daily.AT_RISK, daily.COMMITTED, daily.MOVED}


def test_wants_attention_counts_tiles_and_never_sums_money():
    """Dead stock and overdue cash are different claims. Adding them produces a
    figure that means nothing and invites a decision against it, which is why
    the headline is a count of tiles."""
    out = daily.assemble(
        now=_now(), as_of=None, state_on=None, last_sync=_sync(1),
        cadence={"overdue_count": 11},
        cash={"overdue": {"inflow": 5_000_000.0, "outflow": 0.0}})
    assert out["wants_attention"] == 2


def test_every_tile_offers_a_way_in():
    """A number you cannot click is a dashboard. The route vocabulary is the
    one ``vizPath`` already resolves."""
    out = daily.assemble(now=_now(), as_of=None, state_on=None, last_sync=_sync(1))
    for band in out["bands"]:
        for tile in band["tiles"]:
            assert tile["route"], f"{tile['key']} has nowhere to go"


# ── the breakdown ────────────────────────────────────────────────────────────
def test_the_per_company_split_adds_up_to_its_own_headline():
    out = daily.assemble(
        now=_now(), as_of=None, state_on=None, last_sync=_sync(1),
        moved={"customers": {"count": 9, "by_company": [
            {"company": "SLS", "count": 5}, {"company": "UPS", "count": 4}]}})
    tile = next(t for b in out["bands"] for t in b["tiles"] if t["key"] == "customers")
    assert sum(r["value"] for r in tile["breakdown"]) == tile["count"]


def test_a_zero_row_is_dropped_from_the_breakdown():
    out = daily.assemble(
        now=_now(), as_of=None, state_on=None, last_sync=_sync(1),
        moved={"products": {"count": 0, "by_company": [
            {"company": "Source not recorded", "count": 0}]}})
    tile = next(t for b in out["bands"] for t in b["tiles"] if t["key"] == "products")
    assert tile["breakdown"] == []


# ── the ingest window ────────────────────────────────────────────────────────
def test_the_window_starts_where_the_previous_sync_finished():
    """Not where the latest one *started*: a sync writes throughout its run, so
    anchoring on its start would re-report rows the run before it already
    counted."""
    now = _now()
    prev_end = now - timedelta(hours=26)
    start, until = daily.window_since(
        {"started_at": (now - timedelta(hours=2)).isoformat()},
        {"finished_at": prev_end.isoformat()},
        now=now)
    assert abs((start - prev_end).total_seconds()) < 2
    assert until == now


def test_with_no_previous_sync_the_window_is_the_last_day():
    """Rather than every row the platform has ever held, which would read as a
    flood on the first morning and be true of nothing."""
    now = _now()
    start, _ = daily.window_since(None, None, now=now)
    assert abs((now - start).total_seconds() - 86400) < 5


# ── picking a day, or a range ───────────────────────────────────────────────
#
# The window governs the MOVED band and nothing else, and that limit is the
# design rather than an unfinished edge. The other three bands are not periods:
# an approval is waiting *now*, an invoice is overdue *now*, a commitment lands
# in the seven days *from now*. "What was overdue last Tuesday" would mean
# reconstructing a past state this platform does not keep, so a control that
# spanned the page would return three numbers that either ignored it or lied.


def test_a_chosen_day_covers_the_whole_of_that_day():
    """A reader picking "the 7th" means all of the 7th. A half-open bound would
    silently drop everything that arrived after midnight — which, since the
    band counts ingest time and syncs run in the morning, is most of it."""
    now = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
    start, end = daily.window_since(None, None, now=now, frm=date(2026, 8, 7))

    assert start == datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
    assert end == now                      # open at the top for a single day


def test_a_range_is_inclusive_at_both_ends():
    now = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
    start, end = daily.window_since(None, None, now=now,
                                    frm=date(2026, 8, 1), to=date(2026, 8, 7))

    assert start == datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    assert end.date() == date(2026, 8, 7)
    assert (end.hour, end.minute) == (23, 59)


def test_no_range_still_means_since_the_previous_sync():
    """The default is unchanged. A control nobody has touched must not quietly
    move the numbers that were there before it existed."""
    now = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
    previous = {"finished_at": "2026-08-07T18:00:00+00:00"}
    start, end = daily.window_since({"started_at": "2026-08-08T08:00:00+00:00"},
                                    previous, now=now)

    assert start == datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc)
    assert end == now


def test_the_band_says_which_window_it_is_reporting_over():
    """Otherwise the subtitle still reads "what the last sync brought in" while
    the numbers under it came from a fortnight in March — the screen
    contradicting itself in its own heading."""
    assert daily.moved_question(None) is None
    assert daily.moved_question((None, None)) is None

    # A range whose ends are the same day reads as a day, not as a range.
    assert daily.moved_question((date(2026, 8, 7), date(2026, 8, 7))) == (
        "What the platform first saw on 2026-08-07")
    # Open at the top is a *range* running to now, and calling it a day is how
    # a live screen came to say "first saw on 2026-07-10" over a month of rows.
    assert daily.moved_question((date(2026, 7, 10), None)) == (
        "What the platform first saw since 2026-07-10")

    assert daily.moved_question((date(2026, 8, 1), date(2026, 8, 7))) == (
        "What the platform first saw between 2026-08-01 and 2026-08-07")


def test_only_the_moved_band_takes_the_window():
    """The claim the docstring makes, asserted rather than trusted."""
    out = daily.assemble(
        now=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
        as_of=date(2026, 8, 8), state_on=date(2026, 8, 8), last_sync=None,
        moved_window=(date(2026, 8, 1), date(2026, 8, 7)))

    by_key = {b["key"]: b for b in out["bands"]}
    assert by_key["MOVED"]["question"].startswith("What the platform first saw")
    # Every other band keeps the question it always had — none of them is a
    # period, so none of them may appear to have been re-scoped.
    for key in ("NEEDS_YOU", "AT_RISK", "COMMITTED"):
        assert by_key[key]["question"] == daily.BAND_QUESTION[key]

    # And the window is echoed, so the control can show what was actually used.
    assert out["moved_window"] == {"from": "2026-08-01", "to": "2026-08-07"}


# ── the committed band's own horizon ────────────────────────────────────────
#
# A separate control from the moved window because it is a separate axis:
# forward over dates documents already carry, rather than backward over when
# the platform learned of a row. Counted in weeks because the cash fold is
# bucketed by ISO week — an arbitrary range would have to be answered
# approximately, which is precision the data does not have.


def _cash(*weeks: tuple[float, float]) -> dict:
    return {"buckets": [{"inflow": i, "outflow": o} for i, o in weeks]}


def _committed_tile(cash: dict, weeks: int = 1) -> dict:
    out = daily.assemble(
        now=datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc),
        as_of=date(2026, 8, 8), state_on=date(2026, 8, 8), last_sync=None,
        cash=cash, committed_weeks=weeks)
    band = next(b for b in out["bands"] if b["key"] == "COMMITTED")
    return {"band": band,
            "tile": next(t for t in band["tiles"] if t["key"] == "cash_this_week")}


def test_one_week_reads_exactly_as_it_always_did():
    """The default must not move. A control nobody has touched changes nothing."""
    got = _committed_tile(_cash((100.0, 40.0)))
    assert got["tile"]["label"] == "Cash due this week"
    assert got["tile"]["amount"] == 100.0 and got["tile"]["amount_out"] == 40.0
    assert got["band"]["question"] == daily.BAND_QUESTION[daily.COMMITTED]


def test_a_longer_horizon_sums_the_weeks_rather_than_reading_the_first():
    """Reading `buckets[0]` was right for a one-week horizon and would silently
    under-report every longer one — the tile would show week one's cash under a
    heading promising a quarter."""
    got = _committed_tile(_cash((100.0, 40.0), (50.0, 10.0), (25.0, 5.0)),
                          weeks=3)
    assert got["tile"]["amount"] == 175.0
    assert got["tile"]["amount_out"] == 55.0
    assert got["tile"]["label"] == "Cash due in 3 weeks"
    assert got["band"]["question"] == "What lands in the next 3 weeks"


def test_the_committed_horizon_is_counted_in_weeks_not_days():
    """The fold buckets by ISO week from the Monday `as_of` falls in, so days
    would promise a precision it does not carry."""
    assert daily.committed_question(1) is None
    assert daily.committed_question(4) == "What lands in the next 4 weeks"


def test_an_empty_committed_book_still_names_the_horizon_that_was_asked_for():
    """Caught by rendering it. The label used to come from ``len(buckets)``, so
    a book with no cash fold yet snapped the heading back to "this week" while
    the control beside it still read 8w — the screen disagreeing with its own
    input, which is worse than admitting the horizon is empty."""
    got = _committed_tile({"buckets": []}, weeks=8)
    assert got["tile"]["label"] == "Cash due in 8 weeks"
    assert got["band"]["question"] == "What lands in the next 8 weeks"
    assert got["tile"]["settled"] is True


# ── the wiring, not the builder ──────────────────────────────────────────────
def test_the_morning_read_is_reachable_over_http(api_client):
    """`GET /insight/daily` returns a page rather than a 500.

    Everything above this line tests `assemble` directly, and `assemble` was
    fine. The router called it with a `th=` keyword it does not accept and has
    never accepted — `_envelope` takes the thresholds object, the builder does
    not — so every request to the morning read raised `TypeError` and the
    landing page of the product was a 500 for both roles that can open it.

    It survived because the unit was covered and the wiring was not: a builder
    tested through its own function signature cannot fail the way a call site
    fails. This test is deliberately the shallowest possible assertion on that
    call site, because depth is not what was missing.
    """
    token = api_client.post("/api/v1/auth/login", json={
        "email": "s.menon@sanketh.in", "password": "change-me-now"}).json()["token"]

    r = api_client.get("/api/v1/insight/daily",
                       headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    # Freshness is its own key rather than a band — the page's claim about its
    # own age, which is the first thing the module's docstring insists on.
    assert body["freshness"]
    assert body["bands"][0]["key"] == daily.NEEDS_YOU
    # `_envelope` exists to stamp this on every manager-facing payload, and a
    # response that 500s stamps nothing.
    assert body["thresholds_version"]
    # The tile the queue's own scope now feeds. Present and countable on an
    # empty book, rather than absent because nothing had been detected yet.
    tiles = {t["key"]: t for b in body["bands"] for t in b["tiles"]}
    assert tiles["decisions"]["count"] == 0
