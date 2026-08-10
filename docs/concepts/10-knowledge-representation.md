# Knowledge representation & ontology

How this system represents "which product is this" and "what is a valid
substitute for it" — and the one property that makes those answers safe to put
in front of a customer.

The headline finding is a negative, and it is worth stating before the
reasoning: **technical equivalence is not treated as transitive anywhere in
either repository.** Nothing takes a closure, in the engine or in the portal.
What follows is the evidence, and what was added so the property stays true.

---

## The question, and why it is the one worth asking

Tolerances compose. Equivalence does not.

`A ≈ B` within tolerance and `B ≈ C` within tolerance does **not** give
`A ≈ C`. With pie-parser's shipped defaults — a ±50% band (`DEFAULT_BAND_FRACTION
= 0.5`) and a TECH threshold of 0.85 — two hops put a 0.4 mm corner radius and a
0.8 mm corner radius in the same class, via 0.6. Both hops score above the band.
Each is individually defensible and would survive review on its own.

The composite is a different insert. It would auto-select as the line's
`supplyCode`, price itself from the books, and go out on a quotation with a
plausible explanation attached to it. That is the specific failure being guarded
against: not a visibly wrong answer, but a confidently wrong one.

This matters commercially rather than aesthetically. A wrong substitution reaches
a customer as a wrong part — a machine down, a rejected delivery, and the kind of
credibility loss a distributor does not get to explain away twice.

---

## The answer: no closure, anywhere

Six paths could have carried one. All six were traced.

### 1. The equivalence search is a star, not a graph

`equivalence/query.py`:

```python
for rec in pool:
    geo = compare_geometry(input_spec, rec.record, self._model)
```

Every comparison's left operand is the **request**. No candidate is ever compared
to another candidate. There is no edge set, no adjacency, no visited set, no
queue, and no union-find anywhere in either repository. `find_equivalents` is
called only from `tools/` and `tests/` — never from inside `equivalence/` — so
there is no recursion and no self-feeding either.

### 2. The band is anchored to the request, structurally

`equivalence/tolerance.py::field_score` always measures `delta / ref` against the
reference, which is always the query. This is why composition is *impossible*
rather than merely *absent*: there is no intermediate for a band to re-anchor to.

### 3. The sourced grade chart does not chain

`equivalence/crossref.py::_CrossrefTable.find(grade_a, grade_b)` is a single
linear scan for a row naming **both** grades, returning the first direct hit or
`None`. It never follows `grade_b` onward to a third grade.

> One legitimate transitivity, named so it is not mistaken for the defect: the
> *shared ISO 513 application group* path is transitive — but that is set
> membership in a partition ("both grades serve group P"), not a tolerance band.
> Transitivity of "same class" is sound. It is also capped at a 0.4 additive
> boost and never gates a candidate in or out.

### 4. De-duplication groups exact rows, not close ones

`query._dedup_key` is an exact tuple over canonical fields (`iso_shape`,
`corner_radius_mm`, `grade`, `description_norm`, …). It collapses byte-identical
duplicate catalogue rows — duplicate MM#s — and nothing else. No tolerance enters
the key, so near-rows never accrete into a class.

This one is worth dwelling on: **class membership *is* a transitive closure.**
0.78 joins 0.80, 0.80 joins 0.82, and 0.78 and 0.82 end up quoted as one product.
A "tolerant dedup" refactor is the most innocent-looking way to introduce the
defect.

### 5. Identity resolution is exact-only

`identity/resolver.py` resolves on material number, catalog number, or a
human-confirmed mapping. The single approximate path, `_candidate_by_prefix`,
requires a **unique** prefix of ≥5 characters and returns `CANDIDATE` /
`NEEDS_REVIEW` — never `SAME_PRODUCT`. Mappings cannot chain either:
`MappingStore.lookup` yields a `target_record_id` that is then resolved against
the *catalog index*, not fed back into the mapping store.

### 6. The one genuine two-hop flow is guarded

`tools/resolve_rfq.py` — the MIXED path, where a reference product is resolved
and *then* matched on ("same as MM# 1913582 but 10 mm"):

```python
if (ident.input_semantics is InputSemantics.MIXED and authoritative_ref
        and split is not None):
