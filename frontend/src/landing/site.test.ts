/** The site's address, pinned.
 *
 * These are cheap assertions about a two-line module, and they are here for a
 * specific reason: the value they guard was wrong in production for the whole
 * life of the deployment, and nothing failed. `scripts/prerender.mjs` read a
 * default nobody had set, wrote `https://pie-portal-seven.vercel.app` into
 * every canonical tag, and the build printed the origin it used on every run —
 * which is exactly the kind of correct-looking output a reader stops reading.
 *
 * A wrong canonical is invisible to every check a repository can run on
 * itself; it is only visible in a search index, months later. So the shape of
 * the value gets a test even though its correctness cannot have one.
 */
import { describe, expect, it } from "vitest";
import { SITE_URL, pageUrl } from "./site";

describe("SITE_URL", () => {
  it("is an absolute https origin", () => {
    expect(SITE_URL).toMatch(/^https:\/\/[a-z0-9.-]+$/);
  });

  /** The canonical tag, the sitemap `<loc>`s and the JSON-LD `@id`s are all
   *  built as `${SITE_URL}/${slug}`. A trailing slash here makes every one of
   *  them `//` — two addresses for one page, which is the defect this whole
   *  change is about, arriving from the other direction. */
  it("carries no trailing slash and no path", () => {
    expect(SITE_URL.endsWith("/")).toBe(false);
    expect(new URL(SITE_URL).pathname).toBe("/");
  });

  /** Not the host the app is deployed *onto*. The platform host is where the
   *  bytes are served from; it is not what any page may claim to be. */
  it("is not a platform preview host", () => {
    expect(SITE_URL).not.toMatch(/vercel\.app|netlify\.app|pages\.dev/);
  });

  /** One host, chosen. `vercel.json` redirects the apex here, so this assertion
   *  and that rule have to agree — if this ever becomes the apex, the redirect
   *  becomes a loop. */
  it("is the www host the apex redirects to", () => {
    expect(new URL(SITE_URL).hostname).toBe("www.syncpie.com");
  });
});

describe("pageUrl", () => {
  it("gives the landing page the origin with a trailing slash", () => {
    expect(pageUrl("")).toBe("https://www.syncpie.com/");
  });

  it("gives a sub-page one unslashed address", () => {
    expect(pageUrl("erp/netsuite")).toBe("https://www.syncpie.com/erp/netsuite");
  });
});
