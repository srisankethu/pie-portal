# Action plan — what to build to make PIE venture-scale

Companion to `venture-thesis-review-2026-08.md`, which scored the thesis 5.2/10
and put the probability of a meaningful company at 20–25%. This document is the
buildable half of closing that gap, written to be handed to an implementing
Claude Code session.

> **Read this paragraph before the rest.** Code cannot make PIE venture-scale.
> Of the five next actions in the review, two — separating PIE from 4U Precision
> so a prospect is not handing data to a competitor, and getting three paying
> customers who are not you — are legal and commercial work that no repository
> change substitutes for. What code *can* do is make those two possible: produce
> the measurements that decide whether the thesis is true, and remove the
> constraint (pack velocity) that otherwise caps the business at whatever the
> founder can personally author. Everything below serves one of those two ends.
> Work that serves neither is listed in §5 as explicitly not to build.

---

## 1. The three gates

Each gate is a number, not a feature. Each has a kill criterion. Build in this
order, because a failed gate changes what the next one should be.

| Gate | The question | Pass | Fail means |
|---|---|---|---|
| **G1 — Pack velocity** | Can someone who is not the founder author a manufacturer pack to ≥95% parse rate? | **≤ 10 working days** | The business is capped at founder throughput. Stop the SaaS framing; go data/services deliberately (see §6) |
| **G2 — Real-RFQ accuracy** | On inbound customer text (not price files), what are precision, coverage, abstention and **wrong-confident** rate? | **≥90% precision at ≥70% coverage, <2% wrong-confident**, on ≥400 lines from ≥2 distributors | Either the engine is not better than a naive matcher — in which case the moat argument is dead — or it abstains so much the product is a human process |
| **G3 — New-tenant coverage** | What share of a *new customer's* master is resolvable at day 1 / 30 / 90, and what did moving it cost? | **≥60% union coverage by day 60, under 30 days of elapsed onboarding** | Time-to-value kills the ACV model; the entry product becomes remediation services |

G2 is the highest-value single thing in this document. It is simultaneously the
investor proof point, the customer proof ("run it on my last 200 RFQs"), and —
because whoever defines the measurement defines the category — the strategic
move in §8 of the review.

---

## 2. Work packages

Nine packages. Each states the repo, the venture reason, the acceptance criterion
as a number, and the invariants the implementing session must not break. Sizes
are calendar days for one engineer, and following this repository's own
convention, treat them as lower bounds.

### WP-1 — Pack authoring kit · `pie-parser` · 4–6 days

**Why:** G1. Today a pack is authored by reading a price file and writing YAML by
hand, with the test suite as the only scoreboard. That is why pack #2 is a
multi-week project, and pack velocity is the constraint that decides §7 of the
review.

**Build** — all under `tools/`, nothing under `engine/`:

- `tools/pack_scaffold.py` — emit a complete pack skeleton (manifest, routing
  ladder, one grammar, one pattern file with example stubs, empty lookups) for a
  named manufacturer, structurally valid and failing loudly rather than silently
  empty.
- `tools/pack_coverage.py` — given a price file and a pack, report per-family
  parse rate, unrouted rows, an unknown-token census ranked by frequency, and a
  "fix this next" list ordered by rows recovered per rule. **This is the
  scoreboard that turns pack authoring into a loop.**
- `tools/pack_lint.py` — structural validation, referential integrity, and the
  rule the suite already enforces (every pattern carries positive *and* negative
  examples), runnable standalone against a work-in-progress pack.

**Acceptance:**
1. `pack_coverage` on `packs/kennametal_widia` + the pinned corpus reproduces
   **6,717 / 6,717 and eleven families at 100%**.
2. Against a deliberately-damaged copy of that pack (delete one grammar slot),
   the "fix next" list ranks the damage first.
3. `make verify` green, with the real numbers reported.

**Invariants:** `engine/` gains no manufacturer literal and no new import. These
are `tools/` scripts and sit below the §4 size floor — do not add a Protocol over
them.

---

### WP-2 — Pack proposal from source documents · `pie-parser` · 6–10 days

**Why:** G1, and it is the difference between a 10-day pack and a 3-day one. A
manufacturer's nomenclature is published; reverse-engineering it by hand is the
expensive part, and reviewing a proposed decode is far cheaper than deriving one.

