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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

  it("marks Accounts current on the vendor side, not only the customer side", () => {
    // The three screens Accounts gained were Today's, and they render exactly
    // the same either way — this is the only place the move is visible.
    for (const s of ["supply", "bonds", "dependency"] as const) {
      cleanup();
      show(s);
      expect(screen.getByRole("link", { name: "Accounts" }))
        .toHaveAttribute("aria-current", "page");
      expect(screen.getByRole("link", { name: "Today" })).not.toHaveAttribute("aria-current");
    }
  });

  it("badges what is open, and says nothing at zero", () => {
    show("home", { today: 7 });
    expect(screen.getByRole("link", { name: /Today/ })).toHaveTextContent("7");

    screen.getByRole("link", { name: "Quotes" });
    expect(screen.getByRole("link", { name: "Quotes" })).not.toHaveTextContent(/\d/);
  });
});

/** The phone nav opens by swipe as well as by button.
 *
 * The first version of this was an edge gesture, and it was reported as not
 * working at all — correctly, because on a phone the left edge belongs to the
 * browser or to the OS and not to the page. `swipe.ts` has that story and owns
 * the arithmetic; these are about the shell: the gesture reaches it, it is
 * armed only where it should be, and nothing is pinned to the left edge any
 * more.
 */
function phoneViewport() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
}

function showPhone() {
  phoneViewport();
  render(
    <MemoryRouter>
      <ThemeProvider theme={createTheme()}>
        <AppShell current="home" userName="O" roleLabel="Owner" onSignOut={() => {}}>
          <div data-surface="content" />
        </AppShell>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** A finger dragged across the screen, starting well clear of either edge —
 *  which is the whole point of the gesture and so the whole point of the test. */
function drag(
  { by, off = 0, from = document.body }: { by: number; off?: number; from?: Element },
) {
  const at = (x: number, y: number) => [{ clientX: x, clientY: y }];
  fireEvent.touchStart(from, { touches: at(140, 400) });
  fireEvent.touchEnd(from, { changedTouches: at(140 + by, 400 + off) });
}

const menuIsOpen = () => screen.queryByRole("link", { name: "Today" }) !== null;

describe("the phone nav's swipe", () => {
  it("opens on a rightward drag across the middle of the screen", () => {
    // Not from the edge. An edge gesture is what did not work: Safari takes
    // that drag for its own back-navigation and Android's gesture navigation
    // takes it at the OS level, before any browser sees it.
    showPhone();
    expect(menuIsOpen()).toBe(false);

    drag({ by: 150 });

    for (const d of DESTINATIONS) {
      expect(screen.getByRole("link", { name: d.label })).toBeInTheDocument();
    }
  });

  it("ignores a drag too short to have been meant", () => {
    showPhone();
    drag({ by: 30 });
    expect(menuIsOpen()).toBe(false);
  });

  it("ignores a drag that is really a scroll", () => {
    // A thumb travelling down a long screen wanders sideways. This is the
    // assertion that keeps the menu from flying out during an ordinary scroll.
    showPhone();
    drag({ by: 90, off: 260 });
    expect(menuIsOpen()).toBe(false);
  });

  it("leaves the drag to a surface that is already scrolled sideways", () => {
    showPhone();
    const grid = document.createElement("div");
    document.body.appendChild(grid);
    Object.defineProperty(grid, "scrollWidth", { value: 900, configurable: true });
    Object.defineProperty(grid, "clientWidth", { value: 300, configurable: true });
    Object.defineProperty(grid, "scrollLeft", { value: 120, configurable: true });

    drag({ by: 150, from: grid });

    expect(menuIsOpen()).toBe(false);
  });

  it("leaves an open menu open — the drag back is the drawer's, not this", () => {
    showPhone();
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));

    drag({ by: 150 });

    expect(menuIsOpen()).toBe(true);
  });

  // The shell passes `!wide && !open`, and NEITHER half of that is observable
  // from out here — at desk width the drawer is not rendered, and an open menu
  // asked to open is React bailing out on an unchanged value. Both are "do not
  // subscribe to touches nobody will use", not guards, and a test written
  // against them from this side passes whichever way the argument goes. Said
  // here because two such tests were written, survived deliberately breaking
  // the thing they named, and would have been read as cover they never were.
  // The contract they were reaching for is `enabled`, and swipe.test.ts holds
  // it against the hook directly.

  it("pins nothing to the left edge, and takes nothing off the browser", () => {
    // Both are regressions from the version that did not work. MUI's own
    // swipe-to-open arms itself with a hit-testable strip down the left edge,
    // which swallowed the clicks landing in the first 20px of every screen; and
    // it needed `overscroll-behavior-x` to stop Chrome navigating back, which
    // cost the reader a back-swipe the app is no longer replacing.
    showPhone();
    expect(document.querySelector(".PrivateSwipeArea-root")).toBeNull();
    const css = [...document.querySelectorAll("style")].map((e) => e.textContent).join("\n");
    expect(css).not.toMatch(/overscroll-behavior/);
  });

  it("still opens from the button, which is how the gesture is discovered", () => {
    showPhone();
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(menuIsOpen()).toBe(true);
  });
});
