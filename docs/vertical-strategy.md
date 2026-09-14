# Vertical strategy — who buys this, and which pages we build

**Phase 1 research. No code was written for this.** Date: 2026-09-14.

This document answers one question: which trades inside B2B distribution should
get a dedicated page on the marketing site, and which should be excluded on
purpose. The excludes are the point. A page for a vertical whose core problem
we do not solve costs more than the traffic it wins, because the first
qualified visitor it brings finds that out on the call.

Two constraints run through every section:

- **A page may only claim what the product does today.** Same rule as
  `frontend/src/landing/` — `content.ts` hides a section rather than fill it,
  `proof.ts` refuses a partial case study, and `packs/nomenclature/.../README.md`
  in pie-parser writes down what is still mixed. A vertical page is a narrower,
  more checkable claim than the landing page, exactly as the `/erp/` pages are,
  and it gets held to the same standard.
- **Capability fit is assessed against code, not against the pitch.** Section 5
  is grounded in specific modules and named constants, and where the answer is
  "we do not do that", it says so.

---

## 0. Research honesty — read this before you read a number

**I could search the web in this session. I could not read a single source
page.**

- `WebSearch` worked. It returns a synthesised summary plus a list of URLs.
- `WebFetch` and `curl` were blocked by the environment's egress proxy for
  **every** external domain attempted — `epicor.com`, `epacube.com`,
  `top10erp.org`, `mdm.com`, `en.wikipedia.org`. All returned
  `EGRESS_BLOCKED` / `CONNECT tunnel failed, response 403`.

So every external claim below is **relayed by a search tool from pages I did
not open.** That is a real difference and it is why the tables carry an
evidence column with three values rather than two:

| Tag | Means |
|---|---|
| **CODE** | Verified in this repository or in pie-parser. First-party, checkable by anyone with the checkout. The strongest evidence here. |
| **SEARCH** | Surfaced by web search in this session, from a page I could not open. Directionally reliable, individually unverified. Treat a specific figure as an unread quotation. |
| **INFERRED** | My reasoning from the two above, or from how distribution generally works. No source. |

**What is deliberately absent:** no market sizes, no growth rates, no TAM, no
analyst figures, no vendor customer counts stated as fact, and no citation to a
page I did not read. Where I found a striking number I have kept it and marked
it SEARCH with the hedge attached, rather than laundering it into a flat
assertion.

Two SEARCH claims are load-bearing enough that they should be re-verified by a
human before they inform spend:

1. *"41% of the top 50 largest distributors rely on Epicor Prophet 21"* — a
   strong claim from a vendor-adjacent page. It supports the ranking weight in
   §6. If it is wrong, the weight is wrong.
2. *"SPA dollars for an electrical distributor are often larger than net income
   dollars."* — this is the single fact that moves electrical and plumbing to
   the defer list. If it is materially overstated, both should be re-scored.

Sources surfaced (**not opened**): erpresearch.com, estesgrp.com, top10erp.org,
epicor.com, epacube.com, mdm.com, zilliant.com, vendavo.com, pricefx.com,
selecthub.com, sumble.com, enable.com, deloitte.com, sparxiq.com, ogl.co.uk,
getboltwise.com, classccomponents.com, gurufocus.com, csimarket.com,
worldlocity.com, appsruntheworld.com, p21ww.org, gawda.org.

---

## 1. Segment landscape

Catalog complexity below means *how hard is the part number*, not how many SKUs.
A million-SKU book of clean manufacturer catalogue numbers is a simpler parsing
problem than a fifty-thousand-SKU book where every customer writes the same
insert three ways.

