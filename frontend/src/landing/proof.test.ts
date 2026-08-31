/** Content appears when it is real, and not before.
 *
 * The site ships with most of Section F empty, and it has to be deployable in
 * that state — which means the empty case is not an edge case here, it is the
 * shipping case. These pin the rules that make it safe: partial content never
 * half-renders, a token never counts as content, and the section knows when it
 * has nothing to say.
 */
import { describe, expect, it } from "vitest";

import { allFilled, filled, isPlaceholder } from "./content";
import { caseStudy, complianceRows, hasProof, namedCustomers } from "./proof";

describe("isPlaceholder / filled", () => {
  it("treats an unreplaced token as no content", () => {
    expect(isPlaceholder("{{CUSTOMER_LOGO_1}}")).toBe(true);
    expect(isPlaceholder("  {{CASE_STUDY_ERP}}  ")).toBe(true);
    expect(filled("{{X_Y}}")).toBeNull();
  });

  it("treats empty and missing as no content", () => {
    // A blank string renders as nothing *and* passes half the truthiness
    // checks it meets, which is how an empty panel reaches a page.
    for (const empty of ["", "   ", null, undefined]) {
      expect(isPlaceholder(empty)).toBe(true);
      expect(filled(empty)).toBeNull();
    }
  });

  it("passes real content through, trimmed", () => {
    expect(filled("  Ohio Fastener Co.  ")).toBe("Ohio Fastener Co.");
    expect(isPlaceholder("Ohio Fastener Co.")).toBe(false);
    // Prose that merely mentions braces is content, not a token.
    expect(isPlaceholder("we resolve {size} codes")).toBe(false);
  });
});

describe("allFilled", () => {
  it("returns nothing unless every field is real", () => {
    expect(allFilled({ a: "one", b: "{{B}}" })).toBeNull();
    expect(allFilled({ a: "one", b: "" })).toBeNull();
  });

  it("returns the whole set when it is complete", () => {
    expect(allFilled({ a: "one", b: "two" })).toEqual({ a: "one", b: "two" });
  });
});

describe("Section F, as it ships today", () => {
  it("has no customers, no case study and no compliance status", () => {
    // This is the assertion that changes on the day real content lands, and
    // it should — the tests below say what happens in each state, and this
    // one records which state the repository is in.
    expect(namedCustomers()).toEqual([]);
    expect(caseStudy()).toBeNull();
    expect(complianceRows()).toEqual([]);
  });

  it("therefore says it has nothing to show, so the section is hidden", () => {
    expect(hasProof()).toBe(false);
  });
});

describe("the case study is all or nothing", () => {
  it("is withheld while any field is missing", () => {
    // A case study missing its figure is not a shorter case study — it is a
    // claim with the evidence taken out, which is worse than no case study.
    // Eight fields are declared; today none is real, so this holds trivially,
    // and `allFilled` above pins the rule itself on data under test control.
    expect(caseStudy()).toBeNull();
    expect(allFilled({ profile: "A fastener distributor", margin: "{{M}}" })).toBeNull();
  });
});
