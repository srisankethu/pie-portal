# M0 — connection containment

The first work package from `docs/reviews/positioning-review-2026-08.md`. Two
weeks, reversible, no migration, and its value does not depend on any strategic
choice: it closes a destructive defect that is live in the current three-company
product and writes the tests that would have caught it.

Every line reference below was verified against the tree at `e4aedf6`.

---

## 1. The defect

`sync._mirror` (`sync.py:724-768`) retires what this connection holds and the
source no longer reports. Its docstring names three guards. **The third is not
implemented:**

> **Only this connection's documents.** Another connected company's invoice is
> not missing merely because this company's listing did not mention it.
> — `sync.py:744-746`

`held = self.repo.ingested_in_window(doc_type, start, end)` at `sync.py:764`
resolves to `repositories.py:474-479`, which filters on `organization_id` and the
document date. There is no connection predicate.

Three sites are unscoped, not two. The third was missed by the analysis that
produced the review, and it is the one that decides how much M0 can honestly
claim:

| # | Site | Scoped by connection? | Fixable in M0? |
|---|---|---|---|
| 1 | `repositories.ingested_in_window` (`:474-479`) | No — org + date only | **Yes** |
| 2 | `EventLog.supersede` (`state/events.py:146-161`) | No — though the constructor holds both values (`:104-110`) and `record` writes them (`:128-130`) | **Yes** |
| 3 | `repositories.retire_document` (`:490-499`) | **No** — `org + ref` match. The *cursor* delete below it (`:501-506`) **is** scoped by `connection_id` | **No — needs M2** |

### What M0 fixes, precisely

**Distinct refs across connections — the normal case, and the one that fires the
moment a second connector with its own id space exists.** Connection B holds
`INV-B-1`, dated inside the window A just pulled. A's listing never mentions it.
Today it lands in `held`, gets superseded and retired. After fix 1 it is not in
A's cursor rows, so it never enters `held`.

### What M0 does not fix, and why

**Colliding refs — both connections hold external ref `1`.** Fix 1 correctly
keeps B's row out of A's `held`. But when A's *own* `1` is genuinely deleted,
`retire_document("invoice", "1")` runs `select(model).where(organization_id ==
org, external_ref == "1")` and deletes **both companies' rows**, because
`SalesTxn`, `InvoiceDoc`, `CostRecord` and `BillDoc` carry no `connection_id`
(verified — neither `connector` nor `connection_id` exists on `sales_txns` or
`cost_records`).

That column is exactly what M2 adds. **M0 must not claim to fix it**, and the
test suite should say so out loud rather than leave a silent gap — see test 3.

This also corrects the review's own framing: the test it proposed ("two
connections, same `external_ref`, assert B's invoice survives") is the colliding
case, and it would **still fail** after the M0 fix.

### Blast radius

Four tables, not fifteen. `_mirror` is called exactly twice — `sync.py:1044`
(`"invoice"`) and `sync.py:1171` (`"bill"`) — and `_MIRRORED`
(`repositories.py:1220-1223`) contains only those two kinds. `_RETIRE_FROM`
(`:1231-1236`) deletes from `SalesTxn`, `InvoiceDoc`, `CostRecord`, `BillDoc`.
Payments, credit notes, sales orders, purchase orders, locations and stock
snapshots are never swept.

---

## 2. The design decision: what to do with NULL provenance

Both fixes scope on `self.connection_id`, which is `Optional[str]`. That creates
three populations:

| Row provenance | Caller's `connection_id` | Behaviour under strict equality |
|---|---|---|
| `NULL` | `None` (scripted caller, existing tests) | `== None` renders `IS NULL` → matches → retires as today |
| `"conn_a"` | `"conn_a"` | matches → retires as today |
| `NULL` | `"conn_a"` | **no match → not retired** |

The third row is the behaviour change, and it is a deliberate one.

**Recommendation: strict equality, and report what was skipped.** A row whose
provenance is unknown must not be retired on the assumption that it is ours.
That is CLAUDE.md §1 — *"When the evidence for a claim is missing the answer is
UNKNOWN, or a refusal that names what is missing — never the benign default"* —
and retiring on unknown provenance is precisely the benign default.

The asymmetry justifies it: a false negative leaves a stale document counting as
revenue, which is the pre-existing behaviour and is recoverable by a full
re-sync. A false positive destroys another company's history and is recoverable
only from backup.

Skipping silently would be its own §1 violation, so the count goes in the sync
report beside `retired` (`sync.py:768`).

Note this is *not* the `adopt_connectionless` rule (`repositories.py:105-110`).
Adoption claims a legacy row on read when there is exactly one connection;
retirement destroys it. The two do not need the same answer, and destruction
should be the more conservative of the pair.

