# Per-company decoded catalogues

**Status: built, both parts, plus §10, §12 and §13.** PR 1 brought the tables, upload, pack
selection, per-company build and the screen; PR 2 was the cutover — the quote
names its company, the resolution API gained its argument and its refusal, the
shipped corpus became a *seed*, and the deployment-wide catalogue was removed.
Read the plan below as the record of why it is shaped this way; where the build
differs from the plan, it is marked at the point it differs.

**§10 is the part that overturns something above.** A company keeps *several*
source files rather than one, each with its own column mapping, merged into one
catalogue at build time — and the pack is chosen against the parser's counts on
the company's own files instead of by its identifier. §2's single `corpus_id`
and §1.2's "the corpus must be phrased like the pack" are both superseded there.
Read §10 before acting on either.

Four things landed differently from the plan and are marked where they occur:
the corpus arrives as a **raw request body**, so `python-multipart` was never
added and `master_health`'s dependency refusal still stands; the **index cache
moved to PR 2**, because nothing in PR 1 read a per-company catalogue and a
cache with no caller cannot be measured; the **seed is a start-up step rather
than a data migration** (§6); and `master_health` takes a **`--pack` flag
rather than reading the company's stored choice**, because reading it would
have needed the database that module refuses.

Before this, the decoded catalogue was deployment-wide: one `PIE_CATALOG` path,
one `PIE_PACK`, one index per process. This planned the move to **one catalogue
per connected company**, each with its own pack and its own uploaded corpus,
and the removal of the shared default.

It was written first because three of the decisions below overturn something
the codebase had already decided on purpose, and one of them breaks on the
deployment we actually run.

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

**As built, only half of it was re-opened.** The corpus arrives as a raw request
body (`payload: bytes = Body(...)` on a sync endpoint) rather than as a
multipart form, so `python-multipart` was never installed and the *dependency*
this refusal declines is still declined. One CSV needs no form fields, the
browser sends a `File` as a body directly, and the narrower mechanism was
simply the better one. `master_health/__init__.py` now records this beside its
refusal, so that paragraph does not read as false to the next person who greps
for `UploadFile`.

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

1. **Seed, then remove.** Each organization's first enabled connection is given
   the shipped corpus as its own, and its catalogue is built from it. An org
   with no connection gets nothing and correctly reports NOT BUILT.

   **Not a data migration, which is where this departs from the plan.** The
   corpus lives inside the pie-parser submodule, which `deploy/backend.Dockerfile`
   says an image may be built without, and `railway.json` runs
   `alembic upgrade head` as a *pre-deploy* command. A migration would therefore
   either fail that deploy or seed nothing exactly once, permanently — while a
   start-up step retries on the next boot, when the submodule or the
   organization's first connection has arrived. It is also the only form that
   can read `settings.PIE_CORPUS` and `catalog.pack_for` rather than restating
   both, which §4's rule against migrations importing application code would
   otherwise have forced. `catalog.seed_company_catalogues` and
   `catalog.ensure_company_catalogues` are the two functions; `app/main.py`
   calls them at start-up and `scripts/build_catalog.py` is the same pair for a
   machine where the app is not running yet.
2. `PIE_CORPUS` and `PIE_PACK` survive as the **seed** for that migration and
   for local development, not as a runtime fallback. `PIE_CATALOG` goes.
3. `AUTO_BUILD_CATALOG` becomes per company: `ensure_company_catalogues` builds
   any company whose corpus is on record and whose decoded file is not, at
   start-up rather than on first use — `pie_service` holds no session and
   giving it one to rebuild mid-resolution would put a two-second parse on the
   hot path. With it off, a company reports NOT BUILT and somebody builds it
   from the screen. It never falls back to another company's catalogue — that
   is the one outcome worse than an empty screen, because it answers with the
   wrong manufacturer's product and stamps it as provenanced.

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
upload with its limits, pack selection per company, per-company build, the
screen gaining a company selector. The deployment default still answers
resolution. Nothing regresses because nothing has been taken away.

**The index cache moved to PR 2, on second look.** §4 puts it here, and that was
wrong: nothing in PR 1 *reads* a per-company catalogue — the screen renders from
the catalogue row, not from a loaded index — so an LRU keyed by connection would
ship with no caller at all. That is the speculative generality §7 warns about,
and an unused cache is worse than none: it cannot be measured, so the bound
would be guessed and then inherited as though it had been chosen. It belongs
with the cutover that gives it a consumer.

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

## 10. Several files per company, and the mapping (built)

The design above gives a company **one** export at a time: an upload supersedes
whatever was there. That turned out to be the wrong grain, and for a reason
worth writing down rather than a preference.

The exports do not arrive as one file. There is an item master out of the ERP, a
manufacturer's range extension for products the master has not caught up with,
and a price list covering a line bought this quarter. Building from one of them
means the other two are not in the catalogue — and a part number that is not in
the catalogue resolves to UNKNOWN, which looks exactly like the engine being
wrong about a product it has simply never seen.

So a company keeps a **set of sources** (`company_corpora.source_key`) and the
build merges them. Three consequences, in the order they bite:

**De-duplication is required, not tidying.** `identity/store.py`'s
`AuthoritativeIndex` indexes identifiers per namespace and treats a duplicate
inside one namespace as a collision that **never resolves** — so two files both
emitting part number `A` would not produce a wrong answer, they would silently
stop `A` resolving at all, which no record count reveals.
`catalog.combined_corpus` therefore de-duplicates by record id, **newest source
wins** (a later file is a later statement about the same product), and counts
every collision into `company_catalogues.ingest` so the overlap is reported
rather than absorbed.

**Staleness became a set comparison.** `row.corpus_id != corpus.corpus_id` could
not see a source *added* or *removed* — both leave the newest id untouched — so
the catalogue went on calling itself current while a rebuild would have produced
a different one. `corpus_digest` is a hash over every live source's key and
content digest. The old comparison survives as the fallback for rows built
before the column existed, where it is still honest.

**A file keeps its own column names.** `validate_corpus` refused any upload
whose headers were not the ones the chosen pack declares, and the message named
`MM#`. That made every export other than the one this platform was written
against unusable, and the fix it implied was to rename spreadsheet columns to
match a pack the person cannot see. It is a stored **mapping** per source now
(`company_corpora.mapping`): `ingestion/item_master.suggest_mapping` guesses it
from the headers at upload, an owner corrects it from the screen, and
`combined_corpus` renames the columns on the way into the parse. `pack_columns`
is the single place the pack's declared names are read, because the parse and
the normalisation must agree exactly or the parse finds no columns at all.

**Excel, and no new dependency.** `openpyxl` is already in
`requirements.txt` — pie-parser pulls it in — so a workbook is read directly
(`read_only`, `data_only=True`, so a price list full of formulas yields the
values a person sees). `python-multipart` is still not installed: the browser
sends one file as a raw body. PDF was considered and left out; it is the least
reliable input and would be a real dependency decision, so it stays a
conversion the person does.

