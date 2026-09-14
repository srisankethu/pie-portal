/**
 * Bake the public landing page into dist/index.html, and emit the crawl
 * files that need an absolute origin.
 *
 * Runs after `vite build` (see the build script in package.json). Without
 * this step the server's answer to `/` is an empty `<div id="root">` — a
 * crawler that does not execute JavaScript reads a titled, blank document,
 * which is exactly what the Aug 2026 SEO audit found deployed. The landing
 * already exists as an audited React component (src/landing/Landing.tsx);
 * this renders that same component to static HTML rather than maintaining a
 * second, hand-written copy that would drift from it.
 *
 * What it does, in order:
 *   1. Renders src/landing/prerender.tsx through Vite's SSR pipeline (so
 *      TSX and the CSS import need no second toolchain) into #root.
 *   2. Injects a static :root token rule so the pre-JavaScript paint uses
 *      the theme's real palette (the tokens are otherwise emitted by JS).
 *   3. Injects the origin-dependent head tags — canonical, og:url, JSON-LD —
 *      reading title and description back out of the built document so those
 *      strings live in exactly one place (index.html).
 *   4. Writes robots.txt and sitemap.xml into dist/. They are generated here
 *      rather than committed to public/ because both carry the absolute
 *      origin, and the origin is a build-time setting.
 *
 * The origin those tags carry is `SITE_URL` in src/landing/site.ts — the one
 * place this site's address is written down. The SITE_ORIGIN env var overrides
 * it for a self-host behind its own domain; it is an override, not the source.
 * Fails loudly — a broken prerender must fail the build, not ship an empty
 * page quietly, and not ship one that names the wrong host as canonical.
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createServer } from "vite";

const DIST = path.resolve(process.cwd(), "dist");
const INDEX = path.join(DIST, "index.html");
const EMPTY_ROOT = '<div id="root"></div>';

function fail(message) {
  console.error(`prerender: ${message}`);
  process.exit(1);
}

/** One named head value out of the built document, or a loud failure —
 *  the JSON-LD below must restate the page's own metadata, never a second
 *  copy that can drift from it. */
function extract(html, re, what) {
  const m = html.match(re);
  if (!m) fail(`could not find ${what} in dist/index.html`);
  return m[1];
}

const html = await readFile(INDEX, "utf8").catch(() =>
  fail("dist/index.html not found — run `vite build` first"),
);

if (html.split(EMPTY_ROOT).length !== 2) {
  fail(
    `expected exactly one ${EMPTY_ROOT} in dist/index.html — ` +
      "the entry markup changed; update this script with it",
  );
}

// Vite's SSR transform compiles the TSX and tolerates the CSS side-effect
// import; middlewareMode means no port is opened. This is the documented
// low-level SSR path, used here as a build step rather than a server.
//
// Loaded once, for every page. The module exports a registry (`PAGES`) rather
// than one render function, so adding a document is an entry in a type-checked
// list — not a second call to this script, and not a second Vite server, which
// is the expensive way to get this wrong.
const vite = await createServer({
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
});
let pages, tokenCss, gaps, siteUrl, faq;
try {
  const mod = await vite.ssrLoadModule("/src/landing/prerender.tsx");
  pages = mod.PAGES;
  tokenCss = mod.landingTokenCss();
  gaps = mod.contentGaps();
  // The site's own address, from the one module that states it. Loaded
  // through the same SSR server rather than duplicated here, so the constant
  // this build writes into every canonical tag is the one `tsc -b` checks and
  // `site.test.ts` asserts — not a second copy in a script nothing type-checks.
  ({ SITE_URL: siteUrl } = await vite.ssrLoadModule("/src/landing/site.ts"));
  // The same array the landing page renders its visible FAQ from. The whole
  // point of reading it here is that the JSON-LD cannot say something the
  // document does not — see the FAQPage note in `documentFor`.
  ({ FAQ: faq } = await vite.ssrLoadModule("/src/landing/faq.ts"));
} finally {
  await vite.close();
}

// `||`, not `??`: docker passes the build-arg as an empty string when unset,
// and an empty origin would write canonical="/" and a sitemap of relative
// URLs. The override exists for a self-host behind its own domain; the
// default is this site, and no longer the platform host it was deployed to.
const SITE_ORIGIN = (process.env.SITE_ORIGIN || siteUrl).replace(/\/+$/, "");