---

## 3. The tests — write these first

All four go in `tests/decision_platform/test_sync_mirror.py`, which has nine
tests and **not one sets up two connections**. Follow that file's existing
style: the `_Source` stub at `:27`, `_invoice()` at `:70`, `_refs()` at `:86`.

`_sync()` at `:79` constructs `SyncService(session, source, ORG)` with no
connection, so it needs a sibling rather than a change — the nine existing tests
depend on the current signature:

```python
def _sync_as(session, source, connection_id: str) -> SyncService:
    """The same pull, on behalf of one connected company."""
    svc = SyncService(session, source, ORG, connection_id=connection_id)
    svc.run()
    session.commit()
    return svc
```

**Test 1 — the sweep (fails today, passes after fix 1).**

```python
def test_one_connections_sweep_does_not_retire_anothers_documents(session):
    """The third guard `_mirror` documents at sync.py:744 and does not implement."""
    _sync_as(session, _Source([_invoice("A1", 10)]), "conn_a")
    _sync_as(session, _Source([_invoice("B1", 10)]), "conn_b")
    assert _refs(session, models.InvoiceDoc) == {"A1", "B1"}

    # A pulls again. Its listing has never mentioned B1 and never will.
    svc = _sync_as(session, _Source([_invoice("A1", 10)]), "conn_a")

    assert _refs(session, models.InvoiceDoc) == {"A1", "B1"}
    assert svc.report.retired == []
```

**Test 2 — the event log (fails today, passes after fix 2).**

```python
def test_superseding_one_connections_document_leaves_anothers_events_live(session):
    _sync_as(session, _Source([_invoice("A1", 10)]), "conn_a")
    _sync_as(session, _Source([_invoice("B1", 10)]), "conn_b")

    log_a = EventLog(session, ORG, connector="zoho", connection_id="conn_a")
    log_a.supersede("invoice", "B1")          # B's document, A's log

    live_b = [e for e in EventLog(session, ORG, connector="zoho",
                                  connection_id="conn_b").live()
              if e.source_doc_id == "B1"]
    assert live_b, "conn_b's events were superseded by conn_a's sweep"
```

**Test 3 — the boundary M0 does not cross.** Mark it `xfail(strict=True)` so it
flips to a failure the moment M2 lands and nobody has to remember to come back:

```python
@pytest.mark.xfail(strict=True, reason=(
    "retire_document matches on (organization_id, external_ref) only — the fact "
    "tables carry no connection_id. Fixed by the M2 provenance migration; this "
    "test is the tripwire that says so."))
def test_retiring_a_colliding_ref_does_not_delete_the_other_companys_rows(session):
    _sync_as(session, _Source([_invoice("1", 10)]), "conn_a")
    _sync_as(session, _Source([_invoice("1", 10)]), "conn_b")

    _sync_as(session, _Source([]), "conn_a")   # A's "1" is genuinely gone

    assert _refs(session, models.InvoiceDoc) == {"1"}   # B's survives
```

**Test 4 — unknown provenance is refused, not assumed.**

```python
def test_a_document_with_no_recorded_connection_is_not_retired(session):
    _sync(session, _Source([_invoice("L1", 10)]))       # legacy: no connection

    svc = _sync_as(session, _Source([]), "conn_a")      # a real connection sweeps

    assert _refs(session, models.InvoiceDoc) == {"L1"}
    assert svc.report.unattributable == 1
```

---

## 4. The changes

### Fix 1 — `repositories.ingested_in_window` (`:460-480`)

Restrict the window query to documents this connection's cursor records. The
cursor table already carries what is needed: `IngestedDocument` has
`connection_id`, `doc_type` and `doc_id`, and `mark_ingested` (`:435-458`) writes
all three on every mirrored document — `sync.py:1041` for invoices, `:1170` for
bills, each immediately before its `_mirror` call.

```python
         model, ref_col, date_col = table
+        # Only this connection's documents — the third guard `_mirror`'s
+        # docstring promises. The fact tables carry no connection, so the
+        # cursor table is what knows: `mark_ingested` writes one row per
+        # document per connection, and a document with no cursor row is one
+        # whose provenance nobody recorded. Those are reported, not swept.
+        held_here = select(models.IngestedDocument.doc_id).where(
+            models.IngestedDocument.organization_id == self.org,
+            models.IngestedDocument.connection_id == self.connection_id,
+            models.IngestedDocument.doc_type == doc_type,
+        )
         rows = self.s.scalars(
             select(getattr(model, ref_col)).where(
                 model.organization_id == self.org,
                 getattr(model, date_col) >= start,
                 getattr(model, date_col) <= end,
+                getattr(model, ref_col).in_(held_here),
             ))
```

