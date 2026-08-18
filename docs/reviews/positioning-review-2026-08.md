# Positioning review — "Global, ERP-agnostic, B2B distribution"

Review date **2026-08-11**, against `ab52e2e` on `main` (pie-portal) and the
pinned `pie-parser` submodule. The question asked was whether the platform is a
*global, ERP-agnostic, B2B distribution* application, and what to do about the
answer.

> ## Status — read this before acting on anything below
>
> **This is a point-in-time analysis, not an open bug list.** Findings are
> preserved as written. Where a claim was verified by opening the file, it is
> marked **[V]**; where it is reported by an analysis agent and not
> independently re-checked, it is marked **[R]**. That distinction is load
> bearing — several **[R]** findings corrected earlier **[R]** findings, and one
> corrected a claim made in this document's own first draft.
>
> Effort figures are **calendar weeks for one competent engineer**. Every figure
> is a *challenged* number: an independent reviewer was asked to refute the
> original estimate. All four original estimates were found optimistic by
> 50–73%. Treat even the corrected numbers as lower bounds.

---

## 1. The answer

**No. It is a B2B distribution platform, built for India, on Zoho Books, for
cutting tools.** One of the three claims holds strongly; two are scaffolded but
not built.

| Claim | Score | One-line reason |
|---|---|---|
| **Global** | 4/10 | Presentation layer is genuinely parameterized; there is no multi-currency, no tax engine, and no jurisdiction concept |
| **ERP-agnostic** | 3/10 | One layer (`identity/`) is genuinely connector-neutral; the ingestion contract is literally named `ZohoSource` and yields raw Zoho payloads |
| **B2B distribution** | 8/10 | 31 insight modules of real distribution economics; the strong axis by a distance |

**Composite: 5/10 against the stated claim.**

The mismatch is between the code and the *claim*, not inside the code. The
repository's own README says "B2B industrial cutting-tool distributor running on
Zoho Books" plainly. The code is honest; the positioning sentence is not.

---

## 2. Method

Fourteen analysis agents in four phases: five parallel seam surveys, four
independently-argued strategy paths, one adversarial reviewer per path, and a
synthesis reconciling on challenged costs.

The run was interrupted once at the challenge phase and resumed from cache;
the nine completed agents replayed without re-running. Final tally: 14/14
complete, 0 errors, 0 empty results, ~905k subagent tokens.

**What this method is good at:** finding things by reading every file, and
catching a plausible-but-wrong claim by having a second reader try to refute it.
Four of the sharpest findings in this document are refutations of other findings.

**What it cannot do:** none of it touched a customer. Every strategic conclusion
below is grounded in code and none is grounded in demand. Each path's own author
flagged this unprompted.

---

## 3. Live defects

This is the most actionable output of the review and it is independent of any
strategic choice. Ordered by consequence.

### 3.1 The deletion sweep and event log are unscoped by connection — **[V]**

`sync.py:744-746` documents a guard — *"Only this connection's documents"* — that
does not exist. `repositories.ingested_in_window` (`repositories.py:460-480`)
filters on `organization_id` and date only. `EventLog.supersede`
(`state/events.py:146-161`) matches on `(organization_id, source_doc_type,
source_doc_id)` with no connector clause, **even though its own constructor at
`:104-110` holds both values and writes them onto every row**.

Three Zoho companies are safe today only because Zoho ids happen to be globally
unique. The day a second connector's ids collide, connection A's sweep retires
connection B's documents, which supersedes their events, which — because every
derived state is replayed from the event log — rebuilds `BusinessState` to empty.
That is a restore from backup, not a bug fix.

Related: `jobs.py:393` constructs `SyncService` without `connector=`, silently
taking the `"zoho"` default at `sync.py:269`. `_sole_connection` (`sync.py:240`)
counts `models.ZohoConnection` only and gates `adopt_connectionless`.

**This fix requires no migration.** `BusinessEvent` already carries `connector`
and `connection_id` **[V]**, so `supersede` is a two-line `WHERE` addition. And
`IngestedDocument` already carries `connection_id` with a `doc_type` vocabulary
identical to `_MIRRORED` **[R]**, so `ingested_in_window` is scopeable by a join.
Two of the four strategy proposals sequenced a three-week, fifteen-table
migration *in front of* this fix. That dependency is fictional.