| Vertical | How quoting actually works | Catalog complexity | Typical ERP | Price: negotiated per line, or read off a book? |
|---|---|---|---|---|
| **Industrial / MRO** | Mixed. National accounts and contract customers priced off agreements; spot, breakdown and non-contract business quoted line by line with real rep discretion. Discounting happens at the edge, and "last price paid" becomes the next quote's anchor. | High — multi-supplier, heterogeneous, no single nomenclature | P21, Acumatica, BC, NetSuite | **Both.** Contract for the top of the book, negotiated below it. The negotiated tail is where the leakage is. |
| **Cutting tools / metalworking** | Per line, per enquiry. Heavy substitution and cross-brand equivalence — "what have you got like a CNMG 120408 in a P25 grade". Application knowledge sits with the rep. | Very high **and decodable** — ISO 1832 designations plus per-manufacturer grade systems | P21 (under "industrial"), BC, Acumatica, Zoho Books at the smaller end | **Negotiated per line.** Substitution is a pricing event, not just a fulfilment one. |
| **Electrical** | Job and project quotes, priced against manufacturer special pricing agreements (SPAs). Rebate and claim-back economics dominate. | Moderate — industry data standards mean catalogue numbers are relatively clean | P21 (core vertical), Eclipse, Trade Service feeds | **Book, then SPA.** The book price is a starting point; the SPA is the real deal, and the real cost. |
| **Plumbing / PVF** | Project bids plus counter business. Commodity pipe moves on daily cost; engineered and valve product carries spread. | Moderate; PVF sizing/schedule notation is structured | P21 (core vertical), Eclipse, DDI | **Both**, and the commodity half moves faster than any price file. |
| **Fasteners** | Per-line RFQ, often against a print. Constant cross-referencing between distributor part numbers, MPNs, obsolete SKUs and competitor codes. Per-print specials sourced by hand. | **Highest** — and highly decodable in principle (thread, pitch, length, grade, drive, head, finish) | P21 (named core vertical: "Industrial & Fasteners") | **Negotiated per line**, with blanket/VMI agreements over the repeat lines. |
| **Bearings / power transmission** | Interchange is the job. A customer names one manufacturer's number and expects an equivalent from whatever you stock. Tiered and negotiated customer pricing. | Very high **and decodable** — bearing designations are rigidly structured | P21, Acumatica, BC | **Negotiated**, over a tiered base. |
| **Safety supply** | National accounts and contract programmes, vending and site agreements. | Low–moderate; manufacturer catalogue numbers, clean | P21, NetSuite, BC | **Book / contract**, mostly. Discretion is thin. |
| **JanSan** | Recurring consumable replenishment on contract price lists; tenders for large facilities. | Low | P21, Acumatica, BC, jansan-specific systems | **Book / contract.** Repeat business at agreed prices. |
| **Building products / LBM** | Takeoff-and-bid, commodity lumber repriced constantly, hard negotiation per job. | Low as a *parsing* problem — dimensional lumber and SKUs, not codes | Epicor BisTrack, DMSi Agility, other LBM-specific | **Negotiated**, aggressively, but on commodities not codes. |
| **Packaging** | Quote per specification for converted product; stock distribution alongside. | Low as codes; high as *specs* (board grade, flute, dimensions) | P21 ("Paper & Packaging"), NetSuite, BC | **Negotiated per job**, especially on custom. |
| **Welding & gas** | Hardgoods quoted like MRO; gas and cylinder rental on contract with rental streams. | Moderate on hardgoods; low on gas | P21 ("Welding"), gas-specific systems | **Both** — split business, split model. |
| **Automation & controls** | Project quotes with engineering content; configuration and BOM work before a price exists. | High as *configuration*, not as part numbers | P21, BC, NetSuite | **Negotiated per project.** |
| **Lab & medical supply** | GPO and IDN contracts set price; tenders for the rest. | Moderate | P21 ("Medical Supply"), NetSuite | **Contract.** Discretion largely removed by the GPO. |
| **Food service** | Weekly price files, contract cost-plus, extremely thin margins. | Low | Food-service-specific (e.g. Entree, iNECTA) | **Book / cost-plus formula.** |
| **Pharma** | WAC, contract and regulated pricing; DSCSA traceability obligations. | Low (NDC is a clean identifier) | Pharma-specific wholesale systems | **Contract / regulated.** Essentially no discretion. |

*Quoting mechanics and ERP mapping: SEARCH for industrial/MRO, cutting tools,
electrical, plumbing/PVF, fasteners, bearings/PT and the P21 vertical list.
INFERRED for welding & gas, automation & controls, food service, pharma, and
for the packaging and JanSan quoting detail.*

The P21 vertical list is the most consistent finding across searches: industrial
and fasteners, electrical, plumbing and HVAC/R, medical supply and
janitorial/sanitation, fluid power, welding and power automation, PVF, safety,
building materials, tile, consumer goods, paper and packaging. **SEARCH.**

---

## 2. Fit scoring

Scores are 1–5. Higher is better *for us*, so "competitive density" is scored
inverted: 5 means the field is open, 1 means Zilliant, Vendavo, PROS, Pricefx
and epaCUBE are all already there.

Each cell carries its evidence tag. Where a row's tags are mostly INFERRED, the
total is an opinion with a number attached and should be read that way.