if (!/^https?:\/\/[^/]+$/.test(SITE_ORIGIN)) {
  fail(
    `SITE_ORIGIN must be an absolute origin with no path or trailing slash, got "${SITE_ORIGIN}"`,
  );
}

if (!Array.isArray(pages) || pages.length === 0) {
  fail("src/landing/prerender.tsx exported no PAGES to render");
}

// The landing's own title and description live in index.html, because that is
// also the document `npm run dev` serves and the shell an un-prerendered
// deployment would show. Read back rather than restated. Every other page's
// single source is its own registry entry.
const landingTitle = extract(html, /<title>([^<]+)<\/title>/, "<title>");
const landingDescription = extract(
  html,
  /<meta\s+name="description"\s+content="([^"]+)"/,
  'the <meta name="description">',
);

/** Replace exactly one occurrence, or fail the build.
 *
 *  A blind `String.replace` in a per-page loop no-ops silently when the tag
 *  shape changes, and the symptom is three sub-pages quietly sharing the
 *  landing page's title — which is precisely the soft-duplicate problem these
 *  pages exist to fix. */
function replaceOnce(source, pattern, replacement, what) {
  const matches = source.match(pattern);
  if (!matches) fail(`could not find ${what} to replace`);
  // Flags deduplicated: `new RegExp(src, "gg")` is a SyntaxError, and a
  // guard that throws one instead of naming the tag it could not replace is
  // a guard that has stopped helping.
  const global = new RegExp(pattern.source, [...new Set(`${pattern.flags}g`)].join(""));
  if ((source.match(global) ?? []).length !== 1) {
    fail(`expected exactly one ${what}; the entry document changed`);
  }
  return source.replace(pattern, replacement);
}

