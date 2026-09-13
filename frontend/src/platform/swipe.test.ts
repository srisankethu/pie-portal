/** What counts as "open the menu", and what belongs to somebody else.
 *
 * The gesture this replaced was an edge one and it did not work on a phone at
 * all, so the interesting question moved: not "does the drag reach us" but
 * "having given up the edge, can we still tell this drag from a scroll, a
 * slider and a sideways table". That is arithmetic and a walk up the DOM, which
 * is why both are pure functions rather than something only a browser can
 * answer.
 */
import { fireEvent, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SWIPE_MIN_X,
  isOpeningDrag,
  ownsHorizontalDrag,
  useOpenOnSwipeRight,
} from "./swipe";

afterEach(() => { document.body.innerHTML = ""; });

describe("what counts as the opening drag", () => {
  it("takes a decisive drag to the right", () => {
    expect(isOpeningDrag(150, 0)).toBe(true);
    expect(isOpeningDrag(SWIPE_MIN_X, 0)).toBe(true);
  });

  it("refuses one too short to have been meant", () => {
    expect(isOpeningDrag(SWIPE_MIN_X - 1, 0)).toBe(false);
    expect(isOpeningDrag(12, 0)).toBe(false);
  });

  it("refuses a leftward drag, which is the close gesture", () => {
    expect(isOpeningDrag(-150, 0)).toBe(false);
  });

  it("refuses a drag that is mostly down the screen", () => {
    // The one that matters: a thumb scrolling a long list wanders sideways, and
    // a menu that flies out during a scroll is worse than no gesture at all.
    expect(isOpeningDrag(90, 260)).toBe(false);
    expect(isOpeningDrag(90, -260)).toBe(false);
  });

  it("allows the wander a real thumb has", () => {
    // Nobody draws a straight line. 150 across and 40 down is a swipe.
    expect(isOpeningDrag(150, 40)).toBe(true);
  });
});

/** A scroller with the geometry jsdom will not compute on its own. */
function scroller({ scrollLeft }: { scrollLeft: number }) {
  const el = document.createElement("div");
  for (const [k, value] of Object.entries({ scrollWidth: 900, clientWidth: 300, scrollLeft })) {
    Object.defineProperty(el, k, { value, configurable: true });
  }
  document.body.appendChild(el);
  return el;
}

describe("whose drag it already is", () => {
  it("is nobody's on ordinary content", () => {
    const el = document.createElement("p");
    document.body.appendChild(el);
    expect(ownsHorizontalDrag(el)).toBe(false);
  });

  it("belongs to a slider, whose whole control is a sideways drag", () => {
    const el = document.createElement("input");
    el.type = "range";
    document.body.appendChild(el);
    expect(ownsHorizontalDrag(el)).toBe(true);
  });

  it("belongs to a surface scrolled away from its left end", () => {
    expect(ownsHorizontalDrag(scroller({ scrollLeft: 120 }))).toBe(true);
  });

  it("is free on a surface already at its left end", () => {
    // It cannot scroll further this way, so the drag would do nothing. This is
    // the difference between "the menu never opens over a table" and "the menu
    // never steals a scroll", and it is the second one that is wanted.
    expect(ownsHorizontalDrag(scroller({ scrollLeft: 0 }))).toBe(false);
  });

  it("looks up the tree, not only at what the finger landed on", () => {
    // A finger lands on a cell, never on the scroller itself.
    const grid = scroller({ scrollLeft: 120 });
    const cell = document.createElement("span");
    grid.appendChild(cell);
    expect(ownsHorizontalDrag(cell)).toBe(true);
  });

  it("answers for a touch that has no element under it at all", () => {
    expect(ownsHorizontalDrag(null)).toBe(false);
    expect(ownsHorizontalDrag(document)).toBe(false);
  });
});

/** `enabled`, against the hook rather than through a screen.
 *
 *  The shell turns the gesture off at desk width and while the menu is already
 *  open, and neither shows through the rendered output — the drawer does not
 *  exist at desk width, and opening an open menu is React bailing out. So the
 *  contract is pinned here, where an un-subscribed listener is the difference
 *  between a call and no call.
 */
describe("arming and disarming", () => {
  const dragRight = () => {
    const at = (x: number) => [{ clientX: x, clientY: 400 }];
    fireEvent.touchStart(document.body, { touches: at(140) });
    fireEvent.touchEnd(document.body, { changedTouches: at(300) });
  };

  it("calls out when armed", () => {
    const open = vi.fn();
    renderHook(() => useOpenOnSwipeRight(true, open));
    dragRight();
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("hears nothing when disarmed", () => {
    const open = vi.fn();
    renderHook(() => useOpenOnSwipeRight(false, open));
    dragRight();
    expect(open).not.toHaveBeenCalled();
  });

  it("lets go of the document when it unmounts", () => {
    // A shell that has been replaced must not still be opening a menu.
    const open = vi.fn();
    const { unmount } = renderHook(() => useOpenOnSwipeRight(true, open));
    unmount();
    dragRight();
    expect(open).not.toHaveBeenCalled();
  });

  it("calls the current handler, not the one it was mounted with", () => {
    // The shell hands it a fresh closure every render; subscribing again on
    // each one would be a listener churned per keystroke.
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ fn }) => useOpenOnSwipeRight(true, fn),
                                    { initialProps: { fn: first } });
    rerender({ fn: second });
    dragRight();
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
