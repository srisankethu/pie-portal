# PIE — Investor deck, slide-by-slide content

Status: **built.** This file was written first, approved, and is now kept in sync with
`build_deck.py` — it is the copy of record. Where the build differs from the Phase 1
draft, the difference is noted in place.

Every factual claim below is traceable to this repository or to `pie-parser`. Numbers were
measured on 2026-08-22, not read from READMEs — both READMEs are stale on test counts.
Anything not verifiable is rendered `[TO VALIDATE]` or `[ASSUMPTION]` and will appear on the
slide in amber, in a dashed box.

Word counts are body copy only, headline excluded, per the brief's caps. Every count below is
the real count of the words on that slide — including diagram labels and captions, because
those are words the reader has to read.

---

## Established facts used in this deck

| Fact | Source | Used on |
|---|---|---|
| 6,717 / 6,717 corpus rows classified, zero quarantine | `pie-parser/corpora/kmt_zcnc_2026-07_nomenclature.csv`, README delivery table | 11, 13 |
| 11 families, each at 100% grammar parse rate | `pie-parser/README.md` delivery table | 11, 13 |
| 326 tests (pie-parser) | `pytest --collect-only`, measured | 13 |
| 2,895 tests (pie-portal backend) | `pytest --collect-only`, measured | 13 |
| Engine holds zero manufacturer literals, AST-enforced | `pie-parser/CLAUDE.md` §1 + `scripts/verify.sh` | 9, 11 |
| Byte-identical reruns; `run_id` from input bytes + ruleset checksum | `pie-parser/CLAUDE.md` §1 | 5, 13 |
| Every field carries provenance, confidence, character span | `pie-parser/CLAUDE.md` §1 | 5 |
| Item master census: 15,028 items, complete, no gaps | `docs/concepts/01-application-engineering.md`, 2026-08-09 | 2, 13 |
| Coverage 21.6% union / 23.5% by stock value / 42.5% stocked Kennametal | same | 13 |
| 1.4% of item names carry a recognised grade token; master has no grade field | same | 3 |
| 7 ERP connectors: Zoho, NetSuite, D365 BC, Acumatica, P21, Sage X3, Sage 100 | `docs/connectors.md` | 9, 13 |
| AI never computes a number; grounding gate rejects untraceable figures | `CLAUDE.md` §1, `backend/app/ai/` | 8 |
| `AI_PROVIDER` defaults to `mock` — AI ships off | `backend/app/config.py:398` | 8 |
| Cost/margin absent from salesperson payload, server-side | `CLAUDE.md` §1 | 13 |
| `CNMG 120408-49 - TN2000` = MM# 2001174 | corpus row, verbatim | 7 |
| C → rhombic 80°; N → clearance 0°, negative polarity | `pie-parser/engine/iso.py:21-47` | 7 |

**Founder line is still blank.** Section 2 asks for a one-line founder background and I will not
invent one. It is not load-bearing on any slide given there is no ask, so it appears only in
`hard-questions.md` Q10 as the evidence you need to assemble.

---

## Slide 1 — Cover

**Headline (drawing title):** PIE — PRODUCT INTELLIGENCE ENGINE

**Single idea:** What this is, in one sentence, before any argument starts.

**Body copy (25 words / cap 25):**

> ERP records the transaction. PIE understands the product and decides what should happen.
>
> Deterministic product intelligence for B2B industrial distribution.
>
> `4U PRECISION · HYDERABAD, INDIA · [STAGE]`

**Visual:** Full drafting-sheet treatment. Product name set as the drawing title in the
bottom-right title block, scaled up and moved to optical centre-left. Construction grid at full
extent, hairline border. No other element. The cover is the only slide where the title block is
the composition rather than an annotation.

**Placeholders:** `[STAGE]` — you declined an ask in the deck; this is the one place a stage
label still reads naturally. Say the word and I remove it entirely.

---

## Slide 2 — The B2B distribution problem

**Headline:** A quote begins as a sentence, not a part number

**Single idea:** The request arrives unstructured, and every step after it is a human doing
lookup from memory.

**Body copy (51 words / cap 60):**

> The request arrives unstructured. Everything after it is manual.

Dimension callouts along the timeline:

> 1. RFQ arrives — WhatsApp, email, PDF, photo
> 2. Code decoded by hand
> 3. Item master searched — 15,028 items, name is not identity
> 4. No grade field — expert memory fills it
> 5. Cost and stock in another screen
> 6. Price set from memory or last invoice
>
> `ELAPSED: [TO VALIDATE]`

