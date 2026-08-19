"""The counts `DecisionType` states about itself, checked against itself.

Worth a test for the same reason `test_deploy_runbook` is: the sentence's only
value is being trusted. This docstring read "The five V1 decision types (§9).
No sixth type in V1" while the enum held twenty-two members across three
grains, and the comment above the state-derived block said "Seven types" above
eleven of them. Both read as settled fact. A reader who counted found the file
arguing with itself; a reader who did not carried the wrong number away, and
the wrong number is the one that ends up in a design note.

Numbers only — the prose around them can be rewritten freely. What this pins is
that every count the docstring claims is a count something below it actually
has, so the next person to add a decision type is told to update the sentence
rather than quietly leaving it wrong.
"""
from __future__ import annotations

import re

from app.domain import enums


def _states(doc: str, n: int) -> bool:
    """Is `n` used as a standalone number anywhere in `doc`?

    `§9` and `V1` are section and version references, not counts, so a digit
    glued to a word character or a section mark does not answer for one.
    """
    return re.search(rf"(?<![\w§]){n}(?!\w)", doc) is not None


def test_the_decisiontype_docstring_states_counts_that_match_the_enum():
    doc = enums.DecisionType.__doc__ or ""
    for what, n in (
        ("decision types in total", len(list(enums.DecisionType))),
        ("Customer × Item types", len(enums.CUSTOMER_ITEM_DECISION_TYPES)),
        ("types derived from Business State", len(enums.STATE_DECISION_TYPES)),
    ):
        assert _states(doc, n), (
            f"DecisionType's docstring states no count of {n}, and there are "
            f"{n} {what}. Say so in the docstring — a number in prose that "
            f"nothing checks is how this one came to claim five.")