/** Attribute-safe: these strings end up inside double-quoted attributes. */
function attr(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** The document for one registry entry, built from the untouched template.
 *
 *  Every page is derived from `html` as read off disk — never from an already
 *  injected document — because the `#root` guard above holds for the template
 *  and would not hold for a page that had been filled in once already. */
function documentFor(page) {
  const markup = page.render();
  if (!markup || !markup.includes("<h1")) {
    fail(`rendered markup for /${page.slug} has no <h1> — refusing to bake it`);
  }

  const title = page.title ?? landingTitle;
  const description = page.description ?? landingDescription;
  const url = `${SITE_ORIGIN}/${page.slug}`;

  let out = html;
  // Each tag follows its own field. Gating all four on `page.title` alone let a
  // page with a description and no title keep the landing's description in the
  // head while its JSON-LD, built from the same `description` variable below,
  // stated its own — two answers to one question in one document.
  if (page.title !== null) {
    out = replaceOnce(out, /<title>[^<]*<\/title>/,
                      `<title>${attr(title)}</title>`, "<title>");
    out = replaceOnce(out, /<meta\s+property="og:title"\s+content="[^"]*"\s*\/?>/,
                      `<meta property="og:title" content="${attr(title)}" />`,
                      'the <meta property="og:title">');
  }
  if (page.description !== null) {
    out = replaceOnce(out, /<meta\s+name="description"\s+content="[^"]*"\s*\/?>/,
                      `<meta name="description" content="${attr(description)}" />`,
                      'the <meta name="description">');
    out = replaceOnce(out, /<meta\s+property="og:description"\s+content="[^"]*"\s*\/?>/,
                      `<meta property="og:description" content="${attr(description)}" />`,
                      'the <meta property="og:description">');
  }

  // A standalone document ships without the application bundle. It is not an
  // optimisation: `main.tsx` mounts a HashRouter and `createRoot().render()`
  // replaces whatever is in `#root`, so a sub-page that loaded the bundle
  // would have its content thrown away and the landing drawn over it. The
  // stylesheet link stays — that is the same one file every page reads.
  if (page.standalone) {
    out = replaceOnce(out, /\s*<script type="module"[^>]*><\/script>/,
                      "", "the module script tag");
  }

  // Organization + WebSite + WebPage on every page; SoftwareApplication and
  // FAQPage on the landing page alone. Every value is true of the product and
  // already stated on the page it appears on.
  //
  // Still deliberately absent: offers, ratings, reviews, BreadcrumbList —
  // schema the audit's ground rules exclude, values the product does not have
  // (a rating, an award), and, for the breadcrumb, a trail no page renders.
  // Schema may only restate what is on the page.
  //
  // FAQPage has moved from that list to the graph, and only because the page
  // moved first. It was excluded when there was no FAQ; there are now visible
  // ones — at the bottom of the landing document and on every `/industries/`
  // and `/roles/` page — each rendered from the same array this node is built
  // from, so the schema restates the page rather than describing a page that
  // does not exist. That ordering is the whole rule: Google's FAQPage guidance
  // requires the question and answer to be visible, and a build that emitted
  // this node from a second copy of the strings would be one edit away from a
  // manual action.
  //
  // It was landing-only when the landing was the only document that rendered
  // an FAQ, written as an `isLanding` branch. Three more families render one
  // now, so the condition is the registry field — `page.faq`, which the
  // component read — and a page that shows no FAQ still gets no node. The rule
  // did not move; the number of pages that satisfy it did.
  //
  // SoftwareApplication is likewise landing-only now, where it used to appear
  // on all eight. The node is referenced from every WebPage's `about`, so the
  // ERP pages still point at the product — they simply no longer each carry a
  // full copy of its declaration. One description of one application, at one
  // @id, on the page that is about it.
  //
  // The WebPage node is per-page — its own @id and url — while Organization,
  // WebSite and SoftwareApplication are one node each, referenced rather than
  // re-declared. Eight documents all claiming to be `${SITE_ORIGIN}/#webpage`
  // would describe one page eight times, which is worse than describing none.
  const isLanding = page.slug === "";

  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${SITE_ORIGIN}/#organization`,
        name: "PIE",
        url: `${SITE_ORIGIN}/`,
        // The favicon is the only mark this repository has. Naming it is
        // honest and useful — an SVG scales to whatever a consumer wants —
        // and inventing a `/logo.png` that 404s would be worse than omitting
        // the property, which is the real alternative here.
        logo: `${SITE_ORIGIN}/favicon.svg`,
        // TODO: add the real profile URLs once they exist — LinkedIn, GitHub,
        // Crunchbase, an X handle. `sameAs` is how a knowledge graph decides
        // that this Organization and a profile elsewhere are one entity, so
        // an empty array is a genuine gap rather than a tidy default. It ships
        // empty rather than guessed: a URL that does not resolve, or resolves
        // to somebody else's account, is a worse answer than no answer.
        sameAs: [],
      },
      {
        "@type": "WebSite",
        "@id": `${SITE_ORIGIN}/#website`,
        url: `${SITE_ORIGIN}/`,
        name: "PIE",
        publisher: { "@id": `${SITE_ORIGIN}/#organization` },
      },
      {
        "@type": "WebPage",
        "@id": `${url}#webpage`,
        url,
        name: title,
        description,
        isPartOf: { "@id": `${SITE_ORIGIN}/#website` },
        about: { "@id": `${SITE_ORIGIN}/#software` },
      },
      ...(isLanding
        ? [
            {
              "@type": "SoftwareApplication",
              "@id": `${SITE_ORIGIN}/#software`,
              name: "PIE",
              url: `${SITE_ORIGIN}/`,
              // The landing page's own meta description, read back out of the
              // built document rather than restated — so the sentence a search
              // result shows and the sentence this node carries cannot drift
              // apart. No `offers`, no `aggregateRating`: there is no public
              // price and there are no reviews, and schema that invents either
              // is the kind that gets a site's rich results turned off.
              description: landingDescription,
              applicationCategory: "BusinessApplication",
              operatingSystem: "Web",
              publisher: { "@id": `${SITE_ORIGIN}/#organization` },
            },
          ]
        : []),
      ...(page.faq?.length
        ? [
            {
              "@type": "FAQPage",
              "@id": `${url}#faq`,
              isPartOf: { "@id": `${url}#webpage` },
              mainEntity: page.faq.map((item) => ({
                "@type": "Question",
                name: item.question,
                acceptedAnswer: { "@type": "Answer", text: item.answer },
              })),
            },
          ]
        : []),
    ],
  };

  const headInjection =
    `    <style id="pie-tokens">${tokenCss}</style>\n` +
    `    <link rel="canonical" href="${url}" />\n` +
    `    <meta property="og:url" content="${url}" />\n` +
    `    <script type="application/ld+json">${JSON.stringify(jsonLd)}</script>\n`;

  return out
    .replace("</head>", `${headInjection}  </head>`)
    .replace(EMPTY_ROOT, `<div id="root">${markup}</div>`);
}

