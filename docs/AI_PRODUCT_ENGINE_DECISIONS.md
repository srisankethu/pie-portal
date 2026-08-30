# AI Product Engine — architectural decisions

A running log. One entry per decision, newest last. Each entry states the
decision, what it rejects, and the evidence — so a later reader can tell a
considered choice from a default that nobody noticed.

Status values: **PROPOSED** (awaiting approval) · **ACCEPTED** · **SUPERSEDED
BY nnn** · **REJECTED**.

Companion document: `AI_PRODUCT_ENGINE_ARCHITECTURE.md` (the Phase 0 report).
Section references below are to that document.

---

## 001 — pie-parser stays the decoder; pie-portal becomes the decider
**Status:** PROPOSED · **Phase:** 0 · **Report §:** 18, 23

**Decision.** The parser keeps its invariants — offline, deterministic, zero
manufacturer or organisation literals, pack-driven — and gains only *generic*
mechanisms. Equivalence policy, ranking, feedback and evidence live in the
portal, org-scoped and versioned.

**Rejected.** Putting tolerance bands or criticality into packs. A pack is
inherited byte-for-byte by every organisation selling that manufacturer, and
`docs/concepts/10` establishes that a band is commercial policy on which two
organisations may legitimately disagree.

**Evidence.** `pie-parser/CLAUDE.md` §1 and §3; `pie_service._rel_from_score`
reading `equivalence_tech_band` per request.

---

## 002 — Attribute decoration is Phase 1, and its exit criterion is coverage
**Status:** PROPOSED · **Phase:** 1 · **Report §:** 6, 19, 39

**Decision.** Persist per-product attributes with full provenance in
`product_attribute_values` before building retrieval, rules, ranking or
learning. Phase 1 succeeds or fails on **published attribute coverage per
category**, not on accuracy.

**Rejected.** Starting with retrieval or ranking. Both would be built over a
catalogue where 78.4% of rows carry no technical fact.

**Evidence.** `docs/concepts/01-application-engineering.md` (2026-08-09):
15,028 items, 9.4% identity-linked, 17.1% geometry-decodable, 21.6% union.
The same document defers substitution *"on the evidence, not on effort"*.
`equivalence/catalog.py:ZohoCatalogSource` already reads five attribute fields
that do not exist on a real row — the socket is built and empty.

---

## 003 — Candidate generation moves into PostgreSQL
**Status:** PROPOSED · **Phase:** 2 · **Report §:** 22, 34

**Decision.** Retrieval becomes staged and database-resident: exact →
normalized part number → lexical (`tsvector` + `pg_trgm`) → structured
attribute filter. The parser's in-memory pool remains the decoder's working
set and stops being the retrieval index.

**Rejected.** Scaling the in-process linear scan.

**Evidence.** Measured on this checkout: `find_equivalents` costs 30.8 ms at
6,717 records, 196.6 ms at 33,585 and 555.1 ms at 100,755 — linear; and the
pool costs **10.2 KB resident per record**, so 100k SKUs is **≈1 GB per worker
process**, multiplied by worker count. `docs/hosting-free-tier.md` already
flags the 13 MB copy per worker at today's size.

---

## 004 — No vector database, no graph database, no broker
**Status:** PROPOSED · **Phase:** 2 · **Report §:** 38

**Decision.** `pg_trgm` first, in the database that already holds the data.
`pgvector` only against a *measured* recall gap on the evaluation set, and in
the same PostgreSQL instance. Relationships stay in relational tables. The
existing DB-backed queue carries every batch job.

**Rejected.** A separate vector store, a graph database, Kafka, Kubernetes.

**Evidence.** A graph database's central affordance is traversal, which this
domain forbids (`tests/test_equivalence_not_transitive.py`). The queue already
implements conditional-`UPDATE` claim, heartbeat, `reap_stale`, bounded retry
and `DEAD_LETTER`.

---

## 005 — UNKNOWN is a verdict, not a missing comparison
**Status:** PROPOSED · **Phase:** 4 · **Report §:** 25

