# A time-series state for trade — architecture analysis

Written before any code, per the same rule the Business State and Decision
Intelligence analyses followed: the expensive mistake here is not a wrong
reducer, it is building the wrong *shape* of one. This one is a genuinely
different shape from `INVENTORY` and `COMMITMENTS`, and that difference has to
be settled before anything is written.

---

## 1. What the eight screens actually need

Read from `routers/insight.py` directly, not inferred:

| Screen | Function | Grain it reduces over | Needs a series? |
|---|---|---|---|
| Storyboard | `flow.compute` + `cohorts.lost_revenue` + `cohorts.dormancy` | customer | Two periods (current vs. prior) |
| Weather | `flow.compute` + `cohorts.dormancy` | customer | Two periods |
| Revenue flow | `flow.compute` | customer | Two periods |
| Customer journey | `cohorts.journey` | customer, **per month** | Yes — up to 24 monthly points |
| Migration matrix | `cohorts.migration` | customer | Two periods |
| Lost revenue | `cohorts.lost_revenue` | customer | Two periods |
| Revenue composition | `composition.build` | customer **or product**, per month | Yes — up to 24 monthly points |
| Buying cadence | `cadence.build` | customer, **per order** | No — needs order-level gaps, not months |

Two things fall out of this that change the scope from what I described
verbally.

**Six of eight need a monthly series; two need something else entirely.**
Storyboard, weather, revenue flow, migration and lost revenue all reduce to
*two periods* — "this window vs. the one before it" — which a monthly series
can produce by summing the right months. Journey and composition need the
*actual monthly points*, not just two aggregates.

**Cadence needs order dates, not monthly totals.** `cadence.build` computes the
typical gap between orders and flags whoever is overdue against their own
rhythm — the same arithmetic the dormancy signal already runs. A month-end
total of "14 orders in June" cannot reconstruct that those orders landed on the
3rd, 9th, 21st and 29th. This screen cannot move onto a monthly-point reducer at
all. It would need the event log directly, which is a different and smaller
change (a bounded read of `SALE_LINE_RECORDED` events for one customer, already
ordered) — worth doing, but not part of this reducer.

**Revised scope: 7 of 8 screens can move.** Cadence stays as it is, or gets its
own narrow fix later. That is not a downgrade from the six I said verbally —
it's one better, because migration and lost-revenue turn out to be
period-aggregates rather than something needing a third kind of data.

---

## 2. Why this is a different shape from `INVENTORY` / `COMMITMENTS`

The existing two reducers answer "what is true as of one day" — a single fold,
one row per key, replaced wholesale on each rebuild. `engine.build()` reads
every live event up to `as_of` and produces exactly that.

A trend chart needs "what was true as of *each* of the last 24 days" — a
series, not a point. Two ways to get there, and they are not the same shape of
change:

**(a) Build the existing engine 24 times, once per month-end.** Reuses
`engine.build()` completely unmodified — call it with 24 different `as_of`
values and store 24 sets of rows. This is *cheap to build* and *expensive to
run*: at this book's volume (~40k lines) that's 24 full folds, each reading the
whole live event set again. Today's single build already takes a fraction of a
second, so 24 of them is still fast here — but it means the sync's fold step
goes from O(events) to O(events × 24), which is the wrong direction given step
4 was about removing an O(events × screens) cost.

**(b) A genuinely incremental monthly reducer**, structurally different from
`INVENTORY`/`COMMITMENTS`: it walks the live events *once*, in date order, and
emits one row per `(customer, month)` — or `(customer, product, month)` for the
composition screen's product dimension — accumulating within each month bucket
as it goes. This is one pass over the event log regardless of how many months
are requested, and it is the shape that actually solves the scaling concern
step 4 raised.

**(b) is the right one**, and it is a new capability the engine does not have
today: every existing `Delta` targets one key or accumulator. A month-bucketed
reducer needs a *composite key* — `(subject, period)` — which `engine.py`
already supports structurally (nothing stops a reducer from making its key
`f"{customer_id}:{month}"`), but no reducer has exercised that path yet, so it
is untested territory rather than a known-good one.

