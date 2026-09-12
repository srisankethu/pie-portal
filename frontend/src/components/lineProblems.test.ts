/** What counts as a problem on a quote line, and what is offered to fix it.
 *
 * Two properties are worth more than the rest here, and both are about honesty
 * rather than layout.
 *
 * **A salesperson is told there is a problem.** The server withholds the cost
 * and the margin from that role, and the temptation is to withhold the strip
 * with them — which would leave the one person who has to do something about
 * the line unable to see that anything is wrong. The sentence changes; the
 * problem does not.
 *
 * **The fix is a number the server sent.** `recommended` is decision support
 * served to both roles. Nothing here derives a price, so there is no arithmetic
 * in this module for a test to catch drifting — what there is to catch is a
 * button offered when it would change nothing, which is how a fix stops being
 * read as one.
 */
import { describe, expect, it } from "vitest";

import { blockersFor, coverage, problemsFor } from "./lineProblems";
import type { Candidate, Line, LineIntelligence, Quote, QuoteException } from "../types";

function line(over: Partial<Line> = {}): Line {
  return {
    id: "l1", raw: "CNMG 120408 x 10", proposed: false, reading: "",
    reqCode: "CNMG120408", reqDesc: "Turning insert", reqQty: 10,
    rel: "EXACT", relLabel: "Identical",
    supplyCode: "2001174", supplyDesc: "CNMG 120408 MP KCP25",
    costBasis: "BOOKS", customCostSet: false, sel: "AUTO",
    avail: 40, availUnknown: false, inBooks: true, shortage: null,
    quoted: 1000, priceSource: "USER", recommended: 1000, lineTotal: 10000,
    createPhase: null, service: null, incompatReason: null,
    status: { kind: "ready", label: "ready" },
    flags: { attention: false, procurement: false, missingBooks: false,
             manualReview: false, unresolved: false, substituted: false },
    candidates: [], notes: [], substituted: false,
    ...over,
  };
}

function candidate(code: string): Candidate {
  return { code, desc: `${code} insert`, rel: "TECH", grade: null, brand: null,
           score: 0.92, reason: "same geometry", attributes: {} };
}

function exception(over: Partial<QuoteException> = {}): QuoteException {
  return {
    code: "NEGATIVE_MARGIN", severity: "CRITICAL",
    title: "Below cost on every piece",
    detail: "This rate needs an approval.",
    manager_detail: "₹8 below landed cost on 400 pieces.",
    impact_amount: null, impact_data_class: "MONEY" as QuoteException["impact_data_class"],
    reference_code: null, requires_approval: true, policy: true,
    ...over,
  };
}

function intel(over: Partial<LineIntelligence> = {}): LineIntelligence {
  return {
    line_id: "l1", product_id: "p1", product_ref: "2001174", resolved: true, qty: 10,
    quantity_band: { index: 0, low: 1, high: null, label: "any" },
    as_of: "2026-09-12", references: [], references_withheld: [],
    exceptions: [], worst_severity: null, requires_approval: false, blocking: false,
    data_quality: { data_sufficiency: "SUFFICIENT", reasons: [], transaction_count: 4 },
    thresholds_version: "v1", engine_version: "v1",
    ...over,
  };
}

const OPTS = { mgmt: true, systemShort: "Zoho" };

