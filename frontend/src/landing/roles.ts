/** What each role page says, and why it is the one family on this site whose
 *  claims are enforced by the server rather than described by it.
 *
 * Three pages, one per role in `backend/app/domain/enums.Role` — salesperson,
 * sales manager, owner. They exist because the three readers buy for genuinely
 * different reasons: a salesperson wants to know whether this thing is going to
 * slow their desk down, a manager wants to stop being surprised by a number
 * after the fact, and an owner wants to know that the control cannot be talked
 * around. One page addressed to all three answers none of them.
 *
 * **The claim these pages make is unusual and it is the reason they are worth
 * writing.** Most software describing "role-based views" is describing what a
 * screen renders. Here the fields a salesperson may not see are *absent from
 * the API response* — `quote_service.project` withholds them server-side, and
 * withholds any rule whose boundary is cost along with them, substituting one
 * fixed `APPROVAL_REQUIRED`. A component that hid a field it had been sent
 * would be a defect, because a network tab is not hard to open. That is a
 * checkable difference and it is what the salesperson page leads with.
 *
 * Two disciplines apply to every sentence in this file:
 *
 *   - **A permission stated here must be one the server actually enforces.**
 *     The approval rules below are read off `backend/app/approvals.py`:
 *     `authority_for` escalates to the owner only when a line is below cost
 *     *and* the org's `below_cost_requires_owner` policy is set, `can_decide`
 *     refuses anyone else, and `rationale_required` makes an owner-authority
 *     approval carry a written reason. That is a setting, not a constant, and
 *     the owner page says so rather than promising a rule the org can turn off.
 *   - **No page here states a nav-item count or a screen count.** They were in
 *     an internal document, they move whenever a screen is added, and a number
 *     on a public page that nobody re-derives is a claim that goes stale
 *     silently. What is durable is *which* facts a role may see, and that is
 *     what these pages say.
 */

import { type FaqItem } from "./faq";

export interface RolePageData {
  /** The URL segment: `/roles/{slug}`. */
  slug: string;
  /** The role in `domain/enums.Role` this page is about. `roles.test.ts` holds
   *  the set of slugs against the set of roles, so a role added to the product
   *  without a page — or a page for a role that does not exist — is a failure
   *  rather than an omission nobody notices. */
  role: "SALESPERSON" | "SALES_MANAGER" | "OWNER";
  /** What this person calls themselves. */
  name: string;
  short: string;
  title: string;
  description: string;
  eyebrow: string;
  headline: { lead: string; em: string; tail: string };
  sub: string;
  /** The day this role actually has, and where PIE lands in it. */
  day: { title: string; body: string };
  /** What this role can see, in their own terms. */
  sees: string[];
  /** What this role cannot see, and whether that is a policy or a constant.
   *  Empty for the owner, which is stated on the page rather than hidden: the
   *  owner sees everything, and a page pretending otherwise would be inventing
   *  a restraint to look rigorous. */
  cannotSee: string[];
  /** Three things the role gets. */
  fit: { title: string; body: string }[];
  /** What PIE does not do for this person today. */
  notServed: string[];
  faq: FaqItem[];
}

