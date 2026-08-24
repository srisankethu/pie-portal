# 14 — Machine learning: which grain this book can be learned at

*What can be estimated from the data this platform holds, what may be deployed
given §1, and what would change a decision. Three different questions with three
different answers, and most proposals in this category die at a different one
than the proposer expects.*

**The verdict, before the reasoning.** This book is **data-rich at the customer
grain and data-poor at the item and customer×item grain**, and every technique
below is decided by which grain it needs. That split is not an accident of size
and growth does not close it — it is what specialist distribution *is*. The
questions the business most wants answered ("what price, for this customer, for
this item") live at the grain with the fewest observations, and the questions
with plenty of observations ("when will this customer next order") are ones a
salesperson can already answer by ringing them.

Three findings are worth the reading time:

1. **One classical method clears its sample-size floor by a factor of 150, and
   it is not one anybody proposes.** Survival analysis over inter-order
   intervals is estimable on this book *by a lower bound derived from counts the
   platform has already published* — no assumption required (§3.3). It needs no
   new dependency. It is also of modest value, for the reason
   `02-process-control.md` gives about a different method: the salesperson makes
   the call either way.
2. **The best target's labels are already in the ERP, unread.** Zoho holds ~290
   quotes for this book with statuses and dates; ~55–70 are won and *maintain
   themselves*, because a quote becomes `invoiced` for accounting reasons rather
   than because anyone recorded an outcome. Nothing ingests them (§5.1). The
   half that is genuinely missing is **why a quote was lost** — six on record —
   and that is the only part a person has to supply.
3. **A fitted model is a wider cost-disclosure channel than any rule, and the
   machinery that closed `filterCounts.MFLOOR` and `NEGATIVE_MARGIN` cannot
   close it** (§4). `boundary_refs` works because a rule's boundary can be
   *named*. A model's boundary is distributed across its weights and cannot be
   named, so the withholding mechanism has nothing to withhold on. This is the
   part of this document that is new work rather than arithmetic.

The measurement is `scripts/measure_learnability.py` — read-only, deterministic
(two runs produce byte-identical JSON), and it recomputes every count below
rather than quoting one:

```bash
cd backend && python3 ../scripts/measure_learnability.py
```

Run it before arguing with anything here. Where this document cites a figure
measured on a live book, the date and the entity are given; where it *derives* a
figure, the derivation is shown so it can be checked.

**If the question is "so when do I use the LLM instead", that is §9.** Most of
what follows is a series of reasons not to fit a model, and the complement of
each one is a job for a different tool — usually the BYOK provider that is
already wired, occasionally a `GROUP BY`. §9 is the routing rule, and it turns
out the safety ordering runs the opposite way from §4's.

---

## 1. Three questions, and which one each proposal dies at

"What machine learning can I use on my data" is three questions wearing one
sentence, and separating them is most of the work:

| | The question | What settles it |
|---|---|---|
| **Estimable?** | Are there enough observations of the thing to fit anything? | Arithmetic. §3. |
| **Deployable?** | May the output be computed and shown, under §1? | Governance. §4. |
| **Worth it?** | Would a better answer change what anybody does? | Judgement, informed by what already ships. §5. |

Almost every forecasting and segmentation proposal dies at **estimable**, and
the reasoning is already written down: `02-process-control.md` rejected control
charts and changepoint detection on the observation rate, and
`08-intermittent-demand.md` rejected Croston and SBA on the same property one
level down. Neither is re-argued here; both are re-*checked*, because a rejection
that rests on a measurement taken once expires.

The interesting proposals die at **worth it**, which is the least satisfying
place to die and the most common one for this platform specifically — the
deterministic layer is unusually complete, so a model's competition is not a
blank screen, it is `insight/payments.py` already publishing the percentile.

The dangerous proposals pass both and die at **deployable**. That is §4, and it
is where the actual risk in this category sits.

---

## 2. The learning surface: what carries a label

A model needs two things from a schema, and this one is generous with the first
and miserly with the second.

**Features are everywhere.** Seventy-five tables, every computed row stamped with
the threshold version that judged it, provenance on every imported record, and
`CustomerItemMetric` already materialising 30-odd derived quantities per
customer-item pair. As a feature store, this schema is better furnished than most
purpose-built ones.

**Labels are scarce, and nearly all of them require a human to type something.**

| Table | The label it carries | Who supplies it | Arrives |
|---|---|---|---|
| `payment_applications` | `paid_on − invoice_due_date` | **Nobody — it is arithmetic over synced dates** | Automatically |
| `quote_outcomes` | `status` | **Zoho's estimate status** — `invoiced`/`accepted` is a won label the business maintains for its own accounting (§5.1). Ingested since `a344f6d` | Automatically |
| `quote_outcomes` | `loss_reason`, `lost_to` | A salesperson — the ERP has a `declined` flag and no field for *why* | Only if asked |
| `inbound_line_dispositions` | `disposition` (QUOTED / NO_STOCK / …) | Whoever worked the enquiry | Only if asked |
| `decisions` | `status`, `human_action` | Whoever worked the queue | Only if worked |
| `quote_decisions` | `overridden`, `override_reason_code` | The pricer, at the moment of pricing | Only on an override |
| `approval_requests` | the decision, and the argument | A manager | Only on an escalation |
| `tender_results` | `won_value ÷ tendered_value` | Somebody reading a portal | Typed in |
| `outcome_snapshots` | realised delta vs frozen baseline | Recomputed after a horizon | Automatically, on accepted decisions only |

**Exactly one label in this schema arrives without being asked for**, and it is
payment lateness. Every other supervised target in this document is a *data
capture* problem first and a modelling problem a distant second. That is the
single most useful sentence in this analysis, and §7 is ordered by it.

Two absences shape the rest, and both are recorded elsewhere as deliberate:

- **No lost-sale or stockout event.** `08-intermittent-demand.md` §6 refuses the
  newsvendor fractile for exactly this reason — the underage cost is not
  measurable anywhere in this book. Any model of "demand we could not serve" has
  no target variable, only a proxy.
- **No randomisation, anywhere.** Every price in `sales_txns` was set by a person
  working under the policy in `commercial/config.py`. §5.G is about what that
  costs, and it is more than it sounds.

### 2.1 Read the ERP before asking anyone to type

The rule that decides where a fact should come from, and it is already this
codebase's rule rather than a new one:

> **The ERP owns the setting. The platform reads it, notices when reality has
> diverged from it, and suggests. It never sets one, never invents one, and never
> asks a human to type into the platform what their ERP already holds.**

Every local table that holds something ERP-shaped already justifies itself
against that rule, in its own docstring:

- `CustomerCreditLimit` — *"Zoho holds no credit limit on a contact in this book
  … **If a connector ever supplies one it belongs on `Customer` as a synced
  field**, and this stays the agreement."*
- `VendorPaymentTerm` — Zoho's dropdown cannot express "45 days from month end",
  so the agreement is held here and **Zoho's value is never overwritten**: both
  are shown, *"because the difference between them is the thing worth seeing"*.
- `VendorTarget` — *"Nothing in Zoho holds a target"*, so it is the one table
  typed rather than synced.
- `enquiry/` — a WhatsApp enquiry *"exists in no ERP"*, which is the whole reason
  that package exists.

So a local copy needs an ERP that genuinely cannot express the fact, and there
are exactly four documented cases. Anything else is a read.

**The test to apply to any proposal.** Does the ERP have a field for this?

- **Yes** → read it. The platform's contribution is the *suggestion*, never the
  value. Where the field is unset, report the coverage — do not fill it in.
- **No** → capture it, and say in the docstring why the ERP cannot hold it.

**Reorder points are the worked example, and the platform already does it
right.** `insight/stock.py` reads Zoho's `reorder_level`, exposes `below_reorder`
(guarded so a missing level is never read as zero), groups `BELOW_REORDER`, and
counts the rows with no level set as `no_policy`. It refuses to compute a reorder
point, correctly — `08-intermittent-demand.md` asked only that the *unset count*
be promoted to a headline, since 0 of 3,673 items across both entities has one.
Nothing more is owed here. That every one of them is unset is a fact the platform
should state; setting them is work that happens in Zoho.

**Two places the platform is not reading what the ERP holds:**

1. **Estimates.** ~290 quotes with statuses, dates, salesperson and the
   business's own `cf_quote_type` / `cf_pricing_type` custom fields — and no
   `list_estimates` in `ingestion/zoho_client.py`. §5.1.
2. **Vendor credits.** `11-procurement.md` records that `rg -ic "vendor.?credit"`
   across `backend/` returns nothing, and that is where rebates live — the same
   document argues a rebate treatment could make every per-line margin
   understated and unevenly so across principals.

Both were previously framed as things the business should start recording. They
are not. They are things the ERP is already recording and the platform has not
read, which is a connector method each, not a habit.

**The failure mode this rule prevents** is a backlog item that reads *"go and
configure your ERP"*. That is not a task on the platform's backlog. It is an
output of one — and §7 had it the wrong way round until this section was written.

---

## 3. The binding constraint: observations per subject

### 3.1 The measured shape

From this repository's own measurement
(`docs/business-state/TRADE_HISTORY_REDUCER_ANALYSIS.md:225`): **420 customers,
3,200 items, 40,000 sale lines over three years**, customers drawing on a ~15-item
repeat basket, Pareto-skewed. Folded: **7,996 customer-months** and **30,046
customer-item-months**.

Per subject:

| Grain | Observations | Derivation |
|---|---|---|
| Book, monthly | **36** | three years |
| Customer, monthly | **19 of 36** | 7,996 ÷ 420 |
| Item, all time | **~12.5 lines** (~4/yr) | 40,000 ÷ 3,200 |
| Customer × item, monthly | **~5 over three years** | 30,046 ÷ (420 × 15) |

The same document states the consequence in words: *"a customer buys a given item
in a given month at most once or twice."*

### 3.2 What each method needs, against what is there

The floor used throughout is **ten events per estimated parameter** — the
long-standing bar for a logistic fit, below which coefficients are unstable and
their intervals are not to be believed. It is a rule of thumb and modern work
argues it is optimistic, which is a reason to treat clearing it as *necessary,
not sufficient*. Judged against a deliberately small ten-feature model, that is
**100 minority-class events**; a survival fit with five covariates needs 50.

| Method | Unit of observation | Available | Needed | |
|---|---|---|---|---|
| Inter-order survival | a completed gap | **≥ 7,576** (§3.3) | 50 events, 25 subjects | **clears, ×150** |
| Book-grain monthly series | a month | 36 | ~50 for an ARIMA worth trusting | marginal |
| Category-grain margin | a month | 36 | as above | marginal |
| Quote win/loss | a decided quote | *capture-limited* (§5.1) | 100 losses | **reachable** |
| Payment lateness | a settled invoice | thousands | 100 | **clears** |
| Item-grain demand | a sale occasion | ~12.5 | ≥ 6 intervals, on most SKUs | **fails** |
| Customer×item price response | a repeat purchase | ~5 | ≥ 30 for any curve | **fails badly** |

### 3.3 The one lower bound that needs no assumption

Survival analysis is the only method here whose sample size can be established
from published counts with no modelling assumption at all, and the derivation is
short enough to check by eye:

> A customer who traded in *m* distinct calendar months has at least *m* distinct
> order dates — at least one per month — and therefore at least *m − 1* completed
> intervals between orders. Summed over the book, the number of completed
> inter-order intervals is at least
>
>     Σ (mᶜ − 1)  =  7,996 − 420  =  **7,576**
>
> over **420 subjects**, against a floor of 50 events and 25 subjects.

It is a *lower* bound because a customer ordering twice in a month contributes
intervals this count discards. The true figure is higher; the script measures it
exactly, using `aggregates.order_dates` so that a census saying a customer has
eight orders cannot disagree with the screen that says the same.

### 3.4 Why this cuts the way it does

The three rows that fail in §3.2 are the three that need the item or
customer×item grain, and **that is the grain every commercially interesting
question lives at**. What price for this customer for this item; will this
customer take this item; how much of this item will be wanted. The grain with
thousands of observations answers "when will this customer be back", which the
business already knows how to find out: it rings them.

This is not a stage the book grows out of. `08-intermittent-demand.md` §2 puts it
plainly for its own method — *"distribution of specialist tooling is
one-order-per-SKU trade; a book ten times this size has ten times as many
one-order SKUs."* Scaling multiplies subjects, not observations per subject. The
only thing that changes the ratio is selling fewer, deeper lines, which is a
strategy decision and not a data one.

---

## 4. The governance finding: a model cannot have `boundary_refs`

This section is the new work in this document. Everything above is arithmetic
over figures the repository already holds; this is an argument about what the §1
machinery can and cannot catch, and the answer is worse than it first looks.

### 4.1 A fitted model is policy, not interpretation

The first question anybody reading `CLAUDE.md` §1 will ask is whether a model
violates **"AI never computes a number."** The honest answer is *no, not on the
letter, and the letter is not the interesting part.*

The invariant is enforced as an import rule — `commercial/`, `signals/`,
`ingestion/`, `state/`, `enquiry/` and `attribution/` must not import `ai/`, and
`ai/` must not import `commercial/` or `attribution/`, checked transitively by
`tests/decision_platform/test_layer_boundaries.py`. Its *purpose*, stated in the
same paragraph, is that an output be auditable and reproducible.

A fitted model with frozen weights satisfies that purpose in a way a language
model does not: it is deterministic at inference, byte-reproducible, and its
parameters hash exactly as `CommercialThresholds.version` does. So the right
reading is:

> **A fitted model is a threshold set with more parameters.** It belongs beside
> `CommercialThresholds` in `commercial/`, not in `ai/`. `ai/` is where facts are
> *phrased*; a model computes a number, and numbers come from the deterministic
> layer or they come from nowhere.

That placement is not a technicality. It settles that a model gets a version
hash, gets stamped on every row it writes, gets replayed by `backtest.py`-shaped
tooling, and is subject to every rule in §1 that a threshold is subject to.

### 4.2 And it is a threshold set whose boundary cannot be written down

Here is the problem. `CLAUDE.md` §1 records two incidents in this family:
`filterCounts.MFLOOR`, where a *count* of below-floor lines was walked to the
exact floor price in twenty probes; and the return of the same defect as a *rule
code*, where `quote-intelligence/assess` takes `proposed_price` from the caller,
so sweeping the price finds the value at which `NEGATIVE_MARGIN` changes its
answer — and that value *is* purchase cost, with no policy multiplier in the
comparison to obscure it. Two hundred lines fit in one request, so it was two
round trips, not twenty probes.

The fix generalised the lesson: **the fact that a named rule fired is a
predicate, and a predicate a caller can walk is the number it tests against.** So
each rule now carries `boundary_refs` — the values that place its boundary — and
`quote_service.project` withholds any rule naming something the recipient may not
see, substituting one fixed `APPROVAL_REQUIRED`. `NEGATIVE_MARGIN` carries the
special member `COST_BASIS`, because its boundary is purchase cost itself and no
`PriceReference` code describes where it sits.

That mechanism has one prerequisite: **a rule's boundary can be named.** It is
one comparison against one quantity, so a frozenset of strings describes it
exactly.

A model has no such object. Its decision boundary is a surface in feature space,
distributed across every weight, and there is no honest `boundary_refs` to write.
Worse, the disclosure is *better* than a rule's, from an attacker's point of
view, in three specific ways:

- **A rule's response is a step; a model's is a gradient.** Walking a price
  against `NEGATIVE_MARGIN` yields one bit per probe, so the boundary comes out
  by bisection — one probe per bit of precision, and twenty of them recovered the
  floor in the MFLOOR incident. A model returning a probability yields a *real
  number* per probe, so a finite difference in the price coordinate estimates the
  model's sensitivity to the cost term directly. Two evaluations, not twenty.
- **Withholding the score does not close it.** Rounding, banding or thresholding
  a model output reduces it to the rule case — which the repository has already
  established is walkable. Bands leak more slowly, not less certainly.
- **Training-set membership leaks too.** A model fitted on margin-labelled rows
  encodes the margin distribution of the training population even when margin is
  not an input at inference, and per-customer-item predictions are a channel for
  reconstructing it. This has no analogue in the rule case at all.

### 4.3 The rule that follows

**Feature scope must equal reader scope.** A model whose training features or
labels include a quantity the recipient may not see must never be queried by that
recipient — not with the score withheld, not banded, not thresholded, not behind
a "requires approval" boolean. There is no projection step that makes it safe,
because unlike `quote_service.project` there is no field to omit.

Where both roles need an answer, that means **two models, not one model with a
projection**: a management model fitted on everything, and a salesperson model
fitted only on quantities a salesperson may see. This is the same shape the
quote-builder skill already uses when it emits an OWNER workbook and an
OPERATIONS workbook built from a record type with no cost field — the guarantee
comes from the object not carrying the quantity, not from a serialiser
remembering to drop it.

Note what this costs the most attractive target in this document. §5.1
recommends quote win/loss, and the natural training table for it is
`quote_decisions` — which carries `unit_cost`, `cogs`, `gross_profit` and
`margin`, all RESTRICTED. **The best target's natural feature table is a
restricted table**, so a salesperson-facing win model has to be fitted on a
deliberately impoverished view. That is a real cost and it should be paid, not
argued around.

### 4.4 Three more conditions, none of them optional

**The version must dereference.** `09-policy-replay.md` names as its real
blocker that a `ci_…` threshold hash cannot be dereferenced — you cannot ask the
system what policy a stamp refers to. A `model_version` with the same defect is
strictly worse, because unlike a threshold set the weights cannot be re-derived
from a config file. Before any model ships, the hash must resolve to a stored
manifest: training window, feature list, row count, hyperparameters, code commit,
and the `ci_…` in force when the labels were made.

**The model must abstain.** Every detector in `signals/` withholds rather than
asserting on thin evidence, and `radar.py` refuses to present confidence as a
probability precisely because `SUFFICIENT` means "enough evidence to act on", not
"this will convert". A model that always returns a number is the benign default
`CLAUDE.md` §1 forbids, wearing a probability. Any model here carries the same
`Sufficiency` object and returns nothing when it is not met — and the tell to
watch for in review is a `predict()` with no branch that declines.

**A score is not evidence.** `identity/matchers.py` states the standard this
codebase holds matching to: a strategy returns *the value it matched on*, not a
score, because "GSTIN 29ABCDE1234F1Z5" settles an argument and "confidence 0.94"
starts one. The same file leaves the door open — *"a future fuzzy or AI-assisted
strategy fits the same shape — it just has to say what it saw."* A model that
cannot say what it saw does not fit the shape, and that constraint is what
scopes §5.E to candidate generation.

---

## 5. Technique by technique

### A. Supervised learning over transactional rows

**5.1 Quote win/loss — the best target, and the one to build labels for first.**

The label exists (`QuoteOutcome.status`), the reason vocabulary exists
(`QuoteLossReason`, five members chosen to be short enough that people use them),
and `set_outcome` already refuses a LOST transition without a reason rather than
defaulting to a benign one. The features are the frozen `QuoteDecision` snapshot,
which is append-only and therefore free of the lookahead that ruins most
retrospective training sets — the row *is* what was known on the day.

**The labels already exist, in Zoho, unread.** An earlier draft of this section
derived the arrival rate from the invoice count and a hypothetical win rate. That
was modelling a quantity that can simply be looked up, and it got the answer
wrong. Measured against the live SLS Engineers book on 2026-08-24 through the
Books API:

| | |
|---|---|
| Quotes raised (`SLS/QTN-01` … `-290`, from 2026-04-02) | **~290**, ~62/month |
| Status `invoiced` — **won**, carrying `accepted_date` | **~55–70** |
| Status `declined` — **lost**, carrying `declined_date` | **6** |
| Draft / sent / viewed / expired / pending — **outcome unrecorded** | **~215** |

Zoho estimates were **not ingested at all** when this was written — there was no
`list_estimates` in `ingestion/zoho_client.py`, so none of it reached
`QuoteOutcome`, which was fed only by hand. That was the finding, and it is
fixed: `a344f6d` reads them. The paragraph stays in the past tense on purpose, the
way `CLAUDE.md`'s own grid rule does — the measurement is what made the case,
and deleting it would leave the recommendation with nothing behind it.

Three things follow, and they are not what the earlier draft said:

- **Won is nearly free, and self-maintaining.** A quote becomes `invoiced`
  because the business needs the invoice, not because anybody was asked to record
  an outcome. That is ~55–70 labels that maintain themselves and cost no habit.
- **Lost is the missing half, and it is missing badly**: 6 recorded against ~215
  quotes whose fate nobody wrote down. So the minority class today is **6**,
  against a floor of 100 — NOT_ESTIMABLE, and the binding constraint is loss
  recording, not quote volume.
- **An expired quote is not a loss.** It is the §1 trap in a new place: reading
  `expired` as LOST would manufacture ~200 labels out of silence, and silence is
  what the queue was never worked, the customer never answered, and the deal was
  lost to a competitor — three different facts. Unrecorded must map to *unknown*
  and stay out of the training set.

So the target is capture-limited, as the earlier draft said — but the capture is
**half done already in the ERP**, and the platform's job is to read it rather than
to ask anyone to re-type it (§2.1).

Two cautions before anybody fits anything:

- **The first deliverable is a contingency table, not a model.** Losses split by
  `loss_reason` answer the owner's question directly: *losing at 8% below my
  quote is a pricing problem; losing on delivery is a stock problem.* That is a
  `GROUP BY`, it needs ~30 losses rather than ~100, and a model that ranks
  pre-send risk is worth building only once the table stops being surprising.
- **A win model that includes price is largely learning the policy.** Price here
  is assigned by `commercial/` from cost and customer, so it is a function of the
  same confounders any elasticity estimate would condition on. See §5.G.

Verdict: **read the estimates, ask only for the reason, then the table, then
reconsider.** And a salesperson-facing version must be fitted under §4.3.

**5.2 Payment lateness — estimable, and already answered better.**

Every settled invoice is a labelled example arriving automatically; the census
counts thousands. It is the only target in this document with no capture problem.

It is also the clearest case of §1's "does this already exist" rule in this
category. `insight/payments.py` already computes **median, spread, trend and
percentiles with an evidence floor**, for customers and suppliers in one module,
on the stated grounds that a sibling `payables.py` would be four hundred lines of
the same statistics waiting to disagree. `insight/credit.py` already sets the
outstanding balance against the recorded limit and distinguishes absent from zero
from over.

A classifier would add *conditional* prediction — this invoice, this size, this
month. Nobody in this business works invoice-by-invoice: the collections queue
works accounts. So the increment is a better-ordered account list, over a
deterministic ordering that already exists and already carries its evidence.

Verdict: **estimable and not worth it.** Named here because it is the target
everybody proposes second and the one whose competition is strongest.

**5.3 Margin or erosion prediction — no, twice over.** The subject is the item
grain (~12.5 observations), and `02-process-control.md` §2 additionally shows
margin here is not a process with noise around a mean: `cost_basis_asof` returns
the latest cost record at or before the date, so unit cost is a *step function*
that jumps when a bill lands. There is no in-control distribution. And the label
is margin, so §4.3 confines any such model to management scope permanently.

**5.4 Credit and exposure risk — marginal, and mis-shaped.** The event a credit
model wants is a default, and defaults in a 420-customer B2B book are rare enough
that the minority class fails the floor for years. What the business actually
needs is exposure against a limit, which `insight/credit.py` computes exactly and
which is arithmetic.

**5.5 Churn classification — no; superseded.** A binary "will churn" over a fixed
horizon discards the timing information that is this book's one abundance, and
`dormancy.py` already answers the question against each customer's own rhythm.
See 5.6 for the version that uses the data properly.

### B. Time to event

**5.6 Survival analysis over inter-order intervals — the one that clears.**

`aggregates.cadence_of` computes a customer's median inter-order gap and flags
them overdue at `median × multiplier`. That is a point estimate with no
dispersion: a customer whose gaps are 28, 30, 31 days and one whose gaps are 5,
30, 90 days have the same median and are not equally late at day 45.

A Kaplan–Meier estimate over the ≥7,576 completed intervals (§3.3) replaces
"overdue by ratio" with "overdue relative to the distribution", and a Cox model
with a handful of covariates — segment, basket breadth, tenure, principal mix —
would say which customers' rhythms differ and by how much. The censoring is
handled properly rather than discarded, which matters because the wait since a
customer's *last* order is the observation the current method silently treats as
ended.

Three things make this the most defensible build in the document: the sample
size is a lower bound rather than an estimate; the output is a *timing*
distribution, which carries no cost or margin and is therefore deployable to a
salesperson without §4.3 biting at all; and **it needs no new dependency** — a
Kaplan–Meier curve is a cumulative product over sorted gaps, about thirty lines
of standard library.

And one thing caps its value, honestly: `02-process-control.md` §3 already made
the argument against a more elaborate answer to this question — *"the salesperson
calls the declining customer either way"*. A calibrated hazard changes queue
*ordering*, not whether the call happens. So it inherits that document's trigger
rather than getting its own: **work the queue for a quarter, read
`/api/v1/internal/detector-outcomes`, and build this only if dormancy's dismissal
rate says the ordering is wrong.**

### C. Forecasting

**5.7 Croston and SBA — no.** Settled in `08-intermittent-demand.md` §2 and
re-checked by the census: at 4U, 107 of 171 SKUs that sold at all sold on a
single day, 81% on two days or fewer, and 3 of 171 have enough occasions to fit
anything. `commercial/config.py`'s own `min_transactions_strong: 6` would refuse
to display the result for 156 of them.

The census applies **two** bars here, and the second was added after the first
version of the script got it wrong: enough SKUs in absolute terms *and* enough of
the traded catalogue. Twenty-five forecastable SKUs out of three thousand reads
ESTIMABLE on an absolute count while 99% of the book remains unforecastable —
a benign default hiding in a verdict field.

**5.8 ARIMA, ETS, Prophet at item grain — no.** Same series, same objection.

**5.9 Gradient-boosted lag features at category grain — no, and the blocker is
downstream.** `02-process-control.md` §6 identifies category-grain margin as the
one shape where a monthly method could fit — hundreds of lines a month, 36 real
points. Grant that a forecast is estimable there. **Nothing consumes it.** Zero of
3,673 items across both entities has a reorder level set; `BELOW_REORDER` is a
group that can never contain a row. A forecast feeding no decision is a chart.

Fix the ten reorder levels first (§7); then ask again whether anything needs
predicting.

**5.10 Hierarchical or grouped forecasting, reconciliation — no.** It presupposes
the base forecasts of 5.7–5.9.

### D. Unsupervised

**5.11 k-means / RFM segmentation — no, and for a reproducibility reason rather
than a statistical one.** `insight/cohorts.py` bands customers by *quantiles
computed across the union of both periods*, deliberately, so that a customer
cannot "move up" while spending less. Quantile edges are reproducible and
versionable. k-means centroids move on every refit, so a customer's segment
changes with no business change behind it — which is precisely what
`CommercialThresholds.version` exists to make impossible.

**5.12 Association rules and market basket — no; a better version already
ships.** `insight/adoption.py` computes directional co-occurrence over line-of-
business pairs and explains why it had to: *lift is literally the same figure both
ways round, so it says a pair is related and never which way round the
relationship runs.* It is "a count of rows, not a model", auditable, and it
answers the question a symmetric rule cannot. At the item grain, support is
hopeless anyway — ~5 observations per customer-item pair.

**5.13 Matrix factorisation and ALS recommenders — no, and the reason is a
category error rather than a sample size.** A factorisation of the customer-item
matrix yields item vectors whose dot products are an *item-item similarity*, and
that similarity means "bought by similar customers". It does not mean
"interchangeable". Those are different claims about a carbide insert, and this
product has a screen where confusing them has physical consequences: a suggested
item that reaches a quotation as a substitute is a wrong part, a machine down and
a rejected delivery.

The confusion is not hypothetical here, because the surrounding vocabulary invites
it. The quote path already speaks in `rel`, `supplyCode` and TECH/COMPAT/POSSIBLE
— *equivalence* words — and `CLAUDE.md` §1 is explicit that a derived `rel` must
never be fed back in as the input to another resolution. A co-purchase similarity
rendered into that vocabulary is precisely the second hop that rule forbids,
arriving through a different door.

Sample size finishes it off: ~5 observations per customer-item pair, and most
pairs seen once.

**5.14 Graph neural networks — no, and the two candidate graphs fail for
different reasons.** Worth separating, because collapsing them overstates the
case against one and understates it against the other.

*Over an item-attribute similarity graph*, the objection is
`10-knowledge-representation.md`'s and it is decisive. Message passing **is**
transitive closure — that is the operation, not a side effect that could be
engineered away — and that document's finding is that technical equivalence must
never compose: `A ≈ B` and `B ≈ C` within tolerance does not give `A ≈ C`, and
with pie-parser's shipped ±50% band two hops put a 0.4 mm and a 0.8 mm corner
radius in one class via 0.6. Each hop is individually defensible and would survive
review; the composite is a different insert. pie-parser is built so it cannot
compose them — every comparison's left operand is the request — and a GNN over
item attributes is a machine for undoing exactly that property.

*Over the customer-item bipartite graph*, the equivalence objection does **not**
apply directly: propagation there means "customers like this bought that", which
is 5.13's claim and fails 5.13's way. What kills it instead is the graph.
~6,300 distinct customer-item edges (420 customers × a ~15-item basket) in a
420 × 3,200 possible space is **under 0.5% dense**, and most of those edges carry
a single purchase — there is no neighbourhood structure to propagate through.

One arithmetic trap, flagged because it is easy to fall into and would flatter the
method: the 30,046 figure in §3.1 is customer-item *months*, not distinct pairs.
Using it as an edge count overstates the density by nearly fivefold.

**5.15 Isolation forests and autoencoders for data hygiene — no.** The real
defects in this book are found by comparing two columns: seven SLS items held at
a cost above their selling price, ₹7,37,814 at cost, one held at ₹50,872 and
priced at ₹37,812. An outlier score would rank those alongside legitimate oddities
and give a reviewer nothing to act on. `signals/quality.py`'s contract is that an
anomaly is *flagged with its evidence, never auto-corrected and never used to
fabricate a fact* — a score satisfies none of that.

**5.16 Clustering for item categorisation — no.** `Product.category` is stored
raw and interpreted at read time by `commercial/categories.py` because the mapping
from a catalogue's words to a line of the business **is policy, and is versioned**.
A cluster label is not policy and cannot be re-read under a corrected map.

### E. Text and retrieval — where the actual opportunity is

**5.17 Embedding-based candidate generation for RFQ → SKU — yes, scoped to
recall.** This is the one genuine ML build this document endorses, and the
scoping is the whole recommendation.

The problem is real: an enquiry arrives as a customer's own words, and
`pie-parser`'s grammar decodes what it can and abstains otherwise.
`13-confidence-and-input-completeness.md` establishes — by suppressing a single
column across 6,717 paired rows — that the abstention is **correct**: 100% of the
confidence gap is attributable to the absent field and 0% to the engine's
judgement, measured field-by-field rather than inferred. The engine is not failing
on messy input. It is declining, rightly.

So an embedding model must never *answer* where the grammar declines — that is
the benign default in its purest form. What it can do is **propose candidates**
the deterministic strategies then adjudicate. Pretrained, no fitting, no labels
required, and the existing pipeline is unchanged: `identity/matchers.py` runs
strongest-first and each strategy still returns the value it matched on; a
retrieved candidate that no strategy can confirm is simply not a match.

Two guardrails are already in the codebase and must stay: `store._identity_candidate`
offers a confirmable code only for the engine's own single-candidate
`NEEDS_REVIEW` proposal — an exact catalogue hit downgraded for namespace
safety, never a scored suggestion — and `routers.quote._confirm_identity` refuses
anything else. **A retrieved neighbour is never a confirmable identity.** It is a
shortlist for a person.

Value: recall on the 78.4% of the master that reaches no catalogue today, and on
enquiry text the grammar cannot parse. Cost: an embedding dependency and an index.
Prerequisite: an evaluation corpus, which is 5.21.

**5.18 Learned NER for part numbers, quantities and grades — not yet.** The
grammar is a better instrument on this domain than a sequence model fitted on a
small corpus would be, and 13 measured that it reads descriptions correctly. This
becomes interesting only against a labelled corpus of *real customer text*, which
does not exist yet.

**5.19 Fine-tuning a language model — no.** `ai/` is already correct in shape:
in-context prompting, a validated output contract (`ai/contract.py`), a
deterministic fallback so the decision surfaces regardless, and telemetry per
call. Fine-tuning would trade auditability for fluency the product does not need,
and needs thousands of examples nobody has. The one thing worth doing in `ai/` is
what is already done: withhold up front on `INSUFFICIENT` without spending a
provider call.

**5.20 Document extraction from PDFs — yes, and buy rather than build.** Bills,
POs, tenders and award notices arrive as documents; `IngestedDocument` exists.
This is solved by pretrained models behind an API, verified against the
deterministic reconciliations the operations skills already run. Training an
extractor here would be a research project competing with a commodity.

**5.21 Enquiry routing and coverage classification — the corpus is the
deliverable, and it is empty.** `InboundLine` is the one table designed for this:
raw customer text with **no normalisation at capture**, append-only, no
deduplication, explicitly documented as the corpus an RFQ benchmark reads and the
only coverage denominator that does not condition on success.
`InboundLineDisposition` supplies the label, superseded rather than mutated.

It is also brand new — added in `a7inbound`, third from the head of a migration
history that ends at `b8lease`. **There is nothing in it.** So the answer for
every text model is the same: *start collecting, and protect the rule that
nothing is cleaned on the way in* — a `strip()` at the capture boundary looks
like hygiene and destroys the exact property that makes the corpus worth having.

### F. Ranking and sequence

**5.22 Learning to rank the decision queue — not yet, and the seam is already
cut for it.** `Decision` carries `priority_deterministic_base` and
`priority_ai_adjustment` as separate columns, so the score is already split
between a computed base and a bounded adjustment. An LTR model would replace the
*base* — which puts it squarely under §4: versioned, dereferenceable, abstaining.

The labels are `status` and `human_action`, and `03-decision-science.md` records
that they were only recently worth anything: the clients used four of seven
actions and collapsed two pairs of intents, so the platform was *"accumulating
data that reads like adoption data and is not."* Fixed, but the clock on usable
labels started at that fix.

Trigger: the same one `02-process-control.md` ships — work the queue for a real
quarter and read the dismissal rate. If a signal type bands HIGH, the first fix
is almost certainly to move `queue_margin_drop_pp`, not to fit a ranker.

**5.23 Sequence models over purchase histories (next-basket, RNN, transformer) —
no.** ~5 observations per customer-item pair. There is no sequence.

### G. Causal inference and experimentation — the second opportunity

**5.24 Price elasticity, double ML, causal forests — no, and this is the deepest
"no" in the document.** Every price in this book was set by a person applying
`commercial/config.py` as a function of cost, customer relationship and quantity
band. Those are precisely the confounders an elasticity estimate would condition
on, and there is no instrument and no discontinuity to exploit — the approval gate
is a *threshold on the treatment*, which is tempting as a regression discontinuity
until you notice that crossing it changes who approves the price, not just the
price.

A "price optimiser" fitted on this data would learn the pricing policy and report
it back as customer behaviour. It would validate beautifully.

**5.25 Randomised evaluation of policy changes — yes, and it costs least of
anything here.** The platform is already unusually well set up for this and
nobody has used it:

- `CommercialThresholds.version` is a content hash of the policy, stamped on every
  computed row. **That is already an assignment variable.**
- `commercial/backtest.py` already replays an approval-floor change over frozen
  `QuoteDecision` rows, reusing the real evaluator rather than restating its
  rules — `09-policy-replay.md` grades this path "exactly sound, zero code".
- `OutcomeSnapshot` already freezes what was true when a recommendation was
  accepted, and `outcome_tracker.evaluate` already recomputes the delta after a
  horizon with an explicit `PENDING` / `REALISED` / `UNKNOWN` status.

The missing piece is assignment: randomising a threshold variant across
organizations, users or customers, rather than switching it for everyone. That
turns every future policy question — the erosion sensitivity, the approval floor,
the CAF payment-terms variant — from an argument into a measurement, and it
answers the questions a price model would have answered badly.

**One live blocker, named in `09-policy-replay.md` and worth repeating because it
gates this too: a `ci_…` hash cannot be dereferenced.** An experiment whose arms
are identified by a hash nobody can resolve to a policy is not analysable. Fix
that first; it is also §4.4's prerequisite, so one change unblocks both.

**5.26 Contextual bandits for price or discount — no, on three independent
grounds.** Exploration is real money and real customer relationships, not
impressions. The approval gate would block the exploratory arm, so the arms are
not comparable. And `04-mechanism-design.md` establishes that CAF's **linearity is
load-bearing** — there is no threshold anywhere inside it, which is what makes
order-splitting worth exactly zero. An adaptively-learned weight reintroduces a
threshold, and the first thing a desk discovers is how to split an order across it.

**5.27 Uplift modelling for outreach — no.** It needs a randomised treatment.
Build 5.25 and this becomes possible; without it, "customers we called grew" is a
statement about who gets called.

### H. Infrastructure

**5.28 A feature store — no.** `CustomerItemMetric` is already the materialised
projection, rebuildable from source by `python -m app.commercial.backfill`, and
carrying `thresholds_version` and `computed_at` so any row says what produced it.

**5.29 Adding numpy / scikit-learn — not until something passes §3, §4 and the
value test.** The backend today is FastAPI, SQLAlchemy, pydantic, cryptography,
pyyaml and openpyxl; there is no numerical stack at all, `requirements-dev.txt`
pins tooling exactly because an unpinned upgrade once held the gate red across
eight consecutive merges to `main` — skipping the whole backend suite each time —
and `docs/hosting-free-tier.md` exists because deployment weight is a real
constraint here. When a dependency is finally justified it belongs in the
worker process — `messaging/queue.py` and `worker.py` already exist — and never in
the request path.

Worth noting against that cost: **the one method in this document that clears its
floor needs no dependency at all** (§5.6).

---

## 6. What sounds advanced but is wrong here

Condensed, so the category is not re-proposed from first principles:

| | Why not |
|---|---|
| Deep learning on tabular rows | 40,000 rows, 3,200 items. Gradient boosting would beat it and a `GROUP BY` beats them both at this grain. |
| A price optimiser | Learns the pricing policy and reports it as customer behaviour (§5.24). Validates beautifully. |
| Recommender / GNN cross-sell | Confuses "bought together" with "interchangeable", in a product whose vocabulary is already equivalence — and over item attributes it performs the closure `10-knowledge-representation.md` exists to prevent (§5.13–5.14). |
| Forecast-driven stocking | Nothing consumes a forecast: 0 of 3,673 reorder levels are set (§5.9). |
| Anomaly detection for data quality | The real defects are found by comparing two columns, and a score is not evidence (§5.15). |
| Fine-tuned LLM | Trades the auditability `ai/contract.py` provides for fluency nobody asked for. |
| AutoML over the schema | Would find the label leakage — `gross_profit` predicts `margin` — and report it as accuracy. |
| A model behind the quote screen | §4. There is no projection that makes it safe. |
| "More data will fix it" | Scaling multiplies subjects, not observations per subject (§3.4). |

---

## 7. What would actually move this, in order

Three kinds of work, and **the first version of this section mixed them**, which
is how it ended up ranking "go and set reorder levels in Zoho" as the platform's
top item. §2.1 is the correction. They are separated here because they have
different owners and only the first is a backlog:

### 7a. Platform work — the actual backlog

1. ~~**Ingest Zoho estimates.**~~ **SHIPPED in `a344f6d`.** `list_estimates` maps
   `invoiced`/`accepted` → WON and `declined` → LOST, each requiring its decision
   date, and **everything else to unrecorded, never to LOST** — enforced by a
   `classify_outcome` whose signature has no expiry-date parameter, so the
   tempting mistake is unreachable rather than merely guarded. Synced facts live
   in their own derived table; the human row keeps the loss reason and points at
   a document, so the sync's write set and a person's are disjoint *tables*
   rather than disjoint columns. It brought the business's own `cf_quote_type` /
   `cf_pricing_type` taxonomy with it, as predicted.
2. ~~**Suggest, on the quotes with no recorded outcome.**~~ **SHIPPED in `a344f6d`.**
   `commercial/insight/unrecorded.py` ranks them by value at stake and age past
   expiry; a quote with no expiry has an *unanswerable* age rather than a zero,
   and one with no total is counted but never valued at zero. What remains is the
   capture screen that actually asks *why* — see the open ends below.
3. **Promote the unset-reorder-level count to a headline.** The reading is built
   (`below_reorder`, `BELOW_REORDER`, `no_policy`); what is missing is that a
   group which can never contain a row currently says nothing about why.
   `08-intermittent-demand.md` asked for exactly this and no more.
4. **Start the enquiry corpus, and defend the no-normalisation rule.**
   `InboundLine` is empty and every text technique waits on it. The ERP cannot
   hold this — that is the package's stated reason for existing — so it is
   genuine capture rather than an unread field. The rule that protects it is one
   line long and the change that breaks it looks like hygiene (§5.21).
5. **Make `ci_…` dereferenceable.** One fix unblocks model versioning (§4.4) and
   randomised policy evaluation (§5.25).
6. **Read vendor credits** (§2.1). Not machine learning at all, and it sits
   upstream of every margin number the platform computes — see
   `11-procurement.md` for what a rebate treatment does to per-line margin.
7. **Randomise a policy variant.** The threshold hash is already an assignment
   variable and `backtest.py` is already the analysis (§5.25).
8. **The embedding shortlist for RFQ resolution** (§5.17), scoped to recall,
   once item 4 has given it something to be evaluated against.
9. **The Kaplan–Meier over inter-order intervals** (§5.6) — last, and only on
   §8's trigger.

**Open ends left by 1 and 2, recorded so they are not rediscovered:**

- **The capture screen.** `unrecorded.py` ranks the pile; nothing yet asks the
  question. That screen is what turns six recorded losses into the §5.1
  contingency table, and it is the next thing worth building.
- **`AmbiguousQuoteDocument` has no router mapping**, because no endpoint reaches
  the ERP-only path yet. Whoever lands the capture screen maps it — 409, as
  `MissingLossReason` and `InvalidTransition` already are.
- **The outcome pointer carries no `(connector, connection_id)` qualifier.** An
  ambiguous reference is refused rather than guessed, so nothing is destroyed —
  but in a two-book org whose ERP reuses quote ids, *neither* person can record
  an outcome. Accepted deliberately: nothing calling it today can say which book
  it means, so the column would be NULL from every current caller. It belongs
  with the capture screen that can supply it, and `_sole_erp_quote`'s docstring
  says so. Not reachable on this book — Zoho estimate ids are globally unique.

### 7b. Suggestions the platform should make — not tasks it should carry

These are things that happen *in Zoho*, prompted by a screen. The platform's job
ends at saying so clearly, with the evidence attached:

- **Set reorder levels on the top ten stock lines.** 0 of 3,673 are set.
- **Fix the eleven dead-stock prices.** 76% of genuinely dead value cannot be
  quoted at all — seven lines at ₹0/₹1, two at exactly cost.
- **Mark declined quotes declined.** Six of ~290 carry the flag the ERP already
  provides.

All three are worth more to the business than most of 7a. None of them is
engineering, and putting them on an engineering list was the category error §2.1
names.

### 7c. Habits — using the platform, not feeding it

- **Work the decision queue for one real quarter**, then read
  `/api/v1/internal/detector-outcomes`. This is the trigger for §5.6 and §5.22
  and there is no substitute for it.
- **Run `measure_learnability.py` quarterly.** One command, and it is what makes
  every verdict in §5 falsifiable rather than a claim that quietly expires.

---

## 8. The trigger

Nothing in §5 marked "not yet" should be built on today's evidence. Each has a
condition, and all of them are checkable rather than arguable:

| Technique | Build it when |
|---|---|
| Quote win/loss ranker (§5.1) | Estimates are ingested (§7a.1, done), so the gate is now ≥100 losses with reasons **and** a loss-reason table that has stopped being surprising. Six losses are on record, and the capture screen does not exist yet — quarters away, not weeks |
| Inter-order survival (§5.6) | `/api/v1/internal/detector-outcomes` bands dormancy HIGH over a worked quarter |
| Queue LTR (§5.22) | Same trigger, **and** moving `queue_margin_drop_pp` did not fix it |
| Enquiry text models (§5.18, §5.21) | `inbound_lines` holds a few thousand rows with live dispositions |
| Embedding shortlist (§5.17) | The enquiry corpus exists (§7a.4), so recall can be measured rather than asserted |
| Anything at item grain (§5.7–5.10) | Never, absent a change in what this business sells |
| Any model exposed to a salesperson | §4.3 is satisfied by construction, not by a projection |

The last row is a gate rather than a trigger, and it does not expire.

---

## 9. ML or LLM: routing a job to the right tool

The complement of "do not fit a model here" is "use something else", and the
something else is already running. Two LLM paths ship today, both through the
per-organization BYOK key (`ai/byok.py`): the decision queue's reading of a
signal (`decisions/service.py` → `ai/interpret.py`), and the Quote Builder's
reading of a prose enquiry (`routers/quote.py` → `ai/reading.py`). Neither
produces a number, and that is not a coincidence.

### 9.1 Ask what the output *is*, not how hard the problem is

| The output is | Tool | Where it lives |
|---|---|---|
| A number a decision rests on | Arithmetic — or, if it must be learned, a fitted model under §4 | `commercial/` |
| A choice from a closed set, with a reason a human can check | Deterministic rules and matchers | `commercial/`, `identity/` |
| Language, or structure recovered from language | An LLM | `ai/` |
| A ranking over things already scored | ML once labels exist; today a bounded adjustment (±`AI_PRIORITY_ADJUST_BOUND`, 20) | `decisions/` |

Difficulty is the wrong axis, and it is the one that misroutes both jobs at
once: it sends people to a model for pricing — where §3 says the evidence is not
there — and to a regular expression for a WhatsApp message, where a pretrained
model is free. The Quote Builder's intake was that regular expression until
`ai/reading.py` landed.

### 9.2 The rule is already written in the tree

`ai/reading.py` states it, and nothing here improves on the phrasing: the model
*"does segmentation and normalisation of language, which is the thing it is
genuinely good at, and the identity of the tool stays with the engine that was
built to decide it."*

It returns a code and a quantity per line **and nothing else** — it does not match
an item, choose a supply option, or price anything. pie-parser then resolves that
code exactly as it resolves typed input, against the same catalogue and the same
score bands. In one sentence:

> **The LLM may say what was asked for. It may not say what it is, or what it
> costs.**

### 9.3 Thin data argues *for* the LLM, which is the counterintuitive part

"LLM for language, ML for numbers" is right, but for a shallower reason than the
one that binds here. §3 establishes that supervised learning is starved at this
book's commercially interesting grain — ~5 observations per customer-item pair.
An LLM needs **zero** examples for the jobs it is good at, because it arrives
already trained. So the routing follows the evidence:

- Data thick, output a number → a model can learn something. §5.6 is the only
  place in this document where that is true.
- Data thin, input is language → the LLM, precisely *because* the book has no
  training set to offer. It brought its own.
- Output is a number → arithmetic, however much data there is.

The instinct this inverts is a common one: reach for a model on the sparse
pricing problem, where it cannot work, and for a regex on the enquiry text, where
the pretrained model costs a fraction of a rupee.

### 9.4 The economics, and why BYOK changes them

The two tools have opposite cost shapes:

| | Fixed cost | Marginal cost | Who pays |
|---|---|---|---|
| Fitted model | High — build, version, the §4.4 manifest, monitoring, re-fitting | ~zero | The platform, once |
| LLM call | ~zero | Per call | **The tenant**, via their own key |

BYOK is what makes the second row scale-free for the platform: the key is the
organization's, so the bill is theirs, and adding tenants adds no inference cost
here. It does *not* make it free for the owner, which is why the discipline is
already built rather than left to judgement — `decisions/preflight.py` prices a
run before anything is sent, `service.py` caches on a context hash so unchanged
context never re-infers, `interpret.py` withholds up front on `INSUFFICIENT`
without spending a call, and `telemetry.py` prices each call from token counts as
backend arithmetic — never a figure the model supplies.

The crossover between the two rows is a volume question, and at this book's
volume it is not close. **At ~210 invoices a month and a few hundred enquiries,
almost nothing is repetitive enough to amortise a fitted model against an API
call.** That is a second, independent argument landing where §3 already landed,
which is worth noticing: the sample-size case and the cost case agree.

### 9.5 The safety ordering is not the one most people would guess

§4 argues a fitted model is a *worse* cost-disclosure channel than a rule. The
LLM is **better** than either — provided the bundle is redacted, which it is by
construction:

- `context/assembler.py` removes RESTRICTED facts before assembly — absent, not
  masked — so the model never sees cost or margin at all.
- `context/bundle.py` exposes the allowed number set, and `ai/contract.py`
  rejects any figure in user-facing text that does not trace back to it.
- A fitted model, by contrast, *encodes* whatever it was trained on and cannot
  un-see it.

So for anything adjacent to cost or margin the ordering is
**arithmetic > redacted LLM > fitted model**, and a proposal that reaches for a
model to avoid "sending data to a provider" has usually got the risk backwards.

One caveat keeps that honest, and `context/quote_bundle.py` already records it:
`unknowns` is free text *the module writes*, not a fact passing the `data_class`
gate, and `_suspicious_cost` once stated a comparison between a restricted value
and one the reader is shown. The gate protects facts. Prose written beside them
has to be written for the recipient.

### 9.6 What the LLM must never be handed here

- **A number to compute, choose or move**, beyond the clamped
  `priority_adjustment`. §1, and `contract.py` enforces it.
- **The final say on identity.** `10-knowledge-representation.md`, and the
  confirmation gate that admits only the engine's own single-candidate proposal.
- **An unredacted bundle.** Redaction is upstream of the call, never a
  post-filter on the answer.
- **A job with no deterministic floor.** Every path degrades to `fallback.py`
  rendering the signal's own metrics; a feature whose failure mode is a blank
  screen is not ready for a provider.
- **Raw records.** `prompt.py` sends exactly the curated bundle JSON, and treats
  an instruction inside a data value as content to be ignored.
- **One tenant's text pooled with another's.** `InboundLine` holds a customer's
  own words about their project and volumes; no consent primitive exists anywhere
  in this codebase, so the cross-tenant aggregate cannot be built yet.

### 9.7 Where the next call of each kind should go

**Next LLM (BYOK) jobs, in order:**

1. **Document extraction** — bill, PO and tender PDFs into structured lines.
   Nothing in the backend does this today: `IngestedDocument` is a Zoho *resume
   cursor*, not a document store, so the operations skills currently do it
   outside the platform. Note the architectural constraint before building it —
   `ingestion/` must not import `ai/` (§1), so the call belongs at a router or
   service seam that hands **already-structured output** to ingestion. That is
   exactly the shape `routers/quote.py` uses for `ai/reading.py`, and it is the
   pattern to copy rather than the rule to argue with.
2. **Widen `ai/reading.py` to the other inbound channels.** It already reads
   prose into quotable lines on one path; `InboundLine` is about to carry the
   same text from five.
3. **Loss-reason free text into the closed `QuoteLossReason` vocabulary**, as a
   suggestion a human confirms. Cheap, bounded, reversible — and it supplies the
   one half of a quote outcome the ERP has no field for, which is what §7a.2
   otherwise has to ask a person for one quote at a time.

**Next fitted model:** §5.6, and only on §8's trigger. There is no second one.

---

## 10. What this document is not

It is not a claim that machine learning is useless in distribution. It is a
claim about **this book, at this size, given what is already computed
deterministically** — three qualifiers, of which only the second changes on its
own, and §3.4 argues it does not change the ratio that matters.

It is also not a licence to skip the capability search. The strongest recurring
finding in §5 is not statistical: it is that `commercial/insight/` has already
answered, deterministically and with its evidence attached, a large share of what
a model would be fitted to answer. Before proposing anything here, run the §2
search from `CLAUDE.md` and read the module docstring. Several of them argue
against the model directly, and they argue well.
