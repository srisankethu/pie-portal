# Per-company decoded catalogues — a plan, not yet built

The decoded catalogue is deployment-wide today: one `PIE_CATALOG` path, one
`PIE_PACK`, one index per process, and `Setup → Decoded catalogue` says so in
words. This plans the move to **one catalogue per connected company**, each
with its own pack and its own uploaded corpus, and the removal of the shared
default.

Nothing here is implemented. It is written first because three of the decisions
below overturn something the codebase has already decided on purpose, and one
of them breaks on the deployment we actually run.

**Decided already** (the answers this plan is built on):

- Keyed on the **connected company** (`ZohoConnection`), not the organization
  and not the user.
- **Pack + corpus + catalogue** all vary per company.
- The corpus is **uploaded through the UI**.
- A quote **names the company** it is raised from, and that is what selects the
  catalogue.

---

## 1. Two things that must be settled before any of it is worth building

### 1.1 The container filesystem is ephemeral, and uploads have no other home

`railway.json` builds from `deploy/backend.Dockerfile` with no volume declared.
The container's disk does not survive a redeploy.

Today that costs nothing, and it is worth understanding why, because it is
exactly the property uploads destroy. `PIE_CORPUS` points into the pinned
pie-parser submodule, so the corpus **ships inside the image**; the catalogue is
a derived file that `AUTO_BUILD_CATALOG` rebuilds from it in under two seconds.
Lose the disk and nothing is lost — the source is still in the image.

An uploaded corpus has no such source. Written to container disk it is gone on
the next deploy, and the catalogue can then never be rebuilt: the company's
resolution silently degrades to "not built" and the file that would fix it no
longer exists anywhere. That is data loss caused by a routine deploy, which is
the kind of defect that is discovered weeks later by somebody wondering why a
customer's quotes stopped resolving.

So an uploaded corpus has to be **durable**, and there are three candidates:

| Where | Durable on Railway | New infrastructure | Notes |
|---|---|---|---|
| **Database** (bytes in a table) | yes | none | Already backed up; the restore drill in `make verify` proves round-trip. RLS gives per-tenant isolation. |
| Object storage (S3 or similar) | yes | credentials, SDK, a new failure mode | Nothing in this codebase talks to object storage today. |
| Mounted volume | **no** on Railway | a volume per environment | The compose/Caddy deployment has one (`BACKUP_DIR`); Railway does not. |

**Recommendation: the database.** A corpus is a CSV of roughly 6,700 rows —
low single-digit megabytes, written once per upload and read once per build.
Storing it as a row buys four things this design needs anyway: it survives
deploys, it is inside the backup and restore drill that already runs in the
gate, row-level security scopes it to its tenant without new code, and it
becomes an auditable record — who uploaded which corpus, when, and what its
checksum was.

The built catalogue (~13 MB) stays a **derived file on local disk**, exactly as
now. That is the point: with the corpus durable, the catalogue is a cache again
and a lost disk costs a rebuild rather than the data. Any deployment can
reconstruct every company's catalogue from rows it already backs up.

### 1.2 Uploading a *pack* is executable configuration, and the repo has already refused uploads once

Two separate problems, and only the first is about dependencies.

**The documented refusal.** `backend/app/master_health/__init__.py` lists three
deliberate refusals, the first being:

> **No upload endpoint.** There is no `UploadFile` and no multipart handler
> anywhere in `backend/app`, and `python-multipart` is not installed. Adding one
> is a dependency decision and a new attack surface, and it buys nothing a path
> argument does not already give a person running a diagnostic.

— and the paragraph above it says these are "written down so it is not re-added
by someone who assumes it was an oversight."

This plan re-adds it, so the reason has to be stated rather than skipped. That
refusal was written about a **diagnostic run by an engineer with a shell**, and
its own justification is that a path argument already serves that person. The
case here is different in the way that matters: the person configuring a
company's catalogue is an owner in a browser, on a deployment where they have no
shell and no way to place a file. A path argument serves them not at all. The
refusal's reasoning does not transfer; its *caution* does, and shows up below as
the limits every upload must carry.

**The security problem, which is larger.** A pie-parser pack is not data — it is
grammars and **regular expressions** that the engine compiles and runs over
every row of a corpus. Accepting an uploaded pack means accepting user-supplied
regexes and executing them: a catastrophic-backtracking pattern over 6,700 rows
is a denial of service that a tenant can upload for themselves, and a build is
already a synchronous request. Pack YAML also names files and layers, so path
traversal in a pack reference is a second surface.

**Recommendation: split the two.**

- **Phase 1 — the pack is *chosen*, not uploaded.** A company selects one of the
  org-layer packs the pinned engine ships (`packs/org/*`), stored as an
  identifier. This satisfies "the pack varies per company" for the real case —
  a second distributor phrasing descriptions their own way — with no new
  execution surface at all.