**Build** a new **top-level `authoring/` directory** — a build-time tool that
proposes pack data as reviewable diffs. It reads a price file plus the
manufacturer's published nomenclature document and emits candidate routing rules,
grammars and lookup rows as YAML/CSV for a human to accept, reject or edit.

**This is the part the implementing session must get exactly right:**

- `authoring/` may call a model. `engine/` may not, ever, and nothing under
  `authoring/` may be imported by `engine/`, `resolver/`, `identity/` or
  `equivalence/`. Add that as a check in `scripts/verify.sh` alongside the two
  existing §1 invariant checks, AST-parsed rather than grepped, so it is enforced
  rather than documented.
- The output is a **proposal**, never a runtime path. Verification is
  `pack_coverage` plus the test suite, both deterministic. A proposal that cannot
  be verified is rejected, not shipped with a confidence score.
- `authoring/README.md` states the above in the first paragraph, because the next
  reader's default assumption will be that PIE now uses AI.

**Acceptance — and this is the honest form of the test:** regenerate an
**existing** family from scratch (`grooving_threading`, 567 rows, twelve systems)
with the human only reviewing proposals, and reach **≥95% of the shipped pack's
parse rate**. Reproducing a known-good pack is a real measurement; grading a new
pack against itself is not.

---

### WP-3 — The RFQ benchmark · `pie-parser` · 5–8 days ⭐ highest value

**Why:** G2. Every accuracy figure in either repository today is measured against
*the manufacturer's own price file* — the clean case. No number exists for the
thing the product actually does. This package creates it.

**Build:**

- `eval/rfq_benchmark/` — a case schema: raw inbound text exactly as received,
  the product the human actually quoted, channel (email / WhatsApp / PDF / phone
  note), source distributor (anonymised), date, and an ambiguity class.
- `tools/eval_rfq.py` — computes, per arm and per slice:
  - **precision** — of the lines it answered, the share correct
  - **coverage** — the share answered rather than abstained
  - **abstention rate**
  - **wrong-confident rate** — answered, confident, and wrong. The one that
    matters, because it is the number the whole determinism argument rests on.
  - bootstrap confidence intervals, and a breakdown by channel and ambiguity class
- **Three arms, because "why isn't this just an LLM" is the question you will be
  asked in every meeting and you should have the number rather than an argument:**
  1. the deterministic engine
  2. a naive baseline — normalised exact match plus fuzzy — as the floor
  3. an LLM arm, run from `authoring/`-style quarantine or from the portal, never
     from `engine/`
- Report emission carries the ruleset checksum and is byte-reproducible, like
  every other output in this repository.

**Acceptance:** runs on **≥400 real lines from ≥2 distributors**, prints all four
metrics with intervals for all three arms, and reruns byte-identically.

**Note on the corpus:** the harness is code and can be built now; the cases are
customer data and are the gating input. Build the harness against a small
hand-written seed set, then fill it. Do not let a missing corpus delay the
harness, and do not let a synthetic corpus be reported as a result — this
repository's own rule is that absence of evidence is not a pass.

---

### WP-4 — Tenant master-health diagnostic · `pie-portal` · 6–9 days

**Why:** G3, and it is the **sellable entry product** identified in §6 of the
review. `docs/concepts/01-application-engineering.md` is this analysis run once,
by hand, on one master. Made repeatable and per-tenant, it is a two-week
engagement a distributor pays for, it is the mandatory first step of any PIE
deployment, and it gets you their data before they have committed to anything.

**Build:** ingest an item master (CSV upload or an existing `ingestion/erp/`
connector), run identity linking and *gated* geometry decode, and emit a **Master
Health Report**: identity / geometry / union coverage, value-weighted coverage,
manufacturer census, blank-field census, duplicate candidates, missing HSN and
cost, and a remediation worklist ranked by rows recovered per hour of work.

**Reuse, do not rebuild:** `identity.store.AuthoritativeIndex` for linking (the
exactness rules live in one place), the existing hygiene-watch logic for the
structural flags, and `ingestion/erp/` for the connector path. A second matcher
here is precisely the responsibility duplication §2 of the portal's CLAUDE.md
warns about.

**Invariants:**
- The report carries **structural flags only — never a cost or margin value**
  (§1). Stock value may be reported at *selling* price, as the existing analysis
  does, and must be labelled as such.
