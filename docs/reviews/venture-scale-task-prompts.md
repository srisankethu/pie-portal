# Task list and session prompts

Execution companion to `venture-scale-action-plan.md` and
`venture-thesis-rebuttal-response.md`. Twelve code tasks and five non-code ones,
sequenced by what they unblock. Every prompt below is self-contained — paste one
into a fresh Claude Code session and it should not need this file for context.

Three things were folded together after the rebuttal exchange, and the merge is
the most useful thing in this document: **the forward capture of inbound lines
(T3) is simultaneously the denominator-C measurement, the RFQ benchmark corpus,
and the unquoted-demand data product.** Build it once, early, and three
downstream tasks stop being blocked on data.

---

## The list

| # | Task | Type | Repo | Serves | Depends on | Size |
|---|---|---|---|---|---|---|
| **T1** | Quoted-line coverage (denominator B) | measurement | portal | Coverage question, lower bound | — | 1 d |
| **T2** | Confidence experiment: one product, two inputs | measurement | portal | Kills or confirms the "0.00" reading | — | 1 d |
| **T3** | **Inbound line capture instrument** | code | portal | Denominator C · benchmark corpus · unquoted demand | — | 3–4 d |
| **T4** | Pack authoring kit | code | parser | **G1** pack velocity | — | 4–6 d |
| **T5** | RFQ benchmark harness | code | parser | **G2** accuracy | T3 for real cases | 5–8 d |
| **T6** | Pack proposal, quarantined in `authoring/` | code | parser | **G1** | T4 | 6–10 d |
| **T7** | Tenant master-health diagnostic | code | portal | **G3** · entry product | — | 6–9 d |
| **T8** | **Pack #2 on the clock, by not-you** | experiment | parser | **G1 decides here** | T4, T6 | ≤10 d budget |
| **T9** | Coverage curve per tenant | code | portal | G3 metric | T7 | 2–3 d |
| **T10** | Resolution outcome loop | code | portal | Moat: acceptance + won/lost | T3 | 5–8 d |
| **T11** | Resolution as a versioned API | code | portal | Tests the infrastructure path | — | 6–10 d |
| **T12** | Unquoted-demand reporting | code | portal | Second revenue line | T3 | 2–3 d |

Non-code, running in parallel, and gating more than the code does:

| # | Task | Why it is here |
|---|---|---|
| **N1** | Separate PIE from 4U Precision — entity, data isolation, contract | Every prospect is 4U's competitor. Precedes N4 and N5, cheap now, unfixable later |
| **N2** | Source 200 historical RFQ lines each from 3 distributors, ≥1 outside cutting tools | Gating input to T5. A phone call, not a commit |
| **N3** | Sell one Master Health Report at ₹2–5 lakh / $10–25k before T7 ships | Cheapest test of whether this segment buys anything |
| **N4** | One week of regulated-vertical discovery (rail / aerospace / nuclear MRO) | Repriced from zero to unknown in the rebuttal response. Asymmetric upside |
| **N5** | One named VAR conversation (P21 / Epicor partner) | The implementation owner that does not otherwise exist. After N1 |

**Order of operations:** T1, T2, T3 this week. T4 and T5 alongside. T6 and T7
next. T8 decides the thesis in weeks five to six. T9–T12 only after the gates
report.

---

## T1 — Quoted-line coverage (denominator B)