**Blast radius is four tables, not fifteen** **[R]**: `_mirror` is called exactly
twice — invoice and bill — and `_RETIRE_FROM` deletes from `SalesTxn`,
`InvoiceDoc`, `CostRecord`, `BillDoc` only.

`test_sync_mirror.py` has nine tests and not one sets up two connections **[R]**.

### 3.2 Per-entity numbers are misattributed today — **[V]**

`SalesTxn` and `CostRecord` carry a customer, a product and a date, and **no
entity** — verified: neither table has a `connector` or `connection_id` column.
The only module that partitions by book, `insight/cycle.py`, does it via
`_book_of` (`routers/insight.py:1428-1436`), which reads `connection_id` off the
**master** record.

So any customer or product traded by two of SLS / 4U / UPS is attributed wholly
to one of them, silently, in GMROI, margin and cash-conversion cycle.
`insight/selffunding.py:30-32` states the consequence in the codebase's own
words: *"the trading rows carry a customer and a date and no entity."*

This is a today-bug in a three-entity book, not a future feature.

### 3.3 The quote total and the created Zoho estimate already disagree — **[V]**

`store.py:64-66` claims *"Line-level tax from the ERP supersedes this wherever it
is available."* That claim is false: `tax_amount|tax_percentage|item_tax|tax_id`
return **zero hits** across the entire backend. The whole tax system is
`store.py:413-414`, `tax = subtotal * rate`.

Meanwhile `zoho_books_service.py:352-356` sends no tax field on the outbound
estimate, so Zoho prices GST from its own item and contact settings. **Any item
on a non-default GST rate makes the quote screen and the created estimate
diverge, today, for the Indian tenant** — on the one screen the product exists
for.

### 3.4 The statutory modules degrade to confidently wrong — **[R]**

Run against a synthetic AED / Asia-Dubai tenant, `insight/msme.py` and
`insight/withholding.py` do not degrade to empty. They return every open supplier
bill as an MSME data gap, all flagged already-past a 15-day Indian deadline,
banded FY2026-27.

There is no jurisdiction concept to gate them on: the word appears in three
comments and nowhere in code, `Organization` has `erp`/`currency`/`timezone` and
no country, and every statutory endpoint is gated on role and never on where the
tenant trades. This is CLAUDE.md §1's *"absence of evidence is not a pass"*,
live.

### 3.5 The erasure receipt overstates what erasure does — **[R]**

`trust/vault.py` concedes `customers.name` and `products.name` remain plaintext;
`erasure.erase()` destroys only the DEK. The signed receipt says
"crypto-shredding". This is the finding most likely to fail a second customer's
security review, and the rest of `trust/` is sound enough that closing this one
gap would validate it.

*Status note, 2026-08-16 — closed since this review.* The receipt no longer
claims crypto-shredding: it names the DEK destruction as the method and
enumerates both what the key loss reached (`erasure.DESTROYED` — the two
DEK-encrypted field classes) and what stays readable in plaintext
(`erasure.SURVIVES_PLAINTEXT`, `customers.name` and `products.name` included,
each with its reason). The attestation is stamped on the receipt row and
covered by the signature, and `test_trust_controls.py` fails if the
surviving-plaintext enumeration is removed. Erasure behaviour itself is
unchanged — encrypting the plaintext columns remains the separate project this
finding said it was.

### 3.6 Smaller, still real — **[R]**

- **`config.target_margin_by_family` keys are parser vocabulary with nothing binding them.** A pack rename silently drops every line to `target_margin_default`. `test_floor_families.py:68` already does exactly this job for `m_floor_by_family`; the counterpart test was never written.
- **The layer-boundary test has a transitive hole.** `ai/ → context/ → signals/` already exists; one import added to `context/assembler.py` opens `ai/ → context/ → commercial/` with the suite still green.
- **pie-parser's §1 invariant check is brand-scoped only.** It reports "clean" while `engine/pack.py:45` hard-codes sixteen cutting-tool family names and `engine/routing.py:119` raises `ConfigError` at pack load for anything outside them.
- **`IngestedDocument.modified_at` is `String(64)` under `func.max()`** — a lexicographic max, correct only for ISO-8601.
- **`equivalence/distance.py:149` returns `dimensional_score = 1.0` when no dimensions are comparable** — a vacuous perfect match. See §6 for why the *urgency* argument around this was wrong.

---

## 4. The five seams

### 4.1 ERP — 16w survey estimate

