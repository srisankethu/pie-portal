"""Decision detectors over Business State. Importing this registers them all.

A new decision category is a class in one of these modules and one import line
below — the service, the queue and the card are untouched. That is the whole
reason the registry exists, and it is why nothing outside these modules names a
decision type.

Business State is the only input. A detector receives loaded state rows, a
policy object and a date; it never touches a session, never reads a sales line,
and never calls the AI layer.
"""
from .base import (ACTIONS, DETECTORS, DecisionPolicy, Impact,  # noqa: F401
                   OpportunityDetector, OpportunityDraft, UnknownAction,
                   register)
from . import inventory, receivables, supplier, supply  # noqa: F401,E402

__all__ = ["ACTIONS", "DETECTORS", "DecisionPolicy", "Impact",
           "OpportunityDetector", "OpportunityDraft", "UnknownAction",
           "register", "inventory", "receivables", "supplier", "supply"]
