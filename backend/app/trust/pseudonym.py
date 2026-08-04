"""Stable labels for entities the analysis talks about but must not name.

A margin calculation does not need to know that a customer is called "Bharat
Forge". It needs to know which rows belong together. The name is needed for
*display*, at the last inch, to a person who is already entitled to see it —
which means it can be held apart from everything that computes, and kept out of
anything that leaves the building.

``Customer C-9F42A1`` is what the analytical core and the model see. It is:

  * **stable** — the same entity gets the same label across calls, so a model's
    output can be matched back and a cached interpretation stays valid;
  * **scoped to one tenant** — the organization id is inside the digest, so the
    same entity id in two tenants yields different labels and nothing can be
    correlated across them;
  * **not reversible from the label** — it is a keyed digest, not an encoding.
    Recovering the name needs the vault, which needs the tenant's data key.

Deliberately not a random id stored in a column: a lookup table is another
thing to migrate, backfill and keep consistent, and the derivation is free.
"""
from __future__ import annotations

import hashlib
import hmac

from ..config import settings

#: Short enough to read aloud in a sentence, long enough that a tenant's whole
#: customer list cannot be enumerated by guessing labels. Six letters over one
#: organization's entity space is ~309M values against a few thousand real
#: entities; the label is not a secret, it is a non-disclosure.
_LENGTH = 6

#: **Letters only, and this is load-bearing.** The AI output validator rejects
#: any number in a model's text that is not grounded in the fact bundle, and it
#: extracts numbers with a regex that does not require a word boundary. A hex
#: label like ``C-A67EF1`` therefore reads as the numbers 67 and 1, so a model
#: politely echoing the subject's label would fail grounding and every
#: interpretation would silently degrade to the deterministic fallback. An
#: alphabetic label cannot collide with the number check at all, which is a
#: better fix than teaching the validator about pseudonyms — that would mean two
#: modules having to agree about a format forever.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"      # no I or O: they read as 1 and 0

_PREFIX = {
    "CUSTOMER": "Customer",
    "PRODUCT": "Item",
    "ITEM": "Item",
}


def label_for(organization_id: str, entity_type: str, entity_id: str) -> str:
    """``Customer C-KQNVXB`` — the same inputs always give the same label."""
    kind = (entity_type or "").upper()
    digest = hmac.new(
        settings.CREDENTIAL_ENCRYPTION_KEY.encode(),
        f"{organization_id}:{kind}:{entity_id}".encode(),
        hashlib.sha256,
    ).digest()
    tag = "".join(_ALPHABET[b % len(_ALPHABET)] for b in digest[:_LENGTH])
    return f"{_PREFIX.get(kind, 'Entity')} {kind[:1] or 'E'}-{tag}"
