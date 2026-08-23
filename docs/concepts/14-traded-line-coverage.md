# 14 — Coverage over what was traded, not over what is in the master

**Status: the instrument is built and tested; the number is UNKNOWN.** This
checkout has no synced database and no ERP credentials, so no coverage figure
appears below. Fabricating one from the demo seed would be worse than leaving it
blank — §4 says exactly what is missing and what running it costs.

**Read this before quoting whatever it eventually prints.** The figure will be a
**lower bound, not the answer**, and the bias is structural rather than a
sampling accident: *a line became an invoice line because somebody could already
identify the product.* The denominator therefore conditions on the very outcome
the product exists to change, and it systematically excludes the lines nobody
could identify — the enquiry never quoted, the competitor part nobody could
cross-reference, the customer who went elsewhere. Those are precisely the lines a
cross-referencing engine is for. Whatever this measures, the reachable population
is larger, and this instrument cannot see how much larger. Closing that gap needs
forward capture of *all* inbound lines including unquoted ones, which is a
different instrument and is noted as such in §5.

---

## 1. Why the published number needed a second denominator

`01-application-engineering.md` §1 reports **21.6% union coverage over 15,028
master items**, and the strategic verdict in its §7 rests on that number. The
denominator is the Zoho item master.

An item master is a graveyard. It holds dead stock, one-time buys, migration
artifacts, and — by the prior measurement dated 2026-08-09 — 6,146 rows carrying
no manufacturer at all. Nobody quotes out of it. A coverage figure whose
denominator is mostly items no salesperson will ever type understates what the
catalogue does for the actual work.

`01` §1 already gestures at this by narrowing to stocked and Kennametal-labelled
items (42.5%), but stock on hand is a proxy for trade, not trade. What moved is
recorded directly, one row per invoice line, and that is what this measures.

This does **not** overturn `01`. It measures a different population and is
expected to read higher; a higher number over a denominator that conditions on
identifiability is not evidence that the master figure was wrong.

---

## 2. What it measures, three ways

`scripts/measure_quoted_line_coverage.py`, read-only, one question.

Coverage is reported **three ways because they answer different questions and
routinely disagree**, and by entity, because a distributor's books do not average:

| Weighting | The question it answers |
|---|---|
| **By distinct product** | How much of the traded catalogue is reachable |
| **Frequency-weighted** (line count) | How much of the day's line-picking is reachable |
| **Value-weighted** (selling price) | How much of the revenue is reachable |

Two coverage halves, mirroring `01` §1, reported separately and as a union:

- **Identity** — `Product.pie_record_id`, the exact catalogue link that
  `sync._link_catalog` already writes on every item pull through
  `pie_service.lookup_record`. Nothing in the codebase reads that column back, so
  on a synced database this half is a JOIN rather than a re-resolution.
  `--reresolve` recomputes it live through
  `SalesTxn.product_id → ItemConnectorRecord.sku → pie_service.lookup_record` and
  reports where the two disagree, which is how a link written under a superseded
  catalogue becomes visible.
- **Geometry** — the item *name* decoded, gated on **full ISO slot fill**
  (`iso_shape` + `edge_length_mm` + `corner_radius_mm`), never on a family route.
  `01` §3 measures the family route putting an EMUGE screwdriver and an `M3X11`
  screw into `turning_insert`, and records that full slot fill drops definite
  misroutes to 1 in 2,057.

Three implementation facts worth stating, because each is a plausible wrong turn:

- **`Product` has no `sku` column** and the sync never persists one. SKU lives
  only on `ItemConnectorRecord.sku`. A join the schema cannot support is the
  obvious approach and it does not exist. (`01` §1 says "`Item.sku`"; there is no
  `Item` model — the item master is `Product`. The prose predates the rename; the
  measurement it describes is unaffected.)
- **Only `lookup_material` resolves against this catalogue.** Of 6,717 records
  just 44 carry `catalog_number_full`, so a run that tries item names or ordering
  numbers through the `lookup_catalog` path measures ≈0 and concludes something
  false.
