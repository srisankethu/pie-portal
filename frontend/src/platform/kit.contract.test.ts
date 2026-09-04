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

// Navigation is an anchor; an action is a button.
//
// `InlineLink` renders both, and which one it renders is decided by the call
// site: `to` for somewhere, `onClick` for something. Getting that wrong is
// invisible on the screen — the two are styled identically, and a left click
// works either way — so the cost only shows up in what the browser can no
// longer do. Ctrl-click, middle-click, "open in a new tab", the destination on
// hover, and the announcement to a screen reader all come from the `href`.
//
// That was the whole point of the router migration `route.ts` opens by
// describing: "**nothing was a link**". The nav bar was fixed then and every
// name inside a screen was not, so the customer names in Payments, Lost
// Revenue, Bonds, Dependency and the migration matrix stayed buttons for as
// long as the URLs they went to have existed.
describe("an InlineLink that goes somewhere is a link", () => {
  const sources = import.meta.glob("../**/*.tsx", {
    query: "?raw", import: "default", eager: true,
  }) as Record<string, string>;

  /** Each `<InlineLink …>…</InlineLink>` in the tree, with its file. */
  const elements = Object.entries(sources)
    .filter(([path]) => !path.includes(".test."))
    .flatMap(([path, text]) => text.split("<InlineLink").slice(1).map((rest) => {
      const end = rest.indexOf("</InlineLink>");
      return { path, tag: end === -1 ? rest.slice(0, 300) : rest.slice(0, end) };
    }));

  it("finds the call sites at all", () => {
    // Without this the two assertions below pass on an empty list — the glob
    // silently returning nothing is the ordinary way a test like this rots.
    expect(elements.length).toBeGreaterThan(12);
    expect(elements.filter((e) => e.tag.includes("to={")).length)
      .toBeGreaterThanOrEqual(9);
  });

  it("never navigates from a click handler", () => {
    // `onNavigate`/`onOpen` are the props that carried a route to a parent's
    // `navigate()`; a direct `navigate(` is the same thing without the hop.
    // Any of them inside an InlineLink means a destination the browser has not
    // been told about.
    for (const { path, tag } of elements) {
      if (!tag.includes("onClick")) continue;
      const navigates = /\bonNavigate\(|\bonOpen\(|[^.\w]navigate\(/.test(tag);
      expect(navigates, `${path}: an InlineLink navigates from onClick — pass \`to\` instead`)
        .toBe(false);
    }
  });

  it("spells no URL out by hand", () => {
    // `route.ts` exists so the shape of a URL is decided in exactly one place,
    // and a literal "/account/…" at a call site is a second one — the kind
    // that still renders, still navigates, and silently stops matching the day
    // a path is renamed there.
    //
    // Stated as "no path literal" rather than "must call vizPath", which is
    // what this first asserted. That version failed on `Dependency`, whose
    // `to={openPath(id)}` is handed the function by a parent that does call
    // `vizPath` — correct code, and a rule that cannot see one hop would have
    // pushed it back towards a literal to satisfy the test.
    for (const { path, tag } of elements) {
      const to = tag.match(/\bto=\{([^]*?)\}\s*(?:\n|>|\s\w)/);
      if (!to) continue;
      const literalPath = /["'`]\//.test(to[1]);
      expect(literalPath, `${path}: to={${to[1].trim()}} writes a URL out by hand`)
        .toBe(false);
    }
  });
});
