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
import { readFile, writeFile } from "node:fs/promises";
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
const vite = await createServer({
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
});
let markup, tokenCss;
try {
  const mod = await vite.ssrLoadModule("/src/landing/prerender.tsx");
  markup = mod.renderLandingMarkup();
  tokenCss = mod.landingTokenCss();
} finally {
  await vite.close();
}

if (!markup || !markup.includes("<h1")) {
  fail("rendered landing markup has no <h1> — refusing to bake it");
}

const title = extract(html, /<title>([^<]+)<\/title>/, "<title>");
const description = extract(
  html,
  /<meta\s+name="description"\s+content="([^"]+)"/,
  'the <meta name="description">',
);

// WebSite + WebPage + SoftwareApplication, every value true of the product
// and already stated on the page. Deliberately absent: offers, ratings,
// reviews, FAQPage — schema the audit's ground rules exclude, and values
// (a rating, an award) the product simply does not have.
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
      "@id": `${SITE_ORIGIN}/#webpage`,
      url: `${SITE_ORIGIN}/`,
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
      description,
      applicationCategory: "BusinessApplication",
      operatingSystem: "Web browser",
    },
  ],
};

const headInjection =
  `    <style id="pie-tokens">${tokenCss}</style>\n` +
  `    <link rel="canonical" href="${SITE_ORIGIN}/" />\n` +
  `    <meta property="og:url" content="${SITE_ORIGIN}/" />\n` +
  `    <script type="application/ld+json">${JSON.stringify(jsonLd)}</script>\n`;

const out = html
  .replace("</head>", `${headInjection}  </head>`)
  .replace(EMPTY_ROOT, `<div id="root">${markup}</div>`);
await writeFile(INDEX, out);

// Crawl files. /api/ is the backend proxy — token-gated anyway, but nothing
// there is a page. Assets and the one public page stay allowed.
await writeFile(
  path.join(DIST, "robots.txt"),
  `User-agent: *\nDisallow: /api/\n\nSitemap: ${SITE_ORIGIN}/sitemap.xml\n`,
);

// One canonical public URL is the honest sitemap: every other route is a
// hash fragment on this document or sits behind sign-in.
const lastmod = new Date().toISOString().slice(0, 10);
await writeFile(
  path.join(DIST, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    `  <url>\n    <loc>${SITE_ORIGIN}/</loc>\n    <lastmod>${lastmod}</lastmod>\n  </url>\n` +
    `</urlset>\n`,
);

console.log(
  `prerender: landing baked into dist/index.html (${(out.length / 1024).toFixed(1)} kB), ` +
    `robots.txt + sitemap.xml written for ${SITE_ORIGIN}`,
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
const placeholders = [...new Set(out.match(/\{\{[A-Z0-9_]+\}\}/g) ?? [])].sort();
if (placeholders.length) {
  console.warn(
    `prerender: WARNING — ${placeholders.length} unreplaced placeholder` +
      `${placeholders.length === 1 ? "" : "s"} in the built page: ` +
      `${placeholders.join(", ")}. These are visible to visitors. ` +
      "Replace them before this build is promoted to production.",
  );
}