**Visual:** A horizontal timeline rule from RFQ to QUOTE. Six friction points marked as
dimension callouts below the rule — extension line, arrowhead, mono label — exactly as a
drawing dimensions a feature. The terminal `ELAPSED` callout is amber and dashed, because it is
the number that would make this slide land and you do not have it yet.

**Placeholders:** `[TO VALIDATE]` elapsed time per quote. This is the single highest-value
number to go and measure — see hand-off.

**Note:** "15,028" is your own item master, measured. That specificity is what makes the slide
credible; a generic "thousands of SKUs" would not be.

---

## Slide 3 — Why ERP doesn't solve this

**Headline:** ERP records transactions. It does not understand products.

**Single idea:** The ERP holds the commercial facts and none of the technical ones, so the
technical half of every quote comes out of a person.

**Body copy (54 words / cap 55):**

Left column, `WHAT ERP KNOWS`:

> SKU string · stock on hand · last cost · last selling price · customer · tax code

Right column, `WHAT A QUOTE NEEDS`:

> what the product physically is · whether another product substitutes · whether this price
> holds margin · whether this customer justifies it

Caption beneath:

> Measured on this item master: 1.4% of item names carry a recognisable grade token. Grade is
> the attribute the decision runs on.

**Visual:** Two columns divided by a single vertical hairline. Left column items set in ink;
right column items set in blueprint. Deliberately no icons — the asymmetry is the argument, and
decoration would soften it. The caption sits below both columns, spanning, with a leader line
to the right column.

**Placeholders:** none. Both halves are fact.

**Note:** This is the slide where the measured 1.4% does disproportionate work. It converts
"ERP doesn't understand products" from an opinion into an observation about your own database.

---

## Slide 4 — The insight

**Headline:** none. A mono label `SHEET 04 — THE INSIGHT` in the title block carries it.

**Single idea:** The bottleneck is the translation step, and it is undocumented because it
lives in a person.

**Body copy (28 words / cap 30):**

> The bottleneck is not recording the order. It is turning an unstructured request into a
> technically correct, commercially actionable quote — and that step lives in one person's head.

**Visual:** Near-empty sheet. One sentence set at roughly 34pt across a measured column no wider
than two-thirds of the sheet, positioned on the upper-third construction line. Grid and border
only. No diagram. The whitespace is the design decision — this is the slide that has to slow a
reader down, and anything else on it defeats that.

**Placeholders:** none.

---

## Slide 5 — How PIE works

**Headline:** One pipeline, ten stages, one legend

**Single idea:** The pipeline is fixed and mostly deterministic; here is the convention that
governs every diagram after this one.

**Body copy (40 words / cap 45):**

Flow nodes:

> RFQ · Parse · Classify · Normalize · Exact match · Equivalents · Cost & availability · Margin
> · Recommendation · ERP

Legend:

> Solid = deterministic. Dashed = AI.

Caption:

> Eight of the ten stages are deterministic. Only intake and explanation are dashed. Every
> emitted field carries provenance, confidence, and a character span.

**Visual:** Ten square-cornered boxes on one horizontal run, thin blueprint arrows with solid
triangle heads between them. First and ninth boxes drawn with a dashed stroke; the other eight
solid. The legend sits in its own small ruled box at lower-left, drawn once here and never
repeated — every later slide inherits it.

**Placeholders:** none.

**Note:** Ten boxes across 13.3 inches is tight. If it crowds at render, I will break it to two
rows of five with a return arrow rather than shrink the type below the body size used elsewhere
— consistency of type scale is on the Phase 3 check list.

---

## Slide 6 — The quote desk wedge

**Headline:** Same request in. Same quote out. Six steps removed.

**Single idea:** The wedge is a specific, daily, painful workflow — not a platform vision.

**Body copy (50 words / cap 60):**

Lane A, `TODAY`:

> Read → decode by hand → search master → check stock → look up cost → price from memory → type
> quote

Lane B, `WITH PIE`:

> Paste → resolved lines, ranked alternatives, stock, cost, recommended price, margin flag →
> review → send

Caption:

> The human still approves every line. PIE removes the lookup, not the judgement.
>
> `[TO VALIDATE] minutes saved per line — not yet measured`

**Visual:** Two stacked lanes sharing a common start node (RFQ) at the left and a common end
node (QUOTE) at the right, so the eye reads identical endpoints and different interiors. Lane A
nodes solid ink, seven of them, visibly cramped. Lane B nodes solid blueprint, four of them,
visibly spaced. The contrast is spatial, not colour-coded.

