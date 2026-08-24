"""The inbound-demand record: what arrived, what happened to it, and both.

These are the properties the table exists for, rather than a walk of its
columns. Three of them are worth more than the rest:

* ``raw_text`` comes back **byte-identical**. Every downstream reader — the
  coverage denominator, the RFQ benchmark corpus, the unquoted-demand report —
  is wrong in a way nobody can see if a well-meaning ``strip()`` lands at the
  capture boundary. So the fixture text below is deliberately horrible.
* A correction keeps **both** answers readable. A coverage number that changed
  with no record of having changed cannot be defended.
* An export is scoped to **one tenant** and stops there. These rows are a
  customer's own words.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import String, inspect as sa_inspect
from sqlalchemy.exc import IntegrityError

from app import enquiry
from app.domain import enums, models

ORG = "org-sls"
OTHER = "org-4u"

#: Everything a normaliser would want to fix, in one string: leading and
#: trailing whitespace, a tab, CRLF, doubled spaces, mixed case, a non-ASCII
#: dimension sign, a zero-width space, and a decomposed accent that unicode
#: normalisation would silently recombine.
MESSY = (
    "  pls quote\tCNMG 120408\r\n"
    "  2 nos  ⌀12 mm  end mill​\n"
    "for Reńe's job — URGENT!!  "
)


def _capture(session, org=ORG, *, text=MESSY, channel=enums.InboundChannel.WHATSAPP,
             **kw):
    return enquiry.capture(session, org, raw_text=text, channel=channel, **kw)


# ── a line captures ─────────────────────────────────────────────────────────
def test_a_line_captures_and_comes_back(session):
    line = _capture(session, customer_ref="+91 98450 00000",
                    source_ref="wa:msg-4417")

    stored = session.get(models.InboundLine, line.inbound_line_id)
    assert stored.organization_id == ORG
    assert stored.channel == "WHATSAPP"
    assert stored.customer_ref == "+91 98450 00000"
    assert stored.source_ref == "wa:msg-4417"
    assert stored.received_at is not None and stored.captured_at is not None


def test_the_raw_text_is_stored_byte_for_byte(session):
    """The single property the whole table rests on."""
    line = _capture(session)
    session.expire_all()  # force a real read back from the database

    stored = session.get(models.InboundLine, line.inbound_line_id)
    assert stored.raw_text == MESSY
    assert stored.raw_text.encode("utf-8") == MESSY.encode("utf-8")
    # Named individually, because each is a normalisation somebody could think
    # was harmless on its way in.
    assert stored.raw_text.startswith("  pls")
    assert stored.raw_text.endswith("!!  ")
    assert "\r\n" in stored.raw_text
    assert "\t" in stored.raw_text
    assert "​" in stored.raw_text
    assert "́" in stored.raw_text, "no NFC recombination"


def test_capture_never_touches_the_string_it_was_given(session):
    """Not merely equal — the same object. A copy is where a cleaner hides."""
    line = _capture(session, text=MESSY)
    assert line.raw_text is MESSY


def test_a_backlog_import_keeps_the_day_the_customer_sent_it(session):
    """``received_at`` is the business fact; ``captured_at`` is when we learned
    it. Collapsing them files a year of demand on one afternoon."""
    sent = datetime(2026, 3, 11, 6, 30, tzinfo=timezone.utc)
    line = _capture(session, received_at=sent, channel=enums.InboundChannel.EMAIL)

    assert line.received_at == sent
    assert line.captured_at > sent


def test_the_same_ask_twice_is_two_lines(session):
    """Repeat demand is demand. A content key here would under-report it."""
    first = _capture(session, source_ref="wa:1")
    second = _capture(session, source_ref="wa:2")

    assert first.inbound_line_id != second.inbound_line_id
    assert len(enquiry.export(session, ORG)) == 2


@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_an_empty_line_is_refused(session, bad):
    """It would inflate the coverage denominator with an ask nobody made."""
    with pytest.raises(enquiry.CaptureRefusal):
        _capture(session, text=bad)


def test_an_unknown_channel_is_refused_and_names_the_closed_set(session):
    with pytest.raises(enquiry.CaptureRefusal) as caught:
        _capture(session, channel="CARRIER_PIGEON")
    assert "WHATSAPP" in str(caught.value)


def test_a_line_with_no_organization_is_refused(session):
    with pytest.raises(enquiry.CaptureRefusal):
        _capture(session, org="")


# ── the disposition sets, and later supersedes ──────────────────────────────
def test_an_undecided_line_has_no_disposition_at_all(session):
    """Absent, not 'pending' and not a benign failure. Absence is UNKNOWN."""
    line = _capture(session)

    assert enquiry.live_disposition(session, ORG, line.inbound_line_id) is None
    assert enquiry.disposition_history(session, ORG, line.inbound_line_id) == []
    assert enquiry.export(session, ORG)[0].disposition is None


def test_a_disposition_sets(session):
    line = _capture(session)

    row, written = enquiry.set_disposition(
        session, ORG, line.inbound_line_id, enums.LineDisposition.NO_STOCK,
        source_ref="stock check 11 Mar", decided_by_user_id="u-7")

    assert written is True
    assert row.disposition == "NO_STOCK"
    assert row.source_ref == "stock check 11 Mar"
    assert row.decided_by_user_id == "u-7"
    assert row.superseded_at is None
    assert enquiry.live_disposition(
        session, ORG, line.inbound_line_id).disposition_id == row.disposition_id


def test_a_correction_supersedes_and_both_versions_stay_readable(session):
    """The acceptance property: superseded, never mutated."""
    line = _capture(session)
    first, _ = enquiry.set_disposition(session, ORG, line.inbound_line_id,
                                       enums.LineDisposition.NO_STOCK)
    second, written = enquiry.set_disposition(session, ORG, line.inbound_line_id,
                                              enums.LineDisposition.QUOTED,
                                              source_ref="quote q31")

    assert written is True
    assert first.disposition_id != second.disposition_id, "a new row, not an edit"

    session.expire_all()
    history = enquiry.disposition_history(session, ORG, line.inbound_line_id)
    assert [r.disposition for r in history] == ["NO_STOCK", "QUOTED"]
    # The old claim is intact — it is still NO_STOCK, only marked replaced.
    assert history[0].disposition == "NO_STOCK"
    assert history[0].superseded_at is not None
    assert history[1].superseded_at is None
    assert enquiry.live_disposition(
        session, ORG, line.inbound_line_id).disposition == "QUOTED"


def test_only_one_disposition_is_ever_live(session):
    """Three corrections, one live row — the partial unique index's job."""
    line = _capture(session)
    for verdict in (enums.LineDisposition.NO_PRICE,
                    enums.LineDisposition.QUOTED,
                    enums.LineDisposition.LOST):
        enquiry.set_disposition(session, ORG, line.inbound_line_id, verdict)

    history = enquiry.disposition_history(session, ORG, line.inbound_line_id)
    assert len(history) == 3
    assert len([r for r in history if r.superseded_at is None]) == 1
    assert history[-1].disposition == "LOST"


