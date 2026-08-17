/** The landing page as a string — what `scripts/prerender.mjs` bakes into
 * `dist/index.html` so the server's response to `/` carries the public content
 * instead of an empty `#root`.
 *
 * This is the whole reason the file lives under `src/`: `tsc -b` type-checks
 * it and `prerender.test.tsx` exercises it in the three-second vitest loop, so
 * "the landing stopped being server-renderable" (someone adds a hook, a
 * `window` read, a data fetch) fails fast and named, not at the end of a
 * production build.
 *
 * Static markup, not hydration, on purpose. `main.tsx`'s
 * `createRoot().render()` replaces the prerendered children when the bundle
 * arrives: a signed-out visitor gets the identical landing back, a signed-in
 * one gets the shell they were always going to get. Hydration would buy
 * nothing here — the landing has no state — and would couple `main.tsx`'s
 * mount to whether the document was prerendered.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { CSS_VARS } from "../theme";
import { Landing } from "./Landing";

/** The landing exactly as `PlatformApp` mounts it for a signed-out visitor.
 *
 * `onEnter` is a required prop but serialises to nothing — static markup keeps
 * the `href="#signin"` anchors and drops the handlers, which is right: without
 * JavaScript the sign-in card cannot open anyway, and with it React owns the
 * page before anybody clicks. `onSignUp` is omitted because whether this
 * deployment accepts sign-ups is a runtime answer (`useSignupOffer`); the
 * markup is identical either way.
 */
export function renderLandingMarkup(): string {
  return renderToStaticMarkup(<Landing onEnter={() => {}} />);
}

/** The theme's design tokens as one `:root` rule.
 *
 * `landing.css` styles the page entirely from `var(--…)` tokens, and at
 * runtime those are emitted by `CssBaseline` — JavaScript. Prerendered HTML
 * would therefore paint tokenless (default fonts, no palette) until the bundle
 * runs. This emits the same `CSS_VARS` map `theme.ts` already exports, so the
 * first paint matches the app and no value exists twice. When the bundle
 * loads, emotion appends its own copy of the same rule later in `<head>`;
 * identical values, and the later one wins — a theme edit needs no rebuild
 * coordination.
 */
export function landingTokenCss(): string {
  const decls = Object.entries(CSS_VARS)
    .map(([name, value]) => `${name}:${value}`)
    .join(";");
  return `:root{${decls}}`;
}
