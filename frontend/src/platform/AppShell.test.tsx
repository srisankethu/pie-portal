/** What the nav shows a reader, and when.
 *
 * Thirty-four items across four groups, its own grouping admitting seven ways
 * to act against twenty to read. The first attempt at that shortened the list
 * two ways — folding the analysis groups by default, and withholding them
 * entirely until an organization had synced books — and the withholding was the
 * mistake worth a test of its own. Those groups hold this product's
 * visualisations, which are the best argument it makes for itself, and hiding
 * them from somebody who has not seen the product yet removes the case exactly
 * where it would have landed.
 *
 * So the property pinned first is that nothing is hidden. Folding is an
 * affordance for a reader who wants a shorter list, never a judgement about
 * what they should be looking at.
 */
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AppShell, { type NavItem } from "./AppShell";

const ITEMS: NavItem[] = [
  { key: "home", label: "Today", group: "decide" },
  { key: "quotes", label: "Quotes", group: "decide" },
  { key: "weather", label: "Weather", group: "understand" },
  { key: "landscape", label: "Landscape", group: "understand" },
  { key: "stock", label: "Stock", group: "book" },
  { key: "settings", label: "Settings", group: "setup" },
];

/** jsdom has no `matchMedia`, so MUI's `useMediaQuery` answers false and the
 *  shell renders its phone layout — where the nav lives behind a closed menu
 *  and none of it is in the document. Reported wide so these tests are about
 *  the nav rather than about the breakpoint. */
function wideViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
}

function show(current: NavItem["key"] = "home") {
  wideViewport();
  render(
    <MemoryRouter>
      <ThemeProvider theme={createTheme()}>
        <AppShell items={ITEMS} current={current} userName="O" roleLabel="Owner"
                  onSignOut={() => {}}>
          <div />
        </AppShell>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("the navigation", () => {
  beforeEach(() => window.localStorage.clear());

  it("shows every screen to a reader who has expressed no preference", () => {
    // The one that matters. A visualisation nobody can find is a visualisation
    // that does not exist, and a new reader has expressed no preference by
    // definition — which is exactly who the earlier version hid these from.
    show();
    for (const item of ITEMS) {
      expect(screen.getByRole("link", { name: item.label })).toBeInTheDocument();
    }
  });

  it("shows the analysis screens whether or not any books have synced", () => {
    // There is no readiness input any more. The component cannot withhold a
    // group on the strength of what an organization has, because it is not
    // told, and this asserts the absence of that channel rather than trusting
    // it stays absent.
    show();
    expect(screen.getByRole("link", { name: "Weather" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Landscape" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Stock" })).toBeInTheDocument();
  });

  it("offers a fold on the analysis groups and on nothing else", () => {
    // `Decide` is the working day; `Setup` is where you go when something is
    // wrong, and folding the exits is not a simplification.
    show();
    const folds = screen.getAllByRole("button", { name: /screens$/ });
    expect(folds.map((b) => b.getAttribute("aria-label"))).toEqual([
      "Understand, 2 screens", "The book, 1 screens",
    ]);
    for (const fold of folds) expect(fold).toHaveAttribute("aria-expanded", "true");
  });

  it("honours a group the reader collapsed last time", () => {
    window.localStorage.setItem("pie.nav.collapsed-groups", '["understand"]');
    show();
    expect(screen.queryByRole("link", { name: "Weather" })).not.toBeInTheDocument();
    // Everything else is still there — collapsing one group is not a mode.
    expect(screen.getByRole("link", { name: "Stock" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Today" })).toBeInTheDocument();
  });

  it("keeps a collapsed group open while it holds the current screen", () => {
    // A nav that hides the page you are reading has lost you.
    window.localStorage.setItem("pie.nav.collapsed-groups", '["understand"]');
    show("weather");
    expect(screen.getByRole("link", { name: "Weather" })).toBeInTheDocument();
  });

  it("does not read the previous release's preference as its opposite", () => {
    // The old key stored the groups a reader had *opened*. Reading that list as
    // a collapse list would fold exactly the groups they chose to see, which is
    // the worst available misreading of a stored preference.
    window.localStorage.setItem("pie.nav.open-groups", '["understand"]');
    show();
    expect(screen.getByRole("link", { name: "Weather" })).toBeInTheDocument();
  });
});
