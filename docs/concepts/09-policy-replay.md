# Counterfactual policy evaluation & simulation

*What replay can honestly do here today, what is missing, what it de-risks, and
what would be wrong to build.*

The framing question: **can a policy change be measured against real history
before it ships?** Several proposals in flight are policy changes — the CAF
payment-terms variant, moves to the approval and review floors, the decision
queue's erosion sensitivity. None of them can currently be sized before the
knob is turned.

This category earns nothing directly. It is worth doing because it is the
cheapest way to stop several other things going wrong.

---

## The one-line summary

The *engine* for this is nearly free: three counterfactual entry points already
exist as pure functions. The *evidence* is not free — two of the three replay
paths read the present rather than the past, in ways that produce plausible
wrong numbers rather than errors. And one thing is not free at all and is the
real blocker: **a `ci_…` threshold hash cannot be dereferenced.**

---

## 1. What replay can honestly do today, with no changes

### Tier 1 — exactly sound, zero code: the quote-approval backtest

`QuoteDecision` is append-only and freezes the values a line was judged against:
`quantity`, `quoted_unit_price`, and the `unit_cost` resolved on the day.

The approval verdict turns out to be a pure function of exactly those three plus
the floor. Only two exceptions set `requires_approval` — `NEGATIVE_MARGIN`
(price ≤ cost) and `BELOW_MIN_MARGIN` (`margin < th.min_margin`), both in
`commercial/quote_exceptions.py` — and `_rank` never suppresses a `policy=True`
exception for being immaterial:

> A stated policy boundary (below cost, below the approval floor, below the
> review floor) is never dropped for being small […] a control that silently
> disappears below some size is not a control.

So *"if the approval floor had been 14% instead of 12%, which quotes would have
needed approval and what was at stake?"* is answerable by reading stored rows and
computing nothing from today's data. **There is no lookahead here to get wrong,
because the row is the snapshot.**

This is implemented in `app/commercial/backtest.py`.

> **A naming trap worth stating.** This is often discussed as moving `m_floor`.
> In this codebase `m_floor` is a *markup* living in
> `incentive_engine/config/parameters.yaml` (`cost × (1 + m)`), while the
> approval floor is `CommercialThresholds.min_margin`, a *margin on selling
> price* (`cost / (1 − m)`). `commercial/floor.py` warns explicitly that
> reinterpreting one as the other "would move every floor by several points
> while looking like a tidy-up." 12% appears as both. Always say which.

### Tier 2 — sound where the evidence is already stamped with a day: Business State

`state/engine.build(as_of=D)` genuinely truncates — `_ordered()` filters
`occurred_on <= as_of`. `business_states` is keyed on
`(organization_id, state, key, as_of)`, so it is a **valid-time series**, not a
current value that overwrites its own history.

And `OpportunityDetector.detect(states, policy, as_of)` takes no session and does
no I/O — `states` arrives already loaded for the right day — with `DecisionPolicy`
carrying its own `version`.

Which means: hold `states` fixed, swap `policy`, re-run, diff. A **pure
in-memory counterfactual** with no database round-trip. This is the cleanest
surface in the codebase and it covers the inventory, receivables, supplier and
supply decision categories today.

### Tier 3 — will run, will produce numbers, and the numbers will be wrong

The Customer × Item and Signal paths.

`recompute(session, org, as_of=D, th=variant, emit_signals=…)` already has
exactly the counterfactual signature, and `backfill.py --dry-run` already
computes-then-rolls-back. It *looks* like a backtester. It is not, because
**`as_of` moves the window origin without truncating the evidence**:

- `_sale_rows` / `_costs_by_product` in `commercial/compute.py` have no date
  predicate at all.
- `compute_relationship` bounds every *window* by `as_of` via
  `in_window(…, end=as_of)` — correct — but `first_transaction_date`,
  `last_transaction_date`, `transaction_count`, `history_months`,
  `cost_covered_txns` and `cost_missing_txns` are taken over **all** lines.
- Those six drive evidence sufficiency (`min_transactions`,
  `min_history_months`). So a backtest at a past date **judges sufficiency using
  data from after that date**: a relationship that was `INSUFFICIENT` then comes
  back `SUFFICIENT` now.

That last point is CLAUDE.md §1's "absence of evidence is not a pass" arriving
through a new door — the benign default is reached by letting the future vouch
for the past.

Signals is worse. `signals/aggregates.load_snapshot` has **no date bound at
all** — only `sales_for_customers` and `costs_for_products`. The dormancy
detector reads `days_since_last` off the snapshot's last order, so at a past
`as_of` it measures the gap to *today's* last order. In a naive backtest,
dormancy essentially stops firing.

