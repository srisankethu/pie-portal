# Application engineering — what the PIE catalogue can and cannot reach

An assessment of whether the decoded metalcutting catalogue (pie-parser) can be
joined to the transactional item master (Zoho) usefully enough to change what
this business sells — from discounting a part number to advising on an
application.

The short answer is **not at present, and the reason is coverage**. This document
records the measurement so nobody has to re-derive it, and separates the pieces
worth building anyway from the pieces the numbers do not support.

Measured 2026-08-09 against the SLS Engineers item master (a complete census,
15,028 items, 76 pages, no gaps) and a catalogue rebuilt from the pinned
pie-parser corpus (`kmt_zcnc_2026-07_nomenclature.csv` → 6,717 rows, zero
quarantined).

---

## 1. The coverage number

Two populations, and they are not the same population.

| | Definition | n | Share |
|---|---|---|---|
| **Identity-linked** | `Item.sku` → PIE `record_id`, exact | 1,420 | **9.4%** |
| **Geometry-decodable** | name decodes to `iso_shape` + `edge_length_mm` | 2,577 | **17.1%** |
| Overlap | both | 744 | 5.0% |
| **Union** | either | 3,253 | **21.6%** |
| Neither | — | 11,775 | 78.4% |

Identity is exact only, via `identity/store.AuthoritativeIndex.lookup_material`.
`Item.sku` holds the Kennametal MM#; `Item.name` holds the product code.

Coverage rises as the population narrows to the live, in-principal range:

| Population | n | Identity | Geometry | Union |
|---|---|---|---|---|
| All items | 15,028 | 9.4% | 17.1% | 21.6% |
| Stocked (`stock_on_hand` > 0) | 2,720 | 18.1% | 24.0% | 30.8% |
| Stocked **and** Kennametal-labelled | 1,721 | 28.5% | 31.8% | **42.5%** |

### Why it is this low

**The corpus is not a superset of what is traded.** Of 5,254 MM#-shaped
Kennametal SKUs in the master, only 27% appear in the corpus. Conversely only
1,420 of 6,717 catalogue records (21.1%) are referenced by the master at all.
The misses are whole families the corpus omits — the `RS1TA…` / `RS2TA…`
adaptors and collets, for instance.

The catalogue and the business are two weakly-overlapping sets. Any plan that
assumes the parser's catalogue describes what this company sells will mis-scope.

**The pack is one manufacturer, and the master is not.** Kennametal accounts for
7,150 of 15,028 items; the rest are EMUGE FRANKEN (702), Renishaw (294), NOGA
(213), Birla (182), Samtec, Tectyl, Contax and others, plus 6,146 rows with no
manufacturer recorded at all.

**Grade is only available through identity.** Every catalogue record carries a
grade (6,714 of 6,717). The item master has no grade field, and only 1.4% of
item names contain a token in the pack's `grade_registry.csv`. Grade is the
attribute application engineering actually runs on, and it arrives with the link
or not at all.

---

## 2. Weighting by value does not rescue it

Coverage by SKU count could understate the business if the linked items were the
valuable ones. Weighted by stock value at selling price (a complete census, not a
sample; ₹32.7M total):

| | SKUs | Stock value | Value per SKU |
|---|---|---|---|
| Identity-linked | 9.4% | 15.5% | 1.64× average |
| Geometry-decodable | 17.1% | 19.6% | — |
| Union | 21.6% | **23.5%** | 1.08× average |

Linked SKUs are individually worth more, but the union is close to
proportional. **Weighted coverage is 23.5% against 21.6% unweighted.** There is
no hidden concentration of the business inside the linked set.

### Turnover: UNKNOWN, deliberately

Stock value understates fast-moving inserts, so invoice frequency was sampled for
12 linked against 12 unlinked stocked SKUs. The point estimate is 3.2×, but the
bootstrap 95% CI is [0.71, 19.2] and a permutation test gives p = 0.099.

**That does not support the claim and it is not recorded as a finding.** What
would settle it: a Sales-by-Item report export, or per-invoice line extraction
across the 3,367-invoice history. Until then this is unmeasured, not favourable.

---

## 3. The refusal that turned out to be load-bearing

Running the engine over all 15,028 item names to harvest attributes looks
attractive and is wrong.

- Every row scores `row_confidence` **0.00**, carrying `GRADE_MISSING` and
  `MANUFACTURER_UNKNOWN`. The engine is declining to vouch for any of it.
- 30.3% route to a named `product_family`, but **11.6% of routed rows carry a
  named non-Kennametal manufacturer**. An EMUGE screwdriver and an `M3X11` screw
  both route to `turning_insert`.

Treating `product_family` as a fact is precisely the failure `CLAUDE.md` §1
names: absence of evidence read as a pass.

The contamination is confined to *partial* decodes. Restricted to full ISO slot
fill (shape + edge + radius, 2,057 rows), definite misroutes fall to **1
(0.05%)** — and that one is a correct geometry read of a YG1 part.

**ISO slot fill validates itself; a family route does not.** That is the gate.

---

## 4. Entity scope

- **SLS Engineers** — measured above.
- **4U Precision** — a YG1 house (191 of the first 200 items). The
  Kennametal/WIDIA pack does not apply; resolution is 0%.
- **UPS** — no Zoho Books connection exists, so it cannot be measured. Zoho
  Analytics has no SLS workspace either, only 4U's.

---

## 5. What is worth building anyway

Ordered. The first three are cheap and were blocked only by an assumption nobody
had measured. **Items 1 and 2 are built** — they shipped with this document.

