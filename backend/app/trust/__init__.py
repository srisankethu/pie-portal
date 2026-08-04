"""Trust controls: the guarantees around the data, rather than the analysis of it.

Five concerns live here, and they share one property — each answers a question a
buyer asks before they will hand over their ledger, and each has to be
*demonstrable* rather than asserted:

``keys``        per-tenant data keys, so deletion can be proved rather than promised
``vault``       display names held apart from the analytical core, encrypted
``pseudonym``   the stable, reversible-only-here label the analysis uses instead
``access``      staff reach into a tenant, justified and visible to that tenant
``disclosure``  exactly what reaches a model, logged and checkable against a schema
``erasure``     export everything, then destroy the key and receipt it

This package is infrastructure, not analysis: nothing here computes a
commercial number, and nothing in ``commercial/`` or ``signals/`` depends on it.
"""
