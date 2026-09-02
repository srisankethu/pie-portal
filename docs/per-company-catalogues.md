# Per-company decoded catalogues

**Status: built, both parts, plus §10.** PR 1 brought the tables, upload, pack
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
this package sees them. The index learns nothing from use in this slice. The
hook for that is already in the schema: a confirmed customer-code mapping
(`identity.confirm_proposed_identity`, §1's gate) is a person asserting "their
phrase means this product", and the natural next step is to index that phrase
as an alias of the record — so the next time the customer writes it, retrieval
finds the product by the words they use. That is a person's confirmation
entering an index, not a model inferring an identity, and it keeps the gate
where it is.