> Repo: pie-portal, branch `claude/measure-quoted-line-coverage`. Read `CLAUDE.md`
> §1 and `docs/concepts/01-application-engineering.md` first — this extends that
> measurement with a different denominator.
>
> The published coverage figure (21.6% union over 15,028 master items) uses the
> item master as its denominator. An item master is a graveyard: dead stock,
> one-time buys, migration artifacts, 6,146 rows with no manufacturer recorded.
> Nobody quotes from it. Measure coverage over what was actually traded instead.
>
> Build a one-off analysis script (`scripts/` — this is a measurement, not a
> product feature): for every invoice and estimate line in the last 12 months,
> resolve the product through `identity.store.AuthoritativeIndex` and report
> coverage three ways — **by distinct product, frequency-weighted by line count,
> and value-weighted at selling price.** Break it down by entity.
>
> **The report must state its own limitation in its opening paragraph**, because
> otherwise this number will be misread later: *a line became a quoted line
> because somebody could identify it, so this denominator conditions on the
> outcome the product exists to change, and systematically excludes lines nobody
> could identify. It is a lower bound, not the answer.* The answer needs T3.
>
> Constraints: reuse `AuthoritativeIndex` — do not hand-roll a matcher. Value at
> **selling price only**; no cost, no margin, anywhere in the output. Gate any
> geometry decode on full ISO slot fill, never on a family route. Write the result
> into `docs/concepts/` as a follow-up section with the same evidentiary
> discipline as the existing document: if a figure is underpowered, record it as
> UNKNOWN rather than as favourable.
>
> Run `make verify` and report the real numbers.

---

## T2 — Confidence experiment: one product, two inputs

> Repo: pie-portal, branch `claude/confidence-input-completeness`. Read `CLAUDE.md`
> §1 and `docs/concepts/01-application-engineering.md` §3.
>
> That document records that running the engine over all 15,028 item names scores
> `row_confidence` **0.00 on every row**, carrying `GRADE_MISSING` and
> `MANUFACTURER_UNKNOWN`. This has been read two ways — "the engine cannot decide
> on real data" and "the engine correctly abstains on an input that lacks the
> evidence." They have opposite implications and nobody has separated them.
>
> Separate them. Take the products present in **both** the item master and the
> pinned price-file corpus (the 1,420 identity-linked rows). Run `ParserPipeline`
> over the master's `Item.name` and over the corpus row for **the same product**,
> and compare `row_confidence` and the emitted flags pairwise.
>
> Report: the paired confidence distribution, the flag census on each side, and
> the share of the master-side gap attributable to fields the master structurally
> does not carry (it has no grade field at all; 6,146 rows have no manufacturer).
>
> **Hard constraint, and the point of the task:** do not change any threshold,
> confidence weight or validator. This measures the input, not the calibration. A
> tuned threshold that emits non-zero confidence over `GRADE_MISSING` is exactly
> the "absence of evidence read as a pass" failure §1 forbids, and would invalidate
> the result. Add a test asserting the confidence configuration is unchanged by
> this branch.
>
> Write the result into `docs/concepts/` alongside the original. `make verify`
> green, real numbers reported.

---

## T3 — Inbound line capture instrument ⭐ build this first

> Repo: pie-portal, branch `claude/inbound-line-capture`. Read `CLAUDE.md` §1, §3
> and §4 before writing anything.
>
> Build the instrument that records **every inbound enquiry line as it arrives**,
> whether or not it ever becomes a quote. This one instrument serves three
> purposes and is the reason it comes first: it is the only way to measure
> coverage over a denominator that does not condition on success, it is the corpus
> the RFQ accuracy benchmark needs, and it is the raw material for the
> unquoted-demand product.
>
> Per line, append-only: raw text exactly as received (no normalisation at
> capture), channel (email / WhatsApp / PDF / portal / phone note), customer,
> timestamp, and a terminal disposition — `QUOTED`, `ABSTAINED`, `NO_STOCK`,
> `NO_PRICE`, `LOST`, `NO_RESPONSE`. Disposition is set later and **superseded,
> never mutated**.
>
> Invariants:
> - `organization_id`-scoped on every row, like every other entity (§3).
> - Append-only and superseded-not-mutated, following the `state/` conventions.
> - Raw customer text is sensitive: it is per-tenant, it never leaves the tenant,
>   and anything cross-tenant belongs behind `trust/` with explicit opt-in. Do not
>   build a cross-tenant aggregate in this task.
> - No cost, no margin, in the model or in any response.
> - Migration in the same commit; verify on an **empty database** (§6).
>
> Acceptance: a line can be captured, its disposition set and later superseded
> with both versions retained, and a per-tenant export produces the full set with
> raw text intact. `make verify` green including the empty-database run and the
> drift test.

---

## T4 — Pack authoring kit