export const ROLE_PAGES: RolePageData[] = [
  {
    slug: "salesperson",
    role: "SALESPERSON",
    name: "the salesperson",
    short: "salespeople",
    title: "PIE for salespeople · quote faster, without ever seeing cost",
    description:
      "Paste an enquiry as it arrived and get priced lines. You see the floor, "
      + "the recommended price and this customer's own history — cost and margin "
      + "are absent from the response, not hidden in the browser. The guardrail "
      + "travels with the quote instead of living in a manager's head.",
    eyebrow: "For the quoting desk",
    headline: {
      lead: "Quote confidently, without ever seeing ",
      em: "cost",
      tail: ".",
    },
    sub:
      "You do not need the cost basis to price well. You need the floor, what "
      + "this customer has paid before, and to know before you send that the line "
      + "is going to stand. PIE gives you those three and withholds the rest — "
      + "the server omits cost and margin from what it sends you, so there is "
      + "nothing to read out of a network tab and nothing you have to be careful "
      + "about repeating.",
    day: {
      title: "An enquiry arrives the way enquiries actually arrive",
      body:
        "A forwarded email, a photograph of a fax, a line of WhatsApp from "
        + "somebody standing at a machine. Paste it in as it came. PIE reads it "
        + "into lines and resolves each one against your own catalogue, you "
        + "confirm anything it flagged, and you price. Where it could not place a "
        + "line it says so rather than guessing — an unrecognised token is kept "
        + "verbatim, never quietly turned into a part number.",
    },
    sees: [
      "The negotiation floor on each line, and the recommended price",
      "What this customer has paid for this item before, and at what quantity",
      "Whether a line will need approval before you send it — and that it will "
      + "be held rather than going out and being unwound afterwards",
      // Was "…with which attribute contributed what", which is true on a
      // catalogue the engine decodes into typed fields and is metalworking
      // only — `CORE_SLOTS` has no vocabulary for anybody else's attributes.
      // A role page is read by every trade, so it gets the claim that holds
      // for all of them and names where the stronger one applies.
      "The alternatives it found in your own book — and, on a catalogue whose "
      + "designations it decodes, which attribute contributed what",
    ],
    cannotSee: [
      "Purchase cost, on any line, anywhere in the product",
      "Margin — per line, per quote, per customer, in any aggregate",
      "Purchase spend and the supplier side of the book",
      "Any rule whose boundary is cost: a held line tells you it needs approval, "
      + "and not which cost-based rule fired",
    ],
    fit: [
      {
        title: "The enquiry, in one pass",
        body:
          "Pasted text to resolved, priced lines without re-keying. Resolution "
          + "is deterministic — the same enquiry resolves the same way every "
          + "time — and it runs against your own catalogue rather than a generic "
          + "product database.",
      },
      {
        title: "A floor you can quote against",
        body:
          "The guardrail is on the line while you are pricing it, not in a "
          + "review afterwards. You will not send something that gets unwound, "
          + "and you do not have to interrupt a manager to find out.",
      },
      {
        title: "Held, not blocked",
        body:
          "A line that breaches the floor routes to somebody with the authority "
          + "to sign it, with the rule that stopped it named. The platform holds "
          + "it — that is not you having to argue for it.",
      },
    ],
    notServed: [
      "Quantity on a quote line cannot be edited once the line exists. Changing "
      + "a quantity means deleting the line and re-pasting it. This is an open "
      + "product decision rather than a limitation anyone is defending.",
      "There is no activity log and no follow-up entity — no call, visit or "
      + "email is recorded against an account. If that is your daily tool, PIE is "
      + "not it.",
      "Manual entry of a product that is not in your catalogue is not reachable "
      + "from the screen. A line resolves against what has been uploaded, or it "
      + "stays unresolved.",
      "Your accounts have to be assigned to you inside PIE — salespeople are not "
      + "imported from any ERP, so until that is done everything routes to "
      + "management.",
    ],
    faq: [
      {
        question: "Can I work out the cost from the floor?",
        answer:
          "With algebra, partly — the floor and the recommended price are both "
          + "cost times a policy multiplier, which leaves two equations in three "
          + "unknowns. That is an accepted, documented trade-off rather than an "
          + "oversight: coarsening those two numbers would blunt the screens you "
          + "use to decide. What is ruled out is a cost or margin field, a count "
          + "or flag that answers a margin question, and any rule you could walk "
          + "a price against to find where the answer changes.",
        source: "CLAUDE.md §1, the accepted residual; the withheld column above",
      },
      {
        question: "Does it work on a phone, in a customer's factory?",
        answer:
          "Customer lookup, customer context, product lookup, price and "
          + "availability are the tasks built for that, one-handed and on poor "
          + "signal. The full quote-building flow is a desk task.",
        source: "PRODUCT.md, Field sales — the five tasks built for it",
      },
      {
        question: "Will it tell me which substitute to offer?",
        answer:
          "It ranks what it found and shows which attribute contributed what. It "
          + "does not decide, and where nothing discriminates between the "
          + "candidates it abstains rather than returning the least-bad one. On a "
          + "catalogue whose designations it does not decode it will find the "
          + "line in your own book by description, which is not the same thing as "
          + "proposing an equivalent.",
        source: "/industries/cutting-tools; the abstention rule in the engine",
      },
      {
        question: "Is my quote going to be second-guessed?",
        answer:
          "Only where it breaches a floor somebody set on purpose, and then by a "
          + "named person against a written policy rather than by whoever happens "
          + "to look. The sign-off is append-only and carries the policy version "
          + "in force when it was given, so the reasoning survives the quarter.",
        source: "this page's “Held, not blocked” panel",
      },
    ],
  },
  {
    slug: "sales-manager",
    role: "SALES_MANAGER",
    name: "the sales manager",
    short: "sales managers",
    title: "PIE for sales managers · stop being surprised by a number",
    description:
      "Every quote line checked against your policy before it goes out, breaches "
      + "held for your sign-off with the rule that stopped them named, and the "
      + "accounts that changed raised as a short list rather than a dashboard. "
      + "Every figure opens into the rows beneath it.",
    eyebrow: "For the desk that owns the number",
    headline: {
      lead: "Stop finding out at ",
      em: "month end",
      tail: ".",
    },
    sub:
      "The margin report is not wrong, it is late — by the time it runs, the "
      + "customer has the price and the concession is a fact. PIE moves the check "
      + "to the moment the line is priced: a breach is held for your sign-off "
      + "instead of sent, and what you sign is recorded with the policy version "
      + "that judged it.",
    day: {
      title: "The two things that actually reach you",
      body:
        "A held line, with cost, floor and recommended price on it and the rule "
        + "that stopped it named — one screen, one decision, on the record. And a "
        + "short list of accounts that changed: margin drifting, a customer "
        + "declining, cost that went up and was never passed through. Not a "
        + "dashboard to go and interrogate every morning.",
    },
    sees: [
      "Cost basis, margin and the supplier side of the book",
      "Every held line, with the rule that held it and the figures behind it",
      "Margin drift per customer and per item, and the rows each figure came from",
      "The signals raised over your own persisted rows, each opening into its "
      + "evidence",
    ],
    cannotSee: [
      "Below-cost approvals, where your organization has set them to require the "
      + "owner's own signature. That is a policy setting rather than a fixed "
      + "rule, and where it is off a manager decides those too.",
      "The owner-only surfaces: margin policy, users and roles, AI keys, "
      + "connections and the trust controls.",
    ],
    fit: [
      {
        title: "The check moves to the moment of quoting",
        body:
          "Your policy sets the floor per line and PIE applies it while the "
          + "salesperson is pricing, not in a report afterwards. A breach does "
          + "not send quietly — the platform holds it, which means the "
          + "conversation happens before the customer has a number.",
      },
      {
        title: "A short list, not a dashboard",
        body:
          "Ten detectors run over rows your ERP already wrote — decline, "
          + "dormancy, margin deterioration, cost not passed through, and six at "
          + "the customer-and-item grain. Each is written up in plain words and "
          + "opens into the figures that raised it.",
      },
      {
        title: "Numbers that still explain themselves",
        body:
          "Every computed row is stamped with a hash of the policy that judged "
          + "it, so editing the margin policy does not make last quarter's "
          + "decisions unreadable. Approvals and signals are append-only: what "
          + "you signed keeps the thresholds that were in force.",
      },
    ],
    notServed: [
      "Quotes are not imported from any ERP, so a win rate has no denominator "
      + "until your desk starts quoting inside PIE. Everything about "
      + "quoted-and-lost is invisible before that.",
      "Special pricing agreements and manufacturer rebates are not modelled. "
      + "Margin is computed from the cost on the AP-invoice line, so on a book "
      + "where a large share of purchases is claimed back afterwards the floor "
      + "sits above the one you actually have.",
      "There is no forecast and no target-tracking. PIE reports what the rows "
      + "say and what the policy did; it does not project.",
      "There is no activity log, so nothing here tells you who called whom.",
    ],
    faq: [
      {
        question: "What stops a salesperson simply overriding the floor?",
        answer:
          "They cannot. The line is held by the platform rather than flagged to "
          + "the person pricing it, and it routes to somebody with the authority "
          + "to sign — a manager, or the owner where the line is below cost and "
          + "your policy requires it. The salesperson is not asked to decide and "
          + "is not told which cost-based rule fired.",
        source: "the withheld column on /roles/salesperson; approvals.can_decide",
      },
      {
        question: "Can I edit the margin policy?",
        answer:
          "The policy is owner-editable. You approve against it. That split is "
          + "deliberate — the person who signs an exception is not the person who "
          + "moves the line the exception is measured from.",
        source: "/roles/owner — the policy is owner-editable",
      },
      {
        question: "What happens to old numbers when the policy changes?",
        answer:
          "Anything a human signed keeps the version that was in force when they "
          + "signed it — approvals, signals and quote snapshots are append-only. "
          + "Derived metrics are upserted and carry the stamp of the policy that "
          + "judged the value they currently hold; a full re-sync rebuilds them "
          + "from your ERP.",
        source: "the determinism band; CLAUDE.md §1, thresholds carry a version",
      },
      {
        question: "Does the AI decide what to escalate?",
        answer:
          "No. The detectors are deterministic arithmetic over persisted rows "
          + "against thresholds you set. A model may phrase a finding in plain "
          + "words; it never produces a number and it never decides one is worth "
          + "raising. Turn it off and the same list appears.",
        source: "the determinism band",
      },
    ],
  },
  {
    slug: "owner",
    role: "OWNER",
    name: "the owner",
    short: "owners and finance",
    title: "PIE for owners · a control that cannot be talked around",
    description:
      "You set the floors and the thresholds; the platform holds every quote to "
      + "them and records who signed what against which version. Read-only "
      + "against your ERP, deterministic by construction, and honest about the "
      + "months it could not measure.",
    eyebrow: "For the owner and finance",
    headline: {
      lead: "You set the floors. The platform ",
      em: "holds",
      tail: " them.",
    },
    sub:
      "A margin policy that lives in somebody's head is a policy that bends under "
      + "pressure and leaves no record of having bent. Here it is a versioned "
      + "object: every computed row is stamped with a hash of the policy that "
      + "judged it, every sign-off is append-only, and a below-cost concession "
      + "can be made to require your own signature and a written reason.",
    day: {
      title: "What you are actually buying",
      body:
        "Not a report. A control that sits in the path of the transaction, plus "
        + "the evidence that it did something — a ledger of lines held to a floor "
        + "and declines raised in time, carrying the operands each figure was "
        + "computed from, so any number opens into the rows that produced it.",
    },
    sees: [
      "Everything a manager sees, plus the margin policy itself and its history",
      "Users, roles and what each role's responses actually contain",
      "Connections, credentials and the trust surface — disclosure, erasure, "
      + "break-glass",
      "The value ledger, with what could not be measured printed above what could",
    ],
    cannotSee: [],
    fit: [
      {
        title: "The policy is an object, not a habit",
        body:
          "You edit the floors and thresholds; the platform stamps every "
          + "computed row with a content hash of what judged it. Change the "
          + "policy and last quarter's numbers still say which one produced "
          + "them, which is what makes the policy safe to change at all.",
      },
      {
        title: "The irreversible concession needs your name on it",
        body:
          "Where your policy requires it, a line priced below what the item cost "
          + "escalates past the manager to you, and approving it will not proceed "
          + "without a written reason. It is the one concession the money is gone "
          + "on the moment the quote leaves.",
      },
      {
        title: "A ledger that prints its own gaps first",
        body:
          "What the platform was worth, counted from the operands rather than "
          + "asserted — and a month with no detection reads UNKNOWN rather than "
          + "zero. What could not be measured prints above what could, because a "
          + "number you cannot check is worth less than an honest gap.",
      },
    ],
    notServed: [
      "PIE writes nothing to Prophet 21, Sage X3 or Sage 100 — those connectors "
      + "are read-only. On NetSuite, Acumatica, Dynamics 365 Business Central and "
      + "Zoho Books the one thing it can create is the quote, and only if you "
      + "grant that permission separately.",
      "Your ERP remains the system of record. What PIE derives is rebuilt from "
      + "nothing by a complete re-sync, which is a property worth checking rather "
      + "than taking on trust.",
      "Special pricing agreements and manufacturer rebates are not modelled at "
      + "all, so on a rebate-heavy book the computed floor is not the floor you "
      + "actually have.",
      "No customer is named on this site and no case study is published, because "
      + "there is not yet one whose figures we have permission to print. Ask on "
      + "the call and you will get a straight answer about who is running it.",
    ],
    faq: [
      {
        question: "How do I know the AI is not quietly setting a price?",
        answer:
          "Because the deterministic packages cannot import the AI one, and a "
          + "test parses the imports rather than grepping them, so a package "
          + "mentioned in a comment cannot pass it. Turn the AI off and every "
          + "number on every screen still computes. That is a structural claim "
          + "rather than a policy one.",
        source: "the determinism band; tests/decision_platform/test_layer_boundaries.py",
      },
      {
        question: "What does PIE do to my ERP?",
        answer:
          "Reads it, over the permissions you grant, and each system's page "
          + "lists them exactly. Three of the seven connectors cannot write at "
          + "all. Nothing is created without a permission granted separately for "
          + "that purpose.",
        source: "the `/erp/` pages' permission lists",
      },
      {
        question: "Can we get our data out, or erased?",
        answer:
          "Yes to both — export and erasure are mechanisms in the product rather "
          + "than clauses in a contract, and erasure produces a receipt. Every "
          + "row carries the connector, the connection and the id it came from, "
          + "so what is held is enumerable rather than a matter of trust.",
        source: "the determinism band's “Your data” bullet",
      },
      {
        question: "What is it going to cost?",
        answer:
          "That depends on how many companies you connect, which ERP each one "
          + "sits on and how much catalogue there is to build, so this site "
          + "states no price rather than printing one that is wrong for you. It "
          + "is a conversation, and a short one.",
        source: "docs/marketing-placeholders.md §1 — the site states no price, deliberately",
      },
      {
        question: "What is the honest reason this might not work for us?",
        answer:
          "Three, in order. If a large share of your purchases is claimed back "
          + "through special pricing agreements, the floor PIE computes is not "
          + "your real floor and it will hold profitable lines. If your value is "
          + "in cross-manufacturer interchange outside metalworking, the engine "
          + "will find lines in your own book and will not propose equivalents. "
          + "And nothing resolves until somebody has uploaded your price lists "
          + "and confirmed how each is read — that is real work, done once.",
        source: "the limits section on each /industries/ page",
      },
    ],
  },
];
