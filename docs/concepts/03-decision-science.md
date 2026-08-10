# Decision science — the queue is the product

The platform's thesis is that dashboards show everything and decide nothing. If
the salesperson does not act on the queue, the return on everything else is
zero — and until this work, nothing in the system could tell you whether they
do.

This is a measurement job first and a design job second. What follows is what
the platform captures, what can be computed from it without new collection,
what genuinely needs instrumentation, what an owner should look at first, and
what sounds advanced but is wrong here.

---

## 1. The finding

The Decision Store's lifecycle model is good: nine states, seven human actions,
an append-only trail, role routing, a dedup key. **The clients used four of the
seven actions and mapped two pairs of intents onto one action each.** So the
platform was accumulating data that reads like adoption data and is not.

| | Before | After |
|---|---|---|
| `ACTIONED` | accept **or** modify | accept |
| `OVERRIDDEN` | escalate | modify |
| `ESCALATED` | never occurred | escalate |
| `VIEWED` | never occurred | somebody opened the card |

Both collapses were client-side. The server has always distinguished all seven
actions, and `HumanAction.ESCALATE` — with its approval request and its
deliberately non-closing status — was built, tested, and then never called by
anything. "Send to management" set a closing status and told management
nothing.

Three consequences, and the third is the one that matters most:

- **Acceptance rate was an engagement rate wearing an acceptance rate's label.**
- **Modify rate was unrecoverable**, on both surfaces.
- **There was no denominator for attention.** With no `VIEW`, "nobody looked at
  this" and "somebody read it and moved on" are the same row — which is exactly
  the difference between a detector that is wrong and a queue that is not being
  worked, and it is the first question an owner has.

Nothing in this work changes what the data says about the past. It means the
data from here on says what it claims to.

---

## 2. What was already captured

More than the brief assumed, and in better shape. The problem was never a thin
schema.

### `decisions`

Indexed and queryable: `decision_type`, `origin`, `assigned_user_id`, `status`,
`subject_entity_id`, `organization_id`. Plus `detected_at`, the four
`priority_*` columns, and `override_reason` — free text on every dismissal, and
the cheapest diagnostic in the platform.

In JSON, readable per row: `impact.financial` (what a state decision is worth,
as a Decimal string), `confidence.ranking` (the queue score, showing its
working), `ai.status`, and `human_action` with its full append-only trail.

Three traps:

- **`detected_at` is not a window basis.** It is copied from the signal and
  rewritten every time a card is refreshed, so a window built on it moves a
  decision forward in time whenever the detectors run and counts the same card
  in two windows. `DecisionRepository.since` uses `created_at` for that reason.
  This document originally argued the opposite; #59 got it right first.
- **`updated_at` is not an action timestamp.** `DecisionService.generate` writes
  to open decisions on every run, so the ORM's `onupdate` fires on
  regeneration. The trail is the only place the human's clock is honest.
- **The trail is deliberately not SQL-reachable**, and the code says so with its
  reasoning. That was right for a per-card reader and is the binding constraint
  the moment anyone wants a rate. It needs no restructuring — at these volumes
  it is a Python pass.

### `ai_call_logs`

The one properly instrumented table: one row per interpretation decision point
including cache hits and up-front suppressions, indexed on `created_at`,
`decision_type`, `ai_status`, `failure_reason`. `ai/metrics.py` already rolls it
up behind an owner-only route. **That module is the template** for any rolling
report here.

### `quote_decisions` — the overlooked one

Append-only, one row per priced line, with real writers. It carries what the
decision queue cannot: `overridden` as a boolean column, `override_reason_code`
coded *and* free text, `requires_approval` so an override rate has a correct
denominator, and `quoted_unit_price` against the `references` the policy
computed — **modify distance, as a number**. The model's own docstring calls the
reason field "the single most valuable field in this table". Nothing read it.

### `quote_outcomes` is real

"The `Outcome` model exists and nothing writes it" is true of `outcomes`, whose
only non-test reference is the erasure sweep. But `quote_outcomes` is a separate
table with live writers and a working `DRAFT → SENT → WON/LOST` machine.
**Outcome capture is half-built, on the half that produces revenue.** Scope an
Outcome Tracker against what exists, not from zero.

---

## 3. The three numbers

Shipped in `backend/app/decisions/outcomes.py`, behind
`GET /api/v1/internal/queue-adoption` (owner only), over rolling 7- and 30-day
windows.