> Repo: pie-parser, branch `claude/pack-authoring-kit`. Read `CLAUDE.md` first and
> obey §1 and §2.
>
> Build a pack authoring kit under `tools/` — nothing under `engine/`:
> - `tools/pack_scaffold.py` — emit a complete, structurally valid pack skeleton
>   for a named manufacturer (manifest, routing ladder, one grammar, one pattern
>   file with example stubs, empty lookups). Fail loudly rather than produce a
>   silently empty pack.
> - `tools/pack_coverage.py` — given a price file and a pack, report per-family
>   parse rate, unrouted rows, an unknown-token census ranked by frequency, and a
>   "fix this next" list ordered by **rows recovered per rule**. This is the
>   scoreboard that turns pack authoring into a loop.
> - `tools/pack_lint.py` — structural validation, referential integrity, and the
>   existing rule that every pattern carries positive **and** negative examples —
>   runnable standalone against a work-in-progress pack.
>
> Acceptance, all three:
> 1. `pack_coverage` on `packs/kennametal_widia` + the pinned corpus reproduces
>    **6,717 / 6,717 with eleven families at 100%**.
> 2. Against a deliberately damaged copy (delete one grammar slot), the "fix next"
>    list ranks the damage first.
> 3. `make verify` green — report the actual numbers, do not claim them.
>
> Constraints: `engine/` gains no manufacturer literal and no new import. These are
> `tools/` scripts, below the §4 size floor — do not add a Protocol or an
> abstraction over them. Run the §2 capability search first and include the §6
> self-review in your response.

---

## T5 — RFQ benchmark harness

> Repo: pie-parser, branch `claude/rfq-benchmark`. Read `CLAUDE.md` first.
>
> Every accuracy figure in this repository is measured against the manufacturer's
> own price file — the clean case. There is no number for what the product
> actually does. Build the harness that produces one.
>
> - `eval/rfq_benchmark/` — case schema: raw inbound text exactly as received, the
>   product the human actually quoted, channel, anonymised source distributor,
>   date, ambiguity class.
> - `tools/eval_rfq.py` — computes per arm and per slice: **precision** (of lines
>   answered, share correct), **coverage** (share answered rather than abstained),
>   **abstention rate**, and **wrong-confident rate** (answered, confident, wrong —
>   the number the entire determinism argument rests on). Bootstrap confidence
>   intervals; breakdown by channel and ambiguity class.
> - **Three arms**, because "why isn't this just an LLM" deserves a number rather
>   than an argument: (1) the deterministic engine, (2) a naive baseline —
>   normalised exact plus fuzzy — as the floor, (3) an LLM arm. The LLM arm runs
>   in quarantine, never from `engine/`; if that is not yet possible, stub it
>   behind a clean interface and say so.
> - Report emission carries the ruleset checksum and is byte-reproducible.
>
> Acceptance: runs on a seed set, prints all four metrics with intervals for all
> arms, reruns byte-identically. Build against a small hand-written seed set — the
> real corpus arrives from T3 and from customer data being sourced separately.
> **Do not report synthetic-corpus results as a finding**; this repository's own
> rule is that absence of evidence is not a pass. `make verify` green.

---

## T6 — Pack proposal, quarantined

> Repo: pie-parser, branch `claude/pack-proposal-authoring`. Read `CLAUDE.md` §1
> carefully before writing anything.
>
> Create a new **top-level `authoring/` directory** — a build-time tool that reads
> a price file plus a manufacturer's published nomenclature document and emits
> candidate routing rules, grammars and lookup rows as reviewable YAML/CSV diffs.
>
> Get this part exactly right:
> - `authoring/` may call a model. `engine/` may not, ever. Nothing under
>   `authoring/` may be imported by `engine/`, `resolver/`, `identity/` or
>   `equivalence/`.
> - Add that as a check in `scripts/verify.sh` alongside the two existing §1
>   invariant checks — **AST-parsed, not grepped** — so it is enforced rather than
>   documented.
> - The output is a **proposal**, never a runtime path. Verification is
>   `tools/pack_coverage.py` plus the test suite, both deterministic. A proposal
>   that cannot be verified is rejected, not shipped with a confidence score.
> - `authoring/README.md` states all of the above in its first paragraph, because
>   the next reader's default assumption will be that PIE now uses AI.
>
> Acceptance, in its honest form: regenerate an **existing** family from scratch
> (`grooving_threading`, 567 rows, twelve systems) with the human only reviewing
> proposals, and reach **≥95% of the shipped pack's parse rate**. Reproducing a
> known-good pack is a measurement; grading a new pack against itself is not.

