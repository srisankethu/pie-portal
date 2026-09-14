# Vertical strategy — who buys this, and which pages we build

**Phase 1 research. No code was written for this.** Date: 2026-09-14.
Second pass, deeper than the first; §11 records what the deeper research changed.

This document answers one question: which trades inside B2B distribution should
get a dedicated page on the marketing site, and which should be excluded on
purpose. The excludes are the point. A page for a vertical whose core problem we
do not solve costs more than the traffic it wins, because the first qualified
visitor it brings finds that out on the call.

Two constraints run through every section:

- **A page may only claim what the product does today.** Same rule as
  `frontend/src/landing/` — `content.ts` hides a section rather than fill it,
  `proof.ts` refuses a partial case study, `erp.ts` prints the gaps prominently
  and `erp.test.ts` fails a floor claim on a connector with no cost. A vertical
  page is a narrower, more checkable claim than the landing page, and it gets
  held to the same standard.
- **Capability fit is assessed against code, not against the pitch.** §5 is
  grounded in named modules and named constants. Where the answer is "we do not
  do that", it says so.

---

## 0. Research honesty — read this before you read a number

**I could search the web in this session. I could not read a single source
page.**

- `WebSearch` worked, and was used roughly twenty times across two passes. It
  returns a synthesised summary plus a list of URLs.
- `WebFetch` and `curl` were blocked by the environment's egress proxy for
  **every** external domain attempted — `epicor.com`, `epacube.com`,
  `top10erp.org`, `mdm.com`, `en.wikipedia.org`. All returned
  `EGRESS_BLOCKED` / `CONNECT tunnel failed, response 403`.

So every external claim below is **relayed by a search tool from pages I did not
open.** Tags:

| Tag | Means |
|---|---|
| **CODE** | Verified in this repository or in pie-parser. First-party, checkable by anyone with the checkout. The strongest evidence here. |
| **SEARCH** | Surfaced by web search, from a page I could not open. Directionally useful, individually unverified. Treat a specific figure as an unread quotation, not a fact. |
| **INFERRED** | My reasoning from the two above, or from how distribution generally works. No source. |

**Deliberately absent:** no market sizes, no growth rates, no TAM, no analyst
figures, no vendor customer counts stated as fact, no citation to a page I did
not read.

### The five load-bearing SEARCH claims

These five carry the recommendation. If one is materially wrong, the section it
sits under is wrong. They deserve a human's verification before spend follows.

1. **"Zilliant typically costs $60k–$150k+/year with a 4–6 month
   implementation… not recommended for small businesses under $50M revenue"**,
   and ROI framed around 1–3% margin recovery for companies with $100M+ revenue.
   This defines the whitespace in §3 and is the most consequential claim in the
   document.
2. **"It assumes that a distributor is selling 30% of revenues to accounts with
   SPA pricing agreements that require rebates from manufacturers after the
   invoice is sent."** This is what moves electrical and plumbing to defer.
3. **"40% to 60% of a distributor's bottom-line profit comes from manufacturer
   rebate programs, including SPAs"** (attributed in the relay to Sikich).
   Supports the same defer.
4. **"41% of the top 50 largest distributors rely on Epicor Prophet 21."**
   Supports the P21 ranking weight in §7. Vendor-adjacent source.
5. **"Margins on hose assembly typically run 35–50% gross, versus 20–30% on pure
   distribution"** in fluid power. This is what turned the fluid power page from
   "validate the pain" into a specific technical question (§7).

Sources surfaced (**not opened**): erpresearch.com, estesgrp.com, top10erp.org,
epicor.com, epacube.com, mdm.com, zilliant.com, vendavo.com, pricefx.com,
selecthub.com, g2.com, sumble.com, enable.com, deloitte.com, sparxiq.com,
ogl.co.uk, ximplesolution.com, vistaar.com, getboltwise.com, conexiom.com,
orderpier.com, orderdrafter.com, ordermatic.co, classccomponents.com,
gurufocus.com, csimarket.com, worldlocity.com, appsruntheworld.com, p21ww.org,
gawda.org, ptda.org, naed.org, asa.net, issa.com, nfda-fastener.org, nfpa.com,
ctacquisitions.com, cuttingtoolsai.eu, kennametal.com, 2wtech.com,
turningpointconsulting.com, zoho.com.

---

## 1. Segment landscape

"Catalog complexity" below means *how hard is the part number*, not how many
SKUs. A million-SKU book of clean manufacturer catalogue numbers is a simpler
parsing problem than a fifty-thousand-SKU book where every customer writes the
same insert three ways.