- **Row confidence is not a gate.** An item name carries no grade column, so it
  scores 0.00 by construction —
  `13-confidence-and-input-completeness.md` establishes that this is the engine
  abstaining on a missing field, not failing on the name. Gating on it would
  discard every geometry decode.

**No cost and no margin appear anywhere in the output**, by construction. Value
weighting is `SalesTxn.line_revenue`, the net selling figure; nothing reads
`CostRecord`. A test asserts the serialized report contains no cost-shaped field,
over the whole blob rather than field by field, because the leak this guards
against is a field nobody thought to name.

---

## 3. Scope: invoice lines only, and that is a limitation

**There is no estimate line-level history anywhere in this system to measure
instead.** Estimates are write-only: `routers/quote.create_estimate` pushes one
to Zoho, and there is no estimates table, no `_sync_estimates` in
`ingestion/sync.py`, and no estimate pull in any `ingestion/erp/` connector.

This is recorded as scope rather than passed over in silence, because it makes
the bound tighter than it first appears. Quoted-but-not-won lines are the closest
available proxy for demand the catalogue failed to reach, and they are not
recorded at all. **An invoice line is a *won* line**, so this denominator is
narrower even than "everything we quoted".

---

## 4. Why the number is UNKNOWN here, and what a live run costs

Nothing in this checkout can produce an admissible figure:

| | State |
|---|---|
| `backend/data/platform.db` | Demo seed: 1 organization, 4 fabricated products, none with a SKU, 26 `SalesTxn` rows |
| `.env` | Absent |
| `settings.ZOHO_SOURCE` | Defaults to `fixture` — 3 invoices, 2 items |

A percentage computed over either would be a fabricated number wearing a
percentage sign. So the script is validated instead against a synthetic database
built so that every axis has a different right answer
(`backend/tests/test_quoted_line_coverage.py`, 15 tests): identity and geometry
are made to *cross* rather than nest, so the union strictly exceeds both halves,
and the covered products are cheap and frequent while the uncovered ones are dear
and rare, so a line-count numerator reused for the value row shows up as two
equal percentages.

**To run it for real:**

```bash
./scripts/setup_pie_parser.sh          # or export PIE_PARSER_ROOT=<checkout>
python3 scripts/build_catalog.py       # 6,717 products
cd backend && python3 ../scripts/measure_quoted_line_coverage.py --json coverage.json
```

**Budget the sync first.** `ingestion/zoho_client.list_invoices` fetches
per-document detail because line items are not on the list response — **one API
call per invoice**. Against roughly 3,367 invoices for SLS (figure supplied with
the task, not reproduced here) that is 3,367 calls before this script sees a
single row. Scope to one entity, or budget the pull.

Two refusals are built in, and both should be read as results rather than
failures:

- **No lines** prints "empty denominator, not 0% coverage" and no percentage.
- **No catalogue** prints that no figure is admissible. `catalog_available`
  exists precisely to keep "the pack does not cover this item" apart from
  "nobody asked the pack"; both return `None` from `lookup_record`, only the
  first is evidence, and printing 0% would be `CLAUDE.md` §1's absence-read-as-
  evidence with the sign flipped.

---

## 5. What this will and will not settle

**Will:** whether the strategic verdict in `01` §7 was taken against a
denominator that understated the catalogue's reach for the work actually done,
and by how much, per entity and per rupee rather than per master row.

**Will not**, and none of these should be read as favourable:

- **The size of the excluded population.** The lower bound is a bound; nothing
  here estimates the gap between it and the truth.
- **Anything about unquoted demand.** See §3 — it is not recorded.
- **Whether a decode is *correct*.** Full ISO slot fill is the gate `01` §3
  validated, at 1 definite misroute in 2,057. It is a strong gate, not a proof,
  and this reports slot fill rather than accuracy.
- **Comparability of the geometry half to `01` §1's 17.1%.** That figure gates on
  `iso_shape` + `edge_length_mm`; this gates on those **plus** `corner_radius_mm`,
  the stricter §3 gate. This number will therefore read lower than `01`'s for
  reasons that have nothing to do with the denominator, and the two must not be
  differenced.
