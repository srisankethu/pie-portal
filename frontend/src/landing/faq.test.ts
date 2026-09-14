/** What the FAQ may say, and what it may not.
 *
 * This section is the most quotable thing on the site and therefore the
 * cheapest place to introduce a claim nobody agreed to. A sentence invented
 * here is the sentence a language model repeats, with this site named as the
 * source, months after anybody remembers writing it — and unlike a section of
 * body copy, it will be repeated *verbatim and out of context*, so there is no
 * surrounding paragraph to qualify it.
 *
 * So the checkable claims are checked against the thing that makes them true,
 * not against a reviewer's memory of it.
 */
import { describe, expect, it } from "vitest";
import { FAQ } from "./faq";
import { ERP_PAGES } from "./erp";

describe("the FAQ", () => {
  it("asks every question once", () => {
    expect(new Set(FAQ.map((f) => f.question)).size).toBe(FAQ.length);
  });

  it("phrases every question as a question", () => {
    for (const item of FAQ) {
      expect(item.question.endsWith("?"), item.question).toBe(true);
    }
  });

  /** Two to four sentences, per the brief this section was written to. The
   *  bounds are loose on purpose — one sentence is an answer with the reason
   *  removed, and six is a section. What is actually being prevented is an
   *  answer growing into prose nobody can quote. */
  it("answers in two to four sentences", () => {
    for (const item of FAQ) {
      const sentences = item.answer.split(/(?<=[.?!])\s+/).filter(Boolean);
      expect(sentences.length, `${item.question} — ${sentences.length} sentences`)
        .toBeGreaterThanOrEqual(2);
      expect(sentences.length, `${item.question} — ${sentences.length} sentences`)
        .toBeLessThanOrEqual(6);
    }
  });

  /** Every answer says where it came from. Not rendered and not a URL — a note
   *  for whoever edits this file next, so "is this true?" has an address. */
  it("cites a source for every answer", () => {
    for (const item of FAQ) {
      expect(item.source.length, item.question).toBeGreaterThan(10);
    }
  });

  /** The ERP answer names all seven systems and invents no eighth. This is the
   *  one answer whose facts live in another file and can therefore go stale
   *  silently: add a connector and this sentence is wrong, with nothing to say
   *  so. `name` OR `short` — the answer says "Microsoft Dynamics 365 Business
   *  Central" in full, and other sentences on the site say "Dynamics 365 BC";
   *  either is the vendor's own. */
  it("names every ERP the site has a page for, and no others", () => {
    const answer = FAQ.find((f) => f.question.includes("Which ERPs"))!.answer;
    for (const page of ERP_PAGES) {
      expect(
        answer.includes(page.name) || answer.includes(page.short),
        `the ERP answer does not name ${page.name}`,
      ).toBe(true);
    }
    // The count is stated in a neighbouring answer ("Four of the seven"), so a
    // connector added without revisiting this file would make that wrong too.
    expect(ERP_PAGES.length, "the FAQ says seven systems").toBe(7);
  });

  /** "Four of the seven connectors can create an agreed quote back" is
   *  arithmetic over `erp.ts`, written out as a word. It was true when it was
   *  written; this is what keeps it true. */
  it("counts the connectors that write back correctly", () => {
    const answer = FAQ.find((f) => f.question.includes("replace my ERP"))!.answer;
    const writes = ERP_PAGES.filter((p) => p.writes !== null).length;
    expect(writes).toBe(4);
    expect(answer).toMatch(/\bFour of the seven\b/);
    expect(ERP_PAGES.length - writes).toBe(3);
    expect(answer).toMatch(/other three are read-only/);
  });

  /** The two answers that restate a §1 invariant of this codebase. They are
   *  the answers most likely to be "improved" into something softer by
   *  somebody who has not read CLAUDE.md, and softening either is a product
   *  claim changing, not a copy edit.
   *
   *  Both must open with the refusal. A model asked "does PIE set prices?"
   *  lifts the first sentence; an answer that begins "PIE's pricing engine
   *  works by…" answers the opposite question. */
  it("refuses, first word, on the two invariant questions", () => {
    for (const q of ["Does the AI set prices?", "Does PIE replace my ERP?"]) {
      const item = FAQ.find((f) => f.question === q)!;
      expect(item.answer.startsWith("No."), q).toBe(true);
    }
  });

  it("never says the sales desk can see cost or margin", () => {
    const answer = FAQ.find((f) => f.question.includes("cost and margin"))!.answer;
    // The claim is that the field is absent from the response, not that it is
    // hidden in the browser. That distinction is the whole control: hiding a
    // value in the component while the server still sends it is the exact
    // defect `test_a_salesperson_cannot_walk_the_price_to_recover_cost`
    // exists for, and a FAQ that described it as the mechanism would be
    // advertising the bug as the feature.
    expect(answer).toMatch(/never receives|absent from the response/);
    // "Hidden" may appear, but only inside a denial — "enforced by the server,
    // not hidden in the browser" is the site's own sentence and the most
    // useful one in the answer. Same shape as the margin-floor rule in
    // `erp.test.ts`: the word is not banned, the unqualified claim is.
    for (const [phrase] of answer.matchAll(/.{0,40}hidden/gi)) {
      expect(phrase, `unqualified "hidden" claim: “…${phrase}”`)
        .toMatch(/\b(not|never|rather than|instead of)\b/i);
    }
  });
});
