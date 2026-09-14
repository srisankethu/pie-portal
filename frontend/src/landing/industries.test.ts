import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ERP_PAGES } from "./erp";
import { INDUSTRY_PAGES, verticalLabel } from "./industries";
import { IndustryPage } from "./IndustryPage";

/** What `renderToStaticMarkup` does to page copy on the way into the document.
 *
 *  Worth a helper rather than a `replace` at the call site: an apostrophe is
 *  the character these questions are full of, it becomes `&#x27;`, and a test
 *  comparing raw copy against rendered markup fails on the one page whose FAQ
 *  reads most naturally. */
function escapeForMarkup(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

/** Rendered once. Every assertion below reads the markup a visitor gets, not
 *  the registry that produced it — a claim can be true in the data and absent
 *  from the page, and it is the page that a reader is held to. */
const rendered = INDUSTRY_PAGES.map((page) => ({
  page,
  html: renderToStaticMarkup(IndustryPage({ page })),
}));

describe("the industry registry", () => {
  it("has an entry for every trade that passed the gate, and no others", () => {
    // Seven, and the list is the point rather than the count.
    // `docs/vertical-strategy.md` scores sixteen trades. Two hard gates decide
    // this list: is there per-line pricing discretion, and is the typical ERP
    // one of the seven we read? Everything else — rebates, interchange,
    // fabricated assemblies — is a disclosure on the page rather than a reason
    // to withhold it, which is the same trade `/erp/sage-100` makes on a book
    // with no purchase cost at all.
    //
    // What stays out: packaging and building products fail the ERP gate
    // (Amtech, ePS, BisTrack, DMSi); safety, JanSan, lab and medical, food
    // service and pharma fail the discretion gate, because a GPO contract or a
    // weekly price file has already removed the decision this product acts on.
    // Welding and gas is held for a third reason — half that trade is cylinder
    // rental, a recurring-revenue model nothing here represents.
    //
    // This is not a check that seven is right forever. It is a check that an
    // eighth is a deliberate act somebody came here to make, rather than
    // something that happened because a page looked easy to copy.
    expect(INDUSTRY_PAGES.map((p) => p.slug)).toEqual([
      "industrial-mro",
      "cutting-tools",
      "fasteners",
      "bearings-power-transmission",
      "fluid-power",
      "electrical",
      "plumbing-pvf",
    ]);
  });

  it("writes every trade's label in sentence case with its acronyms intact", () => {
    // The seven strings, written out. `verticalLabel` derives them from `short`
    // by capitalising one character, which is enough only while every acronym
    // in this registry is already upper-case inside the string — so the check
    // that matters is not "the function runs" but "the seven results are the
    // seven labels". A trade added as "hvac and refrigeration" would come back
    // "Hvac and refrigeration" and fail here rather than ship.
    expect(INDUSTRY_PAGES.map(verticalLabel)).toEqual([
      "Industrial and MRO",
      "Cutting tools",
      "Fasteners",
      "Bearings and power transmission",
      "Fluid power",
      "Electrical",
      "Plumbing and PVF",
    ]);
  });

  it("keeps the label out of the URL", () => {
    // Casing is a rendering decision and a slug is an address. A slug that
    // followed the label would be a redirect nobody wrote.
    for (const page of INDUSTRY_PAGES) {
      expect(page.slug).toBe(page.slug.toLowerCase());
    }
  });

  it("gives every page a unique slug", () => {
    const slugs = INDUSTRY_PAGES.map((p) => p.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("links only to ERP pages that exist", () => {
    // These are the cross-family links, and they are the ones that rot: a
    // renamed ERP slug leaves valid markup pointing at a 404, which nothing
    // else on the site would notice.
    const known = new Set(ERP_PAGES.map((p) => p.slug));
    for (const page of INDUSTRY_PAGES) {
      expect(page.erpSlugs.length).toBeGreaterThan(0);
      for (const slug of page.erpSlugs) {
        expect(known.has(slug), `${page.slug} links to /erp/${slug}, which does not exist`)
          .toBe(true);
      }
    }
  });
});

describe("what an industry page may claim", () => {
  // The vocabulary of cross-referencing. A page whose trade PIE decodes may
  // use it; a page whose trade PIE only text-matches may not, because the
  // difference is a named constant in another repository and not a matter of
  // emphasis. See `SpeedReach` in industries.ts, and `CORE_SLOTS` in
  // backend/app/decoding/schema.py.
  const INTERCHANGE = /\b(cross[- ]?referenc\w*|interchange\w*|equivalents?)\b/i;

  it("never promises interchange on a trade the engine does not decode", () => {
    const matched = rendered.filter(({ page }) => page.speed === "matched");
    // The screen has to have something in it — a filter that matched nothing
    // would pass in silence, which is the failure CLAUDE.md §1 names.
    expect(matched.length).toBeGreaterThan(0);
    for (const { page, html } of matched) {
      // Stripped of the section that exists precisely to say we do NOT do
      // this: `notServed` names cross-manufacturer interchange in order to
      // disclaim it, and a test that failed on the disclaimer would be a test
      // that deleted the disclaimer.
      const claims = html.split('<section id="gaps">')[0];
      const hit = claims.match(INTERCHANGE);
      expect(hit?.[0] ?? null,
        `/industries/${page.slug} is speed:"matched" and claims “${hit?.[0]}” `
        + "outside its own limits section. Text matching against the reader's "
        + "own catalogue is not cross-referencing.").toBeNull();
    }
  });

  it("says what it does not do, at length, on every page", () => {
    // The ERP pages print seven gaps each and `erp.ts` explains why. Three is
    // the floor here, not the target.
    for (const { page } of rendered) {
      expect(page.notServed.length,
        `/industries/${page.slug} lists too few limits to be believed`)
        .toBeGreaterThanOrEqual(3);
    }
  });

  it("discloses the rebate gap on every page, because it decides the fit", () => {
    // The one limitation that determines whether this product works on a book
    // at all: margin is computed from the AP-invoice line, so where purchases
    // are claimed back afterwards the computed floor sits above the real one
    // and the platform holds profitable lines. `docs/vertical-strategy.md` §6
    // defers two whole verticals on it. A page that omitted it would be
    // selling into books it cannot serve.
    for (const { page, html } of rendered) {
      expect(html, `/industries/${page.slug} does not disclose the rebate gap`)
        .toMatch(/special pricing agreements/i);
    }
  });

  it("renders every question it declares, so the FAQ schema restates the page", () => {
    // `scripts/prerender.mjs` emits FAQPage from `page.faq`. That is only
    // legitimate while the component renders the same array — schema may
    // restate what is on the page and nothing else.
    for (const { page, html } of rendered) {
      expect(page.faq.length).toBeGreaterThan(0);
      for (const { question } of page.faq) {
        expect(html, `/industries/${page.slug} declares a question it does not render`)
          .toContain(escapeForMarkup(question));
      }
    }
  });
});

describe("query intent, against the /erp/ family", () => {
  it("names no ERP in a title, description or h1", () => {
    // The two families answer different questions — "will it work with my
    // system" and "will it work for my trade" — and two pages competing for
    // one query is two pages ranking for neither. This is the rule that keeps
    // them apart, and it is checked against the metadata a search engine
    // actually reads.
    const names = ERP_PAGES.flatMap((p) => [p.name, p.short]);
    for (const { page, html } of rendered) {
      // Tags stripped and whitespace collapsed: the headline puts one word in
      // an <em>, so the raw markup reads "industrial and <em>MRO</em>" and a
      // substring match on it fails for a heading that is perfectly correct.
      // The rule is about the sentence a reader sees, not the elements it is
      // built from.
      const h1 = (html.match(/<h1[^>]*>(.*?)<\/h1>/s)?.[1] ?? "")
        .replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
      for (const name of names) {
        for (const [field, value] of Object.entries(
          { title: page.title, description: page.description, h1 },
        )) {
          expect(value.includes(name),
            `/industries/${page.slug} names “${name}” in its ${field}`).toBe(false);
        }
      }
    }
  });

  it("still sends the reader to the ERP pages from the body", () => {
    // The other half: separated in metadata, joined in the document. A reader
    // who has decided the trade fits asks about their system next.
    for (const { page, html } of rendered) {
      for (const slug of page.erpSlugs) {
        expect(html, `/industries/${page.slug} does not link /erp/${slug}`)
          .toContain(`href="/erp/${slug}"`);
      }
    }
  });
});

describe("the pages stay distinct from each other", () => {
  /** The narrative fields — the ones whose job is to argue, and therefore the
   *  ones a fifth page written in a hurry would copy from the first.
   *
   *  `notServed` is deliberately **not** here, and that exclusion is the more
   *  interesting half of this check. Two trades genuinely share a limitation:
   *  the rebate disclosure is the same fact on every book, and `industries.
   *  test.ts` already requires it on all of them. A distinctness rule applied
   *  to that list would be a rule demanding the same truth be reworded per
   *  page — which is how a limits section turns into copy, and copy is what a
   *  reader stops believing. Shared facts stay word-for-word; shared
   *  *arguments* are the defect. */
  function narrative(page: (typeof INDUSTRY_PAGES)[number]): string[] {
    return [
      page.sub,
      page.problem.body,
      page.problem.detail,
      page.resolution.body,
      ...page.resolution.points,
      ...page.fit.map((f) => f.body),
      ...page.faq.map((f) => f.answer),
    ]
      .flatMap((text) => text.split(/(?<=[.?])\s+/))
      .map((s) => s.replace(/\s+/g, " ").trim())
      .filter((s) => s.length > 40);
  }

  it("gives every page its own headline, problem and lead", () => {
    // The three fields a reader sees before deciding to stay. If two pages
    // share one of these they are one page with two addresses, which is the
    // doorway pattern a search engine is looking for.
    for (const field of ["sub", "title", "description"] as const) {
      const values = INDUSTRY_PAGES.map((p) => p[field]);
      expect(new Set(values).size, `two pages share a ${field}`).toBe(values.length);
    }
    const problems = INDUSTRY_PAGES.map((p) => p.problem.title);
    expect(new Set(problems).size, "two pages state the same problem").toBe(problems.length);
  });

  it("keeps the argument on each page mostly its own", () => {
    // Sentence overlap, pairwise. A little is fine and some is unavoidable —
    // both pages describe one product. A lot means the second page was written
    // by find-and-replace on the first, and that is the moment this family
    // stops being worth having.
    for (const a of INDUSTRY_PAGES) {
      for (const b of INDUSTRY_PAGES) {
        if (a.slug >= b.slug) continue;
        const [x, y] = [narrative(a), narrative(b)];
        const shared = x.filter((s) => y.includes(s));
        const ratio = shared.length / Math.min(x.length, y.length);
        expect(ratio,
          `/industries/${a.slug} and /industries/${b.slug} share `
          + `${shared.length} of ${Math.min(x.length, y.length)} sentences `
          + `(${Math.round(ratio * 100)}%). Shared facts belong in notServed; `
          + `a shared argument means one of these pages is not needed.`)
          .toBeLessThan(0.25);
      }
    }
  });
});

describe("the first heading names what the page is about", () => {
  // The rule the ERP family is already tested on, applied to this one.
  // `erp.ts` states it: a distributor searching for their own system should
  // land on a page that names it in the first line, because "Prophet 21" is
  // what they call their problem and a page that says "your ERP" is a page
  // about somebody else.
  //
  // This family shipped without that check and immediately broke it. The
  // cutting tools page led with "Quote without losing the margin in the
  // cross-reference" — true, well-formed, and it never said "cutting tools",
  // so the one reader it was written for could not tell it was theirs.
  it("puts the trade in the h1 of every page", () => {
    expect(INDUSTRY_PAGES.length).toBeGreaterThan(0);
    for (const page of INDUSTRY_PAGES) {
      const html = renderToStaticMarkup(IndustryPage({ page }));
      const h1 = (html.match(/<h1[^>]*>(.*?)<\/h1>/s)?.[1] ?? "")
        .replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
      // The label, not `short`: a trade name in a heading is a name, and the
      // h1 is one of the places this site renders it capitalised. Asserting the
      // lower-case fragment here would pass on "Quote cutting tools" and on
      // nothing the reader is actually shown.
      expect(h1, `/industries/${page.slug} h1 does not name ${verticalLabel(page)}`)
        .toContain(verticalLabel(page));
    }
  });
});
