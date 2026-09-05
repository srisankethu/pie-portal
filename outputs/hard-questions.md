# The ten questions most likely to kill this raise

Ordered by how much damage each does if it lands unanswered. For each: how an
investor actually phrases it, what they are really testing, the strongest honest
answer available today, and — where today's answer is weak — the specific evidence
to go and get.

Nothing here is softened. Four of these ten have no good answer yet, and they are
marked as such. A founder who knows which four is in a far better position than one
who has talked himself into believing there are none.

---

## 1. "You built this for your own distribution business. Is this a product, or is it your internal tooling with a pitch deck attached?"

**What they are testing.** Whether there is any demand signal that did not originate
inside your own head. You are the founder, the customer, the domain expert and the
only user. Every requirement was validated by asking yourself.

**The honest answer today.** It is internal tooling that has been *built* like a
product — multi-tenant with organization isolation, seven ERP connectors rather than
a Zoho integration, a connector-blind sync, a pack architecture with no manufacturer
knowledge in the engine, and 2,895 backend tests. Those are not choices you make for
a tool serving one company; they cost real time and buy nothing internally. But
architecture is not demand. There is no external user, no revenue, no design partner
and no pipeline, and the deck says so on sheet 13 rather than dressing it up.

**This is the weakest point in the raise.** The generalisation was engineered on
conviction, not evidence, and an investor is right to discount it.

**Go get.** Three unaffiliated distributors — ideally one not in cutting tools —
through a structured problem interview, and one of them onto the software in any
form, paid or not. A single external user who did not know you before changes this
answer completely. Until then expect the round to be priced as pre-product.

---

## 2. "Your own document says the decoded catalogue reaches 21.6% of your item master. Why is that not a fatal number?"

**What they are testing.** Whether you understand your own measurement, and whether
you disclose bad news before it is discovered. Volunteering this is a credibility
asset; being caught concealing it is fatal.

**The honest answer today.** The measurement is a complete census, not a sample:
15,028 items, weighted 23.5% by stock value, rising to 42.5% on stocked
Kennametal-labelled items. The gap is **corpus completeness, not engine capability**,
and the distinction is checkable. Of 6,717 catalogue records, every family parses at
100% with zero quarantine. The misses are whole product families the source price
file never contained — the adaptors and collets, for instance — plus the seven-odd
other manufacturers in the master: EMUGE FRANKEN, Renishaw, NOGA, Birla and others,
and 6,146 rows with no manufacturer recorded at all. Only 27% of the MM#-shaped
Kennametal SKUs in the master appear in the corpus. Point the same engine at the
remaining price files and coverage moves; nothing in the engine has to change, which
is the whole claim of the pack architecture.

**The part that genuinely hurts.** Grade reaches the platform only through an exact
identity link, because the item master has no grade field. Grade is the attribute
application engineering runs on. So the ambitious version of this product — advising
on application rather than matching a part number — is gated behind coverage in a way
that a coverage percentage alone understates.

**Go get.** Coverage re-measured after ingesting two more manufacturers' price files.
A curve from 21.6% with one pack to a materially higher number with three is the
single most persuasive artefact available to you, and it is a few weeks of work
against data you already have.

---

## 3. "Why can't Epicor, SAP, or Kennametal themselves ship this in two quarters?"

**What they are testing.** Whether you have thought about incumbents seriously or are
relying on them being slow.

**The honest answer today.** Each of them can build a piece and none of them wants
the whole. A manufacturer like Kennametal has perfect knowledge of *its own*
nomenclature and a structural disincentive to decode a competitor's — the value of
the equivalence layer to a distributor is precisely that it crosses brands, which is
the one thing a manufacturer will not do. An ERP vendor sells to every vertical and
will not fund a carbide-grade decoder; their product strategy is horizontal by
definition, and this problem is only tractable if you go deep on one category's
physics first. A PIM vendor sells the container, not the contents; PIM assumes
somebody supplies clean attributes, which is the exact work PIE does.

**The part that genuinely hurts.** None of this is a moat against a well-funded team
that simply decides to do it. It is an argument that the incumbents are
*misaligned*, not that the work is hard to copy once someone sees it is valuable.
Your protection is a head start on a specific artefact plus proximity to a real
distributor — real, but time-limited.

**Go get.** Nothing to research; this answer is already your strongest. Deliver it
crisply and move on.

