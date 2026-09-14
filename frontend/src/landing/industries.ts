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

export interface IndustryFaq {
  q: string;
  a: string;
}

export interface IndustryPageData {
  /** The URL segment: `/industries/{slug}`. */
  slug: string;
  /** The trade, as its own people write it. */
  name: string;
  /** What fits in a sentence. */
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
  faq: IndustryFaq[];
  /** ERP pages worth reading next, most likely first. Slugs in `erp.ts`;
   *  `industries.test.ts` holds every one against that registry, so a renamed
   *  ERP page cannot leave a dead link here. */
  erpSlugs: string[];
}

export const INDUSTRY_PAGES: IndustryPageData[] = [
  {
    slug: "industrial-mro",
    name: "industrial and MRO distribution",
    short: "industrial and MRO",
    title:
      "PIE for industrial & MRO distributors · a floor on every quote line",
    description:
      "Every quote line checked against your own margin floor before it goes out, "
      + "and a breach held for a named approver instead of sent. Computed from the "
      + "invoice and AP-invoice lines your system already holds, and stamped with "
      + "the policy version that judged it.",
    eyebrow: "For industrial and MRO distributors",
    headline: {
      lead: "Margin discipline for industrial and ",
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
        + "against the catalogue you uploaded: an exact match where there is one, "
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
      + "resolves an enquiry against your own catalogue; it does not rank one "
      + "manufacturer's part as an equivalent for another's.",
      "Nothing resolves until a price list has been uploaded and its decoding "
      + "confirmed by a person. There is no default decoder and no shared "
      + "catalogue — a company that has uploaded nothing resolves nothing.",
      "Stock levels and customer payments are not read from Prophet 21 in this "
      + "version, so the stock, GMROI and collections screens stay empty on a P21 "
      + "book.",
      "Salespeople are not imported from any ERP, so every approval routes to "
      + "management until accounts are assigned inside PIE.",
    ],
    faq: [
      {
        q: "Does PIE write anything back to my ERP?",
        a:
          "On Prophet 21, Sage X3 and Sage 100, nothing — those connectors are "
          + "read-only and there is no method in them that creates a record. On "
          + "NetSuite, Acumatica, Dynamics 365 Business Central and Zoho Books, "
          + "the one thing PIE can create is the quote itself, and only if you "
          + "grant that permission separately. Everything else is read.",
      },
      {
        q: "How far back does the first sync read?",
        a:
          "The first pull is offered from the first day of the month 18 months "
          + "back, and you choose the date before it starts. It commits as it "
          + "goes, so you can watch it move — and the screens then report the span "
          + "the rows actually cover, not the window that was asked for.",
      },
      {
        q: "Can a salesperson see cost or margin?",
        a:
          "No, and not because a screen hides it. The server omits those fields "
          + "from the response, so there is nothing to read out of a network tab, "
          + "and a rule whose boundary is cost is withheld too and replaced with a "
          + "single approval-required marker. What the desk does get is the floor, "
          + "the recommended price and this customer's own history.",
      },
      {
        q: "Does the AI set the prices?",
        a:
          "No. Prices, margins, floors and thresholds are computed "
          + "deterministically from your persisted rows. A model may read those "
          + "numbers and phrase them; it never produces one. Turn the AI off "
          + "entirely and every figure on every screen still works.",
      },
      {
        q: "We already have a pricing matrix in our ERP. Why add this?",
        a:
          "A matrix decides what price to offer. PIE checks what was actually "
          + "typed on the line against the floor your policy sets, at the moment "
          + "it is typed, and holds a breach for a named approver. The two answer "
          + "different questions and PIE does not replace the matrix.",
      },
    ],
    erpSlugs: ["prophet-21", "acumatica", "dynamics-365-business-central"],
  },
  {
    slug: "cutting-tools",
    name: "cutting tool and metalworking distribution",
    short: "cutting tools",
    title:
      "PIE for cutting tool & metalworking distributors · resolve the enquiry, "
      + "hold the floor",
    description:
      "Paste a customer's enquiry and each line resolves against your own decoded "
      + "catalogue — ISO designations and grade systems read into typed fields, "
      + "alternatives ranked on the dimensions actually decoded, and nothing "
      + "offered where nothing discriminates. Then every priced line is checked "
      + "against your margin floor.",
    eyebrow: "For cutting tool and metalworking distributors",
    headline: {
      lead: "Quote without losing the margin in the ",
      em: "cross-reference",
      tail: ".",
    },
    sub:
      "The enquiry names a competitor's designation. Twenty minutes later somebody "
      + "has found what you stock that is equivalent, and the price on it is a "
      + "judgement made at speed by whoever did the finding. PIE reads the enquiry "
      + "as it arrived, resolves each code against your own decoded catalogue, and "
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
        "This is the one catalogue the engine understands as more than text. ISO "
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
          + "catalogue's own ruleset version is in the record too, which is what "
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
      + "engine is a seed for the first company, not a catalogue of your book.",
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until you start quoting here.",
      "An equivalence is never composed. PIE will not reason from one scored "
      + "match to a second — every comparison is against the request, because two "
      + "hops of a tolerance band put a 0.4 mm corner radius and a 0.8 mm one in "
      + "the same class.",
    ],
    faq: [
      {
        q: "Which manufacturers' nomenclature does PIE decode?",
        a:
          "Whichever ones you upload a price list for. Each list is analysed on "
          + "its own to find the shapes of description it contains, a person "
          + "confirms what the varying parts mean, and the catalogue is built from "
          + "that. There is no shipped list of supported brands and no default "
          + "decoder — a decoder that guessed would be a wrong number with a real "
          + "provenance stamp on it.",
      },
      {
        q: "Will it pick a substitute for me?",
        a:
          "It will rank candidates on the attributes it decoded and show which "
          + "field contributed what. It will not decide. Where nothing "
          + "discriminates it abstains, and a scored suggestion is never written "
          + "down as an identity — that stays a person's call, which is also how "
          + "your applications engineer would want it.",
      },
      {
        q: "Can a salesperson see cost or margin?",
        a:
          "No. The server omits those fields from the response rather than the "
          + "screen hiding them, and a rule whose boundary is cost is withheld too. "
          + "The desk gets the floor, the recommended price and this customer's own "
          + "history — enough to negotiate, without the cost basis.",
      },
      {
        q: "Does the AI read the enquiry?",
        a:
          "The resolution does not. It is a deterministic parser with a versioned "
          + "rule set, and identical input produces identical bytes — which is what "
          + "makes a resolution auditable months later. A model may phrase what was "
          + "found; it never decides what was found and never produces a number.",
      },
      {
        q: "What happens when we rebuild the catalogue?",
        a:
          "Resolutions carry the catalogue's ruleset version, so a line that "
          + "resolved differently before the rebuild can say which edition answered "
          + "it. That is deliberate: a rebuilt catalogue decoding differently is "
          + "exactly the fact that explains an old answer.",
      },
    ],
    erpSlugs: ["prophet-21", "zoho-books", "dynamics-365-business-central"],
  },
];