`self.connection_id` is already an attribute of `ReadModelRepository` — it is
used the same way by `ingested_high_water` (`:431`) and `mark_ingested` (`:449`).

For test 4's counter, return the excluded refs alongside, or expose a sibling
`ingested_in_window_unattributable()`. Prefer the sibling: `ingested_in_window`
has one caller and a documented contract, and widening its return type to a
tuple would touch that contract for a diagnostic.

### Fix 2 — `EventLog.supersede` (`state/events.py:146-161`)

No migration. `BusinessEvent` already carries `connector` and `connection_id`
(verified), the constructor already holds both (`:104-110`), and `record` already
writes them (`:128-130`). Only the retirement predicate ignores them.

```python
         result = self.s.execute(
             update(models.BusinessEvent)
             .where(models.BusinessEvent.organization_id == self.org,
+                   models.BusinessEvent.connector == self.connector,
+                   models.BusinessEvent.connection_id == self.connection_id,
                    models.BusinessEvent.source_doc_type == doc_type,
                    models.BusinessEvent.source_doc_id == doc_id,
                    models.BusinessEvent.superseded_at.is_(None))
             .values(superseded_at=utc_now())
         )
```

Update the method docstring: it says *"Retire every live event read from one
document"*, which after this is *"…from one document, as this connection read
it."*

**Regression check on the two production writers.** `sync.py:308` and
`replay.py:187` both construct `EventLog` with `connector=` and `connection_id=`,
so write and retire always carry the same identity. The nine construction sites
in `test_event_log.py` use `EventLog(session, "org_a")` — both values `None` on
write and on supersede, and SQLAlchemy renders `== None` as `IS NULL`, so they
stay green. Confirm rather than assume: those tests are the ones most likely to
be surprised.

### Fix 3 — the `"zoho"` default that writes the wrong provenance

`SyncService.__init__` (`sync.py:267`) declares `connector: str = "zoho"`.
`jobs.py:393-396` passes `connection_id=target.connection_id` and **not**
`connector=`, so it silently takes the default. Today that is correct by
accident. The day a Tally pull runs through the same job runner it writes rows
labelled `zoho`, and `repositories._for_upsert` adopts them into the Zoho id
space.

Make it required and fix the caller:

```python
-                 connector: str = "zoho",
+                 connector: str,
```

Keyword-only is already effectively the convention here; grep every construction
site before changing the signature, because the nine existing mirror tests and
`_sync()` construct it positionally up to `organization_id` only.

### Fix 4 — `_sole_connection` (`sync.py:240-256`)

Counts `models.ZohoConnection` and gates `adopt_connectionless`. With a second
connector's connection present the count is wrong in the unsafe direction — it
can report `1` when two companies exist, which is exactly the case the
function's own docstring says *"silently merges a stranger's customers into a
book they never traded with."*

Low-cost now, load-bearing later. Note it cannot be fully fixed until the
connection tables are restructured (M2/M4); what M0 should do is make it count
connections of **all** connectors once such a table exists, and until then add
the assertion that it is Zoho-only to its docstring so the limit is stated
rather than implied.

---

## 5. The two measurements — no code, and they are the decisive half

Both are in M0 because they gate everything after M3, and neither needs the
fixes above to land first.

**A. Is FX a live defect or a hypothesis?** Pull twenty live purchase bills
through the Zoho connector and read `currency_code` and `exchange_rate` off the
raw payloads. `grep -n "currency" app/ingestion/zoho_client.py` returns exactly
one hit — line 393, on `/organizations` — so the platform provably never fetches
either on a document today. If a material share come back non-INR, M1 stops
being a guard and becomes the roadmap.

**B. Is cross-brand the moat?** Run twenty real lost-quote lines through
**`app.pie_service.resolve`**. Not through `tools/find_equivalents.py` — that CLI
already accepts `--catalogs` plural and is therefore tempting, but it bypasses
the identity short-circuit at `resolve_rfq.py:459`, so it will look good while
the product path is broken. Threshold: ≥15% returning a TECH or COMPAT candidate
the owner confirms he could have sold.

---

## 6. Done means

- The four tests above exist; 1, 2 and 4 pass; 3 is `xfail(strict=True)`.
- `make verify` green, with the real numbers reported — not "tests pass".
- No Alembic revision in the diff. If one appears, the change has grown past M0.
- The `_mirror` docstring's third guard is true, or its wording is narrowed to
  what the code does. A docstring asserting a capability that does not exist is
  the defect `store.py:64-66` already demonstrates elsewhere in this codebase.
- Both measurements recorded as numbers, in the review doc, dated.