**Placeholders:** `[TO VALIDATE]` minutes saved per line.

**Note:** The caption's second sentence is doing defensive work on purpose. An investor who has
seen four "AI replaces your sales team" decks this quarter relaxes when a founder draws the
line themselves.

---

## Slide 7 — Example workflow

**Headline:** One line, end to end

**Single idea:** Here is the actual mechanism working on an actual product, with nothing
abstracted.

**Body copy (43 words / cap 50):**

Input, mono — a verbatim corpus row:

> `CNMG 120408-49 - TN2000`

Decode chain, nine callouts:

> rhombic 80° · clearance 0°, negative · tolerance class M · fixing type · edge length 12 mm ·
> thickness 4.76 mm · corner radius 0.8 mm · chipbreaker · grade — column, WIDIA legacy T

Resolution:

> Identity MM# 2001174 → Zoho stock · landed cost → Pricing recommended · floor → EXACT

Caption:

> The eight character spans are the engine's own output.

**Visual:** The input code set large in mono across the top. Leader lines drop from each
character group of the code to its decoded meaning below, in the manner of a parts callout on a
drawing — this is the one slide where the blueprint metaphor and the actual subject matter
coincide, and it should be the most visually satisfying sheet in the deck. Beneath the decode,
a short solid flow into identity → Zoho → price.

**Placeholders: none.** The Phase 1 draft reserved `[EXAMPLE — REPLACE WITH LIVE SCREENSHOT]`
here. It is gone, because the slide is now real rather than illustrative.

**What changed once the parser was actually run.** The row was put through
`tools/run_parser.py` against `packs/kennametal_widia` (v0.10.0, checksum `6be04ced55b4a239`)
and the slide uses the emitted record, not a reading of ISO 1832. Two corrections to the draft:

- **The brand is WIDIA, not Kennametal.** The pack covers both; this record resolves to WIDIA.
- **`-49` is a chipbreaker**, per `field_meta.chipbreaker`, span `[12,14]`. The draft
  deliberately declined to guess this; the pack settled it.

The eight code spans drawn on the slide are the engine's own `field_meta` spans — `C[0,1]`,
`N[1,2]`, `M[2,3]`, `G[3,4]`, `12[5,7]`, `04[7,9]`, `08[9,11]`, `49[12,14]`. **Grade is drawn
with a leader and no span rule**, because its provenance is `EXPLICIT_COLUMN` with `span: null`
— it resolves from the Grade column, not from the text. Drawing a span there would have made
the caption beneath it false.

`docs/evidence/after-quote-lines.png` was considered as a live screenshot and rejected: it is
824×4888, a mobile-width capture far too tall for a sheet, and it shows a real customer name.

---

## Slide 8 — Deterministic engine + AI layer

**Headline:** Correctness is deterministic. Ambiguity is where AI earns its place.

**Single idea:** The architecture is a deliberate commercial choice, and it is the reason the
output can be trusted.

**Body copy (67 words / cap 70):**

Top tier, dashed:

> NL RFQ intake · Conversation · Explanation · Exploration · Agent configuration

Bottom tier, solid:

> Parsing · Classification · Attributes · Equivalence · Pricing · Margin · Policy

Watcher strip beneath the solid tier:

> Margin Leakage · Cost Change · Quote Risk · Price Increase · Dead Stock · Equivalent
> Opportunity

Caption:

> The AI never computes a number. Every figure it states must trace to a supplied fact or the
> response is rejected and the deterministic reading is shown. The AI ships off by default —
> the engine underneath is the valuable half.

**Visual:** Two horizontal tiers. Bottom tier: seven solid-stroke boxes on a continuous
baseline. Top tier: five dashed-stroke boxes, drawn slightly narrower and floating above with a
visible gap. The watcher strip is six small solid boxes tucked under the bottom tier, half
height, as evidence rather than as a peer. No arrow crosses from top to bottom except one
dashed line labelled `reads`.

**Placeholders:** none.

**Note:** "The AI ships off by default" is the strongest anti-hype sentence available to you
and it is literally true — `AI_PROVIDER` defaults to `mock`. Investors who are sceptical of AI
positioning are the target audience for this slide, and this is the sentence that converts
them.

---

## Slide 9 — Market

**Headline:** The engine is manufacturer-agnostic by construction

**Single idea:** Horizontal expansion is a property of the architecture, not a roadmap promise
— and here is the arithmetic for what that is worth, with every input exposed.

