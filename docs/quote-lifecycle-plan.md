# Quotes across PIE and the ERP — implementation plan

**Status: approved 2026-09-20, all six decisions in §4 taken on the
recommendation. Phase 0 landed (`71f2a6b`). Phase 1 landed (`9d520f5`).
Phase 2 landed (`d4767a6`), with two departures noted in place. Phase 3 landed
(`a37d87d`, `104a5b0`, `ad6f803`), with three departures noted in place and an
adversarial review answered. Phase 4 is complete: the qualified pointer, the
deletion sweep and counters, and the three connectors that read quotes.
Phases 5–6 next.**

The question this answers: *how do we handle quotes the ERP raised, quotes across
several connected companies and ERPs, a PIE quote not yet sent to the ERP, and a
PIE quote that has been sent?* The first section answers it for the code as it
stands; the rest is the plan for closing what that answer exposes.

Every line reference below was verified against the tree at `23436d9`
(the merge of #272). How the findings were produced and how far each one was
checked is in Appendix C — read it before treating any gap here as settled.

---

## 0. The answer for the code as it was, before this plan

**Read this section in the past tense.** It is the diagnosis the plan was
written from, pinned to the tree at `23436d9`, and it is kept unedited so the
phases below can be read against what they changed. Several sentences in it
are now false on purpose — "Zoho only" below is the clearest, closed by Phase
4. Where a phase closed something, its own **Landed** note says so.

Four situations, four different sets of tables, and no join between them. The
legend used throughout: **ERP_QUOTE**, **MULTI_CONNECTION**, **PIE_UNSENT**,
**PIE_SENT**.

### ERP_QUOTE — a quote the ERP raised itself

Read by the sync, from **Zoho only**. `sync.run_supply` runs the quote phase
only when the source object has a `list_quotes` method (`ingestion/sync.py:627-629`),
and the only sources that have one are `ZohoApiSource` (`zoho_client.py:1811`) and
the demo fixture. Each estimate lands as one `erp_quotes` row keyed
`(org, connector, connection_id, external_ref = Zoho estimate_id)` with its lines in
`erp_quote_lines`, **rewritten wholesale on every pull** (`repositories.py:1006-1055`).
The ERP's own status word is stored verbatim and classified once, into
`outcome ∈ {WON, LOST, UNRECORDED}`, by `normalize.classify_outcome` — WON needs
`accepted`/`invoiced` *and* a date, LOST needs `declined` *and* a date, everything
else (including `expired`) is UNRECORDED, never a loss (`normalize.py:558-683`).

It is shown read-only: the "From your ERP" tab of the workspace
(`GET /api/v1/insight/quote-book`, `QuoteWorkspace.tsx:357-403`) and the
`/quotes/erp/:ref` page (`ErpQuoteScreen.tsx`). A person can record *why* one was
lost only from the Unanswered worklist or the Today queue, and that writes a
`quote_outcomes` row with `quote_id NULL` and `quote_document_ref` = the ERP id
(`quote_service.set_outcome`, `quote_intelligence.py:682-729`). The sync never
opens `quote_outcomes`; that is pinned by an AST test
(`test_quote_document_sync.py:382-416`).

What it cannot do today: an outcome recorded on an ERP quote enters no win rate,
no loss-reason mix and no competitor mix (G06); the ERP tab and page never show
it; and nothing lets a person open an ERP quote in the builder.

### MULTI_CONNECTION — several companies, possibly several ERPs

Every synced document row carries `(connector, connection_id, external_ref)` and
`domain/origin.Companies` labels them; connectors may be mixed in one
organization (`resolution.company_for`, `resolution.py:498-525`). A draft is bound
to **one catalogue company** at creation (`QuoteDraft.connection_id`, chosen through
`CompanyPicker` when there is more than one) and every line resolves only against
that company's decoded catalogue. The **book it is sent into** is decided
separately, at read and send time, from the *customer's* own connection
(`routers/quote.books_for_quote` → `connections.book_for_customer`), and **nothing
compares the two** (G03). Four connectors can write a quote — Zoho, Business
Central, Acumatica, NetSuite (`connections._QUOTE_ADAPTERS`); Prophet 21 and
Sage cannot, and their customers are refused by name at the moment Send is
pressed. A quote to a Business Central or NetSuite customer cannot be sent from the
builder at all, because no code path ever fills a line's `itemId` for a book the
platform does not read live, and both writers refuse every line without one (G04).
The sent-document row records the connector but not the company (G09), so a
two-company Zoho organization cannot say which book holds `EST-1001`.

### PIE_UNSENT — a Quote Builder draft

A `quote_drafts` row with a per-organization number `QB-nnnn` and a reference
`QB-nnnn-<8 hex>` minted once (`quote_workspace._mint`, `:92-134`). Its state is
never stored; `quote_workspace.readiness` derives it on every read in the send
gate's own order: EMPTY → NEEDS_ATTENTION → MISSING_DETAILS → NO_CUSTOMER → SENT →
READY / NEEDS_APPROVAL / AWAITING_APPROVAL (`quote_workspace.py:579-624`). An
outcome row exists for it only if somebody recorded an override or requested an
approval (`POST /quote-intelligence/snapshot` is the only opener of DRAFT,
`quote_intelligence.py:552-617`); such a draft can be marked LOST. There is no
screen that marks a draft sent to the customer other than the ERP write, so a
quote for a Prophet 21 or Sage customer — or one emailed as a PDF from PIE — can
never leave this state through the UI (G12).

### PIE_SENT — a draft written into the ERP

`POST /api/v1/quotes/{id}/estimate` runs the gates, writes the document through
the customer's book, and records one append-only `quote_documents` row —
connector, ERP id and number, the reference, a fingerprint of `supplyCode:qty:rate`
per line, and the policy version (`routers/quote.py:1009-1206`,
`quote_service.record_document`). Readiness reads SENT while that fingerprint
still matches the lines; once a price moves the chip says "amended since".

Three things the design says happen here do not:

- **The outcome never moves and the ERP link is never written.** The router reads
  `est.estimate_id`; every writer returns `WrittenDocument`, whose field is
  `document_id`. The `AttributeError` is swallowed by `except Exception:
  log.exception(...)` and the response says "created" (G01, reproduced live through
  the endpoint in this session; regression since the 5 September merge of #223). So
  no PIE quote has moved to SENT since then, the outcome bar's "Mark won" — which
  appears only at SENT — is unreachable for every PIE quote, and the durable join
  between a PIE quote and its ERP document does not exist.
- **An amended re-send never lands.** The reference is minted once, every live
  adapter keys its idempotency on it, and so Zoho returns the *old* estimate as
  "already existed"; Business Central and Acumatica do the same when the line
  count is unchanged and answer "unknown, check it" forever when it differs;
  NetSuite silently updates in place and reports "already sent". The router then
  records the new fingerprint against the old number, the "amended since" warning
  disappears, and the ERP holds the old prices (G02). Only the mock writer behaves
  as the docs describe.
- **After the next Zoho sync the same document appears twice** — a "Sent" draft
  and an ERP quote — with no join shown, although the join keys are stored on both
  sides (G07). The ERP's later `accepted`/`declined` never reaches the PIE row (G08).

pie-parser needs no change for anything in this plan: it holds no quote or
commercial state and is reached only through `pie_service.resolve` /
`search_catalogue` and the `catalog_version` stamp.

---

## 1. The gaps, and where each is closed

Severity is the consolidated reading. **Verified** means all three adversarial
lenses (code, tests-and-docs, impact) confirmed it; **direct** means it was
reproduced or read in this session outside the workflow; **cited** means the
evidence is file-and-line but nobody tried to refute it (Appendix C).

| Id | Gap | Sev | Checked | Phase |
|---|---|---|---|---|
| G01 | Send never moves the outcome to SENT nor writes the ERP link (`est.estimate_id`) | high | verified + direct | 0 |
| G02 | Amended re-send never lands; stale document recorded as current | high | verified + direct | 2 |
| G03 | Draft's catalogue company and the customer's book never reconciled | high | verified | 1 |
| G04 | BC / NetSuite quotes cannot be sent: no path fills `itemId` | high | verified + direct | 5 |
| G05 | No registry connector reads quotes; P21 / Sage cannot be written | high | verified | 4 (read); out of scope (P21/Sage write) |
| G06 | Outcomes on ERP quotes reach no number; three "won" definitions | high | verified | 3 |
| G07 | One document, five surfaces, no join shown | high | cited + direct (keys) | 1 |
| G08 | ERP's decision never reaches the PIE row; no screen says they disagree | med | cited | 3 |
| G09 | `quote_documents` has no `connection_id`; outcome pointer is a bare id | med | cited + direct | 1, 4 |
| G10 | Every bare-ref reader but one merges or picks first on a collision | med | cited | 4 |
| G11 | "SENT" means written into the ERP; the ERP row says draft; the tip says "with the customer" | med | cited + direct | 3 (decision D1) |
| G12 | Manual DRAFT→SENT exists only at the API; no exit for P21/Sage/PDF quotes | med | cited + direct | 3 |
| G13 | No ERP quote → builder path; no outcome action on the ERP tab/page | med | cited | 3, 5 |
| G14 | Customer or field change after a send leaves the quote "Sent" and current | med | cited + direct (fingerprint) | 2 |
| G15 | Pre-flight / scope / auth / throttle failures on send are bare 500s | med | cited | 2 |
| G16 | An unverified write is recorded nowhere | med | cited | 2 |
| G17 | Outcome cannot follow a second document; repoint refusal swallowed | med | cited | 2 |
| G18 | Acumatica / Sage / P21 order listings do not exclude quote-type orders | med | cited, ERP semantics unverified | 4 |
| G19 | A quote deleted in the ERP is never retired | med | cited | 4 |
| G20 | Neither tab names the company; the builder never names its book | med | cited | 1 |
| G21 | Assessment and sellable pool are org-wide while the catalogue is per company | med | cited | decision D6 |
| G22 | Every send-path test is `requires_pie` and skips unless the engine path is set | med | cited (accepted) | every phase's gate line |
| G23 | Docs and comments promise a new document on amend | med | cited + direct | 0 |
| G24 | Two halves of one document visible to different salespeople | low | cited (accepted) | 3 |
| G25 | A decided PIE quote sits on Drafts as "Sent" for ever | low | cited | 3 |
| G26 | Removing a draft leaves its outcome live and counted | low | cited | 3 |
| G27 | Every press of Send on a sent quote appends a snapshot set | low | cited | 2 |
| G28 | `docs/connectors.md` understates which connectors write | low | cited + direct | 0 |
| G29 | ML doc says `quote_outcomes.status` is ingested | low | cited | 0 |
| G30 | Gate docs describe five checks and 1174 tests | low | cited | 6 |
| G31 | Mock mode records every send as Zoho; the fixture never syncs a sent quote back | low | cited | 2, 5 |
| G32 | One sync counter never persisted; a quote-only run that errors is FAILED not PARTIAL | low | cited | 4 |
| G33 | A lineless quote pays its detail call every run | low | cited | 4 |
| G34 | Demo ERP quote has no company; no PIE_SENT row is seeded | low | cited | 5 |
| G35 | Unattributed customers are refused at the send, not on the draft | low | cited (accepted) | 3 |
| G36 | ERP tab has no filters; nothing links into the source system | low | cited | 1 (filters); out of scope (deep links) |
| G37 | Two outcome forms with different fields | low | cited (accepted until now) | 3 |
| G38 | Today and the worklist pass different customer refs | low | cited | 3 |
| G39 | Only `Quote` is contract-checked; sent/amended UI states unpinned | low | cited | 6 (and each phase adds its own) |
| G40 | Stored diagnosis read is unscoped by desk | low | cited, disclosure unverified | 6 |

---

## 2. Target model

### 2.1 One quote, two records, one join

A quote the desk builds has exactly one canonical record, `quote_drafts`. Every
time it is written into an ERP, one append-only `quote_documents` row says *which
document, in which system, in which company, from which content, under which
policy*. When the sync later reads that same document back, it lands in
`erp_quotes` like any other ERP quote — derived, rewritten wholesale, never a place
a human fact lives.

The join between the two halves is a **value join on the qualified document
identity**, never a surrogate:

```
quote_documents (external_system, connection_id, external_document_id)
      ==
erp_quotes      (connector,        connection_id, external_ref)
```

Value-based for the reason `QuoteOutcome.quote_document_ref` already is
(`models.py:3158-3170`): `DELETE FROM erp_quotes` plus a full re-sync re-mints every
surrogate and every pointer still resolves. Qualified by company because an ERP
id is unique only inside the company that issued it (`sole_erp_quote`'s docstring,
`quote_service.py:755-800`). Read-side only: `commercial/` performs the join; the
sync never learns that `quote_documents` exists (it must not name it, for the
reason it must not name `quote_outcomes`).

The human lifecycle stays on `quote_outcomes`: one row per PIE quote *or* per
ERP-raised quote, pointing at the newest document through
`(quote_document_connection_id, quote_document_ref)`.

### 2.2 What each table owns

| Table | Owns | Never holds |
|---|---|---|
| `quote_drafts` | the quote as the desk built it: lines with cost, fields, owner, catalogue company, number, reference | any status (readiness stays derived) |
| `quote_documents` | one row per send or manual send: system, **company** (new), ERP id and number, **revision** (new), reference actually sent, fingerprint of lines, **header fingerprint** (new), **channel** ERP/MANUAL (new), **write state** WRITTEN/UNVERIFIED (new), policy version | anything a later event changes — it is append-only history |
| `quote_outcomes` | what a person knows: DRAFT/SENT/WON/LOST, loss reason, who won, note; the document it is about, **qualified by company** (new) | anything the sync could rewrite |
| `erp_quotes` / `erp_quote_lines` | the ERP's own view, verbatim status and the sync's classification, from every connector that reads quotes | a human fact, a PIE id, a status the platform decided |

No new table anywhere in this plan. Every schema change is an additive nullable
column on a table that already has its row-level-security policy, its erasure
export entry and its demo purge entry — which is what keeps every phase's
migration a `NO RISK` row in `scripts/deploy_runbook.py` except the one constraint
change in Phase 4, flagged there.

### 2.3 Two vocabularies, one meaning each

Readiness (derived, per read) answers *what is this draft waiting on*. The outcome
status (a person's record) answers *what happened to it commercially*. They stay
separate; what changes is that each becomes honest about the other:

- **Readiness** gains `WON` and `LOST` (from the outcome of record, §2.5) so a
  decided quote leaves the "Sent" pile (G25), and keeps `SENT` for "a current
  document exists in the ERP, or a person marked it sent". The chip's tip says
  exactly that; the ERP's own word for the same document is shown beside it when
  the sync has read it.
- **Outcome `SENT`** means *the quote has left this desk*: written into the ERP
  under the customer's account (the platform's own act, which it can prove), or
  marked sent by a person for a quote that went out another way. It does not claim
  the customer has opened it — that is `erp_quotes.client_viewed_at`, shown, never
  inferred. Decision D1 records the alternative and why it is not recommended.

### 2.4 Revisions

A quote's reference is minted once and stays the key of its *first* document. An
amended re-send is a **new revision with a new reference** —
`QB-0042-3f9a1c2e` for revision 1 (unchanged, so every document already written
stays findable) and `QB-0042-3f9a1c2e-r2`, `-r3` … after it (23 characters at
r99, inside Business Central's 35-character `externalDocumentNumber`,
`erp/base.py:EXTERNAL_REF_MAX`). Each revision is its own ERP document and its own
`quote_documents` row; the outcome row follows the newest. The previous document
stays in the ERP and is named in the response so the desk can void it there
(D2 records the void-on-revise follow-up).

Idempotency keeps both halves it has today: the local fingerprint short-circuit
answers an unchanged re-press without a round trip, and the per-revision
reference lets every adapter's settle-by-read recognise a repeat whose reply was
lost. What changes is only that "changed content" now produces a reference the
adapter has never seen.

### 2.5 The outcome of record

One function, in `commercial/`, answers "how did this quote end" for every reader:

1. A human WON/LOST row wins, always, with its reason and winner.
2. Otherwise, if the quote's ERP row (joined through §2.1 for a PIE quote, or the
   row itself for an ERP quote) is classified WON or LOST with a decision date, that
   is the outcome, marked `source = ERP`, reason `NOT_RECORDED`.
3. Otherwise the quote is open: SENT if a person or a document says so, DRAFT if not.

Derived, never written back. The sync still never opens `quote_outcomes`, a re-sync
still cannot change a recorded reason, and nothing invents a reason the ERP does
not hold. Won & lost, the attribution evaluator, replay, the wallet, the ERP tab
headline and the Unanswered worklist all read this one answer instead of the three
they compute today (G06, G08). D3 records the alternative of writing ERP decisions
onto the human table and why it is not recommended.

### 2.6 Multi-connection rules

- **A quote belongs to the company whose catalogue priced it, and that is the
  company that invoices.** The customer must belong to that company. The builder
  offers only that company's customers; choosing one from another company is
  refused with a sentence naming both companies (G03; D5 for the alternative).
- **Every pointer to an ERP document is qualified by company.** New writes fill
  the qualifier; legacy rows with NULL are resolved by `(connector, id)` only while
  that is unique, and refused by name otherwise — the rule `sole_erp_quote`
  already applies, applied everywhere (G09, G10).
- **A connector reads quotes the way it reads everything else:** a `list_quotes`
  method and a `Permission` naming the `quotes` stage, an entry in the two status
  vocabularies, and an `external_ref` in the same id space as its writer's
  `document_id` — so the join in §2.1 holds by construction. Generic code gains no
  branch on a connector key (CLAUDE.md §3).
- **Evidence for pricing stays organization-wide** (`ZohoConnection` docstring,
  `models.py:200-209`); D6 records the two edges of that decision the plan leaves
  alone.

### 2.7 Invariants this model keeps

- AI never computes a number: nothing here touches `ai/`; the outcome of record is
  arithmetic over rows.
- Cost and margin never reach a salesperson: every new field is a status word, a
  date, a company label, a document number or a quote's own selling total. No new
  count answers a margin question; no new predicate has a cost boundary.
- Money is `Decimal`; thresholds carry a version (`quote_documents.thresholds_version`
  stays on every row, manual rows included).
- Absence of evidence is not a pass: an ERP that has not been read says "not read",
  a write whose fate is unknown is recorded UNVERIFIED and blocks, a quote whose
  company cannot be placed is refused by name on the draft, not at the button.
- An equivalence score is never persisted as identity: untouched.
- `state/` and `erp_quotes` stay derived; `quote_outcomes` stays human; the sync
  names neither `QuoteOutcome` nor `QuoteDocument`.
- Migrations: additive, literal, one per commit, each reversible, none imports a
  model, no `create_all`.

---

## 3. Phases

Each phase ships green on its own and is reversible. The gate for every phase is

```bash
PIE_PARSER_ROOT=/home/user/pie-parser make verify
```

and the report must quote the `requires_pie` count, because without the engine
path every send-path test skips and a change here can pass the gate untested
(G22, `scripts/verify.sh:94-104`).

### Phase 0 — Restore the link, tell the truth (one day)

**Closes** G01, G17 (refusal surfaced), G23, G28, G29.

**Changes**

- `backend/app/routers/quote.py:1181-1194` — pass `quote_document_ref=est.document_id`;
  replace `except Exception` with `except (InvalidTransition, QuoteOutcomeRepointed)`
  and carry the refusal into the response as a `warning` field ("Recorded in Zoho
  Books as EST-1001; the outcome could not be attached because …"). Any other
  exception propagates: a bookkeeping failure after a real ERP write must be loud,
  not logged.
- `backend/app/schemas.py` `EstimateResponse` + `frontend/src/types.ts`
  `EstimateResult` — optional `warning: string | null`; `QuoteBuilder.tsx` renders
  it as a snackbar.
- `backend/app/routers/quote.py:1108-1113` comment and `docs/user-flows.md` §7.8
  steps 5–7 — describe the real behaviour (the reference is reused; every live
  adapter answers "already exists" to changed content) until Phase 2 lands.
- `docs/connectors.md:78-86, 196-206` and the `_QUOTE_ADAPTERS` comment
  (`connections.py:436-448`) — four connectors write quotes, not two.
- `docs/concepts/14-machine-learning.md:103` — `quote_outcomes.status` is written
  by a person, a snapshot or the send; the sync writes `erp_quotes.outcome`.

**Migration** none.

**Tests**

- `tests/test_quote_flow.py::test_a_send_moves_the_outcome_to_sent_and_links_the_document`
  (new, `requires_pie`): press `/estimate` with the mock writer; assert
  `get_outcome(...).status == "SENT"`, `quote_document_ref == doc.external_document_id`,
  `sent_at` set. This is the test that would have caught the regression: it presses
  the endpoint and reads the row, where every existing test does one or the other.
- `test_quote_flow.py::test_a_bookkeeping_refusal_is_in_the_response_not_the_log`:
  seed an outcome row already pointing at another ref; assert `ok`, `documentNumber`
  and the `warning` sentence.
- `tests/decision_platform/test_platform_quote_outcome_scope.py:201-229` — keep;
  it calls `set_outcome` directly and now has a sibling that goes through HTTP.

**Docs** as listed. **Rollback** revert one commit; no schema.

### Phase 1 — Qualify the pointer, join the halves, name the company (one week)

**Closes** G03, G07, G09 (columns), G20, G36 (filters).

**Changes — backend**

- `models.QuoteDocument.connection_id` (nullable, indexed) and
  `models.QuoteOutcome.quote_document_connection_id` (nullable, indexed).
- `routers/quote.QuoteBooks` gains `connection_id`; `_books_for` fills it from
  `book.connection.connection_id`; `record_document` and the send's `set_outcome`
  write it. `sole_erp_quote` accepts an optional qualifier and uses it when
  present; the ERP-only outcome path passes the row's own connection.
- **Company agreement** (G03): a single helper
  `quote_workspace.book_matches_company(session, org, quote, customer)` used by
  `create_quote`, `create_quote_form`, `set_customer` and `create_estimate`.
  Mismatch → 422 naming both companies: *"Pitti Engineering belongs to 4U
  Precision; this quote prices from SLS Engineers' catalogue. Start the quote from
  4U Precision, or choose one of SLS Engineers' customers."* A customer with no
  recorded company follows `book_for_customer`'s existing rule (one enabled
  company → allowed; otherwise refused with its sentence). `GET /api/v1/accounts`
  gains an optional `connection_id` filter and the builder's `CustomerPicker` passes
  the quote's; in a single-company organization nothing changes.
- **The join, read-side** (G07): `quote_service.erp_document_for(session, org,
  quote_id)` → the `erp_quotes` row matching the newest `quote_documents` row on
  `(external_system, connection_id, external_document_id)`; legacy rows with NULL
  `connection_id` match on `(external_system, external_document_id)` only while that
  is unique. Used by `quote_workspace.list_drafts` (`sent.erp = {source_status,
  outcome, decided_on, client_viewed_at}` or null) and `routers/quote._view`
  (`estimate.erp`, same shape). The reverse join in
  `commercial/insight/quote_book.build`: each `BookQuote` gains `platform_quote =
  {quote_id, number} | null` and `connector`.
- **Company everywhere** (G20): `QuoteDraftSummary.company` and `Quote.company`
  (labels through `origin.Companies`, the one dictionary); `ErpQuote.connector`.

**Changes — frontend**

- Drafts tab: a Company column and card line when `companies > 1` (reuse
  `platform/CompanyFilter.tsx`), the sent chip renders the number and, when
  `sent.erp` is present, the ERP's own word beside it ("Sent · Zoho Books EST-1001 ·
  Zoho: sent 12 Sep"). Rows are `DataGrid`, filters are `FilterChip`.
- ERP tab: Company column, the same `CompanyFilter`, an outcome filter row
  (Won / Lost / No outcome), and a "Built in PIE · QB-0042" chip that navigates to
  the builder. Rows that are PIE-originated are **not hidden** — the count on the
  tab must stay the count of the book — they are labelled.
- Builder `IdentityStrip` gains Book, matching the ERP page. `ErpQuoteScreen`
  shows "Built in PIE as QB-0042 — open" when `platform_quote` is set.

**Migration** `u8qlink_quote_document_company.py` (revision `u8qlink`, down
`t7concept`): two nullable `String(64)` columns with indexes; downgrade drops
them. No backfill: a row written before the column existed cannot be attributed
after the fact, the rule every document table already follows.

**Endpoints and roles** no new endpoint; `?connection_id=` on `/accounts` narrows
only. Every new field is a label, a status word or a date; the reverse join
exposes a PIE quote *number* to whoever may already see the ERP row, and opening
it goes through the builder's own scope.

**Tests**

- `test_quote_book_routing.py` — new: a draft against company A refuses a
  customer from company B with a sentence naming both; single-company org
  unchanged; NULL-provenance customer follows the existing rule.
- `test_quote_workspace.py` — the two-company fixture the file lacks today:
  `sent.erp` is null before a sync and carries the ERP's word after
  `upsert_quote_document` of the same id under the same connection; a colliding id
  under another connection does **not** join.
- `test_quote_document_sync.py` — a document written before `connection_id`
  existed still joins while `(connector, id)` is unique and stops joining when it
  is not.
- `test_quote_book.py` — `platform_quote` set only for the row that matches the
  qualified triple; a salesperson who may not see the draft still may not open it.
- `test_frontend_contract.py` — add `QuoteDraftSummary`, `ErpQuote`, `ErpQuoteBook`
  to the contract check (G39 for the shapes this phase changes).
- `QuoteWorkspace.test.tsx`, `ErpQuoteList.test.tsx` — company column and filter
  appear only when `companies > 1`; the "Built in PIE" chip navigates.

**Docs** `docs/user-flows.md` §7.1 (company rule and the refusal), appendix row for
the `/accounts` parameter; `PRODUCT.md` operating context ("a quote belongs to the
company whose catalogue priced it").

**Rollback** downgrade `u8qlink`; the join helpers tolerate NULL, so reverting the
code before the data is safe.

### Phase 2 — Revisions and an honest send (one week)

**Closes** G02, G14, G15, G16, G17, G27, G31 (mock honesty).

**Changes — backend**

- `models.QuoteDocument` gains `revision` (Integer, default 1), `channel`
  (String(8), default `ERP`), `write_state` (String(16), default `WRITTEN`).
- ~~`header_fingerprint`~~ — **dropped at implementation.** No writer payload
  carries a quote-level field: the four adapters send the customer, the
  reference and the lines, and nothing else. A fingerprint over values that
  never reach the document would turn "amended since" on for a change the
  customer cannot see, and the one header value that *does* reach the source —
  the customer — is covered by D4 (refused after a send) rather than by a
  stamp. The three columns above are what landed.
- `create_estimate`, in this order: gates → **local short-circuit first** (both
  fingerprints match the newest WRITTEN row → "already covers", nothing recorded,
  G27) → `assess_and_record` → approval gate → `revision = newest.revision + 1` when
  content moved, reference `f"{q.reference}-r{revision}"` for revision ≥ 2 → write →
  record the row → `set_outcome(SENT, quote_document_ref=doc.document_id,
  repoint_from=newest.external_document_id)`.
- `quote_service.set_outcome` gains `repoint_from`: allowed only when the row's
  current pointer equals it *and* it names one of this quote's own
  `quote_documents` rows. `QuoteOutcomeRepointed` stays the answer for everything
  else (a human fact recorded against a different document is never moved).
- `create_estimate` catches `SourceUnavailable`, `SourceScopeError`,
  `SourceAuthError`, `SourceThrottleError` and `IngestionError` from the pre-flight
  and the write and answers `ok=False` with the naming words and the source's
  sentence (G15). "Exactly three answers" becomes true.
- `SourceWriteUnknown` → record a `quote_documents` row with `write_state =
  UNVERIFIED`, the reference, `line_count`, no id or number (G16). Readiness reads
  **`UNVERIFIED_SEND`** — a state of its own rather than the NEEDS_ATTENTION written
  here, because NEEDS_ATTENTION means "a line is unresolved" and a pile that mixed
  the two would hide the one that needs a person to look in the books. It sits in
  the workspace's "Needs work" filter. The builder shows the sentence and the
  reference to look for; the next press retries that revision under its reference,
  and the source's pre-flight settles it: found (recorded WRITTEN, same revision,
  with the content it was sent with) or nothing (the write runs now).
- `set_customer` refuses with 409 once a WRITTEN document exists ("Sent to Pitti as
  EST-1001 — start a new quote for another customer"), the rule `delete_quote`
  already applies (G14, D4).
- `MockZoho.create_sales_quotes` honours `reference`: the same reference returns
  the same document with `already_existed=True`, a new one mints a new document.
  The mock then behaves like the live adapters, and the amend test exercises the
  real path. Mock-mode sends record `external_system` from the quote's own
  connector words (G31).

**Migration** `v9qrev_quote_document_revisions.py` (revision `v9qrev`, down
`u8qlink`): four nullable-or-defaulted columns, literal, reversible. Existing rows
read as revision 1, channel ERP, WRITTEN.

**Endpoints** none new. `EstimateResponse` gains `revision` and, on a revision,
`superseded = {number}` so the desk knows which document to void in the ERP.

**Tests**

- `test_quote_flow.py::test_amending_a_sent_quote_produces_a_new_estimate`
  (`:543-556`) — keep the assertion, now true against a reference-aware mock; add
  the reference `-r2` on the wire, `revision == 2`, the outcome pointing at the new
  id, and `superseded.number` in the response.
- `test_zoho_books_service.py`, `test_erp_connectors.py` — a second reference
  never matches the first document's pre-flight (Zoho, BC, Acumatica); NetSuite
  upserts a new external id rather than updating the old.
- `test_quote_flow.py` — pre-flight `ZohoUnavailable`, a `SourceScopeError` and a
  throttle each answer `ok=False` with `systemLabel` and no 500; an
  `UNVERIFIED` row is recorded and readiness reads NEEDS_ATTENTION; a customer
  change on a sent quote is 409.
- `test_quote_workspace.py` — the field change moves the header fingerprint and
  readiness leaves SENT; pressing Send twice on unchanged content records no
  second snapshot set.
- `test_quote_document_sync.py::test_a_platform_quote_that_became_an_erp_quote_is_one_row_not_two`
  — extend: `repoint_from` an own document is accepted; a foreign document is
  still refused.

**Docs** `docs/user-flows.md` §7.8 rewritten around revisions; `QuoteDocument`
docstring. **Rollback** downgrade `v9qrev`; documents already written as `-r2`
stay findable by their own reference.

### Phase 3 — One outcome of record (one to two weeks)

**Closes** G06, G08, G11, G12, G13 (outcome action), G24, G25, G26, G35, G37, G38.

**Changes — backend**

- ~~`commercial/insight/outcomes.py`~~ → **`commercial/quote_service.decide`
  (departure).** `outcomes.py` is a pure view over `DecidedQuote` rows with no
  session, and the rule needs the join `quote_service` already owns
  (`erp_documents_for`), so the rule lives beside the join: `decide(human, erp,
  document)` is pure, and `outcomes_of_record` (platform quotes, keyed by quote
  id) and `erp_outcomes_of_record` (ERP rows, keyed by `quote_document_id`) are
  the two loaders over one rule (`records_for_rows` joins rows of both kinds —
  a platform quote through its document, an ERP-raised quote through the
  reference it names). `DecidedQuote` gains `source`; an ERP-decided quote
  with no snapshot takes `value` from Σ `erp_quote_lines.amount` — pre-tax,
  the grain a snapshot's revenue is — never the header's tax-inclusive
  `total`, and no margin.
  `routers/insight._quote_evidence`, `attribution.evaluator._quote_outcomes`,
  `quote_diagnosis.replay._outcomes`, the wallet's lost-asks and
  `quote_book.totals` all read it. Won & lost gains `erp_decided_quotes` beside
  `unpriced_quotes`; price comparisons keep needing snapshots.
- `quote_workspace.readiness` gains `WON` and `LOST` from the outcome of record
  (`READINESS`, `QuoteReadiness`, `READINESS` map, `FILTERS` → "Decided").
- **Mark as sent** (G12): `POST /api/v1/quotes/{id}/mark-sent` — for a quote with no
  current WRITTEN document. Records a `quote_documents` row with `channel = MANUAL`,
  the current fingerprints, `external_system` = the quote's connector or empty,
  `thresholds_version`; then `set_outcome(SENT)`. Same gates as the send except the
  writer. Readiness, idempotency and the delete guard need no change because they
  already read `latest_document`. `Quote.canSendToErp` and `Quote.sendBlock`
  (sentence) come from `book_for_customer` at view time (no credential read), so a
  Prophet 21 / Sage quote shows "Mark as sent" instead of a Send that will refuse,
  and an unplaceable customer is named on the draft, not at the button (G35).
- The send's outcome row carries `customer_id = q.customerId` (G24, landed in
  Phase 0); Today passes `customer_label` like the worklist (G38) — looked up
  from the unanswered rows it already holds, since the queue item carries no
  label of its own (departure from "like the worklist" only in mechanism).
- `delete_quote` refuses when the outcome of record is SENT/WON/LOST (G26).

**Changes — frontend**

- One outcome form (G37): `platform/RecordOutcomeDialog` gains `lost_to`;
  `useQuoteIntelligence.recordOutcome` and `intelligence.documentOutcome` carry both
  `note` and `lost_to` (the writers already did; the callback and the hook did
  not); `QuoteOutcomeBar` uses the shared dialog. The ERP tab row and
  `ErpQuoteScreen` gain "Record…" / "Record outcome" (scoped as the worklist is,
  by the server) and show the recorded outcome beside the ERP's word (G13).
  **Departure:** offered only where nobody here has said *and the ERP has not
  already recorded a win* — a decline the ERP holds still wants a reason, an
  acceptance wants nothing. `kit.FormDialog`, full screen on a phone.
- `QuoteOutcomeBar`: SENT tip reads "Written into Zoho Books as EST-1001 (or marked
  sent). The ERP's own status is shown beside it once synced." A line "Zoho says:
  accepted 14 Sep" with **Record as won** when the ERP has decided and the person
  has not; "Mark as sent" where `canSendToErp` is false or no document exists.