def test_re_asserting_the_same_verdict_writes_nothing(session):
    """A nightly NO_RESPONSE sweep must not append a row every night."""
    line = _capture(session)
    first, _ = enquiry.set_disposition(session, ORG, line.inbound_line_id,
                                       enums.LineDisposition.NO_RESPONSE,
                                       source_ref="14-day deadline")
    again, written = enquiry.set_disposition(session, ORG, line.inbound_line_id,
                                             enums.LineDisposition.NO_RESPONSE,
                                             source_ref="14-day deadline")

    assert written is False
    assert again.disposition_id == first.disposition_id
    assert len(enquiry.disposition_history(session, ORG, line.inbound_line_id)) == 1


def test_an_unknown_disposition_is_refused(session):
    line = _capture(session)
    with pytest.raises(enquiry.CaptureRefusal) as caught:
        enquiry.set_disposition(session, ORG, line.inbound_line_id, "PENDING")
    assert "ABSTAINED" in str(caught.value)


def test_a_disposition_for_another_tenants_line_is_refused(session):
    """The numerator and the denominator have to be the same population."""
    line = _capture(session, org=ORG)
    with pytest.raises(enquiry.CaptureRefusal):
        enquiry.set_disposition(session, OTHER, line.inbound_line_id,
                                enums.LineDisposition.QUOTED)
    assert enquiry.live_disposition(session, ORG, line.inbound_line_id) is None


def test_a_disposition_for_a_line_that_does_not_exist_is_refused(session):
    with pytest.raises(enquiry.CaptureRefusal):
        enquiry.set_disposition(session, ORG, "no-such-line",
                                enums.LineDisposition.QUOTED)


# ── the export ──────────────────────────────────────────────────────────────
def test_the_export_returns_the_full_set_with_raw_text_intact(session):
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    texts = [f"{MESSY}#{n}" for n in range(5)]
    for n, text in enumerate(texts):
        _capture(session, text=text, received_at=base + timedelta(days=n))

    exported = enquiry.export(session, ORG)

    assert len(exported) == 5, "the whole set, not a page"
    assert [row.raw_text for row in exported] == texts
    for row in exported:
        assert row.raw_text.startswith("  pls") and "\r\n" in row.raw_text


def test_the_export_is_scoped_to_one_tenant(session):
    ours = _capture(session, org=ORG, text="ours: CNMG 120408")
    _capture(session, org=OTHER, text="theirs: a competitor's whole order book")

    exported = enquiry.export(session, ORG)

    assert [row.inbound_line_id for row in exported] == [ours.inbound_line_id]
    assert all("theirs" not in row.raw_text for row in exported)


