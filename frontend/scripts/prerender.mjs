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
 * SITE_ORIGIN is that setting: set the env var to build for a custom domain;
 * the default is the current public home. Fails loudly — a broken prerender
 * must fail the build, not ship an empty page quietly.
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { createServer } from "vite";

// `||`, not `??`: docker passes the arg as an empty string when unset, and an
// empty origin would write canonical="/" and a sitemap of relative URLs.
const SITE_ORIGIN = (
  process.env.SITE_ORIGIN || "https://pie-portal-seven.vercel.app"
).replace(/\/+$/, "");

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
let pages, tokenCss;
try {
  const mod = await vite.ssrLoadModule("/src/landing/prerender.tsx");
  pages = mod.PAGES;
  tokenCss = mod.landingTokenCss();
} finally {
  await vite.close();
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
  const global = new RegExp(pattern.source, `${pattern.flags}g`);
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
  if (page.title !== null) {
    out = replaceOnce(out, /<title>[^<]*<\/title>/,
                      `<title>${attr(title)}</title>`, "<title>");
    out = replaceOnce(out, /<meta\s+name="description"\s+content="[^"]*"\s*\/?>/,
                      `<meta name="description" content="${attr(description)}" />`,
                      'the <meta name="description">');
    out = replaceOnce(out, /<meta\s+property="og:title"\s+content="[^"]*"\s*\/?>/,
                      `<meta property="og:title" content="${attr(title)}" />`,
                      'the <meta property="og:title">');
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

  // WebSite + WebPage + SoftwareApplication, every value true of the product
  // and already stated on the page. Deliberately absent: offers, ratings,
  // reviews, FAQPage — schema the audit's ground rules exclude, and values
  // (a rating, an award) the product simply does not have. BreadcrumbList is
  // absent for the same reason it always was: no page here renders a visible
  // breadcrumb, and schema may only restate what is on the page.
  //
  // The WebPage node is per-page — its own @id and url — while WebSite and
  // SoftwareApplication are one node each, referenced rather than re-declared.
  // Four documents all claiming to be `${SITE_ORIGIN}/#webpage` would describe
  // one page four times, which is worse than describing none.
  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${SITE_ORIGIN}/#website`,
        url: `${SITE_ORIGIN}/`,
        name: "PIE",
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
      {
        "@type": "SoftwareApplication",
        "@id": `${SITE_ORIGIN}/#software`,
        name: "PIE",
        url: `${SITE_ORIGIN}/`,
        description: landingDescription,
        applicationCategory: "BusinessApplication",
        operatingSystem: "Web browser",
      },
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
 *  a flat `<slug>.html`, which is what `vercel.json`'s explicit rewrites and
 *  the Caddyfile's `try_files` both resolve `/{slug}` to. Flat rather than
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
// there is a page. Assets and the public pages stay allowed.
await writeFile(
  path.join(DIST, "robots.txt"),
  `User-agent: *\nDisallow: /api/\n\nSitemap: ${SITE_ORIGIN}/sitemap.xml\n`,
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
    `; robots.txt + sitemap.xml written`,
);

// Unreplaced placeholders, named out loud.
//
// The repositioned page carries `{{…}}` tokens on purpose — a price the owner
// has not fixed yet, a scheduling link, a customer logo that must be real and
// permissioned before it can appear. Deliberate, and each one is a thing that
// must not reach a visitor. A checklist in a commit message is read once; this
// is read on every build, and it prints what is actually in the artefact rather
// than what somebody remembered to write down.
//
// A warning, not a failure: the branch has to be buildable and deployable to a
// preview while the real values are still being decided, and a build that
// refuses would only teach somebody to delete the check.
const placeholders = [...new Set(
  written.flatMap(({ document }) => document.match(/\{\{[A-Z0-9_]+\}\}/g) ?? []),
)].sort();
if (placeholders.length) {
  console.warn(
    `prerender: WARNING — ${placeholders.length} unreplaced placeholder` +
      `${placeholders.length === 1 ? "" : "s"} in the built page: ` +
      `${placeholders.join(", ")}. These are visible to visitors. ` +
      "Replace them before this build is promoted to production.",
  );
}