The seam is **declared but not built**. `ingestion/source.py:18` defines a
Protocol literally named `ZohoSource` whose docstring commits to yielding "raw
Zoho Books payloads". Quantified **[R]**: 19 of 23 functions in `normalize.py`
are payload-shaped; 20 of 45 in `sync.py`. There is no canonical intermediate
record.

What genuinely survives a second connector **[R]**: `identity/` is
connector-neutral (zero connector strings in `matchers.py`); `state/engine.py`
already keys master resolution on `(connector, connection_id, external_id)`;
`repositories._for_upsert` already scopes upserts by source; and **only one real
foreign key points at a `zoho_*` table** (`models.py:171`) — every other
`connection_id` is a bare `String(64)` — so the connection tables can be
restructured while preserving values and no other constraint moves.

The gap: the `(connector, connection_id)` fix applied to `customers`/`products`/
`vendors` — with a comment at `models.py:271-274` naming Tally's per-company
ledger numbering as the exact hazard — was **never applied to the document
tables**. There are **fifteen** such constraints, not thirteen; the count is
easy to get wrong because `InvoiceSalesOrderLink` (`models.py:2205`) and
`StockLocationSnapshot` (`models.py:2296`) key on an external ref under a
different shape.

Good news on the backfill: the `connector` half is free — `normalize.py`
constructs `SourceRef(system=ZOHO, …)` at nineteen sites and `SalesTxn.source_ref`
is a JSON column on every row. Only `connection_id` needs a heuristic, and
`repositories.py:105-110` gates adoption on **exactly one connection** — which
this organization deliberately violates by running three.

### 4.2 FX — 16w survey estimate

**There is no FX seam.** Currency exists in exactly one place —
`Organization.currency` **[V]** — and it is a label, never an attribute of a
number. None of the money columns carries a currency, and neither do the DTOs
every deterministic path funnels through.

The load-bearing consequence is one line: `commercial/economics.py:92-95`
computes `cogs = unit_cost * qty` then `gross_profit = revenue - cogs`, where
revenue came from an invoice line and cost from a bill line. A EUR bill against
an INR invoice yields a ~99% margin and nothing can notice.

**Important correction to the obvious framing** **[R]**: `zoho_client.ping`
returns the *company's base* currency — one value per Zoho company. A single
INR-base company can and does hold EUR-denominated purchase bills, which is
precisely the exposure. So a guard comparing connection base to organization
currency passes that book cleanly. It covers only the narrower
two-companies-with-different-bases case.

**And the blast radius is differently shaped than assumed** **[R]**:
`economics.aggregate` has **9** call sites, and exactly **one** of the 32 insight
modules imports economics (`gmroi.py:89`). `cycle.py` does not. `dependency.py`
does not, and holds money as `float` rather than `Decimal`. That is worse news,
not better — fixing `economics.py` leaves 30 of 32 insight modules doing their
own cross-row money sums.

Rate provenance cannot live in `CommercialThresholds`: `version` is
`sha256(asdict(self))`, so a daily rate would re-hash `ci_` every day and destroy
the one claim §1 makes for the stamp.

### 4.3 Jurisdiction — 9w survey estimate

The India coupling is **narrower than it looks and worse-placed than it looks**
**[R]**.

The statutory *arithmetic* is portable: `msme.deadline_for` implements
default-days / agreed / capped-at-statutory-max, which is precisely EU Late
Payment Directive 2011/7/EU art.3 and the UK LPCDA 1998. `withholding.crossings`
is a cumulative per-counterparty per-year threshold — the shape of US 1099-NEC.

What is India-only is the *consequence* model: pricing a deduction disallowance,
and the financial year as the unit of consequence.

**The single hardest knot is `FY_START_MONTH = 4`** — one integer, inside a
statute module, from which `withholding.py` and `selffunding.py` import their
year boundary. A capital-efficiency module inherits the Indian April year through
two statute modules.

HSN is a **false positive**: every heading in `hsn_category_ranges` is a real WCO
4-digit heading and `heading_of` truncates to exactly the internationally
harmonized prefix. That module needs a docstring edit, not an abstraction.

### 4.4 Vertical coupling — 24w survey estimate

**The portal is far less cutting-tool-coupled than its name suggests, and the
parser is far more so than its own invariant admits** **[R]**.

