# AI Product Equivalence & Substitution Engine — Phase 0 Repository Architecture Report

**Status:** Phase 0 (reconnaissance) complete. **No functional code changed.**
**Awaiting approval before Phase 1.**

Scope of this report: both repositories that make up the system —
`pie-portal` (the platform) and `pie-parser` (the deterministic decoder it
consumes) — assessed against the requirement to become a production-grade
product equivalence and substitution engine for an industrial distributor.

Every number below was measured on this checkout or cited from a file in it.
Where a figure is reproduced from an existing document, the document and its
measurement date are named. Where something is *absent*, the search that
established the absence is given, because an ungrounded absence claim is the
most dangerous kind of finding in a report like this.

---

## Executive summary — read this part

**This is not a greenfield build, and it must not be run as one.** Roughly
half of the target architecture already exists, deliberately designed, with
mechanically-enforced invariants that already encode most of the ten
non-negotiable principles in the brief. Specifically, these are already true
and enforced by tests rather than convention:

| Brief's principle | Already implemented as |
|---|---|
| Unknown is not compatible | Five distinct abstention outcomes on `POST /api/v1/resolve` (`CATALOGUE_UNAVAILABLE`, `ENGINE_ERROR`, `NO_MATCH`, `AMBIGUOUS`, `NEEDS_CONFIRMATION`); `EvidenceSufficiency` enum; "absence of evidence is not a pass" as a CLAUDE.md §1 invariant |
| Correctness over semantic similarity | `equivalence/distance.py` hard gates run *before* any score; grade equivalence is looked up from cited charts, never computed |
| Technical before commercial | Deterministic `commercial/` may not import `ai/`; the layer boundary is asserted by `tests/decision_platform/test_layer_boundaries.py` by parsing imports |
| Traceability | Every decoded fact carries provenance, confidence and a character span; `thresholds_version` is stamped on every computed row |
| Fail conservatively | `_is_discriminating`, the vacuous-match note, the `NEEDS_REVIEW`-only identity confirmation gate, and `tests/test_equivalence_not_transitive.py` |
| No premature ML | `docs/concepts/14-machine-learning.md` already adjudicates 21 candidate ML builds and endorses exactly one |

**Three premises in the brief are not supported by this repository and need a
decision before Phase 1 begins.** They are stated here rather than buried in
§40 because they change what Phase 1 *is*:

1. **"The catalogue contains approximately 100,000 SKUs."** Nothing in either
   repository supports this. The one complete census on record
   (`docs/concepts/01-application-engineering.md`, 2026-08-09) is **15,028
   items** for *one* entity, and a live check against the connector on
   2026-08-30 puts the SLS master at **15,032–15,996** today. The second
   entity's master is **513 items**. The third has **no ERP presence at all** —
   the UPS connector returns the SLS organisation, so "three legal entities" is
   two reachable masters. The decoded catalogue the equivalence engine actually
   queries is **6,717 records**; I rebuilt it on this checkout and confirmed the
   count. 100k may be the ambition across entities and future principals; it is
   roughly **6× today's reachable data**.

2. **"Which products in our catalogue satisfy those requirements"** presumes
   the catalogue carries technical attributes. **It does not.** The `products`
   table has `name`, `uom`, `hsn`, `category` (raw), `manufacturer` (raw),
   `active`, and a nullable pointer to a decoded catalogue record. There is no
   attribute storage of any kind, in any table, for any category.

3. **"Process raw customer RFQs"** presumes document intake. There is **no
   file-upload endpoint, no file storage, no PDF library, no OCR, and no
   document parsing** anywhere in either repository. `IngestedDocument` sounds
   like a document store and is not — it holds `doc_type`/`doc_id`/
   `modified_at` as a *sync cursor* and no content at all.

**The binding constraint is attribute coverage, not algorithms.** Measured
against the SLS item master on 2026-08-09:

| Population | n | Share |
|---|---|---|
| Identity-linked (`Item.sku` → catalogue `record_id`, exact) | 1,420 | **9.4%** |
| Geometry-decodable (shape + edge length) | 2,577 | 17.1% |
| Union of the two | 3,253 | **21.6%** |
| Neither — no technical fact known at all | 11,775 | **78.4%** |

A previous analysis in this repository reached the substitution question,
measured this, and **deliberately deferred it**: *"Substitution via
`equivalence/` — needs grade, which lives entirely inside the 9.4%. Deferred
on the evidence, not on effort."* Building retrieval, ranking and learning on
top of 21.6% coverage would be building nine-tenths of a system that can speak
about one-fifth of the business. **Phase 1 must be attribute decoration, and
its success metric is coverage, not accuracy.**

**Four measured facts that shape the design** (all measured by me on this
checkout, method in §34):

- The equivalence search is an **unindexed full scan of an in-memory pool**.
  It costs 30.8 ms at 6,717 records, 196.6 ms at 33,585, and **555.1 ms at
  100,755** — linear, single-threaded, re-scanned on every query.
- The pool costs **10.2 KB of resident memory per record**, so a 100k-SKU
  catalogue is **≈1 GB per worker process**. `docs/hosting-free-tier.md`
  already warns that "each worker warms its own ~13 MB catalogue copy" against
  a 4 GB recommendation. **The deployment model breaks before the algorithm
  does.**
- **Two reproducible defects put a wrong part on a quote — found by this
  reconnaissance and since fixed** (decision 017; pie-parser `2af5d7e`,
  pie-portal `578add2`). They are worth reading anyway, because what they were
  says where the risk in this programme lies. Asking the
  live engine for `"same as 2001174 but 0.4 corner radius"` returns
  `rel=EXACT`, `supplyCode=2001174` — **the 0.8 mm insert**, auto-selected and
  priced, for a request that explicitly asked for 0.4. The alternatives offered
  beside it are labelled `TECH` at score 0.96 with **no corner radius at all**
  (`r=None`): square inserts and screw-on inserts scored as technically
  equivalent to a CNMG turning insert on a comparison where no dimension was
  comparable. Separately, `"6205 2RS C3 bearing"` — the brief's own worked
  example — returns carbide inserts and endmills at score 1.0.
  **Neither was hypothetical; each was one line to reproduce (§34).** They were
  precisely the failure the repository's own documentation names as the one it
  most fears: not a visibly wrong answer, but a confidently wrong one with a
  defensible explanation attached. And **neither needed new machinery to fix**:
  the engine already stamped the vacuous case and nobody read the stamp, and the
  identity payload could not say whether a matched product was the answer or the
  thing the customer asked to change. **Both were contract defects, not
  algorithm defects** — the engine knows more than its consumers read, and that
  gap is where a wrong part reaches a customer.
- **The comparison is computed, and only its verdict survives.**
  `equivalence/distance.py` produces a per-field `field_matches` list with a
  `gate_reason`, `geometry_score` and a `dimensionally_vacuous` flag. The
  decoded **attribute values do cross** the bridge — a candidate carries ten of
  them — but a grep across the whole portal for those four comparison names
  returns **nothing**, and `SupplyDrawer.tsx` never renders even the attributes
  it does receive. So the brief's `CompatibilityResult` is largely computed
  already and lost in two different places: the *comparison* stops at
  `pie_service`, and the *attributes* stop at the component.

**Recommended shape.** Additive, in this order: decorate the master with
provenanced attributes (Phase 1); move retrieval into PostgreSQL where the
data already lives (Phase 2); *then* the rule engine, equivalence
classification, evidence and ranking. Do not introduce a vector database, a
graph database, a message broker, or an ML ranker. The existing DB-backed
queue, the connector registry, the identity confirmation gate and the pack
schema are the extension points; use them rather than building beside them.

---

# Part A — What exists

## 1. Repository structure

Two repositories, deliberately separated. `pie-portal` vendors `pie-parser` as
a git submodule at `pie-portal/pie-parser` and imports it **in-process**.

```
pie-portal/                     the platform
  backend/app/        93,118 LOC   FastAPI, 21 packages (see §3)
  backend/tests/      67,117 LOC   194 files, 3,419 test functions
  backend/alembic/     7,639 LOC   87 migrations
  frontend/src/       42,924 LOC   React 18 + Vite + TypeScript
  docs/                            58 files incl. 14 numbered concept papers
  scripts/                         20 scripts incl. verify.sh (the gate)
  deploy/, compose*.yaml, railway.json

pie-parser/                     the decoder — offline, no AI, no network, no DB
  engine/     2,532 LOC   generic pipeline; zero manufacturer literals
  packs/      4,302 LOC   all manufacturer/organisation knowledge, as YAML+CSV
  equivalence/ 1,249 LOC  cross-manufacturer comparison
  identity/   1,138 LOC   authoritative index and identity resolution
  resolver/     782 LOC   canonical spec assembly from parsed output
  tools/      5,299 LOC   CLI entry points, pack-authoring kit, eval harnesses
  tests/      5,551 LOC   49 files; the corpus is the contract
  corpora/                1 file, 6,717 rows
```

**Finding — the submodule is not checked out in this environment.**
`ls pie-portal/pie-parser/` is empty; I ran everything against the sibling
clone via `PIE_PARSER_ROOT=/home/user/pie-parser`. This is the exact failure
`CLAUDE.md` §6 records: CI once never fetched pie-parser and all 25
migration-integrity tests died at collection with `PIE corpus not found`,
through eight consecutive merges. `scripts/setup_pie_parser.sh` is the fix and
any new CI job touching the catalogue must run it.

## 2. Frontend architecture

React 18.3 + Vite 5.4 + TypeScript 5.5. Material UI v9 is the design system;
AG Grid v36 is mandated for any table whose row count is set by the size of the
business, via the `platform/DataGrid.tsx` wrapper. TanStack Query v5 for server
state, React Router v7, CASL for ability checks, Zod for schema validation,
Playwright for e2e, Vitest + Testing Library for unit tests.

Routing is a single table in `frontend/src/platform/route.ts` — `PATH` for the
37 flat screens, `PATTERN` for the three that carry an id, and `pathFor()` as
the only writer, so a link and a route are one shape stated once.

The screens that matter for this project:

- **`/quotes` — the Quote Builder** (`QuoteBuilder.tsx`, 753 lines;
  `components/LineGrid.tsx`, 759 lines). This is the substitution surface. A
  line already distinguishes `reqCode` (what the customer asked for) from
  `supplyCode` (what will actually ship), carries `sel: "AUTO" | "USER" |
  "MANUAL"` for who chose it, and `substituted: boolean`.
- **`components/SupplyDrawer.tsx`** — the drawer where a person reviews ranked
  alternatives and picks one. `onSelect(code, manual)` is the human decision.
- **`/identity` (`platform/IdentityScreen.tsx`)** — the existing review queue
  for identity suggestions: link, unlink, accept, reject.
- **`/item-lines` (`platform/viz/Catalogue.tsx`)** — the item master screen.

**The relationship vocabulary already exists end-to-end** and is rendered as a
`StatusChip` with a tone, mapped in `frontend/src/rel.ts`:

```
EXACT · TECH · COMPAT · POSSIBLE · AMBIGUOUS · UNRESOLVED · INCOMPATIBLE · PIE_DOWN · NONE
```

This is nine values against the brief's nine. They are not the same nine, and
reconciling them is a real design decision — see §26.

## 3. Backend architecture

Python 3.11 / FastAPI / SQLAlchemy 2.0 / Alembic. Session-passing, not
ports-and-adapters. **21 packages under `backend/app/`**, and the boundary that
matters is *deterministic vs interpreted*, not domain vs infrastructure:

| Package | Role | May import `ai/`? |
|---|---|---|
| `domain/` | SQLAlchemy models + enums. No decisions. | — |
| `commercial/` | Deterministic computation: metrics, thresholds, policy, quote assessment. **The numbers live here.** | **No** |
| `signals/` | Detectors over persisted rows | **No** |
| `ingestion/` | Zoho + the `erp/` connector registry, normalisation, sync | **No** |
| `state/` | Append-only event log and what is derived from it | **No** |
| `enquiry/` | Inbound demand, raw customer text | **No** |
| `decisions/` | **The seam** — deterministic signal in, AI reading out | Yes |
| `ai/` | Providers, prompts, validation, telemetry | (may not import `commercial/`) |
| `identity/` | Cross-connector record linking. Links, never merges | — |
| `trust/` | Tenant keys, name vault, pseudonyms, audit, erasure | Neither |
| `master_health/` | Item-master quality analysis and decoding | — |
| `routers/` | HTTP mapping and role scoping. Thin | — |
| `messaging/` | The durable job queue | — |
| `observability/`, `attribution/`, `context/`, `equivalence`-adjacent helpers | supporting | — |