**Migration** none (no column: `channel` and the qualified pointer arrived in
Phases 1–2).

**Endpoints and roles** `POST /quotes/{id}/mark-sent` — same guard as
`/estimate` (`_get_editable`); appendix row. The "Record as won" action posts the
existing outcome endpoint. Nothing new carries cost.

**Tests**

- `test_quote_outcomes.py` — an ERP-decided quote with no snapshot counts in the
  rate with `source = ERP` and reason NOT_RECORDED; a human row overrides the ERP;
  the attribution and replay readers agree with Won & lost on one fixture (the test
  that ends "three win rates"). **Landed**, plus: an ERP-declined quote is a loss
  with reason NOT_RECORDED and no winner; an ERP-raised quote a person marked
  sent follows the ERP; an undated ERP decision is not one; a DRAFT row beside a
  confirmed document reads SENT.
- `test_unrecorded_quotes.py` — a PIE-sent quote whose ERP row says accepted leaves
  the pile (**landed**, in that file); the Drafts row reads WON (**landed**, in
  `test_quote_workspace.py`, beside the mark-sent, delete-guard and send-capability
  tests).
- `test_quote_workspace.py` — `mark-sent` records a MANUAL row, readiness SENT, Send
  refused as "already covers", delete refused; a P21 quote's view carries
  `canSendToErp false` and the book sentence.
