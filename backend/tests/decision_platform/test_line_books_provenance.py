"""Where a line's books facts came from, said on the line.

Three adapters answer ``get_item`` — the connected ledger read live, the last
pull's synced master, and the offline stand-in — and until now a line could
not say which one had answered it, which quantity its stock figure was, or
when a live read happened. A line is persisted whole and served again on
every open, so a hashed 20, a synced 20 and a week-old live 20 were the same
glyphs; the only cue was a quote-level alert keyed on a process-wide flag.
These pin the three fields that say so, for both roles, and the two
placeholder descriptions the engine used to write into ``reqDesc``.
"""
from __future__ import annotations

from app.store import Line, QuoteStore
from app.zoho import MockZoho, ZohoItem

READ_AT = "2026-09-27T04:10:00+00:00"
PULLED_AT = "2026-09-12T06:00:00+00:00"


def _line(**over) -> Line:
    base = dict(id="l1", raw="2001174 x10", reqCode="2001174", reqDesc="",
                reqQty=10, rel="EXACT", supplyCode="2001174", candidates=[],
                outcome="EXACT", semantics="KNOWN")
    base.update(over)
    return Line(**base)


def _item(**over) -> ZohoItem:
    base = dict(code="2001174", name="CNMG 120408-49 TN2000", in_books=True,
                list_price=420.0, stock=20, cost=None)
    base.update(over)
    return ZohoItem(**base)


class _Adapter:
    """One answer, whatever is asked — the adapter under test is the line."""

    available = True

    def __init__(self, item: ZohoItem) -> None:
        self._item = item
        self.created: list[tuple] = []

    def get_item(self, code: str) -> ZohoItem:
        return self._item

    def create_item(self, code: str, name: str, list_price=None) -> ZohoItem:
        self.created.append((code, name, list_price))
        return ZohoItem(code=code, name=name, in_books=True, list_price=list_price,
                        stock=None, cost=None, item_id="new-1")


# ── which adapter answered ───────────────────────────────────────────────────

def test_a_live_read_is_named_live_with_the_moment_it_happened():
    ln = _line()
    QuoteStore()._enrich_from_zoho(ln, _Adapter(_item(stock_kind="ON_HAND", read_at=READ_AT)))
    assert (ln.booksSource, ln.stockKind, ln.booksReadAt) == ("LIVE", "ON_HAND", READ_AT)
    assert ln.booksAsOf is None, "a live answer invents no pull date"


def test_a_synced_answer_is_named_synced_as_of_the_pull():
    ln = _line()
    QuoteStore()._enrich_from_zoho(ln, _Adapter(_item(stock_kind="AVAILABLE", as_of=PULLED_AT)))
    assert (ln.booksSource, ln.stockKind) == ("SYNCED", "AVAILABLE")
    assert (ln.booksAsOf, ln.booksReadAt) == (PULLED_AT, None)


def test_the_stand_in_is_named_demo_and_claims_no_read():
    ln = _line()
    QuoteStore()._enrich_from_zoho(ln, MockZoho())
    assert ln.booksSource == "DEMO"
    assert ln.booksReadAt is None, "a stamp would claim a read that did not happen"
    assert ln.stockKind == ("AVAILABLE" if ln.avail is not None else None)


def test_an_unknown_figure_has_no_kind_but_still_says_who_was_asked():
    ln = _line()
    QuoteStore()._enrich_from_zoho(ln, _Adapter(_item(stock=None, stock_kind=None, read_at=READ_AT)))
    assert ln.avail is None and ln.stockKind is None
    assert (ln.booksSource, ln.booksReadAt) == ("LIVE", READ_AT)
    assert ln.to_dict(False)["availUnknown"] is True


def test_a_code_the_ledger_does_not_hold_is_absent_as_of_now():
    ln = _line()
    QuoteStore()._enrich_from_zoho(ln, _Adapter(_item(
        in_books=False, list_price=None, stock=None, read_at=READ_AT)))
    assert ln.inBooks is False
    assert (ln.booksSource, ln.booksReadAt) == ("LIVE", READ_AT)


def test_both_roles_read_the_sources_and_stamps_and_neither_reads_a_value():
    ln = _line()
    QuoteStore()._enrich_from_zoho(ln, _Adapter(_item(
        stock_kind="AVAILABLE", read_at=READ_AT, cost=318.5)))
    for mgmt in (False, True):
        d = ln.to_dict(mgmt)
        assert (d["booksSource"], d["stockKind"], d["booksReadAt"]) == ("LIVE", "AVAILABLE", READ_AT)
    assert "economics" not in ln.to_dict(False)


def test_a_row_persisted_before_the_fields_existed_is_not_read_as_live():
    """Inferring LIVE from ``booksAsOf is None`` would be the re-derived
    predicate CLAUDE.md §1 describes: an old row says nothing, and must read
    as nothing."""
    old = _line().to_state()
    for key in ("booksSource", "stockKind", "booksReadAt"):
        old.pop(key)
    back = Line.from_state(old)
    assert (back.booksSource, back.stockKind, back.booksReadAt) == (None, None, None)


# ── the engine writes no status into a description ──────────────────────────

def test_no_catalogue_is_a_state_not_a_description():
    from app.pie_service import pie_service
    res = pie_service.resolve("CNMG 120408 provenance x10", connection_id=None)
    assert res.rel == "UNRESOLVED"
    assert res.reqDesc == "", "the grid used to read 'No catalogue' as the product's caption"
    assert any("no decoded catalogue" in note for note in res.notes)


def test_an_engine_failure_is_a_state_not_a_description(monkeypatch):
    from app import pie_service as ps

    class _View:
        path = "/nowhere"

    monkeypatch.setattr(ps.pie_service, "_view", lambda connection_id: _View())

    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(ps.pie_service, "_make_args", boom)
    res = ps.pie_service.resolve("CNMG 120408 engine-down x10", connection_id="cx_any")
    assert res.rel == "PIE_DOWN" and res.pie_offline
    assert res.reqDesc == "", "the grid used to read 'Awaiting PIE' as the product's caption"


def test_the_ledger_never_receives_a_status_as_an_items_name():
    """With ``reqDesc`` empty on a line the engine never answered, the name
    handed to ``create_item`` falls back to the code — never to the request's
    description, which used to hold the engine's status."""
    ln = _line(reqDesc="", supplyDesc="", inBooks=False)
    adapter = _Adapter(_item())
    assert QuoteStore().create_item(ln, adapter) == ""
    assert adapter.created == [("2001174", "2001174", None)]
