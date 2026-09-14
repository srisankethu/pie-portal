# Vertical strategy — who buys this, and which pages we build

**Research and the decision it produced.** Date: 2026-09-14. Second pass,
deeper than the first; §12 records what the deeper research changed.

**Status: seven trade pages live, and the gate that produced them has been
revised once since this document first argued for two.** See §14 — the short
version is that the original fourth condition was stricter than the rule this
repository already applies to its ERP pages, and `/erp/sage-100` is the proof:
it ships for a book with no purchase cost at all, under a different headline,
with the gap printed prominently rather than the page withheld.

Live now: industrial & MRO, cutting tools, fasteners, bearings & power
transmission, fluid power, electrical, plumbing & PVF, plus the three `/roles/`
pages. `frontend/src/landing/industries.ts` is the registry, and its tests hold
the site to the gate below rather than to this paragraph — including one that
pins the list, so an eighth page is a deliberate act rather than a copy.

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
- **Capability fit is assessed against code, not against the pitch.** §6 is
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
   Supports the P21 ranking weight in §8. Vendor-adjacent source.
5. **"Margins on hose assembly typically run 35–50% gross, versus 20–30% on pure
   distribution"** in fluid power. This is what turned the fluid power page from
   "validate the pain" into a specific technical question (§8).

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
   whether anyone else is selling into it. §5 scores density with this in mind.
2. **The vertical pages are addressed to independents under roughly $50M**, and
   their voice, worked examples and proof should assume that reader. A page
   written at a $200M distributor is a page written for a Zilliant evaluation we
   would lose on references.

An honest counterweight, INFERRED: under-$50M is unserved partly because it is
hard to sell to — long sales cycles relative to contract value, owner-operators,
low software budgets. Whitespace is not the same as demand.

---

## 4. The stated ICP, scored against every vertical

The owner stated the ideal customer as seven properties. This section scores
every vertical against them, unmodified. It is the primary ranking in this
document; §5 is the commercial overlay that sits beside it.

| # | Criterion |
|---|---|
| **C1** | Thousands of SKUs, or a complex technical catalog |
| **C2** | Multiple suppliers and changing purchase costs |
| **C3** | Salespeople who prepare frequent quotations |
| **C4** | Customer-specific prices, discounts and negotiated margins |
| **C5** | Technical product specifications or compatibility requirements |
| **C6** | An ERP containing product, purchase, sales and pricing history |
| **C7** | A meaningful risk of margin leakage or incorrect quotations |

Scale 0–3: 0 absent, 1 weak, 2 solid, 3 defining. Max 21.

| Vertical | C1 | C2 | C3 | C4 | C5 | C6 | C7 | **/21** |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **Fasteners** | 3 | 3 | 3 | 3 | 3 | 3 | 3 | **21** |
| **Cutting tools / metalworking** | 3 | 3 | 3 | 3 | 3 | 2 | 3 | **20** |
| **Bearings / power transmission** | 3 | 3 | 3 | 3 | 3 | 2 | 3 | **20** |
| **Industrial / MRO** | 3 | 3 | 3 | 3 | 2 | 3 | 3 | **20** |
| **Electrical** | 3 | 3 | 3 | 3 | 2 | 3 | 3 | **20** |
| **Plumbing / PVF** | 3 | 3 | 3 | 3 | 2 | 3 | 3 | **20** |
| **Automation & controls** | 3 | 2 | 3 | 3 | 3 | 2 | 3 | **19** |
| **Fluid power / hose & fittings** | 2 | 2 | 3 | 2 | 3 | 2 | 3 | **17** |
| **Building products / LBM** | 1 | 3 | 3 | 3 | 1 | 1 | 3 | **15** |
| **Welding & gas** | 2 | 2 | 2 | 2 | 2 | 2 | 2 | **14** |
| **Packaging** | 2 | 2 | 3 | 2 | 2 | 1 | 2 | **14** |
| **Safety supply** | 2 | 2 | 1 | 2 | 2 | 2 | 1 | **12** |
| **Lab & medical supply** | 3 | 2 | 1 | 1 | 2 | 2 | 1 | **12** |
| **JanSan** | 2 | 2 | 1 | 2 | 0 | 2 | 1 | **10** |
| **Food service** | 2 | 3 | 1 | 1 | 0 | 1 | 1 | **9** |
| **Pharma** | 2 | 1 | 0 | 0 | 0 | 2 | 0 | **5** |

