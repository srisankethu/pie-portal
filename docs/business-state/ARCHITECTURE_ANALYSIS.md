# Business State Platform — architecture analysis

Phase 1. Written before any implementation, against the code as it stands at
`cb920a7`. Its purpose is to say what already exists, what genuinely does not,
what the minimum change is, and — in two places — where the brief's design
would make the system worse if followed literally.

---

## 1. What the target architecture asks for, and what is already here

The brief's chain is:

    ERP transaction → Business Event → State transition → Business State
    → Derived Metrics → Business Impact → Business Signals → Decision
    Intelligence → Dashboards

Five of those nine boxes exist today, under different names, and they are the
better-built half of this codebase. They should be reused, not rebuilt.

| Target box | What exists | Where |
|---|---|---|
| Derived Metrics | `CustomerItemMetric` — a recomputable projection holding no source facts, rebuildable end-to-end (`commercial.backfill`), stamped with `thresholds_version` | `domain/models.py:600`, `commercial/compute.py` |
| Business Signals | Deterministic detectors over persisted rows, never AI, each with an evidence-sufficiency gate | `signals/` (7 detectors + engine) |
| Decision Intelligence | `Decision`, role-scoped, with `evidence` refs and a recorded human action | `decisions/`, `domain/models.py` |
| Determinism guarantee | "AI never computes a number." `commercial/`, `signals/`, `ingestion/` may not import `ai/`; `ai/` may not import `commercial/`. Enforced by a grep in the build gate | `CLAUDE.md` §1 |
| Reproducibility | Every computed row carries the content hash of the thresholds that produced it | `commercial/config.py`, `policy.py` |

**The brief's "do not introduce probability models / do not use AI scoring" is
already the constitution of this codebase.** That is not a change to make; it is
the property to avoid breaking.

Two more pieces are closer than they look:

- **Traceability** is partly built. Every ingested row carries `source_ref`
  (`record_type`, `record_id`, `line_id`) back to the ERP document, and
  decisions carry evidence refs. The missing link is not provenance — it is the
  *intermediate* hop: today a metric points at ERP rows directly, with no named
  transition in between.
