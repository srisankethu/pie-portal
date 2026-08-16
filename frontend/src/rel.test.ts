// One line, identified and coloured the same way on every screen.
//
// Both functions here exist because there were two of each and they had
// drifted. `productRef` is the sharper story: `useQuoteIntelligence` asked the
// platform about `supplyCode || reqCode`, `DecisionSupport` asked about
// `supplyDesc || reqDesc || …` — so the two panels of one drawer looked up
// different things and each reported "no history" about a different product.
// On an unresolved line it was worse than inconsistent, because `reqDesc` holds
// the resolver's status message: the panel looked up a product called "No PIE
// match" and told the reader, in quotation marks, that it had no sales history.
//
// So what is worth pinning is not that the functions return something, but the
// two properties consolidating them bought: that the code is asked about rather
// than the description, and that the supply product wins — because the thing
// being priced is what will actually ship.
import { describe, expect, it } from "vitest";

import { productRef, relTone, statusTone } from "./rel";
import type { Line } from "./types";

/** Only the fields these functions read; the grid's Line is much wider. */
function line(over: Partial<Line> = {}): Line {
  return {
    reqCode: "CNMG120408",
    reqDesc: "Turning insert",
    supplyCode: "2001174",
    supplyDesc: "CNMG 120408 MP KCP25",
    ...over,
  } as Line;
}

describe("productRef", () => {
  it("asks about the product that will actually ship", () => {
    // Supply ahead of requested: the price and the history that matter belong
    // to what leaves the building.
    expect(productRef(line())).toBe("2001174");
  });

  it("falls back to the requested code when nothing has been selected yet", () => {
    expect(productRef(line({ supplyCode: null }))).toBe("CNMG120408");
    expect(productRef(line({ supplyCode: "" }))).toBe("CNMG120408");
  });

  it("never asks about a description", () => {
    // The bug in one assertion. `reqDesc` on an unresolved line is the
    // resolver's status message, not a product, and it must not reach a lookup.
    const unresolved = line({
      supplyCode: null, reqCode: "XZ-CUSTOM-778", reqDesc: "No PIE match",
    });
    expect(productRef(unresolved)).toBe("XZ-CUSTOM-778");
    expect(productRef(unresolved)).not.toBe("No PIE match");
  });
});

describe("relTone", () => {
  it("gives the settled relationships the tone the label already says", () => {
    expect(relTone("EXACT")).toBe("good");
    expect(relTone("TECH")).toBe("info");
    expect(relTone("AMBIGUOUS")).toBe("warn");
  });

  it("treats a line that cannot be offered as bad, both ways it happens", () => {
    expect(relTone("UNRESOLVED")).toBe("bad");
    expect(relTone("INCOMPATIBLE")).toBe("bad");
  });

  it("leaves an offerable-with-a-caveat relationship neutral", () => {
    // COMPAT, POSSIBLE, PIE_DOWN and NONE are all offerable; the caveat is in
    // the word, and the tone is a second cue rather than the only one.
    for (const rel of ["COMPAT", "POSSIBLE", "PIE_DOWN", "NONE"]) {
      expect(relTone(rel), rel).toBe("neutral");
    }
  });

  it("does not colour a relationship it has never heard of as good or bad", () => {
    // The safe direction for a value the server adds first: neutral prints the
    // label plainly, where "good" would be a green chip nobody decided on.
    expect(relTone("SOMETHING_NEW")).toBe("neutral");
    expect(relTone("")).toBe("neutral");
  });
});

describe("statusTone", () => {
  it("ranks the blocking kinds the way the screen has to read them", () => {
    // technical cannot be sold at all, operational needs somebody to act,
    // commercial needs a decision — three different things a reader does next.
    expect(statusTone("technical")).toBe("bad");
    expect(statusTone("operational")).toBe("warn");
    expect(statusTone("commercial")).toBe("info");
  });

  it("treats a line with nothing wrong as good", () => {
    expect(statusTone("ready")).toBe("good");
  });
});
