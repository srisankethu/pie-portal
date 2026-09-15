/** What each trade's landing page says, and why every sentence of it is safe.
 *
 * These pages exist for the reader the `/erp/` pages cannot reach. That family
 * answers "will this work with the system I run"; a distributor who has not yet
 * decided whether a margin product is *for their trade at all* is asking a
 * different question, and a page that answers it in their own vocabulary is the
 * one they can act on. So the split is deliberate and it is a rule:
 *
 *   /erp/        — will this work with the system I run?
 *   /industries/ — will this work for the trade I'm in?
 *
 * No page in this family carries an ERP name in its title, description or `h1`,
 * because two pages competing for one query is two pages ranking for neither.
 * `industries.test.ts` holds that. An ERP name in the *body* is fine and now
 * required — see `erpLead`.
 *
 * **These pages are problem-first, and the field order below is the page
 * order.** That is the one structural rule, and it is worth stating because the
 * first version of this family broke it while looking correct: every page led
 * with the product's own decision card beside the `h1`, then named the trade's
 * problem in a single panel two screens down. A reader arriving from a search
 * for their own trouble met a screenshot of software. So:
 *
 *   `sub` → `wrong` → `cost` → `today` → `intervenes` → `worked` →
 *   `notServed` → `faq` → the closing ask
 *
 * `wrong` is the longest section on every page and is meant to be. A capability
 * is the answer to a problem that has already been stated; where the product
 * sections outweigh the problem sections the page has reverted to what it was.
 *
 * **The honesty rule is harder here than anywhere else on the site, and it is
 * the reason this file is mostly `notServed`.** An `/erp/` page's claims are
 * checkable against a connector module — `erp.test.ts` reads the source and
 * fails a claim the code does not implement. There is no equivalent oracle for
 * "this helps a fastener distributor". So the discipline has to come from the
 * other end: **every page prints what it does not do beside what it does**, and
 * the gate that decides whether a trade gets a page at all is two binary
 * conditions in `docs/vertical-strategy.md` §8 — is there real per-line pricing
 * discretion, and is the typical ERP one of the seven PIE reads. Everything
 * else is a disclosure rather than a reason to withhold the page, which is the
 * trade `/erp/sage-100` already makes for a book with no purchase cost at all.
 *
 * Seven trades have passed that gate and this file has seven entries.
 * `industries.test.ts` pins the list, so an eighth is a deliberate act somebody
 * came here to make rather than something that happened because a page looked
 * easy to copy. Two of the seven — fasteners and bearings — score at the top of
 * that document's own ICP table and cannot have the one thing their trade is
 * defined by: cross-manufacturer interchange. `CORE_SLOTS` in
 * `backend/app/decoding/schema.py` carries no thread, grade-class, bore or seal
 * slot and `pie-parser/equivalence/distance.py` gates on `iso_shape` and
 * `insert_polarity`. Those pages exist anyway and say so in their first two
 * sentences, because a reader who can be half-served is better served by an
 * honest page than by no page. Two of the other five lead with a limitation for
 * the same reason.
 *
 * The `speed` field is where that discipline is mechanical rather than
 * editorial — see its comment.
 *
 * **Anti-boilerplate.** Seven pages sharing one problem narrative with the
 * nouns swapped is duplicate content, and it arrives the ordinary way: page
 * five written by find-and-replace on page one. `industries.test.ts` fails the
 * build on pairwise sentence overlap above 25% across the narrative fields, and
 * on a repeated title, description, lead or first problem. `notServed` is
 * deliberately exempt: the rebate disclosure is the same fact on every book and
 * rewording one truth seven ways is how a limits section turns into copy.
 */

/** How far resolution actually goes on this trade's catalogue.
 *
 * Two values, and the difference between them is a named constant in another
 * repository rather than a matter of emphasis.
 *
 * `"decoded"` — the engine reads this trade's designations into typed slots and
 * ranks alternatives on the attributes it decoded. True for cutting tools and
 * for nothing else today: every slot in `CORE_SLOTS` is metalworking
 * (`iso_shape`, `chipbreaker`, `corner_radius_mm`, `flute_count`), the gate and
 * dimension fields in `pie-parser/equivalence/distance.py` are the same
 * vocabulary, and a fact the vocabulary has no name for is carried as an `ext:`
 * field which that module's own docstring says is **never compared**.
 *
 * `"matched"` — an enquiry line resolves against this company's own catalogue
 * by exact match and by nearest neighbour over the description text
 * (`backend/app/retrieval/`, a hashed n-gram embedder — deterministic, offline,
 * and indifferent to trade). That is genuinely useful and it is not
 * cross-referencing: it finds the line in *your* book, it does not propose an
 * equivalent from somebody else's.
 *
 * A page set to `"matched"` must not use the words "cross-reference",
 * "interchange" or "equivalent" about what PIE does — anywhere above its own
 * limits section, which is where those words belong and are disclaimed.
 * `industries.test.ts` checks the rendered markup for exactly that, because
 * this is the claim a marketing edit would widen without noticing it had.
 */
export type SpeedReach = "decoded" | "matched";

import { type FaqItem } from "./faq";

/** The trade's name where it is a *label* rather than part of a sentence.
 *
 * Derived from `short` rather than stored beside it, and that is the whole
 * point. Two fields would be two spellings of one trade, and the one that went
 * stale would be whichever the next author did not open — the failure
 * `shared.tsx` opens by describing, arriving as a capital letter instead of a
 * promise. One string, two renderings: `short` is the prose fragment ("on a
 * cutting tools book", which is correct English and stays lowercase), and this
 * is the chip, the breadcrumb, the nav link and the llms.txt entry.
 *
 * Capitalising the first character is the entire transform, and it is enough
 * because every acronym in this registry is already upper-case *inside* the
 * string — "industrial and MRO" becomes "Industrial and MRO", "plumbing and
 * PVF" becomes "Plumbing and PVF". A title-caser would have to know that MRO
 * and PVF are acronyms and that "and" is not a word to capitalise, which is
 * three rules where one will do; `industries.test.ts` pins all seven results so
 * a trade whose `short` does not survive this transform fails rather than ships
 * mis-cased.
 */
export function verticalLabel(page: Pick<IndustryPageData, "short">): string {
  return page.short.charAt(0).toUpperCase() + page.short.slice(1);
}

/** One step of the trail a trade page renders, and the same one its
 *  `BreadcrumbList` node restates.
 *
 *  Derived here rather than written in either place, because the site's
 *  standing rule is that schema may only restate what is on the page — the rule
 *  that kept `FAQPage` out of the graph until a page rendered a visible FAQ.
 *  `IndustryPage` renders this array and `scripts/prerender.mjs` builds the
 *  node from it, so a trail that changes shape cannot leave a schema node
 *  describing the old one.
 *
 *  The middle crumb is a real destination and not a label. `/#trades` is the
 *  front page's own "Written for your trade" strip, which is the index of this
 *  family; there is no `/industries` listing page, and a breadcrumb pointing at
 *  one would be the dangling cross-document link `shared.tsx` documents against
 *  itself — valid markup, scrolls to the top of the front page, looks like it
 *  worked. */
export interface Crumb {
  name: string;
  /** Absolute, for the reason every other link on a prerendered sub-page is:
   *  a bare `#trades` on this document is a fragment that goes nowhere. */
  path: string;
}

export function verticalTrail(page: IndustryPageData): Crumb[] {
  return [
    { name: "PIE", path: "/" },
    { name: "Trades", path: "/#trades" },
    { name: verticalLabel(page), path: `/industries/${page.slug}` },
  ];
}

/** One named, concrete thing that goes wrong in this trade.
 *
 *  Concrete is the whole requirement and it is not a style note: "margin
 *  erosion" is a category, and a reader cannot tell whether a page that says it
 *  has understood their business or has a thesaurus. The same part carrying
 *  four numbers across three suppliers is a thing that happened to them on
 *  Tuesday. `notes` are the specifics that make the claim checkable against
 *  their own week, and they are optional — a problem that needs none should not
 *  be padded with three. */
export interface Wrong {
  /** The eyebrow above the heading: "Problem one", and so on. Numbered rather
   *  than themed, because a theme is a claim about which of these matters most
   *  and this file does not know that about any particular book. */
  tag: string;
  title: string;
  body: string;
  notes: string[];
}

/** What distributors do about it today. The honest baseline, and it is honest
 *  in both directions — most of these are reasonable responses to a real
 *  constraint, and "nothing" is usually the most reasonable of them. A page
 *  that sneers at the spreadsheet is a page written by somebody who has never
 *  had to get a quote out by four o'clock. */
export interface TodayPractice {
  practice: string;
  body: string;
}

export interface IndustryPageData {
  /** The URL segment: `/industries/{slug}`. */
  slug: string;
  /** The trade, as its own people write it. */
  name: string;
  /** What fits in a sentence — lower-case, because that is where it is used:
   *  "on a {short} book". For the same trade as a label, see `verticalLabel`. */
  short: string;
  title: string;
  description: string;
  /** Above the `h1`. */
  eyebrow: string;
  /** The `h1`. It names the failure mode, not the product, and it carries the
   *  trade's own label — `industries.test.ts` holds both, because the first
   *  version of the cutting tools page led with "Quote without losing the
   *  margin in the cross-reference", which is true, well-formed, and never says
   *  what trade it is for. */
  headline: { lead: string; em: string; tail: string };
  /** One sentence under the `h1`, in this trade's vocabulary. One, not a
   *  paragraph: the hero's job is to let a reader recognise their own problem
   *  and keep reading, and the problems are the next section. */
  sub: string;
  /** Section 2 — three or four of them, and the longest thing on the page. */
  wrong: Wrong[];
  /** Section 3 — the mechanism by which the leak compounds, and why nothing
   *  catches it. No invented figures: the only numbers on these pages are the
   *  worked card's, which are labelled sample throughout. */
  cost: { lead: string; compounds: string; invisible: string };
  /** Section 4 — the baseline, stated plainly. */
  today: TodayPractice[];
  /** Section 5 — the exact workflow moment, one paragraph per capability that
   *  genuinely applies here. Deliberately variable in length: a capability that
   *  does not apply to a trade is omitted rather than stretched, which is why
   *  the electrical page has no margin-floor paragraph at all. */
  intervenes: { title: string; body: string }[];
  /** How far resolution goes here. See `SpeedReach`. */
  speed: SpeedReach;
  /** Section 6 — one quote line, start to finish, on sample figures. */
  worked: { lead: string; steps: { title: string; body: string }[] };
  /** The catalogue code the worked card carries, or `null` for the neutral one.
   *
   *  A trade-specific code here is not a violation of the landing page's
   *  neutrality rule but the point of it: `worked-example.ts` explains why a
   *  carbide designation on the *front* page tells a fastener distributor the
   *  product was built for somebody else, and that argument turns entirely on
   *  the front page being addressed to every trade at once. A page whose
   *  address names one trade may name it. See `DecisionCard` in `shared.tsx`.
   *
   *  `null` says this page has no single trade to name — which is true of
   *  industrial and MRO by definition, and is the same fact its own row in
   *  `docs/vertical-strategy.md` records as "multi-supplier, heterogeneous, no
   *  single nomenclature". It falls back to `EXAMPLE_ITEM`, the shared code
   *  that decodes to nothing in any trade. Naming a real designation there
   *  would be worse than neutral: a bearing or a fastener code on the
   *  industrial page would tell exactly the two trades whose interchange PIE
   *  cannot do that it can. */
  exampleItem: string | null;
  /** Section 7 — what PIE does not do on this trade's book. Stated, never
   *  softened, and never shorter than three items; the `/erp/` pages print
   *  seven and the reason given in `erp.ts` applies here word for word. */
  notServed: string[];
  /** Section 8 — rendered as a visible FAQ, and *therefore* emitted as
   *  `FAQPage` JSON-LD by `scripts/prerender.mjs`. That order matters and is
   *  the site's standing rule: schema may only restate what is on the page. */
  faq: FaqItem[];
  /** The one line above the ERP list: which of the seven this trade actually
   *  runs, and why.
   *
   *  It is a routing layer and not a second copy of the `/erp/` pages. Nothing
   *  here restates a permission list, a write capability or a gap — those are
   *  one page each, and a summary of them on seven trade pages is seven places
   *  for the same fact to go stale. What this line adds is the thing an `/erp/`
   *  page cannot say, because it is about the trade rather than the system:
   *  which book the reader is most likely to be on.
   *
   *  Two of these lines name a system PIE does **not** read, because on those
   *  two trades the commonest system in the trade is one of them and that is a
   *  hard gate rather than a caveat. A page that listed only what we connect to
   *  would be answering a question the reader did not ask. */
  erpLead: string;
  /** The systems this trade most often runs, most likely first — the ones
   *  `erpLead` names. Every trade page links all seven regardless; this decides
   *  which are drawn out of the list first. Slugs in `erp.ts`, and
   *  `industries.test.ts` holds every one against that registry, so a renamed
   *  ERP page cannot leave a dead link here. */
  erpSlugs: string[];
}

