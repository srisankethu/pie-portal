# SEO / GEO Audit — pie-portal

**Date:** 2026-08-17 · **Deployed at:** https://pie-portal-seven.vercel.app/ ·
**Status:** audited, fixed (Option A — build-time prerender), verified locally.

**Method note.** The deployed URL is unreachable from the sandbox this work ran in
(network egress proxy), so all evidence is the repository source plus local
production builds. Before the fix, the built `dist/index.html` (729 bytes, empty
`#root`) matched the reported deployed response exactly. Production
re-verification after merge stays with the repository owner.

---

## 1. What the audit found (state before the fix)

**Stack.** React 18 + TypeScript, Vite 5, no meta-framework. `react-router-dom` 7
in library mode with **`HashRouter`** (`frontend/src/main.tsx:73`) — every screen
behind `/#/…`, so the server sees exactly one URL. Vercel serves static `dist/`
with a catch-all rewrite to `/index.html` (`frontend/vercel.json`); `/api/*` goes
through an edge function to the Railway backend. The Caddy self-host serves the
same `dist/`.

**Root cause of the empty server HTML.** 100 % client-side rendering: the only
document the server could return was `dist/index.html` — head tags plus an empty
`<div id="root">`. The public landing page existed as a well-built React
component (`frontend/src/landing/Landing.tsx`) but only ever rendered client-side
behind `if (!session)` (`PlatformApp.tsx:541`), after 785 kB (251 kB gzip) of
JavaScript executed. Hash routing collapsed every route onto `/`; `/robots.txt`
and `/sitemap.xml` did not exist as files, so the rewrite answered them with the
same empty shell, HTTP 200.

**Classification at audit time:** crawlable initial HTML ❌ · server-rendered
content ❌ · title ⚠️ (static, wording predated positioning) · meta description ❌
· headings ✅-in-component/absent-pre-JS · canonical ❌ · robots ❌ · sitemap ❌ ·
Open Graph ❌ · Twitter ❌ · structured data ❌ · internal linking ⚠️ (one public
URL by design) · image alt ➖ (no images exist) · a11y-for-crawling ✅ ·
public/private separation ✅ · mobile ✅ · crawl performance ⚠️ (content required
251 kB gzip JS) · GEO/AI readability ❌ · product entity clarity ✅-rendered/
❌-crawlable · content quality ✅.

---

## 2. Already correct — found working, left untouched

- **The landing content and markup** (`frontend/src/landing/Landing.tsx`,
  `landing.css`): exactly one `h1`, ordered `h2`/`h3` hierarchy, navigation in a
  real `<nav>` with anchor links, `<header>`/`<section>`/`<footer>` landmarks,
  decorative drawing-frame elements `aria-hidden`, CTAs as anchors rather than
  buttons, and copy that keeps a documented honesty rule (audited against the
  backend in Aug 2026, `Landing.tsx:17-49`). Not a line of it was changed.
- **Private-route protection**: gated screens live behind hash fragments (never
  sent to the server) and every data read requires a bearer token enforced
  server-side. No change was needed and none was made.
- **Mobile rendering**: viewport meta and responsive breakpoints already present.
- **Bundle discipline**: analysis screens and ag-grid lazy-loaded away from the
  entry; fonts self-hosted. Unchanged.
- **Deployment plumbing**: the SPA rewrite, the `/api/*` edge proxy, and the
  Caddy self-host path all work as designed and were not modified (one comment
  in `deploy/web.Dockerfile` updated to stay truthful, plus the `SITE_ORIGIN`
  build-arg pass-through).

## 3. Fixed — what actually changed

