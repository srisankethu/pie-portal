/* The Vercel Web Interface Guidelines checks that can only be answered by the
 * running page: computed contrast, real focus indicators, real touch-target
 * sizes, and whether the page scrolls sideways. The rest of that rule set is a
 * source-level read and was done by inspection.
 *
 * Run it the way `capture.mjs` is run — both servers up, from `frontend/`:
 *   node e2e/.shots/a11y.mjs
 *
 * **Read its output as a shortlist, not a verdict.** It measures `color`
 * against the nearest opaque `background-color`, and three times in one pass
 * that was not the whole truth: a chip on `rgba(0,0,0,0.08)` read as
 * near-black-on-black until this script learned to composite alpha; a label at
 * 2.26:1 turned out to belong to a genuinely disabled input, which WCAG 1.4.3
 * exempts; and the numerals on the journey chart's bars carry a `text-shadow`
 * halo, put there for exactly this reason, that no `getComputedStyle` reading
 * can account for. Check what it reports before changing anything.
 */
import { chromium } from "@playwright/test";

const BASE = "http://127.0.0.1:5173";
const CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const PASSWORD = "change-me-now";

/* A colour's luminance. `alpha` matters: a semi-transparent background has to
 * be composited over what is behind it before it can be compared, and reading
 * `rgba(0, 0, 0, 0.08)` as opaque black is how this script first reported a
 * perfectly legible chip at 1.27:1. */
const parse = (c) => {
  const m = (c || "").match(/[\d.]+/g);
  if (!m) return null;
  const [r, g, b] = m.slice(0, 3).map(Number);
  const a = m.length > 3 ? Number(m[3]) : 1;
  return { r, g, b, a };
};
const over = (fg, bg) => {
  if (!fg || !bg) return fg || bg;
  return {
    r: fg.a * fg.r + (1 - fg.a) * bg.r,
    g: fg.a * fg.g + (1 - fg.a) * bg.g,
    b: fg.a * fg.b + (1 - fg.a) * bg.b,
    a: 1,
  };
};
const lumOf = ({ r, g, b }) => {
  const f = (x) => { const v = x / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
const cr = (fg, bg, page = "rgb(242,242,243)") => {
  const base = parse(page);
  const b = over(parse(bg), base);
  const f = over(parse(fg), b);
  if (!f || !b) return 21;
  const la = lumOf(f), lb = lumOf(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
};

async function signIn(page, email) {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1400);
  if (!(await page.locator('input[name="email"]').count())) {
    await page.getByRole("button", { name: /^sign in$/i })
      .or(page.getByRole("link", { name: /^sign in$/i })).first().click();
    await page.locator('input[name="email"]').waitFor({ timeout: 15000 });
  }
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).last().click();
  await page.locator('input[name="password"]').waitFor({ state: "detached", timeout: 30000 });
}

const SCREENS = ["#/", "#/quotes", "#/customers", "#/approvals", "#/unanswered-quotes", "#/observability"];

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await signIn(page, "m.rao@pie.example");

const report = { contrast: [], focus: [], touch: [], overflow: [], labels: [] };

for (const hash of SCREENS) {
  await page.goto(`${BASE}/${hash}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2600);

  // ── contrast on text-bearing controls and body copy
  const texts = await page.evaluate(() => {
    const out = [];
    const sel = "button, a, .MuiChip-root, p, h1, h2, h3, h4, h5, h6, span, td, th, dd, dt";
    for (const el of document.querySelectorAll(sel)) {
      const t = (el.textContent || "").trim();
      if (!t || t.length > 60 || el.children.length > 2) continue;
      const s = getComputedStyle(el);
      if (s.visibility === "hidden" || s.display === "none") continue;
      let bg = s.backgroundColor, n = el;
      while ((bg === "rgba(0, 0, 0, 0)" || bg === "transparent") && n.parentElement) {
        n = n.parentElement; bg = getComputedStyle(n).backgroundColor;
      }
      out.push({ t: t.slice(0, 40), fg: s.color, bg, size: parseFloat(s.fontSize), w: s.fontWeight });
    }
    return out;
  });
  for (const r of texts) {
    const ratio = cr(r.fg, r.bg);
    const large = r.size >= 24 || (r.size >= 18.66 && Number(r.w) >= 700);
    const need = large ? 3 : 4.5;
    if (ratio < need) report.contrast.push(`${hash}  ${ratio.toFixed(2)}:1 (need ${need})  "${r.t}" ${r.size}px/${r.w}`);
  }

  // ── icon-only buttons without an accessible name
  const unlabelled = await page.evaluate(() => {
    const out = [];
    for (const b of document.querySelectorAll("button, [role=button]")) {
      const text = (b.textContent || "").trim();
      const name = b.getAttribute("aria-label") || b.getAttribute("title");
      if (!text && !name) out.push(b.className.toString().slice(0, 50) || b.tagName);
    }
    return out;
  });
  unlabelled.forEach((c) => report.labels.push(`${hash}  ${c}`));

  // ── touch targets
  const small = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll("button, a, [role=button], input[type=checkbox]")) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.width < 44 || r.height < 44) {
        out.push(`${Math.round(r.width)}x${Math.round(r.height)} ${(el.textContent || "").trim().slice(0, 24) || el.className.toString().slice(0, 30)}`);
      }
    }
    return out;
  });
  small.slice(0, 6).forEach((s) => report.touch.push(`${hash}  ${s}`));

  // ── focus ring, walking real tab stops
  let rings = 0, stops = 0;
  await page.keyboard.press("Tab"); await page.waitForTimeout(180);
  for (let i = 0; i < 18; i++) {
    const info = await page.evaluate(() => {
      const a = document.activeElement;
      if (!a || a === document.body) return null;
      const s = getComputedStyle(a);
      return { o: s.outlineWidth, st: s.outlineStyle, sh: s.boxShadow };
    });
    if (info) {
      stops++;
      if ((info.o !== "0px" && info.st !== "none") || (info.sh && info.sh !== "none")) rings++;
    }
    await page.keyboard.press("Tab"); await page.waitForTimeout(70);
  }
  report.focus.push(`${hash}  ${rings}/${stops} tab stops indicated`);

  // ── horizontal overflow at phone width
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(1100);
  const over = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  if (over) report.overflow.push(`${hash} scrolls sideways at 390px`);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(500);
}

console.log("\n== CONTRAST failures ==");
console.log(report.contrast.length ? [...new Set(report.contrast)].slice(0, 12).join("\n") : "  none");
console.log("\n== ICON BUTTONS with no accessible name ==");
console.log(report.labels.length ? [...new Set(report.labels)].slice(0, 10).join("\n") : "  none");
console.log("\n== TOUCH TARGETS under 44px ==");
console.log(report.touch.length ? [...new Set(report.touch)].slice(0, 12).join("\n") : "  none");
console.log("\n== FOCUS INDICATORS ==");
console.log(report.focus.join("\n"));
console.log("\n== HORIZONTAL OVERFLOW at 390px ==");
console.log(report.overflow.length ? report.overflow.join("\n") : "  none");

await browser.close();
