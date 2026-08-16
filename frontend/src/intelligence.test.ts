// Indexing the assessment back onto the grid's own lines.
//
// `intelligence.ts` is mostly a thin wrapper over four endpoints — nothing to
// test there that the contract test and the server suite do not already own.
// The one piece of logic is `byLine`, and it earns a test because of what
// happens when it is wrong: the drawer reads `byLine(data)[line.id]`, so a
// mismatch does not throw or render an error. It renders *nothing*, on a panel
// whose whole job is to say what the platform knows about this line, and an
// empty panel is indistinguishable from "we have no history for this product".
import { describe, expect, it } from "vitest";

import { SEVERITY_LABEL, byLine } from "./intelligence";
import type { LineIntelligence, QuoteIntelligence } from "./types";

function result(...lineIds: string[]): QuoteIntelligence {
  return {
    lines: lineIds.map((line_id) => ({ line_id } as LineIntelligence)),
  } as QuoteIntelligence;
}

describe("byLine", () => {
  it("keys the results by the Quote Builder's own line id", () => {
    const indexed = byLine(result("l1", "l2"));
    expect(Object.keys(indexed).sort()).toEqual(["l1", "l2"]);
    expect(indexed.l1.line_id).toBe("l1");
  });

  it("returns an empty index rather than throwing when there is nothing yet", () => {
    // The drawer renders before the first assessment lands, and on a quote that
    // was never assessed. Both must be an empty panel, not a crash.
    expect(byLine(null)).toEqual({});
    expect(byLine({} as QuoteIntelligence)).toEqual({});
    expect(byLine(result())).toEqual({});
  });

  it("answers undefined for a line the assessment did not cover", () => {
    // Not an empty object: the drawer distinguishes "no intelligence for this
    // line" from "intelligence that found nothing", and only the first is a
    // reason to say so.
    expect(byLine(result("l1")).l2).toBeUndefined();
  });
});

describe("SEVERITY_LABEL", () => {
  it("says what to do rather than how bad it is", () => {
    // The severities arrive as CRITICAL/WARNING/INFO; on screen they are the
    // reader's next action, which is the difference between a chip somebody
    // acts on and a chip somebody learns to scroll past.
    expect(SEVERITY_LABEL.CRITICAL).toBe("Approval needed");
    expect(SEVERITY_LABEL.WARNING).toBe("Check price");
    expect(SEVERITY_LABEL.INFO).toBe("Context");
  });

  it("covers every severity the server can send", () => {
    // Mirrors `Severity` in types.ts. A missing key renders `undefined` in a
    // chip, which reads as a bug in the analysis rather than a gap in a table.
    expect(Object.keys(SEVERITY_LABEL).sort())
      .toEqual(["CRITICAL", "INFO", "WARNING"]);
  });
});
