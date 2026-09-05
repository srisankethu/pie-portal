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
import { EXAMPLE_ITEM, exampleFor } from "./worked-example";
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

describe("the sub-pages' menu, which has no JavaScript behind it", () => {
  /* These pages ship no bundle — `scripts/prerender.mjs` bakes them without the
     module script tag — so every interactive thing on them is the browser's or
     it does not happen. The nav used to take that as "no menu is possible" and
     laid five links flat in the bar. Measured in Chromium across 320–430px,
     that bar was **131px tall** against the landing page's 71, and each link
     was **17px** high; the landing page's own mobile menu gives each one 44+,
     which is what `e2e/.shots/a11y.mjs` holds the signed-in app to.

     A `<details>` opens with no script at all, so the sub-pages now carry the
     same collapsed bar the front page does: 77px, one row, and a panel whose
     links are 44px when it is open. Verified with `javaScriptEnabled: false`.

     Everything below is structure rather than pixels, because jsdom has no
     layout. What it can hold is that the mechanism is still there. */

  it("gives every ERP page a disclosure the browser can open by itself", () => {
    for (const { page, html } of documents) {
      if (page.slug === "") continue;   // the landing page has React for this
      expect(html, `/${page.slug} has no <details> menu`)
        .toContain('<details class="lp-nav-menu">');
      expect(html, `/${page.slug}'s menu has no summary to press`)
        .toContain('<summary class="lp-nav-toggle"');
      // The panel has to be *inside* the details, or `[open]` cannot reach it.
      const menu = html.slice(html.indexOf("<details"), html.indexOf("</details>"));
      expect(menu, `/${page.slug}'s links are not inside its menu`)
        .toContain('class="lp-nav-links"');
    }
  });

  it("carries no flat nav row for a phone to inherit", () => {
    // `lp-nav-static` was the modifier that kept the links in the bar because
    // nothing could open a menu. Its rule is gone from landing.css, so markup
    // still asking for it would get the landing page's mobile `.lp-nav-links`
    // — `display: none` until a JS toggle that never arrives — and the phone
    // would be back to a logo and nothing else, which is the defect the
    // modifier was written to fix in the first place.
    for (const { page, html } of documents) {
      expect(html, `/${page.slug} still asks for the flat nav row`)
        .not.toContain("lp-nav-static");
    }
  });

  it("keeps the row of other ERP pages tappable on a phone", () => {
    // On a phone this row is the only route from one individual ERP page to
    // another — the bar goes back to the front page and nowhere else. As bare
    // links separated by a middot each was a 17px target; the class is what
    // the 44px rule keys on.
    for (const { page, html } of documents) {
      if (page.slug === "") continue;
      expect(html, `/${page.slug}'s footer links to its siblings unmarked`)
        .toContain('class="lp-footer-erp"');
    }
  });

  /* Two things this block deliberately does *not* assert, because asserting
     them here would be theatre.

     The rules that carry this fix are `::details-content { content-visibility:
     visible }` and `.lp-two > * { min-width: 0 }`, and both fail invisibly:
     delete the first and a closed `<details>` sizes as though empty, so the
     desktop bar comes back 0px wide with five links stacked in it; delete the
     second and one long identifier pushes a page sideways. Neither shows in
     jsdom, which has no layout, and neither shows in the markup, which is
     unchanged. The obvious move is to read landing.css as text and grep it —
     that was written, and then removed: it proves a string is present, not
     that a browser lays the bar out, and it goes red when somebody reformats
     a rule it is not really about. `?raw` and `?inline` both come back empty
     under vitest as well, so it could only be done with `node:fs`, and
     `@types/node` is not a dependency of this frontend.

     `e2e/.shots/public-a11y.mjs` measures both, in Chromium, on the built
     documents — nav height, sideways scroll and every touch target across four
     phone widths. That is where a layout claim belongs. What stays here is the
     structure: the mechanism exists, nothing asks for the row it replaced, and
     the footer row is marked for the rule that makes it tappable. */

  it("keeps the copy that exposed the sideways scroll", () => {
    /* /erp/zoho-books prints its scope list in full, and one of those tokens —
       `ZohoBooks.customerpayments.READ` — is 31 characters with nowhere to
       break. A grid item's default `min-width: auto` is its content's
       min-content width, so at 320px that single word held the panel at 293px
       inside the 254px the phone had: `documentElement.scrollWidth` 326
       against a 320 viewport, the whole document sliding sideways.

       The fix is on the grid, not in this copy, because the page that showed
       the defect is not the page that had it — every ERP page prints its own
       connector's identifiers and only one vocabulary happened to be long
       enough. But the case is worth keeping: shorten this text and
       `public-a11y.mjs` still passes while the rule it is checking has nothing
       left to check. */
    const zoho = ERP_PAGES.find((p) => p.slug === "zoho-books")!;
    const longest = Math.max(...zoho.setup.split(/\s+/).map((w) => w.length));
    expect(longest, "no token here is long enough to exercise the grid fix")
      .toBeGreaterThan(24);
  });
});