| Vertical | How quoting actually works | Catalog complexity | Typical ERP | Negotiated per line, or read off a book? |
|---|---|---|---|---|
| **Industrial / MRO** | Mixed. National accounts and contract customers priced off agreements; spot, breakdown and non-contract business quoted line by line with real rep discretion. Discounting happens at the edge of the business, and last-price-paid becomes the next quote's anchor. | High — multi-supplier, heterogeneous, no single nomenclature | P21, Acumatica, BC, NetSuite | **Both.** Contract at the top of the book, negotiated below it. The negotiated tail is where the leakage is. |
| **Cutting tools / metalworking** | Per line, per enquiry. Heavy cross-brand substitution — "what have you got like this in a P25 grade". Application knowledge sits with one or two people. | Very high **and decodable** — ISO 1832 designations plus per-manufacturer grade systems, with published cross-brand equivalence at ISO application position | P21 (under "industrial"), BC, Acumatica, Zoho Books at the small end | **Negotiated per line.** Substitution is a pricing event, not only a fulfilment one. |
| **Electrical** | Job and project quotes priced against manufacturer special pricing agreements. Ship-and-debit, claimbacks and volume rebates dominate the economics. | Moderate — industry data standards keep catalogue numbers relatively clean | P21 (core vertical), Eclipse, Trade Service feeds, Ximple | **Book, then SPA.** The book price is a starting point; the SPA is the deal, and the real cost. |
| **Plumbing / PVF** | Project bids plus counter business. Commodity pipe moves on daily cost; engineered and valve product carries spread. Same SPA machinery as electrical. | Moderate; PVF sizing/schedule notation is structured | P21 (core vertical), Eclipse, DDI, Ximple | **Both**, and the commodity half reprices faster than any synced cost. |
| **Fasteners** | Per-line RFQ, often against a print. Constant cross-referencing between distributor part numbers, MPNs, obsolete SKUs and competitor codes. Per-print specials sourced by hand. | **Highest** — and decodable in principle (thread, pitch, length, grade class, drive, head, finish) | P21 (named core vertical: "Industrial & Fasteners") | **Negotiated per line**, with blanket and VMI agreements over the repeat lines. |
| **Bearings / power transmission** | Interchange is the job. A customer names one manufacturer's number and expects an equivalent from what you stock. Tiered and negotiated customer pricing applied per line. | Very high **and decodable** — bearing designations are rigidly structured | P21, Acumatica, BC | **Negotiated**, over a tiered base. |
| **Safety supply** | National accounts, GPO contracts, vending and site programmes. Nationals (Grainger, Fastenal, White Cap, MSC, Uline, Zoro) push vending placement hard. | Low–moderate; manufacturer catalogue numbers, clean | P21, NetSuite, BC | **Book / contract.** Per-line discretion is thin. |
| **JanSan** | Recurring consumable replenishment on contract price lists; tenders for large facilities. | Low | P21, Acumatica, BC, jansan-specific | **Book / contract.** Repeat business at agreed prices. |
| **Building products / LBM** | Takeoff-and-bid; commodity lumber repriced constantly; hard negotiation per job. | Low as a *parsing* problem — dimensional lumber and SKUs, not codes | Epicor BisTrack, DMSi Agility, other LBM-specific | **Negotiated**, aggressively, but on commodities not codes. |
| **Packaging** | Quote per specification for converted product — dimensions, board grade, quantity breaks, freight. Stock distribution alongside. | Low as codes; high as *specs* | Industry-specific (Amtech, Advantive, ePS, CBS); P21 for the stock half | **Negotiated per job**, especially on custom. |
| **Welding & gas** | Split business. Hardgoods quoted like MRO; gas and cylinder rental on contract with recurring rental streams. | Moderate on hardgoods; low on gas | P21 ("Welding"), gas-specific systems | **Both** — split model, split answer. |
| **Automation & controls** | Project quotes with engineering content. Configuration and BOM work happens before a price exists. | High as *configuration*, not as part numbers | P21, BC, NetSuite | **Negotiated per project.** |
| **Lab & medical supply** | GPO and IDN contracts set price, in tiers keyed to compliance percentage (typically ~base / 60–80% commitment / 90%+). | Moderate | P21 ("Medical Supply"), NetSuite | **Contract.** Discretion largely removed by the GPO. |
| **Food service** | Weekly price files, contract cost-plus, very thin margins. | Low | Food-service-specific | **Book / cost-plus formula.** |
| **Pharma** | WAC, contract and regulated pricing; DSCSA traceability obligations. | Low (NDC is a clean identifier) | Pharma-specific wholesale systems | **Contract / regulated.** Essentially no discretion. |

*Evidence: SEARCH for industrial/MRO, cutting tools, electrical, plumbing/PVF,
fasteners, bearings/PT, safety, packaging, lab/medical and the P21 vertical
list. INFERRED for welding & gas quoting detail, automation & controls, food
service, pharma and the JanSan quoting detail.*

**The P21 vertical list**, the most consistent finding across both research
passes: industrial and fasteners, electrical, plumbing and HVAC/R, medical
supply and janitorial/sanitation, fluid power, welding and power automation,
PVF, safety, building materials, tile, consumer goods, paper and packaging.
**SEARCH.**

---

## 2. Two competitor categories, not one

The brief named six competitors, all of them in one category. The second pass
found a second category that competes with a different half of the product, and
missing it would have mis-scored every vertical.

### Category A — price optimisation and management

Zilliant, PROS, Pricefx, Vendavo, epaCUBE, plus the ERP-native option. These
compete with **Control** and **Evidence**.

- Zilliant, Vendavo, PROS and Pricefx all name manufacturing and distribution.
  Zilliant publishes MRO/industrial-specific material and is described as a
  defensible buy for industrial B2B with high SKU counts and clean transaction
  history. **SEARCH.**
- epaCUBE describes itself as serving HVAC, electrical, plumbing, industrial and
  construction distributors for 25 years, with customers achieving 2–4% gross
  profit improvement. **SEARCH.**
- **ERP-native:** Epicor Strategic Pricing is an add-on for both Eclipse and
  Prophet 21, and P21's own pricing is a **matrix** structure rather than price
  lists — rules on product groups, quantity breaks, customer types, regions,
  with override guidelines by customer type and product sensitivity. **SEARCH.**
  This matters for positioning: a floor check is complementary to a pricing
  matrix, not a replacement for one, and the page should say so rather than
  argue with a module the reader already owns.

### Category B — order and document automation

Conexiom, OrderPier, OrderDrafter, Workist, Esker, and DCKAP in the P21 channel.
These compete with **Speed**, and I had missed them entirely in the first pass.

- Conexiom extracts order data from PDF, Excel, email, CSV and handwritten
  notes, validates against the ERP, and is described as trusted by 16 of the top
  20 distributors and manufacturers. **SEARCH.**
- **OrderDrafter automates PO-to-Prophet 21 order entry from $399/month,
  self-serve, live in a week.** **SEARCH** — and note the source is a competitor
  comparison page, so read the framing sceptically and the price point
  seriously. It is the closest published price to the band we would sell in.
- The same competitor-adjacent sources describe Conexiom's model as
  per-trading-partner configuration with an enterprise motion that does not fit
  a $20M–$500M distributor. **SEARCH, and self-serving** — but it is consistent
  with what the newer entrants are built to exploit.

**The distinction the pages must draw:** Category B turns *a known customer's
purchase order* into *an order*. PIE turns *an unknown enquiry* into *priced
quote lines resolved against a decoded catalogue, each checked against a floor*.
Different job, adjacent category, and a buyer will conflate them. Any page
claiming Speed has to say what it does that an order-automation tool does not —
and, honestly, has to concede the reverse where it applies.

---

## 3. The whitespace is a size band, not a vertical

This is the single most useful thing the deeper research produced, and it
reframes the scoring.

The relayed evidence on Category A is consistent: **Zilliant $60k–$150k+ per
year, 4–6 month implementation, explicitly not recommended below $50M revenue,
with ROI framed around 1–3% margin recovery for companies at $100M+.** Vendavo
is described as built for large enterprises. Pricefx does not publish. **All
SEARCH.**

Against that, the reference points for what our buyer already pays: **Prophet 21
from around $75/user/month** (SEARCH), and the nearest adjacent tool priced
publicly at **$399/month** (SEARCH).

So the market structure looks like this, and it cuts *across* verticals:

| Distributor revenue | Served by | Reality |
|---|---|---|
| $100M+ | Zilliant, Vendavo, PROS, epaCUBE | Contested. We would be the cheap option in a category where cheap is read as unserious. |
| $50M–$100M | Category A, reluctantly | Priced at the edge of viability; implementation length is the real barrier. |
| **Under $50M** | **Nobody in Category A** | **The whitespace.** Same leakage mechanism, no product addressed to it. |

Two consequences:

1. **Competitive density should be scored per size band, not per vertical.** The
   vertical determines the *shape* of the pain; the revenue band determines
   whether anyone else is selling into it. §4 scores density with this in mind.
2. **The vertical pages are addressed to independents under roughly $50M**, and
   their voice, worked examples and proof should assume that reader. A page
   written at a $200M distributor is a page written for a Zilliant evaluation we
   would lose on references.

An honest counterweight, INFERRED: under-$50M is unserved partly because it is
hard to sell to — long sales cycles relative to contract value, owner-operators,
low software budgets. Whitespace is not the same as demand.

---

## 4. Fit scoring

Scores 1–5, higher is better *for us*. Competitive density is inverted (5 = open
field) and is now scored **for an under-$50M distributor in that vertical**, per
§3.

| Vertical | Per-line discretion | ERP overlap (our 7) | Part-number messiness | Margin pressure | Deal size / ability to pay | Competitive density (inverted, sub-$50M) | Total |
|---|---|---|---|---|---|---|---|
| **Cutting tools / metalworking** | 5 SEARCH | 4 SEARCH | **5 CODE** | 4 SEARCH | 3 INFERRED | 5 SEARCH | **26** |
| **Fasteners** | 5 SEARCH | 5 SEARCH | 5 SEARCH | 4 SEARCH | 3 INFERRED | 3 SEARCH | **25** |
| **Industrial / MRO** | 4 SEARCH | **5 SEARCH** | 4 SEARCH | 4 SEARCH | 4 INFERRED | 4 SEARCH | **25** |
| **Bearings / power transmission** | 4 SEARCH | 4 INFERRED | 5 SEARCH | 3 INFERRED | 3 SEARCH | 4 INFERRED | **23** |
| **Fluid power / hose & fittings** | 4 INFERRED | 4 SEARCH | 3 INFERRED | 4 SEARCH | 3 INFERRED | 5 INFERRED | **23** |
| **Welding & gas** | 3 INFERRED | 4 SEARCH | 3 INFERRED | 3 INFERRED | 3 SEARCH | 5 INFERRED | **21** |
| **Electrical** | 4 SEARCH | 5 SEARCH | 2 SEARCH | 4 SEARCH | 4 INFERRED | 2 SEARCH | **21** |
| **Plumbing / PVF** | 4 SEARCH | 5 SEARCH | 2 INFERRED | 4 SEARCH | 4 INFERRED | 2 SEARCH | **21** |
| **Automation & controls** | 4 INFERRED | 3 INFERRED | 2 INFERRED | 3 INFERRED | 4 INFERRED | 4 INFERRED | **20** |
| **Packaging** | 4 INFERRED | 2 SEARCH | 2 INFERRED | 3 INFERRED | 3 INFERRED | 4 INFERRED | **18** |
| **Safety supply** | 2 SEARCH | 4 INFERRED | 2 INFERRED | 3 SEARCH | 3 INFERRED | 4 INFERRED | **18** |
| **JanSan** | 2 SEARCH | 4 INFERRED | 1 INFERRED | 3 SEARCH | 3 INFERRED | 4 INFERRED | **17** |
| **Lab & medical supply** | 1 SEARCH | 4 SEARCH | 2 INFERRED | 3 INFERRED | 4 INFERRED | 3 INFERRED | **17** |
| **Building products / LBM** | 4 INFERRED | 1 SEARCH | 1 INFERRED | 4 INFERRED | 4 INFERRED | 3 INFERRED | **17** |
| **Pharma** | 1 INFERRED | 1 INFERRED | 1 INFERRED | 5 INFERRED | 5 INFERRED | 3 INFERRED | **16** |
| **Food service** | 1 INFERRED | 1 INFERRED | 1 INFERRED | 5 INFERRED | 3 INFERRED | 4 INFERRED | **15** |

**The total is not the decision, and §5 is why.** Fasteners scores second and is
still deferred, because the thing that earns it a 5 on messiness is the thing we
cannot do. A score measures the size of the prize; §5 measures whether we can
collect it.

### Notes on the columns

**Margin pressure.** Concrete figures found, all SEARCH, none of them a
benchmark for a mid-market book: MSC Industrial around 41% gross margin, near a
ten-year median of ~42% and compressing, with trailing net margin 5.4% against
6.2% a year earlier. JanSan described at 18–28% gross, 4–8% net. Fluid power
described at 35–50% gross on hose assembly against 20–30% on pure distribution,
with pure-distribution fluid power at 4–6% EBITDA. Electrical described as an
industry "where average margins hover around 4%" (net). Beyond these I have no
per-vertical band I am willing to print, so the column scores *reported
pressure*, not a band.

**Margin leakage — the mechanism we intervene on.** Distributors described as
leaking roughly 2–11.7% margin on average, driven by decentralised discounting,
with 3x price variance quoted for identical products, and a named ratchet: a rep
discounts under pressure, the discount is recorded as last price paid, and the
system defaults to it next time. **SEARCH.** This mechanism is vertical-agnostic
wherever discretion exists, and it is the clearest articulation of our Control
pitch that I found anywhere — in a competitor's marketing.

**Part-number messiness.** Scored on *decodability by us*, not on difficulty.
Cutting tools scores 5 CODE because pie-parser demonstrably decodes it —
6,717 corpus rows, eleven families, 100% classified, zero quarantine. Fasteners
and bearings score 5 on the trade's difficulty and are disqualified in §5, not
here.

**Ability to pay.** Mostly INFERRED. Anchors: P21 from ~$75/user/month (SEARCH);
the nearest adjacent tool at $399/month (SEARCH); Zoho Books India free below
₹25 lakh revenue, Professional ₹1,499/month, Ultimate ₹9,999/month (SEARCH) —
see §6 on why that matters. PTDA distributor members employ 39,211 people across
2,570 locations, which averages small (SEARCH), so bearings/PT is scored 3.

---

## 5. Reality check — what we actually serve

The four capability areas are the landing page's own
(`frontend/src/landing/Landing.tsx`, `#outcomes`): **Control** (every quote line
against your floor, breaches held for sign-off), **Speed** (RFQ text to
resolved, priced lines), **Retention** (signals on accounts going quiet),
**Evidence** (the value ledger).

Five capability facts verified in code. These govern everything after them.

