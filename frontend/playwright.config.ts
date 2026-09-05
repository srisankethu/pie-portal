import { defineConfig, devices } from "@playwright/test";

/* End-to-end, against the real API and a real browser.
 *
 * Deliberately not part of `npm test`: vitest owns `src/**\/*.test.tsx` and runs
 * in three seconds, which is what makes it something people run on every save.
 * This suite starts two servers and a browser, so it is `npm run e2e` and a
 * separate CI job.
 *
 * Chromium is expected to be already installed — the container provides it at
 * PLAYWRIGHT_BROWSERS_PATH. Do not add a `playwright install` step.
 */
export default defineConfig({
  testDir: "./e2e",
  // `.shots/` is a capture harness, not a suite: it drives the app to put
  // rendered pixels on disk so a UI change can be judged against what the
  // screen actually looks like. It asserts almost nothing, so including it in
  // `npm run e2e` would add minutes to the run and a green result that means
  // nothing.
  //
  // This ignore also applies to an explicitly named path, so the harness is
  // run through its own config rather than a flag:
  //   npx playwright test -c playwright.shots.config.ts
  testIgnore: "**/.shots/**",
  // One worker, no parallelism: both specs drive the same backend and the same
  // seeded database, and a second worker would be racing it.
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? "line" : "list",
  // A failed run must say why without a re-run: CI has no browser to reopen.
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        /* Normally Playwright's own bundled Chromium, installed by
         * `npx playwright install chromium`. Some CI images ship a browser
         * already and pin it by path — set PLAYWRIGHT_CHROMIUM_PATH there
         * rather than re-downloading one, or bumping this package to whatever
         * build that image happens to hold. */
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
          : {},
      },
    },
  ],
  webServer: [
    {
      command: "bash e2e/serve-backend.sh",
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: "npm run dev -- --port 5173 --strictPort --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