- `test_erp_quote_outcome_capture.py` — `lost_to` accepted from the shared form
  (**already pinned** there before this phase; the endpoint never lacked it).
- `test_platform_quote_outcome_scope.py` — the account holder sees the PIE half
  (customer_id now set) — **not added**: the send has stamped `customer_id` since
  Phase 0 and `test_a_send_moves_the_outcome_to_sent_and_links_the_document`
  presses the real endpoint; the scope file's own fixtures already build rows
  with `customer_id`.
- `QuoteOutcomeBar.test.tsx` (new — **landed**), `RecordOutcomeDialog.test.tsx`
  (**not a file of its own**: the dialog's new field and four-argument callback
  are pinned through its callers, `QuoteOutcomeBar.test.tsx` and
  `UnrecordedQuotes.test.tsx`, which is where a regression would be seen),
  `ErpQuoteScreen.test.tsx` (the "only control" assertion **kept**, its premise
  now stated: the fixture is a quote the ERP has won, the one state with nothing
  to record; the open-quote case has its own tests).

**Docs** `docs/user-flows.md` §7.9 and §8.3; `docs/ui-followups.md` P1 closed;
`ui-standards §10` row if the outcome row becomes a kit component.
**Rollback** revert; no schema.

**Known asymmetry, left for Phase 4.** Won & lost values a decided quote with no
snapshot from the ERP's priced lines, whoever decided it; the wallet's lost asks
still value from snapshots alone, so a loss a salesperson records on an ERP-raised
quote counts in the rate and not in the wallet's competitor share. One valuation
for both is a Phase 4 item; papering over it here would have put a header total
beside pre-tax revenue.

