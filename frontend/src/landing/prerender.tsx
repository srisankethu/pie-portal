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
import { ERP_PAGES } from "./erp";
import { ErpPage } from "./ErpPage";
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

/** One document the build emits, and everything that is true only of it.
 *
 * `slug` is the path under the origin: `""` is the landing at `/`, and
 * `"erp/prophet-21"` is a document at `/erp/prophet-21`. The landing is an
 * entry in this list rather than a special case beside it, so there is one
 * code path in `scripts/prerender.mjs` and a page cannot exist without a
 * canonical URL, a `<title>`, a description and a sitemap entry.
 *
 * `title` and `description` are `null` for the landing alone, and that is
 * deliberate rather than untidy: the landing's own strings live in
 * `index.html`, which is also what `npm run dev` serves and what the
 * un-prerendered shell would show, and moving them here would leave the dev
 * document untitled. The prerenderer reads them back out of the built
 * document. Every other page's single source is its entry here.
 *
 * `standalone` says the document ships without the module script. See
 * `ErpPage.tsx` for why that is the design and not a limitation.
 */
export interface PrerenderPage {
  slug: string;
  title: string | null;
  description: string | null;
  standalone: boolean;
  render: () => string;
}

export const PAGES: PrerenderPage[] = [
  {
    slug: "",
    title: null,
    description: null,
    standalone: false,
    render: renderLandingMarkup,
  },
  ...ERP_PAGES.map((page) => ({
    slug: `erp/${page.slug}`,
    title: page.title,
    description: page.description,
    standalone: true,
    render: () => renderToStaticMarkup(<ErpPage page={page} />),
  })),
];
