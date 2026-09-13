// A viewport, for a test that has to reach the other branch.
//
// jsdom has no `window.matchMedia` at all, so MUI's `useMediaQuery` returns its
// documented default — false — and every component that asks about the viewport
// renders its wide form. That is the right default for the suite: a test should
// not have to say "and this is a desk" to get one. It also means the phone
// branch is unreachable without a stub, which is why this is a helper rather
// than a convenience: `LineGrid` draws cards instead of a grid below 700px and
// `kit.FormDialog` goes full screen below 600px, and neither could be asserted
// at all before this existed.
//
// Shared rather than copied. It was written for the quote grid's own tests and
// the dialog needed exactly the same eight inert listener stubs — a second copy
// is how two suites end up disagreeing about what a phone is.
import { vi } from "vitest";

/** Answer `max-width` queries as though the viewport were this wide.
 *
 *  Everything else about the returned MediaQueryList is inert: this is for
 *  reading a breakpoint once at mount, not for testing a resize. */
export function pretendViewportIs(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const max = /max-width:\s*([\d.]+)px/.exec(query);
    return {
      matches: max ? width <= Number.parseFloat(max[1]) : false,
      media: query,
      onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {},
      dispatchEvent: () => false,
    };
  });
}