Portal: only 6 of ~200 backend files import the parser bridge. An AST scan
excluding docstrings finds **54** cutting-tool string literals in executable
backend code across 8 files — every one a config-shaped lookup table, never
branching logic. **All 32 insight modules have zero parser references.** The
bridge is ~560 LOC of 53,482 — **1.2%**. The frontend is essentially clean.

`PIE_DOWN` degradation is real and verified end to end: with the parser fully
off, the Quote Builder survives as a working manual quoting desk.

Parser: the brand-literal and offline-import invariants both report **clean**.
But `engine/pack.py:45` holds a sixteen-entry `FAMILY_REGISTRY` of cutting-tool
family names and `engine/routing.py:119` hard-fails outside it. Worse, the layers
the portal actually calls — `resolver/`, `identity/`, `equivalence/` — carry
**2,689 LOC of cutting-tool schema written in Python, outside `packs/`**, plus 8
lookup CSVs the pack mechanism cannot swap.

**The engine is brand-free, not industry-free.** A second *manufacturer* is
genuinely unblocked. A second *industry* is not.

### 4.5 Reusable core — the asset register

Portable **[R]**: `commercial/insight/` (12,197 LOC, 31 modules), `signals/`,
`state/`, `trust/`, `ai/`, `identity/`, and the frontend platform layer. The
strongest structural property is verifiable rather than asserted: **28 of the 31
insight modules import nothing beyond stdlib and sibling insight modules** — pure
functions over passed-in dataclasses, no ORM, no connector, no HTTP.

Genuinely differentiated **[R]**:

1. **The cost-non-disclosure machinery** — `quote_exceptions.boundary_refs` plus `quote_service._project_exceptions`, encoding that *the fact a named rule fired is itself a predicate on the number it tests against*. Most teams reach this only after shipping the leak twice, which CLAUDE.md §1 records this one having done.
2. **The trust layer's pseudonym → vault → rehydrate loop**, with a keyed-digest alphabet chosen so labels cannot collide with the AI grounding regex.
3. **`insight/schemes.py`** — principal rebate-slab economics, where `marginal` is measured from projected close rather than spend-to-date.

Honest caveats: the remainder is **vertical-neutral within physical-goods
distribution**, not vertical-neutral — GMROI, stock cover, dead stock, supplier
concentration and rebate slabs are meaningless for a services business. And four
insight modules (`cohorts`, `order_to_cash`, `radar`, `selffunding`) have a
single test file mentioning them; they are wired to live routes and will render
*wrong* for a second customer before anyone notices.

### 4.6 The package nobody scoped — **[V]**

**`backend/incentive_engine/` — 2,238 LOC across 18 files, outside `app/`, with
zero mentions in CLAUDE.md.** Absent from the §3 module map and from all five
surveys.

It is a **payroll** system, and it is India- and rupee-coupled in the same three
ways the generalization paths propose to fix **[R]**: absolute rupee constants
(`training_day_cap: 25000`), a GST gross-up baked into a published rate
(`rate_annual 0.1416`), and GSTIN-keyed customer attribution. It is imported by
`commercial/floor.py` and `commercial/incentive.py` — the two files the FX and
jurisdiction steps both name — and none of the four plans budgeted a week for it.

It also holds the authority for the floor-family vocabulary, in
`config/parameters.yaml`, as a single **global** effective-dated file — while
`policy.py:68-76` explicitly forbids making incentive rates org-editable:
*"a rate a salesperson can watch move mid-year is a discretionary bonus wearing a
formula costume."*

---

## 5. The four paths, challenged

All four verdicts: **OPTIMISTIC**. None UNSOUND — the theses survived, the
schedules did not.

| Path | Proposed | Challenged | Δ | Fatal flaw in its own first move |
|---|---|---|---|---|
| **Widen the connector** (Tally) | 16w | **26w** | +63% | Prices a 3-week migration as prerequisite to a fix needing none |
| **Drop the vertical** (horizontal India) | 16w | **26w** | +63% | Step 2 is a literal no-op |
| **Lean In** (second pack) | 22w | **38w** | +73% | Loading a second pack makes the flagship query *worse* |
| **Finish the seams** (global) | 32w | **48w** | +50% | Budgets zero weeks for `incentive_engine` |

### 5.1 Why Lean In is worse than it looks — **[R]**