**Not a second module.** Concept 02 (#59) landed `decisions/outcomes.py` while
this branch was open, measuring detector false-alarm rate over the same rows.
Two modules would have meant two definitions of what a human did, in two
payloads nobody could reconcile — the responsibility-duplication row of
CLAUDE.md §2, which applies to a sibling's merged code exactly as it does to
one's own. So the adoption half was folded in beside it and reuses its `_rate`,
its `JUDGED_STATUSES` and its `DecisionRepository.since`.

`RULED_STATUSES` is defined one line below `JUDGED_STATUSES` and derived from
it, because the two questions differ only in the denominator: a false-alarm rate
divides dismissals by *every* judgement, while acceptance leaves escalation out
— handing a decision upward is not a verdict on the recommendation. Stating both
a line apart makes the difference visible instead of leaving it to drift across
two files.

**The dismissal rate is not recomputed here.** `/detector-outcomes` owns it. The
dismissal *count* is reported, because without it the acceptance denominator
cannot be decomposed, and a denominator a reader cannot check is one they have
to trust.

Two endpoints from one module, on purpose: a payload carrying per-user
acceptance and quote-line pricing distance is not a statement about detectors,
and an endpoint whose name is wrong is worse than a second endpoint.

### Acceptance by category and by user

Two denominators, both named in the payload, because they answer different
questions and merging them hides the one that matters:

```
acceptance_rate  = accepted ÷ (accepted + modified + dismissed)
engagement_rate  = (accepted + modified + escalated) ÷ raised
```

`EXPIRED`, `SUPERSEDED` and `RESOLVED` are excluded from both: they are
detector-driven transitions, and counting them as rejections blames a person for
the passage of time.

Two cuts come free and are more interesting than the headline:

- **By `origin`.** `SIGNAL` decisions carry an AI reading; `STATE` decisions are
  arithmetic over folded facts and never touch `ai/`. Comparing their acceptance
  is a natural experiment on whether the interpretation layer earns its cost —
  already running, already logged, nobody had looked.
- **Value-weighted, from `impact.financial`.** No Outcome table needed. "₹X of
  flagged exposure was dismissed unread last month" is a sentence an owner can
  act on. It is not the same sentence as "₹X was saved", and the payload says so.

### Modify rate, and distance where distance exists

The rate comes from the lifecycle, now that modify is its own action. **The
distance does not, and is not invented.** A `Decision` has no recommended value
to measure against — the AI recommendation is prose, deliberately, and the
deterministic `actions` are a set of options rather than a number. Manufacturing
a numeric proxy so the metric existed on both surfaces would be a number the
interpretation layer had effectively produced.

Distance is measured where it is genuinely a number: on a priced quote line,
against `TARGET_MARGIN_PRICE`. **The sign is kept.** Systematically pricing
below the recommendation and systematically pricing above it are different
diagnoses with different fixes, and a mean absolute distance reports a business
discounting hard and a business holding firm as the same finding. Lines the
policy could not price are counted and excluded, never read as agreement.

### Acceptance against queue volume

The number that bounds how many signals should exist. Depth at the moment of a
ruling is not stored — `decisions` has no transition log, unlike `BusinessState`
— so it is reconstructed from the trails and the report says it is approximate
rather than implying precision it lacks.

**Read the shape, not a cell.** A rate that falls as depth rises is the
attention ceiling, and every future detector proposal has to argue against it.
If it does not fall, the queue has never been long enough to saturate anyone and
the whole attention-budget concern is premature. Both answers cost the same
query.

### The bands are two-sided

`LOW` / `OK` / `SUSPICIOUSLY_HIGH` / `INSUFFICIENT_DATA`.

A category accepted almost every time is as suspect as one nobody accepts: it is
being rubber-stamped, or the detector only fires on situations the reader
already knew about. Both cost attention without adding judgement, and the second
is harder to notice because it looks like success. This is the same instinct as
the two-sided band on the AI degraded rate.

Below `MIN_SAMPLE` the answer is `INSUFFICIENT_DATA` and no inference is drawn in
either direction. A rate over an empty denominator is `null`, never `0.0` —
zero would read as "everybody rejects it", which is the benign-default failure
this codebase has been bitten by three times, pointing the other way.

### The cut-over is derived, not configured

Rows on either side of the capture fix do not mean the same thing. Rather than a
constant somebody must remember to set — and which is silently wrong on any
environment that deployed on a different day — the cut-over is derived from
evidence: no client ever sent `VIEW` or `ESCALATE` before the fix, so the
earliest one in the data is a lower bound on when the new client was in use.

`null` means **unknown**, not *no cut-over*, and the payload says so. Old rows
are left alone: nobody recorded which were modifies, and inferring it from the
presence of a note would be a guess presented as data.

---

## 4. What genuinely needs new instrumentation

Ranked by cost. Everything above needed none.

| What | Cost | Note |
|---|---|---|
| Effort class per decision type | A lookup table | Declared, not learned. See §6. |
| Queue-depth snapshot | Small, probably unnecessary | Only if the reconstruction proves too noisy. Try the free version first. |
| `Outcome` — realised impact | Weeks | The genuine build, and smaller than it looks: see §2. |

Not instrumentation, and it should not become it: **prompt and response content
logging**. `docs/architecture.md` rejects it because it would create an unscoped
second copy of exactly the cost and margin facts the permission model exists to
contain. A low acceptance rate on an AI-interpreted category will make it
tempting to reopen — "we just need to see what it said". The context hash plus
cited fact labels reproduce the call. The answer stays no.

---

## 5. What the owner should look at first

**In this order, and the first step is not the report.**

1. **Is anyone acting at all.** `GET /internal/queue-adoption` and read
   `engagement_rate` and `untouched` before anything else. If almost every
   decision is untouched, the three numbers are premature and the diagnostic has
   already returned its answer: no amount of ranking sophistication fixes a queue
   nobody opens.
2. **Read the dismissal reasons by eye. Not as a rate.** Twenty free-text
   dismissals read carefully will say more than two hundred counts. "We already
   knew, the customer called last week" is a detector firing too late. "Third
   time for the same account" is a dedup window that is wrong. "Not my customer"
   is a routing bug. None of those is visible in a rate; all three are
   actionable in an afternoon.
3. **Acceptance by category, with bands.** Expect most cells to read
   `INSUFFICIENT_DATA`, and treat that as the correct output rather than a
   failure.
4. **The volume curve.** Last, because it needs the most rows — but it is the
   number that decides whether any future detector is worth building, so start
   accumulating toward it now.

**One caveat before reading any of it.** Nineteen of the twenty-two decision
types are RESTRICTED and route to a manager or owner. Of the three that are not,
`QUOTE_CONTEXT` is excluded from the proactive queue. **A salesperson's queue
contains two decision types out of twenty-two**, and every state-derived decision
lands on one desk with `assigned_user_id` unset.

So "acceptance by user" is in practice a measurement of the owner's own week.
That is not a defect — the routing is correct, since those decisions carry cost
information — but the volume ceiling this category worries about applies to *one
person*, and the per-user analytics one might imagine have a population of about
one. Design for that reality.

---

## 6. What sounds advanced but is wrong here

**ROC curves and chosen operating points per detector.** The framing is right;
the curve is not derivable. A ROC needs labelled ground truth, and accept/dismiss
are not those labels — a dismissal may mean the detector was wrong, or that the
person was busy, or that they already knew, or that they were wrong. Fitting a
curve on those labels and tuning thresholds to it optimises detectors **for being
agreed with**, which is the §1 prohibition on weakening a rule to make output
appear, one level up and much harder to spot.

*What is right and cheap:* the cost-ratio reasoning without the curve. For each
of the twenty-two types, one line — what a miss costs in rupees, what a false
alarm costs in minutes. An hour of domain knowledge, defensible, auditable.

**The attention budget as a knapsack.** Right instinct, wrong implementation, and
the real finding underneath is smaller and more useful. `state/queue.py` scores
`money_points + urgency_points`, where money is `financial ÷ rupees_per_point`
**capped at 75**. At the default `decision_rupees_per_point = 5,000`, that cap is
reached at **₹3,75,000**. Every situation at or above ₹3.75 lakh scores
identically on money — a ₹4 lakh dead-stock line and a ₹40 lakh credit exposure
separated only by lateness and then detection date.

For a distributor holding real inventory and receivables, many situations sit
above that line, so **the top of the queue is plausibly flat, exactly where
ranking matters most.** Checkable today, because `confidence.ranking.money_points`
is persisted on every state decision. If the saturated share is large, the fix is
a threshold conversation about `rupees_per_point` — not an optimiser. A genuine
knapsack also needs a denominator, minutes-to-act, which the platform has no
honest way to derive. The right version is a declared effort class per type: a
table an owner can argue with.

**Learned or personalised priority.** Three objections, and the third is
decisive. It breaks auditability — "because you accepted similar ones" is not a
reason a doubting salesperson can read. With N in the tens it fits noise. And
**it is self-confirming**: a queue that learns to surface what gets accepted
drives acceptance rate up regardless of whether the business improved, and
acceptance rate is the metric being used to judge the platform. Any adaptive
ranking must be evaluated against something other than acceptance — and that
something is `Outcome`, which does not exist. Sequencing is not negotiable.

**Switching off a low-acceptance category too quickly.** Right in spirit, one
guard: a low rate on a restricted type measures one manager's week, not the
category's worth. A category raised three times and accepted zero times is
`INSUFFICIENT_DATA`, not a dead category. The band enforces this rather than
leaving it to judgement.

**A dashboard of the three numbers.** The founding thesis is that dashboards
decide nothing; three charts of acceptance rate would be exactly that, one level
up. The output is a band and a recommended reading per group — *switch this off*,
*this is working*, *not enough data yet* — in the same shape as the AI health
band, which names what to investigate rather than plotting a line and leaving the
reader to infer.

---

## 7. Still open

- **The diagnostic has not been run against real data.** This work fixed the
  instrument and built the readout; nobody has taken a reading. That remains the
  gate on further investment in this category.
- **`Outcome`.** Realised impact. Everything here measures adoption and
  decision quality, which is what the architecture doc says is measurable
  without it — and it is still the most valuable next increment.