> **What is already right, and is the hardest part of any backtest.** Costing.
> `line_economics` resolves cost via `cost_basis_asof(costs, sale.date)` — the
> latest cost record on or before the *sale* date — and `floor.py` and
> `signals.aggregates` reuse that same rule rather than writing a second one.
> Point-in-time-correct cost attribution is what most backtests get wrong. It is
> right here by construction.

> **A second naming trap.** There is no 90-day dormancy threshold to move to 60.
> Dormancy is relative to each customer's own cadence:
> `dormancy_interval_multiplier: 1.5` × their median gap, gated by
> `dormancy_min_orders: 4`. The counterfactual to run is 1.5 → 1.25. The more
> interesting knob is `dormancy_min_orders`, which controls *who is eligible to
> be judged at all* rather than how late they must be.

### What replay cannot do at any tier: prove what was in force

`CommercialThresholds.version` is `"ci_" + sha256(json(asdict(self)))[:10]`;
`SignalThresholds.version` is the `th_…` equivalent. **Nothing maps a hash back
to values.** `commercial_policies` holds one row per organization, overwritten on
edit, and the rest of the dataclass comes from `os.environ` at process start with
no record kept anywhere.

So an approval carrying `ci_9f3a…` proves it was judged under a *different*
policy than today's, and cannot say which. That is **distinguishability, not
explicability**.

CLAUDE.md §1 is scrupulous about this — "A row says which policy judged **the
value it currently holds**" — and the hash's non-invertibility is precisely *why*
that narrowing was necessary. It is also what stops deterministic decision replay
from being possible today.

---

## 2. What is missing, and what it costs

Four items, smallest first.

### 2.1 A threshold-version registry — ~40 lines, one table, one migration

`threshold_versions(version PK, kind, values JSON, first_seen_at)`, written
idempotently wherever a version is stamped — cheapest at *load* time
(`policy.load_for_org`, `signals.engine._thresholds_for_org`), because everything
downstream stamps what it loaded.

This retroactively turns every `ci_…`/`th_…` already sitting on every past
signal, approval and quote snapshot into a dereferenceable pointer. **Nothing
else on this list works without it.**

It must capture the environment-derived fields too: a `CI_RECENT_DAYS` change
moves the hash today and leaves no trace of what moved.

One real risk to review rather than wave through: `load_for_org` sits on read
paths, so this adds a write to requests that currently only read — on SQLite that
changes lock behaviour. Mitigate with an in-process cache keyed by hash (one
INSERT per process per distinct policy) and a best-effort `try/except`, following
the `AiTelemetryRepository` precedent that observability must never fail a
decision.

### 2.2 An `up_to` date bound on the two loaders — ~10 lines

`load_snapshot(…, up_to)` adding `SalesTxn.date <= up_to` /
`CostRecord.date <= up_to` and deriving `last_sale_on` from the bounded set; the
same on `_sale_rows` / `_costs_by_product`. Three `load_snapshot` call sites, two
`compute_for` call sites, all defaulting to `None`.

`load_snapshot`'s docstring already states the rule this must satisfy — "a bound
is only correct when the excluded rows could not have changed the answer." For a
backtest at D that is exactly true; for the live run it is exactly false. Hence a
parameter, never a default.

### 2.3 Truncate the six unbounded fields in `compute_relationship` — ~5 lines

Derive `ordered` from lines ≤ `as_of` before taking first/last/count/history/
coverage. This is arguably a latent correctness bug independent of backtesting:
`as_of` is documented as anchoring "every window", and six fields do not honour
it. Needs a characterisation test on real data first, since it is the one change
here that could move a live number.

### 2.4 Effective-dated thresholds — and do **not** design this fresh

The pattern already exists in this repository and is better than anything worth
inventing. `incentive_engine/config.py` loads a versioned, effective-dated block
with `covers(day)`, refuses floats to protect `Decimal` precision, and
`load_config(as_of=…)` never defaults to the clock:

> a computation that reads the time of day is not reproducible, and a payout that
> changes because it was re-run on a different afternoon is not auditable

`floor.parameters()` even caches per-date specifically so today's rates cannot
leak into a historical period. Give `CommercialThresholds` / `SignalThresholds`
the same loader. A day or two, plus a decision about the environment-variable
half — but the design work is done.

### On bitemporality: two-thirds exists, and the last third should be refused

The general fix for "derived state was overwritten" is valid-time plus
transaction-time. Here:

| | Status |
|---|---|
| Valid time on derived state | **Already there** where it matters — `business_states` is keyed on `as_of`. |
| Transaction time on the event log | **Already there, and unused.** |
| Transaction time on `customer_item_metrics` | **Refuse** — see §5. |

The middle row is the highest-leverage item in this whole category.
`business_events` already carries `recorded_at` **and** `superseded_at`, and its
docstring says exactly what they buy:

> the log records what the platform believed **and when it stopped believing it**

But neither reader supports it: `EventLog.live()` filters
`superseded_at IS NULL`, and `_ordered()` does the same. Adding an
`as_at: datetime` that swaps that for
`recorded_at <= as_at AND (superseded_at IS NULL OR superseded_at > as_at)` is
**~6 lines and no schema change**, and it answers the genuinely hard question:

> Given the documents we had actually read on the day the manager signed, would
> we have produced that signal?

That is not "given what we know now", and it is what an audit of a signed
approval actually needs.

### A data gap no replay design can fix

For the **CAF counterfactual specifically**: `SalesTxn` — the line grain CAF is
computed on — has no salesperson. Zoho *does* send `salesperson_id` on the
invoice and `ingestion/zoho_client.py` reads it, but it is only ever used to set
`Customer.source_owner_id` / `assigned_user_id` — a **current** value, documented
as "the salesperson on this account's *most recent* invoice."

So attributing last quarter's lines would use today's owner. Every account that
changed hands is silently misattributed — and CAF is a *personal payout*, so this
is the error found by the person who was underpaid.

Fix at ingest (`salesperson_external_id` on `SalesTxn` or `InvoiceDoc`); history
is recoverable because Zoho is the system of record and a complete re-sync
rebuilds from nothing. Note this changes the event payload shape, so the field
must be optional or replay of existing events breaks.

Terms data is fine by contrast: `InvoiceDoc` carries `date` and `due_date`, so
the granted credit period is recoverable per invoice.

---

## 3. What this de-risks, specifically

1. **The CAF payment-terms variant.** `parameters.yaml` already carries
   `caf.third_party_charge_rate: 1.00` and `toolkit_charge_rate: 0.50`, each with
   a written first-order-condition argument in `caf.py`. A payment-terms charge is
   a fourth coefficient of the same shape. That argument establishes incentive
   compatibility *at the margin*; what it cannot give is the **level** — how much
   of last quarter's payout it removes, and from whom. A mechanism that is
   theoretically right and takes 30% off one person's quarter is abandoned in
   month two. **Hard prerequisite: the attribution gap above.**

2. **Any move to `min_margin` / `margin_floor`.** Available now, Tier 1. The
   number that matters is not how many quotes cross the line but the
   **approval-queue load**. `OrgPolicy.require_approval_below_review_floor`
   defaults `False` precisely because "flagging every thin line for sign-off
   trains people to rubber stamp, which is worse than not asking." That is a
   claim about volume, and replay is how you learn whether turning it on means
   eleven more approvals a month or four hundred.

3. **`queue_margin_drop_pp`.** This threshold exists *because of an incident*:
   the same sensitivity lived in two places, an owner raised the Settings value to
   quieten the screens, and the decision queue carried on unchanged. The fix
   unified them — so one number now moves both the screens and the queue, and
   there is still no way to see how far the queue moves before shipping. It is
   the knob most likely to be turned by a non-engineer.

4. **The equivalence bands** (`equivalence_tech_band: 0.85`,
   `equivalence_compat_band: 0.60`). These decide whether a pie-parser candidate
   is auto-labelled TECHNICAL EQUIVALENT on a **customer-facing** quote.
   `QuoteDecision.catalog_version` records the parser checksum that resolved each
   line, so "which lines change label" is replayable against real past
   resolutions.

5. **`carrying_rate_is_published`.** One boolean that removes a column and two
   KPI cards from the salesperson stock screen. Not a numeric backtest, but
   replay is how you show what a salesperson stops seeing before flipping it.

6. **The Outcome Tracker**, which `docs/architecture.md` calls "the most valuable
   next increment." Replay does not build it, but it de-risks the design: it says
   which decisions would even have existed under a candidate policy, which
   determines whether outcome measurement has the sample to say anything.

---

## 4. Build order

1. **The quote-approval backtest** — read-only, no migration, no schema change.
   *Shipped in this change.*
2. **The threshold-version registry** — everything past here is guesswork without
   it, and it is retroactively useful the moment it exists.
3. **`as_at` on the event-log readers** — six lines, no schema change, and the
   only item that answers the *audit* question rather than the *tuning* question.
4. **`up_to` on the loaders + the six unbounded fields.** Then
   `recompute(as_of=…, th=variant)` is honest and `--dry-run` becomes a real
   backtester rather than one that looks like one.
5. Only then, if the CAF proposal is scheduled: per-line salesperson attribution.

Steps 2–5 are **not** started here. Step 1 answers the live floor question
exactly and carries no risk; if its answer is boring, the rest is much harder to
justify.

---

## 5. What sounds advanced and is wrong here

### Turning `customer_item_metrics` into an audit log

