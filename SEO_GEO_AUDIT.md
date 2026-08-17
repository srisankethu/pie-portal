# SEO / GEO Audit — pie-portal

**Date:** 2026-08-17 · **Deployed at:** https://pie-portal-seven.vercel.app/ · **Phase:** 1 (audit only — no application file was modified)

**Method note.** The deployed URL is unreachable from this sandbox (network egress
proxy blocks `*.vercel.app`), so the evidence below is the repository source plus a
local production build (`npm ci && npm run build` in `frontend/`, successful). The
built `dist/index.html` is byte-for-byte the document Vercel serves for `/` — it
matches the reported deployed response exactly: a `<title>`, a `theme-color` meta,
a viewport meta, and an empty body. Production re-verification stays with the
repository owner, as agreed.

---

## 1. Stack and rendering — the facts

| Aspect | Finding | Evidence |
|---|---|---|
| Framework | React 18 + TypeScript, built by Vite 5. No meta-framework (no Next/Remix/Astro). | `frontend/package.json` — deps `react@^18.3.1`, `vite@^5.4.2`; build script is `tsc -b && node scripts/check-forms.mjs && vite build` |
| Routing | `react-router-dom@^7.18.2` in **library mode**, mounted as **`HashRouter`** — every screen lives behind `/#/…`. Deliberate and documented: one origin serves API + bundle in self-host, and old hash links keep working. | `frontend/src/main.tsx:73`, `frontend/src/platform/route.ts:17-21` |
| Rendering strategy | **100 % client-side rendering, every route.** No SSR, no SSG, no prerender step exists anywhere in the repo. | `frontend/package.json` scripts; no SSR/prerender tooling in deps; `dist/` contains only `assets/`, `favicon.svg`, `index.html` |
| Entry HTML | `frontend/index.html`: head = charset, viewport, theme-color, favicon link, `<title>PIE · Commercial Decisions</title>`; body = `<div id="root"></div>` + module script. Built `dist/index.html` = **729 bytes**. | `frontend/index.html`; local build output |
| Vercel config | Static hosting of `dist/` with SPA catch-all rewrite `/(.*) → /index.html`. `/api/*` is handled before the rewrite by an edge function proxying to the Railway backend (`BACKEND_URL` env var). | `frontend/vercel.json`, `frontend/api/[...path].ts` |
| Self-host path | Caddy serves the **same** `dist/` with `try_files {path} /index.html`; API reverse-proxied under the same origin. Any fix baked into `dist/` at build time reaches both deployments; a Vercel-runtime-only fix diverges them. | `deploy/Caddyfile:56`, `deploy/web.Dockerfile` |
| Homepage component | Signed-out visitors get `Landing` (`frontend/src/landing/Landing.tsx`) — purely presentational, **zero data dependencies**. The only nearby fetch (`useSignupOffer` → `GET /api/v1/signup`) decides which form the CTA opens; it does not gate rendering. | `frontend/src/platform/PlatformApp.tsx:541-557`, `PlatformApp.tsx:218-229`, `Landing.tsx:2-8` |
| Auth boundary | Everything except Landing + sign-in/sign-up cards requires a `PlatformSession` (client state; the API enforces a bearer token server-side). All gated screens are hash routes on the **same server URL `/`** — no private screen has its own server-addressable URL. | `PlatformApp.tsx:541` (`if (!session)`), `frontend/src/platform/api.ts` (Authorization header on every call) |

## 2. Root cause of the empty server HTML

Three stacked facts, all by construction:

1. **The app is a client-rendered Vite SPA.** The only document the server can
   ever return is `dist/index.html`, whose body is an empty `<div id="root">`.
   All content — including the public landing page — is created by
   `assets/index-*.js` (785.85 kB, 251.13 kB gzip) after download + parse +
   execute. A crawler that does not run JavaScript sees exactly the head tags
   and nothing else.
