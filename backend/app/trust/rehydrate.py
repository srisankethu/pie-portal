"""Put the names back, at the last inch, for the person entitled to see them.

The other half of pseudonymisation, and the half that makes it usable. The
model is given ``Customer C-9F42A1`` and writes a sentence about
``Customer C-9F42A1``; the person reading that sentence works there and knows
the account as Bharat Forge. Without this step, pseudonymisation would be a
privacy control paid for entirely by the user.

Applied at the ``decisions/`` seam, after interpretation and before anything is
persisted or returned, so there is one place where names re-enter and it is the
same place deterministic facts already meet interpreted language.

Substitution is literal and exact-match on the generated label, which is safe
because the labels are keyed digests: ``Customer C-9F42A1`` cannot occur in a
model's output unless it was put there by us. Longest-first so no label that is
a prefix of another can shadow it.
"""
from __future__ import annotations

from typing import Mapping, Optional, TypeVar

T = TypeVar("T")


def text(value: Optional[str], names: Mapping[str, str]) -> Optional[str]:
    """Replace every pseudonym in one string with its display name."""
    if not value or not names:
        return value
    out = value
    for label in sorted(names, key=len, reverse=True):
        if label in out:
            out = out.replace(label, names[label])
    return out


def result(ai_result: T, names: Mapping[str, str]) -> T:
    """Re-hydrate the user-facing strings of an ``AIResult`` in place.

    In place rather than as a copy: the object is already being handed straight
    on to persistence, and a rebuilt copy would need updating every time the
    contract gains a field — the failure mode being a new field that quietly
    ships pseudonyms to the screen.
    """
    if not names:
        return ai_result
    for attr in ("concise_title", "explanation", "recommended_action", "caveat"):
        current = getattr(ai_result, attr, None)
        if isinstance(current, str):
            setattr(ai_result, attr, text(current, names))
    return ai_result