/** Where a page's document goes. `""` is dist/index.html; every other slug is
 *  a flat `<slug>.html`, which is what `vercel.json`'s `/erp/([^/]+)` rewrite
 *  and the Caddyfile's `try_files … {path}.html` both resolve `/{slug}` to. Flat rather than
 *  `<slug>/index.html` so the canonical URL carries no trailing slash and
 *  there is exactly one form of every address — on the page, in the sitemap
 *  and in the canonical tag. */
function fileFor(slug) {
  return slug === "" ? INDEX : path.join(DIST, `${slug}.html`);
}

const written = [];
for (const page of pages) {
  const file = fileFor(page.slug);
  await mkdir(path.dirname(file), { recursive: true });
  const document = documentFor(page);
  await writeFile(file, document);
  written.push({ page, file, document, size: document.length });
}

// Crawl files. /api/ is the backend proxy — token-gated anyway, but nothing
// there is a page. /operator.html is PIE's own console: it refuses every
// request without an operator key, so this is not what protects it — it is
// what keeps it out of a search result, which is a different job and the one
// robots.txt can actually do. The document carries `noindex` itself as well,
// because a crawler that ignores this file still reads that.
// Assets and the public pages stay allowed.
//
// The named agents below are the ones that read a page on behalf of somebody
// asking a question — ChatGPT's crawler and its search fetcher, Claude's,
// Perplexity's, and the token Google honours for AI Overviews and Gemini
// grounding. They are listed rather than left to `User-agent: *` for one
// reason: `*` already allows them, so the block adds no permission — what it
// adds is an unambiguous answer for an operator who checks, and a record that
// the choice was made. Several of these agents are also known to read a named
// group in preference to the wildcard, and a site that only ever answers `*`
// is a site whose intent has to be inferred.
//
// Each group restates the same two Disallow lines. That is not redundancy to
// remove: robots.txt has no inheritance, and a group with an `Allow:` and no
// `Disallow:` would open /api/ and /operator.html to exactly the agents named
// here — the opposite of what the block is for.
const CRAWL_DISALLOW = ["/api/", "/operator.html"];

// Google-Extended is not a crawler. It is a token that says whether Googlebot's
// existing crawl may be used for AI Overviews and Gemini grounding, so it never
// fetches anything itself and its own Disallow lines govern nothing. Listed
// with the others because allowing it is the same decision.
const AI_AGENTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot",
                   "Claude-User", "PerplexityBot", "Google-Extended"];

function robotsGroup(agent) {
  return `User-agent: ${agent}\n`
    + CRAWL_DISALLOW.map((path) => `Disallow: ${path}\n`).join("")
    + "Allow: /\n\n";
}

await writeFile(
  path.join(DIST, "robots.txt"),
  robotsGroup("*")
  + AI_AGENTS.map(robotsGroup).join("")
  + `Sitemap: ${SITE_ORIGIN}/sitemap.xml\n`,
);

// llms.txt — what this site is, for a model that is answering a question about
// it rather than a person browsing it.
//
// Generated rather than committed to public/, for the same two reasons
// robots.txt and sitemap.xml are: every link in it carries the absolute origin,
// which is a build-time setting, and the list of pages comes from `PAGES` — so
// a connector added to `erp.ts` appears here without anybody remembering. A
// hand-written copy in public/ would be a third list of the same seven systems
// and the first one to go stale.
//
// Served as text/plain at /llms.txt by both edges without any extra rule: it is
// a real file in dist/, Vercel checks the filesystem before it applies the SPA
// rewrite, and the Caddyfile's `try_files {path}` finds it before the
// `/index.html` fallback. That is the same mechanism that already serves
// robots.txt and sitemap.xml, which is the evidence that it works.
//
// Every line below is a claim the site already makes — the definition is the
// landing page's own meta description, the ERP list is `PAGES`, the FAQ is the
// FAQ, and the differentiators are the TrustBand's bullets compressed. Nothing
// is written for this file alone. A model reading this and a person reading the
// page have to come away with the same understanding, and the way to guarantee
// that is to have no sentence here that is not there.
const erpPages = pages.filter((page) => page.slug.startsWith("erp/"));