**1. Seven ERP connectors, one uncosted.** `backend/app/ingestion/erp/`:
prophet21, netsuite, acumatica, dynamics365, sagex3, sage100, zoho. The Sage 100
spec declares no `bills` read — its AP history records GL distributions rather
than item lines — so it has no cost, therefore no margin, no floor and no drift.
`ErpPageData.costed` is `false` for it and `erp.test.ts` refuses a floor claim on
that page. **CODE**, re-verified this pass: the `reads=("bills",)` permission in
`sage.py` belongs to the Sage X3 spec, not the Sage 100 one.

**2. No ERP quote history, on any connector.** `READ_STAGES` in
`ingestion/erp/base.py` includes `"quotes"` and **no connector declares it**. A
win rate has no denominator until the customer quotes inside PIE. **CODE.**

**3. There is no rebate, SPA, price-book or contract-price concept anywhere in
the backend.** A recursive case-insensitive grep across `backend/app` for
`rebate`, `SPA`, `special.price`, `price_book`, `pricebook` and `contract_price`
returns nothing but false positives on the word "span". Cost is whatever the AP
invoice line said. **CODE.**

**4. Attribute-level product understanding is metalworking-only, and extending
it is an engine change, not a pack change.** Two named constants:

  - `backend/app/decoding/schema.py` → `CORE_SLOTS`, 40 slots, every one of them
    cutting-tool metalworking: `grade`, `chipbreaker`, `iso_shape`,
    `insert_polarity`, `flute_count`, `point_angle_deg`, `corner_radius_mm`,
    `wiper`, `ball_nose`, `ic_size_mm`. A decoder *can* keep a fact the
    vocabulary has no name for, as an `ext:` field — and the module's own
    docstring states an `ext:` field is **"never compared"**.
  - `pie-parser/equivalence/distance.py` → `HARD_GATE_FIELDS = (product_family,
    iso_shape, insert_polarity)`, `DIMENSIONAL_FIELDS = (cutting_dia_mm,
    edge_length_mm, shank_dia_mm, loc_mm, oal_mm, thickness_mm,
    corner_radius_mm)`, `SOFT_SIGNAL_FIELDS` including `chipbreaker`, `coating`
    and `flute_count`.

  Both are Python tuples in code, not pack data. A thread pitch, a bolt grade
  class, a bearing bore or a hose dash size has nowhere to land that counts, and
  giving it one means editing pie-parser's engine. **CODE.**

  What *is* vertical-agnostic: exact matching, and `backend/app/retrieval/` — a
  hashed n-gram embedder over description text, deterministic and offline. It
  will match `HHCS 1/2-13X2 GR8 ZP` to `HEX CAP SCREW 1/2-13 X 2 GR.8 ZINC` in
  the customer's own book. It cannot rank a *substitute*, because ranking a
  substitute is what the gate and dimension fields do. **CODE.**

  **The honest split: finding the line in your own book — every vertical.
  Proposing an equivalent — metalworking only.**

**5. Catalogues are per-company and per-manufacturer, built from that company's
own uploaded price lists, and there is no default decoder.** A company that has
uploaded nothing resolves nothing (`backend/app/catalog.py`). **CODE.** This is
an onboarding cost on every page, in every vertical, and no page should imply
otherwise.

### Capability × shortlist

| | Industrial / MRO | Cutting tools | Fluid power |
|---|---|---|---|
| **Control** | ✅ Full | ✅ Full | ⚠️ **Full on stocked components; unknown on fabricated assemblies** — see §7 |
| **Speed** | ⚠️ **Partial.** Enquiry → lines → exact and nearest-neighbour match against their own catalogue: yes. Attribute-ranked alternatives: **no**. The page must not claim cross-reference. | ✅ Full, and the only vertical where it is | ⚠️ Partial, as Industrial/MRO |
| **Retention** | ✅ Full — `CUSTOMER_DECLINE`, `CUSTOMER_DORMANCY`, `MARGIN_DETERIORATION`, `COST_PASS_THROUGH` plus six customer×item detectors, all over persisted rows | ✅ Full | ✅ Full |
| **Evidence** | ✅ Full, with the standing rule that a month with no detection reads UNKNOWN, not zero | ✅ Full | ✅ Full |

### What we do NOT address, in every vertical

- **No SPA, rebate or claim-back economics.** Margin is computed on invoiced
  cost.
- **No quote history from the ERP**, so no win/loss rate until they quote here.
- **No stock levels from Prophet 21** in this version — the stock and GMROI
  screens stay empty on a P21 book.
- **No customer payments from Prophet 21** — collections and days-to-pay have
  nothing to read on a P21 book.
- **No credit notes from P21** — a credited line still counts as sold.
- **No salespeople imported** — everything routes to management until accounts
  are assigned inside PIE.
- **No configuration or BOM building** — which excludes automation & controls
  and custom packaging.
- **No cross-manufacturer interchange outside metalworking** — which defers
  fasteners and bearings despite their scores.
- **No recurring-revenue or rental modelling** — which defers the gas half of
  welding & gas.

### The SPA problem, stated precisely

This deserves its own treatment because the first pass got the *direction*
wrong, and the corrected version is a better argument.

The relayed evidence: a distributor may sell **around 30% of revenue to accounts
on SPA agreements** where the rebate arrives after the customer is invoiced;
**40–60% of bottom-line profit** is said to come from manufacturer rebate
programmes; 8–12% of legitimate rebates go unclaimed; and this sits in an
industry described at roughly 4% average net margin. **All SEARCH.**

Under ship-and-debit and claimback mechanics, the distributor buys at standard
cost and claims the difference afterwards. So the AP invoice line — the only
cost we read — is **higher** than the effective cost. Our floor is
`cost / (1 − margin_floor)`, so an inflated cost gives an **inflated floor**.

The failure is therefore **systematic over-holding, not under-selling.** On an
SPA-heavy book we would flag as below-floor a large share of lines that are
genuinely profitable. That is worse than it sounds and worse than the first pass
implied: a control that objects wrongly to a third of the book is overridden
into irrelevance inside a month, and then the control is dead on the lines where
it was right. It also fails the platform's own rule — a floor computed from a
cost that is not the cost is exactly the benign-looking wrong number that
`CLAUDE.md` §1 exists to prevent.

**This is why electrical and plumbing/PVF defer, and it is a build, not a
caveat.** It also means the Industrial/MRO page must name SPA and rebate
economics in its "what PIE does not do" section, because that vertical has some
exposure too — less than electrical, not zero.

---

## 6. Channel — where these distributors actually congregate

This section did not exist in the first pass and is more actionable than half of
the scoring. All figures **SEARCH**.

