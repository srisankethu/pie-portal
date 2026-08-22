# PIE — go-to-market plan

A working plan, not a deck appendix. Decisions, not options. Unknowns are marked
`[ASSUMPTION]` or `[TO VALIDATE]` with the evidence that closes them.

PIE runs today on live Zoho Books data across SLS Engineers, 4U Precision and UPS.
No revenue, no design partners, no external pipeline.

One fact shapes everything below: the commercial ladder is **already implemented**.
`entitlements.py` ships three plans — `FREE` ("Quote Desk"), `INTELLIGENCE`
("Commercial Intelligence") and `PLATFORM` (adds multi-company) — with a once-ever
30-day Intelligence trial keyed to the *connected books*, so it cannot be reset by
signing up again. Slide 12's ladder describes code, not intent.

---

## 1. ICP definition — the screen

**SKU count alone is not the ICP.** Plenty of large catalogues quote fine. The pain
appears where four pressures *converge*: high SKU complexity, frequent quoting,
technical product selection, and margin pressure. Any one is common and means nothing;
together they produce a quote desk that is a real operational bottleneck. Screen for
the convergence, then check the preconditions.

| Criterion | Threshold | Why |
|---|---|---|
| SKU count | **> 5,000 active items** | A good salesperson holds a smaller catalogue in memory |
| Product complexity | Selection is **technical** — geometry, grade, rating, fit — not a catalogue lookup | Where selection is trivial, PIE degrades to a search box |
| Quote volume | **> 150 quoted lines/month** `[ASSUMPTION]` | Below this the time saved never reaches the price |
| Margin sensitivity | Gross margin **under ~25%**, discount authority delegated | Where margin is fat, nobody counts basis points |
| Sales team | **3–30 quoting people** | Under 3 the owner does it himself; over 30 there is an internal team |
| Time lost | Salespeople spend **substantial hours** finding products, alternatives and prices | The symptom the other four produce. If nobody is losing hours, the convergence is not there |
| ERP | One of the **seven connectors** | Anything else is a build before a sale |

How *encoded* the part numbers are is not a qualifier — it sets how much of the
grammar-decode path contributes. Where codes are closer to arbitrary, identity
resolution and equivalence still work. The capability degrades across the ladder
rather than failing.

**Disqualifiers — walk away, do not discount.**

- **Single-manufacturer authorised dealers.** Their principal supplies clean data and
  forbids cross-brand equivalence, which is most of PIE's value.
- **Commodity-only books** — high SKU count but no technical selection; price is the
  whole conversation. This is about the *book*, not the category: a consumables
  distributor with a technical range qualifies, one selling on price alone does not.
- **Configuration, not identification.** If the hard part is which options to combine,
  that is CPQ. PIE answers *which product they mean*, not which build.
- **Bespoke or in-house ERP.** Integration cost exceeds the contract.
- **No cost data in the ERP.** Every margin feature is inert, and the platform
  correctly returns UNKNOWN rather than guessing.
- **"Digital transformation" buyers with no quote-desk pain.** They buy one pilot and
  never a second year.

---

## 2. Segment sequencing

**Ring 1 — Indian industrial cutting-tool distributors.** Same category as the
working deployment, so the existing pack applies on day one; the founder is inside
the buyer community; sales can be face-to-face.

**Ring 2 — more manufacturers, then English-speaking geographies.** Sandvik, Iscar
and Mitsubishi packs raise coverage for every customer at once, and their ISO-coded
insert families reuse the engine's shared ISO 1832 layer rather than needing new
grammar. Then the US and UK, where the same packs apply unchanged — a CNMG code means
the same in Ohio as in Hyderabad — and six non-Zoho connectors already exist.

**Ring 3 — the category ladder**: Bearings → Electrical → MRO → Automation →
Fasteners → Industrial consumables. Ordered by how closely each one's convergence
resembles cutting tools. Bearings is nearest and arguably fits better than the
beachhead, since cross-brand interchange is a bearings distributor's daily work.

**The decision: geography before category.** A *sequencing* call, not a narrowing of
the ladder above. Geography reuses the pack asset intact and needs a connector already
built; a new category needs new packs plus a standards decoder added to the engine,
against unfamiliar physics. The ladder is the ceiling; geography is the next rung to
monetise. Categories first spends the most engineering to prove the least.

