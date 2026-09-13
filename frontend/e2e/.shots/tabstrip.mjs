/* Does the destination tab strip fit, and is every way out of it reachable?
 *
 * Written for one reported defect — "More and other items only when scrolling"
 * on Setup — and kept because it is the check `ui-standards.md` §12 names and
 * the one a read of the source cannot perform: **drive the app and look for a
 * page that scrolls sideways.**
 *
 * The source looked correct. `Tabs` had `flex: 1`, `More` had `flex: "none"`,
 * and a reader concludes they share the row. They do not: a flex item's default
 * `min-width: auto` refuses to shrink below its content, and a scrollable
 * `Tabs` reports every tab's full width as content — so the strip claimed the
 * row, pushed `More` past the right edge, and the page scrolled sideways to
 * reach it. Nothing in the file says that, which is why this asserts pixels.
 *
 * Not a spec, for the reason `capture.mjs` beside it is not one: the runner
 * does not survive a job this long in a constrained container, and a script
 * takes its screens on the command line.
 *
 * Both servers must already be up:
 *   bash e2e/serve-backend.sh &
 *   npm run dev -- --port 5173 --strictPort --host 127.0.0.1 &
 *
 * Then, from `frontend/`:
 *   node e2e/.shots/tabstrip.mjs
 *   node e2e/.shots/tabstrip.mjs --device phone
 */
import { chromium } from "@playwright/test";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : process.argv[i + 1];
};

const PASSWORD = process.env.SEED_PASSWORD ?? "change-me-now";
const BASE = process.env.SHOT_BASE_URL ?? "http://127.0.0.1:5173";
const CHROME = process.env.PLAYWRIGHT_CHROMIUM_PATH ||
  "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const DEVICES = { desktop: { width: 1440, height: 900 },
                  laptop: { width: 1280, height: 800 },
                  small: { width: 1024, height: 800 },
                  tablet: { width: 768, height: 1024 },
                  phone: { width: 390, height: 844 },
                  tiny: { width: 360, height: 780 } };

/** The destinations with an overflow, by the address of a screen in each.
 *
 *  Setup is the only one: `MONEY_TABS` and `ACCOUNT_TABS` declare no
 *  `secondary` entries, so neither renders a `More` button at all. Measured
 *  rather than assumed — the first version of this script listed Money and
 *  reported a missing button as a failure. */
const STRIPS = [
  { name: "Setup", path: "#/data" },
];

const device = arg("device", "desktop");
const size = DEVICES[device] ?? DEVICES.desktop;

const browser = await chromium.launch({ executablePath: CHROME });
const page = await browser.newPage({ viewport: size });

// The same sequence `capture.mjs` uses, and for the reason its comment gives:
// `/` is the marketing landing page, so the form has to be asked for before it
// can be filled.
await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30_000 });
await page.waitForTimeout(1500);
const emailField = page.locator('input[name="email"]');
if ((await emailField.count()) === 0) {
  await page.getByRole("button", { name: /^sign in$/i })
    .or(page.getByRole("link", { name: /^sign in$/i }))
    .first().click();
  await emailField.waitFor({ timeout: 20_000 });
}
await emailField.fill("m.rao@pie.example");
await page.fill('input[name="password"]', PASSWORD);
await page.getByRole("button", { name: /sign in/i }).last().click();
await page.locator('input[name="password"]')
  .waitFor({ state: "detached", timeout: 30_000 });