---

## T7 — Tenant master-health diagnostic

> Repo: pie-portal, branch `claude/master-health-report`. Read `CLAUDE.md` §1, §3
> and §4, and read `docs/concepts/01-application-engineering.md` — this package
> makes that hand-run analysis repeatable and per-tenant.
>
> Ingest an item master (CSV upload or an existing `ingestion/erp/` connector), run
> identity linking and *gated* geometry decode, and emit a **Master Health
> Report**: identity / geometry / union coverage, value-weighted coverage,
> manufacturer census, blank-field census, duplicate candidates, missing HSN and
> cost, and a remediation worklist ranked by rows recovered per hour.
>
> **Reuse, do not rebuild:** `identity.store.AuthoritativeIndex` for linking, the
> existing hygiene-watch logic for structural flags, `ingestion/erp/` for
> connectors. A second matcher here is the responsibility duplication §2 warns
> about.
>
> Invariants: structural flags only — **never a cost or margin value** (stock value
> at *selling* price is allowed and must be labelled); **gate geometry on full ISO
> slot fill** (measured misroute rate for partial decodes is 11.6%); any persisted
> computed number carries a `thresholds_version`.
>
> Acceptance:
> 1. Against the SLS master, reproduce the published census: **15,028 items, 9.4%
>    identity, 17.1% geometry, 21.6% union, 23.5% value-weighted.**
> 2. Against 4U, report **~0% with the reason stated** — "no pack covers this
>    manufacturer" — never a bare zero. "The pack does not cover this" and "nobody
>    asked the pack" are different facts.
> 3. Migration in the same commit; `make verify` green including the
>    empty-database run.

---

## T8 — Pack #2 on the clock

**This is the experiment the whole thesis turns on.** The honest version is a
hired engineer with a stopwatch. A Claude Code session working from the docs alone
is a useful lower bound and a direct test of whether the pack documentation is
sufficient — and given how this codebase is actually built, arguably the more
relevant question. Run whichever you can start this week; the escalation log is
the real output either way.

> Repo: pie-parser, branch `claude/pack-yg1`. **Work only from
> `packs/kennametal_widia/docs/`, `CONTRIBUTING.md`, `CLAUDE.md`, and the tools in
> `tools/`. Do not ask the repository owner questions.**
>
> Author a new manufacturer pack for **YG1** at `packs/yg1/`, following the same
> artifact shapes as the existing pack: manifest, routing ladder, patterns with
> embedded positive and negative examples, typed grammars, lookup CSVs, validator
> instances, golden assertions, and a `docs/<family>.md` per family. Use
> `tools/pack_scaffold.py` to start and `tools/pack_coverage.py` as your
> scoreboard.
>
> **No engine edits** unless a genuinely new slot *type* is required — and if one
> is, add the mechanism generically, named for what it does, and say so
> prominently in your final report. An engine change is a finding about the
> architecture, not a routine step.
>
> Two outputs, and the second matters more than the first:
> 1. The pack, at **≥95% parse rate** on the supplied YG1 price file, with the
>    number reported from `pack_coverage` rather than claimed.
> 2. **`packs/yg1/docs/escalation-log.md`** — every question you would have asked a
>    domain expert if you had been allowed to, what you did instead, and how
>    confident you are in each substitution. This is the specification for the next
>    iteration of the authoring tooling, and it is the deliverable a reader of this
>    experiment cares about most.
>
> Record elapsed wall-clock in the final report. `make verify` green with real
> numbers.

---

## T9 — Coverage curve per tenant

