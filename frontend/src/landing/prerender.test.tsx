/** The landing must stay server-renderable.
 *
 * scripts/prerender.mjs bakes `renderLandingMarkup()` into dist/index.html so
 * the server's response to `/` carries the public content. That only works
 * while `Landing` stays a pure function of its props — no hooks, no `window`,
 * no data fetch at render time. This pins it in the three-second loop; the
 * build would also fail, but at the end of `vite build` and without naming
 * the property that broke.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Landing } from "./Landing";
// The server's own constant, inlined by Vite at transform time (`?raw`) —
// the same idiom `platform/route.test.ts` uses to read the destination list
// rather than restate it.
import connectionsSource from "../../../backend/app/ingestion/connections.py?raw";

import { ERP_PAGES } from "./erp";
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
  /** Not one of them may reach a page.
   *
   *  This assertion replaces a weaker one. The site used to render its
   *  unfilled slots as visible `{{TOKEN}}` strings, on the theory that a
   *  visible placeholder is the loudest reminder to fill it in, and the test
   *  here checked only that every token on the page was on a declared list.
   *  It was the right reminder aimed at the wrong person: the maintainer had
   *  three other ways of knowing, and the visitor got a building site.
   *
   *  Content that does not exist now hides its block — see `content.ts` and
   *  `proof.ts` — so a token in a built page means something rendered one
   *  instead of hiding, which is a defect and not a to-do. `prerender.mjs`
   *  fails the build on the same condition; this catches it in the
   *  three-second loop, and reports the whole set rather than the first.
   */
  it("puts no placeholder on any page, in any form", () => {
    const found = documents.flatMap(({ page, html }) =>
      (html.match(/\{\{[A-Z0-9_]+\}\}/g) ?? []).map((t) => `/${page.slug}: ${t}`));
    expect(found).toEqual([]);
  });

  it("puts no placeholder anywhere a visitor can click", () => {
    // The same rule for destinations rather than text: an `href` holding a
    // token is a dead primary action, which is how the demo CTA behaved
    // before it learned to fall back. See `cta.ts`.
    for (const { page, html } of documents) {
      for (const [, href] of html.matchAll(/href="([^"]*)"/g)) {
        expect(href, `/${page.slug} links to a placeholder: ${href}`)
          .not.toContain("{{");
      }
    }
  });

  it("states no certification, either way, while the status is unknown", () => {
    // The compliance row is absent entirely today. If it returns, this is the
    // edit it guards against: a status token replaced with reassuring prose
    // rather than with the true status.
    for (const { html } of documents) {
      const at = html.indexOf("SOC 2 Type II");
      if (at === -1) continue;
      expect(html.slice(at)).not.toMatch(/SOC 2 Type II[^<]*(certified|compliant)/i);
    }
  });
});

describe("a section with nothing to say", () => {
  const landing = documents.find((d) => d.page.slug === "")!.html;

  it("is absent, rather than present and empty", () => {
    // Section F ships empty today. An empty panel, a heading over nothing, or
    // a "coming soon" would each be worse than the section not being there.
    expect(landing).not.toContain('id="proof"');
    expect(landing).not.toContain("Running PIE today");
    expect(landing).not.toContain("Value ledger");
  });

  it("takes its navigation link with it", () => {
    // A nav item scrolling to a section that does not exist is the visible
    // half of the same defect.
    expect(landing).not.toContain('href="#proof"');
  });

  it("leaves no gap in the section lettering", () => {
    // The letters are computed from the sections that actually render, so
    // hiding one closes the alphabet up rather than running A B C D E *G* —
    // which a reader would rightly read as something removed in a hurry.
    const letters = [...landing.matchAll(/Section ([A-Z]) — /g)].map((m) => m[1]);
    expect(letters.length).toBeGreaterThan(3);
    expect(letters).toEqual(
      letters.map((_, i) => String.fromCharCode(65 + i)));
  });

  it("keeps the body's cross-references pointing at the right letter", () => {
    // "the value ledger in Section D" has to name whichever letter that
    // section ended up with, or the page argues with itself.
    const worth = landing.match(/Section ([A-Z]) — What it was worth/)?.[1];
    expect(worth).toBeTruthy();
    for (const [, cited] of landing.matchAll(/value ledger in Section ([A-Z])/g)) {
      expect(cited).toBe(worth);
    }
  });
});