### Phase 4 — Registry connectors read quotes (one week per connector)

**Closes** G05 (read half), G09 (constraint), G10, G18, G19, G32, G33.

**Changes — per connector** (Business Central first: its `salesQuotes` entity is
the same one the writer creates and its id is the GUID the writer returns;
then Acumatica, whose quotes are `SalesOrder` rows of type `QT`; then NetSuite,
`transaction` type `Estim`; Prophet 21 and Sage after, read-only)

- `list_quotes(self, skip=None)` yielding the canonical (Zoho-shaped) payload
  `normalize_quote_document` validates; `external_ref` in the writer's id space.
- A `Permission` with `reads=("quotes",)` — `test_connector_permissions` pins the
  declaration against the method both ways.
- Entries in `normalize._QUOTE_VOCABULARY` and `_QUOTE_SENT_STATUSES` keyed on the
  connector; without them every quote reads UNRECORDED and never sent, by design.
- Acumatica `list_sales_orders` filters `OrderType ne 'QT'`; Sage and P21 the
  equivalent once their header vocabulary is confirmed against vendor
  documentation (G18 is cited, not verified).

**Landed for Business Central, Acumatica and NetSuite** — the three that can
also write one, which is the set where reading the quote back under the
writer's own id is worth anything. Four departures from the text above, each
decided while writing it:

- **No vocabulary entries, for any of the three.** The plan lists them as a
  change; they are deliberately absent. G18 says in as many words that these
  status enums are cited and not verified, and a word read as WON invents a
  customer decision where the silence merely leaves a quote on a worklist.
  Everything reads UNRECORDED and not-known-to-be-sent — the module's
  documented safe under-claim — and a parametrized test pins it with
  `"Closed - Won"` among the strings, so the pin is only deleted together with
  the vendor's own status list.
- **Acumatica's split is client-side, not `OrderType ne 'QT'`.** A server-side
  filter is the cheaper read and this module already states that the contract
  API's filter grammar varies across builds. A filter that silently matches
  nothing does not read as a broken filter; it reads as a finished listing of
  an empty book, and Phase 4b made a finished empty listing the thing the
  retire sweep acts on. One extra listing against every quote on the
  connection retired is not a trade worth making.
- **Prophet 21 and Sage read no quotes.** The plan has them "after,
  read-only", gated on their header vocabulary being confirmed; it has not
  been, so they are not here. Named in `docs/connectors.md` as a decision
  rather than left as an omission — along with the corollary nobody had
  written down, that P21's `oe_hdr` read may already be counting quotes as
  orders, which the same unconfirmed field would be needed to fix.
- **No expiry date from Acumatica or NetSuite.** Which column carries a
  quote's lapse date is build-specific on one and unconfirmed on the other,
  and a guessed field holding something else puts quotes on a chase list as
  overdue on a date nobody set. Business Central states `validUntilDate` and
  carries it.

