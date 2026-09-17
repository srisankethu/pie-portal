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
WORDS = ("cost", "margin", "purchase_price", "opportunity", "peer")


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