---

## 3. The internal deployment — proof, and its limits

**What it proves.** The platform survives a real, messy book rather than a demo
fixture: 15,028 items, ₹32.7M of stock at selling price, three legal entities, live
data. Its users are not the author and would abandon it if it were slower than the
spreadsheet.

**What it does not prove.** Every requirement was validated by asking the founder. No
evidence anyone will *pay*, none that the workflow survives a sales culture the founder
does not control, none that onboarding works for someone who does not already know the
answer. All three entities are on Zoho in one category, so neither the seven-connector
nor the cross-category claim has been tested.

**What the first external deployment must show**, in order:

1. A **non-founder** completes onboarding, with the founder answering questions but
   not driving.
2. Their **real desk** runs through PIE for 30 days — not a sample.
3. The **attribution report** returns a non-null ATTRIBUTED figure against the 90-day
   baseline.
4. They ask about **price** unprompted.

Until item 4, treat PIE as an internal tool that might generalise.

---

## 4. Entry motion

**Founder-led for the first ten customers.** No hire, no agency.

**Who.** The **owner or managing director** under ~₹100 crore turnover; the **sales
head** above that. Not IT — routing a margin conversation through IT converts it into
a security review. The quote-desk lead is the champion, but holds no budget.

**Trigger events** — one must be true:

- A manufacturer price increase just landed and nobody knows which quotes are now
  below cost.
- Their best technical salesperson resigned, retired, or is about to.
- They lost a tender on a technicality — wrong grade, wrong equivalent.
- They are adding a second brand and the catalogue has doubled.

**The first conversation is a diagnostic, not a demo.** Three questions: *"How long
from enquiry to quote?"* · *"Who is the one person who knows the grades, and what
happens the week they are away?"* · *"When a customer asks for a competitor's part
number, what do you do?"* The answers become the pilot baseline. Demo only if all
three land — and demo their catalogue, never a fixture.

---

## 5. The pilot

**Scope.** One entity, one quote desk, their live ERP connection, their top two
manufacturers by SKU count. **Duration:** 30 days — the trial length already in the
product, not a number invented here.

**What they hand over.** Read-only ERP credentials, their item master, and price files
for the two manufacturers. No data leaves their instance.

**What PIE must show.** Coverage of their stocked book, in the manufacturers PIE has
packs for, above **40%** — anchored on the 42.5% the reference deployment reaches on
its own stocked Kennametal items, so the bar is one a pilot can actually clear.
**Measure it from their item master before agreeing the pilot, not during it**: one
export, and the cheapest disqualifier available. Then every quoted line resolved or
honestly abstained, and a non-null ATTRIBUTED figure.

**Pass/fail, agreed in writing before it starts.** A non-null **ATTRIBUTED** figure
over the window — money that demonstrably moved *and* an intervention that caused it.
Not POTENTIAL, not REALIZED; those report separately and never sum. And **no
time-saving claim**: the platform counts approvals turned round and lines priced but
refuses to put a rupee figure on them, because the business holds no hourly rate. Do
not promise a payback number the product is built not to fabricate.

**Decided: the software is free for the 30 days, the onboarding is paid** — a fixed
fee covering pack work for their two manufacturers, `[ASSUMPTION: ₹1.5–3L]`. The free
trial tests whether they *use* it; a fee tests whether they *value* it, and prices the
one component consuming scarce founder time. A wholly free pilot produces enthusiasm
and no willingness-to-pay signal — the single thing this company most needs to learn.

---

## 6. Onboarding cost — the question that decides what this company is

Two halves, behaving completely differently.

**Half one: the ERP connection. Already productised.** The form renders from the
connector's own spec, credentials are stored encrypted, the sync is connector-blind.
Hours, not weeks, no founder involvement. This half does not scale with customers.

**Half two: the nomenclature pack. Founder work today.** A pack is a routing ladder,
typed grammars, pattern registries with self-testing examples, lookup CSVs, repair
tables and validators — 24 YAML and 14 CSV files for the first one, covering eleven
families and 6,717 rows. This half decides whether PIE is a product or a consultancy.