**Body copy (54 words / cap 55):**

Category ladder:

> Cutting tools → industrial tools → bearings → electrical → MRO → automation → fasteners →
> consumables

Left annotation:

> A new manufacturer is a new data pack. Zero manufacturer literals in the engine — tested, not
> asserted.

TAM/SAM/SOM box, arithmetic shown:

> `TAM = A × B`
> `SAM = TAM × C`
> `SOM = SAM × D`
>
> `A  [ASSUMPTION]  distributors in scope`
> `B  [ASSUMPTION]  annual spend each`
> `C  [ASSUMPTION]  share on a connected ERP`
> `D  [ASSUMPTION]  reachable in three years`

**Visual:** Category ladder as a vertical stack of boxes at left, the first (cutting tools)
solid and filled-stroke-weight heavier, the rest solid but lighter — read as "proven here,
same mechanism onward". The TAM box at right is a ruled table with the formula row above and
four labelled input rows below, every value cell amber and dashed.

**Placeholders:** all four of `A`, `B`, `C`, `D`.

**Note — read this one carefully.** The brief permits a constructed TAM only with every input
labelled, and you have none of the four inputs. So the slide presents the *arithmetic* as the
content and leaves the values open. This is unusual and it is a deliberate trade-off: an
all-amber market slide reads as underprepared to some investors, and as disciplined to others.
The three inputs that are cheap to source properly are `A` (distributor counts from trade-body
registries in your chosen geographies), `C` (published ERP market share), and a defensible `B`
from comparable quoting/CPQ seat pricing. Source those and this becomes the strongest slide in
the deck rather than the weakest. Flagged in the hand-off.

---

## Slide 10 — Competitive landscape

**Headline:** The layer between describing a product and quoting it

**Single idea:** PIE occupies a gap in an existing stack; it displaces nothing, which is also
why it can be sold alongside everything.

**Body copy (49 words / cap 50):**

Layer stack, top to bottom:

> ERP — records the transaction
> PIM — describes the product
> **PIE — understands it and recommends the decision**
> CPQ — configures and issues the quote
> Pricing platforms (Pricefx) — optimise price
> AI copilots — converse about it
> Spreadsheets + memory — the actual incumbent

Label and caption:

> `REPLACES NOTHING`
>
> It supplies the layer none of them owns.

**Visual:** A vertical layer diagram, seven ruled bands. Every band drawn in `rule` grey except
the PIE band, which is drawn in blueprint at full weight and inset slightly so it reads as
seated between the two bands either side of it. The bottom band, spreadsheets and memory, is
drawn dashed in ink — not because it is AI, but because it is the incumbent that does not
formally exist. I will note that exception in the legend so the solid/dashed convention is not
muddied; if it reads ambiguously at render I will use a different device.

**Placeholders:** none.

**Note:** Naming spreadsheets as the real incumbent is the credibility move here. Every
distributor investor knows it is true, and a landscape slide that pretends the competition is
Pricefx gets marked down.

---

## Slide 11 — Moat

**Headline:** The asset is the decoded nomenclature, and it only accumulates

**Single idea:** The defensibility is one specific, built artefact — not a generic claim about
data.

**Body copy (59 words / cap 60):**

Accumulation chain:

> Manufacturer naming grammars → normalisation rules → sourced pairwise equivalence claims →
> distributor commercial data → quote outcomes → pricing policy → workflow position

Body:

> Each manufacturer's code system is reverse-engineered once into a versioned pack: 11
> families, 6,717 rows, every family at 100%. That structure is published nowhere. This is not
> a data-scale claim — it is a specific artefact that took specification work to build.

**Visual:** A staircase — seven boxes ascending left to right, each drawn overlapping the
previous one's baseline so the stack visibly accretes rather than flows. Solid stroke
throughout. A dimension line runs along the bottom labelled `compounds with every pack and
every quote`.

**Placeholders:** none.

**Deliberate deviation from the brief — please confirm.** §6 specifies "equivalence graph" as a
stage in this chain. I have written **"sourced pairwise equivalence claims"** instead, because
an equivalence graph is precisely the thing `pie-parser/CLAUDE.md` §1 forbids: technical
equivalence is not transitive, and a graph is a transitive closure, which puts a 0.4 mm corner
radius and a 0.8 mm one in the same class via 0.6. Claiming a graph on a moat slide would
describe an architecture you deliberately did not build, and a technical diligence call would
catch it. The honest phrasing is also the stronger one — knowing why the graph is wrong is a
signal of depth. Say the word if you want the brief's original wording restored.

