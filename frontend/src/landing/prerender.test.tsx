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
import { landingTokenCss, renderLandingMarkup } from "./prerender";

/** Rendered once and read by every block below — the render is the fixture. */
const markup = renderLandingMarkup();

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
  ]);

  it("carries no placeholder that is not declared", () => {
    const found = new Set(markup.match(/\{\{[A-Z0-9_]+\}\}/g) ?? []);
    expect([...found].filter((token) => !KNOWN.has(token))).toEqual([]);
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
