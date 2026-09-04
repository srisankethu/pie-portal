"""The suggestion report's counting, on the stored line shape."""
from __future__ import annotations

from app.retrieval.report import _empty, _share, count_lines


def _line(**over):
    base = {"customerScope": "c", "semantics": "REQUIREMENT", "sel": "USER",
            "supplyCode": "A", "candidates": [{"code": "A"}]}
    base.update(over)
    return base


def test_every_kind_of_line_lands_in_exactly_one_count():
    counts = _empty()
    count_lines([
        _line(customerScope=None),                                   # not counted
        _line(semantics="IDENTITY"),                                 # not counted
        _line(supplyCode=None),                                      # left open
        _line(sel="AUTO"),                                           # engine's own
        _line(),                                                     # from ranking
        _line(candidates=[{"code": "A", "retrieved": True}]),        # from retrieval
        _line(candidates=[{"code": "A", "retrieved": True, "alias": "X",
                           "alias_kind": "code"}]),                  # confirmed code
        _line(candidates=[{"code": "A", "retrieved": True, "alias": "X",
                           "alias_kind": "phrase"}]),                # a phrase
        _line(sel="MANUAL", supplyCode="Z"),                         # typed, unoffered
    ], counts)
    assert counts == {"lines": 7, "auto_selected": 1, "chosen_by_person": 5,
                      "chosen_from_ranking": 1, "chosen_from_retrieval": 1,
                      "chosen_from_confirmed_code": 1, "chosen_from_phrase": 1,
                      "typed_unoffered": 1, "left_open": 1}


def test_a_share_of_nothing_is_null_not_zero():
    assert _share(0, 0) is None
    assert _share(1, 4) == 0.25