---

## 3. The state shape

One new state, `CUSTOMER_MONTH`, keyed on `f"{customer_id}:{yyyy-mm}"`:

```
revenue            Decimal   ADD, per sale line in that month
gross_profit       Decimal   ADD (RESTRICTED — see §5)
orders             int       ADD, distinct source invoices
units               Decimal   ADD
last_order_on      date      MAX within the month
```

And, for the composition screen's product dimension, a second key shape in the
*same* state — `f"{customer_id}:{product_id}:{yyyy-mm}"` — or a second state,
`CUSTOMER_ITEM_MONTH`. Two states is more honest: `CUSTOMER_MONTH` is read by
five of the seven screens and stays small; `CUSTOMER_ITEM_MONTH` is read by one
(composition, product dimension) and is much larger — this book already has a
`CustomerItemMetric` table at that grain, so the cardinality is known. Folding
them together would make every reader of the small state pay for the large
one's size.

`gross_profit` needs `CostRecord`, which today's `COST_LINE_RECORDED` events
already carry — no new event type. It reuses `cost_basis_asof`'s "most recent
cost known as of that date" rule, ported into the reducer.

---

## 4. What changes in the sync, and what does not

The single-date `engine.build()` call at the end of a sync is unaffected — it
still builds `INVENTORY` and `COMMITMENTS` for `as_of = today`, once, as now.

`CUSTOMER_MONTH` is a **separate build call**, `build_series()`, new to
`engine.py`: walks live events once, buckets by month, and replaces all months
from the earliest touched event onward (not just the current month — a
back-dated correction can rewrite March, and the fold must not leave April
through July stale). This is more expensive than the point build only in
proportion to history length, not in proportion to screens, which is the
property that matters.

Run at the same point in the sync as the existing `build()` call, immediately
after it — same "best-effort, must not fail the pull" wrapping already in
`jobs.py`.

---

## 5. Cost and margin stay out of a salesperson's reach

`gross_profit` in `CUSTOMER_MONTH` is cost-derived and therefore RESTRICTED by
the same rule as everything else in `commercial/`. The reducer computes it
(state has no role concept — nothing in `state/` does), but the **router**
strips it for a salesperson exactly as `stock.to_dict`'s `with_cost` flag does
today. This is not new plumbing; it's the existing pattern applied to a new
field.

---

## 6. The equality harness, per screen

Exactly the discipline the stock-screen move used, because it caught two real
bugs there (the ₹5,00,000 double-count, the offtake-window inversion) that no
amount of "looks correct" review would have.

For each of the five screens that reduce two periods from a series
(storyboard's flow component, weather, revenue-flow, migration, lost-revenue):
a test that runs the *old* line-scanning code and the *new* series-read code
against the same fixture and asserts identical output, before the router is
switched over.

For journey and composition: same discipline, but comparing the full 24-point
series rather than two aggregates — the harder case, since a bucketing error
would only show up a few months in.

---

## 7. Sequence

1. **`build_series()` in `engine.py`** — the incremental, composite-key fold.
   No reducer yet. Tested against the existing `build()`'s single-point
   semantics: a series built up to month M and a point build at `as_of = end of
   month M` must agree on that month's row.
2. **`CUSTOMER_MONTH` reducer.** Five screens' worth of arithmetic (storyboard,
   weather, revenue-flow, migration, lost-revenue) collapses to reading two
   slices of one series and summing. Equality harness per screen, per §6.
3. **`CUSTOMER_ITEM_MONTH` reducer** — unlocks composition's product dimension
   and journey (customer dimension only needs `CUSTOMER_MONTH`).
4. Wire `build_series()` into the sync, best-effort, beside the existing
   `build()` call.
