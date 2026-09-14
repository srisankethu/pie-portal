// The tab strip has two shapes, and the phone one exists because of a defect.
//
// Reported as "More and other items only when scrolling" on Setup, and the
// source read as correct — which is why the diagnosis came from a browser
// (`e2e/.shots/tabstrip.mjs`) rather than from this file. What it measured at
// 390px: the strip overflowed its scroller by 98px with `Catalogue` and
// `Groups` clipped out of frame, and `scrollButtons=2 hidden=2` — MUI renders
// the two arrows and hides them below `sm`, so the only way to those tabs was
// swiping a strip that gave no sign it could be swiped. Turning the arrows on
// made it worse: they cost ~80px, so the overflow grew to 178px.
//
// What this file can hold is the *rule* that came out of it — below `sm` there
// is no strip, and every destination is one tap from the button. jsdom has no
// layout engine, so it cannot see a clipped tab; it can see which shape
// rendered and that nothing was dropped on the way.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import DestinationTabs from "./DestinationTabs";
import { defineAbilityFor } from "./ability";
import type { DestinationTab } from "./destinations";
import { pretendViewportIs } from "../test/viewport";

afterEach(() => vi.unstubAllGlobals());

/** Setup's real shape: four primary and four in the overflow. */
const TABS: readonly DestinationTab[] = [
  { label: "Connections", screen: "data" },
  { label: "Policy & people", screen: "settings" },
  { label: "Catalogue", screen: "decodedCatalog" },
  { label: "Groups", screen: "groups" },
  { label: "Item lines", screen: "catalogue", secondary: true },
  { label: "Identities", screen: "identity", secondary: true },
  { label: "System health", screen: "observability", secondary: true },
  { label: "AI states", screen: "states", secondary: true },
] as const;

function mount(width: number) {
  pretendViewportIs(width);
  return render(
    <MemoryRouter>
      <DestinationTabs
        tabs={TABS}
        current="data"
        ability={defineAbilityFor("OWNER")}
        label="Setup sections"
      />
    </MemoryRouter>,
  );
}

describe("at a desk", () => {
  it("draws the strip, with the rest behind More", () => {
    mount(1440);
    expect(screen.getByRole("tab", { name: "Connections" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Groups" })).toBeTruthy();
    // A secondary tab is not in the strip until it is the screen being read.
    expect(screen.queryByRole("tab", { name: "Identities" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Identities" })).toBeTruthy();
  });
});

describe("on a phone", () => {
  it("draws no strip at all", () => {
    mount(390);
    expect(screen.queryAllByRole("tab")).toHaveLength(0);
  });

  it("names where you are on the button that opens the menu", () => {
    mount(390);
    // `current` is `data`, whose label is Connections — so the control says
    // where you are standing rather than "Menu", which is the part a strip
    // gives away for free and a menu has to be told to do.
    expect(screen.getByRole("button", { name: "Setup sections" }).textContent)
      .toContain("Connections");
  });

  it("puts every destination one tap away, primary and overflow alike", () => {
    mount(390);
    fireEvent.click(screen.getByRole("button", { name: "Setup sections" }));

    const menu = screen.getByRole("menu");
    const labels = within(menu).getAllByRole("menuitem")
      .map((i) => i.textContent);
    // All eight: the four that were tabs and the four that were behind More.
    // The defect was two of these being reachable only by swiping, so the
    // assertion is the whole list rather than a count.
    expect(labels).toEqual([
      "Connections", "Policy & people", "Catalogue", "Groups",
      "Item lines", "Identities", "System health", "AI states",
    ]);
  });

  it("keeps every destination a link", () => {
    // §9: anything that goes somewhere is a link. A `Select` would have been
    // the shorter phone control and would have broken ctrl-click, middle-click
    // and "open in a new tab" on every tab in the product.
    mount(390);
    fireEvent.click(screen.getByRole("button", { name: "Setup sections" }));

    for (const item of within(screen.getByRole("menu")).getAllByRole("menuitem")) {
      expect(item.tagName).toBe("A");
      expect(item.getAttribute("href")).toBeTruthy();
    }
  });
});