export const INDUSTRY_PAGES: IndustryPageData[] = [
  {
    slug: "industrial-mro",
    name: "Industrial and MRO distribution",
    short: "industrial and MRO",
    title:
      "Industrial and MRO distribution: the discount nobody decided | PIE",
    description:
      "A rep clears one line to hold an account, the discount is written down as "
      + "that customer's last price paid, and next quarter it is where the quote "
      + "starts. What that costs, what distributors do about it today, and the one "
      + "moment PIE puts a floor in front of it.",
    eyebrow: "For Industrial and MRO distributors",
    headline: {
      lead: "Industrial and MRO: the discount nobody decided becomes the ",
      em: "price everybody pays",
      tail: ".",
    },
    sub:
      "A rep clears one line to hold an account, the discount is recorded as that "
      + "customer's last price paid, and next quarter it is the number the quote "
      + "opens at — nobody decided that, and nothing in the process noticed.",
    wrong: [
      {
        tag: "Problem one",
        title: "Last price paid is a decision nobody made",
        body:
          "A rep under competitive pressure clears a line at a number that made "
          + "sense on that afternoon, for that order. It is written down as what "
          + "this customer pays. The next enquiry for the same item opens at that "
          + "figure rather than at your price, and the one after that opens a "
          + "little lower. No pricing meeting approved any of it, and by the time "
          + "the account is reviewed there is nothing left to review against."
          + " Ask the rep and they remember one order; ask the system and it reports a price. Neither is wrong, and between the two of them there is no version of events in which anybody chose this.",
        notes: [
          "Each discount is defensible on its own terms",
          "The record of it is indistinguishable from a policy",
          "The next quote inherits it as the default",
          "Nobody in the chain experiences it as a decision",
        ],
      },
      {
        tag: "Problem two",
        title: "One book, two pricing regimes, one report",
        body:
          "National accounts and contract customers are priced off agreements. "
          + "Spot, breakdown and non-contract business is quoted line by line with "
          + "real discretion. A margin report averages the two, so the half with "
          + "the discretion in it — the half that leaks — never appears as a number "
          + "of its own. The stable half is large enough to keep the total looking "
          + "reasonable."
          + " Which half a given line sits in is not a field anybody can filter on either, so even somebody who wanted to read the negotiated tail on its own would have to reconstruct it by hand from customer names.",
        notes: [
          "The contract half barely moves month to month",
          "The negotiated tail moves and is never reported separately",
          "A book-wide margin figure is the average of the two",
          "No field marks which regime a line was priced under",
        ],
      },
      {
        tag: "Problem three",
        title: "Discretion belongs to whoever answered the phone",
        body:
          "The same part leaves at three prices in a week because a counter, a "
          + "branch and an outside rep each priced it, and none of the three had "
          + "the other two's reasoning or the item's own history in front of them. "
          + "Each of them was doing the job as they understand it. There is no "
          + "written rule they were breaking, which is the actual problem."
          + " And the three of them will not find out. Nothing routes a price past anybody, so the first time the inconsistency surfaces it surfaces from the customer's side, as a question about which number is real.",
        notes: [
          "No two of those three see the same screen",
          "None of them sees what this customer last paid for it",
          "The customer often sees all three",
          "The inconsistency surfaces from the customer's side",
        ],
      },
      {
        tag: "Problem four",
        title: "The same part, carrying four numbers",
        body:
          "The book is multi-supplier with no shared nomenclature. One filter "
          + "arrives as your SKU, as the manufacturer's number, as whatever the "
          + "customer's maintenance system calls it, and as the string the last "
          + "person typed into the enquiry. Nothing ties a price history to a part "
          + "that has four names, so the question \"what do we usually get for "
          + "this\" has no reliable answer even when the data is all there."
          + " The practical effect is that the question nobody can answer is the easy-sounding one: has this customer bought this before, and at what. A desk that cannot answer it prices from the list.",
        notes: [
          "An enquiry rarely arrives under your own part number",
          "Price history is keyed to the item, not to the words",
          "A line matched to the wrong item inherits the wrong history",
          "\"Has this customer bought this before\" has no reliable answer",
        ],
      },
    ],
    cost: {
      lead:
        "Nothing on this page is a bad deal. Each one is a small decision made "
        + "correctly with what was on the screen at the time, and the cost is "
        + "entirely in how they accumulate.",
      compounds:
        "A discount becomes a recorded price, a recorded price becomes the next "
        + "quote's default, and a default is never re-argued because nobody "
        + "remembers it was once a concession. The floor under an account ratchets "
        + "in one direction only. Three years of that is not a discount any more; "
        + "it is a price list you did not write, agreed by nobody, and the first "
        + "person to notice is usually the one who inherits the account.",
      invisible:
        "A monthly margin report tells you this happened. It tells you after the "
        + "customer has the number, aggregated across a book whose contract half "
        + "is stable — which is why movement in the negotiated tail rarely clears "
        + "the noise. The transaction it describes closed weeks ago, and the "
        + "conversation it enables is about a price the customer already considers "
        + "theirs.",
    },
    today: [
      {
        practice: "A spreadsheet somebody maintains",
        body:
          "One workbook, owned by one person, holding the floors that matter, last "
          + "refreshed whenever they last had a quiet afternoon. It is usually "
          + "correct and it is never current, and it is not open on the screen of "
          + "the person typing the quote.",
      },
      {
        practice: "Tribal knowledge",
        body:
          "The sales manager knows which accounts have been given too much. They "
          + "know it as a feeling about a handful of names rather than as a list, "
          + "which is enough to act on and impossible to hand over. It leaves when "
          + "they do.",
      },
      {
        practice: "An ERP margin report",
        body:
          "Real numbers, correctly computed, delivered monthly against "
          + "transactions that have already closed. It answers what happened. That "
          + "is a useful question and it is not the one a quote desk has at four "
          + "o'clock.",
      },
      {
        practice: "Nothing at all",
        body:
          "The commonest answer, and not a careless one. Re-deriving a floor by "
          + "hand costs more time per line than most lines are worth, so it is not "
          + "done. That is arithmetic rather than negligence, and it is the reason "
          + "a check has to cost nothing per line to survive contact with a real "
          + "desk.",
      },
    ],
    intervenes: [
      {
        title: "The floor arrives before the quote does",
        body:
          "Your invoice and AP-invoice lines are what a floor is computed from: "
          + "what you sold, to whom, and what it cost. PIE checks the price typed "
          + "on each line against the policy you set, at the moment it is typed, "
          + "and a breach is held for a named approver rather than sent. The "
          + "platform holds it, not the salesperson, and the sign-off is "
          + "append-only and carries the version of the policy that was in force "
          + "when it was given.",
      },
      {
        title: "The enquiry, read into lines against your own book",
        body:
          "Paste a customer's enquiry as it arrived — a forwarded email, a line of "
          + "WhatsApp — and PIE reads it into quote lines and resolves each one "
          + "against the catalog you uploaded: an exact match where there is one, "
          + "the nearest neighbours by description otherwise, scored the same way "
          + "every time. It is deterministic and offline, so the same text resolves "
          + "the same way on every machine and no model is asked. It finds the line "
          + "in your book; it does not propose a substitute from another "
          + "manufacturer's. A line it cannot place stays unresolved and says so.",
      },
      {
        title: "The accounts going quiet, named before the renewal",
        body:
          "Four detectors run over the rows already synced — decline, dormancy, "
          + "margin deterioration and cost that was never passed through — plus "
          + "six more at the customer-and-item grain. Each one opens into the "
          + "figures that raised it, so the first thing you see is not a score but "
          + "the rows behind it.",
      },
      {
        title: "What it was worth, including when it was nothing",
        body:
          "The value ledger counts lines held to a floor and declines raised in "
          + "time, carrying the operands each figure came from. A month with no "
          + "detection reads UNKNOWN rather than zero, and what could not be "
          + "measured prints above what could — which is the only version of this "
          + "number worth having.",
      },
    ],
    speed: "matched",
    worked: {
      lead:
        "One line, from the enquiry arriving to the record that explains it a year "
        + "later. The card carries sample figures and the arithmetic under it is "
        + "the arithmetic the platform applies.",
      steps: [
        {
          title: "The line arrives as the customer wrote it",
          body:
            "An enquiry is pasted in and one of its lines resolves against your "
            + "catalog to the item on the card. The resolution records which route "
            + "found it — an exact match or a nearest neighbour — and the span of "
            + "text it read, so a wrong match is visible as a wrong match rather "
            + "than as a wrong price.",
        },
        {
          title: "A salesperson types the price the customer asked for",
          body:
            "The desk sees the floor, the recommended price and this customer's "
            + "own history for the item. It does not see cost and it does not see "
            + "margin: those fields are absent from the server's response rather "
            + "than hidden by the screen, and so is any rule whose boundary is cost.",
        },
        {
          title: "The policy answers, not a person",
          body:
            "Cost, divided by one minus the margin floor your policy sets. Nothing "
            + "is rounded in your favor — a floor rounded down is a floor that has "
            + "moved. The cost is the one on the AP-invoice line your ERP already "
            + "wrote. On the card, the price asked for is below the result.",
        },
        {
          title: "The quote does not send",
          body:
            "It routes to a sales manager with the rule that stopped it named, and "
            + "no individual owner: the platform holds the line, not the person who "
            + "typed it. An approver can clear it, and the clearing is a row rather "
            + "than a conversation.",
        },
        {
          title: "A year later it still explains itself",
          body:
            "The held line, the rule, the operands and the policy version in force "
            + "at sign-off are one record. When the customer asks why this year's "
            + "price differs from last year's, the answer is that record — and the "
            + "ledger credits the movement up to the floor and no further, because "
            + "clearing it by more than it asked for was somebody's judgement and "
            + "not something the guardrail did.",
        },
      ],
    },
    exampleItem: null,
    notServed: [
      "Special pricing agreements and manufacturer rebates are not modelled. PIE "
      + "computes margin from the cost on the AP-invoice line, so on a book where "
      + "a large share of purchases is claimed back afterwards, the floor it "
      + "computes sits above the floor you actually have — and it will hold lines "
      + "that are genuinely profitable. If that describes your book, say so on the "
      + "call rather than after the pilot.",
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until you start quoting here.",
      "Cross-manufacturer interchange is not offered outside metalworking. PIE "
      + "resolves an enquiry against your own catalog; it does not rank one "
      + "manufacturer's part as an equivalent for another's.",
      "Nothing resolves until a price list has been uploaded and its decoding "
      + "confirmed by a person. There is no default decoder and no shared "
      + "catalog — a company that has uploaded nothing resolves nothing.",
      "Stock levels and customer payments are not read from Prophet 21 in this "
      + "version, so the stock, GMROI and collections screens stay empty on a P21 "
      + "book.",
      "Salespeople are not imported from any ERP, so every approval routes to "
      + "management until accounts are assigned inside PIE.",
    ],
    faq: [
      {
        question: "Does PIE write anything back to my ERP?",
        answer:
          "On Prophet 21, Sage X3 and Sage 100, nothing — those connectors are "
          + "read-only and there is no method in them that creates a record. On "
          + "NetSuite, Acumatica, Dynamics 365 Business Central and Zoho Books, "
          + "the one thing PIE can create is the quote itself, and only if you "
          + "grant that permission separately. Everything else is read.",
        source: "erp.ts — each connector's `writes`; three of seven are null",
      },
      {
        question: "How far back does the first sync read?",
        answer:
          "The first pull is offered from the first day of the month 18 months "
          + "back, and you choose the date before it starts. It commits as it "
          + "goes, so you can watch it move — and the screens then report the span "
          + "the rows actually cover, not the window that was asked for.",
        source: "the `/erp/` pages' “What the first pull reads” panel; connections.DEFAULT_HISTORY_MONTHS",
      },
      {
        question: "Can a salesperson see cost or margin?",
        answer:
          "No, and not because a screen hides it. The server omits those fields "
          + "from the response, so there is nothing to read out of a network tab, "
          + "and a rule whose boundary is cost is withheld too and replaced with a "
          + "single approval-required marker. What the desk does get is the floor, "
          + "the recommended price and this customer's own history.",
        source: "the landing page's Who it's for section; CLAUDE.md §1, quote_service.project",
      },
      {
        question: "Does the AI set the prices?",
        answer:
          "No. Prices, margins, floors and thresholds are computed "
          + "deterministically from your persisted rows. A model may read those "
          + "numbers and phrase them; it never produces one. Turn the AI off "
          + "entirely and every figure on every screen still works.",
        source: "the determinism band on every public page",
      },
      {
        question: "We already have a pricing matrix in our ERP. Why add this?",
        answer:
          "A matrix decides what price to offer. PIE checks what was actually "
          + "typed on the line against the floor your policy sets, at the moment "
          + "it is typed, and holds a breach for a named approver. The two answer "
          + "different questions and PIE does not replace the matrix.",
        source: "this page's own “Where PIE intervenes” section",
      },
    ],
    erpLead:
      "Prophet 21 is what most of this trade runs — Epicor's own vertical list "
      + "names industrial and fasteners first — with Acumatica, Business Central "
      + "and NetSuite common at the mid-market end. PIE reads all seven the same "
      + "way; those four are simply the ones this trade asks about.",
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central", "netsuite"],
  },
  {
    slug: "cutting-tools",
    name: "Cutting tool and metalworking distribution",
    short: "cutting tools",
    title:
      "Cutting tools: the read-across and the price, decided together | PIE",
    description:
      "An enquiry names a competitor's designation. Twenty minutes later somebody "
      + "has found what you stock and put a price on it in the same breath. What "
      + "that costs, what desks do about it today, and where PIE separates the "
      + "technical decision from the commercial one.",
    eyebrow: "For Cutting tool and metalworking distributors",
    headline: {
      lead: "Cutting tools: the technical call and the commercial one get made in the same ",
      em: "twenty minutes",
      tail: ".",
    },
    sub:
      "A customer asks for a designation you do not sell, somebody spends twenty "
      + "minutes reading across to what you stock, and then prices it — two "
      + "different decisions made by one person at speed, and only one of them has "
      + "anything checking it.",
    wrong: [
      {
        tag: "Problem one",
        title: "The twenty-minute line",
        body:
          "A customer asks for something you do not stock, under a designation you "
          + "do not sell. Somebody opens a conversion guide, reads across to an ISO "
          + "application position, checks the edge length and the corner radius, "
          + "and forms a view. It is skilled work and it is correct most of the "
          + "time. It is also done one line at a time while the quote waits, and on "
          + "a twelve-line enquiry it is a morning."
          + " And twenty minutes is the good case. The bad case is the enquiry that waits until the one person who does this reliably is free, by which time the customer has an answer from somebody else.",
        notes: [
          "A published cross-reference is a set of pairwise claims, not a lookup",
          "Two grades at one application position are comparable, not identical",
          "Substrate, coating and edge preparation still differ between brands",
          "Twenty minutes is the good case; the queue is the bad one",
        ],
      },
      {
        tag: "Problem two",
        title: "One head, two decisions, one of them checked",
        body:
          "The person who decided the part is comparable also decides what it is "
          + "worth, in the same moment, with the same urgency. The technical "
          + "judgement gets reviewed by whoever queries the part later. The "
          + "commercial one is reviewed by nothing at all, because a substitution "
          + "is recorded as an ordinary sale of an ordinary item."
          + " So the review that would have caught a pricing error is a technical review, and it asks a technical question. Somebody checks that the part fits; nobody checks what it earned.",
        notes: [
          "Your ERP cannot tell a substituted line from a stock line",
          "So the substituted half of the book has no margin figure of its own",
          "The two decisions have different reviewers and one of them is nobody",
          "A technical review asks a technical question",
        ],
      },
      {
        tag: "Problem three",
        title: "Application knowledge sits with one or two people",
        body:
          "There is a person who can do this reliably, and on a good day there are "
          + "two. When they are on holiday the desk either guesses or the enquiry "
          + "waits until they are back. Both answers cost the order, at different "
          + "odds, and neither appears anywhere as a cost of anything."
          + " It is also why quoting capacity in this trade does not grow with headcount. Adding a salesperson adds somebody who can take the call, not somebody who can answer it.",
        notes: [
          "The queue behind that person is invisible in every system you own",
          "A guess made in their absence looks identical to their own work",
          "Nothing records which of the two answered a given line",
          "Quoting capacity does not grow when you add a salesperson",
        ],
      },
      {
        tag: "Problem four",
        title: "The reasoning goes home at five",
        body:
          "Three months on, a customer asks why they were quoted this part at this "
          + "price. The chart that was consulted, the position that was matched and "
          + "the judgement that closed the gap were never written down — they were "
          + "in somebody's head, and that person is on another call or at another "
          + "company. What you can reconstruct is the price."
          + " The same gap makes the work unteachable. A new hire can be shown the chart and cannot be shown the fifty judgements made around it, because none of them was written down at the time.",
        notes: [
          "The substitution decision leaves no record of its own",
          "Neither does the pricing decision that rode along with it",
          "A rebuilt catalog changes the answer and nothing says which edition answered",
          "And it cannot be taught, because it was never written down",
        ],
      },
    ],
    cost: {
      lead:
        "The loss is not a discount. It is that a substitution is a pricing event "
        + "and gets handled as a fulfilment one.",
      compounds:
        "A read-across lands on a part you stock and a number goes on it in the "
        + "same breath — very often the price of the designation the customer "
        + "named, or that price less something, because that is the figure in "
        + "front of the person deciding. The part you actually supply has its own "
        + "cost, and nothing in the moment compared the two. Repeat it for a year "
        + "and the substituted lines are a slice of the book whose margin nobody "
        + "has ever looked at separately, because nothing in the ERP marks them as "
        + "a slice.",
      invisible:
        "A win looks like a win. The enquiry was answered the same day, the "
        + "customer bought, the line shipped, and the only thing wrong with it is a "
        + "number that was never checked against anything. That is precisely the "
        + "shape of failure this platform is built around — a defensible answer "
        + "with nothing behind it — and it is why the technical half of what PIE "
        + "does refuses to guess as hard as the commercial half does.",
    },
    today: [
      {
        practice: "A conversion chart, printed",
        body:
          "A manufacturer's cross-reference sheet, usually a generation old, on a "
          + "wall or in a drawer. It answers the technical question at the "
          + "application position, which is the half it is for, and says nothing "
          + "whatever about what either part costs you.",
      },
      {
        practice: "The one person who knows",
        body:
          "An applications engineer, or an inside salesperson who has sold carbide "
          + "for twenty years. Right nearly every time, and a single point of "
          + "failure for both halves of the decision at once.",
      },
      {
        practice: "The price of the part they asked for",
        body:
          "The commonest shortcut, and an understandable one: quote the substitute "
          + "at the price of the designation the customer named, because that is "
          + "the number already on the screen. It is correct whenever the two cost "
          + "you the same, which is not often and is never checked.",
      },
      {
        practice: "A margin report, monthly",
        body:
          "Correct numbers, after the fact, averaged over a book in which the lines "
          + "you want to look at are not distinguishable from the lines you do not.",
      },
    ],
    intervenes: [
      {
        title: "Your designations, read rather than matched",
        body:
          "This is the one catalog the engine understands as more than text. ISO "
          + "designations and grade systems decode into typed fields — shape, "
          + "clearance, tolerance, edge length, thickness, corner radius, "
          + "chipbreaker, grade — each carrying provenance, a confidence and the "
          + "character span it was read from. An unrecognized token is captured "
          + "verbatim rather than guessed into a field, and identical input "
          + "produces byte-identical output.",
      },
      {
        title: "Alternatives ranked on what was decoded, not on what looks similar",
        body:
          "A shape or polarity mismatch excludes a candidate outright. Dimensions "
          + "are graded by distance. Coating, chipbreaker and flute count nudge and "
          + "never dominate. Where nothing discriminates between the candidates the "
          + "engine abstains and says so, rather than returning the least-bad one — "
          + "which is the answer your applications engineer would give, and the one "
          + "an order-automation tool cannot.",
      },
      {
        title: "A cross-reference stays a starting point",
        body:
          "A score here is policy under your own equivalence bands: true of this "
          + "quote, read per request, versioned, and never stored as a fact about "
          + "the two products. A scored suggestion is never promoted to a confirmed "
          + "identity, because an equivalence does not compose — two hops of a "
          + "tolerance band put a 0.4 mm corner radius and a 0.8 mm one in the same "
          + "class. Picking a different part is a substitution on one quote and "
          + "stays one.",
      },
      {
        title: "The floor, on the line you resolved",
        body:
          "Once the line resolves, the price typed on it is checked against the "
          + "policy you set, and a breach is held for a named approver with the "
          + "rule that stopped it named. The substitution and the price are two "
          + "records from here on, which is the whole point: one of them can be "
          + "questioned without reopening the other.",
      },
      {
        title: "Why you quoted what you quoted, last March",
        body:
          "Every emitted field carries where it came from. Every computed row "
          + "carries the version of the policy that judged it. And the catalog's "
          + "own ruleset version is in the record too, which is what explains why "
          + "the same text resolved differently before you rebuilt it — a rebuilt "
          + "catalog decoding differently is a fact, not a discrepancy.",
      },
    ],
    speed: "decoded",
    worked: {
      lead:
        "One line, from a competitor's designation arriving to the record that "
        + "explains it next March. Sample figures on the card; the arithmetic under "
        + "it is what the platform applies.",
      steps: [
        {
          title: "A designation you do not sell arrives",
          body:
            "The enquiry is pasted as it came. The engine decodes the code into "
            + "typed fields — shape, clearance, edge length, thickness, corner "
            + "radius, grade — each with the span of characters it was read from, "
            + "and captures verbatim anything it could not name.",
        },
        {
          title: "Candidates are ranked, or refused",
          body:
            "Your own decoded catalog is scored against the request. A shape or "
            + "polarity mismatch drops a candidate; dimensions are graded by "
            + "distance; coating and chipbreaker nudge. Every comparison's left "
            + "operand is the request, never another candidate. If nothing "
            + "discriminates, the engine says so and the line waits for a person.",
        },
        {
          title: "A person picks, and the pick stays a pick",
          body:
            "The applications engineer confirms one candidate for this quote. That "
            + "is a substitution on one quote, recorded as one; it does not become "
            + "an asserted identity between two products, and nothing downstream "
            + "will resolve a later enquiry off it.",
        },
        {
          title: "The price on the resolved line meets the floor",
          body:
            "Cost, divided by one minus your margin floor, on the part you are "
            + "actually supplying rather than the one the customer named. On the "
            + "card the price asked for is below it, so the line routes to a manager "
            + "with the rule that stopped it named. The desk never sees the cost.",
        },
        {
          title: "Next March, the whole chain is one record",
          body:
            "The decode and its spans, the candidates and their scores, the person "
            + "who picked, the policy version that judged the price and the catalog "
            + "ruleset version that produced the decode. The question is answerable "
            + "without the person who answered it the first time.",
        },
      ],
    },
    exampleItem: "CNMG 120408",
    notServed: [
      "Special pricing agreements and manufacturer rebates are not modelled. "
      + "Margin is computed from the cost on the AP-invoice line and nothing is "
      + "claimed back afterwards.",
      "The resolution engine carries no price and no stock. It deals in "
      + "nomenclature only; availability, cost and list price are read from your "
      + "books afterwards and keyed by the manufacturer part number it returned.",
      "Nothing resolves until you have uploaded a price list per manufacturer you "
      + "sell and confirmed how each one is read. The corpus that ships with the "
      + "engine is a seed for the first company, not a catalog of your book.",
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until you start quoting here.",
      "An equivalence is never composed. PIE will not reason from one scored "
      + "match to a second — every comparison is against the request, because two "
      + "hops of a tolerance band put a 0.4 mm corner radius and a 0.8 mm one in "
      + "the same class.",
    ],
    faq: [
      {
        question: "Which manufacturers' nomenclature does PIE decode?",
        answer:
          "Whichever ones you upload a price list for. Each list is analysed on "
          + "its own to find the shapes of description it contains, a person "
          + "confirms what the varying parts mean, and the catalog is built from "
          + "that. There is no shipped list of supported brands and no default "
          + "decoder — a decoder that guessed would be a wrong number with a real "
          + "provenance stamp on it.",
        source: "this page's intervention section; app/decoding/ has no default decoder",
      },
      {
        question: "Will it pick a substitute for me?",
        answer:
          "It will rank candidates on the attributes it decoded and show which "
          + "field contributed what. It will not decide. Where nothing "
          + "discriminates it abstains, and a scored suggestion is never written "
          + "down as an identity — that stays a person's call, which is also how "
          + "your applications engineer would want it.",
        source: "this page's “a cross-reference stays a starting point” paragraph",
      },
      {
        question: "Can a salesperson see cost or margin?",
        answer:
          "No. The server omits those fields from the response rather than the "
          + "screen hiding them, and a rule whose boundary is cost is withheld too. "
          + "The desk gets the floor, the recommended price and this customer's own "
          + "history — enough to negotiate, without the cost basis.",
        source: "the landing page's Who it's for section; CLAUDE.md §1, quote_service.project",
      },
      {
        question: "Does the AI read the enquiry?",
        answer:
          "The resolution does not. It is a deterministic parser with a versioned "
          + "rule set, and identical input produces identical bytes — which is what "
          + "makes a resolution auditable months later. A model may phrase what was "
          + "found; it never decides what was found and never produces a number.",
        source: "the determinism band; pie-parser resolves before any model is asked",
      },
      {
        question: "What happens when we rebuild the catalog?",
        answer:
          "Resolutions carry the catalog's ruleset version, so a line that "
          + "resolved differently before the rebuild can say which edition answered "
          + "it. That is deliberate: a rebuilt catalog decoding differently is "
          + "exactly the fact that explains an old answer.",
        source: "this page's fifth intervention paragraph — the ruleset version is in the record",
      },
    ],
    erpLead:
      "Prophet 21 runs most of this trade as well, though Epicor files it under "
      + "industrial rather than naming it — with Business Central and Acumatica "
      + "common in the mid-market and Zoho Books at the small end. The decoding is "
      + "identical on all seven; what changes is what each book can tell you "
      + "afterwards.",
    erpSlugs: ["prophet-21", "dynamics-365-business-central", "acumatica", "zoho-books"],
  },
  {
    slug: "fasteners",
    name: "Fastener distribution",
    short: "fasteners",
    title: "Fasteners: nobody audits line 174 of a 200-line RFQ | PIE",
    description:
      "The first dozen lines of a large fastener RFQ are priced carefully and the "
      + "rest at speed. What that costs, why a bottom-line review cannot find it, "
      + "and the one check that costs the same on line 200 as on line 1 — with the "
      + "read-across PIE cannot do stated first.",
    eyebrow: "For Fastener distributors",
    headline: {
      lead: "Fasteners: nobody audits line ",
      em: "174",
      tail: " of a two-hundred-line RFQ.",
    },
    sub:
      "The first dozen lines get priced carefully and the rest at speed, because "
      + "the quote has to go back today and no desk re-derives two hundred floors "
      + "by hand.",
    wrong: [
      {
        tag: "Problem one",
        title: "Nobody audits line 174",
        body:
          "A large RFQ is priced the way large RFQs are always priced: the first "
          + "dozen lines with attention, the rest from memory or from what the "
          + "account paid last time. That is not carelessness, it is arithmetic — "
          + "the quote has to go back today, and the lines that get the least "
          + "attention are the ones individually too small to be worth any."
          + " The order is usually won, too, which is what makes this hard to raise internally: the fast half of the pricing does not look like it cost anything until somebody totals the lines it produced.",
        notes: [
          "The economics of this trade are line count, not line value",
          "A per-line check by hand costs more than the line it checks",
          "So the leak is on the small lines, by construction",
          "The order is usually won, which makes it hard to argue about",
        ],
      },
      {
        tag: "Problem two",
        title: "Every line starts with a translation",
        body:
          "The customer quotes from a print, from an obsolete internal code, or "
          + "from a competitor's part number. Almost never from yours. Before a "
          + "price can exist somebody has decided which of your items the line "
          + "means, and on line 174 that decision is made at exactly the speed the "
          + "price is. Get it wrong and the line inherits the wrong item's history "
          + "as well as the wrong cost."
          + " And a mistranslation is invisible at quote time. It looks exactly like a correct line — a part number, a description, a price — and surfaces weeks later as a return, a shortage, or a customer saying this is not what they asked for.",
        notes: [
          "A print callout is a specification, not an identifier",
          "An obsolete code may match two current items",
          "The translation and the price are decided in the same second",
          "A mistranslation looks exactly like a correct line",
        ],
      },
      {
        tag: "Problem three",
        title: "A blanket agreement priced against last year's steel",
        body:
          "The repeat lines sit under an agreement signed when the wire cost what "
          + "it cost then. The agreement does not move when your cost does, and "
          + "nothing compares the two until somebody opens the account at a "
          + "year-end review. This is the commonest single loss on this book and it "
          + "is not a pricing mistake — it is the absence of anybody watching a "
          + "pair of numbers drift."
          + " The review, when it comes, is also the wrong instrument: it covers a year of drift in one conversation, so the ask is a single renegotiation rather than the several small corrections that would each have been easy to justify.",
        notes: [
          "An agreed price is fixed; your purchase cost is not",
          "The pair only matters per customer and per item",
          "Nothing in the quoting flow reads your purchase history",
          "A year of drift arrives as one renegotiation",
        ],
      },
      {
        tag: "Problem four",
        title: "Per-print specials, priced off the last one",
        body:
          "A non-stock line is sourced by hand and priced from whatever the "
          + "previous one landed at — freight and minimum-order quantity included "
          + "or not, depending on who remembered. There is no catalog entry to "
          + "check it against, because there is no catalog entry."
          + " Over time the reference drifts a long way from anything real, because every special is priced from the last one and each of those was priced from the one before it.",
        notes: [
          "The reference price is a memory of one prior transaction",
          "Freight and MOQ are in it or not, unrecorded either way",
          "It is outside anything a catalog check can reach",
          "Each special is priced from the last, which was priced from the one before",
        ],
      },
    ],
    cost: {
      lead:
        "The lines that leak are the ones nobody would defend if they were asked "
        + "about them individually.",
      compounds:
        "Each under-priced line on a two-hundred-line quote is too small to argue "
        + "about. Together they are the margin on the order — and the order is a "
        + "template. The customer sends the same RFQ next quarter, your system "
        + "offers last quarter's prices as the starting point, and the lines that "
        + "were wrong the first time are now the reference for the second. The "
        + "error does not decay with time. It becomes the record, and then it "
        + "becomes the agreement.",
      invisible:
        "A quote this size is reviewed as a total, if it is reviewed at all, and "
        + "the total is fine. A bottom line cannot show a line-level problem; that "
        + "is what a bottom line is for. Only a per-line check finds this, and a "
        + "per-line check by hand is exactly what nobody has time for — which is "
        + "why the answer has to be a check that costs nothing per line rather "
        + "than a discipline somebody is asked to keep.",
    },
    today: [
      {
        practice: "A spreadsheet of floors",
        body:
          "Exported once, annotated by hand, out of date the following week. In "
          + "practice it is consulted for the first page of the RFQ and then the "
          + "quote has to go.",
      },
      {
        practice: "Cost plus a habit",
        body:
          "A multiplier per product group, applied from memory, unchanged for "
          + "years and never re-checked against the current landed cost. It was "
          + "right when it was set, and the cost it was set against has moved "
          + "several times since.",
      },
      {
        practice: "A review of the total",
        body:
          "The sales manager looks at the bottom line before it goes. It is the "
          + "one number on the document that cannot reveal the problem, and "
          + "reviewing it feels like control.",
      },
      {
        practice: "Nothing, on the small lines",
        body:
          "Said plainly because it is the honest and usually correct answer: two "
          + "hundred lines is more checking than the order will bear, so the check "
          + "is not made. Anybody who has priced one of these knows why.",
      },
    ],
    intervenes: [
      {
        title: "The same policy on line 200 as on line 1",
        body:
          "The floor is arithmetic on the AP-invoice line your ERP already wrote, "
          + "so applying it to the two-hundredth line costs what it cost on the "
          + "first. A breach is held for a named approver rather than sent — the "
          + "platform holds it, not the person who typed it — and the hold names "
          + "the rule that stopped it. This is the one capability whose value grows "
          + "with the size of the quote rather than shrinking.",
      },
      {
        title: "The RFQ, read into lines against your own book",
        body:
          "Paste the enquiry as it arrived and PIE reads it into lines, resolving "
          + "each against the catalog you uploaded — an exact match where there is "
          + "one, the nearest neighbours by description otherwise, scored the same "
          + "way every time. On a book of distributor part numbers and clean "
          + "descriptions that is usually the whole job. It finds the line in your "
          + "own book and will not read a competitor's number across to yours; a "
          + "line it cannot place stays unresolved and says so, rather than being "
          + "matched to the nearest thing on the shelf.",
      },
      {
        title: "The pairs where your price stopped following your cost",
        body:
          "The pass-through detector reads your own purchase and invoice history "
          + "and names the customer-and-item pairs where your cost rose and your "
          + "price did not. On a book run off blanket agreements that is the "
          + "detector that matters, and it needs nothing you do not already have in "
          + "the ERP.",
      },
      {
        title: "An order you can explain a year later",
        body:
          "Every held line records the rule that stopped it and the version of the "
          + "policy in force when somebody signed. When a customer asks why this "
          + "year's price differs from last year's on one line of two hundred, the "
          + "answer is a row rather than a recollection.",
      },
    ],
    speed: "matched",
    worked: {
      lead:
        "One line out of two hundred — the one nobody would have looked at. Sample "
        + "figures on the card; the arithmetic under it is what the platform "
        + "applies to every other line at the same cost.",
      steps: [
        {
          title: "Line 174 resolves",
          body:
            "The RFQ is pasted as it arrived. This line carries the customer's own "
            + "description rather than your part number, and it resolves against "
            + "your catalog by nearest neighbour over the description text, "
            + "deterministically. The route it took and the text it read are both on "
            + "the record.",
        },
        {
          title: "It is priced in four seconds, like the other 199",
          body:
            "Nobody slows down for it and nothing asks them to. The desk sees the "
            + "floor for the item, the recommended price and what this customer has "
            + "paid for it before. It does not see cost or margin.",
        },
        {
          title: "The floor is computed anyway",
          body:
            "Cost, divided by one minus the margin floor your policy sets, on the "
            + "AP-invoice cost your ERP already holds. This costs the platform "
            + "nothing on the 174th line, which is the only reason the check exists "
            + "at all on a quote this shape.",
        },
        {
          title: "One line of two hundred does not send",
          body:
            "The quote is held on that line, for a named approver, with the rule "
            + "named. Not the whole quote flagged for review and not a warning the "
            + "desk can dismiss — one line, one decision, one person who can make it.",
        },
        {
          title: "Next quarter's RFQ does not inherit it",
          body:
            "Because it was held rather than sent, the number that becomes this "
            + "customer's reference is the approved one. The ledger credits the "
            + "movement up to the floor and no further, and a month in which it "
            + "found nothing reads UNKNOWN rather than zero.",
        },
      ],
    },
    exampleItem: "HHCS 1/2-13 x 2 GR8 ZP",
    notServed: [
      "Cross-referencing is not offered. Reading a customer's print, an obsolete "
      + "code or a competitor's part number across to yours is the defining task "
      + "of this trade and PIE does not do it: the engine decodes thread, grade "
      + "class, drive and finish for no catalog, so it can find a line in your own "
      + "book by description and cannot propose an equivalent. If that is the "
      + "capability you are shopping for, this is not it yet.",
      "Per-print and non-stock specials are outside the catalog entirely. A line "
      + "resolves against what you have uploaded, or it stays unresolved.",
      "Special pricing agreements and manufacturer rebates are not modelled. "
      + "Margin is computed from the cost on the AP-invoice line, so where a "
      + "large share of purchases is claimed back afterwards the floor sits above "
      + "the one you actually have.",
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until you start quoting here.",
      "Kitting and VMI replenishment are not modelled. PIE prices a quote; it "
      + "does not run a bin.",
    ],
    faq: [
      {
        question: "Our RFQs run to hundreds of lines. Does that break anything?",
        answer:
          "No — that is the case the per-line check is for. The floor is "
          + "arithmetic on a cost your ERP already holds, so it costs the same on "
          + "line 200 as on line 1. What changes with size is the value of having "
          + "it: nobody hand-checks two hundred lines, which is why the leak is "
          + "there.",
        source: "this page's first problem; commercial/ computes the floor per line",
      },
      {
        question: "Will PIE cross-reference a competitor's part number?",
        answer:
          "No. It resolves an enquiry against your own catalog by exact match and "
          + "by description, and it will not read one manufacturer's number across "
          + "to another's. The attribute vocabulary the engine ranks on is "
          + "metalworking, and thread, grade class and drive are not in it.",
        source: "this page's limits section; app/decoding/schema.py CORE_SLOTS",
      },
      {
        question: "Can a salesperson see cost or margin?",
        answer:
          "No. The server omits those fields from the response rather than the "
          + "screen hiding them, and a rule whose boundary is cost is withheld too. "
          + "The desk gets the floor, the recommended price and this customer's own "
          + "history.",
        source: "/roles/salesperson; CLAUDE.md §1, quote_service.project",
      },
      {
        question: "We price most lines off a blanket agreement. Is there anything here?",
        answer:
          "Yes, and it is probably the main thing. An agreed price does not move "
          + "when your cost does, so the detector that matters on this book is "
          + "cost-not-passed-through at the customer-and-item grain — it reads your "
          + "own purchase history and names the pairs that drifted.",
        source: "signals/cost_pass_through.py and commercial/detectors.py",
      },
    ],
    erpLead:
      "Prophet 21, more than on any other page in this family: Epicor names this "
      + "trade in its own vertical list, as \"Industrial & Fasteners\". Acumatica "
      + "and Business Central appear at the mid-market end. All seven are read the "
      + "same way, and each system's page says what it cannot see.",
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "bearings-power-transmission",
    name: "Bearing and power transmission distribution",
    short: "bearings and power transmission",
    title:
      "Bearings and power transmission: the price agreed while a plant is down | PIE",
    description:
      "Breakdown business is quoted verbally in ninety seconds against a tier "
      + "nobody re-reads, and the concession becomes the rate. What that costs, "
      + "what desks do about it today, and where PIE puts the floor — with the "
      + "read-across it cannot do stated first.",
    eyebrow: "For Bearings and power transmission distributors",
    headline: {
      lead: "Bearings and power transmission: the price gets agreed in ninety seconds while a ",
      em: "plant is down",
      tail: ".",
    },
    sub:
      "A stopped line is the best negotiating position your customer will ever "
      + "have, and both of you know it while the number is being said out loud.",
    wrong: [
      {
        tag: "Problem one",
        title: "The price agreed on the phone",
        body:
          "Breakdown business is quoted verbally and confirmed afterwards. There is "
          + "no considered moment between the question and the number — no screen "
          + "consulted, no second opinion, no pause that the customer would read as "
          + "anything other than hesitation. Whatever is said becomes the price, and "
          + "the paperwork follows it rather than the other way round."
          + " It is also the transaction this trade is best at, which is why nobody wants a process in front of it — and why any control that slows the call down will be worked around by the people it is meant to help.",
        notes: [
          "The commitment is made before any record exists",
          "The confirming document is written to match what was said",
          "Nothing that happens later can be a check on it",
          "A control that slows the call down gets worked around",
        ],
      },
      {
        tag: "Problem two",
        title: "The tier nobody reads while a plant is down",
        body:
          "The account sits on a tiered price, and that tier is the single least "
          + "likely thing in your system to be consulted under this pressure. It "
          + "was set against volumes that have changed and costs that have moved, "
          + "quite possibly by somebody who has left, and nobody on the call has a "
          + "reason to believe it is current."
          + " So the tier ends up describing the account's history rather than governing its present, and the longer it goes unreviewed the less anybody trusts it, which makes the next person less likely again to consult it.",
        notes: [
          "Tiers decay quietly — volumes change, costs move",
          "The band an account sits in is rarely re-derived",
          "Under pressure, the tier is a document rather than a constraint",
          "An unreviewed tier is one nobody consults, which keeps it unreviewed",
        ],
      },
      {
        tag: "Problem three",
        title: "The exception becomes the rate",
        body:
          "A price given once under pressure is recorded in the same field as a "
          + "price that was negotiated calmly, and read back the same way. Six "
          + "months later the plant is running, the enquiry is routine, and the "
          + "number the desk starts from is the one that was given at two in the "
          + "morning. The next emergency starts from there again."
          + " The concession travels, too. A customer helped out once will reasonably expect the same treatment, and the desk has no way to say that the last price was exceptional — because the record does not say so either.",
        notes: [
          "There is no field that says \"this was urgent\"",
          "So an emergency and an agreement are indistinguishable afterwards",
          "Each subsequent concession compounds on the last one",
          "The customer will reasonably expect it again",
        ],
      },
      {
        tag: "Problem four",
        title: "Knowing what will fit is the job, and it is one person's job",
        body:
          "A customer names one manufacturer's designation and expects you to know "
          + "what on your shelf will do. Bearing designations are rigidly "
          + "structured, so this is learnable — and in most distributors it has "
          + "been learned by one or two people, who are the queue every urgent "
          + "enquiry waits in. When they are unavailable the enquiry is answered by "
          + "somebody less sure, at the same speed."
          + " And the pressure sits on the wrong half of the decision. The urgent question is technical — will this fit — so that is what gets the attention, and the price is whatever is said at the end of the sentence.",
        notes: [
          "Bore, outside diameter, width, seal and clearance all have to agree",
          "The designation encodes them and reading it is a skill, not a lookup",
          "PIE does not have this skill — see the limits below",
          "The technical question gets the attention; the price gets the end of the sentence",
        ],
      },
    ],
    cost: {
      lead:
        "The cost is not the concession. It is that the concession has no expiry "
        + "date on it.",
      compounds:
        "An emergency price is usually a correct decision: the alternative was "
        + "losing the order and possibly the relationship, and a distributor who "
        + "will not move on a stopped line does not keep breakdown business. What "
        + "makes it expensive is that it is stored as a price rather than as an "
        + "exception, so it is read back as the account's standing position. The "
        + "next routine enquiry opens there. The next emergency discounts from "
        + "there. Two years of that and the tier the account is nominally on "
        + "describes nobody.",
      invisible:
        "None of this appears as an exception, because nothing recorded it as one. "
        + "A tier review a year later sees an account whose realized margin no "
        + "longer resembles its band, with no way to tell whether that was "
        + "negotiated, conceded at three in the morning, or simply drifted — and no "
        + "way to tell which lines were which. The review either re-tiers everybody "
        + "or nobody.",
    },
    today: [
      {
        practice: "The tier table",
        body:
          "Correct as a structure, and the thing least likely to be open on the "
          + "screen at the moment it would matter. It answers what the account is "
          + "nominally on, which is a different question from what this line should "
          + "be.",
      },
      {
        practice: "The person who remembers the deal",
        body:
          "Somebody knows that this customer was given something once, under "
          + "circumstances, and roughly what. That memory is genuinely load-bearing "
          + "and it is not a record — it cannot be queried, handed over, or "
          + "distinguished from a policy by anyone else.",
      },
      {
        practice: "A margin report, after the month closes",
        body:
          "Real numbers, correctly computed, describing a set of decisions that "
          + "were all made in under two minutes each and are now invoiced.",
      },
      {
        practice: "Nothing — it is an emergency",
        body:
          "The honest baseline, and a defensible one. Checking is friction, "
          + "friction is time, and the customer is standing next to a stopped line. "
          + "The only kind of check that survives this is one that is already on "
          + "the screen when the number is typed.",
      },
    ],
    intervenes: [
      {
        title: "A floor that is already there when the price is agreed",
        body:
          "Computed from the AP-invoice line your ERP already wrote, and applied at "
          + "the moment the line is priced rather than in a review afterwards. A "
          + "breach is held for somebody with the authority to sign it, with the "
          + "rule that stopped it named — which is faster than the alternative of "
          + "unwinding the price later, and is the only intervention that fits "
          + "inside a ninety-second call.",
      },
      {
        title: "An emergency price that stays an emergency price",
        body:
          "Every held line is recorded with the policy version in force when it was "
          + "signed, append-only. A concession made on a Tuesday because a plant "
          + "was down is a row somebody can point at — rather than the number the "
          + "account has quietly been on ever since. This is the field your system "
          + "does not have today.",
      },
      {
        title: "The tier that stopped matching the account",
        body:
          "The customer-and-item detectors read your own history and name the pairs "
          + "where realized margin no longer resembles the band the account sits "
          + "in, and where your cost moved and your price did not. A re-tiering "
          + "conversation starts from named pairs instead of from a whole-account "
          + "average.",
      },
      {
        title: "The enquiry, resolved against your own book",
        body:
          "Paste what the customer sent and PIE reads it into lines, resolving each "
          + "against the catalog you uploaded by exact match and, failing that, by "
          + "nearest neighbour over the description text — deterministically, with "
          + "no model asked. It finds the line in your own book. Knowing what on "
          + "your shelf will do instead of a designation you do not stock is this "
          + "trade's central skill and PIE does not have it; a line it cannot place "
          + "says so rather than being matched to whatever was closest.",
      },
    ],
    speed: "matched",
    worked: {
      lead:
        "One urgent line, from the phone call to the row that explains it at the "
        + "next tier review. Sample figures on the card.",
      steps: [
        {
          title: "The call comes in and the line is entered",
          body:
            "Somebody types the enquiry while the customer is still on the phone. It "
            + "resolves against your catalog to the item on the card — exact match, "
            + "on a book of structured designations, most of the time.",
        },
        {
          title: "The price the customer is asking for goes in",
          body:
            "The desk sees the floor for the item, the recommended price and what "
            + "this account has actually paid for it before — which is the number "
            + "that is usually missing from this conversation. No cost, no margin: "
            + "those are absent from the response rather than hidden by the screen.",
        },
        {
          title: "The floor is on the screen before the sentence ends",
          body:
            "Cost, divided by one minus your margin floor. Nothing is rounded in "
            + "your favor. On the card the asked-for price is below it, and the "
            + "person on the phone knows that while they are still talking rather "
            + "than at month end.",
        },
        {
          title: "It routes, and it routes fast",
          body:
            "The line is held for somebody who can sign it, with the rule named. "
            + "That is not a delay in place of a sale — it is a two-minute decision "
            + "by the person whose decision it actually is, instead of a two-year "
            + "price set by whoever answered the phone.",
        },
        {
          title: "The next review can tell the difference",
          body:
            "The concession, the rule, the operands and the policy version are one "
            + "append-only row. At the next tier review this line is legible as an "
            + "emergency, which means the tier can be left alone.",
        },
      ],
    },
    exampleItem: "6205-2RS C3",
    notServed: [
      "Interchange is not offered, and on this book that is the largest thing PIE "
      + "does not do. A customer naming one manufacturer's designation and "
      + "expecting an equivalent from what you stock is the defining transaction "
      + "of the trade; the engine carries no bore, outside-diameter, width, seal "
      + "or clearance field, so it can find a line in your own book and cannot "
      + "read across to it.",
      "Special pricing agreements and manufacturer rebates are not modelled, so "
      + "where purchases are claimed back afterwards the computed floor sits above "
      + "your real one.",
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until you start quoting here.",
      "Nothing resolves until a price list has been uploaded and its decoding "
      + "confirmed by a person. There is no default decoder and no shared catalog.",
      "Stock levels are not read from Prophet 21 in this version, so on a P21 book "
      + "the availability half of an urgent enquiry is not something PIE can "
      + "answer.",
    ],
    faq: [
      {
        question: "Will PIE tell me the equivalent for a competitor's bearing number?",
        answer:
          "No. That is the honest answer and it is the first thing to know about "
          + "this page. The engine ranks candidates on decoded attributes, and its "
          + "attribute vocabulary is metalworking — there is no bore, no width, no "
          + "seal type in it. It resolves an enquiry against your own catalog by "
          + "description; it does not read one manufacturer across to another.",
        source: "this page's limits section; pie-parser equivalence/distance.py",
      },
      {
        question: "Most of our business is breakdown. Is a floor check realistic?",
        answer:
          "That is the case it is for. The check is arithmetic on a cost already "
          + "in your ERP, so it is available the moment the line is priced rather "
          + "than after a review. A breach routes to somebody who can sign it, "
          + "which is faster than the alternative of unwinding it later.",
        source: "this page's first problem and its worked line",
      },
      {
        question: "We price off customer tiers. What does this add?",
        answer:
          "A tier decides the offer; the floor checks what was actually typed. "
          + "They answer different questions, and the gap between them is where "
          + "breakdown pricing lives. PIE does not replace your tiers.",
        source: "the same distinction /industries/industrial-mro draws for pricing matrices",
      },
      {
        question: "Can a salesperson see cost or margin?",
        answer:
          "No. Those fields are absent from the response the server sends, not "
          + "hidden by a screen, and a rule whose boundary is cost is withheld with "
          + "them. The desk gets the floor, the recommended price and the "
          + "account's own history.",
        source: "/roles/salesperson; CLAUDE.md §1, quote_service.project",
      },
    ],
    erpLead:
      "Prophet 21, with Acumatica and Business Central common at the mid-market "
      + "end — the same three systems industrial and MRO runs, which is what you "
      + "would expect of a trade that often sits inside the same branch network. "
      + "All seven are read the same way.",
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "fluid-power",
    name: "Fluid power, hose and fitting distribution",
    short: "fluid power",
    title:
      "Fluid power: two counters, two prices, and an assembly with no cost | PIE",
    description:
      "The same hose assembly quoted a week apart at two branches, at prices that "
      + "are not close — and the fabricated half of the book, where the margin is, "
      + "often carries no cost at all. What that costs, and why PIE answers UNKNOWN "
      + "there rather than estimating.",
    eyebrow: "For Fluid power, hose and fitting distributors",
    headline: {
      lead: "Fluid power: the same assembly quoted twice at two counters, at prices that are not ",
      em: "close",
      tail: ".",
    },
    sub:
      "Counter pricing is local by nature, and the margin in this trade is on the "
      + "crimping bench rather than the shelf — which is the half your ERP is least "
      + "likely to hold a cost for.",
    wrong: [
      {
        tag: "Problem one",
        title: "Two branches, two prices, one customer",
        body:
          "The same assembly quoted a week apart at two counters, at prices that "
          + "are not close. Neither counter did anything wrong: there was no floor "
          + "in front of either of them at the moment they typed, and no reason for "
          + "one to know what the other had said. Each branch has its own habits, "
          + "its own long-standing accounts and its own idea of what a job is worth, "
          + "and none of that is written down anywhere the others can read."
          + " Each branch is also right that it knows its own market better than a central list would, and that is what makes this hard rather than easy: the local judgement is real, and so is the inconsistency it produces.",
        notes: [
          "Neither counter can see the other's quote history",
          "Neither is breaking a written rule, because there isn't one",
          "The customer is frequently the one who notices",
          "The local judgement is real, and so is the inconsistency",
        ],
      },
      {
        tag: "Problem two",
        title: "It gets settled by honouring the lower number",
        body:
          "What follows the discovery is not a pricing conversation but a "
          + "credibility one, and it is usually settled the quick way: the lower "
          + "price is applied across the account. So the cheapest quote any branch "
          + "has ever given becomes the price everywhere, and the branch that was "
          + "pricing correctly has its number replaced by the one that was not."
          + " Nobody records that this happened, either. The new price is simply the price, and the reason it is the price — a credibility problem at one counter eighteen months ago — is attached to it nowhere.",
        notes: [
          "The resolution is a concession made to protect trust, not margin",
          "It propagates to every branch at once",
          "And it is now the reference for the next quote at all of them",
          "Nothing records why the account is on that price",
        ],
      },
      {
        tag: "Problem three",
        title: "The margin is on the bench, and the bench has no cost line",
        body:
          "A fabricated hose assembly is where the spread in this trade actually "
          + "is — the crimping bench rather than the shelf. But an assembly built "
          + "to order at the counter very often carries no item-level cost in the "
          + "ERP at all, only its components do. So the half of the business that "
          + "earns the most is the half nothing can check, this year or any year."
          + " So the decision this trade most needs to make — whether the service capability earns what it costs to run — is the one the ERP cannot inform, in either direction. Nobody knows whether the bench is the profit or the subsidy.",
        notes: [
          "Components are costed; the made-up assembly frequently is not",
          "Labour and crimp time are rarely on the line at all",
          "A margin figure over this half averages real numbers with absences",
          "Nobody knows whether the bench is the profit or the subsidy",
        ],
      },
      {
        tag: "Problem four",
        title: "A spec and a price, decided in one breath",
        body:
          "A counter picks the hose, the two ends and the length, and puts a number "
          + "on the result. Which components that number assumed is not recorded, so "
          + "the same assembly is not comparable to itself next month — let alone to "
          + "the version the other branch built. There is nothing to reconcile "
          + "because there is nothing written down to reconcile against."
          + " It also means two quotes for the same job can both be right and still differ. Without the component list behind each number, a comparison between them is a comparison of two things that were never the same.",
        notes: [
          "The assembly is a decision, not a catalog item",
          "Two builds of \"the same\" assembly may differ in components",
          "So a price comparison between them is not actually a comparison",
          "Two right quotes for one job, not comparable to each other",
        ],
      },
    ],
    cost: {
      lead:
        "Two separate leaks, and only one of them is a pricing decision. They need "
        + "different answers and one of them PIE cannot give.",
      compounds:
        "The first compounds through the account. An inconsistency the customer "
        + "finds resets the whole relationship to the lowest number in it, that "
        + "number becomes the reference at every branch, and the next inconsistency "
        + "resets it again from there. The second does not compound at all — it is "
        + "simply invisible. Where an assembly carries no cost there is nothing to "
        + "compare a price to, so the question \"are we making money on hose "
        + "assembly\" has never been answered on your book, in either direction.",
      invisible:
        "The honest answer to the second one is UNKNOWN, and this platform will "
        + "give you that answer rather than a figure. A cost estimated from "
        + "components would be a plausible number with a real provenance stamp on "
        + "it — worse than no number, because the screens would look complete and "
        + "a decision would get made off them.",
    },
    today: [
      {
        practice: "A branch price list",
        body:
          "Real, and usually respected for stocked components. It has nothing to "
          + "say about an assembly that does not exist until somebody builds it, "
          + "which is where the money is.",
      },
      {
        practice: "The counter's judgement",
        body:
          "Somebody who has built this assembly a hundred times knows roughly what "
          + "it should go out at. That is genuine expertise, it is correct more "
          + "often than not, and it is different at every branch by a margin nobody "
          + "has measured.",
      },
      {
        practice: "A cost built by hand, sometimes",
        body:
          "On a large job somebody adds up the components and adds something for "
          + "the bench. It is the right answer and it takes ten minutes, so it "
          + "happens on the jobs big enough to justify it and not on the ones that "
          + "make up the volume.",
      },
      {
        practice: "Nothing, on assemblies",
        body:
          "Stated plainly: for most made-up lines there is no check, because there "
          + "is no cost to check against. This is a data problem before it is a "
          + "pricing problem, and any tool that claims otherwise is estimating.",
      },
    ],
    intervenes: [
      {
        title: "One policy, however many counters",
        body:
          "The floor is computed from the AP-invoice line your ERP already wrote "
          + "and applied identically at every branch, at the moment the line is "
          + "priced. A breach routes to a named approver, so the exception is "
          + "visible centrally while it is being asked for rather than discovered in "
          + "a margin report — and the branch that prices correctly stops being the "
          + "one whose number gets overwritten.",
      },
      {
        title: "Where there is no cost, the answer is UNKNOWN",
        body:
          "A fabricated assembly that carries no item-level cost gets no estimated "
          + "one. PIE reports the line as unmeasured and says so on every screen "
          + "that would otherwise have used the figure, and a month in which it "
          + "could not measure reads UNKNOWN rather than zero. This is the same rule "
          + "that governs a book with no purchase cost anywhere, and on this trade "
          + "it is the most important sentence on the page.",
      },
      {
        title: "Accounts drifting away from one branch",
        body:
          "Decline and dormancy are computed from invoice history and need no cost "
          + "at all, so they work on the assembly half of the book exactly as well "
          + "as on the stocked half. A customer quietly moving their hose business "
          + "is visible before the renewal conversation rather than during it.",
      },
      {
        title: "The enquiry, resolved against your own book",
        body:
          "Paste what arrived and PIE reads it into lines, resolving each against "
          + "the catalog you uploaded — exact match first, nearest neighbour over "
          + "the description otherwise. It is deterministic, so two branches pasting "
          + "the same text get the same answer, which is a small thing that this "
          + "trade does not currently have. It finds the line in your own book and "
          + "proposes no substitute from another manufacturer's.",
      },
    ],
    speed: "matched",
    worked: {
      lead:
        "One stocked component line, because that is the half a floor can speak "
        + "to. What happens to the assembly line beside it is step five, and it is "
        + "the honest half of this example.",
      steps: [
        {
          title: "The enquiry arrives at one counter",
          body:
            "It is pasted as it came and read into lines. The component line "
            + "resolves against your catalog to the item on the card; the assembly "
            + "line on the same enquiry resolves too, and carries no cost.",
        },
        {
          title: "The counter prices the component line",
          body:
            "They see the floor, the recommended price and what this customer has "
            + "paid before — the same three figures the other branch would see for "
            + "the same item, computed from the same policy. Not cost, and not "
            + "margin.",
        },
        {
          title: "The floor answers, identically at every branch",
          body:
            "Cost, divided by one minus the margin floor your policy sets, on the "
            + "AP-invoice cost your ERP already wrote. On the card the asked-for "
            + "price is below it. The policy is one policy — that is the whole "
            + "intervention on this trade's stocked half.",
        },
        {
          title: "The line is held, centrally and visibly",
          body:
            "It routes to a named approver with the rule named, and the hold is "
            + "visible to whoever owns pricing rather than to the branch alone. If "
            + "it is cleared, the clearing is an append-only row carrying the policy "
            + "version in force.",
        },
        {
          title: "And the assembly line says UNKNOWN",
          body:
            "No floor, no margin, no estimate. It is reported as unmeasured, on "
            + "this screen and on every other one that would have used the figure. "
            + "That is the correct answer and it is also the limitation: on a book "
            + "where most of the value is fabricated, most of this page's first "
            + "intervention does not apply. Settle that on the call before anything "
            + "else.",
        },
      ],
    },
    exampleItem: "1/2 in R2AT x 36 in JIC-JIC",
    notServed: [
      "A fabricated assembly usually carries no item-level cost, and PIE will not "
      + "invent one. On the half of this trade where the margin actually sits — "
      + "hose assembly, not components off the shelf — the floor may have nothing "
      + "to check, and the honest answer is UNKNOWN rather than a number. Ask "
      + "about this on the call before anything else; it decides whether the rest "
      + "of the page is worth having.",
      "Configuration is not modelled. PIE prices a line; it does not build a "
      + "specification, check a pressure rating or validate that two ends fit.",
      "Special pricing agreements and manufacturer rebates are not modelled, so "
      + "where purchases are claimed back the computed floor sits above your real "
      + "one.",
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until you start quoting here.",
      "Nothing resolves until a price list has been uploaded and its decoding "
      + "confirmed by a person.",
    ],
    faq: [
      {
        question: "Our assemblies are built at the counter. Will PIE price them?",
        answer:
          "It will check whatever price is typed against a floor — if your ERP "
          + "holds a cost for that assembly. Where it does not, PIE reports the "
          + "line as UNKNOWN rather than estimating a cost from components. This "
          + "is the question worth settling first, because on many fluid power "
          + "books the assembly half is where the margin is.",
        source: "this page's limits section; CLAUDE.md §1, absence of evidence is not a pass",
      },
      {
        question: "Does one policy work across branches with different customers?",
        answer:
          "The policy sets floors; it does not set prices. Two branches serving "
          + "different accounts still quote differently — what they no longer do "
          + "is quote below a floor without somebody named signing for it.",
        source: "this page's first problem; commercial/policy.py is owner-editable",
      },
      {
        question: "What does PIE do to my ERP?",
        answer:
          "Reads it. On Prophet 21, Sage X3 and Sage 100 it cannot write at all; "
          + "on NetSuite, Acumatica, Dynamics 365 Business Central and Zoho Books "
          + "the one record it can create is the quote, and only with a permission "
          + "granted separately for that.",
        source: "the `/erp/` pages' permission lists; each connector's declared writes",
      },
      {
        question: "Can a salesperson see cost or margin?",
        answer:
          "No. The server omits those fields, so there is nothing to read out of a "
          + "network tab, and a rule whose boundary is cost is withheld too.",
        source: "/roles/salesperson; CLAUDE.md §1, quote_service.project",
      },
    ],
    erpLead:
      "Prophet 21 — Epicor names fluid power in its own vertical list — with "
      + "Business Central and Acumatica at the mid-market end. Worth reading your "
      + "system's page for one reason in particular on this trade: whether it holds "
      + "a cost on a made-up assembly decides how much of this page applies to you.",
    erpSlugs: ["prophet-21", "dynamics-365-business-central", "acumatica"],
  },
  {
    slug: "electrical",
    name: "Electrical distribution",
    short: "electrical",
    title:
      "Electrical: the invoiced cost is not your cost, so the floor is wrong | PIE",
    description:
      "Where purchases are claimed back through special pricing agreements, a "
      + "margin floor computed from invoiced cost sits above your real one and "
      + "holds profitable lines. This page leads with that, and then with the half "
      + "that needs no cost at all.",
    eyebrow: "For Electrical distributors",
    headline: {
      lead: "Electrical: the cost on your AP invoice is not your ",
      em: "cost",
      tail: ", so a floor computed from it is not your floor.",
    },
    sub:
      "This page leads with a limitation because it decides everything after it: "
      + "where purchases are claimed back through special pricing agreements, a "
      + "floor computed from invoiced cost sits above your real one and will hold "
      + "lines that are perfectly profitable.",
    wrong: [
      {
        tag: "Problem one",
        title: "The invoiced cost is the wrong number",
        body:
          "Under ship-and-debit and claimback mechanics you buy at standard cost "
          + "and claim the difference afterwards. The AP-invoice line — the only "
          + "cost PIE reads, and the only cost most systems record against the "
          + "purchase — is therefore higher than what the product actually cost you. "
          + "Everything computed from it inherits that error in a known direction."
          + " Worse, it is wrong by a different amount per manufacturer and per agreement, so there is no single correction anybody could apply. The error has a structure, and the structure lives in a document nobody opens at quote time.",
        notes: [
          "The rebate arrives after the customer has been invoiced",
          "Nothing in the purchase record anticipates it",
          "So cost is overstated on exactly the lines that matter most",
          "Wrong by a different amount per manufacturer and per agreement",
        ],
      },
      {
        tag: "Problem two",
        title: "Which means the failure is over-holding, not under-selling",
        body:
          "A floor is cost divided by one minus your margin floor, so an inflated "
          + "cost gives an inflated floor. On an agreement-heavy book a control "
          + "built this way objects to lines that are genuinely profitable — and a "
          + "control that is wrong often enough gets overridden as a matter of "
          + "routine within a month. After that it is dead on the lines where it was "
          + "right, which is worse than never having had it."
          + " That is a specific and unusual failure: the product does not lose you money, it loses you the control. And it does the damage fastest on your largest accounts, because those are the ones the agreements cover.",
        notes: [
          "The error is systematic, not random, and points one way",
          "An override habit is learned quickly and unlearned slowly",
          "A floor the desk ignores costs the same to run and catches nothing",
          "It costs you the control rather than the money",
        ],
      },
      {
        tag: "Problem three",
        title: "Accounts do not resign, they reduce",
        body:
          "A contractor prices elsewhere on one job, then on two, and the line items "
          + "you used to see stop appearing while the relationship carries on looking "
          + "entirely fine. Nobody reports it because nothing happened — there was no "
          + "complaint, no lost tender, no conversation. By the time it is visible in "
          + "a total it is a year old and the competitor is established."
          + " It is also the half of this problem your book can act on, which is why it sits here rather than in the limits: the evidence for a reduction is the invoices, and the invoices are complete.",
        notes: [
          "There is no event to report, only an absence",
          "A total hides it until the absence is large",
          "Detection needs no cost at all, which is why it works on this book",
          "The evidence for a reduction is the invoices, and those are complete",
        ],
      },
      {
        tag: "Problem four",
        title: "Nothing in the quote knows which lines were claimable",
        body:
          "The agreement is administered after the invoice, in a different system or "
          + "a different spreadsheet, by somebody who is not the person quoting. So "
          + "the person deciding a price does not know whether this line is one the "
          + "agreement covers, and a legitimate claim that nobody files is margin you "
          + "earned and did not collect."
          + " So the desk's own instinct about which lines are worth having is trained on the wrong number, month after month, and it becomes more confident rather than more accurate.",
        notes: [
          "The quoting desk and the claims desk are different people",
          "They work from different records, at different times",
          "PIE does not join them — see the limits below",
          "The desk's instinct is trained on the wrong number",
        ],
      },
    ],
    cost: {
      lead:
        "Two mechanisms pointing in opposite directions, and it matters a great "
        + "deal which one your book is exposed to.",
      compounds:
        "A quiet decline compounds the ordinary way: the conversation you eventually "
        + "have is a retrieval rather than a save. The rebate exposure compounds "
        + "differently and faster, because what it destroys is not margin but the "
        + "control itself. Every wrong hold teaches the desk that the floor is noise. "
        + "Twenty of them and the override is reflexive, and the one line a month "
        + "that genuinely needed stopping goes out with all the others.",
      invisible:
        "This is the exact shape of failure the platform's own rules exist to "
        + "prevent, which is why the page states it rather than selling around it: a "
        + "number computed correctly from the wrong input is indistinguishable from a "
        + "number computed correctly. It carries the same provenance, the same policy "
        + "version and the same audit trail as a right answer.",
    },
    today: [
      {
        practice: "A rebate spreadsheet owned by finance",
        body:
          "Accurate, reconciled monthly or quarterly, and not available to the "
          + "person quoting. It is the real cost record in the building, and it is "
          + "not in the quoting flow.",
      },
      {
        practice: "The agreement letters, in a folder",
        body:
          "The terms exist in writing, per manufacturer and per job. Finding out "
          + "whether this line falls under one takes longer than quoting the line "
          + "does, so mostly it is not looked up.",
      },
      {
        practice: "A margin report that is wrong in a known direction",
        body:
          "Everybody who reads it knows to mentally add something back for rebates. "
          + "That adjustment is a habit rather than a figure, it differs per reader, "
          + "and it is applied to a whole-book average.",
      },
      {
        practice: "Nothing, on decline",
        body:
          "The retention half usually has no process at all — not because it is "
          + "hard, but because nothing raises it. An account that reduces produces no "
          + "event for anyone to respond to.",
      },
    ],
    intervenes: [
      {
        title: "Decline and dormancy, which need no cost",
        body:
          "Four detectors run over invoice history alone — decline, dormancy, margin "
          + "deterioration and cost that was never passed through — plus six at the "
          + "customer-and-item grain. Each opens into the rows that raised it. The "
          + "first two need no purchase cost whatsoever to be right, which is why "
          + "this is the half of the product that works on your book today and the "
          + "half the page leads with.",
      },
      {
        title: "Where your price stopped following your cost",
        body:
          "Even on an agreement-heavy book the *direction* of your invoiced cost is "
          + "real: when it rose and your price did not, the pass-through detector "
          + "names the customer-and-item pairs. It is the rebate-independent half of "
          + "the margin question, and usually the actionable half, because the answer "
          + "is a price conversation rather than a claim.",
      },
      {
        title: "The enquiry, resolved against your own book",
        body:
          "Paste a takeoff or an emailed enquiry and PIE reads it into lines, "
          + "resolving each against the catalog you uploaded by exact match and by "
          + "nearest neighbour over the description. On a book of clean manufacturer "
          + "catalog numbers the exact match usually carries it, and no model is "
          + "asked what a line is. It finds the line in your own book and proposes no "
          + "substitute from another manufacturer's.",
      },
      {
        title: "And not a margin floor, on this book, today",
        body:
          "This is the paragraph that is missing on purpose. Every other trade page "
          + "in this family leads with a per-line floor; on an agreement-heavy "
          + "electrical book that floor is computed from a cost that is not your "
          + "cost, so the platform can enforce whatever policy you set and cannot "
          + "promise the policy means what you think it means. Whatever floor you do "
          + "set, the sign-off on a breach is append-only and carries the version of "
          + "the policy in force when it was given — so editing the policy later does "
          + "not make last quarter's decisions unreadable. That is the whole claim.",
      },
    ],
    speed: "matched",
    worked: {
      lead:
        "Not a floor, on this page. The worked example is a declining account, "
        + "because that is the half of the product your book can actually support. "
        + "The card below is drawn from a floor decision and is shown for what it "
        + "is — sample figures, and the mechanism this trade cannot yet trust.",
      steps: [
        {
          title: "The rows are already there",
          body:
            "Your invoices synced on the first pull — eighteen months by default, "
            + "earlier if you set an earlier date. Nothing here needs a purchase "
            + "cost, a rebate record or a price book.",
        },
        {
          title: "A detector reads one account's history",
          body:
            "Deterministic arithmetic against thresholds you set: this contractor's "
            + "monthly line count and revenue against their own prior pattern, per "
            + "item as well as in total. A model is never asked whether something is "
            + "worth raising.",
        },
        {
          title: "It raises the reduction, not the total",
          body:
            "The account's revenue is down a little and its mix is down a lot — the "
            + "items they stopped buying are named, and the signal opens into the "
            + "rows that produced it rather than into a score.",
        },
        {
          title: "Somebody has the conversation while it is a save",
          body:
            "Months earlier than a year-end review, and armed with what stopped "
            + "rather than with a feeling that something has. Whether the "
            + "conversation works is your business; having it in time is the "
            + "product's.",
        },
        {
          title: "And the floor stays off, until the rebate layer exists",
          body:
            "The card's arithmetic is real and its figures are samples. On your book "
            + "the cost operand is overstated, so the floor it produces is too high "
            + "and the platform would hold profitable lines. This page will not "
            + "pretend otherwise, and there is no date.",
        },
      ],
    },
    exampleItem: "THHN 12 STR CU 500FT",
    notServed: [
      "Special pricing agreements, ship-and-debit and claim-backs are not "
      + "modelled at all, and on this book that is the headline. PIE computes "
      + "margin from the cost on the AP-invoice line; where that cost is later "
      + "reduced by a claim, the floor PIE computes is above your real one and it "
      + "will hold lines that are profitable. Treat the margin half of this "
      + "product as unproven on an agreement-heavy book until the rebate layer "
      + "exists.",
      "Rebate accrual, claim tracking and unclaimed-rebate reporting are not "
      + "features here. If that is the problem you are solving, buy something "
      + "built for it.",
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until you start quoting here.",
      "Nothing resolves until a price list has been uploaded and its decoding "
      + "confirmed by a person.",
      "Salespeople are not imported from any ERP, so every approval routes to "
      + "management until accounts are assigned inside PIE.",
    ],
    faq: [
      {
        question: "Most of our cost is SPA-driven. Is this product wrong for us?",
        answer:
          "The margin half of it is, until a rebate layer exists — a floor "
          + "computed from invoiced cost sits above your real floor and will hold "
          + "profitable lines. The detection half is not: decline, dormancy and "
          + "price-versus-cost drift are computed from invoice history and do not "
          + "depend on the rebate at all. Buy it for that or wait; this page is "
          + "not going to pretend otherwise.",
        source: "this page's lead and limits section; docs/vertical-strategy.md §6",
      },
      {
        question: "What can PIE tell us with no reliable cost?",
        answer:
          "Which accounts are declining, which have gone dormant, which "
          + "customer-and-item pairs have drifted, and what each customer has paid "
          + "for each item over time. That is the same set a book with no purchase "
          + "cost at all gets, and it is arithmetic over rows you already have.",
        source: "/erp/sage-100, which makes the same trade on a book with no cost",
      },
      {
        question: "Does the AI decide what to escalate?",
        answer:
          "No. The detectors are deterministic arithmetic over persisted rows "
          + "against thresholds you set. A model may phrase a finding; it never "
          + "produces a number and never decides one is worth raising. Turn it off "
          + "and the same list appears.",
        source: "the determinism band on every public page",
      },
      {
        question: "Will you build the rebate layer?",
        answer:
          "It is the largest single item on the roadmap and it unlocks this trade "
          + "and plumbing together. There is no date, and this page will not give "
          + "you one — what it will do is tell you plainly that the floor is not "
          + "trustworthy on your book today.",
        source: "docs/vertical-strategy.md §11, ordered by expected value",
      },
    ],
    erpLead:
      "Prophet 21 is a core Epicor vertical here and is what a large part of this "
      + "trade runs. The other systems this trade commonly runs — Eclipse, a Trade "
      + "Service feed — are not among the seven PIE reads, and that is a hard gate "
      + "rather than a caveat: where the book cannot be read, nothing on this page "
      + "applies.",
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "plumbing-pvf",
    name: "Plumbing and PVF distribution",
    short: "plumbing and PVF",
    title: "Plumbing and PVF: the bid is a document, the cost is a stream | PIE",
    description:
      "A project price given in April is drawn against in May, July and October, "
      + "at the April number, against whatever pipe cost that month. What that "
      + "costs, why a month-end report cannot catch it, and the pairs PIE names "
      + "instead.",
    eyebrow: "For Plumbing and PVF distributors",
    headline: {
      lead: "Plumbing and PVF: the bid is a document and the cost is a ",
      em: "stream",
      tail: ".",
    },
    sub:
      "A project price given three weeks ago against a cost that has since moved "
      + "twice is still the number the customer is holding you to, and the first "
      + "place it shows up is a margin report after the job is invoiced.",
    wrong: [
      {
        tag: "Problem one",
        title: "A bid is a price with a shelf life nobody set",
        body:
          "Project work is quoted once and drawn down for months. Between the bid "
          + "and the release your purchase cost moves — sometimes more than once — "
          + "and nothing in the process compares the two. There is usually a "
          + "validity clause, and it is usually not enforced, because enforcing it "
          + "means reopening a price with a customer who is mid-job."
          + " The clause is also not really the point. Even a bid that could be reopened needs somebody to notice that it should be, and noticing means comparing a document written in April against a cost that moved in June.",
        notes: [
          "The bid is a single document with a single set of numbers",
          "The releases are spread across months you cannot choose",
          "The validity clause exists and is rarely invoked",
          "Noticing means comparing April's document against June's cost",
        ],
      },
      {
        tag: "Problem two",
        title: "Commodity pipe reprices faster than any synced cost",
        body:
          "Even a nightly sync is reporting yesterday's cost against a number that "
          + "was given in April. On the commodity half of this book the movement is "
          + "not noise around a mean — it trends, for months, and a bid priced at "
          + "the start of a run is wrong by the end of it in a direction that was "
          + "predictable to everybody except the quoting process."
          + " The direction is often known inside the business, too, which is the frustrating part: the purchasing side can frequently see a run coming, and there is no path by which that reaches a bid already in the market.",
        notes: [
          "The quote is a document; the cost is a stream",
          "A cost snapshot ages from the moment it is taken",
          "Nothing re-reads the pair after the bid is submitted",
          "Purchasing can often see a run coming; the bid cannot",
        ],
      },
      {
        tag: "Problem three",
        title: "The engineered half carries the spread and hides in the total",
        body:
          "Valves and specialties earn what commodity pipe does not, so an account "
          + "quietly moving that half elsewhere matters far more than its share of "
          + "revenue suggests. A customer total that looks flat can be a mix that "
          + "has gone badly wrong — same money, different money."
          + " So the account that looks stable is sometimes the one to worry about, and the one whose revenue dipped is sometimes perfectly healthy. A total cannot tell those two apart, and a total is what gets reviewed.",
        notes: [
          "Revenue can hold steady while margin mix collapses",
          "A per-customer total cannot show a mix shift",
          "Decline needs to be read per item, not per account",
          "The account that looks stable is sometimes the one to worry about",
        ],
      },
      {
        tag: "Problem four",
        title: "Within one book, two different honest answers",
        body:
          "The commodity half is bought direct, and its invoiced cost is the real "
          + "cost. The branded and engineered half is claimed back afterwards, and "
          + "its invoiced cost is not. Which half a line belongs to decides whether "
          + "a floor computed from it means anything — and no field in your ERP "
          + "says which half a line is."
          + " Which makes any single book-wide statement about margin discipline wrong in both directions at once — too strict on the branded lines, and credible enough on the commodity ones that nobody goes back and questions the branded ones.",
        notes: [
          "Both halves are on the same invoices, in the same book",
          "The floor is trustworthy on one of them and not the other",
          "Nothing labels which, so a book-wide claim is wrong either way",
          "A book-wide margin claim is wrong in both directions at once",
        ],
      },
    ],
    cost: {
      lead:
        "A bid does not go wrong all at once. It goes wrong in the middle, and it is "
        + "drawn down anyway.",
      compounds:
        "The release schedule is what makes this expensive. A price agreed in April "
        + "is drawn against in May, July and October, each release invoiced at the "
        + "April number against whatever the material cost that month. One bid is "
        + "not one mistake; it is a mistake with a quantity attached, and the "
        + "quantity is the customer's schedule rather than yours. Meanwhile the next "
        + "bid is priced from the last one's numbers, so the drift is carried forward "
        + "as an input rather than corrected as an error.",
      invisible:
        "Nothing objects at any point along that sequence. The bid was competitive, "
        + "the releases shipped on time, the invoices went out at the agreed price, "
        + "and the whole thing reads as a job that went to plan. It closes as a "
        + "margin figure at month end, aggregated with counter business that had "
        + "nothing to do with it.",
    },
    today: [
      {
        practice: "A cost snapshot taken at bid time",
        body:
          "Correct on the day, saved in the bid file, and never compared to "
          + "anything again. It is the right input to the original decision and no "
          + "input at all to the four releases that follow.",
      },
      {
        practice: "A validity clause nobody enforces",
        body:
          "Thirty days, printed on the quotation. Invoking it means reopening a "
          + "price with a customer who has already won the job on the back of it, "
          + "so in practice it is a negotiating position rather than a control.",
      },
      {
        practice: "A month-end margin report",
        body:
          "Real numbers over a closed period, mixing project releases with counter "
          + "business. It can tell you the month was worse than the last one and not "
          + "which bid did it.",
      },
      {
        practice: "Nothing, between bid and release",
        body:
          "The honest baseline. For the months in which the exposure actually "
          + "accumulates there is usually no process at all, because there is no "
          + "moment in the workflow that would naturally hold one.",
      },
    ],
    intervenes: [
      {
        title: "The pairs where price stopped following cost",
        body:
          "The pass-through detector reads your own purchase and invoice history and "
          + "names the customer-and-item pairs where your cost rose and your price "
          + "did not. On a commodity book that is the whole game, and it is computed "
          + "from rows your ERP already wrote — which means it works on bids raised "
          + "outside PIE, in the months while they are still being drawn against.",
      },
      {
        title: "Which accounts stopped buying the engineered half",
        body:
          "Decline and dormancy are computed per customer and per item, and need no "
          + "cost at all to be right. A mix shift that a customer total cannot show — "
          + "the valves going elsewhere while the pipe stays — is visible as the "
          + "items that stopped appearing.",
      },
      {
        title: "A floor on the line, where the cost is trustworthy",
        body:
          "Where you buy direct and the invoiced cost is the real cost, the floor "
          + "check works exactly as it does anywhere: applied at the moment the line "
          + "is priced, with a breach held for a named approver rather than sent. On "
          + "the branded half, where purchases are claimed back, it is not "
          + "trustworthy and the page says so rather than averaging the two.",
      },
      {
        title: "The enquiry, resolved against your own book",
        body:
          "Paste a takeoff, a schedule or an emailed list and PIE reads it into "
          + "lines against the catalog you uploaded — exact match first, nearest "
          + "neighbour over the description otherwise, deterministically, with no "
          + "model asked. PVF sizing and schedule notation is structured enough that "
          + "the exact match usually carries it. It finds the line in your own book "
          + "and proposes no substitute from another manufacturer's.",
      },
    ],
    speed: "matched",
    worked: {
      lead:
        "One item on one bid, from April to October. The card carries sample "
        + "figures for the floor half; steps two and three are the half that works "
        + "on a bid PIE never saw.",
      steps: [
        {
          title: "April: the bid is priced, outside PIE",
          body:
            "No connector imports quotes, so the bid itself is invisible to PIE. "
            + "What is visible is the purchase history behind the item and what this "
            + "customer has paid for it before — which is what the bid should have "
            + "been priced against.",
        },
        {
          title: "July: your cost moves and the detector notices",
          body:
            "The pass-through detector reads your own purchase and invoice rows and "
            + "names this customer-and-item pair: cost up, price flat. It is "
            + "deterministic arithmetic against a threshold you set, and it opens "
            + "into the two series it compared.",
        },
        {
          title: "July, same afternoon: somebody knows which job",
          body:
            "The pair names the customer and the item, so the job is identifiable "
            + "from your own records. Whether the contract can be reopened is a "
            + "commercial question; knowing before October rather than after is the "
            + "part that was missing.",
        },
        {
          title: "October: the next release is quoted inside PIE",
          body:
            "Now the floor applies. Cost, divided by one minus your margin floor, on "
            + "the AP-invoice cost as it stands in October rather than April. On the "
            + "card the asked-for price is below it, and the line is held for a named "
            + "approver with the rule named.",
        },
        {
          title: "And the next bid is priced from a record, not a memory",
          body:
            "The held line, the operands and the policy version are one row. The "
            + "commodity half of that record is trustworthy; the branded half is not, "
            + "for the reason the limits below give, and PIE does not average the two "
            + "into one comfortable number.",
        },
      ],
    },
    exampleItem: "2 in SCH40 A53B ERW x 21 ft",
    notServed: [
      "Special pricing agreements and manufacturer rebates are not modelled. On "
      + "the branded and engineered half of this book, where purchases are claimed "
      + "back afterwards, the floor PIE computes sits above your real one and will "
      + "hold profitable lines. The commodity half, bought direct, is unaffected — "
      + "but which half you mostly are decides how much of this product works for "
      + "you.",
      "PIE holds no commodity index and no cost forecast. It reports what your "
      + "purchase history says happened, not what pipe is about to do.",
      "Quotes are not imported from any ERP, so a bid's own history lives outside "
      + "PIE until you start quoting here — and on a book of long-lived bids that "
      + "gap matters more than most.",
      "Nothing resolves until a price list has been uploaded and its decoding "
      + "confirmed by a person.",
      "Project and job costing are not modelled. PIE prices and checks lines; it "
      + "does not track a job.",
    ],
    faq: [
      {
        question: "Our bids stay open for months. Does PIE track a quote over time?",
        answer:
          "Not yet, and it is the honest gap on this book. No connector imports "
          + "quotes, so a bid raised in your ERP is invisible to PIE until you "
          + "start quoting inside it. What PIE can do today is read the invoices "
          + "that came out of that bid and tell you where the realized margin went.",
        source: "this page's limits section; READ_STAGES includes quotes and no connector declares it",
      },
      {
        question: "Does PIE know what pipe is going to cost next month?",
        answer:
          "No, and it will not guess. It reports what your own purchase history "
          + "says your cost did, and names the customer-and-item pairs where your "
          + "price did not follow. A forecast presented with the authority of a "
          + "measurement is the thing this platform is built not to produce.",
        source: "the determinism band; CLAUDE.md §1, AI never computes a number",
      },
      {
        question: "How much of this works if we are mostly SPA-priced?",
        answer:
          "The detection half works fully; the floor does not. Where cost is "
          + "claimed back afterwards the computed floor is above your real one. If "
          + "your book is mostly branded and SPA-driven, read "
          + "/industries/electrical — the trade differs and the caveat is "
          + "identical.",
        source: "this page's limits section; /industries/electrical",
      },
      {
        question: "Can a salesperson see cost or margin?",
        answer:
          "No. Those fields are absent from the response rather than hidden by a "
          + "screen, and a rule whose boundary is cost is withheld with them.",
        source: "/roles/salesperson; CLAUDE.md §1, quote_service.project",
      },
    ],
    erpLead:
      "Prophet 21 is a core Epicor vertical here, as it is in electrical, and PVF "
      + "is named separately in the same list. The other systems this trade commonly "
      + "runs — Eclipse, DDI — are not among the seven PIE reads: where the book "
      + "cannot be read, none of this applies, and that is a gate rather than "
      + "something a page can disclose its way past.",
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
];