| Vertical | Per-line discretion | ERP overlap (our 7) | Part-number messiness | Margin pressure / GM band | Deal size & ability to pay | Competitive density (inverted) | Total |
|---|---|---|---|---|---|---|---|
| **Cutting tools / metalworking** | 5 SEARCH | 4 SEARCH | **5 CODE** | 4 SEARCH | 3 INFERRED | 4 INFERRED | **25** |
| **Industrial / MRO** | 4 SEARCH | **5 SEARCH** | 4 SEARCH | 4 SEARCH | 4 INFERRED | 2 SEARCH | **23** |
| **Fasteners** | 5 SEARCH | 5 SEARCH | 5 SEARCH | 4 INFERRED | 3 INFERRED | 3 SEARCH | **25** |
| **Bearings / power transmission** | 4 SEARCH | 4 INFERRED | 5 SEARCH | 3 INFERRED | 3 INFERRED | 3 INFERRED | **22** |
| **Fluid power / hose & fittings** | 4 INFERRED | 4 SEARCH | 4 INFERRED | 3 INFERRED | 3 INFERRED | 4 INFERRED | **22** |
| **Electrical** | 4 SEARCH | 5 SEARCH | 2 SEARCH | 4 SEARCH | 4 INFERRED | 1 SEARCH | **20** |
| **Plumbing / PVF** | 4 SEARCH | 5 SEARCH | 2 INFERRED | 4 SEARCH | 4 INFERRED | 2 SEARCH | **21** |
| **Welding & gas** | 3 INFERRED | 4 SEARCH | 3 INFERRED | 3 INFERRED | 3 INFERRED | 4 INFERRED | **20** |
| **Packaging** | 4 INFERRED | 3 SEARCH | 2 INFERRED | 3 INFERRED | 3 INFERRED | 4 INFERRED | **19** |
| **Automation & controls** | 4 INFERRED | 3 INFERRED | 2 INFERRED | 3 INFERRED | 4 INFERRED | 3 INFERRED | **19** |
| **Safety supply** | 2 INFERRED | 4 INFERRED | 2 INFERRED | 3 SEARCH | 3 INFERRED | 4 INFERRED | **18** |
| **Building products / LBM** | 4 INFERRED | 1 INFERRED | 1 INFERRED | 4 INFERRED | 4 INFERRED | 3 INFERRED | **17** |
| **JanSan** | 2 SEARCH | 4 INFERRED | 1 INFERRED | 3 SEARCH | 3 INFERRED | 4 INFERRED | **17** |
| **Lab & medical supply** | 1 SEARCH | 4 SEARCH | 2 INFERRED | 3 INFERRED | 4 INFERRED | 3 INFERRED | **17** |
| **Food service** | 1 INFERRED | 1 INFERRED | 1 INFERRED | 5 INFERRED | 3 INFERRED | 4 INFERRED | **15** |
| **Pharma** | 1 INFERRED | 1 INFERRED | 1 INFERRED | 5 INFERRED | 5 INFERRED | 3 INFERRED | **16** |

**The total is not the decision, and §5 is why.** Fasteners ties for the top
score and is still on the defer list, because the thing that earns it a 5 on
messiness is the thing we cannot do. A score measures the size of the prize; §5
measures whether we can collect it.

### Notes on specific scores

**Margin pressure / GM band.** The only concrete figure I can attach to a name
is MSC Industrial at roughly 41% gross margin, described as near a ten-year
median around 42% and compressing (SEARCH — a stock-data aggregator, unopened).
MSC is a large metalworking-heavy MRO distributor and is not a benchmark for a
mid-market book. JanSan was described at 18–28% gross (SEARCH). Beyond those two
I have no per-vertical GM band I am willing to print. The general finding — that
distribution GM runs from high single digits to the low 30s depending on mix
(SEARCH) — is too wide to score against, so the column is scored on *pressure*
as reported, not on a band.

**Margin leakage.** One SEARCH finding is worth repeating because it describes
our core loop's target precisely: distributors are described as leaking roughly
2–11.7% margin on average, driven by decentralised discounting, with 3x price
variance for identical products, and a specific ratchet — a rep discounts under
pressure, the discount is recorded as last price paid, and the system defaults
to it next time. That mechanism is vertical-agnostic wherever discretion exists,
which is what makes Industrial/MRO scoreable at all.

**Competitive density.** Zilliant, Vendavo, PROS and Pricefx all name
manufacturing and distribution; Zilliant publishes MRO/industrial-specific
material; epaCUBE describes itself as serving HVAC, electrical, plumbing,
industrial and construction distributors for 25 years (all SEARCH). The
inversion is highest for cutting tools, fluid power and welding because none of
those vendors appears to lead with them, and lowest for electrical, where
epaCUBE and Zilliant both sit. A separate finding worth tracking: at least one
AI-quoting entrant is targeting fasteners specifically (SEARCH).

