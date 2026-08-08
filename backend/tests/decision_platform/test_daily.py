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
