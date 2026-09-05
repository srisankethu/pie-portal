/* The public pages — the landing page and one document per ERP — measured in a
 * real browser at phone widths.
 *
 * The sibling of `a11y.mjs`, and separate from it for one reason: that script
 * signs in and drives the app on the dev server, and these pages do not exist
 * there. `scripts/prerender.mjs` bakes them at build time, so the dev server's
 * answer to `/erp/netsuite` is `index.html` and the landing page — the very
 * documents this measures are only in `dist/`.
 *
 *   npm run build            # required: it is dist/ that gets measured
 *   node e2e/.shots/public-a11y.mjs
 *
 * It serves `dist/` itself rather than asking for a server, so there is one
 * command and no ports to line up.
 *
 * Not in `verify.sh`, for the reason `.shots/` exists at all: it needs a
 * production build, it takes a browser and half a minute, and its output is a
 * shortlist to read rather than a verdict. Run it when you touch `landing.css`,
 * `Landing.tsx` or `ErpPage.tsx`.
 *
 * ── what it checks, and why each one is here ──
 *
 * **Sideways scroll.** `documentElement.scrollWidth` against the viewport, plus
 * the innermost elements past the right edge, because the number alone does not
 * say what pushed. /erp/zoho-books scrolled 6px at 320px: it prints its scope
 * list in full and `ZohoBooks.customerpayments.READ` has nowhere to break, so
 * as a grid item's min-content width it held the panel at 293px inside 254px.
 * Every ERP page prints its connector's identifiers; only one vocabulary was
 * long enough to show it, which is why this runs over all of them.
 *
 * **Touch targets, at 44px.** The same threshold `a11y.mjs` holds the app to,
 * applied to pages that were never measured. The ERP pages' nav links were
 * 17px high and their footer's links to each other were 17px — and that footer
 * row is the only way to get from one ERP page to another on a phone.
 *
 * **Nav height.** Not an accessibility rule; a budget. The bar is sticky, so
 * whatever it takes it takes on every screen of every scroll. The ERP pages'
 * bar was 131px against the landing page's 71 — a fifth of a phone — because
 * five links that could not collapse wrapped in place.
 *
 * Read the output as a shortlist. An inline link inside a sentence is exempt
 * from the target rule by WCAG 2.5.8 and this script cannot tell one from a
 * navigation link, so the landing footer's "Already have an account? Sign in"
 * is reported and is correct as it stands.
 */
import { chromium } from "@playwright/test";
import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";

const DIST = path.resolve(import.meta.dirname, "../../dist");
const CHROME = process.env.PLAYWRIGHT_CHROMIUM_PATH ||
  "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
/* 320 is the narrowest phone still in use and the width every overflow shows at
 * first; 430 is a current large phone. 640 is the breakpoint itself — the width
 * where the bar has the most to fit and has not yet collapsed. */
const WIDTHS = [320, 360, 390, 430, 640];
/** What a sticky bar may take on a phone before it is eating the page. The
 *  landing page's collapsed bar is the reference: logo, menu button, padding. */
const NAV_BUDGET = 90;
const TARGET = 44;

if (!fs.existsSync(path.join(DIST, "index.html"))) {
  console.error("dist/ is not built — run `npm run build` first.");
  process.exit(1);
}

const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2",
  ".woff": "font/woff", ".json": "application/json", ".xml": "application/xml",
  ".txt": "text/plain",
};
/* The prerender writes `dist/erp/netsuite.html`, and the request is for
 * `/erp/netsuite` — the extensionless form the host serves. Resolve it the same
 * way, or every ERP page silently measures the landing page instead. */
const server = http.createServer((req, res) => {
  const url = decodeURIComponent((req.url ?? "/").split("?")[0]);
  let file = path.join(DIST, url);
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    if (fs.existsSync(`${file}.html`)) file = `${file}.html`;
    else if (fs.existsSync(path.join(file, "index.html"))) file = path.join(file, "index.html");
    else file = path.join(DIST, "index.html");
  }
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] ?? "application/octet-stream" });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, r));
const BASE = `http://127.0.0.1:${server.address().port}`;

/** Every page the prerender wrote, read off the sitemap rather than listed
 *  here: a connector added to `erp.ts` gets a page, and a list in this file
 *  would not know about it. That is the failure `ErpPage.tsx`'s own footer
 *  already had once. */
