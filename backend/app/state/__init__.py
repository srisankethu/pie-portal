"""Business state: the event log, and what is derived from it.

Deterministic, like ``commercial/`` and ``signals/`` — it reads persisted rows
and writes persisted rows, and it never imports ``ai/``. It sits below all
three: events are the record of what the platform read, and the numbers those
layers compute are reproducible from them.

Derived, never canonical. Zoho is the system of record; a complete re-sync
rebuilds this from nothing.
"""