describe("the worked card is marked as a drawing, not a record", () => {
  // The page's whole argument is that its numbers come from records and carry
  // the policy that judged them. The hero card was a drawing of that, dressed
  // in the same vocabulary: an invented quote number, an invented policy hash,
  // a revision, and no caption anywhere a sighted reader would find one — the
  // word "illustrative" lived in the `aria-label` and a comment. The one
  // visible tell was an accident (an Ohio machine shop paying rupees, because
  // the region swap moved the money and not the customer).
  //
  // Both halves are pinned here: the marker has to be *in the document*, and
  // the fabricated identifiers may not come back. This is the same rule
  // `proof.ts` already keeps by hiding a section rather than naming a customer
  // it does not have — the hero is not exempt from it for being the hero.
  it("says once, under the card, that the figures are samples", () => {
    // Under it and in a caption's register — not stamped across the card. The
    // first fix for the forged record was a chip inside it reading "not a real
    // quote", which told the reader the screen was not worth looking at; a
    // page that will not show its own product argues that the product is not
    // worth showing. The marker has to exist and has to be quiet.
    expect(markup).toContain("sample figures");
    expect(markup).toMatch(/Figures<\/span>sample/);
  });

  it("draws the product's own decision screen, not an invented layout", () => {
    // The card is the decision detail in `platform/PlatformApp.tsx`, element
    // for element. These are that screen's own words, and they are here so
    // that a card redrawn into something the product does not have fails.
    expect(markup).toContain("Facts · what the data shows");
    expect(markup).toContain("Evidence used");
    expect(markup).toContain("Request approval");
    // Every fact names the record behind it — the property that makes the
    // screen worth showing at all.
    expect(markup).toContain("your margin policy");
    expect(markup).toContain("bill");
  });

  it("prints no invented quote id, policy hash or revision", () => {
    // Specific shapes, so the assertion names what it is refusing rather than
    // banning the words "quote" and "policy" from a page about quoting.
    expect(markup, "an invented quote number is back").not.toMatch(/\bQ-\d{3,}\b/);
    expect(markup, "an invented policy hash is back").not.toMatch(/\b(ci|th)_[0-9a-f]{4,}\b/);
    expect(markup, "an invented revision is back").not.toMatch(/>Rev<\/span>\s*\d/);
  });

  it("takes the customer and the currency from one record", () => {
    // The defect was structural, not a typo: the descriptor was hardcoded in
    // the JSX while the money came from the region example, so nothing could
    // keep them together. They are one object now, and this fails if a future
    // edit pulls them apart again.
    for (const region of ["INTL", "IN"] as const) {
      const example = exampleFor(region);
      expect(example.customer.length).toBeGreaterThan(0);
    }
    expect(exampleFor("IN").customer).not.toBe(exampleFor("INTL").customer);
    // The static document is the dollar one — the rupee card only exists on a
    // browser whose clock says India — so the descriptor it renders has to be
    // the dollar region's.
    expect(markup).toContain(exampleFor("INTL").customer);
    expect(markup).not.toContain(exampleFor("IN").customer);
  });
});

