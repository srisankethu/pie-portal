"""An empty group is not good news when nothing could have been in it.

``08-intermittent-demand.md`` measured the reorder level across both connected
books: **0 of 3,200 SLS items and 0 of 473 active 4U items** — 0 of 3,673. So
``below_reorder`` is a predicate that is structurally ``False``, ``BELOW_REORDER``
is a group that cannot contain a row, and the shelf screen rendered that as

    Nothing here. That is the good answer.

which is `CLAUDE.md` §1's *absence of evidence is not a pass* with a reassuring
sentence attached. The count existed — ``counts["no_reorder_point"]`` and a
``COLLECTABLE`` line in ``unavailable`` — at the foot of the screen, under the
group that had just told the reader everything was fine.

What these tests hold is the fix and its two edges: the finding is a headline the
reader can drill into, the empty group says why it is empty, and **the refusal to
invent a reorder point is untouched**. Reporting that nobody has chosen a
stocking policy is the opposite of choosing one for them.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.commercial.insight import stock

AS_OF = date(2026, 8, 10)
CARRYING = stock.Carrying(annual_pct=0.18, dead_days=365, slow_days=180)


def _line(product_id: str = "p1", **over) -> stock.StockLine:
    fields = dict(
        product_id=product_id, label=f"CNMG {product_id}", on_hand=100.0,
        available=100.0, actual_available=100.0, reorder_level=None,
        last_sold=date(2026, 8, 1), sold_qty_window=200.0,
        purchase_rate=401.25, first_sold=AS_OF - timedelta(days=200))
    fields.update(over)
    return stock.StockLine(**fields)


def _build(lines, *, with_cost: bool = True) -> dict:
    return stock.build(lines, AS_OF, CARRYING, with_cost=with_cost)


def _card(built: dict, key: str):
    return next((c for c in built["kpis"] if c["key"] == key), None)


def _group(built: dict, key: str) -> dict:
    return next(g for g in built["groups"] if g["key"] == key)


def test_a_book_with_no_stocking_policy_says_so_as_a_headline():
    """The finding `08-intermittent-demand.md` asked to be promoted."""
    built = _build([_line("p1"), _line("p2"), _line("p3")])

    card = _card(built, "NO_REORDER_POINT")
    assert card is not None, (
        "0 of 3 lines carry a reorder point and the screen says nothing about "
        "it above the fold — which is the state this change exists to end")
    assert card["value"] == 3
    assert "Every line on the shelf" in card["note"]


def test_the_empty_reorder_group_says_why_rather_than_congratulating_anyone():
    """The sentence that stops a structurally-empty group reading as health."""
    built = _build([_line("p1"), _line("p2"), _line("p3")])
    group = _group(built, "BELOW_REORDER")

    assert group["count"] == 0
    assert group["empty_means"], (
        "no item has a reorder point, so this group cannot contain a row — and "
        "with no explanation the client renders 'That is the good answer.'")
    # It names the population, because "empty" without a denominator is the
    # same non-statement in different words.
    assert "0 of 3" in group["empty_means"]


def test_an_empty_group_on_a_book_that_has_set_them_is_still_good_news():
    """The edge that matters most: this must not cry wolf.

    Every line here carries a reorder point and none is at or below it. That is
    a genuine clean bill of health for the shelf, and the group must go back to
    saying so rather than explaining an absence that is not there.
    """
    built = _build([_line("p1", reorder_level=10.0),
                    _line("p2", reorder_level=20.0)])
    group = _group(built, "BELOW_REORDER")

    assert group["count"] == 0
    assert group["empty_means"] is None
    assert _card(built, "NO_REORDER_POINT") is None, (
        "a book that has done the work should not carry a permanent reminder "
        "that it did")


def test_a_partly_set_book_reports_the_shortfall_without_claiming_all_of_it():
    """Some set, some not. The card must count what is missing, not the book."""
    built = _build([_line("p1", reorder_level=10.0), _line("p2"), _line("p3")])

    card = _card(built, "NO_REORDER_POINT")
    assert card["value"] == 2
    assert "Of 3 lines on the shelf" in card["note"]
    # The group can hold a row again, so an empty one is once more the good
    # answer — even though two thirds of the shelf has no policy.
    assert _group(built, "BELOW_REORDER")["empty_means"] is None


def test_the_headline_narrows_to_exactly_the_rows_it_was_computed_from():
    """The drill-through contract ``_kpis`` states in its own docstring.

    A headline a person cannot open is a headline they have to take on trust,
    so the card carries a filter — and the filter has to select the same rows
    the number counted. Applied here as the client applies it: a predicate over
    the grid the same response carries.
    """
    built = _build([_line("p1", reorder_level=10.0), _line("p2"), _line("p3")])
    card = _card(built, "NO_REORDER_POINT")

    f = next(x for x in built["filters"] if x["key"] == card["filter"])
    assert (f["field"], f["op"]) == ("reorder_level", "is_null")
    selected = [r for r in built["items"] if r[f["field"]] is None]
    assert len(selected) == card["value"]


def test_the_shelf_card_and_the_book_wide_count_are_different_questions():
    """Both exist, and neither is the other. ``counts`` covers every tracked
    item; the card covers the shelf, because the grid it opens is the shelf.
    A card that counted the book and opened onto fewer rows would break the
    contract above — silently, and only on books holding zero-stock items."""
    built = _build([_line("p1"), _line("p2", on_hand=0.0, available=0.0,
                                       actual_available=0.0)])

    assert built["counts"]["no_reorder_point"] == 2
    assert _card(built, "NO_REORDER_POINT")["value"] == 1


def test_no_reorder_point_is_ever_invented_for_a_line_that_lacks_one():
    """The refusal this change must not have weakened.

    A reorder level is a policy somebody chooses with lead time and service
    level in mind. Naming how many are missing is a measurement; filling one in
    would be the platform making the commercial decision instead.
    """
    built = _build([_line("p1"), _line("p2", reorder_level=10.0)])

    levels = {r["product_id"]: r["reorder_level"] for r in built["items"]}
    assert levels == {"p1": None, "p2": 10.0}
    assert not any(r["below_reorder"] for r in built["items"]
                   if r["reorder_level"] is None)


def test_the_collectable_note_at_the_foot_of_the_screen_still_stands():
    """Promoted, not moved. The note explains a blank a reader sees in the
    grid; the card is the finding. Removing the note to avoid saying it twice
    would take the explanation away from the column that needs it."""
    built = _build([_line("p1"), _line("p2")])

    note = next(u for u in built["unavailable"] if u["series"] == "reorder_point")
    assert note["kind"] == "COLLECTABLE"
    assert "2 of 2 items have no reorder level set" in note["reason"]


def test_an_empty_shelf_makes_no_claim_about_stocking_policy_either_way():
    """No rows is not "every line lacks a policy", and 0 of 0 is not a finding.

    The standing refusals in ``unavailable`` — projected cover, supplier
    attribution — are properties of the method and stay whatever the shelf
    holds. The reorder note is a *count*, so on no rows it has nothing to say.
    """
    built = _build([])

    assert built["kpis"] == []
    assert built["groups"] == []
    assert built["counts"] == {}
    assert not [u for u in built["unavailable"] if u["series"] == "reorder_point"]
