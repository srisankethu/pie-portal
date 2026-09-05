"""Master Health Report — an item-master export, measured offline.

What this package does, in one sentence: given *a file* — one ERP's item-master
export — and a **column profile** that says which header means what, it reports
how much of that master this platform's product intelligence can actually
reach, and what it would take to reach more.

Three deliberate refusals, each of which was a live option and is written down
so it is not re-added by someone who assumes it was an oversight:

* **No upload endpoint — for THIS package.** The three sentences that used to
  stand here are now false and are kept as the reason rather than deleted:
  there is an ``UploadFile`` in ``backend/app`` (``routers/enquiries.py``),
  there is a multipart handler, and ``python-multipart`` is installed. Decision
  012 reversed all three in the open, because a salesperson receiving a
  customer's PDF is not a person running a diagnostic, and the second of those
  needs a door.

  **The refusal still holds here, and for the reason it always did.** This
  package takes *a path*, and a path is what a diagnostic wants: it costs no
  authorization, no tenant, no storage and no retention question, and it can be
  pointed at a prospect's export on a laptop. Nothing about an upload route
  existing elsewhere makes routing this through it better; it would trade a
  path argument for a session, an organization, a stored blob and an erasure
  obligation, to answer a question that is a pure function of bytes.

  So: an upload endpoint exists, this package does not use one, and the
  distinction is between a *diagnostic over a file somebody hands you* and
  *inbound demand a business has to keep*. The second is
  ``enquiry/documents.py``.

* **No connector.** ``ingestion/erp/`` is the registry for *live* ERP
  connections, and its own module docstring records that Zoho — the connector
  that would matter most here — deliberately does not register there. A
  diagnostic that needs a working OAuth grant before it can say anything is a
  diagnostic nobody runs on a prospect's data.

* **No database.** Nothing here reads or writes a session. The report is a pure
  function of (export bytes, profile, catalogue) and says so: two runs over the
  same file produce the same report.

The offline shape is not a compromise, and the value-weighted number is the
proof. Coverage weighted by stock value at *selling* price cannot be computed
from what the platform ingests, because the sync does not carry stock and rate
together per item — but an item-master export carries both, in the same row,
for every item. The file is a *better* input than the connector for this
question.

What never appears in the output, per ``CLAUDE.md`` §1: a cost, a purchase
rate, or a margin. The profile has no column for one, so a cost cannot be read
even by accident. Stock value is at selling price and every line that prints it
says so.
"""