| Change | Files |
|---|---|
| **The public landing is now baked into the served HTML.** After `vite build`, `scripts/prerender.mjs` renders the *same* `Landing` component through Vite's SSR pipeline into `#root` of `dist/index.html` — no second copy of the content exists anywhere. `main.tsx`'s `createRoot().render()` replaces the static markup when the bundle arrives (identical landing for signed-out visitors, the shell for signed-in ones). | `frontend/scripts/prerender.mjs`, `frontend/src/landing/prerender.tsx`, `frontend/package.json` (build script) |
| **Pre-JS paint uses the real theme.** The design tokens are otherwise emitted by `CssBaseline` at runtime; the prerender injects the same exported `CSS_VARS` map from `theme.ts` as a static `:root` rule — one source, no duplicated values. | `frontend/scripts/prerender.mjs`, `frontend/src/landing/prerender.tsx` |
| **Title retargeted to the real positioning**: `PIE · Commercial Decisions` → `PIE · Commercial intelligence for distributors`, matching the landing's own eyebrow and footer line (`Landing.tsx:103,463`). | `frontend/index.html` |
| **Meta description added** — states only checkable product behaviour (quote-line policy checks, approval routing, decline signals, ERP ingestion). | `frontend/index.html` |
| **Open Graph + Twitter card added**: `og:type/site_name/title/description` static; `og:url` injected at build from `SITE_ORIGIN`; `twitter:card=summary` (X falls back to the og: tags — no duplicated strings). | `frontend/index.html`, `frontend/scripts/prerender.mjs` |
| **Canonical added**, injected at build from `SITE_ORIGIN` (default `https://pie-portal-seven.vercel.app`, overridable by env var / Docker build-arg — one setting for a future custom domain). | `frontend/scripts/prerender.mjs`, `deploy/web.Dockerfile` |
| **Structured data added**: `WebSite` + `WebPage` + `SoftwareApplication` JSON-LD; name/description read back out of the built head so the strings exist once. No offers, ratings, reviews, or FAQ — excluded on principle and because the values don't exist. | `frontend/scripts/prerender.mjs` |
| **robots.txt and sitemap.xml now exist**, generated into `dist/` at build (they carry the absolute origin). Robots allows everything except `/api/`; the sitemap lists the one canonical public URL. Static files are served by Vercel before rewrites apply, so both now answer instead of the SPA shell. | `frontend/scripts/prerender.mjs` |
| **Regression pin**: `prerender.test.tsx` asserts in the 3-second vitest loop that `Landing` stays server-renderable (one `h1`, nav, entity statement, anchor CTAs) and that the token CSS is emitted. | `frontend/src/landing/prerender.test.tsx` |

The prerendered page answers the product-entity questions in plain crawlable
HTML because the landing already did: what PIE is (the commercial intelligence
layer for distributors), who it is for (B2B distributors on Zoho Books /
NetSuite / Dynamics 365 BC / Acumatica / Prophet 21 / Sage), what problems it
solves (below-floor quoting, quiet customer decline), what decisions it supports
(price/approve/reprice, who needs attention), and what it produces (checked
quotes, routed approvals, a daily attention list, an append-only decision
record).

## 4. Not applicable — with reasons

- **Image alt text / formats**: there are no `<img>` elements anywhere in the
  frontend; the hero mock is a styled `div` with `role="img"` and a full
  `aria-label`. Nothing to fix.
- **BreadcrumbList**: was *not applicable* — one public page, no trail to
  describe. Since the ERP pages landed there is a real two-level trail
  (`/` → `/erp/{system}`), so the schema is now *earnable* rather than
  inapplicable: it may be emitted the day a page renders a visible breadcrumb,
  and not before. Schema may only restate what is on the page, which is the
  rule that excluded it in the first place. No page renders one today.
- **FAQPage**: no visible FAQ content exists on the page, so none is claimed.
- **Organization schema**: the repository states no public legal-entity facts
  (vendor name, logo, address) that could populate it truthfully;
  `WebSite`/`SoftwareApplication` carry the entity instead.
- **Per-route titles/descriptions**: was *not applicable* — one
  server-addressable route existed (hash routing), so there was nothing for
  per-route metadata to attach to. **No longer true as of the Aug 2026
  repositioning**: `/erp/prophet-21`, `/erp/netsuite` and `/erp/acumatica` are
  real documents, each baked by the same prerender with its own `<title>`,
  description, `og:title`/`og:description`, canonical, per-page `WebPage`
  JSON-LD node and sitemap entry. They ship without the module script, so they
  carry no bundle at all. `frontend/src/landing/prerender.tsx` holds the page
  registry; `vercel.json` and `deploy/Caddyfile` resolve the extensionless
  addresses on both hosts.
- **`noindex` on private routes**: private screens have no server URLs to
  noindex — they are hash fragments behind the session check, and the API is
  token-gated. Robots' `/api/` disallow is hygiene, not the control.
- **hreflang**: single-language site.
- **og:image / social image**: deliberately *not* added rather than fabricating
  an asset; listed under remaining issues as a design task.

## 5. Remaining issues — genuine ones only

1. **No social preview image.** Link shares render text-only cards. Needs a real
   designed asset; nothing was invented to fill the slot.
2. **One public URL by design, for the application.** Hash routing means the
   landing's *sections* still cannot be individually indexed or deep-linked as
   URLs, and changing the routing model has product-wide consequences far
   beyond SEO. Partially addressed since: the three ERP pages are separate
   server-addressable documents (see §4), which required no change to the
   application's routing — they are static pages that never load it.