**Buying and marketing groups.** These are the densest concentrations of exactly
our buyer: independent, mid-market, multi-vertical.

| Group | Scale | Relevance |
|---|---|---|
| **AD (Affiliated Distributors)** | 898 independent members, 918 suppliers, 89 service-provider partners, 13 divisions across US/Canada/Mexico | Divisions cover electrical, industrial, safety, **bearings & power transmission**, plumbing, PVF, HVAC, building materials. The single best map of the independent mid-market. |
| **AD + IMARK Electrical** | ~725 independent electrical distributors combined, post-2024 merger | Electrical concentration — a vertical we are deferring. |
| **Current Distribution Group** | IMARK Plumbing, IMARK HVAC, Victory PVF, Victory Waterworks, Victory Irrigation, Luxury Products Group, Empower Electrical | The plumbing/PVF concentration, likewise deferred. |
| **NetPlus Alliance** | 410+ industrial and contractor supplies distributors, $9B+ combined sales | **Squarely the Industrial/MRO page's audience.** |
| **PTDA** | 131 distributor members, 151 manufacturers, 2,570 locations, 39,211 employees | Bearings/PT independent base — real, and small. |
| **GAWDA** | 500–550 member companies | Welding & gas. |
| **NAED** | Membership across 5,100+ locations | Electrical. |
| **ISSA** | 11,000+ members (broad cleaning industry, not only distributors) | JanSan. |
| **NFDA / FDI** | Publishes a monthly Fastener Distributor Index with FCH Sourcing Network | Fasteners — a live sentiment instrument, and a 2018 Profit Report exists. |
| **FPDA** | Trade association specifically for fluid power distributors | Fluid power. |

**The P21 partner channel is small and named.** One database tracks roughly
**12 resellers and systems integrators** for Prophet 21. Names surfaced:
EstesGroup, TurningPoint Consulting (described as the only Epicor Services
Partner holding both Kinetic Manufacturing and P21 Distribution designations),
Scaled Solutions Group, Elevate Technology, Atlas Precision Consulting, DCKAP,
and Conexiom. **SEARCH.**

Two things follow, and neither is a page:

1. **A twelve-partner channel is tractable in a way a market is not.** If the
   ranking is to be weighted toward the P21 installed base, the highest-leverage
   act is probably not a landing page at all — it is a conversation with three
   of those firms. I am flagging it because the brief asked me to weight toward
   that installed base and a page is the weakest instrument for reaching it.
2. **Conexiom appears in that channel and competes with our Speed claim.** A
   partner-led motion into P21 runs into them. The pages should be written so
   that they do not have to win that argument.

**India, and why it is scored separately.** Zoho Books is free in India below
₹25 lakh revenue, ₹1,499/month at the Professional tier that adds inventory and
purchase orders, ₹9,999/month at Ultimate (**SEARCH**). Three of the operating
entities behind this product run there. The ability-to-pay gap against a US
mid-market distributor is an order of magnitude, and no page written for the US
independent will price or read correctly for that buyer. **No vertical page
should be built for the India segment**; `/erp/zoho-books` already carries that
reader, and what that segment needs is a pricing answer, not a trade page.

---

## 7. Recommendation

### The gate

1. Real per-line pricing discretion, **and**
2. cost in the ERP that is actually true cost — no SPA/rebate distortion, **and**
3. an ERP we connect to, **and**
4. no dependence on a capability we have not built, **for the vertical's
   headline pain**.

### Build these

| # | Vertical | Status | Why |
|---|---|---|---|
| 1 | **Industrial & MRO distribution** | **Firm** | Widest overlap with the P21 installed base; NetPlus alone is 410+ distributors of exactly this shape; the leakage mechanism we intervene on is documented for this segment specifically; every claim is true today with Speed scoped honestly. |
| 2 | **Cutting tools & metalworking** | **Firm** | The only vertical where all four capability areas are fully true, the only one where we can show a decode rather than describe one, and — see below — the only one whose own professional norms match our architecture. |
| 3 | **Fluid power, hose & fittings** | **Conditional on one technical question** | Named P21 core vertical, no Category A vendor apparently leading at it, FPDA as a channel. But see the question below. |

**Two firm pages, not three.** I could not find a third vertical that passes the
gate on evidence, and padding to three would mean shipping a page I already know
the risk of.

#### Why cutting tools is the strongest page even though it is the smallest audience

The trade's own norm is that a cross-brand grade equivalence is *a comparable
starting point, not an identical substitute* — substrate, coating and edge
preparation differ between brands, and the advice is always to verify on a part
before switching (**SEARCH**; published cross-reference sets cover on the order
of 53 grades across 9 brands at ISO application position).

That is, almost word for word, this platform's own invariant: an equivalence
score is policy, never an identity; a scored suggestion is never promoted to a
confirmed mapping; the engine abstains where nothing discriminates. **No other
vertical's professionals already believe the thing our architecture enforces.**
Every competitor in Category B is selling confidence; this page can sell
calibrated doubt to the one audience that will read it as competence rather than
weakness. That is worth more than the audience size it costs.

#### The fluid power question — answer this before building

The first pass called this page conditional on validating the pain. The second
pass replaced that with something specific and answerable.

Fluid power's margin is not in the components. Hose assembly is described at
**35–50% gross against 20–30% on pure distribution** (SEARCH), and the largest
variable in a fluid power distributor's EBITDA is service capability — the
crimping bench, not the shelf. But an assembly is *fabricated*, not stocked.

So: **does the ERP carry a cost on a made-up hose assembly, or only on its
components?** If the assembly is a costed item, Control works and the page is
good. If assemblies are built ad-hoc at the counter, then on the highest-margin
half of the business we have no cost — and by `CLAUDE.md` §1 the honest answer
is UNKNOWN, not a floor. A page selling margin control to a business whose margin
pool is invisible to us is the exact failure this document is written to avoid.

One conversation with one fluid power distributor answers it. Do not build page
3 before that call.

### Build priority, and where the P21/Epicor pull moved it

**Ranked: (1) Industrial & MRO, (2) Cutting tools & metalworking, (3) Fluid
power, if it survives its question.**

On capability fit alone, **cutting tools is first** — all four areas true, our
one genuine technical moat, and a worked example that can show a real decode
instead of asserting one.

The installed base does not sit there. P21's own vertical list leads with
industrial and fasteners, electrical, plumbing and HVAC/R, medical supply and
jansan, fluid power, welding, PVF, safety and building materials (SEARCH);
cutting tools appears only underneath "industrial". 41% of the top 50 largest
distributors are said to run P21 (SEARCH, flagged in §0), and P21 is the richest
connector we have. NetPlus's 410+ industrial and contractor distributors are the
Industrial/MRO page's audience and have no cutting-tools equivalent of that size.