```

`authoritative_ref` requires `certainty == "AUTHORITATIVE"` and exactly one
match. So hop one is **exact, zero tolerance**; hop two carries the only band.

**`exact ∘ tolerance` is tolerance. `tolerance ∘ tolerance` is the defect.**

Relax that one conjunct to accept a `CANDIDATE` reference and the defect appears
immediately. It is the highest-value line in either repository for this property.

---

## The portal side: score is policy, not identity

`pie_service._rel_from_score` turns a combined score into TECH / COMPAT /
POSSIBLE using this organization's `equivalence_tech_band` and
`equivalence_compat_band` — commercial policy, read per request from versioned
thresholds. Two organizations may legitimately disagree about the same pair.

Traced forward: `store.build_lines` is the only production `pie_service.resolve`
call site. `rel` flows to `Line.rel` → JSON → the grid, and stops. Nothing feeds a
derived `rel` or `supplyCode` back into another resolution. `select_supply` sets
the code and re-prices from Zoho; it does not re-resolve. There is no
substitutes or alternates table in `domain/models.py`.

### The across-time route, and the two guards that close it

This is the subtle one — the only way transitivity could arrive *between*
sessions rather than within a request.

A confirmed mapping is **asserted identity**. Once written, the engine resolves
that customer's code `AUTHORITATIVE`ly forever — and guard #6 above then
cheerfully derives a requirement from it and ranks equivalents off it. So if a
TECH-band substitution were ever persisted as a confirmed mapping, it would
become an exact reference it never was, and the next "same as their 7781 but
12 mm" would compose two bands. Laundered through the database.

Two narrow conditions prevent it:

- `store._identity_candidate` offers a confirmable code **only** for the engine's
  own single-candidate `NEEDS_REVIEW` proposal — an *exact* catalogue hit
  downgraded for namespace safety, never a scored suggestion;
- `routers.quote._confirm_identity` refuses unless the selected code is exactly
  that candidate.

The intent is already stated in the code: *"Picking a different product is a
substitution on one quote, and filing that as 'their code means this' would teach
the system something the person did not say."*

---

## What was actually wrong: the property was unprotected

No defect. But the property was held by **shape plus three unnamed local
guards**, and nothing failed if one was removed:

- `tests/test_identity.py` asserted `AUTHORITATIVE` twice — on the resolver's own
  output, never on the MIXED branch's *use* of it;
- `backend/tests/` contained **no reference to `identityCandidate` at all**.
  `test_confirmed_mappings.py` exercises the service function directly and never
  reaches the router gate in front of it.

So the fix was regression cover, not a code change. Both repositories now carry
tests that fail if a guard is relaxed, each verified against a deliberate mutant:

| Mutant introduced | Caught by |
|---|---|
| MIXED accepts any reference, not just `AUTHORITATIVE` | `test_a_mixed_input_derives_a_requirement_only_from_an_exact_reference` |
| `crossref.find` chains through an intermediate grade | `test_a_grade_cross_reference_does_not_chain_through_an_intermediate` |
| `corner_radius_mm` dropped from `_dedup_key` | `test_dedup_collapses_identical_rows_but_never_merely_close_ones` |
| `_confirm_identity` ignores which code was picked | `test_a_scored_substitution_is_never_filed_as_identity` |
| `_identity_candidate` widened to any top candidate | `test_only_the_engines_own_single_candidate_proposal_is_confirmable` |

Positive controls are included on both sides, so a refusal cannot pass by its
path simply being dead.

The invariant is now named in both `CLAUDE.md` §1 files, which is the list people
actually read before adding an equivalence feature.

---

## Secondary concepts, ranked against the business

The owner's lens is what improves the whole commercial process, not what is
architecturally interesting.

**Earns its place — interval arithmetic as the *refusal reason*, not a solver.**
The valuable half is ~90% built: `FieldMatch` already carries the input value,
candidate value, subscore and status, and `_explain()` already emits
`different corner_radius_mm=0.4(vs 0.8)`. What is missing is the band:
*"corner radius 0.4 is outside ±50% of 0.8 → [0.4, 1.2]"*. That is a formatting
change. Replacing graded scoring with a constraint solver would rewrite the one
component sitting at 100% on the corpus for no change in what a user sees.

**Earns its place, cheaply — static ambiguity analysis over the routing ladder.**
Best-matched proposal in the set. The ladder is first-match-wins over a *closed*
predicate vocabulary, which is exactly the condition that makes overlap checking
tractable — and the precedent already exists one module over: `ManufacturerResolver`
evaluates *all* claims and raises `MANUFACTURER_CONFLICT` when they disagree,
while `FamilyRouter.route` returns on the first hit and never learns a second rule
would also have matched. A shadowed rule is a silent misroute: the code parses
into the wrong family and every downstream field is confidently wrong. Packs
already carry self-testing examples, so the check is a script over existing data.

**Below the line — the application ontology** (workpiece material → operation →
machine capability → tool class → grade). Architecturally correct as pack data,
and `CLAUDE.md` §3 would accept it. But it is a large data-authoring project whose
payoff is recommendation quality, while the business cannot cross-reference
grades *at all* today: `grade_crossref.csv` ships header-only. Ten sourced rows in
that existing file move quote quality more than an ontology, cost a morning, and
need no code. Build the ontology when customers start asking for tools by
application.

**Below the line — confidence calibration against the golden corpus.** The corpus
is at 100% across eleven families, and `CLAUDE.md` says plainly that it is a
measurement, not a target. Calibrating against a saturated corpus fits to the
ceiling. Revisit when a second manufacturer pack spreads the distribution.

---

## What sounds advanced but is wrong here

- **A materialized equivalence graph or substitution table.** The single change
  that would create the defect. The star topology is *why* this is correct;
  persisting pairwise edges invites the closure.
- **Union-find or clustering catalogue rows into equivalence classes.** The same
  failure with a friendlier name.
- **Replacing graded scoring with a constraint solver.** Rewrites a component at
  100% for no visible change. Take the refusal *reason*; leave the mechanism.
- **An abstraction layer over engine/pack.** pie-parser's `CLAUDE.md` §7
  pre-refuses it and is right. Nothing in this analysis argues past it —
  everything recommended is a test, a doc line, a script, or CSV rows.
- **Treating `_rel_from_score` output as a durable fact about products.** It is
  org-scoped policy read per request. Storing it as a product relationship would
  make one organization's policy another's catalogue, and is the exact route by
  which transitivity arrives across sessions.

---

## Open, and deliberately not done here

**`grade_crossref.csv` is header-only.** Seeding it needs real published
cross-reference charts. Inventing plausible grade equivalences would break
"unknown means unknown" and put a fabricated substitution in front of a customer
— the precise harm this document is about. It needs sourced charts from the
owner, transcribed with `source_ref` cited.

**Equivalence bands carry no version stamp on a quote line.** `Bands` is read per
request and never recorded. Unlike commercial rows, which carry
`thresholds_version` (`CLAUDE.md` §1), a line's `rel` records no band version — so
a quote sent under `tech=0.85` cannot be re-explained once the organization moves
to 0.90. A provenance gap rather than a transitivity one, and a schema change
rather than an afternoon, but it is the repository's own stated standard.

---

## In one line

Equivalence is a star anchored on the request, never a graph — and it now has
tests that fail if anyone makes it a graph.