**Decision.** Every attribute comparison returns `MATCH`, `ACCEPTABLE`,
`INCOMPATIBLE` or `UNKNOWN`. A **critical** attribute that is `UNKNOWN` on
either side yields `INSUFFICIENT_INFORMATION` for that candidate — it does not
produce a score.

**Rejected.** Today's behaviour, where `compare_geometry` skips a hard gate
when either side is `None` (`equivalence/distance.py:131–132`) and a vacuous
perfect score is possible.

**Evidence.** Reproduced live on this checkout: `"6205 2RS C3 bearing"`
returned carbide inserts and endmills at score 1.0. The existing guards did
fire — the line came back `AMBIGUOUS` with the note *"no dimension was
comparable, so a perfect dimensional score is vacuous"* — but the protection is
a tie-heuristic and a note downstream of the score, not the score itself.

---

## 006 — Compatibility rules are data, directional, and category-specific
**Status:** PROPOSED · **Phase:** 4 · **Report §:** 25

**Decision.** Rule kinds — `EXACT`, `MIN`, `MAX`, `RANGE`, `TOLERANCE` (with
`SYMMETRIC | UPWARD_OK | DOWNWARD_OK`), `ENUM` with an explicit compatibility
matrix, `BOOLEAN`, `STANDARD`, `RELATION` — are declared in `ontology_rules`,
never in code.

**Rejected.** Extending `ToleranceModel`. It scores `|ref − cand| / ref`
against a symmetric band and cannot express direction; `HARD_GATE_FIELDS` is
one hardcoded triple for cutting tools. A 12 A contactor may replace a 10 A one
and not the reverse, and 24 V DC versus 230 V AC is a matrix entry rather than
a distance.

---

## 007 — Extend the existing relationship vocabulary; do not add a second
**Status:** PROPOSED · **Phase:** 5 · **Report §:** 26

**Decision.** The nine values already rendered on two screens (`frontend/src/rel.ts`)
are refined rather than replaced: `EXACT` splits into `EXACT_MATCH` /
`OEM_EXACT_PART`; `TECH` splits into `CONFIRMED_EQUIVALENT` /
`FUNCTIONAL_EQUIVALENT` on whether a **citation** exists; `COMPAT` and
`POSSIBLE` map to the substitute classes; `AMBIGUOUS`/`UNRESOLVED` map to
`INSUFFICIENT_INFORMATION`. `SIMILAR_PRODUCT` is genuinely new and must be
visually distinct, because it is the one class that is **not an offer**.
`PIE_DOWN` and `NONE` are service states and stay as they are. Migration is
additive and versioned.

**Rejected.** A parallel nine-value vocabulary — the semantic duplication both
`CLAUDE.md` files name as the repository's characteristic failure mode.

---

## 008 — Derived equivalence is never promoted to asserted identity
**Status:** PROPOSED · **Phase:** 5 · **Report §:** 26, 28

**Decision.** `equivalence_results` (derived, per run, under a policy version)
and `product_relationships` (asserted, human-confirmed, evidenced) are separate
tables and nothing moves from the first to the second automatically.
`POST /api/v1/equivalences` accepts only human-confirmed, evidenced assertions.
The existing confirmation gate is unchanged: only a single-candidate
`NEEDS_REVIEW` proposal may become a permanent identity, through
`identity.service.confirm_proposed_identity`.

**Rejected.** Learning a substitution table from accepted suggestions. A scored
suggestion promoted to a confirmed mapping becomes an exact reference it never
was, and the next request composes two tolerance bands into a wrong part with a
defensible explanation attached.

**Evidence.** `docs/concepts/10` traces six paths that could carry a closure
and finds all six clean; `tests/test_equivalence_not_transitive.py` and
`tests/test_identity_confirmation_gate.py` pin them.

---

## 009 — Technical validity gates; commercial signals only rank
**Status:** PROPOSED · **Phase:** 6, 8 · **Report §:** 27

**Decision.** Anything below `POSSIBLE_SUBSTITUTE` is absent from the ranked
list rather than ranked low. Commercial signals order what survives.
Additionally: **the ordering a salesperson sees is technical and availability
only**; commercial ordering is a management-role projection, and the new
endpoints get the same price-sweep regression test as
`test_a_salesperson_cannot_walk_the_price_to_recover_cost`.