2. **The public content exists but only client-side.** `Landing.tsx` (~470
   lines) carries real, honesty-audited copy — one `h1`, five named sections,
   a `<nav>`, pricing, a footer — and is mounted only inside React, behind
   `if (!session)` in `PlatformApp.tsx:541`.
3. **Hash routing collapses every route onto `/`.** Fragments are never sent to
   the server, so there is exactly one public URL. The catch-all rewrite answers
   *any* path (including `/robots.txt` and `/sitemap.xml`, which do not exist as
   files) with the same empty shell, HTTP 200.

Nothing is misconfigured relative to the code's own intent — the emptiness is the
designed output of a CSR-only build. The defect is that the public surface (the
landing) has no server-visible representation.

## 3. Classification — the 20 items

| # | Item | State | Evidence |
|---|---|---|---|
| 1 | Crawlability of the initial HTML response | ❌ broken | `dist/index.html` (729 B): head + empty `#root`, no content nodes. Matches reported deployed fetch. |
| 2 | Server-rendered indexable content | ❌ missing | No SSR/SSG/prerender anywhere; build = `tsc -b && check-forms && vite build` (`frontend/package.json:8`) |
| 3 | Titles | ⚠️ partial | One static `<title>PIE · Commercial Decisions</title>` (`frontend/index.html:11`) served for every path; nothing sets `document.title` per screen (repo-wide grep: no matches). Present and truthful, but it is the only head signal that exists, and its wording predates the landing's positioning ("the commercial intelligence layer for distributors", `Landing.tsx:103`). |
| 4 | Meta descriptions | ❌ missing | No `<meta name="description">` anywhere (repo-wide grep) |
| 5 | Heading hierarchy (H1/H2) | ⚠️ partial | Correct in the rendered landing — exactly one `h1` (`Landing.tsx:104`), `h2` per section, `h3` for cards. But it exists only after JS executes; the served HTML contains no headings at all (item 1). |
| 6 | Canonicals | ❌ missing | No `rel=canonical` in `index.html` or anywhere else; every path 200s with the same document, so duplicates self-canonicalise nowhere. |
| 7 | Robots | ❌ missing | `frontend/public/` contains only `favicon.svg`; no `robots.txt` in repo (grep). Deployed effect: the rewrite serves the SPA shell for `/robots.txt` as `text/html` 200 — no valid directives. |
| 8 | Sitemap | ❌ missing | Same evidence as 7; no sitemap generation anywhere. |
| 9 | Open Graph | ❌ missing | No `og:*` tags (repo-wide grep). A shared link renders with no preview title/description/image. |
| 10 | Twitter/X metadata | ❌ missing | No `twitter:*` tags (repo-wide grep). |
| 11 | Structured data | ❌ missing | No `ld+json` anywhere (repo-wide grep). |
| 12 | Internal linking | ⚠️ partial | The landing nav is real `<a href>` links (good), but all targets are same-page fragments (`#product`, `#how`, `#trust`, `#pricing`, `#signin` — `Landing.tsx:91-95`); fragments do not create crawlable URLs. There is exactly one public URL in the whole product, so no crawlable internal link graph exists — mostly a consequence of hash routing, not a defect in the markup. |
| 13 | Image alt text and formats | ➖ not applicable | Zero `<img>` elements in the entire frontend (grep over `src/**/*.tsx`). The hero "decision card" is a styled `div` with `role="img"` and a thorough `aria-label` (`Landing.tsx:120-123`); the favicon is an SVG with its own `aria-label`. Nothing to fix, nothing to add. |
| 14 | Accessibility items that affect crawling | ✅ correct (in component) | `<html lang="en">` (`index.html:2`); landing uses semantic `<nav>`, `<header>`, `<section>`, `<footer>`; decorative drawing-frame elements are `aria-hidden` (`Landing.tsx:83`, `125-128`); CTAs are anchors, not buttons. None of it reaches a non-JS crawler (item 1), but the markup itself needs no repair. |
| 15 | Public vs private route indexability | ✅ correct (structurally) | Private screens exist only behind hash fragments — never sent to the server — and every data read requires a bearer token enforced server-side (`platform/api.ts`). There is no server rendering that could leak gated content. Caveat noted for Phase 3: any *new* server-rendered surface must render the public landing only. |
| 16 | Mobile rendering | ✅ correct | Viewport meta (`index.html:5`); landing has responsive breakpoints (`landing.css:66,109,180,240,292`); app screens are MUI-responsive. Moot for crawlers until item 1 is fixed — mobile-first indexing sees the same empty document. |
| 17 | Performance factors affecting crawl | ⚠️ partial | Before any content can exist, a client must fetch and execute 785.85 kB JS (251.13 kB gzip) + 101 kB CSS (build output). Done well within that constraint: ag-grid's 1.16 MB chunk and all twenty analysis screens are lazy-loaded away from the entry (`PlatformApp.tsx:47-67`), fonts are bundled not CDN-fetched, and the landing itself makes no blocking API call. The remaining cost is structural to CSR: content-bearing HTML is 0 bytes until JS runs. |
| 18 | GEO / AI readability of the public HTML | ❌ broken | Non-JS AI crawlers (the common case) receive a titled empty document — no statement of what PIE is exists in any fetchable form. |
| 19 | Product entity clarity | ⚠️ partial | In the rendered landing: clear and specific — what PIE is (`Landing.tsx:103-108`), who it is for (distributors on named ERPs, `:174-184`), what it does (quote checking, margin floors + approvals, attention list, `:234-290`), what it costs (`:407-449`). The copy was audited against the backend in Aug 2026 and de-drifted (`Landing.tsx:17-49` documents six removed overclaims). In the crawlable HTML: absent entirely. |
| 20 | Content quality on public pages | ✅ correct (content) | The landing keeps an explicit honesty rule — every claim checkable against the code, no invented customers, no testimonials, no fabricated stats (`Landing.tsx:17-21`). The defect is delivery (items 1–2), not content. |