- **Provenance across connectors** landed this session: every imported record
  now carries (connector, connected company, that system's id), and
  `domain/origin.py` is the one projection for it. A Business Event layer needs
  exactly that triple, and it is already there.

---

## 2. What genuinely does not exist

### 2.1 There is no event log

Ingestion upserts **current-state rows**. `SalesTxn` is an invoice line, not
"Customer Invoice occurred". There is no append-only, ordered record of business
facts, so:

- state cannot be reconstructed at a past date,
- nothing can be replayed,
- a correction in the ERP silently overwrites history rather than superseding
  it.

`IngestedDocument` is a resume cursor, not a log — it records *that* a document
was fetched and its modification stamp, not what it meant.

### 2.2 There is no commitment layer, and the data for one is not being read

This is the most important finding, because it is a **data gap, not a modelling
gap**, and no engine can compute around it:

| Needed for | Required source | Read today? |
|---|---|---|
| Open sales orders → customer commitment, future inventory, expected cash | Zoho `salesorders` | **No.** The client reads invoices only. |
| Vendor payments → cash out, liquidity | Zoho `vendorpayments` | **No.** |
| Bills as payable → AP, working capital | `bills` are read for **cost**, and normalised to `CostRecord`; due date, paid status and balance are discarded | **Partially.** |
| Open purchase orders → cash and supplier commitment | `PurchaseOrderDoc` (pending qty, expected date) | **Yes** — the one commitment-shaped thing already ingested. |

So of the brief's eleven initial Business States, **Cash, Liquidity, Working
Capital, Accounts Payable and Demand cannot be computed at all today** — not
approximately, not partially. Building their engines now would ship screens that
confidently display zero.

Extending ingestion is therefore the **first** implementation step, not a later
one. It is also cheap: `sync.run_supply` already has the per-stage, scope-aware,
degrade-don't-fail structure these three new pulls need.

### 2.3 Dashboards are the calculation engine

`commercial/insight/` holds fourteen modules, one per screen, each recomputing
from raw rows on every request. `stock.py` computes carrying cost, `payments.py`
computes settlement behaviour, `flow.py` computes movement. This is precisely
the "dashboards as calculation engines" the brief wants removed, and it is the
real technical debt for this evolution.

It is *good* code — deterministic, tested, honest about what it cannot compute —
but it is organised by **screen** rather than by **business state**, so:

- "inventory value" is computed in `stock.py` and again in the storyboard,
- a new screen means a new builder rather than a new projection,
- nothing can answer "what was inventory worth in March" because nothing stores
  it.

### 2.4 Every insight request loads the organization's entire history

`signals/aggregates.load_snapshot` runs `SELECT * FROM sales_txns WHERE
organization_id = ?` with **no date bound**, plus the same for cost records, and
four call sites hit it per page load. At ₹19 Cr and ~40k lines this is tolerable;
at the millions the brief asks us to design for it is the wall we hit first —
before any engine design matters.

**This is the performance finding that should drive the snapshot strategy**, and
it is a pre-existing problem the evolution can fix as a side effect rather than a
new constraint the evolution introduces.

---

## 3. Two corrections to the brief

### 3.1 The event log must be *derived*, not canonical

The brief says "Business State becomes the canonical source of truth". Taken
literally that is wrong here, and the codebase is right to disagree:

> The platform treats Zoho as the system of record and never writes back.
> — `ingestion/zoho_client.py`

Our events are *derived from* an ERP that is itself the authority. If the event
log were canonical, a divergence between it and Zoho would have no arbiter, and
"replay to reconstruct state" would reconstruct **our reading**, not the truth.
An accountant correcting an invoice in Zoho must win.

So the design should be: the event log is **append-only, ordered, replayable and
fully rebuildable from a complete re-sync**. Canonical *for analysis*, derived
*from* the ERP. That keeps replay, traceability and point-in-time reconstruction
— everything the brief actually wants — without claiming an authority the
platform has deliberately refused.

Concretely: event emission keys on the same `(doc_type, doc_id, modified_at)`
that `IngestedDocument` already tracks, so a re-read of an edited document
**supersedes** its prior events rather than appending duplicates. That is where
idempotency comes from, and it reuses a mechanism that already works.

### 3.2 "Signals must never come from ERP transactions" is already satisfied — differently

The brief wants signals derived from state, not transactions. Today they are
derived from *persisted normalised rows plus `CustomerItemMetric`* — which is
the derived-metric layer, not raw ERP. The existing detectors are already one
hop removed and already carry sufficiency gates.

Rewriting them to read a new state store is worth doing for consistency, but it
is a **refactor with no behaviour change**, and should be sequenced last and
covered by the existing detector tests as a regression harness. It is not where
the value is.

---

## 4. Minimum architectural change

Four new concepts. Everything else is reuse.

```
BusinessEvent      append-only, ordered, superseded-not-mutated.
                   (org, connector, connection, type, occurred_at, seq,
                    source_ref, payload, superseded_by)

StateTransition    the traceability hop the system is missing:
                   event → state key → delta. This is what makes
                   "why does this number exist" answerable.

BusinessState      a keyed, versioned projection: (org, state, key, as_of,
                   value, thresholds_version) + periodic snapshots.

Reducers           one per state, in a registry — the pattern already used by
                   signals/matchers.py and origin.CONNECTORS. A new state is a
                   new reducer; a new event type is a new row in a mapping.
                   Neither touches the engine.
```

Extension points that already exist and should be used rather than duplicated:

- `ingestion/sync.py` — the one place documents are normalised. Events are
  emitted here, in the same pass, not in a second traversal.
- `commercial/config.py` + `policy.py` — versioned parameters with a content
  hash. Business State rows get stamped the same way; this is what makes state
  reproducible without storing every input.
- `signals/engine.py` — a detector registry that already runs over persisted
  rows. Point it at state.
- `commercial/insight/*` — become thin projections of state. The arithmetic is
  already correct and tested; it moves rather than gets rewritten.

---

## 5. Sequence, and why this order

1. **Extend ingestion** — sales orders, vendor payments, bill payment terms.
   Without this, five of eleven states are uncomputable. Reuses the existing
   per-stage supply structure and the scope-refusal machinery.
2. **Event log + emission during sync.** No consumers yet; verifiable by
   replaying to reproduce today's `SalesTxn` rows exactly. That is the honest
   test that the event model is complete — if replay cannot rebuild the current
   read model, the events are lossy.
3. **State engine + reducers for Inventory and Commitments** — the two the data
   already supports, and the two that make the inventory-centric model real.
4. **Bound `load_snapshot`**, backed by state snapshots. Fixes the scaling wall.
5. **Move `insight/` builders onto state**, screen by screen, with existing
   tests as the regression harness.
6. **Point detectors at state.** Behaviour-preserving.

Steps 1–2 are the foundation and are independently valuable — the event log
alone makes "why does this number exist" answerable, which nothing today can do.

---

## 6. What I would not do

- **Not** a generic reducer DSL. Reducers are Python functions in a registry;
  a configuration language for state transitions is an abstraction with one
  implementer, which `CLAUDE.md` §5 explicitly calls out as the failure mode
  this document is most likely to cause.
- **Not** eleven state engines up front. Two, on data that exists, with the
  registry proving a third costs nothing.
- **Not** a rewrite of `commercial/insight/`. The arithmetic there is correct,
  tested and honest about its own limits. It moves; it does not get rewritten.