**Rejected.** Margin as a weighted feature in one shared ranking. A weight can
be outvoted by other weights; a gate cannot. And an order a caller can change
by varying a price is a predicate they can walk — the MFLOOR and
`NEGATIVE_MARGIN` defect in a new shape.

---

## 010 — Bind the ontology to published standards
**Status:** PROPOSED · **Phase:** 1 · **Report §:** 23, 38

**Decision.** `ontology_categories` and `ontology_attributes` bind to ETIM
classes and ISO 13399 property identifiers. What this organisation owns is
criticality and tolerance policy — a rule set over a published vocabulary.

**Rejected.** Authoring a bespoke product ontology or "knowledge graph".

**Evidence.** `docs/reviews/venture-thesis-review-2026-08.md` lists it among
the things founders mistake for a moat: *"Ontologies here are published
standards (ISO 1832, ISO 13399, DIN, ANSI, ETIM). 'Our knowledge graph'
describes a maintenance liability with good branding."* `docs/concepts/10`
places a full application ontology below the line.

---

## 011 — No model is trained in this programme
**Status:** PROPOSED · **Phase:** 9 · **Report §:** 38

**Decision.** Phase 9 produces **datasets and offline evaluation only**.
Training waits for label counts that clear the floors `docs/concepts/14` sets.

**Evidence.** Six recorded quote losses against a minority-class floor of 100;
~12.5 observations at item grain. An embedding *shortlist* is endorsed (§5.17)
because it is pretrained and requires no labels — it is not a trained model and
is not covered by this decision.

---

## 012 — Revisit the no-upload decision explicitly
**Status:** PROPOSED · **Phase:** 3 · **Report §:** 11, 24, 33

**Decision.** File upload is added for customer RFQ documents, with type
sniffing, size limits, archive-bomb defence, no inline rendering, a licence
note per document, and erasure reach into stored files and extracted
requirements.

**What this reverses.** `master_health/__init__.py:11` records the absence as
deliberate: *"Adding one is a dependency decision and a new attack surface, and
it buys nothing a path argument does not already give a person running a
diagnostic."* That reasoning is correct for a diagnostic CLI and does not hold
for a salesperson receiving a PDF, so the decision is reversed **in the open**
rather than eroded.

---

## 013 — Populate `grade_crossref.csv` before anything else
**Status:** PROPOSED · **Phase:** 0 → 1 · **Report §:** 35, 39

**Decision.** Add sourced rows to `pie-parser/equivalence/lookups/grade_crossref.csv`
from published cross-reference charts, each citing its chart in `source_ref`,
and fetch the pie-parser submodule in CI.

**Why first.** The file **ships header-only**, so the business cannot
cross-reference a grade at all today, and grade is the field
`docs/concepts/13` measured as carrying 100% of the confidence gap. Ten sourced
rows cost a morning, need no code, and are the precondition for measuring
cross-brand equivalence at all.

---

## 014 — Batch jobs are chunked; the queue is extended, not replaced
**Status:** PROPOSED · **Phase:** 1 · **Report §:** 21, 34

**Decision.** Long jobs (master decode, re-index, re-embed) are submitted as
**chunks over a product range**, each carrying a progress row, following
`ingestion/jobs.execute_sync`'s per-phase `commit`.

**Why this needs saying.** The queue is durable and correct, but it runs a
**single-threaded worker loop per process, draining sequentially**, with no
chunking, no streaming and no per-job progress outside `SyncRun`. A 100k-row
decode submitted as one message would hold the only worker for its whole
duration and report nothing while it ran — and a flush nobody can read is not
progress reporting.

**Rejected.** A second queue, a broker, or a worker pool. The gap is a message
shape, not infrastructure.

---

## 015 — Evaluation is staffed from Phase 1, and reports per category
**Status:** PROPOSED · **Phase:** 1 → throughout · **Report §:** 29, 39

**Decision.** Workstream 12 starts with Phase 1 and runs in parallel
throughout. `tools/scorecard.py` stays the single definition of what an
evaluation counts; both harnesses keep filling it and no second scorecard is
introduced. **`wrong_confident_rate` is the false-equivalence metric** and is
the only measure permitted to block a release.