**Ability to pay.** All INFERRED. I have no pricing data for competitors and the
product itself states no price on any page by design (`docs/marketing-placeholders.md`
§1). Scored on deal size as a proxy.

---

## 3. Personas and pain — shortlist only

Shortlist = the three that reach §6's page list. Language below is the trade's,
not ours.

### Industrial / MRO distribution

- **Economic buyer:** owner or President at a single-site to five-branch
  distributor; VP Sales or CFO above about $50M.
- **Champion:** the person who owns pricing — often a Pricing Manager where one
  exists, otherwise the sales manager who signs off discounts, otherwise the
  owner doing it in their head.
- **Blocker:** the top-billing outside rep who has priced their own accounts for
  twenty years and reads a floor as a leash. Secondary blocker: the IT/ERP owner
  who has survived one P21 upgrade and will not have anything write to it.

**The three pains we genuinely relieve, in their words:**

1. *"I find out we sold it under cost when I close the month."* Margin is a
   post-hoc report, so the correction always arrives after the customer has the
   price.
2. *"Every rep quotes the same part differently, and last price paid keeps
   ratcheting down."* Nobody set the price that is now the default.
3. *"I didn't know that account was slipping until they'd already gone."*
   Decline is visible in the data months before it is visible in the room.

**Workflow moment we intervene at:** the instant a price is typed on a quote
line, before the quote is sent — not in a monthly review, not in a dashboard.

**What they do today:** an ERP margin report read weekly or monthly; a
spreadsheet of "our prices for this customer" on somebody's desktop; the sales
manager's memory; in some houses an ERP-native price matrix that nobody has
re-tuned since go-live; at the larger end, epaCUBE or Zilliant. A meaningful
share do nothing at all and rely on the rep.

### Cutting tools / metalworking distribution

- **Economic buyer:** owner / managing director. Typically an
  application-driven business where the owner still knows the catalogue.
- **Champion:** the internal sales or applications engineer who turns enquiries
  into quotes and does the cross-referencing by hand.
- **Blocker:** the same applications engineer, if the product reads as
  automation that replaces their judgement rather than as a tool that shows its
  working. This one is a positioning risk, not a feature gap.

**The three pains:**

1. *"They ask for a competitor's insert and we spend twenty minutes finding what
   we've got that's equivalent."* Cross-reference is manual, per line, and the
   quote waits.
2. *"The grade substitution was right and the price was wrong."* The technical
   decision and the commercial one are made by the same person at the same
   moment, and only one of them has a check on it.
3. *"Nobody can tell me why we quoted that price last March."* The reasoning
   went home with the person who made it.

**Workflow moment:** the enquiry-to-quote pass — pasting a customer's messy
enquiry text and having each line resolve against the book — and then the price
on each resolved line.

**What they do today:** manufacturer cross-reference PDFs and interchange
tables; the applications engineer's memory; a spreadsheet of past quotes;
re-keying the enquiry into the ERP by hand.

### Fluid power / hose & fittings — *shortlisted conditionally, see §6*

- **Economic buyer:** owner or GM.
- **Champion:** counter and inside sales manager.
- **Blocker:** the branch manager whose branch has its own pricing habits.

**The three pains:** the same three as Industrial/MRO, one register down —
below-cost lines found at month end, inconsistent pricing between branches, and
quiet account decline. I am not claiming a distinct fluid-power pain, because I
did not find evidence of one and inventing one would be exactly the failure
this document is written against.

**Workflow moment:** same as Industrial/MRO — price entry on the quote line.

**What they do today:** INFERRED — ERP reports and rep judgement.

---

## 4. Reality check — what we actually serve, per shortlisted vertical

The four capability areas are the landing page's own
(`frontend/src/landing/Landing.tsx`, section `#outcomes`): **Control** (every
quote line against your floor, breaches held for sign-off), **Speed** (RFQ text
to resolved, priced lines), **Retention** (signals on accounts going quiet),
**Evidence** (the value ledger).

Before the per-vertical table, four capability facts verified in code. These
govern everything:

1. **Seven ERP connectors, one uncosted.** `backend/app/ingestion/erp/`:
   prophet21, netsuite, acumatica, dynamics365, sagex3, sage100, zoho. Sage 100
   declares no `bills` read, so it has no cost, therefore no margin, no floor and
   no drift — `ErpPageData.costed` is `false` for it and `erp.test.ts` refuses a
   floor claim on that page. **CODE.**
2. **No ERP quote history, on any connector.** `READ_STAGES` in
   `ingestion/erp/base.py` includes `"quotes"` and **no connector declares it**.
   A win rate has no denominator until the customer quotes inside PIE. **CODE.**
