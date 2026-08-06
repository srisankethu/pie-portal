"""One module per state. Importing this package registers all of them.

A new state is a new module here plus one import line below — the engine is
untouched. That is the whole reason the registry exists, and it is why nothing
in ``engine.py`` names a state.
"""
from . import cash, commitments, inventory, receivables, supplier, trade  # noqa: F401

__all__ = ["cash", "commitments", "inventory", "receivables", "supplier",
           "trade"]