**What is reusable, and it is more than it looks.** The engine is reused entirely and
by construction: it holds zero manufacturer literals, enforced by an AST check in the
build gate. The ISO 1832 layer is engine-level standards data, so **any**
manufacturer's ISO-coded insert families decode with no new grammar — roughly 1,561 of
6,717 rows in the existing pack, about 23%. Normalisation, equivalence and validators
are shared. What is genuinely new per manufacturer is the proprietary notation: the
drill and endmill series, the descriptive families.

**The honest position: n = 1.** One pack exists; the cost of the second is
**unmeasured**, and every expansion claim in the deck rests on it. First pack
`[ASSUMPTION: 6–10 weeks]`; second `[ASSUMPTION: 3–5 weeks]` on the reuse argument
above.

**Where it stops being founder work.** When a pack is built by someone who did not
write the engine, working only from the extension documentation and the self-testing
pattern examples. The test is concrete: **pack three is built by a hire and the corpus
gate goes green without founder intervention.** Until then headcount scales with
customers and this is a consultancy. That is the gating milestone for any claim of
software margins.

---

## 7. Pricing discovery

No price has ever been quoted to anyone. The ladder exists in code; the numbers on it
do not.

**The question for the first ten conversations**, after the diagnostic and before any
number is named: *"If the quote desk turned round twice as fast and never sent a line
below your floor, what would that be worth per month — and what budget does it come out
of?"* It forces a number and a budget line at once, and the budget line matters more: a
figure with no home is a compliment, not a forecast.

Then anchor — name a number, watch the reaction, record it. Note there is **no
checkout**: `PlanChangeRequest` records a request and grants nothing; plans move only
by operator command. Correct for founder-led sales now, a bottleneck at roughly
customer fifteen `[ASSUMPTION]`.

---

## 8. Expansion within an account

The rungs are the plan tiers, so expansion is an entitlement change, not a project.

**Quote Desk → Commercial Intelligence.** Trigger: the trial ends and the decision
queue has been *acted on* — signals accepted or rejected rather than ignored. An
untouched queue means the pain was not real; re-run the diagnostic instead of chasing
the upgrade. Signed by the owner or sales head.

**Commercial Intelligence → Platform.** Trigger: a second set of books. Multi-company
is deliberately withheld from the trial, so this rung sells itself the moment a group
structure exists — which in Indian distribution is most of them.

Margin management and the watchers are detectors already running: enablement, not
construction.

---

## 9. First 90 days

**Days 1–15 — target list and coverage.** Screen 40 distributors against §1; expect
12–15 to clear. Start the **Sandvik pack** and instrument the hours — the highest-value
work of the quarter, and it answers §6. Fix the `README.md` line contradicting sheet 13.

**Days 16–45 — twenty diagnostics.** No demos unless the three questions land. Record
every answer to the §7 pricing question verbatim. Target: 8 demos, 3 pilot candidates.

**Days 46–75 — two paid pilots running.** Written pass/fail before either starts; the
customer drives onboarding so the §3 test is real. Finish the Sandvik pack and publish
its measured effort against the first.

**Days 76–90 — convert and decide.** Review attribution reports with each pilot owner
and convert at a named price. Then one decision on evidence: does pack two show a
falling cost curve? If yes, hire the pack engineer. If no, the moat argument needs
rebuilding before a raise.

---

## 10. Leading indicators

1. **Pack-build hours, pack over pack.** The most important number in the company. A
   flat curve means consultancy; a falling one means software.
2. **Catalogue coverage per prospect**, stocked items, measured as the 21.6% internal
   census was. Predicts whether a pilot can pass before it starts.
3. **Decision-queue action rate** during a trial — signals acted on over signals
   raised. Ignored output means no renewal, visible weeks before the date.
4. **Diagnostic-to-pilot conversion**, with the reason for every loss. Below roughly
   1 in 6 `[ASSUMPTION]`, the ICP screen is wrong rather than the pitch.

Explicitly **not** tracked: parse rate (already 100%; it says nothing about the
market), test counts, and anything valuing time in rupees.

---

## What this plan does not know

| Gap | Evidence that closes it |
|---|---|
| Willingness to pay | Ten diagnostics with the §7 question |
| Cost of pack two | Build it, record the hours |
| §1 quote-volume and margin thresholds | Validate against 5 real distributors |
| Onboarding fee | Quote it three times, watch the reaction |
| Founder's software-sales capability | `[FOUNDER BACKGROUND — TO SUPPLY]` — unresolved |