let failures = 0;
for (const strip of STRIPS) {
  await page.goto(`${BASE}/${strip.path}`);
  await page.waitForTimeout(1200);

  // Below `sm` the strip is a button and a menu rather than tabs, so the
  // question changes shape with it: not "is anything clipped" but "does one tap
  // reveal every destination". Opened here because the menu does not exist in
  // the DOM until it is.
  const isPhone = size.width < 600;
  if (isPhone) {
    // By its own `aria-label` and nothing else. A looser
    // `[aria-haspopup="menu"]` matched the app bar's account button first and
    // reported a menu holding "Sign out" as the tab list, which is the shape of
    // wrong answer a test gives confidently.
    await page.getByRole("button", { name: `${strip.name} sections` }).click();
    await page.waitForTimeout(400);
  }

  const report = await page.evaluate(() => {
    const doc = document.documentElement;
    const more = [...document.querySelectorAll("button")]
      .find((b) => b.textContent.trim().startsWith("More"));
    const box = more?.getBoundingClientRect();
    return {
      // The symptom, stated the way §12 states it.
      pageScrollsSideways: doc.scrollWidth > doc.clientWidth + 1,
      overflowBy: doc.scrollWidth - doc.clientWidth,
      moreFound: Boolean(more),
      // In frame without scrolling anything, which is the actual complaint.
      moreInViewport: box
        ? box.right <= window.innerWidth + 1 && box.left >= -1
        : false,
      moreRight: box ? Math.round(box.right) : null,
      viewport: window.innerWidth,
      // The other half of the complaint: items inside the strip that are only
      // reachable by scrolling *it*, and whether anything on screen says so.
      // The phone shape: every destination as a menu item, none of it clipped.
      menu: (() => {
        const items = [...document.querySelectorAll('[role="menuitem"]')];
        if (!items.length) return null;
        const clipped = items.filter((i) => {
          const r = i.getBoundingClientRect();
          return r.right > window.innerWidth + 1 || r.left < -1;
        }).map((i) => i.textContent.trim());
        return { count: items.length,
                 labels: items.map((i) => i.textContent.trim()),
                 clipped };
      })(),
      scroller: (() => {
        const el = document.querySelector(".MuiTabs-scroller");
        if (!el) return null;
        const sb = document.querySelectorAll(".MuiTabs-scrollButtons");
        const hidden = [...sb].filter(
          (b) => getComputedStyle(b).display === "none").length;
        const strip = el.getBoundingClientRect();
        const clipped = [...document.querySelectorAll('[role="tab"]')]
          .filter((t) => {
            const r = t.getBoundingClientRect();
            return r.right > strip.right + 1 || r.left < strip.left - 1;
          })
          .map((t) => t.textContent.trim());
        return {
          overflows: el.scrollWidth > el.clientWidth + 1,
          by: el.scrollWidth - el.clientWidth,
          scrollButtons: sb.length,
          scrollButtonsHidden: hidden,
          clipped,
        };
      })(),
    };
  });

  // On a phone `More` is gone by design — everything is in the one menu — so
  // what "ok" means changes with the shape.
  const ok = !report.pageScrollsSideways && (
    isPhone
      ? Boolean(report.menu) && report.menu.count >= 5
        && report.menu.clipped.length === 0
      : report.moreFound && report.moreInViewport);
  if (!ok) failures += 1;
  const sc = report.scroller;
  if (isPhone) {
    const m = report.menu;
    console.log(
      `${ok ? "ok  " : "FAIL"}  ${strip.name} @ ${device} ${size.width}px — ` +
      `sideways=${report.pageScrollsSideways} · menu items=${m ? m.count : 0} ` +
      `clipped=[${m ? m.clipped.join(", ") : "—"}] · [${m ? m.labels.join(" | ") : ""}]`);
    continue;
  }
  console.log(
    `${ok ? "ok  " : "FAIL"}  ${strip.name} @ ${device} ${size.width}px — ` +
    `sideways=${report.pageScrollsSideways} (${report.overflowBy}px) · ` +
    `More inViewport=${report.moreInViewport} right=${report.moreRight}/${report.viewport}`);
  if (sc) {
    console.log(
      `        strip overflows=${sc.overflows} by=${sc.by}px · ` +
      `scrollButtons=${sc.scrollButtons} hidden=${sc.scrollButtonsHidden} · ` +
      `clipped=[${sc.clipped.join(", ")}]`);
  }
}

await browser.close();
process.exit(failures ? 1 : 0);