- **Gate geometry on full ISO slot fill.** The measured misroute rate for partial
  decodes is 11.6%; treating a family route as a fact is the exact "absence of
  evidence read as a pass" failure the portal's §1 names.
- Any persisted computed number carries a `thresholds_version`.

**Acceptance:**
1. Run against the SLS master and reproduce the published census: **15,028
   items, 9.4% identity, 17.1% geometry, 21.6% union, 23.5% value-weighted.**
2. Run against 4U and report **~0% with the reason stated** — "no pack covers
   this manufacturer" — never a bare zero, because "the pack does not cover this"
   and "nobody asked the pack" are different facts.
3. A migration in the same commit; `make verify` green including the
   empty-database run.

---

### WP-5 — Coverage curve per tenant · `pie-portal` · 2–3 days

**Why:** G3's metric, and investor proof point #5. Persist coverage at day 1 / 7 /
30 / 90 per tenant, plus the remediation effort logged against it, so
time-to-value is a number you can show rather than a claim.

**Acceptance:** a per-tenant series exists, is append-only (superseded, not
mutated), and the onboarding screen shows the curve. Small package — do not
over-build it.

---

### WP-6 — Close the loop: resolution outcomes · `pie-portal` · 5–8 days

**Why:** moat #1 and #2 in the review. Confirmed mappings and outcome data are the
only two assets that compound. `OrgMappingStore` already persists the first;
nothing yet records whether a proposed resolution or equivalent was *accepted*,
*quoted*, and *won*.

**Build:** append-only capture, per resolved line, of what was proposed, what the
human chose, whether it reached a quote, and whether that quote was won. Then two
reports: **equivalence acceptance rate** (share of proposals a domain expert
accepts) and **quoted→won by relationship** (EXACT / TECH / COMPAT). These are
investor proof points #4 and #9.

**Invariants — the important one:**
- An accepted suggestion must **never** become an asserted identity. A scored
  suggestion promoted to a confirmed mapping becomes an exact reference it never
  was, and the next "same as their 7781 but 12 mm" composes two tolerance bands
  into a wrong part with a defensible explanation attached.
  `tests/test_identity_confirmation_gate.py` pins this — extend it to cover the
  new outcome path rather than working around it.
- Acceptance rates may inform **ranking**. Ranking is policy, so it carries a
  version stamp, and a `rel` derived under one policy is never persisted as a
  relationship between products.

**Acceptance:** both reports render per tenant; the extended confirmation-gate
test fails if an outcome-derived record is ever offered as confirmable.

---

### WP-7 — Resolution as a versioned external API · `pie-portal` · 6–10 days

**Why:** this is what makes option (E) — infrastructure — testable rather than
aspirational. §8 of the review says a category requires resolution to become an
interface other systems call. You cannot find out whether an ERP or CPQ partner
would call it until there is something to call.

**Build:** `POST /api/v1/resolve` — text in; structured resolution out carrying
provenance, confidence, span, alternatives, and an explicit abstention when the
evidence is absent. API-key authenticated, per-tenant, rate-limited, versioned.
Publish the OpenAPI spec and a documentation page whose accuracy claims cite
WP-3's report rather than restating them.

**Invariants:** the router stays thin — it maps, it does not decide (§3). Role
scoping and the cost/margin withholding rules apply to API responses exactly as
they do to the UI; an API key is a recipient like any other.

**Acceptance:** an external caller holding only an API key can resolve a line,
receive a well-formed abstention on an unresolvable one, and confirm a mapping —
with the confirmation gate still refusing anything that is not the engine's own
single-candidate `NEEDS_REVIEW` proposal.

---

### WP-8 — Unquoted demand · `pie-portal` · 4–6 days · **deferred until G1–G3 pass**