describe("the public pages name no single trade", () => {
  // The platform reads whatever book a distributor keeps — the connector
  // registry alone spans six ERPs and no vertical — so the front page must not
  // quietly pick one. It did: the worked card's item was a real ISO
  // turning-insert designation and the RFQ card ranked alternatives on
  // "geometry and grade", which is a carbide catalogue's vocabulary. A
  // fasteners distributor reading either learns the product was built for
  // somebody else.
  //
  // The example item is a code that decodes to nothing, and the attributes are
  // named in the words any catalogue would use. This is the check that keeps
  // it that way, because the tempting edit is always to make the example more
  // vivid by making it somebody's.
  // The attribute words are in here too, and they are the half that would
  // otherwise go unchecked: "ranks alternatives on geometry and grade" is as
  // much a carbide catalogue as the part number was.
  // The buyer is in here for the same reason the item is. "A machine shop in
  // Ohio" was the customer on the card long after the item stopped naming a
  // trade, and it named the same one — a machine shop buys cutting tools. What
  // the sentence needs is a returning account, which is what gives the line a
  // price history; the trade never mattered.
  const TRADE = /\b(carbide|DNMG|CNMG|insert|end ?mill|drill bit|fastener|bearing|geometry|grade|machine shop|foundry|tool ?room)s?\b/i;

  it("keeps the landing page's example free of a trade", () => {
    const landing = documents.find((d) => d.page.slug === "")!.html;
    const hit = landing.match(TRADE);
    expect(hit?.[0] ?? null,
      `the front page names a trade: “${hit?.[0]}”. The example has to read for `
      + "whatever this distributor sells.").toBeNull();
  });

  it("cites the record type, not one vendor's name, on the worked card", () => {
    // The real decision screen names the connector each record came from,
    // because by then a book has answered. Here none has: the reader has
    // connected nothing and the strip below this card offers seven systems, so
    // "zoho · invoice" told six of those distributors the card was drawn for
    // somebody else's stack. The record types are what is true of every
    // connection, and the page names those.
    const landing = documents.find((d) => d.page.slug === "")!.html;
    expect(landing).toContain("your ERP · invoice");
    expect(landing).toContain("your ERP · bill");
  });

  it("uses an item code that decodes to nothing", () => {
    // Shaped like a catalogue code and meaning nothing in any of them.
    expect(EXAMPLE_ITEM).not.toMatch(TRADE);
    expect(documents.find((d) => d.page.slug === "")!.html).toContain(EXAMPLE_ITEM);
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

describe("the wordmark in the nav", () => {
  it("points at the site root on every page, not at a fragment", () => {
    // The landing page's logo was `href="#top"`, so pressing it navigated:
    // the address bar became `/#top`, and that is what a visitor then copied,
    // shared or bookmarked — a URL that reads like a section deep-link to a
    // section nobody links to. It is `/` now on both surfaces, and Landing
    // cancels the reload on a plain click so the mark scrolls to the top
    // without touching the URL at all. The static document keeps the working
    // link for a reader whose bundle never arrives.
    //
    // Pinned here because the fault and the fix are each one attribute, and
    // `#top` is the thing a nav logo is most often written as.
    for (const { page, html } of documents) {
      const logo = html.match(/<a[^>]*class="lp-logo"[^>]*>/);
      expect(logo, `/${page.slug} has no wordmark`).not.toBeNull();
      expect(logo![0], `/${page.slug} wordmark`).toContain('href="/"');
    }
  });
});

describe("the landing page's own fragments", () => {
  /** Both renders, not just the prerendered one.
   *
   *  `renderLandingMarkup` calls `<Landing onEnter />` with no `onDemo`, so the
   *  static document only ever carries one side of the hero's ternary — and the
   *  side it leaves out is the one a deployment with a demonstration workspace
   *  actually serves. Checking `documents` alone therefore proves nothing about
   *  half the page, which is why `#demo` was reachable and unpinned. */
  const variants = [
    ["without a demo workspace", renderToStaticMarkup(<Landing onEnter={() => {}} />)],
    ["with a demo workspace",
      renderToStaticMarkup(<Landing onEnter={() => {}} onDemo={() => {}} />)],
  ] as const;

  /** Fragments that name a door rather than a section, and so carry no `id`.
   *
   *  `#signin` is read by `doorFromHash` in PlatformApp on mount: pressing it
   *  before the bundle has hydrated leaves `/#signin` in the address bar, and
   *  the app opens the sign-in card when it arrives. It is the one fragment on
   *  this page whose job is to be in the URL. */
  const DOORS = new Set(["signin"]);

  it("points every same-page anchor at a section that exists", () => {
    // The mirror of the cross-page test above, and the half that was missing.
    // A dangling `#fragment` is not an error a browser reports: the page does
    // not move, the address bar keeps the fragment, and the reader is left on
    // a URL that reads like a deep-link to a section nobody can find. That is
    // how `#demo` survived. Its button cancels the jump, so a plain press
    // never reached the href at all — what reached it were the presses the
    // handler never sees, and a middle-click opened a second tab on `/#demo`
    // that scrolled nowhere.
    for (const [what, html] of variants) {
      const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
      const fragments = [...html.matchAll(/href="#([^"]+)"/g)].map((m) => m[1]);
      expect(fragments.length).toBeGreaterThan(0);
      for (const frag of fragments) {
        if (DOORS.has(frag)) continue;
        expect(ids.has(frag), `${what}: the landing links to #${frag}, which no element carries`)
          .toBe(true);
      }
    }
  });
});
