"""Business state: the event log, and what is derived from it.

Deterministic, like ``commercial/`` and ``signals/`` — it reads persisted rows
and writes persisted rows, and it never imports ``ai/``. It sits below all
three: events are the record of what the platform read, and the numbers those
layers compute are reproducible from them.

Derived, never canonical. Zoho is the system of record; a complete re-sync
rebuilds this from nothing.
"""

# Importing the package registers every reducer. Done here rather than at each
# call site: a state that exists only when somebody remembered to import its
# module is a state that is missing in exactly the process that forgot.
from . import reducers  # noqa: E402,F401