Evidence carries over from §1 and §6 rather than being restated per cell: C1,
C3, C4 and C5 are SEARCH for the top eight and INFERRED below them; C6 is CODE
where one of our seven connectors is the typical system and SEARCH otherwise;
C7 is SEARCH for industrial/MRO, cutting tools, electrical, plumbing/PVF and
fasteners, INFERRED elsewhere.

### What this ranking does and does not measure

**Every one of the seven criteria is a property of the customer. None is a
property of us.** So this is a clean measure of *who has the problem* and says
nothing about *whom we can serve today*. That is not a criticism of the ICP —
it is the right shape for an ICP — but the two rankings must be read together,
because they disagree at the top.

Six verticals sit within one point of each other at 20–21, which is the real
finding: **the ICP does not discriminate among the top six.** What separates
them is §6, and the separator is almost entirely **C5**.

### C5 is where the ICP and the product diverge

C5 asks whether the trade has technical specification or **compatibility**
requirements. Compatibility is interchange, and interchange is precisely the
capability that `CORE_SLOTS` and `equivalence/distance.py` only carry for
metalworking (§6, fact 4).

So C5 is the one criterion where a score of 3 is simultaneously the strongest
buying signal and, outside cutting tools, the clearest statement of what we
cannot yet do:

| Vertical | C5 = 3 because | Can we serve that today? |
|---|---|---|
| Cutting tools | ISO 1832 designations, grade systems, cross-brand equivalence at ISO application position | **Yes** — the only one |
| Fasteners | Thread, pitch, grade class, drive, head, finish; DIN/ISO/ASTM/SAE cross-reference | No — no slots |
| Bearings / PT | Bore, OD, width, seal, clearance; manufacturer interchange | No — no slots |
| Automation & controls | Configuration and BOM compatibility | No — we do not configure |
| Fluid power | Dash size, thread standard (JIC/NPT/ORFS/BSPP), pressure rating | No — no slots |

**The eighth criterion the list is missing** is therefore: *can PIE read this
trade's compatibility requirement, or only its description text?* Add it and
the top six separates immediately — cutting tools stays at the top, industrial
and MRO holds because C5 was never its headline, and fasteners, bearings,
automation and fluid power drop to where §6 already puts them.

### Two criteria where we are stronger than the ICP implies

**C2 — changing purchase costs — is a detector we already ship.**
`signals/cost_pass_through.py` raises `COST_PASS_THROUGH`, and
`CI_COST_NOT_PASSED` exists at the customer×item grain. A vertical scoring 3 on
C2 is not just a good prospect; it is one where a named, built feature has
something to find on day one. **CODE.**

**C7 — margin leakage — is the whole of Control plus Retention**, and the
mechanism is documented in this segment's own literature: a rep discounts, the
discount becomes last price paid, the system defaults to it next time (§5).

### One criterion where we are weaker than the ICP implies

**C6 asks for "pricing history". We read realised prices, never quoted ones.**
Our connectors read `contacts`, `vendors`, `items`, `invoices`, `bills`,
`sales_orders`, `purchase_orders` and — on some systems — `customer_payments`.
`READ_STAGES` includes `quotes` and **no connector declares it** (§6, fact 2).

So on a fresh connection we can see every price that was *charged* and no price
that was *quoted and lost*. Everything about won/lost behaviour, quote-to-order
conversion and discount-at-the-point-of-quote is invisible until the customer
starts quoting inside PIE. A C6 score of 3 above should be read as "full
purchase and sales history, realised pricing only".

### What the ICP scoring changes

It does not move the page list — §8's gate is unchanged and the two firm pages
stand. It changes two things:

1. **It re-orders the roadmap in §11.** Fasteners scores a clean 21/21 against
   the owner's own ICP and is blocked by one specific, boundable change: slots
   in `CORE_SLOTS` and matching fields in `equivalence/distance.py`. That is a
   stronger argument for doing the fastener slot work first than anything in
   the previous draft, and it moves that item from "highest-value defer" to the
   thing most worth scheduling.
