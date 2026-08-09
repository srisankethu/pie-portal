# Architecture

How the Commercial Decision Platform is built, and why it is built that way.

---

## The problem

In a small B2B distributor, the signal that a customer is slipping away — or
that a product line is quietly bleeding margin — is buried in thousands of
invoice and bill lines. By the time anyone notices, the decision window has
closed. Dashboards do not fix this: they show everything and decide nothing, and
a busy salesperson ignores them.

This system detects the handful of situations that genuinely deserve a decision,
explains them, and routes each to the person who owns the call.

---

## The pipeline

One direction, one job per stage, each stage independently testable.

```
Zoho Books
  → ingestion / normalization      adapt · preserve source refs · skip-with-reason
  → read model                     org-scoped projection; rebuildable
  → Signal Engine                  pure detectors → immutable Signals
  → Context Assembly               compact, permission-scoped fact bundle
  → AI Decision Layer              validated interpretation; degradable
  → Decision Store                 routed · prioritised · auditable
  → Human action                   accept / modify / dismiss / escalate
  → Outcome capture                (not built — see "Deliberately not built")
```

### A cut-over in the human-action data

Until the fix that accompanies this note, the clients collapsed two pairs of
intents onto one action each, so rows written before it mean something different
from rows written after:

| Recorded | Before the fix | After |
|---|---|---|
| `ACTIONED` | accept **or** modify | accept |
| `OVERRIDDEN` | escalate | modify |
| `ESCALATED` | never occurred | escalate |
| `VIEWED` | never occurred | a person opened the card |

Both collapses were client-side; the server has always distinguished all seven
actions, and `HumanAction.ESCALATE` — with its approval request and its
deliberately non-closing status — was built, tested and then never called.

**Any adoption figure that spans the cut-over is comparing two definitions**, and
will show a fictitious drop in acceptance on the day of the fix as modifies stop
counting as accepts. Report from the cut-over forward, or label the earlier
period. Do not restate the old rows: nobody recorded which of them were modifies,
and inferring it from the presence of a note would be a guess presented as data.

---

## The invariants

These explain most of the design. Each is enforced by tests.

1. **The AI never computes a number.** Every figure originates in deterministic
   backend arithmetic over source records.
2. **Cost and margin never reach a salesperson.** RESTRICTED facts are removed
   server-side before the AI or the client sees them — *absent, not masked*.
3. **No silent drops, no silent mutations.** Anything skipped carries a reason.
4. **No auto-correction of bad data.** Anomalies are flagged and suppress the
   dependent signal.
5. **Determinism.** Detectors are pure functions of
   `(snapshot, thresholds, reference_date)`.
6. **Backward compatibility.** New capability ships behind a config flag whose
   default reproduces prior behaviour.
7. **No business logic in the frontend.** The client formats; it does not
   calculate.

---

## Core concepts

**Signal** — an immutable, write-once record of a deterministic fact: what was
detected, the metrics behind it, the source records as evidence, and an explicit
data-quality/sufficiency assessment. A signal never contains a recommendation.

**Decision** — the object a human acts on. Links its signal(s), the AI
interpretation, a priority (deterministic base plus a *bounded* AI adjustment),
confidence, assigned role/user, lifecycle status, and the captured human action.

**Data class** — every fact is `OPERATIONAL` or `RESTRICTED`. RESTRICTED means
cost and margin. The tag drives redaction at every layer.

**AI status** — every interpretation is honest about its own state:

| Status | Meaning |
|---|---|
| `OK` | Validated, grounded recommendation. |
| `DEGRADED` | The model responded but failed validation; the deterministic template is shown instead. |
| `FAILED` | Provider unavailable or timed out; facts stand, the reading is missing. |
| `SUPPRESSED` | Evidence insufficient; judgement withheld rather than manufactured. |

---

## The five decision categories

V1 detects exactly five situations — there is no sixth type. Two carry
cost/margin and are never routed to a salesperson.

| Category | The fact it states | Routed to |
|---|---|---|
| **Customer Decline** | Recent-period revenue materially down vs a comparable prior period, for an established customer. | Salesperson |
| **Customer Dormancy** | An account with a regular buying cadence has gone silent well beyond its typical interval. | Salesperson |
| **Margin Deterioration** | A product's gross margin has fallen beyond a threshold, computed only from *reliable* cost. | Manager / Owner |
| **Cost Pass-Through** | Purchase cost rose but selling price did not keep pace. | Manager / Owner |
| **Quote Context** | On demand while quoting: this customer's history, last price, trend, cadence (+ cost/margin for managers). | Salesperson + |

The first four run proactively on a schedule. Quote Context is assembled on
demand from inside the Quote Builder.

---

## Signal Engine

`signals/` — detectors are pure functions of `(snapshot, thresholds, as_of)`,
so they are byte-deterministic and testable without a database. Every emitted
signal carries its evidence (the exact source records) and a sufficiency
assessment.