5. **Cadence is out of scope for this reducer.** If it moves at all, it is a
   separate, smaller change reading `SALE_LINE_RECORDED` events directly for
   one customer — order dates, not monthly totals.

---

## 8. What I would not do

- **Not** rebuild the point-in-time engine 24 times per sync. Wrong direction
  given why step 4 existed.
- **Not** one state for both grains. `CUSTOMER_MONTH` and `CUSTOMER_ITEM_MONTH`
  differ in cardinality by roughly the item count, and folding them punishes
  every reader of the small one.
- **Not** move cadence onto this. It needs a different kind of data
  (order-level gaps) that a monthly bucket structurally cannot hold.
- **Not** a generic "time-bucketed reducer" abstraction before there are two
  concrete reducers that need one. `CUSTOMER_MONTH` and `CUSTOMER_ITEM_MONTH`
  are the two; if a third time-bucketed state shows up later, that is when the
  shared shape gets extracted — not before, per CLAUDE.md §5's warning about
  abstractions built for a "might need it later."

---

## 9. What building it actually showed — measured, after the fact

Written after the reducers landed. Two things this note asserted turned out to
be wrong, and one number was worse than assumed. Left as a record rather than
edited away: the reasoning above was sound and the conclusions still moved.

### 9.1 `build_series()` was not needed

§2 and §4 called for a second engine function. §2 also observed, correctly,
that `engine.py` "already supports [a composite key] structurally — nothing
stops a reducer from making its key `f"{customer_id}:{month}"`". That
observation was the whole answer. `build()` folds every live event in
`(occurred_on, seq)` order into `(state, key)` accumulators, in one pass, with
no opinion about what a key means. Putting the month in the key gives the
incremental monthly fold with **no new engine capability**, and the SUPPLIER
reducer had exercised the composite-key path in the meantime, so it was no
longer untested territory either.

A `build_series()` beside `build()` would have been a second way to do one
thing, and the first divergence between them a bug nobody could see.

### 9.2 `CUSTOMER_ITEM_MONTH` barely compresses

§3 argued the item grain's "cardinality is known" because a `CustomerItemMetric`
table already exists at that grain. It exists at **(customer, item)** — adding
the month multiplies it by the months traded, which is the whole difference.

Measured at this book's shape (420 customers, 3,200 items, 40,000 sale lines
over three years, customers drawing on a ~15-item repeat basket, Pareto-skewed):

| State | Rows | Fold time | Compression vs. 40,000 lines |
|---|---|---|---|
| `CUSTOMER_MONTH` | 7,996 | 3.1s | **5:1** |
| `CUSTOMER_ITEM_MONTH` | 30,046 | 5.0s | 1.3:1 |

`CUSTOMER_MONTH` pays for itself plainly. `CUSTOMER_ITEM_MONTH` is close to a
copy of the event log with extra steps: a customer buys a given item in a given
month at most once or twice, so the bucket almost never has more than one line
in it. It is shipped because it is what was asked for and it does work, but it
should be judged on whether the composition screen's product dimension is worth
5 seconds of every sync — and if that screen is the only reader, a
`PRODUCT_MONTH` state (no customer in the key) would serve it at a fraction of
the rows.

### 9.3 The working cost more than the fold

The fold with every state registered took **36 seconds**, and most of it was
writing `StateTransition` rows — about 90,000 of them per build for the two
monthly states alone.

Transitions answer exactly one question: `why()`, the drill-down from a
decision card to the events behind its number. Only a state some *detector*
reads can ever be asked, and the monthly states feed screens. So every one of
those rows was written and would never be queried.

`Reducer.records_transitions` (default `True`, so no existing state changed)
lets a screen-only state opt out. The same fold now takes **15 seconds**.

### 9.4 Still outstanding

The reducers exist and are tested; **no screen reads them yet**. Moving the
seven screens is the remaining work, and §6's equality harness is the way to do
it — running old and new against one fixture and asserting identical output
caught two real bugs when the stock screen moved, and there is no reason to
believe these will be cleaner.
