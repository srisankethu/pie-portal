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
import { PATH, type Screen } from "./route";

// The screens whose door is a link written into a screen rather than a row in
// `destinations.ts`, read as text through Vite's `?raw`. Restating a link here
// would be a copy that stops matching, which is the reason `route.test.ts`
// reads the server's own destination list the same way.
import platformAppSource from "./PlatformApp.tsx?raw";
import queueSource from "./today/queue.ts?raw";
import todaySource from "./today/TodayScreen.tsx?raw";

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
    // Three Evidence cards open a screen that is also a tab — deliberate, and
    // the reason the names have to match. "Suppliers" in the library opening a
    // tab labelled "Vendors" is the two-nav-items-for-one-thing this file's own
    // header is a record of, one level down.
    //
    // Three, not the four this comment used to claim: it counted `Won & lost`
    // as a Quotes tab and there is no Quotes strip. The loop below only ever
    // walks the strips, so the miscount could not fail anything here — which
    // is why the door census in the next block exists.
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

/** Every screen a table puts a door on. */
function tableDoors(): Set<Screen> {
  const doors = new Set<Screen>();
  for (const d of DESTINATIONS) doors.add(d.screen);
  for (const [, tabs] of STRIPS) {
    for (const t of tabs as readonly DestinationTab[]) doors.add(t.screen);
  }
  for (const e of EVIDENCE) doors.add(e.screen);
  return doors;
}

/** The rest: screens somebody arrives at by clicking a link inside another
 *  screen, and the file that writes that link.
 *
 *  Four of the six are Today's — it is the home screen, and it links down to
 *  the whole pile behind each kind of thing in its queue. The other two are
 *  what got chosen from a list, which is the one honest reason for a screen to
 *  have no door of its own: a decision and one item on an account do not
 *  belong in a nav, because there is nothing to put there until a row is
 *  clicked.
 *
 *  Not a licence to skip the tables. Every entry names a file, and the census
 *  reads that file for the reference — an entry whose link has been deleted
 *  fails here rather than quietly stranding the screen, and an entry added to
 *  wave a new screen past fails the minimality check below. The command
 *  palette is deliberately absent: it is built from the same tables plus these
 *  screens, so counting it would be counting a table twice. */
const DOORS_IN_CODE: Readonly<Record<string, { source: string; where: string }>> = {
  detail: { source: queueSource, where: "today/queue.ts" },
  customerItem: { source: platformAppSource, where: "PlatformApp.tsx" },
  list: { source: todaySource, where: "today/TodayScreen.tsx" },
  approvals: { source: queueSource, where: "today/queue.ts" },
  unrecordedQuotes: { source: queueSource, where: "today/queue.ts" },
  evidence: { source: queueSource, where: "today/queue.ts" },
};

describe("every screen with an address has a door", () => {
  it("leaves none of them reachable only by typing the URL", () => {
    // The check this file did not have. `covers` looked like it: every screen
    // is in one, and `journey` was in Today's from the day the five
    // destinations were written. But `covers` is the nav highlight — what
    // looks current once you are standing on a screen — and it answers a
    // different question from "how does anybody get here". `journey` had an
    // address, a route, a passing round-trip in `route.test.ts` and no nav
    // item, tab, card or palette row: reachable only from a storyboard beat,
    // once, and never again from anywhere in the product.
    const doors = tableDoors();
    const stranded = (Object.keys(PATH) as Screen[])
      .filter((s) => !doors.has(s) && !(s in DOORS_IN_CODE));
    expect(stranded).toEqual([]);
  });

  it("finds every link the code-door list claims, in the file it names", () => {
    const missing: string[] = [];
    for (const [screen, door] of Object.entries(DOORS_IN_CODE)) {
      const linked = door.source.includes(`PATH.${screen}`)
                  || door.source.includes(`pathFor("${screen}"`);
      if (!linked) missing.push(`${screen} → ${door.where}`);
    }
    expect(missing).toEqual([]);
  });

  it("keeps that list to screens the tables really do not carry", () => {
    // Otherwise the exemption list becomes the place a screen goes to stop
    // failing the census, which is the census deleted one row at a time.
    const doors = tableDoors();
    expect(Object.keys(DOORS_IN_CODE).filter((s) => doors.has(s as Screen)))
      .toEqual([]);
  });

  it("gives the storyboard's own fallback a card, ungated", () => {
    // `story.py` swaps the lost-revenue beat's call-to-action to `journey`
    // precisely for a salesperson, because `/lost-revenue` is
    // `require_manager_or_owner` and `/insight/journey` takes any principal.
    // So the role with the fewest doors — 8 of the cards, 3 of 4 Accounts
    // tabs, 2 of 5 Money tabs — was the one being sent to the screen that had
    // none. A `need` on this card would strand them again, which is why the
    // absence of one is asserted rather than assumed.
    const card = EVIDENCE.find((e) => e.screen === "journey");
    expect(card).toBeDefined();
    expect(card?.need).toBeUndefined();
  });
});
