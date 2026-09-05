/* Photograph the running app: both seeded roles, desktop and phone.
 *
 * Not a test — it asserts nothing. Its job is to put rendered pixels on disk so
 * a UI change can be judged against what the screen actually looks like rather
 * than against what its source suggests it looks like.
 *
 * A plain script rather than a spec on purpose. As a spec this needed a second
 * Playwright config to escape the `testIgnore` that keeps it out of
 * `npm run e2e`, and the runner did not survive a job this long in a
 * constrained container. A script has one moving part, takes a screen list on
 * the command line, and can be run in pieces.
 *
 * Both servers must already be up — the same two `playwright.config.ts` starts:
 *   bash e2e/serve-backend.sh &
 *   npm run dev -- --port 5173 --strictPort --host 127.0.0.1 &
 *
 * Then, from `frontend/`:
 *   node e2e/.shots/capture.mjs --out /tmp/shots --role sales --device desktop
 *
 * --role    sales | manager | both      (default both)
 * --device  desktop | phone | both      (default both)
 * --only    comma-separated screen names, or "quote" for the builder flow
 */
import { chromium } from "@playwright/test";
import * as fs from "node:fs";

const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i === -1 ? fallback : process.argv[i + 1];
};

const OUT = arg("out", "/tmp/shots");
const PASSWORD = process.env.SEED_PASSWORD ?? "change-me-now";
const BASE = process.env.SHOT_BASE_URL ?? "http://127.0.0.1:5173";
const CHROME = process.env.PLAYWRIGHT_CHROMIUM_PATH ||
  "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

const ROLES = { sales: "r.nair@pie.example", manager: "m.rao@pie.example" };
const DEVICES = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } };

/** Seeded by `app.demo`. Pricing reads the customer's own history, so the
 *  Quote Builder will not open a quote until it knows whose. */
const CUSTOMER = "Pitti Engineering Ltd";
/** A code the seeded catalogue resolves, so the grid has a real line on it. */
const RFQ = "2001174, 20";

const SCREENS = [
  ["home", "#/"],
  ["decisions", "#/decisions"],
  ["quotes", "#/quotes"],
  ["customers", "#/customers"],
  ["stock", "#/stock"],
  ["approvals", "#/approvals"],
  ["quote-outcomes", "#/quote-outcomes"],
  ["unanswered", "#/unanswered-quotes"],
  ["item-lines", "#/item-lines"],
  ["decoded-catalogue", "#/decoded-catalogue"],
  ["weather", "#/weather"],
  ["opportunities", "#/opportunities"],
  ["settings", "#/settings"],
  ["data", "#/data"],
];

/* Deliberately not `networkidle`: react-query keeps refetching after paint, so
 * several screens never reach it and the wait burns its whole timeout. */
const settle = async (page, ms = 2200) => {
  await page.waitForLoadState("domcontentloaded", { timeout: 20_000 }).catch(() => {});
  await page.waitForTimeout(ms);
};

async function shoot(page, dir, name) {
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true, timeout: 30_000 });
  console.log(`  ✓ ${name}`);
}

async function signIn(page, email) {
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.waitForTimeout(1500);
  // `/` is the marketing landing page, not the sign-in card: pre-auth is a
  // `door` state rather than a route, and the door opens on `landing`. The form
  // has to be asked for before it can be filled.
  const email_field = page.locator('input[name="email"]');
  if ((await email_field.count()) === 0) {
    await page.getByRole("button", { name: /^sign in$/i })
      .or(page.getByRole("link", { name: /^sign in$/i }))
      .first().click();
    await email_field.waitFor({ timeout: 20_000 });
  }
  await email_field.fill(email);
  await page.fill('input[name="password"]', PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).last().click();
  await page.locator('input[name="password"]').waitFor({ state: "detached", timeout: 30_000 });
}

/** The Quote Builder holding a real resolved line, and the supply drawer where
 *  the two roles visibly diverge — the two states no static route reaches. */
async function quoteFlow(page, dir) {
  await page.goto(`${BASE}/#/quotes`, { waitUntil: "domcontentloaded" });
  await settle(page);
  // The workspace is a list of drafts; the customer picker belongs to a draft.
  // On an empty desk the only thing on screen is the empty state's own call to
  // action, so the draft has to be started before there is anything to fill in.
  const start = page.getByRole("button", { name: /start the first quote|new quote/i }).first();
  if (await start.count()) {
    await start.click();
    await settle(page);
  }
  // The draft opens with no customer on it — deliberate, and the reason the
  // Commercial column reads "—" until one is chosen. The picker is a dialog
  // behind its own button, so it has to be opened before it can be searched.
  const customer = page.getByRole("combobox", { name: "Customer" });
  if ((await customer.count()) === 0) {
    await page.getByRole("button", { name: /choose customer/i }).first().click();
    await settle(page, 1200);
  }
  await customer.waitFor({ timeout: 25_000 });
  await customer.fill(CUSTOMER.split(" ")[0]);
  await page.getByRole("option", { name: new RegExp(CUSTOMER, "i") }).first().click();
  await page.getByRole("button", { name: /use this customer/i }).click();
  await settle(page);
  await shoot(page, dir, "quote-empty");

  await page.getByRole("button", { name: "Paste RFQ" }).first().click();
  await page.getByLabel("RFQ text").fill(RFQ);
  await shoot(page, dir, "quote-intake-dialog");
  const intake = page.waitForResponse(
    (r) => r.url().includes("/intake") && r.request().method() === "POST",
    { timeout: 90_000 },
  );
  await page.getByRole("button", { name: /resolve & add/i }).click();
  await intake;
  await settle(page, 3500);
  await shoot(page, dir, "quote-with-line");

  await page.getByRole("grid").locator(".ag-row").first().click({ timeout: 20_000 });
  await settle(page);
  await shoot(page, dir, "quote-supply-drawer");
}

const roles = arg("role", "both") === "both" ? Object.keys(ROLES) : [arg("role")];
const devices = arg("device", "both") === "both" ? Object.keys(DEVICES) : [arg("device")];
const only = arg("only", null);
const wanted = only && only !== "quote"
  ? SCREENS.filter(([n]) => only.split(",").includes(n))
  : (only === "quote" ? [] : SCREENS);

const browser = await chromium.launch({ executablePath: CHROME });
try {
  for (const device of devices) {
    for (const role of roles) {
      const dir = `${OUT}/${device}/${role}`;
      console.log(`\n== ${role} / ${device} -> ${dir}`);
      const page = await browser.newPage({ viewport: DEVICES[device] });
      try {
        await signIn(page, ROLES[role]);
        for (const [name, hash] of wanted) {
          await page.goto(`${BASE}/${hash}`, { waitUntil: "domcontentloaded" });
          await settle(page);
          await shoot(page, dir, name).catch((e) => console.log(`  ✗ ${name}: ${e.message.slice(0, 90)}`));
        }
        if (!only || only === "quote") {
          await quoteFlow(page, dir).catch((e) => console.log(`  ✗ quote: ${e.message.slice(0, 160)}`));
        }
      } catch (e) {
        console.log(`  ✗ ${role}/${device} aborted: ${String(e).slice(0, 200)}`);
      } finally {
        await page.close();
      }
    }
  }
} finally {
  await browser.close();
}
console.log("\ndone");
