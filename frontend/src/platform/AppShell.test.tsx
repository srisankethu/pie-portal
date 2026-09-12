/** What the nav shows a reader, and when.
 *
 * The previous shell carried thirty-four items in four collapsible groups, and
 * this file pinned the property that mattered about *that* nav: nothing was
 * hidden from a reader who had expressed no preference, because the groups it
 * was tempting to fold held the screens that make this product's case.
 *
 * Five destinations removes the question rather than answering it — there is
 * nothing to fold, and no stored preference that could hide a screen. What
 * needs pinning now is the other half of the same promise: the five are always
 * all there, the one you are standing in says so even when the screen you are
 * reading is not itself a destination, and the badge counts only what is open.
 */
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import AppShell, { type NavCounts } from "./AppShell";
import { DESTINATIONS } from "./destinations";
import type { Screen } from "./route";

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

function show(current: Screen = "home", counts?: NavCounts) {
  wideViewport();
  render(
    <MemoryRouter>
      <ThemeProvider theme={createTheme()}>
        <AppShell current={current} counts={counts} userName="O" roleLabel="Owner"
                  onSignOut={() => {}}>
          <div />
        </AppShell>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("the navigation", () => {
  it("offers the five destinations, and only those", () => {
    show();
    const nav = screen.getByRole("navigation", { name: "Main" });
    const labels = [...nav.querySelectorAll("a")].map((a) => a.textContent);
    expect(labels).toEqual(DESTINATIONS.map((d) => d.label));
  });

  it("shows every destination to every reader", () => {
    // The shell is not told the role, and this asserts the absence of that
    // channel rather than trusting it stays absent: all five are readable by
    // everybody, and what a role may open is decided by the tabs inside Money
    // and Setup, next to the endpoint gates they mirror.
    show();
    for (const d of DESTINATIONS) {
      expect(screen.getByRole("link", { name: d.label })).toBeInTheDocument();
    }
  });

  it("marks the destination a screen belongs to, not only the ones with links", () => {
    // A decision detail has no nav item of its own and never will. Landing on
    // one from an email should still say which of the five you are in — a nav
    // showing nothing current is a nav that has lost you.
    show("detail");
    expect(screen.getByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Money" })).not.toHaveAttribute("aria-current");
  });

  it("marks Money current on a screen behind one of its tabs", () => {
    show("payables");
    expect(screen.getByRole("link", { name: "Money" })).toHaveAttribute("aria-current", "page");
  });

  it("badges what is open, and says nothing at zero", () => {
    show("home", { today: 7 });
    expect(screen.getByRole("link", { name: /Today/ })).toHaveTextContent("7");

    screen.getByRole("link", { name: "Quotes" });
    expect(screen.getByRole("link", { name: "Quotes" })).not.toHaveTextContent(/\d/);
  });
});
