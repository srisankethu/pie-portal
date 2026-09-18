"""Connector-agnostic conformance against the published ingestion contract.

``app/domain/spec.py`` publishes what a connector must produce. Nothing held
any connector to it, which is the condition the contract was written to end: a
spec nobody is tested against is documentation.

The incident that sets the bar is in ``spec.py``'s own docstring. PIE's Zoho
client dropped ``created_time`` from all three document projections; every row
landed with a NULL ``source_recorded_at``, every quote line answered
INSUFFICIENT_EVIDENCE, and the sync reported success — because nothing anywhere
stated the field was supposed to be there. So the bar for this package is not
"the connectors have tests". It is: **a connector that omits that field fails a
test rather than shipping quietly.**

How it is put together
----------------------
``harness.py``   Emissions, the capture, and the checks — all driven from
                 ``spec.entity_field_contracts()``. The suite keeps no list of
                 fields of its own; a suite with its own copy of the contract
                 is the second source of truth this exercise exists to remove.
``connectors/``  One module per registered ERP: a stub client holding that
                 system's own native records, and the real source class driven
                 over them. The native dress is the only thing that varies, so
                 it is the only thing these modules hold.
``broken.py``    A fixture connector that violates each assertion in turn, and
                 is registered nowhere. It is the reason to believe the rest:
                 a suite nobody has watched fail is one that silently passes
                 everything, and this codebase has the scar twice over — a gate
                 red for eight merges that nobody read, and a test suite that
                 had stopped running at all.
"""
