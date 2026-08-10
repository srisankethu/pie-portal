"""Business state: the event log, and what is derived from it.

Deterministic, like ``commercial/`` and ``signals/`` — it reads persisted rows
and writes persisted rows, and it never imports ``ai/``. It sits below all
three: events are the record of what the platform read, and the numbers those
layers compute are reproducible from them.

Derived, never canonical. Zoho is the system of record; a complete re-sync
rebuilds this from nothing.

One exception to "below", named here rather than discovered by grep:
``opportunities/inventory.py`` imports ``commercial.offtake`` — pure quantity
arithmetic over two fold fields, no policy read and no cost term. It is there
because the stock screen renders the same days-of-cover figure the excess-cover
detector bands on, and ``CLAUDE.md`` §2 is worth more here than a clean arrow:
two copies of one calculation would let the decision queue and the screen
disagree about how much of something the business holds. ``DecisionPolicy`` in
``opportunities/base.py`` states what genuinely must not cross, which is a
*policy read* rather than an import.
"""

# Importing the package registers every reducer. Done here rather than at each
# call site: a state that exists only when somebody remembered to import its
# module is a state that is missing in exactly the process that forgot.
from . import reducers  # noqa: E402,F401