**So the installed-base weight pushed Industrial & MRO above Cutting tools.**
That is the whole of the movement, and the uncomfortable half belongs on the
record too: **the P21 base pulls hardest toward three verticals this product
cannot serve correctly today** — electrical and plumbing/PVF on rebate
economics, fasteners on interchange. Following it any further than first place
would mean building pages for exactly those. It pulled Industrial & MRO to the
top and then had to be stopped.

A third observation the channel research forces: if the goal is genuinely to
reach the P21 base, **three conversations in a twelve-firm partner channel
probably beat three landing pages.** That is outside what was asked for and I am
not acting on it, but it should not go unsaid in a document that was asked to
weight toward that installed base.

### DEFER — the product would have to change first

| Vertical | Reason, and the change that would move it |
|---|---|
| **Fasteners** | Scores second and fails gate 4: the headline pain *is* cross-reference. Needs thread, pitch, length, grade-class, drive, head and finish slots in `CORE_SLOTS` and matching gate/dimension fields in `equivalence/distance.py`. **Highest-value defer on this list** — best combination of ERP overlap, discretion and decodability once the slots exist. |
| **Bearings / power transmission** | Interchange is the entire job and designations are rigidly structured — the cleanest second nomenclature pack we could build. Needs bore, OD, width, seal and clearance slots. Smaller base than fasteners (PTDA: 131 distributor members). |
| **Electrical** | ~30% of revenue on SPA accounts means an inflated cost, an inflated floor, and systematic over-holding until the desk overrides everything. Needs a rebate/SPA cost layer. Also the densest Category A competition (epaCUBE, Zilliant, Ximple, Epicor Strategic Pricing). |
| **Plumbing / PVF** | Same rebate machinery as electrical, plus commodity pipe that reprices faster than any synced cost. |
| **Welding & gas** | The hardgoods half is Industrial/MRO and is already served by page 1; the gas and cylinder-rental half is recurring revenue and rental we model nothing of. Half the business invisible. |
| **Packaging** | Converted product is quoted per specification — dimensions, board grade, quantity breaks, freight — not per catalogue code, and the ERP base is industry-specific (Amtech, Advantive, ePS, CBS) rather than one of our seven. Two gates fail. |
| **Automation & controls** | The work before the price is configuration and BOM building, which we do not do. |
| **Building products / LBM** | Real negotiation, but the ERP base is BisTrack, DMSi Agility and similar — outside our seven. Connector work before page work. |

### EXCLUDE — do not revisit without a change in the market, not in us

| Vertical | Reason |
|---|---|
| **Lab & medical supply** | GPO and IDN contracts set price in tiers keyed to compliance percentage. The per-line discretion the core loop acts on has been contracted away. A floor check has nothing to check. |
| **Pharma** | WAC, contract and regulated pricing. No discretion, and a compliance surface we have no business near. |
| **Food service** | Weekly price files and cost-plus formulas, on food-service-specific ERPs we do not connect to. Both gates fail. |
| **JanSan** | Recurring contract replenishment; discretion sits at annual contract negotiation, not on the line. |
| **Safety supply** | National accounts, GPO and vending programmes, clean part numbers, thin per-line discretion — and a segment where the nationals (Grainger, Fastenal, White Cap, MSC, Uline, Zoro) set the terms. Little for Control or Speed to do. |

### One option that is not a vertical