The tempting move is `valid_from`/`valid_to`, or an append-only history table, so
the upsert stops "losing" superseded numbers. **Refuse it.**

That table is derived by construction — it "holds no source facts of its own" and
"the whole table can be dropped and rebuilt" — and CLAUDE.md §1 says there is no
history there *and there is not meant to be*. Versioning it would:

1. make a disposable projection undroppable, breaking the one property that makes
   `backfill` safe to run;
2. create a second, unscoped copy of RESTRICTED cost and margin — the same
   objection that got prompt/response logging rejected in `docs/architecture.md`;
3. be **worse evidence than what already exists**, because the append-only rows a
   human actually signed (`QuoteDecision`, `ApprovalRequest`, `Signal`) already
   freeze the values. A metrics-history row would be a parallel account of the
   same moment with nobody's signature on it, and two accounts of one moment is
   how an audit gets argued instead of settled.

> The right fix for the upsert is not to version the row. It is to make the
> version stamp on it **dereferenceable** — §2.1, at a tenth of the cost.

### Conflating replay with a randomised holdout

They answer different questions and will be conflated in the room.

**Replay evaluates a policy** on a frozen world: it holds every human decision
constant and asks what the machine would have said. It cannot say what the
salesperson would have done differently, and it **systematically overstates** any
threshold that mostly changes behaviour rather than outcomes — a tighter floor
overridden 80% of the time shows up in replay as recovered margin and in life as
a rubber stamp.

**A holdout evaluates an intervention** on a moving world, and needs randomisation
and outcomes the platform does not yet have: `Outcome` exists and nothing writes
it, and while `QuoteOutcome` moves DRAFT → SENT → WON/LOST, the terminal states
are set by a person, so coverage is an operational question to check before
anyone quotes a win rate.

Two consequences. Run replay first — it is nearly free and needs no consent. And
never report a replayed figure as "margin we would have earned"; it is *"margin
the policy would have protected, assuming everyone behaved identically."*

`QuoteDecision`'s own docstring already names the better signal:

> An override is a price that went out despite a rule firing. The reason is the
> single most valuable field in this table: it is how a threshold that is wrong
> for the business gets found.

That is cheaper and stronger than most simulations, and it is sitting there
unread.

### Rebuilding `BusinessState` for every historical day

`build()` re-folds from the beginning each time, deliberately, writing a
`StateTransition` per event per state — the reducers already carry
`records_transitions` because two monthly trade states alone produced ~90,000
transition rows per build. A 250-business-day sweep is that, 250 times, for a
question usually answerable at a handful of period ends. **Backtest at
month-ends.**

### A simulation service or scenario UI

Nothing here needs a request path. All three counterfactual surfaces are already
pure — `compute_drafts(snapshot, th, as_of)`,
`OpportunityDetector.detect(states, policy, as_of)`,
`detect(metrics, benchmark, th, evidence)` — and `--dry-run` already supplies
transactional isolation. A CLI that prints a diff is the whole product.

An endpoint would mean role scoping, RESTRICTED redaction and an approval story
for a tool three people use quarterly. It would also cross a boundary that
currently holds: this is analysis *about* the policy, not analysis the platform
serves.

### Letting the AI propose the threshold

Violates §1 outright, and worth stating because a "what should the floor be"
screen is exactly the shape someone will ask for. The model may read and phrase a
replay result. **It may not produce the number.**

---

## 6. What shipped with this document

`app/commercial/backtest.py` — the Tier 1 backtest, read-only, no schema change:

```
python -m app.commercial.backtest --org org_pie --min-margin 0.14
python -m app.commercial.backtest --org org_pie --min-margin 0.14 \
    --margin-floor 0.18 --since 2025-04-01 --until 2026-03-31
```

It reuses `quote_exceptions.evaluate` over `references.build_references` — the
same pair `quote_intelligence.assess_line` calls on the live quote screen —
rather than restating the two rules that set `requires_approval`.
`test_replayed_verdict_matches_the_live_quote_assessment` pins them together, so
a change to the exception rules moves both or fails there.

Two things it refuses to report quietly:

- A line with no cost on record, or a zero placeholder cost, is counted
  `unjudgeable` — never as passing.
- `baseline_disagreements` counts rows where replaying today's policy does not
  reproduce the verdict the row was stamped with. Those were priced under a
  policy this replay cannot rebuild.

**That second counter is the measurement that decides §2.1.** Its value on the
first real run says what share of the quote history is currently un-replayable —
which is the business case for the threshold-version registry, measured rather
than argued.

The relationship and peer rules are absent from the replayed exception list,
because the stored row carries no trading history. None of them can set
`requires_approval`, which is why the approval verdict is still exact — and why
the report states that outcome only and never claims to be the full exception set
a quoter saw.