This is enforced, not documented: `tests/decision_platform/test_layer_boundaries.py`
parses the imports rather than grepping them.

Singleton entry points worth knowing: `db.py` (one engine, one `Base`, one
`SessionLocal`), `config.py` (every setting including `DATABASE_URL`),
`migration_state.py`, `schema_check.py`, `bootstrap.py`, `clock.py`.

## 4. Database architecture

**85 tables** in `domain/models.py` (4,831 lines), **87 Alembic migrations**.
PostgreSQL in production (`psycopg` 3); SQLite is the dev/test default via
`DATABASE_URL` falling back to `backend/data/platform.db`. Both are exercised
by the gate — migrations run from empty on SQLite *and* on a disposable
PostgreSQL, and RLS and the queue suites run only on PostgreSQL because that is
the only place their behaviour is real.

Tables group into: organisations/auth/subscriptions (12) · customers, products
and item overrides (4) · sales, cost and quoting (11) · signals, decisions and
AI logs (4) · sync and ingestion (5) · approvals and outcomes (8) · identity
(7) · trust, audit and erasure (9) · ERP documents — invoices, bills, POs,
credit notes, payments, stock, locations (18) · state event log (3) ·
queue/leases (2) · inbound enquiry (2).

Conventions that any new table must follow:

- **Org-scoped.** 72 of 74 models carry `organization_id`.
- **Superseded, never mutated,** for anything historical. `BusinessEvent`,
  `ValueEvent` and `InboundLineDisposition` all use a *partial unique index on
  the live rows only* (`sqlite_where`/`postgresql_where` on
  `superseded_at IS NULL`) so a correction writes a second row rather than
  being rejected.
- **Stamped with the policy version that judged it** — `thresholds_version` on
  every computed row; `mh_` for master-health reports; `pie_catalog_version`
  on a catalogue link.
- **Migration in the same commit**, verified against an *empty* database.

## 5. Authentication / authorization

Two kinds of principal, one `authz.Principal` dataclass, deliberately:

- **Session** — `POST /api/v1/auth/login`, PBKDF2 password hashes,
  `user_sessions` rows, logout/logout-all, session listing.
- **API key** — `api_keys` rows for machine callers (`POST /api/v1/resolve`).
  Only a hash is stored, the secret is shown once, `key_id` is the non-secret
  lookup handle, and **the key carries a role** so that the same projection
  code decides what a key's JSON contains as decides what a salesperson's
  screen contains.

Roles are org memberships; entitlements and plan tiers gate features
separately. **Tenant isolation is defence in depth:**

1. Every query filters `organization_id` in Python, and
2. **81 tables carry PostgreSQL row-level security**, applied across five
   migrations (`d1`–`d5rls_*`), `ENABLE` *and* `FORCE`, with the policy reading
   a connection GUC: `organization_id = current_setting('app.current_org', true)`.
   `app/tenancy.py` is the only writer, via `set_config(..., is_local => true)`
   so the tenant is transaction-scoped and travels as a bind parameter.
   **The policies fail closed by construction** — an unset GUC makes the
   predicate NULL, not true, so a connection that never announced a tenant sees
   nothing.

Exact counts, measured from `Base.metadata`: **85 tables, 83 carrying
`organization_id`** (the two that do not are `zoho_credentials` and
`process_leases`), **81 in `EXPECTED_POLICIED`**, and two `CROSS_TENANT_BY_DESIGN`
(`sync_runs`, `zoho_connections`). `docs/postgres.md` still says "70 of the 72
tenant-scoped tables" and is stale by eleven.

**RLS was not in force in either shipped deployment recipe, and that was the
sharpest finding in this section.** The policies bind *the role that issues the
query*, so they do nothing for a connection made as the database owner.
`APP_DATABASE_URL` — the non-bypassing role — appeared in `docs/postgres.md`
and **nowhere else**: not in `compose.yaml`, `compose.dev.yaml`, `railway.json`,
`deploy/production.env.example`, `.env.example`, `docs/hosting.md`,
`docs/hosting-free-tier.md` or `docs/operations.md`. `compose.yaml` served
requests as `POSTGRES_USER`, the owner. So the second layer of defence was
built, tested against a correctly-restricted role in the gate, and **switched on
by no documented deployment** — which the platform's own
`observability/health.py` would have reported as `tenant_isolation UNHEALTHY`
if anyone had read it. Python-side filtering was doing the work alone.

**Closed — decision 019.** `deploy/release.sh` now provisions the role and every
recipe carries the variable; the 27-test RLS suite was re-run against a role
that script created, so the gate's evidence is about what a deployment actually
gets. It is opt-in on `APP_DB_PASSWORD` rather than mandatory, so an existing
`.env.production` keeps working — and the health component is what stops that
being a silent no-op. The paragraph above is kept in the past tense on purpose:
it is the reason the control exists in the recipes, not an open defect.

The other half of authorization is **withholding**, and it is unusually
rigorous. `RESTRICTED_FACT_FIELDS` in `domain/enums.py` is the single list of
cost/margin field names. `commercial.quote_service.project(intel, role)` is the
one function that projects a response for a recipient. Rules carry
`boundary_refs` — the values that place their boundary — and any rule naming
something the recipient may not see is replaced wholesale by a fixed
`APPROVAL_REQUIRED`, because **a predicate a caller can walk is the number it
tests against**. The regression test does not assert on fields; it sweeps the
price across a range and asserts the response does not change at cost.

## 6. Product catalogue implementation

This is the weakest part of the system relative to the brief, and it is worth
being blunt about it.

**`products` has no technical attributes.** The complete column list:

```
product_id, organization_id, connector, connection_id, external_id,
name, uom, hsn, category, manufacturer, active,
pie_record_id, pie_link_method, pie_catalog_version,
source_ref (JSON), created_at, updated_at
```

`category` and `manufacturer` are stored **raw, exactly as the ERP words them**
("Cutting Tools", "KENNAMETAL INDIA LIMITED") and interpreted at read time by
`commercial/categories.py`. That is a deliberate decision with a good reason —
the mapping from a catalogue's words to a line of business is *policy*, it is
versioned, and a value rewritten at sync time could never be re-read under a
corrected map. **Any attribute layer must respect this: normalise on read,
never destroy the source value.**

`pie_record_id` is a nullable pointer to a decoded catalogue record, set only
by `pie_link_method = 'SKU_EXACT'`. Its docstring states the link rate: ~9% of
items link, so **NULL is the common case** and means "not known here", not "no
such product".

The decoded catalogue itself is not in the database. `scripts/build_catalog.py`
runs the parser over the corpus and writes `backend/data/products.jsonl` —
gitignored, rebuilt from source. I rebuilt it: **6,717 products, 13,264,167
bytes, 1.46 s**. `PieService` loads it into process memory and holds it.

**`master_health/` is the closest thing to an attribute pipeline that exists,
and it persists nothing.** It reads an item-master export through a
*column profile* (`profiles/zoho.yaml`, `netsuite.yaml`, `prophet21.yaml` — a
data-driven source-column mapping), decodes each name through the parser, and
gates on a **full ISO slot fill**: `GATED_SLOTS = ("iso_shape",
"edge_length_mm", "corner_radius_mm")`. The gate is well-reasoned — an
ungated family route misroutes badly (an `M3X11` screw routes to
`turning_insert`), while three-slot fill misroutes at 1 row in 2,057. `DecodeOutcome.slots` is the per-item attribute bag this project needs —
**except that it was filtered down to the three gated slot names before
`analysis` ever saw it.** ~~The other eleven the engine decoded~~ — **and the
count in that sentence was wrong in the platform's favour. It is 41, not
eleven.** Measured directly over the 6,717-row catalogue rather than estimated:
the engine emits **44 distinct fact fields**, `chipbreaker` on 22.8% of rows,
`corner_radius_mm` on 14.5%, `flute_count` on 9.2%, `coating` on 4.8%. The gap
between what the engine knew and what the platform stored was four times wider
than this report first said.

**Closed by decision 002's first half**, 2026-08-30. `decode_names` now keeps
every decoded fact and drops 25 named metadata fields, and `master_health`'s
published census comes out byte-identical — verified by hashing the JSON report
before and after over all 6,717 real names, because the gate is deliberate and
moving it would have been a worse defect than the one being fixed. The package
still imports no SQLAlchemy: it decodes, and `app/attributes/` persists.

**A human-maintained taxonomy already exists upstream and is discarded at
ingest.** Live SLS items in Zoho carry custom fields `cf_item_type`
(Insert / Drill / Endmill / Tool Holder / Tap / Measuring Instrument),
`cf_item_category` (Milling / Holemaking / Threading / Toolholding / Grooving &
Parting / General), plus `cf_bin_location` and `cf_catalog_status` — populated
on six of eight items sampled against the live connector.
`ingestion/zoho_client._item_payload` reads **none** of them; it reads
`category_name`/`category`, which its own comment records as set on **0 of 800**
SLS items. So the platform's category column is empty while a per-item
classification somebody actually maintains sits one field away. This is the
cheapest coverage win available in Phase 1 and it needs no decoding at all.

**And there is a socket waiting for it.** `equivalence/catalog.py:ZohoCatalogSource`
already reads `grade`, `iso_shape`, `product_family`, `corner_radius_mm` and
`cutting_dia_mm` from an item-master row. `docs/concepts/01` records that
**none of those fields exist on a real row**, so it contributes candidates with
null geometry that score nothing. The seam was built for a decorated master
that was never built.

## 7. RFQ implementation

Two separate things, and neither is a document pipeline.

**`inbound_lines`** is the demand corpus: `raw_text` (`Text`, not `String(n)`,
because a truncated corpus is a silently wrong benchmark), `channel`,
`customer_ref`, `source_ref`, `received_at`, `captured_at`. Deliberately **no
normalisation at capture and no deduplication** — it is the coverage
denominator that does not condition on success. `InboundChannel` is a closed
enum: `EMAIL · WHATSAPP · PDF · PORTAL · PHONE_NOTE`. **`PDF` is a member: the
schema anticipates document RFQs that nothing can yet read.**

**`inbound_line_dispositions`** records how a line ended, superseded rather
than mutated: `QUOTED · ABSTAINED · NO_STOCK · NO_PRICE · LOST · NO_RESPONSE`.
There is deliberately **no `PENDING`** — a line with no disposition row is
"not answered yet", which must stay distinguishable from "answered with
nothing". `ABSTAINED` is explicitly *ours* — we could not read the requirement.
**That is a ready-made evaluation label for extraction quality.**

Capture is wired — `enquiry.capture` is called from `routers/enquiries.py:158`
and `routers/quote.py:323` — but **it is not usable**, and this matters more
than it first appears. There is **no frontend consumer of `/api/v1/enquiries`
at all**: those five endpoints are reachable only by `curl`, there is no route,
screen or nav entry for inbound demand, and every RFQ interaction requires
first creating a Quote and choosing a customer. So in practice `inbound_lines`
fills only as a side effect of `routers/quote._capture_enquiry`, which by
construction captures only enquiries somebody already chose to work — and
**coverage is therefore not answerable from it**, which is the one question the
table exists to answer. (`docs/concepts/14` §5.21 recorded the table as empty
because nothing called it. Something calls it now; nothing *reaches* it.)

**The actual RFQ path today** is `store.add_rfq(quote, text, ...)`: free text
in, lines out, each resolved through `pie_service`. There is **no extracted
requirement storage** — no table holds "the customer asked for a 25 mm bore".
The requirement exists only transiently inside a `CanonicalSpec` during the
request.

## 8. Existing search

**There is no search infrastructure.** What exists is exact identity lookup
plus a linear scan, and it is important to be precise about the three
mechanisms:

