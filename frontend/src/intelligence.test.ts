// Indexing the assessment back onto the grid's own lines.
//
// `intelligence.ts` is mostly a thin wrapper over four endpoints — nothing to
// test there that the contract test and the server suite do not already own.
// The one piece of logic is `byLine`, and it earns a test because of what
// happens when it is wrong: the drawer reads `byLine(data)[line.id]`, so a
// mismatch does not throw or render an error. It renders *nothing*, on a panel
// whose whole job is to say what the platform knows about this line, and an
// empty panel is indistinguishable from "we have no history for this product".
//
// The wrapper stopped being entirely thin when the outcome path learned to name
// an ERP-raised quote. What a failed outcome *says* is now the useful half of
// it — 422 names every loss reason a person may choose, 409 names the two books
// an ambiguous reference is torn between — and no server test can pin that,
// because the server demonstrably sends both and the question is whether this
// client still has them by the time a screen catches. That is the last block
// below.
import { afterEach, describe, expect, it, vi } from "vitest";

import { SEVERITY_LABEL, byLine, intelligence } from "./intelligence";
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

// ── what a refused outcome tells the person who hit it ──────────────────────

/** One canned response, and the request that reached it. */
function answering(status: number, body: string, contentType = "application/json") {
  const seen: { body?: Record<string, unknown> } = {};
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    seen.body = JSON.parse(String(init.body));
    return new Response(body, { status, headers: { "Content-Type": contentType } });
  }));
  return seen;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("recording an outcome on an ERP-raised quote", () => {
  it("names the quote by the source system's reference, not by quote_id", async () => {
    const seen = answering(200, JSON.stringify({ quote_id: null }));
    await intelligence.documentOutcome("tok", "EST-4471", "WON", "Pitti");

    // `quote_id` travelling as undefined is the point: the server treats the
    // two keys as alternatives, and a "" here would name a platform quote that
    // does not exist rather than leaving the ERP reference to identify it.
    expect(seen.body).toMatchObject({ quote_document_ref: "EST-4471",
                                      status: "WON", customer: "Pitti" });
    expect(seen.body).not.toHaveProperty("quote_id");
  });

  it("keeps the 422 that lists every loss reason a person may choose", async () => {
    // Trimmed, but the shape the server really sends: `detail` is a plain
    // string carrying the choices, deliberately not a list.
    const detail = "Recording a quote as lost needs a reason: PRICE, DELIVERY, "
      + "COMPETITOR, CUSTOMER_CANCELLED, NO_DECISION.";
    answering(422, JSON.stringify({ detail }));

    await expect(intelligence.documentOutcome("tok", "EST-4471", "LOST"))
      .rejects.toMatchObject({ message: detail, status: 422 });
  });

  it("keeps the 409 that names both books an ambiguous reference means", async () => {
    const detail = "'Q-19' names 2 quotes in this organization (zoho/conn-a, "
      + "zoho/conn-b). An ERP reference is unique only inside one connected "
      + "company's book…";
    answering(409, JSON.stringify({ detail }));

    // Distinguishable from the 422 by `status` alone, which is what lets a
    // screen say "pick a reason" for one and "this cannot be recorded" for the
    // other without matching on prose.
    await expect(intelligence.documentOutcome("tok", "Q-19", "LOST"))
      .rejects.toMatchObject({ message: detail, status: 409 });
  });

  it("does not swallow a body that is not JSON", async () => {
    // A crash that escapes FastAPI's handlers arrives as plain text. Parsing as
    // JSON and giving up reported a bare status line and threw away the only
    // description of what went wrong.
    answering(500, "Traceback: something specific", "text/plain");

    await expect(intelligence.documentOutcome("tok", "EST-4471", "WON"))
      .rejects.toThrow("Traceback: something specific");
  });
});