**Data-quality discipline is the interesting part.** Anomalies — cost at or
above price, zero/placeholder cost, negative values, quantity or price outliers
— are *flagged, never auto-corrected*. A detector that would depend on a flagged
fact **withholds its signal** rather than asserting on bad data.

Concretely: margin deterioration is suppressed when *either* the recent or the
prior cost basis is unreliable, because a placeholder prior cost would inflate
the baseline margin toward 100% and manufacture a large, false deterioration.
This is what lets the system behave sanely on incomplete real-world SME data —
and it is why heavy suppression is a correct outcome, not a failure.

---

## Context assembly

`context/` builds the `ContextBundle` — the curated structure the model sees:

- **Compact by construction.** Facts and a signal summary, never raw records.
- **Permission-scoped.** RESTRICTED facts are dropped for a salesperson before
  the bundle exists, so they cannot leak through the model.
- **Self-describing.** Carries evidence sufficiency and explicit unknowns.
- **Bounds the model's numbers.** `allowed_numbers()` is the set of values the
  AI may cite; the validation gate rejects anything else.

The ×100 "percent" form is admitted only for fractional ratios. A ₹430 fact
therefore cannot ground a fabricated ₹43,000 — closing a 100× monetary
inflation path.

---

## AI Decision Layer

`ai/` — deliberately minimal: **one model call per decision.** No agents, no
tool use, no multi-step orchestration. Its job is interpretation, and it is
fenced in on every side.

### The grounding gate (`ai/contract.py`)

Every response is validated deterministically before it can be shown:

1. Strict JSON matching a fixed schema.
2. Cited fact labels ⊆ supplied facts; cited signal ids ⊆ supplied signals.
3. **Every number in user-facing text must trace to a supplied fact value.**
4. Priority adjustment clamped to a bounded range.
5. A withheld recommendation is stripped of any action text.

### Failure behaviour

The deterministic signal is the floor. Any provider error, timeout, malformed
output, or grounding failure degrades to a plain, fact-derived template with an
honest status — the decision still surfaces and stays actionable. Insufficient
evidence withholds up front, without spending a call.

### Prompt-injection resistance

The user message is *exactly* the curated bundle JSON — no free text, no raw
records. The system prompt instructs the model to treat every value inside the
data (customer names, product text) as data, not instructions. Combined with
grounding and output validation, text hidden inside an ERP record cannot
redirect the system.

### Cost control

Bounded output tokens, temperature 0, and a context hash: unchanged context
means the stored interpretation is reused rather than re-inferred.

---

## Permissions and isolation

| Role | Sees | Cost / margin |
|---|---|---|
| Salesperson | Own assigned customers; the two restricted categories are excluded entirely. | **Never** |
| Sales manager | The whole organization. | Yes |
| Owner | The whole organization, plus AI ops metrics. | Yes |

Scope is enforced **server-side at three layers** — context assembly, decision
routing, and API filtering — never in the UI. A decision outside a caller's
scope returns **404, not 403**, so scope is not probeable.

**Organization isolation:** every record carries an `organization_id`, and every
repository query is scoped to one organization. There is deliberately no
cross-organization query surface. V1 runs a single organization; the seam is
built in rather than bolted on.

---

## Quote Builder integration

**One identity.** `/api/quotes` authenticates the platform user in
`Authorization`, exactly as `/api/v1/*` does, and `is_manager_or_owner` decides
whether a response carries economics. It used to authenticate a Quote Builder
principal of its own — two fixed accounts, any password — and read the real,
org-scoped identity from an optional second `X-Platform-Authorization` header.
That was two logins in one browser: the screen showed the demo account's name
and role, and the org-scoped half of resolution (confirmed mappings, equivalence
bands, the approval gate) fell back to packaged defaults whenever the second
header was absent — which meant "send without signing in to the platform" was
the way around every approval in the product.

The Quote Builder resolves a pasted RFQ into priced lines via the PIE engine.
Each line's drawer offers on-demand decision support for that
*(customer, product)*: the deterministic facts on one side, a clearly separated
AI recommendation on the other, and accept/modify/reject capture that feeds the
Decision Store.

Responsibility boundaries are strict: **PIE** identifies and resolves the
product; the **deterministic layer** computes every number; the **AI** only
interprets; the **salesperson** chooses the final price and product. Nothing is
auto-selected, auto-priced, or auto-sent.

---

## AI observability

### Failure taxonomy

`AiFailureReason` has three tiers:

| Tier | Reasons | Effect |
|---|---|---|
| **Gate rejection** | `SCHEMA_INVALID`, `UNKNOWN_FACT_LABEL`, `UNKNOWN_SIGNAL_ID`, `UNGROUNDED_NUMBER`, `SCALE_VIOLATION` | Raises; the decision degrades to the deterministic template. |
| **Correction** | `PRIORITY_OUT_OF_RANGE`, `ACTION_TEXT_ON_WITHHELD` | Repaired deterministically and *recorded*; output stays usable. |
| **Provider** | `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`, `PROVIDER_ERROR` | Never reaches the gate. |