1. **`identity/store.AuthoritativeIndex.lookup_material`** — exact, indexed,
   ~1 ms. `normalize_identifier` upper-cases and trims **and deliberately does
   not strip internal separators**, because separators can be meaningful in a
   catalogue number and exact identity must not be manufactured by
   over-normalising.
2. **`app/identity/matchers.py`** — a *registry* of strategies run
   strongest-first, first hit wins: `by_gstin`, `by_sku`, `by_name`. Each
   returns **the value it matched on, not a score** ("GSTIN 29ABCDE1234F1Z5
   settles an argument; confidence 0.94 starts one"). Its own docstring
   anticipates this project: *"A future fuzzy or AI-assisted strategy fits the
   same shape — it just has to say what it saw."* Note that its
   `normalize_sku` **does** strip `[\s\-_./]`, the opposite of
   `normalize_identifier` — defensible, because they do different jobs, but a
   third normaliser must not be added without deciding which job it is doing.
3. **`equivalence/query.py:find_equivalents`** — loads the entire pool and
   scans it, comparing every record against the request.

Confirmed absent, by exhaustive search across both repositories of `*.py`,
`*.ts`, `*.tsx`, `*.txt`, `*.yaml`, `*.md`:
`rg -niE 'embed(ding)?s?\b|pgvector|\bvector\b|cosine|faiss|hnsw'` and
`rg -niE 'tsvector|to_tsquery|pg_trgm|gin|bm25|elasticsearch|opensearch|meilisearch|typesense|rapidfuzz|fuzzywuzzy|SequenceMatcher|levenshtein'`
return **no implementation hits** — only prose in `docs/concepts/` and the word
"embed" in unrelated English sentences. There is no full-text index, no
trigram index, no vector store, and no fuzzy matcher.

## 9. Existing AI/LLM integrations

Mature, narrow, and correct in shape. `ai/provider.py` defines the entire
contract as `complete(system, user) -> raw_text` with five implementations
(`mock`, `anthropic`, `openai`, `gemini`, `openrouter`); selection is
config-driven with per-organisation BYOK keys encrypted at rest, and
**selection never raises** — a provider that cannot be built falls back to the
mock and says so in `provider_status()`.

The part worth reusing is `ai/contract.py`: the model must return strict JSON,
and a **deterministic gate** then enforces schema validity, that cited facts
and signal ids are subsets of the supplied context, **fact-grounding — every
number in user-facing text must trace to a supplied fact value** — that a
withheld recommendation carries no action text, bounded priority adjustment,
and specificity (a reading of a bundle containing figures must quote one).
Any failure raises and the caller falls back to a deterministic template.

`ai_call_logs` records every call. `AiStatus` distinguishes `PENDING`, `OK`,
`DEGRADED`, `FAILED`, `SUPPRESSED` and `NOT_APPLICABLE` (no model was ever
asked). `EvidenceSufficiency` is `SUFFICIENT | PARTIAL | INSUFFICIENT`.

The hard rule: **AI never computes a number**, enforced by import-parsing.

## 10. Existing embedding/vector infrastructure

**None.** See §8 for the searches. The only trace is analysis:
`docs/concepts/14-machine-learning.md` §5.17 endorses embedding-based candidate
generation *scoped strictly to recall* — "an embedding model must never
*answer* where the grammar declines" — and `scripts/measure_learnability.py`
carries a placeholder that reports the shortlist as `UNKNOWN`.

## 11. Document processing

**None.** `pip` dependencies for the whole backend are: `fastapi`, `uvicorn`,
`pydantic`, `SQLAlchemy`, `alembic`, `psycopg`, `cryptography`, `pyyaml`,
`openpyxl`, `pytest`, `httpx` (plus `ruff`, `pytest-xdist`, `cffi` for the
gate). No `numpy`, no `pandas`, no `pypdf`/`pdfplumber`/`PyMuPDF`/`pdfminer`,
no `pytesseract`, no image library, no ML framework. `openpyxl` is present
only because the parser reads XLSX.

There **was** no file-upload endpoint and no file storage backend of any kind;
`ingested_documents` still holds no content and is unrelated (it is a Zoho fetch
cursor). **Decision 012 reversed that, explicitly**, and this paragraph is kept
in the past tense because the reversal was supposed to be argued rather than
assumed and the argument is the record.

`master_health/__init__.py:11` stated the absence in terms: *"There is no
`UploadFile` and no multipart handler anywhere in `backend/app`, and
`python-multipart` is not installed. Adding one is a dependency decision and a
new attack surface, and it buys nothing a path argument does not already give a
person running a diagnostic."* For a diagnostic CLI that reasoning holds and
still holds — that package still takes a path. For customer RFQs arriving as
PDFs it does not, because a salesperson cannot pass a path argument.

What was built, and what it cost: `enquiry/documents.py` receives and retains
and reads nothing — no PDF is parsed, no spreadsheet opened — and the one place
it looks inside a container it does so without decompressing. Bytes are Fernet
ciphertext under the tenant DEK, so `trust/erasure.erase` reaches them by
destroying the key, which is the only deletion that also reaches the backups.
The dependency is `python-multipart`. The surface is priced in §33 and in
decision 032: four refusals with distinct statuses, a size ceiling checked both
before and during the read, an archive-ratio check on the central directory, and
a download that serves `application/octet-stream` with `attachment` and
`nosniff` set **by the application** — because `deploy/Caddyfile` sets those
headers and the free-tier topology has no Caddy at all.

## 12. Existing data imports

`ingestion/` with a **declarative connector registry** (`ingestion/erp/base.py`).
A connector declares a `ConnectorSpec` and generic code — the connect
endpoints, the connect form, `sync.get_source` — reads the registry and
**never branches on a connector key**. Six sources: Zoho Books (the primary,
three legal entities) plus NetSuite, Dynamics 365 BC, Acumatica, Prophet 21 and
Sage.

`READ_STAGES` is a closed vocabulary of eleven: `contacts, vendors, items,
invoices, bills, customer_payments, vendor_payments, sales_orders, quotes,
purchase_orders, users`. `WRITE_STAGES` is separate and currently
`("sales_quotes",)`, because reading and writing a record kind are different
grants. Permissions declare which stages they cover and a test holds the claim
against the implementation both ways.

**The product row is the ceiling for every connector, not just for Zoho.**
`normalize_product` builds `ProductIn(external_id, name, uom, hsn, category,
manufacturer, active, source_ref)` and nothing else, so no connector can supply
a technical attribute even if its source system holds one. Coverage is uneven
below that: Zoho is the only source supplying `hsn` and `manufacturer`;
NetSuite and Prophet 21 supply only id, name, SKU, status and item type. A
source is a duck-typed Protocol and optional stages are probed with `hasattr`,
so **a source missing a stage produces no rows and no error** — silence, not a
failure.

`ingestion/jobs.execute_sync` **commits at each phase boundary** rather than
holding one long transaction — both for lock contention and because a flush
nobody else can read is not progress reporting.

**What the commercial ranking inputs actually look like today**, because
Phase 8 depends entirely on this and three of the six are weaker than they
sound:

| Input | Persisted? | Where | Caveat |
|---|---|---|---|
| Stock | **Yes** | `stock_snapshots` (on-hand, available, reorder level, purchase rate), one row per item per day, upserted | Skipped entirely when all three stock keys are `None` |
| Warehouse | **Partly** | `locations`, `stock_location_snapshots` | **Zoho only** — no ERP connector implements the location stages |
| Supplier | **Yes** | `vendors`, `cost_records.vendor_id` | — |
| Cost | **Yes** | `cost_records.unit_cost` (bill-line grain), `stock_snapshots.purchase_rate` | Manager scope only |
| **Selling price** | **No** | — | **Not persisted on `products` at all.** Read live per quote line from Zoho (`get_item` → `rate`) |
| **Lead time** | **No** | — | Not stored. `PurchaseOrderDoc.expected_date` is documented as blank on effectively every order; `insight/supply.py` derives a lead time only from `received_on − ordered_on`, behind a floor of three receipts |

So of the brief's commercial signals, **lead time and price are not available as
persisted facts**, and warehouse is available for one connector out of six.
Ranking on them requires either persisting them or accepting a live call per
candidate — which at ten candidates per line is a per-quote latency decision,
not a modelling one.

## 13. Existing integrations

Zoho Books and Zoho Inventory via OAuth (`oauth_states`, encrypted
`zoho_credentials`, shareable across connections); the five US ERP connectors
above; the public resolution API as an *outbound* integration surface
(`POST /api/v1/resolve` with a machine-readable contract served unauthenticated
at `GET /api/v1/resolve/openapi.json`, generated from the routes so it cannot
drift); and four LLM providers.

## 14. Deployment architecture

Docker Compose for local, Railway for hosting (`railway.json` with a
`preDeployCommand` of `alembic upgrade head`), PostgreSQL as the production
database, Vercel for the frontend (`frontend/vercel.json`).
`docs/hosting.md` recommends 4 GB; `docs/hosting-free-tier.md` documents
running below that and already flags that **each worker warms its own ~13 MB
catalogue copy**.

The queue worker is in-process (`app/worker.py` starts a thread), with
`process_leases` ensuring only one holder runs scheduled work across processes.

## 15. Testing architecture

**3,419 test functions in 194 files.** `tests/decision_platform/` holds 169 of
them; `tests/incentive_engine/` 6; `tests/live/` 3. `pytest-xdist` with
per-worker database isolation cuts the suite from ~7m40s to ~2m20s.

`scripts/verify.sh` (351 lines) is the **single definition of "verified"** —
`make verify` runs it, CI runs it, and the Claude Code stop-hook checks against
it. Seven steps, and it runs all of them and reports every failure at the end
rather than stopping at the first:

1. `ruff check .` (rule set pinned in `ruff.toml`, version pinned in `requirements-dev.txt`)
2. the §1 layer invariants (import-parsed, not grepped)
3. the backend suite, in parallel
4. frontend tests, `tsc -b`, and the production build
5. `alembic upgrade head` **on an empty SQLite database**, then the drift test and single-head check
6. the same again on a disposable **PostgreSQL**, plus the row-level-security suite against a role that is neither superuser nor owner, plus the two queue suites
7. the **restore drill** — `pg_dump`, restore into an empty database, then compare every row, every `Decimal` money sum, every audit chain and every erasure receipt

`pie-parser` has its own `scripts/verify.sh`: lint, the full suite with the
golden corpus, the two §1 invariants (engine free of manufacturer/family/org
literals, every parser package free of networked imports — both AST-parsed),
the corpus parsed **twice and compared byte for byte**, and corpus health
(nothing quarantined, no family below 100%).

## 16. Existing reusable components

Ranked by how much they save this project:

1. **The pack schema** (`pie-parser/packs/`) — a two-layer, versioned,
   lint-checked, YAML-declared ontology with self-testing examples. See §23.
2. **The engine's three extension registries** — `SLOT_PROCESSORS` (12 typed
   slot kinds), `_VALIDATOR_BUILDERS` (validator kinds; an unknown kind is a
   load error), `_PREDICATE_KINDS` (routing predicates). New mechanisms go here
   generically and packs supply the specifics.
3. **`GeometryComparison` / `FieldMatch`** — a per-field comparison with a
   status, a gate reason and a vacuity flag. This *is* `CompatibilityResult`,
   minus persistence and minus directional rules.
4. **`GradeEvidence`** — score, kind, label, `source_ref`, relationship,
   confidence, backed by CSVs where every row must cite a real published chart.
   This is the evidence model, and it is a *citation* model rather than a RAG
   model, which is the right shape for this domain.
5. **`CanonicalSpec` + `Provenance`** (`resolver/spec.py`) — `DECODED |
   SOURCED | INFERRED | VERBATIM`, answering "how do we know this?" rather than
   "how sure do we feel". This is the requirement contract.
6. **The identity confirmation gate** — `store._identity_candidate` offers a
   confirmable code *only* for a single-candidate `NEEDS_REVIEW` proposal;
   `identity.service.confirm_proposed_identity` refuses anything else; both the
   Quote Builder and the public API go through the one function.
7. **`ai/contract.py`'s fact-grounding gate** — reusable verbatim for evidence
   summarisation.
