/* Baseline capture — the rendered PIE app, both roles, desktop and phone.
 *
 * Not a test. It asserts almost nothing on purpose: its job is to put real
 * pixels on disk so the redesign is judged against what the app actually looks
 * like rather than against what its source suggests it looks like.
 */
import { test, type Page } from "@playwright/test";
import * as fs from "node:fs";

const PASSWORD = process.env.SEED_PASSWORD ?? "change-me-now";
const SALES = "r.nair@pie.example";
const MANAGER = "m.rao@pie.example";
const RFQ = "2001174, 20";
const CUSTOMER = "Pitti Engineering Ltd";
const OUT = process.env.SHOT_DIR ?? "/tmp/shots";

/** Screens worth a picture, in the order a person meets them. */
const SCREENS: [name: string, hash: string][] = [
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

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/");
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.locator('input[name="password"]').waitFor({ state: "detached", timeout: 30_000 });
}

/** Let the screen settle without asserting on any particular content: these
 *  screens legitimately differ by role, and a wait that demands a grid would
 *  fail on the ones that have none.
 *
 *  Deliberately NOT `networkidle`. This app keeps work in flight after paint —
 *  react-query refetches and the lazy route chunks — so "no request for 500ms"
 *  is a state several screens never reach, and waiting for it burned the whole
 *  run against a timeout per screen and produced nothing. `domcontentloaded`
 *  plus a fixed beat is what a person actually waits for. */
async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("domcontentloaded", { timeout: 15_000 }).catch(() => {});
  await page.waitForTimeout(2500);
}

async function shoot(page: Page, dir: string, name: string): Promise<void> {
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png`, fullPage: true });
}

for (const [role, email] of [["sales", SALES], ["manager", MANAGER]] as const) {
  for (const [device, width, height] of [
    ["desktop", 1440, 900],
    ["phone", 390, 844],
  ] as const) {
    test(`${role} ${device}`, async ({ page }) => {
      test.setTimeout(360_000);
      await page.setViewportSize({ width, height });
      await signIn(page, email);
      const dir = `${OUT}/${device}/${role}`;

      for (const [name, hash] of SCREENS) {
        await page.goto(`/${hash}`);
        await settle(page);
        await shoot(page, dir, name).catch(() => {});
      }

      // The Quote Builder with a real line on it — the screen this product
      // lives or dies by, and the one no static route reaches.
      try {
        await page.goto("/#/quotes");
        const customer = page.getByRole("combobox", { name: "Customer" });
        await customer.waitFor({ timeout: 20_000 });
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
          { timeout: 60_000 },
        );
        await page.getByRole("button", { name: /resolve & add/i }).click();
        await intake;
        await settle(page);
        await shoot(page, dir, "quote-with-line");

        // The supply drawer: where the pricing context lives, and where the
        // two roles visibly diverge.
        await page.getByRole("grid").locator(".ag-row").first().click({ timeout: 15_000 });
        await settle(page);
        await shoot(page, dir, "quote-supply-drawer");
      } catch (err) {
        fs.writeFileSync(`${dir}/quote-FAILED.txt`, String(err));
      }
    });
  }
}