1. **`pie_record_id` on `Product`** (`domain/models.py`) — *done*. Nullable
   `pie_record_id` + `pie_link_method` + `pie_catalog_version`, written by
   `ingestion/sync._link_catalog` from an exact SKU match only. Link, never
   merge, per `identity/`. NULL means unlinked and must never be read as an
   assumption. 1,420 rows on the live master.

   The link is *derived*, so it is recomputed every sync and a lost match clears
   it — a write-only link would keep asserting a superseded `record_id` under a
   stamp claiming otherwise. The one exception is a catalogue that is absent
   entirely: that leaves existing links untouched, because "the pack does not
   cover this item" and "nobody asked the pack" are different facts and only the
   first is evidence.

2. **Pass the decode through the EXACT path in `pie_service._map`.** `Candidate`
   already carries `attributes`, but only via `_candidates_from_suggestions`.
   Branch (1) — AUTHORITATIVE identity, the path with 0.97 median confidence —
   dropped it while the lower-confidence suggestion paths kept it, so a
   salesperson got more about a guess than about a certainty.

   The loss is not in `_map` alone. pie-parser's `identity/model.py`
   `IdentityMatch` holds the whole catalogue row in `.record`, but its
   `to_dict()` projects it to four fields, so geometry never crossed the
   boundary at all. Rather than change the pinned submodule, the portal now
   re-reads the row from the same `AuthoritativeIndex` item 1 loads anyway, and
   projects it through `pie_service.ATTRIBUTE_FIELDS` — deliberately identical
   to the tuple `equivalence/query.py` uses, so a product is described the same
   way however it was found.

3. **Master rationalisation.** The measurement is the product: 6,146 items with
   no manufacturer, 73% of Kennametal MM#s absent from the corpus, an HSN code
   sitting in a 4U SKU field. Feeds the existing hygiene watch.

4. **Price-per-edge as a quoting unit** (`commercial/`, deterministic, never AI).
   Shape gives corner count, `insert_polarity` doubles it for negative inserts;
   both present on 94.3% of decodable items. This is the reachable part of
   selling value rather than discount — it reframes price without touching cost.
   Price-per-component stays refused: parts-per-edge is customer-reported and
   genuinely underivable.

5. **Geometry-class peer benchmarking.** `commercial/compute.py` builds the peer
   index as `by_product[product_id][customer_id]`; `compute_benchmark` is already
   agnostic to how the group formed, so this is a caller-side change. The classes
   are dense: at (family, shape, edge, radius) there are 319 classes and 95.2% of
   decodable items sit in a class with ≥2 members, the largest holding 159. Peers
   genuinely appear where there is silence today.

   **A class median mixes grades and therefore mixes cost.** Unconstrained, it
   manufactures a false comparison. It needs a same-grade constraint or
   per-edge normalisation, a class definition versioned in `CommercialThresholds`
   because it is policy, and `ItemBenchmark` recording that a result is a class
   result so it is never read as like-for-like.

6. **Substitution via `equivalence/` and `GradeCrossref`.** Highest ceiling,
   hardest gate — it needs grade, so it lives entirely inside the 9.4%. Defer.

`equivalence/catalog.py:ZohoCatalogSource` already reads `grade`, `iso_shape`,
`product_family`, `corner_radius_mm` and `cutting_dia_mm` from a Zoho row. **None
of those fields exist on a real one** — only the identity and label fields do, so
it contributes candidates with null geometry that score nothing. The seam was
built for a decorated master; item 1 supplies the pointer that can decorate it.

---

## 6. What sounds advanced and is wrong here

- **Decoding the whole master and trusting the family route.** Measured above:
  0.00 confidence throughout, 11.6% misroutes. Gate on ISO slot fill.
- **Fuzzy or embedding matching to lift the 9.4%.** `normalize_identifier`
  deliberately does not strip separators, because separators are meaningful in a
  catalogue number. A loose-normalisation tier bought 109 rows (0.7%) and is
  already at the edge of safe. A fuzzy link puts a wrong MM# on a customer quote.
- **Treating the catalogue as the master's superset**, and rationalising the
  master against it. It would delete real products.
- **Cross-entity peering.** 4U is a YG1 house — no pack overlap. And peering
  prices between commonly-owned legal entities measures transfer pricing, not the
  market.
- **Shipping class benchmarks under the existing `thresholds_version`.** A class
  definition is policy and needs its own hash, or a row cannot say which class
  rule judged it.
- **Inferring manufacturer or grade from the item name** to close the 6,146
  blanks. The same failure the codebase already refuses for
  `incentive_eligibility`.

---

## 7. Verdict

The strategic claim — that this changes what the company sells — is **not
reachable at 21.6% union coverage on one of three entities**, with the second
structurally out of pack scope and the third unconnected. Value weighting was the
one measurement that could have overturned that, and it moved coverage by under
two points.

Items 1–3 are worth doing on their own merits and are days of work. Item 4 is the
reachable piece of the value-selling thesis, on roughly a sixth of the master.
The transformation itself is not fundable ahead of the cash and margin work.

### Reproducing this

Rebuild the catalogue from the pinned submodule (`scripts/build_catalog.py`),
pull the item master in full, and join on `sku` → `record_id` through
`identity.store.AuthoritativeIndex` — not a hand-rolled matcher, so the exactness
rules stay in one place. Geometry figures come from running `ParserPipeline` over
item *names* and are only admissible where the ISO slots actually filled.