> Repo: pie-portal, branch `claude/coverage-curve`. Depends on T7.
>
> Persist coverage at day 1 / 7 / 30 / 90 per tenant, plus remediation effort
> logged against it, so time-to-value is a number rather than a claim. Append-only,
> superseded-not-mutated. Surface the curve on the onboarding screen using
> `platform/DataGrid.tsx` and the `docs/ui-standards.md` conventions — read that
> document before writing UI.
>
> Deliberately small. Do not over-build it. Migration in the same commit,
> `make verify` green including the empty-database run.

---

## T10 — Resolution outcome loop

> Repo: pie-portal, branch `claude/resolution-outcomes`. Read `CLAUDE.md` §1 and
> `tests/test_identity_confirmation_gate.py` before writing anything. Depends on T3.
>
> Capture, append-only, per resolved line: what was proposed, what the human chose,
> whether it reached a quote, whether that quote was won. Then two reports:
> **equivalence acceptance rate** (share of proposals a domain expert accepts) and
> **quoted→won by relationship** (EXACT / TECH / COMPAT).
>
> **The invariant that governs this task:** an accepted suggestion must never
> become an asserted identity. A scored suggestion promoted to a confirmed mapping
> becomes an exact reference it never was, and the next "same as their 7781 but
> 12 mm" composes two tolerance bands into a wrong part with a defensible
> explanation attached. Extend `test_identity_confirmation_gate.py` to cover the
> new outcome path rather than working around it.
>
> Acceptance rates may inform **ranking**. Ranking is policy, so it carries a
> version stamp, and a `rel` derived under one policy is never persisted as a
> relationship between products.
>
> Acceptance: both reports render per tenant; the extended gate test fails if an
> outcome-derived record is ever offered as confirmable.

---

## T11 — Resolution as a versioned API

> Repo: pie-portal, branch `claude/resolution-api`. Read `CLAUDE.md` §1 and §3.
>
> `POST /api/v1/resolve` — text in; structured resolution out carrying provenance,
> confidence, span, alternatives, and an explicit abstention when the evidence is
> absent. API-key authenticated, per-tenant, rate-limited, versioned. Publish the
> OpenAPI spec and a documentation page whose accuracy claims cite the T5 report
> rather than restating them.
>
> Invariants: the router stays thin — it maps, it does not decide. Role scoping and
> the cost/margin withholding rules apply to API responses exactly as to the UI; an
> API key is a recipient like any other, and `boundary_refs` withholding applies
> unchanged.
>
> Acceptance: an external caller holding only an API key can resolve a line,
> receive a well-formed abstention on an unresolvable one, and confirm a mapping —
> with the confirmation gate still refusing anything that is not the engine's own
> single-candidate `NEEDS_REVIEW` proposal.

---

## T12 — Unquoted-demand reporting

> Repo: pie-portal, branch `claude/unquoted-demand`. Depends on T3, which already
> holds the data. Do not start before the T5 and T8 gates report.
>
> Report, per tenant, every inbound line that never became a quote, with its
> reason, aggregated by manufacturer and product class — "the money you did not
> quote last month."
>
> **Guardrail:** per-tenant by default. Any cross-tenant aggregate — the version a
> manufacturer would pay for — requires explicit opt-in recorded per tenant and
> belongs in `trust/` with the rest of the disclosure controls. Shipping it without
> that loses the customers who were already nervous about the ownership question.

---

# Verified revision — 2026-08-23

Before spawning child sessions, all six ready-to-start prompts were checked
against the actual repositories by six independent agents, each instructed to
verify every path, symbol, model name and quoted acceptance number by opening
files rather than inferring from naming. **Three tasks came back
BLOCKED_ON_DATA and four acceptance criteria were unsatisfiable or rested on
something that does not exist.** The findings are recorded here because they are
permanent knowledge about these repositories, worth more than the sessions that
prompted them.

## Findings that apply to every pie-portal task