**Why now rather than after Phase 6.** The instrument already exists and is
already reporting — and what it reports is a reason to act rather than to wait:
on fourteen RFQ cases the engine arm scores 60.0% precision, 71.4% coverage and
**21.4% wrong-confident**, against a baseline arm at 70.0% / 71.4% / **7.1%**.
Fourteen cases supports no conclusion about the engine; it does establish that
the measurement exists, that it can move in the wrong direction, and that the
dataset — not the metric — is the missing half.

**Consequence.** A metric introduced after the thing it measures tends to be a
metric the thing already passes.

---

## 016 — Lead time and selling price need a decision before Phase 8
**Status:** OPEN — decision required · **Phase:** 8 · **Report §:** 12, 40

**The situation.** Of the brief's commercial ranking signals, stock, supplier
and cost are persisted; **selling price is not stored on `products` at all**
(it is read live per quote line from Zoho), **lead time is not stored at all**
(`expected_date` is blank on effectively every purchase order, and the only
derivation needs three prior receipts), and warehouse data arrives from one
connector out of six.

**The choice.** Persist them on a sync — a schema and freshness question — or
fetch live per candidate, which at ten candidates a line is a per-quote latency
decision. Both are defensible; neither is a modelling problem, and Phase 8
cannot be scoped until one is chosen.

**Not a decision to defer into implementation.** A ranking that silently ranks
on a stale or absent lead time is the benign default this codebase already
refuses three times over: the answer would be UNKNOWN, and a candidate whose
availability is UNKNOWN must say so rather than sort as though it were quick.

---

## 017 — Fix the two resolution defects before building on that path
**Status:** **DONE** — landed, both gates green · **Report §:** 17, 34

**The defects**, both reproduced on this checkout, both unrecorded anywhere in
either repository:

1. **The MIXED path asserts the reference product as the answer.**
   `"same as 2001174 but 0.4 corner radius"` returns `rel=EXACT`,
   `supplyCode=2001174` — the **0.8 mm** insert. The resolution keeps
   `outcome=AUTO_MATCH` with an `AUTHORITATIVE` match while attaching the
   effective-requirement suggestions, so the line auto-selects and prices the
   product the customer asked to *vary*, and the variation is read, used for the
   suggestion list, then dropped for the selection.
2. **Vacuous comparisons are labelled `TECH`.** The candidates beside it score
   0.96 as `TECH` with `corner_radius_mm = None` — square and screw-on inserts
   held technically equivalent to a CNMG turning insert on a comparison where no
   dimension was comparable. The vacuity note written for exactly this case is
   rendered only when the candidate list is **empty**, so it never appears in
   the case it describes.

**Decision.** Both are fixed, with golden-corpus regression cases, before any
new surface is built on the MIXED path. Neither depends on the ontology, the
attribute store, or any decision in this programme.

**Why it is stated as a decision rather than a bug report.** `docs/concepts/10`
spends 260 lines protecting against *"not a visibly wrong answer, but a
confidently wrong one"*. This is that, reachable in one line, on the path a
customer request most naturally takes. It should not be queued behind an
architecture review.

### What landed

**The contract, in pie-parser.** `resolution` says what the identity layer
matched and cannot say what that match *is to the answer* — on both reference
paths it is a truthful `AUTO_MATCH` over a truthful `AUTHORITATIVE` match and
still not the product. `tools/resolve_rfq.run` now states `identity_role` —
`ANSWER`, `REFERENCE` or `NONE` — on **every** exit. It is seeded to `NONE`
at the top of `run` rather than set per branch, so a path added later that
forgets to say cannot thereby claim to be the answer. The MIXED path also
prepends a note naming the reference and quoting what was actually asked for.

**The reading, in pie-portal.** `pie_service._map` branch (1) is skipped for a
reference; a new branch (1r) keeps the named product *offerable* — a person may
well decide to offer it and ask — as a `POSSIBLE` candidate carrying its role in
its reason, appended last and carrying no score, so it can neither win a
ranking it never entered nor be auto-selected. Two fallbacks cover an engine
that predates the field: `MIXED` semantics, or a derived `effective_requirement`.
Absence must not read as ANSWER.

