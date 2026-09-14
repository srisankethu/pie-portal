/** What each industry landing page says, and why every sentence of it is safe.
 *
 * These pages exist for the reader the `/erp/` pages cannot reach. That family
 * answers "will this work with the system I run"; a distributor who has not yet
 * decided whether a margin product is *for their trade at all* is asking a
 * different question, and a page that answers it in their own vocabulary is the
 * one they can act on. So the split is deliberate and it is a rule:
 *
 *   /erp/*        — will this work with the system I run?
 *   /industries/* — will this work for the trade I'm in?
 *
 * No page in this family carries an ERP name in its title, description or `h1`,
 * because two pages competing for one query is two pages ranking for neither.
 * `industries.test.ts` holds that.
 *
 * **The honesty rule is harder here than anywhere else on the site, and it is
 * the reason this file is mostly `notServed`.** An `/erp/` page's claims are
 * checkable against a connector module — `erp.test.ts` reads the source and
 * fails a claim the code does not implement. There is no equivalent oracle for
 * "this helps a fastener distributor". So the discipline has to come from the
 * other end: **a trade only gets a page when the product serves its headline
 * pain today**, and the page prints what it does not do beside what it does.
 *
 * `docs/vertical-strategy.md` is the research and the gate. Two trades passed
 * it and this file has two entries. Fasteners and bearings score at the top of
 * that document's own ICP table and are absent here on purpose: their headline
 * pain is cross-manufacturer interchange, `CORE_SLOTS` in
 * `backend/app/decoding/schema.py` carries no thread, grade-class, bore or seal
 * slot, and `pie-parser/equivalence/distance.py` gates on `iso_shape` and
 * `insert_polarity`. A page for either would be selling the one thing we cannot
 * do. Adding an entry here is a claim that the gate was passed; do not add one
 * because a trade sounds adjacent.
 *
 * The `speed` field is where that discipline is mechanical rather than
 * editorial — see its comment.
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
 * "interchange" or "equivalent" about what PIE does. `industries.test.ts`
 * checks the rendered markup for exactly that, because this is the claim a
 * marketing edit would widen without noticing it had.
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
 * is the chip, the nav link and the llms.txt entry.
 *
 * Capitalising the first character is the entire transform, and it is enough
 * because every acronym in this registry is already upper-case *inside* the
 * string — "industrial and MRO" → "Industrial and MRO", "plumbing and PVF" →
 * "Plumbing and PVF". A title-caser would have to know that MRO and PVF are
 * acronyms and that "and" is not a word to capitalise, which is three rules
 * where one will do; `industries.test.ts` pins all seven results so a trade
 * whose `short` does not survive this transform fails rather than ships
 * mis-cased.
 */