`SCALE_VIOLATION` separates *a supplied fact rendered at the wrong scale* (₹430
written as ₹43,000 — a units/prompt problem) from *an arbitrary fabrication* (a
model problem). Both are rejected; only the diagnosis differs.

`AIValidationError.code` keeps its original strings for backward compatibility;
`.reason` carries the taxonomy member.

### Telemetry

`ai_call_logs` holds **exactly one row per interpretation decision point**,
including the two paths that never reach the provider: an up-front suppression
on `INSUFFICIENT` evidence, and a context-hash cache hit.

Each row records provider, model, prompt version, context hash, resulting
status, attempts, latency, token usage, estimated cost, failure reason, and any
corrections applied. It records **how a call went, never prompt or response
content** — storing that would create a second, unscoped copy of exactly the
cost/margin facts the permission model exists to contain.

`CallTelemetry` is a pure dataclass built in the AI layer (which stays DB-free);
the caller — which knows the organization — persists it through the org-scoped
`AiTelemetryRepository`. Writing is best-effort: observability must never be
able to fail a decision.

Token usage is read from an **optional** `last_usage` attribute a provider may
expose, so the `AIProvider` protocol (`complete(system, user) -> str`) is
unchanged. Cost is a deterministic estimate from tokens × configured rates, and
is `None` — not zero — when usage is unknown.

### Ops metrics and the health band

`GET /api/v1/internal/ai-metrics` (owner only) reports rolling 7- and 30-day
windows. The band is **two-sided**: a degraded rate above the maximum is `HIGH`;
a rate below the minimum is `SUSPICIOUSLY_LOW`, because a gate that never
rejects anything is either too permissive or paired with a prompt too
constrained to be adding interpretive value. Below a minimum sample it reports
`INSUFFICIENT_DATA` and draws no inference either way.

### Live-provider contract suite

`tests/live/` runs the **real** provider against adversarial fixtures — an
injection string inside a customer name, a product description containing digits
that could be mistaken for facts, a fact set that invites a scale error, an
empty fact set — and asserts **the gate's behaviour, not the model's wording**.
Excluded from the default run (`pytest -m live` to opt in); skips cleanly
without credentials.

---

## Technology

| Layer | Choice |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.0, Pydantic v2, Alembic |
| Database | SQLite (dev/test), Postgres-ready (production) |
| AI | Swappable provider: deterministic offline mock (the default), or Anthropic Claude — see [Turning the AI on](operations.md#turning-the-ai-on) |
| Frontend | React 18, Vite, TypeScript — screen-state driven, plain `fetch` |
| Product intelligence | PIE (pie-parser) for RFQ resolution |
| Source of record | Zoho Books, read-only |

---

## Deliberately not built

Stating these explicitly matters as much as the design itself.

- **Outcome Tracker.** The `Outcome` model exists but nothing writes it. Until
  it does, adoption and decision quality are measurable; realised monetary
  impact is not. This is the most valuable next increment.

  Note that `quote_outcomes` is a different table with live writers and a real
  `DRAFT → SENT → WON/LOST` machine. Outcome capture is half-built, on the half
  that produces revenue — scope the Tracker against what exists rather than from
  zero. For state-derived decisions, `impact.financial` already quantifies what
  each situation is worth at the moment it is raised, so value-*at-risk*-weighted
  acceptance needs no new table; only realised impact does.

- **Reporting on the queue's own lifecycle.** Nothing aggregates what the
  Decision Store captures. The three numbers that would say whether this
  platform works — acceptance by category and by user, modify rate and modify
  distance, and acceptance as a function of queue volume — are not computed
  anywhere. The data for the first and third is present today; the second needs
  `quote_decisions`, where the distance from the recommended price is already a
  number on every priced line.

  When it is built it belongs in `commercial/` — deterministic aggregation over
  persisted rows, and it must never be able to import `ai/`. Copy the shape of
  `ai/metrics.py`: a pure `summarize` over a row sequence, a `report` for the
  rolling windows, an owner-only route, and a **two-sided** band. A category
  accepted almost every time is as suspect as one nobody accepts — it is being
  rubber-stamped, or the detector only fires on the already-obvious — exactly as
  a gate that never rejects is as suspect as one that rejects constantly. Below
  `AI_HEALTH_MIN_SAMPLE` the answer is `INSUFFICIENT_DATA` and no inference is
  drawn in either direction.
- **Prompt/response content logging.** The easiest way to debug a bad
  recommendation, and rejected on purpose: it would create an unscoped second
  copy of the cost/margin facts the permission model works to contain. The
  context hash plus cited fact labels are enough to reproduce a call.
- **Cost budget enforcement / circuit breaker.** Visibility first; acting on the
  numbers should follow real cost data, not precede it.
- **Machine learning in detection.** Detectors stay rule-based and auditable. A
  salesperson who doubts an alert must be able to read exactly why it fired.
- **Multi-organization queries.** The isolation seam exists; a cross-org surface
  does not, and would need explicit group scoping to be safe.