8. **The DB-backed queue** — conditional-`UPDATE` claim, heartbeat,
   `reap_stale`, bounded exponential retry, `DEAD_LETTER` with the last error
   attached and visible in `/api/health`. Adequate for indexing jobs as-is.
9. **The connector registry** and **`master_health` column profiles** — two
   independent, working precedents for data-driven source mapping.
10. **The `Line` / `Candidate` contract**, already end-to-end from Python
    dataclass through JSON to the TypeScript interface, already carrying
    `availUnknown` (explicit unknown), `candidates[]`, and `sel: AUTO|USER|MANUAL`.

## 17. Existing technical debt relevant to this project

- **Two defects that put a wrong part on a quote — found here, and since
  fixed** (decision 017; pie-parser `2af5d7e`, pie-portal `578add2`). They are
  described in the present tense below because that is the behaviour this
  reconnaissance measured, and kept because they are the clearest evidence for
  why §25's compatibility work is the shape it is. Neither is an open defect:

  1. **The MIXED path asserts the reference product as the answer.**
     `"same as 2001174 but 0.4 corner radius"` returns `rel=EXACT`,
     `supplyCode=2001174` — the **0.8 mm** insert. The resolution keeps
     `outcome=AUTO_MATCH` with an `AUTHORITATIVE` match while attaching the
     effective-requirement suggestions, so the line auto-selects and prices the
     product the customer asked to *vary*. The "but 0.4" is read, acted on for
     the suggestion list, and then discarded for the selection.
  2. **Vacuous comparisons are labelled `TECH`.** The alternatives beside it
     score 0.96 as `TECH` with `corner_radius_mm = None` — square inserts and
     screw-on inserts held technically equivalent to a CNMG turning insert on a
     comparison where no dimension was comparable. The vacuity note that exists
     for exactly this case is rendered by `SupplyDrawer.tsx` **only when the
     candidate list is empty**, so it never appears in the case it describes.

  Together these were the failure `docs/concepts/10` names as the one worth
  guarding against: *"not a visibly wrong answer, but a confidently wrong one."*

  **What fixed them, and what it says about the architecture.** Neither needed
  new machinery. The engine already stamped `dimensionally_vacuous` on exactly
  the vacuous case, with a docstring saying consumers must be able to see it —
  nobody read it. And the identity payload could not distinguish "this is the
  product" from "this is the product you asked to change", so `run` now states
  an `identity_role` on every exit, seeded to the refusing value. **Both were
  contract defects rather than algorithm defects**, which is the single most
  useful thing this reconnaissance learned about where the risk in this
  programme actually lies: the engine knows more than its consumers read, and
  the gap between the two is where a wrong part reaches a customer.

  A **third** defect surfaced by the same query is still open (decision 021):
  the override decode does not reliably turn "but 0.4 corner radius" into
  `corner_radius_mm = 0.4`, so the derived requirement is partly fiction. It is
  now *visible* — the line abstains and says no dimension could be compared —
  rather than hidden behind a quoted reference.

- **The comparison stops at `pie_service`; the attributes stop at the
  component.** `field_matches`, `gate_reason`, `geometry_score` and
  `dimensionally_vacuous` are read nowhere in the portal (grep: zero hits). The
  decoded attribute *values* do cross — a candidate carries ten of them, and
  `Candidate.attributes` is declared all the way into `types.ts` — but
  `SupplyDrawer.tsx` never renders them. Meanwhile `POST /api/v1/resolve`
  emits per-slot attributes with provenance, confidence, `read_from` and
  character spans. **The public API is richer than the screen**, which is the
  wrong way round and is the single most useful thing to fix early (§40).

- **The layer-boundary invariant covers six packages, not the codebase.**
  `DETERMINISTIC = ("attribution", "commercial", "enquiry", "ingestion",
  "signals", "state")`. `identity/`, `trust/`, `context/`, `master_health/`,
  `messaging/`, `observability/`, `routers/` and the top-level `pie_service.py`,
  `store.py` and `resolution.py` are unconstrained. **A new `app/equivalence/`
  or `app/compatibility/` package would sit outside the invariant until its
  name is added to that tuple** — which is a one-line change nobody will think
  to make.
- **Hard gates are hardcoded and cutting-tool-specific.**
  `HARD_GATE_FIELDS = ("product_family", "iso_shape", "insert_polarity")` is a
  module constant in `equivalence/distance.py`, not pack data. A second product
  category cannot express its own gates.
- **Tolerance is symmetric and unsigned.** `ToleranceModel` scores
  `|ref − cand| / ref` against a ±50% band with hardcoded per-field weights for
  cutting-tool dimensions. Most industrial compatibility is **directional** —
  a 12 A contactor may replace a 10 A one and not the reverse — and the model
  cannot express that. This is the single largest engine gap (§25).
- **The combined score exceeds the band range it is compared against.**
  `combined = geometry_score + 0.15 × grade_score`, so it ranges over
  `[0, 1.15]`, while `equivalence_tech_band = 0.85` and
  `equivalence_compat_band = 0.60` are stated as if on `[0, 1]`. I observed
  live scores of 1.15. It is not a bug today, but it means the bands are not
  the calibrated quantity they look like.
- **Vacuous matches are handled by a note and a tie-heuristic, not a verdict.**
  `compare_geometry` skips a hard gate when *either* side is `None`
  (`distance.py:131`), so a candidate with unknown `iso_shape` is not gated
  out. `dimensionally_vacuous` and `_is_discriminating` catch the common case,
  but the protection is downstream of the score rather than in it.
- **Normalisation is brittle at the boundary.** Live: `"CNMG 120408 TN2000"`
  resolves; `"CNMG120408"` returns `UNRESOLVED` with zero candidates.
- **The catalogue is a process-resident artefact,** rebuilt by a script,
  gitignored, loaded per worker, with no incremental update path.
- **`docs/` has drifted in one place worth naming:** `docs/concepts/14` §5.20
  cites `IngestedDocument` as the basis for PDF extraction. It stores no
  document content.
- **The API surface has no conventions for a new list endpoint to inherit.**
  Only **8 of 203 routes declare a `response_model`**, so the generated OpenAPI
  describes almost nothing; pagination is ad-hoc (`total` in some routers,
  `has_more` in others, absent in the rest, with no shared `Page` model); and
  response casing is bifurcated — camelCase for anything feeding the Quote
  Builder's `Line`, snake_case everywhere else, with nothing enforcing or
  documenting the split.
- **`insight.py` is 5,326 lines and 55 endpoints — 27% of every route in the
  platform.** It is the path of least resistance for a 56th and the wrong
  place for it.
- **`RESTRICTED` is declared four separate times** (`commercial/references.py:28`,
  `signals/quote_context.py:25`, `context/quote_bundle.py:30`, and
  `commercial/incentive.py:70` as a different concept), with
  `domain/enums.RESTRICTED_FACT_FIELDS` a fifth, field-name-based statement of
  the same policy having exactly one consumer. A new subsystem must pick one,
  not add a sixth.
- **No batch resolve.** `POST /api/v1/resolve` caps input at 512 characters and
  takes one line per request; `POST /quotes/{id}/intake` is the only multi-line
  door and it requires a quote. `GET /api/v1/enquiries` refuses above
  `_EXPORT_CEILING = 50_000` rather than paging, and its own docstring says
  paging is the right answer.

---

## Interlude — decisions this repository has already recorded

`docs/concepts/14-machine-learning.md` (1,291 lines) adjudicates twenty-one
candidate ML builds on measured grounds, and `docs/concepts/10-knowledge-representation.md`
settles the ontology and transitivity questions. **These are not opinions to be
re-litigated; they are findings with methods attached.** The brief overlaps
them at seven points:

