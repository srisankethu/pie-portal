// Which of the five a screen is behind, and what each strip offers.
//
// `destinations.ts` is one table read by four surfaces — the shell's nav
// highlight, the tab strips, the command palette and the Evidence index — and
// its failure mode is not a crash. It is a screen listed in the wrong place,
// which renders perfectly: `destinationFor` uses `find`, so a screen claimed by
// two destinations quietly belongs to whichever appears first in the array,
// and a tab whose screen its own destination does not cover puts the strip on
// screen while the nav marks a different one of the five current. Both look
// like a working page to whoever wrote them and like being lost to whoever is
// reading it.
//
// Written against the exported tables rather than a list restated here. A
// second copy of the tab set would agree with the first right up until somebody
// adds a tab.
import { describe, expect, it } from "vitest";

import { defineAbilityFor } from "./ability";
import {
  ACCOUNT_TABS, DESTINATIONS, EVIDENCE, MONEY_TABS, SETUP_TABS,
  destinationFor, visibleTabs, type DestinationTab,
} from "./destinations";
import type { Screen } from "./route";

/** Every strip, with the destination it hangs under. */
const STRIPS = [
  ["accounts", ACCOUNT_TABS],
  ["money", MONEY_TABS],
  ["setup", SETUP_TABS],
] as const;

describe("Accounts holds both sides of the book", () => {
  it("offers Customers and Vendors, and the two views that read both", () => {
    // The whole point of the destination. It carried Customers alone while the
    // vendor side and the two both-sides views were reachable only from the
    // Evidence library — a place named after the counterparties that knew
    // about the half that buys.
    expect(ACCOUNT_TABS.map((t) => t.label))
      .toEqual(["Customers", "Vendors", "Relationships", "Dependencies"]);
  });

  it("keeps every tab in the strip — none of them is overflow", () => {
    // Four is the number the strip is sized for; a `More` menu here would be
    // hiding one of two sides of the business behind a click.
    expect(ACCOUNT_TABS.filter((t) => t.secondary)).toEqual([]);
  });

  it("shows a salesperson the three tabs whose endpoints answer them", () => {
    // `/supply` is manager-or-owner at the door, so Vendors is gated to match.
    // `bonds` and `dependency` are not: the server drops their supplier half
    // for a salesperson and answers the customer half, so both are real
    // screens for every role and gating them here would withhold a page the
    // API would have served.
    const sales = visibleTabs(ACCOUNT_TABS, defineAbilityFor("SALESPERSON"));
    expect(sales.map((t) => t.label))
      .toEqual(["Customers", "Relationships", "Dependencies"]);

    const owner = visibleTabs(ACCOUNT_TABS, defineAbilityFor("OWNER"));
    expect(owner).toHaveLength(ACCOUNT_TABS.length);
  });

  it("puts an account and one of its items in Accounts, tabs or not", () => {
    // Neither is a tab — they are what got chosen from one — and both must
    // still say which of the five they are in.
    expect(destinationFor("customer")).toBe("accounts");
    expect(destinationFor("customerItem")).toBe("accounts");
  });
});

describe("the table cannot put a screen in two places", () => {
  it("claims each screen exactly once", () => {
    // `destinationFor` takes the first destination that covers a screen, so a
    // screen listed twice does not fail — it silently belongs to whichever is
    // earlier in the array. That is how `supply`, `bonds` and `dependency`
    // would have stayed Today's after becoming Accounts tabs.
    const seen = new Map<Screen, string>();
    const twice: string[] = [];
    for (const d of DESTINATIONS) {
      for (const s of d.covers) {
        const first = seen.get(s);
        if (first) twice.push(`${s}: ${first} and ${d.key}`);
        else seen.set(s, d.key);
      }
    }
    expect(twice).toEqual([]);
  });

  it("covers every screen its own strip offers", () => {
    // A tab renders the strip; `covers` decides the nav highlight. When they
    // disagree the reader gets Accounts' tabs under a nav marking Today, which
    // is worse than either alone — the page says one place and the shell says
    // another.
    const stray: string[] = [];
    for (const [key, tabs] of STRIPS) {
      for (const t of tabs as readonly DestinationTab[]) {
        if (destinationFor(t.screen) !== key) {
          stray.push(`${key} · ${t.label} → ${destinationFor(t.screen)}`);
        }
      }
    }
    expect(stray).toEqual([]);
  });
});

describe("the Evidence library and the strips agree on a name", () => {
  it("calls a screen the same thing wherever it is a door", () => {
    // Four Evidence cards open a screen that is also a tab — deliberate, and
    // the reason the names have to match. "Suppliers" in the library opening a
    // tab labelled "Vendors" is the two-nav-items-for-one-thing this file's own
    // header is a record of, one level down.
    const named = new Map<Screen, string>(
      EVIDENCE.map((e) => [e.screen, e.name]));
    const disagree: string[] = [];
    for (const [, tabs] of STRIPS) {
      for (const t of tabs as readonly DestinationTab[]) {
        const card = named.get(t.screen);
        // Plural on the tab, singular on the card, or the other way round, is
        // the same word — `Dependencies` and `Dependency` are not a rename.
        if (card && !card.startsWith(t.label.replace(/e?s$/, ""))) {
          disagree.push(`${t.label} vs ${card}`);
        }
      }
    }
    expect(disagree).toEqual([]);
  });
});
