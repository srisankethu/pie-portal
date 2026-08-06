# Decision Intelligence — architecture analysis

Phase 1 of the Decision Intelligence brief. Written before any code, for the
same reason as the Business State analysis: the expensive mistake here is not a
wrong algorithm, it is building a second copy of a subsystem that already
exists.

---

## 1. The headline finding

**PIE already has a decision layer, and it is most of what the brief
describes.**

| The brief asks for | What already exists |
|---|---|
| Decision Opportunity — a first-class domain object | `models.Decision` — 23 columns, 9-state lifecycle, 7 human actions, role routing, dedup key, evidence refs, confidence, outcome |
| Decision Queue | `routers/decisions.py::list_decisions` + the "Today" / "Where to intervene" screen, already role-scoped and priority-ordered |
| Decision Card | `GET /decisions/{id}/detail` and its screen |
| Available business actions | `HumanAction` — VIEW, ACT, DISMISS, SNOOZE, OVERRIDE, ESCALATE, REOPEN |
| Deterministic prioritisation | `priority_deterministic_base`, already computed from `signal.severity_base` |
| Traceability to ERP | `evidence_refs` → `source_ref` → Zoho record id |

Introducing a parallel `DecisionOpportunity` model with its own engine, queue,
priority algorithm and screen would give this codebase **two things called a
decision**, two lifecycles to keep in step, and two places a role-scoping bug
can hide. That is the "responsibility duplication" row of CLAUDE.md §2, and it
is the failure mode that section exists to prevent.

### 1.1 The real gap is the producer, not the consumer

Today there is exactly one path into `Decision`:

```
SalesTxn / CostRecord  →  signals/ detectors  →  Signal  →  DecisionService (AI)  →  Decision
```

The brief wants:

```
BusinessState  →  deterministic opportunity detectors  →  Decision   (no AI)
```

That is a **second producer into the same pipeline**, not a second pipeline.
Everything downstream of `Decision` — queue, card, actions, scoping, approvals,
audit — is reused unchanged.

**This is the whole architectural change.** The rest is detectors.

---

## 2. Three corrections to the brief

### 2.1 "Decision Opportunity" is `Decision`, with a new origin

Add a discriminator (`origin = SIGNAL | STATE`) and the columns a state-derived
decision needs that a signal-derived one does not: quantified impact, the state
keys involved, and the `as_of` of the fold it was computed from. Do not add a
second table.

### 2.2 "Confidence is always deterministic" collides with an existing column

`Decision.priority_ai_adjustment` exists and is applied today, clamped to
±`AI_PRIORITY_ADJUST_BOUND` (20). The brief requires deterministic priority.

Removing it would change behaviour the platform already ships. Setting it to 0
for state-derived decisions is easy — but then **one queue ranks an
AI-adjusted score against a purely deterministic one**, and "priority 72" means
two different things depending on which producer made the row.

Proposal: the queue orders on `priority_deterministic_base` and uses
`priority_score` only within a producer. That keeps the existing AI nuance
visible on a signal card without letting it reorder a state-derived decision
that quantified ₹4 lakh of locked capital. **This needs your call — it changes
the order of the existing screen.**

### 2.3 "The engine must not consume ERP transactions" makes most categories
unbuildable *today*

Two states exist: `INVENTORY` (per product) and `COMMITMENTS` (per party). Held
strictly to state-only input, here is what the brief's ~30 categories actually
resolve to.

**Buildable now — 9 categories:**

| Category | From | Impact quantified as |
|---|---|---|
| Dead Stock | INVENTORY `on_hand`, `last_sold_on` | monthly holding cost, capital locked |
| Slow Inventory / Capital Locked | INVENTORY + carrying policy | ₹ held, ₹/month drain |
| Excess Inventory | `on_hand` ÷ measured historical offtake rate | months of cover held |
| Low Stock / Reorder Required | `on_hand` ≤ `reorder_level` | revenue at risk from stockout |
| Inventory Shortage / Service Risk | `actual_available` < 0 | value of orders that cannot ship |
| Capital Release Opportunity | idle `on_hand` × `purchase_rate` | ₹ recoverable before discount |
| High Commitment Exposure | COMMITMENTS `open_purchase_value` | ₹ committed, unreceived |
| Purchase Commitment Risk | `open_purchase_orders`, `oldest_open_purchase_on` | ₹ and age |
| Payment Prioritisation | `overdue_balance`, `earliest_due_on` | ₹ overdue by supplier |

**Already exists as a signal — do not re-derive:** Margin Erosion, Low Profit
Customer, Cost Not Passed Through, Customer Decline, Customer Dormancy. Five of
the brief's categories are the existing `CI_*` and customer detectors under
different names. Building them again from state would be two implementations of
one finding, drifting apart.