2. **It confirms Industrial & MRO as page one on a second, independent basis.**
   It is the only vertical scoring 3 on C1, C2, C3, C4, C6 and C7 whose *sole*
   2 is the one criterion we cannot serve outside metalworking anyway. Nothing
   is lost by its C5 score, because the page was never going to claim
   interchange.

---

## 5. Fit scoring — the commercial overlay

§4 scores the owner's ICP, which is entirely about the customer. This scores
what the ICP does not: how contested the vertical is, and whether the buyer can
pay. Read the two together — this one is not a second opinion on the same
question.

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

**The total is not the decision, and §6 is why.** Fasteners scores second and is
still deferred, because the thing that earns it a 5 on messiness is the thing we
cannot do. A score measures the size of the prize; §6 measures whether we can
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
and bearings score 5 on the trade's difficulty and are disqualified in §6, not
here.

**Ability to pay.** Mostly INFERRED. Anchors: P21 from ~$75/user/month (SEARCH);
the nearest adjacent tool at $399/month (SEARCH); Zoho Books India free below
₹25 lakh revenue, Professional ₹1,499/month, Ultimate ₹9,999/month (SEARCH) —
see §7 on why that matters. PTDA distributor members employ 39,211 people across
2,570 locations, which averages small (SEARCH), so bearings/PT is scored 3.

---

## 6. Reality check — what we actually serve

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
| **Control** | ✅ Full | ✅ Full | ⚠️ **Full on stocked components; unknown on fabricated assemblies** — see §8 |
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

## 7. Channel — where these distributors actually congregate

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

## 8. Recommendation

### The gate

Two conditions, both binary, both checkable:

1. **Is there real per-line pricing discretion?** Where a GPO contract or a
   weekly price file sets the price, the core loop has nothing to act on.
2. **Is the typical ERP one of the seven we read?** Where it is BisTrack or
   Amtech, we cannot read the book at all.

**Everything else is a disclosure on the page, not a reason to withhold it** —
rebate economics, cross-manufacturer interchange, a fabricated assembly that
carries no cost. That is the trade `/erp/sage-100` already makes, and §14
records why this document originally got it wrong.

The two conditions that used to sit here — true cost, and no dependence on an
unbuilt capability for the headline pain — were not deleted for convenience.
They are now what each page's `notServed` list is *for*, and
`industries.test.ts` requires the rebate disclosure on every page rather than
trusting an author to remember it.

### Build these

Seven, all live. The status column is what each page actually does, because a
page that partly serves a trade is the norm here rather than the exception.

| # | Vertical | What its page leads with | What it prints as a limit |
|---|---|---|---|
| 1 | **Industrial & MRO** | The last-price ratchet — a discount becomes the default nobody set | Rebates; no interchange outside metalworking |
| 2 | **Cutting tools & metalworking** | The twenty-minute cross-reference, and the floor on the line it resolves | Rebates; the parser carries no price or stock |
| 3 | **Fasteners** | Line count — nobody audits line 174 of a 200-line RFQ | **Cross-referencing, which is the trade's defining task** |
| 4 | **Bearings & power transmission** | Urgency — the price agreed on the phone while a plant is down | **Interchange, likewise the defining task** |
| 5 | **Fluid power, hose & fittings** | One floor across every branch | **A fabricated assembly may carry no cost — UNKNOWN, not a guess** |
| 6 | **Electrical** | Decline and dormancy, which need no cost at all | **SPA-claimed cost inflates the floor; the margin half is unproven here** |
| 7 | **Plumbing & PVF** | A bid priced against a cost that has since moved | Same SPA caveat on the branded half |

Three of those pages lead with a limit in the first two sentences, and the
electrical one is mostly about what does not work. That is deliberate and it is
`/erp/sage-100`'s design: a page for a reader we can half-serve is worth more
than no page, **provided the half we cannot do is the part they read first**.

The pages are held apart by a test rather than by intention —
`industries.test.ts` fails the build on pairwise sentence overlap above 25%, and
on a repeated title, description, lead or problem. Seven pages differing only in
a trade noun is the doorway pattern, and it arrives the ordinary way: page five
written by find-and-replace on page one.

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

### DEFER — the ERP gate, which is the one we cannot disclose our way past