**The `pie-parser` submodule is empty and the gate hides it.** `pie-portal/pie-parser/`
has no files (`git submodule status` shows `-41ee3d005b2ff`, uninitialised, and
the repo is private); a full checkout at the pinned commit sits at
`/home/user/pie-parser`. `settings.PIE_PARSER_ROOT` defaults to the empty
directory and there is no `.env`. Without `export PIE_PARSER_ROOT=/home/user/pie-parser`:
`app/catalog.py` raises `PIE corpus not found`, `pie_service._ensure_index` logs
a warning and returns None, and **`scripts/verify.sh:41-50` prints "pie-parser is
not checked out", SKIPS every `requires_pie` test, and still stamps the run
VERIFIED** ("but narrowed"). Any session reporting "make verify green" without
setting that variable is reporting a narrowed run as a full one. This is the
same class of failure `CLAUDE.md` §6 records as having survived eight merges.

**There is no local data.** `backend/data/platform.db` is demo seed only — one
organization, four fabricated products with no SKU, ~20 `SalesTxn` rows. There is
no `.env`, no `ZOHO_*` credentials, and `settings.ZOHO_SOURCE` defaults to
`"fixture"` (3 invoices, 2 items). Any coverage number computed here would be
fabricated.

**The engine chain itself works offline and was proven to.** A verifier ran
`PIE_PARSER_ROOT=/home/user/pie-parser python3 scripts/build_catalog.py` →
6,717 products, 0 quarantined, matching the published figure, and
`AuthoritativeIndex.from_jsonl(...).lookup_material('2001174')` returned the
record with grade TN2000. So instruments can be built and unit-tested; only the
real runs are blocked.

## Per-task corrections

### T1 — BLOCKED_ON_DATA, and two structural errors

- **No estimate line-level history exists anywhere.** The prompt said "invoice and
  estimate lines"; only the invoice half exists. Invoice lines are
  `models.SalesTxn` / `sales_txns` (models.py:1177, "Invoice-line grain"), product
  reference `SalesTxn.product_id`. Estimates are **write-only**: `routers/quote.py:356`
  pushes to Zoho, there is no `_sync_estimates` in `ingestion/sync.py`, no
  estimates table, no estimate pull in any `ingestion/erp/*.py`. Scope to invoice
  lines, or add "pull Zoho estimates" as explicit extra scope.
- **`Product` has no `sku` column** and the sync never persists one, so the
  SKU→index join the prompt assumed does not exist where a session would look.
  SKU lives only on `models.ItemConnectorRecord.sku` (models.py:2313). The real
  path is `SalesTxn.product_id` → `ItemConnectorRecord.product_id` →
  `ItemConnectorRecord.sku` → `lookup_material`.
- **The answer is already persisted and never read.** `sync.py:1156 _link_catalog`
  writes `Product.pie_record_id` / `pie_link_method` / `pie_catalog_version` on
  every item pull, and nothing in the codebase reads those columns back. On a
  synced database the identity half of this measurement is a join, not a
  re-resolution.
- **Reuse surface is `pie_service.lookup_record()` and `pie_service.catalog_available`**,
  not `AuthoritativeIndex` directly — the latter exists precisely to separate "the
  pack does not cover this item" from "nobody asked the pack".
- Only `lookup_material` resolves against this catalogue: of 6,717 records just
  **44 carry `catalog_number_full`**, so `lookup_catalog` is effectively dead here.
- No existing coverage script to reuse — the one that produced the 21.6% was never
  committed. Copy the convention of `scripts/measure_crossbrand.py`.
- A live pull is expensive: `zoho_client.list_invoices` fetches per-document detail
  because `line_items` is not on the list response — one API call per invoice,
  against ~3,367 invoices for SLS.

### T2 — unblocked by a better experiment design

The verifier found the master side can be **modelled exactly by re-running the same
corpus rows with the Grade column suppressed**, because what makes the master a
poorer input is precisely the fields it lacks. That converts T2 from blocked to
runnable offline, and it is a cleaner experiment than the original.

- There is **no `Item` model** in pie-portal; it is `Product` (models.py:643).
- The corpus is **nomenclature, not a price file** — `MM# / Material Description /
  Grade`, no prices anywhere.
- The 15,028 / 1,420 / 6,146 figures are **prose only** in
  `docs/concepts/01-application-engineering.md`; they are not reproducible from the
  repository. Cite them as prior measurements, do not claim to reproduce them.
- Confirmed reproducible: the §3 "0.00 on every row" finding does reproduce.