3. **There is no rebate, SPA, price-book or contract-price concept anywhere in
   the backend.** A recursive case-insensitive grep for `rebate`, `SPA`,
   `special.price`, `price_book`, `pricebook`, `contract_price` across
   `backend/app` returns nothing but false positives on the word "span". Cost is
   whatever the AP invoice line said. **CODE.** This is the fact that decides
   electrical and plumbing.
4. **Attribute-level product understanding is metalworking-only, and extending
   it is an engine change, not a pack change.** Two named constants:
   - `backend/app/decoding/schema.py` → `CORE_SLOTS`, 40 slots, every one of them
     cutting-tool metalworking: `grade`, `chipbreaker`, `iso_shape`,
     `insert_polarity`, `flute_count`, `point_angle_deg`, `corner_radius_mm`,
     `wiper`, `ball_nose`, `ic_size_mm`. A decoder *can* keep a fact the
     vocabulary has no name for, as an `ext:` field — and the module's own
     docstring states that an `ext:` field is **"never compared"**.
   - `pie-parser/equivalence/distance.py` → `HARD_GATE_FIELDS = (product_family,
     iso_shape, insert_polarity)`, `DIMENSIONAL_FIELDS = (cutting_dia_mm,
     edge_length_mm, shank_dia_mm, loc_mm, oal_mm, thickness_mm,
     corner_radius_mm)`, `SOFT_SIGNAL_FIELDS` including `chipbreaker`, `coating`,
     `flute_count`.

   Both are Python tuples in code, not pack data. So a thread pitch, a bolt
   grade class, a bearing bore or a hose dash size has nowhere to land that
   counts, and giving it one means editing pie-parser's engine. **CODE.**

   What *is* vertical-agnostic: exact matching, and `backend/app/retrieval/` —
   a hashed n-gram embedder over description text, deterministic and offline. It
   will happily match `HHCS 1/2-13X2 GR8 ZP` to `HEX CAP SCREW 1/2-13 X 2 GR.8
   ZINC` in the customer's own book. It cannot rank a *substitute*, because
   ranking a substitute is what the gate and dimension fields above do. **CODE.**

   So the honest split is: **finding the line in your own book — every vertical.
   Proposing an equivalent — metalworking only.**

| | Industrial / MRO | Cutting tools / metalworking | Fluid power |
|---|---|---|---|
| **Control** | ✅ Full. Floor per line, breach held for a named approver, append-only record, thresholds versioned. | ✅ Full. | ✅ Full. |
| **Speed** | ⚠️ **Partial.** Enquiry text → lines → exact and nearest-neighbour match against their own catalogue: yes. Attribute-ranked alternatives: **no**, outside metalworking. The page must not claim cross-reference. | ✅ Full, and it is the only vertical where it is. ISO designations and grade systems decode; alternatives rank on decoded dimensions; the engine abstains when nothing discriminates. | ⚠️ **Partial**, same as Industrial/MRO. |
| **Retention** | ✅ Full — `CUSTOMER_DECLINE`, `CUSTOMER_DORMANCY`, `MARGIN_DETERIORATION`, `COST_PASS_THROUGH` plus six customer×item detectors, all over persisted rows. | ✅ Full. | ✅ Full. |
| **Evidence** | ✅ Full, with the standing caveat that a month with no detection reads UNKNOWN rather than zero. | ✅ Full. | ✅ Full. |

**What we do NOT address, in any of the three:**

- **No SPA, rebate or claim-back economics.** Margin is computed on invoiced
  cost. Where real cost is post-rebate, our floor is computed against the wrong
  number.
- **No quote history from the ERP**, so no win/loss rate until they quote here.
- **No stock levels from Prophet 21** in this version, so the stock and GMROI
  screens stay empty on a P21 book (`erp.ts`, P21 gaps).
- **No customer payments from Prophet 21**, so collections and days-to-pay have
  nothing to read on a P21 book.
- **No credit notes from P21**, so a credited line still counts as sold.
- **No salespeople imported**, so everything routes to management until accounts
  are assigned inside PIE.
- **No configuration or BOM building** — which is why automation & controls and
  custom packaging are not on the shortlist.
- **No cross-manufacturer interchange outside metalworking** — which is why
  fasteners and bearings are not on the shortlist, despite scoring at the top.

---

## 5. Recommendation

### Build these pages — three, not five

I was asked for three to five. **Three is the honest number**, and I would
rather hand over three pages every sentence of which survives a first call than
five where two get taken apart. The gate each page had to pass:

1. Real per-line pricing discretion, **and**
2. cost in the ERP that is actually true cost — no SPA/rebate distortion, **and**
3. an ERP we connect to, **and**
4. no dependence on a capability we have not built for the vertical's *headline*
   pain.

