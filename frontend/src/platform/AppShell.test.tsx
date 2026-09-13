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

import AppShell, { SWIPE_AREA_WIDTH, TOOLBAR_HEIGHT, type NavCounts } from "./AppShell";
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
 * jsdom cannot show a gesture working — it has no layout, so the drawer it is
 * dragging measures zero and the arithmetic that decides "far enough to open"
 * divides by it. The one stub below gives the paper a width; everything after
 * that is the real component doing the real sums. It is worth the stub: the
 * whole feature is arithmetic on touch coordinates, and the alternative is
 * pinning the props and hoping.
 *
 * The two `PrivateSwipeArea-root` assertions are a pair on purpose. It is MUI's
 * own private class, so it could be renamed under us — but the test that says
 * the strip is THERE fails loudly if that happens, which is what keeps the test
 * that says it is GONE from quietly passing for the wrong reason.
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
          <div />
        </AppShell>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

function swipeArea() {
  return document.querySelector(".PrivateSwipeArea-root") as HTMLElement | null;
}

/** Drag a finger from `from` to `to` along the top of the content column.
 *  The listeners are on `document`; only the first touch has to land on the
 *  strip, which is how the component tells an edge drag from a scroll. */
function dragRight(from: number, to: number) {
  const area = swipeArea();
  if (!area) throw new Error("no swipe area to start the gesture on");
  const at = (x: number) => [{ pageX: x, clientX: x, clientY: 400, pageY: 400 }];
  fireEvent.touchStart(area, { touches: at(from) });
  fireEvent.touchMove(document, { touches: at(from + 16) });
  fireEvent.touchMove(document, { touches: at(Math.round((from + to) / 2)) });
  fireEvent.touchEnd(document, { changedTouches: at(to) });
}

/** Every stylesheet this render put in the document, as one string. */
function emittedCss() {
  return [...document.querySelectorAll("style")].map((e) => e.textContent ?? "").join("\n");
}

/** The width the drawer would have on a real phone — `sx` sets 260, and jsdom
 *  applies no stylesheet, so it has to be said again here. */
function giveTheDrawerAWidth() {
  const paper = document.querySelector(".MuiDrawer-paper") as HTMLElement;
  Object.defineProperty(paper, "clientWidth", { value: 260, configurable: true });
}

describe("the phone nav's swipe", () => {
  it("opens the menu when a finger drags rightwards from the left edge", () => {
    showPhone();
    giveTheDrawerAWidth();
    expect(screen.queryByRole("link", { name: "Today" })).not.toBeInTheDocument();

    dragRight(4, 250);

    // Every destination, reachable without the button.
    for (const d of DESTINATIONS) {
      expect(screen.getByRole("link", { name: d.label })).toBeInTheDocument();
    }
  });

  it("leaves the menu shut when the finger starts past the edge", () => {
    // The strip is 20px wide, and the rest of the screen belongs to the screen.
    // A drag beginning on a row, a chart or a grid is that surface's gesture.
    showPhone();
    giveTheDrawerAWidth();

    const at = (x: number) => [{ pageX: x, clientX: x, clientY: 400, pageY: 400 }];
    fireEvent.touchStart(document.body, { touches: at(120) });
    fireEvent.touchMove(document, { touches: at(240) });
    fireEvent.touchEnd(document, { changedTouches: at(360) });

    expect(screen.queryByRole("link", { name: "Today" })).not.toBeInTheDocument();
  });

  it("arms the gesture on a phone, below the bar so the button stays whole", () => {
    showPhone();
    const area = swipeArea();
    expect(area).not.toBeNull();
    // The strip is fixed and sits above the AppBar. Starting it at the full
    // height of the screen would put it over the left edge of the menu button,
    // which is the one control this gesture is an alternative to.
    expect(area!.style.top).toBe(`${TOOLBAR_HEIGHT}px`);
    expect(area!.style.width).toBe(`${SWIPE_AREA_WIDTH}px`);
  });

  it("arms nothing at desk width, where the strip would only eat clicks", () => {
    // The five destinations are spelled out along the top there, so the gesture
    // buys nothing — and the strip is hit-testable, so it would swallow every
    // click landing in the first 20px of every screen.
    show();
    expect(swipeArea()).toBeNull();
  });

  it("stands down where the browser's own edge gesture already owns the edge", () => {
    // Safari navigates back on this exact drag. Two gestures on the same pixels
    // means one of them loses unpredictably, and a stray "back" costs a
    // half-written quote — so on those devices the button is the way in.
    const real = navigator.userAgent;
    const pretend = (ua: string) =>
      Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
    try {
      for (const ua of [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        // An iPad on iPadOS 13+ calls itself a Macintosh. Touch points are all
        // that separate it from a desktop Mac, which has no edge gesture.
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
      ]) {
        cleanup();
        pretend(ua);
        Object.defineProperty(navigator, "maxTouchPoints", { value: 5, configurable: true });
        showPhone();
        expect(swipeArea()).toBeNull();
      }

      // And a desktop Mac is not an iPad: no touch, so nothing to stand down for.
      cleanup();
      pretend("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15");
      Object.defineProperty(navigator, "maxTouchPoints", { value: 0, configurable: true });
      showPhone();
      expect(swipeArea()).not.toBeNull();
    } finally {
      pretend(real);
    }
  });

  it("takes the edge drag back off the browser, or it never reaches the drawer", () => {
    // Chrome navigates back on this exact drag — its overscroll history
    // gesture — and it consumes the touches before any listener sees them. In
    // a real browser the swipe simply went back instead of opening anything;
    // `overscroll-behavior-x: contain` is what stops that, and it is the half
    // of this feature jsdom cannot exercise, because jsdom has no gesture to
    // take away. All this can say is that the rule is emitted, and emitted
    // exactly where the gesture is armed.
    showPhone();
    expect(emittedCss()).toMatch(/overscroll-behavior-x:\s*contain/);
  });

  it("leaves the browser's own back-swipe alone where the gesture is not armed", () => {
    // At desk width and on iOS there is no swipe to protect, so taking the
    // gesture away would be a straight loss — a trackpad's two-finger back on
    // one, Safari's edge swipe on the other.
    show();
    expect(emittedCss()).not.toMatch(/overscroll-behavior-x/);
  });

  it("still opens from the button, which is how the gesture is discovered", () => {
    showPhone();
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(screen.getByRole("link", { name: "Today" })).toBeInTheDocument();
  });
});