**Why:** the second revenue line, and the best retention story available ("here is
the money you did not quote last month"). PIE is the only system that sees, at
part-level granularity, what customers asked for and never got quoted.

**Build:** capture every inbound line that never became a quote, with a reason
(abstained / no stock / no price / lost / no response), aggregated by manufacturer
and product class.

**Guardrail:** per-tenant by default. Any cross-tenant aggregate — which is the
version manufacturers would pay for — requires explicit opt-in recorded per
tenant, and belongs in `trust/` with the rest of the disclosure controls. Shipping
it without that is how you lose the customers who were nervous about you being a
distributor.

---

### WP-9 — Pack #2, on the clock, by someone who is not you · 10 days budgeted

**Why:** this *is* G1. It is not really an engineering package, it is the
experiment, and everything in WP-1 and WP-2 exists to make it cheap.

**Do:** pick YG1 — it is sitting inside 4U Precision, it is a manufacturer the
founder has not reverse-engineered, and it takes 4U's coverage from a measured 0%
to something. Hand it to a hired engineer, or to a Claude Code session working
only from `packs/kennametal_widia/docs/` and the WP-1 tooling. Start a clock.
Record calendar days to ≥95% parse rate, and record every question that had to be
escalated to the founder — that list is the real specification for WP-1's next
iteration.

**Acceptance:** a dated record of days elapsed, and a re-run of WP-4 against the
4U master showing coverage moving off zero.

---

## 3. Sequencing

| Weeks | Packages | Gate served |
|---|---|---|
| 1–2 | WP-1, WP-3 harness skeleton | G1, G2 |
| 3–4 | WP-2, WP-4 | G1, G3 |
| 5–6 | WP-9 (clock running), WP-5, WP-3 corpus fill | **G1 decides here** |
| 7–10 | WP-6, WP-7 | moat, option (E) |
| Later | WP-8 | only after G1–G3 pass |

WP-1 and WP-3 are first because everything else is either unblocked by them or
made pointless by their results.

---

## 4. What runs in parallel, and is not code

Listed because the plan is dishonest without them, and because they gate the same
outcomes the packages do.

1. **Separate PIE from 4U Precision.** Every prospect is 4U's competitor. Entity,
   data isolation, contractual commitment, sayable in the first meeting. Cheap
   now, nearly unfixable later. Do this before customer conversations, not after.
2. **Source the WP-3 corpus.** Three distributors, 200 historical RFQ lines each
   with the answer the human actually quoted, at least one outside cutting tools.
   This is the gating input to the highest-value package in the plan, and it is a
   phone call, not a commit.
3. **Sell one Master Health Report** (WP-4's output) at ₹2–5 lakh / $10–25k before
   the software is finished. It is the cheapest possible test of whether anyone in
   this segment pays for anything.
4. **Pick vertical two deliberately.** The review argues fluid power over
   fasteners (occupied) and over electrical (ETIM already solved the data). Decide
   it before WP-9, because it changes which manufacturer pack #2 should be.

---

## 5. What not to build

Each of these is attractive, and each moves none of the three gates.

- **Pricing optimisation.** Needs cross-customer transaction scale you will not
  have for years, against incumbents whose whole category tops out near $30M
  revenue per player.
- **More signal detectors, or more Commercial Decision Platform surface.** The
  platform is the strong axis of the codebase and the weak axis of the thesis.
  Adding to it widens the pitch, which is the current problem.
- **A conversational interface.** It demos well, it is what every competitor
  leads with, and it argues *against* your only differentiated claim.
- **More UI on existing screens.** Zero gate movement.
- **Geometry-class peer benchmarking, price-per-edge, and substitution via
  `equivalence/`.** Correctly deferred once already, on measurement rather than
  effort. The measurement has not changed.
- **A second manufacturer pack authored by the founder.** That is not the
  experiment. A pack the founder writes proves nothing that is in question.

---

## 6. Kill criteria, stated in advance

Written now, while they are cheap to write.

- **WP-9 exceeds 15 working days even with WP-1 and WP-2 in hand** → pack velocity
  is not solvable by tooling. Stop the vertical-SaaS framing. The honest company
  is then a cross-reference *data* business with maintenance headcount, or a
  services practice — both real, neither venture-scale, and both better entered
  deliberately in month six than discovered in year three.
- **WP-3 shows the deterministic engine within noise of the naive baseline on real
  RFQ text** → the moat argument does not survive. Reposition on workflow and
  distribution, which is a genuine business and a different pitch.
- **WP-3 shows coverage below 40% at acceptable precision** → the product is a
  human process with a good assistant. Price it as one.
- **Three Master Health Reports offered and none sold** → the segment does not
  buy. That is the cheapest possible finding and it is worth having early.

None of these are failure. Each is a measurement that redirects spending before it
compounds, which is the entire reason to write them down before running the
experiment.