---

## Slide 12 — Business model + GTM

**Headline:** Land on the quote desk. Expand into the commercial decision.

**Single idea:** A specific entry workflow with a specific expansion path — priced, with the
pricing honestly marked as untested.

**Body copy (59 words / cap 65):**

Left, `PRICING MODEL`:

> Platform subscription per organization `[ASSUMPTION]`
> Quote-desk seats `[ASSUMPTION]`
> SKU intelligence volume `[ASSUMPTION]`
> Premium modules `[ASSUMPTION]`

Right, `LAND AND EXPAND`:

> Quote Desk → Product Intelligence → Pricing → Margin Management → Agents → Commercial
> Intelligence

Caption:

> The quote desk is the wedge because it is where a distributor already feels the pain daily
> and where the value is legible in one session. Price points are unvalidated.

**Visual:** Split sheet, vertical hairline at centre. Left is a four-row ruled table, each row's
value cell an amber dashed box. Right is a six-rung ladder ascending, first rung (Quote Desk)
solid blueprint at full weight, the remaining five in lighter stroke — proven wedge, projected
expansion, and the weight difference says so without a word.

**Placeholders:** four `[ASSUMPTION]` tags on every pricing line.

---

## Slide 13 — Current Stage

**Headline:** Current Stage

**Single idea:** Exactly what is true today, sorted by how much evidence stands behind it.

**Body copy (53 words / cap 55):**

`BUILT`

> Parser — 11 families, 6,717 rows, 100% · Platform — 2,895 tests · 7 ERP connectors ·
> Role-gated economics

`IN PROGRESS`

> Second manufacturer pack · Outcome Tracker beyond four detectors

`VALIDATED`

> Live on real Zoho data, three entities · Coverage: 21.6% of 15,028 items; 42.5% of stocked
> Kennametal · Determinism byte-verified

`UNVALIDATED`

> Willingness to pay · Time saved per quote · Margin impact · External customer `[TO VALIDATE]`

**Visual:** Four equal columns divided by three vertical hairlines, column headers in mono
caption style. Entries as short ruled rows. The `UNVALIDATED` column is drawn entirely in amber
with a dashed outer boundary, so the eye reads the honest column as a deliberate design element
rather than an omission.

**Placeholders:** the whole `UNVALIDATED` column.

**Notes — two things you should push back on if I have them wrong:**

1. **Titled "Current Stage", not "Traction",** because you confirmed no revenue, no design
   partners, no pipeline. That is the correct call at this stage and investors read it as
   discipline.
2. **Putting the 21.6% coverage number on the slide is my recommendation, and it is arguable.**
   It is an unflattering number: it says the decoded catalogue overlaps your own traded
   catalogue less than a quarter of the time. My reasoning for including it — a sophisticated
   investor will ask "how much of the item master does this actually reach?", and a founder who
   has already measured it, in a complete census rather than a sample, and who leads with the
   number, converts a hostile question into evidence of rigour. Concealing it and being found
   out is fatal; disclosing it costs one uncomfortable minute. The number also improves under
   the right framing: 42.5% on stocked Kennametal-labelled items, and the gap is a
   corpus-completeness problem with a known fix, not an engine limitation. This is covered
   properly in `hard-questions.md` Q3. Tell me if you want it off the slide and handled only in
   the Q&A.

---

## Slide 14 — Investment thesis

**Headline:** The thesis in five answers

**Single idea:** Close on the argument, compressed to the five things that must be true.

**Body copy (47 words / cap 50):**

> `WHY THIS PROBLEM` — The quote is where margin is made, and it runs on memory.
> `WHY NOW` — Distributor ERPs are readable by API; models handle the ambiguous edge.
> `WHY PIE` — Deterministic, auditable, with AI confined to where it helps.
> `WHY PIE WINS` — The pack asset compounds; the engine is manufacturer-agnostic.
> `WHY THIS GETS LARGE` — The same engine crosses categories without new code.

**Visual:** Five ruled rows spanning the sheet, each with the mono question label in the left
third and the answer in blueprint in the right two-thirds. Heavy hairline above the first row
and below the last, so the block reads as a specification table — a summary of the drawing,
which is what a title-block-driven deck should end on.

**Placeholders:** none.

**Note:** No ask and no use-of-funds, per your instruction. No "Thank You" slide — the deck
ends on the argument, per the acceptance checklist.