- **Phase 2 — uploaded pack bundles**, if a tenant genuinely needs a pack nobody
  ships. That needs its own design: a bundle format, a validation pass, regex
  linting with a complexity budget, and a build that runs somewhere it can be
  killed on a timeout. It should not ride along with this change.

The **corpus** upload goes ahead in phase 1: a CSV is data, and the parser
already treats every field as untrusted text.

---

## 2. Storage and layout

Two new tables. Both are tenant-scoped and both belong to a connection, so both
carry `organization_id` **and** `connection_id` — the organization column is
what RLS and every existing query pattern key on, and the connection is the
grain.

**`company_corpora`** — the uploaded item-master export, kept.

| column | why |
|---|---|
| `corpus_id`, `organization_id`, `connection_id` | identity and tenancy |
| `filename`, `content_type`, `size_bytes` | what was sent |
| `sha256` | the input fingerprint; also lets a re-upload of the same bytes be recognised |
| `content` (LargeBinary) | the file itself — the durability requirement of §1.1 |
| `uploaded_by`, `uploaded_at` | provenance a build report can cite |
| `superseded_at` | append-only, replaced not mutated — the convention `state/` uses |

**`company_catalogues`** — what was built from it, and the provenance the screen
already renders.

| column | why |
|---|---|
| `organization_id`, `connection_id` (PK) | one current catalogue per company |
| `corpus_id`, `pack_id` | what it was built from |
| `records`, `rows_read`, `quarantined`, `duration_s` | the run report's headline counts |
| `report` (JSON) | pie-parser's `RunReport.to_dict()`, stored whole and served verbatim, as now |
| `ruleset_checksum`, `run_id`, `pack_version`, `org_id`, `engine_version` | the stamp — unchanged in meaning, just moved off a sidecar file into a row |
| `built_at`, `built_by` | when, and by whom |

The **built JSONL stays on disk**, at
`backend/data/catalogues/<connection_id>/products.jsonl`, derived and
rebuildable. The `.run_report.json` sidecar this branch added goes away: the row
above replaces it, and a row is the thing that survives a deploy.

`Organization.config["pie_pack"]` is *not* where the pack goes, despite
`pie_service.py`'s docstring suggesting it — that predates the decision to key
on the company. It becomes a column on `ZohoConnection` (or on
`company_catalogues`), because the grain is the company.

---

## 3. Which catalogue answers — the quote names the company

This is the half that makes the rest usable, and it is smaller than it looks.

**The live quote is in memory.** `app/store.py` says so at the top: "State lives
in process memory (single-node demo)." `Quote.organizationId` is stamped at
`QuoteStore.create` from the principal. So the live path needs **no migration**:

- `Quote` gains `connectionId`; `QuoteStore.create` takes and stamps it.
- The Quote Builder gains a company picker, next to the customer picker, and
  disables quoting until one is chosen where an org has more than one company.
  Where it has exactly one, it is chosen silently — a picker with one option is
  a question with one answer.
- `Line` resolution passes the quote's `connectionId` into `pie_service.resolve`.

**`QuoteDraft` is dead code.** It is defined at `models.py:1625` and constructed
nowhere in `backend/app`. Do not add a column to it; that would be a migration
for a table nothing writes. Worth deleting separately.

**`QuoteDocument`** — a quote this platform wrote back into a source system —
*should* record the connection, for the same provenance reason the stamp exists.
That one is a real migration, and a small one.

**The public resolution API is a contract change.** `routers/resolve.py` takes
text and returns a provenanced answer with no company in it. It gains an
optional company argument; absent, it answers from the org's only company and
**refuses with a named error where the org has several** rather than picking.
That refusal is the §1 rule applied to this surface: an answer from an
unspecified catalogue is not a provenanced answer, and guessing which company
the caller meant is exactly the benign default the invariant forbids.

---

## 4. The index cache

`PieService` holds one index per process today, plus a remembered failure so a
missing catalogue is not retried per row.

It becomes a **small bounded cache keyed by `connection_id`** — an LRU of two or
three resident indexes, not one per company without limit. Each index is a
decoded 6,700-record structure; a deployment with a dozen companies holding
every one resident is a memory profile nobody measured. The per-key failure memo
survives, keyed the same way, for the same reason.

`pie_service.reload()` gains a `connection_id` argument and evicts one key
rather than everything. A rebuild for one company must not cost every other
company its warm index.

**Measure before choosing the bound.** The current build is 1.7 s and the index
load is not separately timed; the eviction rate at the chosen bound is the thing
to look at, not the bound itself.

---

## 5. What else reads `PIE_PACK`, and what it means for them

Four consumers, and only one of them is the catalogue build:

- `catalog.build_catalog` — takes the company's pack. Direct.
- `pie_service.pack_families` — the family vocabulary. Per company now.
- `commercial/policy.save_for_org` — **validates family names on save, per
  organization.** With per-company packs an org has several vocabularies. It
  should validate against the **union** of its companies' declared families, and
  keep the existing refusal when no pack is readable at all. Validating against
  one company's pack would reject a family another company legitimately declares.
