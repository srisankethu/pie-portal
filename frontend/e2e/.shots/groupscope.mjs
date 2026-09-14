/* Look at the page-level group bar in a real browser, at desk and phone width.
 *
 * **Counts the group selects on each page**, which is the regression this
 * exists for: the Payments screen once drew two, because two panels on it each
 * owned one, and a reader who set either was looking at one panel narrowed and
 * the other not. jsdom can assert that there is one control and that two panels
 * read it — `platform/groupScope.test.tsx` does — and it cannot see where the
 * control sits or whether it reads as part of the page.
 *
 * That second half is why this is a script and not a spec. The first version of
 * the bar was a bare select above the page's own heading with a rule under it;
 * the source read as correct and the screenshot read as a stray control left
 * over from the brand bar. It is a `Paper` with a lead-in line now.
 *
 * Both servers must already be up — the same two `playwright.config.ts` starts:
 *   bash e2e/serve-backend.sh &
 *   npm run dev -- --port 5173 --strictPort --host 127.0.0.1 &
 *
 * The seeded workspace has no groups, so create a couple first or the bar
 * correctly renders nothing. Then, from `frontend/`:
 *   node e2e/.shots/groupscope.mjs
 */
import { chromium } from "@playwright/test";
import * as fs from "node:fs";

const argOut = process.argv.indexOf("--out");
const OUT = argOut === -1 ? "/tmp/shots" : process.argv[argOut + 1];
const BASE = process.env.SHOT_BASE_URL ?? "http://127.0.0.1:5173";
const CHROME = process.env.PLAYWRIGHT_CHROMIUM_PATH ||
  "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const DEVICES = { desktop: { width: 1440, height: 1000 }, phone: { width: 390, height: 844 } };
const PAGES = ["/payments", "/composition", "/customers", "/supply",
               "/item-lines", "/cadence", "/landscape", "/opportunities",
               "/bonds", "/dependency", "/mix", "/stock", "/gmroi",
               "/quote-outcomes", "/payables"];

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: CHROME });

for (const [device, viewport] of Object.entries(DEVICES)) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForTimeout(1500);
  const email = page.locator('input[name="email"]');
  if ((await email.count()) === 0) {
    await page.getByRole("button", { name: /^sign in$/i })
      .or(page.getByRole("link", { name: /^sign in$/i })).first().click();
    await email.waitFor({ timeout: 20_000 });
  }
  await email.fill("m.rao@pie.example");
  await page.fill('input[name="password"]', "change-me-now");
  await page.getByRole("button", { name: /sign in/i }).last().click();
  await page.locator('input[name="password"]').waitFor({ state: "detached", timeout: 30_000 });
  await page.waitForTimeout(1500);

  for (const path of PAGES) {
    await page.goto(`${BASE}/#${path}`);
    await page.waitForTimeout(2200);
    const m = await page.evaluate(() => {
      const bar = document.querySelector('section[aria-label="Group scope"]');
      const combos = bar ? bar.querySelectorAll('[role="combobox"]').length : 0;
      const allCombos = document.querySelectorAll('[role="combobox"]').length;
      const head = document.querySelector("h1, h2, h3");
      return {
        bar: bar ? bar.getBoundingClientRect().toJSON() : null,
        selectsInBar: combos,
        selectsOnPage: allCombos,
        headTop: head ? Math.round(head.getBoundingClientRect().top) : null,
        headText: head ? head.textContent.slice(0, 40) : null,
        pageWidth: document.documentElement.scrollWidth,
        viewWidth: document.documentElement.clientWidth,
      };
    });
    console.log(device, path.padEnd(14),
      "bar:", m.bar ? `y=${Math.round(m.bar.top)} h=${Math.round(m.bar.height)} w=${Math.round(m.bar.width)}` : "none",
      "| selects in bar:", m.selectsInBar,
      "| head:", m.headText, `@${m.headTop}`,
      "| sideways scroll:", m.pageWidth > m.viewWidth ? `YES +${m.pageWidth - m.viewWidth}px` : "no");
    const name = path.replace(/\//g, "") || "home";
    await page.screenshot({ path: `${OUT}/${device}-${name}.png`, fullPage: false });
  }

  // And with a group actually selected, which is the state nobody looks at.
  await page.goto(`${BASE}/#/payments?customers=aerospace`);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/${device}-payments-scoped.png` });
  const clear = await page.getByRole("button", { name: /whole book/i }).count();
  console.log(device, "scoped payments — 'Whole book' button present:", clear === 1);

  await ctx.close();
}
await browser.close();
