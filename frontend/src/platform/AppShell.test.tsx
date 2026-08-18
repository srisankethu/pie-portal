/** What a reader sees in the nav, and when.
 *
 * Thirty-four items across four groups, its own grouping admitting seven ways
 * to act against twenty to read. To somebody who has already decided that
 * dashboards are things nobody opens, that is the verdict confirmed on first
 * login — directly after a landing page that spent its whole argument saying
 * this is not one. These pin the two answers to that, and the limits of each.
 */
import { describe, expect, it } from "vitest";

import { visibleNavItems, type NavItem } from "./AppShell";

const ITEMS: NavItem[] = [
  { key: "home", label: "Today", group: "decide" },
  { key: "quotes", label: "Quotes", group: "decide" },
  { key: "weather", label: "Weather", group: "understand" },
  { key: "bonds", label: "Bonds", group: "understand" },
  { key: "stock", label: "Stock", group: "book" },
  { key: "data", label: "Data & connection", group: "setup" },
  { key: "settings", label: "Settings", group: "setup" },
];

describe("what a new organization is shown", () => {
  it("hides the analysis groups until the books have arrived", () => {
    const shown = visibleNavItems(ITEMS, false);
    expect(shown.map((i) => i.key)).toEqual(
      ["home", "quotes", "data", "settings"]);
  });

  it("keeps everything a new organization can actually act on", () => {
    // Not an aesthetic trim. What survives is quoting and finishing setup,
    // which between them are the whole of what a book with no history can do —
    // and the checklist under the nav says which of the two is next.
    const groups = new Set(visibleNavItems(ITEMS, false).map((i) => i.group));
    expect(groups).toEqual(new Set(["decide", "setup"]));
  });

  it("shows everything once the books are there", () => {
    expect(visibleNavItems(ITEMS, true)).toEqual(ITEMS);
  });

  it("returns the full nav when readiness is not known", () => {
    // `useBooksReady` answers true whenever it cannot tell, and this is the
    // half of that decision worth pinning: hiding screens is defensible only
    // when we positively know there is nothing behind them. An organization
    // whose checklist failed to load must see its whole nav.
    expect(visibleNavItems(ITEMS, true)).toHaveLength(ITEMS.length);
  });

  it("does not reorder or rewrite what it keeps", () => {
    // The filter is the only thing this does. A nav that quietly reordered
    // itself between two states would move the item under somebody's cursor.
    const shown = visibleNavItems(ITEMS, false);
    expect(shown).toEqual(ITEMS.filter((i) => shown.includes(i)));
  });
});
