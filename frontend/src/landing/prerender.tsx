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
import { isPlaceholder } from "./content";
import { DEMO_BOOKING_URL } from "./cta";
import { ERP_PAGES } from "./erp";
import { caseStudy, complianceRows, namedCustomers } from "./proof";
import { ErpPage } from "./ErpPage";
import { Landing } from "./Landing";

/** The landing exactly as `PlatformApp` mounts it for a signed-out visitor.
 *
 * `onEnter` is a required prop but serialises to nothing — static markup keeps
 * the `href="#signin"` anchors and drops the handlers, which is right: without
 * JavaScript the sign-in card cannot open anyway, and with it React owns the
 * page before anybody clicks.
 *
 * There is nothing else to pass, and that is the point of the current shape.
 * This used to take `onSignUp` too, and it was the one prop that changed what
 * the document *said*: whether a deployment accepted sign-ups was a runtime
 * answer (`useSignupOffer`), the page labelled its primary action for the door
 * that would actually open, and a static render had to guess which. It guessed
 * "Start free" and the mounted app corrected the label within a paint where
 * that was wrong.
 *
 * The page asks for a demo now and asks for nothing else, so its call to
 * action is the same sentence for every deployment and the static document is
 * simply true rather than true-by-assumption. One fewer thing the prerender
 * has to be right about.
 *
 * `onDemo` is likewise not passed: the sample-data door depends on a
 * demonstration workspace this render knows nothing about, and the page falls
 * back to "See how it works" — an in-document anchor, which is exactly what a
 * reader without JavaScript can use.
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

/** What the site is still waiting for, and what is hidden for want of it.
 *
 * The build used to report this by scanning the built HTML for `{{TOKEN}}`
 * strings. That check is still in `scripts/prerender.mjs` and should now never
 * fire, because a token no longer reaches a page — which is the whole point of
 * the change, and which would have left the founder with no signal at all.
 *
 * So the signal moves here, to the source rather than the artefact: what has
 * no content yet, and what a visitor is therefore not seeing. Reported at the
 * end of every build.
 */
export function contentGaps(): { slot: string; effect: string }[] {
  const gaps: { slot: string; effect: string }[] = [];

  if (isPlaceholder(DEMO_BOOKING_URL)) {
    gaps.push({
      slot: "DEMO_BOOKING_URL",
      effect: "every \"Book a demo\" button opens the request form instead of a calendar",
    });
  }

  if (namedCustomers().length === 0) {
    gaps.push({ slot: "CUSTOMER_LOGO_1..4", effect: "no customer strip" });
  }
  if (caseStudy() === null) {
    gaps.push({ slot: "CASE_STUDY_*", effect: "no case study" });
  }
  if (complianceRows().length === 0) {
    gaps.push({ slot: "SOC2_TYPE_II_STATUS, DATA_RESIDENCY, GDPR_DPA_STATUS",
                effect: "no compliance row" });
  }
  if (namedCustomers().length === 0 && caseStudy() === null
      && complianceRows().length === 0) {
    gaps.push({ slot: "— all three above —", effect: "the Proof section is hidden entirely" });
  }

  for (const page of ERP_PAGES) {
    if (isPlaceholder(page.evidence)) {
      gaps.push({
        slot: page.evidence.replace(/[{}]/g, ""),
        effect: `no "what ${page.short} distributors tell us" panel on /erp/${page.slug}`,
      });
    }
  }

  return gaps;
}