Two things the text did not name, both found by writing the code. Each
connector's `_documents` helper was written for invoices and hardcoded three
invoice facts — the date field in the window filter, the `{kind}_id` key, and
that a non-trade status is droppable. All three are wrong for a quote: the
filter would have named `invoiceDate` on an entity that has no such field
(answered with nothing, which reads as a company that has never quoted), the
canonical payload spells a quote's id `estimate_id` while the stage is
`quote`, and a draft or on-hold quote is real quoting activity whose removal
shrinks the denominator this pull exists to build. And the resume-signature
pin in `test_quote_document_sync.py` named its two sources by hand; it now
derives them, because the protocol it guards binds any source that offers the
method.

**Changes — generic**

- `uq_quote_outcome_org_document` → `(org, quote_document_connection_id,
  quote_document_ref)`; `sole_erp_quote`, `unrecorded._load`, `quote_book.lines_for`,
  `assess_erp_quote`, `quote_diagnosis.service._source_record` and the route
  `/quotes/erp/:connection/:ref` all take the qualifier (G10). Legacy NULL rows
  resolve while unique, refuse by name otherwise. **Landed** — and two things
  the text above did not name, both found by writing the tests:
  `set_outcome`'s own *row lookup* keyed on the bare reference, so the second
  book's outcome found the first book's row and answered "a quote that is LOST
  cannot become WON" about a quote nobody had asked about; and the nested
  EXISTS in `unrecorded._load` needs an explicit `.correlate` — left to itself
  it asks "does this organization hold any colliding reference at all", which
  is true for every row the moment one collision exists anywhere.
