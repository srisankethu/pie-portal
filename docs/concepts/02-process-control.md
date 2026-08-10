# Statistical process control and changepoint detection

**Verdict: rejected for this book, and not close.** Most of this category is not
merely low-value here — it is arithmetically inapplicable, and the reason is a
property of the data that will not change by tuning anything.

One idea in it (common-cause vs special-cause separation) is real and cheap and
still does not earn a place yet. The trigger that would change that is named at
the end, and it is not a statistical trigger.

This document exists so the category is not re-proposed from first principles
every time somebody reads "CUSUM detects small persistent shifts" and notices
that margin erosion is a small persistent shift. It is. The problem is elsewhere.

---

## 1. What the detector layer actually does today

Four proactive detectors in `backend/app/signals/`, wired in `engine.py`:

| Detector | Subject | Withholds when |
|---|---|---|
| `decline.py` | customer | < 6 months history, < 3 prior orders, or a non-positive baseline |
| `dormancy.py` | customer | < 4 orders, or a degenerate (same-day) median gap |
| `margin.py` | product | *either* cost basis is unreliable — `margin.py:54` |
| `cost_pass_through.py` | product | < 2 cost points, or the latest cost is unreliable |

Each is a pure function of `(snapshot, thresholds, as_of)`, byte-deterministic,
stamping `SignalThresholds.version` (`th_…`). They run after every sync
(`ingestion/jobs.py:541`).

Four properties matter for everything below.

**Windows are rolling 90-day, not calendar.** `aggregates.recent_window` is
`(as_of − 90, as_of]` and `prior_window` the 90 days before it, where `as_of` is
the organisation's last sale date — so the anchor drifts with trading.

**Withholding is genuine.** `margin.py` suppresses when either the recent or the
prior cost basis is flagged, because a placeholder prior cost would inflate the
baseline margin toward 100% and manufacture a large false deterioration. Heavy
suppression on incomplete SME data is a correct outcome, not a failure.

**Severity is calibrated to read right, not to an error rate.** `drop × 400` for
margin, `|change| × 100` for decline, `(cost_delta − max(price_change, 0)) × 300`
for pass-through. `margin.py:98` says so outright: the scale was chosen so a
26%→13% collapse does not band LOW.

**There is no stated false-alarm rate anywhere.** `test_signals_detectors.py`
is eighteen single-fixture behavioural assertions;
`test_decline_below_threshold_no_false_positive` is one case, not a rate. This
is the gap that actually matters, and §5 returns to it.

---

## 2. The arithmetic that decides the question

From this repo's own measurement
(`docs/business-state/TRADE_HISTORY_REDUCER_ANALYSIS.md:225`): **420 customers,
3,200 items, 40,000 sale lines over three years**, a ~15-item repeat basket,
Pareto-skewed.

Per subject, that is:

| Subject | Observations available | Derivation |
|---|---|---|
| Item — the margin detector's subject | **~12.5 lines total over 3 years** (~4/yr) | 40,000 ÷ 3,200 |
| Customer, monthly | **~19 of 36 months** | 7,996 customer-months ÷ 420 |
| Customer × item, monthly | **~5 over 3 years** | 30,046 ÷ (420 × 15) |

The same document states it in words: *"a customer buys a given item in a given
month at most once or twice."*

**This is what kills EWMA, CUSUM and ARL on the margin subject.** CUSUM's whole
advantage is detecting a small persistent shift in *fewer observations*. Tuned
to ARL₀ = 500, it detects a 1σ shift in roughly ARL₁ ≈ 10 observations. At ~4
observations per item per year that is **about 2.5 years in wall-clock time** —
strictly slower than the 90-day window it would replace.

The binding constraint is the arrival rate of observations. No amount of
statistical efficiency buys wall-clock time the data does not contain. A method
that is more efficient per observation is worth nothing when observations are
what you are short of.

Two further problems, either of which would be sufficient on its own:

**σ is unestimable, and what you could estimate is the wrong variance.** Twelve
observations will not support a control limit. Worse, a per-item margin series
pools different customers and different quantity bands, and `commercial/config.py:171`
already establishes that quantity is part of the identity of a price. The
dispersion you would measure is mostly mix, not process. Limits drawn from it
would be wide enough never to fire.

**Margin here is not a process with noise around a mean.**
`aggregates.cost_basis_asof` returns the latest cost record with `date ≤ as_of`,
so unit cost is a **step function that jumps when a bill lands**. Margin is a
windowed average price over that step. There is no in-control distribution to
attach an ARL to.

---

## 3. Item by item

### EWMA / CUSUM control charts — no

Slower than the incumbent (§2), no estimable σ, and no stationary in-control
model. EWMA additionally re-introduces a rolling exponential window, which
`commercial/insight/periods.py:9` deliberately rejected: *a distributor's
customers order against month-ends, so a rolling window slices order cycles in
half.* Adopting EWMA would silently reverse a decision this codebase made on
purpose.

### ARL as the tuning parameter instead of a threshold in pp — no

ARL₀ is a statement about a stationary process with known σ. Neither holds here
(§2). Setting ARL₀ = 500 would produce limits derived from assumptions the data
visibly violates — rigour in form, not in substance. A threshold in pp is at
least honest about being a judgement.

### Changepoint detection (PELT, binary segmentation) — no

The promise is answering *when* a change started, which a fixed period
comparison structurally cannot. True, and not worth it on 12–36 point series:
the penalty term is a tuning parameter that behaves exactly like the threshold
it replaces, and will either find nothing or find whatever the penalty says.