**Reading a file is streamed, and that was measured.** Materialising a 33 MB
CSV as lists of cells peaked at **394 MB** of resident memory — for one file, on
an upload an owner can repeat, on a container sized in hundreds of megabytes.
`item_master.Table` holds the bytes and re-reads them per pass instead, and
`combined_corpus` writes each row straight to the temp file the parser reads,
remembering only the record ids it has already emitted. Two 33 MB files now
merge inside 335 MB peak, and finding the header row went from 5.9 s to 0.18 s.
`CompanyCorpus.content` is `deferred` for the same reason: the listing endpoint
asks for no blob at all, and a build expires each one after writing it out.

The sources are therefore read **newest first** — the winner of a collision is
then the first row seen, so only the keys have to be remembered rather than the
rows. Nothing downstream depends on a corpus's order, and a given set of files
still produces the same bytes.

**Nomenclature only, structurally.** The normalised corpus carries the three
mapped columns and nothing else — a price column is absent from it because it
was never written, not because a filter removed it, which is the same reasoning
CLAUDE.md §1 gives for the server omitting cost rather than the browser hiding
it. `ingest` still *names* every dropped column, commercial ones first, because
"12 columns ignored" is not a claim a person can check against their own
spreadsheet.

### Choosing a pack on evidence

`zcnc` says nothing about whether it reads a given export, which made the pack
selector a dropdown of identifiers to guess between. `catalog.pack_fit` runs
each shipped pack over the first `SAMPLE_ROWS` of the company's real files and
reports **the parser's own counts** — classified, quarantined, the per-family
census. It computes no score: a single "83% fit" would be the second parse-rate
calculation `run_parse` refuses to have, and it would hide the distinction that
decides the choice, since a pack that classifies few rows is wrong for this
export while a pack that errors could not read it at all.

It is safe for exactly the reason §1.2 gives: it executes only the packs the
pinned engine ships, so no tenant-supplied pattern is compiled. Uploaded pack
bundles remain phase 2, unchanged.

And the empty case is now stated. `deploy/backend.Dockerfile` builds an image
without the private submodule on purpose, so `packs: []` is a legitimate
production state — and the screen rendered it as a dropdown that opened onto
nothing, with the server's own explanation sitting unused in `source.reason`. An
empty control that does not say why is the interface's version of the benign
default §1 forbids.

## 11. Still open, now that both parts are built

- The **index cache bound** (§4) is three, chosen against the shape of the
  business rather than a benchmark. Revisit it from a measurement of the
  eviction rate, not in advance.
- `commercial/policy` validates family names against the **union** of the
  organization's companies' packs (§5), which is the conservative choice: it
  rejects only names no company declares. Whether an org editing policy should
  name a company for that too is still open — it would be a narrower check and
  a longer question to answer at the screen.
- `master_health` takes `--company` for the catalogue and `--pack` for the
  pack, separately, because that module reads no database and the pairing lives
  in `zoho_connections.config`. Passing mismatched ones is possible and nothing
  stops it; the report names both. Closing that would mean either giving the
  module a session (overturning a refusal its `__init__` argues for at length)
  or a portal-side entry point that resolves the pair — the second is the
  better shape if it is ever worth doing.
- Uploaded **pack bundles** remain phase 2, with the regex budget and sandbox
  §1.2 describes. §10's pack trial narrows what they are *for*: a tenant can now
  see which shipped pack reads their export, so the remaining case is a
  distributor whose phrasing no shipped pack covers at all — which is a pack
  somebody writes in pie-parser, not one uploaded through a browser.
- **Newest-wins on a collision** is a policy, chosen because a later file is a
  later statement about the same product. The alternative worth considering is
  an explicit precedence per source, which is a real answer for a company whose
  item master should always beat a supplier's price list. It needs a screen to
  set it and it is not obviously worth one; the collision count is what would
  say whether it is.
- **A workbook's first sheet only.** Which sheet of a multi-sheet workbook is
  the item master is a question `item_master` cannot answer, and concatenating a
  "Discontinued" tab into the catalogue is worse than reading one sheet. It is
  logged, not surfaced. A sheet picker is the obvious fix if a real file needs it.

## 12. Retrieval: the nearest descriptions as extra options (built)

§10 answered "the pack does not read my file". This answers the next thing a
person says: "the engine could not read my *line*". The rule engine resolves a
requirement by decoding the text to a spec and scoring every record against
it, which is exact and explainable and blind to anything the pack has no
grammar for. Two lines from the shipped corpus show the two ways that bites:

- `VSM11 milling insert r1.2` decodes to *milling insert, R = 1.2*. Every
  R = 1.2 milling insert in the catalogue scores 1.0, the ranking ties dozens,
  and `TOP_N` cuts the list at six KSOM inserts. The three records that
  actually say `VSM11` are never shown.
- `12mm carbide drill through coolant for stainless` decodes to *drill,
  12 mm*. The engine ranks the 12 mm drills correctly, but the four words that
  said which drill — `through coolant`, `stainless` — are not in the spec and
  cannot move the ranking.

`app/retrieval` asks a different question of the same catalogue: *which
records' descriptions read most like this text?* It is a **candidate generator
and nothing more**, and the lines that keep it one are the whole design:

1. **The engine compares every retrieved record.** Each goes through
   `equivalence.distance.compare_geometry` against the spec the engine decoded
   from the text — the same function, gates and tolerance model the ranking
   uses. A record the gates reject (a reamer whose description happens to
   contain the insert code) is dropped. One the engine could not compare on any
   dimension is `unverified`, exactly as a ranked suggestion would be.
2. **Similarity never becomes a relationship.** A retrieved record is
   `POSSIBLE` whatever it scored, carries no `score`, and is flagged
   `retrieved`. §1's "an equivalence score is policy, never an identity" is
   about the engine's own geometry score; a text-similarity score is further
   from an identity than that, and it is never persisted as anything.
3. **Never the answer.** Retrieved records are appended after the engine's
   ranked suggestions, and the auto-selection decision in `pie_service._map`
   is taken on the ranked list *before* they are appended. When the engine
   ranked nothing and retrieval found something, the line is `AMBIGUOUS` with a
   note saying the options are nearest descriptions, not a shortlist — the
   same abstention a tie gets, with its own sentence so the reader knows which
   of the two happened.
4. **Deterministic, stamped, and offline.** The embedding is a hashed
   character-n-gram model (`retrieval/embedder.py`): no learned vocabulary, no
   network, no dependency, `zlib.crc32` rather than Python's per-process salted
   `hash`. The index (`retrieval.jsonl`, beside `products.jsonl`) is stamped
   with the model id, the dimension and the SHA-256 of the catalogue it was
   built from; a stamp that does not describe the file or the model is
   rebuilt, and an unreadable file is rebuilt rather than trusted. The same
   catalogue through the same model produces the same bytes and the same
   neighbours, in this process or the next — `test_retrieval` pins it, which
   is what lets a retrieved option be explained months later the way a ranked
   one already can.
5. **A floor, chosen against the corpus.** Every text has a least-unlike
   record. Below `MIN_SIMILARITY` (0.2) nothing is offered: on the shipped
   corpus a paraphrase of a real description sits near 0.7, the drill request
   above near 0.28, and `6205 2RS C3 bearing` at 0.13. The bearing and the
   drill are both pinned, so a change to the model or the floor that lets one
   through or shuts the other out fails a test rather than a quote.