Only Industrial/MRO and Cutting tools pass all four on evidence. Fluid power
passes on inference, and is included third with that stated on its face.

| # | Vertical | Why it earns a page |
|---|---|---|
| 1 | **Industrial & MRO distribution** | Widest overlap with the P21 installed base; the leakage mechanism we intervene on is documented for this segment specifically; every claim on the page is true today with Speed scoped honestly. |
| 2 | **Cutting tools & metalworking** | The only vertical where all four capability areas are fully true, and the only one where we can show a decode rather than describe one. Deepest fit, highest conversion per visit, smallest audience. |
| 3 | **Fluid power, hose & fittings** | A named P21 core vertical with, as far as I can find, none of the big pricing vendors leading at it. The core loop applies unchanged. **Conditional** — see below. |

**Page 3 is conditional and should not be built on this document alone.** Its
entire pain section is INFERRED; I found no fluid-power-specific evidence about
how those distributors quote. One conversation with one fluid power distributor
either confirms it or kills it. Build pages 1 and 2 first regardless.

### Build priority, and where the P21/Epicor pull moved it

**Ranked: (1) Industrial & MRO, (2) Cutting tools & metalworking, (3) Fluid
power.**

Weighting toward the P21 and Epicor VAR installed base changed this order, and
it is worth being exact about where.

On capability fit alone, **cutting tools is first** — it is the only page where
all four areas are true, it carries our one genuine technical moat, and its
worked example can show a real decode instead of asserting one.

The installed base does not sit there. P21's own vertical list leads with
industrial and fasteners, electrical, plumbing and HVAC/R, medical supply and
jansan, fluid power, welding, PVF, safety and building materials (SEARCH).
Cutting tools appears only underneath "industrial", and metalworking-specialist
distributors are a modest slice of the P21 base. Meanwhile 41% of the top 50
largest distributors are said to run P21 (SEARCH, flagged in §0), and P21 is the
richest connector we have.

So the installed-base weight pushed **Industrial & MRO above Cutting tools**.
That is the whole of the movement, and I want the uncomfortable half on the
record too: **the P21 base pulls hardest toward three verticals this product
cannot serve correctly today** — electrical, plumbing/PVF and fasteners. Two of
them fail on rebate economics and one on interchange. Following the installed
base any further than first place would mean building pages for exactly those.
It pulled Industrial & MRO to the top and then had to be stopped.

### EXCLUDE / DEFER

**DEFER** — the product would have to change first. Each line names the change.

| Vertical | One-line reason |
|---|---|
| **Fasteners** | Scores joint-top and fails the gate: the headline pain *is* cross-reference, and attribute ranking needs thread, pitch, grade-class, drive, head and finish slots that `CORE_SLOTS` does not have and `equivalence/distance.py` could not gate on. Highest-value defer on this list. |
| **Bearings / power transmission** | Interchange is the entire job and the designations are rigidly structured — the best second nomenclature pack we could build — but bore, OD, width, seal and clearance have nowhere to land that counts today. |
| **Electrical** | True cost is post-SPA and there is no rebate concept in the backend, so the floor would be computed against a cost that is not the cost. Not a content gap; the core loop returns a wrong number. |
| **Plumbing / PVF** | Same rebate problem as electrical, plus commodity pipe that reprices faster than any synced cost. |
| **Welding & gas** | The hardgoods half is Industrial/MRO and is already served by page 1; the gas and cylinder-rental half is a recurring-revenue model we model nothing of. No separate page until the split is worth its own argument. |
| **Packaging** | Converted product is quoted per specification, not per catalogue code; we resolve codes. Stock packaging is served by page 1. |
| **Automation & controls** | The work before the price is configuration and BOM building, which we do not do. |
| **Building products / LBM** | Real negotiation, but the ERP base is BisTrack/Agility and similar — outside our seven. Connector work before page work. |

**EXCLUDE** — do not revisit without a change in the *market*, not in us.

| Vertical | One-line reason |
|---|---|
| **Lab & medical supply** | GPO and IDN contracts remove the per-line discretion the core loop acts on. A floor check has nothing to check. |
| **Pharma** | WAC, contract and regulated pricing. No discretion, and a compliance surface we have no business near. |
| **Food service** | Weekly price files and cost-plus formulas, on food-service-specific ERPs we do not connect to. Both gates fail. |
| **JanSan** | Recurring contract replenishment; the discretion is at contract negotiation, once a year, not on the line. |
| **Safety supply** | National-account and vending programmes; clean part numbers and thin per-line discretion. Little for either Control or Speed to do. |