export function verticalLabel(page: Pick<IndustryPageData, "short">): string {
  return page.short.charAt(0).toUpperCase() + page.short.slice(1);
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
  /** The `h1`, and the paragraph under it. */
  headline: { lead: string; em: string; tail: string };
  sub: string;
  /** The problem this trade actually has, in its own words. */
  problem: { title: string; body: string; detail: string };
  /** How far resolution goes here. See `SpeedReach`. */
  speed: SpeedReach;
  /** The section that describes resolution on this trade's book. */
  resolution: { title: string; body: string; points: string[] };
  /** What the platform does with what it read — three, as on the ERP pages. */
  fit: { title: string; body: string }[];
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
  /** What PIE does not do on this trade's book. Stated, never softened, and
   *  never shorter than three items — the `/erp/` pages print seven and the
   *  reason given in `erp.ts` applies here word for word: a distributor who has
   *  survived one implementation believes the vendor who volunteers the gaps. */
  notServed: string[];
  /** Rendered as a visible FAQ, and *therefore* emitted as `FAQPage` JSON-LD by
   *  `scripts/prerender.mjs`. That order matters and is the site's standing
   *  rule: schema may only restate what is on the page. The SEO audit recorded
   *  FAQPage as "not claimed, because no visible FAQ exists"; this earns it
   *  rather than overriding it. */
  faq: FaqItem[];
  /** ERP pages worth reading next, most likely first. Slugs in `erp.ts`;
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
      "PIE for Industrial and MRO distributors · a floor on every quote line",
    description:
      "Every quote line checked against your own margin floor before it goes out, "
      + "and a breach held for a named approver instead of sent. Computed from the "
      + "invoice and AP-invoice lines your system already holds, and stamped with "
      + "the policy version that judged it.",
    eyebrow: "For Industrial and MRO distributors",
    headline: {
      lead: "Margin discipline for Industrial and ",
      em: "MRO",
      tail: " distributors.",
    },
    sub:
      "A rep discounts a line to hold an account. The discount is recorded as the "
      + "last price paid, and next quarter that price is the default. Nobody "
      + "decided it. PIE checks each quote line against the floor your policy sets, "
      + "using the invoice and AP-invoice lines your system already wrote — and "
      + "where a line breaches, the platform holds it for a named approver instead "
      + "of sending it.",
    problem: {
      title: "The price nobody set",
      body:
        "Discounting on an industrial book does not happen in a pricing meeting. "
        + "It happens at the edge — a rep under competitive pressure, a counter "
        + "quoting from memory, a branch that has always done it this way. Each "
        + "decision is defensible on its own and none of them is written down.",
      detail:
        "What makes it compound is where the discount goes afterwards: it becomes "
        + "this customer's last price paid, and the next quote starts from there. "
        + "A margin report tells you this happened. It tells you monthly, after "
        + "the customer has the price, which is the wrong end of the transaction "
        + "to learn it at.",
    },
    speed: "matched",
    resolution: {
      title: "An enquiry, read into lines against your own book",
      body:
        "Paste a customer's enquiry as it arrived — a forwarded email, a line of "
        + "WhatsApp — and PIE reads it into quote lines and resolves each one "
        + "against the catalog you uploaded: an exact match where there is one, "
        + "and otherwise the nearest neighbours by description, scored the same "
        + "way every time.",
      points: [
        "Deterministic and offline — the same text resolves the same way on every "
        + "machine, in every process, and no model is asked",
        "It finds the line in your book; it does not propose a substitute from "
        + "another manufacturer's",
        "A line it cannot place stays unresolved and says so, rather than being "
        + "matched to the closest thing on the shelf",
      ],
    },
    fit: [
      {
        title: "Every line, against your own floor",
        body:
          "Your invoice and AP-invoice lines are what a floor is computed from: "
          + "what you sold, to whom, and what it cost. PIE checks each new line "
          + "against the policy you set and routes a breach for sign-off — the "
          + "platform holds it, not the salesperson, and the sign-off is "
          + "append-only.",
      },
      {
        title: "The accounts going quiet",
        body:
          "Four detectors run over the rows already synced — decline, dormancy, "
          + "margin deterioration and cost that was never passed through — plus "
          + "six more at the customer-and-item grain. Each one opens into the "
          + "figures that raised it.",
      },
      {
        title: "What it was worth, including when it was nothing",
        body:
          "The value ledger counts lines held to a floor and declines raised in "
          + "time, carrying the operands each figure came from. A month with no "
          + "detection reads UNKNOWN rather than zero, and what could not be "
          + "measured prints above what could.",
      },
    ],
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
        source: "this page's own “Where PIE intervenes” panel",
      },
    ],
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "cutting-tools",
    name: "Cutting tool and metalworking distribution",
    short: "cutting tools",
    title:
      "PIE for Cutting tool and metalworking distributors · resolve the enquiry, "
      + "hold the floor",
    description:
      "Paste a customer's enquiry and each line resolves against your own decoded "
      + "catalog — ISO designations and grade systems read into typed fields, "
      + "alternatives ranked on the dimensions actually decoded, and nothing "
      + "offered where nothing discriminates. Then every priced line is checked "
      + "against your margin floor.",
    eyebrow: "For Cutting tool and metalworking distributors",
    headline: {
      lead: "Cutting tools, quoted without losing the margin in the ",
      em: "cross-reference",
      tail: ".",
    },
    sub:
      "The enquiry names a competitor's designation. Twenty minutes later somebody "
      + "has found what you stock that is equivalent, and the price on it is a "
      + "judgement made at speed by whoever did the finding. PIE reads the enquiry "
      + "as it arrived, resolves each code against your own decoded catalog, and "
      + "then checks the price on the resolved line against your floor. Both halves "
      + "show their working.",
    problem: {
      title: "The twenty-minute line",
      body:
        "A customer asks for something you do not stock under a designation you "
        + "do not sell. Somebody opens a conversion guide, reads across to an ISO "
        + "application position, checks the dimensions, and forms a view. It is "
        + "skilled work and it is done one line at a time while the quote waits.",
      detail:
        "Then the same person prices it. The technical decision and the "
        + "commercial one are made in the same moment by the same head, and only "
        + "one of them has anything checking it. When the quote is questioned "
        + "three months later, the reasoning went home with whoever made it.",
    },
    speed: "decoded",
    resolution: {
      title: "Your designations, read rather than matched",
      body:
        "This is the one catalog the engine understands as more than text. ISO "
        + "designations and grade systems decode into typed fields — shape, "
        + "clearance, tolerance, edge length, thickness, corner radius, "
        + "chipbreaker, grade — each carrying provenance, a confidence and the "
        + "character span it was read from. Alternatives are then ranked on those "
        + "decoded fields rather than on how similar two strings look.",
      points: [
        "A shape or polarity mismatch excludes a candidate outright; dimensions "
        + "are graded by distance; coating, chipbreaker and flute count nudge and "
        + "never dominate",
        "Where nothing discriminates between the candidates, the engine abstains "
        + "and says so — it does not return the least-bad one",
        "An unrecognised token is captured verbatim rather than guessed into a "
        + "field, and identical input produces byte-identical output",
      ],
    },
    fit: [
      {
        title: "A cross-reference is a starting point, and PIE treats it as one",
        body:
          "Your applications engineer already knows that two grades at the same "
          + "ISO application position are comparable, not identical — substrate, "
          + "coating and edge preparation differ. So a score here is policy under "
          + "your own equivalence bands, true of this quote and never stored as a "
          + "fact about the two products. A scored suggestion is never promoted to "
          + "a confirmed identity.",
      },
      {
        title: "The floor, on the line you resolved",
        body:
          "Substitution is a pricing event, not only a fulfilment one. Once the "
          + "line resolves, the price typed on it is checked against the policy "
          + "you set, and a breach is held for a named approver with the rule that "
          + "stopped it named.",
      },
      {
        title: "Why you quoted what you quoted, last March",
        body:
          "Every emitted field carries where it came from and every computed row "
          + "is stamped with the version of the policy that judged it. The "
          + "catalog's own ruleset version is in the record too, which is what "
          + "explains why the same text resolved differently before you rebuilt "
          + "it.",
      },
    ],
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
        source: "this page's resolution section; app/decoding/ has no default decoder",
      },
      {
        question: "Will it pick a substitute for me?",
        answer:
          "It will rank candidates on the attributes it decoded and show which "
          + "field contributed what. It will not decide. Where nothing "
          + "discriminates it abstains, and a scored suggestion is never written "
          + "down as an identity — that stays a person's call, which is also how "
          + "your applications engineer would want it.",
        source: "this page's “a cross-reference is a starting point” panel",
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
        source: "this page's third fit panel — the catalog's ruleset version is in the record",
      },
    ],
    erpSlugs: ["prophet-21", "zoho-books", "dynamics-365-business-central"],
  },
  {
    slug: "fasteners",
    name: "Fastener distribution",
    short: "fasteners",
    title: "PIE for Fastener distributors · a floor on every line of a 200-line RFQ",
    description:
      "A fastener RFQ is not one decision, it is two hundred. PIE checks each "
      + "priced line against your own margin floor before the quote goes out and "
      + "holds a breach for a named approver, so line 174 gets the same policy as "
      + "line 1.",
    eyebrow: "For Fastener distributors",
    headline: {
      lead: "Fasteners: two hundred lines on one RFQ, and a floor on ",
      em: "every line",
      tail: ".",
    },
    sub:
      "The economics of this trade are line count. A fastener RFQ arrives with "
      + "dozens or hundreds of lines, most of them low value, and nobody re-checks "
      + "line 174 against anything — it gets a number from memory or from what the "
      + "account paid last time. PIE checks every one of them against the floor "
      + "your policy sets, at the moment it is priced, and holds what breaches it.",
    problem: {
      title: "Nobody audits line 174",
      body:
        "A large RFQ is priced the way large RFQs are always priced: the first "
        + "dozen lines carefully, the rest at speed. That is not carelessness, it "
        + "is arithmetic — the quote has to go back today, and no desk re-derives "
        + "two hundred floors by hand.",
      detail:
        "Which means the lines that leak are the ones nobody would defend if "
        + "asked. They are individually too small to argue about and collectively "
        + "the whole margin on the order. A per-line check costs nothing per line, "
        + "which is the only way a check survives contact with a quote this size.",
    },
    speed: "matched",
    resolution: {
      title: "The RFQ, read into lines against your own book",
      body:
        "Paste the enquiry as it arrived and PIE reads it into lines, resolving "
        + "each against the catalog you uploaded — an exact match where there is "
        + "one, the nearest neighbours by description otherwise, scored the same "
        + "way every time. On a book of distributor part numbers that is usually "
        + "the whole job.",
      points: [
        "Deterministic and offline — the same line resolves the same way on every "
        + "machine, and no model is asked",
        "It finds the line in your own book. It does not read across from a "
        + "competitor's number to yours, and this page will not pretend otherwise",
        "A line it cannot place stays unresolved and says so, rather than being "
        + "matched to the nearest thing on the shelf",
      ],
    },
    fit: [
      {
        title: "The same policy on every line, however many there are",
        body:
          "The floor is arithmetic on the AP-invoice line your ERP already wrote, "
          + "so it costs the same to apply to line 200 as to line 1. A breach is "
          + "held for a named approver rather than sent — the platform holds it, "
          + "not the person who typed it.",
      },
      {
        title: "Blanket agreements that quietly stopped being right",
        body:
          "A price agreed a year ago against a steel cost from a year ago is the "
          + "commonest loss in this trade. The cost-not-passed-through detector "
          + "reads your own purchase history and raises the customer-and-item "
          + "pairs where your cost moved and your price did not.",
      },
      {
        title: "An order you can explain a year later",
        body:
          "Every held line records the rule that stopped it and the version of "
          + "the policy in force when somebody signed. When a customer asks why "
          + "this year's price differs from last year's on one line of two "
          + "hundred, the answer is a row rather than a recollection.",
      },
    ],
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
        source: "this page's problem section; commercial/ computes the floor per line",
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
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "bearings-power-transmission",
    name: "Bearing and power transmission distribution",
    short: "bearings and power transmission",
    title:
      "PIE for Bearings and power transmission distributors · hold the floor on "
      + "an urgent line",
    description:
      "A bearing goes down and the customer needs it today. PIE checks the price "
      + "typed on that line against your own margin floor before the quote goes "
      + "out, so urgency does not become the discount.",
    eyebrow: "For Bearings and power transmission distributors",
    headline: {
      lead: "Bearings and power transmission: urgency is not a reason to ",
      em: "give the margin away",
      tail: ".",
    },
    sub:
      "A stopped line is the best negotiating position your customer will ever "
      + "have, and both of you know it. The price gets agreed on the phone in "
      + "ninety seconds, against a tier nobody re-reads. PIE puts the floor your "
      + "policy sets in front of that decision while it is being made, and holds "
      + "what breaches it for a named approver.",
    problem: {
      title: "The price agreed on the phone",
      body:
        "Breakdown business is quoted verbally and confirmed afterwards. There is "
        + "no considered moment between the question and the number, and the "
        + "tiered price the account is nominally on is the thing least likely to "
        + "be consulted while a plant is down.",
      detail:
        "So the exception becomes the rate. A price given once under pressure is "
        + "recorded as what this customer pays, and the next enquiry starts from "
        + "it — not because anybody decided that, but because nothing in the "
        + "system distinguishes an emergency from a standing agreement.",
    },
    speed: "matched",
    resolution: {
      title: "The enquiry, resolved against your own book",
      body:
        "Paste what the customer sent and PIE reads it into lines, resolving each "
        + "against the catalog you uploaded by exact match and, failing that, by "
        + "nearest neighbour over the description text.",
      points: [
        "Deterministic and offline — the same designation resolves the same way "
        + "every time, and no model is asked",
        "It finds the line in your own book. Reading one manufacturer's "
        + "designation across to another's is this trade's central skill and PIE "
        + "does not have it",
        "A line it cannot place says so, rather than being matched to whatever "
        + "was closest",
      ],
    },
    fit: [
      {
        title: "A floor that arrives before the price is agreed",
        body:
          "Computed from the AP-invoice line your ERP already wrote, applied at "
          + "the moment the line is priced rather than in a review afterwards. A "
          + "breach is held for somebody with the authority to sign it, with the "
          + "rule that stopped it named.",
      },
      {
        title: "The tier that stopped matching the account",
        body:
          "Tiered pricing decays quietly: volumes change, costs move, and the "
          + "band an account sits in was set by somebody who has left. The "
          + "customer-and-item detectors read your own history and name the pairs "
          + "where the realised margin no longer resembles the tier.",
      },
      {
        title: "An emergency price that stays an emergency price",
        body:
          "Every held line is recorded with the policy version in force when it "
          + "was signed, so a concession made on a Tuesday because a plant was "
          + "down is a row somebody can point at — rather than the number the "
          + "account has quietly been on ever since.",
      },
    ],
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
        source: "this page's problem section",
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
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "fluid-power",
    name: "Fluid power, hose and fitting distribution",
    short: "fluid power",
    title: "PIE for Fluid power distributors · one floor across every branch",
    description:
      "Two branches quote the same assembly a week apart and the prices are not "
      + "close. PIE applies one margin policy wherever the line was priced, and "
      + "says UNKNOWN rather than guessing where a fabricated assembly carries no "
      + "cost.",
    eyebrow: "For Fluid power, hose and fitting distributors",
    headline: {
      lead: "Fluid power: one quote, one floor — whichever ",
      em: "branch",
      tail: " typed it.",
    },
    sub:
      "Counter pricing is local by nature. Each branch has its own habits, its "
      + "own long-standing accounts and its own idea of what a job is worth, and "
      + "none of that is written down anywhere the others can read. PIE applies "
      + "one policy wherever the line was priced, and holds a breach for a named "
      + "approver rather than sending it.",
    problem: {
      title: "Two branches, two prices, one customer",
      body:
        "The same assembly quoted a week apart at two counters, at prices that "
        + "are not close. Neither counter did anything wrong — there was no floor "
        + "in front of either of them at the moment they typed, and no reason for "
        + "one to know what the other had said.",
      detail:
        "The customer finds out before you do. What follows is not a pricing "
        + "conversation but a credibility one, and it is usually settled by "
        + "honouring the lower number across the account — so the cheapest quote "
        + "any branch has ever given becomes the price everywhere.",
    },
    speed: "matched",
    resolution: {
      title: "The enquiry, resolved against your own book",
      body:
        "Paste what arrived and PIE reads it into lines, resolving each against "
        + "the catalog you uploaded — exact match first, nearest neighbour over "
        + "the description text otherwise.",
      points: [
        "Deterministic and offline, so two branches pasting the same text get the "
        + "same answer",
        "It finds the line in your own book. It does not read one "
        + "manufacturer's designation across to another's",
        "A line it cannot place stays unresolved and says so",
      ],
    },
    fit: [
      {
        title: "One policy, however many counters",
        body:
          "The floor is computed from the AP-invoice line your ERP already wrote "
          + "and applied identically at every branch. A breach routes to a named "
          + "approver, so the exception is visible centrally at the moment it is "
          + "asked for rather than discovered in a margin report.",
      },
      {
        title: "Where there is no cost, the answer is UNKNOWN",
        body:
          "A fabricated assembly built at the counter may carry no item-level "
          + "cost in your ERP at all. PIE will not estimate one. It reports the "
          + "line as unmeasured and says so on every screen that would otherwise "
          + "have used the figure — which is the same rule that governs a book "
          + "with no purchase cost anywhere.",
      },
      {
        title: "Accounts drifting away from one branch",
        body:
          "Decline and dormancy are computed from invoice history and need no "
          + "cost at all, so they work on the assembly half of the book as well as "
          + "the stocked half. A customer quietly moving their hose business is "
          + "visible before the renewal conversation.",
      },
    ],
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
        source: "this page's problem section; commercial/policy.py is owner-editable",
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
    erpSlugs: ["prophet-21", "dynamics-365-business-central", "acumatica"],
  },
  {
    slug: "electrical",
    name: "Electrical distribution",
    short: "electrical",
    title:
      "PIE for Electrical distributors · what your book says before the rebates "
      + "land",
    description:
      "On an SPA-heavy book the invoiced cost is not your cost, so PIE leads with "
      + "what needs no cost at all: which accounts are declining, which have gone "
      + "quiet, and where your price stopped following your purchase price.",
    eyebrow: "For Electrical distributors",
    headline: {
      lead: "Electrical: your book knows which accounts are ",
      em: "going quiet",
      tail: ". That needs no cost at all.",
    },
    sub:
      "This page starts with a limitation because it decides everything after it. "
      + "Where a large share of your purchases is claimed back through special "
      + "pricing agreements, the cost on the AP-invoice line is higher than what "
      + "the product really cost you — so a floor computed from it sits above your "
      + "real floor and would hold lines that are perfectly profitable. PIE does "
      + "not model rebates. What it does instead is the half that needs no cost: "
      + "decline, dormancy and price-versus-cost drift, computed from invoice "
      + "history your ERP already holds.",
    problem: {
      title: "The account that left without telling you",
      body:
        "Electrical accounts do not resign. They reduce — a contractor prices "
        + "elsewhere on one job, then two, then the line items you used to see "
        + "stop appearing while the relationship carries on looking fine. Nobody "
        + "reports it because nothing happened.",
      detail:
        "By the time it is visible in a total it is a year old and the "
        + "conversation is a retrieval rather than a save. Detection is arithmetic "
        + "over rows you already have — which is why it works on this book even "
        + "though the margin arithmetic does not.",
    },
    speed: "matched",
    resolution: {
      title: "The enquiry, resolved against your own book",
      body:
        "Paste a takeoff or an emailed enquiry and PIE reads it into lines, "
        + "resolving each against the catalog you uploaded by exact match and by "
        + "nearest neighbour over the description. On a book of clean "
        + "manufacturer catalog numbers the exact match usually carries it.",
      points: [
        "Deterministic and offline — no model is asked what a line is",
        "It finds the line in your own book; it proposes no substitute from "
        + "another manufacturer",
        "A line it cannot place says so rather than being matched to the nearest "
        + "thing",
      ],
    },
    fit: [
      {
        title: "Decline and dormancy, which need no cost",
        body:
          "Four detectors run over invoice history alone — decline, dormancy, "
          + "margin deterioration and cost that was never passed through — plus "
          + "six at the customer-and-item grain. Each opens into the rows that "
          + "raised it. None of the first two needs a purchase cost to be right.",
      },
      {
        title: "Where your price stopped following your cost",
        body:
          "Even on an SPA book the *direction* of your invoiced cost is real: "
          + "when it rose and your price did not, the pass-through detector names "
          + "the customer-and-item pairs. It is the rebate-independent half of the "
          + "margin question, and usually the actionable half.",
      },
      {
        title: "One quote, one record, one policy version",
        body:
          "Whatever floor you do set, the sign-off on a breach is append-only and "
          + "carries the version of the policy in force when it was given. Editing "
          + "the policy does not make last quarter's decisions unreadable.",
      },
    ],
    exampleItem: "THHN 12 STR CU 500FT",
    notServed: [
      "Special pricing agreements, ship-and-debit and claim-backs are not "
      + "modelled at all, and on this book that is the headline. PIE computes "
      + "margin from the cost on the AP-invoice line; where that cost is later "
      + "reduced by a claim, the floor PIE computes is above your real one and it "
      + "will hold lines that are profitable. Treat the margin half of this "
      + "product as unproven on an SPA-heavy book until the rebate layer exists.",
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
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "plumbing-pvf",
    name: "Plumbing and PVF distribution",
    short: "plumbing and PVF",
    title: "PIE for Plumbing and PVF distributors · the bid you priced last month",
    description:
      "Pipe reprices while a bid is open. PIE reads what each customer actually "
      + "paid, names where your price stopped following your cost, and checks a "
      + "quote line against your policy — with the rebate caveat stated plainly.",
    eyebrow: "For Plumbing and PVF distributors",
    headline: {
      lead: "Plumbing and PVF: the cost moved after you ",
      em: "quoted",
      tail: ". Nobody told the price.",
    },
    sub:
      "Commodity pipe reprices on a timescale bids do not respect. A number given "
      + "three weeks ago against a cost that has since moved is still the number "
      + "the customer is holding you to, and the first place it shows up is a "
      + "margin report after the job is invoiced. PIE reads the same rows sooner "
      + "and names the customer-and-item pairs where your price stopped following "
      + "your cost.",
    problem: {
      title: "A bid is a price with a shelf life nobody set",
      body:
        "Project work is quoted once and drawn down for months. Between the bid "
        + "and the release your purchase cost moves — sometimes twice — and "
        + "nothing in the process compares the two. The quote is a document; the "
        + "cost is a stream.",
      detail:
        "Counter business has the same shape at a shorter interval. The result is "
        + "the same either way: a price that was right when it was given, is not "
        + "now, and nobody is looking at the pair until the month closes.",
    },
    speed: "matched",
    resolution: {
      title: "The enquiry, resolved against your own book",
      body:
        "Paste a takeoff, a schedule or an emailed list and PIE reads it into "
        + "lines against the catalog you uploaded — exact match first, nearest "
        + "neighbour over the description otherwise.",
      points: [
        "Deterministic and offline — the same schedule resolves the same way every "
        + "time",
        "It finds the line in your own book and proposes no substitute from "
        + "another manufacturer",
        "A line it cannot place stays unresolved and says so",
      ],
    },
    fit: [
      {
        title: "The pairs where price stopped following cost",
        body:
          "The pass-through detector reads your own purchase and invoice history "
          + "and names the customer-and-item pairs where your cost rose and your "
          + "price did not. On a commodity book that is the whole game, and it is "
          + "computed from rows your ERP already wrote.",
      },
      {
        title: "A floor on the line, where the cost is trustworthy",
        body:
          "Where you buy direct and the invoiced cost is the real cost, the floor "
          + "check works exactly as it does anywhere: applied at the moment the "
          + "line is priced, with a breach held for a named approver rather than "
          + "sent.",
      },
      {
        title: "Which accounts stopped buying the engineered half",
        body:
          "Valves and specialties carry the spread that commodity pipe does not, "
          + "so an account quietly moving that half elsewhere matters more than "
          + "its total suggests. Decline and dormancy are computed per customer "
          + "and item, and need no cost to be right.",
      },
    ],
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
          + "that came out of that bid and tell you where the realised margin went.",
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
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
];