await writeFile(
  path.join(DIST, "llms.txt"),
  `# PIE\n\n`
  + `> ${landingDescription}\n\n`
  + `## Who it is for\n\n`
  + `B2B distributors running one or more of the ERPs below, where somebody `
  + `prices quotes by hand and margin is checked after the fact rather than `
  + `before the quote goes out. Three roles use it: owners and finance, who set `
  + `the margin floors and see cost and margin; approvers, who decide on lines `
  + `that breached a floor; and the sales desk, which quotes against the floor `
  + `and never receives a cost or margin field.\n\n`
  + `## Supported ERPs\n\n`
  + erpPages.map((page) => `- ${page.erp.name}\n`).join("")
  + `\n## What is true of it\n\n`
  + `- The AI never computes a number. Every figure is deterministic arithmetic `
  + `on the customer's own records; turn the AI off and every number still `
  + `works. The AI reads those numbers and explains them.\n`
  + `- Same inputs, same answer, every time, with a paper trail. Every figure `
  + `names the policy version that produced it, so a price quoted last quarter `
  + `still explains itself.\n`
  + `- Cost and margin never reach the sales desk. The fields are absent from `
  + `the server's response, not hidden in the browser.\n`
  + `- It works on top of the ERP and does not replace it. Four of the seven `
  + `connectors can create an agreed quote back as an estimate or sales quote; `
  + `the other three are read-only. Nothing else is ever written.\n`
  + `- The first sync reads 18 months by default, and the customer can set an `
  + `earlier date before it runs.\n`
  + `- The customer's data is theirs: one export of everything the organization `
  + `owns, and erasure that destroys the tenant key and issues a signed `
  + `receipt naming what was and was not encrypted.\n`
  + `\n## Pages\n\n`
  + `- [PIE](${SITE_ORIGIN}/): ${landingTitle}\n`
  + erpPages
      .map((page) => `- [${page.erp.name}](${SITE_ORIGIN}/${page.slug}): ${page.description}\n`)
      .join("")
  + `\n## Questions\n\n`
  + faq.map((item) => `### ${item.question}\n\n${item.answer}\n\n`).join(""),
);

// Generated from the same registry the documents are, so a page cannot exist
// without a sitemap entry or a sitemap entry without a page. Every other route
// in this application is a hash fragment on the landing document or sits
// behind sign-in, and neither is a URL a crawler can be given.
const lastmod = new Date().toISOString().slice(0, 10);
await writeFile(
  path.join(DIST, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    pages
      .map((page) =>
        `  <url>\n    <loc>${SITE_ORIGIN}/${page.slug}</loc>\n` +
        `    <lastmod>${lastmod}</lastmod>\n  </url>\n`)
      .join("") +
    `</urlset>\n`,
);

console.log(
  `prerender: ${written.length} page${written.length === 1 ? "" : "s"} baked for ` +
    `${SITE_ORIGIN} — ` +
    written
      .map(({ page, size }) =>
        `/${page.slug} (${(size / 1024).toFixed(1)} kB${page.standalone ? ", no bundle" : ""})`)
      .join(", ") +
    `; robots.txt + sitemap.xml + llms.txt written`,
);

// What the site is still waiting for.
//
// A `{{…}}` token no longer reaches a page: the block that would have shown
// one is not rendered at all, which is what makes the site deployable while
// its content is incomplete. That removed the old signal along with the
// defect — a build that says nothing is a build nobody learns anything from —
// so the report is now over the *source*: which slots are empty, and what a
// visitor is not seeing because of it.
if (gaps.length) {
  console.warn(
    `prerender: ${gaps.length} content slot${gaps.length === 1 ? " is" : "s are"} ` +
      "still empty, and the pages hide what depends on them:",
  );
  for (const { slot, effect } of gaps) {
    console.warn(`    ${slot.padEnd(38)} → ${effect}`);
  }
  console.warn(
    "  Nothing here is broken and nothing shows a placeholder; these are " +
      "sections a visitor is not seeing.\n" +
      "  docs/marketing-placeholders.md says what each one needs.",
  );
}

// And the backstop the above replaces: a token reaching a built page is now a
// defect rather than a reminder, because something rendered one instead of
// hiding its block. It should never fire.
const leaked = [...new Set(
  written.flatMap(({ document }) => document.match(/\{\{[A-Z0-9_]+\}\}/g) ?? []),
)].sort();
if (leaked.length) {
  fail(
    `a placeholder reached a built page: ${leaked.join(", ")}. ` +
      "Content that is not filled in must hide its block, never render its token.",
  );
}
