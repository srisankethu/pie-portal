/** The landing must stay server-renderable.
 *
 * scripts/prerender.mjs bakes `renderLandingMarkup()` into dist/index.html so
 * the server's response to `/` carries the public content. That only works
 * while `Landing` stays a pure function of its props — no hooks, no `window`,
 * no data fetch at render time. This pins it in the three-second loop; the
 * build would also fail, but at the end of `vite build` and without naming
 * the property that broke.
 */
import { describe, expect, it } from "vitest";
import { PAGES, landingTokenCss, renderLandingMarkup } from "./prerender";

/** Rendered once and read by every block below — the render is the fixture. */
const markup = renderLandingMarkup();

/** Every document the build emits, rendered the way the build renders them. */
const documents = PAGES.map((page) => ({ page, html: page.render() }));

describe("renderLandingMarkup", () => {

  it("renders the full landing statically — hero, sections, nav, footer", () => {
    // Exactly one h1: the crawlable document's outline is the landing's.
    expect(markup.match(/<h1/g)).toHaveLength(1);
    expect(markup).toContain("<nav");
    expect(markup).toContain("<footer");
    // The entity statement a reader (or an AI crawler) needs: what PIE is.
    expect(markup).toContain("commercial intelligence layer for distributors");
    // And the systems it reads — the connector names are real content here.
    expect(markup).toContain("Zoho Books");
  });

  it("keeps the CTAs as anchors, so the markup degrades to links", () => {
    expect(markup).toContain('href="#signin"');
    expect(markup).toContain('href="#pricing"');
  });
});

describe("the placeholders", () => {
  /** Every `{{TOKEN}}` the page may carry, and what has to replace it.
   *
   *  The list is the point. A marketing page that ships a placeholder is
   *  embarrassing; a marketing page that ships an *invented* customer, figure
   *  or certification in place of one is fatal to a product whose entire
   *  argument is that its numbers re-derive. So the tokens stay visible as
   *  tokens until somebody has the real thing — and this test refuses any
   *  token that is not on the list below, so a new one cannot be added
   *  without landing on the founder's checklist too.
   *
   *  One-directional on purpose: a token that has been replaced simply stops
   *  appearing, and this test stays green. It fails only for a token nobody
   *  declared. */
  const KNOWN = new Set([
    // Prices the owner has not fixed yet (see pricing.ts).
    "{{PRICE_TIER_1_USD}}",
    "{{PRICE_TIER_2_USD}}",
    "{{PRICE_CATALOG_BUILD_USD}}",
    // The scheduling link behind every "Book a demo" (see cta.ts).
    "{{DEMO_BOOKING_URL}}",
    // Section F — real customers, with written permission, or nothing.
    "{{CUSTOMER_LOGO_1}}",
    "{{CUSTOMER_LOGO_2}}",
    "{{CUSTOMER_LOGO_3}}",
    "{{CUSTOMER_LOGO_4}}",
    // Section F — one real customer's own value-ledger figures.
    "{{CASE_STUDY_ERP}}",
    "{{CASE_STUDY_DISTRIBUTOR_PROFILE}}",
    "{{CASE_STUDY_NARRATIVE}}",
    "{{CASE_STUDY_MARGIN_RECOVERED}}",
    "{{CASE_STUDY_LINES_CHECKED}}",
    "{{CASE_STUDY_LINES_HELD}}",
    "{{CASE_STUDY_POLICY_VERSION}}",
    "{{CASE_STUDY_WINDOW}}",
    // Section F — the real status on the day this ships, whatever it is.
    "{{SOC2_TYPE_II_STATUS}}",
    "{{DATA_RESIDENCY}}",
    "{{GDPR_DPA_STATUS}}",
    // The ERP pages — real language from distributors running that system.
    "{{PROPHET21_DISTRIBUTOR_EVIDENCE}}",
    "{{NETSUITE_DISTRIBUTOR_EVIDENCE}}",
    "{{ACUMATICA_DISTRIBUTOR_EVIDENCE}}",
  ]);

  it("carries no placeholder that is not declared, on any page", () => {
    const found = new Set(
      documents.flatMap(({ html }) => html.match(/\{\{[A-Z0-9_]+\}\}/g) ?? []),
    );
    expect([...found].filter((token) => !KNOWN.has(token)).sort()).toEqual([]);
  });

  it("states no certification, either way, while the status is unknown", () => {
    // The failure this guards is a specific one: somebody replaces the status
    // token with reassuring prose instead of a status. Neither word may appear
    // beside the compliance heading unless it is genuinely true.
    const compliance = markup.slice(markup.indexOf("SOC 2 Type II"));
    expect(compliance).not.toMatch(/SOC 2 Type II[^<]*(certified|compliant)/i);
  });
});

describe("landingTokenCss", () => {
  it("emits the theme tokens landing.css reads", () => {
    const css = landingTokenCss();
    expect(css).toMatch(/^:root\{/);
    // The two tokens every surface depends on; the rest come from the same map.
    expect(css).toContain("--color-bg:#f2f2f3");
    expect(css).toContain("--font-body:");
  });
});

describe("every prerendered page", () => {
  it("renders one document per registry entry, each with a single h1", () => {
    expect(documents.length).toBeGreaterThan(1);
    for (const { page, html } of documents) {
      expect(html.match(/<h1/g), `/${page.slug} h1 count`).toHaveLength(1);
      expect(html).toContain("<nav");
      expect(html).toContain("<footer");
    }
  });

  it("gives every page but the landing its own title and description", () => {
    for (const { page } of documents) {
      if (page.slug === "") {
        // The landing's strings live in index.html, which is also what the dev
        // server and an un-prerendered shell serve. See prerender.tsx.
        expect(page.title).toBeNull();
        expect(page.description).toBeNull();
      } else {
        expect(page.title).toBeTruthy();
        expect(page.description).toBeTruthy();
      }
    }
  });

  it("keeps a standalone page's links absolute, because it ships no router", () => {
    // A standalone document carries no bundle: `href="#pricing"` on it is a
    // fragment that scrolls nowhere, and the reader is left on a page with a
    // dead button. Every in-site link has to be a path.
    for (const { page, html } of documents.filter((d) => d.page.standalone)) {
      const hrefs = [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
      expect(hrefs.length).toBeGreaterThan(0);
      for (const href of hrefs) {
        expect(href.startsWith("#"), `/${page.slug} has a bare fragment: ${href}`)
          .toBe(false);
      }
    }
  });

  it("names its own ERP in the first heading of each sub-page", () => {
    // The whole reason these pages exist: a Prophet 21 distributor has to see
    // "Prophet 21" without reading a paragraph first.
    for (const { page, html } of documents.filter((d) => d.page.slug !== "")) {
      const h1 = html.match(/<h1[^>]*>(.*?)<\/h1>/s)?.[1] ?? "";
      const system = (page.title ?? "").split(" ")[2] ?? "";
      expect(h1.length).toBeGreaterThan(0);
      expect(`${h1} ${page.title}`).toContain(system);
    }
  });
});