const sitemap = fs.readFileSync(path.join(DIST, "sitemap.xml"), "utf8");
/* `new URL(...).pathname`, and not a regex over the `<loc>`. The regex that
   was here — `<loc>[^<]*?(\/[^<]*)<\/loc>` — is non-greedy, so it matched from
   the `//` in `https://` and every path came out as
   `//pie-portal-seven.vercel.app/erp/netsuite`. The static server below answers
   an unknown path with `index.html`, so all eight "pages" measured were the
   landing page, forty times, and the run came back clean while nothing it
   claims to cover had been loaded. A harness that silently measures the wrong
   document is worse than no harness. `assertOnPage` below is the guard. */
const PAGES = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)]
  .map((m) => new URL(m[1]).pathname);

const browser = await chromium.launch({ executablePath: CHROME });
const report = { overflow: [], touch: [], nav: [] };

for (const url of PAGES) {
  for (const width of WIDTHS) {
    const ctx = await browser.newContext({
      viewport: { width, height: 844 }, isMobile: true, hasTouch: true,
    });
    const page = await ctx.newPage();
    await page.goto(BASE + url, { waitUntil: "networkidle" });
    /* The document that came back has to be the one asked for. The prerender
       stamps a canonical URL on each page, so this compares the path the server
       actually served against the path requested — not the copy, which will be
       rewritten, and not the `<h1>`, which the first version of this guard got
       wrong. Without it the SPA fallback above turns a wrong path into a clean
       result, which it did for the whole first run of this script: eight
       "pages" that were all the landing page, forty times, reported clean. */
    const served = await page.evaluate(() => {
      const el = document.querySelector('link[rel="canonical"]');
      return el ? new URL(el.getAttribute("href")).pathname : null;
    });
    if (served !== url) {
      console.error(`FATAL ${url}: the server returned ${served ?? "a page with no canonical"}`);
      process.exit(1);
    }
    const found = await page.evaluate(({ vw, target }) => {
      const out = { scrollWidth: document.documentElement.scrollWidth, past: [], small: [], nav: 0 };
      const bar = document.querySelector(".lp-nav");
      out.nav = bar ? Math.round(bar.getBoundingClientRect().height) : 0;
      const name = (el) => {
        const cls = typeof el.className === "string" ? el.className : "";
        return `<${el.tagName.toLowerCase()}${cls ? ` class="${cls}"` : ""}>`;
      };
      for (const el of document.querySelectorAll("*")) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        if ((r.right > vw + 1 || r.left < -1) && getComputedStyle(el).position !== "fixed") {
          out.past.push({ name: name(el), left: Math.round(r.left), right: Math.round(r.right),
            width: Math.round(r.width), text: (el.textContent ?? "").trim().slice(0, 40) });
        }
        /* Height against the full target, width only against WCAG 2.5.8's
           24px floor. A three-character wordmark is 30px wide and correct at
           that width; padding it to 44 would push the bar apart for nothing,
           and a list that always holds the same two rows is a list people stop
           reading. `display: inline` is 2.5.8's own exception for a link
           inside a sentence — the landing footer's "Already have an account?
           Sign in" is one, and is right as it is. */
        if (["A", "BUTTON", "SUMMARY", "INPUT", "SELECT"].includes(el.tagName)
            && getComputedStyle(el).display !== "inline"
            && (r.height < target || r.width < 24)) {
          out.small.push(`${Math.round(r.width)}x${Math.round(r.height)} ${name(el)} `
            + `${(el.textContent ?? "").trim().slice(0, 28)}`);
        }
      }
      /* An ancestor is past the edge because its child is. Keep the innermost
         one — it is the element with the property that has to change. */
      out.past = out.past.filter((x, i, all) => !all.some((y, j) =>
        j !== i && y.right >= x.right - 1 && y.left <= x.left + 1 && y.width < x.width));
      return out;
    }, { vw: width, target: TARGET });

    if (found.scrollWidth > width + 1) {
      report.overflow.push(`${url} @${width}  scrollWidth ${found.scrollWidth}`
        + found.past.map((p) => `\n      ${p.name} ${p.left}→${p.right} (${p.width}px)  ${p.text}`).join(""));
    }
    for (const s of new Set(found.small)) report.touch.push(`${url} @${width}  ${s}`);
    if (found.nav > NAV_BUDGET) report.nav.push(`${url} @${width}  ${found.nav}px sticky`);
    await ctx.close();
  }
}
await browser.close();
server.close();

const show = (title, rows) => {
  console.log(`\n── ${title} ──`);
  console.log(rows.length ? [...new Set(rows)].join("\n") : "  none");
};
console.log(`${PAGES.length} pages x ${WIDTHS.length} widths, from dist/`);
show("scrolls sideways", report.overflow);
show(`touch targets under ${TARGET}px`, report.touch);
show(`sticky nav over ${NAV_BUDGET}px`, report.nav);
process.exitCode = report.overflow.length ? 1 : 0;
