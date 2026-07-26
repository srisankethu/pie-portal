# Commercial Decision Platform — Architecture

The system is one deterministic pipeline. Data flows one way; each stage has a
single job and is independently testable.

```
Zoho Books
  → ingestion / normalization      (adapt, preserve source refs, skip-with-reason)
  → read model                     (org-scoped projection; rebuildable)
  → Signal Engine                  (pure detectors → immutable Signals)
  → Context Assembly               (compact, permission-scoped fact bundle)
  → AI Decision Layer              (validated interpretation; degradable)
  → Decision Store                 (routed, prioritised, auditable)
  → Human action                   (accept / modify / dismiss)
  → Outcome capture                (deferred — see "Not built")
```

## Non-negotiable invariants

1. **The AI never computes a number.** Every figure originates in deterministic
   backend arithmetic over source records. The grounding gate enforces this and
   no code path may bypass it.
2. **Cost and margin never reach a salesperson.** RESTRICTED facts are removed
   server-side before the AI or the client sees them — absent, not masked.
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

## AI observability (WS3)

### Why

The grounding gate had only ever been exercised against a deterministic offline
mock, and nothing recorded what the AI layer cost or how often it degraded. A
single generic "validation failed" flag also made a prompt problem
indistinguishable from a schema problem.

### Failure taxonomy

`AiFailureReason` (`app/domain/enums.py`) has three tiers:

| Tier | Reasons | Effect |
|---|---|---|
| **Gate rejection** | `SCHEMA_INVALID`, `UNKNOWN_FACT_LABEL`, `UNKNOWN_SIGNAL_ID`, `UNGROUNDED_NUMBER`, `SCALE_VIOLATION` | Raises; decision degrades to the deterministic template |
| **Correction** | `PRIORITY_OUT_OF_RANGE`, `ACTION_TEXT_ON_WITHHELD` | Repaired deterministically and *recorded*; output stays usable |
| **Provider** | `PROVIDER_TIMEOUT`, `PROVIDER_UNAVAILABLE`, `PROVIDER_ERROR` | Never reaches the gate; decision degrades to FAILED |

`SCALE_VIOLATION` is new and operationally important: it separates *a supplied
fact rendered at the wrong scale* (₹430 written as ₹43,000 — a units/prompt
problem) from *an arbitrary fabrication* (a model problem). Both are still
rejected.

`AIValidationError.code` keeps its original string values for backward
compatibility; `.reason` carries the taxonomy member.

### Telemetry

`ai_call_logs` holds **exactly one row per interpretation decision point** —
including the two paths that never reach the provider:

* an up-front suppression on `INSUFFICIENT` evidence (`provider_called=False`),
* a context-hash cache hit (`cache_hit=True`).

Each row records provider, model, prompt version, context hash, resulting
`ai_status`, attempts, latency, token usage, estimated cost, the failure reason,
and any corrections applied. It records **how a call went, never prompt or
response content**.

`CallTelemetry` (`app/ai/telemetry.py`) is a pure dataclass built inside the AI
layer, which stays DB-free; the caller — which knows the organization — persists
it through the org-scoped `AiTelemetryRepository`. Writing is best-effort by
design: observability must never be able to fail a decision.

Token usage is read from an **optional** `last_usage` attribute a provider may
expose, so the `AIProvider` protocol (`complete(system, user) -> str`) is
unchanged. Cost is a deterministic estimate from tokens × configured rates, and
is `None` — not zero — when usage is unknown.

### Ops metrics

`GET /api/v1/internal/ai-metrics` (**owner only**) reports, over rolling 7- and
30-day windows: call counts by status, degraded/failed/suppressed/cache-hit
rates, cost per decision and per day, latency, the failure-reason distribution,
and the health band.

**Two-sided health band.** A degraded rate above `AI_DEGRADED_RATE_MAX` is
flagged `HIGH`. A rate below `AI_DEGRADED_RATE_MIN` is flagged
`SUSPICIOUSLY_LOW` — a gate that never rejects anything is either too permissive
or paired with a prompt too constrained to be adding interpretive value. Below
`AI_HEALTH_MIN_SAMPLE` calls the band reports `INSUFFICIENT_DATA` and draws no
inference in either direction.

### Live-provider contract suite

`tests/live/` runs the **real** provider against adversarial fixtures: an
injection string inside a customer name, a product description containing digits
that could be mistaken for facts, a fact set that invites a scale error, and an
empty fact set. It asserts **the gate's behaviour, not the model's wording**.

Excluded from the default run via `pytest.ini` (`addopts = -m "not live"`); opt
in with `pytest -m live`. Skips cleanly without `ANTHROPIC_API_KEY`.

### Config keys

| Key | Default | Reproduces V1? | Meaning |
|---|---|---|---|
| `AI_TELEMETRY_ENABLED` | `1` | yes — additive only | Write telemetry rows. Off ⇒ no rows; no other behaviour changes. |
| `AI_COST_PER_MTOK_INPUT` | `1.0` | n/a (new) | USD per million input tokens. **Set to the deployment's actual contracted rate**; the default is indicative only. |
| `AI_COST_PER_MTOK_OUTPUT` | `5.0` | n/a (new) | USD per million output tokens. Same caveat. |
| `AI_DEGRADED_RATE_MAX` | `0.25` | n/a (new) | Upper health bound on the degraded rate. |
| `AI_DEGRADED_RATE_MIN` | `0.005` | n/a (new) | Lower health bound (suspiciously-clean gate). |
| `AI_HEALTH_MIN_SAMPLE` | `20` | n/a (new) | Minimum calls before any health inference. |

Telemetry is purely additive: it changes no decision, no existing API payload,
and no existing behaviour. All 111 pre-existing tests pass unmodified.

### Migration

`b2f4c81d90a7_ws3_ai_call_telemetry` creates `ai_call_logs` with its indexes;
`downgrade` drops them. Forward and reverse are exercised by
`test_migrations.py`, which asserts every ORM table exists after upgrade and
none remain after downgrade.

### Deliberately not built in WS3

* **No prompt/response content is logged.** Storing it would be the easiest way
  to debug a bad recommendation, but it is customer commercial data and would
  create a second, unscoped copy of exactly the cost/margin facts the permission
  model works to contain. The context hash plus the cited fact labels are enough
  to reproduce a call from the read model.
* **No cost budget enforcement or circuit breaker.** WS3 asks for visibility.
  Acting on the numbers (throttling, hard caps) is a product decision that
  should follow real cost data, not precede it.
* **No time-series/percentile store.** Rolling windows are computed by scanning
  the logged rows, which is correct and cheap at this volume. A metrics backend
  is premature.
* **No frontend surface.** The metrics endpoint is owner-scoped JSON; a UI for
  it has no decision-support purpose yet.
