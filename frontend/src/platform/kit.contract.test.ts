// `ui-standards.md` §10 is a table of shared components. This checks it is true.
//
// Two of its rows named components that were never written — `ChartContainer`
// and `AuditTimeline`. That is worse than listing nothing: the next person looks
// for the pattern, is told it already exists, cannot import it, and writes it by
// hand anyway. A standard that cannot be trusted on the easy claims does not get
// read on the hard ones.
//
// So the table is treated as an assertion about `kit.tsx`, and this is the
// assertion. Adding a row before the export now fails here rather than in
// somebody's afternoon.
/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

import standards from "../../../docs/ui-standards.md?raw";
import kit from "./kit.tsx?raw";

/** The rows of the §10 component table, by name.
 *
 *  Parsed rather than hardcoded — a list here would be a third copy of the same
 *  set, and the drift this test exists to catch would just move into it. */
function claimedComponents(): string[] {
  const section = standards.slice(
    standards.indexOf("## 10. Reusable components"),
    standards.indexOf("Where a surface wraps a business entity"));
  const table = section.slice(section.indexOf("| Component |"));
  return [...table.matchAll(/^\| `([A-Za-z][A-Za-z0-9]*)` \|/gm)].map((m) => m[1]);
}

describe("ui-standards §10 names components that exist", () => {
  it("finds the table at all", () => {
    // If the heading is renamed this test would otherwise pass vacuously, which
    // is the failure mode of every check that parses prose.
    const names = claimedComponents();
    expect(names.length).toBeGreaterThan(8);
    expect(names).toContain("SectionHeader");
  });

  it("exports every component the table claims", () => {
    const missing = claimedComponents().filter(
      (name) => !new RegExp(`export (function|const) ${name}\\b`).test(kit));
    expect(missing).toEqual([]);
  });
});
