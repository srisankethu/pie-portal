"""Inbound demand: every enquiry line that arrived, whether or not it was quoted.

The record of what customers *asked for*, kept apart from the record of what the
business managed to sell. Three downstream readers need it and none of them can
be served from anywhere else:

* Coverage. Quotes, orders and invoices all exist because something went right,
  so every one of them is a denominator that conditions on success. "How much of
  what we were asked for did we answer" needs a row per ask, written before the
  answer is known.
* An RFQ parser benchmark. Its corpus has to be the text as sent; a benchmark
  over cleaned input measures the cleaner.
* Unquoted demand. Lines that went nowhere appear in no other table by
  construction.

**Deterministic, and never interpreted here.** This package reads and writes
persisted rows and nothing else — it never imports ``ai/``, which
``test_layer_boundaries`` enforces. That matters more here than the rule
generally does: ``raw_text`` is a customer's own words, and a model handed this
table would be handed one tenant's commercial intelligence wholesale.

**Not ``state/``, deliberately.** ``state/`` is derived by contract — Zoho is the
system of record and a complete re-sync rebuilds it from nothing. An enquiry
that arrived as a WhatsApp message exists in no ERP, so these rows are
canonical: a re-sync rebuilds none of them and losing them is data loss. The
supersede *convention* comes from there and from ``attribution/ledger`` anyway;
only the lifecycle is different.

**Not ``ingestion/``, either.** That package is the ERP boundary — the one place
raw Zoho structures are allowed. An enquiry is not an ERP payload; it is a
person sending a message.

Nothing in this package computes a price, a cost or a margin, and no row it
writes may carry one (§1).
"""
from .capture import (  # noqa: F401
    CaptureRefusal,
    ExportedLine,
    capture,
    disposition_history,
    export,
    live_disposition,
    set_disposition,
)