- Deletion sweep: `list_quotes` records `listed` / `listing_complete`; `_mirror`
  and `_RETIRE_FROM` gain `quote_document`; a quote absent from a complete listing
  is retired per connection, its human outcome left dangling and counted (G19).
  **Landed**, under the kind name `quote` rather than `quote_document`: that is
  what `_skipper` and `mark_ingested` already key the cursor on, and the sweep
  reconciles against that cursor — two names for one kind would have made
  `ingested_in_window` return nothing and the sweep silently never run.
- `execute_sync` persists `quote_documents_unreadable_view`; `wrote_anything`
  counts quotes (G32); `mark_ingested("quote", …)` runs for a lineless detail too
  (G33). **Landed.** The lineless case cannot be read off the payload — a
  resumed row carries no lines either — so the sync wraps its own resume
  predicate and records which references it answered "skip" to, rather than
  re-deriving the producer's answer downstream.

**Migration** `w10qptr_qualified_outcome_pointer.py` (revision `w10qptr`, down
`v9qrev`): drop and recreate the unique constraint. **RISK** in the runbook
(`drop_constraint`); NULL companies stay distinct on both SQLite and PostgreSQL,
so no existing row is refused. Verified on an empty database and on the Postgres
sandbox as the gate already does.

**Tests** `test_erp_connectors.py::test_multi_connector_sync` extended with
quotes; per-connector translator tests; `test_quote_document_sync.py` — two
companies of one connector holding `SQ-1001` are two rows, two outcomes, two
worklist entries, and the bare-ref route 404s until qualified; `test_sync_mirror.py`
— a quote absent from a complete listing is retired, a cut-short listing retires
nothing.

**Docs** `docs/connectors.md` grant table and "does not read yet" list;
`docs/spec` regenerated only if `QuoteDocIn` changes (it should not).
**Rollback** downgrade `w10qptr`; connector reads are additive.

### Phase 5 — Send to Business Central and NetSuite from the builder; demo (one week)

**Closes** G04, G13 (open in builder), G31 (fixture), G34.

**Changes**

- A `SourceCatalogue` backed by the synced master for registry connectors —
  `ingestion/item_master.SyncedCatalogue` (extend `item_master.py`, which already
  reads `item_connector_records`): `get_item(code)` answers `in_books`, `item_id =
  external_id`, the master's list price where the source carries one, `stock` from
  the latest stock snapshot or `None`, `cost = None` unless a costed record exists,
  and an `as_of` stamp. `_books_for` hands it to `QuoteBooks.zoho` instead of the
  refusing adapter; the line status reads "SYNCED 12 Sep" rather than BOOKS
  OFFLINE, and `select_supply` copies `externalId` from search results onto
  `itemId`. `create_item` stays refused for these connectors (no live write).
- **Revise an ERP quote in PIE** (G13): `POST /api/v1/quotes/from-erp` with
  `{connection_id, ref}` builds a form from `erp_quote_lines` (code, description,
  qty, rate) through the ordinary `build_lines`, bound to that connection's
  catalogue and customer, with `QuoteFormDraft.source_erp_quote_ref` carried into
  `QuoteDraft` on save so the new quote's first document can name the ERP quote it
  revises. The ERP page gains "Revise in PIE".
- Demo: seed a `ZohoConnection` for the demo organization, point `qdoc_demo_ace`
  at it, seed one PIE_SENT quote (draft + document + outcome) that joins to a
  second seeded ERP quote; extend `purge_demo_seed` and
  `test_purge_removes_every_demo_row`. The fixture source's `list_quotes` includes
  documents the mock writer created (G31).

**Migration** `x11qsrc_quote_source_erp_ref.py` (revision `x11qsrc`, down
`w10qptr`): nullable `source_erp_quote_ref` and `source_erp_connection_id` on
`quote_form_drafts` and `quote_drafts`.

**Tests** `test_quote_flow.py` — a BC customer's quote sends end to end with item
ids from the master; a NetSuite one too; a line the master does not hold reads NOT
IN BOOKS and is refused by name. `test_quote_book.py` — `from-erp` refuses a ref
the principal may not see (404 like the lines endpoint). `test_demo_purge.py`.

**Docs** `docs/user-flows.md` §7.2 (the synced catalogue state), appendix rows.
**Rollback** downgrade `x11qsrc`.

### Phase 6 — Gate and contract hygiene (two days)

**Closes** G30, G39, G40.

- Contract-check `EstimateResult`, `QuoteOutcome`, `UnrecordedQuote`; frontend
  tests for the sent / amended / revision states of `SummaryBar`.