The §3 finding — that the whitespace is a size band — suggests a page addressed
to *independent distributors under $50M* rather than to a trade. Every claim on
it would be true, and it targets a real search intent ("pricing software for
small distributors", and the alternatives-to-Zilliant intent).

I am **not recommending it**, for one reason: it is a competitor-comparison page
in everything but name, and whether this company names competitors on its own
site is a positioning decision for its owner, not an inference from a research
document. Raised so the option is visible, not to be actioned here.

---

## 8. Personas and pain — shortlist only

### Industrial / MRO distribution

- **Economic buyer:** owner or President at a single-site to five-branch
  distributor; VP Sales or CFO above about $50M.
- **Champion:** whoever owns pricing — a Pricing Manager where one exists,
  otherwise the sales manager who signs off discounts, otherwise the owner doing
  it in their head.
- **Blocker:** the top-billing outside rep who has priced their own accounts for
  twenty years and reads a floor as a leash. Secondary: the ERP owner who has
  survived one P21 upgrade and will not have anything write back to it. *(Our
  read-only posture answers the second blocker directly and the page should lead
  with it.)*

**The three pains, in their words:**

1. *"I find out we sold it under cost when I close the month."*
2. *"Every rep quotes the same part differently, and last price paid keeps
   ratcheting down."* Nobody set the price that is now the default.
3. *"I didn't know that account was slipping until they'd already gone."*

**Workflow moment:** the instant a price is typed on a quote line, before the
quote is sent. Not a monthly review, not a dashboard.

**What they do today:** an ERP margin report read weekly or monthly; a
spreadsheet of "our prices for this customer" on somebody's desktop; the sales
manager's memory; a P21 pricing matrix nobody has re-tuned since go-live;
Epicor Strategic Pricing at the larger end; epaCUBE or Zilliant above $50–100M.
A meaningful share do nothing and rely on the rep.

### Cutting tools / metalworking distribution

- **Economic buyer:** owner or managing director, usually still fluent in the
  catalogue.
- **Champion:** the internal sales or applications engineer who turns enquiries
  into quotes and does the cross-referencing by hand.
- **Blocker:** the same applications engineer, if the product reads as
  automation that replaces their judgement rather than a tool that shows its
  working. A positioning risk, not a feature gap — and the abstention behaviour
  is the answer to it.

**The three pains:**

1. *"They ask for a competitor's insert and we spend twenty minutes finding what
   we've got that's equivalent."* Cross-reference is manual, per line, and the
   quote waits.
2. *"The grade substitution was right and the price was wrong."* The technical
   decision and the commercial one are made by the same person in the same
   moment, and only one of them has a check on it.
3. *"Nobody can tell me why we quoted that price last March."*

**Workflow moment:** the enquiry-to-quote pass, then the price on each resolved
line.

**What they do today:** manufacturer conversion guides (Kennametal publishes
one) and cross-brand grade charts; the applications engineer's memory; a
spreadsheet of past quotes; re-keying the enquiry into the ERP by hand.

### Fluid power / hose & fittings — conditional

- **Economic buyer:** owner or GM. **Champion:** counter and inside sales
  manager. **Blocker:** the branch manager whose branch has its own pricing
  habits.
- **The three pains:** the same three as Industrial/MRO, one register down —
  below-cost lines found at month end, inconsistent pricing between branches,
  quiet account decline. **I am not claiming a distinct fluid-power pain**, and
  the assembly-costing question in §7 may turn out to be the real one.
- **What they do today:** INFERRED — ERP reports and rep judgement.

---

## 9. Page outlines

### Shared decisions

**URL shape: `/industries/{slug}`**, mirroring `/erp/{slug}`. One prerender
registry entry, one sitemap entry, one canonical form per page —
`frontend/src/landing/prerender.tsx` already treats the landing as an entry in
`PAGES` rather than a special case, so a second family costs a `.map()`.
`vercel.json` needs one rewrite (`/industries/([^/]+)` → `/industries/$1.html`)
and one trailing-slash redirect, matching the `/erp/` pair; `deploy/Caddyfile`
needs the same (it already carries the `@erp_slash` redirect and the `@html`
matcher to copy).

**Query-intent separation from `/erp/`, as a rule:**

> `/erp/*` answers *"will this work with the system I run?"*
> `/industries/*` answers *"will this work for the trade I'm in?"*
> No page targets both, and no `/industries/` page carries an ERP name in its
> title, H1 or meta description.

Checked against all seven existing pages — prophet-21, netsuite, acumatica,
dynamics-365-business-central, sage-x3, sage-100, zoho-books. Those rank for
`{system} + margin/pricing/quoting`; the proposed pages rank for
`{trade} + distributor + pricing/quoting/margin`. **No collision**, with one
watch item: *"industrial distribution ERP pricing"* could plausibly serve both.
Mitigation — the industries page never says "ERP" in its metadata and links to
`/erp/prophet-21` for the system question inside its first section, so the pages
cooperate rather than compete.

**Cross-linking, both directions or not at all.** Each industries page links to
`/erp/prophet-21` plus the two next most likely systems for that trade; each
`/erp/` page gains a link to the industries page(s) naming it. One-way internal
linking leaks.

**Every page needs a "what PIE does not do here" section, above the final CTA.**
The `/erp/` pages already do this and `erp.ts` explains why at length: a
distributor who has survived one ERP implementation does not believe a
capability list, they believe a vendor who volunteers the gaps. Each vertical
page's list is drawn from §5 plus that vertical's own — and the Industrial/MRO
page must name SPA and rebate economics.

**A collision Phase 2 will hit.** `worked-example.ts` deliberately made the
landing's example item (`EXAMPLE_ITEM = "Part 4114-08"`) decode to nothing, with
a comment saying a carbide insert on the front page tells a fastener distributor
the product was built for somebody else. **That rule is about shared surfaces
and must stay.** A vertical page is the opposite case — a trade-specific worked
example is the reason the page exists. Phase 2 must not reuse or edit
`worked-example.ts`'s constants; each page gets its own example module with its
own arithmetic test, on the pattern of `worked-example.test.ts`.

**CTA placement, all pages:** primary CTA immediately after the worked example,
repeat at the foot. Same `cta.ts` behaviour as the `/erp/` pages — no new CTA
vocabulary, no invented booking link while `DEMO_BOOKING_URL` is a token.

---

### Page 1 — `/industries/industrial-mro`

- **H1:** Margin discipline for industrial and MRO distributors
- **Meta title:** `PIE for industrial & MRO distributors · a floor on every quote line`
- **Meta description:** `Every quote line checked against your own margin floor before it goes out, and a breach held for a named approver instead of sent. Computed from the invoice and AP-invoice lines your system already holds. Read-only — nothing is written back.`

**Sections:**
1. Hero — headline, lead, primary CTA
2. The ratchet — how last price paid becomes the floor nobody set
3. Worked example — one line, one floor, one held quote
4. Read-only, and what it reads (links to `/erp/prophet-21`, `/erp/acumatica`, `/erp/dynamics-365-business-central`)
5. What each role sees — cost absent from the salesperson's response, not hidden in it
6. Accounts going quiet — the four detectors, named
7. **What PIE does not do on an industrial book** — no SPA or rebate economics, no ERP quote history, no interchange outside metalworking, no stock from P21 in this version
8. CTA

**Worked example:** a repeat MRO line quoted below floor. Cost 100, a 15% margin
floor policy puts the floor at 117.65, the rep types 112. The line does not
send; it routes to a named approver carrying the policy version. Same formula
the product applies (`floor = cost / (1 − margin floor)`), its own constants,
its own test.

**Draft headline and lead:**

> # Margin discipline for industrial and MRO distributors
>
> A rep discounts a line to hold an account. The discount is recorded as the
> last price paid, and next quarter that price is the default. Nobody decided
> it. PIE checks each quote line against the floor your policy sets, using the
> invoice and AP-invoice lines your system already wrote — and where a line
> breaches, the platform holds it for a named approver instead of sending it.
> The sign-off is on record with the policy version that judged it. Nothing is
> written back to your ERP.

---

### Page 2 — `/industries/cutting-tools`

- **H1:** Quote cutting tools without losing the margin in the cross-reference
- **Meta title:** `PIE for cutting tool & metalworking distributors · resolve the enquiry, hold the floor`
- **Meta description:** `Paste a customer's enquiry and each line resolves against your own decoded catalogue — ISO designations and grade systems read, alternatives ranked on the dimensions actually decoded, and nothing offered where nothing discriminates. Then every priced line is checked against your margin floor.`

**Sections:**
1. Hero — headline, lead, primary CTA
2. The twenty-minute line — cross-referencing a competitor's designation by hand
3. Worked example — an enquiry line resolving, then being priced
4. How resolution works: exact match, then decoded attributes, then **abstention**
5. **A cross-reference is a starting point, and the product treats it as one** — a scored equivalence is policy under *your* bands, never a stored fact about the products, and a suggestion is never promoted to a confirmed identity
6. The floor, on the resolved line
7. **What PIE does not do** — no SPA or rebate economics, no ERP quote history, no price or stock inside the parser itself, and a catalogue has to be uploaded before anything resolves
8. Links to `/erp/prophet-21`, `/erp/zoho-books`, `/erp/dynamics-365-business-central`
9. CTA

**Worked example:** a customer asks for a competitor's turning-insert
designation. The line resolves against the distributor's own decoded book, the
alternative is ranked on the dimensions and grade the engine actually read, the
reasoning is shown — and then the price the rep types is checked against the
floor. The only page that gets to show both halves, and it should.

**Draft headline and lead:**

> # Quote cutting tools without losing the margin in the cross-reference
>
> The enquiry names a competitor's designation. Twenty minutes later somebody
> has found what you stock that is equivalent, and the price on it is a
> judgement made at speed by whoever did the finding. PIE reads the enquiry as
> it arrived — a pasted email, a line of WhatsApp — resolves each code against
> your own decoded catalogue, and ranks alternatives on the dimensions and grade
> it actually read. Where nothing discriminates it says so instead of guessing,
> because a same-ISO-position grade is a starting point and your applications
> engineer already knows that. Then it checks the price on the resolved line
> against your floor. Both halves show their working; neither is a model's
> opinion.

---

### Page 3 — `/industries/fluid-power` *(do not build until §7's question is answered)*

- **H1:** A floor on every fluid power quote line, across every branch
- **Meta title:** `PIE for fluid power, hose & fitting distributors · one floor across every branch`
- **Meta description:** `Every quote line checked against your own margin policy before it goes out, wherever in the business it was priced. Computed from the invoice and AP-invoice lines your system already holds, and held for a named approver where it breaches.`

**Sections:** the page-1 skeleton, with branch-level price consistency as the
lead problem instead of the last-price ratchet, and — **if and only if the
assembly-costing question comes back favourably** — a section on assembly lines.
If it comes back unfavourably, the page is not built.

**Draft headline and lead:**

> # A floor on every fluid power quote line, across every branch
>
> Two branches quote the same assembly a week apart and the prices are not
> close. Neither counter did anything wrong — there was no floor in front of
> either of them at the moment they typed. PIE puts one there, computed from the
> invoice and AP-invoice lines your system already holds, and holds a breach for
> a named approver instead of sending it.

---

## 10. What would move a deferred vertical onto the page list

Ordered by expected value, so this reads as a roadmap rather than a list of
apologies.

1. **Fastener slots** — add thread, pitch, length, grade class, drive, head and
   finish to `CORE_SLOTS`, and the corresponding gate/dimension/soft fields in
   `pie-parser/equivalence/distance.py`. Unlocks the second-highest-scoring
   vertical, with the best ERP overlap on the list. An engine change in both
   repositories plus a nomenclature pack.
2. **A rebate / SPA cost layer** — a sourced adjustment between invoiced cost
   and effective cost, versioned like everything else, refusing to estimate
   where no agreement is on record. Unlocks electrical and plumbing/PVF
   together, the two densest P21 verticals. Largest build on this list, and the
   one with the largest unlock.
3. **Bearing slots** — bore, OD, width, seal, clearance. Same shape of change as
   (1), smaller audience.
4. **The `quotes` read stage on any connector** — not a vertical unlock, but it
   is the denominator every Evidence claim currently lacks, on every page.
5. **Stock and customer payments from Prophet 21** — closes two of the seven
   gaps that every P21 page currently has to print.

---

## 11. What the second research pass changed

Recorded because a document that quietly overwrites its own conclusions is not
worth re-reading.

1. **A whole competitor category was missing.** The first pass scored only price
   optimisation (Zilliant, Vendavo, PROS, Pricefx, epaCUBE). Order and document
   automation — Conexiom, OrderPier, OrderDrafter, Workist, Esker — competes
   with the Speed capability and one of them sits in the P21 partner channel.
   §2 is new.
2. **Competitive density was the wrong axis.** It is far more a function of
   revenue band than of vertical: nothing in Category A is addressed below $50M.
   §3 is new and it re-scored the density column throughout §4.
3. **The SPA argument was directionally wrong.** The first pass said an inflated
   cost gives "a wrong number". It gives an inflated *floor* and therefore
   systematic **over-holding** — a control that objects wrongly to a third of
   the book and gets overridden into irrelevance. Same conclusion, much better
   reason, and it now also puts a required disclosure on the Industrial/MRO page.
4. **Fluid power's conditionality became specific.** It was "validate the pain".
   It is now: does the ERP carry a cost on a fabricated hose assembly, or only
   on components? That question is answerable in one call, and the 35–50%
   assembly gross margin figure is why it matters.
5. **Cutting tools gained its strongest argument.** The trade's own norm — a
   same-ISO-position grade is a comparable starting point, not an identical
   substitute — is this platform's equivalence-is-policy invariant stated by the
   customer. That is now the spine of page 2.
6. **A channel section exists.** Buying groups (AD's 898 members and 13
   divisions, NetPlus's 410+, PTDA, GAWDA, NAED, FPDA, NFDA) map the independent
   mid-market better than ERP vendor lists do, and the P21 partner channel turns
   out to be roughly twelve firms — which raises the question, honestly, of
   whether pages are the right instrument for the installed-base weighting the
   brief asked for.
7. **Fluid power moved from "recommended third" to "conditional third", and
   nothing was promoted to replace it.** Two firm pages is the honest count.

---

## 12. Self-review

**Capability search run:** yes — searched `docs/` for an existing market,
vertical, segment, ICP or GTM document (none), and read the public-page
machinery before proposing a second page family: `landing/erp.ts`,
`landing/ErpPage.tsx`, `landing/prerender.tsx`, `landing/content.ts`,
`landing/proof.ts`, `landing/worked-example.ts`, `scripts/prerender.mjs`,
`vercel.json`, `deploy/Caddyfile`.

**Near-matches found:** `ERP_PAGES` + `ErpPage.tsx` — an existing
registry-driven page family with exactly the metadata, canonical, JSON-LD and
sitemap behaviour a vertical page needs.

**Reuse decision:** extend the pattern, do not copy it. Phase 2 should add an
`INDUSTRY_PAGES` registry mapped into the same `PAGES` array in
`prerender.tsx`, not a second prerender path. Whether `ErpPage.tsx` is
parameterised or a sibling component written is a Phase 2 call that depends on
how far the section shapes actually diverge; deciding it here, before either
page exists, would be the abstraction `CLAUDE.md` §7 warns against.

**Could this have been data instead of code?** Yes, and it must be: page content
belongs in a registry module like `erp.ts`, with no vertical facts in a
component.

**Invariants:** no code written, so none touched. Phase 2 has one that is easy
to miss — `worked-example.ts`'s deliberate vertical neutrality applies to shared
surfaces and must not be edited to serve a vertical page.

**Corpus / tests:** unchanged. Nothing was run because nothing was built.

**Verdict:** APPROVED WITH NOTED TRADE-OFF — two firm pages and one conditional,
against the three to five requested. The shortfall is deliberate; §7 gives the
gate that produced it and §10 gives the route to widening it.