### T3 — READY, two small corrections

- The org-scoping rule is **not** in `CLAUDE.md` §3 (that section is Module
  boundaries). It is the module docstring of `backend/app/domain/models.py`.
- **There is no cross-tenant consent primitive in `trust/`** — it holds tenant keys,
  the name vault, pseudonyms, break-glass and erasure. Do not write as though an
  opt-in mechanism exists.

### T4 — READY, but the acceptance test was broken

- **Acceptance #2 was both unsatisfiable and vacuous.** A verifier ran it: deleting
  `{group: shape, slot: iso_shape, type: token}` from `G-KMT-TI-TRUNC` does not move
  the named metrics, and `KMT-VAL-005 / iso_shape` is **already the largest gap on
  the undamaged pack** (785 rows vs 3 and 1 for its neighbours) — so any "fix next"
  list ranks it first regardless. Replaced with a **pack-version diff**: the tool
  compares two pack versions and names the regression.
- **"Every pattern carries positive and negative examples" is not a rule.**
  `engine/patterns.py:75-92` fails a pattern only for missing *positive* examples;
  `examples_nomatch` run only when present. Adding a non-fatal lint warning for
  missing negatives is a genuine improvement, but it is new, not existing.
- **"Eleven families" is a corpus property.** The manifest declares **sixteen**;
  five (cartridge, boring_bar, tap, toolholder, accessory) never occur in the corpus.
- The corpus is `corpora/kmt_zcnc_2026-07_nomenclature.csv`, 6,717 data rows.

### T5 — READY, and it uncovered a real hole in the gate

- **`scripts/verify.sh` step 4 scans `engine/*.py` only.** It would not catch an
  `anthropic` or `openai` import landing in `tools/`, `resolver/`, `identity/` or
  `equivalence/`. Since this task introduces the first plausible route for exactly
  that, widening the check is worth landing as its own commit.
- `tools/eval_identity.py`'s Scorecard **already computes** `correct_abstention` /
  `expected_abstention`. Extend it; do not write a second scorer.
- "Reruns byte-identically for all arms" cannot hold — a live LLM arm is
  nondeterministic. Engine and baseline arms are byte-reproducible; the LLM arm must
  be record/replay with committed transcripts, or stubbed.
- **Do not add an LLM SDK to `requirements.txt`** — it contradicts §1
  offline-and-dependency-light. Optional import, skipped with a visible note.
- The framing "there is no number for what the product does" is overstated: the repo
  does have abstention semantics and a golden identity eval. This task is about
  *real inbound text*, not about inventing measurement from nothing.

### T7 — HELD. The task as written has a false premise.

Do not run this prompt as specified. Four of its instructions refer to things that
do not exist:

- **"Reuse the existing hygiene-watch logic" — there is none in pie-portal.**
  `rg -i hygiene` over the whole repository returns only prose in `docs/`. It is a
  Claude skill, not backend code, and is not importable from the backend.
- **"CSV upload" does not exist and is not a one-line addition.** There is no
  `UploadFile` or multipart handler anywhere in `backend/app`, and `python-multipart`
  — which FastAPI requires for form parsing — is not installed.
- **`ingestion/erp/` is the wrong path for both acceptance entities.**
  `ingestion/erp/base.py` states in its own module docstring that Zoho deliberately
  does not register there.
- **Value-weighted coverage cannot be computed** from what the platform currently
  ingests.
- The acceptance number is also **already stale**: the live SLS master holds
  **15,082** items today (Zoho `/items`, `filter_by=Status.All`, 75×200 + 82), not
  the published 15,028.
- And "the measured misroute rate for partial decodes is 11.6%" **mislabels the
  measurement**: doc 01 §3 says 30.3% of rows route to a named family and 11.6% *of
  routed rows* carry a named non-Kennametal manufacturer — a contamination rate, not
  a misroute rate.

Rescope before running: decide whether the report is (a) offline-only over a
supplied items export, which removes the upload and connector questions entirely,
or (b) a live-Zoho tool, which needs the paging limit solved first — the connector's
`page` parameter silently resets to 1 beyond roughly the 10,000-record window.
