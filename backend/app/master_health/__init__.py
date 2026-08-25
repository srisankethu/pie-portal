"""Master Health Report — an item-master export, measured offline.

What this package does, in one sentence: given *a file* — one ERP's item-master
export — and a **column profile** that says which header means what, it reports
how much of that master this platform's product intelligence can actually
reach, and what it would take to reach more.

Three deliberate refusals, each of which was a live option and is written down
so it is not re-added by someone who assumes it was an oversight:

* **No upload endpoint.** There is no ``UploadFile`` and no multipart handler
  anywhere in ``backend/app``, and ``python-multipart`` is not installed.
  Adding one is a dependency decision and a new attack surface, and it buys
  nothing a path argument does not already give a person running a diagnostic.

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