**The vacuity guard.** `_candidates_from_suggestions` reads
`dimensionally_vacuous` — which the engine already stamped and nobody read —
caps such a candidate at `POSSIBLE`, and says why in its reason. Branch (3)
refuses to auto-select a vacuous leader, with its own note: a tie and a
comparison containing no dimension are different failures and the reader is
told which happened. `_rel_from_score` is untouched; the mapping was always
correct, and what was wrong was feeding it a score that measured nothing.

**The note is visible.** `SupplyDrawer` rendered `line.notes` only when there
were *no* candidates — so the engine's vacuity caveat, which by definition
describes candidates on screen, was invisible every time it applied.

### Verified

`"same as 2001174 but 0.4 corner radius"` now returns `AMBIGUOUS` with
`supplyCode=None`, the reference last and labelled, and the note first.
`"same as 2001174 but TN4000 grade"` — a variation the catalogue *can* satisfy —
now surfaces real CNMG 120408 TN4000 inserts, which it did not before.
`"6205 2RS C3 bearing"` offers nothing as `TECH` or `COMPAT`. A pure identity
still resolves `EXACT`.

The public `POST /api/v1/resolve` inherits all of it: `resolution.py` builds its
document from `Resolution.supplyCode` and `.candidates` rather than re-reading
`matches`, and `_identity_proposal` delegates to `store._identity_candidate`,
which still requires a single-candidate `NEEDS_REVIEW` — so a reference cannot
become a confirmable mapping.

Gates: pie-parser **439 passed**, all six steps. pie-portal **3,581 passed, 34
skipped**, all seven steps including PostgreSQL migrations, row-level security
and the restore drill.

### The first version of this fix was wrong, and an adversarial pass caught it

Recorded because the failure is more instructive than the fix. Three
independent reviewers were pointed at the landed change with instructions to
defeat it. Two did, immediately:

1. **It fixed one string, not the defect.** `detect_mixed` recognises only
   "same/like/similar … but/instead/->". Every other phrasing of a variation —
   `"2001174 but 0.4 corner radius"`, `"2001174 with 0.4"`, `"like 2001174 in
   0.4"`, `"2001174 uncoated"` — classified as REQUIREMENT, and the branch that
   answers an exact identity fired **without checking what the classifier
   said**, returning the unvaried product as EXACT. The fix's own test file
   listed one of those strings and passed, because the test asserted only that
   the role was *one of the three values* — a shape assertion cannot see a
   wrong value.
2. **The guard voided itself in the common case.** The reference is normally
   *in* `suggestions`, usually first: the effective requirement is derived from
   the reference's own facts, so the reference matches it better than anything
   else. The labelled `POSSIBLE` copy was appended only when no candidate
   already carried that code — so in the common case nothing was appended, the
   *ranked* copy stayed at position 0, and it was auto-selected with a score
   band. Measured: **21 of 120** sampled variation requests auto-selected their
   own reference, at `TECH`.

Both are fixed. `run` now refuses to answer an exact identity unless the
identifier accounts for the whole input — reusing `classify`, which already
decides exactly that, rather than teaching one regex every phrasing of a change
— and the classifier learned that a *count* is not a specification, so
`"2001174 x 10 nos"` still resolves exactly while `"2001174 but 0.4"` does not.
The reference is now **excluded from the ranked list and re-added last**, and
branch (3) refuses to auto-select it even when it is the only candidate.
Re-measured: **0 of 120**.

Three smaller holes closed with them: an unverified comparison was not refused
when the payload carried `dimensions_compared` but no `dimensionally_vacuous`
key (the same benign default this fix was written to remove, left in the fix
itself); a variation could still be offered as a **confirmable identity** for a
scoped customer, filing "this sentence means the unvaried product" permanently;
and `distance.py` skips a dimension the candidate does not carry, so a record
silent on the one dimension the customer changed — agreeing on two incidental
ones — scored 1.0, was not flagged, and was auto-selected. That last one is
2.1% of scored candidates today and rises with exactly what Phase 1 adds, so
the portal now treats *any* incomplete comparison as unverified, not only a
wholly vacuous one. The field is named `unverified` rather than `vacuous`
because it now means both.

