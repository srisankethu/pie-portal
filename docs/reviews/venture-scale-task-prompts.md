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