describe("what a line says is wrong with it", () => {
  it("says nothing about a line with nothing wrong", () => {
    expect(problemsFor(line(), intel(), OPTS)).toEqual([]);
  });

  it("asks which item an unresolved line is, offering what the engine ranked", () => {
    const problems = problemsFor(
      line({ supplyCode: null, candidates: [candidate("63201"), candidate("63204"),
                                            candidate("63207")] }),
      intel(), OPTS);
    expect(problems).toHaveLength(1);
    expect(problems[0].title).toBe("Which item is this?");
    expect(problems[0].blocking).toBe(true);
    // Two candidates and a way to the rest. A strip offering eleven is a list,
    // and a list belongs in the drawer built for it.
    expect(problems[0].fixes.map((f) => f.label))
      .toEqual(["63201 · tech", "63204 · tech", "Search"]);
  });

  it("asks for a reading to be checked before anything else on the line", () => {
    const problems = problemsFor(
      line({ proposed: true, supplyCode: null, reading: "12mm 4-flute end mill" }),
      intel(), OPTS);
    expect(problems[0].title).toBe("Check what was read from this line");
    expect(problems[0].detail).toContain("12mm 4-flute end mill");
  });

  it("tells a salesperson a price is blocked, without telling them why in money", () => {
    const blocked = intel({ exceptions: [exception()], blocking: true,
                            requires_approval: true });
    const theirs = problemsFor(line({ recommended: 1210 }), blocked,
                               { mgmt: false, systemShort: "Zoho" });
    const managers = problemsFor(line({ recommended: 1210 }), blocked, OPTS);

    // Both see it. Only one of them sees the cost sentence.
    expect(theirs[0].title).toBe("Below cost on every piece");
    expect(theirs[0].detail).toBe("This rate needs an approval.");
    expect(managers[0].detail).toContain("₹8 below landed cost");
    expect(theirs[0].blocking).toBe(true);
  });

  it("offers the recommended rate as the fix, and the ask beside it", () => {
    const problems = problemsFor(
      line({ quoted: 298, recommended: 361 }),
      intel({ exceptions: [exception()], blocking: true, requires_approval: true }),
      OPTS);
    expect(problems[0].fixes.map((f) => f.label))
      .toEqual(["Use the recommended rate", "Ask for approval"]);
    expect(problems[0].fixes[0].fix).toEqual({ kind: "set-price", price: 361 });
  });

  it("does not offer a rate the line already beats", () => {
    // A button that changes nothing is a button that teaches people to ignore
    // the strip it is on.
    const problems = problemsFor(
      line({ quoted: 900, recommended: 800 }),
      intel({ exceptions: [exception({ requires_approval: false })] }),
      OPTS);
    expect(problems[0].fixes).toEqual([]);
  });

  it("says an approval is waiting rather than offering to ask twice", () => {
    const problems = problemsFor(
      line({ quoted: 298, recommended: 361 }),
      intel({ exceptions: [exception()], blocking: true, requires_approval: true }),
      { ...OPTS, approvalPending: true });
    expect(problems[0].detail).toContain("Asked for");
    expect(problems[0].fixes.map((f) => f.label)).toEqual(["Use the recommended rate"]);
  });

  it("mentions a shortfall without holding the quote up for it", () => {
    const [problem] = problemsFor(line({ shortage: 90 }), intel(), OPTS);
    expect(problem.title).toBe("90 short against free stock");
    expect(problem.blocking).toBe(false);
  });

  it("offers no fixes at all on a quote this reader cannot change", () => {
    const problems = problemsFor(
      line({ supplyCode: null, candidates: [candidate("63201")] }), intel(),
      { ...OPTS, readOnly: true });
    expect(problems[0].fixes).toEqual([]);
    // Still says what is wrong: a reader who cannot fix it is often the person
    // who has to ask somebody who can.
    expect(problems[0].title).toBe("Which item is this?");
  });
});

function quote(over: Partial<Quote> = {}): Quote {
  return {
    id: "q1", number: "QT-1", customer: "Bharat Forge", customerId: "c1",
    lines: [line()], summary: { subtotal: 10000, tax: 1800, taxLabel: "GST",
      taxRate: 0.18, taxBasis: { known: 1, assumed: 0, defaultRate: 0.18 },
      grand: 11800, total: 1, unpriced: 0, atListPrice: 0 },
    filterCounts: {}, missingFields: [], canEdit: true,
    system: "zoho", systemLabel: "Zoho Books", systemShort: "Zoho",
    documentTerm: "estimate", booksLive: true,
    ...over,
  } as Quote;
}

describe("what is still stopping the send", () => {
  it("counts nothing on a quote that is ready", () => {
    expect(blockersFor(quote(), {}, null)).toEqual([]);
  });

  it("counts a kind of problem once, not once per line", () => {
    // Three unresolved lines are one thing to settle and three rows to look at.
    // Counting seven things over three rows is the bar arguing with the grid.
    const lines = [line({ id: "a", supplyCode: null }),
                   line({ id: "b", supplyCode: null }),
                   line({ id: "c", supplyCode: null })];
    const blockers = blockersFor(quote({ lines }), {}, null);
    expect(blockers).toHaveLength(1);
    expect(blockers[0].text).toBe("3 lines have no item yet");
  });

  it("says it in the singular when there is one", () => {
    const blockers = blockersFor(quote({ lines: [line({ supplyCode: null })] }), {}, null);
    expect(blockers[0].text).toBe("1 line has no item yet");
  });

  it("names the details the organization requires, and the missing customer", () => {
    const blockers = blockersFor(
      quote({ customer: "  ", missingFields: ["Delivery terms", "Validity"] }), {}, null);
    expect(blockers.map((b) => b.text)).toEqual([
      "2 required details: Delivery terms, Validity",
      "no customer chosen",
    ]);
  });

  it("takes the gate's own sentence when the lines do not account for it", () => {
    const blockers = blockersFor(quote(), {}, "A manager must approve this quote.");
    expect(blockers).toEqual([{ key: "gate", text: "A manager must approve this quote." }]);
  });

  it("does not say the same thing twice when the lines already carry it", () => {
    const blockers = blockersFor(
      quote(), { l1: intel({ blocking: true }) }, "One line needs approval.");
    expect(blockers.map((b) => b.key)).toEqual(["approval"]);
  });
});

describe("what the total covers", () => {
  it("says so plainly when it covers everything", () => {
    expect(coverage(quote())).toBe("Covers all 1 lines.");
  });

  it("says what is missing rather than letting the figure imply completeness", () => {
    const q = quote({
      lines: [line({ id: "a" }), line({ id: "b" }), line({ id: "c", quoted: null })],
      summary: { ...quote().summary, unpriced: 1 },
    });
    expect(coverage(q)).toContain("Covers 2 of 3 lines");
    expect(coverage(q)).toContain("missing from this total rather than counted as zero");
  });
});
