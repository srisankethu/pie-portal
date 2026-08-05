"""Deterministic salesperson incentive engine.

One payable currency — Contribution Above Floor — shaped by relationship
strength, a portfolio-health multiplier, binary gates and deferral.

Three properties the whole package is built to hold:

**Cost never reaches an operations output (I1).** Not by filtering — by type.
``CustomerPoints`` and ``SalespersonPayout`` have no cost field, no margin
field and no ``m_floor`` field to populate. ``OwnerReconciliation`` does. A
serialiser cannot leak what the dataclass does not carry.

**Deterministic (I3).** Pure functions on typed inputs, ``Decimal`` throughout,
no network calls and no clock reads in the computation path. Same inputs plus
same config version gives byte-identical output.

**Computable by the salesperson (I4).** Every term in the currency is a price
or a declared amount. The Floor Price is published; the margin behind it is
not. That is the device that resolves I1 against I4 without weakening either.
"""
from .config import Config, load_config          # noqa: F401
from .models import (                            # noqa: F401
    CustomerPoints,
    InvoiceLine,
    OwnerReconciliation,
    SalespersonPayout,
)

__all__ = [
    "Config", "load_config", "InvoiceLine",
    "CustomerPoints", "SalespersonPayout", "OwnerReconciliation",
]
