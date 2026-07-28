"""The composite subject id for a Customer × Item signal.

A signal's subject is one id. The grain here is a *pair*, so the two ids are
joined with a separator that cannot occur in either (both are UUIDs or Zoho
ids). Kept in one tiny module so the encoding is defined exactly once and every
producer and consumer agrees.
"""
from __future__ import annotations

from typing import Optional

SEPARATOR = "::"


def encode(customer_id: str, product_id: str) -> str:
    return f"{customer_id}{SEPARATOR}{product_id}"


def decode(subject_entity_id: str) -> Optional[tuple[str, str]]:
    """``(customer_id, product_id)``, or None if this is not a pair id."""
    if SEPARATOR not in subject_entity_id:
        return None
    customer_id, _, product_id = subject_entity_id.partition(SEPARATOR)
    if not customer_id or not product_id:
        return None
    return customer_id, product_id
