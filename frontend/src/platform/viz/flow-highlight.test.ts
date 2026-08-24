// The flow map's highlight has to cross every stage, and no more than that.
//
// Two failures look almost identical on screen and are opposites underneath: a
// walk that stops one stage short leaves the customers dark, and a walk that
// mixes directions lights competitors who merely share a line. Both were
// reachable from the one-hop test this module replaced, so both are pinned.

import { describe, expect, it } from "vitest";

import { pathThroughLink, pathThroughNode, type Edge } from "./flow-highlight";

// Two principals, two lines, two customers — the smallest book that can show
// both failures. Kennametal and Sandvik both feed Milling; only Kennametal
// feeds Turning.
//
//   0:kmt ─┬─ 1:milling ─┬─ 2:acme
//          │             └─ 2:bosch
//          └─ 1:turning  ─── 2:acme
//   0:sdv ──── 1:milling
const BOOK: Edge[] = [
  { source: "0:kmt", target: "1:milling" },
  { source: "0:kmt", target: "1:turning" },
  { source: "0:sdv", target: "1:milling" },
  { source: "1:milling", target: "2:acme" },
  { source: "1:milling", target: "2:bosch" },
  { source: "1:turning", target: "2:acme" },
];

describe("pathThroughNode", () => {
  it("reaches the customers, not just the lines", () => {
    // The bug this module exists for: one hop from a principal lit the lines
    // and dimmed every customer behind them.
    const on = pathThroughNode("0:kmt", BOOK);
    expect(on.has("1:milling")).toBe(true);
    expect(on.has("2:acme")).toBe(true);
    expect(on.has("2:bosch")).toBe(true);
  });

  it("never walks forward then backward into a competitor", () => {
    // Sandvik shares Milling with Kennametal. A both-directions flood would
    // step kmt → milling → sdv and light a principal you are not tracing.
    expect(pathThroughNode("0:kmt", BOOK).has("0:sdv")).toBe(false);
  });

  it("lights both ends when seeded in the middle", () => {
    const on = pathThroughNode("1:milling", BOOK);
    expect([...on].sort()).toEqual(
      ["0:kmt", "0:sdv", "1:milling", "2:acme", "2:bosch"]);
    // Turning is neither upstream nor downstream of Milling.
    expect(on.has("1:turning")).toBe(false);
  });

  it("walks back to the principals when seeded at a customer", () => {
    const on = pathThroughNode("2:bosch", BOOK);
    expect([...on].sort()).toEqual(["0:kmt", "0:sdv", "1:milling", "2:bosch"]);
  });

  it("includes the seed even when nothing connects to it", () => {
    expect([...pathThroughNode("0:orphan", BOOK)]).toEqual(["0:orphan"]);
  });

  it("terminates on a cycle rather than hanging the render", () => {
    const looped: Edge[] = [
      { source: "a", target: "b" },
      { source: "b", target: "a" },
    ];
    expect([...pathThroughNode("a", looped).values()].sort()).toEqual(["a", "b"]);
  });
});

describe("pathThroughLink", () => {
  it("lights one band's path, not the source's whole fan", () => {
    // Pointing at Kennametal→Milling must not light Kennametal→Turning.
    const on = pathThroughLink(
      { source: "0:kmt", target: "1:milling" }, BOOK);
    expect(on.has("1:milling")).toBe(true);
    expect(on.has("1:turning")).toBe(false);
  });

  it("keeps the principals that feed the band it ends at", () => {
    const on = pathThroughLink(
      { source: "1:milling", target: "2:acme" }, BOOK);
    expect([...on].sort()).toEqual(
      ["0:kmt", "0:sdv", "1:milling", "2:acme"]);
    // The other customer of Milling is off this band's path.
    expect(on.has("2:bosch")).toBe(false);
  });
});

describe("the both-endpoints rule callers use", () => {
  // The component dims a ribbon unless both of its ends are lit. That is only
  // exact because the graph is layered — this asserts the property directly
  // rather than trusting the argument in the module comment.
  const litLinks = (on: Set<string>) =>
    BOOK.filter((l) => on.has(l.source) && on.has(l.target))
      .map((l) => `${l.source}>${l.target}`).sort();

  it("selects exactly the bands under a principal", () => {
    expect(litLinks(pathThroughNode("0:kmt", BOOK))).toEqual([
      "0:kmt>1:milling", "0:kmt>1:turning",
      "1:milling>2:acme", "1:milling>2:bosch", "1:turning>2:acme",
    ]);
  });

  it("leaves the competitor's own band dark", () => {
    expect(litLinks(pathThroughNode("0:kmt", BOOK)))
      .not.toContain("0:sdv>1:milling");
  });
});
