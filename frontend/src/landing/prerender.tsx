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
 * `onSignUp` is passed, and it is the one prop here that changes what the
 * document *says*. Whether a deployment accepts sign-ups is a runtime answer
 * (`useSignupOffer`), and the page now labels its own primary action for the
 * door that will actually open — "Start free" where sign-up is offered, "Sign
 * in" where it is not. A static render has to assume one, so it assumes the
 * one a marketing deployment exists for: this document is the crawlable
 * surface of a page whose whole job is to get sign-ups, and baking "Sign in"
 * as its call to action would sell the product to nobody.
 *
 * Where a deployment does not accept them, the mounted app corrects the label
 * within a paint — the same swap it already makes for the worked example's
 * currency.
 * That is the right way round: the common case is served statically and the
 * exception is corrected, rather than every visitor being shown the exception.
 */
export function renderLandingMarkup(): string {
  return renderToStaticMarkup(<Landing onEnter={() => {}} onSignUp={() => {}} />);
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
      effect: "every \"Book a demo\" button falls back to the trial door",
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