| Vertical | Reason, and what would move it |
|---|---|
| **Packaging** | Converted product is quoted per specification, and the ERP base is industry-specific — Amtech, Advantive, ePS, CBS — rather than one of our seven. **Connector work before page work.** |
| **Building products / LBM** | Real negotiation and violent commodity movement, on BisTrack, DMSi Agility and similar. Same answer: a connector, then a page. |
| **Welding & gas** | Passes both hard gates on its hardgoods half, which page 1 already serves. Held because the other half is cylinder rental and gas contracts — recurring revenue and rental, a model nothing here represents. A page would be about half a company. |
| **Automation & controls** | Passes both gates, and the work that decides the price is configuration and BOM building, which happens before any line exists. Closest to the line of anything on this list; revisit if a distributor asks. |

**What is no longer a defer reason.** Rebate economics moved electrical and
plumbing onto the page list with a disclosure rather than off it, and the
absence of interchange did the same for fasteners and bearings. §14 has the
argument. Those pages exist because a reader who can be half-served is better
served by an honest page than by no page — and because withholding one was, on
inspection, a stricter rule than this repository applies to its own ERP pages.

### EXCLUDE — the discretion gate, and it is not close

| Vertical | Reason |
|---|---|
| **Lab & medical supply** | GPO and IDN contracts set price in tiers keyed to compliance percentage. The per-line decision this product acts on has been contracted away. |
| **Pharma** | WAC, contract and regulated pricing. No discretion, and a compliance surface we have no business near. |
| **Food service** | Weekly price files and cost-plus formulas, on food-service-specific ERPs. Both gates fail. |
| **JanSan** | Recurring contract replenishment; the discretion sits at annual negotiation, not on the line. |
| **Safety supply** | National accounts, GPO and vending programmes, and a segment where the nationals set the terms. Thin per-line discretion. |

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

## 9. Personas and pain — shortlist only

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
  the assembly-costing question in §8 may turn out to be the real one.
- **What they do today:** INFERRED — ERP reports and rep judgement.

---

## 10. Page outlines

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
page's list is drawn from §6 plus that vertical's own — and the Industrial/MRO
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

### Page 3 — `/industries/fluid-power` *(specified, not built)*

This outline is kept rather than deleted: it is what somebody builds the day
§8's question comes back favourably, and deleting it would lose the argument
along with the page. Nothing in it has been written into the site, and
`industries.test.ts` asserts the registry holds two entries — so adding a
third is a deliberate act somebody has to come and make.

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

## 11. What would turn a disclosure into a capability

Ordered by expected value. **This is no longer a list of what unlocks a page** —
§14 changed that, and four of the trades below already have one. It is now the
list of limits those pages currently print, ranked by what closing each is
worth.

1. **Fastener slots** — add thread, pitch, length, grade class, drive, head and
   finish to `CORE_SLOTS`, and the corresponding gate/dimension/soft fields in
   `pie-parser/equivalence/distance.py`. The fasteners page exists and leads its
   limits section with the admission that PIE will not cross-reference, which is
   the task that trade would name first. Closing this turns the weakest sentence
   on that page into its strongest. Best ERP overlap on the list — P21 names
   "Industrial & Fasteners" as a core vertical. An engine change in both
   repositories plus a nomenclature pack.
2. **A rebate / SPA cost layer** — a sourced adjustment between invoiced cost
   and effective cost, versioned like everything else, refusing to estimate
   where no agreement is on record. The electrical page currently tells its
   reader in the second sentence that the margin half of this product is
   unproven on their book; this is what retires that sentence, for electrical
   and plumbing/PVF together — the two densest P21 verticals. Largest build on
   this list and the largest unlock.
3. **Bearing slots** — bore, OD, width, seal, clearance. Same shape of change as
   (1), smaller audience, and the same relationship to its page: interchange is
   what that trade does all day and the page opens its limits with not doing it.

   Between (1) and (3) sits the question of whether `CORE_SLOTS` should stay one
   flat metalworking vocabulary at all, or become per-trade the way `packs/`
   already is for nomenclature. Adding two more trades' fields to one tuple is
   the point at which that stops being a refactor and starts being the design.
4. **The `quotes` read stage on any connector** — not a vertical unlock, but it
   is the denominator every Evidence claim currently lacks, on every page.
