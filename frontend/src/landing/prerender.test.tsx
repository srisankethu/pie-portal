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

describe("renderLandingMarkup", () => {
  const markup = renderLandingMarkup();

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

describe("landingTokenCss", () => {
  it("emits the theme tokens landing.css reads", () => {
    const css = landingTokenCss();
    expect(css).toMatch(/^:root\{/);
    // The two tokens every surface depends on; the rest come from the same map.
    expect(css).toContain("--color-bg:#f2f2f3");
    expect(css).toContain("--font-body:");
  });
});