---

## 6. Page outlines

### Shared decisions

**URL shape: `/industries/{slug}`.** Mirrors `/erp/{slug}` so one prerender
registry entry, one sitemap entry and one canonical form per page —
`frontend/src/landing/prerender.tsx` already treats the landing as an entry in
`PAGES` rather than a special case, so a second family costs a `.map()`.
`vercel.json` needs one rewrite (`/industries/([^/]+)` → `/industries/$1.html`)
and one trailing-slash redirect, matching the `/erp/` pair exactly;
`deploy/Caddyfile` needs the same.

**Query-intent separation from `/erp/`, stated as a rule:**

> `/erp/*` answers *"will this work with the system I run?"*
> `/industries/*` answers *"will this work for the trade I'm in?"*
> No page targets both, and no `/industries/` page targets an ERP name in its
> title, H1 or meta description.

Checked against the seven existing pages — prophet-21, netsuite, acumatica,
dynamics-365-business-central, sage-x3, sage-100, zoho-books. Those rank for
`{system} + margin/pricing/quoting`. The proposed pages rank for
`{trade} + distributor + pricing/quoting/margin`. **No collision**, with one
watch item: *"industrial distribution ERP pricing"* is a query both families
could plausibly serve. Mitigation — the industries page never says "ERP" in its
metadata and links out to `/erp/prophet-21` for the system question in its first
section, so the two pages cooperate rather than compete.

**Cross-linking:** each industries page links to `/erp/prophet-21` plus the two
next most likely systems for that trade, and each `/erp/` page gains a link to
the industries page(s) that name it. Both directions, or the internal linking is
a one-way street that leaks.

**A note on the neutrality rule, because Phase 2 will collide with it.**
`worked-example.ts` deliberately made the landing's example item
(`EXAMPLE_ITEM = "Part 4114-08"`) decode to nothing, with a comment saying that
a carbide insert on the front page tells a fastener distributor the product was
built for somebody else. That rule is about **shared** surfaces and it must
stay. A vertical page is the opposite case — a trade-specific worked example is
the entire reason the page exists. Phase 2 must not reuse `worked-example.ts`'s
constants and must not touch them; each page gets its own example, in its own
module, with its own arithmetic test. The neutrality of the landing is a
constraint on Phase 2, not a contradiction of it.

**CTA placement, all three pages:** primary CTA after the worked example (the
moment the reader has just seen the mechanism), repeat at the foot. Same
`cta.ts` behaviour as the `/erp/` pages — no new CTA vocabulary, no invented
booking link while `DEMO_BOOKING_URL` is still a token.

---

### Page 1 — `/industries/industrial-mro`

- **H1:** Margin discipline for industrial and MRO distributors
- **Meta title:** `PIE for industrial & MRO distributors · a floor on every quote line`
- **Meta description:** `Every quote line checked against your own margin floor before it goes out, and a breach held for a named approver instead of sent. Computed from the invoice and AP-invoice lines your ERP already holds. Read-only.`

**Sections:**
1. Hero — headline, lead, primary CTA
2. The ratchet — how last-price-paid becomes the floor nobody set
3. Worked example — one line, one floor, one held quote
4. What it reads, and from which system (links to `/erp/prophet-21`, `/erp/acumatica`, `/erp/dynamics-365-business-central`)
5. What each role sees — and that cost is absent from the salesperson's response, not hidden in it
6. Accounts going quiet — the four detectors, named
7. **What PIE does not do on an industrial book** — no rebates or SPAs, no ERP quote history, no interchange outside metalworking
8. CTA

**Worked example:** a repeat MRO line quoted below floor. Cost 100, floor at a
15% margin policy = 117.65, rep types 112. The line does not send; it routes to
a named approver with the policy version on it. Same arithmetic the product
applies (`floor = cost / (1 − margin floor)`), same shape as the hero card, its
own constants and its own test.

**Draft headline and lead:**

> # Margin discipline for industrial and MRO distributors
>
> A rep discounts a line to hold an account. The discount is recorded as the
> last price paid, and next quarter that price is the default. Nobody decided
> it. PIE checks each quote line against the floor your policy sets, using the
> invoice and AP-invoice lines your ERP already wrote — and where a line
> breaches, the platform holds it for a named approver instead of sending it.
> The sign-off is on record, with the policy version that judged it. Nothing is
> written back to your ERP.

---

### Page 2 — `/industries/cutting-tools`