def test_the_export_carries_the_live_verdict_and_the_superseded_one(session):
    line = _capture(session)
    enquiry.set_disposition(session, ORG, line.inbound_line_id,
                            enums.LineDisposition.ABSTAINED)
    enquiry.set_disposition(session, ORG, line.inbound_line_id,
                            enums.LineDisposition.QUOTED, source_ref="quote q9")

    row = enquiry.export(session, ORG)[0]

    assert row.disposition == "QUOTED"
    assert row.disposition_decided_at is not None
    assert [r.disposition for r in row.disposition_history] == ["ABSTAINED", "QUOTED"]


def test_the_export_order_is_total_and_repeatable(session):
    """Two lines received in the same instant still export in one fixed order."""
    same = datetime(2026, 3, 4, 9, 0, tzinfo=timezone.utc)
    for n in range(6):
        _capture(session, text=f"line {n}", received_at=same)

    first = [row.inbound_line_id for row in enquiry.export(session, ORG)]
    session.expire_all()
    second = [row.inbound_line_id for row in enquiry.export(session, ORG)]

    assert first == second
    assert first == sorted(first), "received_at ties break on the id"


# ── what these rows may never hold ──────────────────────────────────────────
#: Cost, margin and price are absent by construction, not by review. Both tables
#: are checked, and so is the export dataclass, because the export is the
#: surface most likely to leave the building.
_FORBIDDEN = ("cost", "margin", "price", "profit", "discount", "amount",
              "value", "rate")


@pytest.mark.parametrize("model", [models.InboundLine,
                                   models.InboundLineDisposition])
def test_no_column_here_is_economics(model):
    offenders = [c.key for c in sa_inspect(model).columns
                 if any(word in c.key.lower() for word in _FORBIDDEN)]
    assert offenders == [], (
        f"{model.__tablename__} grew an economics column: {offenders}. A line's "
        "economics belong to the quote it became (§1)")


def test_the_export_shape_is_economics_free():
    offenders = [name for name in enquiry.ExportedLine.__dataclass_fields__
                 if any(word in name.lower() for word in _FORBIDDEN)]
    assert offenders == []


def test_every_row_is_scoped_to_an_organization():
    for model in (models.InboundLine, models.InboundLineDisposition):
        assert "organization_id" in sa_inspect(model).columns


# ── the columns are wide enough for what is written to them ─────────────────
def test_the_captured_values_fit_their_declared_columns(session):
    """``String(n)`` is enforced by Postgres and ignored by SQLite, so the check
    is against the *declared* width — see ``test_column_widths``."""
    line = _capture(session, customer_ref="x" * 255, source_ref="y" * 255)
    row, _ = enquiry.set_disposition(session, ORG, line.inbound_line_id,
                                     enums.LineDisposition.NO_RESPONSE,
                                     source_ref="z" * 255)

    for record in (line, row):
        for column in sa_inspect(type(record)).columns:
            # ``Text`` is a ``String`` with no declared length — unbounded on
            # both backends, which is exactly why ``raw_text`` is one.
            if not isinstance(column.type, String) or column.type.length is None:
                continue
            value = getattr(record, column.key, None)
            if isinstance(value, str):
                assert len(value) <= column.type.length, (
                    f"{record.__tablename__}.{column.key}")


def test_the_widest_disposition_name_fits(session):
    """A seventh member longer than the column is the failure this catches."""
    widest = max(len(d.value) for d in enums.LineDisposition)
    declared = sa_inspect(models.InboundLineDisposition).columns["disposition"].type
    assert widest <= declared.length


def test_the_database_itself_refuses_a_second_live_disposition(session):
    """Not just the store: the partial unique index is the guarantee.

    Written past ``set_disposition`` on purpose. The store supersedes correctly,
    and a future second writer that forgot to would produce two live rows and a
    line with two current answers — which is the thing the index makes
    impossible rather than merely unlikely.
    """
    line = _capture(session)
    enquiry.set_disposition(session, ORG, line.inbound_line_id,
                            enums.LineDisposition.QUOTED)

    session.add(models.InboundLineDisposition(
        organization_id=ORG,
        inbound_line_id=line.inbound_line_id,
        disposition="LOST",
        decided_at=datetime.now(timezone.utc),
        recorded_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_a_superseded_row_does_not_block_the_next_one(session):
    """The other half: the index is unique over the *live* rows only, or the
    correction this table exists for would be refused."""
    line = _capture(session)
    for verdict in ("NO_STOCK", "NO_PRICE", "QUOTED", "LOST"):
        enquiry.set_disposition(session, ORG, line.inbound_line_id, verdict)
    session.flush()

    assert len(enquiry.disposition_history(session, ORG, line.inbound_line_id)) == 4