The index is built at the end of `catalog.build_for_company` and never fails a
build — the catalogue is the deliverable and the index is derived from it — so
a build that could not write one, or a catalogue built before this existed,
gets its index on the first requirement line instead. On the shipped 6,717-row
corpus: 2.7 s to build, 11 MB on disk, 0.75 s to load, single-digit
milliseconds a search. The screen's fact panel shows the model id and record
count as provenance, and says when the index is behind the catalogue.

Off switch: `PIE_RETRIEVAL_TOP_K=0`. The engine's own answer is byte-identical
either way; retrieval only ever adds options beneath it.

**Layering.** `retrieval/` is in `test_layer_boundaries.DETERMINISTIC`, and
that is not a formality: `catalog.py` and `pie_service.py` import it, every
deterministic package imports those, and the closure test walks every import.
A retrieval layer that reached `ai/` — a hosted embedding behind a BYOK key —
would carry the whole of `commercial/` with it. That is why the first embedder
is local and why a neural one is the next slice rather than this one: it plugs
in behind the same `features` protocol, injected from a layer that may reach
interpretation, with its own model id on the stamp so two indexes are never
read as one.

**What "learning" means here, and what it does not.** The catalogue is
nomenclature; the price columns are dropped at the door (§10) and nothing in
this package sees them. The one place the index learns from people is
`retrieval/aliases.py`, and it learns from exactly one act: a confirmed
customer-code mapping (`identity.confirm_proposed_identity`, §1's gate) — a
person asserting "this customer's code means this product".

The engine already reads those exactly: the same code, from the same customer,
resolves authoritatively and retrieval never sees it. What the alias index
adds is the *near miss* — `PITTI 7781 x 10 pcs urgent` for a confirmed
`PITTI-7781`, a hyphen dropped, a label in front — which the engine cannot read
as the code and would otherwise answer with nothing the customer meant. The
confirmed record is offered, listed before description neighbours because a
person's confirmation is stronger evidence than a description that reads
alike, flagged with the code it matched (`alias` on the candidate,
`found_by: "confirmed_code"` on the API), and under every refusal above:
compared by the engine, POSSIBLE, unscored, never selected.

Three rules that keep it honest:

- **A customer's codes answer only that customer.** A code is meaningful
  inside the relationship that confirmed it; another customer's `PART-0042` is
  a different part. A line with no linked customer searches no aliases, and the
  provenance says so (`aliases_searched: 0`).
- **A confirmed record another company's item master carries is not this
  company's to quote.** The mapping is the organization's, the catalogue is one
  company's, and a record the catalogue does not hold is dropped like any
  other.
- **The gate does not move.** Selecting the offered record on that line is a
  substitution on one quote, as it always was; nothing here files a mapping,
  and the exact code still needs the exact answer to be recorded.
- **The engine's hard gate is stated, not obeyed, for a confirmed code.** On
  the shipped corpus the engine reads `PITTI 7781 x 10` as an ISO P-shape and
  the confirmed insert is a C-shape, so the gate that rightly drops a
  description neighbour would drop the one record the customer meant. A
  shape guessed from the letters of a customer's own code is weaker evidence
  than the person who confirmed the code, so the record is kept — unverified,
  with the engine's objection written beside the confirmation for the reader
  to weigh. Description neighbours, which carry no confirmation, keep the gate.

The alias index is built from `OrgMappingStore.aliases()` — the same snapshot
the engine's authoritative lookup reads — and memoised in `pie_service` by the
store's fingerprint, the value the resolution cache already keys on, so a
correction is seen on the next line and a 200-line quote builds it once. It is
small (an organization's confirmed codes are hundreds of short strings) and
deterministic like the catalogue index.

**Phrases: learning from a person's choice (built).** A confirmed code is one
act of teaching, and the gate that files it is narrow on purpose. The other
act happens on every quote: a person puts a product on a requirement line —
words, not a code — for a linked customer. That choice is now recorded
(`identity.service.record_phrase_alias`, from `routers.quote.select_supply`)
in its own table, `customer_phrase_aliases`, and indexed as an alias of the
`"phrase"` kind, so the next time this customer writes something close the
record is offered back: "quoted before for *12mm drill for SS*".

Its own table, and not a row kind on `confirmed_code_mappings`, is the design:
a confirmed mapping is asserted identity the engine resolves authoritatively
and derives requirements from; a phrase asserts nothing. `OrgMappingStore`
reads phrases into the snapshot and the fingerprint but never into `lookup`,
so nothing the engine reads can be reached through a phrase, and the
composition of tolerances §1 forbids has no new path. On the wire it is
`found_by: "prior_choice"` with `prior_phrase`, and the chip says "quoted
before", never "confirmed".

What is recorded, and what is refused: any selection on a line the engine read
as words (`REQUIREMENT` or `MIXED`), for a linked customer, where the chosen
code is not the line itself — a substitution included, because "last time you
quoted them Y for this" is exactly what the next person needs to see and is
free to ignore. Nothing on an `IDENTITY` line (a code goes through the gate or
nowhere), nothing for an unlinked customer, and nothing the engine chose on
its own. The same words chosen for a different product supersede the old row
rather than stacking, so the newest choice is the one offered. There is no
screen to retire an alias yet; superseding it by choosing again is the only
correction, and that is the first thing to add if a wrong one proves sticky.

### The learned vocabulary (built)

A phrase alias is one customer's words beside one chosen record. Across a
tenant's aliases those pairs add up to something more general, and
`retrieval/vocabulary.py` counts it: which of the tenant's words go with which
technical attributes of the records people chose — family, application group,
coating, series, shape. At one tenant `SS` appeared in 41 requests and 39 of
the chosen records were M-group; `BOHRER` appeared in 5 and all 5 were
solid-carbide drills. That is the tenant's vocabulary, learned from the first
pair, computed from one organization's rows only, and never shared.

Three scopes, in order: the **customer's** own usage where that customer alone
has enough pairs for the word (a customer whose `SS` means something else keeps
their meaning); the **tenant's** usage across all its customers otherwise;
nothing when neither has `MIN_SUPPORT` (3) pairs agreeing at `MIN_SHARE` (60%).
A word used for several things is read as none of them.

What a hint does, and what it may not do. It is evidence about *words*:

- it widens the description search with the attribute's own tokens, so
  `BOHRER 12MM` reaches the 12 mm drills once five quotes have said what
  `BOHRER` means here — the engine has no grammar for the word, and without
  this the line's options were the first six records by sort order;
- it is written beside a retrieved candidate that agrees with it ("agrees with
  what 'BOHRER' usually means here: product_family=solid_carbide_drill, 5 of
  5"), and reported on the line with every count behind it
  (`engine.retrieval.vocabulary`);
- it never enters the engine's spec, never changes a relationship and never
  selects. The geometry comparison stands between a word and a product exactly
  where it stood.

Deterministic: counts over sorted rows, named thresholds, ties broken by value,
memoised per store fingerprint and catalogue in `pie_service`.

**The bootstrap.** A tenant that has quoted through the platform already made
the choices this learns from; they are in `quote_drafts.lines`. `python -m
app.retrieval.backfill [--org X] [--dry-run]` replays them through the same
writer the quote screen uses, under the same rules, so a backfilled alias
differs from a live one only in its `source_ref`. `enquiry` lines are not
pairs — a disposition names a quote, not a product — so they are not read.

**A reading reaching the ranking (built).** One learned reading may enter the
engine's *ranking*, and only one kind: a `product_family` the engine could
not decode from the text, when the tenant's quotes have taught the word and
the family has a word the engine's own fuzzy decoder reads (`FAMILY_WORDS`:
`BOHRER` → `drill`). `pie_service._with_ranking_reading` resolves the line
again with that word appended; the second result is used only if the engine
did decode that family from it, so the engine still applies its own gate and
decodes every dimension itself. The line keeps its original text, the note
says "Read 'BOHRER' as drill — this tenant's usage in 5 of 5 quotes; the
ranking below was made with that reading", and `engine.retrieval.ranking_reading`
carries the evidence. Everything else about the ranking — scores, bands,
auto-selection, the discrimination and vacuity guards — is untouched: the
engine was handed one word, in its own vocabulary, with the counts that
justify it.

### Measuring it (built)

`GET /catalog/retrieval-report`, `python -m app.retrieval.report`, and the
"Suggestions at work" panel on the Decoded catalogue screen count, from the
quotes a tenant stored, what each layer is doing: of a person's choices on
customer-linked requirement lines, how many took a record found beneath the
ranking (retrieval, a confirmed code, a phrase), how many were typed in with
nothing offered, how many the engine chose on its own; and how much has been
learned. Two thresholds are printed beside the numbers as the reading: a fifth
of choices found beneath the ranking says the ranking is the bottleneck; a
fifth typed unoffered says the gap is meaning, and two thousand pairs is the
floor for training on it. A share of nothing is `null`, never `0`.

### Retiring a memory (built)

"What the system remembers" on the same screen lists every active phrase
alias with the customer, the words, the product and the quote it came from.
A manager may read it; an owner may retire one. Retiring deactivates and
audits (`PHRASE_ALIAS_RETIRED`); it never deletes, because the quote it came
from still cites it. A retired phrase leaves the mapping store's snapshot and
fingerprint at once, so the alias index is rebuilt on the next line.

### A meaning-aware embedder (the seam is built; the model is yours)

`retrieval/dense.py` is where a small language model plugs in. The protocol is
a `model_id`, a `dim`, and `embed(texts) -> unit vectors`; `OnnxEmbedder`
runs a sentence-embedding model exported to ONNX from a local directory —
`model.onnx` and `tokenizer.json`, no network at build or quote time, the
weights' hash as the model id on every stamp. `DenseReranker` re-orders the
hashed index's candidates by cosine of dense vectors, **re-rank rather than
replace**: the hashed index stays the candidate source because on codes and
designations it is the better instrument, and scoring only the handful of
candidates costs a few vector products. Record vectors are computed on first
use and persisted beside the catalogue, named by model, so the next process
reads them back. Downstream nothing changes: a re-ranked candidate is still
compared by the engine, still POSSIBLE, still never selected; the dense
similarity is carried as provenance ("0.81 by meaning"), never as the score.

To turn it on: `pip install -r requirements-embed.txt`, put a model directory
on the host, set `PIE_EMBEDDER_MODEL_DIR` to it, restart. Without those three
the code path is inert and retrieval answers exactly as before.

Which model. Any sentence-embedding model exportable to ONNX runs as is. A
general one knows English, not the trade; the one worth having is tuned on the
tenant's own pairs, and that is what `python -m app.retrieval.export_pairs
--org X --catalogue <products.jsonl> --out pairs.jsonl` writes: one line per
active phrase alias, the customer's words as the anchor and the chosen
record's indexed text as the positive, one tenant per file — a model tuned
on one tenant's pairs is that tenant's. The training is not in this codebase
(it needs `torch` and `sentence-transformers`, far heavier than anything the
backend runs) and is a one-off, offline:

```
pip install sentence-transformers optimum[exporters]
python - <<'PY'
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader
import json
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
pairs = [json.loads(l) for l in open("pairs.jsonl")]
examples = [InputExample(texts=[p["anchor"], p["positive"]]) for p in pairs]
loader = DataLoader(examples, shuffle=True, batch_size=32)
model.fit(train_objectives=[(loader, losses.MultipleNegativesRankingLoss(model))],
          epochs=3, warmup_steps=50)
model.save("tuned")
PY
optimum-cli export onnx --model tuned --task feature-extraction model_dir/
```

`model_dir/` then holds `model.onnx` and `tokenizer.json`. Two thousand pairs
is the floor below which this is not worth running; the report above says
where a tenant stands.

**Why the model could not be proven here.** The model host was not reachable
from the environment this was built in and no tenant has two thousand pairs
yet, so the seam is tested against a fake embedder that satisfies the
protocol — re-ranking, persistence, provenance and the model id on the stamp
are pinned; the quality of any particular model is not.

## 13. Several catalogues per company, and no default decoder (built)

Two things were wrong, and they are one change.

**A company had one catalogue.** One set of files, one pack, one
`products.jsonl`. A distributor sells several manufacturers, so that never fit.

**A decoder was a fact about the company.** The pack lived on
`zoho_connections.config["pie_pack"]`, so every file a company uploaded was
decoded through whatever it had chosen once — and "which pack does this company
use" has no answer for a company selling Kennametal and YG-1. Worse, it made
the wrong thing easy: a YG-1 price list uploaded into a company set to `zcnc`
decoded through Kennametal's grammars and produced a catalogue that was wrong
while carrying a real provenance stamp.

### The shape now

```
Catalogue (the company's canonical product knowledge, the union)
└── Manufacturer catalogue          company_catalogues, keyed catalogue_key
    ├── Price list 1                company_corpora
    │   └── Decoding config 1       columns + rule set, on that row
    └── Price list 2
        └── Decoding config 2
```

A **catalogue** is a manufacturer's product universe and owns no decoder. A
**price list** is one uploaded document. A **decoding config** is what it takes
to decode *that* document: which of its own columns hold the part number, the
description and the grade, and which **rule set** decodes its descriptions. A
rule set is what pie-parser keeps as an org-layer pack; the portal calls it by
what it is to the portal, because that is the only thing it is here.

**There is no default.** `settings.PIE_PACK` names the rule set the shipped
seed corpus was written against, and it is read in exactly two places: the seed
(§6), which stores it as that one file's own saved config, and `master_health
--rule-set`'s error message. Nothing uploaded is ever decoded through it.

### The upload flow

Upload → the file is read as a table and its columns settled → **every** shipped
rule set is run over its first rows and the parser's own counts reported for
each → a config is *proposed* → a person checks it and saves it
(`PUT …/sources/{key}/decoding`) → the build decodes the file through it.

`catalog.analyze_source` does the discovery, and what it refuses to do is the
point: it proposes a rule set only when exactly one classified any sampled row.
Where several read the file the counts are shown and a person chooses — ranking
them by a number this module invented would be the second parse-rate
calculation `run_parse` refuses to have. Where none reads it, the proposal is
empty and the reason says so: that file needs a rule set nobody has written
yet, and no amount of choosing from the menu fixes it. A proposal is never a
config. `decoding_confirmed_at` is what a build looks at, and
`POST …/sources/{key}/analyze` re-runs the discovery on a stored file without
touching a saved config.

**Failing clearly rather than falling back.** A build with any file lacking a
saved config is refused with the files named
(`Not decoded: yg1-prices.xlsx has no saved decoding config…`), the catalogue
reports `decoding_ready: false` and `awaiting_decoding: [...]`, and the screen
says so on the file's own row. `ensure_company_catalogues` logs it and leaves
the catalogue NOT BUILT rather than decoding it at boot.

### The build

`build_for_company` decodes **each file on its own** through its own rule set
and merges the decoded records afterwards, where the old code normalised every
file into one CSV and parsed it once through one pack. So each file carries its
own stamp — its `run_id` over its own bytes and its own rule set — and
`company_catalogues.sources` holds one entry per file with that stamp, its rule
set and the parser's own report for it. The row's own stamp fields are null
where two files disagree, and `report` is null with more than one file: summing
two censuses here would be the recomputation the parser has already made
unnecessary.

Merging moved to `_merge_decoded`, which is the collision rule stated once and
used twice — across the files of one catalogue, and across the catalogues of
one company. pie-parser's `AuthoritativeIndex` treats a duplicate identifier
inside one namespace as a collision that never resolves, so a merge that kept
both rows would silently stop that part number resolving; the newest statement
wins (a later file, a later build) and the overlap is counted and named. Each
kept record is tagged with where it came from — `source_key` inside a
catalogue, `catalogue_key` in the union — which is how a resolution says which
manufacturer's catalogue answered without a second map to keep in step.

### The union, and what a company resolves against

`catalog.union_catalogue` merges every built catalogue into
`data/catalogues/<connection_id>/_union/products.jsonl`, and that one file is
what `pie_service._view` loads and hands the engine as `pie_data`. The engine's
identity step reads one path (`resolve_rfq._identity_resolver`), so a union
file keeps that step exactly as it is — no engine change, no second index. It
is assembled from the disk (each catalogue's `catalogue.json` sidecar) so it
can be built in the resolver, at start-up, or in a test that linked a file into
place, and it is rebuilt when a member's file changes.

`catalog_version` is a hash over each member's key and the **run ids of the
files it was built from**. The ruleset checksum alone would not do: it is the
rule set's hash, identical for two companies decoding different item masters
through `zcnc`, so a resolution cache keyed on it could hand one company the
other's answer. One rule whatever the count, so the value means the same thing
for a company with one catalogue as for one with five.

### What moved, and what the migration does

`m1cats` re-keys `company_catalogues` to
`(organization_id, connection_id, catalogue_key)` — in place on Postgres,
because a batch recreate would drop `h2rls`'s tenant policy; via batch on
SQLite, which has no policies and cannot alter a key in place. It adds `name`,
makes `built_at` nullable (a row is a definition before it is a build) and
drops `pack`. It adds `catalogue_key` and the decoding-config columns to
`company_corpora`, files every existing row under `default`, and **carries the
company's recorded pack choice onto each of its live files** as that file's own
saved config, marked confirmed by the migration — a recorded decision, not a
default applied to an unknown file. A company holding files but no catalogue
row gets its `default` definition. Downgrade supersedes rather than deletes the
non-default files.

`commercial.policy` validates family names against the union of the rule sets
this organization's saved configs name (`catalog.rule_sets_in_use`), and
`master_health` takes `--rule-set` with no fallback: absent, nothing is decoded
and geometry coverage is UNKNOWN, which is what that module says everywhere
else.

### Accepted, and stated

The union manifest is overwritten on every refresh, so a composite version on
an append-only quote snapshot names *a* set of builds but cannot be decomposed
once a member has been rebuilt; the per-line `catalogue` the API reports is the
durable half. And only one rule set ships with the pinned engine, so a YG-1
catalogue can be *defined* and its files analysed, but until a YG-1 rule set is
written in pie-parser the analysis will say so and the file will wait — which
is the honest answer, and the one the old default hid.


## 14. Decoding a file with no rule set at all — the artifact (Stage A, built)

§13 removed the *default* decoder: every price list names which shipped rule
set decodes it. This removes the shipped rule sets. A file is decoded by a
**decoder built for that file**, and the work is staged — this section is
Stage A, which is the part that makes the rest safe to build.

### The problem Stage A solves, and it is not extraction

Working out how an unseen file should be read is a judgement. It looks at the
text, proposes structure, and a person confirms it — and no judgement is
reproducible. Decoding, on the other hand, has to be reproducible forever: a
quote raised last March has to be explainable this March, and the record that
answered it has to say what produced it.

Those two are reconciled by a **freeze**, not by making inference
deterministic:

```
read the file → propose a decoder → a person confirms → FROZEN
                                                          ↓
                   file bytes + frozen decoder → records, forever
```

Inference is a *build* step, in the sense that authoring a migration is: the
non-reproducible act happens once, produces a reviewed artifact, and the
artifact is what runs. One rule holds it — **inference never runs at decode
time** — and `app/decoding/` is the machinery for that rule. Stage A ships no
inference at all, deliberately: the artifact is data, so it can be written by
hand, and everything downstream can be built and tested against one before
anything proposes one.

### The artifact

`app/decoding/schema.py`. A decoder is an ordered list of **segments** — one
per shape of description the file contains — each a pattern with named groups,
bindings from those groups to attribute slots, and real rows as examples and
counterexamples. Plus one file-level declaration: which character its numbers
use as a decimal point.

Four properties carry the guarantee:

- **Content-addressed.** `decoder_id` is 16 hex of the sha256 of the artifact's
  canonical JSON — the width pie-parser's own `ruleset_checksum` and `run_id`
  use, because this stands beside those on a record and replaces them. Every
  record carries it and the source file's own sha256, so "what produced this
  row" is answerable from the row alone.
- **Self-contained.** No references out. Nothing can move underneath a stored
  decoder, because there is nothing underneath it.
- **Version-refusing.** The artifact names the executor schema it was frozen
  against, and a different executor **refuses** it rather than doing its best.
  Silent re-interpretation under a new executor is the one failure that would
  destroy the guarantee quietly, so it is an error rather than a behaviour.
- **Validated at freeze, never at decode.** Every rejection happens in
  `freeze()`: the pattern compiles and is safe, every binding names a real
  group and a known slot, no two bindings fill one slot, every example matches
  and no counterexample does. A decode has no decisions left to take.

### Why the pattern check is static

The obvious defence against a pathological regex is a timeout, and a timeout is
exactly what this design cannot have: a wall-clock limit makes the same file
decode into different records on a loaded machine than on an idle one. So
`app/decoding/safety.py` judges a pattern **once, by its shape** — refusing
nested unbounded quantifiers (`(a+)+`), alternation inside an unbounded repeat
(`(a|aa)+`), and backreferences — and rows are bounded to 512 characters before
matching, with the truncation counted rather than silent. It is deliberately
conservative: a wrong refusal costs an author a rewrite, a wrong acceptance
costs a rebuild that never finishes. It also **fails closed** — it walks
Python's own parse tree through a private name, and the day that name moves it
refuses everything rather than passing everything.

### The one thing that is not per file

`CORE_SLOTS` — 50 attribute names. Every file gets its own extraction rules,
but they all extract into the same names, because a quote asks whether this
drill is equivalent to that one and the answer is a comparison of
`cutting_dia_mm` against `cutting_dia_mm`. If each file named its own fields
there would be nothing to compare and equivalence would silently return less.
Declared in the portal on `pie_service.ATTRIBUTE_FIELDS`' reasoning — it is the
portal's statement of what a record may hold, and importing the engine's copy
would let a submodule bump widen it — with a test pinning it as a subset of the
engine's. A fact the vocabulary has no name for is kept as an `ext:` field and
is **never compared**: nothing else knows what it means.

There is no `mm`/`inch` type and no unit conversion. The slot name carries the
unit, so a decoder that read an inch value into a millimetre slot would be a
wrong number with a real stamp — the class of defect this whole design exists
to prevent.

### What the real file taught us

The shipped corpus writes `SC DRILL 5.1mm` and `SC DRILL 11,1mm` in the same
column of the same export. A single decimal convention per decoder was the
first design and it was wrong: under `dot` the comma rows are refused —
correctly, since a comma there could be a thousands separator — at a cost of
several hundred drills. So there is a third convention, `either`, and it is
still a *declaration* rather than a guess: the same text always converts the
same way, whatever else the file contains. What it cannot represent is a
thousands separator, and a file that groups digits must declare `dot` and
accept what that costs. The convention is in the artifact precisely so the
choice is recorded rather than assumed.

### How the guarantee is proved

`tests/decision_platform/test_decoder_determinism.py`, and the two kinds of
test there do different jobs. Most compare one decode to another in the same
process, which cannot catch a change that moves both. The **golden file** can:
bytes committed to the repository, so a change to output that somebody's stored
catalogue depends on fails the gate rather than shipping. And the whole thing
runs again over the **real 6,717-row corpus**, where one hand-authored segment
claims 1,219 of the 1,273 drill rows — evidence at the size the guarantee has
to hold at, rather than on five rows made up to pass.

### Not built here, deliberately

No inference (Stages B and C), and nothing wired into the build path: the
catalogue build still decodes through the rule set each file's decoding config
names. Stage A is the foundation the rest lands on, and it is worth having on
its own — it is the only part that says what "deterministic" means in code
rather than in a docstring. There is also no CLI: there is no stored decoder
for one to load yet, and a command with no artifact to run against would be
surface for a flow that does not exist (§7).

Still open, and it is Stage D: identity namespacing. Today an identity is
namespaced by the pack's `org_id`. With no packs it should become the
catalogue — the manufacturer — which is already modelled, but it touches
`identity/store.py` and getting it wrong makes part numbers stop resolving
silently.


## 15. Proposing a decoder from the file itself (Stage B, built)

§14 made a frozen decoder reproducible and shipped no inference, deliberately —
the artifact is data, so it can be written by hand, and everything downstream
could be built against one first. This is the step that writes one by reading
the file. `app/decoding/infer.py`.

### What it does

1. **Tokenise** each description into atoms: runs of letters, runs that are a
   number, single characters otherwise. A number is what varies between two
   rows of the same shape, so it is the thing worth finding.
2. **Cluster** by the leading two tokens with numbers masked. On the shipped
   corpus that gives `SC DRILL` (1,273 rows), `ANSI/ISO Turning` (1,016),
   `GP SC` (282) — the groups a person would name looking at the file.
3. **Align** within a cluster and turn the differences into optional parts.
   This is the step that matters: masking numbers alone splits the drills into
   `SC DRILL #mm/.#/ #xD`, `…#xD COOLANT` and `SC DRILL KU …` as three
   unrelated shapes, when they are one shape with two optional pieces.
4. **Validate** over every row of the file — not the rows the pattern was
   induced from, which is the overfitting check and the reason a proposal
   carries coverage numbers rather than a promise.

### The result

**64.6% of the real 6,717-row corpus, with no manufacturer knowledge of any
kind**, in 0.39 seconds. 135 segments; the drill segment claims 1,239 rows —
*more* than the hand-authored segment in §14 claims (1,219), because inference
found the tool-family alternation (`FLAT|HPR|HPS|HP|KU|XL|XS`) that a person
reading the file had missed.

### What it will not do

- **It binds no slots.** What a captured number means — cutting diameter or
  shank diameter — is a judgement about the trade, and a wrong binding is a
  confidently wrong dimension, which is the failure this whole design exists to
  prevent. Groups come back named `num1`, `opt1`; binding them is Stage C.
- **It never offers a pattern that fails its own examples.** Every candidate
  goes through `freeze` before it is proposed, so a proposal is a decoder that
  already works or it is not a proposal.
- **It ranks nothing by quality and computes no score.** Segments come back in
  coverage order, which is a count. Whether that coverage is good enough is a
  judgement it leaves alone.
- **It never guesses at a row it could not place.** Unclaimed rows come back
  counted with samples — on a file this does not understand, that is the
  finding rather than the failure.

### Deterministic, which is not required and is worth having

The freeze is what makes *decoding* reproducible, so inference does not have to
be — a proposal is reviewed before it becomes a decoder. But a proposal that
came out differently each run would make review useless: a person could not
tell a change they caused from noise, and two people looking at one file would
be arguing about different things. So every ordering is explicit and every tie
breaks on a stated rule, and the same file proposes the same decoder **whatever
order its rows are in**. That last property had to be worked at: counting
skeletons over the first N rows of a cluster cost it, and counting over all of
them bought it back at no meaningful expense, since the bounded work is the
pairwise alignment and not the counting.

### Three bugs the real file found

Each cost hundreds of rows, and none would have shown up against a fixture
small enough to write by hand. They are regressions in
`test_decoder_inference.py` now.

- **The base skeleton was chosen by frequency.** The most common drill skeleton
  includes `COOLANT`, so every drill *without* coolant was a deletion relative
  to the base, could not fold in, and went unclaimed — the segment claimed 472
  of 1,273. The base is now the skeleton the most rows are pure insertions of.
- **Only one optional run per position was kept.** A drill line names one of
  several tool families in the same place, and the rest were silently dropped.
  They are an alternation inside the optional group now, which is safe: what
  `safety` refuses is an alternation inside an unbounded *repeat*, and an
  optional group is not one. The comment claiming otherwise was simply wrong.
- **Case split a shape in two.** The corpus writes `3mm` and `1,8MM`. Read
  case-sensitively those are different skeletons, and the smaller half fell
  below the cluster floor. Case is folded when the skeleton is taken and the
  pattern matches case-insensitively at exactly those atoms — nothing else
  loosens, because a `/` between two numbers is structure and not decoration.

Together those took coverage from 44.9% to 64.6%.

### Still not wired in

Nothing calls this yet, on §14's reasoning: the catalogue build still decodes
through the rule set each file's decoding config names, and a second build path
with no caller would be worse than none. What connects them is Stage C — the
step that proposes slot bindings and asks a person to confirm them — which is
where a proposal becomes something a build can use.

---

## 16. Naming what a captured number is (Stage C, built)

§15 proposes structure and stops there, on purpose: it hands back groups called
`num1`, `opt2` and says nothing about what they hold. This is the step that
names them. `app/decoding/evidence.py`, `app/decoding/bind.py` and
`app/decisions/decoder_binding.py`.

It is three steps in that order, and the order is the design — the
deterministic answer is computed first and is never overridden.

### 1. Evidence: what the file actually put in each group

For every group of every segment, measured over every row: how often it
captured, how many distinct values, the most frequent of them, the token
written immediately before and after, and whether every value was an integer.

**From the matches, never from the pattern.** The pattern says what its author
intended a group to catch; the matches say what the file put there. A group
whose pattern accepts any number and which in this file only ever holds `0` is
the finding, and reading the pattern would have hidden it behind "accepts any
number".

Two things had to be worked at, both caught by a test rather than by reading:

- **Adjacent tokens are case-folded**, because the corpus writes `11,1mm` and
  `11,1MM` and the pattern matches both — inference folded the case, so the two
  are one token as far as the decoder is concerned. Unfolded, the drill
  diameter had 1,175 `mm` against 64 `MM` and therefore *no unit at all*.
- **Examples are the lexicographically smallest claimed rows**, not the first
  three seen. The first three seen made evidence depend on the order the file
  was read in, which is the same defect §15 had to fix in its skeleton
  counting, for the same reason.

### 2. The surface binder: derived from the vocabulary, not tabulated

A number written `3xD` is a depth ratio because `depth_ratio_xd` is the **only**
slot in `CORE_SLOTS` ending `_xd`. A number written `5.1mm` is *a* millimetre
dimension and the vocabulary has eleven of those, so this narrows to the eleven
and **declines**.

That decline is as much the point as the bindings are. On the real corpus:

| | |
|---|---|
| Groups in the proposed decoder | 330 |
| Named by the file's own text | **12** |
| Declined — a bare `mm`, eleven ways (`AMBIGUOUS_UNIT`) | 16 |
| Declined — no unit written at all (`NO_UNIT`) | 223 |
| Declined — a group no row uses (`NO_OCCURRENCES`) | 28 |
| Declined — an optional group holding two words (`MIXED_VALUES`) | 30 |
| Declined — one word, unrecognised (`UNKNOWN_WORD`) | 21 |

Twelve of three hundred and thirty. A step claiming more than that would be
guessing, and the twelve fill 1,680 attribute values across 4,342 decoded
records.

`UNIT_TOKENS` is nine entries and every one is a unit of measure or a counter
written like one — `mm`, `in`, `inch`, `"`, `deg`, `°`, `xd`, `fl`, `fls`. It
says what `mm` *is*; which slots that reaches is derived from `CORE_SLOTS`, so
adding a second `_xd` slot would make the binder stop binding `xD` and start
narrowing, with no edit to the binder. Nothing here names a manufacturer, a
family or a file format, and there is no default to fall back to.

Three rules with teeth:

- **A unit counts only as a suffix.** An earlier version also looked at the
  left-hand token, on the theory that a file might write `dia 5.1`. It never
  caught one of those — `dia` is not a unit, so it was never in the table.
  What it caught instead was the *previous field's* suffix: in
  `GP SCEM 2FL 20x20x75x150` the token before `20` is `FL`, so the shank
  diameter was bound to `flute_count` on **seven segments** of the shipped
  corpus. Each one a wrong slot with a real stamp on it.
- **A group that captured nothing is never bound.** Not with a plausible slot,
  not with a low-confidence one. Twenty-eight groups of the shipped corpus
  never participate in a match, and a binding on one would be a claim with no
  evidence under it — reading, on a screen, as a decoded attribute.
- **One slot is claimed once per segment.** `freeze` refuses a set with two
  groups on one slot, so without this a reviewer would be handed an error where
  a review should be.

An optional group is bound as a **flag**, which the executor makes `True` when
the group participated and *absent* otherwise, never `False` — because a
pattern can establish that a file said `COOLANT` and can never establish that
it said the tool has no through-coolant.

The `MIXED_VALUES` decline is worth reading as a finding about §15 rather than
about this step: inference folds `COOLANT` and `MQL` into one optional group,
and those are two different slots. One binding cannot express both, so nothing
is offered for that group at all — splitting it is inference's job, and the
empty candidate list is what keeps that visible instead of resolved by picking
the more common word.

### 3. The model, and why it cannot state a number

Choosing among eleven millimetre slots needs to know that in `16x16x56x110` the
first number is the shank and the third the length of cut. That is knowledge of
the trade, not of the file, and it is the one interpreted step in the whole
pipeline. It lives in `decisions/` because `decoding/` is deterministic by
contract and must never import `ai/` (`CLAUDE.md` §1, §3).

**"AI never computes a number" holds by construction here, not by review.** The
model's entire output vocabulary is four fields:

```json
{"bindings": [{"segment": "s4-gp-sc", "group": "num3",
               "slot": "shank_dia_mm", "type": "integer"}]}
```

— an id of a segment that already exists, a name of a group that segment
already declares, a slot from the list *this file* narrowed, and one of four
executor types. There is no field through which a measurement could arrive, so
a model asserting that a drill is 9.99 mm has nowhere to put it. What it
decides is that the number the file **already contains** at this position is a
cutting diameter; the number itself is read out by a frozen regular expression
and would be the same under any binding.

The gate is deterministic, and every check is a comparison against something
computed from the file:

| Refusal | What it catches |
|---|---|
| `UNKNOWN_TARGET` | A segment or group that does not exist, or **that was not asked about** — so a reply cannot overrule a group the file's own text already settled |
| `SLOT_NOT_A_CANDIDATE` | A group followed by `mm` typed as an inch dimension, however confident the reply |
| `TYPE_NOT_SUPPORTED` | `5.1` typed as an integer — a thousand rows lost at decode time to a decision made in a prompt |
| `DUPLICATE_SLOT` | Two groups on one slot. Evidence order breaks the tie, because that order is a property of the file |

Entries are dropped individually rather than failing the batch: a reply that
names forty groups and gets two wrong should still leave a reviewer
thirty-eight. The counts are reported, because a mostly-refused reply is a
finding about the prompt rather than a quietly thin review.

**The floor is the deterministic answer.** A provider that is missing,
misconfigured, slow, or answers unparseable text degrades to the surface
suggestions with a stated reason (`PROVIDER_FAILED`, `UNREADABLE_REPLY`) and
never raises — this runs behind a screen, and a caller that gets a degraded
review got something. Where nothing is left to ask, no call is made at all
(`NOTHING_LEFT_TO_ASK`).

### Nothing is confirmed, and nothing is wired into the build yet

The output is a **review**: every group, named or not, with its evidence and
the slots it could still be. A person changes what they disagree with and
confirms, and only then does `bind.apply_bindings` freeze a new artifact — a
**new** one, with its own `decoder_id`, never an edit in place. Rows already
decoded were stamped with the id of a decoder that did not have these bindings,
and changing what that id means is the one thing the freeze exists to prevent.

A binding set is the whole answer for the segments it names, not a patch, so
which of a segment's groups are unbound is a property of the set somebody
confirmed rather than of the order things were confirmed in.

What is still **not** done: `catalog.py`'s build path still decodes through a
pie-parser rule set (`run_parse`), so Stages A–C are the substrate and not yet
the road. Replacing that call — and with it, deciding how identity namespacing
works when there is no pack id to namespace by — is Stage D, and it is the part
that touches `identity/store.py`.

---

## 17. The decoder as a decode path, and one namespace (Stage D, built)

§§14–16 built the substrate: an artifact that decodes reproducibly, inference
that proposes one from a file, and a binding step that names what it captured.
None of it was wired into a build. This is where it becomes a path a build
takes — and where a question the packs answered by accident has to be answered
on purpose.

### Two paths, and a config names exactly one

`company_corpora` gains `decoder` (the frozen artifact) and `decoder_id` (its
content id), migration `n1dec`. A file's decoding config now names either a
shipped **rule set** or a **decoder** built for that file, and `decode_path` is
the one place that reads which.

Not a preference and a fallback. A config naming both is refused — which of
them read a row would become a question about evaluation order, and nothing on
the row would answer it. A config naming neither is still a file that is not
decoded, and a build still says which file by name.

`build_for_company` picks per file, so a company mid-migration can hold one
file read by `zcnc` and one read by its own decoder in the same catalogue, each
row of `built_from` saying which path produced it.

### What the two paths actually cost, measured

This is the number that decided the design, and it is why Stage D is **not** a
switchover. Both paths over the shipped 6,717-row corpus:

| | rule set (`zcnc`) | decoder inferred from the file |
|---|---|---|
| Records | 6,717 (100%) | 4,342 (64.6%) |
| Quarantined | 0 | 2,375 |
| Attribute values per record | **10.1** | **1.4** |

Flipping the build today would lose a third of the records and seven eighths of
the attributes. So the rule-set path stays, and the two run side by side.

The gap is not mostly where it looks, either. Six of those 10.1 values —
`grade_system`, `material_class`, `toughness_index`, `applications`,
`grade_segment`, and `grade` itself — come from the **grade column**, not the
description: the pack decodes `TN2000` into a grade system and a material class
through a second grammar. The portal's decoder reads descriptions and carries
the grade code through unparsed. Closing that is a decoder over the grade
column, which is a stage of its own and not something to bodge into this one.

### The namespace, stated rather than borrowed

`record_namespace` in pie-parser reads `org_id` first, and its docstring says
what the field means: *the organisation, not the manufacturer* — `record_id` is
the number the distributor's own system issued, unique inside that system and
nowhere else. Under packs the value was the org pack's id, which is a **proxy**
for the organisation that happened to be constant across every company in a
deployment, because exactly one org pack ships.

There is no pack on the decoder path, so there is no proxy. `_merge_decoded`
now stamps the connection's own id as `org_id`, and does it for **both** paths.

That uniformity is the part that matters. A company with one file stamped
`zcnc` by the pack and one stamped with its own id would hold two namespaces,
and the same material number in both would stop being a collision the merge
resolves — newest wins, counted and named — and become a key present in two
spaces, which `AuthoritativeIndex` answers as a structured AMBIGUOUS abstention.
A part number that silently stops resolving is the defect no record count
reveals, and it is the one this whole merge exists to prevent.

Stamped *before* the de-duplication key is taken, so the key is the one the
index will use rather than the one the decode path happened to leave behind.

### One namespace field, two numbering authorities

The honest limitation, named because it cannot be fixed from this side.

`AuthoritativeIndex` keeps two maps — one for `record_id`, one for
`catalog_number_full` — and namespaces **both** by the single
`record_namespace` field. But those identifiers have different authorities:

- `record_id` is the *distributor's* material number. All of a company's
  catalogues share one, so the same number twice is their own master
  contradicting itself. Newest-wins with the collision named is right.
- `catalog_number_full` is the *manufacturer's* number. Two manufacturers
  reusing one is entirely normal — and inside one company they now land in one
  space, where the index treats the repeat as a collision that resolves for
  neither.

A record carries one namespace and `record_id` is the identifier that must have
the company's, so the manufacturer's number is the one that loses. pie-parser
is a separate repository and a pinned submodule, so the index cannot be changed
from here.

What Stage D does instead: **counts the repeats, names them, and keeps both
records.** Dropping one would lose a product over its secondary identifier;
`ingest.catalog_collisions` and `catalog_collision_examples` say which
catalogue numbers have stopped resolving and somebody is told. This also
corrected `union_catalogue`'s docstring, which claimed two manufacturers'
records sat in different namespaces — never true of any deployment, since one
org pack ships and every catalogue was stamped `zcnc` all along.

### Propose, review, save — and the split that keeps patterns out of requests

`POST …/sources/{key}/propose-decoder` runs the three inferred stages over the
file and returns the proposal plus the binding review. It **saves nothing**, so
a proposal can be asked for twice and compared without changing what the file
currently decodes through.

`PUT …/sources/{key}/decoding` then takes the artifact *as the proposal
returned it* — its own id must still match its contents — plus `bindings` (a
slot and a type per group) and `decimal`. The server applies those and
re-freezes under a new id.

That split is deliberate. A wrong **binding** names a dimension wrongly: bad,
visible, and exactly what the review is for. A **pattern** is a regular
expression that will run over every row of every rebuild, so accepting one from
a request would be accepting arbitrary matching work from a caller.
`decoding.safety` would still refuse the dangerous shapes, and not offering the
door is better than relying on the check behind it.

Two integrity checks, in the two places each can mean something:

- `confirm_decoding` requires the *proposed* artifact's id to match its
  contents, which is the evidence it came from a proposal. It does **not**
  refuse a changed one after bindings are applied — a review exists so a person
  can disagree with it, and the result legitimately has a new id.
- `run_decoder` re-freezes the stored artifact on every load and cross-checks
  the denormalised `decoder_id` column, so an artifact edited in the database,
  or swapped for another valid one, decodes nothing.

Making that work needed a fix in Stage A: `Decoder.to_dict()` did not include
`decoder_id`, so `from_dict`'s tamper check — which reads
`payload["decoder_id"]` — was skipped silently for any payload that had not had
the id attached by hand. Two places in the test suite did attach it, and they
were the only evidence the check worked. The id is now part of `to_dict` and
still absent from `artifact_dict`, which is what the hash is taken over, so no
existing id moved.

### Still not done

No UI. The API is complete and tested, but nothing on the Decoded catalogue
screen calls `propose-decoder` yet, so a decoder can be proposed and confirmed
through the API and not by a person on a screen. That, and a decoder for the
**grade column**, are what stand between this and the rule-set path being
removable.