---

## 4. "Does this scale, or do you hire a reverse engineer for every manufacturer forever?"

**What they are testing.** Whether the moat is an asset or a services business
wearing a software costume. This is the question that decides your gross margin.

**The honest answer today.** The architecture is designed for exactly this: the
engine contains zero manufacturer literals, enforced by an AST check in the build
gate rather than asserted in a README, so a new manufacturer is a new data directory
against unchanged code. The pack for the first manufacturer covers eleven families
and 6,717 rows, and the extension path is documented — routing rule, grammar,
patterns with self-testing examples, vocabulary, validators.

**The part that genuinely hurts.** *n = 1.* One pack exists. You have never built a
second, so you cannot answer the only question that matters: does pack two take 40%
of the time of pack one, or 90%? If it is 90%, this is a consultancy and the margin
profile is wrong. Every claim about horizontal expansion — the category ladder on
sheet 9, the entire moat argument on sheet 11 — rests on a number you have not
measured.

**Go get.** Build the second pack and *instrument the effort*. Hours, and where they
went. This is the highest-value engineering work available to you before a raise,
above any feature. A credible declining cost curve turns sheets 9 and 11 from
assertion into evidence.

---

## 5. "In eighteen months a frontier model will read a price list and extract all of this. What happens to your deterministic engine then?"

**What they are testing.** Whether your central architectural bet is a durable
insight or a temporary workaround for models that were not good enough yet.

**The honest answer today.** The bet is not that models cannot extract attributes.
It is that a distributor cannot send a customer a quote whose numbers nobody can
trace. The engine's value is that every field carries provenance, a confidence and
the character span it came from, that identical input reproduces identical bytes, and
that an unrecognised token is captured verbatim rather than guessed. That property is
required by the commercial context, not by the state of the art — a wrong corner
radius is a wrong insert on a customer's machine, and "the model said so" is not a
defence. A better model makes the ambiguous-intake layer better, which is a layer PIE
already has and deliberately keeps dashed.

**The sharper version of the same question, which you should raise yourself.** A
frontier model plus a verification pass could produce traceable output too. The
honest answer is that the deterministic pack is cheaper, faster and auditable by
construction, and that the durable asset is the *decoded structure*, not the code
that reads it. If models make building pack two dramatically cheaper, that is good
for you, not bad — provided the structure remains yours.

**Go get.** A measured comparison: the same 6,717 rows through a frontier model,
scored against the golden corpus for accuracy, cost and reproducibility. You have the
ground truth sitting there. This is a one-day experiment that either strengthens your
central claim or tells you something you urgently need to know.

---

## 6. "Who signs the cheque, for what budget line, and how much?"

**What they are testing.** Whether there is a business model or a set of guesses. Every
pricing line on sheet 12 is marked `[ASSUMPTION]` — investors will notice.

**The honest answer today.** You do not know, and the deck does not pretend to. The
structural argument is that this sits on a line distributors already fund — the cost
of the technical sales desk — rather than asking for new budget, and that the buyer is
the owner or commercial head rather than IT, because the pain is margin and speed
rather than infrastructure.

**The part that genuinely hurts.** No price has ever been quoted to anyone. You have
no willingness-to-pay data, no anchor, and no idea whether this is a ₹50k/month
product or a ₹5L/month one — a hundredfold uncertainty in the core unit of the
business.

**Go get.** Ten pricing conversations with real distributors, quoting a real number
and recording the reaction. Not "would you pay for this" — an actual proposal. The
answers will be worth more than another quarter of engineering.

---

## 7. "Why now? This problem has existed for thirty years."

**What they are testing.** Whether there is a real unlock or you simply got around to
it.

**The honest answer today.** Two things changed. Distributor ERPs became readable by
API across the systems these businesses actually run — the connector registry reads
NetSuite, Business Central, Acumatica, Prophet 21, Sage X3, Sage 100 and Zoho, and
that breadth was not available a decade ago without per-customer integration work
that destroyed the economics. And models became good enough to absorb the *ambiguous*
edge of an inbound request, which is the part that defeated every rules-only attempt
at this problem before — the reason those attempts died is that they had to be
complete to be useful, and now the deterministic core only has to be correct where it
speaks, with the messy intake handled above it.

**Assessment.** This is a solid answer. The connector half is verifiable in the
codebase, which makes it stronger than the usual hand-waving.