- `master_health/geometry` — measures an export against a pack. Takes the
  company whose export is being measured.

None of these can keep reading a deployment-wide default once it is gone, which
is why §6 removes it last rather than first.

---

## 6. Removing the default, without a silent regression

The order matters, and the risk is entirely in this section.

Every existing deployment resolves today against one catalogue built from the
pinned corpus. Deleting `settings.PIE_CATALOG` as the source without putting
something in its place turns every existing quote line UNRESOLVED on deploy —
a regression that looks exactly like the engine being down.

So:

1. **Seed, then remove.** A data migration assigns the existing default
   catalogue to each organization's first enabled connection, recording the
   shipped corpus's checksum as its `corpus_id` provenance. An org with no
   connection gets nothing and correctly reports NOT BUILT.
2. `PIE_CORPUS` and `PIE_PACK` survive as the **seed** for that migration and
   for local development, not as a runtime fallback. `PIE_CATALOG` goes.
3. `AUTO_BUILD_CATALOG` becomes per company: build on first use from that
   company's stored corpus, or report NOT BUILT. It must never fall back to
   another company's catalogue — that is the one outcome worse than an empty
   screen, because it answers with the wrong manufacturer's product and stamps
   it as provenanced.

The screen this branch shipped already renders NOT BUILT as its own state
rather than zero coverage, so the honest end state is already drawn. It gains a
company selector and drops the "Deployment-wide" sentence.

---

## 7. Tests this needs

The interesting ones, beyond the obvious CRUD:

- **A company never answers from another company's catalogue.** Two connections,
  one with a catalogue and one without; a quote naming the second resolves
  UNRESOLVED, not from the first. This is the test the whole change exists for.
- **An org with several companies and no company named** gets a named refusal
  from the resolution API, not an arbitrary catalogue.
- **A lost disk costs a rebuild, not the data.** Delete the built JSONL, assert
  the catalogue rebuilds from the stored corpus row and lands on the same
  `ruleset_checksum`.
- **Upload limits**: oversize rejected, wrong content-type rejected, a corpus
  whose mapped columns are absent rejected with the column named.
- **Tenant isolation on the corpus rows**, under the RLS suite that already runs
  against a non-bypassing role.
- **The seed migration** leaves an existing single-connection deployment
  resolving exactly as before — same checksum, same record count.

---

## 8. Sequencing

Two changes, because the risky half should be isolated:

**PR 1 — the plumbing, default still answering.** Tables and migration, corpus
upload with its limits, pack selection per company, per-company build and index
cache, the screen gaining a company selector. The deployment default still
answers resolution. Nothing regresses because nothing has been taken away.

**PR 2 — the cutover.** The quote names the company, the resolution API gains
its argument and its refusal, the seed migration runs, the default is removed,
and the other three `PIE_PACK` consumers move over.

**Phase 2, separately** — uploaded pack bundles, if still wanted, with the regex
budget and sandbox §1.2 describes.

---

## 9. The four open questions, answered

Settled before PR 1 rather than during it. Recorded here because a decision that
lives only in a chat log is a decision the next person re-litigates.

1. **Where an uploaded corpus lives → database rows.** Durable across the
   redeploys §1.1 describes, inside the backup and restore drill that already
   runs in the gate, RLS-scoped, and carrying its own provenance. Object storage
   was declined as new infrastructure for a few megabytes; a mounted volume was
   declined because `railway.json` declares none, which would make durability
   differ by environment — the worst of the three outcomes.

2. **How a company gets its pack → chosen from the packs the pinned engine
   ships.** Phase 1 stores an identifier, not a bundle. This is the decision
   that keeps PR 1 free of the regex-execution surface in §1.2: no tenant-
   supplied pattern is compiled or run. Uploaded bundles remain possible later,
   with the validation, complexity budget and killable build they require.

3. **`QuoteDraft` → left alone.** No `connection_id` column: a migration for a
   table nothing writes is pure cost. Not deleted here either — dropping a table
   is its own decision with its own migration, and folding it into a tenancy
   change would muddy both diffs. Wiring it up so quotes actually persist is a
   real and separate project; `store.py` already says "a real deployment
   persists quotes".

4. **The resolution API with no company named → refuse, listing the valid
   company ids.** A 422, not a guess. An answer from an unspecified catalogue is
   not a provenanced answer, and silently choosing one is the benign default §1
   forbids. An organization with exactly one company still answers with no
   argument, so single-entity callers see no change.

## 10. Still to decide, but not blocking PR 1

- The **index cache bound** (§4) — pick it from a measurement of eviction rate,
  not in advance.
- Whether `commercial/policy` validating family names against the **union** of an
  org's packs (§5) is right, or whether an org editing policy should name a
  company for that too. The union is the conservative choice: it rejects only
  names no company declares.