describe("the history window", () => {
  it("states the number DEFAULT_HISTORY_MONTHS actually holds", () => {
    // Three pages tell a prospective customer how far back their first pull
    // reads, and the figure is a literal in each of them. The page used to
    // hedge it as "about 18 months" and the hedge was removed on the grounds
    // that the number is exact — which makes it a number that has to stay
    // right. `connections.DEFAULT_HISTORY_MONTHS` is the one that decides, and
    // the day somebody moves it, this fails instead of the page quietly
    // telling customers the old figure.
    const months = connectionsSource.match(/^DEFAULT_HISTORY_MONTHS\s*=\s*(\d+)/m)?.[1];
    expect(months, "DEFAULT_HISTORY_MONTHS not found in connections.py").toBeTruthy();

    // Anchored on the sentence that describes the window — "the first day of
    // the month" — rather than on every number followed by "months". The page
    // also says "rates, locked for 24 months", which is a commitment about
    // billing and has nothing to do with this constant; a test that fails on
    // that is a test somebody deletes.
    let checked = 0;
    for (const { page, html } of documents) {
      for (const match of html.matchAll(/first day of/g)) {
        const sentence = html.slice(Math.max(0, match.index - 160), match.index + 160);
        expect(sentence, `/${page.slug} states the window without ${months} months`)
          .toContain(`${months} months`);
        checked += 1;
      }
    }
    // And every page that makes the claim is actually reached — a loop that
    // matched nothing would pass in silence, which is the shape of test this
    // repository has been burned by before (CLAUDE.md §1, "absence of evidence
    // is not a pass").
    expect(checked, "no page states the history window at all")
      .toBeGreaterThanOrEqual(documents.length);
  });
});

describe("the currency the prerender bakes", () => {
  it("puts no rupee figure in any served document", () => {
    // The repositioning's one hard rule about price: a visitor outside India
    // must not find ₹9,999 anywhere — "in markup, alt text or structured
    // data" — because a reader who finds it anchors to it, and it is not the
    // offer being made to them. The static documents are what a crawler, a
    // no-JavaScript reader and every visitor's first paint get, so they are
    // where that rule has to hold; the Indian list is swapped in on mount, by
    // `detectRegion`, for browsers whose own clock says India.
    //
    // This is pinned because the whole invariant otherwise rests on one
    // `useState` default in Landing.tsx, and a default is a one-character
    // edit away from being the other one.
    for (const { page, html } of documents) {
      expect(html, `/${page.slug} carries a rupee figure`).not.toMatch(/₹|\bINR\b/);
    }
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
    //
    // Checked against `erp.ts`'s own `short` name and against the h1 alone.
    // The first version of this test derived the system name *from the title*
    // and then asserted the title contained it, which is true of every string
    // and could not fail.
    const pages = ERP_PAGES.map((p) => [`erp/${p.slug}`, p.short] as const);
    expect(pages.length).toBeGreaterThan(0);
    for (const [slug, short] of pages) {
      const html = documents.find((d) => d.page.slug === slug)?.html ?? "";
      const h1 = html.match(/<h1[^>]*>(.*?)<\/h1>/s)?.[1] ?? "";
      expect(h1, `${slug} h1`).toContain(short);
    }
  });
});

describe("the page's own call to action", () => {
  /** The landing rendered as each kind of deployment sees it. */
  const offered = renderToStaticMarkup(<Landing onEnter={() => {}} onSignUp={() => {}} />);
  const notOffered = renderToStaticMarkup(<Landing onEnter={() => {}} />);

  it("says 'Start free' where the deployment accepts sign-ups", () => {
    expect(offered).toContain(">Start free<");
    expect(offered).toContain(">Start your trial<");
  });

  it("never says 'Start free' where it does not", () => {
    // The defect: `onSignUp` is absent wherever SELF_SERVE_SIGNUP is off, the
    // handler fell back to the sign-in card, and the label did not follow — so
    // a button reading "Start free" opened a form asking for a password the
    // visitor had never set. The same shape as the demo button pointing at a
    // placeholder, and it survived that fix because it lives elsewhere.
    expect(notOffered).not.toContain(">Start free<");
    expect(notOffered).not.toContain(">Start your trial<");
  });

  it("promises no free trial it cannot let anyone start", () => {
    // "Commercial Intelligence free for 30 days — no card" over a sign-in form
    // is the same sentence pointing at the same closed door.
    expect(offered).toContain("free for 30 days");
    expect(notOffered).not.toContain("free for 30 days");
  });

  it("bakes the offered case, because that is what a marketing page is for", () => {
    // A static render has to assume one. It assumes the door a deployment
    // running this page wants open; the mounted app corrects the label within
    // a paint where it is not, which is the same swap the rupee price list
    // already makes. The reverse — baking "Sign in" for everyone — would sell
    // the product to nobody, and to a crawler it is the page's headline action.
    const landing = documents.find((d) => d.page.slug === "")!.html;
    expect(landing).toContain(">Start free<");
  });
});