---

## 8. "These are conservative businesses that buy from people they know and have run the same process for twenty years. Can you actually sell to them?"

**What they are testing.** Founder–market fit on the *commercial* axis, not the
technical one. Plenty of technically excellent industrial software has died here.

**The honest answer today.** You are one of them. You run a distribution business
across three legal entities and you are inside the buyer's daily reality in a way an
outside founder cannot fake — which is also the fastest route to a first reference
customer and to credibility in a room of distributors.

**The part that genuinely hurts, and it is unresolved.** Being the customer is not
the same as being able to sell to the customer, and running a distributorship is not
evidence of being able to run a B2B software sales motion. There is no closed deal,
no letter of intent, and no named prospect. `[FOUNDER BACKGROUND — TO SUPPLY]` is
still blank in the brief, which means this answer currently rests on assertion.

**Go get.** Two things. First, write the founder line honestly, including any prior
software or commercial-sales experience — if there is none, say so and pair it with a
plan, because investors forgive a gap they can see and punish one they discover.
Second, use your position: you are a member of the buyer community, so convene a
handful of peer distributors. Five peers who will take a call is a distribution
advantage most seed founders do not have, and it directly answers this question.

---

## 9. "One country, one vertical, one company's data. How much of what you have learned actually transfers?"

**What they are testing.** Concentration risk, and whether the deck's
market-agnostic positioning is supported or aspirational.

**The honest answer today.** The transferable asset is deliberately separated from
the local one. Nomenclature grammars are properties of *manufacturers*, who are
global — a Kennametal code means the same thing in Hyderabad and Ohio — so the pack
work transfers intact. The commercial layer is where locality lives, and it is
already parameterised: thresholds are versioned per organization, equivalence bands
are read per request as policy, and the ERP layer is a connector registry rather than
a Zoho integration precisely so the platform is not shaped by one back office.

**The part that genuinely hurts.** The deck positions market-agnostically while
100% of the deployment evidence is one Indian distributor on Zoho. The six non-Zoho
connectors are built and tested but no US client has run one, so the strongest
transfer claim is architectural rather than demonstrated. Expect this to be probed
hard, and do not overclaim — an investor who finds the gap themselves will assume
there are others.

**Go get.** One connector exercised against a real instance of a non-Zoho ERP, even a
sandbox. It converts "we built connectors" into "we have read a live NetSuite
account", which is a different sentence.

---

## 10. "What happens if you get hit by a bus? And who else is on this?"

**What they are testing.** Key-person risk and whether an institution can underwrite
a single point of failure. Usually asked last and politely, and it kills more seed
rounds than it appears to.

**The honest answer today.** The codebase is unusually defended against exactly this.
The knowledge that would normally live in one engineer's head is externalised: the
invariants are written down and machine-enforced, the manufacturer knowledge is data
rather than code, the gate is a single script that CI, the Makefile and the local hook
all share, and the test suites are large enough to make change safe for someone who
did not write the original — 326 tests in the parser, 2,895 in the platform. A second
engineer can be productive here faster than in most codebases of this age.

**The part that genuinely hurts.** That is an argument about the code, not about the
company. There is no second person. The domain expertise that produced the
nomenclature specifications in the first place is genuinely concentrated in one head,
and no amount of test coverage substitutes for it.

**Go get.** Name your first two hires and what they cost — and be ready to say
whether this is a company you intend to run full-time, given that you are also
running a distribution business. Investors will ask that directly and an evasive
answer here is worse than an uncomfortable one.

---

## Where this leaves the raise

Questions 3, 5, 7 and 9 have good answers today and 9 only partly. Questions 1, 4, 6
and 8 do not, and they are the ones that decide the round.

Three pieces of evidence would move all four at once, and none needs new funding:

1. **Build the second manufacturer pack and measure the effort.** Answers 4 directly,
   strengthens 2 and 3.
2. **Ten real pricing conversations with unaffiliated distributors.** Answers 6 and
   materially weakens 1.
3. **One external user, on any terms.** Answers 1 and gives 8 its first real evidence.

The deck as written is honest about all of it, which is the right posture: at this
stage investors are pricing the founder's judgement, and visible placeholders where
the data is missing read as discipline. The risk is not that these gaps exist. It is
walking into the room without having decided what to say about them.