**Not buildable — the data is not read:**

| Category | Missing |
|---|---|
| Cash Pressure, Liquidity Risk | bank balance. PIE reads payments, not balances — and one side of a ledger is not cash |
| Collection Opportunity, High Credit Exposure | a receivables state. `PaymentReceipt` exists but is not folded |
| Inventory Transfer / Imbalance | location-level stock. Zoho exposes it only on Inventory-plan warehouse endpoints this pull does not read (already documented on the stock screen) |
| Order Allocation Conflict | line-level sales orders. We read header grain, deliberately |
| Supplier Concentration / Dependency / Single Supplier Risk | spend by supplier. `INVENTORY.spend` is per product; no state joins spend to vendor |
| Supplier Delay Impact / Late Deliveries | promised dates. Blank on effectively every order in this book — already reported as unanswerable rather than measured against an assumed lead time |

Shipping a category whose inputs do not exist means a card that says zero, or a
number somebody invented. CLAUDE.md §1: *do not weaken a rule to make output
appear.* **Nine real categories beat thirty empty ones.**

Three of the gaps are cheap to close later and worth naming: a `RECEIVABLES`
reducer unlocks four cash/customer categories, and a `SUPPLIER` reducer
unlocks three. Both are new rows in the reducer registry — the registry exists
precisely so a third state costs nothing structural.

---

## 3. What-If: unblock the simulator that already says it is blocked

`commercial/insight/simulate.py` opens with:

> Two of the four scenarios in the specification are not implemented, because
> the data does not exist and building them would mean fabricating it:
> **supplier delay** needs vendor lead times and open purchase orders;
> **inventory change** needs stock levels.

Both now exist — open purchase orders in COMMITMENTS, stock levels in
INVENTORY. So the simulation work is **completing a module that already
declared its own gap**, not building a second simulator beside it.

The state-level what-if the brief describes falls out of the reducer design
almost for free: apply a hypothetical `Delta` to a loaded fold and re-read the
result, using the same `apply_change` the engine uses. No new arithmetic, and
the diff is inspectable because deltas already are.

---

## 4. Traceability already exists in two halves

The brief's chain:

```
Decision → Business Impact → Business State → State Transition → Business Event → ERP
```

- **Decision → ERP** exists today via `evidence_refs`.
- **State → Transition → Event** exists since step 3, via `engine.why()`.

What is missing is the **join**: a decision recording which state keys it was
computed from. One column, and the chain closes end to end. This is the
cheapest high-value item in the whole brief.

---

## 5. Minimum change

```
domain/models.py     Decision gains: origin, impact (JSON), state_keys, state_as_of
state/opportunities/ One module per category, a registry, exactly like
                     state/reducers/. Pure functions of (state rows, thresholds,
                     as_of) → OpportunityDraft. Never touches a session.
state/queue.py       Deterministic impact → priority mapping. One function,
                     testable in isolation, stamped with thresholds_version.
decisions/service.py Gains a second entry point that persists state-derived
                     drafts. The AI path is untouched.
routers/decisions.py Detail gains the state drill-down. List unchanged.
```

No new table. No new queue. No new screen shell.

---

## 6. Sequence

1. **`Decision` gains origin + impact + state provenance** (migration), and the
   traceability join closes. Nothing generates them yet; the existing path is
   provably unchanged.
2. **Opportunity registry + the three inventory categories with the clearest
   impact arithmetic** — Dead Stock, Capital Locked, Inventory Shortage. Proves
   the pipeline end to end with the existing queue and card.
3. **Deterministic impact-based priority**, and the queue-ordering decision from
   §2.2.
4. **The remaining six buildable categories.**
5. **Decision card drill-down** — decision → impact → state → transition → event.
6. **What-if**: unblock `simulate.py`'s two declared-blocked scenarios.
7. *(optional, opens 7 more categories)* `RECEIVABLES` and `SUPPLIER` reducers.

Steps 1–2 are the foundation and are independently valuable: the first
state-derived decision in the existing queue is the proof the architecture is
right, and it is small.

---

## 7. What I would not do

- **Not** a parallel Decision Intelligence Engine. §1.
- **Not** thirty categories. Nine, on data that exists.
- **Not** re-derive the five categories that are already signals.
- **Not** a generic rules DSL for decision categories. Detectors are Python
  functions in a registry — the same shape `signals/` and `state/reducers/`
  already use, and a configuration language with one implementer is the
  abstraction CLAUDE.md §5 calls out.
- **Not** a "confidence score". The brief asks for confidence to be
  deterministic; the honest deterministic version of confidence is the evidence
  sufficiency gate that already exists, not a number between 0 and 1.