---

## Consistency check across the deck

- **Five questions answered:** why this problem (2, 3, 4), why now (14), why PIE (5, 7, 8), why
  PIE wins (9, 11), why large (9, 12).
- **Solid/dashed introduced once** (slide 5), used on 8, 9, 10, 12. One documented exception on
  slide 10 to be resolved at render.
- **No replacement claim** for ERP, PIM or CPQ anywhere; slide 10 states the opposite explicitly.
- **AI is a layer, never a foundation:** slide 5 puts it at two of ten stages, slide 8 puts it
  on the upper tier, slide 8's caption says it ships off.
- **Invented numbers: zero.** Every figure traces to the table at the top of this file.

## Open placeholders (6)

| # | Sheet | Placeholder |
|---|---|---|
| 1 | 1 | `[STAGE]` |
| 2 | 2 | Elapsed RFQ→quote time |
| 3 | 6 | Minutes saved per line |
| 4 | 9 | TAM inputs A, B, C, D |
| 5 | 12 | Four pricing assumptions |
| 6 | 13 | Four unvalidated claims |

Sheet 7's placeholder was removed rather than filled — see that sheet's note.

Plus the founder background line, which appears in no slide but is needed for
`hard-questions.md` Q10.

---

## Build

`python3 build_deck.py` regenerates `pie-investor-deck.pptx` from this copy and prints every
sheet's word count against its cap. Editing copy means editing that script and re-running; no
shape is hand-placed, so the layout cannot drift.

Three decisions were taken on the stated defaults when the go-ahead came without them:

1. Sheet 11 keeps **"sourced pairwise equivalence claims"** rather than the brief's
   "equivalence graph" — a graph is a transitive closure, which `pie-parser/CLAUDE.md` §1
   forbids by construction.
2. Sheet 13 **keeps the 21.6% coverage figure** on the sheet rather than holding it for Q&A.
3. Sheet 1 **keeps `[STAGE]`** as an amber placeholder.

Any of the three is a one-line change in `build_deck.py`.

---

## Hand-off — what must be supplied before this deck is sent

Six placeholders render in amber on the sheets. None is a blocker to showing the deck;
all are blockers to it reading as finished.

| # | Sheet | Placeholder | What to supply | Effort |
|---|---|---|---|---|
| 1 | 1 | `[STAGE]` | "Pre-seed" or "Seed", or tell me to delete the cell | minutes |
| 2 | 2 | `ELAPSED: [TO VALIDATE]` | Median hours from RFQ received to quote sent, timed across ~20 real enquiries | a week of logging |
| 3 | 6 | Minutes saved per line | Same measurement, run once with PIE and once without | a week |
| 4 | 9 | TAM inputs `A`,`B`,`C`,`D` | Distributor counts in scope; annual spend each; share on a connected ERP; share reachable in three years | 2–3 days desk research |
| 5 | 12 | Four `[ASSUMPTION]` pricing lines | Real quoted numbers, ideally after ten pricing conversations | weeks |
| 6 | 13 | The `UNVALIDATED` column | Willingness to pay · time saved per quote · margin impact · one external customer | the substance of the next quarter |

**Not a placeholder, but still needed:** the founder background line. It appears on no
sheet, and it is the entire basis of `hard-questions.md` Q8.

### Two things to fix outside the deck

1. **`README.md` contradicts sheet 13.** It says "V1 is complete and demo-ready. Before it
   runs on real customer data, three deployment gates remain." Sheet 13 says PIE runs live
   on real Zoho data across three entities, on your instruction. That README is stale in
   other respects too — it claims 138 backend tests where there are 2,895 — but an investor
   in diligence will read it and find the disagreement. Update it before sending anything.

2. **Sheet 9 is the weakest sheet in the deck, and it is fixable in days.** It presents the
   TAM arithmetic with all four inputs open, because the brief permits a constructed TAM
   only with every input labelled and you have none of them. Three of the four are ordinary
   desk research. Sourced, this becomes one of the strongest sheets rather than the one an
   investor's eye stops on.

### Priorities, if only some of this gets done

The order that most improves the raise, from `hard-questions.md`:

1. Build the **second manufacturer pack** and record the hours. It is the only evidence that
   the moat is an asset rather than a services business, and every horizontal-expansion
   claim in the deck rests on it.
2. **Ten pricing conversations** with unaffiliated distributors, quoting a real number.
3. **One external user**, on any terms.

Placeholders 4, 5 and 6 close themselves as a by-product of those three.