## 4. Already implemented correctly — to be left alone

- **The landing content itself** (`frontend/src/landing/Landing.tsx` + `landing.css`) — semantic structure, heading order, honest copy, accessible decoration. It is the single source of truth for the public surface and must not be duplicated by a second hand-written marketing page.
- **Semantic markup and a11y** on the landing (item 14) and the **absence of images needing alt text** (item 13).
- **Private-route protection** — token-gated API + fragment-only client routes; nothing gated is server-addressable today (item 15).
- **Mobile/responsive behaviour** (item 16) and **bundle discipline** (lazy screens, bundled fonts — item 17's mitigations).
- **The deployment plumbing**: `vercel.json` SPA rewrite, `/api/*` edge proxy, Caddy self-host serving the same `dist/`.
- **The static `<title>`** exists and is truthful (wording revisited in Phase 3 only with justification).

## 5. Constraints any fix must respect (from the repo's own rules)

- `CLAUDE.md` §1: no fabricated output; the landing's own comment block demands every public claim be checkable against the code. This aligns with the task's no-spam rules.
- Hash routing is a **documented decision** (`route.ts:17-21`) with user-facing consequences (saved links, one-origin self-host). A fix that forces `BrowserRouter` has a blast radius far beyond SEO.
- One `dist/` serves Vercel **and** the Caddy self-host: a build-time fix helps both; a Vercel-runtime-only fix (edge SSR) leaves self-host deployments empty.
- No test currently covers the Landing (no vitest/e2e references) — low regression surface for prerendering it, but also no safety net; Phase 4 verification must be explicit.

## 6. Open question for the owner

- Canonical host: is `pie-portal-seven.vercel.app` the intended public home, or is a
  custom domain planned? Canonical URL, `og:url`, sitemap `<loc>` and JSON-LD all
  need one absolute origin; it should be configurable either way.

*Phases 2–5 (options, decision, implementation, verification) follow separately; this file records the audited state before any change.*