`resolve_rfq.run` is **identity-first**. At `resolve_rfq.py:459` an authoritative
same-product hit returns immediately with `suggestions: []`. Today a competitor
part number is not in the authoritative index, so it falls through and gets
suggestions. **The moment the second pack's catalogue is loaded, that same
competitor code becomes an exact authoritative identity** — `run` returns zero
suggestions, `pie_service._map` sets `supplyCode` to the competitor's own part,
and `_enrich_from_zoho` finds nothing and sets `inBooks = False`.

Loading the second pack makes the headline query answer with an un-sellable
competitor part and no alternatives — **strictly worse than today**.

Compounding: `identity/store.py` builds `_material_collisions` and
`_catalog_collisions` **globally** and returns `None` for any colliding key, so a
part number shared across two makers stops resolving **for both, silently**. And
the ranker is structurally biased — measured, a competitor's own record scored
**1.0000** against the geometrically identical Kennametal part at **0.9200**,
because soft-signal vocabularies never string-match across brands.

### 5.2 Why horizontal-India is worse than it looks — **[V]**

Its step 2 proposes feeding the floor-family ladder's answer into
`target_margin`. The two vocabularies are **disjoint** — verified by reading both:

- `floor_families.FAMILIES` = `inserts, solid_carbide, holders_toolsystems, metrology, chemicals, machines`
- `target_margin_by_family` keys = `solid_carbide_drill, solid_carbide_endmill, milling_insert, drill_tip, reamer`

`config.target_margin` matches exactly (`if name == family`), so the fallthrough
returns `target_margin_default` — 0.24 — for all six families. That is precisely
the blended-margin outcome the step exists to prevent.

Worse **[R]**: `floor._m_floor` calls `m_floor_for_family(strict=True)` and
`routers/insight.py` turns the miss into a **400**. A bearings tenant does not
get default pricing; they get a hard error on `/negotiate` for every item their
own tariff table placed.

---

## 6. Two corrections worth recording

Both are cases where a confident, plausible finding was wrong, and the refutation
came from someone *running the code* rather than reading it.

**The vacuous-match urgency argument was false.** The claim was that adding a
second pack would break the score ties that currently mask
`equivalence/distance.py:149`, so the fix had to land first. It does not: soft
signals only adjust when **both** sides specify a field, and a vacuous input
(which by definition specifies no dimensions) specifies no coating or chipbreaker
either — so `soft_adjust` is 0.0 on every candidate regardless of how many packs
are loaded. Ties persist; adding candidates makes the abstention fire *more*, not
less. The fix remains worth doing on its own merits. The reason to fear a second
pack is §5.1, which is a different and larger problem.

**`Signal` does not carry `ci_`.** The claim that a `CommercialThresholds` change
orphans `Signal` rows is wrong — `Signal` carries `threshold_config_version` from
`SignalThresholds`, which CLAUDE.md §1 itself notes (`th_` and `ci_` are not the
same stamp). The genuinely orphaned append-only rows are `ApprovalRequest` and
`QuoteDecision` only. Blast radius roughly half of what was claimed — but the
uncosted fix (persisting retired threshold *sets* keyed by hash, ~3w) is a hard
prerequisite to any field addition, not a follow-on.

---

## 7. Recommendation

**Do not pick a path yet. Spend ~12 weeks on containment and measurement, then
branch.**

The reasoning is not caution for its own sake. Each path is a 26–48 week bet on a
customer nobody has met, and each one's *first move* is blocked or fictional
(§5). Meanwhile there are two live destructive defects that are cheap and
certain, and two zero-code experiments that would decide between the paths — and
no proposal scheduled either as its own first move.

### M0 · Containment and measurement — 2w, reversible

Write the two failing tests first (`test_sync_mirror.py` has nine tests and not
one sets up two connections):

- two connections, same `external_ref`, run `_mirror` for A, assert B's invoice survives
- `supersede("invoice","1")` under A, assert B's events for that doc are live

Then fix both — **no migration**. `BusinessEvent` already has the columns;
`IngestedDocument` already carries `connection_id`. Make `connector` a required
keyword on `SyncService.__init__` and fix `jobs.py:393`. Fix `_sole_connection`.

**Then the decisive half, which is two measurements and no code:**

1. Pull twenty live purchase bills and read `currency_code` and `exchange_rate` off the raw payloads. Says whether FX is a live defect on this book or a hypothesis.
2. Run twenty real lost-quote lines through **`app.pie_service.resolve`** — *not* `tools/find_equivalents.py`. That CLI already accepts `--catalogs` plural and is tempting, but it bypasses the `resolve_rfq.py:459` identity short-circuit, so it will look good while the product path is broken.

