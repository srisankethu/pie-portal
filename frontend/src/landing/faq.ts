/** The questions a reader arrives with, answered in one place.
 *
 * Read by two consumers that must never disagree: the visible FAQ section at
 * the bottom of the landing page, and the `FAQPage` JSON-LD the prerenderer
 * injects into the same document. Google's own rule for that schema is that
 * the question and the answer have to be *visible on the page* — so a second
 * copy of these strings written for the crawler would be both a lie and a
 * manual action waiting to happen. One array, two renderers.
 *
 * These answers are written to be extracted, not to persuade. A language model
 * quoting this site will quote a sentence, not a section, so each answer opens
 * with the answer — "No.", "18 months." — and explains afterwards. Marketing
 * voice actively hurts here: a model asked "does PIE set prices?" needs a
 * sentence it can lift verbatim, and "PIE puts you back in control of your
 * margins" is not one.
 *
 * **Every claim below already appears elsewhere on this site**, and that is a
 * hard rule rather than a preference — `faq.test.ts` pins the ones that are
 * checkable against their source. The FAQ is the most quotable thing on the
 * page and therefore the cheapest place to introduce a claim nobody agreed to:
 * a sentence invented here would be the one a model repeats, with this site
 * named as the source, long after anybody remembers writing it. Where a number
 * is stated (18 months, seven systems, four that write back) it is the number
 * the product actually has, and it is stated in the same words the section it
 * comes from uses.
 */

export interface FaqItem {
  /** The question, phrased as a reader would ask it rather than as the site
   *  would title it. "Does the AI set prices?" is what somebody types; "AI
   *  determinism" is what a section header says. */
  question: string;
  /** Two to four sentences. The answer first, then what makes it true. */
  answer: string;
  /** Where on this site the claim already lives, for the reader of this file
   *  and for `faq.test.ts`. Not rendered. */
  source: string;
}

export const FAQ: FaqItem[] = [
  {
    question: "What is PIE?",
    answer:
      "PIE is a margin-control layer for B2B distributors. It reads the books you "
      + "already run, checks every quote line against a margin floor you set before "
      + "the quote goes out, holds what breaches that floor for a named approver, and "
      + "reports the margin that held. It works on top of your ERP rather than "
      + "replacing it.",
    source: "index.html meta description; Landing.tsx §outcomes",
  },
  {
    question: "Which ERPs does PIE connect to?",
    answer:
      "Zoho Books, Oracle NetSuite, Microsoft Dynamics 365 Business Central, "
      + "Acumatica, Epicor Prophet 21, Sage X3 and Sage 100. Every connector reads "
      + "customers, vendors, the item master and invoice history; what else it reads, "
      + "and whether it can write anything back, differs by system and is set out on "
      + "that system's own page.",
    source: "erp.ts ERP_PAGES — the seven pages under /erp/",
  },
  {
    question: "Does PIE replace my ERP?",
    answer:
      "No. Your ERP stays the system of record and PIE works on top of it. Four of "
      + "the seven connectors can create an agreed quote back in your ERP as an "
      + "estimate or a sales quote, and that is the only thing any of them ever "
      + "creates; the other three are read-only. Nothing else is written to your "
      + "books at all.",
    source: "erp.ts `writes` — 4 non-null, 3 null; Landing.tsx §ownership",
  },
  {
    question: "Does the AI set prices?",
    answer:
      "No. Every figure is deterministic arithmetic on your own records — turn the "
      + "AI off and every number still works. The AI reads those numbers and explains "
      + "them; it never produces one. Same inputs, same answer, every time, with a "
      + "paper trail that names the policy each figure was computed under.",
    source: "shared.tsx TrustBand — \"The AI never computes a single number.\"",
  },
  {
    question: "Who can see cost and margin?",
    answer:
      "Owners and finance see cost, margin and the policy behind every decision. An "
      + "approver sees cost, the floor and the recommended price on the line that was "
      + "held, with the rule that stopped it named. The sales desk never receives a "
      + "cost or margin field at all — it gets the floor, the recommended price and "
      + "the customer's own history. What each role can see is enforced by the "
      + "server, not hidden in the browser: the fields are absent from the response, "
      + "so there is nothing to read out of a network tab.",
    source: "Landing.tsx §roles — the three role panels",
  },
  {
    question: "How far back does the first sync read?",
    answer:
      "18 months by default, from the first day of that month: customers, items, "
      + "invoices and bills, plus payments and orders where the system exposes them. "
      + "Set an earlier date before it runs and it reads from there instead.",
    source: "Landing.tsx §how step 1; ErpPage.tsx",
  },
  {
    question: "What happens to my data if I leave?",
    answer:
      "One export gives you everything your organization owns, whenever the owner "
      + "asks for it, and your decoded catalog is part of it. Erase and your tenant "
      + "key is destroyed, which makes the ciphertext written under it inert wherever "
      + "it lives — live tables, replicas and backups alike. What was never encrypted "
      + "stays readable, and the signed receipt names both halves rather than the "
      + "flattering one.",
    source: "Landing.tsx §ownership — export and erasure panels",
  },
];
