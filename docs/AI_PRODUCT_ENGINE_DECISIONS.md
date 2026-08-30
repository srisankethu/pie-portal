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