### M1 · Currency truth at ingestion — 3w, reversible

Add `currency_code` to the document projections; add `base_currency` to the
connection; **refuse at ingestion** any document whose currency differs from its
connection's base, using the existing skip-with-context machinery. Note the
write-back site: the timezone is written in `routers/connections.py`, inside
`_check`, which only runs on an explicit operator action — `add_connection` never
pings. So the guard belongs on the **creation** path.

This buys the whole "no silently wrong number" property without touching
`economics.py`, `floor.py`, `incentive_engine`, or the version hash.

### M2 · Document provenance — 5w, the UQ change is one-way

Fifteen constraints. `connector` half of the backfill is free from `source_ref`
JSON; `connection_id` half is not. Ship nullable plus the two-step fallback
`get_customer_by_external` already implements; `NOT NULL` only after a full
re-sync.

### M3 · Branch gate — week 10

Decide on M0's two measurements plus prospect evidence.

### M4 · Quote-total honesty — 6w, severable

§3.3. Wrong today, on the screen the product exists for.

---

## 8. What not to do

- **Do not load a second pack into the authoritative index** until the reference-vs-identity branch at `resolve_rfq.py:459` is written. Irreversible for identity: collisions null out *both* brands.
- **Do not add any field to `CommercialThresholds`** until retired-threshold persistence exists (~3w, uncosted by every plan).
- **Do not make floor families tenant-authored.** It breaks `test_floor_families.py:67` and re-opens the price-enumeration channel `floor.py:26-32` was written to close.
- **Freeze rather than extend:** `Organization.erp` (written by `seed.py`, read nowhere — delete it before a second connector makes it a contradictory discriminator); `domain/origin.py` `CONNECTORS` (six rows, one implemented — keep it out of every deck); `incentive_engine/config/parameters.yaml` (published annually by design).

### One-way doors

| Door | When | Take it? |
|---|---|---|
| The 15-table UQ change | M2, week 9 | **Yes** — the only prerequisite shared by every forward path |
| The `ci_` rehash | Not before retired-threshold persistence | Defer past week 12 |
| Second pack into the authoritative index | Only after the reference-vs-identity branch | Not yet |
| Connections/credentials restructure (8w) | Until a real Tally prospect exists | Defer — cheap, only one real FK |
| `VendorMsmeStatus` generalization | Released migration | Forward reconciliation only |

---

## 9. What the claim should say

**Today, and every clause is backed by code:**

> *A margin and cash decision desk for Indian B2B distributors running Zoho Books
> across several companies — with a quote engine that structurally cannot leak
> your cost to the person negotiating.*

Multi-company is `ZohoConnection` many-per-organization. The cost claim is
`quote_exceptions.boundary_refs` + `quote_service._project_exceptions`, pinned by
`test_a_salesperson_cannot_walk_the_price_to_recover_cost`.

**After M0–M2**, add: *"…that tells you when it cannot answer instead of
guessing, and computes your MSMED and 194Q exposure rather than asking you to
remember it."*

Still not "ERP-agnostic" — one connector. Still not "vertical-neutral" —
`insight/` is meaningless outside physical-goods distribution.

**The word to retire now is *Global*.** It is the only one of the three with no
code behind it, and §3.4 is worse than absence: the statutory modules do not fail
to apply outside India, they answer confidently and wrongly.

---

## 10. Honest dissent

The strongest case against this recommendation: twelve weeks of correctness work
ships nothing a customer can see, and §4.4's own numbers say the general asset is
twenty times the vertical one by volume — so the window goes to neither growth
nor differentiation, while a competitor with a worse product but a real customer
learns things this analysis cannot. Every refutation honoured here is of the form
*"this costs more than you said"*, and the correct response to that may be to
pick the highest-upside path and pay, rather than to buy information.

**The signal that flips it:** the `pie_service.resolve` measurement in M0. If
≥15% of lost-quote lines return a TECH or COMPAT candidate the owner confirms he
could have sold, cross-brand cross-referencing is the moat and Lean In wins —
with the reference-vs-identity branch as step *zero*. Conversely, if the twenty
live bills come back with `currency_code ≠ INR` on any material share, M1 stops
being a guard and becomes the roadmap.