- **H1:** Quote cutting tools without losing the margin in the cross-reference
- **Meta title:** `PIE for cutting tool & metalworking distributors · resolve the enquiry, hold the floor`
- **Meta description:** `Paste a customer's enquiry and each line resolves against your own decoded catalogue — ISO designations and grade systems read, alternatives ranked on decoded dimensions, and nothing invented where nothing discriminates. Then every priced line is checked against your margin floor.`

**Sections:**
1. Hero — headline, lead, primary CTA
2. The twenty-minute line — cross-referencing a competitor's designation by hand
3. Worked example — an enquiry line resolving, then being priced
4. How the resolution actually works: exact match, then decoded attributes, and **abstention** when nothing discriminates
5. Why a suggestion is never promoted to an identity — a scored equivalence is policy under *your* bands, never a stored fact about the products
6. The floor, on the resolved line
7. **What PIE does not do** — no rebates, no ERP quote history, no price or stock in the parser itself
8. Links to `/erp/prophet-21`, `/erp/zoho-books`, `/erp/dynamics-365-business-central`
9. CTA

**Worked example:** a customer asks for a competitor's turning insert
designation. The line resolves to the equivalent in the distributor's own book,
the alternative is ranked on the dimensions the engine decoded, the reasoning is
shown — and then the price the rep types is checked against the floor. This is
the only page that gets to show both halves, and it should.

**Draft headline and lead:**

> # Quote cutting tools without losing the margin in the cross-reference
>
> The enquiry says a competitor's designation. Twenty minutes later somebody has
> found what you stock that is equivalent, and the price on it is a judgement
> made at speed by whoever did the finding. PIE reads the enquiry as it arrived
> — pasted email, a line of WhatsApp — resolves each code against your own
> decoded catalogue, and ranks alternatives on the dimensions and grade it
> actually read, abstaining where nothing discriminates rather than guessing.
> Then it checks the price on the resolved line against your floor. Both halves
> show their working; neither one is a model's opinion.

---

### Page 3 — `/industries/fluid-power` *(conditional — validate before building)*

- **H1:** A floor on every fluid power quote line, across every branch
- **Meta title:** `PIE for fluid power, hose & fitting distributors · one floor across every branch`
- **Meta description:** `Every quote line checked against your own margin policy before it goes out, wherever it was priced. Computed from the invoice and AP-invoice lines your system already holds, and held for a named approver where it breaches.`

**Sections:** same skeleton as page 1, with branch-level price consistency as
the lead problem instead of the last-price ratchet.

**Worked example:** the same line priced differently at two branches; both
checked against one floor.

**Draft headline and lead:**

> # A floor on every fluid power quote line, across every branch
>
> Two branches quote the same assembly a week apart and the prices are not
> close. Neither rep did anything wrong — there was no floor in front of either
> of them at the moment they typed. PIE puts one there, computed from the
> invoice and AP-invoice lines your system already holds, and holds a breach for
> a named approver instead of sending it.

**Do not build this page until one fluid power distributor has confirmed the
lead problem.** The two above are grounded; this one is a hypothesis with a
layout.

---

## 7. Self-review

**Capability search run:** yes — searched `docs/` for an existing market,
vertical, segment, ICP or GTM document (none), and read the existing public-page
machinery before proposing a second page family: `landing/erp.ts`,
`landing/ErpPage.tsx`, `landing/prerender.tsx`, `landing/content.ts`,
`landing/proof.ts`, `landing/worked-example.ts`, `scripts/prerender.mjs`,
`vercel.json`.

**Near-matches found:** `ERP_PAGES` + `ErpPage.tsx` — an existing registry-driven
page family with exactly the metadata, canonical, JSON-LD and sitemap
behaviour a vertical page needs.

**Reuse decision:** extend the pattern, do not copy it. Phase 2 should add an
`INDUSTRY_PAGES` registry mapped into the same `PAGES` array in
`prerender.tsx`, rather than a second prerender path. Whether `ErpPage.tsx`
itself is parameterised or a sibling component is written is a Phase 2 call and
depends on how far the section shapes actually diverge — deciding it here,
before either page exists, would be the abstraction §7 of `CLAUDE.md` warns
against.

**Could this have been data instead of code?** Yes, and it must be: page content
belongs in a registry module like `erp.ts`, with no vertical facts in a
component.

**Invariants:** no code written, so none touched. Phase 2 has one to respect
that is easy to miss — `worked-example.ts`'s deliberate vertical neutrality
applies to shared surfaces and must not be edited to serve a vertical page.

**Corpus / tests:** unchanged. Nothing was run because nothing was built.

**Verdict:** APPROVED WITH NOTED TRADE-OFF — three pages rather than the three
to five requested, and the third conditional on one customer conversation. The
shortfall is deliberate and §5 gives the gate that produced it.