| The brief asks for | Recorded verdict | Standing |
|---|---|---|
| Embedding candidate generation | **§5.17 yes — "scoped to recall"**; an embedding may propose, never answer where the grammar declines | **Consistent** |
| PDF/OCR RFQ intake | **§5.20 yes — "buy rather than build"** | **Consistent** |
| Hybrid retrieval | not refused, but bounded by §4.4 — *a score is not evidence*; a strategy must return the value it matched on | **Consistent, with a constraint** |
| ML ranking | bounded by §4.3 — feature scope must equal reader scope (two models, not one with a projection); and the label floors are not met | **Deferred on measurement** |
| Learned substitution | **refused** (§5.13–5.14, and doc 10's six traced paths) | **Contradicted** |
| A category/application ontology | **below the line** — "architecturally correct as pack data" but a large authoring project whose payoff is recommendation quality, while the business cannot cross-reference grades at all because `grade_crossref.csv` ships header-only | **Contradicted as scoped** |
| ~100,000 SKUs | contradicts nothing recorded, but **has no supporting measurement** — the largest census in either repository is 15,028 rows | **Unsupported** |

Two measured findings underneath these are worth carrying forward, because
they explain *why* attribute coverage is the binding constraint:

- **Confidence is limited by an absent field, not by engine judgement.**
  `docs/concepts/13` suppressed the `grade` column across the same 6,717 corpus
  rows — same pipeline, same pack, population and ordering identical by
  construction — and mean `row_confidence` fell 0.9444 → 0.0000. **100% of the
  gap is attributable to the absent field and 0% to the engine's judgement**,
  measured field-by-field rather than inferred. `critical_slots = ("grade",)`,
  so grade contributes nothing to a row that has one and acts purely as a veto
  when absent.
- **Losing grade loses the axis this project needs.** In the same paired run,
  50.4% of emitted attribute values disappear, and not a random half: geometry
  survives (33,630 values both sides) while the entire material-and-application
  axis collapses (34,370 → 123).

The honest reading for this programme: the engine is not failing on messy
input, it is **declining for want of a fact**. That is a data problem, and it
is why §39 puts attribute decoration first and puts ten sourced rows in
`grade_crossref.csv` before any of it.

---

# Part B — What is proposed

The governing choice: **`pie-parser` remains the decoder, `pie-portal` becomes
the decider.** The parser knows how a part number is *encoded* and stays
offline, deterministic and pack-driven. The portal knows what this
organisation *sells*, what it *has*, what it *costs*, and what a person
*decided* — and that is where equivalence policy, ranking and feedback belong,
because all four are org-scoped, versioned and auditable there and none of them
can be in a repository whose invariant is that it holds no organisation
knowledge.

## 18. Proposed product intelligence architecture

Six new concerns, each a package, each additive:

```
RAW RFQ (text / file)
  │
  ├─ ingestion/documents/   NEW  upload, store, parse, OCR; page + span provenance
  ▼
enquiry/  (exists)          raw_text preserved verbatim, InboundLine
  │
  ├─ extraction/            NEW  text/tables → RFQLine → requirements
  │                              deterministic grammar first; LLM only where it declines
  ▼
CanonicalRequirement        EXTEND resolver/spec.py's CanonicalSpec, persisted
  │                              every value carries Provenance + confidence + span
  ▼
retrieval/                  NEW  STAGED candidate generation, in PostgreSQL
  │   exact → normalized → lexical (tsvector/trigram) → structured attribute filter
  │   100k → 100–500
  ▼
compatibility/              NEW  deterministic rule engine, data-driven
  │   MATCH | ACCEPTABLE | INCOMPATIBLE | UNKNOWN, per attribute
  │   100–500 → 10–50   (hard failures and criticals removed here)
  ▼
equivalence/                NEW  classification from compatibility + evidence
  │   the nine classes, with confidence and explanation
  ▼
evidence/                   NEW  citation store; datasheet page, chart row, span
  ▼
ranking/                    NEW  deterministic weighted; technical validity is a
  │                              PREREQUISITE, not a feature with a weight
  ▼
commercial/  (exists)       stock, lead time, price, margin, projection by role
  ▼
Recommendation → SupplyDrawer (exists) → human decision
  ▼
feedback/                   NEW  every human action as an event; training substrate
```

**Two boundaries are load-bearing and must not be blurred:**

- **Technical filtering completes before commercial ranking begins.** A
  candidate that fails a critical rule is removed, not down-weighted. A weight
  can be outvoted; a gate cannot.
- **A score is never an identity.** `pie_service._rel_from_score` already turns
  a score into a relationship *under this organisation's versioned policy*.
  That relationship is true of this quote under this policy, not of the
  products. Nothing derived may be persisted as a product relationship or fed
  back in as an input — the rule that keeps equivalence non-transitive.

## 19. Proposed database changes

All new tables carry `organization_id`, join the RLS migration list, and are
covered by `test_row_level_security.py`. Ontology and rules are versioned by
content hash in the `thresholds_version` idiom. Nothing here replaces an
existing table.

**Ontology (small, policy, versioned)**

| Table | Purpose | Key columns |
|---|---|---|
| `ontology_versions` | content hash of a whole ontology revision | `ontology_version` (`ont_…`), `created_at`, `note` |
| `ontology_categories` | the category vocabulary | `category_key`, `label`, `parent_key`, `ontology_version` |
| `ontology_attributes` | what attributes a category has | `category_key`, `attribute_key`, `datatype`, `unit`, `criticality` (`CRITICAL`/`MAJOR`/`MINOR`), `ontology_version` |
| `ontology_rules` | how one attribute is compared | `category_key`, `attribute_key`, `rule_kind`, `params` (JSON), `direction`, `severity`, `ontology_version` |

**Product knowledge (large, factual, provenanced)**

| Table | Purpose | Notes |
|---|---|---|
| `product_attribute_values` | **the core new table** — one row per (product, attribute) | `original_value`, `normalized_value`, `value_num`, `value_text`, `unit`, `source`, `source_type`, `document_id`, `page`, `extraction_method`, `normalization_method`, `confidence`, `ontology_version`, `decoder_version`, `created_at`. Superseded, never mutated. |
| `product_aliases` | alternate codes and customer part numbers | `alias`, `alias_kind`, `source`, `confidence` |
| `product_relationships` | **asserted only** — supersession, accessory, confirmed equivalent | `from_product_id`, `to_product_id`, `kind`, `evidence_id`, `confirmed_by_user_id`, `confirmed_at`. **No derived row may ever be written here.** |
| `product_documents` | datasheets and catalogue pages | `storage_ref`, `sha256`, `mime`, `pages`, `licence_note` |
| `document_chunks` | retrievable evidence spans | `document_id`, `page`, `char_start`, `char_end`, `text`, `table_context` |
| `product_embeddings` | **Phase 2b only, if justified** | `representation_kind`, `embedding_model`, `embedding_version`, `source_representation_hash`, `generated_at`, vector |

**RFQ and decision trace (append-only)**

| Table | Purpose |
|---|---|
| `rfq_documents` | an uploaded RFQ file: storage ref, sha256, mime, page count |
| `inbound_line_requirements` | the extracted requirement per line: `attribute_key`, values, `unit`, `provenance`, `confidence`, `char_start`/`char_end`, `page`, `extraction_version`. Superseded on human correction. |
| `resolution_runs` | one engine execution: input hash, ontology/rule/decoder/catalogue versions, timings |
| `resolution_candidates` | what retrieval returned: `run_id`, `product_id`, `stage`, `retrieval_score`, `rank` |
| `compatibility_results` | `run_id`, `product_id`, `status`, per-attribute verdicts (JSON), `critical_failures`, `unknown_attributes`, `rule_version` |
| `equivalence_results` | `run_id`, `product_id`, `classification`, `confidence`, `explanation`, `evidence_ids`, `ontology_version` |
| `evidence` | `kind`, `document_id`, `page`, `span`, `quote`, `source_ref`, `verified_at`, `verified_by` |
| `feedback_events` | every human action on a recommendation, append-only |

**Naming caution, checked:** none of the eighteen proposed names collides with
an existing table. But `product_documents` and `document_chunks` will sit
alongside `ingested_documents` (a *sync cursor*, no content) and
`quote_documents`, and a reader who confuses the first with the second will
reach for a document store that is not one. Either name the new ones
unambiguously (`rfq_documents`, `datasheet_documents`) or rename
`ingested_documents` to what it is.

**Existing tables changed: none structurally.** `products` gains no attribute
columns — attributes live in `product_attribute_values` precisely so a
category-specific schema does not become 200 sparse columns.

**Extensions required on PostgreSQL:** `pg_trgm` (ships with PostgreSQL) in
Phase 2. `pgvector` only in Phase 2b and only against a measured recall gap —
and its availability depends on who controls the deployed database, which is
an open question (§40).

## 20. Proposed APIs

Additive, matching the existing conventions exactly: `/api/v1/` prefix,
role-declaring dependency, projection through `quote_service.project` for
anything commercial, five-way abstention rather than a null.

```
POST   /api/v1/rfq/documents                     upload an RFQ file
POST   /api/v1/rfq/documents/{id}/extract        queue extraction
GET    /api/v1/rfq/{id}/lines                    extracted lines + requirements + confidence
PATCH  /api/v1/rfq/{id}/lines/{lid}/requirements human correction (writes feedback)
POST   /api/v1/products/search                   staged retrieval; filters + text
GET    /api/v1/products/{id}                     product + attributes + provenance
POST   /api/v1/products/{id}/equivalents         classified, evidenced, ranked
POST   /api/v1/products/attributes/import        CSV/XLSX attribute decoration
POST   /api/v1/equivalences                      ASSERT a confirmed relationship (gated, §26)
POST   /api/v1/feedback                          a human decision as an event
GET    /api/v1/recommendations/{rfq_id}          the ranked outcome for an RFQ
GET    /api/v1/ontology/categories               the ontology, versioned
```

**Extended, backwards-compatibly** (new optional fields only, never a changed
meaning): `GET /api/v1/quotes/{qid}/lines/{lid}/options` — the existing supply
drawer feed — gains `compatibility` and `evidence` per candidate.
`POST /api/v1/resolve` gains the same, behind an explicit version bump in its
generated OpenAPI contract.

## 21. Proposed background jobs

All on the **existing** `queued_messages` queue — conditional-`UPDATE` claim,
heartbeat, `reap_stale`, three attempts with 30 s→900 s backoff, `DEAD_LETTER`.
No new infrastructure is needed, but the queue is **not** adequate unchanged:
it runs a **single-threaded worker loop per process draining messages
sequentially**, with two topics, **no chunking, no streaming, and no per-job
progress** other than what `SyncRun` reports for a sync. A 100k-row decode
submitted as one message would occupy the only worker for the duration and
report nothing while it ran. The additive fix is to **chunk by product range
and carry a progress row**, following `execute_sync`'s per-phase commit — not
to replace the queue.

| Job | Trigger | Idempotency |
|---|---|---|
| `decode_master` | after an `items` sync; manual | per-product content hash; skips unchanged rows |
| `extract_document` | on upload | document sha256 |
| `reindex_lexical` | after attribute or catalogue change | rebuild-in-place, per product |
| `embed_products` *(2b)* | after attribute change | `source_representation_hash` — never re-embeds an unchanged representation |
| `rebuild_catalogue` | on pack/corpus version change | ruleset checksum |
| `eval_run` | manual, and on rule/ontology change | run id from input hashes |

Each commits at phase boundaries (the `execute_sync` precedent) so progress is
visible while the job is still running.

## 22. Proposed indexing / embedding architecture

**Staged retrieval, in PostgreSQL, in this order — and stop as soon as recall
is adequate:**

1. **Exact identity** — `AuthoritativeIndex` and `products.external_id`. ~1 ms.
   Already exists.
2. **Normalized part number** — a separator-insensitive functional index.
   Must be a *third* named normaliser only if it is genuinely a third job;
   otherwise reuse `matchers.normalize_sku` (§8).
3. **Lexical** — `tsvector` over a composed technical description plus
   `pg_trgm` on codes. Ships with PostgreSQL; no dependency; handles
   misspelling and word order.
4. **Structured attribute filter** — an indexed join on
   `product_attribute_values` for the requirement's critical attributes. This
   is the highest-precision stage and it depends entirely on Phase 1.
5. **Vector — Phase 2b, conditional.** Only if 1–4 leave a *measured* recall
   gap on the evaluation set. `docs/concepts/14` §5.17 already endorses this
   scoped strictly to recall: an embedding may propose, never answer.

**Embedding representations, if built, are separate and small** — a technical
representation ("Deep groove ball bearing, bore 25 mm, OD 52 mm, width 15 mm,
double rubber seal, C3 clearance"), an application representation, and a
document-chunk representation. Never one embedding of every column.
`source_representation_hash` makes re-embedding incremental.

**The memory finding governs the physical design.** At 10.2 KB/record resident,
a 100k catalogue is ~1 GB per worker. Candidate generation must therefore
happen **in the database**, not in a process-resident pool. The parser's
in-memory pool stays what it is — the decoder's own working set — and stops
being the retrieval index.

## 23. Proposed ontology architecture

**Data-driven, versioned, and split along the line the repositories already
draw** — this is the most consequential design decision in the report:

| Layer | Holds | Lives in | Varies with |
|---|---|---|---|
| **Nomenclature** | how a manufacturer encodes a part number: families, grammars, slots, lookups | `pie-parser/packs/nomenclature/<maker>/` | the manufacturer |
| **Attribute vocabulary** | what attributes a category has, their datatype and unit | `pie-parser/packs/` — industry/standards fact | the industry |
| **Comparison policy** | criticality, tolerance bands, directionality, thresholds | **`pie-portal`**, org-scoped and versioned | **the organisation** |

The last row is the important one and it follows from the repository's own
rules rather than from preference. `docs/concepts/10-knowledge-representation.md`
establishes that a band is *commercial policy*, read per request, versioned,
and that two organisations may legitimately disagree about the same pair. A
tolerance band therefore cannot live in a pack, because a pack is inherited
byte-for-byte by every organisation selling that manufacturer. Equally,
`bore_mm` is not organisation knowledge and must not be typed twice.

**Bind to published standards; do not author a rival.** This repository's own
venture review lists "product ontology / technical knowledge graph" among the
things founders mistake for a moat — *"Ontologies here are published standards
(ISO 1832, ISO 13399, DIN, ANSI, ETIM). 'Our knowledge graph' describes a
maintenance liability with good branding"* — and `docs/concepts/10` places a
full application ontology below the line. Both are right, and the way to
satisfy the brief without contradicting them is to make
`ontology_categories` / `ontology_attributes` a **binding to ETIM classes and
ISO 13399 property identifiers**, not a bespoke vocabulary. The rows are then
maintained by the standards body, `attribute_key` is a published identifier a
supplier feed can be matched against, and what this organisation owns is only
the part that is genuinely its own: **criticality and tolerance policy**. What
is being built is a *rule set over a published vocabulary*, which is a
different and much smaller thing than a knowledge graph.

Adding a category (bearing, contactor, sensor) is then: **pack data + at most a
few new generic engine mechanisms.** The engine's registries make this concrete.
Today it has 12 slot types (`numeric_mm`, `dimension_mm`, `inch_mm`,
`tenths_mm`, `decimal_comma_mm`, `hundredths_mm`, `numeric_inch`, `integer`,
`degrees`, `token`, `token_lower`, `flag`), so **units are already in the type
system** — the pattern generalises. What a bearing or a contactor would need
that does not exist: electrical and physical unit types (`volts`, `amps`,
`watts`, `newtons`, `celsius`, `bar`, `rpm`), an `enum` slot bound to a lookup,
and a `ratio` type. Those are ~40 lines in `SLOT_PROCESSORS` and are exactly
the kind of generic mechanism `pie-parser/CLAUDE.md` §3 says belongs in the
engine.

## 24. Proposed RFQ architecture

```
file / email / WhatsApp / pasted text
  → rfq_documents (stored, hashed, licence-noted)
  → parse: PDF text, tables, OCR only where there is no text layer
  → InboundLine per product line — raw_text VERBATIM, unchanged (exists)
  → extraction: deterministic grammar first (the parser already reads
    descriptions correctly — docs/concepts/13 measured that the abstention is
    the *absent field*, not engine judgement), LLM only where it declines
  → inbound_line_requirements: attribute, value, unit, provenance, confidence,
    char span, page
  → human review screen; every correction writes a feedback_event
  → CanonicalRequirement → retrieval
```

**Buy, do not build, the document extractor** — `docs/concepts/14` §5.20
already reaches this conclusion, and it is right: a pretrained extractor behind
an API, verified against deterministic reconciliation, beats a research project
competing with a commodity. What must be built here is the *provenance*: page,
table context and character span, so every extracted value can be shown against
the pixels it came from.

**`ABSTAINED` is the label that makes extraction measurable.** It already
exists on `LineDisposition` and already means "we could not read the
requirement". Extraction quality is the rate at which that disposition falls
without `false` readings rising.

## 25. Proposed compatibility architecture

A deterministic, data-driven rule engine producing **four verdicts per
attribute** and one rolled-up verdict per candidate:

```
MATCH        the values are the same under this attribute's comparison
ACCEPTABLE   they differ, and the rule permits the difference in this direction
INCOMPATIBLE they differ, and the rule forbids it
UNKNOWN      one side is not known — NOT a pass, and never rolled up as one
```

**Rule kinds**, all declared in `ontology_rules`, none in code:
`EXACT` · `MIN` · `MAX` · `RANGE` · `TOLERANCE` (with `direction`:
`SYMMETRIC | UPWARD_OK | DOWNWARD_OK`) · `ENUM` (with an explicit
compatibility matrix) · `BOOLEAN` · `STANDARD` (conformance to a named
standard) · `RELATION` (an attribute pair that must co-vary).

**Three things the existing model cannot do, and they are the reason this is a
new mechanism rather than a configuration change:**

1. **Direction.** `ToleranceModel.field_score` is
   `1 − |ref − cand| / (ref × band)` — symmetric by construction. The brief's
   own example (required 10 A, candidate 12 A → possibly acceptable; required
   12 A, candidate 10 A → not) cannot be expressed. Most electrical, load and
   pressure ratings are directional.
2. **Enumerated incompatibility.** 24 V DC versus 230 V AC is not a distance;
   it is a matrix entry. There is no enum rule kind today.
3. **Category-specific gates.** `HARD_GATE_FIELDS` is one hardcoded triple.
   Bearings gate on bore, contactors on coil voltage and poles.

**And the change that matters most: UNKNOWN must gate.** Today
`compare_geometry` skips a hard gate when either side is `None`
(`distance.py:131–132`), so an unknown value silently passes and a vacuous
perfect score is possible — which is exactly what I reproduced with the bearing
query. The rule must become: **a CRITICAL attribute that is UNKNOWN on either
side yields `INSUFFICIENT_INFORMATION` for that candidate, never a score.** The
existing `dimensionally_vacuous` flag and `_is_discriminating` heuristic stay
as belt-and-braces, but they stop being the only protection.

## 26. Proposed equivalence architecture

Classification is a **function of the compatibility result and the evidence**,
not a threshold on a similarity score:

| Class | Condition |
|---|---|
| `EXACT_MATCH` | same catalogue record, exact identity |
| `OEM_EXACT_PART` | the manufacturer's own part for the requested reference |
| `CONFIRMED_EQUIVALENT` | every critical attribute `MATCH`; a **cited** cross-reference or a human-confirmed relationship |
| `FUNCTIONAL_EQUIVALENT` | every critical attribute `MATCH` or `ACCEPTABLE`; no critical `UNKNOWN`; evidence present for the differing attributes |
| `COMPATIBLE_SUBSTITUTE` | criticals satisfied; majors differ acceptably |
| `POSSIBLE_SUBSTITUTE` | criticals satisfied; evidence thin or majors unverified |
| `SIMILAR_PRODUCT` | retrieval neighbour, technical validity not established — **never offerable as a substitute** |
| `INCOMPATIBLE` | any critical `INCOMPATIBLE` |
| `INSUFFICIENT_INFORMATION` | any critical `UNKNOWN` |

**The vocabulary reconciliation is a decision that needs making, not
inventing.** Nine values already exist and are rendered on two screens
(`rel.ts`): `EXACT · TECH · COMPAT · POSSIBLE · AMBIGUOUS · UNRESOLVED ·
INCOMPATIBLE · PIE_DOWN · NONE`. Introducing a parallel nine-value vocabulary
would be precisely the semantic duplication both `CLAUDE.md` files forbid.
**Recommendation: extend the existing vocabulary rather than replace it** —
`EXACT` splits into `EXACT_MATCH` / `OEM_EXACT_PART`; `TECH` splits into
`CONFIRMED_EQUIVALENT` / `FUNCTIONAL_EQUIVALENT` on whether a *citation* exists;
`COMPAT` → `COMPATIBLE_SUBSTITUTE`; `POSSIBLE` → `POSSIBLE_SUBSTITUTE`;
`AMBIGUOUS`/`UNRESOLVED` map onto `INSUFFICIENT_INFORMATION`; `SIMILAR_PRODUCT`
is genuinely new and must be visually distinct because it is the one class that
is *not* an offer. `PIE_DOWN` and `NONE` are service states, not
classifications, and stay as they are. Migration is additive: old values keep
rendering, new ones are introduced behind the ontology version.

**Non-transitivity must survive.** Four mechanisms hold it today and all four
stay: every comparison's left operand is the request; no substitution table,
graph or union-find; `_dedup` groups by an **exact** key; and the one genuine
two-hop flow is guarded on the reference being `AUTHORITATIVE`.
`POST /api/v1/equivalences` therefore accepts **only human-confirmed, evidenced
assertions**, and `equivalence_results` (derived) may never be promoted into
`product_relationships` (asserted). `tests/test_equivalence_not_transitive.py`
and `tests/test_identity_confirmation_gate.py` extend to cover the new path.

## 27. Proposed ranking architecture

**Deterministic, weighted, configurable, versioned. No ML.** Technical
validity is a *prerequisite*: anything not at least `POSSIBLE_SUBSTITUTE` is
absent from the ranked list, not ranked low.

Signals, each stated separately rather than fused into one score: exact/
normalized part-number match · manufacturer match · classification class ·
critical-attribute match count · retrieval score (lexical, and vector if built)
· evidence strength · standards conformance · customer's own history with this
substitute · stock · lead time · supplier reliability · commercial fit.

Weights live in the same versioned-policy place as the tolerance bands and are
stamped on every `equivalence_results` row.

**Two constraints from the existing invariants:**

- A high-margin incompatible product cannot outrank a valid one — guaranteed
  structurally by the prerequisite gate, not by weighting.
- **Rank order can itself leak cost.** The withholding rule (§5) is that a
  predicate a caller can walk is the number it tests against. If commercial fit
  enters the ordering, a salesperson who can vary a price and watch the order
  change can recover a boundary. **Recommendation: the ranking a salesperson
  sees is ordered on technical and availability signals only; commercial
  ordering is a management-role projection.** This needs the same
  price-sweep test as `test_a_salesperson_cannot_walk_the_price_to_recover_cost`.

## 28. Proposed feedback architecture

Every human action on a recommendation becomes an append-only event, reusing
the conventions already in place (`HumanAction`: `VIEW · ACT · DISMISS ·
SNOOZE · OVERRIDE · ESCALATE · REOPEN`; `identity_events`; the supersede idiom).

Captured: which candidates were shown and in what order · which was selected ·
which were rejected and why · corrections to an extracted requirement · a
confirmed equivalence assertion · the quote outcome · the eventual order.

**The gate stays.** Selecting a candidate is a substitution *on one quote*.
Only a single-candidate `NEEDS_REVIEW` proposal may become a permanent
identity, and only through `confirm_proposed_identity`. A scored suggestion
that a person happened to pick is feedback, not an assertion — promoting it
would turn a tolerance match into an exact reference and compose two bands on
the next request.

## 29. Proposed evaluation architecture

**Build this before Phase 4, not after Phase 6.** The dataset is the
deliverable, and `docs/concepts/14` §5.21 already identifies `InboundLine` as
the corpus and `InboundLineDisposition` as the label.

Dataset strata: exact matches · known equivalents (from published charts) ·
known substitutes · **non-equivalents** · ambiguous cases · incomplete RFQs ·
and the adversarial cases from the brief, each as a named fixture.

Metrics, reported **per category, never only in aggregate**: Recall@1/3/5/10 ·
Precision@K · MRR · NDCG · exact-match accuracy · compatibility accuracy ·
classification accuracy · evidence correctness · abstention rate · and the
primary metric —

> **False-equivalence rate:** the share of candidates presented as
> `CONFIRMED_EQUIVALENT` or `FUNCTIONAL_EQUIVALENT` that a domain expert judges
> not to be. It is the only metric permitted to block a release.

**Could it be measured today? The metric exists and is already reporting; the
dataset is what is missing.** `tools/scorecard.py` is the *one* definition of
what an evaluation counts — `precision`, `coverage`, `abstention_rate` and
`wrong_confident_rate`, each `Optional[float]` returning `None` rather than a
benign default, with a seeded percentile bootstrap. **`wrong_confident_rate` is
false-equivalence under another name**, and both harnesses fill the same
scorecard.

What it reports today is worth putting in front of whoever approves this
programme:

| Harness | Cases | Precision | Coverage | Wrong-confident |
|---|---|---|---|---|
| `eval_identity` | 13 | 13/13, zero false positives | — | — |
| `eval_rfq` — engine arm | 14 | 60.0% | 71.4% | **21.4%** |
| `eval_rfq` — baseline arm | 14 | 70.0% | 71.4% | **7.1%** |

On fourteen cases the engine arm is **worse than its own baseline on the metric
that matters most** — three times the wrong-confident rate for ten points less
precision. Fourteen cases is far too few to conclude anything, and that is
exactly the point: **the measurement instrument is built and the dataset is
empty.** What is missing is a labelled cross-manufacturer substitution set,
which cannot exist until there is a second manufacturer pack and a decorated
master — the strongest argument for the phase order in §39, and the reason
evaluation is staffed from Phase 1 rather than Phase 6.

---

# Part C — Plan, risk and sequence

## 30. Multi-agent implementation plan

Ownership is by **file boundary**, so two workstreams never edit one module.
The orchestrator owns every shared contract and is the only party that may
change one.

| # | Workstream | Owns (exclusive write) | Must not touch |
|---|---|---|---|
| 0 | **Orchestrator / architect** | `docs/AI_PRODUCT_ENGINE_*.md`, all cross-package contracts, `domain/enums.py` additions, integration and the gate | — |
| 1 | **Platform analyst** | this report; no code | everything |
| 2 | **Ontology & product model** | `app/ontology/`, ontology migrations, `pie-parser/packs/*/` attribute vocabulary | `commercial/`, `ai/` |
| 3 | **Ingestion & normalisation** | `app/ingestion/attributes/`, `app/master_health/` extensions, importers | `routers/`, `commercial/` |
| 4 | **Retrieval** | `app/retrieval/`, retrieval migrations and indexes | `compatibility/`, `equivalence/` |
| 5 | **Document & RFQ intelligence** | `app/ingestion/documents/`, `app/extraction/`, `app/enquiry/` extensions | `commercial/`, `retrieval/` |
| 6 | **Resolution & equivalence** | `app/equivalence/`, `app/pie_service.py` projection widening | `commercial/`, `ranking/` |
| 7 | **Rule engine** | `app/compatibility/`, `pie-parser/engine/` new slot types and validator kinds | `equivalence/`, `retrieval/` |
| 8 | **Evidence** | `app/evidence/`, `document_chunks`, citation lookups | `compatibility/` |
| 9 | **Ranking & commercial** | `app/ranking/`, `commercial/` integration points | `compatibility/`, `equivalence/` |
| 10 | **Historical learning** | `app/feedback/` datasets, offline notebooks/scripts | anything serving a request |
| 11 | **Human-in-the-loop UI** | `frontend/src/` new screens, `components/` | backend |
| 12 | **Evaluation** | `tests/evaluation/`, `pie-parser/eval/`, fixtures | production code |
| 13 | **Security & platform** | RLS migrations, `authz`, `trust/` review of every new surface | feature code |
| 14 | **DevOps & observability** | queue handlers, jobs, metrics, `scripts/verify.sh` | feature code |

Every workstream: read the surrounding code first, run the capability search
that both `CLAUDE.md` files require, stay in scope, add tests, and report
changed files, assumptions and unresolved issues.

## 31. Dependencies between agents

```
1 analyst ──▶ 0 architect ──▶ 2 ontology ──┬──▶ 3 ingestion ──▶ 4 retrieval ──┐
                                           │                                  │
                                           └──▶ 7 rules ────────┐             │
                                                                ▼             ▼
                              5 RFQ/document ──────────────▶ 6 equivalence ◀──┘
                                                                │
                                                    8 evidence ─┤
                                                                ▼
                                                          9 ranking
                                                                │
                                                   11 UI ◀──────┼──────▶ 10 learning
                                                                ▼
                                                         12 evaluation
                              13 security and 14 devops review every stage
```

Hard rules: **no workstream implements against an undefined interface** — the
orchestrator defines and freezes the contract first. **12 (evaluation) starts
with 2**, not after 9; a metric introduced after the thing it measures tends to
be a metric the thing already passes.

## 32. Major technical risks

| Risk | Why it is real here | Mitigation |
|---|---|---|
| **Attribute coverage never rises** | 78.4% of the master has no technical fact; the whole system is downstream of this | Make coverage the Phase 1 exit criterion and publish it per category. Do not start Phase 5 below an agreed floor |
| **The catalogue outgrows process memory** | measured 10.2 KB/record → ~1 GB per worker at 100k | Retrieval in PostgreSQL from Phase 2; the in-memory pool stops being the index |
| **Equivalence becomes transitive by accident** | six paths were traced clean once; a "tolerant dedup" or a substitution cache reopens it | Extend `test_equivalence_not_transitive.py` to every new path; no derived row in `product_relationships` |
| **A second normaliser diverges** | two already exist with opposing philosophies, correctly | No third without a written statement of which job it does |
| **Category ontology becomes a maintenance liability** | the repo's own venture review names "our knowledge graph" as a moat founders imagine | Bind to published standards (ISO 13399, ETIM, DIN) rather than authoring a bespoke ontology; see §38 |
| **A new screen is written as a hand-rolled `<table>`** | it has happened, through three UI passes; the check is deliberately outside the gate | `platform/DataGrid.tsx` for anything business-sized; run the diff check in review |
| **`insight.py` absorbs the new endpoints** | 5,326 lines, 55 endpoints, 27% of the platform's routes — the path of least resistance | New routers, mounted with an explicit plan gate |
| **A new package sits outside the layer invariant** | `DETERMINISTIC` names six packages; a new `equivalence/` or `compatibility/` is unconstrained until added | Add the names in the same commit that creates the packages — the invariant is opt-in, not automatic |
| ~~A "same as X but Y" request quotes X~~ | **fixed** (017). The residual risk is the class, not the instance: the engine knows more than its consumers read | Every new consumer of an engine payload states which fields it reads and what it does when one is absent |

## 33. Security risks

- **Cost recovery through a new predicate.** The MFLOOR and `NEGATIVE_MARGIN`
  incidents were both *predicates* a caller could walk. A comparison endpoint
  that says "this candidate would be below floor", or a ranking whose order
  changes when a price is varied, is the same defect wearing new clothes.
  **Every new surface goes through `quote_service.project` and gets a
  price-sweep test, not a field-level assertion.**
- ~~**RLS is not switched on in any shipped deployment.**~~ **Closed —
  decision 019**, and it landed before this programme added its first column
  rather than after, which was the point. What stands from the finding is the
  standing rule it implies: a new table outside the policy list is isolated only
  by Python, so **every new table joins the RLS migration and
  `test_row_level_security.py` in the commit that creates it** — the same shape
  of rule as decision 020 for the layer-boundary invariant, and for the same
  reason: a check that does not cover a thing reads exactly like one that
  passes.
- **Customer RFQ text is the most sensitive corpus in the system** — it names
  what a customer is buying. It must sit under the existing trust machinery:
  tenant keys, the name vault, audit, disclosure and erasure. An erasure
  request must reach uploaded documents and extracted requirements, not just
  the rows that existed when erasure was written.
- **Manufacturer data licensing.** Datasheets and catalogue pages are
  third-party copyright. Store a licence note per document and keep evidence as
  a *citation with a span*, not a redistributed copy.
- **Document upload is a new attack surface** — type sniffing, size limits,
  archive bombs, and never executing or rendering an uploaded file inline.
- **A demo organisation refuses all non-SAFE methods by method, not
  allowlist**, so a POST that only reads (a resolve preview, an extraction dry
  run) will 403 there. Design around it or change the rule deliberately.

## 34. Performance risks

Measured on this checkout, single-threaded Python 3.11, 4 cores:

| Operation | 6,717 records | 33,585 | 100,755 |
|---|---|---|---|
| `find_equivalents` — **well-specified** query (88.9% gated out) | 30.8 ms | 196.6 ms | 555.1 ms |
| `find_equivalents` — **undecodable** input (0% gated out) | **72.4 ms** | — | **1,662.3 ms** |
| catalogue load into memory | 0.23 s | — | — |
| resident memory, equivalence pool | 67 MB (10.2 KB/rec) | — | ≈1.0 GB |
| resident memory, **including the second copy** the identity index keeps | +61 MB (**19.7 KB/rec total**) | — | **≈2.0 GB per worker** |
| `pie_service.resolve` — first call after boot | **813 ms** | — | — |
| `pie_service.resolve` — subsequent | 22–116 ms | — | — |
| exact identity lookup | ~1 ms | — | — |
| catalogue rebuild from corpus | 1.46 s (6,717 products, 13.3 MB) | — | — |

*Method: `PieCatalogSource.load()` over `backend/data/products.jsonl`, pool
multiplied ×1/×5/×15, through `EquivalenceQuery.find_equivalents`; RSS by
`resource.getrusage`.*

Three corrections to the obvious reading, each of which makes the case for
moving retrieval into PostgreSQL stronger rather than weaker:

- **The cheap number is the best case.** A well-specified spec gates out 88.9%
  of the pool before scoring. But `distance.py` skips a gate whenever either
  side is `None`, so an input the engine *cannot decode* gates out **nothing**
  and costs **1.66 s at 100k**. That is the `"6205 2RS C3 bearing"` case — and
  an input with no technical content is exactly what arrives from a real RFQ.
  **The expensive query is the one the system understands least.**
- **Memory is roughly double, because the catalogue is held twice.**
  `identity/store.AuthoritativeIndex.from_jsonl` keeps a second independent full
  copy of the same records (+61 MB measured), and both hang off the one
  process-wide `pie_service` singleton. A worker that syncs *and* resolves
  carries ~19.7 KB/record — **≈2.0 GB at 100k**. Separately,
  `docs/hosting-free-tier.md`'s "each worker warms its own ~13 MB catalogue
  copy" is the *file* size; measured resident cost is 68 MB for the pool alone
  and 187.6 MB RSS for a warmed process. That sizing guidance is wrong by about
  an order of magnitude.
- **`warm()` does not warm.** `main.py:167` calls it, but `_ensure_loaded` only
  builds a lazy source — the 13 MB file is not read until the first `resolve`.
  Measured: `warm()` 0.035 s, first resolve 813.6 ms, subsequent 22–116 ms.
  **The first real user request after every deploy pays the load.**

And the scan is not one pass but **five**, one of which allocates:
`_NamespaceRestrictedSource.load()` rebuilds a filtered copy of the entire pool
on every call with no cache, then `query.py` does a `pool.extend`, an `any()`
over all records and a set comprehension over all records — all before the
scoring loop begins.

Concurrency makes every line of this worse: CPU-bound Python holding the GIL,
so N workers cost N × 2 GB and share nothing.

**The two defects in §17, reproduced:**

```bash
PIE_PARSER_ROOT=/home/user/pie-parser python3 -c "
import sys; sys.path.insert(0,'backend')
from app.pie_service import pie_service; pie_service.warm()
r = pie_service.resolve('same as 2001174 but 0.4 corner radius').to_dict()
print(r['rel'], r['outcome'], r['supplyCode'])
for c in r['candidates'][:4]:
    print(' ', c['code'], c['rel'], c['score'],
          (c['attributes'] or {}).get('corner_radius_mm'), c['desc'][:40])"
```
```
EXACT AUTO_MATCH 2001174
  2001174 EXACT None 0.8  CNMG 120408-49 - TN2000     <- the 0.8 mm insert, auto-selected
  1182698 TECH  0.96 None KENDEX SQUARE INSERTS CVW1  <- no corner radius at all
  2824077 TECH  0.96 None TPCB SCREW ON INSERT
  2827560 TECH  0.96 None GPCT SCREW ON INSERT
```

Second-order: `POST /api/v1/resolve` caps input at 512 characters and handles
one line per request, and there is **no batch resolve endpoint**. A 200-line
tender needs one, or a review queue will make 200 round trips.

## 35. Cost risks

- **Do not send products to a model.** Retrieval, normalisation, arithmetic,
  filtering and compatibility are deterministic code. Models are for document
  interpretation, ambiguous extraction and evidence summarisation only.
- **Embeddings are an ongoing cost, not a one-off.** 100k products × several
  representations, re-embedded whenever a representation changes. The
  `source_representation_hash` design makes this incremental; without it a
  re-index is a full re-spend.
- **Document extraction is per-page and recurring.** Cache by document
  `sha256`; never re-extract an unchanged file.
- **BYOK already exists**, so per-organisation cost attribution is available —
  use it rather than inventing metering.
- The cheapest high-value action in this whole programme costs nothing:
  `equivalence/lookups/grade_crossref.csv` **ships header-only**. Ten sourced
  rows from a published cross-reference chart would move quote quality more
  than any model, and need no code at all.

## 36. Migration risks

- **Every schema change needs a migration in the same commit, verified against
  an empty database** on both SQLite and PostgreSQL. The §4 incident in
  `CLAUDE.md` is what happens otherwise.
- **Never `Base.metadata.create_all` outside a test fixture**; never edit a
  released migration; one `Base`; every model reachable from `Base.metadata`.
- **Backwards compatibility is the default.** The `Line`, `Candidate` and
  `/api/v1/resolve` contracts have live consumers. Add optional fields; never
  change the meaning of an existing one. The relationship vocabulary migration
  (§26) is additive and versioned.
- **The vendored submodule must be fetched in CI.** It is not checked out here,
  and that exact omission once silently disabled 25 migration tests for eight
  merges.
- Rollback for each phase is dropping the new tables and the new router: no
  existing behaviour depends on them until the UI is switched over, which is
  deliberately the last step of each phase.

## 37. Estimated complexity per phase

Sized in engineer-weeks for one experienced engineer, excluding domain data
authoring, which is the real cost driver and is called out separately.

| Phase | Scope | Complexity | Data cost |
|---|---|---|---|
| 0 | Reconnaissance | **done** | — |
| 1 | Product data foundation: ontology tables, `product_attribute_values`, provenance, decoration job, importers | **M–L** (4–6 wks) | **High** — this is the programme's real cost |
| 2 | Search: exact + normalized + lexical + structured filter, in PostgreSQL | **M** (3–4 wks) | Low |
| 2b | Vector, only against a measured recall gap | **M** (2–3 wks) | Medium |
| 3 | RFQ intelligence: upload, storage, extraction, review surface | **L** (5–7 wks) | Medium |
| 4 | Compatibility rule engine + new engine slot types | **M** (3–4 wks) | **High** — rules are domain data |
| 5 | Equivalence classification + evidence | **M** (3–4 wks) | High |
| 6 | Deterministic ranking | **S–M** (2–3 wks) | Low |
| 7 | Human feedback capture and screens | **M** (3–4 wks) | Low |
| 8 | Commercial integration | **S–M** (2 wks) | Low |
| 9 | Historical learning datasets — **datasets only, no training** | **S** (1–2 wks) | — |
| 10 | Production hardening | **M** (3 wks) | — |

The honest summary: **the engineering is medium and the data authoring is
large.** Every phase from 4 onward is gated by how much of the master carries
attributes, which is Phase 1's job and not a coding problem.

## 38. What should NOT be built

- **A vector database.** PostgreSQL with `pg_trgm` first; `pgvector` in the
  same database if measurement demands it. Two stores means two consistency
  problems.
- **A graph database.** Relationships are relational, sparse, and — critically
  — **must not be traversed**. A graph database's central affordance is the
  thing this domain forbids.
- **Kafka, Kubernetes, microservices, a feature store, distributed training.**
  The DB-backed queue is adequate and already proven.
- **An ML ranker, learned NER, a fine-tuned model, or learned substitution.**
  `docs/concepts/14` adjudicates these individually and refuses them on
  measured grounds — the label counts are not there (6 recorded losses against
  a floor of 100; ~12.5 observations at item grain). Learned substitution is
  refused outright.
- **A bespoke "product knowledge graph" as a differentiator.** The repository's
  own venture review lists it among the things founders mistake for a moat:
  *"Ontologies here are published standards (ISO 1832, ISO 13399, DIN, ANSI,
  ETIM). 'Our knowledge graph' describes a maintenance liability with good
  branding."* Bind to the published standards; do not author a rival.
- **A second scorecard, a second normaliser, a second relationship vocabulary,
  or a second tolerant matcher.** Each already exists once.
- **Clustering for item categorisation.** `category` is stored raw and
  interpreted under versioned policy on purpose; a cluster label cannot be
  re-read under a corrected map.
- **Inferring manufacturer or grade from an item name** to close the blanks.
  The codebase already refuses this elsewhere and it is how a wrong part
  reaches a customer.
- **A full application ontology** (workpiece material → operation → machine
  capability → tool class → grade) now. It is architecturally correct as pack
  data and recorded as below the line until customers ask for tools by
  application.

## 39. Recommended implementation order

The order follows the dependency graph, but the *first* item is chosen because
it is nearly free and unblocks measurement:

0. **Four things that are cheap, independent of every decision below, and
   should not wait for approval of the rest.** Three are now done; what is left
   of this group is the grade chart (which needs the product owner) and the CI
   submodule fetch:
   - ~~**Fix the two defects in §17.**~~ **Done** — decision 017, both gates
     green. So are the two that followed from the same query: the override
     decode (021) and the harness that scored it (022), which turned out to
     carry 017's own defect and was reporting the engine as worse than it is.
   - ~~**Read `cf_item_type` and `cf_item_category` at ingest.**~~ **Done** —
     decision 018, as `products.source_item_type` / `source_item_category`.
     Re-measured first: 23 of 23 sampled items carry both, against 0 of 800 for
     the column the platform already read. The sample also found a customer
     lookup that is deliberately *not* read, and a delivery-date field that is
     the first evidence for the open decision 016.
   - **Populate `grade_crossref.csv` with sourced rows.** It ships header-only,
     so no grade can be cross-referenced at all today. A morning, no code —
     **and it needs the product owner**, because the rows have to be sourced
     from published charts rather than invented. This is now the only item in
     this group still open.
   - ~~**Set `APP_DATABASE_URL` in the deployment recipes**~~ **Done** —
     decision 019. `deploy/release.sh` provisions the non-bypassing role and
     every recipe carries the variable; the existing 27-test RLS suite was run
     against a role that script created, so what the gate proves is what a
     deployment following the recipe gets. **Still open in this group: fetch
     the pie-parser submodule in CI.**
1. **Phase 1 — attribute decoration.** Persist what `master_health` already
   decodes, with provenance. Add importers so attributes can also arrive from a
   manufacturer file rather than only from a decoded name. **Exit criterion:
   published attribute coverage per category, not accuracy.**
   → **Decode half landed** (decision 002, ACCEPTED 2026-08-30):
   `product_attribute_values` with its RLS policy, `app/attributes/`, and the
   decoder widened from 3 kept fields to 44. The figure in §6 was wrong in the
   platform's favour — it said eleven decoded slots were dropped, and it is 41.
   **Import half still open**, waiting on the export decision 025 names; until
   it exists this phase reaches only the ~21% a name can carry, and the phase is
   not complete.
2. **Phase 2 — retrieval in PostgreSQL.** Exact, normalized, lexical,
   structured filter. Measure recall on the evaluation set before considering
   vectors.
   → **Split, and the first half is in progress** (decision 003, ACCEPTED IN
   PART 2026-08-30). The staged lexical ladder is a *scale* answer — its
   evidence is 555 ms at 100,755 records — and decision 023 puts today's
   reachable catalogue at ~16k. It stays PROPOSED until a measured recall gap
   or a catalogue that has actually grown justifies it.
   What is being built now is a different problem the same section hid: the
   portal ranks against the manufacturer catalogue and **not against what the
   business sells**. `_build_sources` makes a `ZohoCatalogSource` only for a
   `--zoho-fixture` path and the portal passes none, so a Zoho item reaches the
   ranking only through `pie_record_id` — **~9% of items**. The other ~91% of
   the sellable book cannot be offered however well it matches. No amount of
   PostgreSQL fixes that; it is pool composition, and Phase 1's attribute store
   is what made it solvable.
3. **Phase 4 before Phase 3.** *Deviation from the brief's numbering, and
   deliberate:* the compatibility rule engine depends only on Phase 1, while
   RFQ document intelligence is the largest and least certain piece. Building
   rules first means the existing text-RFQ path gets sound technical filtering
   months earlier, and Phase 3 lands into a system that can already judge what
   it extracts.
4. **Phase 3 — RFQ document intelligence**, with the review surface. Note the
   pre-req found in reconnaissance: **there is no frontend for inbound demand
   at all today** — the five `/api/v1/enquiries` endpoints have no consumer, so
   this phase includes the first screen for enquiries as well as the extractor.
5. **Phase 5 — equivalence classification and evidence.**
6. **Phase 6 — deterministic ranking**; **Phase 8 — commercial integration.**
7. **Phase 7 — feedback capture** (the events can and should be captured from
   Phase 5 onward; the *screens* land here).
8. **Phase 9 — datasets only.** No training until the labels clear the floors
   `docs/concepts/14` sets.
9. **Phase 10 — hardening.**

**Evaluation (workstream 12) runs from Phase 1, in parallel, throughout.**

## 40. Open questions requiring decisions

**Five of these were answered by the product owner on 2026-08-30, and the
answers are recorded as decisions 016 and 024–029 rather than only here.** The
questions are kept in place with their answers attached, because a question
deleted once answered leaves a later reader unable to tell a considered choice
from a default nobody noticed — which is the whole reason this section exists.
Answered items are marked **ANSWERED**; the rest are still open.

**Blocking — Phase 1 cannot be scoped without these:**

1. **What is the actual catalogue, and is there a third entity?** The brief
   says ~100,000 SKUs. Measured: the SLS master is 15,032–15,996 items, the
   second entity is 513, and the UPS connector returns the SLS organisation —
   so there is no third ERP master. Together that is under 17k reachable items
   against a 100k target. Is 100k the union across entities, an ambition, a
   future principal's catalogue, or a dataset not in this repository? *Design
   target assumed: 100k, with today's ~16k as the working set — but the answer
   changes Phase 1's sizing by a factor of six.*
2. **Which categories, in what order?** Everything here is cutting tools. The
   brief names bearings, contactors and sensors. Each is a pack, a rule set and
   a data-authoring project. One category done properly beats four started.
   → **ANSWERED — cutting tools only (decision 024).** Bearings, contactors and
   sensors are out of scope, and the engine slot types §23 lists as missing stay
   unbuilt: a generic mechanism with one speculative implementer is
   over-engineering, not foresight.
3. **Where do attributes come from?** Decoding a name reaches ~17–21%.
   Manufacturer data files, ETIM/ISO 13399 feeds, or distributor PIM exports
   reach much further. Is such a feed available, and under what licence?
   → **ANSWERED — a distributor PIM / price-list export, in both spreadsheet and
   PDF form (decision 025).** The spreadsheet path reuses
   `master_health/profiles/`, which is already a data-driven column mapping; the
   PDF path needs table extraction and yields weaker provenance. **Measure what
   the spreadsheet buys before writing any PDF extraction.**
4. **Who authors the compatibility rules, and who signs them off?** These are
   engineering claims a distributor is liable for. This is a named-person
   question, not a technical one.
   → **ANSWERED — nobody, for now (decision 027).** No in-house rules are
   authored; the system asserts only what a manufacturer published and returns
   `INSUFFICIENT_INFORMATION` otherwise, so the liability stays where the claim
   was made. The cost is coverage, and it must not be bought back by lowering a
   threshold. The named-person question returns in full the moment anyone wants
   an assertion no manufacturer made.

**Important — needed before the phase they govern:**

5. **Lead time and selling price are not persisted (§12). Which way?** Persist
   them on a sync (a schema and freshness question) or fetch live per candidate
   (a latency question — ten candidates per line, per quote). Phase 8 cannot be
   scoped until this is answered, and it is the difference between ranking on
   availability and ranking on a promise.
   → **ANSWERED — split by use (decision 016).** Lead time is a ranking input
   compared across every candidate, so it is persisted; selling price is a
   single-line output read once for the product chosen, so it stays live. The
   question's own framing treated them as one decision, and they are not.
6. **Is `pgvector` installable on the deployed PostgreSQL?** Depends on who
   controls the instance. It decides whether Phase 2b is possible in-database.
7. **Does an RFQ surface attach as a new top-level screen or as a step inside
   the Quote Builder?** The backend supports the former today; the latter keeps
   capture conditioned on somebody already working the line, which the
   `enquiries.py` docstring says makes coverage unanswerable.
8. **How does a comparison UI get the structured comparison?** Widen
   `pie_service`'s projection, or route the screen through
   `POST /api/v1/resolve`, which *already* emits per-slot provenance and spans —
   the public API is currently richer than the internal quote path.
9. **May a salesperson see a commercially-ordered list?** §27 argues no,
   because order is a walkable predicate. That is a policy call.
10. **What confidence thresholds, per category, gate an auto-selected
   substitute versus one that requires review?** The brief proposes 95/85/70;
   these must be configurable and category-specific, and calibration should not
   be claimed until it is measured.
11. **Does a second manufacturer pack get commissioned?** It would raise
    coverage more than anything else on the list, and it is a `pie-parser`
    programme rather than a portal change.

**To confirm:**

12. Retention and residency for customer RFQ documents; erasure reach into
    uploaded files and extracted requirements.
13. Whether `product_attribute_values` is org-scoped (a distributor's own
    decoration) or shared (a manufacturer fact). *Recommendation: org-scoped
    rows pointing at a shared decoded catalogue record, mirroring the existing
    link-never-merge rule.*
    → **ANSWERED — org-scoped (decision 026).** Decided by the answer to Q3
    rather than on its own: a distributor PIM export is licensed to the
    organization that obtained it, so sharing those rows would redistribute
    another party's licensed data. Splitting the table by provenance — shared for
    designation-decoded facts, org-scoped for imported ones — was considered and
    deferred until a second tenant exists.
14. Whether the nine-value relationship vocabulary is extended in place
    (recommended, §26) or replaced behind a version flag.

---

## Appendix — how the measurements in this report were produced

```bash
# catalogue rebuild (6,717 products, 13,264,167 bytes, 1.46 s)
PIE_PARSER_ROOT=/home/user/pie-parser python3 scripts/build_catalog.py

# live resolution, including the false-equivalence demonstration
PIE_PARSER_ROOT=/home/user/pie-parser python3 -c "
from app.pie_service import pie_service; pie_service.warm()
print(pie_service.resolve('6205 2RS C3 bearing').to_dict())"

# scan cost against a synthetic pool (×1, ×5, ×15) and resident memory
#   see §34; PieCatalogSource.load() + EquivalenceQuery.find_equivalents,
#   RSS via resource.getrusage(RUSAGE_SELF).ru_maxrss

# absence checks (both repos, all source and config file types)
rg -niE 'embed(ding)?s?\b|pgvector|\bvector\b|cosine|faiss|hnsw'
rg -niE 'tsvector|to_tsquery|pg_trgm|bm25|elasticsearch|opensearch|rapidfuzz|SequenceMatcher'
rg -niE 'pypdf|pdfplumber|fitz|pdfminer|tesseract|easyocr|UploadFile|multipart'

# structured comparison discarded at the bridge (zero hits)
rg -n 'field_matches|geometry_score|dimensionally_vacuous|gate_reason' backend/app frontend/src
```