For a declining customer, the salesperson calls and asks. That answer is cheaper
and more accurate than a segmentation of noisy monthly totals.

Worth flagging explicitly: PELT **is** deterministic and would pass the
determinism constraint in the brief. The constraint will not catch this one.

### Common-cause vs special-cause separation — real, and still not yet

This is the only item with an obvious business case, and it deserves weighing on
its own rather than being dismissed with the rest.

**The gap is genuine.** `signals/decline.py:41` computes `change_pct` purely
against that customer's own baseline and never references book-wide movement. So
a month in which the whole book falls 20% — FY-end, a festival, one lost
principal — fires forty decline signals that all mean one thing, and the queue
cannot say so.

**But three things cut against acting on it now:**

1. **The ingredient already exists, and is better than a control chart.**
   `commercial/insight/flow.py` decomposes book-wide movement into
   NEW / LOST / GROWN / SHRUNK / RECOVERED, exhaustive and exactly reconciling.
   The owner can already see "everyone declined."
2. **The right fix is not SPC.** It is one number: book-wide revenue change over
   the same two windows, and this customer's change relative to it. No σ, no
   ARL, no new tuning parameter — roughly twenty lines in
   `signals/aggregates.py` and `signals/decline.py`.
3. **The consequence is small.** The salesperson calls the declining customer
   either way. Common-cause context changes the *framing* of that call and the
   *ordering* of the queue, not whether it happens.

If it is ever built, it must be **additive** — a metric alongside the existing
ones, informing severity. A gate that withholds the signal when the book fell
would be weakening a rule to reduce output, which CLAUDE.md §1 forbids in both
directions.

---

## 4. Two adjacent findings, ranked above everything in this category

Neither is SPC. Both address the complaint that motivates it more cheaply.

**The `signals/` windows contradict `insight/periods.py`.**
`aggregates.recent_window` / `prior_window` are rolling 90-day blocks off a
drifting `as_of`, while `insight/periods.py` fixes calendar months and states
why. That reasoning applies to `decline.py` and nothing in `signals/` honours
it. Aligning the two is cheaper, better-motivated, and reuses an argument this
codebase has already accepted.

**"Fires constantly" is a keying artefact, not a threshold artefact.**
`decisions/service.py:51` buckets `_decision_key` by ISO week, so a persistent
condition opens a fresh decision — and spends a fresh AI call — every week,
indefinitely. Nothing closes the previous one:
`repositories.py:893` records that detector-driven transitions
(RESOLVED / SUPERSEDED / EXPIRED) are out of Phase 1 scope. If alarm fatigue is
the real pain, that is where it is, and CUSUM would not touch it.

Neither is proposed here. They belong to whoever owns those areas.

---

## 5. What ships with this document, and why

`GET /api/v1/internal/detector-outcomes` (`backend/app/decisions/outcomes.py`),
owner-only, alongside the existing `/ai-metrics` whose shape it mirrors.

It is here because §1 ends on the finding that actually matters: **the detector
layer has no measured false-alarm rate.** Re-tuning `queue_margin_drop_pp` today
means tuning against a number nobody has. Everything in §3 was rejected partly
on the grounds that the problem it claims to solve has never been measured — so
leaving it unmeasured would make this document unfalsifiable.

It reports, per signal type over 7- and 30-day windows: signals emitted,
decisions opened, the outcome distribution, and the dismissal rate with a
two-sided band. It reads `Decision.status` and `Decision.human_action`, which
already persist. **No schema change, no migration, no new persistence, and no
change to what fires.**

**A dismissal rate over nothing is not zero.** The rate is `None` and the band
reads `NOT_REVIEWED` until enough decisions have actually been judged. A queue
nobody has worked produces no dismissals, and reporting that as a 0% false-alarm
rate is the benign default §1 forbids — the same shape as the send gate that
found no recorded snapshot and answered "nothing is wrong".

Two calls worth arguing with:

- **`VIEWED` does not count as judged.** Opening a card and leaving it is not a
  verdict, and counting it would let an unworked queue pass as reviewed.
- **The bands live in `config.py`, not `SignalThresholds`.** They judge the
  detectors; they do not feed them. Putting them in the thresholds hash would
  move `th_…` every time a *report* was tuned, making past signals look
  re-judged when nothing that produced them had changed.

Signals emitted and decisions raised are reported separately per type, because
detectors re-emit nightly while decisions are keyed weekly (§4) — the ratio is
what a persistent finding costs, and it is unreadable from either count alone.
On the existing fixtures, three runs over unchanged data emit 15 signal rows.

---

## 6. The trigger

**Nothing in §3 should be built yet.** The trigger that would change that is
operational, not statistical:

> Work the decision queue for one real quarter, then read
> `/api/v1/internal/detector-outcomes`. If a type bands `HIGH` — a large share
> of judged cards dismissed — the noise is real and there is finally a number to
> calibrate against.

Note what happens then: with a dismissal rate in hand, the fix is almost
certainly **to move `queue_margin_drop_pp`**, not to adopt a control chart. The
measurement is the prerequisite for the cheap fix, not a step toward CUSUM.

The one shape in which SPC could genuinely fit is **category-grain margin**
(CUTTING_TOOLS across all items — hundreds of lines a month, 36 real monthly
points, an estimable σ). That is where to look if this is ever revisited.
Whether a category-level margin drift is news to an owner who already has the
weather front and `flow.py` is a separate and doubtful question.

Everything else in this category needs the observation rate to change, and the
observation rate is a fact about how a distributor's customers buy.
