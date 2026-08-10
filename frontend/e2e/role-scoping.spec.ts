/* Cost and margin never reach a salesperson — asserted through the real thing.
 *
 * This invariant has been proven in two halves that never met. The backend
 * suite proves the *server* omits the fields. `LineGrid.test.tsx` proves the
 * *grid* will not render them if it is handed them. Neither proves that the
 * screen a salesperson actually loads is wired to the endpoint that omits them,
 * and the wiring is precisely where `filterCounts.MFLOOR` lived: a below-floor
 * count computed for every role, two lines under the guard that correctly
 * withheld `marginFloor`, hidden in the component by `{mgmt && …}`.
 *
 * So this signs in as a real salesperson, builds a real quote against the real
 * API, and reads every response the browser received. The assertion is on the
 * network rather than the DOM: hiding a field in a component still ships it to
 * the browser, and a network tab is not a hard thing to open.
 *
 * The manager case is not decoration. Without it the salesperson assertion
 * passes just as well when the flow produced no economics at all — the
 * "absence of evidence is not a pass" trap, which CLAUDE.md §1 records as
 * having already been made three times in this codebase.
 */
import { expect, test, type Page, type Response } from "@playwright/test";

const PASSWORD = process.env.SEED_PASSWORD ?? "change-me-now";
const SALES = "r.nair@sanketh.in";
const MANAGER = "m.rao@sanketh.in";

/** A code the seeded catalogue resolves, so the grid has a real line on it. */
const RFQ = "2001174, 20";
/** Seeded by `app.demo`. Pricing reads the customer's own history, so the
 *  Quote Builder will not open a quote until it knows whose. */
const CUSTOMER = "Pitti Engineering Ltd";

/* Every one of these answers a margin question. `MFLOOR` is in the list as a
 * key because it arrives as one, inside `filterCounts`. */
const RESTRICTED = new Set([
  "economics", "marginFloor", "MFLOOR",
  "cost", "margin", "below_floor", "list_price",
]);

/** Every key appearing anywhere in a JSON value, at any depth. */
function keysIn(value: unknown, out = new Set<string>()): Set<string> {
  if (Array.isArray(value)) {
    for (const item of value) keysIn(item, out);
  } else if (value !== null && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      out.add(k);
      keysIn(v, out);
    }
  }
  return out;
}

interface ApiWatch {
  urls: string[];
  /** Every key seen, once the bodies outstanding at call time have been read. */
  keys(): Promise<Set<string>>;
}

/** Collect the keys of every JSON body the page receives from the API.
 *
 * Reading a body is asynchronous, so the parses in flight have to be awaited
 * before asserting. Without that this watches a set that is still filling and
 * "no restricted field arrived" can simply mean "the response had not been read
 * yet" — a green test asserting nothing, which is the failure mode this whole
 * file exists to prevent.
 */
function watchApi(page: Page): ApiWatch {
  const seen = new Set<string>();
  const urls: string[] = [];
  const pending: Promise<void>[] = [];
  page.on("response", (res: Response) => {
    const url = res.url();
    if (!url.includes("/api/")) return;
    urls.push(url);
    pending.push(
      res.json()
        .then((body) => { keysIn(body, seen); })
        .catch(() => {/* not JSON — nothing to read */}),
    );
  });
  return {
    urls,
    async keys() {
      await Promise.all(pending);
      return seen;
    },
  };
}

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/");
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  // The sign-in card is gone once the session is real.
  await expect(page.locator('input[name="password"]')).toHaveCount(0, {
    timeout: 20_000,
  });
}

async function buildAQuote(page: Page): Promise<void> {
  await page.goto("/#/quotes");

  // The screen opens by asking who the quote is for — pricing reads that
  // customer's own history, so there is no quote to build without one.
  const customer = page.getByRole("combobox", { name: "Customer" });
  await customer.waitFor({ timeout: 20_000 });
  await customer.fill(CUSTOMER.split(" ")[0]);
  await page.getByRole("option", { name: new RegExp(CUSTOMER, "i") }).first().click();
  await page.getByRole("button", { name: /use this customer/i }).click();

  await page.getByRole("button", { name: "Paste RFQ" }).first().click();
  await page.getByLabel("RFQ text").fill(RFQ);

  // Wait on the intake *response*, not on the code appearing somewhere in the
  // page. The code is also the text just typed into the RFQ box, so a
  // `getByText` wait is satisfied by the still-open dialog and the test races
  // ahead of the request it exists to inspect.
  const intake = page.waitForResponse(
    (r) => r.url().includes("/intake") && r.request().method() === "POST",
    { timeout: 30_000 },
  );
  await page.getByRole("button", { name: /resolve & add/i }).click();
  const response = await intake;
  expect(response.ok(), `intake failed: ${response.status()}`).toBe(true);

  // …and the grid really did render the line, so this is the screen a person
  // would be looking at rather than a request made in the dark.
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("grid")).toBeVisible({ timeout: 30_000 });
}

test.describe("the quote grid", () => {
  test("a manager is served the economics — the control for the test below",
    async ({ page }) => {
      const api = watchApi(page);
      await signIn(page, MANAGER);
      await buildAQuote(page);

      const keys = await api.keys();
      expect(
        api.urls.some((u) => u.includes("/intake")),
        `the flow never called /intake. URLs seen: ${api.urls.join(", ")}`,
      ).toBe(true);
      // If these fail, the salesperson test below proves nothing: it would be
      // asserting the absence of something the flow never produces for anyone.
      const saw = [...keys].sort().join(", ");
      expect([...keys], `a manager must be served economics. Keys seen: ${saw}`)
        .toContain("economics");
      expect([...keys], `a manager must be served cost. Keys seen: ${saw}`)
        .toContain("cost");
    });

  test("a salesperson is never sent cost or margin, in any response",
    async ({ page }) => {
      const api = watchApi(page);
      await signIn(page, SALES);
      await buildAQuote(page);

      const keys = await api.keys();
      // The flow must actually have happened, or "nothing leaked" is vacuous.
      expect(
        api.urls.some((u) => u.includes("/intake")),
        `the flow never called /intake. URLs seen: ${api.urls.join(", ")}`,
      ).toBe(true);

      const leaked = [...keys].filter((k) => RESTRICTED.has(k));
      expect(
        leaked,
        `these fields reached a salesperson's browser: ${leaked.join(", ")}. ` +
        `Hiding them in the component is not a fix — the response is readable ` +
        `in a network tab.`,
      ).toEqual([]);

      // The same guarantee where a person would look for it. The server is the
      // real control; this is the second half, and it is cheap.
      await expect(
        page.getByRole("columnheader", { name: /margin|cost/i }),
      ).toHaveCount(0);
    });
});
