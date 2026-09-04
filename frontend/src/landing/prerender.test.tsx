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

describe("every ERP page is reachable from the landing page", () => {
  // The strip in the landing page's "Reads the books you already keep" row is
  // the only route from the site's front door to these pages. A page in the
  // sitemap and nowhere in the site is an orphan: a crawler finds it, a reader
  // never does, and nothing fails. The links are derived from `ERP_PAGES` for
  // that reason, and this is the test that says so — it would have failed on
  // the hand-written list the moment a fourth page was added.
  it.each(ERP_PAGES.map((p) => [p.slug, p] as const))("links /erp/%s", (slug, page) => {
    expect(markup).toContain(`href="/erp/${slug}"`);
    // And by the name a reader is looking for, not by the slug.
    expect(markup).toMatch(new RegExp(`href="/erp/${slug}"[^>]*>${page.short}<`));
  });
});

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
    // `#signin` is the existing customer's door and `#talk` is every "Book a
    // demo" button's destination while no scheduling link is configured. Both
    // have to survive as real anchors: without JavaScript they are all a
    // visitor has, and the second is the page's headline action.
    expect(markup).toContain('href="#signin"');
    expect(markup).toContain('href="#talk"');
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

describe("what a plan costs", () => {
  /** The site does not say, anywhere, and the form is how it does not.
   *
   *  This is the assertion that keeps the decision from eroding one panel at a
   *  time. A price on this page is not merely a number: it answers a question
   *  the page has asked the reader nothing about — how many companies, which
   *  ERP, how much catalogue — and it answers it wrongly for the reader who is
   *  worth the most. `worked-example.test.ts` holds the same line at the source;
   *  this holds it in the markup, which is where a hand-written literal would
   *  land.
   *
   *  It cannot simply forbid a currency symbol: the worked quote line is money
   *  and is the page's whole argument. So it forbids the shapes a *plan* price
   *  takes — a period after an amount, and the phrase the panels fell back to
   *  when a figure was unset.
   */
  it("is not stated on any page, in any currency", () => {
    for (const { page, html } of documents) {
      // "/month" is the shape a subscription price takes on a panel, and
      // "per month" is the same thing written out. Not a blanket ban on the
      // word: Section A says a quote desk makes hundreds of decisions *a
      // month*, which is the problem being described rather than a price.
      expect(html, `/${page.slug} prices a plan by the month`)
        .not.toMatch(/\/\s?month|per month/i);
      expect(html, `/${page.slug} still carries the price fallback`)
        .not.toContain("Priced per organization");
    }
  });

  it("names no plan either, which is the newer half of the rule", () => {
    // Removing the figures left three named tiers on the page, and three named
    // tiers is still a pricing conversation — held with a reader who has not
    // yet been told what the product does. The section is gone. This is what
    // would notice it coming back one panel at a time, the way it went.
    const landing = documents.find((d) => d.page.slug === "")!.html;
    expect(landing).not.toContain('id="plans"');
    expect(landing).not.toContain('href="#plans"');
    // The ladder's own labels, as a heading or a picker would print them. The
    // trailing `<` matters: "commercial intelligence layer for distributors" is
    // the entity statement this page is pinned to elsewhere, and a bare
    // substring check would forbid it.
    expect(landing).not.toMatch(/Commercial Intelligence</);
    expect(landing).not.toContain("Which plan are you asking about");
  });

  it("is asked about instead, through a door that is in the document", () => {
    // The other half, and the half that makes the first honest: removing the
    // price and then the plans and leaving nothing in their place would be a
    // page that gives a buyer no way to ask about anything at all.
    //
    // The fields themselves are not here, and that is the design rather than a
    // regression: the form opens in a dialog (`ContactModal`) on the press of
    // any "Book a demo" button, so nothing of it is rendered until somebody
    // asks for it. What the static document therefore has to carry is the
    // invitation and the way in — the `#talk` block, its copy, and the anchors
    // that reach it — because that is all a crawler, or a visitor whose bundle
    // never arrives, can see.
    const landing = documents.find((d) => d.page.slug === "")!.html;
    expect(landing).toContain('id="talk"');
    expect(landing).toContain("Tell us about your business");
    expect(landing).toContain("Book a demo");
    // Every CTA on the page leads here, so the jump target has to exist in the
    // same document that links to it.
    expect(landing).toContain('href="#talk"');
  });

  it("is reachable from every ERP page, which ship no JavaScript", () => {
    // A form on a page with no bundle would render and refuse to send, so the
    // sub-pages link to the landing's instead. An absolute path, because a
    // bare `#talk` on /erp/prophet-21 is a fragment that goes nowhere.
    for (const { page, html } of documents) {
      if (page.slug === "") continue;
      expect(html, `/${page.slug} cannot reach the form`).toContain('href="/#talk"');
      expect(html, `/${page.slug} carries a form it cannot submit`)
        .not.toContain("<form");
    }
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

  it("points every cross-page anchor at a section that exists", () => {
    // The other half of the test above, and the one that was missing. A
    // sub-page's `/#outcomes` is right in form and can still be wrong in fact:
    // the landing page owns those ids, this file only names them, and nothing
    // connects the two. Renaming a section on the front page therefore breaks
    // links on four other documents silently — the browser scrolls to the top
    // of the landing page and the reader has no way to know they missed.
    //
    // Which is exactly what happened when this page was rebuilt: `#product`
    // became `#outcomes`, `#plans` was deleted with the plans section, and both
    // links survived in the sub-page nav, looking and behaving like working
    // ones. `#signin` is the deliberate exception — no element carries it; it
    // is a signal the mounted app reads to open the sign-in card.
    const landing = documents.find((d) => d.page.slug === "")!.html;
    const ids = new Set(
      [...landing.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
    for (const { page, html } of documents) {
      if (page.slug === "") continue;
      for (const [, frag] of html.matchAll(/href="\/#([^"]+)"/g)) {
        if (frag === "signin") continue;
        expect(ids.has(frag), `/${page.slug} links to /#${frag}, which the landing page has no id for`)
          .toBe(true);
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
  const landing = documents.find((d) => d.page.slug === "")!.html;

  it("asks for one thing, and asks for it in the static document", () => {
    // The page had two headline actions — "Book a demo" and "Start free" — and
    // they wanted different readers. It has one now. A crawler and a visitor
    // whose bundle never arrives both see it, because it is an anchor into the
    // same document rather than a handler.
    expect(landing).toContain("Book a demo");
    expect(landing).toContain('href="#talk"');
  });

  it("markets no trial and no plan", () => {
    // What this replaces is the pair of tests that pinned the opposite:
    // "Start free" where sign-up was offered, "Sign in" where it was not, and
    // the 30-day line beside them. All three were correct for a page whose job
    // was sign-ups. This page asks for a demo, and a trial button beside that
    // is a second destination competing for the one decision the reader is
    // being asked to make — as well as a plan conversation held before the
    // product has been described.
    //
    // Sign-up itself is untouched: `SELF_SERVE_SIGNUP` still governs it and
    // `SignInCard` still offers "Create your organization" one click behind
    // this page. It is simply not what the front door asks for.
    expect(landing).not.toContain(">Start free<");
    expect(landing).not.toContain(">Start your trial<");
    expect(landing).not.toContain("free for 30 days");
  });

  it("still lets an existing customer in", () => {
    // The half that would be easy to lose while removing the other buttons.
    // Every visitor who already pays for this arrives looking for exactly one
    // link, and a page that only sells is a page they cannot use.
    expect(landing).toContain('href="#signin"');
    expect(landing).toContain(">Sign in<");
  });

  it("renders the same call to action whatever the deployment offers", () => {
    // The prerender used to have to guess. `onSignUp` decided whether the
    // headline button said "Start free" or "Sign in", the static document had
    // to bake one, and the mounted app corrected it within a paint where the
    // guess was wrong. The prop is gone and there is nothing left to guess:
    // the only optional prop is `onDemo`, which changes the *secondary*
    // button, and the primary is the same sentence either way.
    const withDemo = renderToStaticMarkup(
      <Landing onEnter={() => {}} onDemo={() => {}} />);
    const withoutDemo = renderToStaticMarkup(<Landing onEnter={() => {}} />);
    for (const html of [withDemo, withoutDemo]) {
      expect(html).toContain("Book a demo");
      expect(html).not.toContain(">Start free<");
    }
    // And the secondary button says what it actually does in each case, rather
    // than naming a sample workspace this deployment may not have.
    expect(withDemo).toContain("See it on sample data");
    expect(withoutDemo).not.toContain("See it on sample data");
    expect(withoutDemo).toContain("See how it works");
  });
});