3. **Signed-in flash.** A signed-in user briefly sees the prerendered landing
   before React swaps in the shell (previously: a blank white page). Cosmetic.
4. **Bundle weight unchanged.** The prerender fixes what crawlers and first
   paint see; interactive use still needs the 251 kB gzip entry bundle.
5. **Production unverified from here.** The sandbox cannot reach the deployed
   URL. After merge, confirm in production: `curl -s https://pie-portal-seven.vercel.app/`
   contains the landing markup; `/robots.txt` and `/sitemap.xml` return the
   generated files (Vercel serves real files before rewrites, so they should).

## 6. Validation

All run locally on this branch, 2026-08-17:

- **Full gate** (`make verify` → `scripts/verify.sh`): **VERIFIED** — ruff clean,
  §1 layer invariants clean, backend **2430 passed / 52 skipped** (2 min 56 s),
  frontend vitest ok, `tsc -b` + production build (including the prerender) ok,
  migrations from an empty database to a single head with no drift on **SQLite
  and PostgreSQL**. One narrowing, pre-existing and environmental: the
  engine-backed (`requires_pie`) tests skip where pie-parser is not checked out;
  CI covers them.
- **Frontend tests**: 224 passed (221 baseline + 3 new prerender pins).
- **Browser check** (Chromium against `vite preview`): exactly one `h1` in the
  live DOM; clicking "Sign in" opens the sign-in card — proof React mounted over
  the prerendered markup, since static HTML has no handlers. The only console
  error is `GET /api/v1/signup` → 500, the signup-offer probe hitting a preview
  server that has no backend; it is caught by `useSignupOffer` and does not occur
  where the API is deployed.

**Raw HTML, no JavaScript executed** — `curl -s http://localhost:4173/ > /tmp/raw.html`:

```
16324 /tmp/raw.html        (was 729 bytes before the fix)
```

Head (abridged to the tags the audit found missing):

```html
<title>PIE · Commercial intelligence for distributors</title>
<meta name="description" content="The commercial intelligence layer for B2B distributors: PIE checks every quote line against your own margin policy, routes breaches for approval, and spots customers quietly buying less — reading the ERP books you already keep." />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="PIE" />
<meta property="og:title" content="PIE · Commercial intelligence for distributors" />
<meta property="og:description" content="The commercial intelligence layer …" />
<meta name="twitter:card" content="summary" />
<style id="pie-tokens">:root{--color-bg:#f2f2f3;…}</style>
<link rel="canonical" href="https://pie-portal-seven.vercel.app/" />
<meta property="og:url" content="https://pie-portal-seven.vercel.app/" />
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
  {"@type":"WebSite","url":"https://pie-portal-seven.vercel.app/","name":"PIE"},
  {"@type":"WebPage","name":"PIE · Commercial intelligence for distributors","description":"…"},
  {"@type":"SoftwareApplication","name":"PIE","applicationCategory":"BusinessApplication","operatingSystem":"Web browser","description":"…"}]}</script>
```

Body (first lines; one `<h1>`, real `<nav>` links):

```html
<div id="root"><div class="pie-landing"><div class="lp-sheet">…
<nav class="lp-nav">…<a class="lp-logo" href="#top">PIE<span>.</span></a>
<a href="#product">Product</a><a href="#how">How it works</a>
<a href="#trust">Trust</a><a href="#pricing">Pricing</a>
<a class="lp-btn solid" href="#signin">Sign in</a>…</nav>
<header class="lp-hero" id="top">…
<p class="lp-eyebrow">The commercial intelligence layer for distributors</p>
<h1>Stop quoting away your <em>margin.</em></h1>…
```

All seven section `<h2>`s present in the raw HTML: *Margin doesn't vanish in one
bad deal. It leaks.* · *A quote desk with a commercial brain behind it* · *Four
steps, and PIE tracks which are done.* · *The AI never computes a single
number.* · *What you own — and we can prove it* · *Free to quote. Cheap to know.
Priced per organization.* · *Your books already know where the margin went.*

`/robots.txt` (200, `text/plain`):

```
User-agent: *
Disallow: /api/

Sitemap: https://pie-portal-seven.vercel.app/sitemap.xml
```

`/sitemap.xml` (200, `text/xml`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://pie-portal-seven.vercel.app/</loc>
    <lastmod>2026-08-17</lastmod>
  </url>
</urlset>
```

**Public vs private under the new rendering:** the prerender renders exactly one
component — `Landing`, which holds no session, no token, and performs no data
access (it is a pure function of two callback props). No authenticated screen,
API response, or user datum can reach the static HTML. Nothing else is
server-rendered.
