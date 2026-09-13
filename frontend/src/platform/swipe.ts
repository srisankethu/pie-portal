/** A rightward drag across the screen, as the way to open the phone nav.
 *
 * This exists because the obvious implementation does not work on a phone.
 * `SwipeableDrawer` opens on an *edge* gesture: MUI pins a 20px hit-testable
 * strip to the left edge and the drag has to start inside it. That is the right
 * design for a native app and the wrong one for a web page, because on a phone
 * the left edge is not the page's to take:
 *
 *   - Safari navigates back on a left-edge drag, so MUI disables swipe-to-open
 *     on iOS by default and the gesture simply is not there.
 *   - Android 10+ gesture navigation reserves the left inset for Back at the
 *     OS level. The system consumes those touches before Chrome sees them, and
 *     no web page can opt out — `overscroll-behavior-x` suppresses Chrome's own
 *     overscroll navigation and has no bearing on the system one.
 *
 * Between them that is nearly every phone, which is how the first version of
 * this shipped green and did nothing in the hand. So the edge is left to the
 * browser, where it already belongs, and the gesture is what was actually asked
 * for: a drag rightwards **anywhere on the screen**. A drag that starts 40px in
 * is not an edge gesture on any platform, so there is nothing to fight, and the
 * iOS carve-out and the overscroll rule both stop being necessary.
 *
 * The cost is the opposite risk — firing when the reader meant something else —
 * and that is what `ownsHorizontalDrag` is for. The decision is split out as
 * two pure functions so it can be tested as arithmetic rather than as touch
 * plumbing.
 */
import { useEffect, useRef } from "react";

/** How far right the finger travels before this counts as the nav's gesture.
 *  About a fifth of a 390px screen: far enough that a stray diagonal on a
 *  scrolling list does not reach it, short enough for one thumb. */
export const SWIPE_MIN_X = 70;

/** How far a drag must beat its own vertical component to count as sideways.
 *  A scroll down a long screen wanders left and right by a few px; this is what
 *  says that is a scroll, not a swipe. */
export const SWIPE_AXIS_RATIO = 1.5;

/** Did this drag mean "open the menu"? */
export function isOpeningDrag(dx: number, dy: number): boolean {
  return dx >= SWIPE_MIN_X && dx > Math.abs(dy) * SWIPE_AXIS_RATIO;
}

/** Does something under the finger own a rightward drag already?
 *
 *  Two cases, both of which are somebody else's gesture:
 *
 *  - **A slider.** Dragging one sideways is the whole control. `viz/Screens`
 *    has three and `viz/Bonds` a fourth, and every one of them is a horizontal
 *    drag longer than this gesture's threshold.
 *  - **Something scrolled away from its left end.** Dragging right there scrolls
 *    it back, so the drag is the scroll. The test is whether it can *still*
 *    scroll that way: a grid already at its left end cannot, so the drag is
 *    free and the menu may have it. That is the rule a native drawer uses, and
 *    it is the difference between "the menu never opens over a table" and "the
 *    menu never steals a scroll".
 */
export function ownsHorizontalDrag(from: EventTarget | null): boolean {
  let el = from instanceof Element ? from : null;
  while (el) {
    if (el.matches('input[type="range"], [role="slider"]')) return true;
    if (el.scrollWidth > el.clientWidth && el.scrollLeft > 0) return true;
    el = el.parentElement;
  }
  return false;
}

/** Call `onOpen` when the reader drags rightwards across the screen.
 *
 *  Listens on the document, like the drawer's own close gesture does, because
 *  the drag may start on any screen the shell is framing. It fires on
 *  `touchend` rather than the moment the threshold is crossed: opening midway
 *  through a drag hands the still-moving finger to `SwipeableDrawer`'s
 *  open-state handler, which reads a continuing rightward drag as a close.
 */
export function useOpenOnSwipeRight(enabled: boolean, onOpen: () => void): void {
  const fire = useRef(onOpen);
  useEffect(() => { fire.current = onOpen; }, [onOpen]);

  useEffect(() => {
    if (!enabled) return;
    const doc = document;
    let from: { x: number; y: number } | null = null;
    let theirs = false;

    const start = (e: TouchEvent) => {
      // A second finger means a pinch or a two-handed gesture, neither of which
      // is this one. Abandoning rather than ignoring, so the touchend that
      // follows cannot be read against a stale starting point.
      if (e.touches.length !== 1) { from = null; return; }
      const t = e.touches[0];
      from = { x: t.clientX, y: t.clientY };
      theirs = ownsHorizontalDrag(e.target);
    };
    const end = (e: TouchEvent) => {
      const began = from;
      from = null;
      if (!began || theirs) return;
      const t = e.changedTouches[0];
      if (t && isOpeningDrag(t.clientX - began.x, t.clientY - began.y)) fire.current();
    };
    const cancel = () => { from = null; };

    // Passive: this never calls preventDefault. The page must keep scrolling
    // normally under a drag that turns out not to be this gesture, and a
    // non-passive touchmove listener on the document costs that on every screen.
    doc.addEventListener("touchstart", start, { passive: true });
    doc.addEventListener("touchend", end, { passive: true });
    doc.addEventListener("touchcancel", cancel, { passive: true });
    return () => {
      doc.removeEventListener("touchstart", start);
      doc.removeEventListener("touchend", end);
      doc.removeEventListener("touchcancel", cancel);
    };
  }, [enabled]);
}