**The lesson worth keeping:** a fix for a "confidently wrong" defect is exactly
the kind of change that is itself confidently wrong. Every assertion in the
first round's tests passed. What caught it was running the real engine over a
sample of the real catalogue and counting — which is the same instrument
`docs/concepts/13` used, and the one this report keeps recommending.

### Not fixed here, and deliberately

The override decode is unreliable — `"but 0.4 corner radius"` does not reliably
become `corner_radius_mm = 0.4`, so the derived requirement is partly wrong and
the candidates come back vacuous. That is a **third** defect, in
`identity/effective.py` and the override parse, outside this decision's scope.
It is now *visible* rather than hidden: the line abstains and says no dimension
could be compared, instead of quoting the reference. Fixing the decode is
tracked separately — see decision 021.

---

## 018 — Read the item taxonomy that already exists in the ERP
**Status:** PROPOSED · **Phase:** 1 · **Report §:** 6

**Decision.** `ingestion/zoho_client._item_payload` reads `cf_item_type`,
`cf_item_category`, `cf_bin_location` and `cf_catalog_status` and carries them
into the product row as source-attributed attributes.

**Why.** A human-maintained per-item classification (Insert / Drill / Endmill /
Tool Holder / Tap / Measuring Instrument; Milling / Holemaking / Threading /
Toolholding / Grooving & Parting / General) is populated on most sampled items
and **discarded at ingest** today, while the column the platform does read —
`category_name` — is set on **0 of 800** SLS items. This is category coverage
that costs a few lines and no decoding.

**Constraint.** It arrives as raw source text and is stored raw, interpreted at
read time under a versioned map, exactly as `category` and `manufacturer`
already are. It is evidence about an item, not a normalised truth.

---

## 019 — Enable the row-level security that is already written
**Status:** PROPOSED — deployment change, not code · **Report §:** 5, 33

**The situation.** 81 tables carry RLS policies, `ENABLE` and `FORCE`, reading
a transaction-scoped GUC, failing closed on an unset tenant, and the gate tests
them against a role that is neither superuser nor owner. But `APP_DATABASE_URL`
— the non-bypassing role — appears **only** in `docs/postgres.md`. Not in
`compose.yaml`, `compose.dev.yaml`, `railway.json`,
`deploy/production.env.example`, `.env.example`, `docs/hosting.md`,
`docs/hosting-free-tier.md` or `docs/operations.md`. Every documented
deployment connects as the owner, who bypasses RLS.

**Decision.** Set it in the deployment recipes before this programme adds
tables. The platform's own `observability/health.py` already reports
`tenant_isolation` and would say `UNHEALTHY` today.

**Note.** Python-side `organization_id` filtering is doing the work alone in
production. That is the control the RLS layer exists to back up, and it was
added precisely because a survey found places where the filter lives in a
comprehension rather than in SQL.

---

## 020 — Add new packages to the layer-boundary invariant explicitly
**Status:** PROPOSED · **Phase:** 1 · **Report §:** 17, 32

**Decision.** Any new deterministic package — `ontology/`, `retrieval/`,
`compatibility/`, `equivalence/`, `ranking/`, `evidence/` — is added to
`DETERMINISTIC` in `tests/decision_platform/test_layer_boundaries.py` **in the
commit that creates it**.

**Why this needs a decision.** The invariant is opt-in. `DETERMINISTIC` names
six packages (`attribution`, `commercial`, `enquiry`, `ingestion`, `signals`,
`state`); `identity/`, `trust/`, `context/`, `master_health/`, `messaging/`,
`observability/`, `routers/` and the top-level `pie_service.py`, `store.py` and
`resolution.py` are unconstrained today. A new package that imports `ai/` would
pass the gate in silence — which is the failure mode this codebase has
documented twice: a check that does not run reads exactly like a check that
passes.

---

## 021 — The override decode is a third defect, and it is now visible
**Status:** **DONE** — landed, both gates green · **Report §:** 17

