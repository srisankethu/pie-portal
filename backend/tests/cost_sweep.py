"""What "no cost reached this response" means, as one assertion.

The sweep itself is the right shape and is kept: not a list of fields that must
be absent, but the whole serialised payload searched for the cost value and for
the vocabulary of economics. Every field-level assertion in this repository's
previous leak passed while the endpoint gave up cost, because the leak was in a
field nobody had thought to name.

**What was wrong with doing that over the raw JSON.** A server-minted uuid is
thirty-two hex digits, every one of which is also a decimal digit, so a
three-digit cost is a legal substring of one. ``PURCHASE_COST = 371`` appears
inside roughly one uuid in two hundred — and it did, on CI, in
``quote_diagnosis_id``: `…-393cae023371`. The test failed having found no leak
at all, on a build with nothing wrong with it. A check that is wrong once in two
hundred runs is a check people learn to re-run, and a security check people
re-run is a security check nobody reads.

So opaque identifiers come out of the payload before the sweep — **and are
asserted to be opaque**, which is the half that keeps this honest. Removing a
field from a leak check without proving it carries no data would be a way to
smuggle cost into an id, so every id that is dropped must be `null` or a real
uuid. Nothing else is exempt.

Used by both endpoints that project a diagnosis, so the two cannot come to
disagree about what counts as a leak.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

#: Canonical uuid form. Anything dropped from the sweep must match this, or it
#: is carrying something other than an identity.
_UUID = re.compile(
    r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z", re.I)

#: Keys the server mints rather than derives. `quote_id` and `line_id` are the
#: caller's own strings and stay in the sweep — a leak hidden in a value the
#: caller chose would still be a leak, and those are not random.
ID_KEYS = frozenset({"quote_diagnosis_id"})

#: The vocabulary of economics. A salesperson's projection is built from a type
#: that declares none of these, so any of them appearing is a structural failure
#: rather than a wording problem.
#:
#: The second line is driver attribution, added when the engine learned to split
#: a line's margin movement between its price and its cost level. Those figures
#: are percentage points rather than rupees, so the numeric half of this sweep
#: would not have caught one: a margin beside the price the caller sent is the
#: cost in one step, P x (1 - m), exactly. They are economics words because the
#: thing they name is a cost, not because they sound like one.
WORDS = ("cost", "margin", "purchase_price", "opportunity", "peer",
         "attribution", "driver", "effect", "movement_pp", "residual_pp")

#: Keys whose value **is** the caller's own input, spelled back. They move when
#: the caller moves the price, by definition, so a price walk cannot compare
#: them — and they are named here rather than skipped inside one test, because a
#: field that quietly joined this list is exactly where a boundary would hide.
ECHOED = frozenset({"quoted"})


def assert_no_cost(payload: Any, *, cost: int | str,
                   words: Iterable[str] = WORDS,
                   id_keys: Iterable[str] = ID_KEYS) -> None:
    """Fail if ``cost`` or any economics word survives anywhere in ``payload``.

    ``payload`` is the decoded response. It must be non-empty: a sweep over
    ``{"lines": []}`` passes while proving nothing, which is this repository's
    own *absence of evidence is not a pass* rule pointed at its own test.
    """
    lines = payload.get("lines") if isinstance(payload, dict) else None
    assert lines, "nothing was diagnosed, so this sweep proves nothing"

    scrubbed = _without_ids(payload, frozenset(id_keys))
    body = json.dumps(scrubbed)

    assert str(cost) not in body, (
        f"the cost {cost} reached a recipient who may not see it, in:\n{body}")
    for word in words:
        assert word not in body.lower(), f"{word!r} reached a salesperson"


def answer(payload: Any, *, id_keys: Iterable[str] = ID_KEYS,
           echoed: Iterable[str] = ECHOED) -> str:
    """Everything in ``payload`` that the SERVER decided, as one comparable string.

    What a price walk compares. **Not a handful of named fields**: the point of
    walking the price is to find any answer that moves as it crosses the cost,
    and a walk that watches only the three fields somebody remembered is this
    repository's original leak with a loop around it. Every key the server
    decided is in here, including ones added after this was written.

    Two kinds come out. Server-minted identifiers, for the reason in this
    module's docstring — and asserted opaque on the way, so nothing can be
    smuggled into one. And the caller's own inputs echoed back, which move with
    the price because they *are* the price: a test that includes them proves
    nothing, and one that drops them silently would let a real boundary hide
    behind ``quoted``. Naming them here makes that list reviewable.
    """
    scrubbed = _without_keys(_without_ids(payload, frozenset(id_keys)),
                             frozenset(echoed))
    return json.dumps(scrubbed, sort_keys=True)


def _without_keys(value: Any, keys: frozenset[str]) -> Any:
    """``value`` with ``keys`` removed at every depth, not only the top."""
    if isinstance(value, dict):
        return {k: _without_keys(v, keys) for k, v in value.items()
                if k not in keys}
    if isinstance(value, list):
        return [_without_keys(item, keys) for item in value]
    return value


def _without_ids(value: Any, id_keys: frozenset[str]) -> Any:
    """``value`` with opaque ids removed, each one checked for opacity first."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in id_keys:
                assert item is None or (isinstance(item, str) and _UUID.match(item)), (
                    f"{key!r} is dropped from the cost sweep because it is an "
                    f"opaque identifier, and this one is not: {item!r}")
                continue
            out[key] = _without_ids(item, id_keys)
        return out
    if isinstance(value, list):
        return [_without_ids(item, id_keys) for item in value]
    return value
