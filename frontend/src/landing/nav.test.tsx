/** The bar's links point at sections that exist.
 *
 * This is the check `ErpPage.tsx` has had a paragraph asking for and no code
 * behind. Its nav hrefs are cross-document — `/#worth` from a sub-page into the
 * landing document — so a renamed or deleted section id breaks nothing a build
 * can see. `/#product` and `/#plans` both dangled exactly that way: the first
 * outlived a rename to `#outcomes`, the second outlived the section's deletion,
 * and both kept working well enough to look fine, because an unmatched fragment
 * does not error. It scrolls to the top of the page and the visitor concludes
 * the link was decorative.
 *
 * The landing page's own bar has the same hole for the same reason; it is only
 * less likely to be noticed because the two files usually change together.
 *
 * So the assertion is against the rendered document rather than against another
 * list. A list checked against a list agrees with itself while both are wrong —
 * which is the shape of the two bugs above, where the nav and somebody's memory
 * of the page agreed for months.
 */
import { describe, expect, it } from "vitest";
import { renderLandingMarkup } from "./prerender";
import { navItems } from "./shared";
import { hasProof } from "./proof";

const markup = renderLandingMarkup();

/** Every `id="…"` the landing page actually renders. */
const renderedIds = new Set(
  [...markup.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]),
);

describe("the nav", () => {
  it("offers at least one link", () => {
    // A filter bug that emptied the list would otherwise make every assertion
    // below vacuously true, which is the failure mode of a check over a
    // collection: nothing to test is not the same as nothing wrong.
    expect(navItems().length).toBeGreaterThan(3);
  });

  it("points every link at a section the landing page renders", () => {
    for (const item of navItems()) {
      expect(renderedIds.has(item.id), `nav links to #${item.id}, which is not on the page`)
        .toBe(true);
    }
  });

  it("labels every link", () => {
    for (const item of navItems()) {
      expect(item.label.trim().length, item.id).toBeGreaterThan(0);
    }
  });

  it("lists each section once", () => {
    const ids = navItems().map((i) => i.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  /** The two sections deliberately left out, asserted as decisions rather than
   *  left to be re-added by whoever next reads the list and counts. `problem`
   *  sits under the hero, and `talk` is already in the bar as the button. */
  it("leaves out the opening section and the one the button already goes to", () => {
    const ids = navItems().map((i) => i.id);
    expect(ids).not.toContain("problem");
    expect(ids).not.toContain("talk");
  });

  /** Proof is conditional on both sides of the same predicate. When it is
   *  hidden the bar must not name it — a link to a section that did not render
   *  is the dangling link this file is about, arriving from content rather than
   *  from a rename. */
  it("names Proof only when the page shows it", () => {
    const named = navItems().some((i) => i.id === "proof");
    expect(named).toBe(hasProof());
    if (!named) expect(renderedIds.has("proof")).toBe(false);
  });
});