**What it is.** On the MIXED path, `"same as 2001174 but 0.4 corner radius"`
does not reliably turn into `corner_radius_mm = 0.4`. The change text is parsed
as though it were part number notation, so plain English contributes letters to
ISO designation slots rather than a value to a dimension. The derived
requirement is therefore partly fiction, and the candidates ranked against it
come back with no comparable dimension at all — which is why the same query
that exposed 017 also returns square and screw-on inserts.

**Why it is not folded into 017.** Different cause, different file, different
risk. 017 is a *contract* fix — who may be quoted — and is provably safe: it can
only ever withhold an assertion. Rewriting the override parse changes what the
engine *believes a customer asked for*, which is the input to everything
downstream, and it needs its own corpus cases before it is touched. Bundling
them would have made a safe fix unreviewable.

**What 017 already changed about it.** The failure used to be invisible: the
line quoted the reference and looked resolved. It now abstains and says "no
dimension of the request could be compared against these candidates". A wrong
decode that announces itself is a different class of problem from one that
prices a part.

**What fixing it needed** — and it turned out to be three things, each in the
layer that owned the problem:

1. **The words that name a specification are data.** `resolver/lookups/spec_terms.csv`
   is a fourth lookup beside application / category / material, consumed by the
   same phrase pass — the one whose comment already said it exists *"so 'STEEL'
   is not mis-read as an ISO 'S' shape"*. Dimension nouns simply had no table,
   so "corner", "flute" and "mm" reached the insert-code decoder and became ISO
   designations taken from their own letters: `CORN` → shape C, clearance O,
   tolerance R, fixing N. Each of those is a **hard gate** in the equivalence
   engine, so the fiction did not add noise — it decided which records could be
   compared at all. The table also carries *which slot* a number beside the word
   fills, which is why it is a table rather than a stopword list: a stopword is
   discarded, while "corner radius" tells us what the 0.4 next to it means.
2. **The vocabulary of variation is connectives.** "but", "like", "instead"
   went into `_STOPWORDS` beside "with" and "for" — without them, `iso_shape: B`
   came out of "**b**ut" and `L` out of "**l**ike".
3. **A change fragment is read with the fuzzy resolver only.** `decode_request`
   runs the nomenclature pipeline first and trusts it, which is right for a
   whole RFQ and wrong for a fragment: over "with 0.4 corner radius" the
   pipeline routed to the terminal catch-all family and invented
   `product_family: turning_insert`. Correct here by luck, and a hard gate
   either way.

Plus the residue path: "2001174 but 0.4 corner radius" says what "same as
2001174 but 0.4 corner radius" says, and only the second has a word
`detect_mixed` keys on. The input minus **the identifier that actually
matched** is the change. Stripping every identifier-*shaped* token looked
equivalent and was not — a grade is identifier-shaped, so "2001174 but TN4000
grade" removed its own change and came back as the unvaried product.

### Verified

Nine phrasings of one intent, against the real catalogue. 2001174 is a CNMG
120408 at 0.8 mm; a correct answer for a 0.4 request is a CNMG **120404**:

| Request | Top candidates |
|---|---|
| `same as 2001174 but 0.4 corner radius` | 2559548, 2560926 — both r=0.4 |
| `2001174 but 0.4 corner radius` | same |
| `2001174 with 0.4 corner radius` | same |
| `like 2001174 in 0.4 corner radius` | same |
| `need 2001174 in 0.4 corner radius` | same |
| `2001174 -> 0.4 corner radius` | same |
| `instead of 2001174 give 0.4 corner radius` | same |
| `2001174 but 0.4 nose radius` | same |
| `2001174 but TN4000 grade` | 2045826, 2559490 — both TN4000 |

**9/9.** Before this, all nine returned the reference's own 0.8 mm neighbours
or unrelated square and screw-on inserts. No regression: a bare code and a
code-plus-quantity still resolve `EXACT`, `CNMG 0.8 insert for cast iron` still
decodes, and a bearing still offers nothing.

Gates: pie-parser **468 passed** (was 439), all six steps, corpus 6,717 rows at
full parse rate, byte-identical reruns. pie-portal all seven steps green.