5. **Stock and customer payments from Prophet 21** — closes two of the seven
   gaps that every P21 page currently has to print.

---

## 12. What the second research pass changed

Recorded because a document that quietly overwrites its own conclusions is not
worth re-reading.

1. **A whole competitor category was missing.** The first pass scored only price
   optimisation (Zilliant, Vendavo, PROS, Pricefx, epaCUBE). Order and document
   automation — Conexiom, OrderPier, OrderDrafter, Workist, Esker — competes
   with the Speed capability and one of them sits in the P21 partner channel.
   §2 is new.
2. **Competitive density was the wrong axis.** It is far more a function of
   revenue band than of vertical: nothing in Category A is addressed below $50M.
   §3 is new and it re-scored the density column throughout §5.
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
8. **The owner's seven-point ICP was scored separately (§4), after the second
   pass.** It does not move the page list, and it sharpens the roadmap: the ICP
   does not discriminate among its top six verticals, the criterion that does
   is C5 (compatibility), and C5 is exactly where `CORE_SLOTS` runs out. The
   missing eighth criterion — *can PIE read this trade's compatibility
   requirement, or only its description text?* — separates the six.

---

## 13. Self-review

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

**Invariants:** none touched by the research. The build that followed respected
the one that was easy to miss — `worked-example.ts`'s deliberate vertical
neutrality applies to *shared* surfaces, so the landing page's example item is
untouched and each vertical page names its own trade through `DecisionCard`'s
`item` prop. `prerender.test.tsx` still holds the front page to naming no trade.

**Corpus / tests:** unchanged by the research itself. The pages it approved
were built afterwards and carry their own suites — `industries.test.ts` and
`roles.test.ts` — which hold the site to this document's gate rather than to
its prose.

**Verdict:** APPROVED WITH NOTED TRADE-OFF — two firm pages and one conditional,
against the three to five requested. The shortfall is deliberate; §8 gives the
gate that produced it and §11 gives the route to widening it. Two shipped; the
conditional one did not, which is the outcome this verdict described rather than
a departure from it.

---

## 14. Why the gate moved, after two pages had shipped

Recorded rather than edited into §8, because a decision record that quietly
rewrites its own reasoning to match the outcome is worth nothing.

**What the first two passes said.** Four conditions, the fourth being "no
dependence on a capability we have not built, *for the vertical's headline
pain*". That cut sixteen trades to two. Fasteners scored a clean 21/21 against
the owner's own ICP and was deferred anyway, because cross-referencing is what a
fastener distributor would name first if asked what hurts.

**What was wrong with it.** `/erp/sage-100` ships in this repository for a
system that reads **no purchase cost at all** — no floor, no margin, no drift,
which means the headline claim of the entire site is false on that book. It gets
a page: different `h1`, an honest lead, and `erp.test.ts` enforcing that it
*cannot* claim a floor. The established answer to "we serve part of this" is a
page with a different headline and the gap printed prominently. I applied a
stricter rule to trades than the repo applies to ERPs and did not notice.

**And it was already inconsistent.** The industrial & MRO page shipped carrying
the SPA disclosure, with a test requiring it on every page in the family. So
rebate-exposed readers were already being accepted with a disclosure, while
electrical — the same exposure, more of it — was excluded for having it.

**What replaced it.** Two hard gates, both binary (§8). Everything else is a
disclosure, and each page's `notServed` list is where it goes. Three of the five
new pages lead with their limit inside two sentences; the electrical page is
mostly about what does not work, and says so before it says anything else.

**What this does not change.** The excludes in §8 are unchanged and are now
cleaner, because they rest on the two gates alone: five trades have no per-line
discretion to act on, and two run on ERPs we cannot read. Those are not
disclosures. A page cannot honestly disclose its way past "we cannot connect to
your system" or "your prices are set by a contract you signed last year".

**The risk this created, and the guard on it.** Seven pages differing only in a
trade noun is a doorway-page pattern. `industries.test.ts` fails the build on
pairwise sentence overlap above 25% and on a repeated title, description, lead
or problem — with `notServed` deliberately outside the check, because two trades
genuinely share a limitation and rewording the same truth per page is how a
limits section turns into copy.