- `GET /quote-diagnosis/quote/{quote_id}` scoped like `quote_audit` (uniform 404).
- `CONTRIBUTING.md`, `docs/development.md`, `Makefile` help, `verify.sh:90`,
  `require-verify.sh:64` — eight steps, the real test count, and the `--fast`
  description; `verify.sh`'s pie-parser note names `PIE_PARSER_ROOT`.

---

## 4. Open decisions for the owner

Each has a recommendation; the plan is written to it. **All six were decided on
2026-09-20, each on the recommendation** — kept here with the alternatives so
the reasoning stays readable beside the code that implements it.

**D1 — What does SENT mean?** *Recommended: "left this desk"* — written into the
ERP under the customer's account, or marked sent by a person — with the ERP's own
word shown beside it. The alternative, "the customer has it", would need either an
ERP-side send action per connector (Zoho has a mark-as-sent status call; Business
Central, Acumatica and NetSuite have no uniform equivalent) or a manual step on
every quote, and would leave every quote that is emailed from the ERP stuck at
DRAFT in PIE until the sync catches up. Phase 3 implements the recommendation;
choosing the alternative changes Phase 3's tips and adds a per-connector action.

**D2 — What happens to the previous ERP document on a revision?** *Recommended:
leave it, name it in the response, void it in the ERP by hand* for now; add
void-on-revise for Zoho as a follow-up once its status endpoint is verified against
the live API. Automatic voiding across four connectors is a second write path with
its own settle logic, and a wrongly voided document is worse than a duplicate one.

**D3 — Should an ERP-side decision be written onto the human table?**
*Recommended: no — derive it* (§2.5). Writing it would either give the sync a
pen on `quote_outcomes` (pinned against, and the reason a re-sync cannot destroy a
recorded reason) or need a second background writer in `commercial/` with its own
audit story. Deriving gives the numbers and the screens the same answer with no
new writer; a person still records the reason, which is the one fact the ERP
never holds.

**D4 — Customer change after a send.** *Recommended: refuse*, as delete already is;
start a new quote. The alternative — a revision under the new customer — leaves the
first document on the wrong account in the ERP with nothing in PIE to say so.

**D5 — May a draft price from one company's catalogue and be sent to a customer
of another?** *Recommended: no.* The company whose catalogue priced the lines is
the company that invoices. Allowing it means a quote whose item ids, prices and
audit stamp come from two books. If a real case exists (a customer held in two
books), the answer is to choose the company first, which the picker already does.

**D6 — Organization-wide pricing evidence across companies (G21).** *Recommended:
leave as designed and documented* (`ZohoConnection` docstring); note the two edges
— name-tie product matching picks by row order, and the sellable pool offers every
company's decoded products — as known, and revisit only if a company pair with
different masters is connected.

---

## 5. Out of scope, and why

- **Writers for Prophet 21, Sage 100 and Sage X3.** Sage 100 has no HTTP write
  surface (`docs/connectors.md`); P21 and X3 write paths are unverified. Their
  quotes leave PIE through "Mark as sent" (Phase 3).
- **Voiding or emailing ERP documents from PIE** (see D1, D2).
- **Deep links into the ERP's own UI** (G36 second half): per-tenant URL shapes;
  not verified for any connector.
- **A CRM-style activity or follow-up log** — `PRODUCT.md` records it as an open
  product decision.
- **Any change to pie-parser.**

---

## 6. Risks

- **Phase 0 restores a write that has been dead since 5 September.** Quotes sent
  since then have documents but no outcome row; the first Won & lost view after
  Phase 0 will count only quotes sent afterwards. A one-off script to open SENT
  rows for existing `quote_documents` is cheap (one query) and should be run under
  the owner's eye, not silently on deploy.
- **Phase 2's reference scheme touches every adapter's idempotency.** Each
  adapter's pre-flight is tested with a second reference; the rollback keeps
  every document findable because r1 keeps the bare reference.
- **Phase 4's constraint change is the only RISK migration.** Verified on an empty
  SQLite and the Postgres sandbox before merge, as the gate does.
- **The engine-backed tests skip silently without `PIE_PARSER_ROOT`.** Every
  phase's report quotes the `requires_pie` count.
- **UI surface.** Two new chips, one filter, one dialog widening and one page
  control, all through kit components; the phone layout is covered by
  `renderNarrow` and `FormDialog`'s full-screen rule.

---

## Appendix A — Gap → phase

| Phase | Gaps |
|---|---|
| 0 | G01, G17 (surfacing), G23, G28, G29 |
| 1 | G03, G07, G09 (columns), G20, G36 (filters) |
| 2 | G02, G14, G15, G16, G17 (rule), G27, G31 (mock) |
| 3 | G06, G08, G11, G12, G13 (outcome), G24, G25, G26, G35, G37, G38 |
| 4 | G05 (read), G09 (constraint), G10, G18, G19, G32, G33 |
| 5 | G04, G13 (revise), G31 (fixture), G34 |
| 6 | G30, G39, G40 |
| decisions | G11 (D1), G21 (D6) |
| out of scope | G05 (P21/Sage write), G36 (deep links) |

## Appendix B — What each phase reuses rather than adds

`set_outcome` (extended with `repoint_from`), `record_document` (four columns),
`latest_document` (unchanged, now also covers manual sends), `sole_erp_quote`
(qualifier), `book_for_customer` (draft-level refusal), `origin.Companies`
(every label), `settle_by_read` (unchanged), `_mirror` / `_RETIRE_FROM` (quotes
added), `item_master.py` (synced catalogue), `RecordOutcomeDialog` (one form),
`CompanyFilter`, `DataGrid`, `FilterChip`, `FormDialog`. No new table, no new
package, no new protocol with one implementer.

## Appendix C — How this was produced, and how far it was checked

Seven independent readers mapped the subsystems (platform lifecycle, ERP
ingestion, the write path per connector, outcomes and insight, the frontend,
multi-connection, tests/docs/gate) with file-and-line evidence; a consolidator
merged their 92 claims into 40 gaps and resolved seven contradictions between
readers by re-reading the code. Gaps **G01–G06** were then each put to three
adversarial verifiers with different lenses (refute from the code; refute from
tests and stated intent; refute on relevance), default refuted; all six survived
all three. **G07–G40 were not adversarially verified**: the run hit a session
limit before those verifiers ran. Their evidence is cited file-and-line by the
reader that found them, and the ones marked **direct** above were additionally
reproduced or read in this session (G01 through the real endpoint with the mock
writer; G02 in `zoho_books_service._estimate_by_reference` and
`dynamics365._settled_quote`; G04 in `store._enrich_from_zoho`; G09, G11, G14,
G23, G28 by reading). Treat a **cited** gap as a strong lead with evidence, not as
settled; the phase that addresses it starts by confirming it.