### The evaluation moved, and not all of it upward

Reported in full because the precision drop is real:

| `eval_rfq` engine arm | before | after |
|---|---|---|
| precision | 66.7% | **60.0%** |
| coverage | 64.3% | **71.4%** |
| abstention | 35.7% | 28.6% |
| **wrong-confident** | **21.4%** | **21.4%** |

One case changed: `underspec-wa-no-grade` — *"need cnmg120408, 20 pcs urgent"* —
previously abstained and now answers. Precision fell because the denominator
grew while `correct` did not; the metric that matters did not move.

**Why that case abstained before is the interesting part, and it was not
judgement.** The junk this fix removed — "need", "pcs" and "urgent" decoding
into ISO slots — was depressing the score below the harness's answer bar. With
the junk gone the engine's real state is visible: **all five candidates tie at
0.960**, which is the engine declining to choose. The harness takes the first
of a tie and calls it an answer. See decision 022.

**Not fixed here, deliberately.** Correcting the harness in the same change as
the thing it measures is how a measurement gets tuned into agreement. It is its
own decision, reviewed on its own evidence.

---

## 022 — The RFQ harness counts a tie as an answer
**Status:** OPEN — instrument defect, evidence below · **Report §:** 29

**What it does.** `tools/eval_rfq.py`'s engine arm decides "answered" from the
top candidate's score against `answer_at`. It does not ask whether the ranking
*separated* that candidate from the next one. So a tie is resolved by sort
order and reported as an answer the engine did not make.

**Evidence.** Case `underspec-wa-no-grade` — *"need cnmg120408, 20 pcs urgent"*,
whose own note says two records share the geometry in different grades and
nothing chooses between them. Run against the fixture catalogue today:

```
5000001  combined=0.96  KCK15   CNMG 120408 - KCK15
5000002  combined=0.96  TN2000  CNMG 120408 - TN2000
5000003  combined=0.96  TN2000  CNMG 120412 - TN2000
5000004  combined=0.96  KCK15   SNMG 120408 - KCK15
5000005  combined=0.96  KCP10   CNMG 120404 - KCP10
```

A five-way tie. The engine is abstaining in substance and the harness records
an answer.

**Why it matters beyond one case.** The production consumer already applies the
missing rule: `pie_service._is_discriminating` refuses to auto-select when the
scores do not separate, because *"treating the first of those as the technical
equivalent manufactures certainty the engine never expressed."* The harness
measures a system that does not exist — one more willing to answer than the one
that ships — so **coverage is over-reported and abstention under-reported**, and
by an unknown amount until it is fixed.

**Decision.** Align the arm with the consumer: a tied top is an abstention, not
an answer. `scorecard.py` stays the one definition of what an evaluation counts
(CLAUDE.md §3); this is the *arm's* judgement of answered/abstained, which is
where it belongs.

**Why it is not in the 021 commit.** 021 made this case change, so fixing the
harness in the same change would be adjusting the instrument to agree with the
result it had just produced. Numbers will move when this lands — coverage down,
abstention up, precision probably up — and they should move on their own
evidence, in a change that does nothing else.

---

## 023 — 100k SKUs is the design target; the working set is what exists
**Status:** ACCEPTED — product owner's call, 2026-08-30 · **Report §:** 40 Q1

**Decision.** Engineer for 100,000 SKUs. Scope and measure Phase 1 against the
catalogue that actually exists, and let the real number emerge as attribute
coverage is built.

**What was measured, so nobody re-derives it.** SLS is 15,032–15,996 items
(live check, 2026-08-30, against the census of 15,028 on 2026-08-09); the second
entity is 513; the UPS connector returns the SLS organisation, so there are two
reachable masters and not three. Under 17k today against a 100k target.

**What follows regardless of the real number.** Retrieval moves into PostgreSQL
(decision 003) on the memory evidence alone — the in-process pool costs ~19.7 KB
resident per record across the two copies the singleton holds, so the deployment
model breaks well before 100k whatever the catalogue turns out to be. Nothing in
the phase order depends on settling this; only Phase 1's *sizing* does, by about
a factor of six.
